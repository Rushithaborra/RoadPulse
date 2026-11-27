"""Shared data models. The Incident shape is the integration contract between workstreams (see docs/incident_schema.json)."""
from __future__ import annotations

import time
import uuid
from typing import Annotated, Literal, Optional

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
    simulated: bool = False  # sent by the demo simulator, not a real device or person


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


# ---- API inputs (validated at the model level) ----

from pydantic import field_validator, model_validator  # noqa: E402

MAX_FUTURE_SKEW_S = 120          # device clocks may run slightly ahead
MAX_READING_AGE_S = 6 * 3600     # buffered offline readings older than this are rejected

Lat = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Lon = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
ShortId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")]


def _check_ts(ts: float | None) -> float | None:
    if ts is None:
        return None
    t = now()
    if ts > t + MAX_FUTURE_SKEW_S:
        raise ValueError(f"timestamp is in the future (more than {MAX_FUTURE_SKEW_S} s ahead of server time)")
    if ts < t - MAX_READING_AGE_S:
        raise ValueError(f"timestamp is older than {MAX_READING_AGE_S // 3600} h")
    return ts


class SensorReading(BaseModel):
    sensor_id: ShortId
    depth_cm: float = Field(ge=0, le=300, allow_inf_nan=False)
    ts: Optional[float] = Field(default=None, allow_inf_nan=False)
    simulated: bool = False  # set by the demo simulator so the UI can label the data

    _ts = field_validator("ts")(classmethod(lambda cls, v: _check_ts(v)))


class CitizenReport(BaseModel):
    type: IncidentType
    lat: Lat
    lon: Lon
    reporter_id: ShortId = "anonymous"  # client-supplied and unauthenticated: never treated as an identity
    note: Optional[str] = Field(default=None, max_length=200)
    photo_url: Optional[str] = Field(default=None, max_length=500)
    simulated: bool = False  # set by the demo simulator so the UI can label the data


class ProbeSample(BaseModel):
    vehicle_id: ShortId
    lat: Lat
    lon: Lon
    speed_kph: float = Field(ge=0, le=150, allow_inf_nan=False)
    ts: Optional[float] = Field(default=None, allow_inf_nan=False)
    simulated: bool = False  # set by the demo simulator so the UI can label the data

    _ts = field_validator("ts")(classmethod(lambda cls, v: _check_ts(v)))


class PlannedWork(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    lat: Lat
    lon: Lon
    starts: float = Field(allow_inf_nan=False)
    ends: float = Field(allow_inf_nan=False)
    full_closure: bool = True
    agency: str = Field(default="Municipality", min_length=1, max_length=80)
    simulated: bool = False  # set by the demo simulator so the UI can label the data

    @model_validator(mode="after")
    def _window(self):
        if self.ends <= self.starts:
            raise ValueError("ends must be after starts")
        if self.ends - self.starts > 31 * 86400:
            raise ValueError("planned works window longer than 31 days")
        if self.ends < now() - 3600:
            raise ValueError("planned works window has already ended")
        return self


class RouteRequest(BaseModel):
    from_lat: Lat
    from_lon: Lon
    to_lat: Lat
    to_lon: Lon
    vehicle: VehicleClass = "car"
    alternatives: int = Field(default=3, ge=1, le=4)


class RainOverride(BaseModel):
    rain_mm_per_hr: Optional[float] = Field(default=None, ge=0, le=200, allow_inf_nan=False)  # None clears it


class WhatIfRequest(BaseModel):
    incident_id: str
    trips: int = Field(default=400, ge=50, le=2000)
    seed: int = 7
