# Probe Deck to-do

What's next. Newest decisions first in each section.

## To build

### Shared syslog rules module
- `swalarms.py` is written (0.36.0). Copy it unchanged into GXL-LANapp's Event Server when its switch syslog listener is built.

### HTTPS: CA-signed certificates
- Upload your own certificate and key, or generate a key and a certificate request (CSR) on the probe to sign with any CA.
- `tls.py` already reads plain PEM files, so a signed certificate drops in.

## Not here
- Edge health (the firewall's state table, upstream saturation, Steam reachability) lives on the GXL Event Server only.

## On hold
- **Push alerts** to Discord, ntfy or PorkChopCheck (including PorkChopCheck listening for the probe's `probe-alert` messages). Held so nobody gets bombarded; alarms stay on the page for now.
- **Traceroutes for GXL's traffic map** (its Paths view). The traffic map project is paused.

## Done recently
- Server mode + Proxmox container script (0.43.0): `tools/proxmox-ct.sh` (create, usb, update), `tools/install-server.sh`. Tested on Ubuntu 26.04 in podman; **the Proxmox part hasn't run on a real host yet**. Unsupported pages (VLAN walk, Jack survey, Renew DHCP) are greyed out with the reason.
- Switch intake: boot health report from the console, IOS version check, preload by template, file or paste (0.42.0). Console fixes: the reader can't be killed by a client joining at the wrong moment, Break waits for the line being written, logs roll over at 10 MB. **Not yet tried on a real switch boot** (only a fake one).
- CS2 fairness report: two teams' seats compared, the odd seat flagged with its switch port, ARP timing for PCs that ignore ping, reports saved and downloadable (0.39.0).
- DNS test: a 30-second spot check of which DNS server to use at a site (0.38.0). Switch alarms can be acknowledged; Switch logs has Mark read (0.38.0).
- Host scan: mark hosts known or unknown (green frame), colour by maker. Switches: err-disabled ports striped red, with Restore port (shutdown / no shutdown over SSH) (0.37.0).
- Watchdog switch alarms: err-disable with its cause, spanning-tree guard blocks and MAC flapping, from Switch logs plus the err-disable table on each switch poll (0.36.0).
- System stats page (0.34.0), now with its README section and screenshot.
- mDNS browser by device, in colour-coded categories (0.35.0).
- Probe Viewer on the T-Display shows the jack survey's next seat (t-display-progs 1.10.0).
- Tested on real gear: Cable test and Configs over SSH, wireless client lookup, Headscale switching, Shut down.
