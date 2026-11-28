"use strict";
// Control room: reports come in, you send the nearest police unit, police confirm on the scene, you confirm and drivers are warned.
const { $, esc, clock, icon, KIND, SEV, SEV_RANK, meter, published, headline, reason, vehicleNote, evidence, toast, api, makeMap, drawIncidents, makeSheet } = RP;
RP.auth.key = "rp_ctrl_token"; RP.auth.token = RP.store.sget(RP.auth.key); RP.auth.onExpire = () => { renderAll(); };
const state = { incidents: [], detail: {}, units: {}, unitList: [], sig: "", selected: null, seen: null, fitted: false, center: [17.7287, 83.3086], msg: "", sound: RP.store.get("rp_ctrl_sound") === "1" };
const M = makeMap(state.center), map = M.map;
const sheet = makeSheet($("sheet"), $("grab"), () => map.invalidateSize());
$("btn-style").innerHTML = icon("layers", 22); $("btn-style").onclick = () => M.toggleStyle();
const unitLayer = L.layerGroup().addTo(map);

// ---- sound alert for new reports (off by default; the tap that turns it on also unlocks audio on phones)
function beep() {
  try { const ac = beep.ac || (beep.ac = new (window.AudioContext || window.webkitAudioContext)());
    [0, 0.22].forEach((t) => { const o = ac.createOscillator(), g = ac.createGain(); o.frequency.value = 880; g.gain.value = 0.18; o.connect(g); g.connect(ac.destination); o.start(ac.currentTime + t); o.stop(ac.currentTime + t + 0.16); }); } catch (e) { /* no audio */ }
  try { navigator.vibrate && navigator.vibrate([200, 100, 200]); } catch (e) { /* unsupported */ }
}
function drawSound() { const b = $("btn-sound"); b.innerHTML = icon("bell", 22); b.style.color = state.sound ? "var(--brand)" : "var(--muted)"; b.setAttribute("aria-pressed", String(state.sound)); b.title = state.sound ? "Sound alert is on" : "Sound alert is off"; }
$("btn-sound").onclick = () => { state.sound = !state.sound; RP.store.set("rp_ctrl_sound", state.sound ? "1" : "0"); drawSound(); if (state.sound) beep(); toast(state.sound ? "Sound alert on: you will hear new reports." : "Sound alert off."); };
drawSound();

// ---- police units on the map, with a line to the incident each one is sent to
function drawUnits() {
  unitLayer.clearLayers();
  for (const u of state.unitList) {
    if (u.lat == null) continue; const stale = u.age_s > 120;
    L.marker([u.lat, u.lon], { icon: RP.pinIcon(`<div class="pin" style="--c:#2563eb;opacity:${stale ? 0.55 : 1}"><div class="dot2" style="width:36px;height:36px">${icon("shield", 18)}</div><div class="tag" style="display:block">${esc(u.unit)}${stale ? " · " + Math.max(1, Math.round(u.age_s / 60)) + " min ago" : ""}</div></div>`) }).addTo(unitLayer);
    for (const id of u.incidents) { const i = state.incidents.find((x) => x.id === id); if (i) L.polyline([[u.lat, u.lon], [i.lat, i.lon]], { color: "#2563eb", weight: 4, dashArray: "6 8" }).addTo(unitLayer); }
  }
}
async function loadUnitsMap() { try { state.unitList = (await api("/api/units")).units; } catch (e) { /* keep the last positions */ } drawUnits(); }

