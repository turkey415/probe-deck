"""Cable test: the switch's own TDR (test cable-diagnostics tdr), read back pair by pair.

TDR takes the port's link down for a few seconds on most Catalysts. When the port is the probe's
own, the SSH session goes down with it, so the test is started in one session and read in a second
one after the link comes back.
"""
import re
import threading
import time

import swcli
from appbase import App, load_json, save_json

IF_RE = re.compile(r"[A-Za-z][\w\-]{0,30}\d+(/\d+){0,3}")
PAIR_RE = re.compile(r"Pair ([A-D])\s+(N/A|\d+)\s*(?:\+/-\s*(\d+)\s*m\w*)?\s+(?:Pair [A-D]|N/A|-)?\s*(.+?)\s*$")
HIST_MAX = 50


def parse_tdr(text):
    pairs = []
    speed = None
    for line in text.splitlines():
        m = re.match(r"\s*\S+\s+(\d+[MG]|auto|N/A)\s+Pair", line)
        if m:
            speed = m.group(1)
        p = PAIR_RE.search(line)
        if p:
            length = None if p.group(2) == "N/A" else int(p.group(2))
            pairs.append({"pair": p.group(1), "length": length, "tol": int(p.group(3)) if p.group(3) else None, "status": p.group(4).strip()})
    return speed, pairs


def verdict(pairs, speed):
    if not pairs:
        return "unknown", "The switch gave no TDR results."
    bad = [p for p in pairs if p["status"].lower() not in ("normal", "n/a", "not completed")]
    pending = [p for p in pairs if p["status"].lower() == "not completed"]
    if pairs and all(p["status"].lower() == "open" for p in pairs):
        far = max((p["length"] or 0) for p in pairs)
        return "warn", f"Every pair is open about {far} m from the switch: nothing is plugged in at the far end, or the cable is cut there."
    if bad:
        worst = bad[0]
        where = f" about {worst['length']} m from the switch" if worst["length"] is not None else ""
        return "bad", f"Pair {worst['pair']} is {worst['status'].lower()}{where}." + (" More pairs are bad too." if len(bad) > 1 else "")
    if pending and speed in ("100M", "10M"):
        return "ok", "The two pairs used at 100 Mbps are fine; the others weren't tested at this speed."
    if pending:
        return "warn", "Some pairs didn't finish the test: run it again."
    return "ok", "All four pairs are normal."


class CableTest(App):
    name = "cable"

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.hist_file = self.dir / "history.json"
        self.history = load_json(self.hist_file, [])[-HIST_MAX:]
        self.run = None

    def own_port(self):
        nb = self.p.neighbor or {}
        return nb.get("mgmt"), nb.get("port")

    def targets(self):
        cfg = self.p.apps.get("configs")
        devs = cfg.devices() if cfg else []
        return [{"key": d["key"], "name": d["name"], "host": d["host"], "console": bool(d.get("console"))} for d in devs]

    def begin(self, body, user):
        key = str(body.get("dev") or "")
        port = str(body.get("port") or "").strip()
        if not IF_RE.fullmatch(port):
            raise ValueError("Type the port, e.g. Gi0/7")
        t = next((x for x in self.targets() if x["key"] == key), None)
        if not t:
            raise ValueError("Pick a switch")
        if self.run and self.run.get("running"):
            raise ValueError("A test is already running")
        mgmt, own = self.own_port()
        mine = bool(not t["console"] and t["host"] == mgmt and own and port.lower().replace("gigabitethernet", "gi") == own.lower().replace("gigabitethernet", "gi"))
        self.run = {"running": True, "step": "starting the test", "dev": key, "switch": t["name"], "port": port, "mine": mine, "t": time.time(),
                    "by": user, "log": []}
        login = swcli.take_login(self.p, body, None if t["console"] else t["host"])
        threading.Thread(target=self.go, args=(t, port, self.run, login), daemon=True).start()

    def go(self, t, port, r, login=None):
        target = {"console": True} if t["console"] else {"host": t["host"]}
        target["login"] = login or {}

        def step(s):
            r["step"] = s
            r["log"].append([round(time.time() - r["t"], 1), s])
        try:
            step(f"logging in to {t['name']}")
            with swcli.open_session(self.p, target) as s:
                out = s.run(f"test cable-diagnostics tdr interface {port}", 30)
                if re.search(r"not supported|Invalid|failed", out, re.I):
                    raise swcli.CLIError(out.strip().splitlines()[-1][:120])
            step("TDR started: the port's link drops for a few seconds")
            time.sleep(4)
            if r["mine"]:
                step("waiting for the probe's link to come back")
                end = time.time() + 60
                while time.time() < end and not self.p.link["up"]:
                    time.sleep(1)
                time.sleep(6)  # the address and route settle
            res = None
            for i in range(6):
                step("reading the results" + (f" (try {i + 1})" if i else ""))
                try:
                    with swcli.open_session(self.p, target) as s:
                        out = s.run(f"show cable-diagnostics tdr interface {port}", 30)
                except swcli.CLIError as e:
                    if i == 5:
                        raise
                    step(f"not yet: {e}")
                    time.sleep(4)
                    continue
                speed, pairs = parse_tdr(out)
                if re.search(r"in progress", out, re.I) or (not pairs and i < 5):
                    time.sleep(3)
                    continue
                res = {"speed": speed, "pairs": pairs, "raw": out[-2000:]}
                break
            if not res:
                raise swcli.CLIError("The switch never gave results")
            level, text = verdict(res["pairs"], res["speed"])
            r.update(res, level=level, verdict=text)
            if r["mine"] and self.p.net.get("speed") and self.p.net["speed"] < 1000:
                r["verdict"] += f" The probe's link is at {self.p.net['speed']} Mbps: a gigabit port that falls back to 100 often has a bad pair."
            step("done")
        except swcli.CLIError as e:
            r.update(level="error", verdict=str(e))
            step("failed")
        except Exception as e:
            r.update(level="error", verdict=f"{type(e).__name__}: {str(e)[:120]}")
            step("failed")
        r["running"] = False
        r["secs"] = round(time.time() - r["t"], 1)
        with self.lock:
            self.history = (self.history + [{k: v for k, v in r.items() if k not in ("log", "running", "step")}])[-HIST_MAX:]
            save_json(self.hist_file, self.history)

    # ---------- for the page ----------
    def status(self):
        if self.run and self.run.get("running"):
            return {"dot": "live"}
        return None

    def get(self, sub, arg, q, user):
        mgmt, own = self.own_port()
        return {"run": self.run, "history": self.history[::-1], "targets": self.targets(), "own": {"host": mgmt, "port": own},
                "login": self.p.logins.public(), "link": {"up": self.p.link["up"], "speed": self.p.net.get("speed")}}

    def post(self, action, body, user):
        if action == "start":
            self.begin(body, user["name"])
            return {}
        if action == "clear":
            with self.lock:
                self.history = []
                save_json(self.hist_file, [])
            return {}
        return super().post(action, body, user)
