#!/bin/bash
# Install or update Probe Deck on an Ubuntu or Debian server (a Proxmox container or VM), as root.
#
#   curl -fsSL https://raw.githubusercontent.com/turkey415/probe-deck/main/tools/install-server.sh | bash
#   ... | bash -s -- --ref v0.43.0      a tag or branch (default: main)
#
# Safe to run again: it updates the code and keeps the settings, users and history.
# Server mode: the page leaves out what only a probe on a jack can do (VLAN walk, Jack survey, Renew DHCP).
set -euo pipefail

REPO=${REPO:-https://github.com/turkey415/probe-deck}
REF=main
while [ $# -gt 0 ]; do
  case $1 in
    --ref) REF=$2; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

step() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
[ "$(id -u)" = 0 ] || { echo "Run this as root."; exit 1; }
command -v apt-get >/dev/null || { echo "This needs Ubuntu or Debian (apt)."; exit 1; }

step "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
  python3 python3-serial python3-paramiko git ca-certificates curl openssl \
  iproute2 iputils-ping iperf3 tcpdump lldpd ethtool dnsutils jq ieee-data \
  avahi-daemon avahi-utils >/dev/null
# iperf3 may install its own always-on server; Probe Deck runs its own.
systemctl disable --now iperf3 2>/dev/null || true

step "Code ($REF)"
SRC=/opt/probe-deck-src
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch -q --depth 1 origin "$REF"
  git -C "$SRC" checkout -q -f FETCH_HEAD
else
  rm -rf "$SRC"
  git clone -q --depth 1 --branch "$REF" "$REPO" "$SRC"
fi
echo "at $(git -C "$SRC" log -1 --format='%h %s')"
mkdir -p /opt/probe-deck/web /opt/probe-deck/vendor /opt/probe-deck/gamelan
cp "$SRC"/*.py "$SRC/index.html" /opt/probe-deck/
cp "$SRC"/web/* /opt/probe-deck/web/
cp "$SRC"/vendor/* /opt/probe-deck/vendor/
cp "$SRC"/gamelan/*.py /opt/probe-deck/gamelan/

step "Service"
cat > /etc/systemd/system/probe-deck.service <<'EOF'
[Unit]
Description=Probe Deck (server mode)
After=network-online.target lldpd.service
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 -u /opt/probe-deck/server.py --port 80 --iface auto --mode server
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
mkdir -p /etc/avahi/services
cp "$SRC/probe-deck.avahi.xml" /etc/avahi/services/probe-deck.service
# Cisco switches send CDP, not LLDP: let lldpd hear it.
if [ -f /etc/default/lldpd ] && ! grep -q '^DAEMON_ARGS=.*-c' /etc/default/lldpd; then
  echo 'DAEMON_ARGS="-c"' >> /etc/default/lldpd
  systemctl restart lldpd || true
fi
systemctl daemon-reload
systemctl enable -q probe-deck
systemctl restart probe-deck
sleep 3
systemctl is-active -q probe-deck || { journalctl -u probe-deck -n 30 --no-pager; exit 1; }

IP=$(ip -4 -o route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
step "Done"
echo "Probe Deck $(sed -n 's/^__version__ = "\(.*\)"/\1/p' /opt/probe-deck/server.py) is running."
echo "Open https://${IP:-<this box>}/ (self-signed certificate) and make the admin account."
