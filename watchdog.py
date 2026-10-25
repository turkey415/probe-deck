"""Watchdog: listens to the broadcast and multicast traffic on the port for things that break a LAN.

- Rogue DHCP: a DHCP scan (DISCOVER with the broadcast flag, never followed by a REQUEST) every minute,
  plus any offer heard on the wire. Servers other than the probe's own lease server are rogue until trusted.
- IP conflicts: one IP claimed by two MACs in ARP, someone claiming the gateway's or the probe's address.
- Broadcast storms: broadcast packets per second over a limit, with the top senders.
- Spanning tree: the root bridge and topology changes, from the BPDUs the switch sends.
- IPv6 router advertisements (a PC sharing its connection, or a rogue router), and DTP on the port.

A kernel filter passes only broadcast/multicast frames, so speed tests don't load Python.
"""
import collections
import ctypes
import json
import os
import random
import re
import socket
import struct
import subprocess
import threading
import time

ETH_P_ALL = 0x0003
SO_ATTACH_FILTER = 26
PACKET_OUTGOING = 4
EVENTS_MAX = 300
ARP_HOLD = 600          # an IP->MAC claim older than this no longer counts toward a conflict
REALERT = 1800          # the same problem alerts again after this much quiet

DEFAULTS = {
    "watch": True,
    "watch_scan": 60,          # seconds between DHCP scans, 0 = listen only
    "watch_storm": 1000,       # broadcast packets per second that count as a storm
    "watch_trusted": "",       # DHCP servers / RA routers that are fine (IPs or MACs, comma separated)
}

# Pass a frame up only if the destination MAC has the group bit set (broadcast or multicast).
BPF = [(0x30, 0, 0, 0), (0x45, 0, 1, 1), (0x06, 0, 0, 0x0000FFFF), (0x06, 0, 0, 0)]

mac_s = lambda b: ":".join(f"{x:02x}" for x in b)
ip_s = lambda b: socket.inet_ntoa(b)


def checksum(b):
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def norm_trusted(text):
    out = []
    for part in re.split(r"[,\s]+", str(text)):
        part = part.strip().lower()
        if not part:
            continue
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", part):
            out.append(part)
        elif re.fullmatch(r"([0-9a-f]{2}[:-]){5}[0-9a-f]{2}", part):
            out.append(part.replace("-", ":"))
        elif re.fullmatch(r"[0-9a-f:]{2,39}", part) and ":" in part:
            out.append(part)  # IPv6 link-local of an RA router
        else:
            raise ValueError(f"Not an IP or MAC: {part}")
    if len(out) > 32:
        raise ValueError("At most 32 trusted entries")
    return out


