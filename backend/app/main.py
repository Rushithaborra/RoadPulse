"""RoadPulse API: one FastAPI app (modular monolith). Run: uvicorn app.main:app --app-dir backend"""
from __future__ import annotations

import asyncio
import json
import threading
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import alerts, config, evaluate, field, forecast, fusion, routing, services
from .geo import haversine_m
from .db import Database
from .models import (CitizenReport, Dispatch, Severity, Incident, PlannedWork, PositionIn, ProbeSample, RainOverride,
                     RouteRequest, SensorReading, Signal, SubscriberIn, TripStart, now)
from .models import AssignIn, CallStatusIn, FieldReportIn, PlacesIn, UnitPositionIn
from .roadgraph import RoadGraph
from .routing import incident_effects, route_alternatives
from .security import (DEVICE_LIMIT, EVAL_LIMIT, REPORT_LIMIT, ROUTE_LIMIT, SUBSCRIBE_LIMIT, consume_stream_ticket, issue_stream_ticket,
                       issue_token, limit, optional_officer, police_units, require_device, require_officer, require_police)
from .store import store

log = logging.getLogger("roadpulse")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


class State:
    rg: RoadGraph
    db: Database
    version: int = 0          # bumps on every change; SSE clients re-send when it moves
    saved: dict[str, float] = {}


S = State()


def bump():
    S.version += 1


_persist_lock = threading.Lock()


def persist():
    """Save every incident that changed since it was last saved (serialised: called from requests and the loop)."""
    with _persist_lock:
        for inc in store.snapshot():
            if S.saved.get(inc.id) != inc.updated:
                with store.lock:
                    S.db.save_incident(inc)  # serialise under the store lock so the JSON is consistent
                S.saved[inc.id] = inc.updated


def _on_status(inc: Incident, status: str, ts: float, reason: str | None, actor: str):
    S.db.status_change(inc.id, status, ts, reason, actor)
    try:
        alerts.on_status(S.rg, store, inc, status, ts)  # warn (or clear) the people this incident affects
    except Exception:
        log.exception("alert delivery failed for %s", inc.id)
    bump()


_sub_saved: dict[str, str] = {}


def persist_subscribers():
    """Save subscribers whose data changed (saved places, what they were told). Serialised like incidents."""
    with _persist_lock:
        for sub in list(store.subscribers.values()):
            doc = json.dumps(sub.doc(), sort_keys=True)
            if _sub_saved.get(sub.id) != doc:
                S.db.save_subscriber(sub.doc(), now())
                _sub_saved[sub.id] = doc


