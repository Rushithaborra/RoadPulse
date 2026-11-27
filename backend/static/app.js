/* RoadPulse frontend: one page, two views (commuter, authority), live via Server-Sent Events.
   Everything shown comes from the API; nothing here invents traffic, incidents or metrics. */
"use strict";

const SEV_COLOR = { Low: "#c99a06", Medium: "#e8770f", Severe: "#d1242f", Closed: "#6e2b8a" };
const ROUTE_COLORS = ["#1558c0", "#1a7f37", "#8250df"];
const VEHICLE_LABEL = { two_wheeler: "two-wheeler", auto: "auto-rickshaw", car: "car", bus: "bus", ambulance: "ambulance" };
const TYPE_GLYPH = { flood: "W", accident: "A", blockage: "X", construction: "C", slowdown: "S" };
const TYPE_LABEL = { flood: "Waterlogging", accident: "Accident", blockage: "Road blocked", construction: "Construction", slowdown: "Slow traffic" };
// RTC Complex = APSRTC Dwaraka Bus Station, position from OpenStreetMap. Used only as a reference point on the map.
const RTC = { name: "RTC Complex (Dwaraka Bus Station)", lat: 17.7238639, lon: 83.3068879 };
const STALE_AFTER_S = 35;       // the server sends a heartbeat every ~10 s; this long without anything = stale

function loadMine() {
  try { return JSON.parse(localStorage.getItem("rp_mine") || "[]"); } catch (e) { return []; }
}
const state = {
  view: "commuter", mode: "route",     // mode: where the next map click goes (route | report | works)
  token: sessionStorage.getItem("rp_token"),
  start: null, end: null, reportPoint: null, worksPoint: null,
  incidents: [], hotspots: [], selected: null, evidence: {},
  prev: null,                           // last snapshot's incidents by id, to announce real changes
  routeSig: "", routeSeq: 0, hasRoute: false,
  connected: false, everConnected: false, lastHeard: 0, lostSince: 0,
  mapWarning: null, mine: loadMine(), lastPoll: 0,
};

const $ = (id) => document.getElementById(id);
const SIM = `<span class="badge sim" title="From the demo simulator, not a real sensor or person">SIMULATED</span>`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDist = (m) => (m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(2)} km`);
const fmtTime = (s) => (s < 90 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`);
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString();
const ll5 = (p) => `${p[0].toFixed(5)}, ${p[1].toFixed(5)}`;

