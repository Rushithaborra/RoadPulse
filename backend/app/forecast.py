"""Rain forecast (Open-Meteo, free, no key) and the per-hotspot flood countdown."""
from __future__ import annotations

import time

import httpx

from . import config
from .store import HotspotState, Store

_cache: dict = {"ts": 0.0, "data": None, "error": None, "failed_at": 0.0, "good_at": 0.0}
GOOD_TTL_S = 600        # refresh a good forecast every 10 min
FAIL_BACKOFF_S = 120    # after a failure, do not call the API again for 2 min (keeps requests fast offline)
STALE_LIMIT_S = 3 * 3600  # keep using the last good forecast for up to 3 h


def fetch_forecast(lat: float, lon: float) -> dict | None:
    """Next hours of rain in mm/h. Returns the last good forecast (up to 3 h old) or None."""
    t = time.time()
    if _cache["data"] is not None and t - _cache["good_at"] < GOOD_TTL_S:
        return _cache["data"]
    if t - _cache["failed_at"] < FAIL_BACKOFF_S:
        return _cache["data"] if _cache["data"] is not None and t - _cache["good_at"] < STALE_LIMIT_S else None
    try:
        r = httpx.get(config.OPEN_METEO_URL, params={
            "latitude": lat, "longitude": lon, "hourly": "precipitation",
            "forecast_days": 1, "timezone": "auto",
        }, timeout=4)
        r.raise_for_status()
        j = r.json()
        times, rain = j["hourly"]["time"], j["hourly"]["precipitation"]
        now_hour = time.strftime("%Y-%m-%dT%H:00", time.localtime())
        idx = next((i for i, t in enumerate(times) if t >= now_hour), 0)
        data = {"next_hours_mm": rain[idx: idx + 6], "source": "open-meteo"}
        _cache.update(ts=t, data=data, error=None, good_at=t)
        return data
    except Exception as e:  # network blocked, API down, etc.
        _cache.update(failed_at=t, error=str(e))
        return _cache["data"] if _cache["data"] is not None and t - _cache["good_at"] < STALE_LIMIT_S else None


def current_rain(store: Store) -> tuple[float, str]:
    """Rain intensity for the next hour (mm/h) and where it came from."""
    if store.rain_override is not None:
        return store.rain_override, "manual override set by an officer, not a forecast"
    f = fetch_forecast(config.CENTER_LAT, config.CENTER_LON)
    if f and f["next_hours_mm"]:
        return float(f["next_hours_mm"][0]), "open-meteo forecast"
    return 0.0, "no forecast available"


def countdown(h: HotspotState, rain_mm_per_hr: float) -> dict:
    """Minutes until the hotspot reaches each severity level, assuming the current rain continues.

    Model: depth rises at k * rain - drain (cm per hour). Rule-based starting point; replace with a
    regression fitted to this hotspot's history after one monsoon of data.
    """
    rate = h.k_rain_cm_per_mm * rain_mm_per_hr - h.drain_cm_per_hr  # cm per hour
    out = {"hotspot_id": h.id, "name": h.name, "lat": h.lat, "lon": h.lon,
           "depth_cm": round(h.depth_cm, 1), "rise_cm_per_hr": round(rate, 1), "eta_min": {}}
    for label, level in (("Low", config.DEPTH_LOW), ("Medium", config.DEPTH_MEDIUM),
                         ("Severe", config.DEPTH_SEVERE)):
        if h.depth_cm >= level:
            out["eta_min"][label] = 0
        elif rate > 0:
            out["eta_min"][label] = round((level - h.depth_cm) / rate * 60)
        else:
            out["eta_min"][label] = None
    nxt = next(((k, v) for k, v in out["eta_min"].items() if v not in (None, 0)), None)
    if rate <= 0:
        out["message"] = "Not expected to rise at current rain"
    elif nxt:
        out["message"] = f"{nxt[0]} in about {nxt[1]} min"
    else:
        out["message"] = "At or above Severe"
    out["risk"] = "high" if (nxt and nxt[1] <= 30) or h.depth_cm >= config.DEPTH_MEDIUM else ("watch" if rate > 0 else "normal")
    return out