async def lifecycle_loop():
    last_retention = 0.0
    while True:
        try:
            await asyncio.to_thread(services.tick, store, S.rg)
            await asyncio.to_thread(alerts.tick, S.rg, store)
            await asyncio.to_thread(persist)
            await asyncio.to_thread(persist_subscribers)
            if any(i.status != "Cleared" for i in store.snapshot()):
                bump()  # timers and auto-clear countdowns move even without new input
            if time.time() - last_retention > 3600:
                await asyncio.to_thread(S.db.apply_retention, time.time())
                last_retention = time.time()
        except Exception:
            log.exception("lifecycle tick failed")
        await asyncio.sleep(config.TICK_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.rg = RoadGraph.load()
    if S.rg.warning:
        log.warning("ROAD GRAPH FALLBACK: %s", S.rg.warning)
    if S.rg.mode == "osm":
        config.CENTER_LAT, config.CENTER_LON = S.rg.centre()
        store.reset()  # re-place hotspots relative to the real map centre if no hotspots file
        log.info("OSM road graph: %s", S.rg.stats())
        if store.hotspot_source == "synthetic":
            log.warning("No data/hotspots.json: demo hotspots are SIMULATED points placed on real road segments, "
                        "not known flood locations.")
    store.place_demo_hotspots(S.rg)
    S.db = Database()
    for inc in S.db.load_open_incidents():
        store.incidents[inc.id] = inc
        S.saved[inc.id] = inc.updated
    _sub_saved.clear()
    for doc in S.db.load_subscribers():
        sub = alerts.Subscriber.from_doc(doc)
        store.subscribers[sub.id] = sub
        _sub_saved[sub.id] = json.dumps(sub.doc(), sort_keys=True)
    fusion.STATUS_HOOKS.append(_on_status)
    log.info("graph=%s nodes=%d edges=%d reloaded_incidents=%d", S.rg.source, S.rg.g.number_of_nodes(),
             S.rg.g.number_of_edges(), len(store.incidents))
    task = asyncio.create_task(lifecycle_loop())
    yield
    task.cancel()
    try:
        await task  # let an in-flight tick finish before the database is closed
    except asyncio.CancelledError:
        pass
    fusion.STATUS_HOOKS.remove(_on_status)
    S.db.close()


# The interactive API reference (/docs, /openapi.json) is on by default; set API_DOCS=0 to hide it on a public server.
_docs_on = os.getenv("API_DOCS", "1") != "0"
app = FastAPI(title="RoadPulse API", version="0.1.0", lifespan=lifespan,
              docs_url="/docs" if _docs_on else None, redoc_url=None, openapi_url="/openapi.json" if _docs_on else None)


from fastapi.exceptions import RequestValidationError  # noqa: E402


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Readable 422s. Raw input values are not echoed back: they may be huge, or non-finite (NaN/Infinity)
    and impossible to encode as JSON, which made FastAPI's default handler fail with a 500."""
    errors = [{"field": ".".join(str(p) for p in e.get("loc", ()) if p != "body"), "message": e.get("msg", "invalid")}
              for e in exc.errors()]
    return JSONResponse({"detail": "; ".join(f"{e['field']}: {e['message']}" for e in errors), "errors": errors},
                        status_code=422)


@app.middleware("http")
async def request_log(request: Request, call_next):
    rid = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        log.exception("rid=%s unhandled error on %s %s", rid, request.method, request.url.path)
        return JSONResponse({"error": "internal error", "request_id": rid}, status_code=500)
    if request.url.path.startswith("/api/") and request.url.path != "/api/stream":
        log.info("rid=%s %s %s %d %.0fms", rid, request.method, request.url.path, resp.status_code,
                 (time.perf_counter() - t0) * 1000)
    resp.headers["X-Request-ID"] = rid
    p = request.url.path
    if p in ("/", "/classic", "/commute", "/board", "/control", "/police") or p.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"  # revalidate (ETag) so a redeployed UI is never served stale
    elif p.startswith(("/api/subscribers", "/api/auth", "/api/police", "/api/stream/ticket", "/api/audit")):
        resp.headers["Cache-Control"] = "no-store"  # private data and tokens must not sit in any browser or proxy cache
    # Safe-by-default headers that cannot break the apps (no script or style restrictions, so the map tiles and the
    # inline page scripts keep working). Referrer stays on the default: OpenStreetMap's tile policy needs a Referer.
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


# ---------------------------------------------------------------- serialisation

def incident_view(inc: Incident, detail: bool = False) -> dict:
    ts = now()
    by_source: dict[str, int] = {}
    for s in inc.signals:
        by_source[s.source] = by_source.get(s.source, 0) + 1
    order = ["Reported", "Verified", "Responding", "Cleared"]
    times = inc.status_times
    durations = {}
    for a, b in zip(order, order[1:]):
        if a in times:
            durations[f"{a}->{b}"] = round((times.get(b, ts) - times[a]) / 60, 1)
    d = {
        "id": inc.id, "type": inc.type, "title": inc.title, "lat": inc.lat, "lon": inc.lon,
        "severity": inc.severity, "confidence": inc.confidence, "status": inc.status,
        "depth_cm": inc.depth_cm, "affected_vehicles": inc.affected_vehicles, "hotspot_id": inc.hotspot_id,
        "published": services.is_published(inc, ts), "active": services.is_active(inc, ts),
        "starts": inc.starts, "ends": inc.ends,
        "status_times": times, "stage_minutes": durations, "signals_by_source": by_source,
        "clearing_in_s": (max(0, round(config.CLEAR_WINDOW_S - (ts - inc.clear_candidate_since)))
                          if inc.clear_candidate_since else None),
        "cleared_reason": inc.cleared_reason, "updated": inc.updated,
        "simulated": any(s.simulated for s in inc.signals),
        "needs_field": field.needs_field(inc), "field_state": field.field_state(inc), "urgent": inc.urgent,
        "field_severity": inc.field_severity,
        "people_warned": sum(1 for a in store.alert_log if a["incident_id"] == inc.id and a["kind"] != "clear"),
        "road": S.rg.road_name(inc.lat, inc.lon),
        "affected_segments": [S.rg.edge_geo(u, v) for u, v in
                              S.rg.edges_near(inc.lat, inc.lon, config.INCIDENT_EDGE_RADIUS_M) if str(u) < str(v)],
    }
    if detail:
        d["signals"] = [s.model_dump() for s in inc.signals[-20:]]
        # who was warned is deliberately anonymous: when, what kind of message and whether Telegram carried it
        d["alert_history"] = [{"ts": a["ts"], "kind": a["kind"], "telegram": a["telegram"]}
                              for a in reversed([a for a in store.alert_log if a["incident_id"] == inc.id][:50])]
        d["dispatched"] = inc.dispatched
        d["assignments"] = inc.assignments
        d["field_reports"] = inc.field_reports
        d["requests"] = [r for r in inc.requests if not r.get("done")]
    return d


def hotspots_view() -> dict:
    rain, src = forecast.current_rain(store)
    return {"rain_mm_per_hr": rain, "rain_source": src,
            "hotspots": [forecast.countdown(h, rain) | {"sensor_id": h.sensor_id,
                         "last_reading_age_s": round(now() - h.last_reading) if h.last_reading else None,
                         "last_reading_simulated": h.last_simulated,
                         "simulated_location": h.simulated_location}
                         for h in store.hotspots.values()]}


def active_published() -> list[Incident]:
    ts = now()
    return [i for i in store.snapshot() if services.is_published(i, ts)]


def _get(incident_id: str) -> Incident:
    inc = store.incidents.get(incident_id)
    if not inc:
        raise HTTPException(404, "Incident not found")
    return inc


def require_on_network(lat: float, lon: float, what: str):
    """Reject points that are not on (or near) the mapped road network instead of silently snapping them."""
    d = S.rg.distance_to_network(lat, lon)
    if d > config.MAX_SNAP_M:
        far = f"{d:.0f} m" if d < 1000 else f"{d / 1000:.1f} km"
        raise HTTPException(422, f"{what} is {far} from the nearest mapped road (limit {config.MAX_SNAP_M:.0f} m). "
                                 f"Pick a point on or near a road inside the map area.")


def _changed(*incs: Incident):
    persist()
    bump()


# ---------------------------------------------------------------- public endpoints

@app.get("/api/health")
def health():
    return {"ok": True, "graph": S.rg.source, "map_mode": S.rg.mode, "hotspots_source": store.hotspot_source,
            "routing": "road-segment", "map_warning": S.rg.warning,
            "nodes": S.rg.g.number_of_nodes(), "edges": S.rg.g.number_of_edges(),
            "incidents_open": sum(1 for i in store.snapshot() if i.status != "Cleared"),
            "center": [config.CENTER_LAT, config.CENTER_LON]}


@app.get("/api/network")
def network():
    segs = []
    for u, v, d in S.rg.g.edges(data=True):
        if str(u) < str(v) or not S.rg.g.has_edge(v, u):
            segs.append({"coords": S.rg.edge_geo(u, v), "speed_kph": d["speed_kph"], "lanes": d["lanes"]})
    return {"source": S.rg.source, "map_mode": S.rg.mode, "map_warning": S.rg.warning,
            "hotspots_source": store.hotspot_source,
            "center": [config.CENTER_LAT, config.CENTER_LON], "segments": segs}


@app.get("/api/incidents")
def list_incidents(scope: str = Query("public", pattern="^(public|all)$"), include_cleared: bool = False,
                   officer=Depends(optional_officer)):
    if scope == "all" and not officer:
        raise HTTPException(401, "Officer login required for scope=all")
    ts = now()
    items = []
    for inc in sorted(store.snapshot(), key=lambda i: i.updated, reverse=True):
        if scope == "public" and not services.is_published(inc, ts):
            continue
        if not include_cleared and inc.status == "Cleared":
            continue
        items.append(incident_view(inc))
    return {"incidents": items}


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str, officer=Depends(optional_officer)):
    inc = _get(incident_id)
    if not officer and not services.is_published(inc, now()):
        raise HTTPException(404, "Incident not found")
    return incident_view(inc, detail=bool(officer))


