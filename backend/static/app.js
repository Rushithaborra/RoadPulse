/* RoadPulse commuter app: live map, vehicle-aware routes, citizen reports with tracking.
   Only incidents the server has published (verified by police or trusted sensors) are shown or routed around. */
"use strict";

const { $, esc } = RP;
const state = {
  incidents: [], hotspots: [], start: null, end: null, picking: false, reportPoint: null,
  routeSig: null, routeSeq: 0, lastRoute: null, prev: null, mine: loadMine(), polling: false,
};

function loadMine() { try { return JSON.parse(localStorage.getItem("rp_mine") || "[]"); } catch (e) { return []; } }
function saveMine() { try { localStorage.setItem("rp_mine", JSON.stringify(state.mine.slice(0, 10))); } catch (e) { /* private mode */ } }
const vehicle = () => document.querySelector('input[name="veh"]:checked').value;

// ---------------------------------------------------------------- map
const map = RP.createMap("map");
const L_ = {};
for (const k of ["network", "watch", "incidents", "routes", "pins", "mine"]) L_[k] = L.layerGroup().addTo(map);
RP.legend(map, ["roads", "blocked", "signs", "routes", "watch"]);   // "Map legend"; roads "Blocked for the vehicle you picked"

const ll = (p) => `${p[0].toFixed(5)}, ${p[1].toFixed(5)}`;

function drawPins() {
  L_.pins.clearLayers();
  if (state.start) L.marker(state.start, { icon: RP.pinIcon("a", "A"), zIndexOffset: 900 }).bindTooltip("Start").addTo(L_.pins);
  if (state.end) L.marker(state.end, { icon: RP.pinIcon("b", "B"), zIndexOffset: 900 }).bindTooltip("Destination").addTo(L_.pins);
  if (state.reportPoint) L.marker(state.reportPoint, { icon: RP.pinIcon("r", "!"), zIndexOffset: 950 }).bindTooltip("Your report").addTo(L_.pins);
  $("od-start").textContent = state.start ? ll(state.start) : "Tap the map";
  $("od-start").className = state.start ? "" : "muted";
  $("od-end").textContent = state.end ? ll(state.end) : state.start ? "Now tap your destination" : "Then tap where you are going";
  $("od-end").className = state.end ? "" : "muted";
  $("btn-clear-route").disabled = !state.start;
}

map.on("click", (e) => {
  const p = [e.latlng.lat, e.latlng.lng];
  if (!RP.inPilot(e.latlng)) { RP.toast(RP.OUTSIDE, true); return; }
  if (state.picking) { state.reportPoint = p; setPicking(false); updateReport(); drawPins(); return; }
  if (!$("tab-route").hidden) {
    if (!state.start || state.end) { state.start = p; state.end = null; state.lastRoute = null; L_.routes.clearLayers(); $("route-result").innerHTML = ""; }
    else state.end = p;
    drawPins();
    if (state.end) findRoute();
  }
});

function popup(inc) {
  const avoid = inc.affected_vehicles.length ? inc.affected_vehicles.map((v) => RP.VEHICLE[v]).join(", ") : "No vehicle class is blocked";
  return `<b>${esc(inc.title)}</b><div style="margin:6px 0">${RP.sevTag(inc.severity)} ${inc.review.urgent && inc.review.stage !== "confirmed" ? '<span class="tag urgent">Urgent</span>' : ""} ${RP.simTag(inc.simulated)}</div>` +
    `${inc.depth_cm != null ? `Water about ${Math.round(inc.depth_cm)} cm<br>` : ""}Avoid: ${esc(avoid)}<br><span class="muted small">${esc(inc.review.label)}</span>`;
}

function draw() {
  RP.drawIncidents(L_.incidents, state.incidents, { blockedFor: vehicle(), popup });
  RP.drawHotspots(L_.watch, state.hotspots);
  L_.mine.clearLayers();
  for (const r of state.mine) {
    if (r.outcome !== "pending") continue;
    L.circleMarker([r.lat, r.lon], { radius: 7, color: "#23272c", weight: 2, dashArray: "2 3", fillColor: "#f5b700", fillOpacity: 0.9 })
      .bindTooltip(`Your report: ${esc(r.stageLabel || "with the control room")}`).addTo(L_.mine);
  }
}

