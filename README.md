# Probe Deck

The web page and test loop for a wired network probe: a Radxa Zero 3E with its PoE HAT (`probe-01`), running DietPi.
It has the same look as [iPerf Deck](https://github.com/turkey415/Perf-Deck).

Open `http://probe-01.local/` (port 80).

- **Port:** the switch, port, VLAN and platform from CDP/LLDP (lldpd), link speed and duplex, link drops, and the time from plug-in to an IP address.
- **Network:** the address, gateway, DNS servers and DHCP lease.
- **Checks:** pings every 2 s to up to 8 targets (`GW` = the gateway), DNS through each DHCP-given server, and an HTTP internet check.
- **Speed test:** an iperf3 client against any server. Perf-Deck stations on the LAN are listed as one-tap buttons. History is kept with the switch port each test ran on.
- **iperf3 server** on 5201. The probe answers Perf-Deck discovery (UDP 52010), so it shows up as a station in Perf-Deck.
- **Probe message:** a small JSON broadcast on UDP 47474 every ~9 s for the T-Displays and PorkChopCheck (see `t-display-progs/TODO.md`). `GET /api/probe` returns the same message.
- **Settings** are saved on the page to `/etc/probe-deck/config.json`.
- Re-plugging into a new jack gets a new DHCP lease (can be turned off).

There is no login, so only use it on networks you trust.

## Install / update

```
./install.sh root@probe-01.local
```

This copies `server.py` and `index.html` to `/opt/probe-deck`, installs the `probe-deck` systemd service (it runs as root because it reads CDP and renews DHCP), and turns on CDP in lldpd.
Needs `python3 iperf3 lldpd dnsutils iputils-ping`.