@app.post("/api/reports", status_code=201)
def post_report(r: CitizenReport, request: Request):
    limit(REPORT_LIMIT, "report", request)
    require_on_network(r.lat, r.lon, "Report location")
    inc = services.handle_report(store, r, S.rg.road_name(r.lat, r.lon))
    S.db.report(r.type, r.lat, r.lon, r.reporter_id, r.note, now())
    _changed(inc)
    return {"incident_id": inc.id, "status": inc.status, "confidence": inc.confidence,
            "message": "Thanks, your report was received" if inc.status == "Reported"
            else "Thanks, this matches a verified incident"}


@app.get("/api/reports/{incident_id}/status")
def report_status(incident_id: str):
    """What happened to a report: the reporter keeps the incident id from POST /api/reports. Only a coarse outcome
    is returned (no evidence, no location), so unpublished incidents are not exposed."""
    inc = store.incidents.get(incident_id)
    if not inc:
        raise HTTPException(404, "Report not found (it may be too old)")
    published = services.is_published(inc, now())
    if inc.status == "Cleared":
        outcome = "rejected" if (inc.cleared_reason or "").startswith("rejected") else "resolved"
    else:
        outcome = "published" if published else "pending"
    return {"incident_id": inc.id, "outcome": outcome, "status": inc.status, "published": published}


@app.post("/api/sensors/readings")
def post_reading(r: SensorReading, request: Request, device=Depends(require_device)):
    limit(DEVICE_LIMIT, "device", request)
    res = services.handle_sensor(store, r)
    if not res.get("ok"):
        raise HTTPException(404, res.get("error"))
    S.db.sensor_reading(r.sensor_id, r.depth_cm, r.ts or now())
    _changed()
    return res


@app.post("/api/probes")
def post_probe(p: ProbeSample, request: Request, device=Depends(require_device)):
    limit(DEVICE_LIMIT, "device", request)
    res = services.handle_probe(store, S.rg, p)
    if res.get("edge") is None:
        raise HTTPException(422, "Probe position is not on a mapped road (more than 40 m from any segment)")
    if res.get("incident"):
        _changed()
    return res


