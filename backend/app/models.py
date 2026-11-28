"""Shared data models. The Incident shape is the integration contract between workstreams (see docs/incident_schema.json)."""
from __future__ import annotations

import time
import uuid
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

IncidentType = Literal["flood", "accident", "blockage", "construction", "slowdown"]
Severity = Literal["Low", "Medium", "Severe", "Closed"]
Status = Literal["Reported", "Verified", "Responding", "Cleared"]
Source = Literal["sensor", "probe", "cctv", "report", "planned", "officer", "field"]
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
    dispatched: list[dict] = Field(default_factory=list)  # units sent by an officer: {unit, by, at}
    severity_set: bool = False  # an officer chose the severity (e.g. road fully closed); rules no longer override it
    # Two-check review of citizen reports (see review.py): "control" = control-room check, "field" = on-ground police
    # check. Each is {"by", "at", "decision", "note"}. urgent = control room fast-tracked it before the field check.
    checks: dict[str, dict] = Field(default_factory=dict)
    urgent: bool = False


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


DispatchUnit = Literal["police", "ambulance", "tow", "drainage", "marshal"]


class Dispatch(BaseModel):
    unit: DispatchUnit
    note: Optional[str] = Field(default=None, max_length=120)


HHMM = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class SubscriberIn(BaseModel):
    """A commuter who wants alerts. Location is only stored with explicit consent."""
    name: str = Field(min_length=1, max_length=60)
    vehicle: VehicleClass = "two_wheeler"
    voice: bool = False                       # commute mode: alerts are spoken by the phone page
    telegram_chat: Optional[str] = Field(default=None, max_length=32, pattern=r"^-?\d+$")
    home_lat: Optional[Lat] = None
    home_lon: Optional[Lon] = None
    work_lat: Optional[Lat] = None
    work_lon: Optional[Lon] = None
    depart_hhmm: Optional[HHMM] = None        # usual departure time (India Standard Time)
    consent_location: bool = False

    @model_validator(mode="after")
    def _checks(self):
        if not self.consent_location:
            raise ValueError("consent_location must be true: alerts need the commuter's location")
        for a, b in ((self.home_lat, self.home_lon), (self.work_lat, self.work_lon)):
            if (a is None) != (b is None):
                raise ValueError("give both latitude and longitude")
        return self


class TripStart(BaseModel):
    """Start (or change) the current trip. dest is 'work', 'home' or explicit coordinates."""
    dest: Optional[Literal["work", "home"]] = None
    lat: Optional[Lat] = None
    lon: Optional[Lon] = None
    depart_in_min: Optional[float] = Field(default=None, ge=0, le=1440, allow_inf_nan=False)  # next departure

    @model_validator(mode="after")
    def _one(self):
        if self.dest is None and (self.lat is None or self.lon is None) and self.depart_in_min is None:
            raise ValueError("give dest, or lat and lon, or depart_in_min")
        return self


class PositionIn(BaseModel):
    lat: Lat
    lon: Lon
    speed_kph: Optional[float] = Field(default=None, ge=0, le=200, allow_inf_nan=False)
    ts: Optional[float] = Field(default=None, allow_inf_nan=False)
    simulated: bool = False

    _ts = field_validator("ts")(classmethod(lambda cls, v: _check_ts(v)))


class PlacesIn(BaseModel):
    """Change saved places and usual departure (the Telegram bot sets them from shared locations)."""
    home_lat: Optional[Lat] = None
    home_lon: Optional[Lon] = None
    work_lat: Optional[Lat] = None
    work_lon: Optional[Lon] = None
    depart_hhmm: Optional[HHMM] = None

    @model_validator(mode="after")
    def _pairs(self):
        for a, b in ((self.home_lat, self.home_lon), (self.work_lat, self.work_lon)):
            if (a is None) != (b is None):
                raise ValueError("give both latitude and longitude")
        if all(v is None for v in (self.home_lat, self.work_lat, self.depart_hhmm)):
            raise ValueError("nothing to change")
        return self
