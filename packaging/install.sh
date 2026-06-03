#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/wardragon-console"
BIN_PATH="/usr/local/bin/wardragon-console"
ALIAS_BIN_PATH="/usr/local/bin/wardragon-avahi-alias"
SERVICE_PATH="/etc/systemd/system/wardragon-console.service"
ALIAS_SERVICE_PATH="/etc/systemd/system/wardragon-avahi-alias.service"
SUDOERS_PATH="/etc/sudoers.d/wardragon-console"
AVAHI_PATH="/etc/avahi/services/wardragon-console.service"

REQUIRED_PKGS=(
  python3
  python3-zmq
  rsync
  avahi-daemon
  avahi-utils
  python3-dbus
  python3-gi
  gir1.2-glib-2.0
)

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo packaging/install.sh" >&2
  exit 1
fi

# This installer was built for the WarDragon kit layout: a user named 'dragon'
# with DragonSync at /home/dragon/WarDragon/DragonSync. You can override via
# WARDRAGON_USER and WARDRAGON_DRAGONSYNC_DIR env vars, or interactively when
# running on a TTY. Non-default values have not been heavily tested — DragonSync
# itself may also expect /home/dragon paths.
DEFAULT_USER="dragon"
TARGET_USER="${WARDRAGON_USER:-}"
if [[ -z "${TARGET_USER}" ]]; then
  if [[ -t 0 ]]; then
    read -r -p "Install for which user? [${DEFAULT_USER}]: " TARGET_USER
  fi
  TARGET_USER="${TARGET_USER:-${DEFAULT_USER}}"
fi

if ! id "${TARGET_USER}" >/dev/null 2>&1; then
  echo "User '${TARGET_USER}' does not exist. Create it first, or pass WARDRAGON_USER=<existing-user>." >&2
  exit 1
fi
TARGET_HOME=$(getent passwd "${TARGET_USER}" | cut -d: -f6)
TARGET_GROUP=$(id -gn "${TARGET_USER}")

DEFAULT_DRAGONSYNC_DIR="${TARGET_HOME}/WarDragon/DragonSync"
DRAGONSYNC_DIR="${WARDRAGON_DRAGONSYNC_DIR:-}"
if [[ -z "${DRAGONSYNC_DIR}" ]]; then
  if [[ -t 0 && "${TARGET_USER}" != "${DEFAULT_USER}" ]]; then
    read -r -p "DragonSync directory? [${DEFAULT_DRAGONSYNC_DIR}]: " DRAGONSYNC_DIR
  fi
  DRAGONSYNC_DIR="${DRAGONSYNC_DIR:-${DEFAULT_DRAGONSYNC_DIR}}"
fi

# DragonScope lives in its own directory. The naming convention switched
# from antsdr_dji_droneid (legacy) to dragonsdr_dji_droneid (current), and
# both can exist side-by-side on a transitioning kit. To pick the right one
# we ask systemd which directory the running dragonscope.service actually
# uses. Operator override via WARDRAGON_DRAGONSCOPE_DIR wins over all.
DRAGONSCOPE_DIR="${WARDRAGON_DRAGONSCOPE_DIR:-}"
if [[ -z "${DRAGONSCOPE_DIR}" ]] && command -v systemctl >/dev/null 2>&1; then
  unit_path=$(systemctl show -p FragmentPath dragonscope.service --value 2>/dev/null || true)
  if [[ -n "${unit_path}" && -f "${unit_path}" ]]; then
    exec_line=$(grep -m1 '^ExecStart=' "${unit_path}" 2>/dev/null | sed 's|^ExecStart=||')
    for token in ${exec_line}; do
      case "${token}" in
        */dragonscope*.py)
          DRAGONSCOPE_DIR=$(dirname "${token}")
          break
          ;;
      esac
    done
    if [[ -z "${DRAGONSCOPE_DIR}" ]]; then
      wd=$(systemctl show -p WorkingDirectory dragonscope.service --value 2>/dev/null || true)
      [[ -n "${wd}" && -d "${wd}" ]] && DRAGONSCOPE_DIR="${wd}"
    fi
  fi
fi
if [[ -z "${DRAGONSCOPE_DIR}" || ! -d "${DRAGONSCOPE_DIR}" ]]; then
  for candidate in \
    "${TARGET_HOME}/WarDragon/dragonsdr_dji_droneid" \
    "${TARGET_HOME}/WarDragon/antsdr_dji_droneid"; do
    if [[ -d "${candidate}" ]]; then
      DRAGONSCOPE_DIR="${candidate}"
      break
    fi
  done
  : "${DRAGONSCOPE_DIR:=${TARGET_HOME}/WarDragon/dragonsdr_dji_droneid}"
fi

if [[ "${TARGET_USER}" != "${DEFAULT_USER}" ]]; then
  cat >&2 <<WARN
warning: installing for user '${TARGET_USER}' (not the WarDragon default '${DEFAULT_USER}').
         This is a best-effort path; DragonSync itself may still expect /home/${DEFAULT_USER}
         paths. Make sure DragonSync exists at ${DRAGONSYNC_DIR} before continuing.
WARN
fi

echo "Installing WarDragon Console as user ${TARGET_USER}:${TARGET_GROUP}"
echo "DragonSync directory:   ${DRAGONSYNC_DIR}"
echo "DragonScope directory:  ${DRAGONSCOPE_DIR}"

missing_pkgs=()
for pkg in "${REQUIRED_PKGS[@]}"; do
  if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
    missing_pkgs+=("${pkg}")
  fi
done