@app.post("/api/routes")
def post_routes(req: RouteRequest, request: Request):
    limit(ROUTE_LIMIT, "route", request)
    ts = now()
    # validate against the nearest road SEGMENT (a point on a long road is valid even if junctions are far),
    # then compute the path from the nearest graph node
    require_on_network(req.from_lat, req.from_lon, "Start")
    require_on_network(req.to_lat, req.to_lon, "Destination")
    # exact road-segment routing: both ends are inserted on their nearest road segment in a per-request copy
    g, src, dst, src_pt, dst_pt = routing.graph_with_endpoints(S.rg, req.from_lat, req.from_lon,
                                                               req.to_lat, req.to_lon)
    if src is None or dst is None:
        raise HTTPException(422, "Start or destination is not on a mapped road")
    if src == dst or haversine_m(*src_pt, *dst_pt) < 10:
        raise HTTPException(422, "Start and destination are on the same spot of the road; pick points further apart")
    published = active_published()
    effects = incident_effects(S.rg, published)
    speeds = {e: services.observed_speed(store, e, ts) for e in effects}
    routes = route_alternatives(S.rg, src, dst, req.vehicle, effects, speeds, k=max(1, min(req.alternatives, 4)), g=g)
    # the route a driver would take without RoadPulse (fastest legal route, incidents unknown)
    import networkx as nx
    try:
        base = nx.shortest_path(g, src, dst, weight=lambda u, v, d: routing.edge_time(S.rg, (u, v), req.vehicle, {}, g=g))
    except nx.NetworkXNoPath:
        base = None
    crossed = {}
    usual_time = 0.0
    for a, b in zip(base or [], (base or [])[1:]):
        root = g.edges[a, b].get("root", (a, b))
        usual_time += routing.edge_time(S.rg, (a, b), req.vehicle, {}, g=g) or 0.0   # the way they would go, no incidents
        for i in effects.get(root, []):
            crossed[i.id] = i
    for r in routes:
        r.pop("path", None)
    if base is None:
        raise HTTPException(422, "No legal route between these points on the mapped network (one-way streets or a "
                                 "disconnected part of the map). Try points on a main road.")
    return {"vehicle": req.vehicle, "routes": routes, "usual_route": S.rg.path_coords(base, g),
            "usual_route_hits": [{"id": k, "title": v.title, "type": v.type, "severity": v.severity,
                                 "road": S.rg.road_name(v.lat, v.lon)} for k, v in crossed.items()],
            "usual_time_s": round(usual_time),
            "snapped": {"start": list(src_pt), "destination": list(dst_pt)},
            "routing": "road-segment", "no_route": not routes}


LANDMARKS = [{"name": "RTC Complex (Dwaraka Bus Station)", "lat": 17.7238639, "lon": 83.3068879, "kind": "place"}]


def _places_index() -> list[dict]:
    idx = S.rg.__dict__.get("_places")
    if idx is None:
        groups: dict[str, list] = {}
        for u, v, d in S.rg.g.edges(data=True):
            if d.get("name"):
                (a, b), (c, e) = S.rg.node_latlon(u), S.rg.node_latlon(v)
                groups.setdefault(d["name"], []).append(((a + c) / 2, (b + e) / 2))
        roads = []
        for name, pts in groups.items():
            clat, clon = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            lat, lon = min(pts, key=lambda p: (p[0] - clat) ** 2 + (p[1] - clon) ** 2)   # a point ON the road, near its middle
            roads.append({"name": name, "lat": lat, "lon": lon, "kind": "road"})
        c = S.rg.centre()
        near = [x for x in LANDMARKS if S.rg.distance_to_network(x["lat"], x["lon"]) <= config.MAX_SNAP_M]
        idx = [{"name": "Dwaraka Nagar (centre)", "lat": c[0], "lon": c[1], "kind": "place"}] + near + sorted(roads, key=lambda r: r["name"].lower())
        S.rg.__dict__["_places"] = idx
    return idx


@app.get("/api/places")
def places(q: str = Query("", max_length=60), limit: int = Query(8, ge=1, le=20)):
    """Search roads and landmarks by name so people can pick a start and a destination without tapping the map."""
    q = q.strip().lower()
    idx = _places_index()
    hits = [p for p in idx if q in p["name"].lower()] if q else idx
    hits.sort(key=lambda p: (not p["name"].lower().startswith(q), p["name"].lower()))   # names that START with it first
    return {"places": hits[:limit]}


@app.get("/api/hotspots")
async def get_hotspots():
    return await asyncio.to_thread(hotspots_view)


@app.get("/api/network/stats")
def network_stats():
    """Actual statistics of the loaded road graph (after loading rules: curves split, weak component kept)."""
    return {"source": S.rg.source, "map_mode": S.rg.mode, **S.rg.stats()}


@app.get("/api/feed")
def feed():
    """Open incident feed, modelled on the Waze for Cities incident-feed fields."""
    out = []
    for inc in active_published():
        out.append({"id": inc.id, "type": "ROAD_CLOSED" if inc.severity == "Closed" else "HAZARD",
                    "subtype": inc.type.upper(), "location": {"lat": inc.lat, "lon": inc.lon},
                    "description": inc.title, "severity": inc.severity,
                    "starttime": inc.starts or inc.status_times.get("Verified"), "endtime": inc.ends,
                    "vehicles_blocked": inc.affected_vehicles})
    return {"generated": now(), "publisher": "RoadPulse", "incidents": out}


@app.get("/api/evaluation")
def evaluation(request: Request, trips: int = Query(400, ge=50, le=2000), seed: int = 7):
    limit(EVAL_LIMIT, "evaluation", request)   # public and CPU-heavy: one caller must not be able to hammer it
    incs = active_published()
    if not incs:
        raise HTTPException(409, "No active verified incident to evaluate; run the demo scenario first")
    return evaluate.before_after(S.rg, incs, trips, seed)


