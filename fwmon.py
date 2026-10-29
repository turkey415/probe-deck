"""Firewall: the LAN's firewall over SNMP v2c (read-only). CPU, memory, the state table, every
interface's traffic and errors, and the firewall's own pass and block counters.

Its SNMP has no temperature, so the firewall can send that itself: a cron job on it pipes its
temperature sysctls to this probe on UDP 47475 once a minute (one small packet, a line per
sensor such as "dev.cpu.0.temperature: 45.0C"). Only packets from one of the firewall's own
addresses count. Turning the receiver off closes the port; delete the cron job on the firewall
to stop it sending.
"""
import ipaddress
import re
import socket
import threading
import time

import pc_snmp as S
from appbase import App

TEMP_PORT = 47475
POLL = 5                 # seconds between SNMP reads
HIST_SECS = 3600         # the charts keep an hour
TEMP_STALE = 180         # a reading older than this is shown as stale (the cron job runs every minute)
DROP_RECENT = 600        # memory drops in the last 10 minutes keep the light red
CPU_WARN, CPU_CRIT = 80, 95
MEM_WARN, MEM_CRIT = 85, 95
ST_WARN, ST_CRIT = 70, 90            # percent of the state limit
TEMP_WARN, TEMP_CRIT = 75, 90
HIDE_IF = re.compile(r"^(lo|pflog|pfsync|enc)\d+$")

UCD, PF = "1.3.6.1.4.1.2021", "1.3.6.1.4.1.12325.1.200.1"
SCALARS = {
    "sysname": "1.3.6.1.2.1.1.5.0", "uptime": "1.3.6.1.2.1.25.1.1.0",
    "load1": UCD + ".10.1.3.1", "load5": UCD + ".10.1.3.2", "load15": UCD + ".10.1.3.3",
    "mem_total": UCD + ".4.5.0", "mem_avail": UCD + ".4.6.0", "mem_cache": UCD + ".4.15.0",
    "swap_total": UCD + ".4.3.0", "swap_avail": UCD + ".4.4.0",
    "cpu_user": UCD + ".11.50.0", "cpu_nice": UCD + ".11.51.0", "cpu_sys": UCD + ".11.52.0",
    "cpu_idle": UCD + ".11.53.0", "cpu_intr": UCD + ".11.56.0",
    "pf_on": PF + ".1.1.0",
    "states": PF + ".3.1.0", "st_search": PF + ".3.2.0", "st_ins": PF + ".3.3.0", "st_rem": PF + ".3.4.0",
    "st_limit": PF + ".5.1.0",
    "c_match": PF + ".2.1.0", "c_badoff": PF + ".2.2.0", "c_frag": PF + ".2.3.0",
    "c_short": PF + ".2.4.0", "c_norm": PF + ".2.5.0", "c_mem": PF + ".2.6.0",
}
SOIDS = {k: S.oid(v) for k, v in SCALARS.items()}
CORES = S.oid("1.3.6.1.2.1.25.3.3.1.2")          # hrProcessorLoad: each core, 1-minute average
ADDRS = S.oid("1.3.6.1.2.1.4.20.1.1")            # the firewall's own addresses
IF_COLS = {"descr": (S.IF, 2), "type": (S.IF, 3), "oper": (S.IF, 8), "in_err": (S.IF, 14), "out_err": (S.IF, 20),
           "in_disc": (S.IF, 13), "out_disc": (S.IF, 19), "in32": (S.IF, 10), "out32": (S.IF, 16),
           "name": (S.IFX, 1), "in64": (S.IFX, 6), "out64": (S.IFX, 10), "speed": (S.IFX, 15), "alias": (S.IFX, 18)}
PF_IF = "1.3.6.1.4.1.12325.1.200.1.8.2.1"      # the firewall's per-interface counters
PF_COLS = {"name": 2, "in_pass": (11, 19), "in_block": (12, 20), "out_pass": (13, 21), "out_block": (14, 22)}   # (IPv4, IPv6) packets
TEMP_LINE = re.compile(r"^([A-Za-z0-9_.%-]{1,64}):\s*(-?\d{1,3}(?:\.\d+)?)C\s*$")


