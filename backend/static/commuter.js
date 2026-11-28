"use strict";
// Commuter app: see what is wrong, check my trip, get a way round. One card at a time, almost no words.
const { $, esc, clock, icon, KIND, SEV, SEV_RANK, meter, published, headline, reason, vehicleNote, toast, api, makeMap, drawIncidents, makeSheet, pinHtml } = RP;
const loadMine = () => { try { return JSON.parse(RP.store.get("rp_mine") || "[]"); } catch (e) { return []; } };
const state = { mine: loadMine(), lastPoll: 0, incidents: [], sig: "", seen: null, view: "overview", selected: null, A: null, B: null, report: { type: "accident", pt: null, note: "" }, vehicle: RP.store.get("rp_vehicle") || "car",
                connected: false, lastOk: 0, fitted: false, center: [17.7287, 83.3086], trip: null, An: null, Bn: null, loaded: false, search: { field: "to", results: [] } };

const M = makeMap(state.center), map = M.map, L_ = M.layers;
const sheet = makeSheet($("sheet"), $("grab"), () => map.invalidateSize());
$("btn-style").innerHTML = icon("layers", 22); $("btn-locate").innerHTML = icon("locate", 22); $("btn-report").innerHTML = `${icon("plus", 20)} Report`;
$("pill-ic").innerHTML = icon("nav", 22);
$("btn-style").onclick = () => M.toggleStyle();

const visible = () => state.incidents.filter((i) => i.status !== "Cleared" && published(i));
const worst = () => visible().sort((a, b) => SEV_RANK[b.severity] - SEV_RANK[a.severity]);

// ------------------------------------------------------------------ the top pill
function renderPill() {
  const t = $("pill-t"), s = $("pill-s"), x = $("pill-x");
  x.classList.toggle("hidden", !state.A);
  if (!state.A) { t.textContent = "Where to?"; s.textContent = "Tap your start and your destination on the map"; }
  else if (!state.B) { t.textContent = "Start set"; s.textContent = "Now tap where you are going"; }
  else { t.textContent = state.An && state.Bn ? `${state.An} → ${state.Bn}` : state.trip ? "Your trip" : "Checking your trip"; s.textContent = `${{ two_wheeler: "Two-wheeler", auto: "Auto-rickshaw", car: "Car", bus: "Bus" }[state.vehicle]}`; }
}
$("pill").onclick = (e) => { if (e.target.id === "pill-x") { resetTrip(); return; } openSearch(); };

// ------------------------------------------------------------------ sheet content
function setView(v) { state.view = v; renderSheet(); }
function hero() {
  const list = visible();
  if (!list.length) return `<div class="hero ok"><span class="ic">${icon("check", 26)}</span><div><b>All clear</b>No road problems reported right now.</div></div>`;
  const by = {}; list.forEach((i) => { by[i.severity] = (by[i.severity] || 0) + 1; }); const blocked = by.Closed || 0;
  return `<div class="hero ${blocked ? "bad" : "warn"}"><span class="ic">${icon("warn", 26)}</span><div><b>${list.length} road problem${list.length > 1 ? "s" : ""}</b>${blocked ? `${blocked} blocked. ` : ""}Check your trip before you leave.
    <div class="row" style="margin-top:6px;gap:6px">${["Closed", "Severe", "Medium", "Low"].filter((k) => by[k]).map((k) => `<span class="chip c" style="--c:${SEV[k].color}">${by[k]} ${SEV[k].label.toLowerCase()}</span>`).join("")}</div></div></div>`;
}
function probCard(i) {
  const s = SEV[i.severity];
  return `<div class="prob ${state.selected === i.id ? "sel" : ""}" style="--c:${s.color}" data-open="${i.id}"><div class="row" style="flex-wrap:nowrap"><span class="ico">${icon(KIND[i.type].ic, 22)}</span>
    <div class="grow"><b>${esc(headline(i))}</b><div class="small">${meter(i.severity)} <span class="lvl">${s.label}</span>${i.simulated ? ' <span class="demo">demo</span>' : ""}</div></div><span class="muted">›</span></div></div>`;
}
function overview() {
  if (!state.loaded) return '<div class="stack"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>';
  const list = worst();
  return `<div class="stack"><div class="brandrow"><span class="mark" aria-hidden="true"></span><b>RoadPulse</b><span class="where">Dwaraka Nagar, Visakhapatnam</span></div>${hero()}${list.length ? `<div class="stack">${list.map(probCard).join("")}</div>` : ""}${myReports()}
    <div class="card row spread"><div class="row" style="flex-wrap:nowrap"><span style="color:var(--brand)">${icon("bell", 24)}</span><div><b>Get warned on your phone</b><div class="small muted">Only if a problem is on your route.</div></div></div><a class="btn-sm" href="/commute">Set up</a></div>
    <p class="small muted" style="text-align:center">Staff: <a class="link" href="/control">Control room</a> · <a class="link" href="/police">Traffic police</a> · <a class="link" href="/demo">All screens</a></p></div>`;
}
// ------------------------------------------------------------------ "Your reports": follow each report to its outcome
const OUTCOME = { pending: ["Waiting for a check", "Traffic police and the control room check every report before other drivers are warned."],
  published: ["Confirmed", "Checked and confirmed. Drivers whose route is affected are being warned."], rejected: ["Not confirmed", "It was checked and no problem was found, so nobody was warned."],
  resolved: ["Resolved", "The road is clear again."], gone: ["No longer tracked", "The server no longer has this report."] };