if (( ${#missing_pkgs[@]} > 0 )); then
  echo "Installing missing apt packages: ${missing_pkgs[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends "${missing_pkgs[@]}"
fi

systemctl enable --now avahi-daemon.service

install -d -o root -g root -m 0755 "${APP_DIR}"
rsync -a --delete --exclude '__pycache__' "${ROOT_DIR}/src/" "${APP_DIR}/src/"

cat > "${BIN_PATH}" <<'WRAPPER'
#!/usr/bin/env bash
export PYTHONPATH=/opt/wardragon-console/src
exec /usr/bin/python3 -m wardragon_console "$@"
WRAPPER
chmod 0755 "${BIN_PATH}"

unit_tmp=$(mktemp)
sed \
  -e "s|^User=dragon$|User=${TARGET_USER}|" \
  -e "s|^Group=dragon$|Group=${TARGET_GROUP}|" \
  -e "s|/home/dragon/WarDragon/DragonSync|${DRAGONSYNC_DIR}|g" \
  -e "s|/home/dragon/WarDragon/dragonsdr_dji_droneid|${DRAGONSCOPE_DIR}|g" \
  "${ROOT_DIR}/packaging/wardragon-console.service" > "${unit_tmp}"
install -o root -g root -m 0644 "${unit_tmp}" "${SERVICE_PATH}"
rm -f "${unit_tmp}"

install -o root -g root -m 0755 "${ROOT_DIR}/packaging/wardragon-avahi-alias" "${ALIAS_BIN_PATH}"
install -o root -g root -m 0644 "${ROOT_DIR}/packaging/wardragon-avahi-alias.service" "${ALIAS_SERVICE_PATH}"

sudoers_tmp=$(mktemp)
sed "s|^dragon |${TARGET_USER} |" "${ROOT_DIR}/packaging/sudoers-wardragon-console" > "${sudoers_tmp}"
install -o root -g root -m 0440 "${sudoers_tmp}" "${SUDOERS_PATH}"
rm -f "${sudoers_tmp}"
visudo -cf "${SUDOERS_PATH}"

install -d -o root -g root -m 0755 /etc/avahi/services
install -o root -g root -m 0644 "${ROOT_DIR}/packaging/avahi/wardragon-console.service" "${AVAHI_PATH}"

# Tell avahi not to advertise on docker bridges / veth pairs. Enumerated at
# install time; if you create new docker networks later, re-run install.sh
# (or edit /etc/avahi/avahi-daemon.conf manually).
AVAHI_DAEMON_CONF=/etc/avahi/avahi-daemon.conf
if [[ -f "${AVAHI_DAEMON_CONF}" ]]; then
  if [[ ! -f "${AVAHI_DAEMON_CONF}.wardragon.bak" ]]; then
    cp -a "${AVAHI_DAEMON_CONF}" "${AVAHI_DAEMON_CONF}.wardragon.bak"
  fi
  # `ip -o link` prints veth names as `vethXXXX@ifN`; the real interface name
  # is the part before `@`. Strip it before matching.
  deny_list=$(ip -o link show 2>/dev/null \
    | awk -F': ' '{print $2}' \
    | awk -F'@' '{print $1}' \
    | awk '{print $1}' \
    | grep -E '^(docker|br-|veth)' \
    | sort -u \
    | paste -sd, - || true)
  awk -v deny="${deny_list}" '
    BEGIN { in_server = 0; printed = 0 }
    /^\[server\]/ { in_server = 1; print; next }
    /^\[/ {
      if (in_server && !printed && length(deny) > 0) {
        print "deny-interfaces=" deny
        printed = 1
      }
      in_server = 0
      print
      next
    }
    in_server && /^[[:space:]]*#?[[:space:]]*deny-interfaces[[:space:]]*=/ {
      if (!printed && length(deny) > 0) {
        print "deny-interfaces=" deny
        printed = 1
      }
      next
    }
    { print }
    END {
      if (in_server && !printed && length(deny) > 0) {
        print "deny-interfaces=" deny
      }
    }
  ' "${AVAHI_DAEMON_CONF}" > "${AVAHI_DAEMON_CONF}.tmp" \
    && mv "${AVAHI_DAEMON_CONF}.tmp" "${AVAHI_DAEMON_CONF}"
  if [[ -n "${deny_list}" ]]; then
    echo "avahi deny-interfaces = ${deny_list}"
  else
    echo "avahi deny-interfaces = (none; no docker/bridge/veth interfaces found)"
  fi
  systemctl restart avahi-daemon.service
fi

# Pin wardragon.local to loopback in /etc/hosts so the kit's OWN browser
# always reaches the local console. Without this, avahi may answer same-host
# queries with a docker0 / veth IP that has no listener. Remote clients
# (tablets over tether, dev boxes on LAN) ignore this entry; they keep
# resolving via mDNS.
HOSTS_BEGIN="# >>> wardragon-console >>>"
HOSTS_END="# <<< wardragon-console <<<"
if ! grep -qF "${HOSTS_BEGIN}" /etc/hosts; then
  cat >>/etc/hosts <<EOF

${HOSTS_BEGIN}
127.0.0.1 wardragon.local
::1       wardragon.local
${HOSTS_END}
EOF
fi

install -d -o "${TARGET_USER}" -g "${TARGET_GROUP}" -m 0700 "${DRAGONSYNC_DIR}/certs"

systemctl daemon-reload
systemctl enable wardragon-console.service
systemctl restart wardragon-console.service
systemctl enable wardragon-avahi-alias.service
systemctl restart wardragon-avahi-alias.service

echo "WarDragon Console installed. Open http://localhost:4280/ or http://wardragon.local:4280/ on the kit."
