"use strict";
// Traffic police in the field (phone first): get a call, accept, travel, arrive, report what you SEE. The control room then confirms.
// Built for a weak signal: updates are saved on the phone and sent by themselves when the connection returns.
const { $, esc, clock, icon, KIND, SEV, toast, api, makeMap, makeSheet, pinIcon } = RP;
RP.auth.key = "rp_police_token"; RP.auth.token = RP.store.sget(RP.auth.key); RP.auth.onExpire = () => { renderAll(); };
const state = { unit: null, calls: [], sel: null, pos: null, centre: [17.7287, 83.3086], draft: { finding: null, need: [], note: "" }, msg: "", known: null, lastSent: 0, lastDraw: 0, sig: "" };
const M = makeMap(state.centre), map = M.map;
const sheet = makeSheet($("sheet"), $("grab"), () => map.invalidateSize());
$("btn-style").innerHTML = icon("layers", 22); $("btn-style").onclick = () => M.toggleStyle();
const signedIn = () => !!RP.auth.token;
const call = () => state.calls.find((c) => c.id === state.sel);
const dist = (a, b) => { const R = 6371000, r = (x) => (x * Math.PI) / 180, h = Math.sin(r(b[0] - a[0]) / 2) ** 2 + Math.cos(r(a[0])) * Math.cos(r(b[0])) * Math.sin(r(b[1] - a[1]) / 2) ** 2; return 2 * R * Math.asin(Math.sqrt(h)); };
const ORDER = ["assigned", "accepted", "on_way", "arrived", "scene_safe"];
const buzz = (p = 30) => { try { navigator.vibrate && navigator.vibrate(p); } catch (e) { /* unsupported */ } };

// ------------------------------------------------------------------ no-signal queue
const QKEY = "rp_police_queue";
const queue = () => { try { return JSON.parse(localStorage.getItem(QKEY) || "[]"); } catch (e) { return []; } };
function saveQueue(q) { try { localStorage.setItem(QKEY, JSON.stringify(q)); } catch (e) { /* ignore */ } const n = q.length, el = $("queued"); el.classList.toggle("hidden", !n); el.textContent = `${n} waiting to send`; }
async function post(path, body, optimistic) {
  try { return await api(path, { method: "POST", body: JSON.stringify(body) }); }
  catch (e) {
    if (!/Cannot reach/.test(e.message)) throw e;                       // a real refusal from the server is shown; only "no signal" is queued
    const q = queue(); q.push({ path, body, at: Date.now() }); saveQueue(q); optimistic && optimistic(); renderAll();
    toast("No signal. Saved on your phone: it sends by itself when you are back online.", 7000); return null;
  }
}
async function flush() {
  const q = queue(); if (!q.length || !signedIn()) return; const rest = [];
  for (const it of q) { try { await api(it.path, { method: "POST", body: JSON.stringify(it.body) }); } catch (e) { if (/Cannot reach/.test(e.message)) rest.push(it); } }   // refused items are dropped
  saveQueue(rest); if (rest.length < q.length) { toast("Back online: your updates were sent."); buzz([60, 40, 60]); refresh(true); }
}
setInterval(flush, 5000); addEventListener("online", flush);