// ---- "what happened", in plain words, oldest first
const SAY = { assigned: "sent to check", accepted: "accepted", declined: "can't go", on_way: "on the way", arrived: "arrived" };
function timeline(i) {
  const d = state.detail[i.id] || {}, ev = [], U = { ambulance: "Ambulance", tow: "Tow vehicle", police: "Police" };
  const st = d.status_times || i.status_times || {};
  if (st.Reported) ev.push([st.Reported, "Report received"]);
  for (const a of d.assignments || []) for (const [k, t] of a.history) if (SAY[k]) ev.push([t, `${a.unit} ${SAY[k]}`]);
  for (const r of d.field_reports || []) ev.push([r.at, `${r.unit} sees: ${{ blocked: "road blocked", one_lane: "one lane blocked", not_real: "nothing here", open: "road is open" }[r.finding]}${r.need.length ? ", needs " + r.need.join(" and ") : ""}`]);
  for (const x of d.dispatched || []) ev.push([x.at, `${U[x.unit] || x.unit} sent`]);
  if (st.Verified) ev.push([st.Verified, i.urgent ? "You confirmed it as urgent (no police check)" : "You confirmed it: drivers warned"]);
  if (st.Responding) ev.push([st.Responding, "Diversion approved"]);
  ev.sort((a, b) => a[0] - b[0]);
  return ev.length ? `<div><b class="small muted" style="text-transform:uppercase;letter-spacing:.06em">What happened</b><div class="stack" style="margin-top:6px;gap:0">${ev.map(([t, w]) => `<div class="row" style="flex-wrap:nowrap;gap:10px;padding:5px 0;border-bottom:1px solid var(--line)"><span class="small muted" style="min-width:48px">${clock(t)}</span><span>${esc(w)}</span></div>`).join("")}</div></div>` : "";
}
const signedIn = () => !!RP.auth.token;
const open = () => state.incidents.filter((i) => i.status !== "Cleared");
const pending = () => open().filter((i) => i.status === "Reported");
const active = () => open().filter((i) => i.status !== "Reported");

// ------------------------------------------------------------------ plain words for the police step
const FIELD = { none: "No police sent yet", assigned: "Waiting for police to accept", accepted: "Police accepted", on_way: "Police on the way", arrived: "Police on the scene",
  scene_safe: "Police on the scene", confirmed: "Police confirmed it", not_real: "Police found nothing", open: "Police say the road is open" };
const fieldLine = (i) => FIELD[i.field_state] || "";
function stepper(i) {
  const fs = i.field_state, sent = !["none"].includes(fs), arrived = ["arrived", "scene_safe", "confirmed", "not_real", "open"].includes(fs), done = ["confirmed", "not_real"].includes(fs);
  const st = [true, sent, arrived, done && fs === "confirmed", false];
  const labels = ["Reported", "Police sent", "On the scene", "Police confirm", "You confirm"];
  return `<div class="steps" style="margin:4px 0 2px">${labels.map((l, k) => `${k ? `<span class="l ${st[k] ? "done" : ""}"></span>` : ""}<span class="d ${st[k] ? "done" : (k === 0 || st[k - 1]) ? "on" : ""}" title="${l}">${st[k] ? "✓" : k + 1}</span>`).join("")}</div>
    <div class="row small muted" style="justify-content:space-between"><span>Reported</span><span>Police sent</span><span>On scene</span><span>Confirmed</span><span>You</span></div>`;
}

// ------------------------------------------------------------------ sign in
function signInView() {
  return `<div class="stack"><div class="row" style="gap:12px"><span class="ico" style="--c:var(--brand)">${icon("shield", 24)}</span><div><h2>Control room</h2><div class="muted small">Sign in to see reports and warn drivers.</div></div></div>
    <label for="user">Name</label><input id="user" value="officer" autocomplete="username"><label for="pass">Password</label><input id="pass" type="password" autocomplete="current-password">
    <button class="btn-big" id="btn-login">Sign in</button><p id="login-msg" class="small" style="color:var(--bad);min-height:1.2em;margin:0">${esc(state.msg)}</p></div>`;
}
async function signIn() {
  const b = $("btn-login"); b.disabled = true; state.msg = "";
  try {
    const r = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: $("user").value.trim(), password: $("pass").value }) });
    if (r.role === "police") { state.msg = "That is a police login. Open the traffic police app instead."; renderAll(); return; }
    RP.auth.token = r.token; RP.store.sset(RP.auth.key, r.token); await refresh(true);
  } catch (e) { state.msg = /wrong username/i.test(e.message) ? "That name or password is not right." : e.message; renderAll(); }
}
document.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.id === "pass") signIn(); });

