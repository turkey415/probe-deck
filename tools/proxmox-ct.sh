#!/bin/bash
# Probe Deck as a Proxmox container. Run on the Proxmox host's shell, as root.
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/turkey415/probe-deck/main/tools/proxmox-ct.sh)"
#
#   proxmox-ct.sh [create]       make a new Ubuntu container with Probe Deck (asks, with defaults)
#   proxmox-ct.sh usb <ctid>     pass a USB console cable into the container (Console, Switch intake)
#   proxmox-ct.sh update <ctid>  update Probe Deck inside the container
#
# Settings can also come from the environment, e.g.
#   CTID=120 CT_NAME=probe-hq BRIDGE=vmbr0 VLAN=10 IP=10.0.0.50/24 GW=10.0.0.1 YES=1 bash proxmox-ct.sh
set -euo pipefail

RAW=${RAW:-https://raw.githubusercontent.com/turkey415/probe-deck/main}
REF=${REF:-main}
SELF=probe-deck-ct   # the create step saves this script on the host under that name

step() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }
ask() {  # ask VAR "question" default
  local var=$1 q=$2 def=$3 val
  if [ -n "${!var:-}" ]; then return; fi
  if [ -n "${YES:-}" ] || [ ! -t 0 ]; then printf -v "$var" '%s' "$def"; return; fi
  read -r -p "$q [$def]: " val
  printf -v "$var" '%s' "${val:-$def}"
}

[ "$(id -u)" = 0 ] || die "Run this as root on the Proxmox host."
command -v pct >/dev/null && command -v pveam >/dev/null || die "This is for a Proxmox VE host (pct and pveam not found)."

in_ct() {  # run the installer inside the container
  local id=$1
  pct exec "$id" -- bash -c "command -v curl >/dev/null || { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q curl ca-certificates >/dev/null; }"
  pct exec "$id" -- bash -c "curl -fsSL '$RAW/tools/install-server.sh' | bash -s -- --ref '$REF'"
}

ct_ip() {
  pct exec "$1" -- ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1
}

