#!/usr/bin/env bash
# Install the WarDragon Pi-greeter status overlay. A thin waybar bar
# rotates through receiver state, DragonSig SDR state, drone/signal
# counts, and the tablet URL on the LightDM login screen.
#
# Targets Pi OS Trixie with pi-greeter-labwc (the default Wayland greeter
# on current Pi OS). Bails cleanly if pi-greeter-labwc is not the active
# greeter, leaving the system untouched.
#
# Not invoked by install.sh — modifying the greeter session is a
# system-wide change and that decision belongs to the operator. Same
# pattern as setup-time-sync.sh and setup-sddm-status.sh.
#
# usage: sudo packaging/setup-pi-status-overlay.sh

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo packaging/setup-pi-status-overlay.sh" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="${ROOT_DIR}/packaging/pi-greeter-overlay"

GREETER_CONF_DIR=/etc/xdg/labwc-greeter
AUTOSTART="${GREETER_CONF_DIR}/autostart"
WAYBAR_CONF_DIR="${GREETER_CONF_DIR}/wardragon-waybar"
ROTATOR_PATH=/usr/local/bin/wardragon-status-rotator

# Check this is actually a Pi OS labwc greeter setup
if [[ ! -d "${GREETER_CONF_DIR}" ]]; then
  cat >&2 <<MSG
This helper expects pi-greeter-labwc to be the LightDM greeter (Pi OS Trixie).
The directory ${GREETER_CONF_DIR} does not exist, so the system is using a
different greeter and we will not modify anything. To install a status
overlay on a different greeter (SDDM/Lubuntu), use setup-sddm-status.sh
instead.
MSG
  exit 2
fi

if [[ ! -f "${SRC_DIR}/wardragon-status-rotator" ]]; then
  echo "Packaged overlay files missing at ${SRC_DIR}" >&2
  exit 3
fi

# apt-install the runtime deps if they aren't already present.
missing=()
for pkg in waybar jq curl; do
  if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
    missing+=("${pkg}")
  fi
done
if (( ${#missing[@]} > 0 )); then
  echo "Installing missing packages: ${missing[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends "${missing[@]}"
fi

echo "Installing status overlay"
install -o root -g root -m 0755 "${SRC_DIR}/wardragon-status-rotator" "${ROTATOR_PATH}"

install -d -o root -g root -m 0755 "${WAYBAR_CONF_DIR}"
install -o root -g root -m 0644 "${SRC_DIR}/config.json" "${WAYBAR_CONF_DIR}/config.json"
install -o root -g root -m 0644 "${SRC_DIR}/style.css" "${WAYBAR_CONF_DIR}/style.css"

# Add a marker-fenced launch line to the greeter autostart. Re-run is safe;
# any prior block is removed first so the file stays clean.
if [[ ! -f "${AUTOSTART}.wardragon.bak" ]]; then
  cp -a "${AUTOSTART}" "${AUTOSTART}.wardragon.bak"
fi

sed -i '/^# >>> wardragon-status >>>/,/^# <<< wardragon-status <<</d' "${AUTOSTART}"

cat >>"${AUTOSTART}" <<EOF

# >>> wardragon-status >>>
/usr/bin/waybar -c ${WAYBAR_CONF_DIR}/config.json -s ${WAYBAR_CONF_DIR}/style.css &
# <<< wardragon-status <<<
EOF

cat <<EOF

Installed.

The status bar appears at the bottom of the login screen, rotating every 3 s
through: receiver dots (WiFi/BLE/DJI), DragonSig SDR state, drone/signal
counts, and tablet URL. It polls http://127.0.0.1:4280/api/snapshot.

To see it, log out of your current session (or reboot) — pi-greeter-labwc
will pick up the autostart entry on next launch and waybar will appear.

To revert:
  sudo cp ${AUTOSTART}.wardragon.bak ${AUTOSTART}
  sudo rm -rf ${WAYBAR_CONF_DIR} ${ROTATOR_PATH}
EOF
