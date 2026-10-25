"""Tick test: game-rate UDP (64 or 128 small packets a second, like a CS2 client) to a reflector,
timing every reply. It shows what ping can't: runs of lost packets, late replies that miss a tick,
jitter, out-of-order and duplicate packets. PorkChopCheck's test (pc_net.py), so the probe and
PorkChopCheck can test each other.

The probe also runs a reflector all the time (UDP 27777), so any PorkChopCheck box or another
probe can tick-test the path to it.
"""
import threading
import time

import pc_net as N
from appbase import App, load_json, save_json

HIST_MAX = 30
CHART_MAX = 2400                 # points sent to the page for the chart


class Tick(App):
    name = "tick"
    SETTINGS = {"reflector": True, "target": "", "rate": 64, "secs": 30, "strict": False}
    LIMITS = {"rate": (64, 128), "secs": (5, 600)}

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.refl = None
        self.refl_error = None
        self.test = None
        self.run = None
        self.hist_file = self.dir / "history.json"
        self.history = load_json(self.hist_file, [])[-HIST_MAX:]

    def check(self, k, v):
        if k == "rate":
            v = int(v)
            if v not in N.TICK_RATES:
                raise ValueError("64 or 128 a second")
            return v
        if k == "target":
            v = str(v or "").strip()
            if v and not N.parse_tick_server(v):
                raise ValueError("Type the reflector's address, e.g. 192.168.1.20 or 192.168.1.20:27777")
            return v
        return super().check(k, v)

    # ---------- the reflector ----------
    def on_conf(self):
        self.sync_reflector()

    def sync_reflector(self):
        if self.conf["reflector"] and not self.refl:
            try:
                self.refl = N.Reflector(N.TICK_PORT)
                self.refl.start()
                self.refl_error = None
            except OSError as e:
                self.refl_error = f"Can't listen on UDP {N.TICK_PORT}: {e.strerror or e}"
        elif not self.conf["reflector"] and self.refl:
            self.refl.stop()
            self.refl = None

    def start(self):
        self.sync_reflector()
        while True:
            time.sleep(1)
            r = self.run
            if r and r["running"] and (self.test.stop_ev.is_set() or time.time() - r["started"] >= r["secs"] + 1.2):
                try:
                    self.finish()
                except Exception as e:
                    print(f"tick test: {e}", flush=True)
                    r["running"] = False

    # ---------- a test ----------
    def begin(self, body, user):
        if self.run and self.run["running"]:
            raise ValueError("A test is already running")
        new = {k: body[k] for k in ("target", "rate", "secs", "strict") if k in body}
        if new:
            self.save_conf(new)
        t = N.parse_tick_server(self.conf["target"])
        if not t:
            raise ValueError("Type the reflector's address first")
        try:
            self.test = N.TickTest(self.p.net.get("ip"), t[0], t[1], int(self.conf["rate"]))
        except OSError as e:
            raise ValueError(f"Can't reach {t[0]}: {e.strerror or e}") from None
        self.test.start()
        nb = self.p.neighbor or {}
        self.run = {"running": True, "started": time.time(), "host": t[0], "port": t[1], "rate": int(self.conf["rate"]), "secs": int(self.conf["secs"]),
                    "strict": bool(self.conf["strict"]), "by": user, "switch": nb.get("switch"), "jack": nb.get("port")}

    def finish(self):
        r = self.run
        self.test.stop()
        r["running"] = False
        r["ended"] = time.time()
        out = self.figures(final=True)
        r.update(out)
        with self.lock:
            self.history = (self.history + [{k: v for k, v in r.items() if k != "points"}])[-HIST_MAX:]
            save_json(self.hist_file, self.history)

    def figures(self, final=False):
        r, t = self.run, self.test
        now = time.time()
        samples = t.buf.samples(now + (5 if final else 0), timeout=1.0)
        s = N.tick_summary(samples, r["rate"])
        head, grade = N.tick_verdict(s, r["rate"], r["strict"])
        ratings = [{"key": k, "label": N.TICK_LABELS[k], "value": v, "grade": g, "felt": N.TICK_FELT[k]} for k, v, g in N.tick_ratings(s, r["rate"], r["strict"])]
        holds = N.hold_summary(t.buf.holds(r["started"]), r["rate"])
        pts = [[round(ts - r["started"], 3), None if rtt is None else round(rtt, 3)] for ts, rtt in samples]
        if len(pts) > CHART_MAX:
            step = len(pts) / CHART_MAX
            pts = [pts[int(i * step)] for i in range(CHART_MAX)]
        return {"summary": s, "headline": head, "grade": grade, "ratings": ratings, "holds": holds, "hold_mode": t.buf.hold_mode,
                "counters": t.buf.counters(), "pace_ms": round(t.pace_err, 2), "error": t.error, "points": pts}

    # ---------- for the page ----------
    def status(self):
        if self.run and self.run["running"]:
            return {"dot": "live"}
        return {"dot": "on" if self.refl else ""}

    def get(self, sub, arg, q, user):
        r = None
        if self.run:
            r = dict(self.run)
            if r["running"]:
                r.update(self.figures())
        refl = {"on": bool(self.refl), "port": N.TICK_PORT, "error": self.refl_error,
                "count": self.refl.count if self.refl else 0,
                "peers": sorted(((ip, round(time.time() - t)) for ip, t in self.refl.peers.items()), key=lambda x: x[1])[:12] if self.refl else []}
        peers = [{"name": p.get("name"), "ip": p.get("ip")} for p in self.p.peers.values()]
        return {"run": r, "history": self.history[::-1][:15], "reflector": refl, "conf": {k: self.conf[k] for k in self.SETTINGS},
                "limits": N.TICK_LIMITS, "probe_ip": self.p.net.get("ip"), "perfdeck": peers, "now": time.time()}

    def post(self, action, body, user):
        if action == "start":
            self.begin(body, user["name"])
            return {}
        if action == "stop":
            if self.run and self.run["running"]:
                self.test.stop_ev.set()
            return {}
        if action == "clear":
            with self.lock:
                self.history = []
                save_json(self.hist_file, [])
            return {}
        return super().post(action, body, user)
