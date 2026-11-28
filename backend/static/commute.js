"use strict";
// My alerts: save your usual trip once, then get warned before you leave and while you drive, only if a problem is on YOUR route.
const { $, esc, icon, toast, api: rpApi, makeMap, makeSheet, pinIcon } = RP;
const state = { sub: null, home: null, work: null, vehicle: RP.store.get("rp_vehicle") || "two_wheeler", voice: false, depart: "", consent: false, results: [], field: null,
                alerts: [], since: 0, speak: false, watch: null, sim: null, speed: 4, route: null, center: [17.7287, 83.3086], busy: false };
try { const s = JSON.parse(RP.store.get("rp_sub") || "null"); if (s && s.id) state.sub = s; } catch (e) { /* ignore */ }
const M = makeMap(state.center), map = M.map, L_ = M.layers;
const sheet = makeSheet($("sheet"), $("grab"), () => map.invalidateSize());
$("btn-style").innerHTML = icon("layers", 22); $("btn-style").onclick = () => M.toggleStyle(); $("t-ic").innerHTML = icon("bell", 20);
const shown = new Set();

// the subscriber token travels in a header, never in the address
async function api(path, opts = {}) { const h = { ...(opts.headers || {}) }; if (state.sub) h["X-Subscriber-Token"] = state.sub.token; return rpApi(path, { ...opts, headers: h }); }
const VEH = [["two_wheeler", "Bike"], ["auto", "Auto"], ["car", "Car"], ["bus", "Bus"]];

// ------------------------------------------------------------------ sign-up
function placeBox(key, label, dot) {
  const p = state[key];
  return `<div class="stack"><label>${label}</label><div class="row" style="flex-wrap:nowrap"><span class="dot ${dot}">${dot === "a" ? "H" : "W"}</span>
    <input id="q-${key}" data-f="${key}" placeholder="${p ? esc(p.name) : "Search a road or place, or tap the map"}" value="${p && state.field !== key ? esc(p.name) : ""}" autocomplete="off"></div>
    ${state.field === key ? `<div class="stack" id="results">${resultsHtml()}</div>` : ""}</div>`;
}
const resultsHtml = () => (state.results.length ? state.results.map((p, k) => `<button class="result" data-pick="${k}"><span style="color:var(--brand)">${icon(p.kind === "road" ? "road" : "pin", 22)}</span><span>${esc(p.name)}<small>${p.kind === "road" ? "Road" : "Place"}</small></span></button>`).join("") : '<p class="muted small">No places match. Try a road name or tap the map.</p>');
function signupView() {
  return `<div class="stack"><div><h2>Get warned before you go</h2><p class="muted" style="margin:4px 0 0">Save your usual trip. We warn you only if a problem is on your route.</p></div>
    <div><label for="name">Your name</label><input id="name" maxlength="60" placeholder="e.g. Ravi" value="${esc(state.name || "")}" autocomplete="given-name"></div>
    <div><label>I travel by</label><div class="grid3" style="grid-template-columns:repeat(4,1fr)">${VEH.map(([v, n]) => `<button class="tile ${state.vehicle === v ? "on" : ""}" style="--c:var(--brand);padding:10px 4px" data-veh="${v}">${n}</button>`).join("")}</div></div>
    ${placeBox("home", "Home", "a")}${placeBox("work", "Work", "b")}
    <div><label for="depart">I usually leave at (optional)</label><input id="depart" type="time" value="${esc(state.depart)}"></div>
    <button class="tile ${state.voice ? "on" : ""}" style="--c:var(--brand);text-align:left;padding:12px 14px" data-act="voice">${icon("bell", 22)}Speak alerts aloud<small>Hands-free on the road. Works while this page is open.</small></button>
    <label style="display:flex;gap:10px;align-items:flex-start;font-weight:500;color:var(--ink)"><input id="consent" type="checkbox" style="width:22px;height:22px;margin-top:2px" ${state.consent ? "checked" : ""}> <span>I agree RoadPulse may use my location only to check whether a road problem is on my route. I can delete my data any time.</span></label>
    <div class="sheet-foot"><button class="btn-big" data-act="signup" ${state.busy ? "disabled" : ""}>Start getting alerts</button></div></div>`;
}
let tSearch;
async function search(q) { try { state.results = (await api(`/api/places?q=${encodeURIComponent(q)}&limit=6`)).places; } catch (e) { state.results = []; } const el = $("results"); if (el) el.innerHTML = resultsHtml(); }
function pins() {
  L_.pins.clearLayers();
  if (state.home) L.marker([state.home.lat, state.home.lon], { icon: RP.pinHtml("H", "#16a34a") }).addTo(L_.pins);
  if (state.work) L.marker([state.work.lat, state.work.lon], { icon: RP.pinHtml("W", "#2563eb") }).addTo(L_.pins);
  if (state.home && state.work) map.fitBounds(L.latLngBounds([[state.home.lat, state.home.lon], [state.work.lat, state.work.lon]]), { paddingTopLeft: [60, 90], paddingBottomRight: [60, innerHeight * 0.55], maxZoom: 17 });
}
function setPlace(key, p) { state[key] = { name: p.name, lat: p.lat, lon: p.lon }; state.field = null; state.results = []; pins(); render(); toast(`${key === "home" ? "Home" : "Work"}: ${p.name}`); }
map.on("click", (e) => { if (state.sub || !state.field) return; setPlace(state.field, { name: "A spot you tapped", lat: e.latlng.lat, lon: e.latlng.lng }); });

