"""Tests for swalarms.py: python3 tools/test_swalarms.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import swalarms as A  # noqa: E402

CASES = [
    ("%PM-4-ERR_DISABLE: bpduguard error detected on Gi0/14, putting Gi0/14 in err-disable state",
     dict(kind="errdisable", state="set", port="Gi0/14", cause="bpduGuard")),
    ("%PM-4-ERR_DISABLE: psecure-violation error detected on Gi1/0/5, putting Gi1/0/5 in err-disable state",
     dict(kind="errdisable", state="set", port="Gi1/0/5", cause="portSecurityViolation")),
    ("%PM-4-ERR_DISABLE: link-flap error detected on GigabitEthernet1/0/22, putting GigabitEthernet1/0/22 in err-disable state",
     dict(kind="errdisable", state="set", port="Gi1/0/22", cause="linkFlap")),
    ("%PM-4-ERR_DISABLE: loopback error detected on Fa0/3, putting Fa0/3 in err-disable state",
     dict(kind="errdisable", cause="portLoopback", port="Fa0/3")),
    ("%PM-4-ERR_DISABLE: inline-power error detected on Gi0/7, putting Gi0/7 in err-disable state",
     dict(kind="errdisable", cause="inlinePower")),
    ("%PM-4-ERR_DISABLE_VP: bpduguard error detected on Gi0/1, vlan 10. Putting in err-disable state.",
     dict(kind="errdisable", port="Gi0/1", vlan=10, cause="bpduGuard")),
    ("%PM-4-ERR_RECOVER: Attempting to recover from bpduguard err-disable state on Gi0/14",
     dict(kind="errdisable", state="clear", port="Gi0/14", cause="bpduGuard")),
    ("%LINK-3-UPDOWN: Interface GigabitEthernet0/14, changed state to up",
     dict(kind="errdisable", state="clear", port="Gi0/14")),
    ("%LINK-3-UPDOWN: Interface GigabitEthernet0/14, changed state to down", None),
    ("%SPANTREE-2-BLOCK_BPDUGUARD: Received BPDU on port Gi0/14 with BPDU Guard enabled. Disabling port.",
     dict(kind="errdisable", state="set", port="Gi0/14", cause="bpduGuard")),
    ("%SPANTREE-2-ROOTGUARD_BLOCK: Root guard blocking port GigabitEthernet1/0/1 on VLAN0010.",
     dict(kind="stpguard", state="set", port="Gi1/0/1", vlan=10, cause="ROOTGUARD_BLOCK")),
    ("%SPANTREE-2-ROOTGUARD_UNBLOCK: Root guard unblocking port GigabitEthernet1/0/1 on VLAN0010.",
     dict(kind="stpguard", state="clear", port="Gi1/0/1", vlan=10)),
    ("%SPANTREE-2-LOOPGUARD_BLOCK: Loop guard blocking port TenGigabitEthernet1/1/1 on VLAN0001.",
     dict(kind="stpguard", port="Te1/1/1", vlan=1)),
    ("%SPANTREE-2-BLOCK_PVID_LOCAL: Blocking GigabitEthernet0/1 on VLAN0020. Inconsistent local vlan.",
     dict(kind="stpguard", port="Gi0/1", vlan=20, cause="BLOCK_PVID_LOCAL")),
    ("%SPANTREE-2-UNBLOCK_CONSIST_PORT: Unblocking GigabitEthernet0/1 on VLAN0020. Port consistency restored.",
     dict(kind="stpguard", state="clear", port="Gi0/1")),
    ("%SPANTREE-2-LOOPGUARD_CONFIG_CHANGE: Loop guard enabled on port GigabitEthernet0/1.", None),
    ("%SW_MATRIX-4-MACFLAP_NOTIF: Host 0011.2233.4455 in vlan 10 is flapping between port Gi0/2 and port Gi0/1",
     dict(kind="macflap", state="set", port="Gi0/2", ports=["Gi0/1", "Gi0/2"], mac="0011.2233.4455", vlan=10)),
    ("%MAC_MOVE-SP-4-NOTIF: Host 0011.2233.4455 in vlan 10 is flapping between port Gi1/1 and port Gi1/2",
     dict(kind="macflap", ports=["Gi1/1", "Gi1/2"])),
    ("access-1: 000123: Oct 27 21:14:05.123: %PM-4-ERR_DISABLE: udld error detected on Gi0/9, putting Gi0/9 in err-disable state",
     dict(kind="errdisable", port="Gi0/9", cause="udld")),
    ("%SYS-5-CONFIG_I: Configured from console by admin on vty0", None),
    ("%LINEPROTO-5-UPDOWN: Line protocol on Interface Vlan1, changed state to up", None),
]


def main():
    bad = 0
    for line, want in CASES:
        got = A.match_line(line)
        ok = got is None if want is None else got is not None and all(got.get(k) == v for k, v in want.items())
        if not ok:
            bad += 1
            print("FAIL", line, "\n   want", want, "\n   got ", got)
    assert A.short_port("TwentyFiveGigE1/0/1") == "Twe1/0/1" and A.short_port("Port-channel3") == "Po3"
    rows = [(A.ERRDISABLE_CAUSE_OID + (10114, 0), 2), (A.ERRDISABLE_CAUSE_OID + (10105, 20), 9), (A.ERRDISABLE_CAUSE_OID + (7, 0), 99)]
    snmp = A.from_snmp(rows, {10114: "GigabitEthernet0/14", 10105: "Gi0/5"})
    assert snmp == [{"port": "Gi0/14", "cause": "bpduGuard", "text": A.CAUSE_TEXT["bpduGuard"], "vlan": None},
                    {"port": "Gi0/5", "cause": "portSecurityViolation", "text": A.CAUSE_TEXT["portSecurityViolation"], "vlan": 20},
                    {"port": "ifIndex 7", "cause": "cause 99", "text": "cause 99", "vlan": None}], snmp
    print(f"{len(CASES) - bad}/{len(CASES)} lines OK, SNMP OK" if not bad else f"{bad} failed")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
