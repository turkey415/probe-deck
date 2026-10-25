"""Switch logs: a syslog receiver for the switches (logging host <probe>).

Messages are kept in memory (the last 5000) and written to a file per day in
/var/lib/probe-deck/logs/, flushed every few seconds to spare the SD card.
Cisco messages (%LINK-3-UPDOWN: …) are split into facility, severity and mnemonic.
The page can merge them with the Watchdog's and CS2's events on one timeline.
"""
import collections
import json
import re
import socket
import threading
import time
from datetime import date, datetime, timedelta

from appbase import App, Reply

KEEP = 5000
RATE_CAP = 200                     # messages per second per sender, past this they're counted and dropped
CISCO = re.compile(r"%([A-Z0-9_\-]+)-(\d)-([A-Z0-9_\-]+)\s*:\s*(.*)", re.S)
SEVS = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]


def parse(raw, ip):
    text = raw.decode("utf-8", "replace").strip().replace("\x00", "")
    pri = None
    m = re.match(r"<(\d{1,3})>(.*)", text, re.S)
    if m:
        pri, text = int(m.group(1)), m.group(2)
    sev = pri & 7 if pri is not None else 6
    rec = {"t": round(time.time(), 3), "ip": ip, "sev": sev, "code": None, "msg": text.strip()[:1000]}
    c = CISCO.search(text)
    if c:
        rec.update(sev=int(c.group(2)), code=f"{c.group(1)}-{c.group(2)}-{c.group(3)}", msg=c.group(4).strip()[:1000])
        head = text[:c.start()]
        # "logging origin-id hostname" puts the switch's name in front
        h = re.search(r"([A-Za-z][\w.\-]{1,62}):\s*(?:\d+:\s*)?[*.]?\w{3}\s+\d", head) or re.match(r"\s*(?:\d+:\s*)?([A-Za-z][\w.\-]{1,62}):", head)
        if h:
            rec["origin"] = h.group(1)
    return rec


