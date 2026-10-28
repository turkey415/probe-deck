"""Switch alarms: which switch log lines mean a port is in trouble, and the SNMP err-disable table.

Shared with GXL-LANapp's Event Server: copy this file unchanged between the two repos (like
pc_snmp.py). Standard library only, and nothing here knows about either app.

    m = match_line("%PM-4-ERR_DISABLE: bpduguard error detected on Gi0/14, putting Gi0/14 in err-disable state")
    # {"kind": "errdisable", "state": "set", "port": "Gi0/14", "cause": "bpduGuard", ...}

kind   errdisable | stpguard | macflap
state  set (the problem started or is still happening) | clear (the switch says it's over)
"""
import re

VERSION = 1

# CISCO-ERR-DISABLE-MIB cErrDisableIfStatusCause, indexed by ifIndex then VLAN (0 = the whole port).
ERRDISABLE_CAUSE_OID = (1, 3, 6, 1, 4, 1, 9, 9, 548, 1, 3, 1, 1, 2)
CAUSES = {
    1: "udld", 2: "bpduGuard", 3: "channelMisconfig", 4: "pagpFlap", 5: "dtpFlap", 6: "linkFlap", 7: "l2ptGuard",
    8: "dot1xSecurityViolation", 9: "portSecurityViolation", 10: "gbicInvalid", 11: "dhcpRateLimit", 12: "unicastFlood",
    13: "vmps", 14: "stormControl", 15: "inlinePower", 16: "arpInspection", 17: "portLoopback", 18: "packetBuffer",
    19: "macLimit", 20: "linkMonitorFailure", 21: "oamRemoteFailure", 22: "dot1adIncompEtype", 23: "dot1adIncompTunnel",
    24: "sfpConfigMismatch", 25: "communityLimit", 26: "invalidPolicy", 27: "lsGroup", 28: "ekey", 29: "portModeFailure",
    30: "pppoeIaRateLimit", 31: "oamRemoteCriticalEvent", 32: "oamRemoteDyingGasp", 33: "oamRemoteLinkFault", 34: "mvrp",
    35: "tranceiverIncomp", 36: "other", 37: "portReinitLimitReached", 38: "adminRxBBCreditPerfBufIncomp",
    39: "ficonNotEnabled", 40: "adminModeIncomp", 41: "adminSpeedIncomp", 42: "adminRxBBCreditIncomp",
    43: "adminRxBufSizeIncomp", 44: "eppFailure", 45: "osmEPortUp", 46: "osmNonEPortUp", 47: "udldUniDir",
    48: "udldTxRxLoop", 49: "udldNeighbourMismatch", 50: "udldEmptyEcho", 51: "udldAggrasiveModeLinkFailed",
    52: "excessivePortInterrupts", 53: "channelErrDisabled", 54: "hwProgFailed", 55: "internalHandshakeFailed",
    56: "stpInconsistencyOnVpcPeerLink", 57: "stpPortStateFailure", 58: "ipConflict", 59: "multipleMSapIdsRcvd",
    60: "oneHundredPdusWithoutAck", 61: "ipQosCompatCheckFailure", 62: "loopDetect",
}
# The names IOS prints in %PM-4-ERR_DISABLE lines, where they differ from the MIB's.
SYSLOG_CAUSES = {
    "psecure-violation": "portSecurityViolation", "security-violation": "dot1xSecurityViolation",
    "loopback": "portLoopback", "link-flap": "linkFlap", "channel-misconfig": "channelMisconfig",
    "channel-misconfig (stp)": "channelMisconfig", "dhcp-rate-limit": "dhcpRateLimit",
    "arp-inspection": "arpInspection", "storm-control": "stormControl", "inline-power": "inlinePower",
    "gbic-invalid": "gbicInvalid", "sfp-config-mismatch": "sfpConfigMismatch", "pagp-flap": "pagpFlap",
    "dtp-flap": "dtpFlap", "l2ptguard": "l2ptGuard", "mac-limit": "macLimit", "unicast-flood": "unicastFlood",
    "link-monitor-failure": "linkMonitorFailure", "bpduguard": "bpduGuard", "udld": "udld", "vmps": "vmps",
    "loopdetect": "loopDetect", "pppoe-ia-rate-limit": "pppoeIaRateLimit", "mvrp": "mvrp",
}
_BY_LOWER = {v.lower(): v for v in CAUSES.values()}

