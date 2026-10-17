// Map tab. Loaded on first open; polls /api/map only while the tab is visible in Live mode.
(() => {
  const POLL_MS = 3000;
  const TILE_TIMEOUT_MS = 8000;
  const RINGS_M = [250, 500, 1000, 2000, 5000];
  const LAYERS = [
    ["drones", "Drones"],
    ["trails", "Paths"],
    ["pilots", "Pilots"],
    ["homes", "Home points"],
    ["aircraft", "Aircraft"],
    ["signals", "Signals"],
    ["kit", "Kit"],
  ];

  let map = null;
  const groups = {};
  let mode = "live";
  let active = false;
  let running = false;
  let timer = null;
  let baseLayer = null;
  let baseChoice = "auto";
  let baseHint = "";
  let activeOffline = null;
  let tiles = null;
  let offline = { maps: [], loaded: false };
  let fitted = false;
  let options = { hasDragonSig: true };
  const layerOn = Object.fromEntries(LAYERS.map(([key]) => [key, true]));
  const hiddenTracks = new Set();
  const markers = new Map();
  let kitMarker = null;
  let kitRings = [];
  let lastPayload = null;
  let history = { status: null, data: null, selected: null, selectedLayers: [] };

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const fmt = (value, unit, digits = 0) => (value === null || value === undefined ? "—" : `${Number(value).toFixed(digits)} ${unit}`);
  const ago = (seconds) => {
    if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s ago`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
    return `${Math.round(seconds / 3600)}h ago`;
  };
  const when = (t) => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const duration = (seconds) => (seconds < 90 ? `${Math.round(seconds)}s` : `${Math.round(seconds / 60)} min`);
  const distance = (m) => (m === null || m === undefined ? "—" : m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`);

  function init() {
    map = L.map("map-canvas", { worldCopyJump: true }).setView([39.5, -98.35], 4);
    L.control.scale({ metric: true, imperial: true }).addTo(map);
    const ZoomLevel = L.Control.extend({
      onAdd() {
        const el = L.DomUtil.create("div", "map-zoom-level");
        const update = () => { el.textContent = `Zoom ${map.getZoom()}`; };
        map.on("zoomend", update);
        update();
        return el;
      },
    });
    new ZoomLevel({ position: "bottomleft" }).addTo(map);
    map.on("moveend", checkOfflineCoverage);
    for (const [key] of LAYERS) groups[key] = L.layerGroup().addTo(map);
    groups.history = L.layerGroup().addTo(map);
    $("map-layers").addEventListener("change", onLayerToggle);
    $("map-tracks").addEventListener("change", onTrackToggle);
    $("map-tracks").addEventListener("click", onListClick);
    $("map-mode").addEventListener("click", onModeClick);
    $("map-range").addEventListener("change", loadHistory);
    $("map-export-csv").addEventListener("click", () => exportHistory("csv"));
    $("map-export-kml").addEventListener("click", () => exportHistory("kml"));
    $("map-history-form").addEventListener("change", onSettingsChange);
    $("map-history-form").addEventListener("click", onSettingsClick);
    $("map-offline-body").addEventListener("click", onOfflineClick);
    $("map-base").addEventListener("change", (event) => { baseChoice = event.target.value; applyBase(); });
    renderLayerList();
    loadOffline();
  }


  function setStatus(text, tone) {
    const el = $("map-status");
    el.textContent = text;
    el.className = `pill ${tone || ""}`;
  }

  function clearBase() {
    if (baseLayer) map.removeLayer(baseLayer);
    baseLayer = null;
    baseHint = "";
    activeOffline = null;
    $("map-canvas").classList.remove("map-grid");
  }

  function showOnline(onFail) {
    let loaded = 0;
    let failed = 0;
    const layer = L.tileLayer(tiles.url, {
      maxZoom: 19,
      attribution: esc(tiles.attribution),
      referrerPolicy: "strict-origin-when-cross-origin",
    });
    const fail = () => {
      if (loaded > 0 || baseLayer !== layer) return;
      onFail();
    };
    layer.on("tileload", () => {
      loaded += 1;
      if (loaded === 1 && baseLayer === layer) setStatus("Online map", "ok");
    });
    layer.on("tileerror", () => {
      failed += 1;
      if (failed >= 3) fail();
    });
    baseLayer = layer.addTo(map);
    setStatus("Loading map…");
    setTimeout(fail, TILE_TIMEOUT_MS);
  }

  function showOffline(entry) {
    clearBase();
    baseLayer = L.tileLayer(`/api/map/offline/tile/${encodeURIComponent(entry.name)}/{z}/{x}/{y}`, {
      minNativeZoom: entry.minzoom,
      maxNativeZoom: entry.maxzoom,
      maxZoom: 19,
      bounds: entry.bounds ? L.latLngBounds(entry.bounds) : undefined,
      attribution: esc(entry.attribution),
    }).addTo(map);
    activeOffline = entry;
    setStatus(`Offline map: ${entry.label}`, "ok");
    checkOfflineCoverage();
  }

  function checkOfflineCoverage() {
    if (!activeOffline) return;
    const zoom = map.getZoom();
    const outsideArea = activeOffline.bounds && !L.latLngBounds(activeOffline.bounds).intersects(map.getBounds());
    if (outsideArea) baseHint = `You are outside the area of ${activeOffline.label}. Use Show under Offline maps to go there.`;
    else if (zoom > activeOffline.maxzoom) baseHint = `${activeOffline.label} has detail up to zoom ${activeOffline.maxzoom}; zoom out for a sharper map.`;
    else baseHint = "";
    $("map-note").textContent = baseHint;
  }

  function showOfflineArea(name) {
    const entry = validOffline().find((m) => m.name === name);
    if (!entry) return;
    baseChoice = `offline:${name}`;
    applyBase();
    if (entry.bounds) map.fitBounds(L.latLngBounds(entry.bounds), { maxZoom: entry.maxzoom });
  }

  function showGrid(label, hint) {
    clearBase();
    const Grid = L.GridLayer.extend({
      createTile() {
        const tile = document.createElement("canvas");
        tile.width = 256;
        tile.height = 256;
        const ctx = tile.getContext("2d");
        ctx.strokeStyle = "rgba(98, 104, 98, 0.18)";
        ctx.strokeRect(0.5, 0.5, 255, 255);
        return tile;
      },
    });
    baseLayer = new Grid({ maxZoom: 19 }).addTo(map);
    $("map-canvas").classList.add("map-grid");
    setStatus(label, "warn");
    if (hint) {
      baseHint = "No map tiles. You can add an offline map under Offline maps.";
      $("map-note").textContent = baseHint;
    }
  }

  const validOffline = () => offline.maps.filter((m) => m.ok);

  function applyBase() {
    if (!tiles || !offline.loaded) return;
    clearBase();
    const maps = validOffline();
    const picked = maps.find((m) => `offline:${m.name}` === baseChoice);
    const fallback = () => (maps.length ? showOffline(maps[0]) : showGrid("Grid (no map tiles)", true));
    if (baseChoice === "grid") showGrid("Grid");
    else if (picked) showOffline(picked);
    else if (baseChoice === "online" && tiles.url) showOnline(() => showGrid("Grid (no map tiles)", true));
    else if (tiles.url) showOnline(fallback);
    else fallback();
    renderBaseChoice();
  }

  function renderBaseChoice() {
    const maps = validOffline();
    const field = $("map-base-field");
    field.hidden = maps.length === 0;
    if (field.hidden) return;
    const choices = [["auto", "Automatic"], ...(tiles?.url ? [["online", "Online"]] : []),
      ...maps.map((m) => [`offline:${m.name}`, `Offline: ${m.label}`]), ["grid", "Grid only"]];
    if (!choices.some(([value]) => value === baseChoice)) baseChoice = "auto";
    $("map-base").innerHTML = choices
      .map(([value, label]) => `<option value="${esc(value)}"${value === baseChoice ? " selected" : ""}>${esc(label)}</option>`)
      .join("");
  }

  function setTiles(value) {
    if (tiles) return;
    tiles = value || { url: "" };
    applyBase();
  }


  async function loadOffline() {
    try {
      const response = await fetch("/api/map/offline", { cache: "no-store" });
      if (!response.ok) throw new Error(response.statusText);
      offline = { ...(await response.json()), loaded: true };
    } catch (error) {
      offline = { maps: [], loaded: true, error: "Cannot list offline maps." };
    }
    renderOffline();
    applyBase();
  }

  function renderOffline() {
    const mb = (bytes) => `${Math.round(bytes / 1048576)} MB`;
    const rows = (offline.maps || []).map((m) => `
      <div class="map-row">
        <span class="map-flight-label">${esc(m.ok ? m.label : m.name)}</span>
        <div class="map-row-meta">
          <span class="subtle">${m.ok ? `Zoom ${m.minzoom}–${m.maxzoom} · ${mb(m.size)}` : `<span class="map-error">${esc(m.error)}</span>`}</span>
          <span class="map-row-actions">
            ${m.ok ? `<button class="link-button" type="button" data-show-map="${esc(m.name)}">Show</button>` : ""}
            ${offline.can_change ? `<button class="link-button map-delete" type="button" data-delete="${esc(m.name)}">Delete</button>` : ""}
          </span>
        </div>
      </div>`).join("");
    const upload = offline.can_change ? `
      <input type="file" id="map-offline-file" class="map-file" accept=".mbtiles">
      <button class="secondary map-upload" type="button" id="map-offline-upload">Upload map</button>
      <div class="subtle map-settings-info" id="map-offline-progress"></div>` :
      '<div class="subtle map-settings-info">Offline maps can only be added from the kit itself.</div>';
    const limit = offline.max_upload_bytes ? mb(offline.max_upload_bytes) : "";
    $("map-offline-body").innerHTML = `
      ${rows || '<div class="subtle map-settings-info">No offline maps installed.</div>'}
      ${offline.error ? `<div class="notice error">${esc(offline.error)}</div>` : ""}
      ${upload}
      <div class="subtle map-settings-info">Raster .mbtiles files only, up to ${esc(limit)}${offline.free_bytes ? ` · ${esc(mb(offline.free_bytes))} free on the kit` : ""}. Larger files can be copied to ${esc(offline.dir || "the maps folder")} over SSH.</div>`;
  }

  function onOfflineClick(event) {
    if (event.target.id === "map-offline-upload") uploadOffline();
    if (event.target.dataset.showMap) showOfflineArea(event.target.dataset.showMap);
    const del = event.target.dataset.delete;
    if (del && window.confirm(`Delete the offline map ${del}?`)) deleteOffline(del);
  }

  function uploadOffline() {
    const input = $("map-offline-file");
    const file = input?.files?.[0];
    const progress = $("map-offline-progress");
    if (!file) { progress.textContent = "Choose an .mbtiles file first."; return; }
    if (offline.max_upload_bytes && file.size > offline.max_upload_bytes) {
      progress.textContent = "That file is larger than the upload limit.";
      return;
    }
    const button = $("map-offline-upload");
    button.disabled = true;
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/map/offline/upload");
    xhr.setRequestHeader("X-Filename", file.name);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) progress.textContent = `Uploading… ${Math.round((e.loaded / e.total) * 100)}%`;
    };
    xhr.onload = () => {
      let payload = {};
      try { payload = JSON.parse(xhr.responseText); } catch (error) { /* non-JSON error page */ }
      button.disabled = false;
      if (xhr.status === 200 && payload.ok) {
        progress.textContent = "";
        loadOffline().then(() => { if (payload.map?.ok) showOfflineArea(payload.map.name); });
      } else {
        progress.textContent = `Upload failed: ${payload.error || xhr.statusText || "unknown error"}`;
      }
    };
    xhr.onerror = () => {
      button.disabled = false;
      progress.textContent = "Upload failed: connection lost.";
    };
    progress.textContent = "Uploading…";
    xhr.send(file);
  }

  async function deleteOffline(name) {
    try {
      const response = await fetch("/api/map/offline/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || response.statusText);
    } catch (error) {
      $("map-note").textContent = `Could not delete: ${error.message}`;
    }
    if (baseChoice === `offline:${name}`) baseChoice = "auto";
    loadOffline();
  }


  function visibleLayers() {
    return LAYERS.filter(([key]) => {
      if (key === "signals" && !options.hasDragonSig) return false;
      if (mode === "history" && key === "drones") return false;
      return true;
    });
  }

  function renderLayerList() {
    $("map-layers").innerHTML = visibleLayers()
      .map(([key, label]) => `<label class="map-check"><input type="checkbox" data-layer="${key}"${layerOn[key] ? " checked" : ""}><span>${esc(label)}</span></label>`)
      .join("");
  }

  function applyLayers() {
    for (const [key] of LAYERS) {
      const show = layerOn[key] && (key !== "signals" || options.hasDragonSig) && (mode === "live" || key === "kit");
      if (show) groups[key].addTo(map);
      else map.removeLayer(groups[key]);
    }
    if (mode === "history") groups.history.addTo(map);
    else map.removeLayer(groups.history);
    if (mode === "history" && history.data) drawHistory();
  }

  function onLayerToggle(event) {
    const key = event.target.dataset.layer;
    if (!key) return;
    layerOn[key] = event.target.checked;
    applyLayers();
  }

  function onModeClick(event) {
    const button = event.target.closest("[data-mode]");
    if (!button || button.dataset.mode === mode) return;
    mode = button.dataset.mode;
    for (const el of $("map-mode").querySelectorAll("button")) el.classList.toggle("active", el.dataset.mode === mode);
    $("map-history-controls").hidden = mode !== "history";
    $("map-history-settings").hidden = mode !== "history";
    $("map-list-title").textContent = mode === "history" ? "Flights" : "Live tracks";
    $("map-note").textContent = "";
    renderLayerList();
    applyLayers();
    if (mode === "history") {
      loadHistory();
    } else {
      if (lastPayload) render(lastPayload);
      startPolling();
    }
  }


  function onTrackToggle(event) {
    const key = event.target.dataset.track;
    if (!key) return;
    if (event.target.checked) hiddenTracks.delete(key);
    else hiddenTracks.add(key);
    if (lastPayload) render(lastPayload);
  }

  function onListClick(event) {
    const focus = event.target.closest("[data-focus]");
    if (focus) {
      const entry = markers.get(focus.dataset.focus);
      if (entry) {
        map.setView(entry.marker.getLatLng(), Math.max(map.getZoom(), 16));
        entry.marker.openPopup();
      }
      return;
    }
    const flight = event.target.closest("[data-flight]");
    if (flight) selectFlight(Number(flight.dataset.flight));
  }

  function arrowIcon(track) {
    const cls = `map-arrow ${track.type}${track.live ? "" : " stale"}`;
    const shape = track.heading === null
      ? '<circle cx="12" cy="12" r="6"/>'
      : '<path d="M12 2 L19 21 L12 16.5 L5 21 Z"/>';
    return L.divIcon({
      className: "map-icon",
      html: `<div class="${cls}"><svg viewBox="0 0 24 24" aria-hidden="true">${shape}</svg></div>`,
      iconSize: [26, 26],
      iconAnchor: [13, 13],
      popupAnchor: [0, -12],
    });
  }

  function rotate(marker, heading) {
    const el = marker.getElement()?.querySelector(".map-arrow");
    if (el) el.style.transform = heading === null ? "" : `rotate(${heading}deg)`;
  }

  function popupHtml(track, now) {
    const rows = [
      ["Type", track.type === "aircraft" ? "Aircraft" : (track.detail.ua_type || "Drone")],
      ["ID", track.id],
      ["Altitude", fmt(track.alt, "m")],
      ["Speed", fmt(track.speed, "m/s", 1)],
      ["Heading", track.heading === null ? "—" : `${Math.round(track.heading)}°`],
      ["Last seen", ago(now - track.last_seen)],
    ];
    for (const [key, label] of [["make_model", "Make/model"], ["operator_id", "Operator ID"], ["transport", "Transport"], ["rssi", "RSSI"], ["hex", "ICAO"], ["squawk", "Squawk"], ["reg", "Registration"]]) {
      if (track.detail[key] !== undefined) rows.push([label, track.detail[key]]);
    }
    if (track.pilot) rows.push(["Pilot", `${track.pilot.lat.toFixed(5)}, ${track.pilot.lon.toFixed(5)}`]);
    return `<strong>${esc(track.label)}</strong>${track.live ? "" : ' <span class="subtle">(not currently seen)</span>'}${popupRows(rows)}`;
  }

  const popupRows = (rows) => `<dl class="map-popup">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;

  function render(payload) {
    lastPayload = payload;
    const now = payload.updated_at || Date.now() / 1000;
    renderKit(payload.kit);
    if (mode !== "live") return;

    const seen = new Set();
    for (const track of payload.tracks) {
      const key = `${track.type}:${track.id}`;
      if (hiddenTracks.has(key)) continue;
      seen.add(key);
      const latlng = [track.lat, track.lon];
      const group = track.type === "aircraft" ? groups.aircraft : groups.drones;
      let entry = markers.get(key);
      if (!entry) {
        entry = { marker: L.marker(latlng, { icon: arrowIcon(track) }).bindPopup(""), shape: "" };
        entry.marker.addTo(group);
        entry.trail = L.polyline([], { className: `map-trail ${track.type}`, interactive: false }).addTo(groups.trails);
        markers.set(key, entry);
      }
      const shape = `${track.live}|${track.heading === null}`;
      if (entry.shape !== shape) {
        entry.marker.setIcon(arrowIcon(track));
        entry.shape = shape;
      }
      entry.marker.setLatLng(latlng);
      rotate(entry.marker, track.heading);
      entry.marker.setPopupContent(popupHtml(track, now));
      entry.trail.setLatLngs(track.trail);
      updatePoint(entry, "pilot", track.pilot, groups.pilots, "map-pilot", `Pilot of ${track.label}`, latlng);
      updatePoint(entry, "home", track.home, groups.homes, "map-home", `Home point of ${track.label}`, null);
    }
    for (const [key, entry] of markers) {
      if (seen.has(key)) continue;
      for (const layer of [entry.marker, entry.trail, entry.pilot, entry.pilotLink, entry.home]) {
        if (layer) layer.remove();
      }
      markers.delete(key);
    }

    groups.signals.clearLayers();
    for (const signal of payload.signals) addSignal(signal, groups.signals);

    renderTrackList(payload.tracks, now);
    if (!fitted) fitOnce(payload.tracks.map((t) => [t.lat, t.lon]), payload.kit);
  }

  function addSignal(signal, group) {
    const extra = signal.first_seen ? `<br>${esc(when(signal.first_seen))} – ${esc(when(signal.last_seen))}` : "";
    L.circle([signal.lat, signal.lon], { radius: signal.radius_m || 150, className: "map-signal" })
      .bindPopup(`<strong>${esc(signal.label)}</strong>${signal.freq_mhz ? `<br>${esc(signal.freq_mhz)} MHz` : ""}${extra}`)
      .addTo(group);
  }

  function updatePoint(entry, name, point, group, className, title, linkFrom) {
    if (!point) {
      if (entry[name]) { entry[name].remove(); entry[name] = null; }
      if (linkFrom && entry.pilotLink) { entry.pilotLink.remove(); entry.pilotLink = null; }
      return;
    }
    const latlng = [point.lat, point.lon];
    if (!entry[name]) {
      entry[name] = L.circleMarker(latlng, { radius: 6, className }).bindTooltip(esc(title)).addTo(group);
    } else {
      entry[name].setLatLng(latlng);
    }
    if (linkFrom) {
      if (!entry.pilotLink) {
        entry.pilotLink = L.polyline([linkFrom, latlng], { className: "map-pilot-link", interactive: false }).addTo(group);
      } else {
        entry.pilotLink.setLatLngs([linkFrom, latlng]);
      }
    }
  }

  function renderKit(kit) {
    if (!kit) {
      if (kitMarker) { kitMarker.remove(); kitMarker = null; }
      kitRings.forEach((ring) => ring.remove());
      kitRings = [];
      return;
    }
    const latlng = [kit.lat, kit.lon];
    if (!kitMarker) {
      kitMarker = L.circleMarker(latlng, { radius: 8, className: "map-kit" }).bindTooltip("WarDragon kit").addTo(groups.kit);
      kitRings = RINGS_M.map((radius) => L.circle(latlng, { radius, className: "map-ring", interactive: false }).addTo(groups.kit));
    } else {
      kitMarker.setLatLng(latlng);
      kitRings.forEach((ring) => ring.setLatLng(latlng));
    }
  }

  function fitOnce(points, kit) {
    const all = [...points];
    if (kit) all.push([kit.lat, kit.lon]);
    if (!all.length) return;
    fitted = true;
    if (all.length === 1) map.setView(all[0], 15);
    else map.fitBounds(L.latLngBounds(all).pad(0.25), { maxZoom: 16 });
  }

  function renderTrackList(tracks, now) {
    const list = $("map-tracks");
    if (!tracks.length) {
      list.innerHTML = '<div class="subtle">No tracks with a position yet.</div>';
      return;
    }
    list.innerHTML = tracks.map((track) => {
      const key = `${track.type}:${track.id}`;
      const visible = !hiddenTracks.has(key);
      return `<div class="map-row${track.live ? "" : " stale"}">
        <label class="map-check"><input type="checkbox" data-track="${esc(key)}"${visible ? " checked" : ""}><span>${esc(track.label)}</span></label>
        <div class="map-row-meta">
          <span class="subtle">${track.type === "aircraft" ? "Aircraft" : "Drone"} · ${ago(now - track.last_seen)}</span>
          ${visible ? `<button class="link-button" type="button" data-focus="${esc(key)}">Show</button>` : ""}
        </div>
      </div>`;
    }).join("");
  }

  async function poll() {
    timer = null;
    if (!active || mode !== "live") { running = false; return; }
    if (document.visibilityState === "visible") {
      try {
        const response = await fetch("/api/map", { cache: "no-store" });
        if (!response.ok) throw new Error(response.statusText);
        const payload = await response.json();
        setTiles(payload.tiles);
        render(payload);
        if (mode === "live") $("map-note").textContent = payload.kit ? baseHint : "No kit GPS position yet.";
      } catch (error) {
        $("map-note").textContent = "Cannot reach the console.";
      }
    }
    if (active && mode === "live") timer = setTimeout(poll, POLL_MS);
    else running = false;
  }

  function startPolling() {
    if (!running) { running = true; poll(); }
  }


  function historyWindow() {
    const to = Date.now() / 1000;
    return { from: to - Number($("map-range").value), to };
  }

  async function loadHistory() {
    if (mode !== "history") return;
    if (!tiles) {
      try {
        const live = await (await fetch("/api/map", { cache: "no-store" })).json();
        setTiles(live.tiles);
        renderKit(live.kit);
      } catch (error) { /* base map waits for the next successful request */ }
    }
    const list = $("map-tracks");
    try {
      history.status = await (await fetch("/api/map/history/status", { cache: "no-store" })).json();
    } catch (error) {
      list.innerHTML = '<div class="notice error">Cannot reach the console.</div>';
      return;
    }
    renderSettings();
    const enabled = history.status.enabled;
    $("map-export-csv").disabled = !enabled;
    $("map-export-kml").disabled = !enabled;
    if (!enabled) {
      history.data = null;
      groups.history.clearLayers();
      list.innerHTML = '<div class="subtle">History is off. Turn it on under History settings to keep flights on this kit.</div>';
      $("map-history-settings").open = true;
      return;
    }
    const { from, to } = historyWindow();
    list.innerHTML = '<div class="subtle">Loading…</div>';
    try {
      const response = await fetch(`/api/map/history?from=${from}&to=${to}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || response.statusText);
      history.data = payload;
    } catch (error) {
      list.innerHTML = `<div class="notice error">${esc(error.message)}</div>`;
      return;
    }
    if (history.selected && !history.data.flights.some((f) => f.id === history.selected.id)) history.selected = null;
    drawHistory();
    renderFlightList();
    const points = history.data.flights.flatMap((f) => (f.path || []).slice(-1));
    if (points.length) map.fitBounds(L.latLngBounds(points).pad(0.3), { maxZoom: 16 });
    $("map-note").textContent = history.data.truncated ? "Showing the most recent 500 flights." : "";
  }

  function drawHistory() {
    groups.history.clearLayers();
    if (!history.data) return;
    for (const flight of history.data.flights) {
      if (!flight.path || flight.path.length < 1) continue;
      if (flight.type === "aircraft" ? !layerOn.aircraft : !layerOn.trails) continue;
      const selected = history.selected?.id === flight.id;
      const line = L.polyline(flight.path, { className: `map-trail ${flight.type}${selected ? " selected" : " faded"}` });
      line.on("click", () => selectFlight(flight.id));
      line.addTo(groups.history);
    }
    if (layerOn.signals && options.hasDragonSig) {
      for (const signal of history.data.signals) addSignal(signal, groups.history);
    }
    if (history.selected) drawSelected(history.selected);
  }

  function drawSelected(flight) {
    if (!flight.path.length) return;
    const start = flight.path[0];
    const end = flight.path[flight.path.length - 1];
    L.polyline(flight.path, { className: `map-trail ${flight.type} selected`, interactive: false }).addTo(groups.history);
    L.circleMarker(start, { radius: 5, className: "map-start" }).bindTooltip(`Start ${esc(when(flight.first_seen))}`).addTo(groups.history);
    L.circleMarker(end, { radius: 6, className: "map-end" })
      .bindPopup(`<strong>${esc(flight.label)}</strong>${popupRows(flightRows(flight))}`)
      .addTo(groups.history);
    if (layerOn.pilots && flight.pilot) {
      L.circleMarker([flight.pilot.lat, flight.pilot.lon], { radius: 6, className: "map-pilot" }).bindTooltip("Pilot").addTo(groups.history);
    }
    if (layerOn.homes && flight.home) {
      L.circleMarker([flight.home.lat, flight.home.lon], { radius: 6, className: "map-home" }).bindTooltip("Home point").addTo(groups.history);
    }
  }

  function flightRows(flight) {
    const rows = [
      ["ID", flight.track_id],
      ["Seen", `${when(flight.first_seen)} (${duration(flight.last_seen - flight.first_seen)})`],
      ["Max altitude", fmt(flight.max_alt, "m")],
      ["Closest to kit", distance(flight.min_kit_dist)],
    ];
    if (flight.make_model) rows.push(["Make/model", flight.make_model]);
    if (flight.operator_id) rows.push(["Operator ID", flight.operator_id]);
    if (flight.transport) rows.push(["Transport", flight.transport]);
    return rows;
  }

  async function selectFlight(id) {
    if (history.selected?.id === id) {
      history.selected = null;
      drawHistory();
      renderFlightList();
      return;
    }
    try {
      const payload = await (await fetch(`/api/map/history/flight?flight=${id}`, { cache: "no-store" })).json();
      if (!payload.ok) throw new Error(payload.error || "flight not found");
      history.selected = payload.flight;
    } catch (error) {
      $("map-note").textContent = error.message;
      return;
    }
    drawHistory();
    renderFlightList();
    if (history.selected.path.length) map.fitBounds(L.latLngBounds(history.selected.path).pad(0.3), { maxZoom: 17 });
  }

  function renderFlightList() {
    const list = $("map-tracks");
    const flights = history.data?.flights || [];
    if (!flights.length) {
      list.innerHTML = '<div class="subtle">No flights in this time range.</div>';
      return;
    }
    list.innerHTML = flights.map((flight) => `
      <button type="button" class="map-row map-flight${history.selected?.id === flight.id ? " selected" : ""}" data-flight="${flight.id}">
        <span class="map-flight-label">${esc(flight.label || flight.track_id)}</span>
        <span class="subtle">${esc(when(flight.first_seen))} · ${esc(duration(flight.last_seen - flight.first_seen))} · closest ${esc(distance(flight.min_kit_dist))}</span>
      </button>`).join("");
  }

  function exportHistory(format) {
    const { from, to } = historyWindow();
    const flight = history.selected ? `&flight=${history.selected.id}` : "";
    const link = document.createElement("a");
    link.href = `/api/map/history/export?format=${format}&from=${from}&to=${to}${flight}`;
    link.download = "";
    document.body.appendChild(link);
    link.click();
    link.remove();
    $("map-note").textContent = history.selected ? "Exported the selected flight." : "Exported all flights in this range.";
  }

  function renderSettings() {
    const s = history.status;
    const disabled = s.can_change ? "" : " disabled";
    const mb = (bytes) => `${(bytes / 1048576).toFixed(bytes < 10485760 ? 1 : 0)} MB`;
    $("map-history-form").innerHTML = `
      <label class="map-check"><input type="checkbox" data-setting="enabled"${s.enabled ? " checked" : ""}${disabled}><span>Keep history on this kit</span></label>
      <label class="map-field"><span>Keep for</span>
        <select data-setting="retention_days"${disabled}>${s.retention_choices.map((d) => `<option value="${d}"${d === s.retention_days ? " selected" : ""}>${d === 1 ? "1 day" : `${d} days`}</option>`).join("")}</select>
      </label>
      <label class="map-check"><input type="checkbox" data-setting="include_aircraft"${s.include_aircraft ? " checked" : ""}${disabled}><span>Include aircraft (ADS-B)</span></label>
      <div class="subtle map-settings-info">Using ${mb(s.size_bytes)} of ${mb(s.max_bytes)}${s.oldest ? ` · oldest ${esc(when(s.oldest))}` : ""}</div>
      <div class="subtle map-settings-info">Includes pilot locations when drones broadcast them.</div>
      ${s.error ? `<div class="notice error">${esc(s.error)}</div>` : ""}
      ${s.can_change ? "" : '<div class="subtle map-settings-info">Settings can only be changed from the kit itself.</div>'}
      <button class="secondary map-clear" type="button" data-action="clear"${disabled}>Clear history</button>`;
  }

  async function saveSettings(changes) {
    try {
      const response = await fetch("/api/map/history/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || response.statusText);
    } catch (error) {
      $("map-note").textContent = `Could not save: ${error.message}`;
    }
    loadHistory();
  }

  function onSettingsChange(event) {
    const key = event.target.dataset.setting;
    if (!key) return;
    const value = event.target.type === "checkbox" ? event.target.checked : Number(event.target.value);
    saveSettings({ [key]: value });
  }

  async function onSettingsClick(event) {
    if (event.target.dataset.action !== "clear") return;
    if (!window.confirm("Delete all stored map history on this kit? This cannot be undone.")) return;
    try {
      const response = await fetch("/api/map/history/clear", { method: "POST" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || response.statusText);
      history.selected = null;
    } catch (error) {
      $("map-note").textContent = `Could not clear: ${error.message}`;
    }
    loadHistory();
  }

  window.WardragonMap = {
    open(opts) {
      options = { ...options, ...opts };
      if (!map) init();
      else renderLayerList();
      applyLayers();
      active = true;
      setTimeout(() => map.invalidateSize(), 0);
      if (mode === "live") startPolling();
      else loadHistory();
    },
    close() {
      active = false;
      if (timer) { clearTimeout(timer); timer = null; running = false; }
    },
  };
})();
