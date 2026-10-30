# Probe Deck

A pocket gigabit **network probe** with its own web page. Plug it into any switch port and it is powered by PoE. Within a few seconds the page shows what is on the other end of the cable: which switch and port, which VLAN, whether DHCP, DNS and the internet work, and how fast the port really is.

Every page is in the **sidebar**, grouped, under a **This jack** card that always shows the port, switch, VLAN and link lights:
- **Port:** the **Overview** (what is on the other end of this cable), **Speed test** (LAN with iperf3; the internet with Fast.com, Cloudflare, Ookla or a download server), **Cable test** (the switch's TDR, pair by pair) and **VLAN walk** (DHCP, gateway, DNS and internet on every VLAN of a trunk).
- **Network:** **Switches** (GXL-LANapp's Switches page over SNMP), **Trace** (the path to any IP, MAC or name), **Traceroute** (every hop watched once a second, like mtr), **DNS test** (which DNS server to use at this site), **Host scan** (who is on this subnet, with makers, names, smart-device types and open ports), **mDNS** (every service announced on the LAN) and **Multicast** (groups, the IGMP querier, a join test).
- **Watch:** **Watchdog** (rogue DHCP, IP conflicts, spoofing, storms, spanning tree, rogue IPv6 routers, and what's on the wire), **Switch logs** (a syslog receiver for the switches) and **Capture** (tcpdump to .pcap, and alarms that capture themselves).
- **Event:** **Jack survey** (walk the hall and record every seat's jack), **CS2** (is this network fit for a match?), **Fairness** (is one team or seat worse off?), **Tick test** (game-rate UDP to a reflector), **LAN cache** (are game downloads hitting the cache?) and **Wake** (Wake-on-LAN for the room).
- **Switch tools:** **Console** (the USB console cable in the browser), **Configs** (running-config backups and diffs) and **Files** (a TFTP server for images and configs).
- **System:** **System stats** (how the probe itself is doing), **Settings & power**, **Tailscale**, **HTTPS** and **Users**.

It is built on a **Radxa ZERO 3E** with its PoE HAT, and the page has the same look as [iPerf Deck](https://github.com/turkey415/Perf-Deck). The probe joins Perf-Deck's network of stations, so the two can test each other.

![Probe Deck](docs/screenshot.png)

The screenshots use made-up names and addresses (core-1, access-1, 10.10.x.x), and some show sample data.

<table>
<tr><td width="50%"><b>Speed test</b><br><img src="docs/speed.png" alt="Speed test: arc dials for download and upload, small dials, throughput chart"></td><td width="50%"><b>Cable test</b><br><img src="docs/cable.png" alt="Cable test: the switch TDR test, pair by pair, with pair C open 7 m away"></td></tr>
<tr><td width="50%"><b>VLAN walk</b><br><img src="docs/vlans.png" alt="VLAN walk: DHCP, gateway, DNS and internet on six VLANs of a trunk"></td><td width="50%"><b>Switches</b><br><img src="docs/switches.png" alt="Switches: hall gauges, firewall and LAN cache monitors, bandwidth monitor and network stats"></td></tr>
<tr><td width="50%"><b>Ports</b><br><img src="docs/ports.png" alt="The Ports drill-down: environment needles, front panel and the CDP neighbor table"></td><td width="50%"><b>Trace</b><br><img src="docs/trace.png" alt="A host trace: the path from probe-01 through the switches to the host"></td></tr>
<tr><td width="50%"><b>Traceroute</b><br><img src="docs/mtr.png" alt="Traceroute: every hop probed once a second, with loss, latency and a strip of the last 40 probes"></td><td width="50%"><b>Host scan</b><br><img src="docs/hosts.png" alt="Host scan: every host with its maker, name, smart-device type and open ports"></td></tr>
<tr><td width="50%"><b>mDNS</b><br><img src="docs/mdns.png" alt="mDNS: services announced on the LAN, by device and category"></td><td width="50%"><b>Multicast</b><br><img src="docs/mcast.png" alt="Multicast: the IGMP querier, groups with their traffic, and a join test"></td></tr>
<tr><td width="50%"><b>Watchdog</b><br><img src="docs/watchdog.png" alt="Watchdog: a rogue DHCP server, broadcasts per second, spanning tree"></td><td width="50%"><b>On the wire</b><br><img src="docs/wire.png" alt="On the wire: the probe adapter, flooding, loud senders, switch protocols, ICMP notices"></td></tr>
<tr><td width="50%"><b>Switch logs</b><br><img src="docs/logs.png" alt="Switch logs: syslog from the switches, coloured by severity"></td><td width="50%"><b>Capture</b><br><img src="docs/capture.png" alt="Capture: tcpdump with a filter, live packets and kept .pcap files"></td></tr>
<tr><td width="50%"><b>Jack survey</b><br><img src="docs/survey.png" alt="Jack survey: every seat in a hall with its port, link, time to online, gateway and verdict"></td><td width="50%"><b>CS2</b><br><img src="docs/cs2.png" alt="CS2: match readiness, ping, jitter and loss to each Perf-Deck node"></td></tr>
<tr><td width="50%"><b>Tick test</b><br><img src="docs/tick.png" alt="Tick test: game-rate UDP to a reflector, every reply charted, graded for Counter-Strike"></td><td width="50%"><b>LAN cache</b><br><img src="docs/cache.png" alt="LAN cache: every game download name pointing at the cache, heartbeat and a HIT"></td></tr>
<tr><td width="50%"><b>Wake</b><br><img src="docs/wake.png" alt="Wake-on-LAN: saved machines with up/down lights"></td><td width="50%"><b>Console</b><br><img src="docs/console.png" alt="The green CRT-style web console on a Cisco 3560"></td></tr>
<tr><td width="50%"><b>Configs</b><br><img src="docs/configs.png" alt="Configs: running-config versions and a coloured diff"></td><td width="50%"><b>Files (TFTP)</b><br><img src="docs/files.png" alt="Files: IOS images and configs served over TFTP, with the commands to paste"></td></tr>
<tr><td width="50%"><b>HTTPS</b><br><img src="docs/https.png" alt="The HTTPS page: the certificate, its expiry and fingerprint"></td><td width="50%"><b>Users</b><br><img src="docs/users.png" alt="The Users page: accounts and roles"></td></tr>
<tr><td width="50%"><b>System stats</b><br><img src="docs/stats.png" alt="System stats: needle meters for CPU, temperature, memory, SD card and traffic, per-core CPU and heat"></td><td width="50%"><b>Log in</b><br><img src="docs/login.png" alt="The login screen"></td></tr>
</table>

## Features

### The sidebar
- A **This jack** card at the top: the switch port in big amber letters, the switch, VLAN and link speed, lights for link, DHCP, DNS and internet, and how many Perf-Deck stations are heard. It's on every page; click it for the Overview.
- Pages are grouped in **modules**: Port, Network, Watch, Event, Switch tools and System. Click a module's heading to fold it; the page remembers which are folded. Each module has a light showing the worst state inside it (red when the Watchdog has a problem or a switch port is in trouble, even while folded).
- On a phone the sidebar slides out from the ☰ button.

### Port: what is this jack?
- **Switch, port and VLAN** from **CDP** (Cisco) or **LLDP** (everyone else), e.g. `Gi0/7` on `access-1`. Also shows the switch model, IOS version and management IP.
- **Time to online:** seconds from link-up until the port passes traffic. Without portfast a switch port listens and learns for 30–50 seconds first, and every PC on it waits that long; over 25 s is flagged **portfast off?**. Live (address, gateway answering, internet answering) when the link comes up while the probe is running: a port bounce, or a move to another jack on USB-C power. **On PoE** the probe is powered by the port, so it boots after the link is already up and can't time it live: it reads the boot logs instead (the kernel's *Link is Up*, then dhclient's DHCP offer and address). DHCP's own retry timer adds a few seconds to that figure. Under the number, only labelled times: after link-up (address, gateway, internet, or DHCP offer and address on PoE), then **Boot (PoE)** or **Boot (USB)**, seconds from power-on until Probe Deck is running. PoE is assumed when the link was already up as the probe booted; the probe has no power sensor. Jack survey records both for every seat.
- **Port details** (from CDP/LLDP and DHCP): data and **voice VLAN**, the **PoE** the switch offers, duplex, whether the port asks for **802.1X**, whether it offers to trunk (DTP), whether the gateway is an **HSRP / VRRP / GLBP** virtual MAC and its maker, and the DHCP **phone options** (150 / 66 TFTP server, 43, NTP).
- **Link** speed and duplex, how long the link has been up, and **link drops** (with the time of the last one).
- **Plug to IP**: seconds from cable-in to a DHCP address.
- **New jack, new lease**: when the cable is moved, the probe asks DHCP again, so a different VLAN gets a fresh address. You can turn this off.

### Network
- IP address and prefix, gateway, DNS servers, search domain, **DHCP server**, lease length and expiry, MAC and MTU.

### Checks
- **Ping** up to 8 targets every 2 seconds (`GW` means whatever the gateway is), showing last, average, jitter and loss over the last minute.
- **DNS**: looks up a test name through **each** DNS server it was given, with the answer and the time taken. A server that fails stands out.
- **Internet**: an HTTP check (`generate_204` by default), with its time.
- **Charts**: latency per target over the last 5 minutes, and live in/out traffic on the port.

### Speed tests
The **Speed test** page (Port in the sidebar) holds the LAN test, the Stations and both histories.
- **Instruments and charts from iPerf Deck**, for either test (a LAN / Internet switch): big **arc dials** for download and upload (live while a test runs, then the average, with a peak marker) with **Avg, Peak, Low and Data** readouts; small dials for **round trip, retransmits per second, congestion window** and the gateway ping (LAN), or **idle latency, latency under load** both ways and **bufferbloat** (internet).
- A **throughput chart** with a shaded area and a hover crosshair and tooltip shared by every chart; **retransmits per second** (bars) and **round-trip time** for a LAN test, **latency under load** (with the idle line) for an internet test.
- A **waterfall**: one row per test, coloured by speed from start to finish. Click a row, or a row in either history table, to bring that test back on the dials and charts.
- Round trip, retransmits and congestion window come from the sending side, so a LAN test shows them for the upload (run Upload or Both).
- An **internet speed test** with a choice of provider: **Fast.com** (Netflix's servers nearest you), **Cloudflare**, **Ookla Speedtest** (its own program, installed from the page once; you accept Ookla's license there) or a **download server** (Vultr, Hetzner, OVH, or any URL; download only). Download and upload for 3–30 s each way over 1–16 streams, with live readouts and a chart. The transfers run in `curl`, one process per stream, so multi-gig lines measure properly (use 8 or more streams). It pings 1.1.1.1 before the test (**idle latency** and jitter) and while it runs (**loaded latency**): when latency climbs under load, the uplink has bufferbloat. It shows the public IP, the network and the server. A multi-gig test moves several GB, so it only runs when you press the button. Providers limit how much one public IP can test (Cloudflare answers 429, Hetzner allows 2 streams, Vultr rations); the page says so, and a server that refuses some streams is tested with the rest.
- An **iperf3** client against any server, as download, upload or both, with live seven-segment readouts and a live chart.
- **Pick a probe**: under the server box, every other probe on the network is a button with the switch port it's plugged into (from its CDP/LLDP), and Perf-Deck PCs are listed below them. A probe-to-probe test tests the path between two jacks, and the history shows both ends.
- **History** of every test, with the switch port it ran on.
- An **iperf3 server** on port 5201, so other machines can test the probe.

### Stations: Perf-Deck on the LAN
- The probe speaks **Perf-Deck discovery** (UDP 52010). It shows up as a station in every Perf-Deck on the network segment, and it lists every Perf-Deck station it hears.
- Other probes show up here too, tagged **PROBE**, with the switch port each one is plugged into. Probes add `probe` and `jack` (switch, port, VLAN, link speed) to the Perf-Deck hello; Perf-Deck ignores fields it doesn't know.
- Each station shows a live **ping**, a ready/busy light, and its last result.
- **Send / Receive / Both** buttons run a quick 5-second test against one station.
- **Sweep all** runs a 2-second test from every station to the probe in turn. It follows Perf-Deck's rules: it waits while another station is sweeping, and every station rests 5 minutes after any sweep.

### Probe message (for the displays)
- The probe announces itself over **mDNS** as `_probe-deck._tcp`, so displays can find it by name and poll `GET /api/probe`. That works even where the Wi-Fi drops broadcasts.
- Every ~9 s the probe broadcasts a small JSON summary on **UDP 47474**: switch and port, VLAN, link, IP, gateway ping, DNS and internet status, and the last speed test. This is the shared probe message from [t-display-progs](https://github.com/turkey415/t-display-progs) (`TODO.md`) for the T-Display and CYD **Probe Viewer** app, and for PorkChopCheck. `GET /api/probe` returns the same message.

### Settings and system
- Probe name, ping targets, check interval, DNS test name, internet check URL, and on/off switches for the iperf3 server, Perf-Deck discovery, the broadcast, and DHCP-on-replug.
- **Energy Efficient Ethernet off** (on by default): EEE lets the link sleep between packets, which adds a wake-up delay to every test (the probe's port came set to sleep up to a second). It's switched off at start and after a link comes up, and only when it's on, because changing it makes the link renegotiate for a few seconds. Settings are saved from the page.
- **Check now** and **Renew DHCP** buttons on the Overview's Network card.
- Works on a phone: the layout stacks to one column.

### Console (USB serial cable to a switch)
- A **web terminal** (xterm.js, served by the probe, so no internet is needed) on any USB serial adapter: FTDI, Prolific, CH340, CP210x. Amber, green or white phosphor; full screen; text size.
- **Break** button, for Cisco password recovery and ROMMON (send it in the first 60 s of boot).
- **Paced paste**: a pasted config goes out one line at a time, and each line waits for the switch's echo, so a 3560 at 9600 baud doesn't drop characters. A multi-line paste into the terminal is paced the same way.
- **Macro keys**: one click for `show interfaces status`, `show vlan brief`, `write memory`… Edit the list on the page.
- **Session logs**: everything the switch prints is saved, one file per time the cable is plugged in, and can be downloaded from the page.
- Several people can watch the same console. The last 128 KB of output is replayed to anyone who opens it.
- **Telnet port** (off by default, port 2001): what ser2net does. PuTTY, SecureCRT or `telnet probe-01 2001` reach the same console. In telnet, `Ctrl-]` then `send brk` sends a Break.
- **Who can use it:** admins type, paste, send Break and change console settings. Viewers watch the same live screen and can download the logs, but what they type goes nowhere. The telnet port asks for an **admin** user name and password.

### Switch intake (a new switch: boot health, then a preload)
- Plug the probe's console cable into the switch, press **Watch for a boot** (or let it start by itself when a boot shows on the console) and power the switch on. The probe only **listens** while it boots.
- The **report** reads the boot: boot loader, every power-on self test, the image it loaded, the IOS banner (model, serial, base MAC, memory, last reset reason) and any error messages. A timeline bar shows how long each stage took. Then it runs a few show commands over the console: `show env all`, `show post`, `show power inline`, `dir flash:`, `dir nvram:`, error counters and `show inventory`. Pass, check or fail, with each finding listed; every report is saved and can be downloaded as text with its boot log.
- **Where it stopped:** the setup dialog (factory blank), a prompt, a login (the last owner's config is still there: **Erase** runs `write erase`, deletes `vlan.dat` and reloads, then watches the next boot), or ROMMON.
- **IOS version:** compared with the recommended versions you list (e.g. `C3560CX = 15.2(7)E13`). With a Cisco openVuln API key it also lists the security advisories for that version.
- **Preload:** when the switch is ready, a window offers a config: a saved template, a file, or a **pasted** config (a whole `show running-config` works: headers, comments, the old switch's self-signed certificate and `^C` banner delimiters are cleaned up). `{{placeholders}}` become fields; `{{serial}}`, `{{model}}`, `{{mac}}` and `{{ios}}` fill in from the report. Before sending, it warns about ports this model doesn't have, a `login local` with no user, no enable secret, VTP server/client and non-ASCII characters. Lines go out one at a time; any the switch rejects are listed with their line number. You see the before/after diff, and nothing is saved until you press **Save to the switch** (`write memory`).
- If the console text is garbled it tries the other common speeds.

### Watchdog (what is wrong with this LAN?)
A kernel filter passes only broadcast and multicast frames to the watchdog, so speed tests don't load it.
- **Rogue DHCP**: every 60 s it sends a DHCP Discover (with the broadcast flag, and it never takes the lease) and lists every server that answers, with the address, router and DNS it offers. Servers other than the probe's own lease server are **rogue** until you press **Trust**. Offers heard on the wire count too.
- **IP conflicts** from ARP: one IP claimed by two MACs, someone else using the probe's address, and anyone **claiming to be the gateway** (ARP spoofing).
- **Broadcast storms**: broadcasts per second with a chart, an alarm over a limit (1000/s by default) for 3 s, and the **top broadcast senders**, to find the loop or the chatty PC.
- **Spanning tree**: root bridge, protocol, **topology changes** and root changes, from the BPDUs the switch sends.
- **IPv6 router advertisements**: a PC sharing its connection (Windows ICS) or a rogue router.
- **DTP**: says when the port could be talked into a trunk.
- **Switch alarms**: a port that gets **err-disabled** (with the cause: BPDU guard, port security, link flapping, loopback, UDLD, PoE…), **blocked by a spanning-tree guard** (root guard, loop guard, a native VLAN mismatch) or sees a **MAC address flapping** between two ports (usually a loop). They come from the lines arriving on Switch logs, so they cover every port on every switch that logs to the probe. Switches on the Switches page are also checked for err-disabled ports on every poll (CISCO-ERR-DISABLE-MIB), in case a log line got lost. An alarm stays until the switch says it's over (a recovery line, the port coming back up, or the poll no longer listing it); MAC flapping clears after 5 quiet minutes. Each one turns the Watch module red and starts the 30-second capture. **Acknowledge** keeps it listed (amber, with who acked it) but stops it turning the lamp red; it alerts again only if it clears and comes back. A cleared one can be **dismissed**. The matching rules are in `swalarms.py`, which GXL-LANapp's Event Server uses too (`python3 tools/test_swalarms.py` tests them).
- **On the wire** (PorkChopCheck's wire report): the probe's own adapter (flow control, **Energy Efficient Ethernet**, a link slower than both ends offer, PAUSE and error counters), **flooding** (traffic for other machines reaching this port: a spanning-tree change, a full MAC table or a hub), the loudest broadcast and multicast senders by maker, address conflicts, IPv6 routers, **switch protocols heard** (LACP, PAgP, UDLD, VTP, loopback keepalives, PAUSE frames) and **ICMP redirects or "fragmentation needed"** sent to the probe. If more than 5,000 frames a second arrive for other machines (a mirror port or a bad flood) it stops listening for 30 seconds and says so.
- An **event list** of everything above. Warnings also go out at once on UDP 47474 as an `app: "probe-alert"` message (the displays ignore it; PorkChopCheck can use it), and the probe message gets an `alerts` count.

### Cable test (is it the cable or the port?)
- Runs the switch's own **TDR** test (`test cable-diagnostics tdr`) on any port, over SSH or the console cable, and reads back **each pair**: Normal, Open, Short or Impedance mismatch, and **how many metres** from the switch. **This jack** fills in the probe's own switch and port.
- A plain verdict: "All four pairs are normal", "Pair C is open about 7 m from the switch", "Every pair is open about 30 m away: nothing is plugged in at the far end". At 100 Mbps the two unused pairs may say *Not completed*, and that's fine. A gigabit jack where the probe only links at 100 Mbps gets a hint that a pair is probably bad.
- TDR drops the port's link for a few seconds. On the probe's own jack the test starts in one login, waits for the link to come back, and reads the result in a second one.
- Uses the switch login from the Configs page, or one typed on the form. The last 50 results are kept.
- **Enable mode:** a login that lands at `>` tries `enable` with the saved enable secret, then the login password, then an empty password. A switch with **no enable password** refuses `enable` over SSH (`% No password set`): then, if the probe's console cable is on that same switch (same hostname), the test runs over the cable instead, which needs no enable password and doesn't lose its link during TDR. Otherwise the page says what to change: `username <user> privilege 15`, or an `enable secret`.

### VLAN walk (does every VLAN on this trunk work?)
- Plug the probe into a **trunk** port and list the VLANs (`10,20,30-35`, up to 64; a button offers the switch's own VLAN list when the Switches page reads that switch).
- For each VLAN the probe makes a tagged interface and gets a **real DHCP lease** (Discover, Offer, Request, Ack), then, from that address, **pings the gateway**, asks **each DNS server** it was given, and fetches the **internet check** URL. Then it releases the lease and removes the interface.
- A table shows, per VLAN: how long DHCP took (and the relay, if any), the address and DHCP server, gateway replies and latency, each DNS server, and the internet check. Missing pieces (no router or DNS handed out) are called out.
- Each test VLAN gets its own routing table, so the probe's own address and traffic don't move. The Watchdog ignores the walk's DHCP, so the other VLANs' servers don't show up as rogue. On an access port nothing answers, and the page says the port probably isn't a trunk.

### Traceroute (where does it go slow?)
- Type an address or a name: every hop on the way is probed **once a second**, like `mtr`, with its loss, last, average, best and worst times and a strip of its last 40 probes (lost ones in red), all hops on one scale so a slow hop stands out. Hop names come from reverse DNS. **Gateway** and recent targets are one click.
- A hop that loses probes or answers slowly while the hops after it don't is a router that limits its own replies, not a fault; loss or delay that carries on to the last hop is real.

### mDNS (what's announced on the LAN?)
- Every service announced over multicast DNS (Bonjour / Avahi): Chromecasts, AirPlay, printers, ESPHome, WLED, Home Assistant, Matter, Shelly, file shares… It asks every 10 minutes and listens all the time. PorkChopCheck's browser (`pc_net.py`).
- **By device** (the default): one row per device, grouped into colour-coded categories (printers, TVs and speakers, smart home, phones and Apple, computers and storage, network and lab, other). Each service is a chip with its own colour, and each host has its own colour dot. Click a device for its services, ports, TXT details and an **open** link for web UIs. Categories fold.
- **By service**: the old list, one group per service type.

### Multicast (does multicast work here?)
- **IGMP querier:** who it is, its IGMP version and how often it asks. With IGMP snooping on and no querier, streams, AirPlay and mDNS work for a few minutes and then stop; **Ask for a querier** joins a scratch group for a moment so a querier answers within a second instead of up to two minutes.
- **Groups** seen on the port with their rate, sources and the members heard reporting, named where known (mDNS, SSDP, Dante, sACN, PTP…).
- **Join test:** the probe joins a group through Linux and shows whether traffic for it arrives.

### Host scan (who is on this subnet?)
- Sends one small packet to every address on the probe's subnet (up to a /22), then reads the probe's ARP table: anything that answered ARP is a host, **even with its firewall up**. Hosts the Watchdog heard in the last 10 minutes count too.
- For each host: IP, MAC and **maker** (from the IEEE lists, so `Cisco`, `Ubiquiti`, `Raspberry Pi`…; phones with a private MAC say so), **names** from reverse DNS, NetBIOS (Windows PCs) and mDNS, and which of a few **TCP ports** are open (22, 80, 443, 445, 3389, 5201, 8080, 9100, 27015 by default; change the list).
- Names the probe already knows are shown first: Wake machines, Perf-Deck stations, switches from the Switches page, the gateway.
- Hosts are remembered by MAC with first and last seen; machines that turn up after the first scan are marked **new**. Filter by anything; each host has a **Trace** button.
- **Known or unknown:** every host starts out unknown. **Mark known** (next to Trace, admins) puts a green frame round the row and a KNOWN tag on it; click **✓ Known** to make it unknown again. An **Unknown** readout and an **Unknown only** filter show what nobody has vouched for yet. Known hosts are kept when the list is full.
- **Colour by maker:** each maker has its own colour (a stripe down the row and its name, the same colour on every visit), also beside each maker in the Makers list.
- Scans every 15 minutes (or only when you press **Scan now**). A /24 takes about 12 seconds.

### DNS test (which DNS server should this site use?)
- Press **Go**: for 30 seconds (10–120, a setting) every server is asked for everyday names (Steam, Discord, Google, Twitch, Riot, Epic, Battle.net, FACEIT…) four times a second, and pinged. Then it stops: nothing runs in the background. Anyone logged in can run it; the lists are admin settings.
- Public resolvers are on the list (Cloudflare, Google, Quad9, OpenDNS, AdGuard, CleanBrowsing, Control D, Level3, DNS.SB), and the DNS servers **this network handed out (DHCP)** are added, to compare.
- Live results, ranked: the **typical answer** (median), the slow 10% (90th percentile), the fastest, ping, and lost or refused queries, a colour per server. The top card names a **primary** and a **second server from another provider**, and says how this network's own DNS compares.
- Every few queries it asks for a made-up name under example.com: a server that answers it is **making up answers** for names that don't exist (some ISPs do, to show ads) and is ranked down.
- The last 10 runs are kept, with the switch port they were run from.
- The idea comes from [go-dnstester](https://github.com/t0mer/go-dnstester) (Apache-2.0), a full DNS benchmark with history and schedules; this is a small spot check written for Probe Deck.

### Jack survey (every seat, before the doors open)
- Press **Start surveying**, then walk the hall and plug the probe into each seat's jack in order. On each jack it waits for the switch port (CDP/LLDP), a DHCP address, its DNS and internet checks and a few gateway pings, then **records the seat** and moves the label on (B14 → B15). On PoE the probe boots on every jack; the survey carries on across reboots. Plugging into the same switch port again within 10 minutes isn't counted twice.
- Each seat keeps: switch, port and VLAN, link speed and duplex, seconds from plug to address, gateway latency and loss, DNS and internet, and (optional) a 3-second speed test to a Perf-Deck station.
- A verdict per seat, **good**, **check** or **bad**, with the reasons: not gigabit, half duplex, no DHCP, slow DHCP, gateway loss or latency, DNS or internet failing, a VLAN that differs from most seats, or a switch port shared with another seat.
- Readouts for the next seat and the good/check/bad counts, a table (with **Only problems**), rename or delete seats, **Download CSV**, and **New survey** (the old one is kept on the probe).
- The probe message on UDP 47474 carries the survey (`sv`: armed, next seat, count, last seat's verdict), so a T-Display can show it while you walk.
- **Send to GXL:** with the GXL event server's address and the event's agent key saved, each seat's switch and port go straight into GXL's seat list, and the verdict into the seat's history. Only seats already on GXL's chart are changed (A-01, A01 and A1 are the same seat); the ones GXL doesn't have are listed. The key is kept on the probe and never shown on the page.
- **Show the probe in GXL** (on by default once the address and key are set): the probe checks in like a PC agent, with its address, MAC and the switch port it sees. GXL puts it at the seat on that port (moving it to another jack moves it to that seat), its connections show at that seat on GXL's traffic map, and it runs GXL's speed tests to the GXL server with iperf3 like the PC agents do.

- **Smart devices** (on by default): PorkChopCheck's discovery (`pc_smart.py`) asks over SSDP/UPnP, mDNS, Kasa/Tapo, LIFX, Govee, WiZ, Yeelight and Tuya, and reads Roku, Hue, Shelly and Cast device pages, then shows each device's **type** (TV, speaker, camera, plug, bulb…), **brand**, **model** and, when a device publishes one, its **serial number**. A **Smart devices** filter shows just those. It adds about 20 seconds to a scan.

### Switch logs (syslog from the switches)
- The probe is a **syslog receiver** (UDP 514). The page shows the lines to paste into each switch (`logging host <probe>`, `logging trap informational`, timestamps and `logging origin-id hostname`).
- Cisco messages are split into their code (`%LINK-3-UPDOWN`) and text, coloured by severity, and named by switch (from the message, the Switches page, or the probe's own neighbor).
- **Last hour** readouts (messages, errors, switches sending), messages per minute, and a list you can filter by severity, switch and text. Tick **Watchdog and CS2 events too** to see everything on one timeline: "why did seat 14 lag at 8:42?"
- Kept 14 days (one file per day in `/var/lib/probe-deck/logs/`, written every 5 s to spare the SD card); download any day as text. A sender is capped at 200 messages a second.
- The sidebar shows how many errors (severity 0–3) came in the last hour.
- Lines that mean a port is in trouble (err-disable, spanning-tree guards, MAC flapping) also become **Watchdog** alarms.
- **Mark read**: the sidebar counts only errors that came in since someone last pressed it (an ordinary link flap is severity 3, so this is what lets the Watch module go green again).
- **Who's sending:** each sender is named from the Switches page or the message itself; anything else shows what Host scan knows about it (name and maker). Cisco access points that haven't joined their controller send syslog to the broadcast address, so the probe hears them without any setup.
- **Ignore** a sender to drop anything new from it, and **Listen again** to take its messages again: ignored senders stay in the list, dimmed, even after their messages are deleted. **✕** deletes a sender's saved messages.

### LAN cache (are game downloads hitting the cache?)
For a [lancache](https://lancache.net) (or any cache that works the same way):
- **DNS:** looks up the download names of Steam, Battle.net, Epic, Riot, EA, Ubisoft, Xbox and Windows Update through **every** DNS server the probe was given, and shows which ones point at the cache. The names are taken from the [uklans cache-domains](https://github.com/uklans/cache-domains) lists that lancache-dns uses. Steam's list is just `lancache.steamcontent.com`: the Steam client looks that name up and, when it's a LAN address, downloads through it. One DNS server that skips the cache is enough to send half the room to the internet.
- **The cache answers:** lancache's heartbeat (`/lancache-heartbeat`, which says which cache answered), and whether port 443 is open for HTTPS passthrough.
- **A test file (optional):** paste an `http://` URL from the cache's access log (a Steam chunk is ideal). It's fetched through the cache twice: the first should say **MISS** and the second **HIT**, and the second shows how fast the cache serves.
- **Other DNS servers:** add up to 6 DNS servers to be checked every time, next to (or instead of) the ones DHCP hands out, or type one in **Try a DNS server** for a one-off check that isn't saved: test a cache's DNS before it goes into the DHCP config. Each column says where the server came from (DHCP, added, trial).
- The cache's address is found by itself (the LAN address most game names point at), or set it. Checks run every 10 minutes and on **Check now**. The sidebar light is green when every name goes to the cache, amber when some don't, red when the cache doesn't answer.

### Capture (packets, as .pcap)
- **tcpdump** on the probe's port with any filter (buttons for DHCP, ARP, broadcast, DNS, CS2, spanning tree, CDP/LLDP). Stop after N seconds or by hand.
- The capture goes into a **ring in RAM** (32 MB by default; the oldest packets drop off), not the SD card. **Download .pcap** at any time and open it in Wireshark; **Keep** copies it to the card.
- **Show packets live**: the last 80 packets, as tcpdump prints them, on the page.
- **Watchdog alarms capture themselves** (on by default): a rogue DHCP server, a broadcast storm, an IP conflict or ARP spoofing, a spanning tree change or a rogue IPv6 router starts a 30-second capture with a filter for that problem, kept on the card, so there's evidence of what happened while nobody was looking. At most one per kind every 10 minutes; the card keeps the last 30 (512 MB at most).

### Files (TFTP)
- Upload IOS images, configs or anything else (up to 512 MB each) from the page, with a progress bar; each file gets its **MD5** for `verify /md5`.
- A built-in **TFTP server** (UDP 69, off until you turn it on) serves them to switches, with the `blksize` and `tsize` options so a big image comes over quickly. The page shows the commands to paste (`copy tftp://<probe>/<file> flash:`, `verify /md5 …`, `copy running-config tftp://…`).
- **Allow writes** (off by default) lets a switch copy files to the probe, such as its running config.
- A list of recent transfers: which switch, which file, which way, how long, and whether it worked.
- Downloads stream from the card, so a big file doesn't fill the probe's memory.

### Configs (switch config backups and diffs)
- Pulls every switch's **running-config** (the switches on the Switches page, plus the probe's own switch) over **SSH**, or over the **console cable** for a switch with no network. Every 24 hours, on **Pull** / **Pull all now**, and a minute after Switch logs hears `%SYS-5-CONFIG_I` (someone changed the config).
- A version is kept only when the config **really changed** (timestamps and "Last configuration change" lines are ignored), with how many lines were added and removed. The last 100 per switch are kept.
- **Label** a version ("Before the event"); labelled versions are never thrown away. Tick two versions and **Compare** for a coloured diff. View or download any version.
- **Switch logins:** type a user, password and enable secret right on the Cable test form or above the Configs list: it's used for that run only, or saved for that switch with **Remember**. The saved logins (one shared by every switch, and any switch can have its own) are what scheduled pulls use. They're kept in `/etc/probe-deck/logins.json`, root-only; passwords never go back to the page.
- Configs hold password hashes and SNMP communities, so only **admins** can view, compare or download them; viewers see the list.

### Fairness (is one team, or one seat, worse off?)
- For a disputed round. Put each team's PCs in a box: IPs, ranges (`10.10.0.101-105`), names from Wake or Host scan, MACs, or a Wake group (`group:Team A`, or pick it from the list). Each box shows who it found as you type. Pick the game server from the CS2 tab, and press **Start**.
- For 2 minutes (30 s to 10 min) every PC is pinged twice a second. A PC that ignores ping (Windows' firewall does by default) is timed with **ARP** instead, which every PC answers (same subnet only). Ping and ARP times aren't comparable, so seats are only compared with seats timed the same way.
- The report: each team's median latency, jitter and worst loss side by side, the game server's ping and A2S times over the same minutes, and every seat with its **switch port** (from the Switches page). A seat whose latency or jitter is well above the rest, that loses packets or spikes, is flagged red with the reason.
- The verdict: **fair** (teams within 0.5 ms), **check** (a small difference, or a seat stands out) or **not fair**.
- It measures each seat's own path from the probe: cable, jack, switch port and network card, where seats differ. The path from the switches to the server is shared.
- The last 20 reports are kept, and each **downloads as text** for the match record. Anyone logged in can run one.

### Tick test (what a CS2 player feels)
- CS2 sends small UDP packets at a steady 64 or 128 a second; switches and routers often treat that differently from ping. The tick test sends the same traffic to a **reflector** and times every reply: **loss**, the **longest run of lost packets** (three in a row is a visible hitch), **late replies** that miss a tick, **jitter**, and packets **out of order** or **twice**, with a chart of every reply (lost ones in red) and a verdict for Counter-Strike: excellent, good, fair or bad, using PorkChopCheck's limits. **Stricter limits** switches to its "Claude recommended" set.
- The reflector's own delay is measured and taken out, so a busy reflector doesn't look like a slow network. The probe's own sending accuracy is shown too.
- **The probe is a reflector itself** (UDP 27777, on by default), so a PorkChopCheck box (Tools › Tick test › Server = the probe's address) or another probe can test the path to it. It answers only test packets, one same-size reply each, so it can't amplify traffic. Or run `porkchopcheck --reflector` on the game server or a spare PC. It's PorkChopCheck's code (`pc_net.py`), so the two work together.

### Wake (Wake-on-LAN)
- Save machines with a name, MAC, optional IP and a **group** (a row of seats). Add them by hand, **import** a list (`name, MAC, IP, group` or a CSV), or pick from **Seen on the network** (MACs the watchdog heard in ARP).
- **Wake all**, wake a group, or wake the ones you tick. Machines wake **2 s apart** by default, so 30 PCs don't all draw power in the same second.
- An **up/down light** for each machine (ping), and the **boot time**: seconds from the magic packet to the first ping reply. With no IP saved, the watchdog's ARP table supplies one.
- Magic packets go to 255.255.255.255 and the subnet broadcast on UDP 9 and 7. For another VLAN, give the machine that VLAN's broadcast address (the switch needs `ip directed-broadcast` on that VLAN's interface).

### Switches (SNMP): the GXL-LANapp look
The Switches tab is the same page as GXL-LANapp's Switches tab (ported from its React code and its server's switch poller), so Probe Deck can watch a network on its own.
- **Hall readouts:** needle meters for the whole network's download and upload, and for the busiest uplink and busiest PC port (named on a split-flap board); a phosphor **bandwidth monitor** with each switch's uplink trace; **network stats** (broadcasts and multicasts per second from PCs, errors and discards per minute, and counts: switches up, ports up, PCs, neighbors, VLANs, ports in trouble, flapping, slow or half duplex).
- **Each switch:** CPU, memory, temperature and PoE as LED bars, ports up, uplink traffic and a trace; a lamp per port (green, amber, red; purple outline to a known switch, orange to an unknown uplink, amber ring for the probe's own port); and **backlit VU meters** for its five busiest ports. Drag the ⠿ handle to reorder.
- **Ports (drill-down):** big CPU, memory, temperature and PoE needles; the **front panel** (odd ports on top, even below, uplink modules on their own) with speed, load and neighbor on each port; the **CDP/LLDP neighbor** table; and, for the port you click, in and out needles, LED bars and everything known: link, busy %, link-up time, flaps, CRC and other errors, discards, collisions, VLAN, MAC addresses, neighbor.
- **Event gear:** mark a port as the **event firewall** or **LAN cache** (hazard stripes; left out of the busiest-port and PC counts). Once a port is marked, GXL's **FIREWALL** (internet in/out) and **LAN CACHE** (served/fill) monitors pop up at the top of the tab, with seven-segment readouts and a half-hour trace in the same colours.
- **Err-disabled ports** (from the switch poll's err-disable table and the Watchdog's switch alarms) are striped red with **ERR** on the port lamp and the front panel. Click one for the cause, and admins get **Restore port…**: the probe logs in over SSH (the login saved for that switch, or one typed in the box) and runs `shutdown` / `no shutdown` on it, then shows what `show interfaces <port> status` says. If the cause is still there (the same device plugged in), the switch shuts it again, and the page says so.
- **Environment** table: CPU (5 s, 1 min, 5 min), memory, every temperature sensor against its limit, fans, power supplies, PoE. A switch that stops answering shows **DOWN** with why (what its neighbor switch sees on the link).
- Ports are graded by PorkChopCheck's rules (link changes, half duplex, slow links, errors, drops, busy). Read every 20 s; the MAC tables, neighbors and VLANs every minute.
- Set **one read-only community** (v1 or v2c) and add switches by IP, or from **found but not added** (CDP neighbors of your switches) and the subnet **Scan**. An **AireOS wireless controller** shows its access points and clients (its community is set on the controller: Management → SNMP → Communities).
- The community is kept on the probe (`/etc/probe-deck/config.json` and `devices.json`, root-only) and never sent to the page. Viewers see everything, including the drill-down; admins add, remove, reorder and mark ports.

### Firewall (how is the firewall itself doing?)
Network › Firewall reads the LAN's firewall over **SNMP v2c, read-only**, every 5 s. Turn on the firewall's SNMP service with the Host Resources, UCD and PF modules, allow the probe, and type its address and community (blank uses the Switches community).
- **Needles:** CPU, memory, the **state table** (states in use against the limit), the busiest interface's download, and the CPU temperature when it's sent (below).
- **Readouts:** uptime, load, new states a second, packets blocked a second, and packets **dropped for lack of states or memory** in the last 10 minutes (red when any). A full state table makes new connections fail even with the CPU idle, so it's the number to watch at an event.
- **CPU:** each core, busy now, memory, cache and swap. **State table:** use, new states and lookups a second, and the drop and bad-packet counters.
- **Interfaces:** every port in use with its name, link, speed, traffic now and since boot, errors, drops, and packets the firewall blocked coming in. Unused ports are hidden.
- **Charts** for the last hour: CPU and memory, states, traffic per interface, temperature.
- The sidebar light is amber at 80% CPU, 85% memory, 70% of the state limit or 75 °C, and red at 95%, 95%, 90%, 90 °C, when packets are dropped, or when the firewall stops answering.
- **Temperature:** the firewall's SNMP has none, so it sends its own. Tick **Receive the temperature** and add the command the page shows as a cron job on the firewall (every minute, as root): it reads the temperature sensors and sends one small UDP packet to the probe on **UDP 47475**. The probe only takes it from the firewall's own addresses (read over SNMP) and lists anything else it ignored. A reading older than 3 minutes is shown as stopped. To stop it, delete the cron job; unticking the box closes the port.

### CS2 (is this network fit for a match?)
Everything about the network that decides a Counter-Strike match, measured from the probe's switch port:
- **Game servers**: list them (`Name = host:port`) or pick one the **LAN scan** found (the same broadcast the game's server browser sends). For each: **ping** (ICMP, once a second) and **server reply** (the game's own A2S query over UDP, once a second, so it includes the server answering), as needle meters and a 5-minute chart; the server's game, **map** (split-flap), player count and the **players' names and scores**.
- **Perf-Deck nodes**: every PC running Perf-Deck (found by the same discovery as the Stations list, UDP 52010) gets the same tests and its own card below the servers, probe ↔ node: **ping** (ICMP) and **app reply** (Perf-Deck's UDP ping/pong, answered by the app), once a second, plus MTU and the switch path. No game server needed, so you can check the LAN at each table before the match. They count toward the verdict; switch them off in the settings (the section then shows a striped "Node tests are off" box with a **Turn on** button). A node that misses about 3 s of pings and app replies goes **dark** at once (red light, **no reply**, meters off) instead of showing the last minute's numbers; one that stops sending discovery for 12 s is marked **gone** and leaves the page after 2 minutes.
- **Stats** over 1 and 10 minutes: average, best, p95, p99, worst, **jitter** (also in CS2 ticks: 15.6 ms at 64 tick), **loss**, **spikes**, and **latency during a speed test** (bufferbloat: run a test on the Probe tab and watch it).
- **MTU**: whether full-size packets reach the server without fragmenting.
- **Switch path** to the server (from Trace), with each port's load, errors and health.
- **Player PCs**: every machine saved on the Wake tab (or one group, e.g. a team), pinged every 2 seconds.
- **Lag events**: spanning tree changes, broadcast storms, IP conflicts, rogue DHCP, the probe's link dropping, latency spikes and loss bursts, on one timeline.
- **Steam**: connect time to Steam's API (servers with online auth need it).
- A **verdict** at the top: **MATCH READY**, **CHECK** or **NOT READY**, with the reasons. LAN limits: ping 2 ms (bad at 5), jitter 1 ms (bad at 3), loss 0.1% (bad at 1%).
- The server query code is GameLAN-Visualizer's (`gamelan/`, copied unchanged).

### Trace (where is this host plugged in?)
- Type an **IP, MAC or host name**. The probe pings it (so the switches' MAC tables are fresh), finds its MAC (its own ARP table, the Watchdog, the switches' ARP tables, or the wireless controller), then asks every switch's MAC table, VLAN by VLAN (`community@vlan`), where it was learned.
- The **end** is the port where the MAC was learned and the CDP neighbor isn't another managed switch. The **path** from the probe to it follows the CDP links between your switches.
- **Wireless clients:** the controller gives the access point, band, SSID, signal (RSSI) and SNR, and the AP is traced to its switch port by CDP. The path ends: switch → access point → radio → client.
- The end-port card shows the port's link, VLAN, errors and description, and warns when **several MACs** share the port (an unmanaged switch, hub or VM host behind it), when the port has **errors**, or when it's at **half duplex**.
- A live log shows each step. The last 20 traces are kept. Viewers can trace too. On the Wake tab, every machine has a **Trace** button.
- Limits: a host behind a switch that isn't managed (or doesn't speak CDP) can be traced only to the port that switch is on. The MAC must have been seen in the last ~5 minutes.

### System stats (how is the probe itself doing?)
- **Needle meters** for CPU, CPU temperature, memory, SD card and eth0 traffic, then uptime, load, process count and whether the clock is in sync (NTP).
- **CPU:** every core's load and clock, the clock range and governor, and whether it's slowing down for heat.
- **Heat and cooling:** each temperature with its trip points (it slows down at 70 °C, shuts off at 95 °C), the cooling steps, and the fan (the Zero 3E has none, so a warm room or a closed box shows here first).
- **Memory, swap and storage:** a memory bar (in use, shared, cache), swap and zram if any, and the SD card's name, size, date and how full it is.
- **Interfaces:** every network interface with its state, speed, rates, errors and drops.
- **Charts** of the last 10 minutes, and the **top processes** (tracked only while the page is open, so it costs nothing otherwise).

### Power
The **Settings & power** page (System in the sidebar, admins only) holds the probe's settings and shuts the probe down or restarts it, behind the same slide-to-ARM latch as the Tailscale switch. Shut down before pulling the PoE cable: the SD card is safe once the green light on the board stops blinking (about 15 s). After a restart the page waits and reloads by itself.

### Tailscale (remote access, optional)
Install it once on the probe: `curl -fsSL https://tailscale.com/install.sh | sh`. The **Tailscale tab** does the rest:
- **Live map** (dark US map, top of the tab): every device on the tailnet at its **nearest Tailscale relay city** (Tailscale doesn't hand out devices' addresses), or, with the opt-in **city-level locations** switch, at the city of its public IP (the probe's own, and devices with a direct connection; looked up at ip-api.com). Arcs from the probe to each online device, green when direct and magenta through the relay city; amber and cyan pulses fly along them whenever the probe sends or receives Tailscale traffic. It shows the probe's own traffic only: a tailnet device can't see traffic between two others.
- **Status**: on/off, the probe's Tailscale IP and MagicDNS name, the tailnet, the home relay, Tailscale's own health warnings, and every device on the tailnet (online, direct or relayed, last seen, traffic).
- **On / off** with a guarded switch (slide the latch to ARM, then press the lit button; it disarms after 8 s), like `tailscale up` / `down` (off keeps the login), **log in** (the login link shows on the page) and **log out**, and a switch that stops the `tailscaled` service and keeps it off after reboots.
- **Headscale**: add your Headscale server (its URL, plus a pre-auth key or a login link) and it's saved as its own **connection** next to your tailnet. The Connections list switches between them in one click (Tailscale's profiles, like `tailscale switch`), each keeping its own login, and removes ones you don't need.
- **Settings**: the device name, use the tailnet's DNS, accept routes, **share LAN subnets** (subnet router, with a "Use this LAN" button), **offer as an exit node** (IP forwarding is turned on for you; approve both in the Tailscale admin console), Tailscale SSH, shields up, automatic updates.
- **Add the Tailscale name and IP to the HTTPS certificate** with one button, so `https://probe-01.<your-tailnet>.ts.net/` matches the certificate.
- Viewers can see it; only admins change it. Turning Tailscale off while you're on the page through Tailscale asks first.
- It talks to `tailscaled`'s local API on its unix socket, the same way the `tailscale` command does.

### HTTPS
- The page is on **https://** (port 443). On first start the probe makes a **self-signed** certificate (ECDSA P-256, 10 years) for its host name, `<host>.local` and its IP. Plain **http://** visits move to HTTPS, except `GET /api/probe`, which stays on HTTP because the T-Displays poll it (an ESP32 can't check a self-signed certificate). The redirect can be turned off.
- The **HTTPS tab** shows the certificate (names, expiry, key, SHA-256 fingerprint), warns when it doesn't cover the probe's current IP (after a move to another VLAN), **regenerates** it with the names you give, and downloads it so you can trust it on your PCs and stop the browser warning.
- The certificate is plain PEM in `/etc/probe-deck/tls/` (`cert.pem`, `key.pem`), so one signed by your own root CA can replace it.
- The login cookie is marked `Secure` over HTTPS. The login screen warns when the page is on plain HTTP.

### Users and login
- **Everything needs a login**, except `GET /api/probe` (status for the T-Displays). Perf-Deck discovery and the UDP broadcasts are unchanged.
- **First visit:** with no users yet, the page asks you to create the **admin** account. A probe that had the old console password (0.4–0.5) asks for it once, to prove the probe is yours.
- **Roles:**
  - **admin**: everything, including users, the certificate, the console and the telnet console.
  - **viewer**: every tab, live. Can watch the console and download its logs, and change their own password. Buttons that change things are hidden, and the server refuses them anyway.
- **Users tab:** add users, change roles, reset passwords, delete. The last admin can't be demoted or deleted, and you can't delete yourself.
- Passwords are salted PBKDF2 hashes in `/etc/probe-deck/users.json`. Logins last 30 days and survive a restart. 10 wrong passwords from one address in 5 minutes lock that address out for 5 minutes.
- **Locked out?** Over SSH on the probe: `python3 /opt/probe-deck/auth.py passwd <name>`, `add <name> admin`, `del <name>` or `list`. The running page picks the change up by itself, and a reset logs that user out. The rest of the page has none, like Perf-Deck, so only use it on networks you trust.

## Hardware

<p align="center"><img src="docs/hardware/00-probe.jpg" width="460" alt="probe-01: the Radxa ZERO 3E and PoE HAT in a blue and black 3D-printed case"></p>

| Part | Notes |
| --- | --- |
| **Radxa ZERO 3E** (2 GB is plenty) | RK3566, gigabit Ethernet, 65 × 30 mm. The **3E** has Ethernet; the 3W does not. |
| **Radxa ZERO 3E PoE HAT** | IEEE 802.3af. Comes with 4 standoffs and 8 screws, and usually arrives with the standoffs **already screwed onto the HAT**. **The standoffs and screws carry the power**: PoE does not work without them, so tighten them, and keep metal away from them. |
| **microSD card**, 16–32 GB | The board has no built-in storage. A high-endurance card lasts longer. |
| Fan (optional) | A 20 mm 5 V fan under the HAT, on the HAT's fan plug (as in the photos). Worth it in a closed case. |
| **USB console cable** (for the Console tab) | USB-C on the probe to the switch's RJ45 console port. Any FTDI, Prolific, CH340 or CP210x cable works. |
| Case (optional) | 3D printed, from two Printables models. **You may need both:** print the bottom from [Case for Radxa 3E with PoE HAT](https://www.printables.com/model/848702-case-for-radxa-3e-with-poe-hat), and the lid from [Lid: case Radxa 3E with PoE HAT](https://www.printables.com/model/1572977-lid-case-radxa-3e-with-poe-hat/files). The newer lid is taller: the original lid can be too tight over v1.2 of the HAT and its parts. |

### Putting it together

Radxa's [ZERO 3E PoE HAT guide](https://docs.radxa.com/en/zero/zero3/accessories/3e-poe-hat) has official diagrams. These are photos of probe-01 being built. Steps 1–3 show the parts; step 4 is the first thing you might do, and most HATs arrive with it already done.

<table>
<tr>
<td width="50%"><img src="docs/hardware/01-parts.jpg" alt="All the parts: PoE HAT, ZERO 3E, microSD card, standoffs and screws, both case halves, patch cable and USB console cable"></td>
<td width="50%"><img src="docs/hardware/02-board.jpg" alt="The Radxa ZERO 3E from the top: RK3566, RAM, USB-C ports, micro HDMI, 40-pin header, gigabit Ethernet and mounting holes"></td>
</tr>
<tr>
<td><b>1. The parts.</b> Board, PoE HAT, microSD card, 4 standoffs and 8 screws, the case, a patch cable and (for the Console tab) a USB console cable.</td>
<td><b>2. The board.</b> PoE power comes in through the Ethernet jack. The HAT plugs onto the 40-pin header, and the standoffs go in the four corner holes.</td>
</tr>
<tr>
<td><img src="docs/hardware/03-hat-top.jpg" alt="The PoE HAT from the top: PoE converter, 40-pin pass-through and the POE12 and POE36 holes"></td>
<td><img src="docs/hardware/04-hat-under.jpg" alt="The PoE HAT from underneath with four standoffs screwed in, the 40-pin socket and an optional 20 mm fan"></td>
</tr>
<tr>
<td><b>3. The HAT.</b> The holes marked <b>POE12</b> and <b>POE36</b> are how the power reaches the HAT: through the standoffs. That's why they must be tight.</td>
<td><b>4. Standoffs on the HAT (if needed).</b> The HAT usually arrives with these already on: if yours did, skip to step 5. If not, screw the four standoffs into the underside of the HAT. Fit the fan here now if you're using one, and plug it into the HAT's fan connector.</td>
</tr>
<tr>
<td><img src="docs/hardware/05-stack.jpg" alt="The HAT stacked on the board with the standoffs between them, sitting in the case bottom"></td>
<td><img src="docs/hardware/06-sd-screws.jpg" alt="The underside of the board: the microSD card in its slot and screws into the four standoffs"></td>
</tr>
<tr>
<td><b>5. Stack them.</b> Press the HAT's socket straight down onto the board's 40-pin header, so the standoffs land on the board's corner holes.</td>
<td><b>6. Screws and microSD.</b> Turn it over and screw through the board into all four standoffs. Tighten them. Slide the flashed microSD card into the slot under the board.</td>
</tr>
<tr>
<td><img src="docs/hardware/07-case.jpg" alt="The assembled probe in the black case bottom, with the Ethernet jack in its opening"></td>
<td><img src="docs/hardware/08-in-use.jpg" alt="The finished probe on a Cisco Catalyst 3560-CX, with a PoE patch cable and a USB console cable to the switch's console port"></td>
</tr>
<tr>
<td><b>7. Into the case.</b> Drop it into the case bottom with the Ethernet jack in its opening, then put the lid on.</td>
<td><b>8. On the job.</b> One patch cable from a PoE port powers it and connects it. The console cable runs from the probe's USB-C to the switch's console port.</td>
</tr>
</table>

If it doesn't power up, check that the switch port gives PoE (802.3af) and that all eight screws are tight.

At idle the board sits around 45 °C with no case. Gigabit tests load the CPU, so add a small fan if it goes in a closed case.

The photos are made by `tools/annotate_photos.py` from the originals. It keeps only the pixels (no location or camera data) and blurs the serial-number stickers.

## Run it on a server (Proxmox container)

Most of Probe Deck doesn't need to sit on a jack: Switches, Configs, Switch logs, Cable test, Host scan, Traceroute, DNS test, Speed test, Capture, the Console and Switch intake all work from a server at the site. One command on the **Proxmox host's shell** makes an Ubuntu container (the newest Ubuntu template Proxmox offers) with Probe Deck running in it:

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/turkey415/probe-deck/main/tools/proxmox-ct.sh)"
```

It asks for the container ID, name, bridge, VLAN tag, address and storage (Enter takes the default), downloads the template, creates a small unprivileged container (1 core, 1 GB, 8 GB disk), installs Probe Deck and prints the address. Open it and make the admin account. The script stays on the host as `probe-deck-ct`:

- `probe-deck-ct usb <ctid>`: pass a USB console cable plugged into the host into the container (Proxmox VE 8.1 or newer), for Console and Switch intake. Restart the container after replugging the cable.
- `probe-deck-ct update <ctid>`: update Probe Deck inside it (settings, users and history are kept).

On any other Ubuntu or Debian box (a VM, a mini PC), run `tools/install-server.sh` as root instead.

**Server mode** turns on by itself in a VM or container (or with `--mode server`). Pages that only make sense on a probe at a jack stay in the sidebar but are greyed out with the reason: **VLAN walk** (needs a trunk and tagged interfaces of its own), **Jack survey** (moves seat to seat) and **Renew DHCP** (the host handles it). The "This jack" card shows the host's CDP neighbour, if the bridge passes CDP.

## Build one

### 1. Make the SD card image (on a Linux PC, no root needed)

```
tools/prepare-image.sh probe-02            # hostname; uses ~/.ssh/id_ed25519.pub and this PC's time zone
BOARD=rpi tools/prepare-image.sh probe-02  # the same for a Raspberry Pi 3/4/5
```

The script downloads **DietPi (Debian 13 "Trixie") for the Radxa ZERO 3** (or for the Raspberry Pi with `BOARD=rpi`) and checks its SHA-256. It then writes the first-boot settings into the image's FAT setup partition with `mtools` (`DIETPISETUP` on the Radxa, the boot partition on a Pi):
- the hostname and your time zone
- **OpenSSH** with your key, and a random root password (saved next to the image)
- a fully automated first boot, with the survey turned off
- the probe's packages: `iperf3 lldpd tcpdump ethtool python3 avahi-daemon dnsutils curl jq`

It prints the `dd` command to write the card. **Check the device name with `lsblk` first.**

```
sudo dd if=build/probe-02.img of=/dev/sdX bs=4M conv=fsync status=progress
```

Needs `curl xz mtools sfdisk`.

### 2. First boot

Fit the PoE HAT with the standoffs and screws, insert the card, and plug it into a PoE switch port with internet access. You don't need a screen or keyboard. First boot updates Debian and installs the packages, which takes about 5–15 minutes. Then the probe answers at `probe-02.local`.

DietPi swaps its temporary SSH server for OpenSSH during setup, so SSH warns once that the host key changed. That is expected: run `ssh-keygen -R <ip>`.

### 3. Install Probe Deck

```
./install.sh root@probe-02.local
```

This copies the Python files, `index.html` and `vendor/` (xterm.js) to `/opt/probe-deck` and installs and starts the `probe-deck` systemd service. It also turns on CDP in lldpd (Cisco switches send CDP, not LLDP, by default). Run it again after any change to update the probe.

Open **https://probe-02.local/** and accept the self-signed certificate (or trust it: see the HTTPS tab).

## How it works

- **`server.py`**: Python 3, standard library only (plus `python3-serial` for the console). It runs as root because it reads CDP via `lldpcli`, renews DHCP with `ifdown`/`ifup`, and serves on port 80. Background threads:
  - a link watcher (every 1 s)
  - pings (every 2 s)
  - DNS, internet and neighbour checks (every 30 s, and right after a link change)
  - an iperf3 server keeper
  - Perf-Deck discovery and the probe broadcast (every 3 s)
- **`console.py`**: one reader owns the serial port. Output goes to a scrollback buffer, the session log and every attached web terminal or telnet client. **`ws.py`** is a small WebSocket server for the web terminal.
- **`watchdog.py`**: a raw packet socket with a BPF filter (broadcast/multicast only), plus the DHCP scan.
- **`wake.py`**: saved machines, magic packets and the ping checks.
- **`index.html`**: one file. It polls `/api/state` every second (twice a second during a test). The console loads `vendor/xterm.js` the first time you open it.
- **`netmap.py`**: devices, the poller, discovery and the trace. **`gxl_switch.py`** reads a switch the way GXL-LANapp's server does (forwarding table, CDP/LLDP, VLANs, environment, rates, history). **`pc_snmp.py`** is the SNMP v1/v2c client and port grading, copied unchanged from PorkChopCheck.
- **Add-on pages** plug in through **`appbase.py`** (`/api/x/<name>`, settings and data folders, downloads): **`inet.py`** (internet speed test), **`cable.py`**, **`vlanwalk.py`**, **`hosts.py`**, **`logs.py`** (syslog), **`capture.py`**, **`survey.py`**, **`cache.py`**, **`configs.py`** and **`tftp.py`** (Files). Their page code is in **`web/<name>.js`**. **`swcli.py`** logs in to a switch's CLI over SSH (paramiko) or the console cable, for Configs and Cable test. **`intake.py`** watches a boot on the console cable (Switch intake) and preloads configs through `swcli.py`.
- **From PorkChopCheck:** **`pc_net.py`** (frame decoding, the tick test and reflector, the mDNS browser, the multicast tracker and the wire report) and **`pc_smart.py`** (smart-device discovery) are copied from [PorkChopCheck](https://github.com/turkey415/PorkChopCheck) unchanged, like `pc_snmp.py`, so a fix in one can be copied to the other. Around them: **`wire.py`** (one listener feeding the wire report, Port details, mDNS and Multicast), **`online.py`** (time to online, live or from the boot journal), **`tick.py`** (the tick test page and the always-on reflector) and **`mtr.py`** (the traceroute, raw ICMP).
- **`auth.py`**: users, roles and sessions (and the `passwd`/`add`/`del` command for SSH).
- **`tls.py`**: makes the certificate with `openssl` and holds the SSL context. The HTTPS server does each handshake in the request's thread and picks up a new certificate on the next connection.
- **Files on the probe:** settings in `/etc/probe-deck/config.json`, the certificate in `/etc/probe-deck/tls/`; in `/var/lib/probe-deck`: test history `history.json`, watchdog events `events.json`, saved machines `wol.json`, console logs `console/`.

### API

| Method | Path | Does |
| --- | --- | --- |
| GET | `/api/state` | Everything the page shows |
| GET | `/api/probe` | The probe message (same as the UDP 47474 broadcast) |
| POST | `/api/settings` | Save settings (any subset of the keys in `config.json`) |
| POST | `/api/speed/start` | `{server, mode: both\|down\|up, port?, secs?}` |
| POST | `/api/speed/stop` | Stop the running test |
| POST | `/api/sweep/start`, `/api/sweep/stop` | Sweep every station |
| POST | `/api/recheck` | Run the checks now |
| POST | `/api/renew` | Drop the address and renew DHCP |
| POST | `/api/history/clear` | Clear the speed test history |
| GET | `/api/auth` | `{setup, legacy, user}`: is setup needed, who am I |
| POST | `/api/auth/setup`, `/login`, `/logout`, `/password` | First admin; log in (`{name, password}`); log out; change my password |
| GET | `/api/users` | Users (admin) |
| POST | `/api/users/add`, `/update`, `/delete` | `{name, role, password}` (admin) |
| GET | `/api/console` | Console status, ports, macros and logs |
| WS | `/ws/console` | The terminal: binary frames are serial output; JSON `{t: in\|paste\|paste_stop\|break\|macro\|clear}` |
| POST | `/api/console/settings` | Port, baud, paste delay, telnet, macros |
| GET | `/api/console/log/<name>` | Download a session log |
| GET | `/api/net` | Switches, controllers, ports, discovered devices |
| POST | `/api/net/settings` | `{community?, version}` (admin) |
| POST | `/api/net/add`, `/remove`, `/scan`, `/refresh` | `{host, name?, community?}`, `{id}` (admin) |
| POST | `/api/net/order`, `/api/net/role` | `{ids}`; `{id, port, role: firewall\|cache\|null}` (admin) |
| POST | `/api/trace` | `{q: IP, MAC or name}` → `{id}` (any user) |
| POST | `/api/system/power` | `{action: poweroff\|reboot}`: shut down or restart the probe (admin) |
| GET | `/api/tailscale` | Tailscale status, settings and devices |
| POST | `/api/tailscale` | `{action: up\|down\|login\|logout\|service\|settings\|cert\|add_server\|switch\|remove, ...}` (admin) |
| GET | `/api/cs` | The CS2 tab: servers, Perf-Deck nodes, stats, path, player PCs, lag events, verdict |
| GET / POST | `/api/x/<page>[/…]` | The add-on pages (`speed`, `cable`, `vlans`, `hosts`, `logs`, `capture`, `survey`, `cache`, `configs`, `files`, `cli`, `tick`, `mtr`, `mdns`, `mcast`, `wire`, `online`): each is a module built on `appbase.App`, with its settings in `/etc/probe-deck/<page>.json` and its data in `/var/lib/probe-deck/<page>/` |
| POST | `/api/cs/scan` | Scan the LAN for game servers now (any user) |
| GET | `/api/trace/<id>`, `/api/traces` | A trace's steps and result; recent traces |
| GET | `/api/tls`, `/api/tls/cert.pem` | Certificate details; download the certificate |
| POST | `/api/tls/generate` | `{names: "probe-01, probe-01.local, 192.168.1.50", days?}`: new self-signed certificate (admin) |
| POST | `/api/tls/settings` | `{https_redirect}` (admin) |
| POST | `/api/watch/trust` | `{value: IP or MAC, remove?}` |
| POST | `/api/watch/scan`, `/api/watch/clear` | DHCP scan now; clear the events |
| POST | `/api/wol/save`, `/delete`, `/import` | Saved machines |
| POST | `/api/wol/wake` | `{ids?: [...]}` (none = all), or `{mac}` for a one-off; `/api/wol/stop` |

Every call needs a login cookie except `/api/probe` and `/api/auth*`. Every POST needs an admin, except `/api/auth/password`. Cross-site POSTs and WebSockets are refused (an `Origin` header must match `Host`).

To try it on a PC without a cable or root: `socat -d pty,raw,echo=0,link=/tmp/ttyPD pty,raw,echo=0,link=/tmp/ttySW`, then `PROBE_DECK_TEST_SERIAL=/tmp/ttyPD python3 server.py --port 8580 --iface <your NIC> --data /tmp/pd --conf /tmp/pd/config.json` and talk to `/tmp/ttySW`. The watchdog needs root.

### Ports

| Port | Use |
| --- | --- |
| TCP 443 | The page (HTTPS) |
| TCP 80 | Moves to HTTPS; `/api/probe` for the displays |
| TCP 5201 | iperf3 server |
| UDP 52010 | Perf-Deck discovery (broadcast hello, plus ping/pong for station latency) |
| UDP 47474 | Probe message and `probe-alert` broadcasts |
| UDP 47475 | Firewall temperature from its cron job (received, off by default) |
| TCP 2001 | Console over telnet (off by default) |
| UDP 9, 7 | Wake-on-LAN magic packets (sent) |
| UDP 67/68 | DHCP scan (sent: a Discover every 60 s) |
| UDP 161 | SNMP to your switches, controller and firewall (sent) |
| UDP 5353 | mDNS: `<host>.local` and `_probe-deck._tcp` (avahi) |

## Related

- [iPerf Deck](https://github.com/turkey415/Perf-Deck): the desktop speed-test station this probe joins.
- [t-display-progs](https://github.com/turkey415/t-display-progs): T-Display-S3 and CYD firmware. Its **Probe Viewer** app shows this probe's results.
- [PorkChopCheck](https://github.com/turkey415/PorkChopCheck): LAN party network checks.
