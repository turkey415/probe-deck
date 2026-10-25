"""VLAN walk: on a trunk port, test every VLAN in turn.

For each VLAN: a tagged interface (pdv<id>), a real DHCP lease (Discover, Offer, Request, Ack), then
from that address: ping the gateway, ask each DNS server, fetch the internet check URL. Its own
routing table keeps the probe's normal traffic where it was. Then the lease is released and the
interface removed. On an access port nothing comes back, and the page says the port isn't a trunk.
"""
import http.client
import ipaddress
import random
import re
import socket
import struct
import subprocess
import threading
import time
from urllib.parse import urlsplit

from appbase import App, load_json, save_json

MAX_VLANS = 64
HIST_MAX = 20


def checksum(b):
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def parse_vlans(text):
    out = []
    for part in re.split(r"[,\s]+", str(text or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,4})(?:-(\d{1,4}))?", part)
        if not m:
            raise ValueError(f"Not a VLAN: {part[:20]}")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if not (1 <= a <= b <= 4094):
            raise ValueError("VLANs are 1-4094")
        out.extend(range(a, b + 1))
        if len(out) > MAX_VLANS:
            raise ValueError(f"At most {MAX_VLANS} VLANs per walk")
    return sorted(set(out))


def run(argv, timeout=10):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


class Dhcp:
    """A one-shot DHCP client on one interface, over a packet socket."""

    def __init__(self, ifname, mac, watch=None):
        self.ifname, self.mac = ifname, mac
        self.watch = watch
        self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
        self.sock.bind((ifname, 0))
        self.sock.settimeout(0.5)

    def close(self):
        self.sock.close()

    def frame(self, mtype, xid, extra=b"", src="0.0.0.0", dst="255.255.255.255", dmac=b"\xff" * 6, ciaddr="0.0.0.0", flags=0x8000):
        bootp = struct.pack("!BBBBIHH4s4s4s4s16s64s128s", 1, 1, 6, 0, xid, 0, flags, socket.inet_aton(ciaddr), b"\0" * 4, b"\0" * 4, b"\0" * 4,
                            self.mac + b"\0" * 10, b"", b"")
        opts = b"\x63\x82\x53\x63" + bytes([53, 1, mtype]) + b"\x37\x05\x01\x03\x06\x0f\x1c" + b"\x0c\x0dprobe-vlanwlk" + extra + b"\xff"
        payload = bootp + opts + b"\0" * max(0, 300 - len(bootp) - len(opts))
        udp = struct.pack("!HHHH", 68, 67, 8 + len(payload), 0) + payload
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0x10, 20 + len(udp), random.getrandbits(16), 0, 64, 17, 0, socket.inet_aton(src), socket.inet_aton(dst))
        ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
        return dmac + self.mac + b"\x08\x00" + ip + udp

    def wait(self, xid, want, secs):
        end = time.time() + secs
        while time.time() < end:
            try:
                f = self.sock.recv(2048)
            except socket.timeout:
                continue
            if len(f) < 14 + 20 + 8 + 240:
                continue
            ihl = (f[14] & 0x0F) * 4
            u = 14 + ihl
            if f[23] != 17 or struct.unpack("!HH", f[u:u + 4]) != (67, 68):
                continue
            d = f[u + 8:]
            if struct.unpack("!I", d[4:8])[0] != xid or d[236:240] != b"\x63\x82\x53\x63":
                continue
            opts, i = {}, 240
            while i < len(d) and d[i] != 255:
                if d[i] == 0:
                    i += 1
                    continue
                opts[d[i]] = d[i + 2:i + 2 + d[i + 1]]
                i += 2 + d[i + 1]
            mt = opts.get(53, b"\0")[0]
            if mt == 6:
                return {"nak": True}
            if mt != want:
                continue
            ips = lambda b: [socket.inet_ntoa(b[j:j + 4]) for j in range(0, len(b) - 3, 4)]
            mask = opts.get(1)
            return {"ip": socket.inet_ntoa(d[16:20]), "server": ips(opts.get(54, b""))[0] if opts.get(54) else socket.inet_ntoa(f[26:30]),
                    "prefix": bin(struct.unpack("!I", mask)[0]).count("1") if mask and len(mask) == 4 else 24,
                    "router": (ips(opts.get(3, b"")) or [None])[0], "dns": ips(opts.get(6, b""))[:3],
                    "domain": opts.get(15, b"").decode("ascii", "replace").strip("\0") or None,
                    "lease": struct.unpack("!I", opts[51])[0] if len(opts.get(51, b"")) == 4 else None,
                    "relay": socket.inet_ntoa(d[24:28]) if d[24:28] != b"\0" * 4 else None, "smac": f[6:12]}
        return None

    def lease(self):
        xid = random.getrandbits(32)
        if self.watch:
            if len(self.watch.ignore_xids) > 500:
                self.watch.ignore_xids.clear()
            self.watch.ignore_xids.add(xid)
        t0 = time.time()
        offer = None
        for _ in range(2):
            self.sock.send(self.frame(1, xid))
            offer = self.wait(xid, 2, 3)
            if offer:
                break
        if not offer or offer.get("nak"):
            return None, round(time.time() - t0, 2)
        req = bytes([50, 4]) + socket.inet_aton(offer["ip"]) + bytes([54, 4]) + socket.inet_aton(offer["server"])
        ack = None
        for _ in range(2):
            self.sock.send(self.frame(3, xid, req))
            ack = self.wait(xid, 5, 3)
            if ack:
                break
        if not ack or ack.get("nak"):
            return dict(offer, acked=False), round(time.time() - t0, 2)
        return dict(ack, acked=True, xid=xid), round(time.time() - t0, 2)

    def release(self, l):
        try:
            extra = bytes([54, 4]) + socket.inet_aton(l["server"])
            self.sock.send(self.frame(7, random.getrandbits(32), extra, src=l["ip"], dst=l["server"], dmac=l["smac"], ciaddr=l["ip"], flags=0))
        except OSError:
            pass


