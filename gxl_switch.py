"""Switch readings in the shape GXL-LANapp's Switches page uses, ported from GXL-LANapp's
server/src/gxl_server/switches.py (minus its database, seats and alert acknowledgements).

pc_snmp (from PorkChopCheck) does the SNMP and grades each port; this adds the forwarding table,
CDP/LLDP neighbors, VLANs, the environment (CPU, memory, temperatures, fans, power, PoE), broadcast
and multicast rates, and the traffic history the gauges and the bandwidth monitor draw from.
"""
import collections
import re
import socket
import time

import pc_snmp

FDB_EVERY = 3            # read the forwarding table on every third reading of a switch; it is the slow part
EDGE_MAX_MACS = 4        # a port with more addresses than this is an uplink, not one PC
MACS_KEPT = 8            # addresses kept per port; the count is kept in full
HISTORY_POINTS = 90      # readings of traffic kept per switch: half an hour at one every 20 seconds
MAX_VLANS = 48

O = pc_snmp.oid
BASE_PORT_IFINDEX = O("1.3.6.1.2.1.17.1.4.1.2")
BRIDGE_FDB_PORT = O("1.3.6.1.2.1.17.4.3.1.2")
QBRIDGE_FDB_PORT = O("1.3.6.1.2.1.17.7.1.2.2.1.2")
CISCO_VLAN_STATE = O("1.3.6.1.4.1.9.9.46.1.3.1.1.2")
CDP_CACHE = O("1.3.6.1.4.1.9.9.23.1.2.1.1")
CDP_ADDRESS, CDP_DEVICE, CDP_THEIR_PORT, CDP_PLATFORM, CDP_CAPABILITIES = 4, 6, 7, 8, 9
IF_NAME = O("1.3.6.1.2.1.31.1.1.1.1")
IF_TYPE = O("1.3.6.1.2.1.2.2.1.3")
IF_STACK = O("1.3.6.1.2.1.31.1.2.1.3")
CDP_IS_SWITCH, CDP_IS_HOST = 0x08, 0x10
LLDP_REMOTE = O("1.0.8802.1.1.2.1.4.1.1")
LLDP_THEIR_PORT, LLDP_THEIR_PORT_DESC, LLDP_NAME, LLDP_DESC, LLDP_CAPS = 7, 8, 9, 10, 12
LLDP_LOCAL_PORT = O("1.0.8802.1.1.2.1.3.7.1.4")
LLDP_ADDRESS = O("1.0.8802.1.1.2.1.4.2.1.3")
LLDP_BRIDGE, LLDP_AP, LLDP_STATION = 0x20, 0x10, 0x01
IN_MULTICAST, IN_BROADCAST = O("1.3.6.1.2.1.31.1.1.1.8"), O("1.3.6.1.2.1.31.1.1.1.9")
PORT_VLAN = O("1.3.6.1.4.1.9.9.68.1.2.2.1.2")
TRUNK_STATE = O("1.3.6.1.4.1.9.9.46.1.6.1.1.14")
VTP_DOMAIN_NAME, VTP_LOCAL_MODE = O("1.3.6.1.4.1.9.9.46.1.2.1.1.2"), O("1.3.6.1.4.1.9.9.46.1.2.1.1.3")
VTP_MODES = {1: "client", 2: "server", 3: "transparent", 4: "off"}
PROCESS, MEMORY, ENVMON = "1.3.6.1.4.1.9.9.109.1.1.1.1", "1.3.6.1.4.1.9.9.48.1.1.1", "1.3.6.1.4.1.9.9.13.1"
CPU_5S, CPU_1M, CPU_5M = (O(f"{PROCESS}.{n}") for n in (6, 7, 8))
MEM_NAME, MEM_USED, MEM_FREE = (O(f"{MEMORY}.{n}") for n in (2, 5, 6))
TEMP_NAME, TEMP_VALUE, TEMP_LIMIT, TEMP_STATE = (O(f"{ENVMON}.3.1.{n}") for n in (2, 3, 4, 6))
FAN_NAME, FAN_STATE = (O(f"{ENVMON}.4.1.{n}") for n in (2, 3))
PSU_NAME, PSU_STATE = (O(f"{ENVMON}.5.1.{n}") for n in (2, 3))
POE_BUDGET, POE_USED = (O(f"1.3.6.1.2.1.105.1.3.1.1.{n}") for n in (2, 4))
ENV_LEVEL = {1: "ok", 2: "warn", 3: "crit", 4: "crit", 6: "crit"}
CPU_WARN, CPU_CRIT = 60, 85
MEM_WARN, MEM_CRIT = 75, 90
TEMP_MARGIN = 8
POE_WARN, POE_CRIT = 80, 95
LEVELS = {pc_snmp.BAD: "crit", pc_snmp.WARN: "warn", pc_snmp.GOOD: "ok", pc_snmp.DOWN: "down", pc_snmp.OFF: "off"}


