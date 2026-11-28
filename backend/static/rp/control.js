/* RoadPulse control room: check 1 of 2 on citizen reports, urgent fast-track, diversions, resolution. */
"use strict";

const { $, esc } = RP;
const st = { incidents: [], hotspots: [], rain: null, events: [], sel: null, detail: null, detailKey: "", picking: false, worksPoint: null, outFor: null };

const STAGE_TAG = {
  control_review: ['plain', "Needs your check"],
  with_field: ['plain', "With field police"],
  urgent_awaiting_field: ['urgent', "Urgent, field check pending"],
  field_disputed: ['urgent', "Field could not confirm"],
  confirmed: ['ok', "Two checks passed"],
  trusted_evidence: ['ok', "Sensor evidence"],
};
const SOURCE = { sensor: "Water sensor", probe: "Fleet speeds", cctv: "CCTV", report: "Citizen report", planned: "Planned works", officer: "Control room", field: "Field police" };
const UNITS = { police: "Police", ambulance: "Ambulance", tow: "Tow truck", drainage: "Drainage crew", marshal: "Traffic marshal" };
const SEV_CHOICES = [["", "Keep as assessed"], ["Low", "Low: slow but passable"], ["Medium", "Medium: partly blocked"], ["Severe", "Severe: mostly blocked"], ["Closed", "Closed: nobody can pass"]];

// ---------------------------------------------------------------- sign in
RP.useSession("rp_control", () => showDesk(false));
function showDesk(on) {
  $("signin").hidden = on; $("desk").hidden = !on;
  for (const id of ["conn", "who", "btn-signout"]) $(id).hidden = !on;
  if (on) { $("who").textContent = sessionStorage.getItem("rp_control_who") || ""; start(); }
  else { RP.disconnect(); }
}
$("signin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("signin-err"); err.textContent = ""; $("btn-signin").disabled = true;
  try {
    const r = await RP.post("/api/auth/login", { username: $("user").value.trim(), password: $("pass").value });
    if (r.role === "field") { err.textContent = "This is a field police account. Sign in on the field app instead."; return; }
    RP.setToken(r.token); sessionStorage.setItem("rp_control_who", $("user").value.trim());
    $("pass").value = ""; showDesk(true);
  } catch (ex) { err.textContent = ex.message; } finally { $("btn-signin").disabled = false; }
});
$("btn-signout").addEventListener("click", () => { RP.setToken(null); st.sel = null; showDesk(false); });

// ---------------------------------------------------------------- map
let map = null; const Lg = {};
async function start() {
  if (map) { RP.connect({ staff: true, onSnapshot }); return; }
  map = RP.createMap("map");
  for (const k of ["network", "watch", "sel", "incidents", "pins"]) Lg[k] = L.layerGroup().addTo(map);
  RP.legend(map, ["roads", "pending", "signs", "watch"]);
  map.on("click", (e) => {
    if (!st.picking) return;
    if (!RP.inPilot(e.latlng)) { RP.toast(RP.OUTSIDE, true); return; }
    st.worksPoint = [e.latlng.lat, e.latlng.lng]; setPicking(false); drawPins();
  });
  await RP.loadNetwork(map, Lg.network);
  RP.connect({ staff: true, onSnapshot });
}
function drawPins() {
  Lg.pins.clearLayers();
  if (st.worksPoint) L.marker(st.worksPoint, { icon: RP.pinIcon("r", "W") }).bindTooltip("Planned works").addTo(Lg.pins);
  $("pw-loc").textContent = st.worksPoint ? `${st.worksPoint[0].toFixed(5)}, ${st.worksPoint[1].toFixed(5)}` : "No place chosen";
  $("btn-pw").disabled = !st.worksPoint;
}
function setPicking(on) {
  st.picking = on; $("map").classList.toggle("picking", on); $("btn-pw-pick").classList.toggle("picking", on);
  $("btn-pw-pick").textContent = on ? "Click the map" : "Choose on the map";
}

