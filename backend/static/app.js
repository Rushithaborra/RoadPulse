/* RoadPulse frontend: one page, two views (commuter, authority), live via Server-Sent Events. */
"use strict";

const SEV_COLOR = { Low: "#d4a72c", Medium: "#e8770f", Severe: "#d1242f", Closed: "#6e2b8a" };
const ROUTE_COLORS = ["#1f6feb", "#1a7f37", "#8250df"];
const VEHICLE_LABEL = { two_wheeler: "two-wheeler", auto: "auto", car: "car", bus: "bus", ambulance: "ambulance" };

const state = {
  view: "commuter",
  token: sessionStorage.getItem("rp_token"),
  start: null, end: null, lastClick: null,
  incidents: [], hotspots: [], selected: null, seen: new Set(), firstSnapshot: true,
};

const $ = (id) => document.getElementById(id);
const SIM = `<span class="badge sim" title="From the demo simulator, not a real sensor or person">SIMULATED</span>`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg, ms = 3500) {
  const t = $("toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let res;
  try {
    res = await fetch(path, { ...opts, headers });
  } catch (e) {
    throw new Error("Cannot reach the RoadPulse server. Check that it is running and the network is up.");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && state.token && path !== "/api/auth/login") {
      logout();
      throw new Error("Your officer session expired. Please log in again.");
    }
    if (res.status === 429) throw new Error("Too many requests. Wait a few seconds and try again.");
    if (res.status >= 500) throw new Error(`Server error (request ${res.headers.get("X-Request-ID") || "?"}). Check the server log.`);
    throw new Error(typeof body.detail === "string" ? body.detail : body.error || `Request failed (HTTP ${res.status})`);
  }
  return body;
}

// ---------------------------------------------------------------- map
const map = L.map("map", { zoomControl: true });
// Standard OSM tiles are loaded on demand only while someone views the map (no bulk download / pre-fetch),
// per the OSM tile usage policy. Attribution links to the OSM copyright page; the road graph is OSM data too.
map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: 'Map tiles and road data &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" ' +
    'rel="noopener">OpenStreetMap contributors</a> (ODbL)',
}).addTo(map);
const layers = {
  network: L.layerGroup().addTo(map), incidents: L.layerGroup().addTo(map),
  hotspots: L.layerGroup().addTo(map), routes: L.layerGroup().addTo(map), pins: L.layerGroup().addTo(map),
};

async function loadNetwork() {
  const net = await api("/api/network");
  map.setView(net.center, 16);
  const mm = $("mapmode");
  mm.classList.remove("hidden");
  if (net.map_mode === "synthetic") {
    mm.textContent = "Synthetic demo road network (not real roads)";
  } else {
    mm.textContent = net.hotspots_source === "synthetic" ? "Real OpenStreetMap roads · demo hotspots are simulated" : "Real OpenStreetMap roads";
    mm.classList.add("osm");
  }
  for (const s of net.segments) {
    L.polyline(s.coords, { color: s.speed_kph >= 40 ? "#7d8590" : "#b8bec7", weight: s.speed_kph >= 40 ? 5 : 3, opacity: 0.7 })
      .addTo(layers.network);
  }
}

map.on("click", (e) => {
  state.lastClick = [e.latlng.lat, e.latlng.lng];
  if (state.view !== "commuter") { pin(state.lastClick, "Selected point"); return; }
  if (!state.start || (state.start && state.end)) {
    state.start = state.lastClick; state.end = null; layers.routes.clearLayers();
    $("route-hint").innerHTML = "Now click your <b>destination</b>.";
  } else {
    state.end = state.lastClick;
    $("route-hint").innerHTML = "Route shown. Click again to start a new one.";
    findRoute();
  }
  drawPins();
});

function pin(ll, label) {
  layers.pins.clearLayers();
  L.circleMarker(ll, { radius: 7, color: "#1d2430", fillColor: "#fff", fillOpacity: 1, weight: 2 }).bindTooltip(label).addTo(layers.pins);
}

function drawPins() {
  layers.pins.clearLayers();
  if (state.start) L.circleMarker(state.start, { radius: 8, color: "#1a7f37", fillColor: "#1a7f37", fillOpacity: 1 }).bindTooltip("Start").addTo(layers.pins);
  if (state.end) L.circleMarker(state.end, { radius: 8, color: "#d1242f", fillColor: "#d1242f", fillOpacity: 1 }).bindTooltip("Destination").addTo(layers.pins);
  $("btn-reroute").disabled = !(state.start && state.end);
}

