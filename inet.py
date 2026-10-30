"""Internet speed test, with a choice of provider: Fast.com (Netflix), Cloudflare, Ookla or a plain download server.

Fast.com, Cloudflare and the download servers: several curl streams pull (then push) for N seconds.
curl does the TLS in C, one process per stream, so a multi-gig line isn't held back by Python; this
module only counts the bytes. Latency is measured idle and loaded (ping to 1.1.1.1 while the streams
run): the difference is bufferbloat.
Ookla: its own speedtest CLI (installed from the page, which accepts Ookla's license) runs the test.
A multi-gig test moves several GB, so it only runs when someone presses Start.
"""
import http.client
import io
import json
import os
import platform
import re
import shutil
import ssl
import statistics
import subprocess
import tarfile
import threading
import time
import urllib.request

from appbase import App, load_json, save_json

HOST = "speed.cloudflare.com"
HIST_MAX = 40
SIZES = [25_000_000, 10_000_000, 5_000_000, 2_000_000, 1_000_000]  # Cloudflare limits each size per IP: step down on a 429
FAST_SIZE = 26_214_400          # what fast.com's own page asks for per request
FAST_TOKEN = "YXNkZmFzZGxmbnNkYWZoYXNkZmhrYWxm"   # fast.com's public app token (read fresh from its page when possible)
READ = 1 << 20
ZEROS = memoryview(bytes(READ))
OOKLA_VER = "1.2.0"
OOKLA_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64", "armv7l": "armhf", "armv6l": "armel"}

PROVIDERS = [
    {"id": "fast", "name": "Fast.com (Netflix)", "up": True,
     "about": "Netflix's servers nearest you, the ones fast.com uses. Download and upload."},
    {"id": "cloudflare", "name": "Cloudflare", "up": True,
     "about": "speed.cloudflare.com. Download and upload. Cloudflare limits how much one public address can test: after a few big tests it answers 429 for a while."},
    {"id": "ookla", "name": "Ookla Speedtest", "up": True,
     "about": "Ookla's own speedtest program (speedtest.net servers). It picks its streams itself; choose the nearest server or one by name."},
    {"id": "mirror", "name": "Download server", "up": False,
     "about": "A big file from a hosting company's test server, or any URL you give. Download only."},
]
MIRRORS = [
    {"id": "vultr-nj", "name": "Vultr · New Jersey", "url": "https://nj-us-ping.vultr.com/vultr.com.100MB.bin"},
    {"id": "vultr-il", "name": "Vultr · Chicago", "url": "https://il-us-ping.vultr.com/vultr.com.100MB.bin"},
    {"id": "vultr-ga", "name": "Vultr · Atlanta", "url": "https://ga-us-ping.vultr.com/vultr.com.100MB.bin"},
    {"id": "vultr-tx", "name": "Vultr · Dallas", "url": "https://tx-us-ping.vultr.com/vultr.com.100MB.bin"},
    {"id": "vultr-lax", "name": "Vultr · Los Angeles", "url": "https://lax-ca-us-ping.vultr.com/vultr.com.100MB.bin"},
    {"id": "hetzner-ash", "name": "Hetzner · Ashburn, VA", "url": "https://ash-speed.hetzner.com/10GB.bin", "max": 2},
    {"id": "hetzner-hil", "name": "Hetzner · Hillsboro, OR", "url": "https://hil-speed.hetzner.com/10GB.bin", "max": 2},
    {"id": "ovh", "name": "OVH · France", "url": "https://proof.ovh.net/files/10Gb.dat"},
    {"id": "custom", "name": "Your own URL", "url": ""},
]


class Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Inet(App):
    name = "speed"
    SETTINGS = {"secs": 10, "streams": 4, "provider": "fast", "mirror": "vultr-nj", "custom_url": "", "ookla_server": ""}
    LIMITS = {"secs": (3, 30), "streams": (1, 16)}

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.ctx = ssl.create_default_context()
        self.run = {"running": False, "phase": None, "points": [], "error": None}
        self.hist_file = self.dir / "history.json"
        self.history = load_json(self.hist_file, [])[-HIST_MAX:]
        self.stop = False
        self.procs = set()
        self.ookla_bin = self.dir / "bin" / "speedtest"
        self.ookla_servers = []
        self.ookla_job = None

    # ---------- shared pieces ----------
    def check(self, k, v):
        if k == "provider" and v not in {p["id"] for p in PROVIDERS}:
            raise ValueError("Unknown provider")
        if k == "mirror" and v not in {m["id"] for m in MIRRORS}:
            raise ValueError("Unknown download server")
        if k == "custom_url" and v and not re.fullmatch(r"https?://[\w.\-]+(:\d+)?(/[^\s]*)?", str(v)):
            raise ValueError("The URL should look like https://host/file.bin")
        if k == "ookla_server" and v and not re.fullmatch(r"\d{1,7}", str(v)):
            raise ValueError("Bad Ookla server")
        return super().check(k, v)

    def get_json(self, url, timeout=10):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 probe-deck"})
        with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
            return json.loads(r.read())

    def cf_meta(self):
        """Cloudflare puts who-and-where in the headers of any test response."""
        try:
            c = http.client.HTTPSConnection(HOST, timeout=5, context=self.ctx)
            c.request("GET", "/__down?bytes=0", headers={"User-Agent": "probe-deck"})
            r = c.getresponse()
            r.read()
            h = {k.lower(): v for k, v in r.getheaders()}
            c.close()
            return {"ip": h.get("cf-meta-ip"), "asn": h.get("asn"), "colo": h.get("colo"), "city": h.get("city"), "country": h.get("country"),
                    "server": f"Cloudflare {h.get('colo')}" if h.get("colo") else "Cloudflare"}
        except Exception:
            return {"server": "Cloudflare"}

    _fast_ping = None

    @classmethod
    def fast_ping(cls):
        """Whether this ping takes intervals under a second. iputils does (as root, or down to 0.2 s for
        anyone); BusyBox's ping, on routers, only takes whole seconds and refuses "-i 0.2"."""
        if cls._fast_ping is None:
            try:
                r = subprocess.run(["ping", "-n", "-c", "1", "-i", "0.2", "-W", "1", "127.0.0.1"], capture_output=True, text=True, timeout=5)
                cls._fast_ping = "invalid" not in (r.stdout + r.stderr).lower()
            except (OSError, subprocess.SubprocessError):
                cls._fast_ping = False
        return cls._fast_ping

    def idle_latency(self, n=12):
        """Ping 1.1.1.1 before any load, the same way the loaded latency is measured."""
        fast = self.fast_ping()
        n = n if fast else 5  # at one a second, 5 is enough and keeps the test short
        out = subprocess.run(["ping", "-n", "-c", str(n), "-i", "0.2" if fast else "1", "-W", "2", "1.1.1.1"],
                             capture_output=True, text=True, timeout=n * (0.2 if fast else 1) + 10).stdout
        ms = [float(x) for x in re.findall(r"time=([\d.]+)", out)]
        if not ms:
            raise RuntimeError("1.1.1.1 doesn't answer ping")
        return ms

    def pinger(self, out, stop, phase=None, t0=None):
        """ICMP to 1.1.1.1 every 0.25 s (every second where ping can't go faster) while the streams run: the loaded latency."""
        p = subprocess.Popen(["ping", "-n", "-i", "0.25" if self.fast_ping() else "1", "-W", "2", "1.1.1.1"], stdout=subprocess.PIPE, text=True)
        try:
            for line in p.stdout:
                m = re.search(r"time=([\d.]+)", line)
                if m:
                    out.append(float(m.group(1)))
                    if phase:
                        with self.lock:
                            self.run.setdefault("lat", []).append([phase, round(time.time() - t0, 2), float(m.group(1))])
                if stop.is_set():
                    break
        finally:
            p.kill()

    # ---------- providers: where the streams go ----------
    def fast_targets(self):
        token = FAST_TOKEN
        try:
            req = urllib.request.Request("https://fast.com/", headers={"User-Agent": "Mozilla/5.0"})
            page = urllib.request.urlopen(req, timeout=8, context=self.ctx).read().decode("utf-8", "replace")
            js = re.search(r"app-[\w]+\.js", page)
            if js:
                src = urllib.request.urlopen(urllib.request.Request("https://fast.com/" + js.group(0), headers={"User-Agent": "Mozilla/5.0"}),
                                             timeout=8, context=self.ctx).read().decode("utf-8", "replace")
                m = re.search(r'token:"([A-Za-z0-9]+)"', src)
                token = m.group(1) if m else token
        except Exception:
            pass  # the known token still works
        d = self.get_json(f"https://api.fast.com/netflix/speedtest/v2?https=true&token={token}&urlCount=5")
        urls = [t["url"] for t in d.get("targets") or [] if t.get("url")]
        if not urls:
            raise RuntimeError("fast.com gave no test servers")
        c = d.get("client") or {}
        loc = (d["targets"][0].get("location") or {})
        meta = {"ip": c.get("ip"), "asn": c.get("asn"), "city": (c.get("location") or {}).get("city"),
                "server": "Netflix · " + ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)}
        return urls, meta

    def plan(self, provider):
        """-> (meta, down(i) -> list of URLs for one curl run, up(i) -> (url, size) or None)."""
        if provider == "fast":
            urls, meta = self.fast_targets()
            rng = lambda u, n: u.replace("/speedtest?", f"/speedtest/range/0-{n}?", 1)
            return meta, (lambda i, k: [rng(urls[(i + j) % len(urls)], FAST_SIZE) for j in range(k)]), (lambda i: (rng(urls[i % len(urls)], FAST_SIZE), FAST_SIZE))
        if provider == "cloudflare":
            meta = self.cf_meta()
            lvl = self._cf_level
            return meta, (lambda i, k: [f"https://{HOST}/__down?bytes={SIZES[lvl[0]]}"] * k), (lambda i: (f"https://{HOST}/__up", SIZES[lvl[0]]))
        if provider == "mirror":
            m = next((x for x in MIRRORS if x["id"] == self.conf["mirror"]), MIRRORS[0])
            url = self.conf["custom_url"] if m["id"] == "custom" else m["url"]
            if not url:
                raise RuntimeError("Type the URL of a big file first")
            host = re.sub(r"^https?://([^/:]+).*", r"\1", url)
            return {"server": m["name"] if m["id"] != "custom" else host, "max": m.get("max")}, (lambda i, k: [url] * k), None
        raise RuntimeError("Unknown provider")

    # ---------- the streams (curl) ----------
    def streams(self, provider, phase, secs, n, down, up):
        """n curl streams for secs; returns (bps after the first second, loaded latencies, bytes moved)."""
        moved = [0] * n
        stop = threading.Event()
        errs, refused = [], []

        def popen(args, **kw):
            p = subprocess.Popen(args, **kw)
            with self.lock:
                self.procs.add(p)
            return p

        def done(p):
            with self.lock:
                self.procs.discard(p)
            if p.poll() is None:
                p.kill()
            p.wait()

        def codes_of(text):
            return [int(c) for c in re.findall(r"(?m)^(\d{3})$", text)]

        dropped = []

        def handle(i, codes):
            """True: this stream is refused and stops. All of them refused: the test stops."""
            bad = [c for c in codes if c not in (200, 206)]
            if not bad:
                return False
            c = bad[0]
            if provider == "cloudflare" and c == 429 and self._cf_level[0] < len(SIZES) - 1:
                self._cf_level[0] += 1   # smaller requests are still allowed
                return False
            with self.lock:
                dropped.append(i)
                refused.append(c)
                if len(set(dropped)) >= n:
                    stop.set()
            return True

        def down_w(i):
            # many URLs in one curl: it keeps the connection open between them
            k = max(4, min(400, int(secs * 1.25e9 / FAST_SIZE) + 2))
            while not stop.is_set():
                p = popen(["curl", "-s", "-S", "-w", "%{stderr}%{http_code}\n", *down(i, k)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                fd = p.stdout.fileno()
                try:
                    while not stop.is_set():
                        b = os.read(fd, READ)
                        if not b:
                            break
                        moved[i] += len(b)
                finally:
                    done(p)
                err = p.stderr.read().decode("utf-8", "replace")
                if handle(i, codes_of(err)):
                    return
                if not codes_of(err) and not stop.is_set():
                    errs.append(err.strip()[-120:] or "curl failed")
                    time.sleep(0.3)

        def up_w(i):
            while not stop.is_set():
                url, size = up(i)
                p = popen(["curl", "-s", "-S", "-o", "/dev/null", "-w", "%{http_code}\n", "-X", "POST", "-T", "-", "-H", "Transfer-Encoding:",
                           "-H", f"Content-Length: {size}", "-H", "Content-Type: application/octet-stream", url],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                fd = p.stdin.fileno()
                sent = 0
                try:
                    while sent < size and not stop.is_set():
                        w = os.write(fd, ZEROS[:min(READ, size - sent)])
                        sent += w
                        moved[i] += w
                    p.stdin.close()
                    if not stop.is_set():
                        p.wait(timeout=30)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
                finally:
                    done(p)
                out = p.stdout.read().decode("utf-8", "replace")
                if handle(i, codes_of(out)):
                    return
                if not codes_of(out) and not stop.is_set():
                    errs.append(p.stderr.read().decode("utf-8", "replace").strip()[-120:] or "curl failed")
                    time.sleep(0.3)

        lat = []
        threads = [threading.Thread(target=down_w if phase == "down" else up_w, args=(i,), daemon=True) for i in range(n)]
        tx0 = self.tx_payload()
        count = (lambda: sum(moved)) if phase == "down" or tx0 is None else (lambda: self.tx_payload() - tx0)
        t0 = time.time()
        pt = threading.Thread(target=self.pinger, args=(lat, stop, phase, t0), daemon=True)
        for t in threads:
            t.start()
        pt.start()
        last_b, last_t, base = 0, t0, None
        try:
            while time.time() - t0 < secs and not self.stop and not stop.is_set():
                time.sleep(0.5)
                now, b = time.time(), count()
                bps = (b - last_b) * 8 / max(0.01, now - last_t)
                last_b, last_t = b, now
                with self.lock:
                    self.run["points"].append([phase, round(now - t0, 2), round(bps)])
                    self.run["live"] = {"phase": phase, "bps": round(bps)}
                if base is None and now - t0 >= 1.0:
                    base = (now, b)       # skip the first second: TCP slow start
        finally:
            stop.set()
            self.kill_all()
        end_t, end_b = time.time(), count()
        who = next(p["name"] for p in PROVIDERS if p["id"] == provider)
        if refused and len(set(dropped)) >= n:
            raise RuntimeError(f"{who} is limiting tests from this public IP (429): wait a few minutes or pick another provider"
                               if 429 in refused else f"{who} refused the test (HTTP {refused[0]})")
        if base is None or end_b == 0:
            raise RuntimeError(errs[-1] if errs else "nothing moved")
        if dropped:
            with self.lock:
                self.run.setdefault("notes", []).append(f"{who} allowed {n - len(set(dropped))} of {n} {phase}load streams (HTTP {refused[0]} for the rest)")
        return (end_b - base[1]) * 8 / max(0.1, end_t - base[0]), lat, end_b

    def iface(self):
        """The card internet traffic leaves by (the default route's), else the probe's own."""
        try:
            for r in json.loads(subprocess.run(["ip", "-j", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout or "[]"):
                if r.get("dev"):
                    return r["dev"]
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
        return getattr(self.p, "iface", None)

    def tx_payload(self):
        """Bytes this box has sent, minus packet headers (about 4% at MTU 1500): what an upload test reports."""
        name = self.iface()
        try:
            tx = int(open(f"/sys/class/net/{name}/statistics/tx_bytes").read())
            mtu = int(open(f"/sys/class/net/{name}/mtu").read())
        except (OSError, ValueError, TypeError):
            return None
        return tx * (mtu - 52) / (mtu + 14)

    def kill_all(self):
        with self.lock:
            ps = list(self.procs)
        for p in ps:
            try:
                p.kill()
            except OSError:
                pass

    # ---------- Ookla ----------
    _which = (0, None)

    def ookla_path(self):
        """Ours (installed from the page), or Ookla's own package. Ubuntu's speedtest-cli also
        installs a 'speedtest', a different program: only Ookla's says so in --version."""
        if self.ookla_bin.is_file():
            return str(self.ookla_bin)
        t, found = self._which
        if time.time() - t > 60:
            w = shutil.which("speedtest")
            found = w if w and "ookla" in self._ookla_version(w).lower() else None
            self._which = (time.time(), found)
        return found

    @staticmethod
    def _ookla_version(path):
        try:
            return subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout.split("\n")[0]
        except (OSError, subprocess.SubprocessError):
            return ""

    def ookla_install(self):
        arch = OOKLA_ARCH.get(platform.machine())
        if not arch:
            raise ValueError(f"Ookla doesn't make the speedtest program for {platform.machine()}")
        url = f"https://install.speedtest.net/app/cli/ookla-speedtest-{OOKLA_VER}-linux-{arch}.tgz"
        data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "probe-deck"}), timeout=60, context=self.ctx).read()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            m = next((x for x in tf.getmembers() if x.name.rsplit("/", 1)[-1] == "speedtest" and x.isfile()), None)
            if not m:
                raise ValueError("The download has no speedtest program in it")
            blob = tf.extractfile(m).read()
        self.ookla_bin.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ookla_bin.with_suffix(".tmp")
        tmp.write_bytes(blob)
        tmp.chmod(0o755)
        os.replace(tmp, self.ookla_bin)
        return self._ookla_version(str(self.ookla_bin))

    def ookla_list(self):
        exe = self.ookla_path()
        if not exe:
            raise ValueError("Install Ookla's speedtest program first")
        r = subprocess.run([exe, "--accept-license", "--accept-gdpr", "-L", "-f", "json"], capture_output=True, text=True, timeout=40)
        try:
            d = json.loads(r.stdout)
        except ValueError:
            raise ValueError((r.stderr or r.stdout).strip()[-160:] or "speedtest -L failed") from None
        self.ookla_servers = [{"id": str(s.get("id")), "name": f"{s.get('name')} · {s.get('location')}"} for s in d.get("servers") or []]
        return self.ookla_servers

    def ookla_run(self, res):
        exe = self.ookla_path()
        if not exe:
            raise RuntimeError("Ookla's speedtest program isn't installed (press Install on the Speed test page)")
        args = [exe, "--accept-license", "--accept-gdpr", "-f", "jsonl", "-p", "yes"]
        if self.conf["ookla_server"]:
            args += ["-s", self.conf["ookla_server"]]
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with self.lock:
            self.procs.add(p)
        final, last_err = None, ""
        try:
            for line in p.stdout:
                if self.stop:
                    raise RuntimeError("stopped")
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                kind = ev.get("type")
                if kind == "testStart":
                    s = ev.get("server") or {}
                    res.update(server=f"Ookla · {s.get('name')}, {s.get('location')}", isp=ev.get("isp"),
                               ip=(ev.get("interface") or {}).get("externalIp"))
                elif kind in ("download", "upload"):
                    ph = "down" if kind == "download" else "up"
                    x = ev.get(kind) or {}
                    with self.lock:
                        self.run["phase"] = ph
                        if x.get("bandwidth") is not None and x.get("elapsed") is not None:
                            bps = x["bandwidth"] * 8
                            self.run["points"].append([ph, round(x["elapsed"] / 1000, 2), round(bps)])
                            self.run["live"] = {"phase": ph, "bps": round(bps)}
                        iqm = (x.get("latency") or {}).get("iqm")
                        if iqm is not None and x.get("elapsed") is not None:
                            self.run.setdefault("lat", []).append([ph, round(x["elapsed"] / 1000, 2), round(iqm, 1)])
                elif kind == "ping":
                    with self.lock:
                        self.run["phase"] = "latency"
                elif kind == "result":
                    final = ev
                elif kind == "log" or ev.get("error"):
                    last_err = str(ev.get("message") or ev.get("error") or "")[:160]
            p.wait(timeout=30)
        finally:
            with self.lock:
                self.procs.discard(p)
            if p.poll() is None:
                p.kill()
        if not final:
            err = (p.stderr.read() or "").strip()
            raise RuntimeError(last_err or err[-160:] or "Ookla's test gave no result")
        pg = final.get("ping") or {}
        res["idle_ms"] = round(pg["latency"], 1) if pg.get("latency") is not None else None
        res["jitter_ms"] = round(pg["jitter"], 1) if pg.get("jitter") is not None else None
        for kind, ph in (("download", "down"), ("upload", "up")):
            x = final.get(kind) or {}
            res[ph] = round(x["bandwidth"] * 8) if x.get("bandwidth") else None
            res[f"{ph}_bytes"] = x.get("bytes")
            res[f"{ph}_ms"] = round((x.get("latency") or {}).get("iqm"), 1) if (x.get("latency") or {}).get("iqm") is not None else None
        res["loss"] = final.get("packetLoss")
        res["link"] = (final.get("result") or {}).get("url")
        s = final.get("server") or {}
        res["server"] = f"Ookla · {s.get('name')}, {s.get('location')}" if s else res.get("server")

    # ---------- the whole test ----------
    def start(self):
        pass

    def begin(self, user):
        with self.lock:
            if self.run["running"]:
                raise ValueError("A test is already running")
            if self.p.speed.get("running"):
                raise ValueError("A LAN speed test is running")
            if not self.p.link["up"]:
                raise ValueError("No link")
            prov = self.conf["provider"]
            if prov != "ookla" and not shutil.which("curl"):
                raise ValueError("curl isn't installed on this box (apt install curl)")
            if prov == "ookla" and not self.ookla_path():
                raise ValueError("Install Ookla's speedtest program first (the Install button below the provider)")
            self.stop = False
            self.run = {"running": True, "phase": "meta", "points": [], "lat": [], "error": None, "started": time.time(), "live": None, "result": None,
                        "provider": prov}
        threading.Thread(target=self.go, args=(user,), daemon=True).start()

    def go(self, user):
        secs, n, prov = int(self.conf["secs"]), int(self.conf["streams"]), self.conf["provider"]
        res = {"t": time.time(), "by": user, "secs": secs, "streams": n, "provider": prov,
               "port": (self.p.neighbor or {}).get("port"), "switch": (self.p.neighbor or {}).get("switch")}
        try:
            if prov == "ookla":
                res["streams"] = None
                self.ookla_run(res)
            else:
                self._cf_level = [0]
                meta, down, up = self.plan(prov)
                res.update(meta)
                with self.lock:
                    self.run["phase"] = "latency"
                    self.run["server"] = res.get("server")
                ms = self.idle_latency()
                res["idle_pts"] = ms
                res["idle_ms"] = round(statistics.median(ms), 1)
                res["jitter_ms"] = round(statistics.mean(abs(a - b) for a, b in zip(ms[1:], ms[:-1])), 1) if len(ms) > 2 else None
                for phase in ("down", "up"):
                    if self.stop:
                        raise RuntimeError("stopped")
                    if phase == "up" and not up:
                        res["up_note"] = "This server only does downloads"
                        continue
                    with self.lock:
                        self.run["phase"] = phase
                    bps, lat, moved = self.streams(prov, phase, secs, min(n, res.get("max") or n), down, up)
                    res[phase] = round(bps)
                    res[f"{phase}_ms"] = round(statistics.median(lat), 1) if lat else None
                    res[f"{phase}_bytes"] = moved
            res["ok"] = True
            with self.lock:
                res["pts"], res["lat"] = list(self.run["points"]), list(self.run["lat"])[:600]
                if self.run.get("notes"):
                    res["notes"] = list(self.run["notes"])
        except Exception as e:
            res.update(ok=False, error=str(e)[:200])
        finally:
            self.kill_all()
        with self.lock:
            self.run.update(running=False, phase=None, result=res, error=res.get("error"), live=None)
            self.history = (self.history + [res])[-HIST_MAX:]
        save_json(self.hist_file, self.history)

    _cf_level = [0]

    # ---------- for the page ----------
    def status(self):
        return {"dot": "live" if self.run["running"] else ""}

    def get(self, sub, arg, q, user):
        with self.lock:
            exe = self.ookla_path() if not self.ookla_job else None
            return {"run": dict(self.run), "history": self.history[::-1], "secs": self.conf["secs"], "streams": self.conf["streams"],
                    "conf": {k: self.conf[k] for k in ("provider", "mirror", "custom_url", "ookla_server")},
                    "providers": PROVIDERS, "mirrors": MIRRORS, "curl": bool(shutil.which("curl")),
                    "ookla": {"installed": bool(exe), "busy": self.ookla_job, "servers": self.ookla_servers, "arch": OOKLA_ARCH.get(platform.machine())}}

    def post(self, action, body, user):
        if action == "start":
            if body:
                self.save_conf({k: body[k] for k in ("secs", "streams", "provider", "mirror", "custom_url", "ookla_server") if k in body})
            self.begin(user["name"])
            return {}
        if action == "stop":
            self.stop = True
            self.kill_all()
            return {}
        if action == "clear":
            with self.lock:
                self.history = []
            save_json(self.hist_file, [])
            return {}
        if action == "ookla_install":
            with self.lock:
                if self.ookla_job:
                    raise ValueError("Already installing")
                self.ookla_job = "installing"
            try:
                return {"version": self.ookla_install()}
            finally:
                self.ookla_job = None
        if action == "ookla_servers":
            return {"servers": self.ookla_list()}
        return super().post(action, body, user)
