# WarDragon Console

A local web console for [WarDragon](https://github.com/alphafox02/WarDragon) drone detection kits. It runs on the kit and answers two questions: is this kit healthy, and what is it seeing right now? It also lets an operator with physical access (HDMI/keyboard, USB tether tablet, or SSH tunnel) edit a curated subset of DragonSync and DragonScope configuration without ever touching the raw files by hand.

## Current Scope

Health dashboard from ZMQ snapshots:

- `tcp://127.0.0.1:4225` `wardragon_monitor` system/GPS/DragonSDR status
- `tcp://127.0.0.1:4227` `droneid-go` health
- `tcp://127.0.0.1:4228` DragonSig health

Drone and signal summaries from DragonSync HTTP:

- `GET /status`
- `GET /drones`
- `GET /signals`

Curated read/write config editing:

- `<DragonSync>/config.ini` and `<DragonSync>/gps.ini` (INI). Schema-validated form on the Config tab; secrets masked on non-loopback listeners.
- `<DragonScope>/dragonscope.cfg` (JSON). Remote URL, license key, listen address/port on the DragonScope tab. DragonScope re-reads every ~30 s — no service restart needed after save.

Operator actions:

- Restart `dragonsync.service` from the UI (via a narrow sudoers rule).
- Upload TAK PKCS#12 / PEM / key / CA files. Stored under `<DragonSync>/certs/` mode 0700 and written into `config.ini` as absolute paths.
- Check for newer versions of WarDragon Console and DragonSync against the upstream GitHub repos (read-only — does not pull or apply).

The console does **not** subscribe to high-rate drone/signal ZMQ ports and does not query systemd for service state. It also never auto-updates code: surfacing "an update is available" is as far as it goes.

## Install on a kit

If you have not cloned it yet, drop the checkout next to DragonSync inside the WarDragon directory and run the installer. The directory name is **lowercase** `wardragon-console`, matching the repo name and the on-kit install path under `/opt`:

```bash
cd ~/WarDragon
git clone https://github.com/alphafox02/wardragon-console.git
cd wardragon-console
sudo packaging/install.sh
```

The installer is opinionated and built for the WarDragon kit layout. It assumes a user named `dragon`, with DragonSync at `/home/dragon/WarDragon/DragonSync`. Override with `WARDRAGON_USER=<name>` and `WARDRAGON_DRAGONSYNC_DIR=/path` if you have a different layout — or just run the installer on a TTY and it will prompt for the user. Non-default values are best-effort: DragonSync itself was built around `dragon`, so a different user means you also took on the path mapping for DragonSync. The installer exits cleanly if the chosen user does not exist.

`install.sh` is idempotent — re-run it any time you change packaging files, pull a new version, or want to refresh the systemd unit and sudoers.

What it does:

- apt-installs anything missing from `python3 python3-zmq rsync avahi-daemon avahi-utils python3-dbus python3-gi gir1.2-glib-2.0`. No pip and no virtualenv on the kit.
- Copies source to `/opt/wardragon-console` and creates a `/usr/local/bin/wardragon-console` wrapper.
- Installs `wardragon-console.service` (templated to the chosen user and DragonSync / DragonScope paths). Runs as that user, not root.
- Installs the tether-alias helper at `/usr/local/bin/wardragon-tether-alias` for the stable-URL feature (see below).
- Installs `/etc/sudoers.d/wardragon-console` with **two** narrow rules: the service account may run `systemctl restart dragonsync.service` and `wardragon-tether-alias add|del`, and nothing else.
- Publishes `wardragon.local` on the LAN via Avahi (`packaging/avahi/wardragon-console.service`) and a small CNAME publisher unit (`wardragon-avahi-alias.service`) that adds the alias over the Avahi D-Bus API — no `/etc/hostname` change.
- Pins `wardragon.local` to `127.0.0.1` in `/etc/hosts` on the kit only, so the kit's own browser always reaches loopback. Remote clients ignore this entry.
- Sets `deny-interfaces=` in `/etc/avahi/avahi-daemon.conf` to the kit's current docker bridges and veth pairs, so Avahi only announces on real interfaces. Re-run `install.sh` if you create new docker networks later.
- Auto-detects whether DragonScope lives in `dragonsdr_dji_droneid` (current convention) or `antsdr_dji_droneid` (legacy) by reading the running `dragonscope.service` unit; overrides via `WARDRAGON_DRAGONSCOPE_DIR`.
- Creates `<DragonSync>/certs` (mode 0700, owned by the service user) for TAK certificate uploads.

### Hardening trade-off

The packaged systemd unit deliberately runs the console with **`NoNewPrivileges=no`** and minimal Protect/Restrict directives (`PrivateTmp=true` and that is mostly it). Most of the obvious systemd hardening (`ProtectKernelTunables=`, `RestrictNamespaces=`, `RestrictAddressFamilies=`, `LockPersonality=`, `RestrictRealtime=`, etc.) implicitly enables `NoNewPrivileges=yes`, which would silently break sudo for both the DragonSync restart and the tether-alias claim. The kit trust model is "physical/local access = trust"; the kept directives (`User=dragon` non-root, `PrivateTmp`) still meaningfully constrain damage. If you ever need stronger isolation, the right architecture is a separate root-running helper daemon talking to the console over a Unix socket, not extra `Protect*` directives.

### Optional helper: GPS as backup time source

`packaging/setup-time-sync.sh` is a separate, operator-run helper. It is **not** invoked by `install.sh` because it touches system-wide time configuration, and that is your call. Run it once per fresh kit image:

```bash
sudo packaging/setup-time-sync.sh
```

What it does (idempotent — safe to re-run):

- apt-installs `chrony`, `gpsd`, `gpsd-clients` if missing.
- Sets `START_DAEMON="true"`, `USBAUTO="true"`, `GPSD_OPTIONS="-n"` in `/etc/default/gpsd`. Leaves `DEVICES=""` so the gpsd udev rules auto-pick a plugged-in USB GPS.
- Adds a managed `refclock SHM 0` block to `/etc/chrony/chrony.conf` so chrony reads gpsd's shared-memory time samples. Stratum 10 so internet NTP stays preferred when reachable, with offset/delay tuned for non-PPS serial GPS so chrony does not mark it as a falseticker.
- Enables and (re)starts `chrony` and `gpsd.socket`.
- Backs up the originals to `*.wardragon.bak` on first run.

When internet NTP is reachable, chrony keeps using it. When the kit goes offline, chrony falls back to GPS so the system clock keeps tracking UTC instead of free-running.

### Optional helper: SDDM login-screen status widget

`packaging/setup-sddm-status.sh` installs a small status panel into the SDDM login screen — useful on headless or shared-kit setups so anyone looking at the kit's display sees system health before logging in. Currently supports the Lubuntu base SDDM theme.

```bash
sudo packaging/setup-sddm-status.sh
```

What it does (idempotent — safe to re-run):

- Copies `/usr/share/sddm/themes/lubuntu/` to `/usr/share/sddm/themes/lubuntu-wardragon/` (preserves the original Lubuntu theme as a fallback).
- Installs `packaging/sddm-theme/Main.qml` into the copy, overlaying a bottom-right widget on the existing Lubuntu greeter scene.
- Drops `/etc/sddm.conf.d/10-wardragon-theme.conf` to select the new theme, without disturbing any existing `[Autologin]` block in `/etc/sddm.conf`.

The widget polls `http://127.0.0.1:4280/api/snapshot` every 3 s and shows:

- console reachable / unreachable
- WiFi / BLE / DJI receiver dots (filtered from droneid-go sources; UART / Sniffle are intentionally hidden)
- DragonSig SDR state, phase/mode, noise floor
- current drone and signal counts
- tablet URL — stable when the claim profile matches, dynamic for any other recognized phone tether

To see it, log out of your current session — SDDM redraws on next login. Revert with `sudo rm /etc/sddm.conf.d/10-wardragon-theme.conf && sudo systemctl restart sddm`. The widget cannot block login: an unreachable console or a parse error just leaves the panel showing "console unreachable", and the rest of the greeter stays interactive.

This is **not** invoked by `install.sh` because changing the SDDM theme is a system-wide change that can disrupt running graphical sessions, and that decision belongs to the operator.

## Updating

Idempotent re-install is the supported update path:

```bash
cd ~/WarDragon/wardragon-console
git pull
sudo packaging/install.sh
```

Each run apt-installs anything new, rsyncs source to `/opt/wardragon-console/src/` with `--delete`, re-templates the systemd unit and sudoers, and restarts `wardragon-console.service`. The **Check for updates** button on the Version tab tells you when there is something to pull (read-only — no auto-apply).

## Configuration model

Useful environment overrides (set in `packaging/wardragon-console.service` for the packaged install):

```bash
WARDRAGON_DRAGONSYNC_DIR=/path/to/DragonSync
WARDRAGON_DRAGONSYNC_URL=http://127.0.0.1:8088
WARDRAGON_DRAGONSCOPE_DIR=/path/to/dragonsdr_dji_droneid
WARDRAGON_CONSOLE_HOST=127.0.0.1
WARDRAGON_CONSOLE_PORT=4280
WARDRAGON_CONSOLE_CONFIG_WRITE=1
WARDRAGON_CONSOLE_REMOTE_CONFIG_WRITE=1
WARDRAGON_CONSOLE_REMOTE_RESTART=1
WARDRAGON_CONSOLE_TETHER_ENABLED=1
WARDRAGON_CONSOLE_TETHER_CIDRS=192.168.42.0/24,192.168.43.0/24,172.20.10.0/28
WARDRAGON_CONSOLE_TETHER_CLAIM_PROFILES=10.152.47.0/24=10.152.47.250
WARDRAGON_CONSOLE_UPDATE_CHECK=1
WARDRAGON_CONSOLE_UPSTREAM_REPO=alphafox02/wardragon-console
WARDRAGON_DRAGONSYNC_UPSTREAM_REPO=alphafox02/DragonSync
```

Config writes are only allowed by default when the server is bound to loopback. The packaged service enables `WARDRAGON_CONSOLE_REMOTE_CONFIG_WRITE=1` and `WARDRAGON_CONSOLE_REMOTE_RESTART=1` so a tethered tablet can both edit configs and restart DragonSync. The trust model is "if you can plug into the USB tether, you have physical access." Flip either to `0` if you want to limit the tablet to read-only or to config-only.

The config UI is intentionally curated. It does not expose Kismet or ADS-B options yet. Saves are atomic and short-circuit when nothing actually changed; real changes create a single timestamped backup beside the edited file. DragonSync still needs a restart or reboot before most changes take effect; DragonScope re-reads every ~30 s automatically.

TAK certificate upload stores files under `<DragonSync>/certs/` and writes absolute paths into `config.ini`. Absolute paths are intentional even though DragonSync runs with `WorkingDirectory=<DragonSync>` — they survive future service changes and remove ambiguity.

## Run for development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
wardragon-console
```

Open `http://localhost:4280/`. No system install needed for iteration — everything reads from the working tree, and tests run with `PYTHONPATH=src python3 -m unittest discover -s tests`.

## Tablet/Tether Access

The safe default is `127.0.0.1:4280` for the kit's own browser. When `WARDRAGON_CONSOLE_TETHER_ENABLED=1`, the console watches for USB-tether-like network interfaces and starts a second HTTP listener only while one is present. The listener binds to the interface IP itself, not `0.0.0.0`, so it never accidentally exposes the console on the LAN.

### Tether interface detection

A USB interface is recognized as a tether when:

- **Driver is unambiguously phone-tether** (`rndis_host`, `cdc_ncm`, `ipheth`): trusted without further filtering. These drivers are not used by any real-world USB-Ethernet dongle, so the match is enough.
- **USB vendor is Apple (`05ac`)** regardless of driver: covers older iOS paths.
- **Driver is `cdc_ether`** (ambiguous — both phones and generic USB-Ethernet dongles use it): trusted only when either the USB vendor is a known phone maker (Samsung `04e8`, Google `18d1`, Xiaomi `2717`, OnePlus `2a70`, Huawei `12d1`, Motorola `22b8`, LG `1004`) **or** the IP lands in `WARDRAGON_CONSOLE_TETHER_CIDRS`.
- IP must be RFC1918 private.
- The AntSDR private link (`172.31.100.0/24`) is **never** treated as a tether.

The `WARDRAGON_CONSOLE_TETHER_CIDRS` default (`192.168.42.0/24`, `192.168.43.0/24`, `172.20.10.0/28`) only matters for the ambiguous `cdc_ether` case now. Modern Android (Samsung, Pixel, etc. using `rndis_host`) is recognized regardless of what subnet the phone hands out.

### Stable URL for shipped tablets

The default `WARDRAGON_CONSOLE_TETHER_CLAIM_PROFILES=10.152.47.0/24=10.152.47.250` means: when a tether comes up with an IP inside `10.152.47.0/24` (the Samsung tablet shipping convention), the console adds `10.152.47.250` as a **secondary IP** on that interface and opens a second listener bound to it. The customer URL becomes a stable `http://10.152.47.250:4280/` regardless of which DHCP lease the tablet hands out, across replugs and across reboots. The alias is added via the `wardragon-tether-alias` helper through a narrow sudoers rule; it is in-kernel state only (no NetworkManager / netplan / `/etc/network/interfaces` change) and disappears automatically when the tether interface goes away.

Format: comma-separated `network=alias_ip` pairs — e.g. `192.168.42.0/24=192.168.42.250,10.152.47.0/24=10.152.47.250`. Empty string (`WARDRAGON_CONSOLE_TETHER_CLAIM_PROFILES=`) disables the feature. iOS lands in `172.20.10.0/28` by Apple convention so no profile is needed there — Safari handles `.local` natively.

### Discovering the kit from the tablet

The reliability of each path depends on the tablet OS. **Honest summary up front**: on Android, `wardragon.local` over USB tether does **not** work — Android suppresses mDNS in the tablet→kit direction, so neither Chrome, Firefox, nor any "Service Browser"-style app sees the announce. iOS and any tablet on Wi-Fi work fine with `.local`. So:

1. **iPhone/iPad over USB tether or Wi-Fi:** Safari resolves `.local` via Bonjour natively. Open `http://wardragon.local:4280/`. Done.
2. **Android over Wi-Fi (not USB tether):** mDNS works in that direction. Use Firefox for Android (honors `.local`) or install a discovery app like *Service Browser* (Druk1) and tap *"WarDragon Console on dragon"*. Chrome on Android does not resolve `.local`.
3. **Android over USB tether (the headless-kit shipping case):** type the **stable URL** `http://10.152.47.250:4280/` directly in Chrome. This is why the static IP alias profile exists — the URL is fixed across replugs and reboots, so the customer just has it from the docs.
4. **Anywhere, mDNS fails:** the Overview and System tabs show the current tether URL (dynamic and stable) once you reach the console once. Bookmark either.

The Overview tab also shows the **stable URL** prominently in operator notes whenever a claim profile is active, so an operator reading the kit display via HDMI sees the current customer URL right there.

### Trust model

Physical access to the kit = full trust. The packaged service enables remote config writes **and** remote DragonSync restart from any tether listener; the assumption is that the operator has physical control of the USB tether path. To lock down further: unplug the internal USB cable to the tether port (which is a kit-board-level option), pad-lock the enclosure, or set `WARDRAGON_CONSOLE_REMOTE_CONFIG_WRITE=0` / `WARDRAGON_CONSOLE_REMOTE_RESTART=0` to require an SSH tunnel for those operations. Cross-origin POST/PUT is rejected by the request handler regardless of bind. Password/token fields are masked on non-loopback listeners; saving the masked placeholder preserves the existing secret, typing a new value replaces it, clearing the field clears it.

## Headless Access Strategy

For a no-screen kit, in priority order:

1. USB tether listener is auto-enabled (default in the packaged unit).
2. If the customer tablet is in your shipping bundle, the **stable URL** is the most reliable customer-facing answer. With the default claim profile and a Samsung tablet, that URL is `http://10.152.47.250:4280/`. One typed URL, no app install, no IP discovery on the tablet side. The customer gets it from the docs you ship with the kit.
3. iPad/iPhone users get `wardragon.local:4280` via Safari without doing anything.
4. Android-with-Wi-Fi users get `wardragon.local:4280` via Firefox or *Service Browser*.
5. If all else fails: HDMI/keyboard for one boot, read the URL from the Overview tab, bookmark it.

The console cannot guarantee zero-friction discovery on every phone model and every browser. The stable-URL profile is the only path that works regardless of Android's mDNS quirks.

## License

MIT — see [LICENSE](LICENSE).
