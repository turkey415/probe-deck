#!/bin/sh
# Copy Probe Deck to a probe and (re)start it:  ./install.sh root@probe-01.local
set -e
HOST=${1:-root@probe-01.local}
DIR=$(cd "$(dirname "$0")" && pwd)
ssh "$HOST" 'mkdir -p /opt/probe-deck'
scp -q "$DIR/server.py" "$DIR/index.html" "$HOST:/opt/probe-deck/"
scp -q "$DIR/probe-deck.service" "$HOST:/etc/systemd/system/probe-deck.service"
# Announce the page over mDNS as _probe-deck._tcp, so displays can find probes by name.
scp -q "$DIR/probe-deck.avahi.xml" "$HOST:/etc/avahi/services/probe-deck.service"
# CDP listening in lldpd (Cisco switches send CDP, not LLDP, by default).
ssh "$HOST" 'grep -q "^DAEMON_ARGS=.*-c" /etc/default/lldpd || { echo "DAEMON_ARGS=\"-c\"" >> /etc/default/lldpd; systemctl restart lldpd; }
  systemctl daemon-reload; systemctl enable -q probe-deck; systemctl restart probe-deck
  sleep 2; systemctl is-active probe-deck'
echo "Open http://${HOST#*@}/"