// ---------------------------------------------------------------- queues
const nowS = () => Date.now() / 1000;
function bucket(i) {
  const s = i.review.stage;
  if (i.type === "construction" && i.starts && i.starts > nowS()) return "live";
  if (s === "control_review" || s === "field_disputed") return "check";
  if (s === "with_field") return "field";
  if (i.published) return "live";
  return "check";
}
function item(i) {
  const [cls, label] = STAGE_TAG[i.review.stage] || ["plain", i.status];
  const urgent = i.review.urgent && i.review.stage !== "confirmed";
  const style = i.published && i.severity !== "Closed" ? `background:${RP.SEV[i.severity]}` : "";
  const since = i.review.checks && i.review.checks.control && i.review.stage === "with_field" ? `sent ${RP.ago(i.review.checks.control.at)}` : `reported ${RP.ago(i.status_times.Reported || i.updated)}`;
  const scheduled = i.type === "construction" && i.starts && i.starts > nowS() ? `<span class="tag plain">Starts ${RP.clock(i.starts)}</span>` : "";
  return `<li class="item ${urgent ? "urgent" : ""} ${st.sel === i.id ? "sel" : ""}" data-id="${i.id}" tabindex="0">
    <span class="sign ${i.published ? i.severity : "pending"}" style="${style}">${RP.GLYPH[i.type]}</span>
    <div><div class="t">${esc(RP.TYPE[i.type])}${i.road ? ` on ${esc(i.road)}` : ""}</div>
    <div class="m">${i.road ? "" : `${esc(RP.roadLabel(i))}. `}${since}${i.signals_by_source.report > 1 ? `, ${i.signals_by_source.report} citizen reports` : ""}</div>
    <div class="tags">${RP.sevTag(i.severity)}<span class="tag ${cls}">${label}</span>${scheduled}${i.status === "Responding" ? '<span class="tag plain">Diversion approved</span>' : ""}${RP.simTag(i.simulated)}</div></div></li>`;
}
function renderQueues() {
  const groups = { check: [], field: [], live: [] };
  for (const i of st.incidents) if (i.status !== "Cleared") groups[bucket(i)].push(i);
  groups.check.sort((a, b) => (b.review.stage === "field_disputed") - (a.review.stage === "field_disputed") || a.updated - b.updated);
  groups.live.sort((a, b) => (b.review.urgent - a.review.urgent) || b.updated - a.updated);
  const empty = { check: "Nothing waiting for you.", field: "No report is with field police.", live: "Nothing on the public map." };
  for (const k of ["check", "field", "live"]) {
    $(`q-${k}`).innerHTML = groups[k].length ? groups[k].map(item).join("") : `<li class="none">${empty[k]}</li>`;
    $(`n-${k}`).textContent = groups[k].length || "";
  }
  $("n-check").className = "n" + (groups.check.length ? " hot" : "");
  $("rain").textContent = st.rain && st.rain.rain_mm_per_hr ? `Rain ${st.rain.rain_mm_per_hr} mm/h: ${st.rain.rain_source}` : "No rain set";
  $("q-watch").innerHTML = st.hotspots.length ? st.hotspots.map((h) => {
    const p = RP.placeName(h.name);
    return `<li><div class="wh"><b>${esc(p.name)}</b>${RP.simTag(h.last_reading_simulated || h.simulated_location)}</div>
    ${p.road ? `<div class="muted">${esc(p.road)}</div>` : ""}
    <div class="wd"><span class="${h.risk !== "low" ? `risk-${h.risk}` : ""}">${RP.depth(h.depth_cm)}</span>, ${RP.trend(h.rise_cm_per_hr)}</div>
    <div class="muted">${h.risk !== "low" ? `${esc(h.message)}. ` : ""}${h.last_reading_age_s == null ? "No sensor reading yet" : `Sensor read ${RP.ago(Date.now() / 1000 - h.last_reading_age_s)}`}</div></li>`;
  }).join("") : `<li class="none">No flood-watch points configured.</li>`;
  if (!st.sel) renderOverview(groups);
  $("events").innerHTML = st.events.length ? st.events.slice(0, 25).map((e) => `<li><time>${RP.clock(e.ts)}</time>${esc(e.text)}</li>`).join("") : `<li>No activity yet.</li>`;
}
for (const q of ["q-check", "q-field", "q-live"]) {
  $(q).addEventListener("click", (e) => { const li = e.target.closest("li[data-id]"); if (li) select(li.dataset.id, true); });
  $(q).addEventListener("keydown", (e) => { const li = e.target.closest("li[data-id]"); if (li && e.key === "Enter") select(li.dataset.id, true); });
}