# What a cause means, for the people walking to the seat.
CAUSE_TEXT = {
    "bpduGuard": "BPDU guard: a switch, or a PC bridging two networks, was plugged into the port",
    "portSecurityViolation": "port security: a MAC address the port doesn't allow",
    "dot1xSecurityViolation": "802.1X security violation",
    "linkFlap": "link flapping: it went up and down too often (cable, NIC or a bad jack)",
    "portLoopback": "loopback: the port hears its own frames, so there's a loop",
    "loopDetect": "loop detected",
    "udld": "UDLD: the link only works one way (a cable or fiber fault)",
    "udldUniDir": "UDLD: the link only works one way (a cable or fiber fault)",
    "udldTxRxLoop": "UDLD: the port hears itself (a loop)",
    "udldNeighbourMismatch": "UDLD: the far end isn't the neighbor it should be (crossed cables)",
    "channelMisconfig": "EtherChannel set up differently at the two ends",
    "dhcpRateLimit": "DHCP snooping rate limit: too many DHCP packets",
    "arpInspection": "ARP inspection: too many or bad ARP packets",
    "stormControl": "storm control: a broadcast or multicast storm",
    "inlinePower": "PoE fault on the port",
    "gbicInvalid": "the SFP isn't supported",
    "sfpConfigMismatch": "the SFP doesn't match the port's settings",
    "pagpFlap": "PAgP flapping", "dtpFlap": "DTP flapping (trunk negotiation)",
    "l2ptGuard": "Layer 2 protocol tunnel guard", "macLimit": "too many MAC addresses on the port",
    "unicastFlood": "unicast flood", "ipConflict": "IP address conflict",
}
STP_TEXT = {
    "ROOTGUARD_BLOCK": "root guard: something on the port claimed to be the spanning-tree root",
    "LOOPGUARD_BLOCK": "loop guard: BPDUs stopped arriving, so the port could loop",
    "BLOCK_PVID_PEER": "native VLAN mismatch with the switch at the other end",
    "BLOCK_PVID_LOCAL": "native VLAN mismatch with the switch at the other end",
    "RECV_PVID_ERR": "native VLAN mismatch with the switch at the other end",
    "BLOCK_PORT_TYPE": "port type inconsistent (trunk on one end, access on the other)",
    "CHNL_MISCFG": "spanning tree found a loop: EtherChannel set up differently at the two ends",
    "BLOCK_BPDUGUARD": "BPDU guard: a switch, or a PC bridging two networks, was plugged into the port",
}
STP_CLEAR = {"ROOTGUARD_UNBLOCK", "LOOPGUARD_UNBLOCK", "UNBLOCK_CONSIST_PORT", "UNBLOCK_PORT_TYPE"}

SHORT = [("TwentyFiveGigE", "Twe"), ("HundredGigE", "Hu"), ("FortyGigabitEthernet", "Fo"),
         ("TenGigabitEthernet", "Te"), ("TwoGigabitEthernet", "Tw"), ("FiveGigabitEthernet", "Fi"),
         ("AppGigabitEthernet", "Ap"), ("GigabitEthernet", "Gi"), ("FastEthernet", "Fa"), ("Port-channel", "Po"),
         ("Ethernet", "Eth")]
CODE = re.compile(r"%([A-Z0-9_]+(?:-[A-Z0-9_]+)*?)-(\d)-([A-Z0-9_]+)\s*:\s*(.*)", re.S)
# An interface: letters then slot/port numbers (Gi1/0/14, GigabitEthernet0/1), or a port-channel.
IFACE = re.compile(r"\b((?:[A-Za-z][A-Za-z\-]*)\d+(?:/\d+)+(?:\.\d+)?|Po(?:rt-channel)?\d+)\b")
VLAN = re.compile(r"\bVLAN0*(\d+)\b|\bvlan\s+(\d+)\b", re.I)


def short_port(name):
    """GigabitEthernet1/0/14 -> Gi1/0/14, the way switches print ports in short."""
    name = (name or "").strip()
    for long, short in SHORT:
        if name.lower().startswith(long.lower()):
            return short + name[len(long):]
    return name


def cause_name(raw):
    """A cause from a log line or the MIB, as the MIB names it (bpduGuard, linkFlap…)."""
    if isinstance(raw, int):
        return CAUSES.get(raw, f"cause {raw}")
    r = (raw or "").strip().lower()
    return SYSLOG_CAUSES.get(r) or _BY_LOWER.get(r.replace("-", "").replace("_", "")) or (raw or "").strip() or "unknown"


def cause_text(cause):
    return CAUSE_TEXT.get(cause) or cause


def _vlan(msg):
    m = VLAN.search(msg)
    return int(m.group(1) or m.group(2)) if m else None


