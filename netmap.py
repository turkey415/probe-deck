"""Switches, wireless controllers and the host trace, over SNMP v1/v2c (read-only).

- Devices: switches and AireOS controllers added by IP (checked with one read before they're kept), or
  picked from what CDP and a subnet scan found. One community for all of them, with an optional
  per-device override. The community never leaves the probe.
- Poller: every minute, each device's ports (link, speed, VLAN, CDP neighbor), CPU, PoE and uptime;
  for a controller, its access points and client count.
- Trace: IP, MAC or host name -> MAC -> the switch port it was learned on (per-VLAN MAC tables), then
  the path to it over the CDP links between managed switches. A wireless client is looked up on the
  controller (AP, band, SSID, signal) and the AP is traced to its switch port by CDP.
"""
import collections
import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import gxl_switch as G
import pc_snmp as S

DEFAULTS = {"snmp_community": "", "snmp_version": 2}
POLL_EVERY = 20       # as GXL-LANapp: traffic rates and the gauges need frequent readings
MAX_DEVICES = 64

O = S.oid
IFNAME, IFALIAS, IFHIGH = O("1.3.6.1.2.1.31.1.1.1.1"), O("1.3.6.1.2.1.31.1.1.1.18"), O("1.3.6.1.2.1.31.1.1.1.15")
IFTYPE, IFADMIN, IFOPER, IFSPEED = O("1.3.6.1.2.1.2.2.1.3"), O("1.3.6.1.2.1.2.2.1.7"), O("1.3.6.1.2.1.2.2.1.8"), O("1.3.6.1.2.1.2.2.1.5")
IFINERR, IFOUTERR = O("1.3.6.1.2.1.2.2.1.14"), O("1.3.6.1.2.1.2.2.1.20")
DUPLEX = O("1.3.6.1.2.1.10.7.2.1.19")
IFSTACK = O("1.3.6.1.2.1.31.1.2.1.3")
CDP = O("1.3.6.1.4.1.9.9.23.1.2.1.1")                 # cdpCacheEntry: .4 address .6 device id .7 port .8 platform .9 capabilities
VTP_STATE = O("1.3.6.1.4.1.9.9.46.1.3.1.1.2")         # VLANs the switch knows (1 = operational)
TRUNK = O("1.3.6.1.4.1.9.9.46.1.6.1.1.14")            # 1 = trunking
VMVLAN = O("1.3.6.1.4.1.9.9.68.1.2.2.1.2")            # access VLAN per ifIndex
FDB_PORT = O("1.3.6.1.2.1.17.4.3.1.2")                # BRIDGE-MIB: MAC -> bridge port (per VLAN: community@vlan)
BP_IF = O("1.3.6.1.2.1.17.1.4.1.2")                   # bridge port -> ifIndex
ARP = O("1.3.6.1.2.1.4.22.1.2")                       # ipNetToMediaPhysAddress
CPU5M = O("1.3.6.1.4.1.9.9.109.1.1.1.1.8")
POE_POWER, POE_USED = O("1.3.6.1.2.1.105.1.3.1.1.2"), O("1.3.6.1.2.1.105.1.3.1.1.4")
# AireOS (AIRESPACE-WIRELESS-MIB)
AP_NAME = O("1.3.6.1.4.1.14179.2.2.1.1.3")            # bsnAPName, indexed by the AP's radio MAC
AP_MODEL = O("1.3.6.1.4.1.14179.2.2.1.1.16")          # bsnAPModel
MS = O("1.3.6.1.4.1.14179.2.1.4.1")                   # bsnMobileStationEntry, indexed by client MAC
MS_IP, MS_USER, MS_AP, MS_SLOT, MS_SSID = 2, 3, 4, 5, 7
MS_STATS = O("1.3.6.1.4.1.14179.2.1.6.1")             # bsnMobileStationStatsEntry: .1 RSSI (dBm), .26 SNR (dB)
CAP_ROUTER, CAP_SWITCH, CAP_HOST = 0x01, 0x08, 0x10

MAC_RE = re.compile(r"^([0-9a-f]{2})[:\-.]?([0-9a-f]{2})[:\-.]?([0-9a-f]{2})[:\-.]?([0-9a-f]{2})[:\-.]?([0-9a-f]{2})[:\-.]?([0-9a-f]{2})$")
fmt_mac = lambda b: ":".join(f"{x:02x}" for x in b)