// ---------------------------------------------------------------- detail
async function select(id, pan) {
  st.sel = id; st.outFor = null; st.detailKey = "";
  const i = st.incidents.find((x) => x.id === id);
  if (i && pan) map.setView([i.lat, i.lon], Math.max(map.getZoom(), 17));
  renderQueues(); drawSel();
  await loadDetail();
  if (window.innerWidth <= 1000) $("detail").scrollIntoView({ behavior: "smooth" });
}
function renderOverview(groups) {
  const pub = groups.live.filter((i) => i.published);
  const closed = pub.filter((i) => i.severity === "Closed").length;
  const urgentOpen = pub.filter((i) => i.review.urgent && i.review.stage !== "confirmed").length;
  const stat = (n, label, hot) => `<div class="stat ${hot && n ? "hot" : ""}"><b>${n}</b><span>${label}</span></div>`;
  $("detail").innerHTML = `<div class="overview">
    <h2>Right now</h2>
    <div class="stats">${stat(groups.check.length, "waiting for your check", true)}${stat(groups.field.length, "with field police")}
      ${stat(pub.length, "on the public map")}${stat(closed, "roads closed")}</div>
    ${urgentOpen ? `<div class="note warn">${urgentOpen} urgent incident${urgentOpen > 1 ? "s are" : " is"} public and still waiting for field police to confirm.</div>` : ""}
    <h3>How a citizen report is checked</h3>
    ${RP.stepsHtml("control_review")}
    <ol class="flow">
      <li>You check it: plausible, a duplicate, or nonsense.</li>
      <li>Field police confirm it at the spot. Only then does it reach the public map and change routes.</li>
      <li>For a real emergency press <b>Urgent: publish now</b>: it goes public at once and field police still confirm it.</li>
    </ol>
    <p class="muted small">Water-sensor evidence is trusted and publishes on its own. Select anything in the queue or on the map to act on it.</p>
  </div>`;
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && st.sel && !$("desk").hidden && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) {
    st.sel = null; st.detail = null; st.detailKey = ""; drawSel(); renderQueues();
  }
});

function drawSel() {
  Lg.sel.clearLayers();
  const i = st.incidents.find((x) => x.id === st.sel);
  if (i) L.circleMarker([i.lat, i.lon], { radius: 24, color: "#23272c", weight: 2.5, fillColor: "#f5b700", fillOpacity: 0.25, interactive: false }).addTo(Lg.sel);
}
async function loadDetail() {
  if (!st.sel) return;
  try { st.detail = await RP.api(`/api/incidents/${st.sel}`); } catch (e) { st.detail = null; RP.toast(e.message, true); }
  renderDetail(true);
}

function confBar(c) {
  return `<span class="conf"><span class="track" title="0.70 is the publishing bar for trusted evidence"><i style="width:${Math.round(c * 100)}%"></i><b style="left:70%"></b></span>${c.toFixed(2)}</span>`;
}
function checkLine(label, c) {
  if (!c) return `<div class="check-line muted">${label}: not yet</div>`;
  const d = { forwarded: "checked and sent to field police", urgent: "marked urgent and published", confirmed: "confirmed at the spot", not_found: "could not find it at the spot" }[c.decision] || c.decision;
  return `<div class="check-line"><b>${label}</b>: ${esc(c.by)} ${d}, ${RP.clock(c.at)}${c.note ? `. “${esc(c.note)}”` : ""}</div>`;
}

