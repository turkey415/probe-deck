#!/usr/bin/env python3
"""Probe Deck: the web page and test loop for a wired network probe (Radxa Zero 3E, probe-01).

Standard library only. Runs as root (it reads CDP/LLDP, renews DHCP and listens on port 80).
- Port: link state, flaps, the switch and port from CDP/LLDP (lldpd), DHCP lease details.
- Checks: ping targets every 2 s, DNS (each server) and an HTTP internet check every cycle.
- Speed tests: iperf3 client against any server (Perf-Deck stations are listed), plus an
  iperf3 server so Perf-Deck can test the probe.
- Shows up in Perf-Deck: answers its discovery (UDP 52010) like a Perf-Deck station does.
- Probe message: broadcasts its results on UDP 47474 for the T-Displays and PorkChopCheck.
"""
import argparse
import collections
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__version__ = "0.1.0"
ROOT = Path(__file__).resolve().parent
CONF = Path("/etc/probe-deck/config.json")
DATA = Path("/var/lib/probe-deck")
IFACE = "eth0"
PERFDECK_TAG, PERFDECK_PORT = "perf-deck", 52010
PROBE_PORT = 47474
PING_EVERY = 2
HISTORY_MAX = 60

DEFAULTS = {
    "name": socket.gethostname(),
    "targets": "Gateway=GW,Cloudflare=1.1.1.1,Quad9=9.9.9.9",
    "interval": 30,
    "dns_name": "google.com",
    "http_url": "http://connectivitycheck.gstatic.com/generate_204",
    "iperf_server": "",
    "iperf_port": 5201,
    "iperf_secs": 10,
    "iperf_streams": 1,
    "iperf_listen": True,
    "perfdeck": True,
    "broadcast": True,
    "renew_on_link": True,
}

HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-:]{0,252}")


def run(argv, timeout=10):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def sysfs(name, default=""):
    try:
        return Path(f"/sys/class/net/{IFACE}/{name}").read_text().strip()
    except OSError:
        return default


def parse_targets(text):
    out = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        label, _, host = part.partition("=") if "=" in part else (part, "", part)
        label, host = label.strip()[:20], host.strip()
        if host.upper() == "GW":
            host = "GW"
        elif not HOST_RE.fullmatch(host):
            raise ValueError(f"Bad target: {part}")
        out.append({"label": label or host, "host": host})
    if len(out) > 8:
        raise ValueError("At most 8 targets")
    return out


