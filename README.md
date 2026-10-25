# Probe Deck

A pocket gigabit **network probe** with its own web page. Plug it into any switch port and it is powered by PoE. Within a few seconds the page shows what is on the other end of the cable: which switch and port, which VLAN, whether DHCP, DNS and the internet work, and how fast the port really is.

Every page is in the **sidebar**, grouped (Port, Network, Watch, Event, Switch tools, System), under a **This jack** card that always shows the port, switch, VLAN and link lights. Besides the Overview, the pages are:
- **Console**: plug a USB console cable into a switch and use its console from the web page (or over telnet).
- **Watchdog**: catches rogue DHCP servers, IP conflicts, gateway spoofing, broadcast storms, spanning tree changes and rogue IPv6 routers.
- **Wake**: Wake-on-LAN for every machine in the room, with a staggered "wake all".
- **HTTPS**: the page is served over HTTPS with a certificate the probe makes itself, and this tab shows it and regenerates it.
- **Switches**: GXL-LANapp's Switches page: hall gauges, a bandwidth monitor, firewall and LAN cache monitors, every switch's ports and busiest ports, and a drill-down into each port, over SNMP.
- **Trace**: type an IP, MAC or host name and see the path from the probe, switch by switch, to the port (or access point) it's on.
- **Users**: everyone logs in. **Admins** can change anything; **viewers** see everything but change nothing.

