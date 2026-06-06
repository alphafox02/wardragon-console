const state = {
  snapshot: null,
  config: null,
  dragonscope: null,
  writeMode: false,
  restartNeeded: false,
};

const compactViewportQuery = window.matchMedia("(max-width: 520px), (max-height: 480px), (max-width: 760px) and (max-height: 560px)");

function updateViewportMode() {
  document.documentElement.classList.toggle("compact-screen", compactViewportQuery.matches);
}

if (compactViewportQuery.addEventListener) {
  compactViewportQuery.addEventListener("change", updateViewportMode);
} else {
  compactViewportQuery.addListener(updateViewportMode);
}
updateViewportMode();

const fmt = {
  number(value, digits = 1) {
    const num = Number(value);
    return Number.isFinite(num) ? num.toFixed(digits) : "N/A";
  },
  bytes(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return "N/A";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let scaled = num;
    let idx = 0;
    while (scaled >= 1024 && idx < units.length - 1) {
      scaled /= 1024;
      idx += 1;
    }
    return `${scaled.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
  },
  age(seconds) {
    const num = Number(seconds);
    if (!Number.isFinite(num)) return "never";
    if (num < 1) return "now";
    if (num < 60) return `${Math.round(num)}s ago`;
    if (num < 3600) return `${Math.round(num / 60)}m ago`;
    return `${Math.round(num / 3600)}h ago`;
  },
  time(seconds) {
    const num = Number(seconds);
    if (!Number.isFinite(num)) return "N/A";
    const hours = Math.floor(num / 3600);
    const minutes = Math.floor((num % 3600) / 60);
    const secs = Math.floor(num % 60);
    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m ${secs}s`;
    return `${secs}s`;
  },
};

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((panel) => panel.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(button.dataset.tab).classList.add("active");
    if (button.dataset.tab === "config") loadConfig();
    if (button.dataset.tab === "dragonscope") loadDragonscope();
  });
});

document.getElementById("write-toggle").addEventListener("click", () => {
  state.writeMode = !state.writeMode;
  renderConfig();
});

document.getElementById("restart-dragonsync").addEventListener("click", restartDragonSync);
document.getElementById("upload-cert").addEventListener("click", uploadCertificate);
document.getElementById("check-updates").addEventListener("click", checkForUpdates);
document.getElementById("dragonscope-save").addEventListener("click", saveDragonscope);
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-reveal]");
  if (!button) return;
  const input = document.getElementById(button.dataset.reveal);
  if (!input) return;
  input.type = input.type === "password" ? "text" : "password";
  button.textContent = input.type === "password" ? "Show" : "Hide";
});

async function loadSnapshot() {
  try {
    const response = await fetch("/api/snapshot", { cache: "no-store" });
    state.snapshot = await response.json();
    renderSnapshot();
  } catch (error) {
    setOverall("ERROR");
  }
}

async function loadConfig() {
  try {
    const response = await fetch("/api/config", { cache: "no-store" });
    state.config = await response.json();
    renderConfig();
  } catch (error) {
    const notice = document.getElementById("config-status");
    notice.className = "notice error";
    notice.textContent = `Config unavailable: ${error}`;
  }
}

async function loadDragonscope() {
  const notice = document.getElementById("dragonscope-status");
  try {
    const response = await fetch("/api/dragonscope/config", { cache: "no-store" });
    state.dragonscope = await response.json();
    renderDragonscope();
  } catch (error) {
    notice.className = "notice error";
    notice.textContent = `DragonScope config unavailable: ${error}`;
  }
}