// ------------------------------------------------------------------ screens
function signInView() {
  return `<div class="stack"><div class="row" style="gap:12px"><span class="ico" style="--c:var(--brand)">${icon("shield", 24)}</span><div><h2>Traffic police</h2><div class="muted small">Sign in to get calls from the control room.</div></div></div>
    <label for="user">Unit</label><input id="user" placeholder="e.g. unit12" autocomplete="username" autocapitalize="none"><label for="pass">Password</label><input id="pass" type="password" autocomplete="current-password">
    <button class="btn-big" id="btn-login">Sign in</button><p class="small" style="color:var(--bad);min-height:1.2em;margin:0">${esc(state.msg)}</p></div>`;
}
const labelFor = (s) => ({ assigned: "New call", accepted: "Accepted", on_way: "On your way", arrived: "On the scene", scene_safe: "On the scene" }[s] || s);
const distM = (c) => (state.pos ? Math.round(dist(state.pos, [c.lat, c.lon])) : null);
const distText = (c) => { const d = distM(c); return d == null ? "" : d >= 1000 ? `${(d / 1000).toFixed(1)} km away` : `${d} m away`; };
function homeView() {
  if (!state.calls.length) return `<div class="empty" style="padding:30px 10px">${icon("shield", 44)}<h2 style="margin-top:8px">No calls right now</h2><p>You will be told here, with a buzz, the moment the control room sends you somewhere.</p>
    <button class="btn-sm ghost" data-act="share">Share my location</button><p><button class="link small" data-act="signout">Sign out</button></p></div>`;
  return `<div class="stack"><div class="row spread"><h2>Your calls</h2><button class="link" data-act="signout">Sign out</button></div>${state.calls.map((c) => {
    const s = SEV[c.severity]; return `<div class="prob pending" style="--c:${s.color}" data-open="${c.id}"><div class="row" style="flex-wrap:nowrap"><span class="ico">${icon(KIND[c.type].ic, 22)}</span>
      <div class="grow"><b>${esc(KIND[c.type].name)} on ${esc(c.road || "this road")}</b><div class="small muted">${labelFor(c.my_status)}${distText(c) ? " · " + distText(c) : ""}</div></div><span class="muted">›</span></div></div>`; }).join("")}</div>`;
}
function stepper(c) {
  const at = ORDER.indexOf(c.my_status), last = c.my_reports[c.my_reports.length - 1], reported = !!last, opened = last && last.finding === "open";
  const st = [at >= 1, at >= 2, at >= 3, reported && !opened, opened], names = ["Accepted", "On the way", "Arrived", "Reported", "Open"];
  return `<div class="steps">${names.map((n, k) => `${k ? `<span class="l ${st[k - 1] ? "done" : ""}"></span>` : ""}<span class="d ${st[k] ? "done" : (k === 0 || st[k - 1]) ? "on" : ""}">${st[k] ? "✓" : k + 1}</span>`).join("")}</div>
    <div class="row small muted" style="justify-content:space-between"><span>Accepted</span><span>On the way</span><span>Arrived</span><span>Reported</span><span>Open</span></div>`;
}
function callView() {
  const c = call(); if (!c) { state.sel = null; return homeView(); }
  const s = SEV[c.severity], st = c.my_status, last = c.my_reports[c.my_reports.length - 1], d = distM(c);
  const head = `<div class="stack"><button class="link" data-act="back">‹ Your calls</button>
    <div class="row" style="flex-wrap:nowrap;gap:12px"><span class="ico" style="--c:${s.color};width:52px;height:52px">${icon(KIND[c.type].ic, 28)}</span><div><h2>${esc(KIND[c.type].name)} on ${esc(c.road || "this road")}</h2><div class="small muted">${distText(c) || "Share your location to see the distance"}</div></div></div>${stepper(c)}`;
  const maps = `<a class="btn-big ghost" target="_blank" rel="noopener" href="https://www.google.com/maps/dir/?api=1&destination=${c.lat},${c.lon}&travelmode=driving">${icon("nav", 20)} Open in maps</a>`;
  const near = d != null && d < 80;
  if (st === "assigned") return `${head}<div class="hero warn"><span class="ic">${icon("warn", 26)}</span><div><b>New call</b>The control room needs you to check this.</div></div>
    <div class="sheet-foot"><button class="btn-big good" data-act="status" data-st="accepted">Accept</button><button class="btn-big ghost" data-act="status" data-st="declined">Can't go</button></div></div>`;
  if (st === "accepted") return `${head}${maps}<div class="sheet-foot"><button class="btn-big" data-act="status" data-st="on_way">I'm on my way</button></div></div>`;
  if (st === "on_way") return `${head}${near ? `<div class="hint-ok">You're at the scene (${d} m). Tap Arrived.</div>` : ""}${maps}
    <div class="sheet-foot"><button class="btn-big good ${near ? "pulse" : ""}" data-act="status" data-st="arrived">I've arrived</button></div></div>`;
  const dr = state.draft, tiles = [["blocked", "Blocked", "Nobody can pass", "stop", "#7a1fa2"], ["one_lane", "One lane", "Slow but moving", "lane", "#ef7d00"], ["not_real", "Not real", "Nothing here", "x", "#64748b"]];
  const done = last && last.finding !== "open";
  return `${head}${done ? `<div class="hero ok"><span class="ic">${icon("check", 26)}</span><div><b>Sent to the control room</b>You reported: ${({ blocked: "blocked", one_lane: "one lane", not_real: "not real" })[last.finding] || last.finding}. They will confirm.</div></div>` : ""}
    <div><b>${done ? "Update what you see" : "What do you see?"}</b></div>
    <div class="grid3">${tiles.map(([k, n, note, ic, col]) => `<button class="tile ${dr.finding === k ? "on" : ""}" style="--c:${col};padding:14px 4px" data-find="${k}">${icon(ic, 26)}${n}<small>${note}</small></button>`).join("")}</div>
    <div><b>Need help here?</b></div>
    <div class="grid2"><button class="tile ${dr.need.includes("ambulance") ? "on" : ""}" style="--c:#dc2626" data-need="ambulance">${icon("ambulance", 26)}Ambulance</button><button class="tile ${dr.need.includes("tow") ? "on" : ""}" style="--c:#2563eb" data-need="tow">${icon("tow", 26)}Tow vehicle</button></div>
    <textarea id="note" placeholder="Anything the control room should know (optional)">${esc(dr.note)}</textarea>
    <div class="sheet-foot"><button class="btn-big" data-act="send" ${dr.finding ? "" : "disabled"}>Send to control room</button><button class="btn-big good" data-act="open">${icon("road", 22)} The road is open</button></div></div>`;
}