async function signup() {
  const name = ($("name").value || "").trim(); state.name = name; state.depart = $("depart").value; state.consent = $("consent").checked;
  if (!name) { toast("Enter your name."); return; }
  if (!state.consent) { toast("Tick the box to agree: alerts need your location."); return; }
  state.busy = true; render();
  try {
    const body = { name, vehicle: state.vehicle, voice: state.voice, consent_location: true, depart_hhmm: state.depart || null };
    if (state.home) { body.home_lat = state.home.lat; body.home_lon = state.home.lon; } if (state.work) { body.work_lat = state.work.lat; body.work_lon = state.work.lon; }
    const r = await api("/api/subscribers", { method: "POST", body: JSON.stringify(body) });
    state.sub = { id: r.id, token: r.token, name, vehicle: state.vehicle, voice: state.voice }; RP.store.set("rp_sub", JSON.stringify(state.sub)); RP.store.set("rp_vehicle", state.vehicle);
  } catch (e) { toast(e.message); }
  state.busy = false; render();
}

// ------------------------------------------------------------------ my alerts
const card = (a) => `<div class="card alert ${esc(a.kind)}"><div class="small muted" style="text-transform:uppercase;letter-spacing:.05em;font-weight:700">${esc(a.kind.replace("_", " "))} · ${new Date(a.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}${a.late ? " · late warning" : ""}${a.simulated ? " · demo" : ""}</div><div style="margin-top:4px;font-weight:600">${esc(a.text)}</div></div>`;
function liveView() {
  const s = state.sub, running = state.watch != null || state.sim != null;
  return `<div class="stack"><div class="hero ok"><span class="ic">${icon("check", 26)}</span><div><b>Alerts are on</b>${esc(s.name)} · ${esc(({ two_wheeler: "bike", auto: "auto", car: "car", bus: "bus" })[s.vehicle] || s.vehicle)}${state.speak ? " · voice on" : ""}</div></div>
    <div><label>I'm leaving in</label><div class="grid3" style="grid-template-columns:repeat(4,1fr)">${[5, 15, 30, 60].map((m) => `<button class="tile" style="--c:var(--brand);padding:10px 2px" data-leave="${m}">${m} min</button>`).join("")}</div></div>
    <div class="grid2"><button class="btn-sm ${running ? "ghost" : ""}" data-act="${running ? "stop" : "gps"}">${running ? "Stop" : "Start my trip (GPS)"}</button><button class="btn-sm ghost" data-act="sim" ${running ? "disabled" : ""}>Demo drive</button></div>
    <button class="tile ${state.speak ? "on" : ""}" style="--c:var(--brand);text-align:left;padding:10px 14px" data-act="speak">Spoken alerts: ${state.speak ? "on" : "tap to turn on"}<small>Tap once so your phone allows sound.</small></button>
    <p class="small muted" style="margin:0">A phone page only knows where you are while it is <b>open with the screen on</b>. Mount your phone, or use the Telegram bot for background updates.</p>
    <h3 style="margin-top:6px">My alerts ${state.alerts.length ? `<span class="chip">${state.alerts.length}</span>` : ""}</h3>
    ${state.alerts.length ? `<div class="stack">${[...state.alerts].reverse().map(card).join("")}</div>` : `<div class="empty">${icon("bell", 34)}<br>No alerts yet. You hear from us only when a problem is on your route.</div>`}
    <button class="btn-big ghost" data-act="forget">Delete my data</button></div>`;
}
function render() { const b = $("body"), keep = b.scrollTop; b.innerHTML = state.sub ? liveView() : signupView(); b.scrollTop = keep; }

