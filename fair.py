"""CS2 fairness report: is one team, or one seat, worse off than the rest?

For a disputed round. Type each team's PCs (IPs, ranges, Wake or Host scan names, MACs, or a Wake group),
press Start, and for 2 minutes the probe pings every PC twice a second. A PC that ignores ping (Windows'
firewall does by default) is timed with ARP instead, which every PC answers. The game server's own numbers
come from the CS2 tab, which already pings it.

What it measures is each seat's own path (its cable, jack, switch port and NIC) from the probe. The path
from the switches to the server is shared, so it's the part where seats differ. The report compares the
teams, flags the seat that stands out (with its switch port, from the Switches page), and is kept so it
can go with the match record.
"""
import ipaddress
import re
import socket
import statistics
import struct
import threading
import time

import cs as C
from appbase import App, Reply, load_json, save_json

EVERY = 0.5             # seconds between probes to one PC
SWITCH_AFTER = 6        # probes with no ping answer before trying ARP
MAX_PER_TEAM = 32
KEEP = 20               # reports kept
FAIR_MS, SMALL_MS = 0.5, 2.0          # team medians closer than this: fair; than this: a small difference
IP_RE = re.compile(r"\d{1,3}(\.\d{1,3}){3}")
RANGE_RE = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.)(\d{1,3})\s*-\s*(\d{1,3})")
MAC_RE = re.compile(r"([0-9a-f]{2}[:-]){5}[0-9a-f]{2}|([0-9a-f]{4}\.){2}[0-9a-f]{4}", re.I)
ETH_P_ARP = 0x0806


def norm_mac(m):
    h = re.sub(r"[^0-9a-f]", "", m.lower())
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


class ArpPinger:
    """Times ARP requests: every PC answers them, firewall or not (same subnet only)."""

    def __init__(self, iface, my_ip):
        self.iface, self.my_ip = iface, my_ip
        self.mac = bytes.fromhex(open(f"/sys/class/net/{iface}/address").read().strip().replace(":", ""))
        self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ARP))
        self.sock.bind((iface, ETH_P_ARP))
        self.sock.settimeout(0.5)
        self.lock = threading.Lock()
        self.pending = {}      # ip -> (sent, done)
        self.macs = {}         # ip -> mac learned from a reply
        self.alive = True
        threading.Thread(target=self.rx, daemon=True).start()

    def send(self, ip, mac, done):
        with self.lock:
            old = self.pending.pop(ip, None)
        if old:
            old[1](None)
        dst = bytes.fromhex(mac.replace(":", "")) if mac else b"\xff" * 6
        arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, self.mac, socket.inet_aton(self.my_ip), b"\0" * 6, socket.inet_aton(ip))
        with self.lock:
            self.pending[ip] = (time.perf_counter(), done)
        try:
            self.sock.send(dst + self.mac + struct.pack("!H", ETH_P_ARP) + arp)
        except OSError:
            with self.lock:
                self.pending.pop(ip, None)
            done(None)

    def rx(self):
        while self.alive:
            try:
                f = self.sock.recv(128)
            except socket.timeout:
                continue
            except OSError:
                return
            t = time.perf_counter()
            if len(f) < 42 or struct.unpack("!H", f[20:22])[0] != 2:
                continue
            ip = socket.inet_ntoa(f[28:32])
            with self.lock:
                self.macs[ip] = ":".join(f"{b:02x}" for b in f[22:28])
                p = self.pending.pop(ip, None)
            if p:
                p[1](round((t - p[0]) * 1000, 3))

    def expire(self, older=1.0):
        now = time.perf_counter()
        with self.lock:
            dead = [self.pending.pop(ip) for ip, v in list(self.pending.items()) if now - v[0] > older]
        for _, done in dead:
            done(None)

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


def median(v):
    return statistics.median(v) if v else None


