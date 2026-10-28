# Probe Deck to-do

What's next. Newest decisions first in each section.

## To build

### Watchdog: switch alarms from Switch logs
- **Err-disable, spanning-tree and MAC-flap alarms.** Promote syslog lines already arriving on Switch logs (UDP 514) to Watchdog alarms that name the switch, the port and the cause:
  - `%PM-4-ERR_DISABLE` with its cause (BPDU guard, port security, link flap…)
  - `%SPANTREE-2-*`
  - MAC flapping (`%SW_MATRIX-4-MACFLAP_NOTIF`)
- Each one starts the 30-second auto-capture like the other alarms and lights the Watch module red.
- Syslog is UDP and lines get lost, so also read each switch's err-disable table (CISCO-ERR-DISABLE-MIB) on the SNMP poll as a backup.
- Covers every port on every switch, so the game server's own port is watched like any other.

### Shared syslog rules module
- Use the same syslog matching rules as GXL-LANapp's Event Server: one module, copied unchanged between the two repos, like `pc_snmp.py`.

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
- System stats page (0.34.0), now with its README section and screenshot.
- mDNS browser by device, in colour-coded categories (0.35.0).
- Probe Viewer on the T-Display shows the jack survey's next seat (t-display-progs 1.10.0).
- Tested on real gear: Cable test and Configs over SSH, wireless client lookup, Headscale switching, Shut down.