class Watchdog:
    def __init__(self, probe, iface, data_dir, on_alert=None):
        self.p = probe
        self.iface = iface
        self.file = os.path.join(data_dir, "events.json")
        self.on_alert = on_alert
        self.lock = threading.Lock()
        self.sock = None
        self.error = None
        self.mac = b"\0" * 6
        self.events = collections.deque(maxlen=EVENTS_MAX)
        try:
            with open(self.file) as f:
                self.events.extend(json.load(f)[-EVENTS_MAX:])
        except (OSError, ValueError):
            pass
        self.alerted = {}                     # problem key -> last alert time
        self.dhcp = {}                        # server id -> info
        self.arp = {}                         # ip -> {"mac", "t"}
        self.macs = {}                        # mac -> {"ip", "t"} (also feeds the Wake panel)
        self.conflicts = {}                   # ip -> {"macs": [...], "t", "first"}
        self.rates = collections.deque(maxlen=150)   # (t, bcast pps, mcast pps)
        self.cur = [0, 0]
        self.talk_now = collections.Counter()
        self.talkers = collections.deque(maxlen=60)  # per-second Counters of broadcast senders
        self.storm = None                     # {"since", "peak", "over"} while over the limit
        self.over = 0
        self.stp = {"root": None, "bridge": None, "cost": None, "tc": 0, "last_tc": None, "tc_on": False,
                    "seen": None, "proto": None, "roots": 0}
        self.ra = {}                          # src ipv6 -> info
        self.dtp = None
        self.ignore_xids = set()
        self.scan = {"xid": None, "sent": None, "answers": 0, "last": None, "count": 0}
        self.gw_mac = None
        self.pkts = 0
        self.save_due = False

    @property
    def conf(self):
        return self.p.conf

    def trusted(self):
        try:
            return set(norm_trusted(self.conf.get("watch_trusted", "")))
        except ValueError:
            return set()

    def start(self):
        for fn in (self.rx_loop, self.tick_loop, self.scan_loop):
            threading.Thread(target=fn, daemon=True).start()

    # ---------- events ----------
    def event(self, sev, kind, msg, key=None, **extra):
        now = time.time()
        if key:
            if now - self.alerted.get(key, 0) < REALERT:
                self.alerted[key] = now
                return
            self.alerted[key] = now
        ev = {"t": round(now, 1), "sev": sev, "kind": kind, "msg": msg, **extra}
        with self.lock:
            self.events.append(ev)
            self.save_due = True
        if self.on_alert and sev in ("warn", "crit"):
            self.on_alert(ev)

    def clear_events(self):
        with self.lock:
            self.events.clear()
            self.alerted.clear()
            self.save_due = True

    # ---------- capture ----------
    def open_sock(self):
        s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
        prog = b"".join(struct.pack("HBBI", *ins) for ins in BPF)
        buf = ctypes.create_string_buffer(prog)
        s.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER, struct.pack("HL", len(BPF), ctypes.addressof(buf)))
        s.bind((self.iface, 0))
        self._bpf = buf  # keep it alive
        # Frames that arrived before the filter was attached: throw them away.
        s.setblocking(False)
        try:
            while True:
                s.recv(65535)
        except BlockingIOError:
            pass
        s.setblocking(True)
        s.settimeout(1)
        self.mac = s.getsockname()[4][:6]
        return s

    def rx_loop(self):
        while True:
            if not self.conf.get("watch"):
                if self.sock:
                    self.sock.close()
                    self.sock = None
                time.sleep(2)
                continue
            if not self.sock:
                try:
                    self.sock = self.open_sock()
                    self.error = None
                except OSError as e:
                    self.error = f"can't listen on {self.iface}: {e.strerror or e}"
                    time.sleep(5)
                    continue
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError as e:
                self.error = str(e)
                self.sock.close()
                self.sock = None
                time.sleep(2)
                continue
            if addr[2] == PACKET_OUTGOING or len(data) < 14:
                continue
            try:
                self.handle(data)
            except (struct.error, IndexError, ValueError, OSError):
                pass

    def handle(self, f):
        self.pkts += 1
        dst, src = f[0:6], f[6:12]
        bcast = dst == b"\xff" * 6
        with self.lock:
            self.cur[0 if bcast else 1] += 1
            if bcast:
                self.talk_now[mac_s(src)] += 1
        et, off = struct.unpack("!H", f[12:14])[0], 14
        if et == 0x8100:
            et, off = struct.unpack("!H", f[16:18])[0], 18
        if et == 0x0806:
            self.on_arp(f[off:off + 28])
        elif et == 0x0800:
            self.on_ipv4(f, off, src)
        elif et == 0x86DD:
            self.on_ipv6(f, off, src)
        elif et <= 1500:
            llc = f[off:off + 8]
            if llc[:3] == b"\x42\x42\x03":
                self.on_bpdu(f[off + 3:], "IEEE")
            elif llc[:3] == b"\xaa\xaa\x03" and llc[3:6] == b"\x00\x00\x0c":
                pid = struct.unpack("!H", llc[6:8])[0]
                if pid == 0x010B:
                    self.on_bpdu(f[off + 8:], "PVST+")
                elif pid == 0x2004:
                    self.on_dtp(mac_s(src))

    # ---------- ARP ----------
    def on_arp(self, a):
        if len(a) < 28:
            return
        sha, spa = a[8:14], a[14:18]
        if spa == b"\0\0\0\0" or sha in (b"\0" * 6, self.mac):
            return
        ip, mac, now = ip_s(spa), mac_s(sha), time.time()
        net = self.p.net
        with self.lock:
            self.macs[mac] = {"ip": ip, "t": now}
            if len(self.macs) > 4096:
                self.macs.pop(next(iter(self.macs)))
            old = self.arp.get(ip)
            self.arp[ip] = {"mac": mac, "t": now}
            if len(self.arp) > 4096:
                self.arp.pop(next(iter(self.arp)))
        if ip == net.get("ip"):
            self.add_conflict(ip, [mac_s(self.mac), mac])
            self.event("crit", "conflict", f"{mac} is using this probe's address {ip}", key=f"self:{mac}", ip=ip, mac=mac)
            return
        if ip == net.get("gateway") and self.gw_mac and mac != self.gw_mac:
            self.add_conflict(ip, [self.gw_mac, mac])
            self.event("crit", "spoof", f"{mac} claims to be the gateway {ip} (the gateway is {self.gw_mac})",
                       key=f"gw:{mac}", ip=ip, mac=mac)
            return
        if old and old["mac"] != mac and now - old["t"] < ARP_HOLD:
            self.add_conflict(ip, [old["mac"], mac])
            self.event("crit", "conflict", f"IP conflict: {ip} is used by {old['mac']} and {mac}",
                       key=f"dup:{ip}:{'/'.join(sorted([old['mac'], mac]))}", ip=ip, macs=[old["mac"], mac])

    def add_conflict(self, ip, macs):
        now = time.time()
        with self.lock:
            c = self.conflicts.setdefault(ip, {"ip": ip, "macs": [], "first": now, "t": now, "n": 0})
            c["t"] = now
            c["n"] += 1
            for m in macs:
                if m not in c["macs"]:
                    c["macs"].append(m)
            c["macs"] = c["macs"][-4:]

    # ---------- DHCP ----------
    def on_ipv4(self, f, off, src):
        ihl = (f[off] & 0x0F) * 4
        if f[off + 9] != 17:
            return
        u = off + ihl
        sport, dport = struct.unpack("!HH", f[u:u + 4])
        if sport == 67 and dport == 68:
            self.on_dhcp(f[u + 8:], ip_s(f[off + 12:off + 16]), mac_s(src))

    def on_dhcp(self, d, src_ip, src_mac):
        if len(d) < 240 or d[236:240] != b"\x63\x82\x53\x63":
            return
        xid = struct.unpack("!I", d[4:8])[0]
        if xid in self.ignore_xids:  # the VLAN walk's own DHCP on other VLANs: not a rogue server
            return
        yiaddr, giaddr = ip_s(d[16:20]), ip_s(d[24:28])
        opts, i = {}, 240
        while i < len(d) and d[i] != 255:
            if d[i] == 0:
                i += 1
                continue
            n = d[i + 1]
            opts[d[i]] = d[i + 2:i + 2 + n]
            i += 2 + n
        mtype = opts.get(53, b"\0")[0]
        if mtype not in (2, 5):  # OFFER, ACK
            return
        sid = ip_s(opts[54]) if len(opts.get(54, b"")) == 4 else src_ip
        ips = lambda b: [ip_s(b[j:j + 4]) for j in range(0, len(b) - 3, 4)]
        mask = opts.get(1)
        prefix = bin(struct.unpack("!I", mask)[0]).count("1") if mask and len(mask) == 4 else None
        now = time.time()
        ours = xid == self.scan["xid"]
        if ours:
            self.scan["answers"] += 1
        tr = self.trusted()
        lease_srv = ((self.p.net.get("dhcp") or {}).get("server"))
        with self.lock:
            new = sid not in self.dhcp
            s = self.dhcp.setdefault(sid, {"id": sid, "first": now, "offers": 0})
            s.update(mac=src_mac, last=now, offers=s["offers"] + 1, offered=yiaddr if yiaddr != "0.0.0.0" else s.get("offered"),
                     prefix=prefix, router=(ips(opts.get(3, b"")) or [None])[0], dns=ips(opts.get(6, b""))[:3],
                     domain=opts.get(15, b"").decode("ascii", "replace").strip("\0")[:60] or None,
                     relay=giaddr if giaddr != "0.0.0.0" else None, scan=s.get("scan") or ours)
            if len(self.dhcp) > 32:
                self.dhcp.pop(min(self.dhcp, key=lambda k: self.dhcp[k]["last"]))
        rogue = sid != lease_srv and sid not in tr and src_mac not in tr
        if rogue:
            via = f" via relay {giaddr}" if giaddr != "0.0.0.0" else ""
            self.event("crit", "dhcp", f"Rogue DHCP server {sid} ({src_mac}){via}, offering {yiaddr}"
                       f"{' router ' + s['router'] if s.get('router') else ''}", key=f"dhcp:{sid}", ip=sid, mac=src_mac)
        elif new:
            self.event("info", "dhcp", f"DHCP server {sid} answered{' (this probe’s lease server)' if sid == lease_srv else ' (trusted)'}")

    def scan_loop(self):
        time.sleep(15)  # let DHCP and the lease settle after a boot
        while True:
            every = int(self.conf.get("watch_scan") or 0)
            if self.conf.get("watch") and every and self.sock and self.p.link["up"] and self.p.net.get("ip"):
                self.send_discover()
                time.sleep(every)
            else:
                time.sleep(5)

    def send_discover(self):
        xid = random.getrandbits(32)
        mac = self.mac
        bootp = struct.pack("!BBBBIHH4s4s4s4s16s64s128s", 1, 1, 6, 0, xid, 0, 0x8000, b"\0" * 4, b"\0" * 4, b"\0" * 4,
                            b"\0" * 4, mac + b"\0" * 10, b"", b"")
        opts = b"\x63\x82\x53\x63" + b"\x35\x01\x01" + b"\x37\x09\x01\x03\x06\x0f\x2a\x2b\x42\x96\x36" + b"\x0c\x0bprobe-watch" + b"\xff"
        payload = bootp + opts + b"\0" * max(0, 300 - len(bootp) - len(opts))
        udp = struct.pack("!HHHH", 68, 67, 8 + len(payload), 0) + payload
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0x10, 20 + len(udp), random.getrandbits(16), 0, 64, 17, 0,
                         b"\0" * 4, b"\xff" * 4)
        ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
        frame = b"\xff" * 6 + mac + b"\x08\x00" + ip + udp
        try:
            self.sock.send(frame)
            self.scan.update(xid=xid, sent=time.time(), answers=0, count=self.scan["count"] + 1)
            threading.Timer(4, self._scan_done, args=(xid,)).start()
        except OSError as e:
            self.error = f"DHCP scan failed: {e.strerror or e}"

    def _scan_done(self, xid):
        if self.scan["xid"] == xid:
            self.scan["last"] = {"t": self.scan["sent"], "answers": self.scan["answers"]}

    # ---------- IPv6 router advertisements ----------
    def on_ipv6(self, f, off, src):
        if f[off + 6] != 58 or f[off + 40] != 134:
            return
        sip = socket.inet_ntop(socket.AF_INET6, f[off + 8:off + 24])
        life = struct.unpack("!H", f[off + 46:off + 48])[0]
        prefixes, i = [], off + 56
        while i + 2 <= len(f):
            t, n = f[i], f[i + 1] * 8
            if not n:
                break
            if t == 3 and n >= 32:
                prefixes.append(f"{socket.inet_ntop(socket.AF_INET6, f[i + 16:i + 32])}/{f[i + 2]}")
            i += n
        mac, now = mac_s(src), time.time()
        with self.lock:
            new = sip not in self.ra
            r = self.ra.setdefault(sip, {"ip": sip, "first": now, "n": 0})
            r.update(mac=mac, last=now, life=life, prefixes=prefixes[:3], n=r["n"] + 1)
        tr = self.trusted()
        if new and sip.lower() not in tr and mac not in tr:
            self.event("warn", "ra", f"IPv6 router advertisement from {mac} ({sip}){' prefix ' + prefixes[0] if prefixes else ''}"
                       f"{'' if life else ', not a default router'}", key=f"ra:{sip}", mac=mac)

    # ---------- spanning tree ----------
    def on_bpdu(self, b, proto):
        if len(b) < 4 or b[0:2] != b"\0\0":
            return
        btype = b[3]
        now = time.time()
        st = self.stp
        # A port can hear both the IEEE BPDU and Cisco's per-VLAN copy. Follow the IEEE one when it's there,
        # so per-VLAN roots don't look like root changes.
        if proto == "IEEE":
            st["ieee"] = now
        elif now - st.get("ieee", 0) < 10:
            return
        if btype == 0x80:  # TCN
            st["tc"] += 1
            st["last_tc"] = now
            self.event("warn", "stp", "Spanning tree topology change notice heard", key=None)
            return
        if len(b) < 35:
            return
        flags = b[4]
        root = f"{struct.unpack('!H', b[5:7])[0]}/{mac_s(b[7:13])}"
        bridge = f"{struct.unpack('!H', b[17:19])[0]}/{mac_s(b[19:25])}"
        cost = struct.unpack("!I", b[13:17])[0]
        if st["root"] and root != st["root"]:
            st["roots"] += 1
            self.event("warn", "stp", f"Spanning tree root changed: {st['root']} → {root}")
        tc = bool(flags & 0x01)
        if tc and not st["tc_on"]:
            st["tc"] += 1
            st["last_tc"] = now
            self.event("warn", "stp", f"Spanning tree topology change (from bridge {bridge})")
        st.update(root=root, bridge=bridge, cost=cost, tc_on=tc, seen=now,
                  proto={0: "STP", 2: "RSTP", 3: "MSTP"}.get(b[2], "STP") + (" · PVST+" if proto == "PVST+" else ""))

    def on_dtp(self, mac):
        first = self.dtp is None
        self.dtp = {"mac": mac, "t": time.time()}
        if first:
            self.event("info", "dtp", f"The switch port speaks DTP ({mac}): it could be talked into a trunk. "
                       "'switchport mode access' and 'switchport nonegotiate' turn that off.")

    # ---------- once a second: rates, storms, the gateway's MAC, saving ----------
    def tick_loop(self):
        n = 0
        while True:
            time.sleep(1)
            now = time.time()
            with self.lock:
                b, m = self.cur
                self.cur = [0, 0]
                self.rates.append((now, b, m))
                self.talkers.append(self.talk_now)
                self.talk_now = collections.Counter()
            limit = int(self.conf.get("watch_storm") or 1000)
            if b >= limit:
                self.over += 1
                if self.storm:
                    self.storm["peak"] = max(self.storm["peak"], b)
                elif self.over >= 3:
                    top = self.top_talkers(5)
                    self.storm = {"since": now - 3, "peak": b}
                    who = f", most from {top[0]['mac']} ({top[0]['pps']}/s)" if top else ""
                    self.event("crit", "storm", f"Broadcast storm: {b} broadcasts/s{who}. Look for a loop.", key=None,
                               talkers=top)
            else:
                self.over = 0
                if self.storm and b < limit * 0.7:
                    s, self.storm = self.storm, None
                    self.event("info", "storm", f"Broadcast storm over after {round(now - s['since'])} s (peak {s['peak']}/s)")
            if n % 10 == 0:
                gw = self.p.net.get("gateway")
                if gw:
                    out = subprocess.run(["ip", "neigh", "show", gw, "dev", self.iface], capture_output=True, text=True).stdout
                    mm = re.search(r"lladdr ([0-9a-f:]{17})", out)
                    if mm:
                        self.gw_mac = mm.group(1)
                with self.lock:
                    cut = now - 3600
                    self.conflicts = {k: v for k, v in self.conflicts.items() if v["t"] > cut}
            if self.save_due and n % 5 == 0:
                with self.lock:
                    evs, self.save_due = list(self.events), False
                try:
                    tmp = self.file + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(evs, f)
                    os.replace(tmp, self.file)
                except OSError:
                    pass
            n += 1

    def top_talkers(self, k=5, secs=60):
        total = collections.Counter()
        with self.lock:
            for c in list(self.talkers)[-secs:]:
                total.update(c)
            n = max(1, min(secs, len(self.talkers)))
            names = {m: v["ip"] for m, v in self.macs.items()}
        return [{"mac": m, "ip": names.get(m), "pps": round(c / n, 1), "n": c} for m, c in total.most_common(k)]

    # ---------- for the page and the probe message ----------
    def active(self):
        """Problems seen recently: what the header lamp and the displays count."""
        now = time.time()
        tr = self.trusted()
        lease = (self.p.net.get("dhcp") or {}).get("server")
        with self.lock:
            rogue = [s for s in self.dhcp.values() if s["id"] != lease and s["id"] not in tr and s.get("mac") not in tr
                     and now - s["last"] < 900]
            dups = [c for c in self.conflicts.values() if now - c["t"] < 900]
            ras = [r for r in self.ra.values() if r["ip"].lower() not in tr and r.get("mac") not in tr and now - r["last"] < 900]
        return {"rogue": len(rogue), "conflicts": len(dups), "storm": bool(self.storm), "ra": len(ras),
                "total": len(rogue) + len(dups) + bool(self.storm) + len(ras)}

    def snapshot(self):
        now = time.time()
        tr = self.trusted()
        lease = (self.p.net.get("dhcp") or {}).get("server")
        with self.lock:
            dhcp = [dict(s, trusted=s["id"] in tr or s.get("mac") in tr, lease=s["id"] == lease) for s in self.dhcp.values()]
            rates = [[round(t, 1), b, m] for t, b, m in self.rates]
            conflicts = sorted(self.conflicts.values(), key=lambda c: -c["t"])
            ra = [dict(r, trusted=r["ip"].lower() in tr or r.get("mac") in tr) for r in self.ra.values()]
            events = list(self.events)[::-1]
            hosts = len(self.macs)
        return {
            "on": bool(self.conf.get("watch")), "listening": bool(self.sock), "error": self.error, "pkts": self.pkts,
            "dhcp": sorted(dhcp, key=lambda s: (s["lease"] or s["trusted"], -s["last"])), "scan": {**self.scan, "every": self.conf.get("watch_scan")},
            "rates": rates, "storm": self.storm, "limit": self.conf.get("watch_storm"), "talkers": self.top_talkers(8),
            "conflicts": conflicts, "gw_mac": self.gw_mac, "stp": {k: v for k, v in self.stp.items() if k not in ("tc_on", "ieee")},
            "ra": ra, "dtp": self.dtp, "events": events, "hosts": hosts, "active": self.active(), "now": now,
        }

    def ip_for(self, mac):
        with self.lock:
            m = self.macs.get(mac)
        return m["ip"] if m and time.time() - m["t"] < 86400 else None

    def seen_hosts(self, limit=200):
        with self.lock:
            items = sorted(self.macs.items(), key=lambda kv: -kv[1]["t"])[:limit]
        return [{"mac": m, "ip": v["ip"], "t": v["t"]} for m, v in items]