def parse_mac(text):
    m = MAC_RE.match(str(text).strip().lower())
    return bytes(int(x, 16) for x in m.groups()) if m else None


def short_if(name):
    for a, b in (("TenGigabitEthernet", "Te"), ("GigabitEthernet", "Gi"), ("FastEthernet", "Fa"), ("TwentyFiveGigE", "Twe"),
                 ("FortyGigabitEthernet", "Fo"), ("Port-channel", "Po"), ("AppGigabitEthernet", "Ap")):
        if name.startswith(a):
            return b + name[len(a):]
    return name


def ip_of(value):
    if isinstance(value, bytes) and len(value) == 4:
        return socket.inet_ntoa(value)
    return S.text(value) if value else None


def kind_of(info):
    d = info.get("descr", "").lower()
    if "cisco controller" in d or "aireos" in d or re.search(r"air-ct\d", (info.get("model") or "").lower()):
        return "wlc"
    return "switch" if S.is_switch(info) else "other"


class NetMap:
    def __init__(self, probe, conf_dir, data_dir):
        self.p = probe
        self.file = os.path.join(conf_dir, "devices.json")      # holds per-device communities: root-only
        self.trace_file = os.path.join(data_dir, "traces.json")
        self.lock = threading.Lock()
        self.devices = []
        try:
            with open(self.file) as f:
                self.devices = [d for d in json.load(f) if isinstance(d, dict) and d.get("host")][:MAX_DEVICES]
        except (OSError, ValueError):
            pass
        self.state = {}             # device id -> polled state
        self.discovered = {}        # ip -> {ip, name, platform, via, kind?}
        self.scan = {"running": False, "at": None, "found": 0, "error": None}
        self.traces = collections.OrderedDict()
        try:
            with open(self.trace_file) as f:
                for t in json.load(f)[-20:]:
                    self.traces[t["id"]] = t
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.wake = threading.Event()
        self.reader = G.Reader()
        self.pool = ThreadPoolExecutor(8)
        threading.Thread(target=self.poll_loop, daemon=True).start()

    # ---------- settings and devices ----------
    @property
    def conf(self):
        return self.p.conf

    def community(self, dev=None):
        return (dev or {}).get("community") or self.conf.get("snmp_community") or ""

    def version(self, dev=None):
        return int((dev or {}).get("version") or self.conf.get("snmp_version") or 2)

    def client(self, dev, suffix="", timeout=1.5):
        return S.Client(dev["host"], self.community(dev) + suffix, self.version(dev), timeout=timeout, retries=1)

    def save(self):
        tmp = self.file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.devices, f, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.file)

    @staticmethod
    def check_community(c):
        c = str(c or "")
        if len(c) > 64 or "@" in c or any(ord(ch) < 33 or ord(ch) > 126 for ch in c):
            raise ValueError("Communities are up to 64 printable characters, no spaces or @")
        return c

    def add(self, host, name=None, community=None):
        host = str(host or "").strip()
        if not S.private_ip(host):
            raise ValueError("Give the device's IPv4 address on the local network, e.g. 192.168.1.2")
        with self.lock:
            if any(d["host"] == host for d in self.devices):
                raise ValueError("That device is already on the list")
            if len(self.devices) >= MAX_DEVICES:
                raise ValueError(f"At most {MAX_DEVICES} devices")
        comm = self.check_community(community) if community else ""
        dev = {"id": uuid.uuid4().hex[:10], "host": host, "community": comm}
        if not self.community(dev):
            raise ValueError("Set the SNMP community first")
        try:
            with self.client(dev, timeout=2) as c:
                info = S.identify(c)
        except S.SnmpError as e:
            raise ValueError(f"{host} didn't answer SNMP ({e}). Check the address and community, and that SNMP is on.") from None
        kind = kind_of(info)
        if kind == "other":
            raise ValueError(f"{host} answered, but it doesn't look like a switch or controller ({info.get('descr', '')[:70]})")
        dev.update(name=(str(name).strip()[:40] if name else "") or info.get("name") or host, kind=kind,
                   model=S.model_of(info), added=time.time())
        with self.lock:
            self.devices.append(dev)
            self.save()
        self.pool.submit(self.poll_one, dev)
        return dev["id"]

    def remove(self, did):
        with self.lock:
            self.devices = [d for d in self.devices if d["id"] != did]
            self.state.pop(did, None)
            self.save()
        self.reader.forget(did)

    def set_order(self, ids):
        with self.lock:
            pos = {i: n for n, i in enumerate(ids)}
            self.devices.sort(key=lambda d: pos.get(d["id"], len(pos)))
            self.save()

    def set_role(self, did, port, role):
        if role not in (None, "firewall", "cache"):
            raise ValueError("Role must be firewall, cache or none")
        with self.lock:
            d = next((x for x in self.devices if x["id"] == did), None)
            if not d:
                raise ValueError("No such device")
            roles = dict(d.get("roles") or {})
            if role:
                roles[str(port)[:40]] = role
            else:
                roles.pop(str(port), None)
            d["roles"] = roles
            self.save()

    # ---------- polling ----------
    def poll_loop(self):
        time.sleep(1)
        while True:
            if self.community():
                with self.lock:
                    devs = list(self.devices)
                try:
                    list(self.pool.map(self.poll_one, devs))
                except Exception as e:  # never let one bad poll stop the poller
                    print(f"switch poll: {e}", flush=True)
                self.refresh_discovered()
            self.wake.wait(POLL_EVERY)
            self.wake.clear()

    def poll_one(self, dev):
        prev = self.state.get(dev["id"], {})
        st = {"at": time.time()}
        t0 = time.time()
        try:
            with self.client(dev) as c:
                info = S.identify(c)
                kind = kind_of(info)
                st.update(status="ok", name=info.get("name"), descr=info.get("descr"), model=S.model_of(info),
                          uptime=(info["uptime"] / 100) if isinstance(info.get("uptime"), int) else None, kind=kind,
                          last_ok_at=time.time())
                if kind == "wlc":
                    st.update(self.read_wlc(c))
                else:
                    st.update(self.reader.read(dev["id"], c, info, dev["host"], self.community(dev), self.version(dev),
                                               dev.get("roles") or {}))
                    self.attach_neighbors(st)
        except (S.SnmpError, OSError) as e:   # OSError: no route for a moment (the link is renegotiating)
            st.update({k: v for k, v in prev.items() if k not in ("status", "error", "at")})
            st.update(status="error", error=str(e)[:160], down_since=prev.get("down_since") or time.time())
        st["took"] = round(time.time() - t0, 2)
        with self.lock:
            self.state[dev["id"]] = st
            if st["status"] == "ok" and (st.get("name") and dev.get("name") in (None, "", dev["host"])):
                dev["name"] = st["name"]
            if st.get("model") and not dev.get("model"):
                dev["model"] = st["model"]

    @staticmethod
    def attach_neighbors(st):
        """Each port gets its CDP/LLDP neighbor (as GXL shows it) and the short form the trace uses."""
        by_port = {G.norm_port(n.get("local_port")): n for n in st.get("neighbors") or [] if n.get("local_port")}
        st["vlans"] = st.get("vlan_list") or [1]
        for p in st.get("ports", []):
            p["if"] = p["index"]
            n = by_port.get(G.norm_port(p["name"]))
            p["nb"] = n
            if n:
                platform = n.get("platform") or ""
                p["cdp"] = {"name": n.get("name") or "", "short": (n.get("name") or "").split(".")[0].split("(")[0],
                            "port": short_if(n.get("their_port") or ""), "platform": platform, "ip": n.get("ip") or None,
                            "kind": "ap" if re.search(r"AIR-(C?AP|LAP|AP)", platform) else
                                    "wlc" if re.search(r"AIR-CT|C9800|Controller", platform) else
                                    "switch" if n.get("is_switch") or re.search(r"WS-C|C9[0-9]{3}|IE-|Catalyst", platform) else
                                    "host"}
            else:
                p["cdp"] = None

    def read_wlc(self, c):
        aps = {}
        for o, v in c.walk(AP_NAME, limit=2000):
            aps[bytes(o[len(AP_NAME):])] = {"name": S.text(v)}
        clients = collections.Counter()
        n = 0
        for o, v in c.walk(MS + (MS_AP,), limit=20000):
            n += 1
            if isinstance(v, bytes):
                clients[bytes(v)] += 1
        for mac, a in aps.items():
            a["clients"] = clients.get(mac, 0)
            a["mac"] = fmt_mac(mac)
        return {"aps": sorted(aps.values(), key=lambda a: a["name"].lower()), "clients": n}

    # ---------- discovery ----------
    def refresh_discovered(self):
        """CDP neighbors of managed devices that look like switches or controllers but aren't added yet."""
        with self.lock:
            have = {d["host"] for d in self.devices}
            names = {(self.state.get(d["id"], {}).get("name") or d.get("name") or "").split(".")[0].lower() for d in self.devices}
            found = {}
            for d in self.devices:
                for p in self.state.get(d["id"], {}).get("ports", []):
                    nb = p.get("cdp")
                    if nb and nb["kind"] in ("switch", "wlc") and nb.get("ip") and nb["ip"] not in have \
                            and nb["short"].lower() not in names and S.private_ip(nb["ip"]):
                        found[nb["ip"]] = {"ip": nb["ip"], "name": nb["short"], "platform": nb["platform"], "kind": nb["kind"],
                                           "via": f"CDP on {d.get('name') or d['host']} {p['name']}"}
            for ip, f in self.discovered.items():
                if f.get("via") == "scan" and ip not in have:
                    found.setdefault(ip, f)
            self.discovered = found

    def start_scan(self):
        if self.scan["running"]:
            raise ValueError("A scan is already running")
        if not self.community():
            raise ValueError("Set the SNMP community first")
        net = self.p.net
        if not net.get("ip") or not net.get("prefix"):
            raise ValueError("The probe has no address to scan around")
        n = ipaddress.ip_network(f"{net['ip']}/{max(int(net['prefix']), 22)}", strict=False)
        self.scan = {"running": True, "at": time.time(), "found": 0, "error": None, "net": str(n)}
        threading.Thread(target=self.scan_run, args=([str(h) for h in n.hosts()],), daemon=True).start()

    def scan_run(self, hosts):
        try:
            found = S.sweep(hosts, self.community(), self.version(), timeout=1.5)
            with self.lock:
                have = {d["host"] for d in self.devices}
                for ip, info in found.items():
                    k = kind_of(info)
                    if ip not in have and k != "other":
                        self.discovered[ip] = {"ip": ip, "name": info.get("name") or ip, "platform": S.model_of(info) or info.get("descr", "")[:40],
                                               "kind": k, "via": "scan"}
            self.scan.update(found=len(found))
        except Exception as e:
            self.scan["error"] = str(e)[:120]
        finally:
            self.scan["running"] = False

    def snapshot(self):
        with self.lock:
            devs, states = list(self.devices), dict(self.state)
        added = set()
        for d in devs:
            added |= {d["host"], G.norm_switch(d.get("name")), G.norm_switch(states.get(d["id"], {}).get("name"))}
        out = []
        for d in devs:
            st = states.get(d["id"], {"status": "pending"})
            sw = {"id": d["id"], "host": d["host"], "name": d.get("name") or d["host"], "kind": d.get("kind", "switch"),
                  "model": st.get("model") or d.get("model"), "own_community": bool(d.get("community")),
                  "status": st.get("status"), "error": st.get("error"), "polled_at": st.get("at"), "last_ok_at": st.get("last_ok_at"),
                  "down_since": st.get("down_since") if st.get("status") == "error" else None,
                  "uptime_s": st.get("uptime_s") or (int(st["uptime"]) if st.get("uptime") else None), "took": st.get("took")}
            if sw["kind"] == "wlc":
                sw.update(aps=st.get("aps"), clients=st.get("clients"))
                out.append(sw)
                continue
            roles = d.get("roles") or {}
            ports = []
            for p in st.get("ports") or []:
                n = p.get("nb")
                is_sw = bool(n and n.get("is_switch"))
                neighbor = None
                if n:
                    neighbor = {k: n.get(k) for k in ("name", "ip", "platform", "their_port")}
                    neighbor.update(protocol=n.get("protocol", "cdp"), switch=is_sw,
                                    added=n.get("ip") in added or G.norm_switch(n.get("name")) in added)
                kind = "switch" if is_sw else "unknown" if p.get("mac_count", 0) > G.EDGE_MAX_MACS else None
                ports.append({k: v for k, v in p.items() if k not in ("nb", "cdp", "if", "index", "bcast_pps", "mcast_pps")} |
                             {"uplink": bool(kind), "uplink_kind": kind, "neighbor": neighbor, "role": roles.get(p["name"])})
            uplinks = [p for p in ports if p["up"] and p["uplink"] and p["in_mbps"] is not None]
            sw.update(ports=ports, environment=st.get("environment") or {}, rates=st.get("rates") if st.get("status") == "ok" else {},
                      history=st.get("history") or [], vtp_domain=st.get("vtp_domain", ""), vtp_mode=st.get("vtp_mode", ""),
                      uplink_in_mbps=round(sum(p["out_mbps"] or 0 for p in uplinks), 2) if uplinks else None,
                      uplink_out_mbps=round(sum(p["in_mbps"] or 0 for p in uplinks), 2) if uplinks else None,
                      down_reason=G.down_report(d, devs, states) if st.get("status") == "error" else "")
            out.append(sw)
        gear = []  # every port marked as the firewall or the cache, firewalls first (GXL's gear_out)
        for d in devs:
            st = states.get(d["id"], {})
            ports = {p["name"]: p for p in st.get("ports") or []}
            for name, role in (d.get("roles") or {}).items():
                p = ports.get(name) or {}
                live = st.get("status") == "ok" and p.get("up")
                gear.append({"role": role, "switch": d.get("name") or d["host"], "port": name, "up": bool(live),
                             "in_hall_mbps": p.get("in_mbps") if live else None, "out_hall_mbps": p.get("out_mbps") if live else None,
                             "history": [list(x) for x in self.reader.gear.get((d["id"], name), ())]})
        gear.sort(key=lambda g: (g["role"] != "firewall", g["switch"], g["port"]))
        return {"community_set": bool(self.conf.get("snmp_community")), "version": self.version(),
                "devices": out, "gear": gear, "discovered": sorted(self.discovered.values(), key=lambda f: f["ip"]),
                "scan": self.scan, "probe_switch": self.probe_switch_id(),
                "probe_port": short_if((self.p.neighbor or {}).get("port") or ""), "every": POLL_EVERY}

    def probe_switch_id(self):
        nb = self.p.neighbor or {}
        mg, sw = nb.get("mgmt"), (nb.get("switch") or "").split(".")[0].lower()
        for d in self.devices:
            if d["host"] == mg or (sw and (d.get("name") or "").split(".")[0].lower() == sw):
                return d["id"]
        return None

    # ---------- trace ----------
    def start_trace(self, q, who):
        q = str(q or "").strip()[:100]
        if not q:
            raise ValueError("Type an IP, MAC address or host name")
        if not self.community():
            raise ValueError("Set the SNMP community first (Switches tab)")
        if not self.devices:
            raise ValueError("Add your switches first (Switches tab)")
        tid = uuid.uuid4().hex[:8]
        t = {"id": tid, "q": q, "by": who, "started": time.time(), "done": False, "steps": [], "error": None, "result": None}
        with self.lock:
            self.traces[tid] = t
            while len(self.traces) > 20:
                self.traces.popitem(last=False)
        threading.Thread(target=self.trace_run, args=(t,), daemon=True).start()
        return tid

    def trace_get(self, tid):
        return self.traces.get(tid)

    def trace_list(self):
        return [{"id": t["id"], "q": t["q"], "started": t["started"], "done": t["done"], "error": t["error"],
                 "summary": (t["result"] or {}).get("summary")} for t in reversed(self.traces.values())]

    def save_traces(self):
        try:
            with open(self.trace_file, "w") as f:
                json.dump([t for t in self.traces.values() if t["done"]][-20:], f)
        except OSError:
            pass

    def trace_run(self, t):
        step = lambda msg: t["steps"].append({"t": round(time.time() - t["started"], 2), "msg": msg})
        try:
            t["result"] = self.trace(t["q"], step)
        except ValueError as e:
            t["error"] = str(e)
            step("✗ " + str(e))
        except Exception as e:  # keep the page informed whatever happens
            t["error"] = f"Trace failed: {e}"
            step("✗ " + t["error"])
        t["done"] = True
        t["took"] = round(time.time() - t["started"], 2)
        self.save_traces()

    def managed(self, kind=None):
        """Devices worth asking: answering, or not read yet (just after a restart)."""
        with self.lock:
            return [d for d in self.devices if self.state.get(d["id"], {}).get("status") != "error" and (kind is None or d.get("kind") == kind)]

    def trace(self, q, step):
        ip = mac = name = None
        mac = parse_mac(q)
        if not mac:
            try:
                ipaddress.IPv4Address(q)
                ip = q
            except ValueError:
                step(f"Looking up {q} in DNS")
                try:
                    ip = socket.gethostbyname(q)
                    name = q
                except OSError:
                    raise ValueError(f"Can't find {q} in DNS") from None
                step(f"{q} is {ip}")
        wlcs = self.managed("wlc")
        if ip:
            own = next((d for d in self.devices if d["host"] == ip), None)
            if own:
                raise ValueError(f"{ip} is {own.get('name')}, one of the managed devices itself: see it on the Switches tab")
            step(f"Pinging {ip} so the switches have fresh MAC tables")
            up = subprocess.run(["ping", "-n", "-c2", "-W1", ip], capture_output=True).returncode == 0
            step(f"{ip} {'answers ping' if up else 'did not answer ping (it may block ping, or be off)'}")
            mac = self.mac_for_ip(ip, wlcs, step)
            if not mac:
                raise ValueError(f"No MAC address found for {ip}: it isn't in the probe's ARP table, the switches' ARP tables "
                                 f"or the wireless controller. Is it on, and on a network the probe can see?")
            if not name:
                try:
                    name = socket.gethostbyaddr(ip)[0]
                except OSError:
                    name = None
        step(f"Target MAC {fmt_mac(mac)}")

        switches = self.managed("switch")
        step(f"Searching the MAC tables of {len(switches)} switch{'es' if len(switches) != 1 else ''}")
        found = {}
        for dev, hit in zip(switches, self.pool.map(lambda d: self.find_mac(d, mac), switches)):
            if hit:
                found[dev["id"]] = hit
                step(f"  {dev['name']}: {hit['port']['name']} (VLAN {hit['vlan']})")
        wireless = None
        for w in wlcs:
            wireless = self.wlc_client(w, mac, step)
            if wireless:
                break

        # The edge: where the MAC was learned on a port whose neighbor isn't another managed switch.
        names = self.device_names()
        edge = None
        for did, hit in found.items():
            nb = hit["port"].get("cdp")
            if not (nb and self.match_device(nb, names)):
                edge = (did, hit)
                if not (nb and nb["kind"] in ("ap", "wlc")):
                    break
        if edge and edge[1]["port"].get("cdp") and edge[1]["port"]["cdp"].get("ip") == ip and (not name or name.startswith("_")):
            name = edge[1]["port"]["cdp"]["short"]   # the name it gives itself in CDP beats no DNS name (or "_gateway")
        result = {"target": {"q": q, "ip": ip or (wireless or {}).get("ip"), "mac": fmt_mac(mac), "name": name,
                             "user": (wireless or {}).get("user")},
                  "seen_on": len(found), "wireless": wireless}
        ap_edge = None
        if wireless and wireless.get("ap"):
            ap_edge = self.find_neighbor_port(wireless["ap"], step)
        end_dev = None
        if ap_edge:
            end_dev, port = ap_edge
            result["end"] = self.port_detail(end_dev, port, step, count_vlan=None)
            result["end"]["role"] = "ap"
        elif edge:
            did, hit = edge
            end_dev = next(d for d in switches if d["id"] == did)
            result["end"] = self.port_detail(end_dev, hit["port"], step, count_vlan=hit["vlan"])
            result["end"]["vlan"] = result["end"].get("vlan") or hit["vlan"]
        elif found:
            raise ValueError("The MAC is only seen on links between switches: the end of the path is a switch the probe "
                             "doesn't manage. Add it on the Switches tab (look under Discovered).")
        else:
            raise ValueError(f"{fmt_mac(mac)} isn't in any managed switch's MAC table" +
                             (" or the wireless controller" if wlcs else "") + ". It may be off, quiet for over 5 minutes, "
                             "or behind a switch the probe doesn't manage.")

        start = self.probe_switch_id()
        hops = self.path(start, end_dev["id"]) if start else None
        if hops is None:
            hops = [{"dev": end_dev["id"]}]
            if start:
                step("No CDP path from the probe's switch: showing the end switch only")
        for h in hops:
            d = next((x for x in self.devices if x["id"] == h["dev"]), {})
            h.update(name=d.get("name"), host=d.get("host"), model=self.state.get(h["dev"], {}).get("model") or d.get("model"))
        result["hops"] = hops
        result["probe"] = {"name": self.p.conf.get("name"), "port": short_if((self.p.neighbor or {}).get("port") or ""),
                           "switch": (self.p.neighbor or {}).get("switch")} if start else None
        e = result["end"]
        where = f"{end_dev.get('name')} {e['port']}"
        result["summary"] = (f"{(wireless or {}).get('ap')} on {where}, {wireless.get('band')} '{wireless.get('ssid')}'"
                             if ap_edge and wireless else where)
        step(f"✓ {result['target']['name'] or result['target']['ip'] or fmt_mac(mac)} is at {result['summary']}")
        return result

    def device_names(self):
        with self.lock:
            out = {}
            for d in self.devices:
                st = self.state.get(d["id"], {})
                for n in (d.get("name"), st.get("name")):
                    if n:
                        out[n.split(".")[0].lower()] = d["id"]
                out[d["host"]] = d["id"]
            return out

    @staticmethod
    def match_device(nb, names):
        return names.get(nb.get("ip")) or names.get((nb.get("short") or "").lower())

    def mac_for_ip(self, ip, wlcs, step):
        out = subprocess.run(["ip", "neigh", "show", ip], capture_output=True, text=True).stdout
        m = re.search(r"lladdr ([0-9a-f:]{17})", out)
        if m:
            step(f"Found {ip} in the probe's own ARP table")
            return parse_mac(m.group(1))
        watch = getattr(self.p, "watch", None)
        if watch:
            with watch.lock:
                hit = next((m for m, v in watch.macs.items() if v["ip"] == ip), None)
            if hit:
                step(f"Found {ip} in the ARP traffic the Watchdog heard")
                return parse_mac(hit)
        idx = tuple(int(x) for x in ip.split("."))
        for dev in self.managed():
            if dev.get("kind") == "wlc":
                continue
            try:
                with self.client(dev) as c:
                    for o, v in c.walk(ARP, limit=20000):
                        if o[-4:] == idx and isinstance(v, bytes) and len(v) == 6:
                            step(f"Found {ip} in {dev['name']}'s ARP table")
                            return v
            except S.SnmpError:
                continue
        for w in wlcs:
            try:
                with self.client(w) as c:
                    for o, v in c.walk(MS + (MS_IP,), limit=20000):
                        if ip_of(v) == ip:
                            step(f"Found {ip} as a wireless client on {w['name']}")
                            return bytes(o[len(MS) + 1:])
            except S.SnmpError:
                continue
        return None

    def find_mac(self, dev, mac):
        st = self.state.get(dev["id"], {})
        idx = tuple(mac)
        for vlan in st.get("vlans") or [1]:
            try:
                with self.client(dev, f"@{vlan}") as c:
                    bp = c.get([FDB_PORT + idx]).get(FDB_PORT + idx)
                    if not isinstance(bp, int):
                        continue
                    ifi = c.get([BP_IF + (bp,)]).get(BP_IF + (bp,))
            except S.SnmpError:
                continue
            port = next((p for p in st.get("ports", []) if p["if"] == ifi), None)
            members = (st.get("po") or {}).get(ifi)
            if port is None and members:  # a port-channel: CDP is seen on its member links
                mp = [p for p in st.get("ports", []) if p["if"] in members]
                cdp = next((p["cdp"] for p in mp if p.get("cdp")), None)
                try:
                    with self.client(dev) as c:
                        nm = short_if(S.text(c.get([IFNAME + (ifi,)]).get(IFNAME + (ifi,))))
                except S.SnmpError:
                    nm = f"Po{ifi}"
                port = {"if": ifi, "name": nm, "cdp": cdp, "up": any(p["up"] for p in mp), "trunk": any(p.get("trunk") for p in mp)}
            if port is None:  # a port the last poll didn't list: name it now
                try:
                    with self.client(dev) as c:
                        nm = short_if(S.text(c.get([IFNAME + (ifi,)]).get(IFNAME + (ifi,))))
                except S.SnmpError:
                    nm = f"if{ifi}"
                port = {"if": ifi, "name": nm, "cdp": None}
            return {"vlan": vlan, "bp": bp, "port": port}
        return None

    def wlc_client(self, w, mac, step):
        idx = tuple(mac)
        cols = {k: MS + (n,) + idx for k, n in (("ip", MS_IP), ("user", MS_USER), ("ap", MS_AP), ("slot", MS_SLOT), ("ssid", MS_SSID))}
        cols.update(rssi=MS_STATS + (1,) + idx, snr=MS_STATS + (26,) + idx)
        try:
            with self.client(w) as c:
                got = c.get(list(cols.values()))
                v = {k: got.get(o) for k, o in cols.items()}
                if not isinstance(v["ap"], bytes):
                    return None
                ap_name = S.text(c.get([AP_NAME + tuple(v["ap"])]).get(AP_NAME + tuple(v["ap"])))
        except S.SnmpError:
            return None
        rssi = v["rssi"] if isinstance(v["rssi"], int) else None
        if rssi is not None and rssi > 0x7FFFFFFF:
            rssi -= 1 << 32
        out = {"wlc": w["name"], "ap": ap_name or fmt_mac(v["ap"]), "ap_mac": fmt_mac(v["ap"]), "ssid": S.text(v["ssid"]),
               "band": {0: "2.4 GHz", 1: "5 GHz", 2: "6 GHz"}.get(v["slot"], f"radio {v['slot']}") if isinstance(v["slot"], int) else None,
               "ip": ip_of(v["ip"]), "user": S.text(v["user"]) or None,
               "rssi": rssi if rssi and -120 < rssi < 0 else None,
               "snr": v["snr"] if isinstance(v["snr"], int) and 0 <= v["snr"] < 100 else None}
        step(f"Wireless client on {w['name']}: AP {out['ap']}, {out['band']} '{out['ssid']}'"
             + (f", {out['rssi']} dBm" if out["rssi"] is not None else ""))
        return out

    def find_neighbor_port(self, ap_name, step):
        """The switch port whose CDP neighbor is this AP."""
        key = ap_name.split(".")[0].lower()
        with self.lock:
            for d in self.devices:
                for p in self.state.get(d["id"], {}).get("ports", []):
                    nb = p.get("cdp")
                    if nb and nb["short"].lower() == key:
                        step(f"AP {ap_name} is plugged into {d['name']} {p['name']} (CDP)")
                        return d, p
        step(f"AP {ap_name} isn't a CDP neighbor of any managed switch")
        return None

    def port_detail(self, dev, port, step, count_vlan=None):
        out = {"port": port["name"], "alias": port.get("alias"), "up": port.get("up"), "speed": port.get("speed_mbps"),
               "duplex": port.get("duplex"), "vlan": port.get("vlan"), "cdp": port.get("cdp"), "trunk": port.get("trunk"),
               "switch": dev.get("name"), "host": dev["host"]}
        try:
            with self.client(dev) as c:
                got = c.get([IFINERR + (port["if"],), IFOUTERR + (port["if"],), IFOPER + (port["if"],)])
                out["errors"] = sum(v for v in (got[IFINERR + (port["if"],)], got[IFOUTERR + (port["if"],)]) if isinstance(v, int))
                out["up"] = got[IFOPER + (port["if"],)] == 1
            if count_vlan:
                with self.client(dev, f"@{count_vlan}") as c:
                    bps = {o[-1]: v for o, v in c.walk(BP_IF, limit=2000)}
                    bp = next((b for b, i in bps.items() if i == port["if"]), None)
                    macs = [fmt_mac(bytes(o[len(FDB_PORT):])) for o, v in c.walk(FDB_PORT, limit=20000) if v == bp]
                out["macs"], out["mac_count"] = macs[:12], len(macs)
                if len(macs) > 1:
                    step(f"{len(macs)} MAC addresses on {port['name']}: an unmanaged switch, hub or VM host may be behind it")
        except S.SnmpError as e:
            step(f"Couldn't read port details: {e}")
        return out

    def path(self, start, end):
        """Hops over CDP links between managed switches, from start to end (BFS)."""
        names = self.device_names()
        with self.lock:
            links = collections.defaultdict(list)
            for d in self.devices:
                for p in self.state.get(d["id"], {}).get("ports", []):
                    nb = p.get("cdp")
                    other = nb and self.match_device(nb, names)
                    if other and other != d["id"]:
                        links[d["id"]].append((other, p["name"], nb["port"]))
        prev = {start: None}
        q = collections.deque([start])
        while q:
            cur = q.popleft()
            if cur == end:
                break
            for nxt, out_port, in_port in links[cur]:
                if nxt not in prev:
                    prev[nxt] = (cur, out_port, in_port)
                    q.append(nxt)
        if end not in prev:
            return None
        chain, cur = [], end
        while cur is not None:
            p = prev[cur]
            chain.append({"dev": cur, "in": p[2] if p else None})
            if p:
                chain[-1]["from_port"] = p[1]
            cur = p[0] if p else None
        chain.reverse()
        for i in range(len(chain) - 1):
            chain[i]["out"] = chain[i + 1].pop("from_port")
        return chain
