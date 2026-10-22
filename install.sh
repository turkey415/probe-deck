#!/bin/sh
# Copy Probe Deck to a probe and (re)start it:  ./install.sh root@probe-01.local
set -e
HOST=${1:-root@probe-01.local}
DIR=$(cd "$(dirname "$0")" && pwd)
ssh "$HOST" 'mkdir -p /opt/probe-deck/vendor'
scp -q "$DIR"/*.py "$DIR/index.html" "$HOST:/opt/probe-deck/"
# xterm.js for the console, served by the probe so it works on a LAN with no internet.
scp -q "$DIR"/vendor/* "$HOST:/opt/probe-deck/vendor/"
scp -q "$DIR/probe-deck.service" "$HOST:/etc/systemd/system/probe-deck.service"
# Announce the page over mDNS as _probe-deck._tcp, so displays can find probes by name.
scp -q "$DIR/probe-deck.avahi.xml" "$HOST:/etc/avahi/services/probe-deck.service"
# CDP listening in lldpd (Cisco switches send CDP, not LLDP, by default).
ssh "$HOST" 'grep -q "^DAEMON_ARGS=.*-c" /etc/default/lldpd || { echo "DAEMON_ARGS=\"-c\"" >> /etc/default/lldpd; systemctl restart lldpd; }
  python3 -c "import serial" 2>/dev/null || apt-get install -y -q python3-serial >/dev/null
  systemctl daemon-reload; systemctl enable -q probe-deck; systemctl restart probe-deck
  sleep 2; systemctl is-active probe-deck'
echo "Open https://${HOST#*@}/ (self-signed certificate: see the HTTPS tab)"