def match(code, msg):
    """One switch log line (its %FAC-SEV-MNEMONIC and the text after it) -> an alarm dict, or None."""
    if not code:
        return None
    fac, sev, mnem = (code.rsplit("-", 2) + ["", ""])[:3]   # the facility can hold a dash: MAC_MOVE-SP-4-NOTIF
    msg = msg or ""
    ports = [short_port(p) for p in IFACE.findall(msg)]
    port = ports[0] if ports else None

    if fac == "PM" and mnem.startswith("ERR_DISABLE"):
        m = re.search(r"^\s*(.+?) error detected on (\S+?)[,.]?(?:\s|$)", msg)
        cause = cause_name(m.group(1)) if m else "unknown"
        port = short_port(m.group(2)) if m else port
        return {"kind": "errdisable", "state": "set", "port": port, "cause": cause, "text": cause_text(cause),
                "vlan": _vlan(msg) if mnem.endswith("_VP") else None, "sev": "crit"}
    if fac == "PM" and mnem.startswith("ERR_RECOVER"):
        m = re.search(r"recover from (.+?) err-disable state on (\S+?)[,.]?(?:\s|$)", msg)
        cause = cause_name(m.group(1)) if m else None
        return {"kind": "errdisable", "state": "clear", "port": short_port(m.group(2)) if m else port,
                "cause": cause, "text": "the switch is bringing the port back (errdisable recovery)", "sev": "info"}
    if fac in ("LINK", "LINEPROTO") and mnem == "UPDOWN" and re.search(r"changed state to up\b", msg):
        # A port that comes back up is no longer err-disabled (someone ran shut / no shut).
        return {"kind": "errdisable", "state": "clear", "port": port, "cause": None, "text": "the port came back up",
                "sev": "info"} if port else None
    if fac == "SPANTREE":
        if mnem in STP_CLEAR:
            return {"kind": "stpguard", "state": "clear", "port": port, "vlan": _vlan(msg), "cause": mnem,
                    "text": "spanning tree unblocked the port", "sev": "info"} if port else None
        if mnem == "BLOCK_BPDUGUARD":   # the ERR_DISABLE line that follows says the same: one alarm
            return {"kind": "errdisable", "state": "set", "port": port, "cause": "bpduGuard",
                    "text": cause_text("bpduGuard"), "vlan": None, "sev": "crit"} if port else None
        if sev in ("0", "1", "2") and not mnem.endswith("CONFIG_CHANGE"):
            return {"kind": "stpguard", "state": "set", "port": port, "vlan": _vlan(msg), "cause": mnem,
                    "text": STP_TEXT.get(mnem) or f"spanning tree: {mnem.replace('_', ' ').lower()}", "sev": "crit"}
        return None
    if mnem in ("MACFLAP_NOTIF", "NOTIF") and fac in ("SW_MATRIX", "MAC_MOVE", "MAC_MOVE-SP"):
        m = re.search(r"Host (\S+) in vlan (\d+) is flapping between port (\S+) and port (\S+)", msg, re.I)
        if not m:
            return None
        a, b = short_port(m.group(3).rstrip(",.")), short_port(m.group(4).rstrip(",."))
        return {"kind": "macflap", "state": "set", "port": a, "ports": sorted({a, b}), "mac": m.group(1).lower(),
                "vlan": int(m.group(2)), "cause": "macflap",
                "text": f"MAC address flapping between {a} and {b}: usually a loop, or a host on two ports",
                "sev": "crit"}
    return None


def match_line(text):
    """The same for a raw log line: finds the %FAC-SEV-MNEMONIC: part itself."""
    m = CODE.search(text or "")
    return match(f"{m.group(1)}-{m.group(2)}-{m.group(3)}", m.group(4).strip()) if m else None


def from_snmp(rows, port_names):
    """Walked ERRDISABLE_CAUSE_OID rows [(oid, value)] -> [{"port", "cause", "text", "vlan"}].

    port_names maps ifIndex to the port's name (ifName or ifDescr)."""
    n = len(ERRDISABLE_CAUSE_OID)
    out = []
    for oid, value in rows:
        idx = tuple(oid[n:])
        if len(idx) < 1 or not isinstance(value, int):
            continue
        cause = cause_name(value)
        out.append({"port": short_port(port_names.get(idx[0]) or f"ifIndex {idx[0]}"), "cause": cause,
                    "text": cause_text(cause), "vlan": (idx[1] or None) if len(idx) > 1 else None})
    return out
