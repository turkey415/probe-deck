"""DNS test: which upstream DNS server answers fastest from this jack?

Press Go: for 30 seconds every server is asked for a list of everyday names (Steam, Discord, Google…) a few
times a second, and each gets a ping. Also asked: a made-up name under example.com, to catch a server that answers names that don't
exist (some ISPs do, to show ads). Then it stops. Nothing runs in the background.

The idea comes from go-dnstester (github.com/t0mer/go-dnstester, Apache-2.0); this is a small spot check
written for Probe Deck, not its code.
"""
import random
import re
import select
import socket
import statistics
import struct
import subprocess
import threading
import time

from appbase import App, load_json, save_json

SERVERS = """Cloudflare = 1.1.1.1
Cloudflare 2 = 1.0.0.1
Google = 8.8.8.8
Google 2 = 8.8.4.4
Quad9 = 9.9.9.9
OpenDNS = 208.67.222.222
AdGuard = 94.140.14.14
CleanBrowsing = 185.228.168.9
Control D = 76.76.2.0
Level3 = 4.2.2.2
DNS.SB = 185.222.222.222"""
NAMES = ("steampowered.com, steamcontent.com, discord.com, google.com, youtube.com, twitch.tv, cloudflare.com, "
         "microsoft.com, epicgames.com, riotgames.com, battle.net, faceit.com, github.com, amazon.com")
EVERY = 0.25            # seconds between queries to one server (4 a second each)
TIMEOUT = 2.0           # no answer by then = lost
NX_EVERY = 8            # one made-up name in every 8 queries
KEEP = 10               # past runs kept
IP_RE = re.compile(r"\d{1,3}(\.\d{1,3}){3}")
NAME_RE = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
RCODES = {0: "ok", 1: "format error", 2: "server failure", 3: "no such name", 4: "not supported", 5: "refused"}


def query(qid, name):
    """A plain A query with recursion desired."""
    q = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    return struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + q + struct.pack(">HH", 1, 1)


def answer(data):
    """(id, rcode, number of A records) from a reply, or None if it isn't one."""
    if len(data) < 12:
        return None
    qid, flags, qd, an = struct.unpack(">HHHH", data[:8])
    if not flags & 0x8000:
        return None
    # count A records: walk past the question, then the answers
    i = 12
    try:
        for _ in range(qd):
            while data[i]:
                if data[i] & 0xC0 == 0xC0:
                    i += 1
                    break
                i += data[i] + 1
            i += 5
        a = 0
        for _ in range(an):
            if data[i] & 0xC0 == 0xC0:
                i += 2
            else:
                while data[i]:
                    i += data[i] + 1
                i += 1
            rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
            a += rtype == 1
            i += 10 + rdlen
    except (IndexError, struct.error):
        a = an
    return qid, flags & 0xF, a


def parse_servers(text):
    out, seen = [], set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, ip = line.rpartition("=") if "=" in line else ("", "", line)
        ip, name = ip.strip(), name.strip() or ip.strip()
        if not IP_RE.fullmatch(ip) or any(int(x) > 255 for x in ip.split(".")):
            raise ValueError(f"Not an IPv4 address: {ip[:40]}")
        if ip not in seen:
            seen.add(ip)
            out.append((name[:40], ip))
    if len(out) > 24:
        raise ValueError("Up to 24 servers")
    return out


def parse_names(text):
    names = [n.strip().lower().rstrip(".") for n in re.split(r"[,\s]+", text or "") if n.strip()]
    bad = [n for n in names if not NAME_RE.fullmatch(n)]
    if bad:
        raise ValueError(f"Not a host name: {bad[0][:60]}")
    if not 1 <= len(names) <= 40:
        raise ValueError("Between 1 and 40 names")
    return names


def ping(ip):
    """Average of 3 pings in ms, or None. Whole-second intervals: some ping builds take nothing shorter."""
    try:
        out = subprocess.run(["ping", "-n", "-q", "-c", "3", "-i", "1", "-W", "1", ip], capture_output=True, text=True, timeout=8).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"= [\d.]+/([\d.]+)/", out)
    return round(float(m.group(1)), 1) if m else None


def pct(v, p):
    if not v:
        return None
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


