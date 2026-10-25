"""Run commands on a Cisco switch's CLI, over SSH or through the console cable.

Configs and Cable test use this. The login (user, password, enable secret) is kept in
/etc/probe-deck/logins.json, root-only, and never sent to the page: the page only learns
whether one is set. One login covers every switch; a switch can have its own.
"""
import json
import os
import queue
import re
import threading
import time

try:
    import paramiko
except ImportError:  # the page says so
    paramiko = None

PROMPT = re.compile(r"(?:^|\n)([A-Za-z0-9][\w.\-()/:]{0,63})([>#]) ?$")
NAME_RE = re.compile(r"[\w.\-@\\]{1,64}")
IP_RE = re.compile(r"\d{1,3}(\.\d{1,3}){3}")


class CLIError(Exception):
    pass


class Logins:
    """Switch logins, root-only on disk."""

    def __init__(self, conf_dir):
        self.file = os.path.join(conf_dir, "logins.json")
        self.lock = threading.Lock()
        self.data = {"user": "", "password": "", "enable": "", "per": {}}
        try:
            with open(self.file) as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data.update({k: d[k] for k in self.data if k in d})
        except (OSError, ValueError):
            pass

    def save(self):
        tmp = self.file + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.file)

    def get(self, host=None):
        with self.lock:
            d = dict(self.data)
            per = dict(self.data["per"].get(host) or {}) if host else {}
        d.pop("per")
        d.update({k: v for k, v in per.items() if v})
        return d

    def public(self):
        with self.lock:
            d = self.data
            return {"user": d["user"], "password_set": bool(d["password"]), "enable_set": bool(d["enable"]),
                    "per": {h: {"user": v.get("user", ""), "password_set": bool(v.get("password"))} for h, v in d["per"].items()}}

    def set(self, body):
        host = str(body.get("host") or "").strip()
        if host and not IP_RE.fullmatch(host):
            raise ValueError("Bad switch address")
        user = str(body.get("user") or "").strip()
        if user and not NAME_RE.fullmatch(user):
            raise ValueError("Bad user name")
        with self.lock:
            tgt = self.data["per"].setdefault(host, {}) if host else self.data
            tgt["user"] = user
            for k in ("password", "enable"):
                if body.get(k):
                    v = str(body[k])
                    if len(v) > 128 or "\n" in v or "\r" in v:
                        raise ValueError(f"Bad {k}")
                    tgt[k] = v
                if body.get("clear_" + k):
                    tgt[k] = ""
            if host and body.get("remove"):
                self.data["per"].pop(host, None)
            self.save()


# ---------- transports: write bytes, read what has arrived ----------
class SSHLink:
    PORT = 22   # tests change it

    def __init__(self, host, login, timeout=12):
        if not paramiko:
            raise CLIError("python3-paramiko isn't installed on the probe (apt install python3-paramiko)")
        if not login.get("user"):
            raise CLIError("Set the switch login first (Configs page, Switch login)")
        self.cli = paramiko.SSHClient()
        # Switches get new keys after a reload or upgrade, and this is a LAN tool: take the key as offered.
        self.cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.cli.connect(host, port=self.PORT, username=login["user"], password=login.get("password") or None, look_for_keys=False,
                             allow_agent=False, timeout=timeout, banner_timeout=timeout, auth_timeout=timeout)
        except paramiko.AuthenticationException:
            raise CLIError(f"{host} refused the login for {login['user']}") from None
        except Exception as e:
            raise CLIError(f"Can't SSH to {host}: {str(e)[:120] or type(e).__name__}") from None
        self.ch = self.cli.invoke_shell(term="vt100", width=500, height=1000)
        self.ch.settimeout(0.3)

    def write(self, b):
        self.ch.send(b)

    def read(self, timeout):
        end = time.time() + timeout
        out = b""
        while time.time() < end:
            try:
                d = self.ch.recv(65536)
            except Exception:  # socket.timeout
                d = b""
                if out:
                    break
                continue
            if not d:
                if self.ch.closed or self.ch.exit_status_ready():
                    break
                continue
            out += d
            if not self.ch.recv_ready():
                break
        return out

    def close(self):
        try:
            self.cli.close()
        except Exception:
            pass


