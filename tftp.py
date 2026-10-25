"""Files: a TFTP server for switch upgrades and config copies, and a place to upload files to it.

Files live in /var/lib/probe-deck/files/. The server speaks TFTP (RFC 1350) with the blksize,
tsize and timeout options (RFC 2347-2349), so a switch pulls a 30 MB IOS image in seconds.
Uploads from switches (copy running-config tftp:) are off until "Allow writes" is ticked.
"""
import hashlib
import os
import re
import shutil
import socket
import struct
import threading
import time
from collections import deque

from appbase import App, Reply, load_json, save_json

RRQ, WRQ, DATA, ACK, ERROR, OACK = 1, 2, 3, 4, 5, 6
NAME_RE = re.compile(r"[A-Za-z0-9_][\w.\-]{0,99}")
MAX_FILE = 512 * 1024 * 1024
RETRIES = 6


def clean_name(n):
    n = os.path.basename(str(n or "").replace("\\", "/")).strip()
    if n.lower().startswith(("flash:", "tftp:")):
        n = n.split(":", 1)[1]
    if not NAME_RE.fullmatch(n):
        raise ValueError("File names: letters, digits, . _ - (no folders)")
    return n


class Files(App):
    name = "files"
    SETTINGS = {"tftp": False, "allow_write": False}
    RAW_POST = ("upload",)
    PORT = 69          # tests change it

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.meta_file = self.dir / ".md5.json"
        self.md5 = load_json(self.meta_file, {})
        self.sock = None
        self.error = None
        self.active = {}
        self.log = deque(maxlen=50)
        self.restart = threading.Event()

    def on_conf(self):
        self.restart.set()

    # ---------- files ----------
    def path(self, name):
        p = self.dir / clean_name(name)
        if p.parent != self.dir:
            raise ValueError("Bad name")
        return p

    def files(self):
        out = []
        for f in sorted(self.dir.iterdir(), key=lambda f: f.name.lower()):
            if f.is_file() and not f.name.startswith(".") and not f.name.endswith(".part"):
                st = f.stat()
                m = self.md5.get(f.name)
                if not m or m.get("size") != st.st_size or m.get("mtime") != int(st.st_mtime):
                    m = None
                out.append({"name": f.name, "size": st.st_size, "t": st.st_mtime, "md5": m and m["md5"]})
        return out

    def hash_later(self, name):
        def go():
            p = self.dir / name
            h = hashlib.md5()
            try:
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                st = p.stat()
            except OSError:
                return
            with self.lock:
                self.md5[name] = {"md5": h.hexdigest(), "size": st.st_size, "mtime": int(st.st_mtime)}
                save_json(self.meta_file, self.md5)
        threading.Thread(target=go, daemon=True).start()

    def free(self):
        st = os.statvfs(self.dir)
        return st.f_bavail * st.f_frsize

    # ---------- TFTP ----------
    def start(self):
        while True:
            if not self.conf["tftp"]:
                self.close()
                self.error = None
                self.restart.wait(5)
                self.restart.clear()
                continue
            if not self.sock:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("0.0.0.0", self.PORT))
                    s.settimeout(1)
                    self.sock, self.error = s, None
                except OSError as e:
                    self.error = f"Can't listen on UDP 69: {e.strerror or e}"
                    self.restart.wait(10)
                    self.restart.clear()
                    continue
            if self.restart.is_set():
                self.restart.clear()
                self.close()
                continue
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                self.close()
                continue
            if len(data) >= 4 and struct.unpack("!H", data[:2])[0] in (RRQ, WRQ) and len(self.active) < 8:
                threading.Thread(target=self.transfer, args=(data, addr), daemon=True).start()

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    @staticmethod
    def err(sock, addr, code, msg):
        try:
            sock.sendto(struct.pack("!HH", ERROR, code) + msg.encode()[:100] + b"\0", addr)
        except OSError:
            pass

    def transfer(self, data, addr):
        op = struct.unpack("!H", data[:2])[0]
        parts = data[2:].split(b"\0")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", 0))
        rec = {"id": f"{addr[0]}:{addr[1]}", "ip": addr[0], "op": "read" if op == RRQ else "write", "file": None, "bytes": 0, "size": None,
               "started": time.time(), "ok": False, "error": None}
        try:
            if len(parts) < 2:
                raise PermissionError((4, "Bad request"))
            raw_name = parts[0].decode("ascii", "replace")
            mode = parts[1].decode("ascii", "replace").lower()
            opts = {}
            for i in range(2, len(parts) - 1, 2):
                if parts[i]:
                    opts[parts[i].decode("ascii", "replace").lower()] = parts[i + 1].decode("ascii", "replace")
            try:
                name = clean_name(raw_name)
            except ValueError:
                raise PermissionError((2, "Bad file name")) from None
            rec["file"] = name
            self.active[rec["id"]] = rec
            if mode not in ("octet", "netascii"):
                raise PermissionError((4, "Only octet and netascii"))
            blk = 512
            tout = 2.0
            oack = {}
            if "blksize" in opts:
                try:
                    blk = max(8, min(65464, int(opts["blksize"])))
                    oack["blksize"] = str(blk)
                except ValueError:
                    pass
            if "timeout" in opts:
                try:
                    tout = max(1, min(255, int(opts["timeout"])))
                    oack["timeout"] = str(int(tout))
                except ValueError:
                    pass
            s.settimeout(tout)
            if op == RRQ:
                self.send_file(s, addr, name, blk, opts, oack, rec)
            else:
                self.recv_file(s, addr, name, blk, opts, oack, rec)
            rec["ok"] = True
        except PermissionError as e:
            code, msg = e.args[0]
            self.err(s, addr, code, msg)
            rec["error"] = msg
        except (OSError, TimeoutError) as e:
            rec["error"] = str(e)[:100] or "transfer failed"
        finally:
            s.close()
            rec["secs"] = round(time.time() - rec["started"], 2)
            self.active.pop(rec["id"], None)
            self.log.appendleft(rec)

    def wait_ack(self, s, addr, packet, block):
        for _ in range(RETRIES):
            s.sendto(packet, addr)
            end = time.time() + s.gettimeout()
            while time.time() < end:
                try:
                    d, a = s.recvfrom(1024)
                except socket.timeout:
                    break
                if a != addr or len(d) < 4:
                    continue
                code, n = struct.unpack("!HH", d[:4])
                if code == ERROR:
                    raise OSError("the switch cancelled: " + d[4:].split(b"\0")[0].decode("ascii", "replace"))
                if code == ACK and n == block:
                    return
        raise TimeoutError("no answer from the switch")

    def send_file(self, s, addr, name, blk, opts, oack, rec):
        p = self.dir / name
        if not p.is_file():
            raise PermissionError((1, "File not found"))
        size = p.stat().st_size
        rec["size"] = size
        if "tsize" in opts:
            oack["tsize"] = str(size)
        if oack:
            self.wait_ack(s, addr, struct.pack("!H", OACK) + b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in oack.items()), 0)
        n = 1
        with open(p, "rb") as f:
            while True:
                chunk = f.read(blk)
                self.wait_ack(s, addr, struct.pack("!HH", DATA, n & 0xFFFF) + chunk, n & 0xFFFF)
                rec["bytes"] += len(chunk)
                n += 1
                if len(chunk) < blk:
                    break

    def recv_file(self, s, addr, name, blk, opts, oack, rec):
        if not self.conf["allow_write"]:
            raise PermissionError((2, "Writes are off on this probe"))
        if "tsize" in opts:
            try:
                want = int(opts["tsize"])
                if want > MAX_FILE or want > self.free() - (64 << 20):
                    raise PermissionError((3, "Not enough room"))
                rec["size"] = want or None
                oack["tsize"] = opts["tsize"]
            except ValueError:
                pass
        p = self.dir / name
        tmp = p.with_name(p.name + ".part")
        first = struct.pack("!H", OACK) + b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in oack.items()) if oack else struct.pack("!HH", ACK, 0)
        n = 1
        last = first
        with open(tmp, "wb") as f:
            tries = 0
            s.sendto(last, addr)
            while True:
                try:
                    d, a = s.recvfrom(blk + 4)
                except socket.timeout:
                    tries += 1
                    if tries > RETRIES:
                        raise TimeoutError("the switch stopped sending") from None
                    s.sendto(last, addr)
                    continue
                if a != addr or len(d) < 4:
                    continue
                code, b = struct.unpack("!HH", d[:4])
                if code == ERROR:
                    raise OSError("the switch cancelled: " + d[4:].split(b"\0")[0].decode("ascii", "replace"))
                if code != DATA:
                    continue
                if b == (n & 0xFFFF):
                    f.write(d[4:])
                    rec["bytes"] += len(d) - 4
                    if rec["bytes"] > MAX_FILE:
                        raise PermissionError((3, "File too big"))
                    last = struct.pack("!HH", ACK, b)
                    s.sendto(last, addr)
                    tries = 0
                    n += 1
                    if len(d) - 4 < blk:
                        break
                elif b == ((n - 1) & 0xFFFF):
                    s.sendto(last, addr)   # our ACK got lost: say it again
        os.replace(tmp, p)
        self.hash_later(name)

    # ---------- for the page ----------
    def status(self):
        if self.active:
            return {"dot": "live"}
        return {"dot": "on" if self.sock else ("warn" if self.conf["tftp"] else "")}

    def get(self, sub, arg, q, user):
        if sub == "file":
            p = self.path(arg)
            if not p.is_file():
                raise ValueError("not found")
            return Reply(p, "application/octet-stream", p.name)
        files = self.files()
        for f in files:
            if not f["md5"]:
                self.hash_later(f["name"])
        return {"files": files, "tftp": self.conf["tftp"], "allow_write": self.conf["allow_write"], "listening": bool(self.sock), "error": self.error,
                "active": list(self.active.values()), "log": list(self.log)[:30], "free": self.free(), "probe_ip": self.p.net.get("ip"),
                "neighbor": (self.p.neighbor or {}).get("switch"), "now": time.time()}

    def raw(self, action, stream, length, q, user):
        p = self.path(q.get("name"))
        if length <= 0:
            raise ValueError("Empty file")
        if length > MAX_FILE:
            raise ValueError("Files up to 512 MB")
        if length > self.free() - (64 << 20):
            raise ValueError("Not enough room on the card")
        tmp = p.with_name(p.name + ".part")
        left = length
        with open(tmp, "wb") as f:
            while left > 0:
                chunk = stream.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            tmp.unlink(missing_ok=True)
            raise ValueError("The upload was cut short")
        os.replace(tmp, p)
        self.hash_later(p.name)
        return {"name": p.name}

    def post(self, action, body, user):
        if action == "delete":
            p = self.path(body.get("name"))
            p.unlink(missing_ok=True)
            with self.lock:
                self.md5.pop(p.name, None)
                save_json(self.meta_file, self.md5)
            return {}
        if action == "rename":
            src, dst = self.path(body.get("name")), self.path(body.get("to"))
            if dst.exists():
                raise ValueError("A file with that name exists")
            shutil.move(src, dst)
            self.hash_later(dst.name)
            return {}
        return super().post(action, body, user)