_snap_cache: dict[tuple, tuple[int, str]] = {}
_snap_lock = asyncio.Lock()


async def snapshot_json(officer: bool) -> str:
    """Build each (version, audience) snapshot once and share it across all connected clients."""
    key = ("officer" if officer else "public",)
    cached = _snap_cache.get(key)
    if cached and cached[0] == S.version:
        return cached[1]
    async with _snap_lock:
        cached = _snap_cache.get(key)
        if cached and cached[0] == S.version:
            return cached[1]
        version = S.version
        claims = {"role": "officer"} if officer else None
        snap = {"version": version,
                "incidents": list_incidents("all" if officer else "public", False, claims)["incidents"],
                "hotspots": await asyncio.to_thread(hotspots_view)}
        if officer:
            snap["events"] = list(store.events)[:30]
        data = json.dumps(snap)
        _snap_cache[key] = (version, data)
        return data


@app.post("/api/stream/ticket")
def stream_ticket(officer=Depends(require_officer)):
    """Exchange the officer's bearer token (sent in a header) for a one-time, 60-second stream ticket."""
    return issue_stream_ticket(officer)


@app.get("/api/stream")
async def stream(request: Request, ticket: str | None = None):
    """Server-Sent Events: pushes a fresh snapshot whenever anything changes (checked every second).
    No ticket = public stream. ?ticket= (from POST /api/stream/ticket) = officer stream; a used, expired or unknown
    ticket gets 401 so the browser fetches a new one instead of silently downgrading to the public stream."""
    officer = None
    if ticket is not None:
        officer = consume_stream_ticket(ticket)
        if not officer:
            raise HTTPException(401, "Stream ticket expired or already used; request a new one")

    async def gen():
        yield "retry: 3000\n\n"  # browsers reconnect after 3 s if the connection drops
        last = -1
        beat = 0
        while True:
            if await request.is_disconnected():
                break
            if S.version != last:
                last = S.version
                yield f"data: {await snapshot_json(bool(officer))}\n\n"
            beat += 1
            if beat % 10 == 0:
                # a real (named) event, not a comment: the page can only tell "connected and quiet" from "silently
                # dead" if something arrives. It does not touch onmessage, so snapshots are unaffected.
                yield f"event: heartbeat\ndata: {json.dumps({'t': now()})}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- officer endpoints

class Login(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: Login, request: Request):
    limit(REPORT_LIMIT, "login", request)
    t = issue_token(body.username, body.password)
    if not t:
        S.db.audit(now(), body.username, "login_failed")
        raise HTTPException(401, "Wrong username or password")
    S.db.audit(now(), body.username, "login")
    return t


def _officer_action(incident_id: str, action: str, officer: dict, reason: str | None = None,
                    severity: str | None = None, urgent: bool = False) -> dict:
    inc = _get(incident_id)
    who = officer["sub"]
    ts = now()
    with store.lock:
        if action in ("verify", "respond") and inc.status == "Reported" and field.needs_field(inc):
            if not urgent:   # both people validate: police on the ground, then the control room
                raise HTTPException(409, "Police have not checked this yet. Send a unit first, or confirm it as urgent.")
            inc.urgent = True
            S.db.audit(ts, who, "urgent_publish", inc.id, {"reason": reason})
        if action in ("verify", "respond") and not severity and inc.field_severity and not inc.severity_set \
                and inc.type != "flood":
            severity = inc.field_severity     # one tap: use what police saw
        if severity and action in ("verify", "respond") and inc.type != "flood":
            inc.severity, inc.severity_set = severity, True   # a flood's severity comes from measured depth
        if action == "verify":
            inc.signals.append(Signal(source="officer", type=inc.type, lat=inc.lat, lon=inc.lon, weight=1.0,
                                      source_id=who, note=reason))
            fusion.refresh(store, inc)
            if inc.status == "Reported":
                fusion.set_status(store, inc, "Verified", ts, "verified by officer", who)
            services.reward_reporters(store, inc, True)
        elif action == "respond":
            if inc.status == "Cleared":
                raise HTTPException(409, "Incident already cleared")
            if inc.status == "Reported":
                fusion.set_status(store, inc, "Verified", ts, "verified by officer", who)
            fusion.set_status(store, inc, "Responding", ts, reason or "diversion approved", who)
        elif action == "clear":
            fusion.set_status(store, inc, "Cleared", ts, reason or "cleared by officer", who)
        elif action == "reject":
            services.reward_reporters(store, inc, False)
            fusion.set_status(store, inc, "Cleared", ts, reason or "rejected as false report", who)
        inc.updated = ts
    S.db.audit(ts, who, f"incident_{action}", inc.id, {"reason": reason})
    _changed(inc)
    return incident_view(inc)


# ---------------------------------------------------------------- field validation: control room assigns, police report

