"""Find Source engine game servers: ask one server directly, or broadcast and collect the answers
the way a game's own LAN tab does."""
import ipaddress
import json
import select
import socket
import subprocess
import sys
import time

from . import source

DEFAULT_PORTS = tuple(range(27015, 27031))   # servers take the next free port up from 27015


def broadcast_addresses():
    """Where to send a broadcast so it reaches every network this machine is on."""
    found = ["255.255.255.255"]
    if sys.platform.startswith("linux"):
        # 255.255.255.255 only leaves by the default route, so add each network's own broadcast address
        try:
            out = subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True,
                                 text=True, timeout=3).stdout
            for iface in json.loads(out):
                flags = iface.get("flags", [])
                if "LOOPBACK" in flags or "UP" not in flags:
                    continue
                for addr in iface.get("addr_info", []):
                    b = addr.get("broadcast")
                    if b and b not in found:
                        found.append(b)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return found


def _local_addresses():
    try:
        return [a for a in socket.gethostbyname_ex(socket.gethostname())[2] if not a.startswith("127.")]
    except OSError:
        return []


def _open_sockets():
    """One socket, plus one per local address where that is what steers a broadcast out of each network card."""
    binds = [""] if sys.platform.startswith("linux") else [""] + _local_addresses()
    socks = []
    for addr in binds:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((addr, 0))
        except OSError:
            s.close()
            continue
        s.setblocking(False)
        socks.append(s)
    return socks


def scan(ports=DEFAULT_PORTS, timeout=2.0, targets=None):
    """Ask every address in targets (default: broadcast) on every port, and return a ServerInfo
    for each server that answers within timeout seconds."""
    targets = list(targets) if targets else broadcast_addresses()
    socks = _open_sockets()
    if not socks:
        return []
    found, asked = {}, {}
    try:
        start = time.monotonic()
        for s in socks:
            for target in targets:
                for port in ports:
                    try:
                        s.sendto(source.info_request(), (target, port))
                    except OSError:
                        pass                    # no route out of this socket to that network
        deadline = time.monotonic() + timeout
        while True:
            wait = deadline - time.monotonic()
            if wait <= 0:
                break
            ready, _, _ = select.select(socks, [], [], wait)
            for s in ready:
                try:
                    data, addr = s.recvfrom(65535)
                except OSError:
                    continue
                now = time.monotonic()
                if addr in found:
                    continue
                challenge = source.challenge_from(data)
                if challenge is not None:
                    if addr not in asked:       # answer the challenge once, and give it time to come back
                        asked[addr] = now
                        try:
                            s.sendto(source.info_request(challenge), addr)
                        except OSError:
                            pass
                        deadline = max(deadline, now + min(timeout, 1.0))
                    continue
                try:
                    info = source.decode_info(data)
                except source.DecodeError:
                    continue
                info.host, info.port = addr[0], addr[1]
                info.ping_ms = round((now - asked.get(addr, start)) * 1000, 1)
                found[addr] = info
    finally:
        for s in socks:
            s.close()
    return sorted(found.values(), key=lambda i: (int(ipaddress.ip_address(i.host)), i.port))


def players(host, port=27015, timeout=2.0):
    """Who is on one server: a list of source.Player, or None if it does not answer.
    Bots are listed like people; Counter-Strike's score is the player's kills."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(timeout)
        addr = (socket.gethostbyname(host), port)
        request = source.player_request()
        for _ in range(3):                      # the challenge, then the list (a fresh challenge once more)
            s.sendto(request, addr)
            try:
                data, _ = s.recvfrom(65535)
            except socket.timeout:
                return None
            challenge = source.challenge_from(data)
            if challenge is not None:
                request = source.player_request(challenge)
                continue
            try:
                return source.decode_players(data)
            except source.DecodeError:
                return None
        return None
    except OSError:
        return None
    finally:
        s.close()


def query(host, port=27015, timeout=2.0):
    """Ask one server. Returns its ServerInfo, or None if it does not answer."""
    found = scan(ports=(port,), timeout=timeout, targets=[socket.gethostbyname(host)])
    return found[0] if found else None
