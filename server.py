#!/usr/bin/env python3
"""Probe Deck: the web page and test loop for a wired network probe (Radxa Zero 3E, probe-01).

Standard library only. Runs as root (it reads CDP/LLDP, renews DHCP and listens on port 80).
- Port: link state, flaps, the switch and port from CDP/LLDP (lldpd), DHCP lease details.
- Checks: ping targets every 2 s, DNS (each server) and an HTTP internet check every cycle.
- Speed tests: iperf3 client against any server (Perf-Deck stations are listed), plus an
  iperf3 server so Perf-Deck can test the probe.
- Shows up in Perf-Deck: answers its discovery (UDP 52010) like a Perf-Deck station does.
- Probe message: broadcasts its results on UDP 47474 for the T-Displays and PorkChopCheck.
- Console: a web terminal (and optional telnet port) on a USB serial cable to a switch console.
- Watchdog: rogue DHCP, IP conflicts, broadcast storms, spanning tree changes, IPv6 RAs.
- Wake: Wake-on-LAN for saved machines, with a staggered "wake all".
"""
import argparse
import collections
import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.request
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import auth as auth_mod
import console as console_mod
import cs as cs_mod
import tailscale as ts_mod
import netmap as netmap_mod
import tls as tls_mod
import wake as wake_mod
import watchdog as watch_mod
import ws as ws_mod
import appbase
import logs as logs_mod
import inet as inet_mod
import hosts as hosts_mod
import cache as cache_mod
import capture as capture_mod
import tftp as tftp_mod
import configs as configs_mod
import cable as cable_mod
import vlanwalk as vlanwalk_mod
import survey as survey_mod
import wire as wire_mod
import online as online_mod
import fwmon as fw_mod
import tick as tick_mod
import stats as stats_mod
import mtr as mtr_mod
import dnstest as dnstest_mod
import fair as fair_mod
import swcli as swcli_mod

__version__ = "0.41.2"
ROOT = Path(__file__).resolve().parent
CONF = Path("/etc/probe-deck/config.json")
DATA = Path("/var/lib/probe-deck")
IFACE = "eth0"
PERFDECK_TAG, PERFDECK_PORT = "perf-deck", 52010
PROBE_PORT = 47474
PING_EVERY = 2
QUICK_SECS = 5            # per-station quick test
SWEEP_SECS = 2            # per-station sweep test (same as Perf-Deck)
SWEEP_COOLDOWN = 300      # Perf-Deck's base recharge after anyone's sweep
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
    "eee_off": True,
    **console_mod.DEFAULTS,
    **watch_mod.DEFAULTS,
    **wake_mod.DEFAULTS,
    **tls_mod.DEFAULTS,
    **netmap_mod.DEFAULTS,
    **cs_mod.DEFAULTS,
    "ts_geoip": False,
}
HTTPS_PORT = 443
PLAIN_OK = ("/api/probe",)
OPEN_GET = ("/", "/index.html", "/api/probe", "/api/auth")      # no login needed (the page shell, the displays, login state)
OPEN_POST = ("/api/auth/login", "/api/auth/setup", "/api/auth/logout")
ANY_USER_POST = ("/api/auth/password", "/api/trace", "/api/cs/scan")          # viewers may change their own password and run traces  # the displays poll this over plain HTTP (an ESP32 can't check a self-signed cert)
VENDOR = {"xterm.js": "text/javascript", "xterm.css": "text/css", "addon-fit.js": "text/javascript", "us-map.json": "application/json"}