class ConsoleLink:
    """The console cable. Whoever is watching the Console page sees the commands go by."""

    def __init__(self, console):
        if not console.ser:
            raise CLIError("No console cable is connected")
        if console.paste and not console.paste.get("done_at"):
            raise CLIError("A paste is running on the console")
        if not console.job_lock.acquire(timeout=1):
            raise CLIError("The console is busy with another job")
        self.con = console
        self.q = console.tap()

    def write(self, b):
        if not self.con.write(b):
            raise CLIError("The console cable was unplugged")

    def read(self, timeout):
        out = b""
        try:
            out += self.q.get(timeout=timeout)
            while True:
                out += self.q.get(timeout=0.15)
        except queue.Empty:
            pass
        return out

    def close(self):
        self.con.untap(self.q)
        self.con.job_lock.release()


# ---------- the Cisco CLI on top of a transport ----------
class Session:
    def __init__(self, link, login):
        self.link = link
        self.login = login
        self.host = None
        self.priv = False
        self.enable_said = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.link.write(b"exit\r") if isinstance(self.link, SSHLink) else None
        except Exception:
            pass
        self.link.close()

    def drain(self, quiet=0.4, most=3.0):
        """Throw away whatever is still waiting (a banner, a prompt the switch printed on connect),
        so the next prompt read is the answer to the next command and not an old one."""
        end = time.time() + most
        while time.time() < end:
            if not self.link.read(quiet):
                return

    def until_prompt(self, timeout, answer_logins=True, echo=None):
        """Read until a CLI prompt. Answers --More--, and Username:/Password: on the console.
        With echo, only a prompt after the switch echoed that text counts."""
        buf = ""
        t0 = time.time()
        end = t0 + timeout
        tries = 0
        base = None if echo else 0
        while time.time() < end:
            d = self.link.read(min(1.0, max(0.1, end - time.time())))
            if not d:
                if base is None and time.time() - t0 > 3:
                    base = 0   # no echo (some devices don't): take any prompt
                continue
            buf += d.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
            if base is None:
                k = buf.find(echo)
                if k < 0:
                    if time.time() - t0 <= 3:
                        continue
                    base = 0
                else:
                    base = k + len(echo)
            tail = buf[base:][-200:]
            if re.search(r"--More--\s*$", tail):
                buf = re.sub(r"\s*--More--\s*$", "", buf)
                self.link.write(b" ")
                continue
            if answer_logins and re.search(r"(Username|login): ?$", tail, re.I):
                if not self.login.get("user") or tries > 2:
                    raise CLIError("The switch asks for a login: set the switch login first")
                self.link.write(self.login["user"].encode() + b"\r")
                tries += 1
                continue
            if answer_logins and re.search(r"Password: ?$", tail):
                if tries > 3:
                    raise CLIError("The switch refused the login")
                self.link.write((self.login.get("password") or "").encode() + b"\r")
                tries += 1
                continue
            m = PROMPT.search(tail)
            if m:
                self.host, self.priv = m.group(1), m.group(2) == "#"
                return buf[base:]
        raise CLIError("The switch didn't answer (no prompt)" if not buf.strip() else f"No prompt after: {buf.strip()[-80:]}")

    def start(self, enable=True):
        self.drain(0.6, 4)
        self.link.write(b"\r")
        self.until_prompt(20)
        self.drain(0.3, 1)
        self.enable_said = None
        if enable and not self.priv:
            self.try_enable()
        self.run("terminal length 0", 10)
        try:
            self.run("terminal width 0", 10)
        except CLIError:
            pass  # some IOS versions want 24-512
        return self

    def try_enable(self):
        """enable, answering a password prompt with: the enable secret, then the login password, then nothing.
        If it still doesn't work the session carries on at '>': a command that needs '#' says why."""
        tries = [p for p in (self.login.get("enable"), self.login.get("password"), "") if p is not None]
        tries = [p for i, p in enumerate(tries) if p not in tries[:i]]
        self.drain(0.3, 2)
        self.link.write(b"enable\r")
        buf, said = "", ""
        t0 = time.time()
        end = t0 + 15
        echoed = False
        while time.time() < end:
            d = self.link.read(1).decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
            buf += d
            said += d
            if not echoed:   # the answer starts after the switch echoes "enable"
                k = buf.find("enable")
                if k < 0 and time.time() - t0 < 3:
                    continue
                buf = buf[k + 6:] if k >= 0 else buf
                echoed = True
            if re.search(r"Password: ?$", buf):
                if not tries:
                    self.link.write(b"\x03\r")   # out of guesses: back to the prompt
                    buf = ""
                    continue
                self.link.write(tries.pop(0).encode() + b"\r")
                buf = ""
            elif PROMPT.search(buf[-120:]):
                break
        m = PROMPT.search(buf[-120:])
        if m:
            self.host, self.priv = m.group(1), m.group(2) == "#"
        if not self.priv:
            lines = [ln.strip() for ln in said.split("\n") if ln.strip().startswith("%")]
            self.enable_said = lines[-1] if lines else None

    def why_not_enable(self):
        said = self.enable_said or ""
        if "No password set" in said:
            head = ("The switch has no enable password, and IOS won't let a remote (SSH/telnet) login into enable mode "
                    "without one (% No password set).")
        elif said:
            head = f"The switch didn't let the login into enable mode ({said})."
        else:
            head = "The login lands at '>' and couldn't get into enable mode."
        return (head + " Fix it on the switch with one of: 'username <user> privilege 15' (the login then lands at #), "
                "or 'enable secret <password>' (and add it to the login here). Or pick 'Console cable' as the switch: "
                "the console needs no enable password.")

    def run(self, cmd, timeout=60):
        self.drain(0.2, 2)
        self.link.write(cmd.encode() + b"\r")
        out = self.until_prompt(timeout, answer_logins=False, echo=cmd[:24])
        lines = out.split("\n")
        if lines and not lines[0].strip():
            lines = lines[1:]   # the rest of the echoed line
        if lines and PROMPT.search(lines[-1]):
            lines = lines[:-1]
        text = "\n".join(lines).strip("\n")
        bad = re.search(r"^\s*% (Invalid|Incomplete|Ambiguous|Authorization failed|Command authorization).*$", text, re.M)
        if bad:
            if not self.priv and cmd not in ("terminal length 0", "terminal width 0"):
                raise CLIError(f"'{cmd}' needs enable mode. " + self.why_not_enable())
            raise CLIError(f"The switch didn't take '{cmd}': {bad.group(0).strip()[:100]}")
        return text