// ------------------------------------------------------------------ actions
$("body").addEventListener("click", async (e) => {
  const a = e.target.closest("[data-act],[data-open],[data-find],[data-need],#btn-login"); if (!a) return;
  if (a.id === "btn-login") { signIn(); return; }
  if (a.dataset.open) { state.sel = a.dataset.open; state.draft = { finding: null, need: [], note: "" }; buzz(); renderAll(); sheet.set(1); return; }
  if (a.dataset.find) { state.draft.finding = a.dataset.find; state.draft.note = $("note") ? $("note").value : ""; buzz(15); renderAll(); return; }
  if (a.dataset.need) { const n = a.dataset.need, d = state.draft; d.note = $("note") ? $("note").value : d.note; d.need = d.need.includes(n) ? d.need.filter((x) => x !== n) : [...d.need, n]; buzz(15); renderAll(); return; }
  const act = a.dataset.act;
  if (act === "back") { state.sel = null; renderAll(); return; }
  if (act === "signout") { RP.auth.token = null; RP.store.sset(RP.auth.key, null); state.calls = []; state.sel = null; renderAll(); return; }
  if (act === "share") { shareLocation(true); return; }
  const c = call(); if (!c) return; a.disabled = true; buzz(40);
  try {
    if (act === "status") {
      const st = a.dataset.st;
      await post(`/api/police/calls/${c.id}/status`, { status: st }, () => { c.my_status = st; });
      if (st === "declined") { state.sel = null; toast("Call returned to the control room."); } else toast({ accepted: "Accepted. Head there safely.", on_way: "The control room knows you are on the way.", arrived: "Arrived. Tell us what you see." }[st]);
    } else if (act === "send" || act === "open") {
      const body = act === "open" ? { finding: "open", need: [], note: $("note") ? $("note").value.trim() || null : null } : { finding: state.draft.finding, need: state.draft.need, note: $("note").value.trim() || null };
      await post(`/api/police/calls/${c.id}/report`, body, () => { c.my_reports.push({ ...body, at: Date.now() / 1000 }); });
      toast(act === "open" ? "The control room is told the road is open." : "Sent. The control room will confirm."); if (act === "send") state.draft = { finding: null, need: [], note: "" };
    }
    await refresh(true);
  } catch (err) { toast(err.message); a.disabled = false; }
});

async function signIn() {
  const b = $("btn-login"); b.disabled = true; state.msg = "";
  try {
    const r = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: $("user").value.trim(), password: $("pass").value }) });
    if (r.role !== "police") { state.msg = "That is a control-room login. Open the control room app instead."; renderAll(); return; }
    RP.auth.token = r.token; RP.store.sset(RP.auth.key, r.token); await start();
  } catch (e) { state.msg = /wrong username/i.test(e.message) ? "That unit or password is not right." : e.message; renderAll(); }
}
document.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.id === "pass") signIn(); });

