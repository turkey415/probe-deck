"""Counter-Strike network stats: what decides whether a LAN is fit for a match.

- Game servers (listed in settings, or found by a LAN broadcast like the game's own server browser):
  ICMP ping once a second, and the A2S query (the server browser's UDP request) once a second, which is
  the round trip through the game server's own network stack. A2S also gives map, players and their
  scores. Stats over 1 and 10 minutes: average, min, p95, p99, max, jitter, loss, spikes; latency while a
  speed test runs (bufferbloat); an MTU check (full-size packets without fragmenting); and the switch path
  to the server (Trace) with each port's health.
- Perf-Deck nodes: every station Perf-Deck discovery finds (UDP 52010) gets the same stats, using ICMP plus
  Perf-Deck's own UDP ping/pong as the "app reply", so two spots on the LAN can be checked without a game server.
- Player PCs: the machines saved on the Wake tab (one group, or all), pinged every 2 seconds.
- Lag events: spanning tree changes, broadcast storms, IP conflicts, link drops, latency spikes and loss.
- Steam: a TCP connect time to Steam's API, since online-auth servers need it.
"""
import collections
import errno
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time

from gamelan import scan as gscan
from gamelan import source as gsource

DEFAULTS = {"cs_servers": "", "cs_group": "", "cs_nodes": True}
PERFDECK_TAG = "perf-deck"
MAX_NODES = 16
NODE_KEEP = 120            # seconds a node that left stays on the page (dark)
NODE_DOWN = 3.5            # no reply to anything for this long = "no reply"
KEEP = 600                 # samples per target: 10 minutes at 1 a second
TICK_MS = 1000 / 64        # one CS2 server tick
# what a LAN match wants (milliseconds, percent)
PING_WARN, PING_BAD = 2.0, 5.0
JIT_WARN, JIT_BAD = 1.0, 3.0
LOSS_WARN, LOSS_BAD = 0.1, 1.0
SPIKE_MIN = 10.0           # a sample this slow, and well above the median, is a spike
SERVER_RE = re.compile(r"^\s*(?:(?P<name>[^=]{1,32}?)\s*=\s*)?(?P<host>[A-Za-z0-9][A-Za-z0-9.\-]{0,252})(?::(?P<port>\d{1,5}))?\s*$")
CS_FOLDERS = ("csgo", "cs2", "cstrike", "csco")


def parse_servers(text):
    out = []
    for line in str(text or "").splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = SERVER_RE.match(line)
        if not m:
            raise ValueError(f"Not a server: {line.strip()[:40]} (use Name = host:port)")
        port = int(m["port"] or 27015)
        if not 1 <= port <= 65535:
            raise ValueError(f"Bad port: {line.strip()[:40]}")
        out.append({"name": (m["name"] or m["host"]).strip()[:32], "host": m["host"], "port": port})
    if len(out) > 8:
        raise ValueError("At most 8 servers")
    return out


def stats(samples, now, secs):
    """Latency stats for the samples in the last `secs` seconds. Samples are (t, ms|None, loaded)."""
    pts = [s for s in samples if now - s[0] <= secs]
    ok = sorted(s[1] for s in pts if s[1] is not None)
    if not pts:
        return None
    seq = [s[1] for s in pts if s[1] is not None]
    jit = [abs(a - b) for a, b in zip(seq, seq[1:])]
    q = lambda p: ok[min(len(ok) - 1, int(round(p * (len(ok) - 1))))] if ok else None
    med = q(.5)
    spikes = sum(1 for v in seq if med is not None and v >= SPIKE_MIN and v > 3 * med)
    loaded = [s[1] for s in pts if s[2] and s[1] is not None]
    r = lambda v: None if v is None else round(v, 2)
    return {"n": len(pts), "avg": r(sum(ok) / len(ok)) if ok else None, "min": r(ok[0]) if ok else None, "p50": r(med),
            "p95": r(q(.95)), "p99": r(q(.99)), "max": r(ok[-1]) if ok else None,
            "jitter": r(sum(jit) / len(jit)) if jit else None,
            "loss": round(100 * (len(pts) - len(ok)) / len(pts), 2), "spikes": spikes,
            "loaded_avg": r(sum(loaded) / len(loaded)) if loaded else None}