function myReports() {
  if (!state.mine.length) return "";
  return `<div class="card stack" data-my-reports><b>Your reports</b>${state.mine.map((r) => { const [t, why] = OUTCOME[r.outcome] || OUTCOME.gone;
    return `<div class="row" style="flex-wrap:nowrap;gap:10px" data-report-status="${esc(r.outcome)}"><span class="ico" style="--c:#0e3a72">${icon(KIND[r.type] ? KIND[r.type].ic : "warn", 20)}</span><div class="grow"><b>${esc((KIND[r.type] || KIND.slowdown).name)}</b> <span class="chip ${r.outcome === "pending" ? "warnc" : "done"}">${esc(t)}</span><div class="small muted">${esc(why)}</div></div></div>`; }).join("")}</div>`;
}
const saveMine = () => RP.store.set("rp_mine", JSON.stringify(state.mine.slice(0, 6)));
async function pollMine(force) {
  const open = state.mine.filter((r) => r.outcome === "pending" || r.outcome === "published");
  if (!open.length || (!force && Date.now() - state.lastPoll < 5000)) return;
  state.lastPoll = Date.now(); let changed = false;
  for (const r of open) {
    try { const s = await api(`/api/reports/${encodeURIComponent(r.id)}/status`); if (s.outcome !== r.outcome) { r.outcome = s.outcome; changed = true;
      if (s.outcome === "published") toast("Your report was confirmed. Drivers are being warned.");
      else if (s.outcome === "rejected") toast("Your report was checked: no problem was found."); } }
    catch (e) { if (/not found/i.test(e.message)) { r.outcome = "gone"; changed = true; } }
  }
  if (changed) { saveMine(); if (state.view === "overview") renderSheet(); }
}
function problemView() {
  const i = state.incidents.find((x) => x.id === state.selected); if (!i) { state.view = "overview"; return overview(); }
  const s = SEV[i.severity], since = i.status_times && i.status_times.Verified ? `since ${clock(i.status_times.Verified)}` : "";
  return `<div class="stack"><button class="link" data-act="back">‹ All problems</button>
    <div class="row" style="flex-wrap:nowrap;gap:14px"><span class="ico" style="--c:${s.color};width:54px;height:54px;border-radius:16px">${icon(KIND[i.type].ic, 28)}</span>
      <div><h2>${esc(headline(i))}</h2><div class="small" style="--c:${s.color}">${meter(i.severity)} <span class="lvl">${s.label}</span> <span class="muted">${since}</span> ${i.simulated ? '<span class="demo">demo</span>' : ""}</div></div></div>
    <p style="margin:0">${esc(reason(i))}${esc(vehicleNote(i))}</p>
    <div class="todo" style="--c:${s.color}"><small>What to do</small>${esc(s.advice)}</div>
    <button class="btn-big" data-act="detour">${icon("nav", 20)} See a way round</button>
    <button class="btn-big ghost" data-act="share">Share this warning</button></div>`;
}
function tripView() {
  const t = state.trip, veh = ["two_wheeler", "auto", "car", "bus"], lab = { two_wheeler: "Bike", auto: "Auto", car: "Car", bus: "Bus" };
  const picker = `<div class="grid3" style="grid-template-columns:repeat(4,1fr)">${veh.map((v) => `<button class="tile ${state.vehicle === v ? "on" : ""}" style="--c:var(--brand);padding:10px 4px" data-veh="${v}">${lab[v]}</button>`).join("")}</div>`;
  let body;
  if (!state.A) body = `<div class="empty">${icon("pin", 34)}<br><b>Tap your start on the map</b></div>`;
  else if (!state.B) body = `<div class="empty">${icon("pin", 34)}<br><b>Now tap where you are going</b></div>`;
  else if (!t) body = `<p class="muted"><span class="spin"></span>Checking your route…</p>`;
  else body = t.html;
  return `<div class="stack"><div class="row spread"><h2>Check my trip</h2><button class="link" data-act="reset">Start again</button></div>${picker}${body}
    ${t ? "" : '<button class="btn-big ghost" data-act="example">Try an example trip</button>'}</div>`;
}
function resultsHtml() {
  const r = state.search.results; if (!r.length) return '<p class="muted small">No places match. Try a road name.</p>';
  return r.map((p, k) => `<button class="result" data-place="${k}"><span style="color:var(--brand)">${icon(p.kind === "road" ? "road" : "pin", 22)}</span><span>${esc(p.name)}<small>${p.kind === "road" ? "Road" : "Place"}</small></span></button>`).join("");
}
function searchView() {
  return `<div class="stack"><div class="row spread"><h2>Plan a trip</h2><button class="link" data-act="back">Cancel</button></div>
    <div class="card stack" style="padding:10px"><div class="row" style="flex-wrap:nowrap"><span class="dot a">A</span><input id="q-from" data-f="from" placeholder="From: a place, or my location" value="${esc(state.An || "")}" autocomplete="off"></div>
      <div class="row" style="flex-wrap:nowrap"><span class="dot b">B</span><input id="q-to" data-f="to" placeholder="To: where are you going?" value="${esc(state.Bn || "")}" autocomplete="off"></div></div>
    <button class="btn-sm ghost" data-act="mylocation">${icon("locate", 18)} Use my location as the start</button>
    ${savedChips()}
    <div id="results" class="stack">${resultsHtml()}</div><p class="small muted" style="margin:0">Or tap the start and the destination on the map.</p></div>`;
}
const savedPlaces = () => { try { return JSON.parse(RP.store.get("rp_saved") || "{}"); } catch (e) { return {}; } };
function savedChips() {
  const sv = savedPlaces(), lab = { home: "Home", work: "Work" };
  return `<div class="row" style="gap:8px">${["home", "work"].map((k) => (sv[k] ? `<button class="btn-sm ghost" data-saved="${k}">${icon(k === "home" ? "pin" : "car", 16)} ${lab[k]}</button>`
    : `<button class="btn-sm ghost" data-save="${k}">+ Set ${lab[k].toLowerCase()}</button>`)).join("")}</div>${state.search.saving ? `<p class="small" style="margin:0;color:var(--brand);font-weight:700">Now search a place to save as ${lab[state.search.saving]}.</p>` : ""}`;
}
let tSearch;
async function searchPlaces(q) { try { state.search.results = (await api(`/api/places?q=${encodeURIComponent(q)}&limit=8`)).places; } catch (e) { state.search.results = []; } const el = $("results"); if (el) el.innerHTML = resultsHtml(); }
function openSearch() { setView("search"); sheet.set(2); searchPlaces(""); setTimeout(() => { const el = $(state.A ? "q-to" : "q-to"); if (el) el.focus(); }, 80); }
$("body").addEventListener("input", (e) => { const f = e.target.dataset && e.target.dataset.f; if (!f) return; state.search.field = f; clearTimeout(tSearch); tSearch = setTimeout(() => searchPlaces(e.target.value), 150); });
$("body").addEventListener("input", (e) => { if (e.target.id === "report-note") state.report.note = e.target.value; });
$("body").addEventListener("focusin", (e) => { const f = e.target.dataset && e.target.dataset.f; if (f) { state.search.field = f; sheet.set(2); } });
// What part of the map is hidden: the pill on top, and the sheet (phones) or the side card (desktop).
const covered = () => (sheet.phone() ? { l: 0, t: 90, r: 56, b: Math.round(innerHeight * 0.5) } : { l: 432, t: 0, r: 0, b: 0 });
function focusOn(ll, z) {   // centre a point in the visible part of the map, not behind the sheet
  const c = covered(), off = L.point((c.l - c.r) / 2, (c.t - c.b) / 2);
  map.flyTo(map.unproject(map.project(ll, z).subtract(off), z), z, { duration: 0.6 });
}
const fitPad = () => { const c = covered(); return { paddingTopLeft: [c.l + 40, c.t + 30], paddingBottomRight: [c.r + 40, c.b + 30] }; };
function fitTrip() { map.fitBounds(L.latLngBounds([state.A, state.B]), { ...fitPad(), maxZoom: 17 }); }
function pickPlace(k) { const p = state.search.results[k]; if (p) applyPlace(p); }
function applyPlace(p) {
  const ll = [p.lat, p.lon];
  if (state.search.saving) { const sv = savedPlaces(); sv[state.search.saving] = { name: p.name, lat: p.lat, lon: p.lon }; RP.store.set("rp_saved", JSON.stringify(sv)); toast(`Saved as ${state.search.saving === "home" ? "Home" : "Work"}.`); state.search.saving = null; }
  if (state.search.field === "from") { state.A = ll; state.An = p.name; } else { state.B = ll; state.Bn = p.name; }
  state.trip = null; L_.trip.clearLayers(); drawTripPins(); try { navigator.vibrate && navigator.vibrate(15); } catch (e) { /* unsupported */ }
  if (state.A && state.B) { fitTrip(); setView("trip"); runTrip(); return; }
  if (state.search.field === "to") { useMyLocation(true); return; }
  renderSheet(); setTimeout(() => { const el = $("q-to"); if (el) el.focus(); }, 60);
}
function useMyLocation(fromSearch) {
  if (!navigator.geolocation) { toast("This browser can't find your location. Search a start place or tap the map."); return; }
  navigator.geolocation.getCurrentPosition((p) => { state.A = [p.coords.latitude, p.coords.longitude]; state.An = "My location"; drawTripPins();
    if (state.B) { fitTrip(); setView("trip"); runTrip(); } else { map.flyTo(state.A, 17); renderSheet(); } },
    () => { toast("Couldn't get your location. Search a start place or tap the map."); if (fromSearch) { renderSheet(); setTimeout(() => { const el = $("q-from"); if (el) el.focus(); }, 60); } }, { enableHighAccuracy: true, timeout: 8000 });
}
function reportView() {
  const types = [["accident", "Accident", "warn"], ["flood", "Water", "drop"], ["blockage", "Blocked", "stop"], ["construction", "Works", "cone"]];
  return `<div class="stack"><div class="row spread"><h2>Report a problem</h2><button class="link" data-act="back">Cancel</button></div>
    <div class="grid2">${types.map(([k, n, ic]) => `<button class="tile ${state.report.type === k ? "on" : ""}" style="--c:var(--brand)" data-rtype="${k}">${icon(ic, 26)}${n}</button>`).join("")}</div>
    <div class="card row" style="flex-wrap:nowrap"><span style="color:var(--brand)">${icon("pin", 22)}</span><b>${state.report.pt ? "Spot chosen on the map" : "Tap the spot on the map"}</b></div>
    <input id="report-note" maxlength="200" placeholder="What did you see? (optional)" aria-label="Describe what you saw" value="${esc(state.report.note || "")}" autocomplete="off">
    <button class="btn-big" id="send-report" ${state.report.pt ? "" : "disabled"}>Send report</button>
    <p class="small muted" style="margin:0">Traffic police check every report before other drivers are warned.</p></div>`;
}
function renderSheet() {
  const v = state.view, body = $("body");
  body.innerHTML = v === "problem" ? problemView() : v === "trip" ? tripView() : v === "report" ? reportView() : v === "search" ? searchView() : overview();
  $("btn-report").classList.toggle("hidden", v !== "overview");
  document.querySelector(".topbar").classList.toggle("hidden", v === "search" && sheet.phone());   // the full-height search covers the pill
}