// ------------------------------------------------------------------ queue
function queueCard(i) {
  const s = SEV[i.severity], isPend = i.status === "Reported";
  const sub = isPend ? `${fieldLine(i)}` : `${s.label} · ${i.people_warned ? i.people_warned + " drivers warned" : "no one on this route yet"}`;
  const want = (state.detail[i.id] && state.detail[i.id].requests || []).map((r) => r.kind);
  return `<div class="prob ${isPend ? "pending" : ""} ${state.selected === i.id ? "sel" : ""}" style="--c:${s.color}" data-open="${i.id}"><div class="row" style="flex-wrap:nowrap">
    <span class="ico">${icon(KIND[i.type].ic, 22)}</span><div class="grow"><b>${esc(headline(i))}</b><div class="small muted">${esc(sub)}</div>
    ${want.length ? `<span class="chip warnc">Police need: ${want.join(", ")}</span>` : ""}${i.field_state === "open" && !isPend ? '<span class="chip done">Police say road is open</span>' : ""}</div><span class="muted">›</span></div></div>`;
}
function queueView() {
  const p = pending(), a = active(), none = (t) => `<div class="empty">${icon("check", 34)}<br>${t}</div>`;
  return `<div class="stack"><div class="row spread"><h2>Queue</h2><button class="link" data-act="signout">Sign out</button></div>
    <h3 class="muted small" style="text-transform:uppercase;letter-spacing:.06em">Needs your decision</h3>${p.length ? `<div class="stack">${p.map(queueCard).join("")}</div>` : none("Nothing is waiting for you.")}
    <h3 class="muted small" style="text-transform:uppercase;letter-spacing:.06em">Happening now</h3>${a.length ? `<div class="stack">${a.map(queueCard).join("")}</div>` : none("No active road problems.")}
    <p class="small muted">More tools: <a class="link" href="/classic">detailed view</a></p></div>`;
}

