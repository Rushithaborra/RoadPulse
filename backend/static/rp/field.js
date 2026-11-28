/* RoadPulse field police app: check 2 of 2. Confirm a forwarded citizen report at the spot, or say it is not there. */
"use strict";

const { $, esc } = RP;
const fs = { tasks: [], incidents: [], me: "", open: null, here: null, miniMap: null, version: -1, loading: false, seen: null };
const SEE = [["Low", "Slow but passable", "Traffic moving"], ["Medium", "Partly blocked", "One lane open"],
  ["Severe", "Mostly blocked", "Only small gaps"], ["Closed", "Fully closed", "Nobody can pass"]];

// ---------------------------------------------------------------- sign in
RP.useSession("rp_field", () => show(false));
function show(on) {
  $("signin").hidden = on; $("app").hidden = !on;
  $("conn").hidden = !on; $("btn-signout").hidden = !on;
  if (on) {
    fs.me = sessionStorage.getItem("rp_field_who") || "";
    $("hello").textContent = "Your checks";
    RP.connect({ staff: true, onSnapshot });
    loadTasks();
  } else RP.disconnect();
}
$("signin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("signin-err"); err.textContent = ""; $("btn-signin").disabled = true;
  try {
    const r = await RP.post("/api/auth/login", { username: $("user").value.trim(), password: $("pass").value });
    if (r.role !== "field" && r.role !== "supervisor") { err.textContent = "This is a control room account. Sign in at /control instead."; return; }
    RP.setToken(r.token); sessionStorage.setItem("rp_field_who", $("user").value.trim());
    $("pass").value = ""; show(true);
  } catch (ex) { err.textContent = ex.message; } finally { $("btn-signin").disabled = false; }
});
$("btn-signout").addEventListener("click", () => { RP.setToken(null); closeTask(); show(false); });

// ---------------------------------------------------------------- location (optional, stays on this phone)
$("btn-locate").addEventListener("click", () => {
  if (!navigator.geolocation) { RP.toast("This browser cannot share location.", true); return; }
  $("btn-locate").textContent = "Finding you";
  navigator.geolocation.watchPosition((p) => { fs.here = [p.coords.latitude, p.coords.longitude]; renderList(); },
    () => { $("btn-locate").textContent = "Location not available"; }, { enableHighAccuracy: true, maximumAge: 15000 });
});
const dist = (i) => (fs.here ? RP.fmtDist(RP.haversine(fs.here, [i.lat, i.lon])) : "");

// ---------------------------------------------------------------- list
async function loadTasks() {
  if (fs.loading) return;
  fs.loading = true;
  try { fs.tasks = (await RP.api("/api/field/tasks")).tasks; } catch (e) { RP.toast(e.message, true); }
  fs.loading = false;
  renderList();
  if (fs.open) {
    const t = fs.tasks.find((x) => x.id === fs.open);
    if (!t) { RP.toast("This check was closed by the control room."); closeTask(); }
  }
}
function reportNotes(t) { return (t.signals || []).filter((s) => s.source === "report"); }
function renderList() {
  const n = fs.tasks.length;
  $("hello-sub").textContent = `Signed in as ${fs.me}. ` + (n ? `${n} report${n > 1 ? "s" : ""} from the control room to check.` : "");
  $("btn-locate").hidden = !n || !!fs.here;
  $("tasks").innerHTML = n ? fs.tasks.map((t) => {
    const urgent = t.review.urgent, c = t.review.checks.control || {};
    const notes = reportNotes(t), first = notes.find((s) => s.note);
    const style = urgent && t.severity !== "Closed" ? `background:${RP.SEV[t.severity]}` : "background:#23272c";
    return `<li class="task ${urgent ? "urgent" : ""}" data-id="${t.id}" tabindex="0" role="button">
      <div class="top"><span class="sign" style="${style}">${RP.GLYPH[t.type]}</span>
      <div><div class="what">${esc(RP.TYPE[t.type])}${urgent ? ", urgent" : ""}</div><div class="road">${esc(RP.roadLabel(t))}</div></div></div>
      ${first ? `<div class="line">Citizen: <b>“${esc(first.note)}”</b></div>` : ""}
      <div class="line">Sent ${RP.ago(c.at || t.updated)}${notes.length > 1 ? `, ${notes.length} citizen reports` : ""}${urgent ? ". Already on the public map" : ""}</div>
      ${fs.here ? `<span class="dist">${dist(t)}</span>` : ""}</li>`;
  }).join("") : `<li class="none"><b>Nothing to check right now.</b>New reports from the control room appear here by themselves.</li>`;

  const mine = fs.incidents.filter((i) => i.review.checks && i.review.checks.field && i.review.checks.field.by === fs.me);
  $("done").innerHTML = mine.length ? mine.map((i) => `<li>${esc(RP.TYPE[i.type])}${i.road ? ` on ${esc(i.road)}` : ""}:
    ${i.review.checks.field.decision === "confirmed" ? "confirmed" : "not found"} at ${RP.clock(i.review.checks.field.at)}</li>`).join("")
    : `<li class="none">Reports you confirm stay listed here while they are open.</li>`;
}
$("tasks").addEventListener("click", (e) => { const li = e.target.closest("li[data-id]"); if (li) openTask(li.dataset.id); });
$("tasks").addEventListener("keydown", (e) => { const li = e.target.closest("li[data-id]"); if (li && e.key === "Enter") openTask(li.dataset.id); });

