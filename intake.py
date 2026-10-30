"""Switch intake: watch a switch boot on the console cable, report its health, then preload a config.

1. Watch: arm it (or let it arm itself when a boot starts) and power the switch on. It only listens
   while the switch boots: a Break at the wrong moment would drop the switch into ROMMON.
2. Report: the boot loader, POST results, the image it loaded, the IOS banner (model, serial, memory)
   and any error messages, then a few show commands once the switch is up. Saved per run.
3. IOS check: the version against a list of recommended versions and, with a Cisco API key, the
   security advisories for that version.
4. Preload: when the switch stops at the setup dialog or a prompt, the page offers a config: a saved
   template, a file, or pasted text. Sent one line at a time; errors are kept with their line number.
   Nothing is saved (write memory) until the change is reviewed.
"""
import difflib
import json
import queue
import re
import threading
import time
import urllib.parse
import urllib.request

import swcli
from appbase import App, Reply, load_json, save_json

HIST_MAX = 100
RAW_MAX = 512 * 1024
TPL_MAX = 50
TPL_SIZE = 64 * 1024
NAME_RE = re.compile(r"[\w .\-()]{1,60}")
ID_RE = re.compile(r"\d{8}-\d{6}")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-Za-z]")
BAUD_TRY = (9600, 115200, 38400, 19200, 57600)

# ---------- what a boot looks like ----------
# Lines that only a boot prints (show version also says "Boot Loader", but on a BOOTLDR:/ROM: line).
BOOT_MARK = re.compile(r"^(?!BOOTLDR|ROM)(?:.*Boot ?Loader \(.*\) Version|System Bootstrap, Version|Xmodem file system is available|"
                       r"POST: .*: Begin|Initializing Hardware|boot: attempting to boot|Using driver version \d+ for media type)", re.I)
R = {
    "bootloader": re.compile(r"^(?!BOOTLDR|ROM).*?(?:Boot ?Loader|System Bootstrap)[^\n]*?Version\s+([^\s,]+)", re.I),
    "base_mac": re.compile(r"^Base ethernet MAC Address\s*:?\s*([0-9a-fA-F]{2}(?:[:.\-]?[0-9a-fA-F]{2}){5})"),
    "image": re.compile(r'^(?:Loading "([^"]+)"|boot: attempting to boot from \[([^\]]+)\])'),
    "ios": re.compile(r"^Cisco IOS(?: XE)? Software.*?Version\s+([^\s,]+)"),
    "proc": re.compile(r"^cisco\s+(\S+)\s+\(([^)]+)\)\s+processor.*?with\s+(\d+)K(?:/(\d+)K)? bytes of memory", re.I),
    "board_id": re.compile(r"^Processor board ID\s+(\S+)"),
    "last_reset": re.compile(r"^(?:Last reset from|System returned to ROM by)\s+(.+?)\s*$"),
    "model": re.compile(r"^Model [Nn]umber\s*:\s*(\S+)"),
    "serial": re.compile(r"^System [Ss]erial [Nn]umber\s*:\s*(\S+)"),
    "hw_rev": re.compile(r"^Hardware Board Revision Number\s*:\s*(\S+)"),
    "version_id": re.compile(r"^Version ID\s*:\s*(\S+)"),
    "pw_recovery": re.compile(r"password-recovery mechanism is (enabled|disabled)", re.I),
    "flash_kb": re.compile(r"^(\d+)K bytes of (?:flash|Flash|.*flash)", re.I),
}
POST_END = re.compile(r"POST:\s*(.+?)\s*:\s*End,\s*Status\s+(\w+)", re.I)
POST_BEGIN = re.compile(r"POST:\s*(.+?)\s*:\s*Begin", re.I)
SYSLOG = re.compile(r"%([A-Z0-9_]+)-(\d)-([A-Z0-9_]+):\s*(.*)")
HW_FACS = {"ILPOWER", "PLATFORM_ENV", "ENVIRONMENT", "ENVMON", "FAN", "HARDWARE", "PLATFORM", "POWER_SUPPLIES", "PM", "TRANSCEIVER", "GBIC_SECURITY", "PHY", "SFP"}
QUIET_FACS = {"LINK", "LINEPROTO", "SYS", "SPANTREE", "CDP", "SSH", "CRYPTO", "PKI", "SNMP"}
BAD_LINES = [
    (re.compile(r"Error loading|boot: cannot load|Unable to (?:load|boot)|Failed to load|Image validation fail|Bad (?:magic|checksum)|checksum (?:error|mismatch|fail)|Boot process failed|image is corrupt", re.I),
     "bad", "Image", "The switch had trouble loading its software"),
    (re.compile(r"(?:flash|fsck|file ?system).{0,40}(?:corrupt|bad block|i/o error|fail)", re.I), "bad", "Flash", "Flash memory reported a problem"),
    (re.compile(r"Traceback=|Unexpected exception|%SYS-\d-(?:CRASH|MALLOCFAIL)|Crashinfo|CPU Signal", re.I), "bad", "Software", "IOS crashed or ran out of memory"),
    (re.compile(r"The system has been interrupted|Break received|Boot process interrupted", re.I), "warn", "Boot", "The boot was interrupted (a Break or the Mode button)"),
    (re.compile(r"config file .*corrupt|startup-config.*(?:corrupt|invalid)|NVRAM.*(?:corrupt|bad|error)", re.I), "warn", "Config", "The saved config looks damaged"),
]
DIALOG = re.compile(r"initial configuration dialog\?\s*\[yes/no\]:\s*$", re.I)
AUTOINST = re.compile(r"terminate autoinstall\?\s*\[yes\]:\s*$", re.I)
RETURN = re.compile(r"Press RETURN to get started", re.I)
LOGIN = re.compile(r"(?:Username|login|Password):\s*$", re.I)
ROMMON = re.compile(r"(?:^|\n)(?:switch|rommon \d+ ?)[:>] ?$")
PROMPT = swcli.PROMPT

STAGES = [("boot", "Boot loader"), ("post", "Self test"), ("load", "Load image"), ("ios", "Start IOS"), ("ready", "Ready")]
READY_TEXT = {
    "dialog": "Factory blank: stopped at the setup dialog",
    "autoinstall": "Factory blank: stopped at the autoinstall question",
    "return": "Up: Press RETURN to get started",
    "prompt": "Up: at the CLI prompt",
    "login": "Asks for a login: the last owner's config is still on it",
    "rommon": "Stopped in ROMMON (switch: prompt): it didn't boot IOS",
    "already": "Was already running: no boot was seen",
}
CONFIGURABLE = ("dialog", "autoinstall", "return", "prompt", "already")


def norm(b):
    t = b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else b
    return ANSI.sub("", t).replace("\r\n", "\n").replace("\r", "\n").replace("\x08", "")


def printable_ratio(b):
    if not b:
        return 1.0
    good = sum(1 for c in b if 32 <= c < 127 or c in (7, 8, 9, 10, 13, 27))
    return good / len(b)


def vkey(v):
    """15.2(7)E5 -> [15, 2, 7, 'E', 5] so versions in one train compare."""
    return [int(x) if x.isdigit() else x.upper() for x in re.findall(r"\d+|[A-Za-z]+", v or "")]


def vtrain(v):
    k = vkey(v)
    return [x for x in k if isinstance(x, str)]