def norm_port(name):
    """'GigabitEthernet1/0/5', 'Gi1/0/5' and 'gi1/0/5' are the same port."""
    return short_port(str(name or "")).lower()


def norm_switch(name):
    return str(name or "").split(".")[0].strip().lower()


def short_port(name):
    for long, short in (("TwentyFiveGigE", "Twe"), ("FortyGigabitEthernet", "Fo"), ("TenGigabitEthernet", "Te"),
                        ("GigabitEthernet", "Gi"), ("FastEthernet", "Fa"), ("Port-channel", "Po"), ("Ethernet", "Et")):
        if name.startswith(long):
            return short + name[len(long):]
    return name


def fdb_from(client):
    to_ifindex = {idx[0]: v for idx, v in client.column(BASE_PORT_IFINDEX).items() if idx and isinstance(v, int)}
    out = {}
    rows = client.column(QBRIDGE_FDB_PORT) or client.column(BRIDGE_FDB_PORT)
    for index, bridge_port in rows.items():
        mac = index[-6:]
        ifindex = to_ifindex.get(bridge_port)
        if len(mac) == 6 and ifindex is not None:
            out.setdefault(ifindex, []).append(":".join(f"{b:02x}" for b in mac))
    return out


def read_vlans(client):
    try:
        state = client.column(CISCO_VLAN_STATE)
    except pc_snmp.SnmpError:
        return []
    return sorted(idx[-1] for idx, v in state.items() if idx and v == 1 and not 1002 <= idx[-1] <= 1005)


def read_fdb(host, community, version, vlans, client_class=pc_snmp.Client):
    """Which MACs the switch has seen on which port. Cisco keeps one table per VLAN, shown to community@vlan."""
    merged = {}

    def add(table):
        for ifindex, macs in table.items():
            merged.setdefault(ifindex, set()).update(macs)

    with client_class(host, community, version) as client:
        add(fdb_from(client))
    for vlan in vlans[:MAX_VLANS] if version == 2 else []:
        try:
            with client_class(host, f"{community}@{vlan}", version, timeout=1.0, retries=0) as client:
                add(fdb_from(client))
        except pc_snmp.SnmpError:
            continue
    return {ifindex: sorted(macs) for ifindex, macs in merged.items()}


def read_casts(client):
    bcast, mcast = client.column(IN_BROADCAST), client.column(IN_MULTICAST)
    return {i[0]: (bcast.get(i), mcast.get(i)) for i in set(bcast) | set(mcast) if i}


def read_neighbors(client):
    """CDP first, then LLDP for the ports CDP says nothing about."""
    cols = {n: client.column(CDP_CACHE + (n,)) for n in (CDP_ADDRESS, CDP_DEVICE, CDP_THEIR_PORT, CDP_PLATFORM, CDP_CAPABILITIES)}
    names = client.column(IF_NAME) if cols[CDP_DEVICE] else {}
    try:
        lldp = read_lldp(client)
    except pc_snmp.SnmpError:
        lldp = []
    out = []
    for index in sorted(cols[CDP_DEVICE]):
        address, caps = cols[CDP_ADDRESS].get(index), cols[CDP_CAPABILITIES].get(index)
        ip = socket.inet_ntoa(address) if isinstance(address, bytes) and len(address) == 4 else ""
        caps = int.from_bytes(caps, "big") if isinstance(caps, bytes) else (caps or 0)
        out.append({"name": pc_snmp.text(cols[CDP_DEVICE][index])[:120], "ip": ip if pc_snmp.private_ip(ip) else "",
                    "platform": pc_snmp.text(cols[CDP_PLATFORM].get(index))[:80],
                    "local_port": short_port(pc_snmp.text(names.get(index[:1])))[:40],
                    "their_port": pc_snmp.text(cols[CDP_THEIR_PORT].get(index))[:40],
                    "is_switch": bool(caps & CDP_IS_SWITCH) and not caps & CDP_IS_HOST, "protocol": "cdp"})
    seen = {n["local_port"].lower() for n in out}
    return out + [n for n in lldp if n["local_port"].lower() not in seen]