APP_RE = re.compile(r"/api/x/([a-z]+)(?:/([\w.\-]+))?(?:/([\w.\-]+))?")
WEB_RE = re.compile(r"/web/([a-z0-9_\-]+\.(js|css))")
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
        self.peers = {}                              # Perf-Deck stations heard, by id
        self.peer_pings = {}                         # nonce -> (peer id, sent)
        self.sweep = {"running": False, "results": [], "started": None, "ended": None, "cancel": False}
        self.seq = 0
        self.renewing = False
        self.iperf_srv = None
        self.speed_proc = None
        self.wake_ev = threading.Event()
        self.watch = self.wake = self.console = self.tls = self.auth = self.netmap = self.cs = None
        self.logins = None
        self.apps = {}                               # add-on pages by name (see appbase.py)
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

    def save_conf(self, new, internal=False):
        """internal=True is for the console module, whose settings sit behind the console password."""
        c = dict(self.conf)
        if internal:
            c.update({k: v for k, v in new.items() if k.startswith(("console_", "https_"))})
            new = {}
        for k in ("watch_scan", "watch_storm"):
            if k in new:
                v = int(new[k])
                lo, hi = (0, 3600) if k == "watch_scan" else (50, 100000)
                if not lo <= v <= hi:
                    raise ValueError(f"{k} must be {lo}-{hi}")
                if k == "watch_scan" and 0 < v < 10:
                    raise ValueError("DHCP scan: 0 (off) or at least 10 s")
                c[k] = v
        if "watch_trusted" in new:
            c["watch_trusted"] = ", ".join(watch_mod.norm_trusted(new["watch_trusted"]))
        if "cs_servers" in new:
            cs_mod.parse_servers(new["cs_servers"])
            c["cs_servers"] = str(new["cs_servers"])[:2000]
        if "cs_group" in new:
            c["cs_group"] = re.sub(r"[^\w .\-#]", "", str(new["cs_group"] or ""))[:20]
        if "snmp_community" in new:
            c["snmp_community"] = netmap_mod.NetMap.check_community(new["snmp_community"])
        if "snmp_version" in new:
            if int(new["snmp_version"]) not in (1, 2):
                raise ValueError("SNMP version must be 1 or 2c")
            c["snmp_version"] = int(new["snmp_version"])
        if "wol_stagger" in new:
            v = float(new["wol_stagger"])
            if not 0 <= v <= 60:
                raise ValueError("Stagger must be 0-60 s")
            c["wol_stagger"] = v
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
        for k in ("iperf_listen", "perfdeck", "broadcast", "renew_on_link", "eee_off", "watch", "cs_nodes", "ts_geoip"):
            if k in new:
                c[k] = bool(new[k])
        eee_changed = "eee_off" in new and bool(new["eee_off"]) != bool(self.conf.get("eee_off"))
        with self.lock:
            self.conf = c
        if eee_changed:
            run(["ethtool", "--set-eee", IFACE, "eee", "off" if c["eee_off"] else "on"], 10)
        CONF.parent.mkdir(parents=True, exist_ok=True)
        CONF.write_text(json.dumps(c, indent=2))
        os.chmod(CONF, 0o600)  # holds the SNMP community
        with self.lock:
            self.pings = {k: v for k, v in self.pings.items() if k in {t["label"] for t in self.targets()}}
        self.wake_ev.set()

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
                    if up:
                        threading.Thread(target=self.eee_off, daemon=True).start()
                    if up and self.conf["renew_on_link"] and self.started < now - 20:
                        threading.Thread(target=self.renew, daemon=True).start()
                    self.wake_ev.set()
                elif self.link["since"] is None:
                    self.link["since"] = now
                if last:
                    dt = now - last[0]
                    self.rates.append((now, max(0, rx - last[1]) * 8 / dt, max(0, tx - last[2]) * 8 / dt))
            last = (now, rx, tx)
            time.sleep(1)

    def eee_off(self):
        """Energy Efficient Ethernet lets the link sleep between packets: a wake-up delay a test probe
        shouldn't add. Switched off at start and after every link-up (renegotiation can turn it back on)."""
        if not self.conf.get("eee_off"):
            return
        # only when it's on: changing it makes the link renegotiate, so never touch a link that's already right
        if re.search(r"EEE status:\s*enabled", run(["ethtool", "--show-eee", IFACE], 10)):
            run(["ethtool", "--set-eee", IFACE, "eee", "off"], 10)

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
            self.wake_ev.set()

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
            self.wake_ev.wait(self.conf["interval"])
            self.wake_ev.clear()
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
        port = int(opts.get("port") or self.conf["iperf_port"])
        secs = int(opts.get("secs") or self.conf["iperf_secs"])
        if not (0 < port < 65536 and 1 <= secs <= 60):
            raise ValueError("Bad port or length")
        with self.lock:
            if self.speed["running"]:
                raise ValueError("A test is already running")
            if self.sweep["running"]:
                raise ValueError("Wait for the sweep to finish")
            self.speed = {"running": True, "phase": None, "points": [], "error": None, "server": server, "mode": mode,
                          "port": port, "secs": secs, "label": str(opts.get("label") or server)[:40],
                          "started": time.time(), "result": {}}
        if not opts.get("quick") and server != self.conf["iperf_server"]:
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
            for _ in range(3):  # the far server restarts between tests (iperf3 -s -1): give it a moment
                err = str(self.speed.get("error") or "")
                if r is not None or not self.speed["running"] or not re.search(r"unable to connect|busy|refused|reset|control message", err):
                    break
                time.sleep(1.5)
                with self.lock:
                    self.speed["error"] = None
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
                entry = {"t": round(sp["started"]), "server": server, "label": sp.get("label"), "secs": sp["secs"],
                         "streams": self.conf["iperf_streams"], **res, "pts": sp["points"][:400],
                         "port": (self.neighbor or {}).get("port"), "switch": (self.neighbor or {}).get("switch")}
                far = next((p for p in self.peers.values() if p["ip"] == server and p.get("probe")), None)
                if far:
                    entry["far"] = far.get("jack") or {}
                self.history = (self.history + [entry])[-HISTORY_MAX:]
                for p in self.peers.values():
                    if p["ip"] == server:
                        p["last"] = {"bps": (res.get("down") or res.get("up"))["bps"], "dir": next(iter(res)), "t": time.time(),
                                     "down": (res.get("down") or {}).get("bps"), "up": (res.get("up") or {}).get("bps")}
                (DATA / "history.json").write_text(json.dumps(self.history))

    def iperf_once(self, server, phase):
        argv = ["iperf3", "-c", server, "-p", str(self.speed["port"]), "-t", str(self.speed["secs"]),
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
                sts = data.get("streams") or []
                rtts = [x["rtt"] for x in sts if x.get("rtt")]          # sender side only: the upload
                cwnd = [x["snd_cwnd"] for x in sts if x.get("snd_cwnd")]
                with self.lock:
                    self.speed["points"].append([phase, round(time.time() - t0, 1), s.get("bits_per_second", 0), s.get("retransmits"),
                                                 round(sum(rtts) / len(rtts) / 1000, 2) if rtts else None,
                                                 round(sum(cwnd) / 1024) if cwnd else None])
            elif kind == "end":
                sent, recv = data.get("sum_sent", {}), data.get("sum_received", {})
                out = {"bps": recv.get("bits_per_second") or sent.get("bits_per_second"), "retr": sent.get("retransmits"),
                       "bytes": recv.get("bytes")}
            elif kind == "error":
                with self.lock:
                    self.speed["error"] = str(data)[:200]
        proc.wait()
        self.speed_proc = None
        return out if out and out.get("bps") else None   # a failed run still sends an "end" with empty sums

    def stop_speed(self):
        with self.lock:
            self.speed["running"] = False
        p = self.speed_proc
        if p and p.poll() is None:
            p.terminate()

    # ---------- sweep: a short test against every station ----------
    def sweep_wait(self):
        """Seconds to wait before a sweep, and why. Follows Perf-Deck's rules so stations don't pile on."""
        now = time.time()
        with self.lock:
            for p in self.peers.values():
                if p["sweeping"]:
                    return {"left": None, "reason": "busy", "by": p["name"]}
            best = {"left": 0, "reason": None, "by": None}
            if self.sweep["ended"]:
                best = {"left": max(0, self.sweep["ended"] + SWEEP_COOLDOWN - now), "reason": "own", "by": None}
            for p in self.peers.values():
                if p.get("swept") and SWEEP_COOLDOWN - (now - p["swept"]) > best["left"]:
                    best = {"left": SWEEP_COOLDOWN - (now - p["swept"]), "reason": "lan", "by": p["name"]}
            return best if best["left"] > 0 else {"left": 0, "reason": None, "by": None}

    def start_sweep(self):
        w = self.sweep_wait()
        if w["reason"] == "busy":
            raise ValueError(f"{w['by']} is sweeping right now")
        if w["left"]:
            raise ValueError(f"Recharging: {int(w['left'])} s left")
        with self.lock:
            if self.speed["running"] or self.sweep["running"]:
                raise ValueError("Wait for the running test to finish")
            targets = [p for p in self.peers.values() if p["running"]]
            if not targets:
                raise ValueError("No stations with a running iperf3 server")
            self.sweep = {"running": True, "cancel": False, "started": time.time(), "ended": None,
                          "results": [{"id": p["id"], "name": p["name"], "ip": p["ip"], "port": p["port"], "status": "queued",
                                       "bps": None, "retr": None, "error": None} for p in sorted(targets, key=lambda p: p["name"].lower())]}
        threading.Thread(target=self.sweep_run, daemon=True).start()

    def sweep_run(self):
        try:
            for attempt in (1, 2):  # stations that were busy get one more try
                for r in self.sweep["results"]:
                    if r["status"] in ("queued", "busy") and not self.sweep["cancel"]:
                        if attempt == 2:
                            time.sleep(1)
                        self.sweep_one(r)
            for r in self.sweep["results"]:
                if r["status"] == "queued":
                    r["status"] = "skipped"
        finally:
            with self.lock:
                self.sweep["running"], self.sweep["ended"] = False, time.time()

    def sweep_one(self, r):
        r["status"] = "testing"
        # Reverse: the station sends, so this is its speed to the probe (as in Perf-Deck).
        argv = ["iperf3", "-c", r["ip"], "-p", str(r["port"]), "-t", str(SWEEP_SECS), "-R", "-J", "--connect-timeout", "2000"]
        try:
            data = json.loads(run(argv, SWEEP_SECS + 10) or "{}")
            if data.get("error"):
                raise ValueError(str(data["error"]).removeprefix("error - "))
            end = data["end"]
            r.update(status="done", bps=end["sum_received"]["bits_per_second"], retr=end["sum_sent"].get("retransmits"))
        except (ValueError, KeyError, TypeError) as e:
            r.update(status="busy" if "busy" in str(e).lower() else "failed", error=str(e)[:160] or "No result")
        with self.lock:
            p = self.peers.get(r["id"])
            if p and r["bps"]:
                p["last"] = {"bps": r["bps"], "dir": "down", "t": time.time()}

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
            pid, port, pport, ago = m.get("id"), m.get("iperf"), m.get("ping"), m.get("sweep_ago")
            if not isinstance(pid, str) or not re.fullmatch(r"[0-9a-f]{12}", pid):
                continue
            if not all(isinstance(v, int) and 0 < v < 65536 for v in (port, pport)):
                continue
            now = time.time()
            with self.lock:
                if pid not in self.peers and len(self.peers) >= 64:
                    continue
                self.peers.setdefault(pid, {"id": pid, "ping_ms": None, "rtts": [], "last": None}).update(
                    ip=addr[0], name=re.sub(r"[^\w .\-]", "", str(m.get("name") or ""))[:40] or addr[0],
                    port=port, ping_port=pport, seen=now, running=bool(m.get("running")), busy=bool(m.get("busy")),
                    sweeping=bool(m.get("sweeping")), version=re.sub(r"[^\w.\-]", "", str(m.get("version") or ""))[:20],
                    os=re.sub(r"[^\w]", "", str(m.get("os") or ""))[:12],
                    probe=m.get("probe") is True, jack=self.clean_jack(m.get("jack")),
                    swept=now - ago if isinstance(ago, (int, float)) and ago >= 0 else None)

    def disc_pong(self):
        while True:
            try:
                data, addr = self.psock.recvfrom(2048)
                m = json.loads(data)
            except (OSError, ValueError):
                continue
            if not (isinstance(m, dict) and m.get("app") == PERFDECK_TAG and isinstance(m.get("n"), str) and len(m["n"]) <= 32):
                continue
            if m.get("t") == "ping":
                try:
                    self.psock.sendto(json.dumps({"app": PERFDECK_TAG, "t": "pong", "n": m["n"]}).encode(), addr)
                except OSError:
                    pass
            elif m.get("t") == "pong":
                with self.lock:
                    sent = self.peer_pings.pop(m["n"], None)
                    peer = sent and self.peers.get(sent[0])
                    if peer and peer["ip"] == addr[0]:
                        # Best of the last few, like Perf-Deck: one sample carries scheduling jitter.
                        peer["rtts"] = (peer["rtts"] + [(time.perf_counter() - sent[1]) * 1000])[-5:]
                        peer["ping_ms"] = round(min(peer["rtts"]), 2)

    def beacon_loop(self):
        n = 0
        while True:
            bc = self.net.get("broadcast")
            if bc and self.link["up"]:
                if self.conf["perfdeck"]:
                    hello = {"app": PERFDECK_TAG, "t": "hello", "id": self.id, "name": self.conf["name"],
                             "iperf": 5201, "ping": self.psock.getsockname()[1],
                             "running": bool(self.conf["iperf_listen"] and self.iperf_srv),
                             "busy": self.speed["running"] or self.sweep["running"], "sweeping": self.sweep["running"],
                             "sweep_ago": round(time.time() - self.sweep["ended"]) if self.sweep["ended"] else None,
                             "version": __version__, "os": "linux", "probe": True, "jack": self.jack()}
                    self.send(self.dsock, hello, bc, PERFDECK_PORT)
                if self.conf["broadcast"] and n % 3 == 0:
                    self.send(self.dsock, self.probe_message(), bc, PROBE_PORT)
            with self.lock:
                now = time.time()
                self.peers = {k: v for k, v in self.peers.items() if now - v["seen"] < 12}
                self.peer_pings = {k: v for k, v in self.peer_pings.items() if time.perf_counter() - v[1] < 10}
                peers = list(self.peers.values())
            for p in peers:
                nonce = uuid.uuid4().hex[:16]
                with self.lock:
                    self.peer_pings[nonce] = (p["id"], time.perf_counter())
                self.send(self.psock, {"app": PERFDECK_TAG, "t": "ping", "n": nonce}, p["ip"], p["ping_port"])
            n += 1
            time.sleep(3)

    def jack(self):
        """Where this probe is plugged in, for the hello: other probes show it next to our name."""
        nb = self.neighbor or {}
        if not nb.get("switch"):
            return None
        return {"sw": str(nb["switch"])[:40], "port": str(nb.get("port") or "")[:32],
                "vlan": str(nb.get("vlan") or "")[:8], "speed": self.net.get("speed")}

    @staticmethod
    def clean_jack(j):
        """A jack from another probe's hello, kept only if it is the shape we send."""
        if not isinstance(j, dict) or not isinstance(j.get("sw"), str):
            return None
        txt = lambda v, n: re.sub(r"[^\w .:/\-]", "", v if isinstance(v, str) else "")[:n]
        sp = j.get("speed")
        return {"sw": txt(j["sw"], 40), "port": txt(j.get("port"), 32), "vlan": txt(j.get("vlan"), 8),
                "speed": sp if isinstance(sp, int) and 0 < sp <= 400000 else None}

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
            "cdp": {"sw": nb.get("switch"), "port": nb.get("port"), "vlan": nb.get("vlan"), "via": nb.get("via"),
                    "plat": nb.get("platform"), "mgmt": nb.get("mgmt")} if nb else None,
            "ip": net.get("ip"), "ipt": self.link["ip_after"], "web": 80, "temp": self.system()["temp"],
            "gw": {"ok": bool(gw and gw["last"] is not None), "ms": gw and gw["avg"], "loss": gw and gw["loss"]},
            "dns": all(d["ok"] for d in self.checks["dns"]) if self.checks["dns"] else None,
            "inet": (self.checks["http"] or {}).get("ok"),
            "speed": {"down": (last.get("down") or {}).get("bps"), "up": (last.get("up") or {}).get("bps")} if last else None,
            "alerts": self.watch.active()["total"] if self.watch else None,
            **({"sv": sv} if (sv := self.survey_message()) else {}),
        }

    def survey_message(self):
        a = self.apps.get("survey")
        try:
            return a.message() if a else None
        except Exception:
            return None

    def on_alert(self, ev):
        """Watchdog problems go out at once, as their own message (the displays ignore apps they don't know)."""
        for a in list(self.apps.values()):
            if hasattr(a, "on_alert"):
                try:
                    a.on_alert(ev)
                except Exception as e:  # an add-on must never stop the alert
                    print(f"{a.name} on_alert: {e}", flush=True)
        bc = self.net.get("broadcast")
        if bc and self.conf["broadcast"] and self.link["up"]:
            msg = {"app": "probe-alert", "v": 1, "id": self.id, "name": self.conf["name"], "ip": self.net.get("ip"),
                   "sev": ev["sev"], "kind": ev["kind"], "msg": ev["msg"][:200], "t": ev["t"]}
            self.send(self.dsock, msg, bc, PROBE_PORT)

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
        wait = self.sweep_wait()
        with self.lock:
            return {
                "id": self.id, "now": time.time(), "started": self.started, "system": self.system(),
                "conf": {k: v for k, v in self.conf.items() if not k.startswith("console_") and k != "snmp_community"},
                "link": {**self.link, "flaps": len(self.link["flaps"]), "last_flap": self.link["flaps"][-1] if self.link["flaps"] else None,
                         "renewing": self.renewing},
                "net": self.net, "neighbor": self.neighbor, "pings": pings, "checks": self.checks,
                "rates": [[round(t, 1), round(r), round(x)] for t, r, x in self.rates],
                "speed": self.speed, "history": self.history[::-1],
                "peers": sorted(({k: v for k, v in p.items() if k not in ("rtts", "ping_port")} for p in self.peers.values()),
                                key=lambda p: (p["name"].lower(), p["ip"])),
                "sweep": {**{k: v for k, v in self.sweep.items() if k != "cancel"}, "wait": wait, "secs": SWEEP_SECS},
                "iperf_server": bool(self.iperf_srv and self.iperf_srv.poll() is None),
                "watch": self.watch.snapshot() if self.watch else None,
                "wol": self.wake.snapshot() if self.wake else None,
                "console": self.console.status() if self.console else None,
                "apps": self.app_status(),
            }

    def app_status(self):
        out = {}
        for k, a in list(self.apps.items()):
            try:
                out[k] = a.status()
            except Exception:
                out[k] = None
        return out


