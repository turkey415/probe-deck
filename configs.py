"""Configs: each switch's running-config, pulled over SSH (or the console cable), kept when it changes.

A version is saved only when the config really changed (the "Last configuration change" lines and
the like are ignored). Versions can be labelled ("Before the event"); labelled ones are never pruned.
Any two versions can be compared. When Switch logs hears %SYS-5-CONFIG_I from a switch, that
switch is pulled a minute later.

Configs hold secrets (password hashes, SNMP communities), so only admins can read them.
"""
import difflib
import hashlib
import os
import re
import threading
import time

import swcli
from appbase import App, Reply, load_json, save_json

KEEP = 100
NOISE = re.compile(r"^(Building configuration|Current configuration ?: |! (Last configuration change|NVRAM config last updated|No configuration change)|"
                   r"ntp clock-period |! Time: )")
KEY_RE = re.compile(r"[\w.\-]{1,64}")
FILE_RE = re.compile(r"\d{8}-\d{6}\.cfg")


def clean(text):
    lines = [ln.rstrip() for ln in text.replace("\r", "").split("\n")]
    lines = [ln for ln in lines if not NOISE.match(ln)]
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).strip("\n") + "\n"


class Configs(App):
    name = "configs"
    SETTINGS = {"every": 24, "on_change": True}
    LIMITS = {"every": (0, 168)}

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        os.chmod(self.dir, 0o700)
        self.targets_file = self.dir / "targets.json"
        self.targets = load_json(self.targets_file, {})        # key -> {"via": "ssh"|"console"|"off"}
        self.index_file = self.dir / "index.json"
        self.index = load_json(self.index_file, {})            # key -> {"name", "host", "versions": [...], "last": {...}}
        self.queue = []
        self.busy = None
        self.wake_ev = threading.Event()

    # ---------- which switches ----------
    def devices(self):
        """Managed switches from the Switches page, plus the console cable."""
        out = []
        nm = self.p.netmap
        if nm:
            with nm.lock:
                for d in nm.devices:
                    if d.get("kind", "switch") == "switch":
                        out.append({"key": d["host"], "host": d["host"], "name": (nm.state.get(d["id"], {}).get("name") or d.get("name") or d["host"]).split(".")[0]})
        nb = self.p.neighbor or {}
        if nb.get("mgmt") and not any(x["host"] == nb["mgmt"] for x in out):
            out.append({"key": nb["mgmt"], "host": nb["mgmt"], "name": (nb.get("switch") or nb["mgmt"]).split(".")[0], "neighbor": True})
        out.append({"key": "console", "host": None, "name": "Console cable", "console": True})
        for d in out:
            d["via"] = (self.targets.get(d["key"]) or {}).get("via") or ("console" if d.get("console") else "ssh")
            if d.get("console") and d["key"] not in self.targets:
                d["via"] = "off"   # the cable is opt-in: it may be plugged into anything
        return out

    # ---------- pulling ----------
    def start(self):
        self.wake_ev.wait(60)
        while True:
            every = int(self.conf["every"])
            now = time.time()
            if every:
                for d in self.devices():
                    if d["via"] == "off":
                        continue
                    last = (self.index.get(d["key"]) or {}).get("last") or {}
                    if now - last.get("t", 0) > every * 3600 and d["key"] not in self.queue:
                        self.queue.append(d["key"])
            while self.queue:
                key = self.queue.pop(0)
                d = next((x for x in self.devices() if x["key"] == key), None)
                if d and d["via"] != "off":
                    self.pull(d)
            self.wake_ev.wait(60)
            self.wake_ev.clear()

    def pull(self, d, by="schedule"):
        self.busy = d["key"]
        t0 = time.time()
        ent = self.index.setdefault(d["key"], {"versions": []})
        ent.update(name=d["name"], host=d["host"])
        res = {"t": t0, "ok": False, "by": by}
        try:
            target = {"console": True} if d["via"] == "console" else {"host": d["host"]}
            with swcli.open_session(self.p, target) as s:
                text = s.run("show running-config", 120)
                if s.host:
                    ent["name"] = s.host
            if "hostname" not in text and "interface" not in text:
                raise swcli.CLIError("That doesn't look like a running-config")
            cfg = clean(text)
            digest = hashlib.sha256(cfg.encode()).hexdigest()
            vs = ent["versions"]
            if vs and vs[-1]["sha"] == digest:
                res.update(ok=True, changed=False)
            else:
                fname = time.strftime("%Y%m%d-%H%M%S", time.localtime(t0)) + ".cfg"
                folder = self.dir / d["key"]
                folder.mkdir(mode=0o700, exist_ok=True)
                fd = os.open(folder / fname, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(cfg)
                prev = (folder / vs[-1]["file"]).read_text() if vs and (folder / vs[-1]["file"]).exists() else ""
                plus, minus = self.count_changes(prev, cfg)
                vs.append({"file": fname, "t": t0, "sha": digest, "lines": cfg.count("\n"), "plus": plus, "minus": minus, "label": None, "by": by})
                self.prune(d["key"])
                res.update(ok=True, changed=True, file=fname)
        except swcli.CLIError as e:
            res["error"] = str(e)
        except Exception as e:
            res["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        res["secs"] = round(time.time() - t0, 1)
        ent["last"] = res
        with self.lock:
            save_json(self.index_file, self.index, private=True)
        self.busy = None
        return res

    @staticmethod
    def count_changes(a, b):
        plus = minus = 0
        for ln in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0):
            if ln.startswith("+") and not ln.startswith("+++"):
                plus += 1
            elif ln.startswith("-") and not ln.startswith("---"):
                minus += 1
        return plus, minus

    def prune(self, key):
        vs = self.index[key]["versions"]
        while len(vs) > KEEP:
            i = next((i for i, v in enumerate(vs[:-1]) if not v.get("label")), None)
            if i is None:
                break
            (self.dir / key / vs[i]["file"]).unlink(missing_ok=True)
            vs.pop(i)

    def on_config_change(self, ip):
        """Switch logs heard %SYS-5-CONFIG_I from this switch: pull it in a minute (changes come in bursts)."""
        if not self.conf["on_change"]:
            return
        d = next((x for x in self.devices() if x["host"] == ip and x["via"] == "ssh"), None)
        if not d:
            return

        def later():
            if d["key"] not in self.queue:
                self.queue.append(d["key"])
                self.wake_ev.set()
        t = getattr(self, "_later", {}).get(ip)
        if t:
            t.cancel()
        self._later = getattr(self, "_later", {})
        self._later[ip] = threading.Timer(60, later)
        self._later[ip].daemon = True
        self._later[ip].start()

    # ---------- for the page ----------
    def status(self):
        if self.busy:
            return {"dot": "live"}
        lasts = [(e.get("last") or {}) for e in self.index.values()]
        if any(x and not x.get("ok") for x in lasts):
            return {"dot": "warn"}
        return {"dot": "on" if lasts else ""}

    def version_text(self, key, fname):
        if not KEY_RE.fullmatch(key or "") or not FILE_RE.fullmatch(fname or ""):
            raise ValueError("not found")
        p = self.dir / key / fname
        if not p.is_file():
            raise ValueError("not found")
        return p.read_text()

    def get(self, sub, arg, q, user):
        admin = user and user["role"] == "admin"
        if sub in ("view", "diff", "download") and not admin:
            raise ValueError("Only admins can read configs (they hold secrets)")
        if sub == "view":
            return {"text": self.version_text(q.get("dev"), q.get("f"))}
        if sub == "download":
            key, f = q.get("dev"), q.get("f")
            name = (self.index.get(key) or {}).get("name") or key
            return Reply(self.version_text(key, f).encode(), "text/plain; charset=utf-8", f"{name}-{f.removesuffix('.cfg')}.txt")
        if sub == "diff":
            key = q.get("dev")
            a, b = self.version_text(key, q.get("a")), self.version_text(key, q.get("b"))
            lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), q.get("a"), q.get("b"), lineterm="", n=3))
            return {"lines": lines[:4000]}
        devs = self.devices()
        for d in devs:
            e = self.index.get(d["key"]) or {}
            d.update(last=e.get("last"), versions=e.get("versions", [])[::-1], name=e.get("name") or d["name"], busy=self.busy == d["key"],
                     queued=d["key"] in self.queue)
        return {"devices": devs, "every": self.conf["every"], "on_change": self.conf["on_change"], "login": self.p.logins.public(),
                "paramiko": bool(swcli.paramiko), "console": bool(self.p.console and self.p.console.ser), "now": time.time()}

    def post(self, action, body, user):
        key = str(body.get("dev") or "")
        if action == "via":
            via = body.get("via")
            if via not in ("ssh", "console", "off") or not KEY_RE.fullmatch(key):
                raise ValueError("Bad choice")
            self.targets[key] = {"via": via}
            save_json(self.targets_file, self.targets)
            return {}
        if action == "pull":
            d = next((x for x in self.devices() if x["key"] == key), None)
            if not d:
                raise ValueError("No such switch")
            if d["via"] == "off":
                d = dict(d, via="console" if d.get("console") else "ssh")
            if self.busy:
                raise ValueError("Another pull is running")
            return {"result": self.pull(d, by=user["name"])}
        if action == "pull_all":
            for d in self.devices():
                if d["via"] != "off" and d["key"] not in self.queue:
                    self.queue.append(d["key"])
            self.wake_ev.set()
            return {}
        if action == "label":
            e = self.index.get(key) or {}
            v = next((v for v in e.get("versions", []) if v["file"] == body.get("f")), None)
            if not v:
                raise ValueError("No such version")
            v["label"] = re.sub(r"[^\w .\-#()]", "", str(body.get("label") or ""))[:40].strip() or None
            with self.lock:
                save_json(self.index_file, self.index, private=True)
            return {}
        if action == "forget":
            e = self.index.pop(key, None)
            if e:
                for v in e.get("versions", []):
                    (self.dir / key / v["file"]).unlink(missing_ok=True)
                with self.lock:
                    save_json(self.index_file, self.index, private=True)
            return {}
        return super().post(action, body, user)