function renderDragonscope() {
  const ds = state.dragonscope;
  if (!ds) return;
  const notice = document.getElementById("dragonscope-status");
  const saveButton = document.getElementById("dragonscope-save");
  saveButton.disabled = !ds.write_allowed;
  document.getElementById("dragonscope-path").textContent = ds.path || "";

  if (ds.error) {
    notice.className = "notice error";
    notice.textContent = `Existing file: ${ds.error}`;
  } else if (!ds.exists) {
    notice.className = "notice";
    notice.textContent = `${ds.path} does not exist yet. Save will create it.`;
  } else if (!ds.write_allowed) {
    notice.className = "notice";
    notice.textContent = "Read-only: config writes are disabled for this bind mode.";
  } else {
    notice.className = "notice";
    notice.textContent = `DragonScope re-reads this file every ~${ds.auto_reload_seconds || 30}s. No restart needed.`;
  }

  const form = ds.form || { groups: [] };
  document.getElementById("dragonscope-form").innerHTML = form.groups
    .map((group) => renderConfigGroup("dragonscope.cfg", group, ds.write_allowed))
    .join("");
}

async function saveDragonscope() {
  const button = document.getElementById("dragonscope-save");
  const notice = document.getElementById("dragonscope-status");
  if (button.dataset.busy === "1") return;
  button.dataset.busy = "1";
  button.disabled = true;
  const values = {};
  document.querySelectorAll('[data-config-file="dragonscope.cfg"]').forEach((input) => {
    if (input.type === "checkbox") {
      values[input.dataset.key] = input.checked;
    } else {
      values[input.dataset.key] = input.value;
    }
  });
  try {
    const response = await fetch("/api/dragonscope/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || response.statusText);
    notice.className = "notice ok";
    if (payload.unchanged) {
      notice.textContent = "dragonscope.cfg: no changes to save";
    } else {
      notice.textContent = `Saved ${payload.path}${payload.backup_path ? ` · backup ${payload.backup_path}` : ""} · DragonScope will pick it up within 30s`;
    }
    await loadDragonscope();
  } catch (error) {
    notice.className = "notice error";
    notice.textContent = `Save failed: ${error.message || error}`;
  } finally {
    delete button.dataset.busy;
    button.disabled = false;
  }
}

function renderSnapshot() {
  const snap = state.snapshot;
  if (!snap) return;
  const monitor = snap.monitor.payload || {};
  const gps = monitor.gps_data || {};
  const stats = monitor.system_stats || {};
  const dsStatus = snap.dragonsync.status.payload || {};
  const drones = (snap.dragonsync.drones.payload || {}).drones || [];
  const signals = (snap.dragonsync.signals.payload || {}).signals || [];

  document.getElementById("kit-id").textContent = snap.summary.kit_id || dsStatus.uid || snap.console.hostname || "Kit unavailable";
  document.getElementById("drone-count").textContent = drones.length;
  document.getElementById("signal-count").textContent = signals.length;
  setOverall(overallState(snap.services));

  renderDl("kit-summary", {
    Hostname: snap.console.hostname,
    "Kit ID": snap.summary.kit_id || "N/A",
    "Tablet URL": snap.access?.tether?.url || "No tether detected",
    "GPS fix": snap.summary.gps_fix ? "yes" : "no",
    "Console uptime": fmt.time(snap.console.uptime_seconds),
  });
  renderDl("activity-summary", {
    "Current drones": drones.length,
    "Current signals": signals.length,
    "GPS source": gps.time_source ?? dsStatus.time_source ?? "N/A",
    "DragonSync API": serviceError(snap.dragonsync.status),
    "Drone snapshot": fmt.age(ageFromSeen(snap.dragonsync.drones.seen_at, snap.generated_at)),
    "Signal snapshot": fmt.age(ageFromSeen(snap.dragonsync.signals.seen_at, snap.generated_at)),
  });
  renderServiceList(snap.services);
  renderOperatorNotes(snap);
  renderDl("gps-position", {
    Fix: gps.gps_fix ?? dsStatus.gps_fix ?? false,
    Latitude: gps.latitude ?? dsStatus.lat ?? "N/A",
    Longitude: gps.longitude ?? dsStatus.lon ?? "N/A",
    Altitude: valueWithUnit(gps.altitude ?? dsStatus.alt, "m"),
    Speed: valueWithUnit(gps.speed ?? dsStatus.speed, "m/s"),
    Track: valueWithUnit(gps.track ?? dsStatus.track, "deg"),
    Source: gps.time_source ?? dsStatus.time_source ?? "N/A",
  });
  renderDl("system-stats", {
    CPU: valueWithUnit(stats.cpu_usage ?? dsStatus.cpu_usage, "%"),
    Memory: memoryLine(stats.memory, dsStatus),
    Disk: diskLine(stats.disk, dsStatus),
    Temperature: valueWithUnit(stats.temperature ?? dsStatus.temperature, "C"),
    Uptime: fmt.time(stats.uptime ?? dsStatus.uptime),
    "DragonSDR": dragonSdrLine(monitor.ant_sdr_temps, dsStatus),
  });
  renderDl("network-stats", networkValues(snap.console.interfaces || [], snap.access));
  renderDroneid(snap.droneid.payload);
  renderDragonsig(snap.dragonsig.payload, snap.services.dragonsig);
  renderDrones(drones);
  renderSignals(signals);
  renderDl("version-info", {
    Console: snap.console.version || "0.1.0",
    Hostname: snap.console.hostname,
    "Kit ID": snap.summary.kit_id || "N/A",
    "droneid-go": snap.droneid.payload?.version || "N/A",
    DragonSig: snap.dragonsig.payload?.version || "N/A",
    "DragonSync API": snap.dragonsync.status.error || "OK",
    Generated: new Date(snap.generated_at * 1000).toLocaleString(),
  });
  renderUpdates(snap.updates || {});
}

function renderUpdates(updates) {
  const target = document.getElementById("update-info");
  if (!updates.checked_at) {
    target.innerHTML = `<div class="subtle">Click "Check for updates" to query GitHub.</div>`;
    return;
  }
  const sections = ["console", "dragonsync"].map((key) => {
    const info = updates[key];
    if (!info) return "";
    const repoLabel = key === "console" ? "WarDragon Console" : "DragonSync";
    const repoLink = info.repo ? `<a href="https://github.com/${escapeAttr(info.repo)}" target="_blank" rel="noopener">${escapeHtml(info.repo)}</a>` : "";
    const rows = {};
    if (info.local_version) rows["Local version"] = info.local_version;
    if (info.local_sha) rows["Local commit"] = info.local_sha;
    if (info.upstream_sha) rows[`Upstream ${info.upstream_branch || "main"}`] = info.upstream_sha;
    if (info.latest_release_tag) {
      rows["Latest release"] = info.latest_release_url
        ? `<a href="${escapeAttr(info.latest_release_url)}" target="_blank" rel="noopener">${escapeHtml(info.latest_release_tag)}</a>`
        : info.latest_release_tag;
    }
    if (info.error) rows["Status"] = `error: ${info.error}`;
    else if (info.update_available) {
      const link = info.compare_url || (info.latest_release_url || (info.repo ? `https://github.com/${info.repo}/releases` : ""));
      rows["Status"] = link
        ? `update available — <a href="${escapeAttr(link)}" target="_blank" rel="noopener">view changes</a>`
        : "update available";
    } else if (info.upstream_sha || info.latest_release_tag) {
      rows["Status"] = "up to date";
    }
    const items = Object.entries(rows).map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${v}</dd>`).join("");
    return `<div class="update-row"><h3>${escapeHtml(repoLabel)}</h3><div class="update-repo subtle">${repoLink}</div><dl>${items}</dl></div>`;
  }).join("");
  const checked = new Date(updates.checked_at * 1000).toLocaleString();
  target.innerHTML = `${sections}<div class="subtle update-checked">Checked ${escapeHtml(checked)}</div>`;
}

async function checkForUpdates() {
  const button = document.getElementById("check-updates");
  const target = document.getElementById("update-info");
  if (button.dataset.busy === "1") return;
  button.dataset.busy = "1";
  button.disabled = true;
  const previous = target.innerHTML;
  target.innerHTML = `<div class="subtle">Checking GitHub…</div>`;
  try {
    const response = await fetch("/api/updates/check", { method: "POST" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || response.statusText);
    state.snapshot = state.snapshot || {};
    state.snapshot.updates = payload;
    renderUpdates(payload);
  } catch (error) {
    target.innerHTML = `<div class="notice error">Update check failed: ${escapeHtml(error.message || String(error))}</div>${previous}`;
  } finally {
    delete button.dataset.busy;
    button.disabled = false;
  }
}

function renderServiceList(services) {
  const labels = { monitor: "wardragon_monitor", droneid: "droneid-go", dragonsig: "DragonSig" };
  document.getElementById("service-list").innerHTML = Object.entries(services).map(([key, service]) => `
    <div class="status-row">
      <span class="status-dot ${stateClass(service.state)}"></span>
      <strong>${labels[key] || key}</strong>
      <span class="pill">${service.state.replace("_", " ")} · ${fmt.age(service.age_seconds)}</span>
    </div>
  `).join("");
}

function renderOperatorNotes(snap) {
  const notes = [];
  if (snap.access?.tether?.stable_url) {
    notes.push(`Tablet stable URL: ${snap.access.tether.stable_url} (preferred for shipped tablets)`);
  } else if (snap.access?.tether?.url) {
    notes.push(`Tablet tether access is available at ${snap.access.tether.url}`);
  } else if (snap.access?.tether?.enabled) {
    notes.push("No USB tether detected. Use the local display at http://localhost:4280/ or try wardragon.local after tethering.");
  }
  if (!snap.summary.gps_fix) {
    notes.push("No live GPS fix reported. Check the GPS tab for static position or gpsd state.");
  }
  const serviceStates = Object.entries(snap.services || {}).filter(([, value]) => value.state === "DEGRADED" || value.state === "NOT_PRESENT");
  serviceStates.forEach(([name, value]) => {
    notes.push(`${name} is ${value.state.replace("_", " ").toLowerCase()}.`);
  });
  ["console", "dragonsync"].forEach((key) => {
    const info = snap.updates?.[key];
    if (info?.update_available) {
      const label = key === "console" ? "Console" : "DragonSync";
      notes.push(`${label} update available — see Version tab.`);
    }
  });
  if (!notes.length) {
    notes.push("No operator action flagged by the console.");
  }
  document.getElementById("operator-notes").innerHTML = notes.map((note) => `<div class="note">${escapeHtml(note)}</div>`).join("");
}

function renderDroneid(health) {
  const target = document.getElementById("droneid-sources");
  if (!health || !health.sources) {
    target.innerHTML = `<div class="subtle">No health snapshot</div>`;
    return;
  }
  target.innerHTML = Object.values(health.sources).map((source) => `
    <div class="receiver-row">
      <span class="status-dot ${source.enabled ? sourceStateClass(source.state_str) : ""}"></span>
      <div>
        <strong>${escapeHtml(source.name)}</strong>
        <div class="subtle">${source.enabled ? escapeHtml(source.state_str || "unknown") : "disabled"}</div>
      </div>
      <span class="pill">${fmt.number(source.messages_per_sec || 0, 2)}/s · ${source.messages_total || 0}</span>
    </div>
  `).join("");
}

function renderDragonsig(health, service) {
  if (!health) {
    renderDl("dragonsig-health", { State: service.state, "Last health": fmt.age(service.age_seconds) });
    return;
  }
  renderDl("dragonsig-health", {
    State: service.state,
    Phase: health.phase || "N/A",
    Mode: health.mode || "N/A",
    "SDR OK": health.sdr_ok ?? "N/A",
    "Noise floor": valueWithUnit(health.noise_floor_db, "dB"),
    Gain: valueWithUnit(health.gain_db, "dB"),
    FPV: health.fpv_detections ?? 0,
    SIK: health.sik_detections ?? 0,
    Version: health.version || "N/A",
  });
}

function renderDrones(drones) {
  if (!drones.length) {
    document.getElementById("drones-table").innerHTML = `<div class="subtle">No current drones</div>`;
    return;
  }
  document.getElementById("drones-table").innerHTML = table(["ID", "Type", "Transport", "RSSI", "Frequency", "Updated"],
    drones.map((drone) => [
      drone.id || "N/A",
      drone.ua_type_name || drone.description || "N/A",
      drone.transport || "N/A",
      drone.rssi ?? "N/A",
      drone.freq || "N/A",
      drone.last_update_time ? new Date(drone.last_update_time * 1000).toLocaleTimeString() : (drone.observed_at || "N/A"),
    ]));
}

function renderSignals(signals) {
  if (!signals.length) {
    document.getElementById("signals-table").innerHTML = `<div class="subtle">No current signals</div>`;
    return;
  }
  document.getElementById("signals-table").innerHTML = table(["Source", "Frequency", "RSSI", "Type", "Updated"],
    signals.map((signal) => [
      signal.source || signal.seen_by || "N/A",
      signal.freq || signal.frequency || signal.center_hz || "N/A",
      signal.rssi ?? "N/A",
      signal.track_type || signal.signal_type || signal.type || "signal",
      signal.last_update_time ? new Date(signal.last_update_time * 1000).toLocaleTimeString() : (signal.timestamp || "N/A"),
    ]));
}

function renderConfig() {
  const config = state.config;
  const toggle = document.getElementById("write-toggle");
  const notice = document.getElementById("config-status");
  if (!config) return;

  toggle.classList.toggle("active", state.writeMode);
  toggle.disabled = !config.write_allowed;
  const restartButton = document.getElementById("restart-dragonsync");
  restartButton.disabled = !config.restart_allowed;
  restartButton.title = config.restart_allowed ? "Restart DragonSync from this console" : "Restart is only available on the local console";
  notice.className = "notice";
  notice.textContent = config.write_allowed
    ? (state.writeMode ? "Read/write mode" : (state.restartNeeded ? "DragonSync restart recommended" : "Read-only mode"))
    : "Config writes disabled for this server bind";
  document.getElementById("cert-upload").style.display = state.writeMode && config.write_allowed ? "block" : "none";

  document.getElementById("config-files").innerHTML = config.forms.map((form) => {
    if (!form.exists) {
      return `<article class="config-editor"><div class="config-header"><div><h2>${form.name}</h2><div class="config-path">${form.path}</div></div></div><div class="notice error">Missing</div></article>`;
    }
    return `<article class="config-editor">
      <div class="config-header">
        <div><h2>${form.name}</h2><div class="config-path">${form.path}</div></div>
        <button type="button" data-save="${form.name}" ${state.writeMode && config.write_allowed ? "" : "disabled"}>Save</button>
      </div>
      ${form.groups.map((group) => renderConfigGroup(form.name, group, state.writeMode && config.write_allowed)).join("")}
    </article>`;
  }).join("");

  document.querySelectorAll("[data-save]").forEach((button) => {
    button.addEventListener("click", () => saveConfig(button.dataset.save));
  });
}

async function saveConfig(name) {
  const notice = document.getElementById("config-status");
  const button = document.querySelector(`[data-save="${CSS.escape(name)}"]`);
  if (button && button.dataset.busy === "1") return;
  if (button) {
    button.dataset.busy = "1";
    button.disabled = true;
  }
  const values = {};
  document.querySelectorAll(`[data-config-file="${CSS.escape(name)}"]`).forEach((input) => {
    if (input.type === "checkbox") {
      values[input.dataset.key] = input.checked;
    } else {
      values[input.dataset.key] = input.value;
    }
  });
  try {
    const response = await fetch(`/api/config/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || response.statusText);
    notice.className = "notice ok";
    if (payload.unchanged) {
      notice.textContent = `${name}: no changes to save`;
    } else {
      notice.textContent = `Saved ${name}; DragonSync restart required${payload.backup_path ? ` · backup ${payload.backup_path}` : ""}`;
      state.restartNeeded = true;
    }
    await loadConfig();
  } catch (error) {
    notice.className = "notice error";
    notice.textContent = `Save failed: ${error.message || error}`;
  } finally {
    if (button) {
      delete button.dataset.busy;
      button.disabled = false;
    }
  }
}

async function uploadCertificate() {
  const notice = document.getElementById("config-status");
  const fileInput = document.getElementById("cert-file");
  const file = fileInput.files[0];
  if (!file) {
    notice.className = "notice error";
    notice.textContent = "Choose a certificate file first.";
    return;
  }
  try {
    const headers = {
      "Content-Type": "application/octet-stream",
      "X-Cert-Role": document.getElementById("cert-role").value,
      "X-Filename": file.name,
    };
    const password = document.getElementById("cert-password").value;
    if (password) headers["X-P12-Password"] = password;
    const response = await fetch("/api/certs/tak", {
      method: "POST",
      headers,
      body: await file.arrayBuffer(),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || response.statusText);
    state.restartNeeded = true;
    notice.className = "notice ok";
    notice.textContent = `Uploaded certificate to ${payload.path}; DragonSync restart required`;
    fileInput.value = "";
    await loadConfig();
  } catch (error) {
    notice.className = "notice error";
    notice.textContent = `Upload failed: ${error.message || error}`;
  }
}

async function restartDragonSync() {
  const notice = document.getElementById("config-status");
  notice.className = "notice";
  notice.textContent = "Restarting DragonSync...";
  try {
    const response = await fetch("/api/actions/restart-dragonsync", { method: "POST" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.stderr || payload.error || response.statusText);
    state.restartNeeded = false;
    notice.className = "notice ok";
    notice.textContent = "DragonSync restart requested.";
  } catch (error) {
    notice.className = "notice error";
    notice.textContent = `Restart failed: ${error.message || error}`;
  }
}

function renderConfigGroup(fileName, group, enabled) {
  return `<div class="config-group">
    <h3>${escapeHtml(group.title)}</h3>
    <div class="config-grid">
      ${group.fields.map((field) => renderConfigField(fileName, field, enabled)).join("")}
    </div>
  </div>`;
}

function renderConfigField(fileName, field, enabled) {
  const attrs = `data-config-file="${escapeAttr(fileName)}" data-key="${escapeAttr(field.key)}" ${enabled ? "" : "disabled"}`;
  const title = field.help ? ` title="${escapeAttr(field.help)}"` : "";
  const help = field.help ? `<small>${escapeHtml(field.help)}</small>` : "";
  if (field.kind === "bool") {
    const checked = ["true", "1", "yes", "on"].includes(String(field.value).toLowerCase()) ? "checked" : "";
    return `<label class="form-check"${title}><input type="checkbox" ${attrs} ${checked}> <span>${escapeHtml(field.label)}</span>${help}</label>`;
  }
  if (field.kind === "select") {
    return `<label class="form-field"${title}><span>${escapeHtml(field.label)}</span><select ${attrs}>${
      field.options.map((option) => `<option value="${escapeAttr(option)}" ${String(field.value).toUpperCase() === option ? "selected" : ""}>${escapeHtml(option || "Disabled")}</option>`).join("")
    }</select>${help}</label>`;
  }
  const type = field.kind === "password" ? "password" : (field.kind === "int" || field.kind === "float" ? "number" : "text");
  const step = field.kind === "float" ? "any" : "1";
  if (field.kind === "password") {
    const id = `secret-${fileName}-${field.key}`.replace(/[^A-Za-z0-9_-]/g, "_");
    return `<label class="form-field"${title}><span>${escapeHtml(field.label)}</span><div class="password-wrap"><input id="${id}" type="password" step="${step}" value="${escapeAttr(field.value)}" ${attrs}><button type="button" data-reveal="${id}">Show</button></div>${help}</label>`;
  }
  return `<label class="form-field"${title}><span>${escapeHtml(field.label)}</span><input type="${type}" step="${step}" value="${escapeAttr(field.value)}" ${attrs}>${help}</label>`;
}

function renderDl(id, values) {
  document.getElementById(id).innerHTML = Object.entries(values).map(([key, value]) => `
    <dt>${escapeHtml(key)}</dt><dd>${escapeHtml(display(value))}</dd>
  `).join("");
}

function table(headers, rows) {
  return `<table><thead><tr>${headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${
    rows.map((row) => `<tr>${row.map((cell, index) => `<td data-label="${escapeAttr(headers[index] || "")}">${escapeHtml(display(cell))}</td>`).join("")}</tr>`).join("")
  }</tbody></table>`;
}

function setOverall(value) {
  const normalized = value.toLowerCase();
  document.getElementById("overall-state").textContent = value.replace("_", " ");
  document.getElementById("overall-dot").className = `status-dot ${stateClass(normalized)}`;
}

function overallState(services) {
  const values = Object.values(services).map((item) => item.state);
  if (values.includes("DEGRADED")) return "DEGRADED";
  if (values.includes("STARTING")) return "STARTING";
  if (values.includes("HEALTHY")) return "HEALTHY";
  return "NOT_PRESENT";
}

function stateClass(value) {
  return `state-${String(value || "").toLowerCase()}`;
}

function sourceStateClass(value) {
  if (value === "connected") return "state-healthy";
  if (value === "connecting" || value === "reconnecting") return "state-starting";
  if (value === "error" || value === "dead") return "state-error";
  return "";
}

function valueWithUnit(value, unit) {
  if (value === undefined || value === null || value === "N/A") return "N/A";
  return `${value} ${unit}`;
}

function memoryLine(memory, status) {
  if (memory && memory.total) return `${fmt.bytes(memory.used)} / ${fmt.bytes(memory.total)} (${fmt.number(memory.percent)}%)`;
  if (status.memory_total) return `${fmt.bytes(status.memory_total - status.memory_available)} / ${fmt.bytes(status.memory_total)}`;
  return "N/A";
}

function diskLine(disk, status) {
  if (disk && disk.total) return `${fmt.bytes(disk.used)} / ${fmt.bytes(disk.total)} (${fmt.number(disk.percent)}%)`;
  if (status.disk_total) return `${fmt.bytes(status.disk_used)} / ${fmt.bytes(status.disk_total)}`;
  return "N/A";
}

function dragonSdrLine(temps, status) {
  const pluto = temps?.pluto_temp ?? status.pluto_temp;
  const zynq = temps?.zynq_temp ?? status.zynq_temp;
  if (pluto === undefined && zynq === undefined) return "N/A";
  return `RF ${display(pluto)} C · Zynq ${display(zynq)} C`;
}

function networkValues(interfaces, access) {
  const values = {
    "Local URL": access?.local_url || "http://127.0.0.1:4280/",
    "Tablet URL": access?.tether?.url || "No tether detected",
  };
  if (access?.tether?.stable_url) {
    values["Stable tablet URL"] = access.tether.stable_url;
  }
  interfaces.forEach((iface) => {
    values[iface.name] = `${iface.ipv4}${iface.tether_kind ? ` · ${iface.tether_kind}` : ""}`;
  });
  return values;
}

function tetherInterfaceLine(iface) {
  if (!iface) return "N/A";
  return `${iface.name} ${iface.ipv4}${iface.tether_kind ? ` (${iface.tether_kind})` : ""}`;
}

function ageFromSeen(seenAt, now) {
  if (!seenAt) return null;
  return now - seenAt;
}

function serviceError(entry) {
  return entry.error || (entry.seen_at ? "OK" : "No snapshot");
}

function display(value) {
  if (value === true) return "yes";
  if (value === false) return "no";
  if (value === null || value === undefined || value === "") return "N/A";
  return String(value);
}

function escapeHtml(value) {
  return display(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[char]));
}

// For HTML attribute values: preserves empty strings as-is. escapeHtml routes
// through display() which turns "" into "N/A" — fine for table cells, fatal
// for <option value="">, <input value="">, etc.
function escapeAttr(value) {
  const text = value === null || value === undefined ? "" : String(value);
  return text.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[char]));
}

loadSnapshot();
setInterval(loadSnapshot, 2000);
