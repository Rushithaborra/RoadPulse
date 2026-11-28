"""Two-check review of citizen reports.

The agreed flow:

    citizen report ─▶ CONTROL ROOM check ─▶ FIELD POLICE check (on the ground) ─▶ published to commuters
                          │                        │
                          │                        └─ not found ─▶ rejected (or, if urgent, flagged back to control)
                          ├─ reject ─▶ rejected
                          └─ URGENT fast-track ─▶ published now, field check still requested

A citizen report alone never publishes, and neither check alone publishes it. The only shortcut is the control room
marking a real emergency as urgent; the field team is then still asked to confirm it, and a "not found" from the field
is sent back to the control room instead of silently unpublishing a road that someone said was dangerous.

Incidents that already carry trusted machine or agency evidence (a keyed water sensor, CCTV, registered planned works)
keep the original rule: they verify on that evidence, and one officer decision is enough.
"""
from __future__ import annotations

from fastapi import HTTPException

from . import fusion, services
from .models import Incident, Signal, now
from .store import Store

MACHINE_SOURCES = {"sensor", "cctv", "planned"}


def needs_two_checks(inc: Incident) -> bool:
    """True when the incident rests on public reports (and possibly untrusted speed data) only."""
    return not any(s.source in MACHINE_SOURCES for s in inc.signals)


def stage(inc: Incident) -> str:
    """Where the incident is in the review. Stable strings; the UIs translate them."""
    if inc.status == "Cleared":
        return "rejected" if (inc.cleared_reason or "").startswith("rejected") else "closed"
    if not needs_two_checks(inc):
        return "trusted_evidence"
    control, field = inc.checks.get("control"), inc.checks.get("field")
    if inc.urgent:
        if field and field["decision"] == "not_found":
            return "field_disputed"
        if field and field["decision"] == "confirmed":
            return "confirmed"
        return "urgent_awaiting_field"
    if control and field and field["decision"] == "confirmed":
        return "confirmed"
    if control:
        return "with_field"
    return "control_review"


PUBLIC_STAGE = {   # what a citizen sees about their own report (no names, no evidence)
    "control_review": "With the traffic control room",
    "with_field": "Sent to traffic police on the ground",
    "urgent_awaiting_field": "Published as urgent; police are on the way to confirm",
    "field_disputed": "Under review by the control room",
    "confirmed": "Confirmed by traffic police",
    "trusted_evidence": "Confirmed by road sensors",
    "rejected": "Could not be confirmed",
    "closed": "Road cleared",
}


def view(inc: Incident, staff: bool) -> dict:
    st = stage(inc)
    out = {"stage": st, "two_check": needs_two_checks(inc), "urgent": inc.urgent, "label": PUBLIC_STAGE[st]}
    if staff:
        out["checks"] = inc.checks
    return out


def _record(inc: Incident, who: str, kind: str, decision: str, note: str | None, ts: float):
    inc.checks[kind] = {"by": who, "at": ts, "decision": decision, "note": note}


# ---------------------------------------------------------------- control room (check 1)

def control_forward(store: Store, inc: Incident, who: str, note: str | None, severity: str | None) -> str:
    """Control room accepts the report as plausible and sends it to the field team. Nothing is published."""
    st = stage(inc)
    if st not in ("control_review", "with_field"):
        raise HTTPException(409, f"This report is not waiting for the control room (stage: {st})")
    ts = now()
    if severity and inc.type != "flood":
        inc.severity, inc.severity_set = severity, True
        fusion.refresh(store, inc)
    _record(inc, who, "control", "forwarded", note, ts)
    store.log(ts, f"{inc.title}: checked by control room ({who}), sent to field police")
    inc.updated = ts
    return "forwarded"


def control_urgent(store: Store, inc: Incident, who: str, note: str | None, severity: str | None) -> str:
    """A real emergency: publish now, keep the field check open."""
    if inc.status == "Cleared":
        raise HTTPException(409, "Incident already cleared")
    ts = now()
    if severity and inc.type != "flood":
        inc.severity, inc.severity_set = severity, True
    inc.urgent = True
    _record(inc, who, "control", "urgent", note, ts)
    inc.signals.append(Signal(source="officer", type=inc.type, lat=inc.lat, lon=inc.lon, weight=1.0,
                              source_id=who, note=note or "urgent fast-track by control room"))
    fusion.refresh(store, inc)
    if inc.status == "Reported":
        fusion.set_status(store, inc, "Verified", ts, "URGENT fast-track by control room, field check pending", who)
    store.log(ts, f"{inc.title}: URGENT, published by control room ({who}); field confirmation requested")
    inc.updated = ts
    return "urgent"


# ---------------------------------------------------------------- field police (check 2)

def field_confirm(store: Store, inc: Incident, who: str, note: str | None, severity: str | None) -> None:
    st = stage(inc)
    if st not in ("with_field", "urgent_awaiting_field", "field_disputed"):
        raise HTTPException(409, "This report has not been sent to the field team"
                            if st == "control_review" else f"Nothing to confirm (stage: {st})")
    ts = now()
    if severity and inc.type != "flood":
        inc.severity, inc.severity_set = severity, True
    _record(inc, who, "field", "confirmed", note, ts)
    inc.signals.append(Signal(source="field", type=inc.type, lat=inc.lat, lon=inc.lon, weight=0.9, source_id=who,
                              note=note or "confirmed on the ground"))
    fusion.refresh(store, inc)
    if inc.status == "Reported":
        fusion.set_status(store, inc, "Verified", ts, "two checks passed: control room and field police", who)
    services.reward_reporters(store, inc, True)
    store.log(ts, f"{inc.title}: confirmed on the ground by {who}")
    inc.updated = ts


def field_not_found(store: Store, inc: Incident, who: str, note: str | None) -> None:
    st = stage(inc)
    if st not in ("with_field", "urgent_awaiting_field"):
        raise HTTPException(409, "This report has not been sent to the field team"
                            if st == "control_review" else f"Nothing to check (stage: {st})")
    ts = now()
    _record(inc, who, "field", "not_found", note, ts)
    if inc.urgent:
        # an urgent, published incident is not withdrawn by one field visit: the control room decides
        store.log(ts, f"{inc.title}: field police ({who}) could NOT confirm it; control room to decide")
    else:
        services.reward_reporters(store, inc, False)
        fusion.set_status(store, inc, "Cleared", ts, "rejected: not found on the ground by field police", who)
    inc.updated = ts
