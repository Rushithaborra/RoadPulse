"""Who gets warned, when, and what they are told.

Rules we agreed on while designing this (each answers a doubt):

  * Recipients are chosen by ROUTE, not by a fixed radius: only people whose own route to their destination
    uses the affected road.
  * The check runs continuously (on every position update and whenever an incident changes), so someone who sets
    off AFTER an incident was verified is warned when it becomes relevant. A standing incident, not a one-time
    broadcast.
  * The warning must arrive before the TURN-OFF: we compare the route the person would take now with the best route
    around the incident, find the last junction they share, and warn when it is ALERT_WARN_HORIZON_S away.
  * If that junction is already behind them they are told to turn back (U-turn) with the new route.
  * Wording is short with one action so it can be spoken: "Accident ahead on X. Turn left onto Y."
  * Regular commuters are warned BEFORE they leave (pre-trip), the safest moment to read.
  * Planned construction is included in pre-trip alerts if it overlaps the departure.
  * When an incident clears, everyone who was warned gets a "road clear" message.
  * Boards are simulated roadside displays for people who have no app.

Limits (say these out loud): only people running the page or bot with location on can be reached; a phone page only
gets location while it is open; mid-block precision depends on the road being a mapped link.
"""
from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass, field

import networkx as nx

from . import channels, config
from .geo import bearing_deg, haversine_m
from .fusion import TYPE_LABEL
from .models import Incident, now
from .roadgraph import RoadGraph, _point_segment_m
from .routing import edge_time, graph_with_endpoints, incident_effects
from .store import Store

INF = float("inf")
SPEED_MPS = {"two_wheeler": 8.0, "auto": 7.0, "car": 9.0, "ambulance": 12.0, "bus": 7.0}
INBOX_MAX = 50


# ------------------------------------------------------------------ subscribers
@dataclass
class Subscriber:
    id: str
    token: str
    name: str
    vehicle: str = "two_wheeler"
    voice: bool = False
    telegram_chat: str | None = None
    home: tuple | None = None
    work: tuple | None = None
    depart_hhmm: str | None = None
    depart_at: float | None = None          # next planned departure (epoch seconds)
    dest: tuple | None = None               # destination of the trip in progress
    last_pos: dict | None = None            # {lat, lon, ts, speed_kph}
    known: set = field(default_factory=set)         # incidents this person already knows about / was told about
    alerted: dict = field(default_factory=dict)     # incident id -> what happened (rerouted, uturn, pretrip, ...)
    inbox: list = field(default_factory=list)
    created: float = field(default_factory=now)
    last_eval: float = 0.0

    def doc(self) -> dict:
        d = {k: getattr(self, k) for k in ("id", "token", "name", "vehicle", "voice", "telegram_chat", "home", "work",
                                           "depart_hhmm", "depart_at", "dest", "created")}
        d["known"], d["alerted"] = sorted(self.known), dict(self.alerted)
        return d

    @classmethod
    def from_doc(cls, d: dict) -> "Subscriber":
        s = cls(id=d["id"], token=d["token"], name=d["name"], vehicle=d.get("vehicle", "two_wheeler"),
                voice=bool(d.get("voice")), telegram_chat=d.get("telegram_chat"),
                home=tuple(d["home"]) if d.get("home") else None, work=tuple(d["work"]) if d.get("work") else None,
                depart_hhmm=d.get("depart_hhmm"), depart_at=d.get("depart_at"),
                dest=tuple(d["dest"]) if d.get("dest") else None, created=d.get("created", now()))
        s.known, s.alerted = set(d.get("known", [])), dict(d.get("alerted", {}))
        return s

    def view(self, full: bool = False) -> dict:
        v = {"id": self.id, "name": self.name, "vehicle": self.vehicle, "voice": self.voice,
             "depart_hhmm": self.depart_hhmm, "depart_at": self.depart_at,
             "has_home": self.home is not None, "has_work": self.work is not None,
             "trip_active": bool(self.dest and self.last_pos and now() - self.last_pos["ts"] <= config.POSITION_FRESH_S),
             "telegram": bool(self.telegram_chat), "alerts": len(self.inbox)}
        if full:
            v.update(home=self.home, work=self.work, dest=self.dest)
        return v