function drawIncidents() {
  layers.incidents.clearLayers();
  for (const inc of state.incidents) {
    if (inc.status === "Cleared" || !inc.active) continue;
    const color = SEV_COLOR[inc.severity] || "#555";
    const dashed = !inc.published;
    for (const seg of inc.affected_segments) {
      L.polyline(seg, { color, weight: 9, opacity: dashed ? 0.45 : 0.85, dashArray: dashed ? "6 8" : null }).addTo(layers.incidents);
    }
    const m = L.circleMarker([inc.lat, inc.lon], { radius: 9, color: "#fff", weight: 2, fillColor: color, fillOpacity: 1 })
      .bindPopup(incidentPopup(inc));
    m.on("click", () => selectIncident(inc.id));
    m.addTo(layers.incidents);
  }
}

function incidentPopup(inc) {
  const blocked = inc.affected_vehicles.length ? inc.affected_vehicles.map((v) => VEHICLE_LABEL[v]).join(", ") : "none";
  return `${inc.simulated ? "<i>Simulated demo data</i><br>" : ""}<b>${esc(inc.title)}</b><br>${inc.severity}${inc.depth_cm != null ? ` · ${Math.round(inc.depth_cm)} cm` : ""}<br>` +
    `Avoid: ${esc(blocked)}<br><small>${inc.status} · confidence ${inc.confidence.toFixed(2)}</small>`;
}

function drawHotspots() {
  layers.hotspots.clearLayers();
  for (const h of state.hotspots) {
    const color = h.risk === "high" ? "#d1242f" : h.risk === "watch" ? "#e8770f" : "#1a7f37";
    L.circle([h.lat, h.lon], { radius: 40, color, weight: 2, fillOpacity: 0.12 })
      .bindTooltip(`${esc(h.name)}: ${h.depth_cm} cm · ${esc(h.message)}`).addTo(layers.hotspots);
  }
}

// ---------------------------------------------------------------- routing
async function findRoute() {
  if (!(state.start && state.end) || state.view !== "commuter") return;
  const vehicle = $("vehicle").value;
  try {
    const r = await api("/api/routes", { method: "POST", body: JSON.stringify({
      from_lat: state.start[0], from_lon: state.start[1], to_lat: state.end[0], to_lon: state.end[1], vehicle,
    }) });
    showRoutes(r);
  } catch (e) { toast(`Route failed: ${e.message}`); }
}

function showRoutes(r) {
  layers.routes.clearLayers();
  const out = $("route-result");
  if (r.usual_route_hits.length) {
    L.polyline(r.usual_route, { color: "#d1242f", weight: 4, dashArray: "4 8", opacity: 0.9 })
      .bindTooltip("Usual route: blocked or slow").addTo(layers.routes);
  }
  r.routes.slice().reverse().forEach((rt, iRev) => {
    const i = r.routes.length - 1 - iRev;
    L.polyline(rt.coords, { color: ROUTE_COLORS[i], weight: i === 0 ? 7 : 5, opacity: i === 0 ? 0.95 : 0.7 })
      .bindTooltip(`Route ${i + 1}: ${Math.round(rt.time_s / 60 * 10) / 10} min · ${Math.round(rt.share * 100)}% of traffic`)
      .addTo(layers.routes);
  });
  let html = "";
  if (r.usual_route_hits.length) {
    html += `<div class="alert">Your usual route crosses: ${r.usual_route_hits.map((h) => esc(h.title)).join(", ")}. ` +
      `RoadPulse rerouted your ${esc(VEHICLE_LABEL[r.vehicle])}.</div>`;
  }
  if (r.no_route) html += `<div class="alert">No safe route for a ${esc(VEHICLE_LABEL[r.vehicle])} right now.</div>`;
  if (r.snapped) {
    L.circleMarker(r.snapped.start, { radius: 4, color: "#1a7f37", fillOpacity: 1 }).bindTooltip("Start, on the road").addTo(layers.routes);
    L.circleMarker(r.snapped.destination, { radius: 4, color: "#d1242f", fillOpacity: 1 }).bindTooltip("Destination, on the road").addTo(layers.routes);
  }
  r.routes.forEach((rt, i) => {
    html += `<div class="route" style="border-color:${ROUTE_COLORS[i]}"><b>Route ${i + 1}</b> · ${(rt.time_s / 60).toFixed(1)} min · ` +
      `${(rt.length_m / 1000).toFixed(2)} km<div class="meta">${Math.round(rt.share * 100)}% of drivers sent this way to avoid a new jam</div></div>`;
  });
  out.innerHTML = html;
}