// ---------------------------------------------------------------- live list
function renderLive() {
  const pub = state.incidents.filter((i) => i.published);
  $("live-count").textContent = pub.length ? `${pub.length} confirmed` : "";
  $("live-list").innerHTML = pub.length ? pub.map((i) => {
    const avoid = i.affected_vehicles.length ? `Avoid by ${i.affected_vehicles.map((v) => RP.VEHICLE[v].toLowerCase()).join(", ")}` : "Passable with care";
    const style = i.severity === "Closed" ? "" : `background:${RP.SEV[i.severity]}`;
    return `<li class="clickable" data-id="${i.id}" tabindex="0">
      <span class="sign ${i.severity}" style="${style}">${RP.GLYPH[i.type]}</span>
      <div class="body"><div class="title">${esc(RP.TYPE[i.type])}${i.road ? ` on ${esc(i.road)}` : ""}</div>
        <div class="meta">${esc(avoid)}${i.depth_cm != null ? `. Water about ${Math.round(i.depth_cm)} cm` : ""}</div>
        <div class="tags">${RP.sevTag(i.severity)}${i.review.urgent && i.review.stage !== "confirmed" ? '<span class="tag urgent">Urgent, police on the way</span>' : ""}${i.status === "Responding" ? '<span class="tag plain">Diversion in force</span>' : ""}${RP.simTag(i.simulated)}</div></div></li>`;
  }).join("") : `<li class="empty">No confirmed disruptions in the pilot area right now.</li>`;
}
function focusIncident(id) {
  const i = state.incidents.find((x) => x.id === id); if (!i) return;
  map.setView([i.lat, i.lon], 17);
  L_.incidents.eachLayer((l) => { if (l.getLatLng && l.getLatLng().equals([i.lat, i.lon])) l.openPopup(); });
}
$("live-list").addEventListener("click", (e) => { const li = e.target.closest("li[data-id]"); if (li) focusIncident(li.dataset.id); });
$("live-list").addEventListener("keydown", (e) => { const li = e.target.closest("li[data-id]"); if (li && e.key === "Enter") focusIncident(li.dataset.id); });

// ---------------------------------------------------------------- routing
async function findRoute(reason) {
  if (!(state.start && state.end)) return;
  const seq = ++state.routeSeq, v = vehicle();
  const out = $("route-result");
  if (!state.lastRoute) out.innerHTML = `<p class="muted small">Finding a route</p>`;
  try {
    const r = await RP.post("/api/routes", { from_lat: state.start[0], from_lon: state.start[1], to_lat: state.end[0], to_lon: state.end[1], vehicle: v });
    if (seq !== state.routeSeq) return;
    const before = state.lastRoute && state.lastRoute.routes[0] ? JSON.stringify(state.lastRoute.routes[0].coords) : null;
    state.lastRoute = r; showRoute(r);
    const after = r.routes[0] ? JSON.stringify(r.routes[0].coords) : null;
    if (reason && before && after && before !== after) RP.toast(`Your route changed: ${reason}`);
  } catch (e) {
    if (seq !== state.routeSeq) return;
    state.lastRoute = null; L_.routes.clearLayers();
    out.innerHTML = `<div class="note bad"><b>No route.</b> ${esc(e.message)}</div>`;
  }
}