class Handler(BaseHTTPRequestHandler):
    probe = None
    server_version = "ProbeDeck"

    def log_message(self, *args):
        pass

    def send_body(self, code, body, ctype="application/json", headers=()):
        if isinstance(body, Path):  # a file: stream it, don't read it all into memory
            return self.send_file(code, body, ctype, headers)
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, code, path, ctype, headers):
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def is_https(self):
        return isinstance(self.connection, ssl.SSLSocket)

    def to_https(self, code):
        """Send plain-HTTP visitors to the HTTPS page. True when it answered."""
        p = self.probe
        if self.is_https() or not p.conf.get("https_redirect") or not (p.tls and p.tls.ctx):
            return False
        if self.path.split("?")[0] in PLAIN_OK:
            return False
        host = re.sub(r":\d+$", "", self.headers.get("Host") or p.net.get("ip") or "")
        self.send_response(code)
        self.send_header("Location", f"https://{host}{'' if HTTPS_PORT == 443 else ':' + str(HTTPS_PORT)}{self.path}")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def tls_info(self):
        p = self.probe
        return {"info": p.tls.info(), "error": p.tls.error, "redirect": p.conf.get("https_redirect"), "https": self.is_https(),
                "port": HTTPS_PORT, "admin": self.user["role"] == "admin",
                "default_names": ", ".join(p.tls.default_names())}

    def same_origin(self):
        origin = self.headers.get("Origin")
        return not origin or re.sub(r"^https?://", "", origin) == self.headers.get("Host")

    def client_ip(self):
        return self.client_address[0]

    def gate(self, path, post):
        """Sets self.user. Answers 401/403 and returns False when this request isn't allowed."""
        self.user = self.probe.auth.user(self.headers)
        if (OPEN_POST if post else OPEN_GET).count(path) or (not post and path.startswith(("/vendor/", "/web/"))):
            return True
        if not self.user:
            self.send_body(401, {"error": "log in first", "login": True})
            return False
        m = APP_RE.fullmatch(path) if post else None
        app = self.probe.apps.get(m.group(1)) if m else None
        if post and self.user["role"] != "admin" and path not in ANY_USER_POST and not (app and m.group(2) in app.VIEWER_POST):
            self.send_body(403, {"error": "viewers can't change things"})
            return False
        return True

    def do_GET(self):
        if self.to_https(301):
            return
        path = self.path.split("?")[0]
        p = self.probe
        if not self.gate(path, False):
            return
        if path == "/api/auth":
            return self.send_body(200, {"setup": p.auth.needs_setup(), "legacy": bool(p.auth.legacy), "user": self.user,
                                        "https": self.is_https()})
        if path == "/api/users":
            if self.user["role"] != "admin":
                return self.send_body(403, {"error": "admins only"})
            return self.send_body(200, p.auth.listing())
        if path in ("/", "/index.html"):
            return self.send_body(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self.send_body(200, {**p.snapshot(), "me": self.user})
        if path == "/api/probe":
            return self.send_body(200, p.probe_message())
        if path.startswith("/vendor/") and path[8:] in VENDOR:
            return self.send_body(200, (ROOT / "vendor" / path[8:]).read_bytes(), VENDOR[path[8:]])
        if path == "/api/tailscale":
            return self.send_body(200, p.ts.snapshot(self.client_ip()))
        if path == "/api/tls":
            return self.send_body(200, self.tls_info())
        if path == "/api/tls/cert.pem":
            if not p.tls.cert.exists():
                return self.send_body(404, {"error": "no certificate yet"})
            return self.send_body(200, p.tls.cert.read_bytes(), "application/x-pem-file",
                                  [("Content-Disposition", f'attachment; filename="{socket.gethostname()}.crt"')])
        if path == "/api/console":
            return self.send_body(200, p.console.info(self.user["role"] == "admin"))
        if path == "/api/net":
            return self.send_body(200, p.netmap.snapshot())
        if path == "/api/cs":
            return self.send_body(200, p.cs.snapshot())
        if path == "/api/traces":
            return self.send_body(200, p.netmap.trace_list())
        if path.startswith("/api/trace/"):
            t = p.netmap.trace_get(path.rsplit("/", 1)[1])
            return self.send_body(200, t) if t else self.send_body(404, {"error": "no such trace"})
        if path.startswith("/api/console/log/"):
            f = p.console.log_path(path.rsplit("/", 1)[1])
            if not f:
                return self.send_body(404, {"error": "no such log"})
            return self.send_body(200, f.read_bytes(), "text/plain; charset=utf-8",
                                  [("Content-Disposition", f'attachment; filename="{f.name}"')])
        if path.startswith("/web/"):
            m = WEB_RE.fullmatch(path)
            f = ROOT / "web" / m.group(1) if m else None
            if not f or not f.is_file():
                return self.send_body(404, {"error": "not found"})
            return self.send_body(200, f.read_bytes(), "text/javascript" if m.group(2) == "js" else "text/css")
        m = APP_RE.fullmatch(path)
        if m and m.group(1) in p.apps:
            from urllib.parse import parse_qs
            q = {k: v[-1] for k, v in parse_qs(self.path.partition("?")[2]).items()}
            try:
                out = p.apps[m.group(1)].get(m.group(2), m.group(3), q, self.user)
            except ValueError as e:
                return self.send_body(404 if str(e) == "not found" else 400, {"error": str(e)})
            if isinstance(out, appbase.Reply):
                return self.send_body(out.code, out.body, out.ctype, out.headers)
            return self.send_body(200, out)
        if path == "/ws/console":
            # A WebSocket has no CORS: without these checks any web page could type into the switch.
            if not self.same_origin():
                return self.send_body(403, {"error": "cross-site request"})
            self.protocol_version = "HTTP/1.1"
            sock = ws_mod.WebSocket.accept(self)
            if sock:
                p.console.serve_ws(sock, admin=self.user["role"] == "admin")
            return
        self.send_body(404, {"error": "not found"})

    def do_POST(self):
        if self.to_https(308):
            return
        if not self.same_origin():
            return self.send_body(403, {"error": "cross-site request"})
        if not self.gate(self.path.split("?")[0], True):
            return
        headers = []
        m = APP_RE.fullmatch(self.path.split("?")[0])
        app = self.probe.apps.get(m.group(1)) if m else None
        if app and m.group(2) in app.RAW_POST:  # uploads: the body is the file, not JSON
            from urllib.parse import parse_qs
            q = {k: v[-1] for k, v in parse_qs(self.path.partition("?")[2]).items()}
            try:
                out = app.raw(m.group(2), self.rfile, int(self.headers.get("Content-Length") or 0), q, self.user)
                return self.send_body(200, {"ok": True, **(out or {})})
            except (ValueError, OSError) as e:
                self.close_connection = True
                return self.send_body(400, {"error": str(e)})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 2 << 20)) or b"{}") if n else {}
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            path = self.path.split("?")[0]
            p = self.probe
            reply = {"ok": True}
            if path == "/api/settings":
                p.save_conf(body)
            elif path == "/api/speed/start":
                p.start_speed(body)
            elif path == "/api/speed/stop":
                p.stop_speed()
            elif path == "/api/sweep/start":
                p.start_sweep()
            elif path == "/api/sweep/stop":
                p.sweep["cancel"] = True
            elif path == "/api/recheck":
                p.wake_ev.set()
            elif path == "/api/renew":
                threading.Thread(target=p.renew, daemon=True).start()
            elif path == "/api/history/clear":
                with p.lock:
                    p.history = []
                (DATA / "history.json").write_text("[]")
            # ----- users -----
            elif path == "/api/auth/login":
                tok = p.auth.login(body.get("name"), body.get("password"), self.client_ip())
                headers.append(("Set-Cookie", p.auth.cookie(tok, self.is_https())))
            elif path == "/api/auth/setup":
                tok = p.auth.setup(body.get("name"), body.get("password"), body.get("legacy"), self.client_ip())
                if p.auth.legacy:  # the old console password has done its job
                    p.save_conf({"console_pw": ""}, internal=True)
                    p.auth.legacy = ""
                headers.append(("Set-Cookie", p.auth.cookie(tok, self.is_https())))
            elif path == "/api/auth/logout":
                p.auth.logout(self.headers)
                headers.append(("Set-Cookie", p.auth.cookie(None, self.is_https())))
            elif path == "/api/auth/password":
                p.auth.change_own(self.user["name"], body.get("current"), body.get("new"), p.auth.token(self.headers))
            elif path == "/api/users/add":
                p.auth.add(body.get("name"), body.get("role"), body.get("password"))
            elif path == "/api/users/update":
                p.auth.update(str(body.get("name")), role=body.get("role"), pw=body.get("password"))
            elif path == "/api/users/delete":
                p.auth.delete(str(body.get("name")), self.user["name"])
            # ----- switches and trace -----
            elif path == "/api/net/settings":
                changes = {}
                if body.get("community"):
                    changes["snmp_community"] = body["community"]
                if "version" in body:
                    changes["snmp_version"] = body["version"]
                p.save_conf(changes)
                p.netmap.wake.set()
            elif path == "/api/net/add":
                reply["id"] = p.netmap.add(body.get("host"), body.get("name"), body.get("community"))
            elif path == "/api/net/remove":
                p.netmap.remove(str(body.get("id")))
            elif path == "/api/net/scan":
                p.netmap.start_scan()
            elif path == "/api/net/order":
                ids = body.get("ids")
                if not (isinstance(ids, list) and all(isinstance(x, str) for x in ids)):
                    raise ValueError("Bad order")
                p.netmap.set_order(ids)
            elif path == "/api/net/role":
                p.netmap.set_role(str(body.get("id")), str(body.get("port") or ""), body.get("role") or None)
            elif path == "/api/net/restore":
                host = next((d["host"] for d in p.netmap.devices if d["id"] == str(body.get("id"))), None)
                login = swcli_mod.take_login(p, body, host)
                reply.update(p.netmap.restore_port(str(body.get("id")), str(body.get("port") or ""), login, self.user["name"]))
            elif path == "/api/net/refresh":
                p.netmap.wake.set()
            elif path == "/api/system/power":
                act = body.get("action")
                if act not in ("poweroff", "reboot"):
                    raise ValueError("Pick poweroff or reboot")
                print(f"{act} asked for by {self.user['name']} from {self.client_ip()}", flush=True)
                # answer the page first, then go down
                threading.Timer(1.5, lambda: subprocess.run(["systemctl", act], capture_output=True)).start()
                reply["in"] = 1.5
            elif path == "/api/tailscale":
                if body.get("action") == "cert":
                    p.tls.generate(p.ts.cert_names(p.tls))
                else:
                    p.ts.action(body)
            elif path == "/api/cs/scan":
                threading.Thread(target=p.cs.scan, daemon=True).start()
            elif app:
                try:
                    reply.update(app.post(m.group(2), body, self.user) or {})
                except (ValueError, TypeError):
                    raise
                except Exception as e:  # an add-on's surprise becomes a message on the page
                    raise ValueError(f"{type(e).__name__}: {e}") from None
            elif path == "/api/trace":
                reply["id"] = p.netmap.start_trace(body.get("q"), self.user["name"])
            # ----- console -----
            elif path in ("/api/console/settings", "/api/console/logs/clear"):
                if path.endswith("clear"):
                    p.console.clear_logs()
                else:
                    p.console.save(body)
            # ----- HTTPS (behind the console password, when one is set) -----
            elif path in ("/api/tls/generate", "/api/tls/settings"):
                if path.endswith("generate"):
                    p.tls.generate(body.get("names") or ", ".join(p.tls.default_names()), body.get("days") or 3650)
                else:
                    p.save_conf({"https_redirect": bool(body.get("https_redirect"))}, internal=True)
            # ----- watchdog -----
            elif path == "/api/watch/clear":
                p.watch.clear_events()
            elif path == "/api/watch/trust":
                v = str(body.get("value", "")).strip().lower()
                watch_mod.norm_trusted(v)
                cur = watch_mod.norm_trusted(p.conf.get("watch_trusted", ""))
                if body.get("remove"):
                    cur = [x for x in cur if x != v]
                elif v not in cur:
                    cur.append(v)
                p.save_conf({"watch_trusted": ", ".join(cur)})
            elif path == "/api/watch/ack":
                if not p.watch.switch_ack(str(body.get("id", ""))[:200], self.user["name"], body.get("on", True) is not False):
                    raise ValueError("That alarm is already gone")
            elif path == "/api/watch/dismiss":
                if not p.watch.switch_dismiss(str(body.get("id", ""))[:200]):
                    raise ValueError("That alarm is already gone")
            elif path == "/api/watch/scan":
                if not p.watch.sock:
                    raise ValueError("The watchdog isn't listening")
                p.watch.send_discover()
            # ----- wake -----
            elif path == "/api/wol/save":
                reply["id"] = p.wake.upsert(body)
            elif path == "/api/wol/delete":
                p.wake.delete(str(body.get("id")))
            elif path == "/api/wol/import":
                reply.update(p.wake.import_text(body.get("text", "")))
            elif path == "/api/wol/wake":
                ids = body.get("ids")
                if ids is not None and not (isinstance(ids, list) and all(isinstance(x, str) for x in ids)):
                    raise ValueError("Bad ids")
                p.wake.wake(ids=ids, mac=body.get("mac"))
            elif path == "/api/wol/stop":
                p.wake.stop()
            else:
                return self.send_body(404, {"error": "not found"})
        except (ValueError, TypeError) as e:
            return self.send_body(400, {"error": str(e)}, headers=headers)
        self.send_body(200, reply, headers=headers)