@app.post("/api/incidents/{incident_id}/assign", status_code=201)
def assign_unit(incident_id: str, body: AssignIn, officer=Depends(require_officer)):
    """Send a traffic-police unit to check this incident on the ground."""
    inc = _get(incident_id)
    if inc.status == "Cleared":
        raise HTTPException(409, "Incident already cleared")
    if body.unit not in police_units():
        raise HTTPException(404, "Unknown police unit")
    ts = now()
    with store.lock:
        field.assign(inc, body.unit, officer["sub"], ts)
        store.log(ts, f"{inc.title}: {body.unit} sent to check by {officer['sub']}")
    S.db.audit(ts, officer["sub"], "assign", inc.id, {"unit": body.unit})
    _changed(inc)
    return {"incident_id": inc.id, "assignments": inc.assignments}


@app.get("/api/units")
def list_units(officer=Depends(require_officer)):
    """Police units for the control-room map: where each is (if shared), how fresh, and which incidents it is sent to."""
    ts, busy = now(), field.busy_counts(store)
    sent: dict[str, list[str]] = {}
    for i in store.snapshot():
        for a in i.assignments:
            if a["status"] in field.OPEN_CALL and i.status != "Cleared":
                sent.setdefault(a["unit"], []).append(i.id)
    units = []
    for n in police_units():
        p = store.units.get(n)
        units.append({"unit": n, "lat": p["lat"] if p else None, "lon": p["lon"] if p else None,
                      "age_s": round(ts - p["ts"]) if p else None, "calls": busy.get(n, 0), "incidents": sent.get(n, [])})
    return {"units": units}


@app.get("/api/incidents/{incident_id}/units")
def units_for_incident(incident_id: str, officer=Depends(require_officer)):
    return {"units": field.units_by_distance(store, _get(incident_id), police_units())}


def _call_view(inc: Incident, unit: str) -> dict:
    mine = next((a for a in inc.assignments if a["unit"] == unit), None)
    return {"id": inc.id, "type": inc.type, "title": inc.title, "road": S.rg.road_name(inc.lat, inc.lon),
            "lat": inc.lat, "lon": inc.lon, "status": inc.status, "severity": inc.severity,
            "published": services.is_published(inc, now()), "my_status": mine["status"] if mine else None,
            "assigned_at": mine["at"] if mine else None, "my_reports": [r for r in inc.field_reports if r["unit"] == unit],
            "signals": sorted({s.source for s in inc.signals})}


@app.get("/api/police/me")
def police_me(police=Depends(require_police)):
    return {"unit": police["sub"], "position": store.units.get(police["sub"]), "centre": list(S.rg.centre())}


@app.post("/api/police/position")
def police_position(p: UnitPositionIn, police=Depends(require_police)):
    store.units[police["sub"]] = {"lat": p.lat, "lon": p.lon, "ts": now()}
    return {"ok": True}


@app.get("/api/police/calls")
def police_calls(police=Depends(require_police)):
    unit = police["sub"]
    calls = [i for i in store.snapshot() if i.status != "Cleared"
             and any(a["unit"] == unit and a["status"] in field.OPEN_CALL for a in i.assignments)]
    calls.sort(key=lambda i: next(a["at"] for a in i.assignments if a["unit"] == unit), reverse=True)
    return {"calls": [_call_view(i, unit) for i in calls]}


def _my_call(incident_id: str, unit: str, statuses=field.OPEN_CALL) -> Incident:
    inc = _get(incident_id)
    if not any(a["unit"] == unit and a["status"] in statuses for a in inc.assignments):
        raise HTTPException(403, "You were not sent to this call")
    return inc


@app.post("/api/police/calls/{incident_id}/status")
def police_call_status(incident_id: str, body: CallStatusIn, police=Depends(require_police)):
    inc = _my_call(incident_id, police["sub"])
    ts = now()
    with store.lock:
        field.set_status(inc, police["sub"], body.status, ts)
        store.log(ts, f"{inc.title}: {police['sub']} {body.status.replace('_', ' ')}")
    S.db.audit(ts, police["sub"], f"call_{body.status}", inc.id)
    _changed(inc)
    return _call_view(inc, police["sub"])


@app.post("/api/police/calls/{incident_id}/report")
def police_report(incident_id: str, body: FieldReportIn, police=Depends(require_police)):
    """What the officer SEES on the scene. A strong signal; the control room still confirms before drivers are warned."""
    inc = _my_call(incident_id, police["sub"])
    ts = now()
    with store.lock:
        field.add_report(inc, police["sub"], body.finding, body.need, body.note, ts)
        fusion.refresh(store, inc)
        store.log(ts, f"{inc.title}: {police['sub']} reports {body.finding.replace('_', ' ')}")
    S.db.audit(ts, police["sub"], "field_report", inc.id, {"finding": body.finding, "need": body.need})
    _changed(inc)
    return _call_view(inc, police["sub"])


# ---------------------------------------------------------------- dispatch (officer)

@app.post("/api/incidents/{incident_id}/dispatch", status_code=201)
def dispatch(incident_id: str, body: Dispatch, officer=Depends(require_officer)):
    """Record that police, an ambulance, a tow vehicle, drainage or a marshal was sent. RoadPulse does not call them:
    this is the control room's checklist, kept with the incident and in the audit log."""
    inc = _get(incident_id)
    if inc.status == "Cleared":
        raise HTTPException(409, "Incident already cleared")
    ts = now()
    with store.lock:
        inc.dispatched.append({"unit": body.unit, "by": officer["sub"], "at": ts, "note": body.note})
        for r in inc.requests:
            if r["kind"] == body.unit:
                r["done"] = True
        store.log(ts, f"{inc.title}: {body.unit} dispatched by {officer['sub']}")
        inc.updated = ts
    S.db.audit(ts, officer["sub"], "dispatch", inc.id, {"unit": body.unit, "note": body.note})
    _changed(inc)
    return {"incident_id": inc.id, "dispatched": inc.dispatched}