def num(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def fnum(v):
    try:
        return float(S.text(v))
    except (TypeError, ValueError):
        return None


def sensor_label(name):
    """dev.cpu.3.temperature -> CPU 3, hw.acpi.thermal.tz0.temperature -> Board zone 0."""
    m = re.match(r"dev\.cpu\.(\d+)\.temperature$", name)
    if m:
        return f"CPU {m.group(1)}"
    m = re.match(r"hw\.acpi\.thermal\.tz(\d+)\.temperature$", name)
    if m:
        return f"Board zone {m.group(1)}"
    return name


def parse_temps(data):
    """{sysctl name: °C} and tjmax from the cron job's packet. Anything else in it is ignored."""
    temps, tjmax = {}, None
    for line in data.decode("ascii", "replace").splitlines()[:128]:
        m = TEMP_LINE.match(line.strip())
        if not m:
            continue
        name, c = m.group(1), float(m.group(2))
        if not -40 <= c <= 150:
            continue
        if name.endswith(".tjmax"):
            tjmax = c if tjmax is None else max(tjmax, c)
        elif name.endswith("temperature") and len(temps) < 64:
            temps[name] = c
    return temps, tjmax


class Firewall(App):
    name = "fw"
    SETTINGS = {"host": "", "community": "", "on": True, "temp": False}

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.cur = None            # the last good read, worked out for the page
        self.prev = None           # the raw read before it, for rates
        self.error = None
        self.ok_at = self.tried_at = None
        self.hist = []             # [t, cpu %, states, memory %, temp °C or None, {iface: [rx bps, tx bps]}]
        self.drops = []            # (t, memory drops since the last read) while they keep coming
        self.addrs = set()         # the firewall's own addresses: temperature packets must come from one
        self.temps, self.tjmax, self.temp_at, self.temp_from = {}, None, None, None
        self.temp_ignored = {}     # sender -> [count, last time], for packets not from the firewall
        self.tsock, self.temp_error = None, None
        self.wake = threading.Event()

    def check(self, k, v):
        if k == "host":
            v = str(v or "").strip()
            if v and not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", v):
                raise ValueError("Type the firewall's address, e.g. 192.168.1.1")
            return v
        if k == "community":
            v = str(v or "")
            if v and not 1 <= len(v) <= 64:
                raise ValueError("The community is 1-64 characters")
            return v
        return super().check(k, v)

    def save_conf(self, new):
        if "community" in new and new["community"] == "" and not new.get("clear_community"):
            new = {k: v for k, v in new.items() if k != "community"}   # blank in the form: keep the saved one
        if new.get("host", self.conf["host"]) != self.conf["host"]:
            self.reset()
        super().save_conf(new)

    def reset(self):
        with self.lock:
            self.cur = self.prev = self.error = self.ok_at = None
            self.hist, self.drops, self.addrs = [], [], set()

    def on_conf(self):
        self.sync_temp()
        self.wake.set()

    def community(self):
        return self.conf["community"] or (self.p.netmap.conf.get("snmp_community") if getattr(self.p, "netmap", None) else "") or ""

    # ---------- SNMP ----------
    def start(self):
        self.sync_temp()
        while True:
            self.wake.wait(POLL)
            self.wake.clear()
            if not (self.conf["on"] and self.conf["host"]):
                continue
            self.tried_at = time.time()
            try:
                self.poll()
                self.error = None
            except (S.SnmpError, OSError, ValueError) as e:
                self.error = str(e) or type(e).__name__
                if isinstance(e, S.SnmpTimeout):
                    self.error = "No answer. Check the address and community, and that SNMP is on and allows this probe."

    def poll(self):
        comm = self.community()
        if not comm:
            raise ValueError("Set the SNMP community")
        with S.Client(self.conf["host"], comm, 2, timeout=1.5, retries=1) as c:
            got = c.get(list(SOIDS.values()))
            raw = {k: got.get(o) for k, o in SOIDS.items()}
            if raw["sysname"] is None and raw["uptime"] is None:
                raise ValueError("It answered but has none of the usual system values")
            raw["cores"] = [num(v) for _, v in sorted(c.column(CORES).items())]
            cols = {k: c.column(t + (n,)) for k, (t, n) in IF_COLS.items()}
            pf = {}
            for k, n in PF_COLS.items():
                if k == "name":
                    pf[k] = c.column(S.oid(f"{PF_IF}.{n}"))
                else:
                    a, b = c.column(S.oid(f"{PF_IF}.{n[0]}")), c.column(S.oid(f"{PF_IF}.{n[1]}"))
                    pf[k] = {i: (num(a.get(i)) or 0) + (num(b.get(i)) or 0) for i in set(a) | set(b)}
            if not self.addrs or time.time() - (self.ok_at or 0) > 300:
                self.addrs = {S.text(v) for v in c.column(ADDRS).values()} - {"0.0.0.0", "127.0.0.1"}
        raw["t"] = time.time()
        raw["ifaces"] = self.read_ifaces(cols, pf)
        self.work_out(raw)

    @staticmethod
    def read_ifaces(cols, pf):
        by_name = {S.text(v): i for i, v in pf["name"].items()}
        out = []
        for idx in sorted(cols["descr"]):
            name = S.text(cols["name"].get(idx)) or S.text(cols["descr"].get(idx))
            if not name or HIDE_IF.match(name) or num(cols["type"].get(idx)) == 24:
                continue
            g = lambda k: num(cols[k].get(idx))
            wide = g("in64") is not None and g("out64") is not None
            p = by_name.get(name)
            out.append({"name": name, "alias": S.text(cols["alias"].get(idx)), "up": g("oper") == 1, "speed": g("speed") or 0,
                        "rx": g("in64") if wide else g("in32"), "tx": g("out64") if wide else g("out32"), "wide": wide,
                        "errors": (g("in_err") or 0) + (g("out_err") or 0), "drops": (g("in_disc") or 0) + (g("out_disc") or 0),
                        "in_pass": pf["in_pass"].get(p) if p else None, "in_block": pf["in_block"].get(p) if p else None,
                        "out_pass": pf["out_pass"].get(p) if p else None, "out_block": pf["out_block"].get(p) if p else None})
        return out

    def work_out(self, raw):
        prev = self.prev if self.prev and 0 < raw["t"] - self.prev["t"] < 120 else None
        dt = raw["t"] - prev["t"] if prev else None
        rate = lambda k, wide=True: None if not prev else S._delta(raw.get(k), prev.get(k), wide) / dt if raw.get(k) is not None and prev.get(k) is not None else None
        cpu = None
        keys = ("cpu_user", "cpu_nice", "cpu_sys", "cpu_idle", "cpu_intr")
        if prev and all(num(raw[k]) is not None and num(prev[k]) is not None for k in keys):
            d = {k: S._delta(raw[k], prev[k]) for k in keys}
            tot = sum(d.values())
            cpu = round(100 * (1 - d["cpu_idle"] / tot), 1) if tot else None
        cores = [c for c in raw["cores"] if c is not None]
        if cpu is None and cores:
            cpu = round(sum(cores) / len(cores), 1)
        kb = lambda k: num(raw[k]) * 1024 if num(raw[k]) is not None else None
        mem = {"total": kb("mem_total"), "avail": kb("mem_avail"), "cache": kb("mem_cache"), "swap_total": kb("swap_total"), "swap_avail": kb("swap_avail")}
        mem_pct = round(100 * (1 - mem["avail"] / mem["total"]), 1) if mem["total"] and mem["avail"] is not None else None
        states, limit = num(raw["states"]), num(raw["st_limit"])
        st_pct = round(100 * states / limit, 2) if states is not None and limit else None
        new_drops = S._delta(num(raw["c_mem"]), num(prev["c_mem"]) if prev else None, True) if prev else 0
        now = raw["t"]
        self.drops = [d for d in self.drops if now - d[0] <= DROP_RECENT] + ([(now, new_drops)] if new_drops else [])
        pi = {i["name"]: i for i in (prev or {}).get("ifaces", [])}
        ifaces = []
        for i in raw["ifaces"]:
            p = pi.get(i["name"])
            r = lambda k, wide: (S._delta(i[k], p[k], wide) / dt) if p and i[k] is not None and p[k] is not None else None
            rx, tx = r("rx", i["wide"]), r("tx", i["wide"])
            ifaces.append({**{k: i[k] for k in ("name", "alias", "up", "speed", "errors", "drops", "in_block", "out_block", "in_pass", "out_pass", "rx", "tx")},
                           "rx_bps": None if rx is None else round(rx * 8), "tx_bps": None if tx is None else round(tx * 8),
                           "block_ps": None if r("in_block", True) is None else round(r("in_block", True), 1),
                           "err_new": bool(p and i["errors"] > p["errors"])})
        temp = self.temp_now()
        cur = {"t": now, "sysname": S.text(raw["sysname"]), "uptime": num(raw["uptime"]) / 100 if num(raw["uptime"]) is not None else None,
               "load": [fnum(raw[k]) for k in ("load1", "load5", "load15")], "cpu": cpu, "cores": raw["cores"],
               "mem": mem, "mem_pct": mem_pct, "pf_on": num(raw["pf_on"]) == 1,
               "states": states, "limit": limit, "st_pct": st_pct,
               "st_ins_ps": None if rate("st_ins") is None else round(rate("st_ins"), 1),
               "st_rem_ps": None if rate("st_rem") is None else round(rate("st_rem"), 1),
               "lookups_ps": None if rate("st_search") is None else round(rate("st_search")),
               "counters": {k[2:]: num(raw[k]) for k in raw if k.startswith("c_")},
               "drops_recent": sum(d[1] for d in self.drops), "ifaces": ifaces}
        with self.lock:
            self.prev, self.cur, self.ok_at = raw, cur, now
            self.hist = [h for h in self.hist if now - h[0] <= HIST_SECS] + [[round(now, 1), cpu, states, mem_pct, temp["max"] if temp else None,
                         {i["name"]: [i["rx_bps"], i["tx_bps"]] for i in ifaces if i["up"]}]]

    # ---------- temperature from the cron job ----------
    def sync_temp(self):
        want = bool(self.conf["temp"])
        if want and not self.tsock:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", TEMP_PORT))
                self.tsock, self.temp_error = s, None
                threading.Thread(target=self.temp_loop, args=(s,), daemon=True).start()
            except OSError as e:
                self.temp_error = f"Can't listen on UDP {TEMP_PORT}: {e.strerror or e}"
        elif not want and self.tsock:
            s, self.tsock = self.tsock, None
            try:
                s.close()
            except OSError:
                pass
            self.temps, self.tjmax, self.temp_at = {}, None, None

    def from_firewall(self, ip):
        if ip in self.addrs:
            return True
        h = self.conf["host"]
        try:
            ipaddress.ip_address(h)
            return ip == h
        except ValueError:
            try:
                return ip in {a[4][0] for a in socket.getaddrinfo(h, None, socket.AF_INET)}
            except OSError:
                return False

    def temp_loop(self, s):
        while self.tsock is s:
            try:
                data, (ip, _) = s.recvfrom(4096)
            except OSError:
                return
            if not self.from_firewall(ip):
                n = self.temp_ignored.get(ip, [0, 0])
                self.temp_ignored[ip] = [n[0] + 1, time.time()]
                if len(self.temp_ignored) > 16:
                    del self.temp_ignored[min(self.temp_ignored, key=lambda k: self.temp_ignored[k][1])]
                continue
            temps, tjmax = parse_temps(data)
            if temps:
                with self.lock:
                    self.temps, self.tjmax, self.temp_at, self.temp_from = temps, tjmax, time.time(), ip

    def temp_now(self):
        """The latest reading, or None when the receiver is off or nothing has come for a while."""
        if not (self.conf["temp"] and self.temps and self.temp_at) or time.time() - self.temp_at > TEMP_STALE:
            return None
        return {"max": max(self.temps.values())}

    # ---------- for the page ----------
    def level(self):
        """ok / warn / crit, and why."""
        c = self.cur
        if not (self.conf["on"] and self.conf["host"]):
            return "", []
        if self.error or not c or time.time() - (self.ok_at or 0) > 30:
            return "crit", ["not answering"] if self.ok_at else []
        why, worst = [], "ok"

        def chk(v, warn, crit, what):
            nonlocal worst
            if v is None:
                return
            if v >= crit:
                worst = "crit"
                why.append(what)
            elif v >= warn:
                worst = "crit" if worst == "crit" else "warn"
                why.append(what)
        chk(c["cpu"], CPU_WARN, CPU_CRIT, "CPU busy")
        chk(c["mem_pct"], MEM_WARN, MEM_CRIT, "memory nearly full")
        chk(c["st_pct"], ST_WARN, ST_CRIT, "state table filling up")
        t = self.temp_now()
        chk(t and t["max"], TEMP_WARN, TEMP_CRIT, "running hot")
        if c["drops_recent"]:
            worst = "crit"
            why.append("dropping packets: out of states or memory")
        return worst, why

    def status(self):
        lv, _ = self.level()
        return {"dot": {"ok": "on", "warn": "warn", "crit": "err"}.get(lv, "")}

    def get(self, sub, arg, q, user):
        now = time.time()
        lv, why = self.level()
        t = self.temp_now()
        temp = {"on": bool(self.conf["temp"]), "port": TEMP_PORT, "error": self.temp_error, "stale_after": TEMP_STALE,
                "sensors": [{"name": k, "label": sensor_label(k), "c": v} for k, v in sorted(self.temps.items(), key=lambda kv: (len(kv[0]), kv[0]))],
                "tjmax": self.tjmax, "at": self.temp_at, "from": self.temp_from, "fresh": bool(t), "max": t["max"] if t else None,
                "ignored": [{"ip": k, "n": v[0], "at": v[1]} for k, v in sorted(self.temp_ignored.items(), key=lambda kv: -kv[1][1])][:5]}
        return {"conf": {"host": self.conf["host"], "on": self.conf["on"], "temp": self.conf["temp"], "community_set": bool(self.conf["community"]),
                         "switch_community": bool(self.p.netmap.conf.get("snmp_community")) if getattr(self.p, "netmap", None) else False},
                "cur": self.cur, "error": self.error, "ok_at": self.ok_at, "tried_at": self.tried_at, "level": lv, "why": why,
                "hist": self.hist, "temp": temp, "probe_ip": self.p.net.get("ip"), "poll": POLL,
                "limits": {"cpu": [CPU_WARN, CPU_CRIT], "mem": [MEM_WARN, MEM_CRIT], "states": [ST_WARN, ST_CRIT], "temp": [TEMP_WARN, TEMP_CRIT]}, "now": now}

    def post(self, action, body, user):
        if action == "test":
            self.wake.set()
            return {}
        return super().post(action, body, user)
