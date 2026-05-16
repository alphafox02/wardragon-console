#!/usr/bin/env bash
# Set up chrony + gpsd so the kit can use GPS as a backup time source when
# internet NTP is unreachable. Intended for one-time setup on a fresh
# WarDragon kit image. Idempotent: safe to re-run on an already-configured
# kit; it will normalize values and refresh the managed block.
#
# Run manually:
#   sudo packaging/setup-time-sync.sh
#
# This is NOT invoked by packaging/install.sh on purpose: install.sh manages
# only the console; system-wide time sync is the operator's call.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo packaging/setup-time-sync.sh" >&2
  exit 1
fi

REQUIRED_PKGS=(chrony gpsd gpsd-clients)
GPSD_DEFAULTS=/etc/default/gpsd
CHRONY_CONF=/etc/chrony/chrony.conf
BEGIN_MARKER="# >>> wardragon-time-sync >>>"
END_MARKER="# <<< wardragon-time-sync <<<"

missing_pkgs=()
for pkg in "${REQUIRED_PKGS[@]}"; do
  if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
    missing_pkgs+=("${pkg}")
  fi
done
if (( ${#missing_pkgs[@]} > 0 )); then
  echo "Installing: ${missing_pkgs[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends "${missing_pkgs[@]}"
fi

backup_once() {
  local path="$1"
  if [[ -f "${path}" && ! -f "${path}.wardragon.bak" ]]; then
    cp -a "${path}" "${path}.wardragon.bak"
    echo "Backed up ${path} -> ${path}.wardragon.bak"
  fi
}

set_kv() {
  local file="$1" key="$2" value="$3"
  if grep -qE "^[[:space:]]*${key}=" "${file}"; then
    sed -i "s|^[[:space:]]*${key}=.*|${key}=\"${value}\"|" "${file}"
  else
    printf '%s="%s"\n' "${key}" "${value}" >> "${file}"
  fi
}

# ----- /etc/default/gpsd -----
# START_DAEMON=true       -> let init start gpsd at boot
# USBAUTO=true            -> udev plug events auto-add USB GPS devices
# GPSD_OPTIONS=-n         -> gpsd polls without waiting for a client; needed
#                            so chrony's SHM refclock keeps getting samples
if [[ -f "${GPSD_DEFAULTS}" ]]; then
  backup_once "${GPSD_DEFAULTS}"
  set_kv "${GPSD_DEFAULTS}" "START_DAEMON" "true"
  set_kv "${GPSD_DEFAULTS}" "USBAUTO" "true"
  set_kv "${GPSD_DEFAULTS}" "GPSD_OPTIONS" "-n"
  echo "Configured ${GPSD_DEFAULTS}"
else
  echo "warning: ${GPSD_DEFAULTS} not found; gpsd may need manual config" >&2
fi

# ----- /etc/chrony/chrony.conf -----
# Neutralize any existing un-managed SHM 0 refclock and append a managed
# block. Offset/delay tuned for non-PPS serial GPS so chrony does not flag
# the source as a falseticker when internet NTP disappears. Stratum 10 so
# internet NTP stays preferred when reachable.
if [[ -f "${CHRONY_CONF}" ]]; then
  backup_once "${CHRONY_CONF}"

  # Comment out any pre-existing active refclock SHM 0 line. Idempotent:
  # once prefixed it no longer matches.
  sed -i 's|^[[:space:]]*refclock[[:space:]]\+SHM[[:space:]]\+0|# wardragon-time-sync replaced: &|' "${CHRONY_CONF}"

  # Remove any prior managed block.
  if grep -qF "${BEGIN_MARKER}" "${CHRONY_CONF}"; then
    sed -i "\|^${BEGIN_MARKER}\$|,\|^${END_MARKER}\$|d" "${CHRONY_CONF}"
  fi

  cat >>"${CHRONY_CONF}" <<EOF
${BEGIN_MARKER}
# GPS via gpsd shared-memory segment 0. Internet NTP stays preferred when
# reachable (stratum 10 keeps GPS below it). Offset/delay tuned for
# non-PPS serial GPS so chrony does not mark the source as a falseticker.
refclock SHM 0 refid GPS precision 1e-1 offset 0.5 delay 0.2 stratum 10
${END_MARKER}
EOF
  echo "Configured ${CHRONY_CONF}"
else
  echo "warning: ${CHRONY_CONF} not found; chrony may not be installed correctly" >&2
fi

# ----- services -----
CHRONY_UNIT=chrony.service
if ! systemctl list-unit-files | grep -q '^chrony\.service'; then
  if systemctl list-unit-files | grep -q '^chronyd\.service'; then
    CHRONY_UNIT=chronyd.service
  fi
fi

systemctl enable --now gpsd.socket
systemctl enable --now "${CHRONY_UNIT}"
systemctl restart "${CHRONY_UNIT}"

echo
echo "=== chronyc sources ==="
chronyc sources || true
echo
echo "Done. If no USB GPS is plugged in yet, plug it in: udev + USBAUTO will"
echo "let gpsd pick it up, and 'GPS' should appear in 'chronyc sources' with"
echo "rising reach. Falleback to GPS only kicks in when internet NTP is"
echo "unreachable; on a connected kit chrony will keep using NTP."