class NeedLogin(Exception):
    pass


class Run:
    """One switch, from the first boot line to the report."""

    def __init__(self, auto, seed=""):
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.t0 = time.time()
        self.auto = auto
        self.phase = "waiting"      # waiting, booting, settling, checks, ready, done
        self.ready = None           # one of READY_TEXT
        self.ready_cand = None
        self.ready_t = None
        self.stages = {}            # stage -> seconds after t0
        self.facts = {}
        self.post = []              # {name, status}
        self.post_open = {}
        self.findings = []          # {lv, area, text, line}
        self.health = {}
        self.ios = None
        self.raw = []
        self.raw_len = 0
        self.partial = ""
        self.tail = ""
        self.last_rx = 0.0
        self.rx = 0
        self.bad_bytes = bytearray()
        self.notes = []
        self.saved = False
        if seed:
            self.feed_text(seed, time.time())

    # ---------- reading ----------
    def add(self, lv, area, text, line=None):
        if any(f["area"] == area and f["text"] == text for f in self.findings):
            return
        self.findings.append({"lv": lv, "area": area, "text": text, "line": (line or "")[:200]})

    def stage(self, k, t):
        if k not in self.stages:
            self.stages[k] = round(t - self.t0, 1)

    def feed_text(self, text, now):
        if self.raw_len < RAW_MAX:
            self.raw.append(text)
            self.raw_len += len(text)
        self.last_rx = now
        if self.phase == "waiting":
            self.phase = "booting"
            self.stage("boot", now)
        self.tail = (self.tail + text)[-600:]
        buf = self.partial + text
        lines = buf.split("\n")
        self.partial = lines.pop()
        for ln in lines:
            self.line(ln.strip(), now)
        self.look_ready(now)

    def line(self, ln, now):
        if not ln:
            return
        f = self.facts
        for k, rx in R.items():
            m = rx.search(ln)
            if not m:
                continue
            if k == "bootloader":
                f.setdefault("bootloader", m.group(1))
                self.stage("boot", now)
            elif k == "image":
                f["image"] = m.group(1) or m.group(2)
                self.stage("load", now)
            elif k == "ios":
                f["ios"] = m.group(1)
                f["ios_line"] = ln[:160]
                self.stage("ios", now)
            elif k == "proc":
                f.setdefault("model", m.group(1))
                f["cpu"] = m.group(2)
                f["ram_kb"] = int(m.group(3)) + int(m.group(4) or 0)
            elif k == "board_id":
                f.setdefault("serial", m.group(1))
            elif k in ("model", "serial"):
                f[k] = m.group(1)
            else:
                f[k] = m.group(1)
        m = POST_BEGIN.search(ln)
        if m:
            self.stage("post", now)
            self.post_open[m.group(1)] = now
        m = POST_END.search(ln)
        if m:
            name, st = m.group(1), m.group(2)
            self.post_open.pop(name, None)
            self.post.append({"name": name, "status": st})
            if st.lower() not in ("passed", "pass", "ok"):
                self.add("bad", "Self test", f"{name}: {st}", ln)
        m = SYSLOG.search(ln)
        if m:
            fac, sev, mn, msg = m.group(1), int(m.group(2)), m.group(3), m.group(4)
            if fac == "LINK" and mn == "UPDOWN" or fac == "LINEPROTO":
                pass
            elif sev <= 2:
                self.add("bad", "Messages", f"%{fac}-{sev}-{mn}: {msg[:120]}", ln)
            elif sev == 3 and fac not in QUIET_FACS:
                self.add("warn", "Messages", f"%{fac}-{sev}-{mn}: {msg[:120]}", ln)
            elif sev == 4 and fac in HW_FACS:
                self.add("warn", "Hardware", f"%{fac}-{sev}-{mn}: {msg[:120]}", ln)
        if re.search(r"%Error opening tftp://", ln):
            self.add("info", "Config", "Autoinstall looked for a config over TFTP: normal on a blank switch", ln)
        for rx, lv, area, text in BAD_LINES:
            if rx.search(ln):
                self.add(lv, area, text, ln)
        if RETURN.search(ln):
            self.cand("return", now)

    def cand(self, what, now):
        # a definite stop (dialog, login, prompt) beats "Press RETURN", which more lines often follow
        if self.ready_cand in (None, "return") or what != "return":
            self.ready_cand = what
            self.ready_t = now

    def look_ready(self, now):
        tail = self.partial[-200:] if self.partial.strip() else self.tail[-200:]
        if DIALOG.search(tail):
            self.cand("dialog", now)
        elif AUTOINST.search(tail):
            self.cand("autoinstall", now)
        elif ROMMON.search(tail):
            self.cand("rommon", now)
        elif LOGIN.search(tail):
            self.cand("login", now)
        elif PROMPT.search(tail):
            self.cand("prompt" if "ios" in self.stages or self.facts.get("bootloader") else "already", now)

    def settle(self, now):
        """True once the switch has stopped where it waits for us."""
        if not self.ready_cand or self.ready:
            return False
        quiet = now - self.last_rx
        wait = 4.0 if self.ready_cand == "return" else 1.5
        if quiet >= wait or now - self.ready_t > 30:
            self.ready = self.ready_cand
            self.stage("ready", now)
            return True
        return False

    def text(self):
        return "".join(self.raw)

    # ---------- the verdict ----------
    def finish_findings(self):
        f = self.facts
        if self.ready == "already":
            self.add("info", "Boot", "The switch was already up, so there was no boot to watch. Power-cycle it to capture the boot.")
        if self.ready == "rommon":
            self.add("bad", "Boot", "It stopped at the switch: prompt (ROMMON). Check the boot setting and the image in flash: (dir flash:, then boot flash:<image>).")
        if self.ready == "login":
            self.add("warn", "Config", "It still has the last owner's config and asks for a login. Use Erase below if you know the login, or password recovery (hold Mode while powering on) if you don't.")
        if self.stages and "post" not in self.stages and self.ready not in ("already",):
            self.add("info", "Self test", "No POST lines were seen (this model may not print them, or the boot started before watching).")
        for name in self.post_open:
            self.add("bad", "Self test", f"{name}: began but never finished")
        if self.post and all(p["status"].lower().startswith("pass") for p in self.post):
            self.add("ok", "Self test", f"All {len(self.post)} power-on self tests passed")
        if f.get("pw_recovery") == "disabled":
            self.add("warn", "Boot", "Password recovery is disabled: a lost password means a full wipe")
        lr = (f.get("last_reset") or "").lower()
        if lr and not re.search(r"power.?on|reload|power-on", lr):
            self.add("warn", "Boot", f"Last reset reason: {f['last_reset']}")
        if f.get("image"):
            self.add("ok", "Image", f"Loaded {f['image']}")

    def level(self):
        lvs = {x["lv"] for x in self.findings}
        if self.ready == "rommon" or "bad" in lvs:
            return "bad"
        if "warn" in lvs:
            return "warn"
        return "ok" if self.ready else ""

    def summary(self):
        f = self.facts
        return {"id": self.id, "t": self.t0, "model": f.get("model"), "serial": f.get("serial"), "ios": f.get("ios"),
                "ready": self.ready, "level": self.level(), "n_bad": sum(x["lv"] == "bad" for x in self.findings),
                "n_warn": sum(x["lv"] == "warn" for x in self.findings)}

    def public(self, live=True):
        dur = self.stages.get("ready") if self.ready else round(time.time() - self.t0, 1)
        return {**self.summary(), "phase": self.phase, "auto": self.auto, "ready_text": READY_TEXT.get(self.ready, ""),
                "configurable": self.ready in CONFIGURABLE or self.ready == "login", "stages": self.stages, "stage_names": STAGES, "facts": self.facts,
                "post": self.post, "findings": self.findings, "health": self.health, "iosinfo": self.ios, "notes": self.notes,
                "rx": self.rx, "secs": dur, "tail": self.text()[-3000:] if live else ""}

    def to_json(self):
        d = self.public(live=False)
        d.pop("tail", None)
        return d


