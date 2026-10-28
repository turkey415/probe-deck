"""Packet capture: tcpdump with a filter, into a ring of files in RAM (/run), downloadable as .pcap.

The SD card is spared: a running capture lives in /run (tmpfs). "Keep" copies it to the card.
Watchdog alarms (rogue DHCP, storm, IP conflict, spoofing, spanning tree, rogue IPv6 router) can start
a short capture by themselves, with a filter for that problem, and keep it.
"""
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from appbase import App, Reply

RUN_DIR = Path("/run/probe-deck/capture")
KEEP_MAX_FILES = 30
KEEP_MAX_BYTES = 512 * 1024 * 1024
AUTO_FILTERS = {"dhcp": "udp port 67 or udp port 68", "storm": "broadcast or multicast", "conflict": "arp", "spoof": "arp",
                "stp": "stp", "ra": "icmp6",
                # switch alarms: a loop or a bridged switch shows up here as BPDUs and flooding
                "errdisable": "stp or broadcast or multicast", "stpguard": "stp", "macflap": "broadcast or multicast"}
NAME_RE = re.compile(r"[\w.\-]{1,80}\.pcap")


def merge_pcaps(files, out):
    """Classic pcap files from one tcpdump run share a header: keep the first, append the rest's records."""
    with open(out, "wb") as o:
        first = True
        for f in files:
            with open(f, "rb") as i:
                head = i.read(24)
                if len(head) < 24:
                    continue
                if first:
                    o.write(head)
                    first = False
                shutil.copyfileobj(i, o)