function renderDetail(force = false) {
  const d = st.detail, box = $("detail");
  if (!d) return;
  const key = `${d.id}:${d.updated}:${st.outFor}`;
  if (!force && key === st.detailKey) return;
  st.detailKey = key;
  const keep = { note: $("act-note") ? $("act-note").value : "", sev: $("act-sev") ? $("act-sev").value : "" };
  const s = d.review.stage, urgent = d.review.urgent && s !== "confirmed";
  const [cls, label] = STAGE_TAG[s] || ["plain", d.status];
  const blocked = d.affected_vehicles.length ? d.affected_vehicles.map((v) => RP.VEHICLE[v]).join(", ") : "None";
  const sevPick = d.type !== "flood" && d.type !== "construction" && d.status !== "Cleared"
    ? `<div><label class="field" for="act-sev">How bad is it?</label><select id="act-sev">${SEV_CHOICES.map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select></div>` : "";
  const note = `<div><label class="field" for="act-note">Note for the record <span class="muted">(optional)</span></label><input id="act-note" type="text" maxlength="200" placeholder="${s === "control_review" ? "Caller confirms two vehicles involved" : "Reason"}"></div>`;

  let actions = "";
  if (d.status === "Cleared") {
    actions = `<p class="muted">Closed: ${esc(d.cleared_reason || "")}</p>`;
  } else if (s === "control_review") {
    actions = `<p class="small">Is this plausible? Send it to field police to confirm at the spot. Nothing reaches the public map until they do.</p>${sevPick}${note}
      <div class="row"><button class="dark" data-act="verify">Send to field police</button></div>
      <div class="row"><button class="urgent" data-act="urgent" title="Publishes now. Field police are still asked to confirm.">Urgent: publish now</button><button class="danger" data-act="reject">Reject</button></div>`;
  } else if (s === "with_field") {
    actions = `<div class="note">Waiting for field police to confirm it at the spot. Sent ${RP.ago(d.review.checks.control.at)}.</div>${sevPick}${note}
      <div class="row"><button class="urgent" data-act="urgent">Urgent: publish now</button><button class="danger" data-act="reject">Reject</button></div>`;
  } else if (s === "field_disputed") {
    actions = `<div class="note bad">Field police could not find this at the spot. It is still on the public map because you marked it urgent. Decide whether it stays.</div>${note}
      <div class="row"><button class="dark" data-act="clear">Take it off the map</button></div>`;
  } else if (d.status === "Reported" && !d.review.two_check) {
    actions = `<p class="small">Trusted evidence is below the publishing bar. You can verify it yourself.</p>${sevPick}${note}
      <div class="row"><button class="dark" data-act="verify">Verify and publish</button><button class="danger" data-act="reject">Reject</button></div>`;
  } else {
    actions = `${urgent ? '<div class="note warn">Published as urgent. Field police have been asked to confirm it.</div>' : ""}${sevPick}${note}
      <div class="row">${d.status === "Verified" ? '<button class="primary" data-act="respond">Approve diversion</button>' : ""}<button data-act="clear">Resolve: road open again</button></div>`;
  }
  const dispatch = d.status !== "Cleared" ? `<div class="d-sec"><h3>Send help</h3><p class="small muted" style="margin:-4px 0 8px">Records who was sent. RoadPulse does not call them.</p>
    <div class="units">${Object.entries(UNITS).map(([k, v]) => `<button class="sm" data-unit="${k}">${v}</button>`).join("")}</div>
    ${d.dispatched && d.dispatched.length ? `<p class="sent">Sent: ${d.dispatched.map((x) => `${esc(UNITS[x.unit])} at ${RP.clock(x.at)} by ${esc(x.by)}`).join("; ")}</p>` : ""}</div>` : "";
  const support = d.published && d.status !== "Cleared" ? `<div class="d-sec"><h3>Decision support</h3>
    <div class="units"><button class="sm" data-tool="whatif">Compare diversions</button><button class="sm" data-tool="signals">Signal timing advice</button><button class="sm" data-tool="bulletin">Draft public bulletin</button></div>
    <div id="tool-out" class="out"></div></div>` : "";

  const ev = (d.signals || []).slice().reverse().map((g) => `<li><div><div class="src">${esc(SOURCE[g.source] || g.source)}</div><div class="when">${RP.clock(g.ts)}</div></div>
    <div>${g.depth_cm != null ? `${Math.round(g.depth_cm)} cm water. ` : ""}${g.note ? esc(g.note) : g.source === "report" ? "No description" : ""} ${RP.simTag(g.simulated)}</div></li>`).join("");

  box.innerHTML = `<div class="d-head ${urgent ? "urgent" : ""}">
      <h2>${esc(RP.TYPE[d.type])}</h2>
      <div class="road">${esc(RP.roadLabel(d))}<br><span class="muted small">${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}</span></div>
      <div class="tags">${RP.sevTag(d.severity)}<span class="tag ${cls}">${label}</span>${d.published ? '<span class="tag ok">On the public map</span>' : '<span class="tag plain">Not public</span>'}${RP.simTag(d.simulated)}</div>
    </div>
    ${d.review.two_check || d.review.stage === "trusted_evidence" ? `<div class="d-sec">${RP.stepsHtml(s)}
      ${d.review.two_check ? checkLine("Control room", d.review.checks.control) + checkLine("Field police", d.review.checks.field) : ""}</div>` : ""}
    <div class="d-sec actions">${actions}</div>
    ${dispatch}
    <div class="d-sec"><h3>Assessment</h3><dl class="facts">
      <dt>Confidence</dt><dd>${confBar(d.confidence)}</dd>
      ${d.depth_cm != null ? `<dt>Water depth</dt><dd>${Math.round(d.depth_cm)} cm</dd>` : ""}
      <dt>Blocked for</dt><dd>${esc(blocked)}</dd>
      <dt>Status</dt><dd>${esc(d.status)}${d.clearing_in_s != null ? `, auto-clears in ${d.clearing_in_s} s if conditions hold` : ""}</dd>
      <dt>Timeline</dt><dd>${timeline(d)}</dd>
      ${d.people_warned ? `<dt>Commuters warned</dt><dd>${d.people_warned}</dd>` : ""}
    </dl></div>
    ${support}
    <div class="d-sec"><h3>Evidence</h3><ul class="evidence">${ev || '<li class="muted">No signals kept.</li>'}</ul></div>`;
  if ($("act-note")) $("act-note").value = keep.note;
  if ($("act-sev")) $("act-sev").value = keep.sev;
}