# ---------- reading the show commands ----------
def parse_health(out):
    """out: command -> text (or None when the switch didn't take it). Returns (health dict, findings)."""
    h, finds = {}, []
    v = out.get("show version") or ""
    m = re.search(r"uptime is (.+)", v)
    if m:
        h["uptime"] = m.group(1).strip()
    m = re.search(r"Configuration register is (\S+)", v)
    if m:
        h["confreg"] = m.group(1)
        if m.group(1).lower() not in ("0x2102", "0xf", "0x102"):
            finds.append(("warn", "Boot", f"Config register is {m.group(1)} (normal is 0x2102). 0x2142 ignores the saved config; set it back with 'config-register 0x2102'."))
    m = re.search(r"System image file is \"([^\"]+)\"", v)
    if m:
        h["image"] = m.group(1)
    m = re.search(r"Cisco IOS(?: XE)? Software.*?Version\s+([^\s,]+)", v)
    if m:
        h["ios"] = m.group(1)
    for k, rx in (("model", R["model"]), ("serial", R["serial"])):
        mm = re.search(rx.pattern.lstrip("^"), v, re.M)
        if mm:
            h[k] = mm.group(1)
    m = re.search(r"^cisco\s+(\S+)\s+\(([^)]+)\)\s+processor.*?with\s+(\d+)K(?:/(\d+)K)?", v, re.M | re.I)
    if m:
        h.setdefault("model", m.group(1))
        h["ram_kb"] = int(m.group(3)) + int(m.group(4) or 0)
    lic = re.findall(r"^\s*\S+\s+(lanbase|lanlite|ipbase|ipservices|ipservicesk9|ipbasek9|universal|network-\w+|dna-\w+)\s", v, re.M | re.I)
    if lic:
        h["license"] = lic[0]

    inv = out.get("show inventory") or ""
    h["inventory"] = [{"name": a, "descr": b, "pid": c.strip(), "sn": d.strip()} for a, b, c, d in
                      re.findall(r'NAME:\s*"([^"]*)",\s*DESCR:\s*"([^"]*)"\s*\n\s*PID:\s*([^,]*),.*?SN:\s*(\S*)', inv)]

    env = out.get("show env all") or out.get("show environment") or ""
    env_rows = []
    for ln in env.splitlines():
        s = ln.strip()
        if not s or s.startswith("-"):
            continue
        if re.search(r"threshold", s, re.I):
            continue
        if re.search(r"\b(FAULTY|FAIL(?:ED|URE)?|BAD|CRITICAL|SHUTDOWN|NOT OK|RED|ABNORMAL)\b", s, re.I) and not re.search(r"not present", s, re.I):
            finds.append(("bad", "Hardware", s[:140]))
            env_rows.append(("bad", s))
        elif re.search(r"\b(WARNING|YELLOW|HIGH)\b", s, re.I):
            finds.append(("warn", "Hardware", s[:140]))
            env_rows.append(("warn", s))
        elif re.search(r"\b(OK|GOOD|NORMAL|GREEN)\b", s):
            env_rows.append(("ok", s))
    h["env"] = [{"lv": a, "line": b[:160]} for a, b in env_rows[:30]]
    temps = [int(x) for x in re.findall(r"(?:Temperature Value|Temp(?:erature)?)\s*:?\s*(\d{1,3})\s*(?:Degree|C\b|°)", env, re.I)]
    if temps:
        h["temp_c"] = max(temps)
        if max(temps) >= 60:
            finds.append(("warn", "Hardware", f"Running hot: {max(temps)} °C"))
    if env and not any(a == "bad" for a, _ in env_rows):
        finds.append(("ok", "Hardware", "Fans, temperature and power report OK"))

    post = out.get("show post") or ""
    fails = [ln.strip() for ln in post.splitlines() if re.search(r"Status\s+Fail|:\s*Failed|FAIL", ln)]
    h["post_cli"] = len(re.findall(r"Status\s+Passed|:\s*Passed", post))
    for ln in fails:
        finds.append(("bad", "Self test", ln[:140]))

    poe = out.get("show power inline")
    if poe:
        m = re.search(r"Available:\s*([\d.]+)\(w\)\s+Used:\s*([\d.]+)\(w\)\s+Remaining:\s*([\d.]+)", poe)
        if m:
            h["poe"] = {"avail": float(m.group(1)), "used": float(m.group(2)), "left": float(m.group(3))}
            if float(m.group(1)) == 0:
                finds.append(("bad", "PoE", "PoE budget is 0 W: the PoE supply may be dead"))
        bad_ports = re.findall(r"^(\S+)\s+\S+\s+(faulty|err-disable|power-deny|fault)\b", poe, re.M | re.I)
        for p, st in bad_ports:
            finds.append(("warn", "PoE", f"{p}: PoE {st}"))
        if m and not bad_ports and float(m.group(1)) > 0:
            finds.append(("ok", "PoE", f"PoE budget {m.group(1)} W, no port faults"))

    fl = out.get("dir flash:") or ""
    m = re.search(r"(\d+) bytes total \((\d+) bytes free\)", fl)
    if m:
        tot, free = int(m.group(1)), int(m.group(2))
        h["flash"] = {"total": tot, "free": free}
        if tot and free / tot < 0.05:
            finds.append(("warn", "Flash", f"Flash is nearly full ({free // 1024} KB free): no room for an upgrade"))
    imgs = re.findall(r"\s(\S+\.(?:bin|tar|pkg|conf))\s*$", fl, re.M)
    h["flash_files"] = re.findall(r"^\s*\d+\s+[-drwx]{4}\s+\d+\s+.*?\s(\S+)\s*$", fl, re.M)[:40]
    if re.search(r"\svlan\.dat\s*$", fl, re.M):
        h["vlan_dat"] = True
        finds.append(("info", "Config", "vlan.dat is in flash: the last owner's VLANs come back unless it's deleted"))
    if len([x for x in imgs if x.endswith(".bin")]) > 1:
        finds.append(("info", "Flash", "More than one image in flash: check 'show boot' picks the right one"))

    nv = out.get("dir nvram:")
    if nv is not None:
        h["startup_config"] = bool(re.search(r"startup-config", nv))
        if h["startup_config"]:
            finds.append(("warn", "Config", "A saved config (startup-config) is still on it: the last owner's settings"))
        else:
            finds.append(("ok", "Config", "No saved config: factory blank"))

    errs = []
    for ln in (out.get("show interfaces counters errors") or "").splitlines():
        m = re.match(r"^\s*((?:Gi|Fa|Te|Tw|Fo|Hu|Po)\S+)\s+([\d\s]+)$", ln)
        if m and any(int(x) for x in m.group(2).split()):
            errs.append(m.group(1))
    if errs:
        h["err_ports"] = sorted(set(errs))
        finds.append(("warn", "Ports", f"Error counters already above 0 on {', '.join(sorted(set(errs))[:8])}"))

    ifs = re.findall(r"^(\S+?\d+(?:/\d+)*)\s+\S+\s+\S+\s+\S+\s+(?:up|down|administratively down)", out.get("show ip interface brief") or "", re.M)
    h["interfaces"] = ifs

    for ln in (out.get("show logging") or "").splitlines():
        m = SYSLOG.search(ln)
        if m and int(m.group(2)) <= 2:
            finds.append(("bad", "Messages", f"Log: %{m.group(1)}-{m.group(2)}-{m.group(3)}: {m.group(4)[:100]}"))
    return h, finds


