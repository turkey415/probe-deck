"""Time to online: how long a port takes from link-up to passing traffic. Without portfast a
switch port listens and learns for 30 to 50 seconds first, and every PC on it waits that long.

Two ways to time it:
- live, when the link comes up while Probe Deck is running (moved to another jack on USB power,
  or the port bounced): seconds to an address, to the gateway answering, to the internet;
- from the boot logs, when the probe has just been powered up by this port (PoE): it can't be
  running when the link comes up, so the journal's "Link is Up" and dhclient's DHCPOFFER and
  "bound to" give the time to an address. dhclient's own retry timer adds a few seconds to that.
"""
import json
import re
import subprocess
import threading
import time
import urllib.request

from appbase import App, load_json, save_json

PORTFAST_SECS = 25
HIST_MAX = 30


def mono_lines(pattern):
    out = subprocess.run(["journalctl", "-b", "-o", "short-monotonic", "--no-pager", "--grep", pattern],
                         capture_output=True, text=True, timeout=20).stdout
    rows = []
    for line in out.splitlines():
        m = re.match(r"\[\s*([\d.]+)\]\s+\S+\s+(.*)", line)
        if m:
            rows.append((float(m.group(1)), m.group(2)))
    return rows


class Online(App):
    name = "online"

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.hist_file = self.dir / "history.json"
        self.history = load_json(self.hist_file, [])[-HIST_MAX:]
        self.cur = None
        self.started_mono = time.monotonic()

    def iface(self):
        return self.p.watch.iface

    def keep(self, r):
        with self.lock:
            self.history = (self.history + [r])[-HIST_MAX:]
            save_json(self.hist_file, self.history)

    # ---------- from the boot logs ----------
    def from_boot(self):
        iface = self.iface()
        ups = [(t, m) for t, m in mono_lines(f"{iface}: Link is Up") if t < self.started_mono + 5]
        if not ups:
            return None
        t_link = ups[-1][0]
        dh = [(t, m) for t, m in mono_lines("DHCPDISCOVER|DHCPOFFER|DHCPACK|bound to") if t >= t_link and "dhclient" in m]
        offer = next((t for t, m in dh if "DHCPOFFER" in m), None)
        bound = next((t for t, m in dh if "bound to" in m), None)
        first_disc = next((t for t, m in dh if "DHCPDISCOVER" in m), None)
        if bound is None and offer is None:
            return None
        boot_age = time.time() - time.monotonic()      # wall clock of boot
        r = {"mode": "boot", "t": boot_age + t_link, "link": ups[-1][1].split(": ", 1)[-1][:60],
             "offer_s": round(offer - t_link, 1) if offer else None, "ip_s": round(bound - t_link, 1) if bound else None,
             "gw_s": None, "inet_s": None, "powered_by_port": t_link < 60,
             "retry_s": round(first_disc - t_link, 1) if first_disc else None}
        r["slow"] = (r["offer_s"] or r["ip_s"] or 0) > PORTFAST_SECS
        # the probe's own boot, in seconds since power-on: the port came up, it got an address, Probe Deck was ready
        started = [t for t, m in mono_lines("Started probe-deck.service")]   # the first start this boot, not a later restart
        r["boot"] = {"link_at": round(t_link, 1), "ip_at": round(bound, 1) if bound else None,
                     "ready_at": round(started[0], 1) if started else None}
        return r

    # ---------- live ----------
    def current_ip(self):
        try:
            a = json.loads(subprocess.run(["ip", "-j", "-4", "addr", "show", self.iface()], capture_output=True, text=True, timeout=3).stdout or "[]")
            info = next((x for x in a[0]["addr_info"] if x.get("family") == "inet"), None) if a else None
            return info and info["local"]
        except (ValueError, KeyError, IndexError, OSError, subprocess.TimeoutExpired):
            return None

    def gateway(self):
        try:
            r = json.loads(subprocess.run(["ip", "-j", "-4", "route", "show", "default", "dev", self.iface()], capture_output=True, text=True, timeout=3).stdout or "[]")
            return r[0].get("gateway") if r else None
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return None

    def live(self, t_link):
        r = {"mode": "live", "t": t_link, "ip_s": None, "gw_s": None, "inet_s": None, "offer_s": None, "powered_by_port": False, "running": True}
        self.cur = r
        end = t_link + 120
        if self.p.conf.get("renew_on_link"):        # Probe Deck asks DHCP again on a new link: wait for that to finish
            t0 = time.time()
            while time.time() - t0 < 3 and not self.p.renewing:
                time.sleep(0.05)
            while self.p.renewing and time.time() < end:
                time.sleep(0.1)
        while time.time() < end and self.p.link["up"]:
            if self.current_ip():
                r["ip_s"] = round(time.time() - t_link, 1)
                break
            time.sleep(0.2)
        gw = self.gateway()
        while gw and r["ip_s"] is not None and time.time() < end and self.p.link["up"]:
            if subprocess.run(["ping", "-n", "-c", "1", "-W", "1", gw], capture_output=True, timeout=3).returncode == 0:
                r["gw_s"] = round(time.time() - t_link, 1)
                break
            time.sleep(0.25)
        url = self.p.conf.get("http_url") or "http://connectivitycheck.gstatic.com/generate_204"
        while r["gw_s"] is not None and time.time() < end and self.p.link["up"]:
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "probe-deck"}), timeout=2):
                    r["inet_s"] = round(time.time() - t_link, 1)
                    break
            except Exception:
                time.sleep(0.5)
        r["running"] = False
        r["slow"] = (r["gw_s"] or r["ip_s"] or 0) > PORTFAST_SECS
        r["gave_up"] = r["gw_s"] is None
        self.keep({k: v for k, v in r.items() if k != "running"})
        self.p.wake_ev.set()

    def start(self):
        time.sleep(3)
        try:
            b = self.from_boot()
        except Exception:
            b = None
        if b and not any(abs(h["t"] - b["t"]) < 5 for h in self.history):
            self.keep(b)
        last = self.p.link["since"]
        while True:
            time.sleep(0.2)
            lk = self.p.link
            if lk["up"] and lk["since"] != last and lk["since"] > self.p.started + 5:
                last = lk["since"]
                try:
                    self.live(lk["since"])
                except Exception as e:  # one bad measurement must not stop the next
                    print(f"time to online: {e}", flush=True)
                    if self.cur:
                        self.cur["running"] = False
            elif lk["since"] != last:
                last = lk["since"]

    # ---------- for the page and the survey ----------
    def latest(self):
        if self.cur and self.cur.get("running"):
            return dict(self.cur, ago=round(time.time() - self.cur["t"]))
        if not self.history:
            return None
        r = self.history[-1]
        return dict(r, ago=round(time.time() - r["t"]))

    def for_plug(self, plug):
        """The result for a link that came up at `plug` (the survey's jack)."""
        for r in reversed(self.history):
            if abs(r["t"] - plug) < 90:
                return r
        return None

    def status(self):
        r = self.latest()
        return {"dot": "warn"} if r and r.get("slow") else None

    def get(self, sub, arg, q, user):
        return {"latest": self.latest(), "history": self.history[::-1][:10], "portfast_secs": PORTFAST_SECS, "now": time.time()}