class VlanWalk(App):
    name = "vlans"
    SETTINGS = {"vlans": ""}

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.hist_file = self.dir / "history.json"
        self.history = load_json(self.hist_file, [])[-HIST_MAX:]
        self.walk = self.history[-1] if self.history else None   # the last walk, after a restart too
        self.stop = False

    def iface(self):
        return self.p.watch.iface

    def check(self, k, v):
        if k == "vlans":
            parse_vlans(v)
            return str(v)[:300]
        return super().check(k, v)

    def begin(self, text, user):
        vlans = parse_vlans(text)
        if not vlans:
            raise ValueError("Type the VLANs to walk, e.g. 10,20,30-35")
        if self.walk and self.walk["running"]:
            raise ValueError("A walk is already running")
        if not self.p.link["up"]:
            raise ValueError("No link")
        self.save_conf({"vlans": text})
        nb = self.p.neighbor or {}
        self.stop = False
        self.walk = {"running": True, "t": time.time(), "by": user, "vlans": vlans, "results": [], "cur": None, "native": nb.get("vlan"),
                     "switch": nb.get("switch"), "port": nb.get("port")}
        threading.Thread(target=self.go, args=(self.walk,), daemon=True).start()

    def go(self, w):
        try:
            run(["modprobe", "8021q"])
            mac = bytes.fromhex(open(f"/sys/class/net/{self.iface()}/address").read().strip().replace(":", ""))
            for v in w["vlans"]:
                if self.stop:
                    break
                w["cur"] = v
                w["results"].append(self.one(v, mac, w))
        except Exception as e:
            w["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        w.update(running=False, cur=None, secs=round(time.time() - w["t"], 1))
        got = [r for r in w["results"] if r.get("dhcp")]
        w["trunk"] = bool(got)
        with self.lock:
            self.history = (self.history + [w])[-HIST_MAX:]
            save_json(self.hist_file, self.history)

    def one(self, v, mac, w):
        dev, table = f"pdv{v}", 20000 + v
        r = {"vlan": v, "step": "tagging", "dhcp": None, "gw": None, "dns": [], "inet": None, "error": None, "native": str(v) == str(w.get("native"))}
        w["live"] = r
        dh = None
        lease = None
        try:
            run(["ip", "link", "del", dev])  # left over from a crash
            code, out = run(["ip", "link", "add", "link", self.iface(), "name", dev, "type", "vlan", "id", str(v)])
            if code:
                raise RuntimeError(f"can't make the tagged interface: {out[:100]}")
            # no IPv6 chatter, no router solicitations on the test VLAN
            run(["sysctl", "-q", "-w", f"net.ipv6.conf.{dev}.disable_ipv6=1"])
            run(["ip", "link", "set", dev, "up"])
            time.sleep(1)
            r["step"] = "asking DHCP"
            dh = Dhcp(dev, mac, self.p.watch)
            lease, secs = dh.lease()
            if not lease:
                r["error"] = "no DHCP answer"
                return r
            r["dhcp"] = {k: lease.get(k) for k in ("ip", "prefix", "server", "router", "dns", "domain", "lease", "relay", "acked")}
            r["dhcp"]["secs"] = secs
            if not lease.get("acked"):
                r["error"] = "an offer, but no ACK"
                lease = None
                return r
            ip, gw = lease["ip"], lease["router"]
            net = ipaddress.ip_network(f"{ip}/{lease['prefix']}", strict=False)
            mine = self.p.net.get("ip")
            if mine and ipaddress.ip_address(mine) in net:
                r["error"] = "this VLAN uses the probe's own subnet (the native VLAN?): skipped the tests"
                return r
            run(["ip", "addr", "add", f"{ip}/{lease['prefix']}", "dev", dev])
            run(["ip", "route", "add", str(net), "dev", dev, "src", ip, "table", str(table)])
            if gw:
                run(["ip", "route", "add", "default", "via", gw, "dev", dev, "table", str(table)])
            run(["ip", "rule", "add", "from", ip, "table", str(table), "priority", str(1000 + v % 30000)])
            time.sleep(0.5)
            if gw:
                r["step"] = "pinging the gateway"
                code, out = run(["ping", "-n", "-c", "4", "-i", "0.3", "-W", "1", "-I", ip, gw], 10)
                rx = re.search(r"(\d+) received", out)
                avg = re.search(r"= [\d.]+/([\d.]+)/", out)
                r["gw"] = {"ip": gw, "got": int(rx.group(1)) if rx else 0, "of": 4, "ms": float(avg.group(1)) if avg else None}
            r["step"] = "asking DNS"
            name = self.p.conf.get("dns_name", "google.com")
            for srv in (lease.get("dns") or [])[:3]:
                code, out = run(["dig", "+time=2", "+tries=1", "-b", ip, f"@{srv}", name, "A"], 6)
                st = re.search(r"status: (\w+)", out)
                qt = re.search(r"Query time: (\d+) msec", out)
                r["dns"].append({"server": srv, "ok": bool(st and st.group(1) == "NOERROR" and re.search(r"\sIN\s+A\s", out)),
                                 "ms": int(qt.group(1)) if qt else None, "status": st.group(1) if st else "no answer"})
            if gw:
                r["step"] = "trying the internet"
                u = urlsplit(self.p.conf.get("http_url") or "http://connectivitycheck.gstatic.com/generate_204")
                t0 = time.perf_counter()
                try:
                    host = socket.getaddrinfo(u.hostname, None, socket.AF_INET)[0][4][0]
                    c = http.client.HTTPConnection(host, u.port or 80, timeout=5, source_address=(ip, 0))
                    c.request("GET", u.path or "/", headers={"Host": u.hostname, "User-Agent": "probe-deck"})
                    code = c.getresponse().status
                    c.close()
                    r["inet"] = {"ok": 200 <= code < 400, "code": code, "ms": round((time.perf_counter() - t0) * 1000)}
                except Exception as e:
                    r["inet"] = {"ok": False, "error": str(e)[:80] or type(e).__name__}
        except Exception as e:
            r["error"] = str(e)[:140]
        finally:
            r["step"] = "cleaning up"
            if dh:
                if lease and lease.get("acked"):
                    dh.release(lease)
                dh.close()
            if lease and lease.get("ip"):
                run(["ip", "rule", "del", "from", lease["ip"], "table", str(table)])
            run(["ip", "route", "flush", "table", str(table)])
            run(["ip", "link", "del", dev])
            r["step"] = None
            r["ok"] = bool(r["dhcp"] and r["dhcp"].get("acked") and (not r["gw"] or r["gw"]["got"]) and all(d["ok"] for d in r["dns"]) and (not r["inet"] or r["inet"]["ok"]))
        return r

    # ---------- for the page ----------
    def status(self):
        if self.walk and self.walk["running"]:
            return {"dot": "live"}
        return None

    def vlan_hint(self):
        """VLANs the probe's switch has, from the Switches page (when it reads that switch)."""
        nb, nm = self.p.neighbor or {}, self.p.netmap
        if not nm or not nb.get("mgmt"):
            return []
        with nm.lock:
            d = next((d for d in nm.devices if d["host"] == nb["mgmt"]), None)
            ids = list((nm.state.get(d["id"], {}) if d else {}).get("vlans") or [])
        return sorted({int(x) for x in ids if str(x).isdigit() and int(x) not in (1002, 1003, 1004, 1005)})

    def get(self, sub, arg, q, user):
        nb = self.p.neighbor or {}
        dtp = self.p.watch.dtp if self.p.watch else None
        return {"walk": self.walk, "history": self.history[::-1][:10], "vlans": self.conf["vlans"], "native": nb.get("vlan"),
                "dtp": dtp, "hint": self.vlan_hint(), "port": nb.get("port"), "switch": nb.get("switch")}

    def post(self, action, body, user):
        if action == "start":
            self.begin(body.get("vlans"), user["name"])
            return {}
        if action == "stop":
            self.stop = True
            return {}
        return super().post(action, body, user)