def next_departure(hhmm: str, ts: float) -> float:
    """Next occurrence of HH:MM (local time at UTC_OFFSET_S) strictly after ts, as epoch seconds."""
    h, m = int(hhmm[:2]), int(hhmm[3:])
    local = ts + config.UTC_OFFSET_S
    day_start = local - (local % 86400)
    t = day_start + h * 3600 + m * 60
    if t <= local:
        t += 86400
    return t - config.UTC_OFFSET_S


def create_subscriber(store: Store, data, ts: float | None = None) -> Subscriber:
    ts = ts or now()
    sub = Subscriber(id="sub-" + secrets.token_hex(4), token=secrets.token_urlsafe(18), name=data.name,
                     vehicle=data.vehicle, voice=data.voice, telegram_chat=data.telegram_chat,
                     home=(data.home_lat, data.home_lon) if data.home_lat is not None else None,
                     work=(data.work_lat, data.work_lon) if data.work_lat is not None else None,
                     depart_hhmm=data.depart_hhmm, created=ts)
    if sub.depart_hhmm:
        sub.depart_at = next_departure(sub.depart_hhmm, ts)
    with store.lock:
        store.subscribers[sub.id] = sub
    return sub


def check_token(sub: Subscriber, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(sub.token, token)


# ------------------------------------------------------------------ route helpers
def _weight(rg, g, vehicle, effects, src=None, incs=()):
    def w(u, v, d):
        t = edge_time(rg, (u, v), vehicle, effects, None, g=g)
        if t is None and u == src and incs:
            # the person is already inside an incident's zone: driving AWAY from it must stay possible
            gu, gv = g.nodes[u], g.nodes[v]
            # allowed only if this piece never gets closer to the incident than the person already is
            if all(_point_segment_m(i.lat, i.lon, gu["lat"], gu["lon"], gv["lat"], gv["lon"])
                   >= haversine_m(gu["lat"], gu["lon"], i.lat, i.lon) - 1 for i in incs):
                t = edge_time(rg, (u, v), vehicle, {}, None, g=g)
        return t
    return w


def _path(rg, g, src, dst, vehicle, effects, incs=()):
    try:
        return nx.shortest_path(g, src, dst, weight=_weight(rg, g, vehicle, effects, src, incs))
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def _path_time(rg, g, path, vehicle, effects, src=None, incs=()) -> float:
    w = _weight(rg, g, vehicle, effects, src, incs)
    total = 0.0
    for a, b in zip(path, path[1:]):
        t = w(a, b, None)
        if t is None:
            return INF
        total += t
    return total


def _path_len(rg, g, path) -> float:
    total = 0.0
    for a, b in zip(path, path[1:]):
        d = g.edges[a, b]
        total += rg.g.edges[d.get("root", (a, b))]["length"] * d.get("frac", 1.0)
    return total


def _hit(rg, g, e, inc, effects) -> bool:
    """Does this edge of the per-request graph lie on the incident? (same rule routing uses)"""
    d = g.edges[e]
    root = d.get("root", e)
    if not any(i.id == inc.id for i in effects.get(root, ())):
        return False
    if d.get("frac", 1.0) < 1.0:
        a, b = e
        return _point_segment_m(inc.lat, inc.lon, g.nodes[a]["lat"], g.nodes[a]["lon"],
                                g.nodes[b]["lat"], g.nodes[b]["lon"]) <= config.INCIDENT_EDGE_RADIUS_M
    return True


def _turn(g, a, b, c) -> str:
    b1 = bearing_deg(g.nodes[a]["lat"], g.nodes[a]["lon"], g.nodes[b]["lat"], g.nodes[b]["lon"])
    b2 = bearing_deg(g.nodes[b]["lat"], g.nodes[b]["lon"], g.nodes[c]["lat"], g.nodes[c]["lon"])
    d = (b2 - b1 + 540) % 360 - 180   # positive = clockwise = right
    if abs(d) > 150:
        return "U-turn"
    if abs(d) < 25:
        return "straight"
    return "right" if d > 0 else "left"


def _road_name(rg, g, e) -> str | None:
    root = g.edges[e].get("root", e)
    return rg.g.edges[root].get("name") or None


def _diverge_index(usual, alt) -> int:
    i = 0
    while i + 1 < min(len(usual), len(alt)) and usual[i + 1] == alt[i + 1]:
        i += 1
    return i


def _label(inc) -> str:
    return TYPE_LABEL[inc.type]


def _is_junction(rg, g, n) -> bool:
    return (not str(n).startswith("__") and not g.nodes[n].get("shape")
            and len(set(rg.g.successors(n)) | set(rg.g.predecessors(n))) >= 3)


def _latest_turnoff(rg, g, veh, fx_all, usual, alt, d, k, incidents, src):
    """The best way round may leave the usual route early. If a LATER junction (up to the one just before the blocked
    stretch) gives a way round that costs at most ALERT_TURNOFF_SLACK_S more, use it: the person gets more time to
    react. Returns (index of the turn-off on the usual route, the full route using it)."""
    best_t = _path_time(rg, g, alt, veh, fx_all, src, incidents)
    w = _weight(rg, g, veh, fx_all, src, incidents)
    for i in range(min(k, len(usual) - 2), d, -1):
        node = usual[i]
        if not _is_junction(rg, g, node):
            continue
        prev = usual[i - 1]
        try:
            tail = nx.shortest_path(g, node, usual[-1],
                                    weight=lambda u, v, dd, n=node, p=prev: None if (u == n and v == p) else w(u, v, dd))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        cand = usual[:i] + tail
        t = _path_time(rg, g, cand, veh, fx_all, src, incidents)
        if t <= best_t + config.ALERT_TURNOFF_SLACK_S:
            return i, cand
    return d, alt


def _alert(sub, kind, inc, text, voice, ts, **extra) -> dict:
    return {"id": f"al-{secrets.token_hex(4)}", "ts": ts, "kind": kind, "incident_id": inc.id if inc else None,
            "title": inc.title if inc else None, "text": text, "voice": voice,
            "speak": sub.voice, "simulated": bool(inc and any(s.simulated for s in inc.signals)), **extra}


# ------------------------------------------------------------------ the core: one person, one trip
def plan_trip(rg: RoadGraph, sub: Subscriber, src_ll, dst_ll, incidents: list, ts: float, mode: str) -> dict | None:
    """Compare the route this person would take (knowing only what they know) with the best route around the
    published incidents. Returns an alert dict, or None if there is nothing to say yet / at all.
    mode 'trip': they are on the road (warn when the turn-off is near). mode 'pretrip': they have not left."""
    g, src, dst, _, _ = graph_with_endpoints(rg, src_ll[0], src_ll[1], dst_ll[0], dst_ll[1])
    if src is None or dst is None or src == dst:
        return None
    veh = sub.vehicle
    unknown = [i for i in incidents if i.id not in sub.known and sub.alerted.get(i.id) != f"ok:{i.severity}"
               and not (i.id in sub.alerted and not sub.alerted[i.id].startswith("ok:"))]
    if not unknown:
        return None
    fx_known = incident_effects(rg, [i for i in incidents if i.id in sub.known])
    usual = _path(rg, g, src, dst, veh, fx_known)
    if not usual:
        return None
    fx_all = incident_effects(rg, incidents)
    first_hit: dict[str, int] = {}
    for idx, e in enumerate(zip(usual, usual[1:])):
        for inc in unknown:
            if inc.id not in first_hit and _hit(rg, g, e, inc, fx_all):
                first_hit[inc.id] = idx
    if not first_hit:
        return None                       # none of these incidents lies on their route: say nothing
    hits = sorted(first_hit, key=first_hit.get)
    lead = next(i for i in unknown if i.id == hits[0])
    road = rg.road_name(lead.lat, lead.lon)
    where = f" on {road}" if road else ""                # unnamed roads happen on a real map: say nothing, not nonsense
    alt = _path(rg, g, src, dst, veh, fx_all, incs=incidents)

    if alt is None:                       # nowhere to go: be honest
        for h in hits:
            sub.known.add(h)
            sub.alerted[h] = "noalt"
        msg = f"{_label(lead)}{where} blocks your route and no way around was found. Expect a long delay."
        return _alert(sub, "no_alternative", lead, msg, msg, ts)
    if alt == usual:                      # slow but still their best way: nothing to tell
        for h in hits:   # remembered with the severity: if an officer later raises it, they are checked again
            sub.alerted[h] = f"ok:{next(i.severity for i in unknown if i.id == h)}"
        return None

    d = _diverge_index(usual, alt)
    d, alt = _latest_turnoff(rg, g, veh, fx_all, usual, alt, d, first_hit[hits[0]], incidents, src)
    dist = _path_len(rg, g, usual[:d + 1])
    t_to = dist / SPEED_MPS.get(veh, 8.0)
    if mode == "trip" and t_to > config.ALERT_WARN_HORIZON_S:
        return None                       # the turn-off is still far: keep checking on every position update

    onto = _road_name(rg, g, (alt[d], alt[d + 1]))
    onto_txt = f" onto {onto}" if onto else ""
    free = _path_time(rg, g, usual, veh, {})
    extra = max(0.0, round((_path_time(rg, g, alt, veh, fx_all, src, incidents) - free) / 60, 1))
    # at the very start the person's heading is unknown: assume they face along their usual route
    start = (g.nodes[src]["lat"], g.nodes[src]["lon"])
    back = 0.0
    if d == 0:
        b_usual = bearing_deg(*start, g.nodes[usual[1]]["lat"], g.nodes[usual[1]]["lon"])
        b_alt = bearing_deg(*start, g.nodes[alt[1]]["lat"], g.nodes[alt[1]]["lon"])
        back = (b_alt - b_usual + 540) % 360 - 180      # how far the way round points away from where they face
    uturn = mode == "trip" and d == 0 and abs(back) > 120
    late = mode == "trip" and not uturn and t_to < config.ALERT_MIN_LEAD_S
    others = [h for h in hits[1:]]
    for h in hits:
        sub.known.add(h)
        sub.alerted[h] = "uturn" if uturn else ("pretrip" if mode == "pretrip" else "rerouted")

    if mode == "pretrip":
        kind = "pre_trip"
        voice = f"{_label(lead)}{where} is on your usual route. Start{onto_txt or ' by another road'}."
        text = f"{voice} Leave at your usual time: +{extra:g} min."
    elif uturn:
        kind = "uturn"
        voice = f"{_label(lead)} ahead{where}. Turn back."
        text = f"{voice} Make a U-turn when it is safe. +{extra:g} min."
    else:
        kind = "in_trip"
        if d == 0:
            turn = "straight" if abs(back) < 25 else ("right" if back > 0 else "left")
        else:
            turn = _turn(g, usual[d - 1], usual[d], alt[d + 1])
        act = ("Continue straight" if turn == "straight" else f"Turn {turn}") + onto_txt
        voice = f"{_label(lead)} ahead{where}. {act}."
        text = f"{voice} About {int(dist)} m. +{extra:g} min." + (" Late warning: little time to react." if late else "")
    return _alert(sub, kind, lead, text, voice, ts, late=late, lead_s=round(t_to) if mode == "trip" else None,
                  dist_m=int(dist) if mode == "trip" else None, extra_min=extra, also=others,
                  route=rg.path_coords(alt, g))


# ------------------------------------------------------------------ delivery
def deliver(store: Store, sub: Subscriber, alert: dict):
    with store.lock:
        sub.inbox.append(alert)
        del sub.inbox[:-INBOX_MAX]
        store.alert_log.appendleft({"ts": alert["ts"], "kind": alert["kind"], "incident_id": alert["incident_id"],
                                    "telegram": bool(sub.telegram_chat and channels.telegram_configured())})
    channels.send_telegram_async(sub.telegram_chat, alert["text"])


def published(store: Store, ts: float) -> list[Incident]:
    from .services import is_published
    return [i for i in store.snapshot() if is_published(i, ts)]


def upcoming_works(store: Store, start: float, horizon_s: float = 3600) -> list[Incident]:
    """Scheduled construction that overlaps the trip, even if it has not started yet."""
    out = []
    for i in store.snapshot():
        if i.type == "construction" and i.status in ("Reported", "Verified", "Responding") and i.starts and i.ends:
            if i.starts <= start + horizon_s and i.ends >= start:
                out.append(i)
    return out


def evaluate_in_trip(rg: RoadGraph, store: Store, sub: Subscriber, ts: float, force: bool = False) -> dict | None:
    pos = sub.last_pos
    if not pos or not sub.dest or ts - pos["ts"] > config.POSITION_FRESH_S:
        return None
    with store.lock:
        if not force and ts - sub.last_eval < config.ALERT_MIN_EVAL_GAP_S:
            return None   # position updates can arrive every second: one route check per gap is plenty
        sub.last_eval = ts
        alert = plan_trip(rg, sub, (pos["lat"], pos["lon"]), sub.dest, published(store, ts), ts, "trip")
    if alert:
        deliver(store, sub, alert)
    return alert


def evaluate_pretrip(rg: RoadGraph, store: Store, sub: Subscriber, ts: float) -> dict | None:
    if not (sub.home and sub.work and sub.depart_at):
        return None
    if not (sub.depart_at - config.PRETRIP_LEAD_S <= ts < sub.depart_at):
        return None
    if sub.last_pos and ts - sub.last_pos["ts"] <= config.POSITION_FRESH_S and sub.dest:
        return None                       # already on the road: the in-trip check handles them
    with store.lock:
        incs = {i.id: i for i in published(store, ts) + upcoming_works(store, sub.depart_at)}
        alert = plan_trip(rg, sub, sub.home, sub.work, list(incs.values()), ts, "pretrip")
    if alert:
        deliver(store, sub, alert)
    return alert


def evaluate_everyone(rg: RoadGraph, store: Store, ts: float | None = None) -> int:
    ts = ts or now()
    n = 0
    for sub in list(store.subscribers.values()):
        for fn in (lambda *a: evaluate_in_trip(*a, force=True), evaluate_pretrip):
            try:
                if fn(rg, store, sub, ts):
                    n += 1
            except Exception:             # one person's bad data must not stop the others
                import logging
                logging.getLogger("roadpulse.alerts").exception("alert evaluation failed for %s", sub.id)
    return n


def on_status(rg: RoadGraph, store: Store, inc: Incident, status: str, ts: float):
    """Called on every incident status change."""
    if status in ("Verified", "Responding"):
        evaluate_everyone(rg, store, ts)
    elif status == "Cleared":
        clear_alerts(rg, store, inc, ts)


def clear_alerts(rg: RoadGraph, store: Store, inc: Incident, ts: float) -> int:
    road = rg.road_name(inc.lat, inc.lon) or "the road"
    n = 0
    for sub in list(store.subscribers.values()):
        if sub.alerted.get(inc.id) in ("rerouted", "uturn", "pretrip", "noalt"):
            msg = f"Road clear: {road} has reopened ({_label(inc).lower()} cleared)."
            sub.alerted[inc.id] = "cleared"
            deliver(store, sub, _alert(sub, "clear", inc, msg, msg, ts))
            n += 1
    return n


def tick(rg: RoadGraph, store: Store, ts: float | None = None):
    """Periodic: roll saved departure times forward and send due pre-trip alerts."""
    ts = ts or now()
    for sub in list(store.subscribers.values()):
        if sub.depart_hhmm and sub.depart_at and ts > sub.depart_at + 3600:
            sub.depart_at = next_departure(sub.depart_hhmm, ts)
            sub.alerted = {k: v for k, v in sub.alerted.items() if v == "cleared"}  # a new day: tell them again
            sub.known = set()
        try:
            evaluate_pretrip(rg, store, sub, ts)
        except Exception:
            import logging
            logging.getLogger("roadpulse.alerts").exception("pre-trip check failed for %s", sub.id)


# ------------------------------------------------------------------ boards (simulated roadside displays)
def boards(rg: RoadGraph) -> list[dict]:
    """The board sites: data/boards.json if present, else the busiest junctions, spread apart (boards cost money)."""
    cache = rg.__dict__.get("_boards")
    if cache is not None:
        return cache
    import json
    import os
    out: list[dict] = []
    path = os.path.abspath(config.BOARDS_PATH)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for i, b in enumerate(json.load(f), 1):
                out.append({"id": b.get("id", f"b{i}"), "name": b["name"], "node": rg.nearest_node(b["lat"], b["lon"])})
    else:
        from .geo import haversine_m
        clat, clon = rg.centre()
        cands = []
        for n, d in rg.g.nodes(data=True):
            if d.get("shape"):
                continue
            deg = len(set(rg.g.successors(n)) | set(rg.g.predecessors(n)))
            if deg >= 3:
                cands.append((-deg, haversine_m(clat, clon, d["lat"], d["lon"]), n))
        cands.sort()
        chosen: list = []
        for _, _, n in cands:
            lat, lon = rg.node_latlon(n)
            if all(haversine_m(lat, lon, *rg.node_latlon(c)) >= 350 for c in chosen):
                chosen.append(n)
            if len(chosen) >= config.BOARD_COUNT:
                break
        for i, n in enumerate(chosen, 1):
            names = sorted({rg.g.edges[e].get("name") for e in [(n, s) for s in rg.g.successors(n)] if rg.g.edges[e].get("name")})
            out.append({"id": f"b{i}", "name": " / ".join(names[:2]) or f"Junction {i}", "node": n})
    for b in out:
        b["lat"], b["lon"] = rg.node_latlon(b["node"])
    rg.__dict__["_boards"] = out
    return out


def board_view(rg: RoadGraph, board: dict, incidents: list) -> dict:
    """What a board at this junction shows: for each published incident within BOARD_LOOKAHEAD_M, which road leaving
    the junction leads to it (the shortest way there), and the roads that do not."""
    node = board["node"]
    fx = incident_effects(rg, incidents)
    dist, paths = nx.single_source_dijkstra(rg.g, node, cutoff=config.BOARD_LOOKAHEAD_M, weight="length")
    flags = {}
    for e, incs in fx.items():
        u, v = e
        if u in dist:
            path = paths[u] + [v]
            first = path[1] if len(path) > 1 else None
            if first is None:
                continue
            for inc in incs:
                cur = flags.get((first, inc.id))
                if cur is None or dist[u] < cur["dist"]:
                    flags[(first, inc.id)] = {"first": first, "dist": dist[u], "inc": inc}
    lines, bad = [], set()
    for (first, _), f in sorted(flags.items(), key=lambda kv: kv[1]["dist"]):
        name = rg.g.edges[node, first].get("name") or "this road"
        inc = f["inc"]
        bad.add(first)
        key = (name, inc.id)
        if any((x["road"], x["incident"]) == key for x in lines):
            continue   # several unnamed side roads would repeat the same line
        lines.append({"road": name, "incident": inc.id, "title": _label(inc), "severity": inc.severity,
                      "metres": int(round(f["dist"] / 10.0) * 10)})
    alts = sorted({rg.g.edges[node, s].get("name") or "side road" for s in rg.g.successors(node) if s not in bad})
    return {"id": board["id"], "name": board["name"], "lat": board["lat"], "lon": board["lon"], "lines": lines,
            "alternatives": alts, "simulated": True}
