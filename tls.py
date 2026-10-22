"""HTTPS for the page: a certificate made on the probe with openssl, and an SSL context the server swaps live.

Self-signed for now. The files are plain PEM (cert.pem, key.pem), so a certificate signed by your own
root CA can replace them later.
"""
import ipaddress
import os
import re
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from pathlib import Path

DEFAULTS = {"https_redirect": True}
NAME_RE = re.compile(r"(?=.{1,253}$)[A-Za-z0-9*]([A-Za-z0-9\-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,62}))*\.?")


def parse_names(text):
    """'probe-01, probe-01.local, 192.168.1.221' -> (dns names, ips)."""
    dns, ips = [], []
    for part in re.split(r"[,\s]+", str(text)):
        part = part.strip()
        if not part:
            continue
        try:
            ips.append(str(ipaddress.ip_address(part)))
            continue
        except ValueError:
            pass
        if not NAME_RE.fullmatch(part):
            raise ValueError(f"Not a host name or IP: {part[:40]}")
        dns.append(part.rstrip(".").lower())
    if not dns and not ips:
        raise ValueError("Give the certificate at least one name or IP")
    if len(dns) + len(ips) > 20:
        raise ValueError("At most 20 names")
    return list(dict.fromkeys(dns)), list(dict.fromkeys(ips))


class TLS:
    def __init__(self, probe, conf_dir):
        self.p = probe
        self.dir = Path(conf_dir) / "tls"
        self.cert = self.dir / "cert.pem"
        self.key = self.dir / "key.pem"
        self.ctx = None
        self.error = None
        self.lock = threading.Lock()

    def default_names(self):
        host = socket.gethostname()
        names = [host, f"{host}.local"]
        if self.p.net.get("ip"):
            names.append(self.p.net["ip"])
        return names

    def ensure(self):
        """Make a certificate on first start, then load it."""
        if not (self.cert.exists() and self.key.exists()):
            try:
                self.generate(", ".join(self.default_names()))
                return
            except (OSError, ValueError, subprocess.CalledProcessError) as e:
                self.error = f"couldn't make a certificate: {e}"
                return
        self.load()

    def load(self):
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(self.cert, self.key)
            self.ctx, self.error = ctx, None
        except (OSError, ssl.SSLError) as e:
            self.ctx, self.error = None, f"couldn't load the certificate: {e}"

    def generate(self, names, days=3650):
        days = int(days)
        if not 1 <= days <= 3650:
            raise ValueError("Days must be 1-3650")
        dns, ips = parse_names(names)
        cn = (dns or ips)[0]
        san = ",".join([f"DNS:{d}" for d in dns] + [f"IP:{i}" for i in ips])
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        with self.lock, tempfile.TemporaryDirectory(dir=self.dir) as tmp:
            key, cert = Path(tmp) / "key.pem", Path(tmp) / "cert.pem"
            r = subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                 "-sha256", "-days", str(days), "-keyout", str(key), "-out", str(cert),
                 "-subj", f"/CN={cn}/O=Probe Deck", "-addext", f"subjectAltName={san}",
                 "-addext", "keyUsage=critical,digitalSignature", "-addext", "extendedKeyUsage=serverAuth",
                 "-addext", "basicConstraints=critical,CA:FALSE"],
                capture_output=True, text=True, timeout=30)
            if r.returncode:
                raise ValueError("openssl: " + (r.stderr.strip().splitlines() or ["failed"])[-1][:200])
            os.chmod(key, 0o600)
            os.replace(key, self.key)
            os.replace(cert, self.cert)
        self.load()

    def info(self):
        if not self.cert.exists():
            return None
        try:
            d = ssl._ssl._test_decode_cert(str(self.cert))
        except (ssl.SSLError, OSError, AttributeError):
            return None
        flat = lambda seq: {k: v for rdn in seq for k, v in rdn}
        subj, iss = flat(d.get("subject", ())), flat(d.get("issuer", ()))
        names = [v for k, v in d.get("subjectAltName", ())]
        fp = subprocess.run(["openssl", "x509", "-in", str(self.cert), "-noout", "-fingerprint", "-sha256"],
                            capture_output=True, text=True).stdout.strip().partition("=")[2]
        key = subprocess.run(["openssl", "x509", "-in", str(self.cert), "-noout", "-text"], capture_output=True, text=True).stdout
        m = re.search(r"Public Key Algorithm: (\S+).*?\((\d+) bit\)", key, re.S)
        ip = self.p.net.get("ip")
        return {
            "cn": subj.get("commonName"), "issuer": iss.get("commonName") or iss.get("organizationName"),
            "self_signed": d.get("subject") == d.get("issuer"), "names": names,
            "from": ssl.cert_time_to_seconds(d["notBefore"]), "to": ssl.cert_time_to_seconds(d["notAfter"]),
            "serial": d.get("serialNumber"), "sha256": fp,
            "key": f"{'ECDSA' if m and 'ec' in m.group(1).lower() else 'RSA' if m else '?'} {m.group(2) if m else ''}".strip(),
            "covers_ip": bool(ip and ip in names), "ip": ip, "now": time.time(),
        }