class TLSServer(ThreadingHTTPServer):
    """HTTPS. The handshake runs in the request's own thread, and a new certificate applies to the next connection."""
    daemon_threads = True

    def __init__(self, addr, handler, tls):
        self.tls = tls
        super().__init__(addr, handler)

    def get_request(self):
        sock, addr = super().get_request()
        ctx = self.tls.ctx
        if not ctx:
            sock.close()
            raise OSError("no certificate")
        return ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    def finish_request(self, request, client_address):
        try:
            request.settimeout(10)
            request.do_handshake()
            request.settimeout(None)
        except (ssl.SSLError, OSError):
            return  # e.g. a browser that hasn't accepted the self-signed certificate yet
        super().finish_request(request, client_address)


APPS = [swcli_mod.CliApp, logs_mod.SwitchLogs, inet_mod.Inet, hosts_mod.HostScan, cache_mod.LanCache, capture_mod.Capture, tftp_mod.Files, configs_mod.Configs, cable_mod.CableTest, vlanwalk_mod.VlanWalk, survey_mod.Survey,
        wire_mod.Wire, wire_mod.Mdns, wire_mod.Multicast, online_mod.Online, tick_mod.Tick, mtr_mod.Mtr, stats_mod.Stats, dnstest_mod.DnsTest, fair_mod.Fairness, fw_mod.Firewall]


