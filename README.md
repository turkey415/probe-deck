# Probe Deck

A pocket gigabit **network probe** with its own web page. Plug it into any switch port and it is powered by PoE. Within a few seconds the page shows what is on the other end of the cable: which switch and port, which VLAN, whether DHCP, DNS and the internet work, and how fast the port really is.

It is built on a **Radxa ZERO 3E** with its PoE HAT, and the page has the same look as [iPerf Deck](https://github.com/turkey415/Perf-Deck). The probe joins Perf-Deck's network of stations, so the two can test each other.

![Probe Deck](docs/screenshot.png)

## Features

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
- An **iperf3** client against any server, as download, upload or both, with live seven-segment readouts and a live chart.
- **History** of every test, with the switch port it ran on.
- An **iperf3 server** on port 5201, so other machines can test the probe.

### Stations: Perf-Deck on the LAN
- The probe speaks **Perf-Deck discovery** (UDP 52010). It shows up as a station in every Perf-Deck on the network segment, and it lists every Perf-Deck station it hears.
- Each station shows a live **ping**, a ready/busy light, and its last result.
- **Send / Receive / Both** buttons run a quick 5-second test against one station.
- **Sweep all** runs a 2-second test from every station to the probe in turn. It follows Perf-Deck's rules: it waits while another station is sweeping, and every station rests 5 minutes after any sweep.

### Probe message (for the displays)
- Every ~9 s the probe broadcasts a small JSON summary on **UDP 47474**: switch and port, VLAN, link, IP, gateway ping, DNS and internet status, and the last speed test. This is the shared probe message from [t-display-progs](https://github.com/turkey415/t-display-progs) (`TODO.md`) for the T-Display and CYD **Probe Viewer**, and for PorkChopCheck. `GET /api/probe` returns the same message.

### Settings and system
- Probe name, ping targets, check interval, DNS test name, internet check URL, and on/off switches for the iperf3 server, Perf-Deck discovery, the broadcast, and DHCP-on-replug. Settings are saved from the page.
- CPU temperature, load, memory and uptime, plus **Check now** and **Renew DHCP** buttons.
- Works on a phone: the layout stacks to one column.

**No login.** Like Perf-Deck, the page has no password. Only use it on networks you trust.

## Hardware

| Part | Notes |
| --- | --- |
| **Radxa ZERO 3E** (2 GB is plenty) | RK3566, gigabit Ethernet, 65 × 30 mm. The **3E** has Ethernet; the 3W does not. |
| **Radxa ZERO 3E PoE HAT** | IEEE 802.3af. **The copper standoffs and screws carry the power**: PoE does not work without them, so tighten them, and keep metal away from them. |
| **microSD card**, 16–32 GB | The board has no built-in storage. |
| Case (optional) | [Case for Radxa 3E with PoE HAT](https://www.printables.com/model/848702-case-for-radxa-3e-with-poe-hat) on Printables. It wants a 40 mm fan, and there is a [taller lid](https://www.printables.com/model/1572977-lid-case-radxa-3e-with-poe-hat) for v1.2 of the HAT. |

At idle the board sits around 45 °C with no case. Gigabit tests load the CPU, so add a small fan if it goes in a closed case.

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

This copies `server.py` and `index.html` to `/opt/probe-deck` and installs and starts the `probe-deck` systemd service. It also turns on CDP in lldpd (Cisco switches send CDP, not LLDP, by default). Run it again after any change to update the probe.

Open **http://probe-02.local/**.

## How it works

- **`server.py`**: one Python 3 file, standard library only. It runs as root because it reads CDP via `lldpcli`, renews DHCP with `ifdown`/`ifup`, and serves on port 80. Background threads:
  - a link watcher (every 1 s)
  - pings (every 2 s)
  - DNS, internet and neighbour checks (every 30 s, and right after a link change)
  - an iperf3 server keeper
  - Perf-Deck discovery and the probe broadcast (every 3 s)
- **`index.html`**: one file with no libraries. It polls `/api/state` every second (twice a second during a test).
- **Files on the probe:** settings in `/etc/probe-deck/config.json`, test history in `/var/lib/probe-deck/history.json`.

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

Cross-site POSTs are refused (an `Origin` header must match `Host`).

### Ports

| Port | Use |
| --- | --- |
| TCP 80 | The page |
| TCP 5201 | iperf3 server |
| UDP 52010 | Perf-Deck discovery (broadcast hello, plus ping/pong for station latency) |
| UDP 47474 | Probe message broadcast |

## Related

- [iPerf Deck](https://github.com/turkey415/Perf-Deck): the desktop speed-test station this probe joins.
- [t-display-progs](https://github.com/turkey415/t-display-progs): T-Display-S3 and CYD firmware. A Probe Viewer app for this probe's broadcast is planned.
- [PorkChopCheck](https://github.com/turkey415/PorkChopCheck): LAN party network checks.