class Fairness(App):
    name = "fair"
    VIEWER_POST = ("start", "stop", "check")   # staff settling a dispute needn't be admins
    SETTINGS = {"secs": 120}
    LIMITS = {"secs": (30, 600)}

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.run = None
        self.pinger = None         # one for the app's life: its receive thread never stops
        self.stop = threading.Event()
        self.reports = load_json(self.dir / "reports.json", []) or []

    def status(self):
        if self.run and self.run["running"]:
            return {"dot": "live"}
        return {"dot": "on" if self.reports else ""}

    # ---------- who is on each team ----------
    def known(self):
        """Every PC the probe has a name for: Wake machines, then Host scan. [{name, ip, mac, group}]"""
        out = []
        wake = self.p.wake
        if wake:
            with wake.lock:
                hosts = [dict(h) for h in wake.hosts]
            for h in hosts:
                out.append({"name": h.get("name") or h["mac"], "ip": wake.ip_of(h)[0], "mac": h["mac"].lower(), "group": h.get("group") or ""})
        hs = self.p.apps.get("hosts")
        if hs:
            with hs.lock:
                hh = [dict(h) for h in hs.hosts.values()]
            have = {k["mac"] for k in out}
            for h in hh:
                if h["mac"] in have:
                    continue
                nm = h.get("names") or {}
                out.append({"name": nm.get("netbios") or nm.get("mdns") or nm.get("dns") or "", "ip": h.get("ip"), "mac": h["mac"], "group": ""})
        return out

    def resolve(self, text):
        """A team box -> [{label, ip, mac}]. Raises ValueError naming what it couldn't place."""
        known = self.known()
        by_ip = {k["ip"]: k for k in known if k["ip"]}
        out, seen = [], set()

        def add(ip, mac=None, label=None):
            if ip in seen:
                return
            seen.add(ip)
            k = by_ip.get(ip) or {}
            out.append({"label": label or k.get("name") or ip, "ip": ip, "mac": mac or k.get("mac")})

        for tok in [t.strip() for t in re.split(r"[,\n;]+", text or "") if t.strip()]:
            if tok.lower().startswith("group:") or any(k["group"] and k["group"].lower() == tok.lower() for k in known):
                g = tok.split(":", 1)[1].strip() if tok.lower().startswith("group:") else tok
                members = [k for k in known if k["group"].lower() == g.lower()]
                if not members:
                    raise ValueError(f"No Wake group called {g[:40]}")
                for k in members:
                    if not k["ip"]:
                        raise ValueError(f"{k['name']} (group {g}) has no address yet")
                    add(k["ip"], k["mac"], k["name"])
                continue
            m = RANGE_RE.fullmatch(tok)
            if m:
                a, b = int(m.group(2)), int(m.group(3))
                if not 0 <= a <= b <= 255 or b - a >= MAX_PER_TEAM:
                    raise ValueError(f"Bad range {tok[:40]}")
                for n in range(a, b + 1):
                    add(f"{m.group(1)}{n}")
                continue
            if IP_RE.fullmatch(tok):
                ipaddress.IPv4Address(tok)
                add(tok)
                continue
            if MAC_RE.fullmatch(tok):
                mac = norm_mac(tok)
                k = next((k for k in known if k["mac"] == mac), None)
                if not k or not k["ip"]:
                    raise ValueError(f"No address known for {tok[:40]} (run Host scan)")
                add(k["ip"], mac, k["name"] or None)
                continue
            hits = [k for k in known if k["name"] and k["name"].lower() == tok.lower()]
            if not hits:
                raise ValueError(f"Can't place '{tok[:40]}': type an IP, a range, a MAC, or a name from Wake or Host scan")
            if len({h["mac"] for h in hits}) > 1:
                raise ValueError(f"More than one PC is called {tok[:40]}")
            if not hits[0]["ip"]:
                raise ValueError(f"{tok[:40]} has no address yet")
            add(hits[0]["ip"], hits[0]["mac"], hits[0]["name"])
        if len(out) > MAX_PER_TEAM:
            raise ValueError(f"Up to {MAX_PER_TEAM} PCs a team")
        return out

    def where(self, mac):
        """The switch port a MAC is on, from the Switches page's last read of each port. Edge ports only."""
        nm = self.p.netmap
        if not nm or not mac:
            return None
        with nm.lock:
            devs, states = list(nm.devices), dict(nm.state)
        for d in devs:
            st = states.get(d["id"]) or {}
            for p in st.get("ports") or []:
                if mac in (p.get("macs") or []) and p.get("mac_count", 99) <= 2:
                    return f"{(st.get('name') or d.get('name') or d['host']).split('.')[0]} {p['name']}"
        return None

    # ---------- the run ----------
    def begin(self, body, user):
        if self.run and self.run["running"]:
            raise ValueError("A report is already running")
        my_ip = self.p.net.get("ip")
        if not my_ip:
            raise ValueError("The probe has no address")
        teams = []
        for i in (1, 2):
            text = str(body.get(f"team{i}") or "")[:4000]
            members = self.resolve(text)
            teams.append({"name": str(body.get(f"name{i}") or f"Team {i}")[:30], "text": text, "members": members})
        if not teams[0]["members"] and not teams[1]["members"]:
            raise ValueError("Put some PCs in a team")
        both = [m["ip"] for t in teams for m in t["members"]]
        if len(both) != len(set(both)):
            raise ValueError("A PC is on both teams")
        secs = int(body.get("secs") or self.conf["secs"])
        if not 30 <= secs <= 600:
            raise ValueError("30-600 seconds")
        server = str(body.get("server") or "")
        rows = []
        for ti, t in enumerate(teams):
            for m in t["members"]:
                rows.append({**m, "team": ti, "mode": "ping", "samples": [], "icmp_ok": 0, "sent": 0})
        nb = self.p.neighbor or {}
        self.stop.clear()
        self.run = {"running": True, "t": time.time(), "secs": secs, "by": (user or {}).get("name"), "server": server,
                    "where": " ".join(x for x in (str(nb.get("switch") or "").split(".")[0], str(nb.get("port") or "")) if x) or None,
                    "teams": [{"name": t["name"], "text": t["text"]} for t in teams], "rows": rows, "error": None}
        threading.Thread(target=self.go, args=(self.run, my_ip), daemon=True).start()

    def go(self, run, my_ip):
        rows = run["rows"]
        if not self.pinger:
            self.pinger = C.Pinger()
        pinger = self.pinger
        arp = None
        try:
            net = ipaddress.IPv4Network(f"{my_ip}/{self.p.net.get('prefix') or 24}", strict=False)
            try:
                arp = ArpPinger(self.p.watch.iface if self.p.watch else "eth0", my_ip)
            except (OSError, PermissionError) as e:
                run["arp_error"] = str(e)[:100]
            end = time.monotonic() + run["secs"]
            while time.monotonic() < end and not self.stop.is_set():
                t0 = time.monotonic()
                now = time.time()
                for r in rows:
                    if r["mode"] == "ping" and r["sent"] >= SWITCH_AFTER and not r["icmp_ok"]:
                        if arp and ipaddress.IPv4Address(r["ip"]) in net:
                            r["mode"], r["samples"] = "arp", []     # timings from the two can't be mixed
                        else:
                            r["mode"] = "none"
                    r["sent"] += 1

                    def done(ms, r=r, now=now, mode=r["mode"]):
                        if r["mode"] != mode:
                            return
                        if ms is not None and mode == "ping":
                            r["icmp_ok"] += 1
                        r["samples"].append((now, ms, False))
                    if r["mode"] == "ping":
                        pinger.send(r["ip"], r["ip"], done)
                    elif r["mode"] == "arp":
                        arp.send(r["ip"], r.get("mac"), done)
                time.sleep(max(0.05, EVERY - (time.monotonic() - t0)))
                pinger.expire(1.0)
                if arp:
                    arp.expire(1.0)
            time.sleep(1.1)
            pinger.expire(0)
            if arp:
                arp.expire(0)
                for r in rows:
                    r["mac"] = r.get("mac") or arp.macs.get(r["ip"])
        except Exception as e:  # keep what was measured
            run["error"] = str(e)[:160]
        finally:
            if arp:
                arp.close()
            run["running"] = False
            run["done"] = time.time()
            rep = self.report(run)
            with self.lock:
                self.reports = ([rep] + self.reports)[:KEEP]
            save_json(self.dir / "reports.json", self.reports)

    # ---------- the report ----------
    def server_stats(self, run, upto):
        cs = self.p.cs
        if not cs or not run["server"]:
            return None
        with cs.lock:
            t = cs.targets.get(run["server"])
        if not t:
            return None
        secs = max(1, upto - run["t"])
        return {"key": run["server"], "icmp": C.stats(list(t.icmp), upto, secs), "a2s": C.stats(list(t.a2s), upto, secs)}

    def report(self, run):
        now = run.get("done") or time.time()
        rows = []
        for r in run["rows"]:
            st = C.stats(r["samples"], now, now - run["t"] + 2) if r["samples"] else None
            rows.append({"label": r["label"], "ip": r["ip"], "mac": r.get("mac"), "team": r["team"], "mode": r["mode"],
                         "st": st, "port": self.where(r.get("mac")), "flags": []})
        # Outliers: compared with seats measured the same way (ping and ARP times differ).
        for mode in ("ping", "arp"):
            group = [x for x in rows if x["mode"] == mode and x["st"] and x["st"]["p50"] is not None]
            if len(group) < 3:
                continue
            meds = [x["st"]["p50"] for x in group]
            jits = [x["st"]["jitter"] or 0 for x in group]
            m, j = median(meds), median(jits)
            mad = median([abs(v - m) for v in meds]) * 1.4826
            jmad = median([abs(v - j) for v in jits]) * 1.4826
            for x in group:
                s = x["st"]
                if s["p50"] > m + max(0.3, 5 * mad):
                    x["flags"].append(f"latency {s['p50']:.2f} ms, the rest about {m:.2f}")
                if (s["jitter"] or 0) > j + max(0.3, 5 * jmad):
                    x["flags"].append(f"jitter {s['jitter']:.2f} ms, the rest about {j:.2f}")
                if s["loss"] > 0.5:
                    x["flags"].append(f"{s['loss']}% lost")
                if s["spikes"]:
                    x["flags"].append(f"{s['spikes']} spike{'s' if s['spikes'] > 1 else ''} over {C.SPIKE_MIN:.0f} ms")
        for x in rows:
            if x["mode"] == "none" or (x["st"] and x["st"]["p50"] is None):
                x["flags"].append("didn't answer ping, and isn't on the probe's subnet for ARP" if x["mode"] == "none" else "no answer")
        teams = []
        for ti, t in enumerate(run["teams"]):
            mine = [x for x in rows if x["team"] == ti and x["st"] and x["st"]["p50"] is not None]
            clean = [x for x in mine if not x["flags"]]
            modes = sorted({x["mode"] for x in mine})
            teams.append({"name": t["name"], "text": t["text"], "n": len([x for x in rows if x["team"] == ti]), "answering": len(mine),
                          "med": round(median([x["st"]["p50"] for x in mine]), 3) if mine else None,
                          "med_clean": round(median([x["st"]["p50"] for x in clean]), 3) if clean else None,
                          "jitter": round(statistics.fmean([x["st"]["jitter"] or 0 for x in mine]), 3) if mine else None,
                          "loss": round(max(x["st"]["loss"] for x in mine), 2) if mine else None, "modes": modes})
        verdict = self.verdict(teams, rows)
        return {"t": run["t"], "done": now, "secs": run["secs"], "by": run["by"], "where": run["where"], "teams": teams,
                "rows": rows, "server": self.server_stats(run, now), "verdict": verdict, "error": run.get("error"),
                "arp_error": run.get("arp_error")}

    @staticmethod
    def verdict(teams, rows):
        bad = [x for x in rows if x["flags"]]
        a, b = (teams + [{}, {}])[:2]
        parts, level = [], "ok"
        if a.get("med") is not None and b.get("med") is not None:
            if a["modes"] != b["modes"]:
                parts.append("The teams were timed differently (ping and ARP), so their totals can't be compared; compare the seats.")
                level = "warn"
            else:
                d = abs(a["med"] - b["med"])
                worse = a["name"] if a["med"] > b["med"] else b["name"]
                if d < FAIR_MS:
                    parts.append(f"The teams are even: within {d:.2f} ms of each other.")
                elif d < SMALL_MS:
                    parts.append(f"A small difference: {worse} is {d:.2f} ms slower. Much less than one server tick ({C.TICK_MS:.1f} ms).")
                    level = "warn"
                else:
                    parts.append(f"{worse} is {d:.2f} ms slower than the other team.")
                    level = "bad"
        if bad:
            parts.append("Stands out: " + "; ".join(f"{x['label']}{' (' + x['port'] + ')' if x['port'] else ''}: {', '.join(x['flags'])}" for x in bad[:6]) + ".")
            level = "bad" if level == "bad" or any("lost" in f or "no answer" in f for x in bad for f in x["flags"]) else "warn"
        elif rows:
            parts.append("No seat stands out.")
        return {"level": level, "text": " ".join(parts)}

    # ---------- page ----------
    def get(self, sub, arg, q, user):
        if sub == "download":
            with self.lock:
                rep = next((r for r in self.reports if str(int(r["t"])) == str(arg or q.get("t"))), None)
            if not rep:
                raise ValueError("No such report")
            return Reply(self.text(rep).encode(), "text/plain; charset=utf-8", f"fairness-{time.strftime('%Y%m%d-%H%M', time.localtime(rep['t']))}.txt")
        run = self.run
        live = self.report(run) if run and run["running"] else None
        wake = self.p.wake
        groups = sorted({h.get("group") for h in (wake.hosts if wake else []) if h.get("group")})
        cs = self.p.cs
        servers = [{"key": f"{s['host']}:{s['port']}", "name": s.get("name") or s["host"]} for s in (cs.servers() if cs else [])]
        with self.lock:
            reps = list(self.reports)
        return {"running": bool(run and run["running"]), "run": {k: v for k, v in run.items() if k != "rows"} if run else None,
                "elapsed": round(time.time() - run["t"], 1) if run else None, "live": live, "reports": reps,
                "groups": groups, "servers": servers, "secs": self.conf["secs"], "now": time.time()}

    @staticmethod
    def text(rep):
        f = lambda v: "-" if v is None else f"{v:.2f}"
        out = [f"CS2 fairness report  {time.strftime('%Y-%m-%d %H:%M', time.localtime(rep['t']))}  ({rep['secs']} s, by {rep['by']}"
               f"{', probe at ' + rep['where'] if rep['where'] else ''})", "", rep["verdict"]["text"], ""]
        for ti, t in enumerate(rep["teams"]):
            out.append(f"{t['name']}: median {f(t['med'])} ms, jitter {f(t['jitter'])} ms, worst loss {t['loss']}%  ({t['answering']} of {t['n']} answering)")
            for x in [x for x in rep["rows"] if x["team"] == ti]:
                s = x["st"] or {}
                out.append(f"  {x['label']:<18} {x['ip']:<15} {x['mode']:<4} med {f(s.get('p50'))}  p95 {f(s.get('p95'))}  jit {f(s.get('jitter'))}"
                           f"  loss {s.get('loss', '-')}%  {x['port'] or ''}  {'; '.join(x['flags'])}")
            out.append("")
        sv = rep.get("server")
        if sv:
            i, a = sv.get("icmp") or {}, sv.get("a2s") or {}
            out.append(f"Game server {sv['key']}: ping median {f(i.get('p50'))} ms, A2S median {f(a.get('p50'))} ms, loss {i.get('loss', '-')}%")
        out.append("Measured: each seat's own path from the probe (cable, jack, switch port, NIC). The path to the server is shared.")
        return "\n".join(out) + "\n"

    def post(self, action, body, user):
        if action == "start":
            self.begin(body, user)
            return {}
        if action == "stop":
            self.stop.set()
            return {}
        if action == "check":                 # what a team box resolves to, before starting
            return {"members": self.resolve(str(body.get("text") or "")[:4000])}
        if action == "forget":
            with self.lock:
                self.reports = [r for r in self.reports if str(int(r["t"])) != str(body.get("t"))] if body.get("t") else []
            save_json(self.dir / "reports.json", self.reports)
            return {}
        return super().post(action, body, user)
