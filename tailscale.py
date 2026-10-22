"""Tailscale on the probe: status, peers, settings, on/off, log in and out.

Talks to tailscaled's local API on its unix socket (what the `tailscale` command uses), so no output parsing.
The page reads it on the Tailscale tab; only admins can change anything.
"""
import http.client
import ipaddress
import json
import re
import socket
import subprocess
import time
from pathlib import Path

SOCK = "/var/run/tailscale/tailscaled.sock"
SYSCTL = Path("/etc/sysctl.d/99-tailscale.conf")
EXIT_ROUTES = ("0.0.0.0/0", "::/0")
TS_V4 = ipaddress.ip_network("100.64.0.0/10")
TS_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?")


class _Conn(http.client.HTTPConnection):
    def __init__(self, timeout):
        super().__init__("local-tailscaled.sock", timeout=timeout)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCK)


def api(method, path, body=None, timeout=5):
    c = _Conn(timeout)
    try:
        c.request(method, "/localapi/v0/" + path, body=json.dumps(body) if body is not None else None,
                  headers={"Sec-Tailscale": "localapi", "Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        if r.status >= 400:
            raise ValueError("tailscale: " + data.decode(errors="replace").strip()[:200])
        return json.loads(data) if data.strip() else None
    except OSError as e:
        raise ValueError(f"tailscaled isn't answering ({e.strerror or e})")
    finally:
        c.close()


def systemctl(*args):
    return subprocess.run(["systemctl", *args, "tailscaled"], capture_output=True, text=True, timeout=30)


def in_tailnet(ip):
    try:
        a = ipaddress.ip_address(ip.removeprefix("::ffff:"))
        return a in TS_V4 if a.version == 4 else a in TS_V6
    except ValueError:
        return False


def parse_routes(text):
    out = []
    for part in re.split(r"[,\s]+", str(text or "")):
        if not part:
            continue
        try:
            n = ipaddress.ip_network(part, strict=False)
        except ValueError:
            raise ValueError(f"Not a subnet: {part[:40]} (use e.g. 192.168.1.0/24)")
        if str(n) in EXIT_ROUTES:
            raise ValueError("Use the exit node switch for 0.0.0.0/0")
        out.append(str(n))
    if len(out) > 16:
        raise ValueError("At most 16 subnets")
    return list(dict.fromkeys(out))


class Tailscale:
    def __init__(self, probe):
        self.p = probe

    def installed(self):
        return Path("/usr/bin/tailscale").exists() or Path("/usr/sbin/tailscaled").exists()

    def service(self):
        en = systemctl("is-enabled").stdout.strip()
        act = systemctl("is-active").stdout.strip()
        return {"enabled": en == "enabled", "active": act == "active", "state": act}

    def lan_cidr(self):
        n = self.p.net
        if n.get("ip") and n.get("prefix"):
            return str(ipaddress.ip_network(f"{n['ip']}/{n['prefix']}", strict=False))
        return None

    def snapshot(self, client_ip=""):
        out = {"installed": self.installed(), "client_tailnet": in_tailnet(client_ip), "lan": self.lan_cidr(), "now": time.time()}
        if not out["installed"]:
            return out
        out["service"] = self.service()
        if not out["service"]["active"]:
            return out
        try:
            st = api("GET", "status")
            pr = api("GET", "prefs")
        except ValueError as e:
            out["error"] = str(e)
            return out
        me = st.get("Self") or {}
        tn = st.get("CurrentTailnet") or {}
        adv = pr.get("AdvertiseRoutes") or []
        au = pr.get("AutoUpdate") or {}
        out.update({
            "version": (st.get("Version") or "").split("-")[0], "state": st.get("BackendState"), "auth_url": st.get("AuthURL") or "",
            "ips": st.get("TailscaleIPs") or [], "dns": (me.get("DNSName") or "").rstrip("."), "online": me.get("Online"),
            "tailnet": tn.get("Name"), "magic_dns": tn.get("MagicDNSEnabled"), "relay": me.get("Relay"),
            "health": [h for h in (st.get("Health") or []) if isinstance(h, str)][:6],
            "prefs": {"want_running": pr.get("WantRunning"), "logged_out": pr.get("LoggedOut"), "hostname": pr.get("Hostname") or socket.gethostname(),
                      "route_all": pr.get("RouteAll"), "corp_dns": pr.get("CorpDNS"), "run_ssh": pr.get("RunSSH"), "shields_up": pr.get("ShieldsUp"),
                      "routes": [r for r in adv if r not in EXIT_ROUTES], "exit_node": any(r in EXIT_ROUTES for r in adv),
                      "auto_update": bool(au.get("Apply"))},
            "approved": [str(r) for r in (me.get("PrimaryRoutes") or [])],
            "forwarding": Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "1",
        })
        peers = []
        for p in (st.get("Peer") or {}).values():
            peers.append({"name": p.get("HostName") or "?", "dns": (p.get("DNSName") or "").split(".")[0], "ip": (p.get("TailscaleIPs") or [None])[0],
                          "os": p.get("OS"), "online": bool(p.get("Online")), "active": bool(p.get("Active")),
                          "direct": p.get("CurAddr") or "", "relay": p.get("Relay") or "", "last_seen": p.get("LastSeen"),
                          "exit": bool(p.get("ExitNodeOption")), "rx": p.get("RxBytes") or 0, "tx": p.get("TxBytes") or 0})
        out["peers"] = sorted(peers, key=lambda x: (not x["online"], x["name"].lower()))[:200]
        return out

    # ---------- changes (admin only) ----------
    def action(self, body):
        a = body.get("action")
        if a == "service":
            on = bool(body.get("on"))
            r = systemctl("enable" if on else "disable", "--now")
            if r.returncode:
                raise ValueError((r.stderr.strip().splitlines() or ["systemctl failed"])[-1][:200])
            return
        if a == "up":
            api("PATCH", "prefs", {"WantRunning": True, "WantRunningSet": True})
            st = api("GET", "status")
            if st.get("BackendState") in ("NeedsLogin", "NoState"):
                api("POST", "login-interactive")
            return
        if a == "down":
            api("PATCH", "prefs", {"WantRunning": False, "WantRunningSet": True})
            return
        if a == "login":
            api("PATCH", "prefs", {"WantRunning": True, "WantRunningSet": True})
            api("POST", "login-interactive")
            return
        if a == "logout":
            api("POST", "logout", timeout=15)
            return
        if a == "settings":
            return self.settings(body)
        raise ValueError("Unknown action")

    def settings(self, b):
        m = {}
        if "hostname" in b:
            h = str(b["hostname"]).strip().lower()
            if not HOST_RE.fullmatch(h):
                raise ValueError("Tailscale name: letters, digits and dashes")
            m.update(Hostname=h, HostnameSet=True)
        for key, pref in (("route_all", "RouteAll"), ("corp_dns", "CorpDNS"), ("run_ssh", "RunSSH"), ("shields_up", "ShieldsUp")):
            if key in b:
                m.update({pref: bool(b[key]), pref + "Set": True})
        if "routes" in b or "exit_node" in b:
            cur = api("GET", "prefs").get("AdvertiseRoutes") or []
            routes = parse_routes(b["routes"]) if "routes" in b else [r for r in cur if r not in EXIT_ROUTES]
            exit_on = bool(b["exit_node"]) if "exit_node" in b else any(r in EXIT_ROUTES for r in cur)
            routes += list(EXIT_ROUTES) if exit_on else []
            m.update(AdvertiseRoutes=routes, AdvertiseRoutesSet=True)
            if routes:
                self.forwarding()
        if "auto_update" in b:
            on = bool(b["auto_update"])
            m.update(AutoUpdate={"Check": True, "Apply": on}, AutoUpdateSet={"CheckSet": True, "ApplySet": True})
        if m:
            api("PATCH", "prefs", m)

    @staticmethod
    def forwarding():
        """Subnet routes and exit nodes need IP forwarding; Tailscale's own docs set it this way."""
        if Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "1":
            return
        SYSCTL.write_text("# Probe Deck: Tailscale subnet router / exit node\nnet.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n")
        subprocess.run(["sysctl", "-p", str(SYSCTL)], capture_output=True, timeout=10)

    def cert_names(self, tls):
        """The certificate's names plus this probe's Tailscale name and IP."""
        snap = self.snapshot()
        if not snap.get("dns"):
            raise ValueError("Tailscale isn't logged in yet")
        info = tls.info() or {}
        names = list(info.get("names") or tls.default_names())
        for n in [snap["dns"], snap["dns"].split(".")[0]] + [i for i in snap.get("ips", []) if "." in i]:
            if n not in names:
                names.append(n)
        return ", ".join(names)