HEALTH_CMDS = ["show version", "show inventory", "show env all", "show post", "show power inline", "dir flash:", "dir nvram:",
               "show interfaces counters errors", "show ip interface brief", "show logging"]


# ---------- preload: cleaning and checking a config ----------
VIRTUAL_IF = re.compile(r"^(vl|lo|po|tu|bvi|null|bdi|nve)", re.I)
IF_ABBR = [("gigabitethernet", "gi"), ("fastethernet", "fa"), ("tengigabitethernet", "te"), ("twentyfivegige", "twe"), ("fortygigabitethernet", "fo"),
           ("hundredgige", "hu"), ("ethernet", "eth"), ("appgigabitethernet", "ap")]


def if_key(name):
    n = name.lower().replace(" ", "")
    for long, short in IF_ABBR:
        if n.startswith(long):
            return short + n[len(long):]
    m = re.match(r"([a-z]+)([\d/.]+)$", n)
    if m:
        for long, short in IF_ABBR:
            if long.startswith(m.group(1)):
                return short + m.group(2)
    return n


def clean_config(text):
    """A pasted config (often a copied show run) -> (lines to send, notes)."""
    notes = []
    raw = str(text).replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ").split("\n")
    out = []
    i = 0
    banner_end = None
    drop_cert = False
    drop_tp = False
    dropped_comments = 0
    while i < len(raw):
        ln = raw[i].rstrip()
        i += 1
        if banner_end is not None:      # inside a banner: send as is, up to the closing delimiter
            if "^C" in ln:
                ln = ln.replace("^C", banner_end)
            out.append(ln)
            if banner_end in ln:
                banner_end = None
            continue
        s = ln.strip()
        if drop_cert:
            if s == "quit":
                drop_cert = False
            continue
        if drop_tp:
            if ln.startswith(" ") or not s:
                continue
            drop_tp = False
        if not s:
            continue
        if s.startswith("!") or re.match(r"^(Building configuration|Current configuration ?:)", s):
            dropped_comments += 1
            continue
        if re.fullmatch(r"(conf(igure)?( t(erminal)?)?|end|enable|en)", s, re.I):
            continue
        if re.match(r"crypto pki certificate chain ", s):
            drop_cert = True
            notes.append("Removed a crypto pki certificate block (another switch's self-signed certificate; this switch makes its own)")
            continue
        if re.match(r"crypto pki trustpoint TP-self-signed-", s):
            drop_tp = True
            notes.append("Removed the TP-self-signed trustpoint (it belongs to the switch the config came from)")
            continue
        m = re.match(r"^(\s*banner\s+(?:motd|login|exec|incoming|slip-ppp|prompt-timeout)\s+)(\^C|\S)(.*)$", ln)
        if m:
            delim, rest = m.group(2), m.group(3)
            if delim == "^C":   # show run writes the delimiter as ^C: use a character the banner doesn't contain
                body = rest
                j = i
                while "^C" not in body and j < len(raw):
                    body += "\n" + raw[j]
                    j += 1
                new = next((c for c in "#%~@$&" if c not in body.replace("^C", "")), "#")
                notes.append(f"Banner delimiter ^C changed to {new}")
                rest = rest.replace("^C", new)
                out.append(m.group(1) + new + rest)
                if new not in rest:
                    banner_end = new
                continue
            out.append(ln)
            if delim not in rest:
                banner_end = delim
            continue
        out.append(ln)
    if dropped_comments:
        notes.append(f"Skipped {dropped_comments} comment and header line{'s' if dropped_comments != 1 else ''} (!, Building configuration…)")
    return out, notes


VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][\w]{0,30})\s*\}\}")


def fill_vars(lines, values):
    missing = []

    def sub(m):
        k = m.group(1)
        v = values.get(k)
        if v in (None, ""):
            missing.append(k)
            return m.group(0)
        return str(v)
    out = [VAR_RE.sub(sub, ln) for ln in lines]
    return out, sorted(set(missing))


def check_config(lines, interfaces=None):
    """Warnings before anything is sent: [{lv, n, text}] (n is the 1-based line, 0 = whole config)."""
    w = []
    known = {if_key(x) for x in interfaces or []}
    has_user15 = has_secret = has_user = has_domain = False
    vty_local = vty_ssh = None
    in_vty = False
    for n, ln in enumerate(lines, 1):
        s = ln.strip()
        low = s.lower()
        if not ln.startswith(" "):
            in_vty = low.startswith("line vty")
        m = re.match(r"interface\s+(\S+(?:\s+\d\S*)?)\s*$", s, re.I)
        if m and known and not VIRTUAL_IF.match(m.group(1)) and if_key(m.group(1)) not in known:
            w.append({"lv": "warn", "n": n, "text": f"This switch has no port {m.group(1)} (ports look like {sorted(interfaces)[0] if interfaces else '?'}). Lines under it will fail."})
        if re.match(r"interface range ", low) and known:
            w.append({"lv": "info", "n": n, "text": "interface range: check the ports exist on this model"})
        if low.startswith("enable secret") or low.startswith("enable password"):
            has_secret = True
            if low.startswith("enable password"):
                w.append({"lv": "info", "n": n, "text": "enable password is weak; enable secret is better"})
        if low.startswith("username "):
            has_user = True
            if re.search(r"privilege 15", low):
                has_user15 = True
        if low.startswith("ip domain-name") or low.startswith("ip domain name"):
            has_domain = True
        if in_vty and re.search(r"^\s*login local", ln):
            vty_local = n
        if in_vty and re.search(r"transport input .*ssh", low):
            vty_ssh = n
        if low.startswith("boot system"):
            w.append({"lv": "warn", "n": n, "text": "boot system: make sure that image is in this switch's flash, or it won't boot next time"})
        if re.match(r"vtp mode (server|client)", low):
            w.append({"lv": "warn", "n": n, "text": "VTP server/client: joining a network with a higher VTP revision can overwrite its VLANs. Transparent is safer."})
        if re.match(r"config-register", low):
            w.append({"lv": "warn", "n": n, "text": "config-register changes how the switch boots"})
        if any(ord(c) > 126 for c in ln):
            w.append({"lv": "warn", "n": n, "text": "Non-ASCII characters (smart quotes, dashes?): IOS will reject this line"})
        if VAR_RE.search(ln):
            w.append({"lv": "bad", "n": n, "text": f"Unfilled placeholder {VAR_RE.search(ln).group(0)}"})
    if vty_local and not has_user:
        w.append({"lv": "bad", "n": vty_local, "text": "line vty uses login local but the config has no username: remote logins would be locked out"})
    if not has_secret and not has_user15:
        w.append({"lv": "warn", "n": 0, "text": "No enable secret and no privilege-15 user: SSH/telnet logins can't get to enable mode"})
    if vty_ssh and not has_domain:
        w.append({"lv": "info", "n": vty_ssh, "text": "SSH needs ip domain-name and an RSA key (crypto key generate rsa modulus 2048)"})
    return w