// ---------------------------------------------------------------- commuter lists
function renderPublic() {
  const pub = state.incidents.filter((i) => i.published);
  $("public-incidents").innerHTML = pub.length ? pub.map((i) => `<li>
      <span class="badge sev-${i.severity}">${i.severity}</span>${i.simulated ? SIM : ""}<b>${esc(i.title)}</b>
      <div class="meta">${i.depth_cm != null ? `${Math.round(i.depth_cm)} cm water · ` : ""}avoid: ${
        i.affected_vehicles.length ? i.affected_vehicles.map((v) => VEHICLE_LABEL[v]).join(", ") : "none"}</div></li>`).join("")
    : `<li class="meta">No verified disruptions right now.</li>`;
  $("public-hotspots").innerHTML = state.hotspots.map((h) => `<li>${h.last_reading_simulated || h.simulated_location ? SIM : ""}<b>${esc(h.name)}</b> · ${h.depth_cm} cm
      <div class="meta countdown risk-${h.risk}">${esc(h.message)}</div></li>`).join("");
}

// ---------------------------------------------------------------- authority
function renderOfficer(events) {
  const list = state.incidents;
  const open = list.filter((i) => i.status !== "Cleared");
  $("kpis").innerHTML = `<div><b>${open.length}</b>open</div>
    <div><b>${open.filter((i) => i.status === "Reported").length}</b>to review</div>
    <div><b>${open.filter((i) => i.status === "Responding").length}</b>responding</div>`;
  $("officer-incidents").innerHTML = list.length ? list.map((i) => {
    const stages = Object.entries(i.stage_minutes).map(([k, v]) => `${k.split("->")[0]} ${v} min`).join(" · ");
    const src = Object.entries(i.signals_by_source).map(([k, v]) => `${k}×${v}`).join(", ");
    const clearing = i.clearing_in_s != null ? ` · <b>auto-clear in ${i.clearing_in_s}s</b>` : "";
    const upcoming = !i.active && i.starts ? ` · starts ${new Date(i.starts * 1000).toLocaleTimeString()}` : "";
    return `<li class="${state.selected === i.id ? "sel" : ""}" data-id="${i.id}">
      <span class="badge sev-${i.severity}">${i.severity}</span><span class="badge st st-${i.status}">${i.status}</span>${i.simulated ? SIM : ""}
      <b>${esc(i.title)}</b>
      <div class="meta">confidence ${i.confidence.toFixed(2)} · ${esc(src)}${i.depth_cm != null ? ` · ${Math.round(i.depth_cm)} cm` : ""}${clearing}${upcoming}</div>
      <div class="meta">${esc(stages)}</div>
      <div class="actions">
        ${i.status === "Reported" ? `<button class="small" data-act="verify">Verify</button><button class="small warn" data-act="reject">Reject</button>` : ""}
        ${["Reported", "Verified"].includes(i.status) ? `<button class="small ok" data-act="respond">Approve diversion</button>` : ""}
        <button class="small ghost" data-act="whatif">What-if</button>
        <button class="small ghost" data-act="signals">Signals</button>
        <button class="small ghost" data-act="bulletin">Bulletin</button>
        <button class="small ghost" data-act="clear">Clear</button>
      </div></li>`;
  }).join("") : `<li class="meta">No open incidents.</li>`;
  $("officer-hotspots").innerHTML = state.hotspots.map((h) => {
    const eta = Object.entries(h.eta_min).map(([k, v]) => `${k}: ${v === null ? "–" : v === 0 ? "now" : v + " min"}`).join(" · ");
    const age = h.last_reading_age_s == null ? "no sensor data" : `sensor ${h.last_reading_age_s}s ago`;
    return `<li>${h.last_reading_simulated || h.simulated_location ? SIM : ""}<b>${esc(h.name)}</b> · ${h.depth_cm} cm · rise ${h.rise_cm_per_hr} cm/h
      <div class="meta countdown risk-${h.risk}">${esc(h.message)}</div><div class="meta">${eta} · ${age}</div></li>`;
  }).join("");
  if (events) $("events").innerHTML = events.map((e) => `<li>${new Date(e.ts * 1000).toLocaleTimeString()} — ${esc(e.text)}</li>`).join("");
}