def level(v, warn, bad):
    return None if v is None else "bad" if v >= bad else "warn" if v >= warn else "ok"


class Pinger:
    """ICMP echo to many hosts from one socket, timed in this process (no ping subprocess per sample)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pending = {}          # seq -> (key, sent)
        self.seq = 0
        self.raw = False
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
        except OSError:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
            self.raw = True
        self.ident = os.getpid() & 0xFFFF
        self.results = {}          # key -> callback
        threading.Thread(target=self.rx, daemon=True).start()

    @staticmethod
    def checksum(b):
        if len(b) % 2:
            b += b"\0"
        s = sum(struct.unpack(f"!{len(b) // 2}H", b))
        s = (s >> 16) + (s & 0xFFFF)
        s += s >> 16
        return ~s & 0xFFFF

    def send(self, host, key, done):
        """Ping host; done(ms or None) is called once, within about a second."""
        with self.lock:
            self.seq = (self.seq + 1) & 0xFFFF
            seq = self.seq
            self.pending[seq] = (key, time.perf_counter(), done)
        hdr = struct.pack("!BBHHH", 8, 0, 0, self.ident, seq) + b"probe-deck-cs".ljust(32, b".")
        pkt = hdr[:2] + struct.pack("!H", self.checksum(hdr)) + hdr[4:]
        try:
            self.sock.sendto(pkt, (host, 0))
        except OSError:
            with self.lock:
                self.pending.pop(seq, None)
            done(None)

    def rx(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except OSError:
                time.sleep(.1)
                continue
            t = time.perf_counter()
            if self.raw:
                data = data[(data[0] & 0x0F) * 4:]
            if len(data) < 8 or data[0] != 0:
                continue
            seq = struct.unpack("!H", data[6:8])[0]
            with self.lock:
                p = self.pending.pop(seq, None)
            if p:
                p[2](round((t - p[1]) * 1000, 3))

    def expire(self, older=1.0):
        now = time.perf_counter()
        with self.lock:
            gone = [k for k, v in self.pending.items() if now - v[1] > older]
            dead = [self.pending.pop(k) for k in gone]
        for _, _, done in dead:
            done(None)


class Target:
    def __init__(self):
        self.icmp = collections.deque(maxlen=KEEP)
        self.a2s = collections.deque(maxlen=KEEP)
        self.info = None
        self.info_at = None
        self.players = None
        self.challenge = b""
        self.mtu = None
        self.path = None
        self.ip = None
        self.name = None           # nodes only
        self.meta = {}
        self.seen = None
        self.gone = False


class CS:
    def __init__(self, probe):
        self.p = probe
        self.lock = threading.Lock()
        self.pinger = Pinger()
        self.targets = {}          # "host:port" -> Target
        self.pcs = {}              # wake id -> deque of samples
        self.events = collections.deque(maxlen=200)
        self.spiked = {}
        self.quiet = set()
        self.found = []
        self.scan_at = None
        self.started = time.time()
        self.steam = collections.deque(maxlen=120)
        self.nodes = {}            # Perf-Deck station id -> Target
        self.nsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.nsock.bind(("", 0))
        self.npend = {}            # nonce -> (node id, sent, done)
        for fn in (self.icmp_loop, self.a2s_loop, self.slow_loop, self.pc_loop, self.node_loop, self.node_rx):
            threading.Thread(target=fn, daemon=True).start()

    @property
    def conf(self):
        return self.p.conf

    def servers(self):
        try:
            return parse_servers(self.conf.get("cs_servers", ""))
        except ValueError:
            return []

    def target(self, s):
        key = f"{s['host']}:{s['port']}"
        with self.lock:
            t = self.targets.get(key)
            if not t:
                t = self.targets[key] = Target()
            return key, t

    def resolve(self, t, host):
        try:
            t.ip = socket.gethostbyname(host)
        except OSError:
            t.ip = None
        return t.ip

    def loaded(self):
        sp = getattr(self.p, "speed", {}) or {}
        return bool(sp.get("running"))

    # ---------- sampling ----------
    def icmp_loop(self):
        while True:
            t0 = time.time()
            load = self.loaded()
            for s in self.servers():
                key, t = self.target(s)
                ip = t.ip or self.resolve(t, s["host"])
                if not ip:
                    t.icmp.append((t0, None, load))
                    continue
                self.pinger.send(ip, key, lambda ms, t=t, t0=t0, load=load, name=s["name"]: self.got(t.icmp, t0, ms, load, name, "ping"))
            time.sleep(max(.05, 1 - (time.time() - t0)))
            self.pinger.expire()

    def got(self, series, t0, ms, load, name, what):
        series.append((t0, ms, load))
        # a spike or a loss burst is worth a line on the timeline (at most one per server every 10 s)
        k = (name, what)
        if time.time() - self.spiked.get(k, 0) < 10 and not (ms is not None and k in self.quiet):
            return
        recent = [s[1] for s in list(series)[-60:] if s[1] is not None]
        med = sorted(recent)[len(recent) // 2] if recent else None
        if ms is not None and med is not None and ms >= SPIKE_MIN and ms > 3 * med:
            self.spiked[k] = time.time()
            self.event("warn", "spike", f"Latency spike: {ms:.0f} ms {what} to {name} (normally {med:.1f} ms)")
        elif ms is None:
            # once when an answering target goes quiet, and once when it comes back
            last = list(series)[-3:]
            if len(last) == 3 and all(s[1] is None for s in last) and any(s[1] is not None for s in series) and k not in self.quiet:
                self.quiet.add(k)
                self.event("bad", "loss", f"No {what} answer from {name} for 3 seconds")
        if ms is not None and k in self.quiet:
            self.quiet.discard(k)
            self.event("info", "loss", f"{name} answers {what} again")

    def a2s_loop(self):
        socks = {}
        while True:
            t0 = time.time()
            load = self.loaded()
            for s in self.servers():
                key, t = self.target(s)
                ip = t.ip or self.resolve(t, s["host"])
                if not ip:
                    t.a2s.append((t0, None, load))
                    continue
                sk = socks.get(key)
                if sk is None:
                    sk = socks[key] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sk.settimeout(.9)
                ms = self.a2s_once(sk, t, ip, s["port"])
                self.got(t.a2s, t0, ms, load, s["name"], "server reply")
            for key in [k for k in socks if k not in {f"{s['host']}:{s['port']}" for s in self.servers()}]:
                socks.pop(key).close()
            time.sleep(max(.05, 1 - (time.time() - t0)))

    def a2s_once(self, sk, t, ip, port):
        """One A2S_INFO round trip, answering the challenge if the server asks for one."""
        try:
            for _ in range(2):
                sent = time.perf_counter()
                sk.sendto(gsource.info_request(t.challenge), (ip, port))
                while True:
                    data, addr = sk.recvfrom(65535)
                    if addr[0] == ip:
                        break
                ms = (time.perf_counter() - sent) * 1000
                ch = gsource.challenge_from(data)
                if ch is not None:
                    t.challenge = ch
                    continue
                info = gsource.decode_info(data)
                t.info, t.info_at = info.as_dict(), time.time()
                return round(ms, 3)
        except (OSError, gsource.DecodeError):
            pass
        return None

    def pc_loop(self):
        while True:
            t0 = time.time()
            wake = getattr(self.p, "wake", None)
            hosts = []
            if wake:
                group = self.conf.get("cs_group") or ""
                with wake.lock:
                    hosts = [dict(h) for h in wake.hosts if not group or h.get("group") == group]
            live = set()
            load = self.loaded()
            for h in hosts[:128]:
                ip, _ = wake.ip_of(h)
                live.add(h["id"])
                d = self.pcs.setdefault(h["id"], collections.deque(maxlen=300))
                if ip:
                    self.pinger.send(ip, h["id"], lambda ms, d=d, t0=t0, load=load: d.append((t0, ms, load)))
            for k in [k for k in self.pcs if k not in live]:
                self.pcs.pop(k, None)
            time.sleep(max(.1, 2 - (time.time() - t0)))

    # ---------- Perf-Deck nodes ----------
    def peers(self):
        if not self.conf.get("cs_nodes", True):
            return []
        with self.p.lock:
            ps = [dict(p) for p in getattr(self.p, "peers", {}).values()]
        return sorted(ps, key=lambda p: p["name"].lower())[:MAX_NODES]

    def node_loop(self):
        """Once a second to each Perf-Deck station: ICMP, and Perf-Deck's UDP ping (answered by its app)."""
        while True:
            t0 = time.time()
            self.node_expire()
            if not self.conf.get("cs_nodes", True):
                with self.lock:  # switched off: clear the cards at once
                    self.nodes.clear()
                time.sleep(1)
                continue
            load = self.loaded()
            live = set()
            for p in self.peers():
                live.add(p["id"])
                with self.lock:
                    t = self.nodes.get(p["id"])
                    if not t:
                        t = self.nodes[p["id"]] = Target()
                        threading.Thread(target=self.node_first, args=(t, p["ip"]), daemon=True).start()
                        if t0 - self.started > 30:  # not the ones already there at start-up
                            self.event("info", "node", f"Perf-Deck node {p['name']} ({p['ip']}) found")
                    elif t.gone:
                        self.event("info", "node", f"Perf-Deck node {p['name']} is back")
                t.name, t.ip, t.seen, t.gone = p["name"], p["ip"], t0, False
                t.meta = {"version": p.get("version"), "os": p.get("os"), "probe": bool(p.get("probe")), "jack": p.get("jack"), "iperf": p.get("running"), "busy": p.get("busy") or p.get("sweeping")}
                name = p["name"]
                self.pinger.send(p["ip"], "node:" + p["id"], lambda ms, t=t, t0=t0, load=load, name=name: self.got(t.icmp, t0, ms, load, name, "ping"))
                nonce = os.urandom(8).hex()
                with self.lock:
                    self.npend[nonce] = (p["id"], time.perf_counter(), lambda ms, t=t, t0=t0, load=load, name=name: self.got(t.a2s, t0, ms, load, name, "app reply"))
                try:
                    self.nsock.sendto(json.dumps({"app": PERFDECK_TAG, "t": "ping", "n": nonce}).encode(), (p["ip"], p["ping_port"]))
                except OSError:
                    pass
            with self.lock:
                for k, t in list(self.nodes.items()):
                    if k not in live and not t.gone:
                        t.gone = True
                        self.event("warn", "node", f"Perf-Deck node {t.name} stopped answering discovery")
                    if t.gone and t0 - (t.seen or 0) > NODE_KEEP:
                        self.nodes.pop(k)
            time.sleep(max(.05, 1 - (time.time() - t0)))

    def node_first(self, t, ip):
        """A new node gets its MTU and path at once, not at the next minute."""
        time.sleep(2)
        t.mtu = self.mtu(ip)
        t.path = self.path(ip, None)

    def node_rx(self):
        while True:
            try:
                data, addr = self.nsock.recvfrom(2048)
                t = time.perf_counter()
                m = json.loads(data)
            except (OSError, ValueError):
                continue
            if not (isinstance(m, dict) and m.get("app") == PERFDECK_TAG and m.get("t") == "pong" and isinstance(m.get("n"), str)):
                continue
            with self.lock:
                p = self.npend.pop(m["n"], None)
                node = p and self.nodes.get(p[0])
            if p and node and node.ip == addr[0]:
                p[2](round((t - p[1]) * 1000, 3))

    def node_expire(self, older=1.0):
        now = time.perf_counter()
        with self.lock:
            dead = [self.npend.pop(k) for k, v in list(self.npend.items()) if now - v[1] > older]
        for _, _, done in dead:
            done(None)

    def slow_loop(self):
        """Every minute: MTU, the path to each server and node, Steam, and the LAN server scan."""
        n = 0
        while True:
            with self.lock:
                nodes = [t for t in self.nodes.values() if not t.gone and t.ip]
            for t in nodes:
                if n % 5 == 0 or t.mtu is None:
                    t.mtu = self.mtu(t.ip)
                if n % 2 == 0 or t.path is None:
                    t.path = self.path(t.ip, t.path)
            for s in self.servers():
                key, t = self.target(s)
                ip = self.resolve(t, s["host"])
                if not ip:
                    continue
                if n % 5 == 0 or t.mtu is None:
                    t.mtu = self.mtu(ip)
                t.path = self.path(ip, t.path)
                try:
                    t.players = [vars(p) for p in (gscan.players(ip, s["port"], timeout=1.5) or [])][:32]
                except Exception:
                    pass
            self.steam_check()
            if n % 2 == 0:
                self.scan()
            n += 1
            time.sleep(60)

    @staticmethod
    def mtu(ip):
        """Can a full 1500-byte packet reach it without fragmenting? (CS2 itself sends up to about 1200.)"""
        for size in (1472, 1372, 1172):
            r = subprocess.run(["ping", "-n", "-M", "do", "-s", str(size), "-c1", "-W1", ip], capture_output=True, text=True)
            if r.returncode == 0:
                return {"ok": size == 1472, "mtu": size + 28, "at": time.time()}
        return {"ok": False, "mtu": None, "at": time.time()}

    def path(self, ip, old):
        nm = getattr(self.p, "netmap", None)
        if not nm or not nm.devices:
            return None
        try:
            r = nm.trace(ip, lambda m: None)
        except Exception as e:
            return {"error": str(e)[:160], "at": time.time()}
        hops = []
        with nm.lock:
            states = {d["id"]: nm.state.get(d["id"], {}) for d in nm.devices}
        norm = lambda n: (n or "").lower()

        def health(dev, port):
            p = next((x for x in states.get(dev, {}).get("ports") or [] if norm(x["name"]) == norm(port)), None)
            if not p:
                return None
            return {"port": p["name"], "level": p.get("level"), "busy": p.get("busy_pct"), "errors": p.get("errors"),
                    "duplex": p.get("duplex"), "speed": p.get("speed_mbps"), "flaps": p.get("flaps"), "reasons": p.get("reasons", [])[:2]}
        for h in r.get("hops", []):
            hops.append({"name": (h.get("name") or "").split(".")[0], "in": health(h["dev"], h.get("in")) if h.get("in") else None,
                         "out": health(h["dev"], h.get("out")) if h.get("out") else None})
        end = r.get("end") or {}
        last = r.get("hops", [])[-1]["dev"] if r.get("hops") else None
        return {"hops": hops, "end": health(last, end.get("port")) if last else None, "end_port": end.get("port"),
                "summary": r.get("summary"), "at": time.time()}

    def steam_check(self):
        t0 = time.perf_counter()
        try:
            with socket.create_connection(("api.steampowered.com", 443), timeout=3):
                ms = round((time.perf_counter() - t0) * 1000, 1)
        except OSError:
            ms = None
        self.steam.append((time.time(), ms))

    def scan(self):
        try:
            found = gscan.scan(timeout=1.5)
        except Exception:
            found = []
        self.found = [{"host": f.host, "port": f.port, "name": f.name, "map": f.map, "game": f.game, "folder": f.folder,
                       "players": f.players, "max": f.max_players, "cs": f.folder in CS_FOLDERS or "counter" in f.game.lower(),
                       "ping": f.ping_ms} for f in found][:32]
        self.scan_at = time.time()

    def event(self, sev, kind, msg):
        self.events.append({"t": round(time.time(), 1), "sev": sev, "kind": kind, "msg": msg})

    # ---------- the page ----------
    def lag_events(self, now):
        out = list(self.events)
        w = getattr(self.p, "watch", None)
        if w:
            with w.lock:
                for e in list(w.events):
                    if e["kind"] in ("stp", "storm", "conflict", "spoof", "dhcp", "ra") and now - e["t"] < 7200 and e["sev"] != "info":
                        out.append({"t": e["t"], "sev": "bad" if e["sev"] == "crit" else "warn", "kind": e["kind"], "msg": e["msg"]})
        for f in self.p.link.get("flaps") or []:
            if now - f < 7200:
                out.append({"t": f, "sev": "bad", "kind": "link", "msg": "The probe's own link dropped"})
        return sorted(out, key=lambda e: -e["t"])[:60]

    def verdict(self, sv, pcs, steam, nodes=()):
        reasons = []
        worst = "ok"
        bump = lambda lv: "bad" if "bad" in (lv, worst) else "warn" if "warn" in (lv, worst) else "ok"
        if not sv and not nodes:
            return {"level": None, "reasons": ["Add your game server in the settings (or pick one the scan found), or start Perf-Deck on a PC to test to it."]}
        for s in list(sv) + [n for n in nodes if not n["gone"]]:
            st = s["icmp"]["1m"] or s["a2s"]["1m"]
            if not st or st["avg"] is None:
                worst = "bad"
                reasons.append(f"{s['name']}: no answer")
                continue
            for what, v, w_, b_, unit in (("ping", st["avg"], PING_WARN, PING_BAD, " ms"), ("jitter", st["jitter"], JIT_WARN, JIT_BAD, " ms"),
                                          ("loss", st["loss"], LOSS_WARN, LOSS_BAD, "%")):
                lv = level(v, w_, b_)
                if lv and lv != "ok":
                    worst = bump(lv)
                    reasons.append(f"{s['name']}: {what} {v}{unit}")
            if st["spikes"]:
                worst = bump("warn")
                reasons.append(f"{s['name']}: {st['spikes']} latency spike{'s' if st['spikes'] > 1 else ''} in the last minute")
            if s["mtu"] and not s["mtu"]["ok"]:
                worst = bump("warn")
                reasons.append(f"{s['name']}: full-size packets don't get through unfragmented")
            if s.get("kind") != "node" and s["a2s"]["1m"] and s["a2s"]["1m"]["avg"] is None and s["icmp"]["1m"] and s["icmp"]["1m"]["avg"] is not None:
                worst = bump("warn")
                reasons.append(f"{s['name']}: answers ping but not the game query: is the server running?")
            for h in (s.get("path") or {}).get("hops") or []:
                for side in ("in", "out"):
                    p = h.get(side)
                    if p and p.get("level") in ("warn", "crit"):
                        worst = bump("warn" if p["level"] == "warn" else "bad")
                        reasons.append(f"Path: {h['name']} {p['port']}: {(p.get('reasons') or ['not healthy'])[0]}")
        bad_pcs = [p for p in pcs if p["up"] is False]
        slow = [p for p in pcs if p["st"] and p["st"]["avg"] is not None and (p["st"]["avg"] >= PING_BAD or (p["st"]["loss"] or 0) >= LOSS_BAD)]
        if bad_pcs:
            worst = bump("warn")
            reasons.append(f"{len(bad_pcs)} player PC{'s' if len(bad_pcs) > 1 else ''} not answering")
        if slow:
            worst = bump("warn")
            reasons.append(f"Slow or lossy: {', '.join(p['name'] for p in slow[:4])}")
        if steam and steam[-1] is None:
            worst = bump("warn")
            reasons.append("Steam isn't reachable (online-auth servers need it)")
        return {"level": worst, "reasons": reasons}

    def snapshot(self):
        now = time.time()
        sv = []
        for s in self.servers():
            key, t = self.target(s)
            icmp, a2s = list(t.icmp), list(t.a2s)
            sv.append({**s, "key": key, "ip": t.ip, "info": t.info, "info_age": round(now - t.info_at) if t.info_at else None,
                       "players": t.players, "mtu": t.mtu, "path": t.path,
                       "icmp": {"1m": stats(icmp, now, 60), "10m": stats(icmp, now, 600)},
                       "a2s": {"1m": stats(a2s, now, 60), "10m": stats(a2s, now, 600)},
                       "series": {"icmp": [[round(x[0], 1), x[1]] for x in icmp[-300:]], "a2s": [[round(x[0], 1), x[1]] for x in a2s[-300:]]}})
        pcs = []
        wake = getattr(self.p, "wake", None)
        if wake:
            group = self.conf.get("cs_group") or ""
            with wake.lock:
                hosts = [dict(h) for h in wake.hosts if not group or h.get("group") == group]
            for h in hosts:
                d = list(self.pcs.get(h["id"], []))
                st = stats(d, now, 60)
                last = d[-1][1] if d else None
                pcs.append({"id": h["id"], "name": h["name"], "group": h.get("group"), "ip": wake.ip_of(h)[0], "st": st,
                            "up": None if not d else last is not None, "last": last})
        nodes = []
        with self.lock:
            nl = sorted(self.nodes.items(), key=lambda kv: (kv[1].name or "").lower())
        for nid, t in nl:
            icmp, rep = list(t.icmp), list(t.a2s)
            recent = [x for x in icmp[-5:] + rep[-5:] if now - x[0] <= NODE_DOWN]
            down = not t.gone and len(recent) >= 3 and all(x[1] is None for x in recent)
            nodes.append({"kind": "node", "key": "node:" + nid, "name": t.name, "host": t.ip, "ip": t.ip, "gone": t.gone, "down": down,
                          "seen": t.seen, **t.meta, "mtu": t.mtu, "path": t.path,
                          "icmp": {"1m": stats(icmp, now, 60), "10m": stats(icmp, now, 600)},
                          "a2s": {"1m": stats(rep, now, 60), "10m": stats(rep, now, 600)},
                          "series": {"icmp": [[round(x[0], 1), x[1]] for x in icmp[-300:]], "a2s": [[round(x[0], 1), x[1]] for x in rep[-300:]]}})
        groups = sorted({h.get("group") for h in (wake.hosts if wake else []) if h.get("group")})
        steam = [x[1] for x in self.steam]
        ok = [x for x in steam if x is not None]
        return {"now": now, "servers": sv, "nodes": nodes, "nodes_on": bool(self.conf.get("cs_nodes", True)), "pcs": pcs, "groups": groups, "group": self.conf.get("cs_group") or "",
                "servers_text": self.conf.get("cs_servers", ""), "events": self.lag_events(now),
                "found": self.found, "scan_at": self.scan_at, "loaded": self.loaded(),
                "steam": {"last": steam[-1] if steam else None, "avg": round(sum(ok) / len(ok), 1) if ok else None, "checks": len(steam)},
                "verdict": self.verdict(sv, pcs, steam, nodes),
                "limits": {"ping": [PING_WARN, PING_BAD], "jitter": [JIT_WARN, JIT_BAD], "loss": [LOSS_WARN, LOSS_BAD], "tick_ms": TICK_MS}}