function haversine(a, b) {
  const R = 6371000, rad = Math.PI / 180;
  const dLat = (b[0] - a[0]) * rad, dLon = (b[1] - a[1]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
const pathLen = (c) => c.reduce((s, p, i) => (i ? s + haversine(c[i - 1], p) : 0), 0);

function toast(msg, ms = 4500) {
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

// ---------------------------------------------------------------- banner and connection status
function renderBanner() {
  const b = $("banner");
  const lost = !state.connected && state.lostSince && Date.now() - state.lostSince > 4000;
  const stale = state.connected && state.lastHeard && (Date.now() - state.lastHeard) / 1000 > STALE_AFTER_S;
  const msgs = [];
  if (lost) msgs.push("Live updates lost: the information below may be out of date. Reconnecting…");
  else if (stale) msgs.push(`No update from the server for ${Math.round((Date.now() - state.lastHeard) / 1000)} s: the information below may be out of date.`);
  if (state.mapWarning) msgs.push(`MAP WARNING: ${state.mapWarning}`);
  b.classList.toggle("hidden", !msgs.length);
  b.classList.toggle("err", msgs.length > 0 && (lost || stale || !!state.mapWarning));
  b.textContent = msgs.join("  ·  ");
}

function renderConn() {
  const c = $("conn");
  const age = Math.round((Date.now() - state.lastHeard) / 1000);
  if (!state.connected) {
    c.className = "chip err"; c.textContent = state.everConnected ? "offline · reconnecting…" : "connecting…";
  } else if (age > STALE_AFTER_S) {
    c.className = "chip warn"; c.textContent = `stale · last update ${age}s ago`;
  } else {
    c.className = "chip ok live"; c.textContent = `live · updated ${age}s ago`;
  }
  renderBanner();
}
setInterval(renderConn, 1000);

// ---------------------------------------------------------------- map
const map = L.map("map", { zoomControl: true, preferCanvas: true });
// Standard OSM tiles are loaded on demand only while someone views the map (no bulk download / pre-fetch),
// per the OSM tile usage policy. Attribution links to the OSM copyright page; the road graph is OSM data too.
map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: 'Map tiles and road data &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" ' +
    'rel="noopener">OpenStreetMap contributors</a> (ODbL)',
}).addTo(map);
const layers = {};
for (const k of ["network", "incidents", "blocked", "hotspots", "routes", "landmarks", "mine", "pins"]) layers[k] = L.layerGroup().addTo(map);

function legendHtml() {
  const line = (c, h = 6, extra = "") => `<span class="sw" style="height:${h}px;background:${c};${extra}"></span>`;
  const dash = (c) => `<span class="sw" style="height:0;border-top:4px dashed ${c}"></span>`;
  const dot = (c) => `<span class="sw" style="height:0;border-top:4px dotted ${c}"></span>`;
  const mk = (cls, t, bg) => `<span class="sw"><span class="mk ${cls}" style="${bg ? `background:${bg};` : ""}width:20px;height:20px;font-size:11px;margin:auto">${t}</span></span>`;
  return `<details class="legend"><summary>Map legend (click to open)</summary>
    <h4>Affected roads (verified)</h4><ul>
      <li>${line(SEV_COLOR.Low, 8)}Low: slow, passable</li>
      <li>${line(SEV_COLOR.Medium, 8)}Medium</li>
      <li>${line(SEV_COLOR.Severe, 8)}Severe: some vehicles blocked</li>
      <li>${line(SEV_COLOR.Closed, 8)}Closed to all</li>
      <li>${dot("#111")}Blocked for the vehicle you picked</li>
      <li>${dash("#888")}Awaiting verification (officers only)</li></ul>
    <h4>Incident markers</h4><ul>
      <li>${mk("", "W", "#d1242f")}Waterlogging &nbsp; ${mk("", "A", "#d1242f")}Accident</li>
      <li>${mk("", "X", "#d1242f")}Road blocked &nbsp; ${mk("", "C", "#d1242f")}Construction</li></ul>
    <h4>Routes</h4><ul>
      <li>${line(ROUTE_COLORS[0], 7)}Recommended route</li>
      <li>${line(ROUTE_COLORS[1], 5)}Alternative route</li>
      <li>${dash("#d1242f")}Usual route (affected)</li></ul>
    <h4>Pins</h4><ul>
      <li>${mk("pinA", "A")}Start &nbsp; ${mk("pinB", "B")}Destination</li>
      <li>${mk("pinR", "<span>R</span>")}Your report / works</li>
      <li><span class="sw" style="text-align:center">&#9711;</span>Flood-watch point (simulated)</li></ul></details>`;
}
const Legend = L.control({ position: "bottomleft" });
Legend.onAdd = () => { const d = L.DomUtil.create("div"); d.innerHTML = legendHtml(); L.DomEvent.disableClickPropagation(d); return d; };
Legend.addTo(map);

function setMapOverlay(html) {
  const o = $("map-overlay");
  if (html == null) { o.classList.add("hidden"); return; }
  o.classList.remove("hidden"); o.innerHTML = html;
}

async function loadNetwork() {
  setMapOverlay("Loading road network…");
  let net;
  try {
    net = await api("/api/network");
  } catch (e) {
    setMapOverlay(`<div>Could not load the road network.</div><div class="hint">${esc(e.message)}</div><button id="btn-retry">Try again</button>`);
    $("btn-retry").addEventListener("click", () => loadNetwork().then(connect).catch(() => {}));
    throw e;
  }
  map.setView(net.center, 16);
  const mm = $("mapmode");
  mm.classList.remove("hidden");
  state.mapWarning = net.map_warning || null;
  if (net.map_mode === "synthetic") {
    mm.className = "chip err"; mm.textContent = "SYNTHETIC grid: not real roads";
  } else {
    mm.className = "chip ok";
    mm.textContent = net.hotspots_source === "synthetic" ? "Real OpenStreetMap roads · demo hotspots simulated" : "Real OpenStreetMap roads";
  }
  layers.network.clearLayers();
  for (const s of net.segments) {
    L.polyline(s.coords, { color: s.speed_kph >= 40 ? "#6b7686" : "#9aa3b0", weight: s.speed_kph >= 40 ? 4 : 2.5, opacity: 0.6, interactive: false })
      .addTo(layers.network);
  }
  drawLandmarks(net);
  setMapOverlay(null);
  renderBanner();
}

function drawLandmarks(net) {
  layers.landmarks.clearLayers(); $("goto").innerHTML = "";
  if (net.map_mode !== "osm") return;
  const marks = [{ name: "Dwaraka Nagar", lat: net.center[0], lon: net.center[1], zoom: 16 }, { ...RTC, zoom: 17 }];
  for (const m of marks) {
    L.circleMarker([m.lat, m.lon], { radius: 4, color: "#18202c", fillColor: "#18202c", fillOpacity: 1, weight: 1 })
      .bindTooltip(m.name, { permanent: true, direction: "right", offset: [6, 0], className: "lm", interactive: false }).addTo(layers.landmarks);
    const b = document.createElement("button");
    b.className = "small ghost"; b.textContent = m.name.split(" (")[0];
    b.addEventListener("click", () => map.setView([m.lat, m.lon], m.zoom));
    $("goto").appendChild(b);
  }
  $("goto").insertAdjacentHTML("afterbegin", `<span class="hint">Jump to:</span>`);
}

// ---------------------------------------------------------------- map clicks and pins
function setMode(m) {
  state.mode = m;
  $("map").style.cursor = m === "route" ? "" : "crosshair";
  $("btn-pick-report").classList.toggle("active", m === "report");
  $("btn-pick-works").classList.toggle("active", m === "works");
  if (m !== "route") {
    toast(m === "report" ? "Click the map where the problem is." : "Click the map where the works are.");
    // on a phone the map is above the form: bring it into view so the next tap lands on it
    if (window.innerWidth <= 860) $("map").scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

map.on("click", (e) => {
  const p = [e.latlng.lat, e.latlng.lng];
  if (state.mode === "report") { state.reportPoint = p; setMode("route"); updateReportUi(); drawPins(); return; }
  if (state.mode === "works") { state.worksPoint = p; setMode("route"); updateWorksUi(); drawPins(); return; }
  if (state.view !== "commuter") return;
  if (!state.start || state.end) {
    state.start = p; state.end = null; state.hasRoute = false; layers.routes.clearLayers(); $("route-result").innerHTML = "";
  } else {
    state.end = p;
  }
  drawPins(); updateOd();
  if (state.end) findRoute();
});

const pinIcon = (cls, t) => L.divIcon({ className: "", html: `<div class="mk ${cls}">${t}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] });

function drawPins() {
  layers.pins.clearLayers();
  if (state.start) L.marker(state.start, { icon: pinIcon("pinA", "A"), zIndexOffset: 900 }).bindTooltip("Start").addTo(layers.pins);
  if (state.end) L.marker(state.end, { icon: pinIcon("pinB", "B"), zIndexOffset: 900 }).bindTooltip("Destination").addTo(layers.pins);
  if (state.reportPoint) L.marker(state.reportPoint, { icon: pinIcon("pinR", "<span>R</span>") }).bindTooltip("Report location").addTo(layers.pins);
  if (state.worksPoint && state.view === "authority") L.marker(state.worksPoint, { icon: pinIcon("pinR", "<span>W</span>") }).bindTooltip("Planned works location").addTo(layers.pins);
  $("btn-reroute").disabled = !(state.start && state.end);
}

function updateOd() {
  $("od-start").textContent = state.start ? `Start · ${ll5(state.start)}` : "Click the map to set your start";
  $("od-start").classList.toggle("muted", !state.start);
  $("od-end").textContent = state.end ? `Destination · ${ll5(state.end)}` : state.start ? "Now click your destination" : "Then click your destination";
  $("od-end").classList.toggle("muted", !state.end);
}

// ---------------------------------------------------------------- incidents on the map
function incidentPopup(inc) {
  const blocked = inc.affected_vehicles.length ? inc.affected_vehicles.map((v) => VEHICLE_LABEL[v]).join(", ") : "none";
  return `${inc.simulated ? "<i>Simulated demo data</i><br>" : ""}<b>${esc(inc.title)}</b><br>` +
    `${esc(TYPE_LABEL[inc.type] || inc.type)} · ${inc.severity}${inc.depth_cm != null ? ` · ${Math.round(inc.depth_cm)} cm` : ""}<br>` +
    `${inc.road ? `Road: ${esc(inc.road)}<br>` : ""}Avoid: ${esc(blocked)}<br><small>${inc.status} · confidence ${inc.confidence.toFixed(2)}</small>`;
}

function drawIncidents() {
  layers.incidents.clearLayers(); layers.blocked.clearLayers();
  const vehicle = $("vehicle").value;
  for (const inc of state.incidents) {
    if (inc.status === "Cleared" || !inc.active) continue;
    const color = SEV_COLOR[inc.severity] || "#555";
    const pending = !inc.published;
    for (const seg of inc.affected_segments) {
      L.polyline(seg, { color, weight: 10, opacity: pending ? 0.45 : 0.85, dashArray: pending ? "6 8" : null, interactive: false }).addTo(layers.incidents);
      if (!pending && state.view === "commuter" && inc.affected_vehicles.includes(vehicle)) {
        L.polyline(seg, { color: "#111", weight: 3, dashArray: "1 7", lineCap: "round", interactive: false }).addTo(layers.blocked);
      }
    }
    const icon = L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13],
      html: `<div class="mk ${pending ? "pending" : ""}" style="background:${color}">${TYPE_GLYPH[inc.type] || "!"}</div>` });
    const m = L.marker([inc.lat, inc.lon], { icon, zIndexOffset: 500 }).bindPopup(incidentPopup(inc));
    m.on("click", () => { if (state.token && state.view === "authority") selectIncident(inc.id); });
    m.addTo(layers.incidents);
  }
}

function drawHotspots() {
  layers.hotspots.clearLayers();
  for (const h of state.hotspots) {
    const color = h.risk === "high" ? "#d1242f" : h.risk === "watch" ? "#e8770f" : "#1a7f37";
    L.circle([h.lat, h.lon], { radius: 40, color, weight: 2, fillOpacity: 0.12 })
      .bindTooltip(`${h.simulated_location ? "SIMULATED point · " : ""}${esc(h.name)}: ${h.depth_cm} cm · ${esc(h.message)}`).addTo(layers.hotspots);
  }
}

function drawMine() {
  layers.mine.clearLayers();
  for (const r of state.mine) {
    if (r.outcome !== "pending") continue;
    L.marker([r.lat, r.lon], { icon: pinIcon("pinR", "<span>R</span>") })
      .bindTooltip(`Your report (${TYPE_LABEL[r.type] || r.type}): waiting for officer review`).addTo(layers.mine);
  }
}

// ---------------------------------------------------------------- routing
async function findRoute() {
  if (!(state.start && state.end) || state.view !== "commuter") return;
  const vehicle = $("vehicle").value, seq = ++state.routeSeq;
  const out = $("route-result");
  if (!state.hasRoute) out.innerHTML = `<p class="msg busy">Calculating route…</p>`;
  try {
    const r = await api("/api/routes", { method: "POST", body: JSON.stringify({
      from_lat: state.start[0], from_lon: state.start[1], to_lat: state.end[0], to_lon: state.end[1], vehicle,
    }) });
    if (seq !== state.routeSeq) return;   // a newer request superseded this one
    state.hasRoute = true; showRoutes(r);
  } catch (e) {
    if (seq !== state.routeSeq) return;
    state.hasRoute = false; layers.routes.clearLayers();
    out.innerHTML = `<div class="alert err"><b>No route.</b> ${esc(e.message)}</div>`;
  }
}

function showRoutes(r) {
  layers.routes.clearLayers();
  const out = $("route-result");
  const usualLen = pathLen(r.usual_route);
  const hit = r.usual_route_hits.length > 0;
  if (hit) {
    L.polyline(r.usual_route, { color: "#d1242f", weight: 4, dashArray: "4 8", opacity: 0.9, interactive: false }).addTo(layers.routes);
  }
  r.routes.slice().reverse().forEach((rt, iRev) => {
    const i = r.routes.length - 1 - iRev;
    L.polyline(rt.coords, { color: ROUTE_COLORS[i], weight: i === 0 ? 7 : 5, opacity: i === 0 ? 0.95 : 0.75 })
      .bindTooltip(`Route ${i + 1}: ${fmtTime(rt.time_s)} · ${fmtDist(rt.length_m)}`).addTo(layers.routes);
  });
  let html = "";
  const best = r.routes[0];
  const sameAsUsual = best && JSON.stringify(best.coords) === JSON.stringify(r.usual_route);
  if (hit && best && !sameAsUsual) {
    html += `<div class="alert">Your usual route is affected by: ${r.usual_route_hits.map((h) => esc(h.title)).join(", ")}. ` +
      `RoadPulse found a way round for your ${esc(VEHICLE_LABEL[r.vehicle])}. Diversion: ${fmtDist(best.length_m)} versus ${fmtDist(usualLen)} on the usual route.</div>`;
  } else if (hit && best) {
    html += `<div class="alert">Your usual route passes: ${r.usual_route_hits.map((h) => esc(h.title)).join(", ")}. ` +
      `It is still the best option for your ${esc(VEHICLE_LABEL[r.vehicle])}, but expect slower traffic there.</div>`;
  } else if (r.routes.length) {
    html += `<p class="hint">No verified disruption on the usual route.</p>`;
  }
  if (r.no_route) html += `<div class="alert err">No safe route for a ${esc(VEHICLE_LABEL[r.vehicle])} right now.</div>`;
  r.routes.forEach((rt, i) => {
    html += `<div class="route" style="border-color:${ROUTE_COLORS[i]}"><b>${i === 0 ? "Recommended route" : `Alternative ${i}`}</b> · ${fmtTime(rt.time_s)} · ${fmtDist(rt.length_m)}` +
      (r.routes.length > 1 ? `<div class="meta">${Math.round(rt.share * 100)}% of drivers are sent this way to spread the traffic</div>` : "") + `</div>`;
  });
  out.innerHTML = html;
}

// ---------------------------------------------------------------- commuter lists
function renderPublic() {
  const pub = state.incidents.filter((i) => i.published);
  $("alert-count").textContent = pub.length ? String(pub.length) : "";
  $("public-incidents").innerHTML = pub.length ? pub.map((i) => `<li data-id="${i.id}">
      <span class="badge sev-${i.severity}">${i.severity}</span>${i.simulated ? SIM : ""}<b>${esc(i.title)}</b>
      <div class="meta">${i.road ? `${esc(i.road)} · ` : ""}${i.depth_cm != null ? `${Math.round(i.depth_cm)} cm water · ` : ""}avoid: ${
        i.affected_vehicles.length ? i.affected_vehicles.map((v) => VEHICLE_LABEL[v]).join(", ") : "none"}</div>
      <div class="actions"><button class="small ghost" data-act="show">Show on map</button></div></li>`).join("")
    : `<li class="empty">No verified disruptions right now.</li>`;
  $("public-hotspots").innerHTML = state.hotspots.length ? state.hotspots.map((h) => `<li>${h.last_reading_simulated || h.simulated_location ? SIM : ""}<b>${esc(h.name)}</b> · ${h.depth_cm} cm
      <div class="meta risk-${h.risk}">${esc(h.message)}</div></li>`).join("") : `<li class="empty">No flood-watch points.</li>`;
}
$("public-incidents").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]"); if (!li || e.target.dataset.act !== "show") return;
  const inc = state.incidents.find((i) => i.id === li.dataset.id);
  if (inc) { map.setView([inc.lat, inc.lon], 18); }
});

// ---------------------------------------------------------------- citizen reports
const OUTCOME_TEXT = {
  pending: "Waiting for officer review. Other commuters cannot see it yet.",
  published: "Verified: now shown to all commuters.",
  rejected: "Reviewed by an officer: not confirmed.",
  resolved: "Resolved: the road is available again.",
  gone: "No longer tracked by the server.",
};
function saveMine() { try { localStorage.setItem("rp_mine", JSON.stringify(state.mine.slice(0, 8))); } catch (e) { /* private mode */ } }

function updateReportUi() {
  $("report-loc").textContent = state.reportPoint ? `Location · ${ll5(state.reportPoint)}` : "No location chosen";
  $("report-loc").classList.toggle("muted", !state.reportPoint);
  $("btn-report").disabled = !state.reportPoint;
}

function renderMine() {
  $("my-reports").innerHTML = state.mine.length ? state.mine.map((r) => `<li><b>${esc(TYPE_LABEL[r.type] || r.type)}</b>
      <span class="meta">· sent ${new Date(r.at).toLocaleTimeString()}</span>
      <div class="meta">${esc(OUTCOME_TEXT[r.outcome] || r.outcome)}</div></li>`).join("") : "";
  drawMine();
}

async function pollMine(force = false) {
  const open = state.mine.filter((r) => r.outcome === "pending" || r.outcome === "published");
  if (!open.length || (!force && Date.now() - state.lastPoll < 8000)) return;
  state.lastPoll = Date.now();
  let changed = false;
  for (const r of open) {
    try {
      const s = await api(`/api/reports/${encodeURIComponent(r.id)}/status`);
      if (s.outcome !== r.outcome) { r.outcome = s.outcome; changed = true; if (s.outcome === "published") toast("Your report was verified by an officer."); }
    } catch (e) { if (/not found/i.test(e.message)) { r.outcome = "gone"; changed = true; } }
  }
  if (changed) { saveMine(); renderMine(); }
}
setInterval(() => pollMine(), 10000);

$("btn-pick-report").addEventListener("click", () => setMode(state.mode === "report" ? "route" : "report"));
$("btn-report").addEventListener("click", async () => {
  const msg = $("report-msg"), btn = $("btn-report");
  if (!state.reportPoint) { msg.className = "msg err"; msg.textContent = "Choose the location on the map first."; return; }
  btn.disabled = true; msg.className = "msg busy"; msg.textContent = "Sending…";
  const uid = localStorage.getItem("rp_uid") || (() => { const u = Math.random().toString(36).slice(2, 10); try { localStorage.setItem("rp_uid", u); } catch (e) { /* ignore */ } return u; })();
  try {
    const type = $("report-type").value;
    const r = await api("/api/reports", { method: "POST", body: JSON.stringify({
      type, lat: state.reportPoint[0], lon: state.reportPoint[1], reporter_id: "web-" + uid, note: $("report-note").value.trim() || null,
    }) });
    msg.className = "msg ok";
    msg.textContent = r.status === "Reported"
      ? `${r.message}. An officer will review it before other commuters see it.` : `${r.message}.`;
    state.mine.unshift({ id: r.incident_id, type, lat: state.reportPoint[0], lon: state.reportPoint[1], at: Date.now(),
      outcome: r.status === "Reported" ? "pending" : "published" });
    state.mine = state.mine.slice(0, 8); saveMine(); renderMine();
    $("report-note").value = ""; state.reportPoint = null; updateReportUi(); drawPins();
  } catch (e) { msg.className = "msg err"; msg.textContent = e.message; btn.disabled = !state.reportPoint; }
});

// ---------------------------------------------------------------- authority
function evidenceHtml(i) {
  const d = state.evidence[i.id];
  if (!d) return `<div class="evidence muted">Loading evidence…</div>`;
  const times = Object.entries(d.status_times || {}).sort((a, b) => a[1] - b[1]).map(([k, v]) => `${k} ${clock(v)}`).join(" → ");
  const rows = (d.signals || []).slice().reverse().map((s) => `<tr><td>${clock(s.ts)}</td><td>${esc(s.source)}${s.simulated ? " " + SIM : ""}</td>
      <td>${s.weight}</td><td>${s.depth_cm != null ? `${Math.round(s.depth_cm)} cm ` : ""}${esc(s.note || "")}</td></tr>`).join("");
  return `<div class="evidence"><dl>
      <dt>Type</dt><dd>${esc(TYPE_LABEL[d.type] || d.type)}</dd>
      <dt>Road</dt><dd>${esc(d.road || "no named road")}</dd>
      <dt>Location</dt><dd>${ll5([d.lat, d.lon])}</dd>
      <dt>Severity</dt><dd>${d.severity}${d.depth_cm != null ? ` · ${Math.round(d.depth_cm)} cm` : ""} · blocks: ${esc(d.affected_vehicles.map((v) => VEHICLE_LABEL[v]).join(", ") || "none")}</dd>
      <dt>Confidence</dt><dd>${d.confidence.toFixed(2)} (auto-verify needs 0.70 and a trusted source)</dd>
      <dt>Timeline</dt><dd>${esc(times)}${d.cleared_reason ? ` · ${esc(d.cleared_reason)}` : ""}</dd></dl>
      <table><tr><th>Time</th><th>Source</th><th>Weight</th><th>Detail</th></tr>${rows || `<tr><td colspan="4" class="muted">No signals recorded.</td></tr>`}</table></div>`;
}

function officerItem(i) {
  const src = Object.entries(i.signals_by_source).map(([k, v]) => `${k}×${v}`).join(", ");
  const clearing = i.clearing_in_s != null ? ` · <b>auto-clear in ${i.clearing_in_s}s</b>` : "";
  const upcoming = !i.active && i.starts ? ` · starts ${clock(i.starts)}` : "";
  const sel = state.selected === i.id;
  return `<li class="${sel ? "sel" : ""}" data-id="${i.id}">
    <span class="badge sev-${i.severity}">${i.severity}</span><span class="badge st st-${i.status}">${i.status}</span>${i.simulated ? SIM : ""}
    <b>${esc(i.title)}</b>
    <div class="meta">${i.road ? `${esc(i.road)} · ` : ""}confidence ${i.confidence.toFixed(2)} · ${esc(src)}${i.depth_cm != null ? ` · ${Math.round(i.depth_cm)} cm` : ""}${clearing}${upcoming}</div>
    <div class="actions">
      ${i.status === "Reported" ? `<button class="small" data-act="verify">Verify</button><button class="small warn" data-act="reject">Reject</button>` : ""}
      ${["Reported", "Verified"].includes(i.status) ? `<button class="small ok" data-act="respond">Approve diversion</button>` : ""}
      <button class="small ghost" data-act="evidence">${sel ? "Hide evidence" : "Evidence"}</button>
      <button class="small ghost" data-act="whatif">What-if</button>
      <button class="small ghost" data-act="signals">Signals</button>
      <button class="small ghost" data-act="bulletin">Bulletin</button>
      <button class="small ghost" data-act="clear" title="Mark the incident resolved so the road becomes available again">Resolve</button>
    </div>${sel ? evidenceHtml(i) : ""}</li>`;
}

function renderOfficer(events) {
  const open = state.incidents.filter((i) => i.status !== "Cleared");
  const pending = open.filter((i) => i.status === "Reported"), active = open.filter((i) => i.status !== "Reported");
  $("kpis").innerHTML = `<div><b>${open.length}</b>open</div><div><b>${pending.length}</b>to review</div>
    <div><b>${open.filter((i) => i.status === "Responding").length}</b>responding</div>`;
  $("officer-pending").innerHTML = pending.length ? pending.map(officerItem).join("") : `<li class="empty">Nothing waiting for review.</li>`;
  $("officer-active").innerHTML = active.length ? active.map(officerItem).join("") : `<li class="empty">No active incidents.</li>`;
  $("officer-hotspots").innerHTML = state.hotspots.map((h) => {
    const eta = Object.entries(h.eta_min).map(([k, v]) => `${k}: ${v === null ? "–" : v === 0 ? "now" : v + " min"}`).join(" · ");
    const age = h.last_reading_age_s == null ? "no sensor data" : `sensor ${h.last_reading_age_s}s ago`;
    return `<li>${h.last_reading_simulated || h.simulated_location ? SIM : ""}<b>${esc(h.name)}</b> · ${h.depth_cm} cm · rise ${h.rise_cm_per_hr} cm/h
      <div class="meta risk-${h.risk}">${esc(h.message)}</div><div class="meta">${eta} · ${age}</div></li>`;
  }).join("");
  if (events) $("events").innerHTML = events.length ? events.map((e) => `<li>${clock(e.ts)} — ${esc(e.text)}</li>`).join("") : `<li class="empty">No activity yet.</li>`;
}

async function loadEvidence(id) {
  try { state.evidence[id] = await api(`/api/incidents/${id}`); } catch (e) { toast(e.message); }
  if (state.selected === id) renderOfficer();
}

function selectIncident(id) {
  state.selected = state.selected === id ? null : id;
  if (state.selected) {
    const inc = state.incidents.find((i) => i.id === id);
    if (inc) map.panTo([inc.lat, inc.lon]);
    loadEvidence(id);
  }
  renderOfficer();
}

async function officerClick(e) {
  const li = e.target.closest("li[data-id]"); if (!li) return;
  const id = li.dataset.id, act = e.target.dataset.act;
  if (!act) { return; }
  try {
    if (act === "evidence") { selectIncident(id); return; }
    if (["verify", "respond", "clear", "reject"].includes(act)) {
      await api(`/api/incidents/${id}/${act}`, { method: "POST", body: "{}" });
      toast({ verify: "Incident verified: now visible to commuters", respond: "Diversion approved", clear: "Incident resolved: road available again", reject: "Report rejected" }[act]);
      if (state.selected === id) loadEvidence(id);
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
      }</table><p class="hint">${esc(r.method)}. Advice only: RoadPulse does not control any signal.</p>`;
    } else if (act === "bulletin") {
      const r = await api(`/api/incidents/${id}/bulletin`);
      $("decision").innerHTML = `<b>Draft bulletin (approve before sending)</b><p>${esc(r.english)}</p><p>${esc(r.telugu)}</p><p class="hint">${esc(r.note)}</p>`;
    }
  } catch (err) { toast(err.message); $("decision").innerHTML = `<span class="muted">${esc(err.message)}</span>`; }
}
$("officer-pending").addEventListener("click", officerClick);
$("officer-active").addEventListener("click", officerClick);

async function login() {
  const msg = $("login-msg"), btn = $("btn-login");
  btn.disabled = true; msg.className = "msg busy"; msg.textContent = "Logging in…";
  try {
    const r = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: $("user").value, password: $("pass").value }) });
    state.token = r.token; sessionStorage.setItem("rp_token", r.token);
    $("pass").value = ""; msg.textContent = ""; showDash(); connect();
  } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
  btn.disabled = false;
}
function logout() {
  state.token = null; state.selected = null; state.evidence = {}; sessionStorage.removeItem("rp_token"); showDash(); connect();
}
function showDash() {
  $("login-box").classList.toggle("hidden", !!state.token);
  $("dash").classList.toggle("hidden", !state.token);
}