class Capture(App):
    name = "capture"
    SETTINGS = {"ring_mb": 32, "snaplen": 0, "auto": True, "auto_secs": 30, "live": True}
    LIMITS = {"ring_mb": (4, 128), "snaplen": (0, 65535), "auto_secs": (5, 300)}

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.keep_dir = self.dir
        self.proc = None
        self.cur = None        # the running (or last) capture
        self.lines = deque(maxlen=200)
        self.auto_last = {}
        try:
            RUN_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def iface(self):
        return self.p.watch.iface

    def check_filter(self, flt):
        flt = str(flt or "").strip()
        if len(flt) > 300 or "\n" in flt:
            raise ValueError("Filter too long")
        if flt:
            r = subprocess.run(["tcpdump", "-d", "-i", self.iface(), flt], capture_output=True, text=True, timeout=10)
            if r.returncode:
                raise ValueError("Bad filter: " + (r.stderr.strip().splitlines() or ["?"])[-1][:150])
        return flt

    # ---------- run ----------
    def begin(self, flt, secs, why, user, keep=False):
        flt = self.check_filter(flt)
        secs = int(secs or 0)
        if not 0 <= secs <= 3600:
            raise ValueError("Seconds must be 0 (until stopped) to 3600")
        with self.lock:
            if self.proc and self.proc.poll() is None:
                raise ValueError("A capture is already running")
            for f in RUN_DIR.glob("ring*"):
                f.unlink(missing_ok=True)
            n_files = 4
            per = max(1, int(self.conf["ring_mb"]) // n_files)
            args = ["tcpdump", "-i", self.iface(), "-n", "-U", "-Z", "root", "-C", str(per), "-W", str(n_files), "-w", str(RUN_DIR / "ring")]
            if int(self.conf["snaplen"]):
                args += ["-s", str(int(self.conf["snaplen"]))]
            live = bool(self.conf["live"])
            if live:
                args += ["--print", "-l", "-tttt"]
            if flt:
                args.append(flt)
            self.lines.clear()
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE if live else subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                         start_new_session=True)
            self.cur = {"started": time.time(), "filter": flt, "secs": secs, "why": why, "by": user, "running": True, "packets": 0,
                        "stopped": None, "summary": None, "kept": None, "keep": keep, "error": None}
            cur = self.cur
        if live:
            threading.Thread(target=self.read_lines, args=(self.proc, cur), daemon=True).start()
        threading.Thread(target=self.watch, args=(self.proc, cur), daemon=True).start()

    def read_lines(self, proc, cur):
        for line in proc.stdout:
            cur["packets"] += 1
            self.lines.append(line.rstrip()[:300])

    def watch(self, proc, cur):
        while proc.poll() is None:
            if cur["secs"] and time.time() - cur["started"] >= cur["secs"]:
                self.end(proc)
                break
            time.sleep(0.5)
        try:
            err = proc.stderr.read()
        except (OSError, ValueError):
            err = ""
        m = re.search(r"(\d+) packets captured", err)
        d = re.search(r"(\d+) packets dropped by kernel", err)
        cur.update(running=False, stopped=time.time(), summary={"captured": int(m.group(1)) if m else None, "dropped": int(d.group(1)) if d else None})
        if not m and proc.returncode not in (0, None, -2, 130):
            cur["error"] = (err.strip().splitlines() or ["tcpdump stopped"])[-1][:160]
        if cur["keep"]:
            try:
                self.keep(cur)
            except OSError as e:
                cur["error"] = f"Couldn't keep it: {e.strerror or e}"

    @staticmethod
    def end(proc):
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except OSError:
            pass
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def ring_files(self):
        return sorted(RUN_DIR.glob("ring*"), key=lambda f: f.stat().st_mtime)

    def keep(self, cur):
        files = self.ring_files()
        if not files:
            raise OSError("nothing captured")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(cur["started"]))
        tag = re.sub(r"[^\w\-]+", "-", cur["why"] or "capture").strip("-")[:30] or "capture"
        out = self.keep_dir / f"{stamp}-{tag}.pcap"
        merge_pcaps(files, out)
        cur["kept"] = out.name
        self.prune()
        return out.name

    def prune(self):
        kept = sorted(self.keep_dir.glob("*.pcap"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in kept)
        while kept and (len(kept) > KEEP_MAX_FILES or total > KEEP_MAX_BYTES):
            f = kept.pop(0)
            total -= f.stat().st_size
            f.unlink(missing_ok=True)

    # ---------- the watchdog starts one by itself ----------
    def on_alert(self, ev):
        kind = ev.get("kind")
        if not self.conf["auto"] or kind not in AUTO_FILTERS:
            return
        if time.time() - self.auto_last.get(kind, 0) < 600:
            return
        if self.proc and self.proc.poll() is None:
            return
        self.auto_last[kind] = time.time()
        try:
            self.begin(AUTO_FILTERS[kind], int(self.conf["auto_secs"]), f"auto-{kind}", "watchdog", keep=True)
        except (ValueError, OSError) as e:
            print(f"auto capture: {e}", flush=True)

    # ---------- for the page ----------
    def status(self):
        if self.proc and self.proc.poll() is None:
            return {"dot": "live"}
        return {"dot": ""}

    def get(self, sub, arg, q, user):
        if sub == "file":
            if arg == "current":
                files = self.ring_files()
                if not files:
                    raise ValueError("not found")
                tmp = RUN_DIR / "merged.pcap"
                merge_pcaps(files, tmp)
                body = tmp.read_bytes()
                tmp.unlink(missing_ok=True)
                return Reply(body, "application/vnd.tcpdump.pcap", f"probe-capture-{time.strftime('%Y%m%d-%H%M%S')}.pcap")
            if not arg or not NAME_RE.fullmatch(arg) or not (self.keep_dir / arg).is_file():
                raise ValueError("not found")
            return Reply(self.keep_dir / arg, "application/vnd.tcpdump.pcap", arg)
        cur = dict(self.cur) if self.cur else None
        ring = sum(f.stat().st_size for f in self.ring_files()) if cur else 0
        kept = [{"name": f.name, "size": f.stat().st_size, "t": f.stat().st_mtime} for f in sorted(self.keep_dir.glob("*.pcap"), key=lambda f: -f.stat().st_mtime)]
        return {"cur": cur, "ring_bytes": ring, "lines": list(self.lines)[-80:], "kept": kept, "now": time.time(),
                "conf": {k: self.conf[k] for k in self.SETTINGS}, "auto_filters": AUTO_FILTERS}

    def post(self, action, body, user):
        if action == "start":
            self.begin(body.get("filter"), body.get("secs"), str(body.get("name") or "capture")[:30], user["name"])
            return {}
        if action == "stop":
            if self.proc and self.proc.poll() is None:
                self.end(self.proc)
            return {}
        if action == "keep":
            if not self.cur or self.cur["running"]:
                raise ValueError("Stop the capture first")
            return {"name": self.keep(self.cur)}
        if action == "delete":
            n = str(body.get("name") or "")
            if not NAME_RE.fullmatch(n):
                raise ValueError("Bad name")
            (self.keep_dir / n).unlink(missing_ok=True)
            return {}
        if action == "test":
            return {"filter": self.check_filter(body.get("filter"))}
        return super().post(action, body, user)