// ------------------------------------------------------------------ one incident
const tile = (k, on, act, id, label, note) => `<button class="tile ${on ? "on" : ""}" style="--c:${SEV[k].color}" data-act="${act}" data-id="${id}" data-sev="${k}">${meter(k)}<div>${label || SEV[k].label}</div><small>${note || SEV[k].note}</small></button>`;
function unitRows(i) {
  const u = state.units[i.id]; if (!u) return `<p class="muted small"><span class="spin"></span>Finding police units…</p>`;
  if (!u.length) return `<p class="muted small">No police units are signed in.</p>`;
  return u.map((x) => `<div class="unit"><span style="color:var(--brand)">${icon("shield", 22)}</span><div class="grow"><b>${esc(x.unit)}</b><div class="small muted">${x.metres != null ? `${x.eta_min} min away · ${x.metres} m` : "location not shared"}${x.calls ? ` · ${x.calls} call${x.calls > 1 ? "s" : ""}` : ""}</div></div>
    <button class="btn-sm" data-act="assign" data-id="${i.id}" data-unit="${esc(x.unit)}">Send</button></div>`).join("");
}
// The evidence behind an incident in plain words, and how sure the system is. Simulated evidence is labelled as such.
function why(i) {
  const ev = evidence(i), pct = Math.round((i.confidence || 0) * 100);
  return `<div data-evidence><b>Why we think so</b><ul style="margin:6px 0 0 18px;padding:0">${ev.map((e) => `<li>${esc(e)}</li>`).join("") || "<li>No evidence recorded yet.</li>"}</ul>
    <div class="small muted" data-confidence style="margin-top:4px">How sure the system is: ${pct}%${i.simulated ? ' · <span class="demo">demo data</span>' : ""}</div></div>`;
}
function pendingDetail(i) {
  const d = state.detail[i.id] || {}, fs = i.field_state, s = SEV[i.field_severity || i.severity], ev = evidence(i), reps = d.field_reports || [];
  const last = reps[reps.length - 1];
  let police;
  if (fs === "none") police = `<div class="stack"><b>Send the nearest police unit to check</b>${unitRows(i)}</div>`;
  else if (fs === "confirmed") police = `<div class="hero ok"><span class="ic">${icon("shield", 24)}</span><div><b>Police confirmed: ${last && last.finding === "one_lane" ? "one lane blocked" : "road blocked"}</b>${last && last.note ? esc(last.note) : "They are on the scene."}</div></div>`;
  else if (fs === "not_real") police = `<div class="hero warn"><span class="ic">${icon("shield", 24)}</span><div><b>Police found nothing</b>${last && last.note ? esc(last.note) : "No problem seen at this spot."}</div></div>`;
  else police = `<div class="hero warn"><span class="ic">${icon("shield", 24)}</span><div><b>${FIELD[fs]}</b>${(d.assignments || []).filter((a) => !["declined"].includes(a.status)).map((a) => esc(a.unit)).join(", ")}</div></div>`;
  const decide = i.field_state === "confirmed" || !i.needs_field
    ? `<div class="stack"><button class="btn-big" data-act="confirm" data-id="${i.id}" data-sev="${i.field_severity || ""}">Confirm: ${SEV[i.field_severity || "Closed"].label} and warn drivers</button>
       <details><summary class="small muted" style="cursor:pointer">Choose a different level</summary><div class="grid2" style="margin-top:8px">${["Closed", "Severe", "Medium", "Low"].map((k) => tile(k, false, "confirm", i.id)).join("")}</div></details></div>`
    : `<div class="card"><b>Waiting for police</b><div class="small muted">Drivers are warned after police confirm and you confirm.</div>
       <details style="margin-top:8px"><summary class="small" style="cursor:pointer;color:var(--bad);font-weight:700">Emergency? Warn drivers now</summary><div class="grid2" style="margin-top:8px">${["Closed", "Severe", "Medium", "Low"].map((k) => tile(k, false, "urgent", i.id)).join("")}</div>
       <p class="small muted">Recorded as urgent without a police check.</p></details></div>`;
  return `<div class="stack"><div class="row" style="flex-wrap:nowrap;gap:12px"><span class="ico" style="--c:${SEV[i.severity].color};width:50px;height:50px">${icon(KIND[i.type].ic, 26)}</span><div><h2>${esc(headline(i))}</h2><span class="chip warnc">Not shown to drivers yet</span>${i.simulated ? ' <span class="demo">demo</span>' : ""}</div></div>
    ${stepper(i)}${why(i)}${police}${decide}
    ${timeline(i)}<button class="btn-big ghost" data-act="reject" data-id="${i.id}">Not real: dismiss</button></div>`;
}
function activeDetail(i) {
  const d = state.detail[i.id] || {}, s = SEV[i.severity], sent = {}; (d.dispatched || []).forEach((x) => { sent[x.unit] = x.at; });
  const reqs = d.requests || [], changeable = !["flood", "construction"].includes(i.type);
  const U = { police: "Police", ambulance: "Ambulance", tow: "Tow vehicle" };
  const helpRow = ["ambulance", "tow"].map((u) => sent[u] ? `<span class="chip done">${U[u]} sent ${clock(sent[u])}</span>` : `<button class="btn-sm ghost" data-act="dispatch" data-id="${i.id}" data-unit="${u}">Send ${U[u].toLowerCase()}</button>`).join("");
  return `<div class="stack"><div class="row" style="flex-wrap:nowrap;gap:12px"><span class="ico" style="--c:${s.color};width:50px;height:50px">${icon(KIND[i.type].ic, 26)}</span><div><h2>${esc(headline(i))}</h2><div class="small" style="--c:${s.color}">${meter(i.severity)} <span class="lvl">${s.label}</span> · <b>${i.people_warned || 0}</b> drivers warned ${i.urgent ? '<span class="chip warnc">urgent</span>' : ""}</div></div></div>
    <div class="todo" style="--c:${s.color}"><small>Drivers are told</small>${esc(s.advice)}</div>
    ${why(i)}
    ${i.status === "Verified" ? `<button class="btn-big" data-act="respond" data-id="${i.id}">Approve diversion: send drivers the way round</button>` : ""}
    ${i.status === "Responding" ? '<div class="hero ok"><span class="ic">' + icon("nav", 24) + '</span><div><b>Diversion approved</b>Drivers on this road are being sent the way round.</div></div>' : ""}
    ${recovery(i)}
    ${i.field_state === "open" ? `<div class="hero ok"><span class="ic">${icon("road", 24)}</span><div><b>Police say the road is open</b>You can clear it now.</div></div><button class="btn-big good" data-act="clear" data-id="${i.id}">Clear road: tell drivers it's open</button>` : ""}
    ${reqs.length ? `<div class="hero warn"><span class="ic">${icon("shield", 24)}</span><div><b>Police need: ${reqs.map((r) => r.kind).join(" and ")}</b>Tap below to send.</div></div>` : ""}
    <div><b class="small muted">HELP</b><div class="row" style="margin-top:6px">${helpRow}</div></div>
    ${changeable ? `<div><b class="small muted">CHANGE LEVEL</b><div class="grid2" style="margin-top:6px">${["Closed", "Severe", "Medium", "Low"].filter((k) => k !== i.severity).map((k) => tile(k, false, "level", i.id)).join("")}</div></div>` : ""}
    ${i.field_state !== "open" ? `<button class="btn-big ghost" data-act="clear" data-id="${i.id}">The road is clear again</button>` : ""}
    ${alertHistory(d)}${timeline(i)}</div>`;
}
// Recovery monitoring: the engine only starts the auto-clear countdown when the evidence says the road is back to normal.
function recovery(i) {
  if (i.clearing_in_s == null || i.status === "Cleared") return "";
  const water = i.type === "flood" && i.depth_cm != null ? ` Water is down to about ${Math.round(i.depth_cm)} cm.` : "";
  const mins = Math.max(1, Math.ceil(i.clearing_in_s / 60));
  return `<div class="hero ok" data-recovery><span class="ic">${icon("check", 24)}</span><div><b>Looks like it is recovering</b>Readings are back to normal.${water} It clears by itself in about ${mins} min if they stay normal, or you can clear it now.</div></div>`;
}
// Notification history: anonymous on purpose (no names, no places): when, which kind of message, and whether Telegram carried it.
const ALERT_LABEL = { pre_trip: "Warned before the trip", in_trip: "Warned on the road", uturn: "Told to turn back", no_alternative: "Warned: no way round", clear: "All-clear sent", route_change: "Route changed" };
function alertHistory(d) {
  const h = d.alert_history || []; if (!h.length) return `<div data-alerts><b class="small muted" style="text-transform:uppercase;letter-spacing:.06em">Warnings sent</b><p class="small muted" style="margin:6px 0 0">None yet. Nobody signed up for alerts has this road on their route.</p></div>`;
  return `<div data-alerts><b class="small muted" style="text-transform:uppercase;letter-spacing:.06em">Warnings sent (${h.length})</b><div class="stack" style="margin-top:6px;gap:0">${h.slice(-8).map((a) => `<div class="row" style="flex-wrap:nowrap;gap:10px;padding:5px 0;border-bottom:1px solid var(--line)"><span class="small muted" style="min-width:48px">${clock(a.ts)}</span><span>${esc(ALERT_LABEL[a.kind] || a.kind.replace(/_/g, " "))}${a.telegram ? " · Telegram" : ""}</span></div>`).join("")}</div></div>`;
}
function detailView() {
  const i = state.incidents.find((x) => x.id === state.selected); if (!i) { state.selected = null; return queueView(); }
  return `<div class="stack"><button class="link" data-act="back">‹ Queue</button>${i.status === "Reported" ? pendingDetail(i) : activeDetail(i)}</div>`;
}

// ------------------------------------------------------------------ render
function renderCounts() {
  const w = state.incidents.reduce((n, i) => n + (i.people_warned || 0), 0);
  $("counts").innerHTML = signedIn() ? `<b>${pending().length}</b><span class="muted small">need you</span><span class="sep">|</span><b>${active().length}</b><span class="muted small">active</span><span class="sep">|</span><b>${w}</b><span class="muted small">warned</span><span id="live" class="chip" style="margin-left:auto"></span>` : "<b>Control room</b>";
  if (signedIn()) RP.bindLive($("live"), () => state);
}
function renderAll() {
  renderCounts(); const body = $("body"), keep = body.scrollTop;
  body.innerHTML = !signedIn() ? signInView() : state.selected ? detailView() : queueView(); body.scrollTop = keep;
  if (signedIn()) { drawIncidents(M.layers.inc, open(), { selected: state.selected, onClick: select }); drawUnits(); } else { M.layers.inc.clearLayers(); unitLayer.clearLayers(); }
  document.title = `${signedIn() && pending().length ? `(${pending().length}) ` : ""}RoadPulse — control room`;
}
async function select(id) {
  state.selected = id; const i = state.incidents.find((x) => x.id === id); if (!i) return;
  map.flyTo([i.lat, i.lon], 18, { duration: 0.6 }); renderAll(); sheet.set(1); await loadDetail(i, true); await loadUnits(i); renderAll();
}
async function loadDetail(i, force) { const c = state.detail[i.id]; if (force || !c || c.updated !== i.updated) { try { state.detail[i.id] = await api(`/api/incidents/${i.id}`); } catch (e) { /* keep */ } } }
async function loadUnits(i) { if (i.status === "Reported" && i.field_state === "none") { try { state.units[i.id] = (await api(`/api/incidents/${i.id}/units`)).units; } catch (e) { state.units[i.id] = []; } } }

$("body").addEventListener("click", async (e) => {
  const a = e.target.closest("[data-act],[data-open],#btn-login"); if (!a) return;
  if (a.id === "btn-login") { signIn(); return; }
  if (a.dataset.open) { select(a.dataset.open); return; }
  const act = a.dataset.act, id = a.dataset.id;
  if (act === "back") { state.selected = null; renderAll(); return; }
  if (act === "signout") { RP.auth.token = null; RP.store.sset(RP.auth.key, null); state.incidents = []; state.sig = ""; state.selected = null; renderAll(); return; }
  if ((act === "reject" || act === "clear" || act === "urgent") && !a.classList.contains("armed")) {          // two taps for anything that is hard to undo
    a.classList.add("armed"); a.dataset.label = a.innerHTML; a.innerHTML = act === "urgent" ? "Tap again: warn drivers now" : "Tap again to confirm";
    setTimeout(() => { if (a.isConnected) { a.classList.remove("armed"); a.innerHTML = a.dataset.label; } }, 4000); return;
  }
  a.disabled = true;
  try {
    if (act === "assign") { await api(`/api/incidents/${id}/assign`, { method: "POST", body: JSON.stringify({ unit: a.dataset.unit }) }); toast(`${a.dataset.unit} is being sent to check.`); }
    else if (act === "confirm") { await api(`/api/incidents/${id}/verify`, { method: "POST", body: JSON.stringify(a.dataset.sev ? { severity: a.dataset.sev } : {}) }); toast("Confirmed. Drivers whose route is affected are being warned now."); state.selected = null; }
    else if (act === "urgent") { await api(`/api/incidents/${id}/verify`, { method: "POST", body: JSON.stringify({ severity: a.dataset.sev, urgent: true, reason: "urgent, no police check" }) }); toast("Drivers are being warned now. Recorded as urgent."); state.selected = null; }
    else if (act === "respond") { await api(`/api/incidents/${id}/respond`, { method: "POST", body: "{}" }); toast("Diversion approved. Drivers are sent the way round."); await loadDetail(state.incidents.find((x) => x.id === id), true); }
    else if (act === "level") { await api(`/api/incidents/${id}/verify`, { method: "POST", body: JSON.stringify({ severity: a.dataset.sev }) }); toast(`Updated to ${SEV[a.dataset.sev].label.toLowerCase()}.`); }
    else if (act === "reject") { await api(`/api/incidents/${id}/reject`, { method: "POST", body: "{}" }); toast("Dismissed. Drivers were never told."); state.selected = null; }
    else if (act === "clear") { await api(`/api/incidents/${id}/clear`, { method: "POST", body: "{}" }); toast("Road marked clear. Everyone who was warned is told."); state.selected = null; }
    else if (act === "dispatch") { await api(`/api/incidents/${id}/dispatch`, { method: "POST", body: JSON.stringify({ unit: a.dataset.unit }) }); toast("Recorded as sent. RoadPulse does not call them: tell them yourself."); }
    await refresh(true);
  } catch (err) { toast(err.message); a.disabled = false; }
});

async function refresh(force) {
  if (!signedIn()) return;            // never redraw the sign-in form: it would wipe what is being typed
  try {
    const r = await api("/api/incidents?scope=all"); state.lastOk = Date.now(); state.connected = true;
    // a boolean for "recovering", not the countdown itself: redrawing every poll would wipe a half-finished two-tap action
    const sig = JSON.stringify(r.incidents.map((i) => [i.id, i.status, i.severity, i.updated, i.people_warned, i.field_state, i.clearing_in_s != null]));
    if (force || sig !== state.sig) {
      if (state.seen) { const fresh = r.incidents.filter((i) => i.status === "Reported" && !state.seen.has(i.id)); fresh.forEach((i) => toast(`New report to check: ${headline(i)}.`)); if (fresh.length && state.sound) beep(); }
      state.seen = new Set(r.incidents.map((i) => i.id)); state.incidents = r.incidents; state.sig = sig;
      await Promise.all(open().map((i) => loadDetail(i))); const sel = state.incidents.find((x) => x.id === state.selected);
      if (sel) { await loadDetail(sel, true); await loadUnits(sel); }   // the warnings list can grow without the incident itself changing
      renderAll();
      if (!state.fitted && open().length) { state.fitted = true; map.fitBounds(L.latLngBounds(open().map((i) => [i.lat, i.lon])), { paddingTopLeft: [460, 90], paddingBottomRight: [60, 60], maxZoom: 17 }); }
    } else renderCounts();
    loadUnitsMap();
  } catch (e) { state.connected = false; }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true); });
(async function start() {
  try { const h = await api("/api/health"); if (h.center) { state.center = h.center; map.setView(h.center, 16); } } catch (e) { /* offline */ }
  renderAll(); await refresh(true); setInterval(() => { if (!document.hidden) refresh(); }, 2500);
})();