class DnsTest(App):
    name = "dnstest"
    VIEWER_POST = ("go", "stop")          # anyone logged in can run a spot check; settings stay admin-only
    SETTINGS = {"servers": SERVERS, "names": NAMES, "secs": 30, "dhcp": True}
    LIMITS = {"secs": (10, 120)}

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.run = None
        self.history = load_json(self.dir / "history.json", []) or []
        self.stop = threading.Event()

    def check(self, k, v):
        if k == "servers":
            parse_servers(v)
            return str(v)[:2000]
        if k == "names":
            return ", ".join(parse_names(v))
        return super().check(k, v)

    def status(self):
        if self.run and self.run["running"]:
            return {"dot": "live"}
        return {"dot": "on" if self.history else ""}

    # ---------- the test ----------
    def servers(self):
        """The list from settings, plus the DNS servers this network handed out (DHCP), marked as local."""
        out = [{"name": n, "ip": ip, "local": False} for n, ip in parse_servers(self.conf["servers"])]
        if self.conf["dhcp"]:
            have = {s["ip"] for s in out}
            for ip in self.p.net.get("dns") or []:
                if IP_RE.fullmatch(ip) and not ip.startswith("127.") and ip not in have:
                    out.insert(0, {"name": "This network (DHCP)", "ip": ip, "local": True})
                    have.add(ip)
        return out

    def begin(self, user):
        if self.run and self.run["running"]:
            raise ValueError("A test is already running")
        if not self.p.net.get("ip"):
            raise ValueError("The probe has no address")
        names = parse_names(self.conf["names"])
        rows = [{**s, "sent": 0, "ok": 0, "fail": 0, "lost": 0, "times": [], "cold": [], "nx_hit": 0, "nx_n": 0,
                 "ping": None, "rcodes": {}} for s in self.servers()]
        secs = int(self.conf["secs"])
        nb = self.p.neighbor or {}
        where = " ".join(x for x in (str(nb.get("switch") or "").split(".")[0], str(nb.get("port") or "")) if x) or None
        self.stop.clear()
        self.run = {"running": True, "t": time.time(), "secs": secs, "by": (user or {}).get("name"), "where": where, "names": len(names),
                    "rows": rows, "error": None}
        threading.Thread(target=self.go, args=(self.run, names), daemon=True).start()
        for r in rows:
            threading.Thread(target=lambda r=r: r.update(ping=ping(r["ip"])), daemon=True).start()

    def go(self, run, names):
        rows = run["rows"]
        socks, pending = [], {}            # pending: (row index, qid) -> (sent at, made-up name?)
        try:
            for i, r in enumerate(rows):
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setblocking(False)
                s.connect((r["ip"], 53))
                socks.append(s)
            by_fd = {s.fileno(): i for i, s in enumerate(socks)}
            # stagger the servers so the queries don't all leave at once
            nxt = [time.monotonic() + i * EVERY / max(1, len(rows)) for i in range(len(rows))]
            step = [random.randrange(len(names)) for _ in rows]
            end = time.monotonic() + run["secs"]
            while not self.stop.is_set():
                now = time.monotonic()
                if now >= end and not pending:
                    break
                for i, s in enumerate(socks):
                    if now < end and now >= nxt[i]:
                        nxt[i] += EVERY
                        r = rows[i]
                        made_up = r["sent"] % NX_EVERY == NX_EVERY - 1
                        if made_up:
                            name = f"pd-{random.getrandbits(40):010x}.example.com"
                        else:
                            name = names[step[i] % len(names)]
                            step[i] += 1
                        qid = random.getrandbits(16)
                        try:
                            s.send(query(qid, name))
                        except OSError as e:
                            r["rcodes"]["send error"] = r["rcodes"].get("send error", 0) + 1
                            r["err"] = str(e)[:80]
                            continue
                        r["sent"] += 1
                        pending[(i, qid)] = (now, made_up)
                for key in [k for k, v in pending.items() if now - v[0] > TIMEOUT]:
                    rows[key[0]]["lost"] += 1
                    del pending[key]
                wait = max(0.0, min([n - now for n in nxt if now < end] + [0.05]))
                ready, _, _ = select.select(socks, [], [], min(wait, 0.05))
                got = time.monotonic()
                for s in ready:
                    i = by_fd[s.fileno()]
                    while True:
                        try:
                            data = s.recv(4096)
                        except (BlockingIOError, InterruptedError):
                            break
                        except OSError:       # ICMP unreachable: nothing listening there
                            rows[i]["rcodes"]["unreachable"] = rows[i]["rcodes"].get("unreachable", 0) + 1
                            break
                        a = answer(data)
                        if not a or (i, a[0]) not in pending:
                            continue
                        sent_at, made_up = pending.pop((i, a[0]))
                        r, ms = rows[i], (got - sent_at) * 1000
                        rc = a[1]
                        if made_up:
                            r["nx_n"] += 1
                            if rc == 0 and a[2]:
                                r["nx_hit"] += 1          # it made up an address for a name that doesn't exist
                            if rc in (0, 3):
                                r["cold"].append(ms)
                            continue
                        if rc == 0:
                            r["ok"] += 1
                            r["times"].append(ms)
                        else:
                            r["fail"] += 1
                            nm = RCODES.get(rc, f"rcode {rc}")
                            r["rcodes"][nm] = r["rcodes"].get(nm, 0) + 1
        except Exception as e:  # keep what was measured
            run["error"] = str(e)[:160]
        finally:
            for s in socks:
                s.close()
            if not self.stop.is_set():          # stopped by hand: the ones in flight weren't lost
                for k in list(pending):
                    rows[k[0]]["lost"] += 1
            run["running"] = False
            run["done"] = time.time()
            self.keep(run)

    def keep(self, run):
        summary = {"t": run["t"], "where": run["where"], "by": run["by"], "secs": run["secs"],
                   "rows": [self.row(r, light=True) for r in run["rows"]]}
        ranked = self.rank(summary["rows"])
        summary["best"] = ranked[0]["ip"] if ranked else None
        with self.lock:
            self.history = ([summary] + self.history)[:KEEP]
        save_json(self.dir / "history.json", self.history)

    # ---------- results ----------
    @staticmethod
    def row(r, light=False):
        t = r["times"]
        asked = r["ok"] + r["fail"] + r["lost"]
        out = {"name": r["name"], "ip": r["ip"], "local": r["local"], "sent": r["sent"], "ok": r["ok"], "fail": r["fail"],
               "lost": r["lost"], "loss_pct": round(100 * (r["fail"] + r["lost"]) / asked, 1) if asked else None,
               "med": round(statistics.median(t), 1) if t else None, "avg": round(statistics.fmean(t), 1) if t else None,
               "min": round(min(t), 1) if t else None, "p90": round(pct(t, 90), 1) if t else None,
               "jitter": round(statistics.pstdev(t), 1) if len(t) > 1 else None,
               "cold": round(statistics.median(r["cold"]), 1) if r["cold"] else None,
               "hijack": bool(r["nx_hit"]), "nx_n": r["nx_n"], "ping": r["ping"], "rcodes": r["rcodes"], "err": r.get("err")}
        if not light:
            out["spark"] = [round(x, 1) for x in t[-60:]]
        return out

    @staticmethod
    def rank(rows):
        """Fastest typical answer first. A server that drops or refuses queries, or makes up answers, goes down."""
        def score(r):
            if r["med"] is None:
                return (2, 10 ** 9)
            bad = (r["loss_pct"] or 0) > 2 or r["hijack"]
            return (1 if bad else 0, r["med"] + 0.25 * ((r["p90"] or r["med"]) - r["med"]))
        return sorted(rows, key=score)

    def get(self, sub, arg, q, user):
        run = self.run
        rows = self.rank([self.row(r) for r in run["rows"]]) if run else []
        try:
            servers = self.servers()
        except ValueError:
            servers = []
        with self.lock:
            hist = list(self.history)
        return {"run": {k: v for k, v in run.items() if k != "rows"} if run else None, "rows": rows,
                "elapsed": round(time.time() - run["t"], 1) if run else None, "servers": self.conf["servers"],
                "names": self.conf["names"], "secs": self.conf["secs"], "dhcp": self.conf["dhcp"],
                "will_test": servers, "history": hist, "now": time.time()}

    def post(self, action, body, user):
        if action == "go":
            self.begin(user)
            return {}
        if action == "stop":
            self.stop.set()
            return {}
        if action == "forget":
            with self.lock:
                self.history = []
            save_json(self.dir / "history.json", [])
            return {}
        return super().post(action, body, user)