It is built on a **Radxa ZERO 3E** with its PoE HAT, and the page has the same look as [iPerf Deck](https://github.com/turkey415/Perf-Deck). The probe joins Perf-Deck's network of stations, so the two can test each other.

![Probe Deck](docs/screenshot.png)

<table>
<tr><td width="50%"><b>Switches</b><br><img src="docs/switches.png" alt="Switches tab: hall gauges, firewall and LAN cache monitors, bandwidth monitor and network stats"></td><td width="50%"><b>Ports</b><br><img src="docs/ports.png" alt="The Ports drill-down: environment needles, front panel and the CDP neighbor table"></td></tr>
<tr><td width="50%"><b>Trace</b><br><img src="docs/trace.png" alt="A host trace: the path from probe-01 through four switches to an access point"></td><td width="50%"><b>Console</b><br><img src="docs/console.png" alt="The green CRT-style web console on a Cisco 3560"></td></tr>
<tr><td width="50%"><b>Watchdog</b><br><img src="docs/watchdog.png" alt="Watchdog: DHCP servers, broadcasts per second, spanning tree"></td><td width="50%"><b>Wake</b><br><img src="docs/wake.png" alt="Wake-on-LAN: saved machines with up/down lights"></td></tr>
<tr><td width="50%"><b>HTTPS</b><br><img src="docs/https.png" alt="The HTTPS tab: the certificate, its expiry and fingerprint"></td><td width="50%"><b>Users</b><br><img src="docs/users.png" alt="The Users tab: accounts and roles"></td></tr>
<tr><td width="50%"><b>Log in</b><br><img src="docs/login.png" alt="The login screen"></td><td></td></tr>
</table>

## Features

### The sidebar
- A **This jack** card at the top: the switch port in big amber letters, the switch, VLAN and link speed, lights for link, DHCP, DNS and internet, and how many Perf-Deck stations are heard. It's on every page; click it for the Overview.
- Pages are grouped in **modules**: Port, Network, Watch, Event, Switch tools and System. Click a module's heading to fold it; the page remembers which are folded. Each module has a light showing the worst state inside it (red when the Watchdog has a problem or a switch port is in trouble, even while folded).
- On a phone the sidebar slides out from the ☰ button.

### Port: what is this jack?
- **Switch, port and VLAN** from **CDP** (Cisco) or **LLDP** (everyone else), e.g. `Gi0/7` on `access-1`. Also shows the switch model, IOS version and management IP.
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
- An **internet speed test** against Cloudflare (speed.cloudflare.com): download and upload for 3–30 s each way over 1–8 streams, with live readouts and a chart. It also pings 1.1.1.1 before the test (**idle latency** and jitter) and while it runs (**loaded latency**): when latency climbs under load, the uplink has bufferbloat. It shows the public IP, the network (AS number) and the Cloudflare data centre. At gigabit one test moves 1–2 GB, so it only runs when you press the button. Cloudflare limits how often one public IP can test; the page says so when that happens.
- An **iperf3** client against any server, as download, upload or both, with live seven-segment readouts and a live chart.
- **History** of every test, with the switch port it ran on.
- An **iperf3 server** on port 5201, so other machines can test the probe.

### Stations: Perf-Deck on the LAN
- The probe speaks **Perf-Deck discovery** (UDP 52010). It shows up as a station in every Perf-Deck on the network segment, and it lists every Perf-Deck station it hears.
- Each station shows a live **ping**, a ready/busy light, and its last result.
- **Send / Receive / Both** buttons run a quick 5-second test against one station.
- **Sweep all** runs a 2-second test from every station to the probe in turn. It follows Perf-Deck's rules: it waits while another station is sweeping, and every station rests 5 minutes after any sweep.

### Probe message (for the displays)
- The probe announces itself over **mDNS** as `_probe-deck._tcp`, so displays can find it by name and poll `GET /api/probe`. That works even where the Wi-Fi drops broadcasts.
- Every ~9 s the probe broadcasts a small JSON summary on **UDP 47474**: switch and port, VLAN, link, IP, gateway ping, DNS and internet status, and the last speed test. This is the shared probe message from [t-display-progs](https://github.com/turkey415/t-display-progs) (`TODO.md`) for the T-Display and CYD **Probe Viewer** app, and for PorkChopCheck. `GET /api/probe` returns the same message.

### Settings and system
- Probe name, ping targets, check interval, DNS test name, internet check URL, and on/off switches for the iperf3 server, Perf-Deck discovery, the broadcast, and DHCP-on-replug. Settings are saved from the page.
- CPU temperature, load, memory and uptime, plus **Check now** and **Renew DHCP** buttons.
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

### Watchdog (what is wrong with this LAN?)
A kernel filter passes only broadcast and multicast frames to the watchdog, so speed tests don't load it.
- **Rogue DHCP**: every 60 s it sends a DHCP Discover (with the broadcast flag, and it never takes the lease) and lists every server that answers, with the address, router and DNS it offers. Servers other than the probe's own lease server are **rogue** until you press **Trust**. Offers heard on the wire count too.
- **IP conflicts** from ARP: one IP claimed by two MACs, someone else using the probe's address, and anyone **claiming to be the gateway** (ARP spoofing).
- **Broadcast storms**: broadcasts per second with a chart, an alarm over a limit (1000/s by default) for 3 s, and the **top broadcast senders**, to find the loop or the chatty PC.
- **Spanning tree**: root bridge, protocol, **topology changes** and root changes, from the BPDUs the switch sends.
- **IPv6 router advertisements**: a PC sharing its connection (Windows ICS) or a rogue router.
- **DTP**: says when the port could be talked into a trunk.
- An **event list** of everything above. Warnings also go out at once on UDP 47474 as an `app: "probe-alert"` message (the displays ignore it; PorkChopCheck can use it), and the probe message gets an `alerts` count.

### Host scan (who is on this subnet?)
- Sends one small packet to every address on the probe's subnet (up to a /22), then reads the probe's ARP table: anything that answered ARP is a host, **even with its firewall up**. Hosts the Watchdog heard in the last 10 minutes count too.
- For each host: IP, MAC and **maker** (from the IEEE lists, so `Cisco`, `Ubiquiti`, `Raspberry Pi`…; phones with a private MAC say so), **names** from reverse DNS, NetBIOS (Windows PCs) and mDNS, and which of a few **TCP ports** are open (22, 80, 443, 445, 3389, 5201, 8080, 9100, 27015 by default; change the list).
- Names the probe already knows are shown first: Wake machines, Perf-Deck stations, switches from the Switches page, the gateway.
- Hosts are remembered by MAC with first and last seen; machines that turn up after the first scan are marked **new**. Filter by anything; each host has a **Trace** button.
- Scans every 15 minutes (or only when you press **Scan now**). A /24 takes about 12 seconds.

### Switch logs (syslog from the switches)
- The probe is a **syslog receiver** (UDP 514). The page shows the lines to paste into each switch (`logging host <probe>`, `logging trap informational`, timestamps and `logging origin-id hostname`).
- Cisco messages are split into their code (`%LINK-3-UPDOWN`) and text, coloured by severity, and named by switch (from the message, the Switches page, or the probe's own neighbor).
- **Last hour** readouts (messages, errors, switches sending), messages per minute, and a list you can filter by severity, switch and text. Tick **Watchdog and CS2 events too** to see everything on one timeline: "why did seat 14 lag at 8:42?"
- Kept 14 days (one file per day in `/var/lib/probe-deck/logs/`, written every 5 s to spare the SD card); download any day as text. A sender is capped at 200 messages a second.
- The sidebar shows how many errors (severity 0–3) came in the last hour.

### LAN cache (are game downloads hitting the cache?)
For a [lancache](https://lancache.net) (or any cache that works the same way):
- **DNS:** looks up the download names of Steam, Battle.net, Epic, Riot, EA, Ubisoft, Xbox and Windows Update through **every** DNS server the probe was given, and shows which ones point at the cache. One DNS server that skips the cache is enough to send half the room to the internet.
- **The cache answers:** lancache's heartbeat (`/lancache-heartbeat`, which says which cache answered), and whether port 443 is open for HTTPS passthrough.
- **A test file (optional):** paste an `http://` URL from the cache's access log (a Steam chunk is ideal). It's fetched through the cache twice: the first should say **MISS** and the second **HIT**, and the second shows how fast the cache serves.
- The cache's address is found by itself (the LAN address most game names point at), or set it. Checks run every 10 minutes and on **Check now**. The sidebar light is green when every name goes to the cache, amber when some don't, red when the cache doesn't answer.

### Capture (packets, as .pcap)
- **tcpdump** on the probe's port with any filter (buttons for DHCP, ARP, broadcast, DNS, CS2, spanning tree, CDP/LLDP). Stop after N seconds or by hand.
- The capture goes into a **ring in RAM** (32 MB by default; the oldest packets drop off), not the SD card. **Download .pcap** at any time and open it in Wireshark; **Keep** copies it to the card.
- **Show packets live**: the last 80 packets, as tcpdump prints them, on the page.
- **Watchdog alarms capture themselves** (on by default): a rogue DHCP server, a broadcast storm, an IP conflict or ARP spoofing, a spanning tree change or a rogue IPv6 router starts a 30-second capture with a filter for that problem, kept on the card, so there's evidence of what happened while nobody was looking. At most one per kind every 10 minutes; the card keeps the last 30 (512 MB at most).

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
- **Environment** table: CPU (5 s, 1 min, 5 min), memory, every temperature sensor against its limit, fans, power supplies, PoE. A switch that stops answering shows **DOWN** with why (what its neighbor switch sees on the link).
- Ports are graded by PorkChopCheck's rules (link changes, half duplex, slow links, errors, drops, busy). Read every 20 s; the MAC tables, neighbors and VLANs every minute.
- Set **one read-only community** (v1 or v2c) and add switches by IP, or from **found but not added** (CDP neighbors of your switches) and the subnet **Scan**. An **AireOS wireless controller** shows its access points and clients (its community is set on the controller: Management → SNMP → Communities).
- The community is kept on the probe (`/etc/probe-deck/config.json` and `devices.json`, root-only) and never sent to the page. Viewers see everything, including the drill-down; admins add, remove, reorder and mark ports.

### CS2 (is this network fit for a match?)
Everything about the network that decides a Counter-Strike match, measured from the probe's switch port:
- **Game servers**: list them (`Name = host:port`) or pick one the **LAN scan** found (the same broadcast the game's server browser sends). For each: **ping** (ICMP, once a second) and **server reply** (the game's own A2S query over UDP, once a second, so it includes the server answering), as needle meters and a 5-minute chart; the server's game, **map** (split-flap), player count and the **players' names and scores**.
- **Perf-Deck nodes**: every PC running Perf-Deck (found by the same discovery as the Stations list, UDP 52010) gets the same tests and its own card below the servers, probe ↔ node: **ping** (ICMP) and **app reply** (Perf-Deck's UDP ping/pong, answered by the app), once a second, plus MTU and the switch path. No game server needed, so you can check the LAN at each table before the match. They count toward the verdict; switch them off in the settings.
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

## Build one

### 1. Make the SD card image (on a Linux PC, no root needed)

```
tools/prepare-image.sh probe-02            # hostname; uses ~/.ssh/id_ed25519.pub and this PC's time zone
```

The script downloads **DietPi (Debian 13 "Trixie") for the Radxa ZERO 3** and checks its SHA-256. It then writes the first-boot settings into the image's small `DIETPISETUP` partition with `mtools`:
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
| TCP 2001 | Console over telnet (off by default) |
| UDP 9, 7 | Wake-on-LAN magic packets (sent) |
| UDP 67/68 | DHCP scan (sent: a Discover every 60 s) |
| UDP 161 | SNMP to your switches and controller (sent) |
| UDP 5353 | mDNS: `<host>.local` and `_probe-deck._tcp` (avahi) |

## Related

- [iPerf Deck](https://github.com/turkey415/Perf-Deck): the desktop speed-test station this probe joins.
- [t-display-progs](https://github.com/turkey415/t-display-progs): T-Display-S3 and CYD firmware. Its **Probe Viewer** app shows this probe's results.
- [PorkChopCheck](https://github.com/turkey415/PorkChopCheck): LAN party network checks.