function showRoute(r) {
  L_.routes.clearLayers();
  const hit = r.usual_route_hits.length > 0, best = r.routes[0];
  if (hit) L.polyline(r.usual_route, { color: RP.SEV.Severe, weight: 4, dashArray: "5 8", opacity: 0.9, interactive: false }).addTo(L_.routes);
  r.routes.slice(1).forEach((rt) => L.polyline(rt.coords, { color: "#8d939a", weight: 5, opacity: 0.8 }).bindTooltip(`${RP.fmtTime(rt.time_s)}, ${RP.fmtDist(rt.length_m)}`).addTo(L_.routes));
  if (best) {
    L.polyline(best.coords, { color: "#fff", weight: 10, opacity: 1, interactive: false }).addTo(L_.routes);
    L.polyline(best.coords, { color: "#23272c", weight: 6, opacity: 1 }).bindTooltip("Recommended route").addTo(L_.routes);
  }
  const vname = RP.VEHICLE[r.vehicle].toLowerCase();
  const same = best && JSON.stringify(best.coords) === JSON.stringify(r.usual_route);
  const names = r.usual_route_hits.map((h) => {
    const i = state.incidents.find((x) => x.id === h.id);
    return esc(i ? `${RP.TYPE[i.type].toLowerCase()}${i.road ? ` on ${i.road}` : ""}` : h.title);
  }).join(", ");
  let html = "";
  if (r.no_route) html += `<div class="note bad">There is no safe way through for a ${esc(vname)} right now. Wait for the road to clear or try another vehicle.</div>`;
  if (best) {
    html += `<div class="eta"><b>${RP.fmtTime(best.time_s)}</b><span>${RP.fmtDist(best.length_m)}</span></div>`;
    if (hit && !same) html += `<div class="note warn">Your usual way is affected by ${names}. This route goes round it for a ${esc(vname)}; the usual way is ${RP.fmtDist(RP.pathLen(r.usual_route))}.</div>`;
    else if (hit) html += `<div class="note warn">Your usual way passes ${names}, but it is still the best option for a ${esc(vname)}. Expect slow traffic there.</div>`;
    else html += `<p class="eta-sub">No confirmed disruption on your way.</p>`;
  }
  if (r.routes.length > 1) {
    html += `<ul class="alts">${r.routes.map((rt, i) => `<li><span><i style="background:${i ? "#8d939a" : "#23272c"}"></i>${i ? `Alternative ${i}` : "Recommended"}: ${RP.fmtTime(rt.time_s)}, ${RP.fmtDist(rt.length_m)}</span>
      <span class="share" title="Traffic is spread over the routes so the detour does not jam">${Math.round(rt.share * 100)}% sent this way</span></li>`).join("")}</ul>`;
  }
  $("route-result").innerHTML = html;
}

document.querySelectorAll('input[name="veh"]').forEach((el) => el.addEventListener("change", () => { draw(); findRoute(); }));
$("btn-clear-route").addEventListener("click", () => {
  state.start = state.end = state.lastRoute = null; L_.routes.clearLayers(); $("route-result").innerHTML = ""; drawPins();
});

// ---------------------------------------------------------------- tabs
function showTab(name) {
  for (const t of ["route", "report"]) {
    $(`tab-${t}`).hidden = t !== name;
    $(`tab-btn-${t}`).setAttribute("aria-selected", String(t === name));
  }
  if (name !== "report") setPicking(false);
}
$("tab-btn-route").addEventListener("click", () => showTab("route"));
$("tab-btn-report").addEventListener("click", () => showTab("report"));

// ---------------------------------------------------------------- reporting
function setPicking(on) {
  state.picking = on;
  $("map").classList.toggle("picking", on);
  $("btn-pick").classList.toggle("picking", on);
  $("btn-pick").textContent = on ? "Tap the map now" : state.reportPoint ? "Choose a different place" : "Choose on the map";
  if (on && window.innerWidth <= 860) $("map").scrollIntoView({ block: "start", behavior: "smooth" });
}
function updateReport() {
  $("report-loc").textContent = state.reportPoint ? `Chosen: ${ll(state.reportPoint)}` : "No place chosen yet";
  $("report-loc").className = state.reportPoint ? "small" : "muted small";
  $("btn-send").disabled = !state.reportPoint;
  setPicking(state.picking);
}
$("btn-pick").addEventListener("click", () => setPicking(!state.picking));

