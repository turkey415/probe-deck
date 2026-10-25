"""Jack survey: walk the hall, plug the probe into every seat's jack, and it records each one.

While armed, every new link-up is a new jack (on PoE the probe boots on each jack, so everything
here survives a reboot). The probe waits until it knows the switch port (CDP/LLDP), has an address,
and has run its checks, then saves a record against the next seat label and moves the label on
(B-14 -> B-15). A short speed test to a Perf-Deck station or iperf3 server is optional.
Each record gets a verdict: good, check or bad, with the reasons.
"""
import csv
import io
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import re
import statistics
import threading
import time
import urllib.parse
import uuid
from collections import Counter

from appbase import App, Reply, load_json, save_json

SETTLE = 8          # seconds after link-up before anything counts
GIVE_UP = 100       # record what we have after this long
SAME_JACK = 600     # the same switch port again within 10 minutes: a reboot on the same jack, not a new seat


def next_label(label):
    m = re.match(r"^(.*?)(\d+)(\D*)$", label or "")
    if not m:
        return (label or "seat") + "-2"
    n = str(int(m.group(2)) + 1).zfill(len(m.group(2)))
    return f"{m.group(1)}{n}{m.group(3)}"


class Survey(App):
    name = "survey"
    SETTINGS = {"armed": False, "next": "A1", "title": "Survey", "speed_to": "", "speed_port": 5201,
                "gxl_url": "", "gxl_key": "",  # GXL event server and its agent key, for Send to GXL
                "gxl_report": True}            # and check in with GXL like a PC agent, so it is placed at its seat
    LIMITS = {"speed_port": (1, 65535)}

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.file = self.dir / "survey.json"
        self.records = load_json(self.file, {}).get("records", [])
        self.done_plug = load_json(self.file, {}).get("done_plug")
        self.state = {"step": None, "waiting": [], "plug": None}
        self.busy = False
        self.ip_at = {}     # plug -> when the address was there (checks before that don't count)
        self.sent = None    # the last Send to GXL: when, and what GXL said
        self.gxl = {"next": 0.0, "seat": None, "error": "", "at": None, "testing": False}  # checking in with GXL

    def check(self, k, v):
        if k in ("next", "title"):
            v = re.sub(r"[^\w .\-#()/]", "", str(v or ""))[:30].strip()
            if not v:
                raise ValueError("Can't be empty")
            return v
        if k == "gxl_url":
            v = str(v or "").strip().rstrip("/")
            if v and not re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?", v):
                raise ValueError("GXL address looks like http://10.2.1.143:8000")
            return v
        if k == "gxl_key":
            v = str(v or "").strip()
            if v and not re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", v):
                raise ValueError("That does not look like a GXL agent key")
            return v
        if k == "speed_to":
            v = str(v or "").strip()
            if v and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,252}", v):
                raise ValueError("Bad speed test server")
            return v
        return super().check(k, v)

    def save(self):
        with self.lock:
            save_json(self.file, {"records": self.records, "done_plug": self.done_plug})

    # ---------- watching for a new jack ----------
    def start(self):
        while True:
            time.sleep(2)
            try:
                self.tick()
            except Exception as e:
                self.state["step"] = f"error: {e}"[:120]
            if self.conf["gxl_report"] and self.conf["gxl_url"] and self.conf["gxl_key"] and time.time() >= self.gxl["next"]:
                try:
                    self.check_in()
                except Exception as e:  # GXL down or unreachable: try again in a minute
                    self.gxl.update(error=str(e)[:160], next=time.time() + 60)

    def plug_id(self):
        lk = self.p.link
        return round(lk["since"] or 0) if lk["up"] else None

    def tick(self):
        if not self.conf["armed"] or self.busy:
            self.state.update(step=None, waiting=[])
            return
        plug = self.plug_id()
        self.state["plug"] = plug
        if plug is None:
            self.state.update(step="waiting for a jack", waiting=["link"])
            return
        if plug == self.done_plug:
            self.state.update(step=f"recorded: move to {self.conf['next']}", waiting=[])
            return
        age = time.time() - plug
        nb, net, ck = self.p.neighbor, self.p.net, self.p.checks
        waiting = []
        if age < SETTLE:
            waiting.append("the link to settle")
        if not nb:
            waiting.append("CDP / LLDP")
        if not net.get("ip"):
            waiting.append("DHCP")
        if net.get("ip") and self.ip_at.get(plug) is None:
            self.ip_at = {plug: time.time()}
        ip_at = self.ip_at.get(plug)
        if not ip_at or not ck.get("at") or ck["at"] < ip_at:
            waiting.append("DNS and internet checks")
            if net.get("ip") and age > SETTLE:
                self.p.wake_ev.set()   # run the checks now, not at the next interval
        if len(self.gw_pings(plug)) < 5:
            waiting.append("gateway pings")
        self.state.update(waiting=waiting, step=f"{self.conf['next']}: waiting for " + ", ".join(waiting) if waiting else None)
        if waiting and age < GIVE_UP:
            return
        # the same port again soon after: the probe rebooted on the same jack
        if nb and self.records:
            last = self.records[-1]
            if last.get("switch") == nb.get("switch") and last.get("port") == nb.get("port") and time.time() - last["t"] < SAME_JACK:
                self.done_plug = plug
                self.save()
                return
        self.busy = True
        threading.Thread(target=self.record, args=(plug, self.conf["next"], "auto"), daemon=True).start()

    def gw_pings(self, since):
        lbl = next((t["label"] for t in self.p.targets() if t["host"] == "GW"), None)
        with self.p.lock:
            pts = list(self.p.pings.get(lbl, [])) if lbl else []
        return [ms for t, ms in pts if t >= since + SETTLE / 2]

    # ---------- a record ----------
    def record(self, plug, label, how):
        try:
            self.state["step"] = f"{label}: recording"
            nb, net, ck, lk = dict(self.p.neighbor or {}), dict(self.p.net), dict(self.p.checks), dict(self.p.link)
            pings = self.gw_pings(plug or 0)
            ok_p = [x for x in pings if x is not None]
            r = {"id": uuid.uuid4().hex[:8], "label": label, "t": time.time(), "how": how,
                 "switch": nb.get("switch"), "port": nb.get("port"), "port_descr": nb.get("port_descr"), "vlan": nb.get("vlan"), "via": nb.get("via"),
                 "platform": nb.get("platform"), "speed": net.get("speed"), "duplex": net.get("duplex"), "ip": net.get("ip"), "prefix": net.get("prefix"),
                 "gateway": net.get("gateway"), "dhcp": (net.get("dhcp") or {}).get("server"), "plug_ip": lk.get("ip_after"),
                 "gw_ms": round(statistics.mean(ok_p), 2) if ok_p else None, "gw_loss": round(100 * (len(pings) - len(ok_p)) / len(pings)) if pings else None,
                 "dns_ok": all(d["ok"] for d in ck.get("dns") or []) if ck.get("dns") else None,
                 "inet_ok": (ck.get("http") or {}).get("ok"), "inet_ms": (ck.get("http") or {}).get("ms"), "flaps": lk.get("flaps") and len(lk["flaps"])}
            on = self.p.apps.get("online")
            res = on.for_plug(plug) if on and plug else None
            if res:
                r["online"] = {k: res.get(k) for k in ("mode", "ip_s", "offer_s", "gw_s", "inet_s", "slow", "boot")}
                if r["plug_ip"] is None:
                    r["plug_ip"] = res.get("ip_s")
            if self.conf["speed_to"] and net.get("ip"):
                self.state["step"] = f"{label}: speed test"
                r["speed_test"] = self.speed_test()
            with self.lock:
                self.records.append(r)
                if plug:
                    self.done_plug = plug
                self.conf["next"] = next_label(label)
            self.save()
            self.save_conf({})
            return r
        finally:
            self.busy = False

    def speed_test(self):
        try:
            self.p.start_speed({"server": self.conf["speed_to"], "port": self.conf["speed_port"], "mode": "both", "secs": 3,
                                "quick": True, "label": "survey"})
        except ValueError as e:
            return {"error": str(e)}
        end = time.time() + 30
        while time.time() < end and self.p.speed.get("running"):
            time.sleep(0.5)
        res = self.p.speed.get("result") or {}
        return {"down": (res.get("down") or {}).get("bps"), "up": (res.get("up") or {}).get("bps"), "error": self.p.speed.get("error")}

    # ---------- verdicts ----------
    def grade(self, r, usual_vlan, seen_ports):
        bad, check = [], []
        if not r.get("speed"):
            bad.append("no link speed")
        elif r["speed"] < 1000:
            (bad if r["speed"] < 100 else check).append(f"{r['speed']} Mbps, not gigabit")
        if (r.get("duplex") or "").lower() == "half":
            bad.append("half duplex")
        if not r.get("ip"):
            bad.append("no DHCP address")
        if r.get("gw_loss") == 100:
            bad.append("gateway doesn't answer")
        elif r.get("gw_loss"):
            check.append(f"{r['gw_loss']}% loss to the gateway")
        if r.get("gw_ms") is not None and r["gw_ms"] > 2:
            check.append(f"gateway {r['gw_ms']} ms")
        if r.get("dns_ok") is False:
            bad.append("DNS fails")
        if r.get("inet_ok") is False:
            check.append("no internet")
        ol = r.get("online") or {}
        if ol.get("slow"):
            secs = ol.get("gw_s") or ol.get("offer_s") or ol.get("ip_s")
            check.append(f"{secs} s before the port passed traffic: portfast off?")
        elif r.get("plug_ip") and r["plug_ip"] > 15:
            check.append(f"{r['plug_ip']} s to get an address")
        if not r.get("switch"):
            check.append("no CDP/LLDP: switch port unknown")
        if usual_vlan and r.get("vlan") and str(r["vlan"]) != str(usual_vlan):
            check.append(f"VLAN {r['vlan']} (most seats are on {usual_vlan})")
        if r.get("port") and seen_ports.get((r.get("switch"), r["port"]), 0) > 1:
            check.append("same switch port as another seat")
        st = r.get("speed_test") or {}
        if st.get("error"):
            check.append("speed test failed")
        elif st.get("down") and st["down"] < 500e6:
            check.append(f"speed test only {round(st['down'] / 1e6)} Mbps")
        return ("bad" if bad else "check" if check else "good"), bad + check

    def graded(self):
        with self.lock:
            recs = [dict(r) for r in self.records]
        vlans = Counter(str(r["vlan"]) for r in recs if r.get("vlan"))
        usual = vlans.most_common(1)[0][0] if vlans else None
        ports = Counter((r.get("switch"), r.get("port")) for r in recs if r.get("port"))
        for r in recs:
            r["level"], r["why"] = self.grade(r, usual, ports)
        return recs

    # ---------- for the page ----------
    def status(self):
        if not self.conf["armed"]:
            return None
        return {"dot": "live" if self.state.get("waiting") or self.busy else "on"}

    def message(self):
        """A small part of the probe message, for the T-Displays."""
        if not self.conf["armed"] and not self.records:
            return None
        last = self.graded()[-1] if self.records else None
        return {"armed": self.conf["armed"], "next": self.conf["next"], "n": len(self.records),
                "last": {"seat": last["label"], "level": last["level"]} if last else None}

    def get(self, sub, arg, q, user):
        recs = self.graded()
        if sub == "csv":
            buf = io.StringIO()
            cols = ["label", "level", "why", "switch", "port", "vlan", "speed", "duplex", "ip", "gateway", "dhcp", "plug_ip", "gw_ms", "gw_loss",
                    "dns_ok", "inet_ok", "speed_down_mbps", "speed_up_mbps", "time"]
            w = csv.writer(buf)
            w.writerow(cols)
            for r in recs:
                st = r.get("speed_test") or {}
                w.writerow([r["label"], r["level"], "; ".join(r["why"]), r.get("switch"), r.get("port"), r.get("vlan"), r.get("speed"), r.get("duplex"),
                            r.get("ip"), r.get("gateway"), r.get("dhcp"), r.get("plug_ip"), r.get("gw_ms"), r.get("gw_loss"), r.get("dns_ok"), r.get("inet_ok"),
                            round(st["down"] / 1e6) if st.get("down") else "", round(st["up"] / 1e6) if st.get("up") else "",
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["t"]))])
            name = re.sub(r"[^\w\-]+", "-", self.conf["title"]).strip("-") or "survey"
            return Reply(buf.getvalue().encode(), "text/csv; charset=utf-8", f"{name}.csv")
        conf = {k: self.conf[k] for k in self.SETTINGS if k != "gxl_key"}  # the key never goes back to the page
        conf["gxl_key_set"] = bool(self.conf["gxl_key"])
        return {"records": recs, "conf": conf, "state": dict(self.state), "busy": self.busy, "sent": self.sent, "gxl": dict(self.gxl),
                "now": time.time(), "jack": {"switch": (self.p.neighbor or {}).get("switch"), "port": (self.p.neighbor or {}).get("port")},
                "archives": sorted((f.stem for f in self.dir.glob("archive-*.json")), reverse=True)[:20]}

    # ---------- checking in with GXL as an agent ----------
    def _gxl(self, path, body):
        req = urllib.request.Request(f"{self.conf['gxl_url']}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Agent {self.conf['gxl_key']}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                raw = res.read()
        except urllib.error.HTTPError as e:
            raise ValueError("GXL does not know that agent key" if e.code == 401 else f"GXL said {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ValueError(f"Could not reach GXL: {getattr(e, 'reason', e)}") from None
        return json.loads(raw) if raw else {}

    def uid(self):
        return "probedeck-" + re.sub(r"[^0-9a-f]", "", (self.p.net.get("mac") or socket.gethostname()).lower())[:40]

    def check_in(self):
        """What a GXL agent reports: address, MAC, the switch port it sees, gateway and DNS. GXL puts the
        probe at the seat on that port, so the traffic map shows its connections there."""
        net, nb, ck = dict(self.p.net), dict(self.p.neighbor or {}), dict(self.p.checks)
        pings = [x for x in self.gw_pings(time.time() - 60 - SETTLE / 2)]
        ok = [x for x in pings if x is not None]
        body = {
            "agent_uid": self.uid(), "hostname": socket.gethostname()[:120], "os": "Probe Deck",
            "version": str(getattr(sys.modules.get("__main__"), "__version__", ""))[:20],
            "nic": {"name": self.p.watch.iface if getattr(self.p, "watch", None) else "eth0", "mac": net.get("mac"), "ip": net.get("ip"),
                    "gateway": net.get("gateway"), "link_mbps": net.get("speed") or None,
                    "duplex": net.get("duplex") if net.get("duplex") in ("full", "half") else None},
            "neighbor": {"switch": nb["switch"][:120], "port": nb["port"][:80]} if nb.get("switch") and nb.get("port") else None,
            "checks": {"gateway_ping_ms": round(statistics.mean(ok), 2) if ok else None,
                       "gateway_loss_pct": round(100 * (len(pings) - len(ok)) / len(pings)) if pings else None,
                       "dns_ok": all(d["ok"] for d in ck.get("dns") or []) if ck.get("dns") else None},
        }
        reply = self._gxl("/agent/report", body)
        self.gxl.update(seat=reply.get("seat"), error="", at=time.time(),
                        next=time.time() + max(5, min(300, int(reply.get("interval_seconds") or 60))))
        task = reply.get("task")
        if task and task.get("kind", "throughput") == "throughput" and not self.gxl["testing"]:
            self.gxl["testing"] = True
            threading.Thread(target=self.run_gxl_test, args=(task,), daemon=True).start()

    def run_gxl_test(self, task):
        """GXL asked for a speed test to its own iperf3: download, then upload, as its PC agents do."""
        host = urllib.parse.urlsplit(self.conf["gxl_url"]).hostname
        out = {"agent_uid": self.uid(), "task_id": task["id"]}
        try:
            res = {}
            for down in (True, False):
                cmd = ["iperf3", "-c", host, "-p", str(int(task["port"])), "-t", str(int(task["seconds"])), "-J", "--connect-timeout", "3000"]
                p = subprocess.run(cmd + (["-R"] if down else []), capture_output=True, text=True, timeout=int(task["seconds"]) + 20)
                d = json.loads(p.stdout or "{}")
                if d.get("error"):
                    raise ValueError(f"iperf3: {d['error']}"[:200])
                end = d.get("end") or {}
                bps = (end.get("sum_received") or {}).get("bits_per_second")
                if bps is None:
                    raise ValueError("iperf3 gave no result")
                res["down_mbps" if down else "up_mbps"] = round(bps / 1e6, 1)
                if not down:
                    res["retransmits"] = (end.get("sum_sent") or {}).get("retransmits")
            out.update(res, error="")
        except (ValueError, OSError, subprocess.TimeoutExpired) as e:
            out["error"] = str(e)[:200] or "The speed test failed"
        try:
            self._gxl("/agent/result", out)
        finally:
            self.gxl["testing"] = False
            self.gxl["next"] = 0  # report again now, so GXL hands out the next test

    def send_gxl(self):
        """Sends every seat's latest record to the GXL event server. GXL only updates seats it has."""
        url, key = self.conf["gxl_url"], self.conf["gxl_key"]
        if not url or not key:
            raise ValueError("Set the GXL address and the event's agent key first")
        latest = {}
        for r in self.graded():
            latest[r["label"].lower()] = r  # the last time each seat was recorded wins
        records = [{"label": r["label"], "switch": r.get("switch"), "port": r.get("port"), "vlan": r.get("vlan"),
                    "speed": str(r["speed"]) if r.get("speed") else None, "level": r["level"], "why": r["why"][:10]}
                   for r in latest.values()]
        if not records:
            raise ValueError("No seats recorded yet")
        body = json.dumps({"records": records, "source": f"Jack survey · {self.conf['title']}"[:60]}).encode()
        req = urllib.request.Request(f"{url}/agent/survey", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Agent {key}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                out = json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise ValueError("GXL does not know that agent key" if e.code == 401 else f"GXL said {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ValueError(f"Could not reach GXL at {url}: {getattr(e, 'reason', e)}") from None
        self.sent = {"t": time.time(), "updated": len(out.get("updated", [])), "unknown": out.get("unknown", [])[:50]}
        return self.sent

    def post(self, action, body, user):
        if action == "settings" and not body.get("gxl_key"):
            body = {k: v for k, v in body.items() if k != "gxl_key"}  # left empty: keep the saved key
        if action == "send_gxl":
            return self.send_gxl()
        if action == "arm":
            on = bool(body.get("on"))
            if on:
                # the jack the probe is on now has been seen: start with the next one, unless asked to record it too
                with self.lock:
                    self.done_plug = None if body.get("this_one") else self.plug_id()
                self.save()
            self.save_conf({"armed": on})
            return {}
        if action == "record":
            if self.busy:
                raise ValueError("Already recording")
            self.busy = True
            label = self.check("next", body.get("label") or self.conf["next"])
            r = self.record(self.plug_id(), label, "by hand")
            return {"id": r["id"]}
        if action == "delete":
            with self.lock:
                self.records = [r for r in self.records if r["id"] != body.get("id")]
            self.save()
            return {}
        if action == "relabel":
            lbl = self.check("next", body.get("label"))
            with self.lock:
                for r in self.records:
                    if r["id"] == body.get("id"):
                        r["label"] = lbl
            self.save()
            return {}
        if action == "new":
            with self.lock:
                if self.records:
                    save_json(self.dir / f"archive-{time.strftime('%Y%m%d-%H%M%S')}.json", {"title": self.conf["title"], "records": self.records})
                self.records, self.done_plug = [], self.plug_id()
            self.save()
            self.save_conf({k: body[k] for k in ("title", "next") if body.get(k)} | {"armed": False})
            return {}
        return super().post(action, body, user)
