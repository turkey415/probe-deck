"""Serial console: a USB serial cable to a switch console, shared by web terminals and an optional telnet port.

One reader owns the serial port. Everything it reads goes to a scrollback buffer, the session log and
every attached client (each client has its own queue and sender thread, so a slow one can't stall the
port). Clients send keystrokes, Break, paced pastes and macros.
"""
import glob
import json
import os
import queue
import re
import socket
import threading
import time
from pathlib import Path

import ws as wsmod

try:
    import serial  # python3-serial (pyserial)
except ImportError:
    serial = None

BAUDS = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400)
SCROLL_MAX = 128 * 1024
LOG_MAX = 10 * 1024 * 1024
LOGS_KEEP = 50
LOG_RE = re.compile(r"[\w.\-]{1,80}\.log")

DEFAULT_MACROS = """No paging = terminal length 0
Int status = show interfaces status
IP brief = show ip interface brief
VLANs = show vlan brief
CDP = show cdp neighbors
Err-disabled = show interfaces status err-disabled
PoE = show power inline
Log = show logging
Save = write memory"""

DEFAULTS = {
    "console_port": "auto",
    "console_baud": 9600,
    "console_line_delay": 50,      # ms, the least wait between pasted lines
    "console_telnet": False,
    "console_telnet_port": 2001,
    "console_macros": DEFAULT_MACROS,
    "console_pw": "",              # the old console password (before user accounts): only used for the first setup
}


def parse_macros(text):
    out = []
    for line in str(text).splitlines():
        if not line.strip():
            continue
        label, sep, cmd = line.partition("=")
        if not sep or not label.strip() or not cmd.strip():
            raise ValueError(f"Macro needs Label = command: {line.strip()[:40]}")
        out.append({"label": label.strip()[:24], "cmd": cmd.strip()[:300]})
    if len(out) > 24:
        raise ValueError("At most 24 macros")
    return out


def list_ports():
    """USB serial adapters. by-id names say what the adapter is (FTDI, Prolific, CH340...)."""
    names = {}
    for link in sorted(glob.glob("/dev/serial/by-id/*")):
        names[os.path.realpath(link)] = os.path.basename(link)
    out = []
    test = os.environ.get("PROBE_DECK_TEST_SERIAL")  # a pty from socat, for testing without a cable
    devs = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*")) + ([test] if test and os.path.exists(test) else [])
    for dev in devs:
        n = names.get(dev, "")
        n = re.sub(r"^usb-", "", n)
        n = re.sub(r"-if\d+(-port\d+)?$", "", n).replace("_", " ")
        out.append({"path": dev, "name": n})
    return out


class Client:
    """One attached viewer. Output goes through a queue so the serial reader never blocks on it."""

    def __init__(self, kind, send_out, send_status, close):
        self.kind, self._out, self._status, self._close = kind, send_out, send_status, close
        self.q = queue.Queue(maxsize=2000)
        self.alive = True
        threading.Thread(target=self._pump, daemon=True).start()

    def put(self, item):
        if not self.alive:
            return
        try:
            self.q.put_nowait(item)
        except queue.Full:  # too slow to keep up: drop it rather than stall the port
            self.stop()

    def _pump(self):
        while self.alive:
            item = self.q.get()
            if item is None:
                break
            try:
                (self._out if isinstance(item, (bytes, bytearray)) else self._status)(item)
            except Exception:
                break
        self.alive = False
        try:
            self._close()
        except Exception:
            pass

    def stop(self):
        self.alive = False
        try:
            self.q.put_nowait(None)
        except queue.Full:
            pass