def read_lldp(client):
    cols = {n: client.column(LLDP_REMOTE + (n,)) for n in (LLDP_NAME, LLDP_THEIR_PORT, LLDP_THEIR_PORT_DESC, LLDP_DESC, LLDP_CAPS)}
    if not any(cols.values()):
        return []
    local = client.column(LLDP_LOCAL_PORT)
    addresses = {}
    for index in client.column(LLDP_ADDRESS):
        if len(index) == 9 and index[3] == 1 and index[4] == 4:
            addresses.setdefault(index[:3], socket.inet_ntoa(bytes(index[5:9])))
    out = []
    for index in sorted(set(cols[LLDP_NAME]) | set(cols[LLDP_THEIR_PORT])):
        caps = cols[LLDP_CAPS].get(index)
        caps = caps[0] if isinstance(caps, bytes) and caps else 0
        ip = addresses.get(index[:3], "")
        their = pc_snmp.text(cols[LLDP_THEIR_PORT_DESC].get(index)) or pc_snmp.text(cols[LLDP_THEIR_PORT].get(index))
        desc = cols[LLDP_DESC].get(index)
        out.append({"name": pc_snmp.text(cols[LLDP_NAME].get(index))[:120], "ip": ip if pc_snmp.private_ip(ip) else "",
                    "platform": pc_snmp.text(desc).splitlines()[0][:80] if desc else "",
                    "local_port": short_port(pc_snmp.text(local.get(index[1:2])))[:40], "their_port": their[:40],
                    "is_switch": bool(caps & LLDP_BRIDGE) and not caps & (LLDP_AP | LLDP_STATION), "protocol": "lldp"})
    return [n for n in out if n["local_port"]]


def read_domains(client):
    names, modes = client.column(VTP_DOMAIN_NAME), client.column(VTP_LOCAL_MODE)
    vlans, trunks = client.column(PORT_VLAN), client.column(TRUNK_STATE)
    out = {"port_vlans": {i[0]: v for i, v in vlans.items() if isinstance(v, int) and i},
           "trunks": sorted(i[0] for i, v in trunks.items() if v == 1 and i)}
    for index in sorted(names):
        name = pc_snmp.text(names[index])
        if name or len(names) == 1:
            return {**out, "vtp_domain": name[:64], "vtp_mode": VTP_MODES.get(modes.get(index), "")}
    return out


def read_portchannels(client):
    """Port-channel ifIndex -> member ifIndexes (for the trace: CDP is seen on the members)."""
    types = client.column(IF_TYPE)
    pos = {i[0] for i, v in types.items() if v == 161 and i}
    out = {}
    if pos:
        for o, _ in client.walk(IF_STACK, limit=4000):
            hi, lo = o[-2], o[-1]
            if hi in pos and lo:
                out.setdefault(hi, []).append(lo)
    return out


