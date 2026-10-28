#!/bin/sh
# Build a ready-to-flash DietPi image for a probe, with no root needed.
#   tools/prepare-image.sh probe-02 [~/.ssh/id_ed25519.pub] [America/Chicago]
#   BOARD=rpi tools/prepare-image.sh probe-02      # Raspberry Pi 3/4/5 instead of a Radxa ZERO 3
# Downloads DietPi for the board, checks it, and writes the first-boot settings into its
# FAT setup partition with mtools. Prints the dd command to write the card.
set -e
NAME=${1:?usage: prepare-image.sh <hostname> [ssh-pubkey-file] [timezone]}
KEY=${2:-$HOME/.ssh/id_ed25519.pub}
TZ_=${3:-$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)}
case ${BOARD:-radxa} in
  radxa) IMG=DietPi_RadxaZERO3-ARMv8-Trixie.img ;;
  rpi)   IMG=DietPi_RPi234-ARMv8-Trixie.img ;;
  *) echo "BOARD must be radxa or rpi"; exit 1 ;;
esac
URL=https://dietpi.com/downloads/images/$IMG.xz
OUT=${OUT:-$PWD/build}
command -v mcopy >/dev/null || { echo "needs mtools (mcopy)"; exit 1; }
[ -r "$KEY" ] || { echo "no SSH public key at $KEY"; exit 1; }
echo "$NAME" | grep -Eq '^[a-z0-9][a-z0-9-]{0,30}$' || { echo "bad hostname"; exit 1; }
mkdir -p "$OUT" && cd "$OUT"

[ -f "$IMG.xz" ] || curl -fSLO "$URL"
curl -fsSLO "$URL.sha256"
sha256sum -c "$IMG.xz.sha256"
rm -f "$NAME.img"; xz -dc "$IMG.xz" > "$NAME.img"

# Find the FAT partition that holds dietpi.txt (DIETPISETUP on the Radxa, the boot partition on a Pi).
START=$(sfdisk -d "$NAME.img" | awk -F'[=,]' '/type=c/ {gsub(/ /,"",$2); print $2}')
[ -n "$START" ] || { echo "no setup partition found"; exit 1; }
FAT="$NAME.img@@$((START * 512))"
mcopy -o -i "$FAT" ::dietpi.txt dietpi.txt

PW=$(head -c 12 /dev/urandom | base64 | tr -d '/+=' | head -c 16)
sed -i \
 -e "s|^AUTO_SETUP_GLOBAL_PASSWORD=.*|AUTO_SETUP_GLOBAL_PASSWORD=$PW|" \
 -e "s|^AUTO_SETUP_LOCALE=.*|AUTO_SETUP_LOCALE=en_US.UTF-8|" \
 -e "s|^AUTO_SETUP_KEYBOARD_LAYOUT=.*|AUTO_SETUP_KEYBOARD_LAYOUT=us|" \
 -e "s|^AUTO_SETUP_TIMEZONE=.*|AUTO_SETUP_TIMEZONE=$TZ_|" \
 -e "s|^AUTO_SETUP_NET_HOSTNAME=.*|AUTO_SETUP_NET_HOSTNAME=$NAME|" \
 -e "s|^AUTO_SETUP_SSH_SERVER_INDEX=.*|AUTO_SETUP_SSH_SERVER_INDEX=-2|" \
 -e "s|^#AUTO_SETUP_SSH_PUBKEY=.*|AUTO_SETUP_SSH_PUBKEY=$(cat "$KEY")|" \
 -e "s|^#AUTO_SETUP_APT_INSTALLS=.*|AUTO_SETUP_APT_INSTALLS=iperf3 tcpdump lldpd ethtool python3 python3-serial avahi-daemon dnsutils iputils-ping curl jq openssl python3-paramiko ieee-data avahi-utils|" \
 -e "s|^AUTO_SETUP_AUTOMATED=.*|AUTO_SETUP_AUTOMATED=1|" \
 -e "s|^SURVEY_OPTED_IN=.*|SURVEY_OPTED_IN=0|" dietpi.txt
mcopy -o -i "$FAT" dietpi.txt ::dietpi.txt
rm -f dietpi.txt
umask 077; echo "$PW" > "$NAME-password.txt"

echo
echo "Ready: $OUT/$NAME.img   (root/dietpi password in $OUT/$NAME-password.txt)"
echo "Write it to the card (check the device with lsblk first!):"
echo "  sudo dd if=$OUT/$NAME.img of=/dev/sdX bs=4M conv=fsync status=progress"