class Console:
    def __init__(self, probe, data_dir):
        self.p = probe
        self.logdir = Path(data_dir) / "console"
        self.logdir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()      # clients, scrollback, status
        self.wlock = threading.Lock()     # writes to the port
        self.ser = None
        self.path = None
        self.opened = None
        self.error = None if serial else "python3-serial is not installed"
        self.clients = set()
        self.scroll = bytearray()
        self.log = None
        self.log_name = None
        self.rx_total = 0
        self.last_rx = 0.0
        self.paste = None
        self.taps = set()                 # queues for swcli jobs reading the switch's answers
        self.job_lock = threading.Lock()  # one swcli job on the cable at a time
        self.auth = None                  # set by the server: users for the telnet login
        self.reopen = threading.Event()
        self.tel_sock = None
        threading.Thread(target=self.loop, daemon=True).start()
        threading.Thread(target=self.telnet_loop, daemon=True).start()

    @property
    def conf(self):
        return self.p.conf

    # ---------- settings (only through the console API, behind the password) ----------
    def save(self, new):
        c = {}
        if "console_port" in new:
            v = str(new["console_port"])
            if v != "auto" and not re.fullmatch(r"/dev/tty(USB|ACM)\d{1,2}", v):
                raise ValueError("Bad serial port")
            c["console_port"] = v
        if "console_baud" in new:
            v = int(new["console_baud"])
            if v not in BAUDS:
                raise ValueError("Unsupported baud rate")
            c["console_baud"] = v
        if "console_line_delay" in new:
            v = int(new["console_line_delay"])
            if not 0 <= v <= 2000:
                raise ValueError("Line delay must be 0-2000 ms")
            c["console_line_delay"] = v
        if "console_telnet" in new:
            c["console_telnet"] = bool(new["console_telnet"])
        if "console_telnet_port" in new:
            v = int(new["console_telnet_port"])
            if not 1024 <= v <= 65535 or v in (5201, 52010, 47474):
                raise ValueError("Telnet port must be 1024-65535 and not one Probe Deck already uses")
            c["console_telnet_port"] = v
        if "console_macros" in new:
            parse_macros(new["console_macros"])
            c["console_macros"] = str(new["console_macros"])[:8000]
        old = dict(self.conf)
        self.p.save_conf(c, internal=True)
        if c.get("console_port", old["console_port"]) != old["console_port"]:
            self.reopen.set()
        elif c.get("console_baud", old["console_baud"]) != old["console_baud"] and self.ser:
            try:
                self.ser.baudrate = c["console_baud"]
            except Exception as e:
                self.error = str(e)[:120]
        self.push_status()

    # ---------- the serial port ----------
    def pick(self):
        want = self.conf["console_port"]
        ports = [p["path"] for p in list_ports()]
        if want != "auto":
            return want if want in ports else None
        usb = [p for p in ports if "ttyACM" not in p]  # FTDI/PL2303/CH340 console cables; ttyACM is usually a dev board
        return (usb or ports or [None])[0]

    def loop(self):
        while True:
            if not serial:
                time.sleep(30)
                continue
            if self.ser is None:
                path = self.pick()
                if not path:
                    if self.error != "no cable":
                        self.error = "no cable"
                        self.push_status()
                    time.sleep(1)
                    continue
                try:
                    s = serial.Serial(path, self.conf["console_baud"], timeout=0.2, bytesize=8, parity="N", stopbits=1,
                                      xonxoff=False, rtscts=False, exclusive=True)
                except Exception as e:
                    msg = str(e)[:160]
                    if self.error != msg:
                        self.error = msg
                        self.push_status()
                    time.sleep(2)
                    continue
                self.ser, self.path, self.opened, self.error = s, path, time.time(), None
                self.reopen.clear()
                self.start_log()
                self.push_status()
                self.feed(f"\r\n\x1b[2m[probe-deck] {path} open at {s.baudrate} baud\x1b[0m\r\n".encode(), log=False)
            try:
                data = self.ser.read(self.ser.in_waiting or 1)
            except Exception as e:
                self.close_port(f"cable unplugged ({str(e)[:80]})")
                continue
            if self.reopen.is_set():
                self.close_port(None)
                continue
            if data:
                self.feed(data)

    def close_port(self, why):
        s, self.ser = self.ser, None
        try:
            s and s.close()
        except Exception:
            pass
        if self.paste:
            self.paste["cancel"] = True
        if why:
            self.feed(f"\r\n\x1b[2m[probe-deck] {why}\x1b[0m\r\n".encode(), log=False)
        self.end_log()
        self.path, self.opened, self.error = None, None, why
        self.push_status()
        time.sleep(0.5)

    def feed(self, data, log=True):
        now = time.time()
        with self.lock:
            if log:
                self.rx_total += len(data)
                self.last_rx = now
            self.scroll += data
            if len(self.scroll) > SCROLL_MAX:
                del self.scroll[: len(self.scroll) - SCROLL_MAX]
            for c in self.clients:
                c.put(bytes(data))
            if log:
                for q in self.taps:
                    q.put(bytes(data))
        if log and self.log:
            try:
                if self.log.tell() < LOG_MAX:
                    self.log.write(data)
                    self.log.flush()
            except OSError:
                pass

    def write(self, data):
        with self.wlock:
            s = self.ser
            if not s:
                return False
            try:
                s.write(data)
                return True
            except Exception:
                return False

    def tap(self):
        q = queue.Queue()
        with self.lock:
            self.taps.add(q)
        return q

    def untap(self, q):
        with self.lock:
            self.taps.discard(q)

    def send_break(self):
        s = self.ser
        if s:
            threading.Thread(target=lambda: s.send_break(0.5), daemon=True).start()
            self.feed(b"\r\n\x1b[2m[probe-deck] Break sent\x1b[0m\r\n", log=False)

    # ---------- paced paste: one line at a time, wait for the echo ----------
    def start_paste(self, text):
        if self.paste and not self.paste.get("done_at"):
            raise ValueError("A paste is already running")
        lines = re.split(r"\r\n|\r|\n", str(text))
        if lines and lines[-1] == "":
            lines.pop()
        if not lines:
            return
        if len(lines) > 5000:
            raise ValueError("Paste is too long (5000 lines at most)")
        self.paste = {"total": len(lines), "done": 0, "cancel": False, "done_at": None}
        threading.Thread(target=self.paste_run, args=(lines, self.paste), daemon=True).start()

    def paste_run(self, lines, job):
        gap = self.conf["console_line_delay"] / 1000
        last_status = 0
        for line in lines:
            if job["cancel"] or not self.ser:
                break
            mark = self.rx_total
            if not self.write(line.encode() + b"\r"):
                break
            t0 = time.time()
            time.sleep(gap)
            # Wait until the switch has echoed something and gone quiet (it's done with the line), at most 1.5 s.
            while time.time() - t0 < 1.5 and not job["cancel"]:
                if self.rx_total > mark and time.time() - self.last_rx > 0.08:
                    break
                time.sleep(0.02)
            job["done"] += 1
            if time.time() - last_status > 0.25:
                last_status = time.time()
                self.push_status()
        job["done_at"] = time.time()
        self.push_status()

    # ---------- session logs ----------
    def start_log(self):
        self.end_log()
        name = time.strftime("%Y-%m-%d_%H%M%S_") + os.path.basename(self.path) + ".log"
        try:
            self.log = open(self.logdir / name, "ab")
            self.log_name = name
            self.log.write(f"# Probe Deck console log: {self.path} at {self.conf['console_baud']} baud, opened {time.ctime()}\n".encode())
            self.log.flush()
        except OSError:
            self.log = None
        logs = sorted(self.logdir.glob("*.log"))
        for old in logs[:-LOGS_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass

    def end_log(self):
        if self.log:
            try:
                self.log.write(f"\n# closed {time.ctime()}\n".encode())
                self.log.close()
            except OSError:
                pass
        self.log, self.log_name = None, None

    def logs(self):
        out = []
        for f in sorted(self.logdir.glob("*.log"), reverse=True):
            try:
                st = f.stat()
                out.append({"name": f.name, "size": st.st_size, "t": st.st_mtime, "live": f.name == self.log_name})
            except OSError:
                pass
        return out

    def log_path(self, name):
        if not LOG_RE.fullmatch(name or ""):
            return None
        f = self.logdir / name
        return f if f.is_file() else None

    def clear_logs(self):
        for f in self.logdir.glob("*.log"):
            if f.name != self.log_name:
                try:
                    f.unlink()
                except OSError:
                    pass

    # ---------- status ----------
    def status(self):
        pj = self.paste
        return {
            "t": "status", "open": bool(self.ser), "port": self.path, "baud": self.conf["console_baud"],
            "want": self.conf["console_port"], "error": self.error, "opened": self.opened,
            "clients": len(self.clients), "n_web": sum(c.kind == "web" for c in self.clients),
            "n_telnet": sum(c.kind == "telnet" for c in self.clients), "log": self.log_name,
            "paste": {k: pj[k] for k in ("total", "done", "done_at")} if pj else None,
        }

    def info(self, admin):
        """For the page. Viewers can watch and read the logs; only admins see the settings."""
        out = {"admin": admin, "pyserial": bool(serial), "ports": list_ports(),
               "telnet": self.conf["console_telnet"], "telnet_port": self.conf["console_telnet_port"],
               "telnet_up": bool(self.tel_sock), "bauds": BAUDS, "logs": self.logs(),
               "macros": parse_macros(self.conf["console_macros"]), **self.status()}
        if admin:
            out.update(macros_text=self.conf["console_macros"], line_delay=self.conf["console_line_delay"])
        return out

    def push_status(self):
        st = self.status()
        with self.lock:
            for c in self.clients:
                c.put(dict(st))

    def attach(self, client):
        with self.lock:
            if self.scroll:
                client.put(bytes(self.scroll))
            self.clients.add(client)
        self.push_status()

    def detach(self, client):
        client.stop()
        with self.lock:
            self.clients.discard(client)
        self.push_status()

    # ---------- web terminal ----------
    def serve_ws(self, sock, admin=True):
        """admin=False: a viewer. They see everything the switch prints; what they send is dropped."""
        c = Client("web", sock.send_bytes, sock.send_json, sock.close)
        c.put({**self.status(), "t": "hello", "readonly": not admin})
        self.attach(c)
        try:
            while c.alive:
                msg = sock.recv()
                if not admin:
                    continue
                if isinstance(msg, bytes):
                    self.write(msg)
                    continue
                try:
                    m = json.loads(msg)
                except ValueError:
                    continue
                t = m.get("t") if isinstance(m, dict) else None
                try:
                    if t == "in" and isinstance(m.get("d"), str):
                        self.write(m["d"].encode("utf-8", "replace"))
                    elif t == "break":
                        self.send_break()
                    elif t == "paste" and isinstance(m.get("d"), str):
                        self.start_paste(m["d"])
                    elif t == "paste_stop" and self.paste:
                        self.paste["cancel"] = True
                    elif t == "macro" and isinstance(m.get("d"), str):
                        self.write(m["d"].encode("utf-8", "replace")[:300] + b"\r")
                    elif t == "clear":
                        with self.lock:
                            self.scroll.clear()
                except ValueError as e:
                    c.put({"t": "error", "error": str(e)})
                if t in ("paste", "paste_stop"):
                    self.push_status()
        except wsmod.Closed:
            pass
        finally:
            self.detach(c)

    # ---------- telnet port (what ser2net would give you) ----------
    def telnet_loop(self):
        while True:
            want = self.conf["console_telnet"] and self.auth and not self.auth.needs_setup()
            port = self.conf["console_telnet_port"]
            if want and (not self.tel_sock or self.tel_sock.getsockname()[1] != port):
                self.telnet_close()
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("", port))
                    s.listen(4)
                    self.tel_sock = s
                    threading.Thread(target=self.telnet_accept, args=(s,), daemon=True).start()
                except OSError as e:
                    self.tel_sock = None
                    self.error = self.error or f"telnet port {port}: {e.strerror}"
            elif not want and self.tel_sock:
                self.telnet_close()
            time.sleep(2)

    def telnet_close(self):
        s, self.tel_sock = self.tel_sock, None
        if s:
            try:
                s.close()
            except OSError:
                pass
        for c in [c for c in self.clients if c.kind == "telnet"]:
            self.detach(c)

    def telnet_accept(self, s):
        while self.tel_sock is s:
            try:
                conn, addr = s.accept()
            except OSError:
                return
            threading.Thread(target=self.telnet_serve, args=(conn,), daemon=True).start()

    def telnet_serve(self, conn):
        IAC = 255
        conn.settimeout(None)
        try:
            # Server echoes and no go-ahead: puts telnet/PuTTY in character mode.
            conn.sendall(bytes([IAC, 251, 1, IAC, 251, 3, IAC, 253, 3]))
            if not self.telnet_login(conn):
                conn.close()
                return
        except OSError:
            conn.close()
            return

        def out(b):
            conn.sendall(bytes(b).replace(b"\xff", b"\xff\xff"))

        last = {"open": None}

        def status(st):
            if st.get("open") != last["open"] and last["open"] is not None:
                conn.sendall(f"\r\n[probe-deck] console {'open on ' + str(st.get('port')) if st.get('open') else 'cable unplugged'}\r\n".encode())
            last["open"] = st.get("open")

        def close():
            try:
                conn.shutdown(socket.SHUT_RDWR)
                conn.close()
            except OSError:
                pass

        c = Client("telnet", out, status, close)
        st = self.status()
        c.put(f"\r\n[probe-deck] {('connected to ' + st['port'] + ' at ' + str(st['baud']) + ' baud') if st['open'] else 'no console cable right now'}."
              f" In telnet, Ctrl-] then 'send brk' sends a Break.\r\n".encode())
        self.attach(c)
        state, cr = 0, True  # True: drop the LF/NUL that followed the password's CR
        try:
            while c.alive:
                buf = conn.recv(4096)
                if not buf:
                    break
                data = bytearray()
                for b in buf:
                    if state == 0:
                        if b == IAC:
                            state = 1
                        elif cr and b in (0, 10):  # CR NUL / CR LF from the client is just Enter
                            pass
                        else:
                            data.append(b)
                        cr = b == 13
                    elif state == 1:
                        state = 0
                        if b == IAC:
                            data.append(IAC)
                        elif 251 <= b <= 254:
                            state = 2
                        elif b == 250:
                            state = 3
                        elif b == 243:  # BRK
                            self.write(bytes(data))
                            data.clear()
                            self.send_break()
                        elif b == 244:  # Interrupt Process: pass Ctrl-C
                            data.append(3)
                    elif state == 2:
                        state = 0
                    elif state == 3:
                        state = 4 if b == IAC else 3
                    elif state == 4:
                        state = 0 if b == 240 else 3
                if data:
                    self.write(bytes(data))
        except OSError:
            pass
        finally:
            self.detach(c)

    @staticmethod
    def telnet_line(conn, echo):
        """One line typed in telnet. We told the client we echo, so echo the name but not the password."""
        line, state = bytearray(), 0
        while True:
            chunk = conn.recv(1)
            if not chunk:
                return None
            b = chunk[0]
            if b in (0, 10) and not line and state == 0:
                continue  # the LF or NUL telnet sends after the CR of the last line
            if state == 1:
                state = 2 if 251 <= b <= 254 else 0
                continue
            if state == 2:
                state = 0
                continue
            if b == 255:
                state = 1
            elif b in (13, 10):
                conn.sendall(b"\r\n")
                return line.decode("utf-8", "replace")
            elif b in (8, 127):
                if line and echo:
                    conn.sendall(b"\b \b")
                line = line[:-1]
            elif b >= 32 and len(line) < 128:
                line.append(b)
                if echo:
                    conn.sendall(bytes([b]))

    def telnet_login(self, conn):
        """Admins only: typing into a switch console is a change."""
        for _ in range(3):
            conn.sendall(b"Probe Deck user: ")
            name = self.telnet_line(conn, True)
            if name is None:
                return False
            conn.sendall(b"Password: ")
            pw = self.telnet_line(conn, False)
            if pw is None:
                return False
            if self.auth and self.auth.check(name, pw, role="admin"):
                return True
            time.sleep(1)
            conn.sendall(b"Wrong user name or password (the telnet console is for admins).\r\n")
        return False