$("officer-incidents").addEventListener("click", async (e) => {
  const li = e.target.closest("li[data-id]"); if (!li) return;
  const id = li.dataset.id; selectIncident(id);
  const act = e.target.dataset.act; if (!act) return;
  try {
    if (["verify", "respond", "clear", "reject"].includes(act)) {
      await api(`/api/incidents/${id}/${act}`, { method: "POST", body: "{}" });
      toast(`Incident ${act === "respond" ? "diversion approved" : act + "ed"}`);
    } else if (act === "whatif") {
      $("decision").innerHTML = "Running what-if…";
      const r = await api(`/api/incidents/${id}/whatif`);
      $("decision").innerHTML = `<b>${esc(r.incident)}</b><table><tr><th>Option</th><th>Avg trip</th><th>Stuck</th></tr>${
        Object.entries(r.options).map(([k, v]) => `<tr${k === r.recommended ? ' style="font-weight:600"' : ""}><td>${esc(k)}</td><td>${v.avg_trip_min} min</td><td>${v.vehicles_reaching_impassable_road}</td></tr>`).join("")
      }</table><p class="hint">Recommended: ${esc(r.recommended)}. ${esc(r.model)}.</p>`;
    } else if (act === "signals") {
      $("decision").innerHTML = "Computing signal advice…";
      const r = await api(`/api/incidents/${id}/signals`);
      $("decision").innerHTML = `<b>Signal timing on the detour</b><table><tr><th>Junction</th><th>+veh/h</th><th>Cycle</th><th>Green N-S / E-W</th></tr>${
        r.junctions.map((j) => `<tr><td>${esc(j.junction)}</td><td>${j.extra_vehicles_per_hr}</td><td>${j.recommended_cycle_s}s</td><td>${j.recommended_green_s["North-South"]}s / ${j.recommended_green_s["East-West"]}s</td></tr>`).join("")
      }</table><p class="hint">${esc(r.method)}. Advice only; apply at the controller.</p>`;
    } else if (act === "bulletin") {
      const r = await api(`/api/incidents/${id}/bulletin`);
      $("decision").innerHTML = `<b>Draft bulletin (approve before sending)</b><p>${esc(r.english)}</p><p>${esc(r.telugu)}</p><p class="hint">${esc(r.note)}</p>`;
    }
  } catch (err) { toast(err.message); }
});

function selectIncident(id) {
  state.selected = id;
  const inc = state.incidents.find((i) => i.id === id);
  if (inc) map.panTo([inc.lat, inc.lon]);
  document.querySelectorAll("#officer-incidents li").forEach((li) => li.classList.toggle("sel", li.dataset.id === id));
}

async function login() {
  try {
    const r = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: $("user").value, password: $("pass").value }) });
    state.token = r.token; sessionStorage.setItem("rp_token", r.token);
    $("login-msg").textContent = ""; showDash(); connect();
  } catch (e) { $("login-msg").textContent = e.message; }
}

function logout() {
  state.token = null; sessionStorage.removeItem("rp_token"); showDash(); connect();
}

function showDash() {
  $("login-box").classList.toggle("hidden", !!state.token);
  $("dash").classList.toggle("hidden", !state.token);
}

// ---------------------------------------------------------------- live stream
// Officer stream: the 8-hour login token never goes in a URL. We exchange it (in a header) for a one-time ticket
// valid 60 s. If the connection drops, the browser's retry reuses the spent ticket, gets 401 and closes; we then
// fetch a fresh ticket and reconnect.
let es = null;
let reconnectTimer = null;
async function connect() {
  clearTimeout(reconnectTimer);
  if (es) { es.close(); es = null; }
  let url = "/api/stream";
  if (state.token && state.view === "authority") {
    try {
      const t = await api("/api/stream/ticket", { method: "POST", body: "{}" });
      url += `?ticket=${encodeURIComponent(t.ticket)}`;
    } catch (e) {
      $("conn").textContent = "offline – retrying"; $("conn").classList.remove("live");
      reconnectTimer = setTimeout(connect, 3000);
      return;
    }
  }
  const source = new EventSource(url);
  es = source;
  source.onopen = () => { $("conn").textContent = "live"; $("conn").classList.add("live"); };
  source.onerror = () => {
    $("conn").textContent = "reconnecting…"; $("conn").classList.remove("live");
    if (source.readyState === EventSource.CLOSED && es === source) reconnectTimer = setTimeout(connect, 2000);
  };
  source.onmessage = (msg) => {
    const snap = JSON.parse(msg.data);
    state.incidents = snap.incidents; state.hotspots = snap.hotspots.hotspots;
    for (const i of state.incidents) {
      if (i.published && !state.seen.has(i.id)) {
        state.seen.add(i.id);
        if (!state.firstSnapshot) toast(`New alert: ${i.title} (${i.severity})`);
      }
    }
    state.firstSnapshot = false;
    drawIncidents(); drawHotspots(); renderPublic();
    if (state.token) renderOfficer(snap.events);
    $("rain-source").textContent = `Rain now: ${snap.hotspots.rain_mm_per_hr} mm/h (${snap.hotspots.rain_source})`;
    if (state.start && state.end && state.view === "commuter") debounceRoute();
  };
}
let routeTimer = null;
function debounceRoute() { clearTimeout(routeTimer); routeTimer = setTimeout(findRoute, 600); }