function updateWorksUi() {
  $("pw-loc").textContent = state.worksPoint ? `Location · ${ll5(state.worksPoint)}` : "No location chosen";
  $("pw-loc").classList.toggle("muted", !state.worksPoint);
  $("btn-pw").disabled = !state.worksPoint;
}
$("btn-pick-works").addEventListener("click", () => setMode(state.mode === "works" ? "route" : "works"));
$("btn-pw").addEventListener("click", async () => {
  const msg = $("pw-msg");
  const starts = Date.now() / 1000 + Number($("pw-start").value) * 60;
  msg.className = "msg busy"; msg.textContent = "Registering…";
  try {
    await api("/api/planned-works", { method: "POST", body: JSON.stringify({
      title: $("pw-title").value, lat: state.worksPoint[0], lon: state.worksPoint[1], starts,
      ends: starts + Number($("pw-hours").value) * 3600, full_closure: $("pw-full").checked,
    }) });
    msg.className = "msg ok"; msg.textContent = "Planned works registered."; state.worksPoint = null; updateWorksUi(); drawPins();
  } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
});

// ---------------------------------------------------------------- live stream
// Officer stream: the 8-hour login token never goes in a URL. We exchange it (in a header) for a one-time ticket
// valid 60 s. If the connection drops, the browser's retry reuses the spent ticket, gets 401 and closes; we then
// fetch a fresh ticket and reconnect.
let es = null;
let reconnectTimer = null;