function speak(t) { if (!state.speak || !("speechSynthesis" in window)) return; try { speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(t); u.lang = "en-IN"; u.rate = 0.95; speechSynthesis.speak(u); } catch (e) { /* unsupported */ } try { navigator.vibrate && navigator.vibrate([200, 100, 200]); } catch (e) { /* unsupported */ } }
function addAlert(a) {
  if (shown.has(a.id)) return; shown.add(a.id); state.alerts.push(a); state.since = Math.max(state.since, a.ts);
  if (a.route && a.route.length) { L_.trip.clearLayers(); const l = L.polyline(a.route, { color: "#16a34a", weight: 6 }).addTo(L_.trip); map.fitBounds(l.getBounds(), { paddingTopLeft: [50, 90], paddingBottomRight: [50, innerHeight * 0.5] }); }
  speak(a.voice); sheet.set(1); render();
}
async function poll() { if (!state.sub || document.hidden) return; try { (await api(`/api/subscribers/${state.sub.id}/alerts?since=${state.since}`)).alerts.forEach(addAlert); } catch (e) { if (/Unknown subscriber|wrong token/i.test(e.message)) forget(true); } }

// ------------------------------------------------------------------ trip: real GPS or a demo drive
const post = (lat, lon, kph) => api(`/api/subscribers/${state.sub.id}/position`, { method: "POST", body: JSON.stringify({ lat, lon, speed_kph: kph ?? null, simulated: state.sim != null }) })
  .then((r) => { L_.pins.addLayer(L.marker([lat, lon], { icon: pinIcon('<div class="me"></div>') })); if (r.alert) addAlert(r.alert); }).catch((e) => toast(e.message));
const startTrip = () => api(`/api/subscribers/${state.sub.id}/trip`, { method: "POST", body: JSON.stringify({ dest: "work" }) });
function stop() { if (state.watch != null) navigator.geolocation.clearWatch(state.watch); state.watch = null; if (state.sim) clearInterval(state.sim); state.sim = null; render(); }
async function gps() {
  if (!navigator.geolocation) { toast("This browser can't share location."); return; }
  try { await startTrip(); } catch (e) { toast(e.message.includes("No saved") ? "Save a work place first: tap Delete my data and set up again with Work." : e.message); return; }
  let last = 0; state.watch = navigator.geolocation.watchPosition((p) => { const t = Date.now(); if (t - last < 3000) return; last = t; L_.pins.clearLayers(); post(p.coords.latitude, p.coords.longitude, p.coords.speed != null ? p.coords.speed * 3.6 : null); },
    (err) => toast("Location error: " + err.message), { enableHighAccuracy: true, maximumAge: 2000 }); render();
}
async function demoDrive() {
  const home = state.home || (state.sub.home && { lat: state.sub.home[0], lon: state.sub.home[1] }); let sv = null;
  try { sv = await api(`/api/subscribers/${state.sub.id}`); } catch (e) { toast(e.message); return; }
  if (!sv.home || !sv.work) { toast("Demo drive needs a saved Home and Work."); return; }
  try {
    await startTrip();
    const r = await api("/api/routes", { method: "POST", body: JSON.stringify({ from_lat: sv.home[0], from_lon: sv.home[1], to_lat: sv.work[0], to_lon: sv.work[1], vehicle: state.sub.vehicle, alternatives: 1 }) });
    const pts = r.usual_route; if (!pts || pts.length < 2) throw new Error("No route between home and work.");
    L_.trip.clearLayers(); L.polyline(pts, { color: "#2563eb", weight: 4, dashArray: "6 6" }).addTo(L_.trip);
    const d = (a, b) => map.distance(a, b); let i = 0, into = 0; const step = 9 * state.speed; sheet.set(0);
    state.sim = setInterval(() => { let left = step; while (left > 0 && i < pts.length - 1) { const dd = d(pts[i], pts[i + 1]) - into; if (left < dd) { into += left; left = 0; } else { left -= dd; i++; into = 0; } }
      if (i >= pts.length - 1) { stop(); toast("Demo drive finished."); return; } const a = pts[i], b = pts[i + 1], f = into / Math.max(d(a, b), 1); L_.pins.clearLayers(); post(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, 32); }, 1000); render();
  } catch (e) { toast(e.message); }
}
async function forget(silent) {
  stop(); try { if (!silent && state.sub) await api(`/api/subscribers/${state.sub.id}`, { method: "DELETE" }); } catch (e) { /* already gone */ }
  RP.store.set("rp_sub", ""); state.sub = null; state.alerts = []; shown.clear(); state.since = 0; L_.pins.clearLayers(); L_.trip.clearLayers(); render(); if (!silent) toast("Your data was deleted.");
}