def _clean(name):
    for suffix in (", GREEN", ", YELLOW", ", RED", ", Normal", " Normal"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()[:60]


def read_environment(client):
    columns = {"cpu5s": CPU_5S, "cpu1m": CPU_1M, "cpu5m": CPU_5M, "mem_name": MEM_NAME, "mem_used": MEM_USED, "mem_free": MEM_FREE,
               "temp_name": TEMP_NAME, "temp_value": TEMP_VALUE, "temp_limit": TEMP_LIMIT, "temp_state": TEMP_STATE,
               "fan_name": FAN_NAME, "fan_state": FAN_STATE, "psu_name": PSU_NAME, "psu_state": PSU_STATE,
               "poe_budget": POE_BUDGET, "poe_used": POE_USED}
    col = {name: client.column(oid) for name, oid in columns.items()}
    first = lambda name: next((v for v in col[name].values() if isinstance(v, int)), None)
    name_of = lambda table, index: _clean(pc_snmp.text(col[table].get(index)))
    env = {"cpu_5s": first("cpu5s"), "cpu_1m": first("cpu1m"), "cpu_5m": first("cpu5m")}
    pool = next((i for i, v in col["mem_name"].items() if pc_snmp.text(v).lower() == "processor"), None)
    used, free = (col[name].get(pool) for name in ("mem_used", "mem_free"))
    if isinstance(used, int) and isinstance(free, int) and used + free:
        env["memory_pct"] = round(used * 100 / (used + free))
    env["temps"] = []
    for i, value in sorted(col["temp_value"].items()):
        if isinstance(value, int) and col["temp_state"].get(i) != 5:
            limit = col["temp_limit"].get(i)
            env["temps"].append({"name": name_of("temp_name", i), "celsius": value,
                                 "limit": limit if isinstance(limit, int) else None, "state": col["temp_state"].get(i)})
    env["fans"] = [{"name": name_of("fan_name", i), "state": s} for i, s in sorted(col["fan_state"].items()) if s != 5]
    env["power"] = [{"name": name_of("psu_name", i), "state": s} for i, s in sorted(col["psu_state"].items()) if s != 5]
    budget = sum(v for v in col["poe_budget"].values() if isinstance(v, int))
    if budget:
        drawn = sum(v for v in col["poe_used"].values() if isinstance(v, int))
        env["poe"] = {"budget_w": budget, "used_w": drawn}
    return grade_environment(env)


def _level(value, warn, crit):
    return "ok" if value is None or value < warn else "warn" if value < crit else "crit"


def grade_environment(env):
    reasons = []
    cpu = env.get("cpu_1m") if env.get("cpu_1m") is not None else env.get("cpu_5s")
    env["cpu_level"] = _level(cpu, CPU_WARN, CPU_CRIT)
    if env["cpu_level"] != "ok":
        reasons.append((env["cpu_level"], f"CPU is {cpu}% busy"))
    env["memory_level"] = _level(env.get("memory_pct"), MEM_WARN, MEM_CRIT)
    if env["memory_level"] != "ok":
        reasons.append((env["memory_level"], f"{env['memory_pct']}% of memory in use"))
    for t in env.get("temps", []):
        level = ENV_LEVEL.get(t.get("state"), "ok")
        limit = t.get("limit")
        if limit and level == "ok":
            level = _level(t["celsius"], limit - TEMP_MARGIN, limit)
        t["level"] = level
        if level != "ok":
            reasons.append((level, f"{t['name']} is {t['celsius']}°C" + (f" (limit {limit}°C)" if limit else "")))
    for kind, word in (("fans", "fan"), ("power", "power supply")):
        for item in env.get(kind, []):
            item["level"] = ENV_LEVEL.get(item.get("state"), "crit")
            if item["level"] != "ok":
                reasons.append((item["level"], f"{item['name'] or word} is not normal"))
    if env.get("poe"):
        pct = round(env["poe"]["used_w"] * 100 / env["poe"]["budget_w"])
        env["poe"]["pct"], env["poe"]["level"] = pct, _level(pct, POE_WARN, POE_CRIT)
        if env["poe"]["level"] != "ok":
            reasons.append((env["poe"]["level"], f"PoE devices draw {pct}% of the power budget"))
    levels = {r[0] for r in reasons}
    env["level"] = "crit" if "crit" in levels else "warn" if "warn" in levels else "ok"
    env["reasons"] = [{"level": lv, "text": text} for lv, text in reasons]
    return env


def trim(port, macs=None):
    macs = macs or []
    c = port.get("c") or {}
    rate = lambda v: None if v is None else round(v, 2)
    return {"name": port["name"], "index": port["index"], "alias": port.get("alias", ""), "up": bool(port["oper"]),
            "speed_mbps": port.get("speed") or None, "duplex": port.get("duplex") or None,
            "level": LEVELS.get(port.get("level"), "down"), "reasons": port.get("reasons", []),
            "in_mbps": rate(port.get("in_mbps")), "out_mbps": rate(port.get("out_mbps")), "errors": port.get("errors", 0),
            "macs": macs[:MACS_KEPT], "mac_count": len(macs), "busy_pct": port.get("busy"),
            "totals": {"crc": (c.get("fcs") or 0) + (c.get("align") or 0), "in_errors": c.get("in_err") or 0,
                       "out_errors": c.get("out_err") or 0, "discards": (c.get("in_disc") or 0) + (c.get("out_disc") or 0),
                       "collisions": (c.get("late") or 0) + (c.get("excess") or 0)}}


def is_uplink(port, to_switches):
    return port.get("mac_count", 0) > EDGE_MAX_MACS or norm_port(port["name"]) in to_switches


class Reader:
    """Remembers each switch between readings (counters, link changes, the forwarding table, history)."""

    def __init__(self):
        self.watches, self.readings, self.fdb, self.slow = {}, {}, {}, {}
        self.casts, self.totals, self.history = {}, {}, {}

    def forget(self, did):
        for d in (self.watches, self.readings, self.fdb, self.slow, self.casts, self.totals, self.history):
            d.pop(did, None)

    def read(self, did, client, info, host, community, version, roles):
        """One reading: the ports, graded, with neighbors, VLANs, rates and the environment."""
        ports = pc_snmp.read_ports(client)
        watch = self.watches.setdefault(did, pc_snmp.SwitchWatch())
        watch.update(ports, info.get("uptime"), time.monotonic())
        count = self.readings.get(did, 0)
        self.readings[did] = count + 1
        slow = self.slow.setdefault(did, {})
        if count % FDB_EVERY == 0:
            try:
                slow["vlan_list"] = read_vlans(client) or [1]
            except pc_snmp.SnmpError:
                pass
            try:
                self.fdb[did] = read_fdb(host, community, version, slow.get("vlan_list", []))
            except pc_snmp.SnmpError:
                pass
            for key, fn in (("neighbors", read_neighbors), ("domains", read_domains), ("po", read_portchannels)):
                try:
                    slow[key] = fn(client)
                except pc_snmp.SnmpError:
                    pass
        fdb = self.fdb.get(did, {})
        domains = slow.get("domains", {})
        vlans, trunks = domains.get("port_vlans", {}), set(domains.get("trunks", []))
        uptime = info.get("uptime") if isinstance(info.get("uptime"), int) else None
        try:
            casts = read_casts(client)
        except pc_snmp.SnmpError:
            casts = {}
        at = time.monotonic()
        then, before = self.casts.get(did, (None, {}))
        seconds = at - then if then else 0
        if casts:
            self.casts[did] = (at, casts)
        out = []
        for p in ports:
            kept = trim(p, fdb.get(p["index"]))
            seen = watch.mem.get(p["index"]) or {}
            kept["flaps"] = len(seen.get("changes") or ())
            last = p.get("last_change")
            kept["link_s"] = int((uptime - last) / 100) if p["oper"] and uptime and last and uptime >= last else None
            kept["vlan"] = vlans.get(p["index"])
            kept["trunk"] = p["index"] in trunks
            now_c, was = casts.get(p["index"]), before.get(p["index"])
            if seconds > 0 and now_c and was and None not in now_c and None not in was:
                b, m = (pc_snmp._delta(n, o, True) for n, o in zip(now_c, was))
                kept["bcast_pps"], kept["mcast_pps"] = round(b / seconds, 1), round(m / seconds, 1)
            out.append(kept)
        neighbors = slow.get("neighbors", [])
        to_switches = {norm_port(n.get("local_port")) for n in neighbors if n.get("is_switch")}
        self.record_traffic(did, out, to_switches, roles)
        rates = self.record_rates(did, out, to_switches, roles)
        try:
            env = read_environment(client)
        except pc_snmp.SnmpError:
            env = {}
        return {"ports": out, "neighbors": neighbors, "environment": env, "rates": rates,
                "vtp_domain": domains.get("vtp_domain", ""), "vtp_mode": domains.get("vtp_mode", ""),
                "vlan_list": slow.get("vlan_list", [1]), "po": slow.get("po", {}),
                "uptime_s": int(uptime / 100) if uptime else None,
                "history": [list(x) for x in self.history.get(did, [])]}

    def record_rates(self, did, ports, to_switches, roles):
        edge = [p for p in ports if not is_uplink(p, to_switches) and p["name"] not in roles]
        rates = {}
        if any(p.get("bcast_pps") is not None for p in edge):
            rates["bcast_pps"] = round(sum(p.get("bcast_pps") or 0 for p in edge), 1)
            rates["mcast_pps"] = round(sum(p.get("mcast_pps") or 0 for p in edge), 1)
        errors = sum(sum((p["totals"].get(k) or 0) for k in ("in_errors", "out_errors", "crc")) for p in ports)
        discards = sum(p["totals"].get("discards") or 0 for p in ports)
        at = time.monotonic()
        then = self.totals.get(did)
        if then and at > then[0]:
            minutes = (at - then[0]) / 60
            rates["errors_pm"] = round(max(0, errors - then[1]) / minutes, 1)
            rates["discards_pm"] = round(max(0, discards - then[2]) / minutes, 1)
        self.totals[did] = (at, errors, discards)
        return rates

    def record_traffic(self, did, ports, to_switches, roles):
        uplink_down = uplink_up = pcs_down = pcs_up = 0.0
        measured = False
        for p in ports:
            if not p["up"] or p["in_mbps"] is None:
                continue
            measured = True
            if p["name"] in roles:
                continue
            if is_uplink(p, to_switches):
                uplink_down, uplink_up = uplink_down + (p["out_mbps"] or 0), uplink_up + p["in_mbps"]
            else:
                pcs_down, pcs_up = pcs_down + (p["out_mbps"] or 0), pcs_up + p["in_mbps"]
        if measured:
            point = (int(time.time()), *(round(v, 2) for v in (uplink_down, uplink_up, pcs_down, pcs_up)))
            self.history.setdefault(did, collections.deque(maxlen=HISTORY_POINTS)).append(point)


def is_switch_named(n, dev):
    return bool(n.get("ip") and n.get("ip") == dev["host"]) or (bool(n.get("name")) and norm_switch(n.get("name")) == norm_switch(dev.get("name")))


def down_report(dev, devs, states):
    """Why a switch that stopped answering has gone: what the switches it plugs into see on that port."""
    found = {}
    mine = states.get(dev["id"], {})
    others = [o for o in devs if o["id"] != dev["id"]]
    for n in mine.get("neighbors") or []:
        for other in others:
            if n.get("is_switch") and n.get("their_port") and is_switch_named(n, other):
                found[(other["id"], norm_port(n["their_port"]))] = (other, n["their_port"])
    for other in others:
        for n in states.get(other["id"], {}).get("neighbors") or []:
            if n.get("local_port") and is_switch_named(n, dev):
                found[(other["id"], norm_port(n["local_port"]))] = (other, n["local_port"])
    links = []
    for other, port_name in found.values():
        st = states.get(other["id"], {})
        if st.get("status") != "ok":
            continue
        port = next((p for p in st.get("ports") or [] if norm_port(p["name"]) == norm_port(port_name)), None)
        if port is None:
            continue
        there = next((n for n in st.get("neighbors") or [] if norm_port(n.get("local_port")) == norm_port(port_name)), None)
        if not port.get("up"):
            state, now_there = "down", None
        elif there and not is_switch_named(there, dev):
            state, now_there = "moved", there.get("name")
        else:
            state, now_there = "up", None
        links.append({"switch": other.get("name"), "port": port["name"], "state": state, "now": now_there})
    if not links:
        return "Nothing shows where it is plugged in, so it may be off, unplugged or unreachable."
    first = next((x for x in links if x["state"] == "down"), None) or links[0]
    at = f"{first['switch']} {first['port']}"
    if first["state"] == "down":
        return f"The link to it at {at} is down: it is turned off, or its cable is out."
    if first["state"] == "moved":
        return f"{at}, where it was plugged in, now has {first['now']} on it: the cable has been moved."
    return (f"The link to it at {at} is still up, so it has power but is not answering SNMP: "
            "it may be restarting, or its SNMP settings changed.")