function announce(snap) {
  const officerView = !!state.token && state.view === "authority";
  const now = new Map(snap.incidents.map((i) => [i.id, i]));
  if (state.prev) {
    for (const [id, i] of now) {
      const p = state.prev.get(id);
      if (officerView) {
        if (!p && i.status === "Reported") toast(`New report to review: ${i.title}`);
        else if (p && p.status !== i.status) toast(`${i.title}: ${p.status} → ${i.status}`);
      } else if (i.published && !(p && p.published)) toast(`New verified alert: ${i.title} (${i.severity})`);
      else if (i.published && p && p.published && p.severity !== i.severity) toast(`Updated: ${i.title} is now ${i.severity}`);
    }
    if (!officerView) {
      for (const [id, p] of state.prev) {
        const i = now.get(id);
        if (p.published && (!i || !i.published)) toast(`Resolved: ${p.title}. The road is available again.`);
      }
    }
  }
  state.prev = now;
}

const routeSignature = () => state.incidents.filter((i) => i.published)
  .map((i) => `${i.id}|${i.status}|${i.severity}|${Math.round(i.depth_cm ?? -1)}`).sort().join(";");

async function connect() {
  clearTimeout(reconnectTimer);
  if (es) { es.close(); es = null; }
  state.prev = null;   // a different stream (public/officer) starts a fresh baseline, so no false alerts
  let url = "/api/stream";
  if (state.token && state.view === "authority") {
    try {
      const t = await api("/api/stream/ticket", { method: "POST", body: "{}" });
      url += `?ticket=${encodeURIComponent(t.ticket)}`;
    } catch (e) {
      state.connected = false; state.lostSince = state.lostSince || Date.now(); renderConn();
      reconnectTimer = setTimeout(connect, 3000);
      return;
    }
  }
  const source = new EventSource(url);
  es = source;
  source.onopen = () => { state.connected = true; state.everConnected = true; state.lastHeard = Date.now(); state.lostSince = 0; renderConn(); };
  source.onerror = () => {
    state.connected = false; state.lostSince = state.lostSince || Date.now(); renderConn();
    if (source.readyState === EventSource.CLOSED && es === source) reconnectTimer = setTimeout(connect, 2000);
  };
  source.addEventListener("heartbeat", () => { state.lastHeard = Date.now(); renderConn(); });
  source.onmessage = (msg) => {
    state.lastHeard = Date.now(); state.connected = true; state.lostSince = 0;
    const snap = JSON.parse(msg.data);
    announce(snap);
    state.incidents = snap.incidents; state.hotspots = snap.hotspots.hotspots;
    drawIncidents(); drawHotspots(); renderPublic();
    if (state.token && state.view === "authority") {
      renderOfficer(snap.events);
      if (state.selected && state.incidents.some((i) => i.id === state.selected)) loadEvidence(state.selected);
      $("rain-source").textContent = `Rain now: ${snap.hotspots.rain_mm_per_hr} mm/h (${snap.hotspots.rain_source})`;
    }
    const sig = routeSignature();
    if (sig !== state.routeSig) {            // recompute the route only when verified incidents really changed
      state.routeSig = sig;
      if (state.start && state.end && state.view === "commuter") findRoute();
    }
    pollMine();
    renderConn();
  };
}