function timeline(d) {
  const names = { Reported: "Reported", Verified: "Published", Responding: "Diversion approved", Cleared: "Closed" };
  const rows = Object.entries(d.status_times).sort((a, b) => a[1] - b[1]).map(([k, t]) => `${names[k] || k} at ${RP.clock(t)}`);
  if (d.status !== "Cleared") rows.push(`${Math.max(0, Math.round((Date.now() / 1000 - d.status_times[d.status]) / 60))} min in this stage`);
  return rows.join("<br>");
}

$("detail").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b || !st.detail) return;
  const id = st.detail.id;
  try {
    if (b.dataset.act) {
      const act = b.dataset.act;
      const body = { reason: ($("act-note") && $("act-note").value.trim()) || null };
      if ($("act-sev") && $("act-sev").value) body.severity = $("act-sev").value;
      if (act === "reject" && !confirm("Reject this report? It will not be published and the reporter will be told it could not be confirmed.")) return;
      b.disabled = true;
      await RP.post(`/api/incidents/${id}/${act}`, body);
      if ($("act-note")) $("act-note").value = "";
      RP.toast({ verify: st.detail.review.two_check ? "Sent to field police" : "Verified and published", urgent: "Published as urgent. Field police asked to confirm.",
        reject: "Report rejected", respond: "Diversion approved", clear: "Resolved. The road is open again on the public map." }[act]);
      await loadDetail();
    } else if (b.dataset.unit) {
      b.disabled = true;
      await RP.post(`/api/incidents/${id}/dispatch`, { unit: b.dataset.unit });
      RP.toast(`${UNITS[b.dataset.unit]} recorded as sent`);
      await loadDetail();
    } else if (b.dataset.tool) {
      st.outFor = null; const out = $("tool-out"); out.innerHTML = '<p class="muted">Working</p>';
      out.innerHTML = await tool(b.dataset.tool, id);
    }
  } catch (ex) { RP.toast(ex.message, true); b.disabled = false; }
});