ERR_OUT = re.compile(r"^\s*%\s*(Invalid|Incomplete|Ambiguous|Unknown|Unrecognized|Error|Bad|Cannot|Can't|.*not (?:supported|allowed|found|permitted)|.*failed).*$", re.I | re.M)
QUESTION = re.compile(r"(\[confirm\]|\[yes/no\]:?|\[y/n\]|\?\s*\[[^\]]*\]:?)\s*$", re.I)


class Intake(App):
    name = "intake"
    SETTINGS = {"auto_arm": True, "auto_checks": True, "auto_baud": True, "recommended": "", "psirt_id": "", "psirt_secret": ""}

    def __init__(self, probe, conf_dir, data_dir):
        super().__init__(probe, conf_dir, data_dir)
        self.rep_dir = self.dir / "reports"
        self.rep_dir.mkdir(exist_ok=True)
        self.tpl_file = self.dir / "templates.json"
        self.hist_file = self.dir / "history.json"
        self.history = load_json(self.hist_file, [])[-HIST_MAX:]
        self.templates = load_json(self.tpl_file, [])
        self.run = None
        self.armed = False
        self.busy = None           # what a background job is doing (checks, preload, save…)
        self.job = None            # the preload job
        self.spot = ""             # recent console text while not armed
        self.baud_tries = []
        self.psirt_cache = {}
        self.token = None
        self.q = None

    def start(self):
        threading.Thread(target=self.watch_loop, daemon=True).start()

    @property
    def con(self):
        return self.p.console

    def status(self):
        r = self.run
        if self.armed and r and not r.ready:
            return {"dot": "warn" if r.phase == "waiting" else "", "watching": r.phase}
        if r and r.phase == "ready" and r.ready in CONFIGURABLE and not self.busy and not self.job:
            return {"dot": {"ok": "on", "warn": "warn", "bad": "err"}.get(r.level(), ""), "pop": r.id}
        last = self.history[-1] if self.history else None
        if self.run and self.run.ready:
            last = self.run.summary()
        if not last:
            return None
        return {"dot": {"ok": "on", "warn": "warn", "bad": "err"}.get(last.get("level"), "")}

    # ---------- watching ----------
    def watch_loop(self):
        while self.con is None:
            time.sleep(1)
        self.q = self.con.tap()
        while True:
            try:
                d = self.q.get(timeout=0.5)
            except queue.Empty:
                d = None
            try:
                with self.lock:
                    if d:
                        self.on_data(d)
                    self.on_tick()
            except Exception as e:  # never let a parse bug stop the watcher
                print(f"intake: {e!r}", flush=True)

    def on_data(self, d):
        now = time.time()
        if self.busy:                  # our own show commands and pasted lines: not part of the boot
            return
        if not self.armed:
            self.spot = (self.spot + norm(d))[-6000:]
            if self.conf["auto_arm"]:
                lines = self.spot.split("\n")
                for k, ln in enumerate(lines):
                    if BOOT_MARK.search(ln.strip()):
                        self.arm(auto=True, seed="\n".join(lines[k:]))
                        return
            return
        r = self.run
        if r.ready:                    # done booting; a second boot starts a new run
            text = norm(d)
            if any(BOOT_MARK.search(x.strip()) for x in text.split("\n")):
                self.arm(auto=True, seed=text)
            return
        r.rx += len(d)
        if self.conf["auto_baud"] and r.rx < 4000:
            r.bad_bytes += d
            if len(r.bad_bytes) >= 300:
                if printable_ratio(r.bad_bytes) < 0.75:
                    self.next_baud()
                    return
                r.bad_bytes = bytearray()
        r.feed_text(norm(d), now)

    def next_baud(self):
        r = self.run
        cur = self.con.conf["console_baud"]
        self.baud_tries.append(cur)
        nxt = next((b for b in BAUD_TRY if b not in self.baud_tries), None)
        r.bad_bytes = bytearray()
        if nxt is None:
            r.add("warn", "Console", "The console shows garbage at every common speed: check the cable")
            self.conf["auto_baud"] = False   # for this run only (not saved)
            return
        r.notes.append(f"Garbled text at {cur} baud: trying {nxt}")
        try:
            self.con.save({"console_baud": nxt})
        except Exception as e:
            r.notes.append(f"Couldn't change the speed: {e}")

    def on_tick(self):
        r = self.run
        if not (self.armed and r) or r.ready:
            return
        now = time.time()
        if r.settle(now):
            r.phase = "settling"
            if self.job and self.job.get("reloading"):
                self.job = None
            if self.baud_tries:
                r.add("warn", "Console", f"The console runs at {self.con.conf['console_baud']} baud, not 9600")
            go = self.conf["auto_checks"] and r.ready in CONFIGURABLE
            if go:
                self.spawn("checks", self.do_checks, {})
            else:
                self.finish()

    def arm(self, auto=False, seed=""):
        self.run = Run(auto, seed)
        self.armed = True
        self.spot = ""
        self.job = None
        self.baud_tries = []
        self.conf["auto_baud"] = load_json(self.conf_file, {}).get("auto_baud", self.SETTINGS["auto_baud"])

    def finish(self):
        r = self.run
        if not r:
            return
        r.finish_findings()
        self.ios_check()
        r.phase = "done" if r.ready == "rommon" else "ready"
        self.save_report()

    def save_report(self):
        r = self.run
        save_json(self.rep_dir / f"{r.id}.json", r.to_json())
        (self.rep_dir / f"{r.id}.log").write_text(r.text(), errors="replace")
        self.history = [h for h in self.history if h["id"] != r.id] + [r.summary()]
        self.history = self.history[-HIST_MAX:]
        keep = {h["id"] for h in self.history}
        for f in self.rep_dir.glob("*"):
            if f.stem not in keep:
                f.unlink(missing_ok=True)
        save_json(self.hist_file, self.history)
        r.saved = True

    # ---------- console jobs ----------
    def spawn(self, what, fn, body):
        if self.busy:
            raise ValueError(f"Busy: {self.busy}")
        self.busy = what

        def go():
            try:
                fn(body)
            except NeedLogin:
                self.note_err(what, "The switch asks for a login: type it in the login fields")
            except swcli.CLIError as e:
                self.note_err(what, str(e))
            except Exception as e:
                self.note_err(what, f"{type(e).__name__}: {str(e)[:160]}")
            finally:
                with self.lock:
                    self.busy = None
                    if what == "checks":
                        self.finish()
        threading.Thread(target=go, daemon=True).start()

    def note_err(self, what, msg):
        with self.lock:
            if what in ("preload", "save", "reload", "erase") and self.job:
                self.job.update(running=False, error=msg, step="stopped")
            elif self.run:
                self.run.notes.append(f"{what}: {msg}")

    def to_prompt(self, link, timeout=90):
        """Get past the setup dialog / autoinstall / Press RETURN to a CLI prompt."""
        end = time.time() + timeout
        buf = ""
        link.write(b"\r")
        kick = time.time()
        while time.time() < end:
            d = link.read(1.0)
            if d:
                buf = (buf + norm(d))[-3000:]
            tail = buf[-300:]
            if DIALOG.search(tail):
                link.write(b"no\r")
                buf, kick = "", time.time()
                if self.run:
                    self.run.notes.append("Answered no to the setup dialog")
                continue
            if AUTOINST.search(tail):
                link.write(b"yes\r")
                buf, kick = "", time.time()
                continue
            if ROMMON.search(tail):
                raise swcli.CLIError("The switch is in ROMMON (switch: prompt)")
            if LOGIN.search(tail):
                return "login"
            if PROMPT.search(tail):
                return "prompt"
            if time.time() - kick > 5:
                link.write(b"\r")
                kick = time.time()
        raise swcli.CLIError("No prompt from the switch")

    def session(self, login=None):
        link = swcli.ConsoleLink(self.con)
        try:
            at = self.to_prompt(link)
            if at == "login" and not (login or {}).get("user"):
                raise NeedLogin()
            s = swcli.Session(link, login or {})
            s.start(True)
            return s
        except Exception:
            link.close()
            raise

    def do_checks(self, body):
        r = self.run
        r.phase = "checks"
        out = {}
        try:
            s = self.session(body.get("login"))
        except NeedLogin:
            with self.lock:
                r.ready = "login"
                r.notes.append("The switch asks for a login, so the checks were skipped. Type its login and press Run checks.")
            return
        with s:
            if not s.priv:
                r.notes.append("Couldn't get into enable mode: some checks need it")
            for cmd in HEALTH_CMDS:
                with self.lock:
                    r.health["step"] = cmd
                try:
                    out[cmd] = s.run(cmd, 120)
                except swcli.CLIError as e:
                    out[cmd] = None
                    if cmd == "show env all":
                        try:
                            out["show environment"] = s.run("show environment", 60)
                        except swcli.CLIError:
                            pass
                    elif "needs enable" in str(e):
                        r.notes.append(f"{cmd}: needs enable mode")
        h, finds = parse_health(out)
        with self.lock:
            h.pop("step", None)
            r.health = h
            r.health["raw"] = {k: (v or "")[-20000:] for k, v in out.items() if v}
            for k in ("model", "serial", "ios", "image"):
                if h.get(k) and not r.facts.get(k):
                    r.facts[k] = h[k]
            for lv, area, text in finds:
                r.add(lv, area, text)

    # ---------- IOS version ----------
    def recommended(self):
        out = []
        for ln in (self.conf.get("recommended") or "").splitlines():
            k, sep, v = ln.partition("=")
            if sep and k.strip() and v.strip():
                out.append((k.strip().upper(), v.strip()))
        return out

    def ios_check(self):
        r = self.run
        f = r.facts
        ver = f.get("ios") or r.health.get("ios")
        if not ver:
            return
        info = {"version": ver, "advice": None, "lv": "ok", "advisories": None}
        fam = " ".join(x for x in (f.get("model"), f.get("image"), f.get("ios_line")) if x).upper()
        rec = next(((k, v) for k, v in self.recommended() if k in fam), None)
        if rec:
            info["recommended"] = rec[1]
            if vtrain(rec[1]) != vtrain(ver):
                info.update(lv="info", advice=f"Running {ver}; the recommended version ({rec[1]}) is in another train")
            elif vkey(ver) < vkey(rec[1]):
                info.update(lv="warn", advice=f"Upgrade suggested: running {ver}, recommended is {rec[1]}")
            else:
                info["advice"] = f"Up to date ({ver}, recommended {rec[1]})"
        elif ver.startswith("12."):
            info.update(lv="warn", advice=f"IOS {ver} is the 12.x line, long out of support")
        else:
            info["advice"] = "No recommended version is set for this model (Settings below)"
        if self.conf.get("psirt_id") and self.conf.get("psirt_secret"):
            try:
                info["advisories"] = self.psirt(ver, "iosxe" if re.match(r"\d{2}\.", ver) and int(ver.split(".")[0]) >= 16 else "ios")
            except Exception as e:
                info["psirt_error"] = str(e)[:160]
        adv = info.get("advisories") or []
        crit = [a for a in adv if a["sir"] in ("Critical", "High")]
        if crit:
            info["lv"] = "warn" if info["lv"] == "ok" else info["lv"]
            r.add("warn", "Software", f"{len(crit)} critical/high security advisories for IOS {ver}")
        if info["lv"] == "warn" and info["advice"]:
            r.add("warn", "Software", info["advice"])
        elif info["advice"] and info["lv"] == "ok" and rec:
            r.add("ok", "Software", info["advice"])
        r.ios = info

    def psirt(self, ver, os_type):
        """Cisco openVuln API: advisories that affect this version. Cached for a day."""
        key = (os_type, ver)
        hit = self.psirt_cache.get(key)
        if hit and time.time() - hit[0] < 86400:
            return hit[1]
        if not self.token or time.time() > self.token[1]:
            data = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.conf["psirt_id"],
                                           "client_secret": self.conf["psirt_secret"]}).encode()
            req = urllib.request.Request("https://id.cisco.com/oauth2/default/v1/token", data=data,
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                j = json.load(resp)
            self.token = (j["access_token"], time.time() + int(j.get("expires_in", 3600)) - 60)
        url = f"https://apix.cisco.com/security/advisories/v2/OSType/{os_type}?version={urllib.parse.quote(ver)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token[0]}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                j = json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                j = {"advisories": []}
            else:
                raise
        out = []
        for a in j.get("advisories") or []:
            out.append({"id": a.get("advisoryId"), "title": (a.get("advisoryTitle") or "")[:160], "sir": a.get("sir"),
                        "url": a.get("publicationUrl"), "fixed": (a.get("firstFixed") or [])[:3], "cves": (a.get("cves") or [])[:4]})
        order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        out.sort(key=lambda a: order.get(a["sir"], 9))
        self.psirt_cache[key] = (time.time(), out)
        return out

    # ---------- preload ----------
    def builtin_vars(self):
        f = self.run.facts if self.run else {}
        return {"serial": f.get("serial") or "", "model": f.get("model") or "", "mac": f.get("base_mac") or "", "ios": f.get("ios") or ""}

    def prepare(self, body):
        text = str(body.get("text") or "")
        if body.get("template"):
            t = self.template(body["template"])
            if not t:
                raise ValueError("No such template")
            text = t["text"]
        if not text.strip():
            raise ValueError("Paste a config, load a file or pick a template")
        if len(text) > TPL_SIZE:
            raise ValueError("That config is too big (64 KB at most)")
        lines, notes = clean_config(text)
        names = sorted({m.group(1) for ln in lines for m in VAR_RE.finditer(ln)})
        vals = {**self.builtin_vars(), **{k: str(v).strip()[:120] for k, v in (body.get("vars") or {}).items() if re.fullmatch(r"\w{1,31}", str(k))}}
        for k, v in vals.items():
            if "\n" in v or "\r" in v:
                raise ValueError(f"Bad value for {k}")
        filled, missing = fill_vars(lines, vals)
        ifs = (self.run.health.get("interfaces") if self.run else None) or []
        warns = check_config(filled, ifs) if not missing else [{"lv": "bad", "n": 0, "text": "Fill in: " + ", ".join(missing)}]
        baud = self.con.conf["console_baud"]
        est = round(sum(len(x) + 2 for x in filled) * 10 / baud * 2 + len(filled) * 0.15)
        return {"lines": filled, "notes": notes, "vars": names, "values": {k: vals.get(k, "") for k in names}, "missing": missing,
                "warnings": warns, "n": len(filled), "est_s": est, "text": text}

    def do_preload(self, body):
        prep = body["_prep"]
        lines = prep["lines"]
        job = self.job
        with self.session(body.get("login")) as s:
            if not s.priv:
                raise swcli.CLIError("Can't get into enable mode. " + s.why_not_enable())
            job["step"] = "reading the config before"
            before = s.run("show running-config", 240)
            job["step"] = "configure terminal"
            s.drain(0.2, 1)
            s.link.write(b"configure terminal\r")
            s.until_prompt(20, answer_logins=False, echo="configure t")
            in_banner = None
            for n, ln in enumerate(lines, 1):
                if job.get("cancel"):
                    job["step"] = "cancelled"
                    break
                job["step"] = f"line {n} of {len(lines)}"
                m = re.match(r"^\s*banner\s+\S+\s+(\S)(.*)$", ln)
                if in_banner is None and m and m.group(1) not in m.group(2):
                    in_banner, quiet = m.group(1), True     # the switch now echoes text with no prompt
                elif in_banner is not None:
                    quiet = in_banner not in ln
                    if not quiet:
                        in_banner = None
                else:
                    quiet = False
                s.link.write(ln.encode("ascii", "replace") + b"\r")
                job["done"] = n
                got = self.read_line_answer(s, ln, quiet)
                said = got["text"]
                if got["question"]:
                    job["errors"].append({"n": n, "line": ln[:200], "said": f"The switch asked a question: {got['question']}. Stopped here."})
                    s.link.write(b"\x03")
                    job["step"] = "stopped at a question"
                    break
                e = ERR_OUT.search(said)
                if e:
                    job["errors"].append({"n": n, "line": ln[:200], "said": e.group(0).strip()[:160]})
                    if body.get("stop_on_error"):
                        job["step"] = "stopped at the first error"
                        break
            s.drain(0.3, 2)
            s.link.write(b"end\r")
            try:
                s.until_prompt(20, answer_logins=False)
            except swcli.CLIError:
                s.link.write(b"\x03\r")
                s.until_prompt(10, answer_logins=False)
            job["step"] = "reading the config after"
            after = s.run("show running-config", 240)
        import configs as configs_mod
        a, b = configs_mod.clean(before).splitlines(), configs_mod.clean(after).splitlines()
        diff = "\n".join(difflib.unified_diff(a, b, "before", "after", lineterm="", n=1))
        with self.lock:
            job.update(running=False, diff=diff[:200000], step="review" if job["step"].startswith("reading") else job["step"] + " · review",
                       changed=sum(1 for x in diff.splitlines() if x[:1] in "+-" and x[:3] not in ("+++", "---")))
            if self.run:
                self.run.notes.append(f"Preload sent {job['done']} lines, {len(job['errors'])} errors")

    def read_line_answer(self, s, line, in_banner):
        """After one pasted line: wait for the echo and then a prompt (or quiet, inside a banner)."""
        slow = re.match(r"\s*(crypto key generate|crypto pki|vlan \d|spanning-tree mode|sdm )", line, re.I)
        end = time.time() + (90 if slow else 12)
        buf = ""
        echo = line.strip()[:20]
        seen = not echo
        last = time.time()
        while time.time() < end:
            d = s.link.read(0.4)
            if d:
                buf += norm(d)
                last = time.time()
            if not seen:
                k = buf.find(echo)
                if k >= 0:
                    seen, buf = True, buf[k + len(echo):]
                elif time.time() - last > 2:
                    seen = True
                else:
                    continue
            tail = buf[-200:]
            if PROMPT.search(tail):
                return {"text": buf, "question": None}
            if in_banner and time.time() - last > 0.3:
                return {"text": buf, "question": None}
            q = QUESTION.search(tail)
            if q and time.time() - last > 0.8:
                return {"text": buf, "question": tail.strip().splitlines()[-1][:120]}
        return {"text": buf, "question": None}

    def interact(self, s, cmd, answers, timeout=60, last=None):
        """Run a command that asks questions. answers: [(regex, reply)]. Returns right after answering `last`."""
        s.drain(0.2, 2)
        s.link.write(cmd.encode() + b"\r")
        buf, end = "", time.time() + timeout
        while time.time() < end:
            d = s.link.read(1.0)
            if not d:
                continue
            buf += norm(d)
            tail = buf[-200:]
            for rx, reply in answers:
                if re.search(rx, tail, re.I):
                    if rx == last and callable(getattr(self, "_before_last", None)):
                        self._before_last()
                    s.link.write(reply)
                    if rx == last:
                        return buf
                    buf = ""
                    break
            else:
                if PROMPT.search(tail):
                    return buf
        return buf

    def do_save(self, body):
        job = self.job
        with self.session(body.get("login")) as s:
            job["step"] = "write memory"
            out = s.run("write memory", 120)
        ok = bool(re.search(r"\[OK\]|\bOK\b", out))
        with self.lock:
            job.update(saved=ok, step="saved" if ok else "write memory said: " + out.strip()[-100:])

    def do_reload(self, body):
        """erase=True: write erase + delete vlan.dat first. Either way the reload doesn't save."""
        job = self.job
        with self.session(body.get("login")) as s:
            if not s.priv:
                raise swcli.CLIError("Can't get into enable mode. " + s.why_not_enable())
            if body.get("erase"):
                job["step"] = "write erase"
                self.interact(s, "write erase", [(r"\[confirm\]\s*$", b"\r")], 60)
                job["step"] = "delete vlan.dat"
                self.interact(s, "delete flash:vlan.dat", [(r"Delete filename \[.*\]\?\s*$", b"\r"), (r"\[confirm\]\s*$", b"\r")], 30)
            job["step"] = "reload"

            def watch_next():  # watch the coming boot from its first line
                with self.lock:
                    old = self.job
                    self.arm(auto=True)
                    self.job = old
                    self.job.update(step="reloading", reloading=True)
                    self.busy = None
            self._before_last = watch_next
            try:
                self.interact(s, "reload", [(r"Save\?\s*\[yes/no\]:\s*$", b"no\r"), (r"Proceed with reload\?\s*\[confirm\]\s*$", b"\r")], 30,
                              last=r"Proceed with reload\?\s*\[confirm\]\s*$")
            finally:
                self._before_last = None

    # ---------- templates ----------
    def template(self, name):
        return next((t for t in self.templates if t["name"] == name), None)

    def save_template(self, name, text):
        name = str(name or "").strip()
        if not NAME_RE.fullmatch(name):
            raise ValueError("Template name: letters, numbers, spaces, . - ( ) (60 at most)")
        text = str(text or "")
        if not text.strip() or len(text) > TPL_SIZE:
            raise ValueError("Template is empty or over 64 KB")
        self.templates = [t for t in self.templates if t["name"] != name] + [{"name": name, "text": text, "t": time.time()}]
        if len(self.templates) > TPL_MAX:
            raise ValueError(f"At most {TPL_MAX} templates")
        self.templates.sort(key=lambda t: t["name"].lower())
        save_json(self.tpl_file, self.templates, private=True)

    # ---------- API ----------
    def get(self, sub, arg, q, user):
        admin = user and user.get("role") == "admin"
        if not sub:
            with self.lock:
                r = self.run
                cs = self.con.status() if self.con else {}
                return {"armed": self.armed, "busy": self.busy, "run": r.public() if r else None,
                        "job": {k: v for k, v in self.job.items() if k not in ("text", "lines")} if self.job else None,
                        "history": list(reversed(self.history)), "console": {"open": cs.get("open"), "port": cs.get("port"), "baud": cs.get("baud"), "error": cs.get("error")},
                        "templates": [{"name": t["name"], "t": t["t"], "lines": t["text"].count("\n") + 1} for t in self.templates] if admin else [],
                        "conf": {**{k: v for k, v in self.conf.items() if k != "psirt_secret"}, "psirt_secret_set": bool(self.conf.get("psirt_secret"))} if admin else None}
        if sub in ("report", "log", "text") and arg and ID_RE.fullmatch(arg):
            f = self.rep_dir / f"{arg}.json"
            if not f.is_file():
                raise ValueError("not found")
            if sub == "report":
                d = load_json(f, {})
                if not admin:
                    d.get("health", {}).pop("raw", None)
                return d
            if sub == "log":
                lf = self.rep_dir / f"{arg}.log"
                return Reply(lf.read_bytes() if lf.is_file() else b"", "text/plain; charset=utf-8", filename=f"boot-{arg}.log")
            return Reply(self.report_text(load_json(f, {})).encode(), "text/plain; charset=utf-8", filename=f"intake-{arg}.txt")
        if sub == "template" and admin:
            t = self.template(urllib.parse.unquote(arg or ""))
            if not t:
                raise ValueError("No such template")
            return t
        raise ValueError("not found")

    def report_text(self, d):
        f = d.get("facts") or {}
        h = d.get("health") or {}
        out = [f"Switch intake report  {time.strftime('%Y-%m-%d %H:%M', time.localtime(d.get('t', 0)))}",
               f"Result: {(d.get('level') or '?').upper()}   {d.get('ready_text') or ''}", "",
               f"Model     {f.get('model') or h.get('model') or '?'}", f"Serial    {f.get('serial') or h.get('serial') or '?'}",
               f"Base MAC  {f.get('base_mac') or '?'}", f"IOS       {f.get('ios') or h.get('ios') or '?'}",
               f"Image     {f.get('image') or h.get('image') or '?'}", f"Boot      {f.get('bootloader') or '?'}",
               f"RAM       {round(f['ram_kb'] / 1024) if f.get('ram_kb') else '?'} MB", f"Last reset {f.get('last_reset') or '?'}", ""]
        st = d.get("stages") or {}
        if st:
            out.append("Boot timeline: " + ", ".join(f"{name} {st[k]} s" for k, name in STAGES if k in st))
        if d.get("post"):
            out.append("Self tests: " + ", ".join(f"{p['name']} {p['status']}" for p in d["post"]))
        out.append("")
        out.append("Findings:")
        for x in d.get("findings") or []:
            out.append(f"  [{x['lv'].upper():4}] {x['area']}: {x['text']}")
        io = d.get("iosinfo") or {}
        if io.get("advice"):
            out += ["", f"IOS: {io['advice']}"]
        for a in io.get("advisories") or []:
            out.append(f"  {a['sir']:8} {a['id']}  {a['title']}")
        if d.get("notes"):
            out += ["", "Notes:"] + [f"  {n}" for n in d["notes"]]
        return "\n".join(out) + "\n"

    def post(self, action, body, user):
        with self.lock:
            if action == "settings":
                new = dict(body)
                if not new.get("psirt_secret"):
                    new.pop("psirt_secret", None)
                if new.get("psirt_clear"):
                    new.update(psirt_id="", psirt_secret="")
                self.save_conf(new)
                return {}
            if action == "arm":
                if self.busy:
                    raise ValueError(f"Busy: {self.busy}")
                self.arm(auto=False)
                return {}
            if action == "stop":
                if self.run and not self.run.ready:
                    self.run.phase = "done"
                    if self.run.rx:
                        self.run.finish_findings()
                        self.save_report()
                self.armed = False
                return {}
            if action == "checks":
                if not self.run or not self.run.ready:
                    raise ValueError("No switch is up yet")
                login = swcli.take_login(self.p, body)
                self.spawn("checks", self.do_checks, {"login": login})
                return {}
            if action == "prepare":
                return self.prepare(body)
            if action == "preload":
                if self.job and self.job.get("running"):
                    raise ValueError("A preload is already running")
                if self.busy:
                    raise ValueError(f"Busy: {self.busy}. Try again in a moment.")
                login = swcli.take_login(self.p, body)
                prep = self.prepare(body)
                if prep["missing"]:
                    raise ValueError("Fill in: " + ", ".join(prep["missing"]))
                if not prep["lines"]:
                    raise ValueError("Nothing to send")
                self.job = {"running": True, "t": time.time(), "step": "starting", "done": 0, "total": prep["n"], "errors": [],
                            "diff": None, "saved": False, "text": prep["text"], "save_as": str(body.get("save_as") or "")[:60]}
                if body.get("save_as"):
                    self.save_template(body["save_as"], prep["text"])
                self.spawn("preload", self.do_preload, {"_prep": prep, "login": login, "stop_on_error": bool(body.get("stop_on_error"))})
                return {}
            if action == "cancel":
                if self.job:
                    self.job["cancel"] = True
                return {}
            if action == "save":
                if not self.job or self.job.get("running"):
                    raise ValueError("Nothing to save yet")
                self.job["running"] = True
                self.spawn("save", lambda b: self._job_done(self.do_save, b), {"login": swcli.take_login(self.p, body)})
                return {}
            if action in ("reload", "erase"):
                if not self.run or not self.run.ready:
                    raise ValueError("No switch is up")
                if self.job and self.job.get("running"):
                    raise ValueError("Wait for the preload to finish")
                self.job = {**(self.job or {"errors": [], "diff": None, "done": 0, "total": 0, "text": ""}), "running": True, "step": action, "t": time.time()}
                self.spawn(action, lambda b: self._job_done(self.do_reload, b), {"login": swcli.take_login(self.p, body), "erase": action == "erase"})
                return {}
            if action == "template_save":
                self.save_template(body.get("name"), body.get("text"))
                return {}
            if action == "template_delete":
                self.templates = [t for t in self.templates if t["name"] != body.get("name")]
                save_json(self.tpl_file, self.templates, private=True)
                return {}
            if action == "forget":
                i = str(body.get("id") or "")
                if not ID_RE.fullmatch(i):
                    raise ValueError("Bad id")
                self.history = [h for h in self.history if h["id"] != i]
                save_json(self.hist_file, self.history)
                for f in self.rep_dir.glob(f"{i}.*"):
                    f.unlink(missing_ok=True)
                return {}
        raise ValueError("Unknown action")

    def _job_done(self, fn, body):
        try:
            fn(body)
        finally:
            with self.lock:
                if self.job:
                    self.job["running"] = False