// ---------------------------------------------------------------- controls
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => { const on = x === b; x.classList.toggle("active", on); x.setAttribute("aria-selected", String(on)); });
  state.view = b.dataset.view; setMode("route");
  $("view-commuter").classList.toggle("hidden", state.view !== "commuter");
  $("view-authority").classList.toggle("hidden", state.view !== "authority");
  layers.routes.clearLayers(); state.hasRoute = false; drawPins(); drawIncidents(); connect();
  if (state.view === "commuter" && state.start && state.end) findRoute();
}));
$("vehicle").addEventListener("change", () => { drawIncidents(); findRoute(); });
$("btn-reroute").addEventListener("click", findRoute);
$("btn-clear-route").addEventListener("click", () => {
  state.start = state.end = null; state.hasRoute = false; layers.routes.clearLayers(); $("route-result").innerHTML = ""; drawPins(); updateOd();
});
$("btn-login").addEventListener("click", login);
$("pass").addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });
$("btn-logout").addEventListener("click", logout);
$("btn-rain").addEventListener("click", async () => {
  const v = $("rain").value;
  try { await api("/api/weather/override", { method: "POST", body: JSON.stringify({ rain_mm_per_hr: v === "" ? null : Number(v) }) });
    toast(v === "" ? "Using forecast rain" : `Rain set to ${v} mm/h (manual override, not a forecast)`); } catch (e) { toast(e.message); }
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
      <p class="hint">${r.pct_time_saved}% time saved on ${a.trips} simulated trips. ${esc(r.model)}. Model output, not field data.</p>
      <p class="hint"><b>Assumptions:</b> ${r.assumptions.map(esc).join(" ")}</p>`;
  } catch (e) { $("eval-result").innerHTML = `<p class="hint">${esc(e.message)}</p>`; }
});

showDash(); updateOd(); updateReportUi(); updateWorksUi(); renderMine(); renderConn();
loadNetwork().then(connect).catch(() => { /* the overlay already explains and offers a retry */ });
