"""Shared data models. The Incident shape is the integration contract between workstreams (see docs/incident_schema.json)."""
from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

IncidentType = Literal["flood", "accident", "blockage", "construction", "slowdown"]
Severity = Literal["Low", "Medium", "Severe", "Closed"]
Status = Literal["Reported", "Verified", "Responding", "Cleared"]
Source = Literal["sensor", "probe", "cctv", "report", "planned", "officer"]
VehicleClass = Literal["two_wheeler", "auto", "car", "ambulance", "bus"]

SEVERITY_ORDER = {"Low": 0, "Medium": 1, "Severe": 2, "Closed": 3}


def now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Signal(BaseModel):
    source: Source
    type: IncidentType
    lat: float
    lon: float
    ts: float = Field(default_factory=now)
    weight: float = 0.5
    depth_cm: Optional[float] = None
    source_id: Optional[str] = None  # sensor id, reporter id, vehicle id
    note: Optional[str] = None


class Incident(BaseModel):
    id: str = Field(default_factory=lambda: new_id("inc"))
    type: IncidentType
    lat: float
    lon: float
    severity: Severity = "Low"
    confidence: float = 0.0
    status: Status = "Reported"
    depth_cm: Optional[float] = None
    title: str = ""
    signals: list[Signal] = Field(default_factory=list)
    status_times: dict[str, float] = Field(default_factory=dict)  # status -> first time entered
    created: float = Field(default_factory=now)
    updated: float = Field(default_factory=now)
    starts: Optional[float] = None  # planned works window
    ends: Optional[float] = None
    clear_candidate_since: Optional[float] = None
    cleared_reason: Optional[str] = None
    affected_vehicles: list[str] = Field(default_factory=list)  # classes that cannot pass
    hotspot_id: Optional[str] = None

    @property
    def published(self) -> bool:
        from .config import PUBLISH_CONFIDENCE
        return self.status != "Cleared" and self.confidence >= PUBLISH_CONFIDENCE


# ---- API inputs ----

class SensorReading(BaseModel):
    sensor_id: str
    depth_cm: float
    ts: Optional[float] = None


class CitizenReport(BaseModel):
    type: IncidentType
    lat: float
    lon: float
    reporter_id: str = "anonymous"
    note: Optional[str] = None
    photo_url: Optional[str] = None


class ProbeSample(BaseModel):
    vehicle_id: str
    lat: float
    lon: float
    speed_kph: float
    ts: Optional[float] = None


class PlannedWork(BaseModel):
    title: str
    lat: float
    lon: float
    starts: float
    ends: float
    full_closure: bool = True
    agency: str = "Municipality"


class RouteRequest(BaseModel):
    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float
    vehicle: VehicleClass = "car"
    alternatives: int = 3


class RainOverride(BaseModel):
    rain_mm_per_hr: Optional[float] = None  # None clears the override


class WhatIfRequest(BaseModel):
    incident_id: str
    trips: int = 400
    seed: int = 7
