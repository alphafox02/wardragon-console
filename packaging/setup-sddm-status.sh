#!/usr/bin/env bash
# Install the WarDragon SDDM login-screen status widget. This drops a small
# panel in the bottom-right corner of the SDDM greeter that polls the local
# console at http://127.0.0.1:4280/api/snapshot and surfaces:
#
#   * console reachable / unreachable
#   * WiFi / BLE / DJI droneid-go receiver state
#   * DragonSig SDR state + noise floor
#   * current drone and signal counts
#   * tablet URL (stable when the claim profile matches, dynamic otherwise)
#
# This is a separate, operator-run helper — it is NOT invoked by install.sh
# because changing the SDDM theme can disrupt running graphical sessions and
# is the operator's call. Same pattern as packaging/setup-time-sync.sh.
#
# Currently supports the Lubuntu base SDDM theme. The widget overlays on
# top of a copy of /usr/share/sddm/themes/lubuntu/ so all the artwork from
# the original (background, icons) is preserved. If you run a different
# distro variant (Kubuntu/Breeze, etc.) this script bails cleanly and the
# original theme stays untouched.
#
# usage: sudo packaging/setup-sddm-status.sh

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo packaging/setup-sddm-status.sh" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_QML="${ROOT_DIR}/packaging/sddm-theme/Main.qml"

SDDM_THEMES=/usr/share/sddm/themes
BASE_THEME=lubuntu
NEW_THEME=lubuntu-wardragon
SDDM_CONF_D=/etc/sddm.conf.d
SDDM_CONF_FILE=${SDDM_CONF_D}/10-wardragon-theme.conf

if ! command -v sddm >/dev/null 2>&1; then
  echo "sddm is not installed; nothing to do." >&2
  exit 1
fi

if [[ ! -d "${SDDM_THEMES}/${BASE_THEME}" ]]; then
  cat >&2 <<MSG
Base SDDM theme '${BASE_THEME}' not found at ${SDDM_THEMES}/${BASE_THEME}.
This helper currently only supports the Lubuntu SDDM theme, since the
widget Main.qml extends Lubuntu's specific scene structure. If you are on
a different distro variant, the widget would need a per-variant Main.qml.
Leaving the existing SDDM theme untouched.
MSG
  exit 2
fi

if [[ ! -f "${SRC_QML}" ]]; then
  echo "Packaged Main.qml missing at ${SRC_QML}. Make sure the working tree is intact." >&2
  exit 3
fi

echo "Installing WarDragon SDDM status widget"
echo "  base theme:  ${SDDM_THEMES}/${BASE_THEME}"
echo "  new theme:   ${SDDM_THEMES}/${NEW_THEME}"

# Always re-sync from the base theme so a re-run picks up any artwork the
# upstream theme has changed (e.g. wallpaper bumped by a system update).
rm -rf "${SDDM_THEMES}/${NEW_THEME}"
cp -a "${SDDM_THEMES}/${BASE_THEME}" "${SDDM_THEMES}/${NEW_THEME}"

# Friendly display name in theme.metadata so the chooser shows what it is.
if [[ -f "${SDDM_THEMES}/${NEW_THEME}/metadata.desktop" ]]; then
  sed -i 's|^Name=.*|Name=Lubuntu (WarDragon status)|' "${SDDM_THEMES}/${NEW_THEME}/metadata.desktop"
fi

install -o root -g root -m 0644 "${SRC_QML}" "${SDDM_THEMES}/${NEW_THEME}/Main.qml"

# Activate via a drop-in so we don't disturb anything else in /etc/sddm.conf
# (e.g. an existing [Autologin] block). To revert, just remove this file.
install -d -o root -g root -m 0755 "${SDDM_CONF_D}"
cat > "${SDDM_CONF_FILE}" <<EOF
[Theme]
Current=${NEW_THEME}
EOF
chmod 0644 "${SDDM_CONF_FILE}"

cat <<EOF

Installed.

The widget will appear in the bottom-right of the SDDM login screen.
It polls http://127.0.0.1:4280/api/snapshot every 3 s and shows console,
receiver, DragonSig, drone/signal, and tether-URL state.

To see it, log out of your current session — SDDM redraws on next login.
A reboot also works. 'systemctl restart sddm' would force-refresh the
greeter, but it will end your active graphical session, so do that
deliberately.

To revert:
  sudo rm ${SDDM_CONF_FILE}
  sudo systemctl restart sddm
EOF