class ActionBody(BaseModel):
    reason: str | None = None
    severity: Severity | None = None   # verify / respond: the officer decides, e.g. "Closed" for a fully blocked road
    urgent: bool = False               # publish without waiting for police (recorded in the audit log)


@app.post("/api/incidents/{incident_id}/{action}")
def incident_action(incident_id: str, action: str, body: ActionBody | None = None, officer=Depends(require_officer)):
    if action not in ("verify", "respond", "clear", "reject"):
        raise HTTPException(404, "Unknown action")
    return _officer_action(incident_id, action, officer, body.reason if body else None, body.severity if body else None,
                           body.urgent if body else False)


@app.post("/api/planned-works", status_code=201)
def planned_works(w: PlannedWork, officer=Depends(require_officer)):
    require_on_network(w.lat, w.lon, "Planned works location")
    inc = services.handle_planned(store, w)
    S.db.audit(now(), officer["sub"], "planned_work_created", inc.id, w.model_dump())
    _changed(inc)
    return incident_view(inc)


@app.post("/api/weather/override")
def weather_override(body: RainOverride, officer=Depends(require_officer)):
    store.rain_override = body.rain_mm_per_hr
    S.db.audit(now(), officer["sub"], "rain_override", None, body.model_dump())
    bump()
    return {"rain_override": store.rain_override}


@app.get("/api/incidents/{incident_id}/whatif")
def whatif(incident_id: str, officer=Depends(require_officer)):
    inc = _get(incident_id)
    others = [i for i in active_published() if i.id != inc.id]
    return evaluate.what_if(S.rg, inc, others)


@app.get("/api/incidents/{incident_id}/signals")
def signals(incident_id: str, officer=Depends(require_officer)):
    inc = _get(incident_id)
    others = [i for i in active_published() if i.id != inc.id]
    return evaluate.signal_advice(S.rg, inc, others)


@app.get("/api/incidents/{incident_id}/bulletin")
def bulletin(incident_id: str, officer=Depends(require_officer)):
    """Template bulletin in English and Telugu. Telugu wording should be checked by a native speaker."""
    inc = _get(incident_id)
    place = inc.title.split(" · ", 1)[-1]
    blocked = ", ".join(v.replace("_", "-") for v in inc.affected_vehicles) or "none"
    depth = f" Water depth about {round(inc.depth_cm)} cm." if inc.depth_cm else ""
    en = (f"RoadPulse alert: {inc.title.split(' · ')[0]} at {place}. Severity {inc.severity}.{depth} "
          f"Vehicles that should avoid this road: {blocked}. Follow the suggested alternative route.")
    te_type = {"flood": "నీరు నిలిచింది", "accident": "ప్రమాదం జరిగింది", "blockage": "రహదారి మూసుకుపోయింది",
               "construction": "నిర్మాణ పనులు జరుగుతున్నాయి", "slowdown": "ట్రాఫిక్ నెమ్మదిగా ఉంది"}[inc.type]
    te_depth = f" నీటి లోతు సుమారు {round(inc.depth_cm)} సెం.మీ." if inc.depth_cm else ""
    te = f"రోడ్‌పల్స్ హెచ్చరిక: {place} వద్ద {te_type}.{te_depth} దయచేసి ప్రత్యామ్నాయ మార్గాన్ని ఉపయోగించండి."
    return {"english": en, "telugu": te, "note": "Template text; Telugu to be reviewed by a native speaker"}


@app.get("/api/audit")
def audit(officer=Depends(require_officer)):
    return {"entries": S.db.audit_entries(50)}


# ---------------------------------------------------------------- commuters: subscribe, trip, position, alerts

def _sub(sub_id: str, token: str | None) -> alerts.Subscriber:
    sub = store.subscribers.get(sub_id)
    if not sub or not alerts.check_token(sub, token):
        raise HTTPException(401, "Unknown subscriber or wrong token")  # same answer for both: no probing for ids
    return sub


@app.post("/api/subscribers", status_code=201)
def subscribe(body: SubscriberIn, request: Request):
    """Sign up for alerts. Consent to location use is required. The returned token is shown once and must be sent as
    X-Subscriber-Token with every later call."""
    limit(SUBSCRIBE_LIMIT, "subscribe", request)
    if body.home_lat is not None:
        require_on_network(body.home_lat, body.home_lon, "Home")
    if body.work_lat is not None:
        require_on_network(body.work_lat, body.work_lon, "Work")
    sub = alerts.create_subscriber(store, body)
    persist_subscribers()
    S.db.audit(now(), f"subscriber:{sub.id}", "subscribe", sub.id)
    bump()
    return {"id": sub.id, "token": sub.token, "depart_at": sub.depart_at, "telegram_ready": channels_ready(sub)}


def channels_ready(sub) -> bool:
    from . import channels
    return bool(sub.telegram_chat and channels.telegram_configured())