// ------------------------------------------------------------------ actions
$("body").addEventListener("click", async (e) => {
  const a = e.target.closest("[data-act],[data-open],[data-veh],[data-rtype],[data-place],[data-saved],[data-save],#send-report"); if (!a) return;
  if (a.dataset.saved) { const p = savedPlaces()[a.dataset.saved]; if (p) applyPlace({ ...p, kind: "place" }); return; }
  if (a.dataset.save) { state.search.saving = a.dataset.save; renderSheet(); setTimeout(() => { const el = $("q-to"); if (el) el.focus(); }, 60); return; }
  if (a.dataset.place != null) { pickPlace(Number(a.dataset.place)); return; }
  if (a.dataset.open) { select(a.dataset.open); return; }
  if (a.dataset.veh) { state.vehicle = a.dataset.veh; RP.store.set("rp_vehicle", state.vehicle); renderPill(); if (state.A && state.B) runTrip(); else renderSheet(); return; }
  if (a.dataset.rtype) { state.report.type = a.dataset.rtype; renderSheet(); return; }
  if (a.id === "send-report") { sendReport(); return; }
  const act = a.dataset.act;
  if (act === "back") { state.selected = null; L_.pins.clearLayers(); state.report.pt = null; setView("overview"); drawMap(); }
  else if (act === "detour") exampleTrip(state.incidents.find((x) => x.id === state.selected));
  else if (act === "reset") resetTrip();
  else if (act === "example") exampleTrip(worst()[0]);
  else if (act === "mylocation") useMyLocation(false);
  else if (act === "share") shareProblem(state.incidents.find((x) => x.id === state.selected));
});
async function shareProblem(i) {
  if (!i) return; const text = `${headline(i)}. ${SEV[i.severity].advice}`;
  try { if (navigator.share) await navigator.share({ title: "RoadPulse road warning", text, url: location.origin }); else { await navigator.clipboard.writeText(`${text} ${location.origin}`); toast("Copied. Paste it in a message."); } } catch (e) { /* cancelled */ }
}
function select(id) {
  state.selected = id; const i = state.incidents.find((x) => x.id === id); if (!i) return;
  setView("problem"); drawMap(); sheet.set(1); focusOn([i.lat, i.lon], 18);
}
function drawTripPins() {
  L_.pins.clearLayers();
  if (state.A) L.marker(state.A, { icon: pinHtml("A", "#16a34a") }).addTo(L_.pins);
  if (state.B) L.marker(state.B, { icon: pinHtml("B", "#dc2626") }).addTo(L_.pins);
  renderPill();
}
function resetTrip() { state.A = state.B = state.trip = state.An = state.Bn = null; L_.trip.clearLayers(); drawTripPins(); setView("overview"); }
function exampleTrip(i) {
  state.A = state.B = state.trip = state.An = state.Bn = null; L_.trip.clearLayers();
  if (i && i.affected_segments && i.affected_segments.length) {
    // The two ends of the affected road, the pair furthest apart: they straddle the incident, so the usual route is the
    // blocked road itself and the card can show the way round. (Points a fixed distance away could land on other streets.)
    const pts = i.affected_segments.flat().map((p) => [p[0], p[1]]), kx = 111320 * Math.cos((i.lat * Math.PI) / 180), ky = 111320;
    let best = [pts[0], pts[pts.length - 1]], bd = -1;
    for (const a of pts) for (const b of pts) { const d = ((a[1] - b[1]) * kx) ** 2 + ((a[0] - b[0]) * ky) ** 2; if (d > bd) { bd = d; best = [a, b]; } }
    state.A = best[0]; state.B = best[1];
  } else { state.A = state.center; state.B = [17.7238639, 83.3068879]; }
  drawTripPins(); setView("trip"); sheet.set(1); runTrip();
}
// quiet = a refresh of a trip already on screen (an incident appeared or changed): keep the old card until the new one is ready.
async function runTrip(quiet) {
  const seq = (runTrip.seq = (runTrip.seq || 0) + 1), before = quiet && state.trip ? state.trip.html : null;
  if (!quiet) { state.trip = null; renderSheet(); L_.trip.clearLayers(); }
  let html;
  try { const r = await api("/api/routes", { method: "POST", body: JSON.stringify({ from_lat: state.A[0], from_lon: state.A[1], to_lat: state.B[0], to_lon: state.B[1], vehicle: state.vehicle, alternatives: 2 }) });
    if (seq !== runTrip.seq) return; L_.trip.clearLayers(); html = tripCard(r); }
  catch (e) { if (seq !== runTrip.seq) return; L_.trip.clearLayers(); html = `<div class="hero bad"><span class="ic">${icon("warn", 24)}</span><div><b>Could not check this trip</b>${esc(e.message)}</div></div>`; }
  state.trip = { html }; renderPill(); renderSheet();
  if (!quiet) { sheet.set(1); const b = L_.trip.getLayers().filter((l) => l.getBounds).reduce((acc, l) => acc.extend(l.getBounds()), L.latLngBounds([state.A, state.B]));
    setTimeout(() => map.fitBounds(b, { ...fitPad(), maxZoom: 18 }), 280); }
  if (before && before !== html) toast("Your trip was updated: road conditions changed.");
}
function tripCard(r) {
  const best = r.routes && r.routes[0], mins = (s) => Math.max(1, Math.round(s / 60));
  const card = (tone, ic, title, body, extra = "") => `<div class="hero ${tone}"><span class="ic">${icon(ic, 26)}</span><div><b>${esc(title)}</b>${body}${extra}</div></div>`;
  if (r.no_route || !best) return card("bad", "stop", "No way to get there", "There is no road route between these points for your vehicle.");
  const line = (c, col, dash) => RP.drawRoute(L_.trip, c, col, dash);
  const hits = r.usual_route_hits || [], km = (best.length_m / 1000).toFixed(1);
  if (!hits.length) { line(best.coords, "#0e3a72"); return card("ok", "check", "Your way is clear", `About ${mins(best.time_s)} min, ${km} km.`); }
  const w = hits.reduce((a, b) => (SEV_RANK[b.severity] > SEV_RANK[a.severity] ? b : a)), s = SEV[w.severity], what = `${(KIND[w.type] || KIND.slowdown).name} on ${w.road || "your road"}`;
  const extra = Math.max(0, mins(best.time_s) - mins(r.usual_time_s || best.time_s)), more = extra < 1 ? "about the same time" : `${extra} min longer`;
  if (JSON.stringify(best.coords) === JSON.stringify(r.usual_route)) { line(best.coords, s.color); return card("warn", "warn", "A problem on your way", `${esc(what)} is ${s.label.toLowerCase()}. No faster way round, so expect delays.`); }
  line(r.usual_route, "#dc2626", "8 8"); line(best.coords, "#16a34a");
  const todo = { Closed: `You can't go that way. Take the green route: ${more}.`, Severe: `Take the green route if you can: ${more}.`, Medium: `Green route only if you are in a hurry: ${more}.`, Low: `You can carry on, or take the green route (${more}).` }[w.severity];
  const usual = r.usual_time_s || best.time_s, blocked = w.severity === "Closed";
  const cmp = `<div class="cmp" role="list">
    <div class="opt best" role="listitem"><span class="sw"></span><div class="grow"><b>Way round</b><small>${(best.length_m / 1000).toFixed(1)} km, no problems reported</small></div><span class="t">${mins(best.time_s)}<small>min</small></span></div>
    <div class="opt usual" role="listitem"><span class="sw"></span><div class="grow"><b>Your usual way</b><small>${esc(what)}</small></div><span class="t">${blocked ? "&times;" : `${mins(usual)}<small>min if clear</small>`}</span></div></div>`;
  return card(w.severity === "Closed" ? "bad" : w.severity === "Low" ? "ok" : "warn", w.severity === "Closed" ? "stop" : "warn", w.severity === "Closed" ? "Your usual way is blocked" : "A problem on your usual way", `${esc(what)} is ${s.label.toLowerCase()}. ${esc(todo)}`) + cmp;
}
async function sendReport() {
  const b = $("send-report"); b.disabled = true;
  let uid = RP.store.get("rp_uid"); if (!uid) { uid = Math.random().toString(36).slice(2, 10); RP.store.set("rp_uid", uid); }
  try { const note = ($("report-note") ? $("report-note").value : state.report.note || "").trim();
    const r = await api("/api/reports", { method: "POST", body: JSON.stringify({ type: state.report.type, lat: state.report.pt[0], lon: state.report.pt[1], reporter_id: "web-" + uid, note: note || null }) });
    state.mine.unshift({ id: r.incident_id, type: state.report.type, at: Date.now(), outcome: r.status === "Reported" ? "pending" : "published" }); state.mine = state.mine.slice(0, 6); saveMine();
    toast(r.status === "Reported" ? "Thank you. Traffic police will check it before drivers are warned." : "Thank you. This matches a confirmed problem."); state.report.pt = null; state.report.note = ""; L_.pins.clearLayers(); setView("overview"); }
  catch (e) { toast(e.message); b.disabled = false; }
}
$("btn-report").onclick = () => { setView("report"); sheet.set(0); toast("Tap the spot on the map."); };
$("btn-locate").onclick = () => {
  if (!navigator.geolocation) { toast("This browser can't find your location."); return; }
  navigator.geolocation.getCurrentPosition((p) => { const ll = [p.coords.latitude, p.coords.longitude]; map.flyTo(ll, 17); L_.trip.addLayer(L.marker(ll, { icon: RP.pinIcon('<div class="me"></div>') }));
    if (!state.A) { state.A = ll; state.An = "My location"; drawTripPins(); setView("trip"); } }, () => toast("Couldn't get your location. Tap the map instead."), { enableHighAccuracy: true, timeout: 8000 });
};
map.on("click", (e) => {
  const ll = [e.latlng.lat, e.latlng.lng];
  if (state.view === "report") { state.report.pt = ll; L_.pins.clearLayers(); L.marker(ll, { icon: pinHtml("!", "#0e3a72") }).addTo(L_.pins); renderSheet(); sheet.set(1); return; }
  if (!state.A || (state.A && state.B)) { state.A = ll; state.An = state.Bn = null; state.B = state.trip = null; L_.trip.clearLayers(); drawTripPins(); setView("trip"); }
  else { state.B = ll; state.Bn = null; drawTripPins(); setView("trip"); runTrip(); }
});