def open_session(probe, target, enable=True):
    """target: {"host": "<switch IP>"} for SSH, or {"console": True} for the cable."""
    logins = probe.logins
    once = {k: v for k, v in (target.get("login") or {}).items() if v}   # typed on the page for this run
    if target.get("console"):
        login = {**logins.get(None), **once}
        link = ConsoleLink(probe.console)
    else:
        host = str(target.get("host") or "")
        if not IP_RE.fullmatch(host):
            raise CLIError("Pick a switch")
        login = {**logins.get(host), **once}
        link = SSHLink(host, login)
    s = Session(link, login)
    try:
        return s.start(enable)
    except Exception:
        s.__exit__()
        raise


def take_login(probe, body, host=None):
    """A login typed on the page: used for this run, and saved for this switch when "remember" is ticked."""
    once = {}
    user = str(body.get("user") or "").strip()
    if user:
        if not NAME_RE.fullmatch(user):
            raise ValueError("Bad user name")
        once["user"] = user
    for k in ("password", "enable"):
        v = str(body.get(k) or "")
        if v:
            if len(v) > 128 or "\n" in v or "\r" in v:
                raise ValueError(f"Bad {k}")
            once[k] = v
    if once and body.get("remember"):
        probe.logins.set({"host": host or "", "user": once.get("user") or probe.logins.get(host).get("user", ""), **once})
    return once


class CliApp:
    """GET /api/x/cli: the login (without secrets). POST save, test."""
    name = "cli"
    VIEWER_POST = ()
    RAW_POST = ()

    def status(self):
        return None

    def __init__(self, probe):
        self.p = probe

    def get(self, sub, arg, q, user):
        return {**self.p.logins.public(), "paramiko": bool(paramiko), "console": bool(self.p.console.ser)}

    def post(self, action, body, user):
        if action == "save":
            self.p.logins.set(body)
            return {}
        if action == "test":
            t0 = time.time()
            try:
                with open_session(self.p, body) as s:
                    ver = s.run("show version | include uptime", 20)
                    return {"host": s.host, "priv": s.priv, "says": ver[:200], "secs": round(time.time() - t0, 1)}
            except CLIError as e:
                raise ValueError(str(e)) from None
        raise ValueError("Unknown action")