class Probe:
    def __init__(self):
        self.lock = threading.Lock()
        self.conf = dict(DEFAULTS)
        try:
            self.conf.update(json.loads(CONF.read_text()))
        except (OSError, ValueError):
            pass
        mac = sysfs("address", "00:00:00:00:00:00")
        self.id = mac.replace(":", "")[-12:].lower()
        self.started = time.time()
        self.link = {"up": sysfs("carrier", "0") == "1", "since": self.started, "flaps": [], "ip_after": None}
        self.rates = collections.deque(maxlen=150)   # (t, rx_bps, tx_bps)
        self.pings = {}                              # label -> deque of (t, ms|None)
        self.checks = {"dns": [], "http": None, "at": None}
        self.neighbor = None
        self.net = {}
        self.speed = {"running": False, "phase": None, "points": [], "error": None}
        self.history = []
        self.peers = {}                              # Perf-Deck stations heard
        self.seq = 0
        self.renewing = False
        self.iperf_srv = None
        self.speed_proc = None
        self.wake = threading.Event()
        DATA.mkdir(parents=True, exist_ok=True)
        try:
            self.history = json.loads((DATA / "history.json").read_text())[-HISTORY_MAX:]
        except (OSError, ValueError):
            pass

    # ---------- settings ----------
    def targets(self):
        try:
            return parse_targets(self.conf["targets"])
        except ValueError:
            return parse_targets(DEFAULTS["targets"])

    def save_conf(self, new):
        c = dict(self.conf)
        if "name" in new:
            name = re.sub(r"[^\w .\-]", "", str(new["name"]))[:32].strip()
            if not name:
                raise ValueError("Name can't be empty")
            c["name"] = name
        if "targets" in new:
            parse_targets(new["targets"])
            c["targets"] = str(new["targets"])
        for k, lo, hi in (("interval", 10, 600), ("iperf_port", 1, 65535), ("iperf_secs", 1, 60), ("iperf_streams", 1, 8)):
            if k in new:
                v = int(new[k])
                if not lo <= v <= hi:
                    raise ValueError(f"{k} must be {lo}-{hi}")
                c[k] = v
        if "dns_name" in new:
            if not HOST_RE.fullmatch(str(new["dns_name"])):
                raise ValueError("Bad DNS name")
            c["dns_name"] = str(new["dns_name"])
        if "http_url" in new:
            if not re.fullmatch(r"https?://[^\s]{1,300}", str(new["http_url"])):
                raise ValueError("Internet check must be an http(s) URL")
            c["http_url"] = str(new["http_url"])
        if "iperf_server" in new:
            v = str(new["iperf_server"]).strip()
            if v and not HOST_RE.fullmatch(v):
                raise ValueError("Bad iperf3 server")
            c["iperf_server"] = v
        for k in ("iperf_listen", "perfdeck", "broadcast", "renew_on_link"):
            if k in new:
                c[k] = bool(new[k])
        with self.lock:
            self.conf = c
        CONF.parent.mkdir(parents=True, exist_ok=True)
        CONF.write_text(json.dumps(c, indent=2))
        with self.lock:
            self.pings = {k: v for k, v in self.pings.items() if k in {t["label"] for t in self.targets()}}
        self.wake.set()

    # ---------- link + traffic ----------
    def link_loop(self):
        last = None
        while True:
            now = time.time()
            up = sysfs("carrier", "0") == "1"
            rx, tx = int(sysfs("statistics/rx_bytes", "0") or 0), int(sysfs("statistics/tx_bytes", "0") or 0)
            with self.lock:
                if up != self.link["up"]:
                    if up:
                        self.link.update(up=True, since=now, ip_after=None)
                    else:
                        self.link.update(up=False, since=now, ip_after=None)
                        self.link["flaps"] = (self.link["flaps"] + [now])[-50:]
                    if up and self.conf["renew_on_link"] and self.started < now - 20:
                        threading.Thread(target=self.renew, daemon=True).start()
                    self.wake.set()
                elif self.link["since"] is None:
                    self.link["since"] = now
                if last:
                    dt = now - last[0]
                    self.rates.append((now, max(0, rx - last[1]) * 8 / dt, max(0, tx - last[2]) * 8 / dt))
            last = (now, rx, tx)
            time.sleep(1)

    def renew(self):
        """New jack, maybe a new VLAN: get a fresh DHCP lease."""
        if self.renewing:
            return
        self.renewing = True
        try:
            run(["ifdown", "--force", IFACE], 20)
            run(["ifup", IFACE], 60)
        finally:
            self.renewing = False
            self.read_net()
            self.wake.set()

    # ---------- addresses, lease, neighbor ----------
    def read_net(self):
        net = {"mac": sysfs("address"), "speed": None, "duplex": sysfs("duplex") or None, "mtu": sysfs("mtu")}
        try:
            sp = int(sysfs("speed", "-1"))
            net["speed"] = sp if sp > 0 else None
        except ValueError:
            pass
        try:
            addrs = json.loads(run(["ip", "-j", "-4", "addr", "show", IFACE]) or "[]")
            info = next((a for a in addrs[0]["addr_info"] if a.get("family") == "inet"), None) if addrs else None
        except (ValueError, KeyError, IndexError):
            info = None
        if info:
            net.update(ip=info["local"], prefix=info["prefixlen"], broadcast=info.get("broadcast"))
        try:
            routes = json.loads(run(["ip", "-j", "-4", "route", "show", "default", "dev", IFACE]) or "[]")
            net["gateway"] = routes[0].get("gateway") if routes else None
        except ValueError:
            net["gateway"] = None
        try:
            rc = Path("/etc/resolv.conf").read_text()
            net["dns"] = re.findall(r"^nameserver\s+(\S+)", rc, re.M)
            m = re.search(r"^(?:domain|search)\s+(\S+)", rc, re.M)
            net["domain"] = m.group(1).rstrip(".") if m else None
        except OSError:
            net["dns"] = []
        try:
            leases = Path(f"/var/lib/dhcp/dhclient.{IFACE}.leases").read_text().split("lease {")
            last = leases[-1] if len(leases) > 1 else ""
            g = lambda pat: (re.search(pat, last) or [None, None])[1]
            net["dhcp"] = {
                "server": g(r"dhcp-server-identifier\s+([\d.]+)"),
                "lease_secs": int(g(r"dhcp-lease-time\s+(\d+)") or 0) or None,
                "expires": g(r"expire \d+ ([\d/]+ [\d:]+)"),
                "renews": g(r"renew \d+ ([\d/]+ [\d:]+)"),
            } if last else None
        except OSError:
            net["dhcp"] = None
        with self.lock:
            # Time from cable-in to an address, only for links that came up while we were running.
            lk = self.link
            if (net.get("ip") and lk["up"] and lk["ip_after"] is None and lk["since"] and not self.renewing
                    and lk["since"] > self.started + 5):
                lk["ip_after"] = round(time.time() - lk["since"], 1)
            self.net = net

    def read_neighbor(self):
        try:
            data = json.loads(run(["lldpcli", "-f", "json", "show", "neighbors", "details"]) or "{}")
            ifs = data.get("lldp", {}).get("interface") or {}
            n = ifs.get(IFACE) if isinstance(ifs, dict) else next((x[IFACE] for x in ifs if IFACE in x), None)
        except (ValueError, AttributeError, TypeError, StopIteration):
            n = None
        if not n:
            with self.lock:
                self.neighbor = None
            return
        (sysname, ch), = n["chassis"].items() if len(n.get("chassis", {})) == 1 else [(None, {})]
        descr = ch.get("descr", "") if isinstance(ch, dict) else ""
        plat = re.match(r"cisco (\S+)", descr)
        ver = re.search(r"Version ([^,\s]+)", descr)
        vlan = n.get("vlan")
        if isinstance(vlan, list):
            vlan = vlan[0]
        port = n.get("port", {})
        mgmt = ch.get("mgmt-ip") if isinstance(ch, dict) else None
        with self.lock:
            self.neighbor = {
                "via": n.get("via"), "age": n.get("age"),
                "switch": sysname, "platform": plat.group(1) if plat else (descr.splitlines() or [None])[0],
                "version": ver.group(1) if ver else None,
                "mgmt": mgmt[0] if isinstance(mgmt, list) else mgmt,
                "port": (port.get("id") or {}).get("value"), "port_descr": port.get("descr"),
                "vlan": vlan.get("vlan-id") if isinstance(vlan, dict) else None,
            }

    # ---------- checks ----------
    def ping_loop(self):
        while True:
            t0 = time.time()
            gw = self.net.get("gateway")
            threads = []
            for t in self.targets():
                host = gw if t["host"] == "GW" else t["host"]
                th = threading.Thread(target=self.ping_one, args=(t["label"], host, t0), daemon=True)
                th.start()
                threads.append(th)
            for th in threads:
                th.join(3)
            time.sleep(max(0.2, PING_EVERY - (time.time() - t0)))

    def ping_one(self, label, host, t):
        ms = None
        if host and self.link["up"]:
            m = re.search(r"time[=<]([\d.]+) ms", run(["ping", "-n", "-c1", "-W1", "-I", IFACE, host], 3))
            ms = float(m.group(1)) if m else None
        with self.lock:
            self.pings.setdefault(label, collections.deque(maxlen=150)).append((t, ms))

    def ping_stats(self):
        out = []
        gw = self.net.get("gateway")
        with self.lock:
            for t in self.targets():
                pts = list(self.pings.get(t["label"], []))
                recent = pts[-30:]
                ok = [p[1] for p in recent if p[1] is not None]
                jit = [abs(a - b) for a, b in zip(ok, ok[1:])]
                out.append({
                    "label": t["label"], "host": gw if t["host"] == "GW" else t["host"], "gw": t["host"] == "GW",
                    "last": recent[-1][1] if recent else None,
                    "avg": round(sum(ok) / len(ok), 2) if ok else None,
                    "jitter": round(sum(jit) / len(jit), 2) if jit else None,
                    "loss": round(100 * (len(recent) - len(ok)) / len(recent), 1) if recent else None,
                    "pts": [[round(p[0], 1), p[1]] for p in pts],
                })
        return out

    def check_loop(self):
        while True:
            self.read_net()
            self.read_neighbor()
            if self.link["up"]:
                self.run_checks()
            self.wake.wait(self.conf["interval"])
            self.wake.clear()
            time.sleep(2)  # let a fresh link settle

    def run_checks(self):
        name = self.conf["dns_name"]
        dns = []
        for srv in self.net.get("dns", [])[:4]:
            out = run(["dig", "+time=2", "+tries=1", f"@{srv}", name, "A"], 6)
            st = re.search(r"status: (\w+)", out)
            qt = re.search(r"Query time: (\d+) msec", out)
            ans = re.findall(r"\sIN\s+A\s+([\d.]+)", out)
            dns.append({"server": srv, "ok": bool(st and st.group(1) == "NOERROR" and ans),
                        "status": st.group(1) if st else "no answer", "ms": int(qt.group(1)) if qt else None,
                        "answer": ans[0] if ans else None})
        http = {"url": self.conf["http_url"], "ok": False, "ms": None, "code": None, "error": None}
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.conf["http_url"], headers={"User-Agent": "probe-deck"}), timeout=5) as r:
                http.update(code=r.status, ok=200 <= r.status < 400)
        except Exception as e:  # any failure is the result
            http["error"] = str(getattr(e, "reason", e))[:80]
        http["ms"] = round((time.perf_counter() - t0) * 1000)
        with self.lock:
            self.checks = {"dns": dns, "dns_name": name, "http": http, "at": time.time()}

    # ---------- speed tests ----------
    def iperf_server_loop(self):
        while True:
            if self.conf["iperf_listen"] and not self.iperf_srv:
                try:
                    self.iperf_srv = subprocess.Popen(["iperf3", "-s", "-p", "5201"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError:
                    pass
            elif self.iperf_srv and (not self.conf["iperf_listen"] or self.iperf_srv.poll() is not None):
                if self.iperf_srv.poll() is None:
                    self.iperf_srv.terminate()
                self.iperf_srv = None
                continue
            time.sleep(2)

    def start_speed(self, opts):
        server = str(opts.get("server") or self.conf["iperf_server"]).strip()
        if not server or not HOST_RE.fullmatch(server):
            raise ValueError("Pick an iperf3 server")
        mode = opts.get("mode", "both")
        if mode not in ("both", "up", "down"):
            raise ValueError("Bad mode")
        with self.lock:
            if self.speed["running"]:
                raise ValueError("A test is already running")
            self.speed = {"running": True, "phase": None, "points": [], "error": None, "server": server, "mode": mode,
                          "started": time.time(), "result": {}}
        if server != self.conf["iperf_server"]:
            try:
                self.save_conf({"iperf_server": server})
            except ValueError:
                pass
        threading.Thread(target=self.speed_run, args=(server, mode), daemon=True).start()

    def speed_run(self, server, mode):
        phases = {"both": ["down", "up"], "up": ["up"], "down": ["down"]}[mode]
        res = {}
        for ph in phases:
            if not self.speed["running"]:
                break
            with self.lock:
                self.speed["phase"] = ph
            r = self.iperf_once(server, ph)
            if r is None:
                break
            res[ph] = r
            with self.lock:
                self.speed["result"] = dict(res)
        with self.lock:
            sp = self.speed
            sp["running"], sp["phase"] = False, None
            if res:
                entry = {"t": round(sp["started"]), "server": server, "secs": self.conf["iperf_secs"],
                         "streams": self.conf["iperf_streams"], **res,
                         "port": (self.neighbor or {}).get("port"), "switch": (self.neighbor or {}).get("switch")}
                self.history = (self.history + [entry])[-HISTORY_MAX:]
                (DATA / "history.json").write_text(json.dumps(self.history))

    def iperf_once(self, server, phase):
        argv = ["iperf3", "-c", server, "-p", str(self.conf["iperf_port"]), "-t", str(self.conf["iperf_secs"]),
                "-P", str(self.conf["iperf_streams"]), "--json-stream", "--forceflush", "--connect-timeout", "4000"]
        if phase == "down":
            argv.append("-R")
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError as e:
            with self.lock:
                self.speed["error"] = str(e)
            return None
        self.speed_proc = proc
        out = None
        t0 = time.time()
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind, data = ev.get("event"), ev.get("data")
            if kind == "interval":
                s = data.get("sum", {})
                with self.lock:
                    self.speed["points"].append([phase, round(time.time() - t0, 1), s.get("bits_per_second", 0)])
            elif kind == "end":
                sent, recv = data.get("sum_sent", {}), data.get("sum_received", {})
                out = {"bps": recv.get("bits_per_second") or sent.get("bits_per_second"), "retr": sent.get("retransmits"),
                       "bytes": recv.get("bytes")}
            elif kind == "error":
                with self.lock:
                    self.speed["error"] = str(data)[:200]
        proc.wait()
        self.speed_proc = None
        return out

    def stop_speed(self):
        with self.lock:
            self.speed["running"] = False
        p = self.speed_proc
        if p and p.poll() is None:
            p.terminate()

    # ---------- Perf-Deck discovery + probe broadcast ----------
    def start_udp(self):
        self.dsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.dsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.dsock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.dsock.bind(("", PERFDECK_PORT))
        self.psock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.psock.bind(("", 0))
        for fn in (self.disc_listen, self.disc_pong, self.beacon_loop):
            threading.Thread(target=fn, daemon=True).start()

    def disc_listen(self):
        while True:
            try:
                data, addr = self.dsock.recvfrom(2048)
                m = json.loads(data) if len(data) < 1024 else None
            except (OSError, ValueError):
                continue
            if not isinstance(m, dict) or m.get("app") != PERFDECK_TAG or m.get("t") != "hello" or m.get("id") == self.id:
                continue
            port = m.get("iperf")
            with self.lock:
                if len(self.peers) < 64 or addr[0] in self.peers:
                    self.peers[addr[0]] = {"ip": addr[0], "name": re.sub(r"[^\w .\-]", "", str(m.get("name") or ""))[:40] or addr[0],
                                           "port": port if isinstance(port, int) else 5201, "running": bool(m.get("running")),
                                           "seen": time.time()}

    def disc_pong(self):
        while True:
            try:
                data, addr = self.psock.recvfrom(2048)
                m = json.loads(data)
            except (OSError, ValueError):
                continue
            if isinstance(m, dict) and m.get("app") == PERFDECK_TAG and m.get("t") == "ping" and isinstance(m.get("n"), str) and len(m["n"]) <= 32:
                try:
                    self.psock.sendto(json.dumps({"app": PERFDECK_TAG, "t": "pong", "n": m["n"]}).encode(), addr)
                except OSError:
                    pass

    def beacon_loop(self):
        n = 0
        while True:
            bc = self.net.get("broadcast")
            if bc and self.link["up"]:
                if self.conf["perfdeck"]:
                    hello = {"app": PERFDECK_TAG, "t": "hello", "id": self.id, "name": self.conf["name"],
                             "iperf": 5201, "ping": self.psock.getsockname()[1],
                             "running": bool(self.conf["iperf_listen"] and self.iperf_srv),
                             "busy": self.speed["running"], "sweeping": False, "sweep_ago": None,
                             "version": __version__, "os": "linux"}
                    self.send(self.dsock, hello, bc, PERFDECK_PORT)
                if self.conf["broadcast"] and n % 3 == 0:
                    self.send(self.dsock, self.probe_message(), bc, PROBE_PORT)
            with self.lock:
                now = time.time()
                self.peers = {k: v for k, v in self.peers.items() if now - v["seen"] < 12}
            n += 1
            time.sleep(3)

    @staticmethod
    def send(sock, msg, host, port):
        try:
            sock.sendto(json.dumps(msg, separators=(",", ":")).encode(), (host, port))
        except OSError:
            pass

    def probe_message(self):
        """The shared probe message (see t-display-progs TODO.md). Keep it small."""
        self.seq += 1
        nb, net, ps = self.neighbor or {}, self.net, self.ping_stats()
        gw = next((p for p in ps if p["gw"]), None)
        last = self.history[-1] if self.history else {}
        return {
            "app": "probe", "v": 1, "id": self.id, "name": self.conf["name"], "seq": self.seq,
            "up": round(time.time() - self.started), "kind": "gigabit",
            "link": {"up": self.link["up"], "speed": net.get("speed"), "duplex": net.get("duplex"), "flaps": len(self.link["flaps"])},
            "cdp": {"sw": nb.get("switch"), "port": nb.get("port"), "vlan": nb.get("vlan"), "via": nb.get("via")} if nb else None,
            "ip": net.get("ip"), "gw": {"ok": bool(gw and gw["last"] is not None), "ms": gw and gw["avg"], "loss": gw and gw["loss"]},
            "dns": all(d["ok"] for d in self.checks["dns"]) if self.checks["dns"] else None,
            "inet": (self.checks["http"] or {}).get("ok"),
            "speed": {"down": (last.get("down") or {}).get("bps"), "up": (last.get("up") or {}).get("bps")} if last else None,
        }

    # ---------- state for the page ----------
    def system(self):
        try:
            temp = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
        except (OSError, ValueError):
            temp = None
        try:
            up = float(Path("/proc/uptime").read_text().split()[0])
            load = Path("/proc/loadavg").read_text().split()[:3]
            mem = dict(re.findall(r"(\w+):\s+(\d+)", Path("/proc/meminfo").read_text()))
        except OSError:
            up, load, mem = None, [], {}
        return {"host": socket.gethostname(), "version": __version__, "uptime": up, "temp": temp, "load": load,
                "mem_used": (int(mem.get("MemTotal", 0)) - int(mem.get("MemAvailable", 0))) // 1024,
                "mem_total": int(mem.get("MemTotal", 0)) // 1024}

    def snapshot(self):
        pings = self.ping_stats()
        with self.lock:
            return {
                "id": self.id, "now": time.time(), "started": self.started, "conf": self.conf, "system": self.system(),
                "link": {**self.link, "flaps": len(self.link["flaps"]), "last_flap": self.link["flaps"][-1] if self.link["flaps"] else None,
                         "renewing": self.renewing},
                "net": self.net, "neighbor": self.neighbor, "pings": pings, "checks": self.checks,
                "rates": [[round(t, 1), round(r), round(x)] for t, r, x in self.rates],
                "speed": self.speed, "history": self.history[::-1],
                "peers": sorted(self.peers.values(), key=lambda p: p["name"].lower()),
                "iperf_server": bool(self.iperf_srv and self.iperf_srv.poll() is None),
            }


class Handler(BaseHTTPRequestHandler):
    probe = None
    server_version = "ProbeDeck"

    def log_message(self, *args):
        pass

    def send_body(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self.send_body(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self.send_body(200, self.probe.snapshot())
        if path == "/api/probe":
            return self.send_body(200, self.probe.probe_message())
        self.send_body(404, {"error": "not found"})

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and re.sub(r"^https?://", "", origin) != self.headers.get("Host"):
            return self.send_body(403, {"error": "cross-site request"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 65536)) or b"{}") if n else {}
            path = self.path.split("?")[0]
            p = self.probe
            if path == "/api/settings":
                p.save_conf(body)
            elif path == "/api/speed/start":
                p.start_speed(body)
            elif path == "/api/speed/stop":
                p.stop_speed()
            elif path == "/api/recheck":
                p.wake.set()
            elif path == "/api/renew":
                threading.Thread(target=p.renew, daemon=True).start()
            elif path == "/api/history/clear":
                with p.lock:
                    p.history = []
                (DATA / "history.json").write_text("[]")
            else:
                return self.send_body(404, {"error": "not found"})
        except (ValueError, TypeError) as e:
            return self.send_body(400, {"error": str(e)})
        self.send_body(200, {"ok": True})


def main():
    ap = argparse.ArgumentParser(description="Probe Deck")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--listen", default="0.0.0.0")
    a = ap.parse_args()
    probe = Probe()
    Handler.probe = probe
    probe.read_net()
    for fn in (probe.link_loop, probe.ping_loop, probe.check_loop, probe.iperf_server_loop):
        threading.Thread(target=fn, daemon=True).start()
    probe.start_udp()
    srv = ThreadingHTTPServer((a.listen, a.port), Handler)
    srv.daemon_threads = True
    print(f"Probe Deck {__version__} on http://{a.listen}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