class SwitchLogs(App):
    name = "logs"
    SETTINGS = {"on": True, "port": 514, "keep_days": 14, "ignore": ""}

    def check(self, k, v):
        if k == "ignore":
            ips = []
            for x in re.split(r"[,\s]+", str(v or "").strip()):
                if x and not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", x):
                    raise ValueError(f"Not an IPv4 address: {x[:30]}")
                if x and x not in ips:
                    ips.append(x)
            return ", ".join(ips[:64])
        return super().check(k, v)

    def ignored(self):
        return {x.strip() for x in self.conf["ignore"].split(",") if x.strip()}
    LIMITS = {"port": (1, 65535), "keep_days": (1, 365)}

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.msgs = collections.deque(maxlen=KEEP)
        self.pending = []
        self.sock = None
        self.error = None
        self.dropped = 0
        self.rate = {}             # ip -> [second, count]
        self.senders = {}          # ip -> {"n", "last", "worst"}
        self.restart = threading.Event()
        self.load_recent()

    def load_recent(self):
        for d in (date.today() - timedelta(days=1), date.today()):
            try:
                with open(self.dir / f"{d.isoformat()}.jsonl") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        self.msgs.append(r)
                        self.count_sender(r)
            except OSError:
                pass

    def count_sender(self, r):
        s = self.senders.setdefault(r["ip"], {"n": 0, "last": 0, "worst": 7})
        s["n"] += 1
        s["last"] = max(s["last"], r["t"])
        s["worst"] = min(s["worst"], r["sev"])
        if r.get("origin"):
            s["origin"] = r["origin"]

    def on_conf(self):
        self.restart.set()

    def start(self):
        threading.Thread(target=self.flush_loop, daemon=True).start()
        while True:
            if not self.conf["on"]:
                self.close()
                self.error = None
                self.restart.wait(5)
                self.restart.clear()
                continue
            if not self.sock:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("0.0.0.0", int(self.conf["port"])))
                    s.settimeout(1)
                    self.sock, self.error = s, None
                except OSError as e:
                    self.error = f"Can't listen on UDP {self.conf['port']}: {e.strerror or e}"
                    self.restart.wait(10)
                    self.restart.clear()
                    continue
            if self.restart.is_set():
                self.restart.clear()
                self.close()
                continue
            try:
                data, (ip, _) = self.sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                self.close()
                continue
            sec = int(time.time())
            r = self.rate.setdefault(ip, [sec, 0])
            if r[0] != sec:
                r[0], r[1] = sec, 0
            r[1] += 1
            if ip in self.ignored():
                self.dropped_ignored = getattr(self, "dropped_ignored", 0) + 1
                continue
            if r[1] > RATE_CAP:
                self.dropped += 1
                continue
            rec = parse(data, ip)
            with self.lock:
                self.msgs.append(rec)
                self.pending.append(rec)
                self.count_sender(rec)
            if rec.get("code") and rec["code"].endswith("CONFIG_I"):  # someone changed the config: Configs pulls it
                cfg = self.p.apps.get("configs")
                if cfg:
                    cfg.on_config_change(ip)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def flush_loop(self):
        last_prune = 0
        while True:
            time.sleep(5)
            with self.lock:
                batch, self.pending = self.pending, []
            if batch:
                by_day = collections.defaultdict(list)
                for r in batch:
                    by_day[datetime.fromtimestamp(r["t"]).date().isoformat()].append(r)
                for day, rs in by_day.items():
                    try:
                        with open(self.dir / f"{day}.jsonl", "a") as f:
                            f.write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rs))
                    except OSError as e:
                        self.error = f"Can't save logs: {e.strerror or e}"
            if time.time() - last_prune > 3600:
                last_prune = time.time()
                cut = (date.today() - timedelta(days=int(self.conf["keep_days"]))).isoformat()
                for f in self.dir.glob("*.jsonl"):
                    if f.stem < cut:
                        f.unlink(missing_ok=True)

    # ---------- for the page ----------
    def what(self, ip):
        """For a sender that isn't a known switch: what Host scan or Wake knows about it."""
        hs = self.p.apps.get("hosts")
        if hs:
            with hs.lock:
                h = next((h for h in hs.hosts.values() if h.get("ip") == ip), None)
            if h:
                nm = h.get("names") or {}
                bits = [nm.get("netbios") or nm.get("mdns") or nm.get("dns"), h.get("vendor")]
                return " · ".join(b for b in bits if b) or None
        return None

    def names(self):
        """switch IP -> name, from the Switches page and the probe's own neighbor."""
        out = {}
        nm = self.p.netmap
        if nm:
            with nm.lock:
                for d in nm.devices:
                    out[d["host"]] = (nm.state.get(d["id"], {}).get("name") or d.get("name") or d["host"]).split(".")[0]
        nb = self.p.neighbor or {}
        if nb.get("mgmt") and nb.get("switch"):
            out.setdefault(nb["mgmt"], nb["switch"].split(".")[0])
        return out

    def status(self):
        if not self.conf["on"]:
            return {"dot": ""}
        if self.error:
            return {"dot": "warn"}
        now = time.time()
        with self.lock:
            bad = sum(1 for r in self.msgs if r["sev"] <= 3 and now - r["t"] < 3600)
            recent = any(now - r["t"] < 600 for r in list(self.msgs)[-50:])
        return {"dot": "on" if recent else "", "badge": bad}

    def get(self, sub, arg, q, user):
        if sub == "download":
            day = q.get("day") or date.today().isoformat()
            if not re.fullmatch(r"\d{4}-\d\d-\d\d", day):
                raise ValueError("Bad day")
            names = self.names()
            try:
                lines = (self.dir / f"{day}.jsonl").read_text().splitlines()
            except OSError:
                lines = []
            out = []
            for line in lines:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                who = names.get(r["ip"]) or r.get("origin") or r["ip"]
                out.append(f"{datetime.fromtimestamp(r['t']).isoformat(sep=' ', timespec='seconds')}  {who:<16} {SEVS[r['sev']]:<7} "
                           f"{('%' + r['code'] + ': ') if r.get('code') else ''}{r['msg']}")
            return Reply(("\n".join(out) + "\n").encode(), "text/plain; charset=utf-8", f"switch-logs-{day}.txt")
        if sub:
            raise ValueError("not found")
        now = time.time()
        names = self.names()
        sev_max = int(q.get("sev", 7))
        host = q.get("host") or ""
        text = (q.get("q") or "").lower()[:80]
        with self.lock:
            msgs = list(self.msgs)
            senders = {k: dict(v) for k, v in self.senders.items()}
        per_min = [[0, 0, 0] for _ in range(60)]  # errors, warnings, other; index = minutes ago
        hour = errs = 0
        for r in msgs:
            age = now - r["t"]
            if age < 3600:
                hour += 1
                errs += r["sev"] <= 3
                per_min[int(age // 60)][0 if r["sev"] <= 3 else 1 if r["sev"] == 4 else 2] += 1
        out = []
        for r in reversed(msgs):
            if r["sev"] > sev_max or (host and r["ip"] != host):
                continue
            if text and text not in (r["msg"] + " " + (r.get("code") or "")).lower():
                continue
            out.append({**r, "who": names.get(r["ip"]) or r.get("origin") or r["ip"], "src": "switch"})
            if len(out) >= 400:
                break
        if q.get("merge") == "1" and not host:
            extra = []
            if self.p.watch:
                extra += [{"t": e["t"], "sev": {"crit": 2, "warn": 4}.get(e["sev"], 6), "msg": e["msg"], "who": "Watchdog", "src": "watchdog",
                           "code": None} for e in list(self.p.watch.events)]
            if self.p.cs:
                extra += [{"t": e["t"], "sev": {"bad": 3, "crit": 3, "warn": 4}.get(e.get("sev"), 6), "msg": e["msg"], "who": "CS2", "src": "cs",
                           "code": None} for e in list(self.p.cs.events)]
            extra = [e for e in extra if e["sev"] <= sev_max and (not text or text in e["msg"].lower())]
            out = sorted(out + extra, key=lambda r: -r["t"])[:400]
        days = sorted((f.stem for f in self.dir.glob("*.jsonl")), reverse=True)
        return {
            "on": self.conf["on"], "port": self.conf["port"], "keep_days": self.conf["keep_days"], "listening": bool(self.sock),
            "error": self.error, "dropped": self.dropped, "probe_ip": self.p.net.get("ip"), "now": now,
            "hour": hour, "errors": errs, "per_min": per_min, "msgs": out, "days": days,
            "senders": sorted(({**v, "ip": ip, "name": names.get(ip) or v.get("origin") or ip, "known": ip in names,
                                "what": None if ip in names else self.what(ip)} for ip, v in senders.items()), key=lambda s: -s["last"]),
            "ignored": sorted(self.ignored()), "dropped_ignored": getattr(self, "dropped_ignored", 0),
            "last": ({"code": msgs[-1].get("code"), "t": msgs[-1]["t"]} if msgs else None),
        }

    def forget(self, ip):
        """Delete one sender's messages, in memory and in the day files."""
        with self.lock:
            keep = [m for m in self.msgs if m["ip"] != ip]
            self.msgs.clear()
            self.msgs.extend(keep)
            self.pending = [m for m in self.pending if m["ip"] != ip]
            self.senders.pop(ip, None)
            for f in self.dir.glob("*.jsonl"):
                try:
                    lines = f.read_text().splitlines(True)
                except OSError:
                    continue
                left = [ln for ln in lines if f'"ip":"{ip}"' not in ln]
                if len(left) != len(lines):
                    tmp = f.with_suffix(".tmp")
                    tmp.write_text("".join(left))
                    tmp.replace(f)

    def post(self, action, body, user):
        ip = str(body.get("ip") or "").strip()
        if action in ("forget", "ignore", "unignore") and not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
            raise ValueError("Pick a sender")
        if action == "forget":
            self.forget(ip)
            return {}
        if action == "ignore":
            self.save_conf({"ignore": ", ".join(sorted(self.ignored() | {ip}))})
            if body.get("forget"):
                self.forget(ip)
            return {}
        if action == "unignore":
            self.save_conf({"ignore": ", ".join(sorted(self.ignored() - {ip}))})
            return {}
        if action == "clear":
            with self.lock:
                self.msgs.clear()
                self.pending = []
                self.senders = {}
            for f in self.dir.glob("*.jsonl"):
                f.unlink(missing_ok=True)
            return {}
        return super().post(action, body, user)