$("btn-send").addEventListener("click", async () => {
  const msg = $("report-msg"), btn = $("btn-send");
  if (!state.reportPoint) return;
  btn.disabled = true; msg.className = "msg"; msg.textContent = "Sending";
  let uid = null;
  try { uid = localStorage.getItem("rp_uid"); if (!uid) { uid = "web-" + Math.random().toString(36).slice(2, 10); localStorage.setItem("rp_uid", uid); } } catch (e) { uid = "anonymous"; }
  const type = document.querySelector('input[name="rtype"]:checked').value;
  try {
    const r = await RP.post("/api/reports", { type, lat: state.reportPoint[0], lon: state.reportPoint[1], reporter_id: uid, note: $("report-note").value.trim() || null });
    state.mine.unshift({ id: r.incident_id, type, at: Date.now(), lat: state.reportPoint[0], lon: state.reportPoint[1],
      outcome: r.status === "Reported" ? "pending" : "published", stage: null });
    saveMine();
    msg.className = "msg ok";
    msg.textContent = r.status === "Reported" ? "Report sent. It is now with the traffic control room." : "Report sent. It matches a disruption that is already confirmed.";
    state.reportPoint = null; $("report-note").value = ""; updateReport(); drawPins();
    pollMine(true);
  } catch (e) { msg.className = "msg err"; msg.textContent = e.message; btn.disabled = false; }
});

const OUTCOME = { rejected: "Could not be confirmed", resolved: "Road cleared", gone: "No longer tracked" };
function renderMine() {
  const open = state.mine.filter((r) => r.outcome === "pending").length;
  $("mine-count").hidden = !open; $("mine-count").textContent = open;
  $("my-reports").innerHTML = state.mine.length ? state.mine.map((r) => {
    const label = r.stageLabel || OUTCOME[r.outcome] || "With the traffic control room";
    return `<li><div class="body"><div class="title">${esc(RP.TYPE[r.type])}</div>
      <div class="meta">Sent ${new Date(r.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}. ${esc(label)}</div>
      ${r.stage && r.outcome !== "gone" ? RP.stepsHtml(r.stage) : ""}</div></li>`;
  }).join("") : `<li class="empty">Reports you send from this phone or browser appear here, with each check as it happens.</li>`;
  draw();
}
async function pollMine(force = false) {
  if (state.polling) return;
  const open = state.mine.filter((r) => r.outcome === "pending" || r.outcome === "published");
  if (!open.length) { renderMine(); return; }
  state.polling = true;
  let changed = false;
  for (const r of open) {
    try {
      const s = await RP.api(`/api/reports/${encodeURIComponent(r.id)}/status`);
      if (s.stage_code !== r.stage || s.outcome !== r.outcome) {
        if (r.stage && s.stage_code !== r.stage) { RP.toast(`Your ${RP.TYPE[r.type].toLowerCase()} report: ${s.stage}`); $("report-msg").textContent = ""; }
        r.stage = s.stage_code; r.stageLabel = s.stage; r.outcome = s.outcome; changed = true;
      }
    } catch (e) { if (/not found/i.test(e.message)) { r.outcome = "gone"; r.stageLabel = null; changed = true; } }
  }
  state.polling = false;
  if (changed || force) { saveMine(); renderMine(); }
}
setInterval(pollMine, 8000);

// ---------------------------------------------------------------- live updates
function onSnapshot(snap) {
  const pub = snap.incidents.filter((i) => i.published);
  const prev = state.prev;
  state.incidents = snap.incidents; state.hotspots = snap.hotspots.hotspots || [];
  if (prev) {
    for (const i of pub) if (!prev[i.id]) RP.toast(`New on the map: ${RP.TYPE[i.type]}${i.road ? ` on ${i.road}` : ""}`);
    for (const id of Object.keys(prev)) if (!pub.find((i) => i.id === id)) RP.toast(`Cleared: ${RP.TYPE[prev[id].type]}${prev[id].road ? ` on ${prev[id].road}` : ""}. The road is available again.`);
  }
  state.prev = Object.fromEntries(pub.map((i) => [i.id, i]));
  draw(); renderLive();
  const sig = pub.map((i) => `${i.id}:${i.severity}:${i.status}`).sort().join("|");
  if (sig !== state.routeSig) {
    const first = state.routeSig === null;
    state.routeSig = sig;
    if (!first) { findRoute("a disruption changed on your way"); pollMine(); }
  }
}

(async function start() {
  drawPins(); renderMine(); updateReport();
  await RP.loadNetwork(map, L_.network);
  RP.connect({ staff: false, onSnapshot });
})();
