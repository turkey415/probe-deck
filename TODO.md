# Probe Deck to-do

What's next. Newest decisions first in each section.

## To build

### Shared syslog rules module
- `swalarms.py` is written (0.36.0). Copy it unchanged into GXL-LANapp's Event Server when its switch syslog listener is built.

### CS2: fairness report
- Compare every seat's latency and jitter to the game server and flag the outlier, for when a team disputes a round.
- Shown on the page only (see "On hold" for push alerts).

### HTTPS: CA-signed certificates
- Upload your own certificate and key, or generate a key and a certificate request (CSR) on the probe to sign with any CA.
- `tls.py` already reads plain PEM files, so a signed certificate drops in.

## Not here
- Edge health (the firewall's state table, upstream saturation, Steam reachability) lives on the GXL Event Server only.

## On hold
- **Push alerts** to Discord, ntfy or PorkChopCheck (including PorkChopCheck listening for the probe's `probe-alert` messages). Held so nobody gets bombarded; alarms stay on the page for now.
- **Traceroutes for GXL's traffic map** (its Paths view). The traffic map project is paused.

## Done recently
- Watchdog switch alarms: err-disable with its cause, spanning-tree guard blocks and MAC flapping, from Switch logs plus the err-disable table on each switch poll (0.36.0).
- System stats page (0.34.0), now with its README section and screenshot.
- mDNS browser by device, in colour-coded categories (0.35.0).
- Probe Viewer on the T-Display shows the jack survey's next seat (t-display-progs 1.10.0).
- Tested on real gear: Cable test and Configs over SSH, wireless client lookup, Headscale switching, Shut down.