// ---------------------------------------------------------------- controls
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  state.view = b.dataset.view;
  $("view-commuter").classList.toggle("hidden", state.view !== "commuter");
  $("view-authority").classList.toggle("hidden", state.view !== "authority");
  layers.routes.clearLayers(); drawPins(); connect();
}));
$("vehicle").addEventListener("change", findRoute);
$("btn-reroute").addEventListener("click", findRoute);
$("btn-clear-route").addEventListener("click", () => {
  state.start = state.end = null; layers.routes.clearLayers(); $("route-result").innerHTML = "";
  $("route-hint").innerHTML = "Click the map to set your <b>start</b>."; drawPins();
});
$("btn-report").addEventListener("click", async () => {
  if (!state.lastClick) { $("report-msg").textContent = "Click the map where the problem is first."; return; }
  try {
    const r = await api("/api/reports", { method: "POST", body: JSON.stringify({
      type: $("report-type").value, lat: state.lastClick[0], lon: state.lastClick[1],
      reporter_id: "web-" + (localStorage.getItem("rp_uid") || (() => { const u = Math.random().toString(36).slice(2, 10); localStorage.setItem("rp_uid", u); return u; })()),
      note: $("report-note").value || null,
    }) });
    $("report-msg").textContent = r.message;
  } catch (e) { $("report-msg").textContent = e.message; }
});
$("btn-login").addEventListener("click", login);
$("pass").addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });
$("btn-logout").addEventListener("click", logout);
$("btn-rain").addEventListener("click", async () => {
  const v = $("rain").value;
  try { await api("/api/weather/override", { method: "POST", body: JSON.stringify({ rain_mm_per_hr: v === "" ? null : Number(v) }) });
    toast(v === "" ? "Using forecast rain" : `Rain set to ${v} mm/h`); } catch (e) { toast(e.message); }
});
$("btn-pw").addEventListener("click", async () => {
  if (!state.lastClick) { toast("Click the map where the works are first"); return; }
  const starts = Date.now() / 1000 + Number($("pw-start").value) * 60;
  try {
    await api("/api/planned-works", { method: "POST", body: JSON.stringify({
      title: $("pw-title").value, lat: state.lastClick[0], lon: state.lastClick[1], starts,
      ends: starts + Number($("pw-hours").value) * 3600, full_closure: $("pw-full").checked,
    }) });
    toast("Planned works registered");
  } catch (e) { toast(e.message); }
});
$("btn-eval").addEventListener("click", async () => {
  $("eval-result").innerHTML = "Running…";
  try {
    const r = await api("/api/evaluation");
    const a = r.without_roadpulse, b = r.with_roadpulse;
    $("eval-result").innerHTML = `<table><tr><th></th><th>Without</th><th>With RoadPulse</th></tr>
      <tr><td>Avg trip</td><td>${a.avg_trip_min} min</td><td>${b.avg_trip_min} min</td></tr>
      <tr><td>Vehicles reaching an impassable road</td><td>${a.vehicles_reaching_impassable_road}</td><td>${b.vehicles_reaching_impassable_road}</td></tr>
      <tr><td>Total vehicle-hours</td><td>${a.total_vehicle_hours}</td><td>${b.total_vehicle_hours}</td></tr></table>
      <p class="hint">${r.pct_time_saved}% time saved on ${a.trips} simulated trips. ${esc(r.model)}.</p>
      <p class="hint"><b>Assumptions:</b> ${r.assumptions.map(esc).join(" ")}</p>`;
  } catch (e) { $("eval-result").innerHTML = `<p class="hint">${esc(e.message)}</p>`; }
});

showDash();
loadNetwork().then(connect).catch((e) => toast(`Could not load map: ${e.message}`));