cmd_create() {
  step "Settings"
  local next; next=$(pvesh get /cluster/nextid 2>/dev/null || echo 120)
  ask CTID "Container ID" "$next"
  pct status "$CTID" >/dev/null 2>&1 && die "Container $CTID already exists. Pick another ID, or use: $SELF update $CTID"
  ask CT_NAME "Host name" "probe-deck"
  ask BRIDGE "Network bridge" "vmbr0"
  ask VLAN "VLAN tag (empty for none)" ""
  ask IP "IP address (dhcp, or e.g. 10.0.0.50/24)" "dhcp"
  if [ "$IP" != dhcp ]; then ask GW "Gateway" ""; fi
  local rootst tmplst
  rootst=$(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')
  tmplst=$(pvesm status -content vztmpl 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')
  ask STORAGE "Disk storage" "${rootst:-local-lvm}"
  ask TSTORAGE "Template storage" "${tmplst:-local}"
  ask DISK "Disk size (GB)" "8"
  ask MEM "Memory (MB)" "1024"
  ask CORES "CPU cores" "1"

  step "Ubuntu template"
  pveam update >/dev/null || echo "(couldn't refresh the template list; using what's known)"
  # Proxmox only sets up Ubuntu versions its pve-container package knows; an older Proxmox can offer
  # a template it then refuses ("unsupported Ubuntu version"). Take the newest one this host supports.
  local tmpl="" newest t v pm=${UBUNTU_PM:-/usr/share/perl5/PVE/LXC/Setup/Ubuntu.pm}
  local all; all=$(pveam available --section system | awk '{print $2}' | grep -E '^ubuntu-[0-9]+\.[0-9]+-standard_' | sort -rV)
  [ -n "$all" ] || die "No Ubuntu template found in 'pveam available'."
  newest=$(echo "$all" | head -1)
  for t in $all; do
    v=$(echo "$t" | sed -n 's/^ubuntu-\([0-9]*\.[0-9]*\)-.*/\1/p')
    if [ ! -f "$pm" ] || grep -q "'$v'" "$pm"; then tmpl=$t; break; fi
  done
  [ -n "$tmpl" ] || die "This Proxmox doesn't support any Ubuntu template it offers. Update it: apt update && apt full-upgrade"
  echo "newest offered: $newest"
  if [ "$tmpl" != "$newest" ]; then
    local nv=${newest#ubuntu-}; nv=${nv%%-*}
    echo "This Proxmox can't set up Ubuntu $nv yet (its pve-container package is older)."
    echo "Using $tmpl instead. To get $nv later: apt update && apt full-upgrade on this host, then make a new container."
    local yn=y
    [ -z "${YES:-}" ] && [ -t 0 ] && read -r -p "Carry on with ${tmpl%%-standard*}? [Y/n]: " yn
    case ${yn:-y} in [Nn]*) die "Stopped. Update Proxmox, then run this again." ;; esac
  fi
  echo "using: $tmpl"
  if ! pveam list "$TSTORAGE" | grep -q "$tmpl"; then
    pveam download "$TSTORAGE" "$tmpl"
  else
    echo "already downloaded"
  fi

  step "Container $CTID"
  local net="name=eth0,bridge=$BRIDGE,firewall=0"
  [ -n "$VLAN" ] && net="$net,tag=$VLAN"
  if [ "$IP" = dhcp ]; then net="$net,ip=dhcp"; else net="$net,ip=$IP${GW:+,gw=$GW}"; fi
  pct create "$CTID" "$TSTORAGE:vztmpl/$tmpl" \
    --hostname "$CT_NAME" --cores "$CORES" --memory "$MEM" --swap 512 \
    --rootfs "$STORAGE:$DISK" --net0 "$net" \
    --unprivileged 1 --features nesting=1 --onboot 1 \
    --description "Probe Deck (server mode). Update: probe-deck-ct update $CTID. USB console: probe-deck-ct usb $CTID"
  pct start "$CTID"

  step "Waiting for the network"
  local i
  for i in $(seq 1 60); do
    if pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1; then break; fi
    sleep 2
  done
  pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 || die "Container $CTID has no internet (DNS or route). Fix its network, then run: $SELF update $CTID"

  step "Installing Probe Deck"
  in_ct "$CTID"
  # keep this script on the host for later (usb, update)
  curl -fsSL "$RAW/tools/proxmox-ct.sh" -o "/usr/local/sbin/$SELF" 2>/dev/null && chmod 755 "/usr/local/sbin/$SELF" || true

  local ip; ip=$(ct_ip "$CTID")
  step "Ready"
  echo "Container $CTID ($CT_NAME) at ${ip:-?}"
  echo "Open https://${ip:-<its address>}/ and make the admin account (first visit)."
  echo "Console cable later: plug it into this host, then run: $SELF usb $CTID"
}

cmd_usb() {
  local id=${1:-}
  [ -n "$id" ] || die "Usage: $SELF usb <ctid>"
  pct status "$id" >/dev/null 2>&1 || die "No container $id"
  local ver; ver=$(pveversion | sed -n 's|^pve-manager/\([0-9]*\)\.\([0-9]*\).*|\1 \2|p')
  set -- $ver
  if [ "${1:-0}" -lt 8 ] || { [ "${1:-0}" -eq 8 ] && [ "${2:-0}" -lt 1 ]; }; then
    die "Passing a device into a container needs Proxmox VE 8.1 or newer (this is $(pveversion | cut -d/ -f2))."
  fi
  step "USB serial adapters on this host"
  local devs=() d n=0
  for d in /dev/serial/by-id/*; do [ -e "$d" ] && devs+=("$d"); done
  [ ${#devs[@]} -gt 0 ] || die "No USB serial adapter found (/dev/serial/by-id is empty). Plug the console cable into this host and try again."
  for d in "${devs[@]}"; do n=$((n + 1)); echo "  $n) $(basename "$d")  -> $(readlink -f "$d")"; done
  local pick=1
  if [ ${#devs[@]} -gt 1 ] && [ -t 0 ]; then read -r -p "Which one? [1]: " pick; pick=${pick:-1}; fi
  d=${devs[$((pick - 1))]:-}
  [ -n "$d" ] || die "No such choice"
  if pct config "$id" | grep -q -- "$d"; then
    echo "Already passed into $id."
  else
    local slot=0
    while pct config "$id" | grep -q "^dev$slot:"; do slot=$((slot + 1)); done
    # gid 20 = dialout in Ubuntu; Probe Deck runs as root in the container anyway
    pct set "$id" "-dev$slot" "$d,gid=20,mode=0660"
    echo "Added as dev$slot: $d"
  fi
  if [ "$(pct status "$id" | awk '{print $2}')" = running ]; then
    local yn=y
    [ -t 0 ] && read -r -p "Restart container $id now so it sees the cable? [Y/n]: " yn
    case ${yn:-y} in [Nn]*) echo "Restart it later: pct reboot $id" ;; *) pct reboot "$id"; echo "Restarted. The Console page should show the cable in a few seconds." ;; esac
  fi
  echo "If the cable is unplugged and plugged back in, restart the container (pct reboot $id)."
}

cmd_update() {
  local id=${1:-}
  [ -n "$id" ] || die "Usage: $SELF update <ctid>"
  [ "$(pct status "$id" 2>/dev/null | awk '{print $2}')" = running ] || die "Container $id isn't running"
  step "Updating Probe Deck in $id"
  in_ct "$id"
}

case ${1:-create} in
  create) cmd_create ;;
  usb) shift; cmd_usb "$@" ;;
  update) shift; cmd_update "$@" ;;
  -h|--help|help) sed -n '2,13p' "$0" 2>/dev/null || true ;;
  *) die "Unknown command: $1 (create, usb <ctid>, update <ctid>)" ;;
esac