// ------------------------------------------------------------------ events
$("body").addEventListener("input", (e) => { const f = e.target.dataset && e.target.dataset.f; if (!f) return; state.field = f; clearTimeout(tSearch); tSearch = setTimeout(() => search(e.target.value), 150); });
$("body").addEventListener("focusin", (e) => { const f = e.target.dataset && e.target.dataset.f; if (f && state.field !== f) { state.name = $("name") ? $("name").value : state.name; state.depart = $("depart") ? $("depart").value : state.depart; state.field = f; state.results = []; render(); const el = $("q-" + f); if (el) el.focus(); search(""); sheet.set(2); } });
$("body").addEventListener("change", (e) => { if (e.target.id === "consent") state.consent = e.target.checked; if (e.target.id === "depart") state.depart = e.target.value; });
$("body").addEventListener("click", async (e) => {
  const a = e.target.closest("[data-act],[data-veh],[data-pick],[data-leave]"); if (!a) return;
  if (a.dataset.veh) { state.name = $("name").value; state.vehicle = a.dataset.veh; render(); return; }
  if (a.dataset.pick != null) { const p = state.results[Number(a.dataset.pick)]; if (p && state.field) setPlace(state.field, p); return; }
  if (a.dataset.leave) { try { await api(`/api/subscribers/${state.sub.id}/trip`, { method: "POST", body: JSON.stringify({ depart_in_min: Number(a.dataset.leave) }) }); toast(`Got it. We check your route before you leave in ${a.dataset.leave} minutes.`); } catch (err) { toast(err.message); } return; }
  const act = a.dataset.act;
  if (act === "voice") { state.name = $("name").value; state.voice = !state.voice; render(); }
  else if (act === "signup") signup();
  else if (act === "speak") { state.speak = !state.speak; if (state.speak) { speak("Spoken alerts are on."); } render(); }
  else if (act === "gps") gps(); else if (act === "sim") demoDrive(); else if (act === "stop") stop();
  else if (act === "forget" && !a.classList.contains("armed")) { a.classList.add("armed"); a.textContent = "Tap again to delete everything"; setTimeout(() => { if (a.isConnected) { a.classList.remove("armed"); a.textContent = "Delete my data"; } }, 4000); }
  else if (act === "forget") forget(false);
});
(async function start() {
  try { const h = await rpApi("/api/health"); if (h.center) { state.center = h.center; map.setView(h.center, 16); } } catch (e) { /* offline */ }
  if (state.sub) { state.speak = !!state.sub.voice; try { const sv = await api(`/api/subscribers/${state.sub.id}`); if (sv.home) { state.home = { name: "Home", lat: sv.home[0], lon: sv.home[1] }; } if (sv.work) { state.work = { name: "Work", lat: sv.work[0], lon: sv.work[1] }; } pins(); } catch (e) { forget(true); } }
  render(); poll(); setInterval(poll, 3000); document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
})();
