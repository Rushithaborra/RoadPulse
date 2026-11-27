"use strict";
// Shared pieces for the three RoadPulse apps (commuter, control room, traffic police): words, icons, map, API.
window.RP = (() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
                  sget(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }, sset(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch (e) { /* ignore */ } } };

  const PATHS = {
    warn: '<path d="M12 3 2 21h20L12 3z"/><path d="M12 10v5M12 18v.01"/>', drop: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
    cone: '<path d="M6 21h12M9 21 11 5h2l2 16M8 12h8M7.5 16.5h9"/>', stop: '<path d="M8 3h8l5 5v8l-5 5H8l-5-5V8l5-5z"/><path d="M9 9l6 6M15 9l-6 6"/>',
    slow: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', check: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
    pin: '<path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>', layers: '<path d="M12 3 3 8l9 5 9-5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>', x: '<path d="M6 6l12 12M18 6 6 18"/>', locate: '<circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/><circle cx="12" cy="12" r="8"/>',
    bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4l2-2z"/><path d="M10 21h4"/>', car: '<path d="M5 16l1.5-5h11L19 16M4 16h16v3H4z"/><circle cx="8" cy="19" r="1"/><circle cx="16" cy="19" r="1"/>',
    ambulance: '<path d="M3 17V7h11v10M14 10h4l3 3v4h-7"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/><path d="M8.5 9v4M6.5 11h4"/>',
    tow: '<path d="M2 17V9h10v8M12 12h5l3 3v2h-8"/><circle cx="6" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>', camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3z"/><path d="M9 12l2 2 4-4"/>', ban: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    road: '<path d="M8 3 5 21M16 3l3 18M12 4v3M12 10v4M12 17v3"/>', nav: '<path d="M12 3 4 20l8-4 8 4-8-17z"/>', lane: '<path d="M7 21V8M17 21V8M12 4v3M12 11v3M12 18v3"/>',
  };
  const icon = (n, s = 20) => `<svg viewBox="0 0 24 24" width="${s}" height="${s}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[n]}</svg>`;

  const KIND = { flood: { name: "Water on the road", short: "Water", ic: "drop" }, accident: { name: "Accident", short: "Accident", ic: "warn" },
    blockage: { name: "Road blocked", short: "Blocked", ic: "stop" }, construction: { name: "Road works", short: "Works", ic: "cone" },
    slowdown: { name: "Slow traffic", short: "Slow traffic", ic: "slow" } };
  // How serious it is, and what a driver should do about it.
  const SEV = {
    Low: { label: "Minor", color: "#d9a400", advice: "Small problem. You can carry on, expect a short delay.", note: "Small delay" },
    Medium: { label: "Slow", color: "#ef7d00", advice: "Traffic is slow here. Take another road only if you are in a hurry.", note: "Traffic is crawling" },
    Severe: { label: "Very slow", color: "#d92d20", advice: "Very slow or partly blocked. Take another road if you can.", note: "Partly blocked" },
    Closed: { label: "Blocked", color: "#7a1fa2", advice: "This road is blocked. You cannot get through. Take another road.", note: "Nobody can pass" },
  };
  const SEV_RANK = { Low: 0, Medium: 1, Severe: 2, Closed: 3 };
  const VEH = { two_wheeler: "two-wheelers", auto: "auto-rickshaws", car: "cars", ambulance: "ambulances", bus: "buses" };
  const meter = (sev) => `<span class="meter" style="--c:${SEV[sev].color}" role="img" aria-label="Seriousness: ${SEV[sev].label}">${[0, 1, 2, 3].map((n) => `<i class="${n <= SEV_RANK[sev] ? "on" : ""}"></i>`).join("")}</span>`;

  const published = (i) => i.published && ["Verified", "Responding"].includes(i.status);
  const road = (i) => i.road || "this road";
  const headline = (i) => (i.status === "Reported" && i.type !== "construction" ? `Possible ${KIND[i.type].name.toLowerCase()} on ${road(i)}` : `${KIND[i.type].name} on ${road(i)}`);
  function reason(i) {
    switch (i.type) {
      case "flood": return i.depth_cm != null ? `Water about ${Math.round(i.depth_cm)} cm deep is covering the road.` : "Water is covering the road.";
      case "accident": return "There has been an accident.";
      case "blockage": return "Something is blocking the road.";
      case "construction": return i.starts && !i.active ? `Road works are planned from ${clock(i.starts)}.` : `Road works are going on${i.ends ? ` until ${clock(i.ends)}` : ""}.`;
      default: return "Traffic has slowed down and we do not know why yet.";
    }
  }
  function vehicleNote(i) {
    if (i.type !== "flood" || !i.affected_vehicles || !i.affected_vehicles.length) return "";
    return i.affected_vehicles.length >= 5 ? " Nobody can pass safely." : ` Not safe for ${i.affected_vehicles.map((v) => VEH[v] || v).join(", ")}.`;
  }
  function evidence(i) {
    const o = i.signals_by_source || {}, out = [];
    if (o.report) out.push(`${o.report} driver report${o.report > 1 ? "s say" : " says"} there is a problem here`);
    if (o.probe) out.push("Traffic data: many vehicles here are moving very slowly");
    if (o.sensor) out.push(`Water sensor reads ${i.depth_cm != null ? Math.round(i.depth_cm) + " cm" : "water on the road"}`);
    if (o.cctv) out.push("A camera sees the problem");
    if (o.planned) out.push("Planned by the agency doing the work");
    if (o.field) out.push("Traffic police on the scene confirmed it");
    if (o.officer) out.push("A control-room officer confirmed it");
    return out;
  }

  let toastT;
  function toast(msg, ms = 4500) { const t = $("toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), ms); }

  // one token per app, kept in sessionStorage; every API call sends it
  const auth = { key: null, token: null, onExpire: () => {} };
  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    if (auth.token) headers.Authorization = `Bearer ${auth.token}`;
    let res;
    try { res = await fetch(path, { ...opts, headers }); } catch (e) { throw new Error("Cannot reach the RoadPulse server. Check your connection."); }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (res.status === 401 && auth.token && path !== "/api/auth/login") { auth.token = null; RP.store.sset(auth.key, null); auth.onExpire(); throw new Error("Your session ended. Please sign in again."); }
      if (res.status === 429) throw new Error("Too many requests. Wait a few seconds and try again.");
      throw new Error(typeof body.detail === "string" ? body.detail : `Something went wrong (code ${res.status}).`);
    }
    return body;
  }

  // ---- map with a Map / Satellite switch
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
  function makeMap(center, zoom = 16) {
    const map = L.map("map", { zoomControl: false, attributionControl: true });
    map.attributionControl.setPrefix(false);
    const street = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>' });
    const sat = L.layerGroup([
      L.tileLayer(`${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 19, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics" }),
      L.tileLayer(`${ESRI}/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 19, opacity: 0.9 }),
      L.tileLayer(`${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 19 })]);
    const layers = { inc: L.layerGroup().addTo(map), trip: L.layerGroup().addTo(map), pins: L.layerGroup().addTo(map) };
    const m = { map, layers, style: store.get("rp_style") || "map",
      setStyle(name) { m.style = name; map.removeLayer(street); map.removeLayer(sat); (name === "sat" ? sat : street).addTo(map); store.set("rp_style", name); },
      toggleStyle() { m.setStyle(m.style === "sat" ? "map" : "sat"); } };
    map.setView(center, zoom); m.setStyle(m.style);
    const labels = () => map.getContainer().classList.toggle("labels", map.getZoom() >= 17);
    map.on("zoomend", labels); labels();
    return m;
  }
  const pinIcon = (html) => L.divIcon({ className: "", html, iconSize: [0, 0] });
  function drawIncidents(layer, list, { selected, onClick, pendingTag = "needs a decision" } = {}) {
    layer.clearLayers();
    for (const i of list) {
      const s = SEV[i.severity], pending = i.status === "Reported";
      for (const seg of i.affected_segments || []) {
        L.polyline(seg, { color: "#fff", weight: 13, opacity: 0.9 }).addTo(layer);
        L.polyline(seg, { color: s.color, weight: 8, opacity: 0.95, dashArray: pending ? "6 8" : null }).addTo(layer);
      }
      const tag = pending ? `${KIND[i.type].short} · ${pendingTag}` : `${KIND[i.type].short} · ${s.label}`;
      L.marker([i.lat, i.lon], { icon: pinIcon(`<div class="pin ${pending ? "pending" : ""} ${selected === i.id ? "sel" : ""}" style="--c:${s.color}"><div class="dot2">${icon(KIND[i.type].ic, 20)}</div><div class="tag">${esc(tag)}</div></div>`) })
        .on("click", () => onClick && onClick(i.id)).addTo(layer);
    }
  }
  const pinHtml = (letter, color) => pinIcon(`<div class="ab" style="background:${color}">${letter}</div>`);

  // ---- bottom sheet with three heights (phones); a fixed side card on desktop
  function makeSheet(sheetEl, grabEl, onChange) {
    const H = [0.26, 0.5, 0.86]; let idx = 1;
    const phone = () => window.matchMedia("(max-width: 899px)").matches;
    function set(i) { idx = i; document.documentElement.style.setProperty("--sheet-h", phone() ? Math.round(innerHeight * H[i]) + "px" : "0px"); onChange && setTimeout(onChange, 260); }
    let y0 = 0, h0 = 0, moved = false;
    grabEl.addEventListener("pointerdown", (e) => { y0 = e.clientY; h0 = sheetEl.getBoundingClientRect().height; moved = false; grabEl.setPointerCapture(e.pointerId); sheetEl.style.transition = "none"; });
    grabEl.addEventListener("pointermove", (e) => { if (!grabEl.hasPointerCapture(e.pointerId)) return; const dy = y0 - e.clientY; if (Math.abs(dy) > 4) moved = true; document.documentElement.style.setProperty("--sheet-h", Math.max(110, Math.min(innerHeight * 0.92, h0 + dy)) + "px"); });
    grabEl.addEventListener("pointerup", () => { sheetEl.style.transition = ""; if (!moved) { set((idx + 1) % 3); return; } const f = sheetEl.getBoundingClientRect().height / innerHeight; set(H.reduce((b, v, k) => (Math.abs(v - f) < Math.abs(H[b] - f) ? k : b), 0)); });
    addEventListener("resize", () => set(idx)); set(1);
    return { set, get idx() { return idx; }, phone };
  }

  function bindLive(el, getState) {
    const draw = () => { const s = getState(), age = Math.round((Date.now() - s.lastOk) / 1000);
      el.className = "chip " + (s.connected ? "done" : "warnc"); el.textContent = s.connected ? (age < 5 ? "live" : `updated ${age}s ago`) : "offline"; };
    setInterval(draw, 1000); draw();
  }
  return { $, esc, clock, store, icon, KIND, SEV, SEV_RANK, VEH, meter, published, headline, reason, vehicleNote, evidence, toast, auth, api, makeMap, pinIcon, pinHtml, drawIncidents, makeSheet, bindLive };
})();
