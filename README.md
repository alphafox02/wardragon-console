# WarDragon Console

A local web console for [WarDragon](https://github.com/alphafox02/WarDragon) drone detection kits. It is intended to run on the kit and answer: is this kit healthy, and what is it seeing right now?

## Current Scope

- Health dashboard from ZMQ snapshots:
  - `4225` wardragon_monitor system/GPS/DragonSDR status
  - `4227` droneid-go health
  - `4228` DragonSig health
- Drone and signal summaries from DragonSync HTTP:
  - `GET /status`
  - `GET /drones`
  - `GET /signals`
- Curated read/write view for the two allowlisted DragonSync config files:
  - `/home/dragon/WarDragon/DragonSync/config.ini`
  - `/home/dragon/WarDragon/DragonSync/gps.ini`

The console does not subscribe to high-rate drone/signal ZMQ ports and does not query systemd for service state.

## Install on a kit

If you have not cloned it yet, drop the checkout next to DragonSync inside the WarDragon directory and run the installer. The directory name is **lowercase** `wardragon-console`, matching the repo name and the on-kit install path under `/opt`:

```bash
cd ~/WarDragon
git clone https://github.com/alphafox02/wardragon-console.git
cd wardragon-console
sudo packaging/install.sh
```

The installer is opinionated and built for the WarDragon kit layout. It is honest about that: it assumes a user named `dragon`, with DragonSync at `/home/dragon/WarDragon/DragonSync`. Override with `WARDRAGON_USER=<name>` and `WARDRAGON_DRAGONSYNC_DIR=/path` if you have a different layout — or just run the installer on a TTY and it will prompt for the user. Non-default values work but are best-effort: DragonSync itself was built around `dragon`, so a different user means you also took on the path mapping for DragonSync. The installer exits cleanly if the chosen user does not exist.

`install.sh` is idempotent — re-run it any time you change packaging files or want to refresh the systemd unit.

What it does:

- apt-installs anything missing from `python3 python3-zmq rsync avahi-daemon avahi-utils python3-dbus python3-gi gir1.2-glib-2.0`. No pip and no virtualenv on the kit.
- Copies source to `/opt/wardragon-console` and creates a `/usr/local/bin/wardragon-console` wrapper.
- Installs `wardragon-console.service` (templated to the chosen user). Runs as that user, not root.
- Installs `/etc/sudoers.d/wardragon-console` with a single narrow rule letting the service account run `systemctl restart dragonsync.service` and nothing else.
- Publishes `wardragon.local` on the LAN via Avahi (`packaging/avahi/wardragon-console.service`) and a small CNAME publisher unit (`wardragon-avahi-alias.service`) that adds the alias over the Avahi D-Bus API — no `/etc/hostname` change.
- Pins `wardragon.local` to `127.0.0.1` in `/etc/hosts` on the kit only, so the kit's own browser always reaches loopback. Remote clients ignore this and keep using mDNS.
- Sets `deny-interfaces=` in `/etc/avahi/avahi-daemon.conf` to the kit's current docker bridges and veth pairs, so Avahi only announces on real network interfaces. If you create new docker networks later, re-run `install.sh`.
- Creates `<DragonSync>/certs` (mode 0700, owned by the service user) for TAK certificate uploads.
- Hardens the service unit (`ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp`, `RestrictAddressFamilies`, etc.). `NoNewPrivileges` is deliberately not set so the sudoers DragonSync restart still works.

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

## Configuration model

Useful environment overrides (set in `packaging/wardragon-console.service` for the packaged install):

```bash
WARDRAGON_DRAGONSYNC_DIR=/path/to/DragonSync
WARDRAGON_DRAGONSYNC_URL=http://127.0.0.1:8088
WARDRAGON_CONSOLE_HOST=127.0.0.1
WARDRAGON_CONSOLE_PORT=4280
WARDRAGON_CONSOLE_CONFIG_WRITE=1
WARDRAGON_CONSOLE_TETHER_ENABLED=1
WARDRAGON_CONSOLE_TETHER_CIDRS=192.168.42.0/24,192.168.43.0/24,172.20.10.0/28
WARDRAGON_CONSOLE_TETHER_CLAIM_PROFILES=10.152.47.0/24=10.152.47.250
WARDRAGON_CONSOLE_REMOTE_CONFIG_WRITE=1
WARDRAGON_CONSOLE_REMOTE_RESTART=0
WARDRAGON_CONSOLE_UPDATE_CHECK=1
WARDRAGON_CONSOLE_UPSTREAM_REPO=alphafox02/wardragon-console
WARDRAGON_DRAGONSYNC_UPSTREAM_REPO=alphafox02/DragonSync
```

The Version tab has a **Check for updates** button. It compares the local install to the configured upstream GitHub repos and tells you whether you are behind. It does not pull or apply anything — actually upgrading is still a manual `git pull && sudo packaging/install.sh`. Set `WARDRAGON_CONSOLE_UPDATE_CHECK=0` on air-gapped kits to disable the endpoint entirely.

Config writes are only allowed by default when the server is bound to loopback. If a future tether/tablet mode exposes the console on a non-loopback address, config writes stay disabled unless `WARDRAGON_CONSOLE_REMOTE_CONFIG_WRITE=1` is set.

DragonSync restart is separate. Remote/tablet config editing does not imply remote restart; keep `WARDRAGON_CONSOLE_REMOTE_RESTART=0` if tablet users should save changes but not restart the service. The default packaged service allows tablet config writes but keeps tablet restart disabled.

The config UI is intentionally curated. It does not expose Kismet or ADS-B options yet. Saves are atomic and short-circuit when nothing actually changed; real changes create a single timestamped backup beside the edited file. DragonSync still needs a restart or reboot before most changes take effect.

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

The safe default is still `127.0.0.1:4280`. When `WARDRAGON_CONSOLE_TETHER_ENABLED=1`, the console watches for USB-tether-like network interfaces and starts a second HTTP listener only while one is present. This should be enabled by default on kit images so a headless operator does not need to turn on tablet mode from the UI.

The tether listener binds to the interface IP itself, not `0.0.0.0`. It only activates for known phone/tablet tether drivers/vendors **and** expected tether subnets. This avoids opening the console on the normal WarDragon USB Ethernet adapter, which is always present on many kits.

Default tether subnets:

- Android USB tether: `192.168.42.0/24`
- Android hotspot/tether variants: `192.168.43.0/24`
- Apple personal hotspot: `172.20.10.0/28`

Override with `WARDRAGON_CONSOLE_TETHER_CIDRS` if a real device uses a different tether subnet.

Examples:

- Android USB tether usually appears as `rndis_host` / `cdc_ether` with an address like `192.168.42.x`.
- iPhone/iPad USB tether usually appears as `ipheth` or Apple USB vendor `05ac` with an address like `172.20.10.x`.

The Overview and System tabs show the current tablet URL, for example `http://172.20.10.2:4280/`.

### What `install.sh` sets up for discovery

`install.sh` apt-installs `avahi-daemon`, `avahi-utils`, `python3-dbus`, `python3-gi`, and `gir1.2-glib-2.0` if missing, then makes four small changes so the kit is reachable as `wardragon.local`:

- Installs [packaging/avahi/wardragon-console.service](packaging/avahi/wardragon-console.service) — advertises the HTTP service on port 4280 over mDNS.
- Installs [packaging/wardragon-avahi-alias](packaging/wardragon-avahi-alias), run as `wardragon-avahi-alias.service`. It publishes a `wardragon.local` CNAME alongside the kit's own `<hostname>.local` record via the Avahi D-Bus API. No `/etc/hostname` change.
- Adds a small managed block to `/etc/hosts` pinning `wardragon.local` to `127.0.0.1` on the kit only. This makes the kit's *own* browser hit loopback instead of getting a docker-bridge or veth IP back from avahi. Remote clients ignore this entry.
- Sets `deny-interfaces=` in `/etc/avahi/avahi-daemon.conf` to the kit's current docker bridges and veth pairs, so avahi only announces on real interfaces (eth/wifi/USB tether). Re-run `install.sh` if you create new docker networks later.

### Discovering the kit from the tablet

After plugging the tablet into the kit via USB and turning on USB tethering, the order to try:

1. **iPhone/iPad:** Safari resolves `.local` natively. Open `http://wardragon.local:4280/`. Done.
2. **Android Chrome:** Chrome does **not** resolve `.local` URLs by default. Either:
   - Install a service-discovery app — *Service Browser* (Druk1, free) is the most common; *Discovery - DNS-SD Browser* and *Bonjour Browser* also work. Open it on the tether network, find *"WarDragon Console on dragon"*, tap to launch the URL in a browser.
   - Or use **Firefox for Android**, which honors `.local` via the system NSD.
3. **Either platform, mDNS fails (some tethers NAT or client-isolate):**
   - Read the tether-side IP from the kit's display if HDMI is plugged in.
   - Or read it from your phone's network settings — the kit will have an address in `192.168.42.x` (Android) or `172.20.10.x` (Apple), typically `.1` or `.2`. Try `http://<that-ip>:4280/`.
   - The Overview and System tabs show the current tablet URL once you do get in, so it's easy to bookmark for next time.

### Trust model

Remote config writes are enabled in the packaged service because physical tether/local access is the intended configuration path. Remote restart remains disabled unless `WARDRAGON_CONSOLE_REMOTE_RESTART=1`. Password/token fields are masked on non-loopback tablet listeners. Saving the masked placeholder preserves the existing secret; typing a new value replaces it, and clearing the field clears it.

## Headless Access Strategy

For a no-screen kit the answer in priority order is:

1. USB tether listener is auto-enabled on trusted tether interfaces (default in the packaged unit).
2. `wardragon.local` is advertised via Avahi on every real interface (not docker/veth/bridge).
3. If the tablet can't resolve `.local` — install a discovery app per the section above.
4. If discovery also fails (rare; some carriers or USB-tether stacks block multicast), plug in HDMI/keyboard once and either bookmark the URL or set a static tether subnet override via `WARDRAGON_CONSOLE_TETHER_CIDRS`.

The kit cannot fully guarantee IP discovery on every tablet without a screen, a managed Wi-Fi AP, or a companion scanner. The combination above covers the common phones.

## License

MIT — see [LICENSE](LICENSE).