// ------------------------------------------------------------------ live data
function drawMap() { drawIncidents(L_.inc, visible(), { selected: state.selected, onClick: select }); }
async function refresh(force) {
  try {
    const r = await api("/api/incidents?scope=public"); state.lastOk = Date.now(); state.connected = true; $("offline").classList.add("hidden");
    if (!state.loaded) { state.loaded = true; force = true; }
    const sig = JSON.stringify(r.incidents.map((i) => [i.id, i.status, i.severity, i.updated]));
    if (force || sig !== state.sig) {
      const ids = new Set(r.incidents.map((i) => i.id));
      if (state.seen) r.incidents.filter((i) => published(i) && !state.seen.has(i.id)).forEach((i) => { toast(`New road problem: ${headline(i)}. ${SEV[i.severity].label}.`); try { navigator.vibrate && navigator.vibrate([120, 60, 120]); } catch (e) { /* unsupported */ } });
      state.seen = ids; state.incidents = r.incidents; state.sig = sig; drawMap(); if (state.view === "overview" || state.view === "problem") renderSheet();
      if (!force && state.A && state.B && state.view === "trip") runTrip(true);   // the road situation changed: check the trip again, no tap needed
      document.title = `${visible().length ? `(${visible().length}) ` : ""}RoadPulse — know before you go`;
      if (!state.fitted && visible().length) { state.fitted = true; const pts = visible().map((i) => [i.lat, i.lon]);
        map.fitBounds(L.latLngBounds(pts), { ...fitPad(), maxZoom: 17 }); }
    }
  } catch (e) { state.connected = false; if (state.loaded) $("offline").classList.remove("hidden"); }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true); });   // save battery and data in the background
(async function start() {
  try { const h = await api("/api/health"); if (h.center) { state.center = h.center; map.setView(h.center, 16); } } catch (e) { /* offline */ }
  renderPill(); renderSheet(); await refresh(true); pollMine(true); setInterval(() => { if (!document.hidden) { refresh(); pollMine(); } }, 2500);
})();