// ------------------------------------------------------------------ map, live location, keep the screen on
function focusCall() {
  M.layers.pins.clearLayers(); const c = call(); if (!c) return; const s = SEV[c.severity];
  L.marker([c.lat, c.lon], { icon: pinIcon(`<div class="pin sel" style="--c:${s.color}"><div class="dot2">${icon(KIND[c.type].ic, 20)}</div><div class="tag">${esc(KIND[c.type].short)}</div></div>`) }).addTo(M.layers.pins);
  if (state.pos) { L.marker(state.pos, { icon: pinIcon('<div class="me"></div>') }).addTo(M.layers.pins); L.polyline([state.pos, [c.lat, c.lon]], { color: "#2563eb", weight: 5, dashArray: "8 8" }).addTo(M.layers.pins);
    if (!focusCall.done || focusCall.id !== c.id) { focusCall.done = true; focusCall.id = c.id; map.fitBounds(L.latLngBounds([state.pos, [c.lat, c.lon]]), { paddingTopLeft: [60, 90], paddingBottomRight: [60, innerHeight * 0.55], maxZoom: 17 }); } }
  else if (focusCall.id !== c.id) { focusCall.id = c.id; map.flyTo([c.lat, c.lon], 17); }
}
let watchId = null;
function startWatch() {
  if (watchId != null || !navigator.geolocation) return;
  watchId = navigator.geolocation.watchPosition((p) => {
    state.pos = [p.coords.latitude, p.coords.longitude]; $("where").textContent = "location shared";
    if (Date.now() - state.lastSent > 15000) { state.lastSent = Date.now(); api("/api/police/position", { method: "POST", body: JSON.stringify({ lat: state.pos[0], lon: state.pos[1] }) }).catch(() => {}); }
    if (state.sel && Date.now() - state.lastDraw > 4000 && !(document.activeElement && document.activeElement.tagName === "TEXTAREA")) renderAll();   // keep the distance live, never while typing
  }, () => { $("where").textContent = "location off"; }, { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 });
}
function shareLocation(announce) { if (!navigator.geolocation) { toast("This browser can't share location."); return; } startWatch(); if (announce) toast("Location shared with the control room."); }
let wake = null;
async function keepAwake() { try { if ("wakeLock" in navigator && !wake && state.calls.length) { wake = await navigator.wakeLock.request("screen"); wake.addEventListener("release", () => { wake = null; }); } } catch (e) { /* not allowed */ } }
document.addEventListener("visibilitychange", () => { if (!document.hidden) { keepAwake(); refresh(true); } });

function renderAll() {
  state.lastDraw = Date.now();
  $("unit").textContent = state.unit || "Traffic police"; $("dot").style.display = signedIn() ? "" : "none";
  const keep = $("body").scrollTop, note = $("note") ? $("note").value : null;
  $("body").innerHTML = !signedIn() ? signInView() : state.sel ? callView() : homeView();
  if (note != null && $("note")) $("note").value = note; $("body").scrollTop = keep;
  if (state.sel) focusCall(); else M.layers.pins.clearLayers();
  document.title = `${state.calls.filter((c) => c.my_status === "assigned").length ? "(new call) " : ""}RoadPulse — traffic police`;
}
async function refresh(force) {
  if (!signedIn()) return;
  try {
    const r = await api("/api/police/calls"); const sig = JSON.stringify(r.calls.map((c) => [c.id, c.my_status, c.my_reports.length, c.status]));
    if (force || sig !== state.sig) {
      const ids = new Set(r.calls.map((c) => c.id));
      if (state.known) r.calls.filter((c) => c.my_status === "assigned" && !state.known.has(c.id)).forEach((c) => { toast(`New call: ${KIND[c.type].name} on ${c.road || "a road"}`, 9000); buzz([300, 150, 300, 150, 300]); });
      state.known = ids; state.calls = r.calls; state.sig = sig; renderAll(); keepAwake();
    }
  } catch (e) { /* no signal: keep what we have */ }
}
async function start() {
  if (!signedIn()) { renderAll(); return; }
  try { const me = await api("/api/police/me"); state.unit = me.unit; if (me.centre) { state.centre = me.centre; map.setView(me.centre, 16); } } catch (e) { renderAll(); return; }
  saveQueue(queue()); startWatch(); await refresh(true); if (!start.timer) start.timer = setInterval(() => { if (!document.hidden) refresh(); }, 3000);
}
renderAll(); start();