// ---------------------------------------------------------------- one check
function openTask(id) {
  const t = fs.tasks.find((x) => x.id === id); if (!t) return;
  fs.open = id;
  $("list-view").hidden = true; $("task-view").hidden = false; window.scrollTo(0, 0);
  const urgent = t.review.urgent, c = t.review.checks.control || {};
  const reps = reportNotes(t);
  const sevPick = t.type !== "flood" ? `<div class="grp"><span class="field-label" style="font-weight:700;display:block;margin-bottom:8px">How bad is it?</span>
    <div class="seg">${SEE.map(([v, a, b], k) => `<input type="radio" name="see" id="see-${v}" value="${v}" ${k === 3 && t.severity === "Closed" ? "checked" : ""}><label for="see-${v}">${a}<span>${b}</span></label>`).join("")}</div></div>`
    : `<p class="hint" style="margin:-4px 0 12px">Water depth comes from the sensor where there is one, so you only confirm what you see.</p>`;
  $("task").innerHTML = `
    <div class="t-head ${urgent ? "urgent" : ""}"><h2>${esc(RP.TYPE[t.type])}</h2><div class="road">${esc(RP.roadLabel(t))}</div>
      <div class="tags">${urgent ? '<span class="tag urgent">Urgent, already public</span>' : '<span class="tag plain">Not public yet</span>'}${RP.sevTag(t.severity)}${RP.simTag(t.simulated)}</div></div>
    <div id="minimap" class="minimap"></div>
    <div class="nav-row"><span class="small muted">${t.lat.toFixed(5)}, ${t.lon.toFixed(5)}${fs.here ? `, ${dist(t)} from you` : ""}</span>
      <a class="btn" href="https://www.google.com/maps/dir/?api=1&destination=${t.lat},${t.lon}" target="_blank" rel="noopener">Directions</a></div>
    <div class="said"><h3>What was reported</h3><ul>
      ${reps.map((s) => `<li>${s.note ? `“${esc(s.note)}”` : "No description given"}<small>Citizen report at ${RP.clock(s.ts)} ${s.simulated ? "(simulated)" : ""}</small></li>`).join("")}
      ${c.by ? `<li class="ctrl">${c.note ? `“${esc(c.note)}”` : "Checked, no note"}<small>Control room, ${esc(c.by)}, ${RP.clock(c.at)}</small></li>` : ""}
    </ul></div>
    <div class="decide"><h3>Is it there?</h3>
      ${sevPick}
      <div class="grp"><label class="field" for="f-note">What you see <span class="muted">(optional)</span></label>
        <input id="f-note" type="text" maxlength="200" placeholder="${t.type === "flood" ? "Knee-deep near the culvert" : "Two cars, left lane blocked"}"></div>
      <button id="btn-confirm" class="primary big">Confirm: it is here</button>
      <div class="or"><button id="btn-notfound" class="danger big">Not found at this spot</button></div>
      <p class="hint">${urgent ? "This report is already public because the control room marked it urgent. If you cannot find it, the control room decides whether it stays."
        : "Confirming puts it on the public map and reroutes commuters. Not found closes the report."}</p>
    </div>`;
  if (fs.miniMap) { fs.miniMap.remove(); fs.miniMap = null; }
  fs.miniMap = RP.createMap("minimap", { zoomControl: true, attributionControl: true, dragging: true, scrollWheelZoom: false, layers: false });
  fs.miniMap.setView([t.lat, t.lon], 17);
  const lg = L.layerGroup().addTo(fs.miniMap);
  RP.drawIncidents(lg, [t]);
  if (fs.here) L.circleMarker(fs.here, { radius: 7, color: "#fff", weight: 2, fillColor: "#23272c", fillOpacity: 1 }).bindTooltip("You").addTo(lg);
  $("btn-confirm").addEventListener("click", () => decide("confirm"));
  $("btn-notfound").addEventListener("click", () => decide("not-found"));
}
async function decide(kind) {
  const id = fs.open; if (!id) return;
  const body = { note: $("f-note").value.trim() || null };
  const pick = document.querySelector('input[name="see"]:checked');
  if (kind === "confirm" && pick) body.severity = pick.value;
  if (kind === "not-found" && !confirm("Report that this is not at the spot?")) return;
  $("btn-confirm").disabled = $("btn-notfound").disabled = true;
  try {
    const r = await RP.post(`/api/field/incidents/${id}/${kind}`, body);
    RP.toast(kind === "confirm" ? "Confirmed. It is now on the public map." : r.review.stage === "field_disputed"
      ? "Sent back to the control room to decide." : "Closed as not found.");
    closeTask(); loadTasks();
  } catch (e) { RP.toast(e.message, true); $("btn-confirm").disabled = $("btn-notfound").disabled = false; }
}
function closeTask() {
  fs.open = null; $("task-view").hidden = true; $("list-view").hidden = false;
  if (fs.miniMap) { fs.miniMap.remove(); fs.miniMap = null; }
}
$("btn-back").addEventListener("click", closeTask);

// ---------------------------------------------------------------- live
function onSnapshot(snap) {
  fs.incidents = snap.incidents;
  const sig = snap.incidents.filter((i) => ["with_field", "urgent_awaiting_field"].includes(i.review.stage)).map((i) => `${i.id}:${i.updated}`).join("|");
  if (sig !== fs.version) {
    fs.version = sig;
    loadTasks().then(() => {
      if (fs.seen) for (const t of fs.tasks) if (!fs.seen.has(t.id)) RP.toast(`New check: ${RP.TYPE[t.type]}${t.road ? ` on ${t.road}` : ""}`);
      fs.seen = new Set(fs.tasks.map((t) => t.id));
    });
  } else renderList();
}

if (RP.auth.token) show(true);