async function tool(kind, id) {
  if (kind === "whatif") {
    const r = await RP.api(`/api/incidents/${id}/whatif`);
    return `<table><tr><th>Option</th><th>Average trip</th><th>Sent into it</th></tr>${Object.entries(r.options).map(([k, v]) =>
      `<tr class="${k === r.recommended ? "best" : ""}"><td>${esc(k)}</td><td>${v.avg_trip_min} min</td><td>${v.vehicles_reaching_impassable_road}</td></tr>`).join("")}</table>
      <p>Recommended: ${esc(r.recommended)}.</p><p class="muted small">${esc(r.model)}. Model output, not field data.</p>`;
  }
  if (kind === "signals") {
    const r = await RP.api(`/api/incidents/${id}/signals`);
    if (!r.junctions.length) return `<p class="muted">No junction on the detour gains enough traffic to retime.</p>`;
    return `<table><tr><th>Junction</th><th>Extra veh/h</th><th>Cycle</th><th>Green N-S / E-W</th></tr>${r.junctions.map((j) =>
      `<tr><td>${esc(j.junction)}</td><td>${j.extra_vehicles_per_hr}</td><td>${j.recommended_cycle_s} s</td><td>${j.recommended_green_s["North-South"]} / ${j.recommended_green_s["East-West"]} s</td></tr>`).join("")}</table>
      <p class="muted small">${esc(r.method)}. Advice only: RoadPulse controls no signal.</p>`;
  }
  const r = await RP.api(`/api/incidents/${id}/bulletin`);
  return `<p><b>English</b><br>${esc(r.english)}</p><p><b>Telugu</b><br>${esc(r.telugu)}</p><p class="muted small">${esc(r.note)}. Approve before sending.</p>`;
}

// ---------------------------------------------------------------- tools
$("btn-pw-pick").addEventListener("click", () => setPicking(!st.picking));
$("btn-pw").addEventListener("click", async () => {
  const msg = $("pw-msg"); msg.textContent = "Registering";
  const starts = nowS() + Number($("pw-start").value || 0) * 60;
  try {
    await RP.post("/api/planned-works", { title: $("pw-title").value.trim() || "Planned works", lat: st.worksPoint[0], lon: st.worksPoint[1],
      starts, ends: starts + Number($("pw-hours").value || 1) * 3600, full_closure: $("pw-full").checked });
    msg.textContent = "Registered. It appears on the public map when it starts."; st.worksPoint = null; drawPins();
  } catch (e) { msg.textContent = e.message; }
});
$("btn-rain").addEventListener("click", async () => {
  const v = $("rain-mm").value;
  try { await RP.post("/api/weather/override", { rain_mm_per_hr: v === "" ? null : Number(v) }); RP.toast(v === "" ? "Rain override cleared" : `Rain set to ${v} mm/h (manual override)`); }
  catch (e) { RP.toast(e.message, true); }
});
$("btn-eval").addEventListener("click", async () => {
  const out = $("eval-out"); out.innerHTML = '<p class="small muted">Running</p>';
  try {
    const r = await RP.api("/api/evaluation");
    out.innerHTML = `<div class="out"><table><tr><th></th><th>Without</th><th>With</th></tr>
      <tr><td>Average trip</td><td>${r.without_roadpulse.avg_trip_min} min</td><td>${r.with_roadpulse.avg_trip_min} min</td></tr>
      <tr><td>Sent into blocked roads</td><td>${r.without_roadpulse.vehicles_reaching_impassable_road}</td><td>${r.with_roadpulse.vehicles_reaching_impassable_road}</td></tr></table>
      <p>${r.vehicle_hours_saved} vehicle-hours saved (${r.pct_time_saved}%) over ${r.with_roadpulse.trips} simulated trips.</p><p class="muted small">${esc(r.model)}.</p></div>`;
  } catch (e) { out.innerHTML = `<p class="small">${esc(e.message)}</p>`; }
});

// ---------------------------------------------------------------- live
function onSnapshot(snap) {
  st.incidents = snap.incidents; st.hotspots = snap.hotspots.hotspots || []; st.rain = snap.hotspots; st.events = snap.events || [];
  RP.drawIncidents(Lg.incidents, st.incidents, { onClick: (i) => select(i.id, false),
    popup: (i) => `<b>${esc(RP.TYPE[i.type])}</b>${i.road ? `<br>${esc(i.road)}` : ""}` });
  RP.drawHotspots(Lg.watch, st.hotspots);
  drawSel();
  renderQueues();
  if (st.sel) {
    const i = st.incidents.find((x) => x.id === st.sel);
    if (!i) { /* cleared: keep the last detail but refresh it once */ if (st.detail && st.detail.status !== "Cleared") loadDetail(); }
    else if (!st.detail || i.updated !== st.detail.updated) loadDetail();
  }
}

if (RP.auth.token) showDesk(true);
