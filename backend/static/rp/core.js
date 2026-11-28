/* RoadPulse shared client: API calls, live stream, map, incident drawing, legend.
   Used by the commuter app (app.js), the control room (control.js) and the field app (field.js).
   Nothing here invents data: everything drawn comes from the API. */
"use strict";

const RP = (() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const SEV = { Low: "#9a8c32", Medium: "#d9781a", Severe: "#c93a26", Closed: "#23272c" };
  const KERB = "#f5b700", ASPHALT = "#23272c";
  const TYPE = { flood: "Waterlogging", accident: "Accident", blockage: "Road blocked", construction: "Construction", slowdown: "Slow traffic" };
  const VEHICLE = { two_wheeler: "Two-wheeler", auto: "Auto", car: "Car", bus: "Bus", ambulance: "Ambulance" };
  // simple road-sign glyphs, drawn in currentColor
  const GLYPH = {
    flood: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M1 5c2-2 3 2 5 0s3 2 5 0 3 2 4 0M1 10c2-2 3 2 5 0s3 2 5 0 3 2 4 0"/></svg>',
    accident: '<svg viewBox="0 0 16 16" fill="currentColor"><rect x="6.6" y="1.5" width="2.8" height="8.5" rx="1"/><circle cx="8" cy="13.4" r="1.7"/></svg>',
    blockage: '<svg viewBox="0 0 16 16" fill="currentColor"><rect x="1.5" y="6.3" width="13" height="3.4" rx="1"/></svg>',
    construction: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 1.5 13.5 14h-11z"/></svg>',
    slowdown: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4l4 4-4 4M9 4l4 4-4 4"/></svg>',
  };
  // RTC Complex = APSRTC Dwaraka Bus Station, position from OpenStreetMap: a reference point only
  const RTC = { name: "RTC Complex", lat: 17.7238639, lon: 83.3068879 };

  const fmtDist = (m) => (m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`);
  const fmtTime = (s) => (s < 60 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`);
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  function ago(ts) {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min ago`;
  }
  function haversine(a, b) {
    const R = 6371000, rad = Math.PI / 180;
    const dLat = (b[0] - a[0]) * rad, dLon = (b[1] - a[1]) * rad;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  const pathLen = (c) => c.reduce((s, p, i) => (i ? s + haversine(c[i - 1], p) : 0), 0);

  function toast(msg, bad = false, ms = 4200) {
    let t = $("toast");
    if (!t) { t = document.createElement("div"); t.id = "toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
    t.className = "toast" + (bad ? " bad" : ""); t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, ms);
  }

  // ---------------------------------------------------------------- API
  const auth = { key: null, token: null, onExpired: null };
  function useSession(key, onExpired) {
    auth.key = key; auth.onExpired = onExpired;
    try { auth.token = sessionStorage.getItem(key); } catch (e) { auth.token = null; }
  }
  function setToken(t) {
    auth.token = t;
    try { t ? sessionStorage.setItem(auth.key, t) : sessionStorage.removeItem(auth.key); } catch (e) { /* private mode */ }
  }
  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    if (auth.token) headers.Authorization = `Bearer ${auth.token}`;
    let res;
    try { res = await fetch(path, { ...opts, headers }); } catch (e) {
      throw new Error("Cannot reach the RoadPulse server. Check that it is running.");
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (res.status === 401 && auth.token && path !== "/api/auth/login") {
        setToken(null); if (auth.onExpired) auth.onExpired();
        throw new Error("Your session ended. Sign in again.");
      }
      if (res.status === 429) throw new Error("Too many requests. Wait a few seconds and try again.");
      if (res.status >= 500) throw new Error(`Server error (request ${res.headers.get("X-Request-ID") || "?"}). Check the server log.`);
      throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (HTTP ${res.status})`);
    }
    return body;
  }
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

  // ---------------------------------------------------------------- live stream (Server-Sent Events)
  const STALE_AFTER_S = 35;   // the server sends a heartbeat about every 10 s
  const live = { es: null, connected: false, ever: false, lastHeard: 0, lostSince: 0, timer: null };
  function connect({ staff, onSnapshot }) {
    clearTimeout(live.timer);
    if (live.es) { live.es.close(); live.es = null; }
    (async () => {
      let url = "/api/stream";
      if (staff) {
        try { url += `?ticket=${encodeURIComponent((await post("/api/stream/ticket")).ticket)}`; } catch (e) {
          live.connected = false; live.lostSince = live.lostSince || Date.now();
          if (auth.token) live.timer = setTimeout(() => connect({ staff, onSnapshot }), 3000);
          return;
        }
      }
      const es = new EventSource(url); live.es = es;
      const heard = () => { live.connected = true; live.ever = true; live.lastHeard = Date.now(); live.lostSince = 0; renderConn(); };
      es.onopen = heard;
      es.addEventListener("heartbeat", heard);
      es.onmessage = (ev) => { heard(); try { onSnapshot(JSON.parse(ev.data)); } catch (err) { console.error(err); } };
      es.onerror = () => {
        live.connected = false; live.lostSince = live.lostSince || Date.now(); renderConn();
        // a staff ticket is single-use: on any drop, fetch a new one instead of letting the browser retry the old URL
        if (staff || es.readyState === EventSource.CLOSED) { es.close(); if (live.es === es) live.timer = setTimeout(() => connect({ staff, onSnapshot }), 2500); }
      };
    })();
  }
  function disconnect() { clearTimeout(live.timer); if (live.es) live.es.close(); live.es = null; live.connected = false; }
  let mapWarning = null;
  function renderConn() {
    const c = $("conn"); if (!c) return;
    const age = Math.round((Date.now() - live.lastHeard) / 1000);
    if (!live.connected) { c.className = "conn down"; c.textContent = live.ever ? "Reconnecting" : "Connecting"; }
    else if (age > STALE_AFTER_S) { c.className = "conn stale"; c.textContent = `No update for ${age} s`; }
    else { c.className = "conn live"; c.textContent = "Live"; }
    const b = $("banner"); if (!b) return;
    const lost = !live.connected && live.lostSince && Date.now() - live.lostSince > 4000;
    const stale = live.connected && age > STALE_AFTER_S;
    const msgs = [];
    if (mapWarning) msgs.push(`Map warning: ${mapWarning}`);
    if (lost) msgs.push("Live updates lost. What you see may be out of date; reconnecting by itself.");
    else if (stale) msgs.push(`No update from the server for ${age} s. What you see may be out of date.`);
    b.hidden = !msgs.length; b.className = "banner" + (mapWarning || lost ? "" : " warn"); b.textContent = msgs.join("   ");
  }
  setInterval(renderConn, 1000);

  // ---------------------------------------------------------------- map
  // Base maps: full-colour street map (default), satellite, and a muted street map that makes incidents stand out.
  const BASES = {
    "Street map": () => L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> (ODbL)' }),
    "Satellite": () => L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19,
      attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics. Roads &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>' }),
    "Muted": () => L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, className: "muted-tiles",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> (ODbL)' }),
  };
  function createMap(el, opts = {}) {
    const { layers: withLayers = true, ...mapOpts } = opts;
    const map = L.map(el, { zoomControl: true, preferCanvas: true, ...mapOpts });
    map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
    const bases = Object.fromEntries(Object.entries(BASES).map(([k, f]) => [k, f()]));
    let pick = "Street map";
    try { pick = localStorage.getItem("rp_base") || pick; } catch (e) { /* storage off */ }
    (bases[pick] || bases["Street map"]).addTo(map);
    map._rpBase = bases[pick] ? pick : "Street map";
    if (withLayers) {
      map._rpLayers = L.control.layers(bases, {}, { position: "topright", collapsed: true }).addTo(map);
      L.control.scale({ imperial: false, position: "bottomright" }).addTo(map);
    }
    map.on("baselayerchange", (e) => {
      map._rpBase = e.name;
      try { localStorage.setItem("rp_base", e.name); } catch (err) { /* storage off */ }
      if (map._rpNet) styleNetwork(map);
    });
    return map;
  }
  // routable roads: faint by default, highlighted with their name on hover
  function netStyle(map, s) {
    const sat = map._rpBase === "Satellite", main = s.speed_kph >= 40;
    return { color: sat ? "#ffffff" : ASPHALT, weight: main ? 3 : 1.8, opacity: sat ? (main ? 0.7 : 0.45) : (main ? 0.3 : 0.18) };
  }
  function styleNetwork(map) { for (const [line, s] of map._rpNet) line.setStyle(netStyle(map, s)); }
  function overlay(mapEl, html) {
    let o = mapEl.querySelector(".map-overlay");
    if (html == null) { if (o) o.remove(); return; }
    if (!o) { o = document.createElement("div"); o.className = "map-overlay"; mapEl.appendChild(o); }
    o.innerHTML = `<div>${html}</div>`;
    return o;
  }
  async function loadNetwork(map, layer, { places = true } = {}) {
    const el = map.getContainer();
    overlay(el, "Loading the road network");
    let net;
    try { net = await api("/api/network"); } catch (e) {
      const o = overlay(el, `<h3>The road network did not load</h3><p class="small muted" style="margin:6px 0 12px">${esc(e.message)}</p><button class="dark">Try again</button>`);
      await new Promise((res) => o.querySelector("button").addEventListener("click", res, { once: true }));
      return loadNetwork(map, layer, { places });
    }
    // The road data covers one pilot area. Outline it, keep the map near it, and offer a way back.
    const b = L.latLngBounds([]);
    for (const s of net.segments) for (const c of s.coords) b.extend(c);
    pilot.bounds = b.pad(0.02);
    map.fitBounds(pilot.bounds, { padding: [10, 10] });
    if (map.getZoom() < 15) map.setView(net.center, 15);
    map.setMinZoom(14);
    map.setMaxBounds(pilot.bounds.pad(0.8));
    L.rectangle(pilot.bounds, { color: ASPHALT, weight: 1.5, dashArray: "6 6", fill: false, interactive: false }).addTo(layer);
    backButton(map);
    mapWarning = net.map_warning || null;
    const mm = $("mapmode");
    if (mm) {
      mm.hidden = false;
      if (net.map_mode === "synthetic") { mm.className = "mapmode bad"; mm.textContent = "Synthetic grid, not real roads"; }
      else { mm.className = "mapmode"; mm.textContent = "OpenStreetMap roads"; mm.title = "Real road network loaded from data/area.graphml"; }
    }
    layer.clearLayers();
    map._rpNet = [];
    const hoverOn = (e) => { e.target.setStyle({ color: KERB, weight: 6, opacity: 1 }); };
    const hoverOff = (e) => { e.target.setStyle(netStyle(map, e.target._rpSeg)); };
    for (const s of net.segments) {
      const line = L.polyline(s.coords, { ...netStyle(map, s), interactive: true });
      line._rpSeg = s;
      line.bindTooltip(`<b>${esc(s.name || "Unnamed lane")}</b><br>${Math.round(s.speed_kph)} km/h assumed speed${s.oneway ? ", one-way" : ""}`, { sticky: true, direction: "top", offset: [0, -6] });
      line.on("mouseover", hoverOn).on("mouseout", hoverOff);
      line.addTo(layer); map._rpNet.push([line, s]);
    }
    if (map._rpLayers && !map._rpNetAdded) { map._rpLayers.addOverlay(layer, "RoadPulse road network"); map._rpNetAdded = true; }
    if (places && net.map_mode === "osm") {
      for (const m of [{ name: "Dwaraka Nagar", lat: net.center[0], lon: net.center[1] }, RTC]) {
        L.circleMarker([m.lat, m.lon], { radius: 3.5, color: ASPHALT, fillColor: ASPHALT, fillOpacity: 1, weight: 1, interactive: false })
          .bindTooltip(m.name, { permanent: true, direction: "right", offset: [6, 0], className: "place", interactive: false }).addTo(layer);
      }
    }
    overlay(el, null); renderConn();
    return net;
  }

  const pilot = { bounds: null };
  const inPilot = (latlng) => !pilot.bounds || pilot.bounds.contains(latlng);
  function backButton(map) {
    if (map._rpBack) return;
    const c = L.control({ position: "topright" });
    c.onAdd = () => {
      const d = L.DomUtil.create("button", "dark sm backhome");
      d.textContent = "Back to the pilot area"; d.hidden = true;
      L.DomEvent.disableClickPropagation(d);
      d.addEventListener("click", () => map.fitBounds(pilot.bounds));
      const check = () => { d.hidden = !pilot.bounds || map.getBounds().intersects(pilot.bounds) && pilot.bounds.contains(map.getCenter()); };
      map.on("moveend", check);
      return d;
    };
    c.addTo(map); map._rpBack = c;
  }
  const OUTSIDE = "RoadPulse has road data for Dwaraka Nagar and the RTC Complex only. Tap inside the dashed outline.";

  const signIcon = (inc) => L.divIcon({
    className: "", iconSize: [28, 28], iconAnchor: [14, 14],
    html: `<div class="sign ${inc.published ? inc.severity : "pending"} ${inc.review && inc.review.urgent ? "urgent" : ""}" style="${inc.published && inc.severity !== "Closed" ? `background:${SEV[inc.severity]}` : ""}">${GLYPH[inc.type] || "!"}</div>`,
  });
  const pinIcon = (cls, t) => L.divIcon({ className: "", iconSize: [30, 30], iconAnchor: [15, 30], html: `<div class="pin ${cls}"><b>${t}</b></div>` });

  /* Affected roads. Verified: coloured by severity; Closed: asphalt with a kerb-yellow dash (barrier striping).
     Not yet verified (staff maps only): thin dashed outline. blockedFor: overlay roads blocked for that vehicle. */
  function drawIncidents(layer, incidents, { blockedFor = null, onClick = null, popup = null } = {}) {
    layer.clearLayers();
    for (const inc of incidents) {
      if (inc.status === "Cleared" || !inc.active) continue;
      for (const seg of inc.affected_segments) {
        if (!inc.published) {
          L.polyline(seg, { color: ASPHALT, weight: 5, opacity: 0.55, dashArray: "2 8", lineCap: "round", interactive: false }).addTo(layer);
          continue;
        }
        if (inc.severity === "Closed") {
          L.polyline(seg, { color: ASPHALT, weight: 9, opacity: 0.95, interactive: false }).addTo(layer);
          L.polyline(seg, { color: KERB, weight: 5, dashArray: "8 8", lineCap: "butt", interactive: false }).addTo(layer);
        } else {
          L.polyline(seg, { color: SEV[inc.severity], weight: 8, opacity: 0.85, interactive: false }).addTo(layer);
          if (blockedFor && inc.affected_vehicles.includes(blockedFor)) {
            L.polyline(seg, { color: "#fff", weight: 2.5, dashArray: "1 6", lineCap: "round", interactive: false }).addTo(layer);
          }
        }
      }
      const m = L.marker([inc.lat, inc.lon], { icon: signIcon(inc), zIndexOffset: inc.published ? 600 : 400, keyboard: true, title: inc.title });
      if (popup) m.bindPopup(popup(inc));
      if (onClick) m.on("click", () => onClick(inc));
      m.addTo(layer);
    }
  }

  function drawHotspots(layer, hotspots) {
    layer.clearLayers();
    for (const h of hotspots) {
      const color = h.risk === "high" ? SEV.Severe : h.risk === "watch" ? SEV.Medium : ASPHALT;
      L.circle([h.lat, h.lon], { radius: 38, color, weight: 1.5, dashArray: "3 4", fillOpacity: 0.06 })
        .bindTooltip(`${h.simulated_location ? "Simulated flood-watch point<br>" : ""}<b>${esc(placeName(h.name).name)}</b><br>${depth(h.depth_cm)}, ${trend(h.rise_cm_per_hr)}`).addTo(layer);
    }
  }

  /* Legend: every colour and symbol the map uses. sections chooses which groups this app needs. */
  function legend(map, sections) {
    const ln = (style) => `<span class="sw"><span class="ln" style="${style}"></span></span>`;
    const sign = (t, extra = "", cls = "") => `<span class="sw"><span class="sign ${cls}" style="width:22px;height:22px;${extra}">${GLYPH[t]}</span></span>`;
    const parts = {
      roads: `<h4>Affected roads</h4><ul>
        <li>${ln(`background:${SEV.Low}`)}Low: slow but passable</li>
        <li>${ln(`background:${SEV.Medium}`)}Medium: two-wheelers and autos avoid</li>
        <li>${ln(`background:${SEV.Severe}`)}Severe: most light vehicles avoid</li>
        <li>${ln(`background:repeating-linear-gradient(90deg,${KERB} 0 6px,${ASPHALT} 6px 12px);height:6px`)}Closed to all traffic</li></ul>`,
      blocked: `<ul><li>${ln(`background:${SEV.Severe};background-image:radial-gradient(circle,#fff 1.2px,transparent 1.6px);background-size:6px 5px`)}Blocked for the vehicle you picked</li></ul>`,
      pending: `<ul><li>${ln(`background:repeating-linear-gradient(90deg,${ASPHALT} 0 2px,transparent 2px 8px);height:4px`)}Awaiting verification (staff only)</li></ul>`,
      signs: `<h4>Signs</h4><ul>
        <li>${sign("flood", `background:${SEV.Severe}`)}Waterlogging</li><li>${sign("accident", `background:${SEV.Severe}`)}Accident</li>
        <li>${sign("blockage", `background:${SEV.Severe}`)}Road blocked</li><li>${sign("construction", `background:${SEV.Medium}`)}Construction</li>
        <li>${sign("accident", `background:${SEV.Severe};outline:3px solid ${KERB}`)}Marked urgent by the control room</li></ul>`,
      routes: `<h4>Routes</h4><ul>
        <li>${ln(`background:${ASPHALT};height:6px`)}Recommended route</li>
        <li>${ln(`background:#8d939a`)}Alternative</li>
        <li>${ln(`background:repeating-linear-gradient(90deg,${SEV.Severe} 0 5px,transparent 5px 9px);height:4px`)}Your usual route, affected</li>
        <li><span class="sw"><span class="pin a" style="width:18px;height:18px"></span></span>Start <span class="sw"><span class="pin b" style="width:18px;height:18px"></span></span>Destination</li></ul>`,
      watch: `<ul><li><span class="sw"><span style="width:18px;height:18px;border-radius:50%;border:1.5px dashed ${SEV.Medium}"></span></span>Flood-watch point (simulated)</li></ul>`,
    };
    const c = L.control({ position: "bottomleft" });
    c.onAdd = () => {
      const d = L.DomUtil.create("details", "legend");
      d.innerHTML = `<summary>Map legend</summary><div class="body">${sections.map((s) => parts[s]).join("")}</div>`;
      L.DomEvent.disableClickPropagation(d); L.DomEvent.disableScrollPropagation(d);
      return d;
    };
    c.addTo(map);
  }

  // ---------------------------------------------------------------- the two-check review, shared wording
  const STEP_NAMES = ["Reported", "Control room", "Field police", "On the map"];
  /* returns [{name, cls}] for the 4 steps. stage = review.stage from the API (or stage_code) */
  function reviewSteps(stage) {
    const s = (cls) => cls;
    const map = {
      control_review: ["done", "now", "", ""],
      with_field: ["done", "done", "now", ""],
      urgent_awaiting_field: ["done", "done", "now", "done"],
      field_disputed: ["done", "done", "fail", "done"],
      confirmed: ["done", "done", "done", "done"],
      trusted_evidence: ["done", "skip", "skip", "done"],
      rejected: ["done", "fail", "", ""],
      closed: ["done", "done", "done", "done"],
    }[stage] || ["done", "", "", ""];
    return STEP_NAMES.map((name, i) => ({ name: stage === "trusted_evidence" && (i === 1 || i === 2) ? (i === 1 ? "Sensor evidence" : "No field check needed") : name, cls: s(map[i]) }));
  }
  const stepsHtml = (stage) => `<div class="steps">${reviewSteps(stage).map((x) => `<div class="${x.cls}">${esc(x.name)}</div>`).join("")}</div>`;

  // backend names read "Demo hotspot A (simulated) · 4 th Lane Dwaraka nagar": show name and road on their own lines
  function placeName(n) { const [name, ...rest] = String(n || "").split(/\s*·\s*/); return { name, road: rest.join(", ") }; }
  function trend(cmPerHr) {
    if (Math.abs(cmPerHr) < 1) return "steady";
    return cmPerHr > 0 ? `rising ${Math.round(cmPerHr)} cm/h` : `falling ${Math.round(-cmPerHr)} cm/h`;
  }
  // where is it? the road's name from OpenStreetMap, or for an unnamed lane the distance from a landmark
  const roadLabel = (i) => i.road || `Unnamed lane, ${fmtDist(haversine([i.lat, i.lon], [RTC.lat, RTC.lon]))} from ${RTC.name}`;
  const depth = (cm) => (cm < 1 ? "Dry" : `${Math.round(cm)} cm of water`);

  const sevTag = (sev) => sev === "Closed" ? `<span class="tag sev-Closed"><span>Closed</span></span>` : `<span class="tag sev-${sev}">${esc(sev)}</span>`;
  const simTag = (on) => (on ? `<span class="tag sim" title="Sent by the demo simulator, not a real sensor or person">Simulated</span>` : "");

  return { $, esc, SEV, TYPE, VEHICLE, GLYPH, RTC, fmtDist, fmtTime, clock, ago, haversine, pathLen, toast,
    useSession, setToken, auth, api, post, connect, disconnect, live, renderConn,
    createMap, loadNetwork, overlay, drawIncidents, drawHotspots, legend, pinIcon, signIcon,
    reviewSteps, stepsHtml, sevTag, simTag, placeName, trend, depth, roadLabel, inPilot, OUTSIDE };
})();
