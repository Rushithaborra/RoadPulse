"""RoadPulse API: one FastAPI app (modular monolith). Run: uvicorn app.main:app --app-dir backend"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, evaluate, forecast, fusion, services
from .db import Database
from .models import (CitizenReport, Incident, PlannedWork, ProbeSample, RainOverride, RouteRequest,
                     SensorReading, Signal, now)
from .roadgraph import RoadGraph
from .routing import incident_effects, route_alternatives, weighted_graph
from .security import (DEVICE_LIMIT, REPORT_LIMIT, ROUTE_LIMIT, issue_token, limit, optional_officer,
                       require_device, require_officer, verify_token)
from .store import store

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
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


def persist():
    """Save every incident that changed since it was last saved."""
    for inc in list(store.incidents.values()):
        if S.saved.get(inc.id) != inc.updated:
            S.db.save_incident(inc)
            S.saved[inc.id] = inc.updated


def _on_status(inc: Incident, status: str, ts: float, reason: str | None, actor: str):
    S.db.status_change(inc.id, status, ts, reason, actor)
    bump()


async def lifecycle_loop():
    while True:
        try:
            await asyncio.to_thread(services.tick, store, S.rg)
            await asyncio.to_thread(persist)
            if any(i.status != "Cleared" for i in store.incidents.values()):
                bump()  # timers and auto-clear countdowns move even without new input
        except Exception:
            log.exception("lifecycle tick failed")
        await asyncio.sleep(config.TICK_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.rg = RoadGraph.load()
    S.db = Database()
    for inc in S.db.load_open_incidents():
        store.incidents[inc.id] = inc
        S.saved[inc.id] = inc.updated
    fusion.STATUS_HOOKS.append(_on_status)
    log.info("graph=%s nodes=%d edges=%d reloaded_incidents=%d", S.rg.source, S.rg.g.number_of_nodes(),
             S.rg.g.number_of_edges(), len(store.incidents))
    task = asyncio.create_task(lifecycle_loop())
    yield
    task.cancel()
    fusion.STATUS_HOOKS.remove(_on_status)


app = FastAPI(title="RoadPulse API", version="0.1.0", lifespan=lifespan)


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
        "road": S.rg.road_name(inc.lat, inc.lon),
        "affected_segments": [S.rg.edge_geo(u, v) for u, v in
                              S.rg.edges_near(inc.lat, inc.lon, config.INCIDENT_EDGE_RADIUS_M) if str(u) < str(v)],
    }
    if detail:
        d["signals"] = [s.model_dump() for s in inc.signals[-20:]]
    return d


def hotspots_view() -> dict:
    rain, src = forecast.current_rain(store)
    return {"rain_mm_per_hr": rain, "rain_source": src,
            "hotspots": [forecast.countdown(h, rain) | {"sensor_id": h.sensor_id,
                         "last_reading_age_s": round(now() - h.last_reading) if h.last_reading else None}
                         for h in store.hotspots.values()]}


def active_published() -> list[Incident]:
    ts = now()
    return [i for i in store.incidents.values() if services.is_published(i, ts)]


def _get(incident_id: str) -> Incident:
    inc = store.incidents.get(incident_id)
    if not inc:
        raise HTTPException(404, "Incident not found")
    return inc


def _changed(*incs: Incident):
    persist()
    bump()


# ---------------------------------------------------------------- public endpoints

@app.get("/api/health")
def health():
    return {"ok": True, "graph": S.rg.source, "nodes": S.rg.g.number_of_nodes(), "edges": S.rg.g.number_of_edges(),
            "incidents_open": sum(1 for i in store.incidents.values() if i.status != "Cleared"),
            "center": [config.CENTER_LAT, config.CENTER_LON]}


@app.get("/api/network")
def network():
    segs = []
    for u, v, d in S.rg.g.edges(data=True):
        if str(u) < str(v) or not S.rg.g.has_edge(v, u):
            segs.append({"coords": S.rg.edge_geo(u, v), "speed_kph": d["speed_kph"], "lanes": d["lanes"]})
    return {"source": S.rg.source, "center": [config.CENTER_LAT, config.CENTER_LON], "segments": segs}


@app.get("/api/incidents")
def list_incidents(scope: str = Query("public", pattern="^(public|all)$"), include_cleared: bool = False,
                   officer=Depends(optional_officer)):
    if scope == "all" and not officer:
        raise HTTPException(401, "Officer login required for scope=all")
    ts = now()
    items = []
    for inc in sorted(store.incidents.values(), key=lambda i: i.updated, reverse=True):
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
    inc = services.handle_report(store, r, S.rg.road_name(r.lat, r.lon))
    S.db.report(r.type, r.lat, r.lon, r.reporter_id, r.note, now())
    _changed(inc)
    return {"incident_id": inc.id, "status": inc.status, "confidence": inc.confidence,
            "message": "Thanks, your report was received" if inc.status == "Reported"
            else "Thanks, this matches a verified incident"}


@app.post("/api/sensors/readings")
def post_reading(r: SensorReading, request: Request, device=Depends(require_device)):
    limit(DEVICE_LIMIT, "device", request)
    if not (0 <= r.depth_cm <= 300):
        raise HTTPException(422, "depth_cm out of range 0..300")
    res = services.handle_sensor(store, r)
    if not res.get("ok"):
        raise HTTPException(404, res.get("error"))
    S.db.sensor_reading(r.sensor_id, r.depth_cm, r.ts or now())
    _changed()
    return res


@app.post("/api/probes")
def post_probe(p: ProbeSample, request: Request, device=Depends(require_device)):
    limit(DEVICE_LIMIT, "device", request)
    if not (0 <= p.speed_kph <= 150):
        raise HTTPException(422, "speed_kph out of range")
    res = services.handle_probe(store, S.rg, p)
    if res.get("incident"):
        _changed()
    return res


@app.post("/api/routes")
def post_routes(req: RouteRequest, request: Request):
    limit(ROUTE_LIMIT, "route", request)
    ts = now()
    src = S.rg.nearest_node(req.from_lat, req.from_lon)
    dst = S.rg.nearest_node(req.to_lat, req.to_lon)
    if src == dst:
        raise HTTPException(422, "Origin and destination are the same point")
    published = active_published()
    effects = incident_effects(S.rg, published)
    speeds = {e: services.observed_speed(store, e, ts) for e in effects}
    routes = route_alternatives(S.rg, src, dst, req.vehicle, effects, speeds, k=max(1, min(req.alternatives, 4)))
    # the route a driver would take without RoadPulse, and which incidents it runs into
    import networkx as nx
    base = nx.shortest_path(weighted_graph(S.rg, req.vehicle, {}), src, dst, weight="w")
    crossed = {i.id: i.title for e in zip(base, base[1:]) for i in effects.get(e, [])}
    for r in routes:
        r.pop("path", None)
    return {"vehicle": req.vehicle, "routes": routes, "usual_route": S.rg.path_coords(base),
            "usual_route_hits": [{"id": k, "title": v} for k, v in crossed.items()],
            "no_route": not routes}


@app.get("/api/hotspots")
async def get_hotspots():
    return await asyncio.to_thread(hotspots_view)


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
def evaluation(trips: int = Query(400, ge=50, le=2000), seed: int = 7):
    incs = active_published()
    if not incs:
        raise HTTPException(409, "No active verified incident to evaluate; run the demo scenario first")
    return evaluate.before_after(S.rg, incs, trips, seed)


@app.get("/api/stream")
async def stream(request: Request, token: str | None = None):
    """Server-Sent Events: pushes a fresh snapshot whenever anything changes (checked every second)."""
    officer = verify_token(token) if token else None

    async def gen():
        last = -1
        beat = 0
        while True:
            if await request.is_disconnected():
                break
            if S.version != last:
                last = S.version
                snap = {"version": last, "incidents": list_incidents("all" if officer else "public", False, officer)["incidents"],
                        "hotspots": await asyncio.to_thread(hotspots_view)}
                if officer:
                    snap["events"] = list(store.events)[:30]
                yield f"data: {json.dumps(snap)}\n\n"
            beat += 1
            if beat % 15 == 0:
                yield ": keep-alive\n\n"
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


def _officer_action(incident_id: str, action: str, officer: dict, reason: str | None = None) -> dict:
    inc = _get(incident_id)
    who = officer["sub"]
    ts = now()
    with store.lock:
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


class ActionBody(BaseModel):
    reason: str | None = None


@app.post("/api/incidents/{incident_id}/{action}")
def incident_action(incident_id: str, action: str, body: ActionBody | None = None, officer=Depends(require_officer)):
    if action not in ("verify", "respond", "clear", "reject"):
        raise HTTPException(404, "Unknown action")
    return _officer_action(incident_id, action, officer, body.reason if body else None)


@app.post("/api/planned-works", status_code=201)
def planned_works(w: PlannedWork, officer=Depends(require_officer)):
    if w.ends <= w.starts:
        raise HTTPException(422, "ends must be after starts")
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