def main():
    global IFACE, DATA, CONF, HTTPS_PORT
    ap = argparse.ArgumentParser(description="Probe Deck")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--iface", default=IFACE, help="wired interface (default eth0)")
    ap.add_argument("--data", default=str(DATA), help="history, logs and saved machines")
    ap.add_argument("--conf", default=str(CONF), help="settings file")
    ap.add_argument("--https-port", type=int, default=HTTPS_PORT, help="0 = no HTTPS")
    a = ap.parse_args()
    IFACE, DATA, CONF, HTTPS_PORT = a.iface, Path(a.data), Path(a.conf), a.https_port
    probe = Probe()
    Handler.probe = probe
    probe.read_net()
    probe.watch = watch_mod.Watchdog(probe, IFACE, DATA, on_alert=probe.on_alert)
    probe.wake = wake_mod.Wake(probe, IFACE, DATA, watchdog=probe.watch)
    probe.console = console_mod.Console(probe, DATA)
    probe.auth = auth_mod.Auth(CONF.parent, DATA, legacy_hash=probe.conf.get("console_pw", ""))
    probe.console.auth = probe.auth
    probe.netmap = netmap_mod.NetMap(probe, CONF.parent, DATA)
    probe.cs = cs_mod.CS(probe)
    probe.ts = ts_mod.Tailscale(probe)
    probe.tls = tls_mod.TLS(probe, CONF.parent)
    probe.logins = swcli_mod.Logins(CONF.parent)
    for cls in APPS:
        try:
            addon = cls(probe, CONF.parent, DATA) if issubclass(cls, appbase.App) else cls(probe)
            probe.apps[addon.name] = addon
        except Exception as e:  # one broken add-on shouldn't take the probe down
            print(f"{cls.__name__} didn't start: {e}", flush=True)
    if HTTPS_PORT:
        probe.tls.ensure()
        tsrv = TLSServer((a.listen, HTTPS_PORT), Handler, probe.tls)
        threading.Thread(target=tsrv.serve_forever, daemon=True).start()
        print(f"HTTPS on port {HTTPS_PORT}" + (f" ({probe.tls.error})" if probe.tls.error else ""), flush=True)
    for fn in (probe.link_loop, probe.ping_loop, probe.check_loop, probe.iperf_server_loop):
        threading.Thread(target=fn, daemon=True).start()
    probe.start_udp()
    threading.Thread(target=probe.eee_off, daemon=True).start()
    probe.watch.start()
    for addon in probe.apps.values():
        if hasattr(addon, "start"):
            threading.Thread(target=addon.start, daemon=True).start()
    srv = ThreadingHTTPServer((a.listen, a.port), Handler)
    srv.daemon_threads = True
    print(f"Probe Deck {__version__} on http://{a.listen}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