@app.get("/api/subscribers/{sub_id}")
def get_subscriber(sub_id: str, x_subscriber_token: str | None = Header(default=None)):
    return _sub(sub_id, x_subscriber_token).view(full=True)


@app.delete("/api/subscribers/{sub_id}")
def delete_subscriber(sub_id: str, x_subscriber_token: str | None = Header(default=None)):
    """Erase the commuter's saved places and alert history."""
    sub = _sub(sub_id, x_subscriber_token)
    with store.lock:
        store.subscribers.pop(sub.id, None)
    S.db.delete_subscriber(sub.id)
    _sub_saved.pop(sub.id, None)
    S.db.audit(now(), f"subscriber:{sub.id}", "unsubscribe", sub.id)
    bump()
    return {"deleted": True}


@app.post("/api/subscribers/{sub_id}/places")
def set_places(sub_id: str, body: PlacesIn, x_subscriber_token: str | None = Header(default=None)):
    sub = _sub(sub_id, x_subscriber_token)
    if body.home_lat is not None:
        require_on_network(body.home_lat, body.home_lon, "Home")
    if body.work_lat is not None:
        require_on_network(body.work_lat, body.work_lon, "Work")
    with store.lock:
        if body.home_lat is not None:
            sub.home = (body.home_lat, body.home_lon)
        if body.work_lat is not None:
            sub.work = (body.work_lat, body.work_lon)
        if body.depart_hhmm:
            sub.depart_hhmm = body.depart_hhmm
            sub.depart_at = alerts.next_departure(body.depart_hhmm, now())
    persist_subscribers()
    return sub.view(full=True)


@app.post("/api/subscribers/{sub_id}/trip")
def start_trip(sub_id: str, body: TripStart, x_subscriber_token: str | None = Header(default=None)):
    sub = _sub(sub_id, x_subscriber_token)
    dest = None
    if body.dest:
        dest = sub.work if body.dest == "work" else sub.home
        if dest is None:
            raise HTTPException(422, f"No saved {body.dest} location")
    elif body.lat is not None:
        require_on_network(body.lat, body.lon, "Destination")
        dest = (body.lat, body.lon)
    with store.lock:
        if dest is not None:
            sub.dest = tuple(dest)
        if body.depart_in_min is not None:
            sub.depart_at = now() + body.depart_in_min * 60
    persist_subscribers()
    alerts.tick(S.rg, store)  # a pre-trip alert may be due right away
    return sub.view(full=True)


@app.post("/api/subscribers/{sub_id}/position")
def post_position(sub_id: str, p: PositionIn, request: Request,
                  x_subscriber_token: str | None = Header(default=None)):
    """Live location while travelling. The route is checked against every published incident; if the turn-off is near
    the alert is returned here and also kept in the inbox."""
    sub = _sub(sub_id, x_subscriber_token)
    ts = p.ts or now()
    if S.rg.distance_to_network(p.lat, p.lon) > config.MAX_SNAP_M:
        return {"on_map": False, "alert": None}
    with store.lock:
        sub.last_pos = {"lat": p.lat, "lon": p.lon, "ts": ts, "speed_kph": p.speed_kph}
    alert = alerts.evaluate_in_trip(S.rg, store, sub, ts)
    if alert:
        persist_subscribers()
        bump()
    return {"on_map": True, "alert": alert}


@app.get("/api/subscribers/{sub_id}/alerts")
def subscriber_alerts(sub_id: str, since: float = 0.0, x_subscriber_token: str | None = Header(default=None)):
    sub = _sub(sub_id, x_subscriber_token)
    return {"alerts": [a for a in sub.inbox if a["ts"] > since], "now": now()}


# ---------------------------------------------------------------- boards (simulated roadside displays)

@app.get("/api/boards")
def list_boards():
    incs = alerts.published(store, now())
    return {"boards": [alerts.board_view(S.rg, b, incs) for b in alerts.boards(S.rg)],
            "note": "SIMULATED displays: a real board needs hardware, power and a data link"}


@app.get("/api/boards/{board_id}")
def get_board(board_id: str):
    b = next((x for x in alerts.boards(S.rg) if x["id"] == board_id), None)
    if not b:
        raise HTTPException(404, "Unknown board")
    return alerts.board_view(S.rg, b, alerts.published(store, now()))


# ---------------------------------------------------------------- frontend

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi import Response
    return Response(status_code=204)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/control", include_in_schema=False)
def control_page():
    return FileResponse(os.path.join(STATIC_DIR, "control.html"))


@app.get("/police", include_in_schema=False)
def police_page():
    return FileResponse(os.path.join(STATIC_DIR, "police.html"))


@app.get("/classic", include_in_schema=False)
def classic_page():
    """The original detailed dashboard (planned works, what-if, signal advice, bulletins, evaluation)."""
    return FileResponse(os.path.join(STATIC_DIR, "classic.html"))


@app.get("/commute", include_in_schema=False)
def commute_page():
    return FileResponse(os.path.join(STATIC_DIR, "commute.html"))


@app.get("/board", include_in_schema=False)
def board_page():
    return FileResponse(os.path.join(STATIC_DIR, "board.html"))
