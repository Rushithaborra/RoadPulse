"""Officer authentication (signed bearer tokens), device keys and a simple per-IP rate limiter."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import defaultdict

from fastapi import Header, HTTPException, Request

SECRET = os.getenv("ROADPULSE_SECRET") or secrets.token_hex(32)  # set in .env so tokens survive restarts
TOKEN_TTL_S = 8 * 3600

# username -> (password, role). Set OFFICER_USERS="officer1:pass1,supervisor:pass2" in .env.
def _users() -> dict[str, tuple[str, str]]:
    raw = os.getenv("OFFICER_USERS", "officer:roadpulse-demo")
    out = {}
    for pair in raw.split(","):
        if ":" in pair:
            u, p = pair.split(":", 1)
            out[u.strip()] = (p.strip(), "supervisor" if u.strip().startswith("supervisor") else "officer")
    return out


DEVICE_KEYS = {k.strip() for k in os.getenv("DEVICE_KEYS", "dev-sensor-key").split(",") if k.strip()}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(username: str, password: str) -> dict | None:
    user = _users().get(username)
    if not user or not hmac.compare_digest(user[0], password):
        return None
    payload = {"sub": username, "role": user[1], "exp": int(time.time()) + TOKEN_TTL_S}
    body = _b64(json.dumps(payload).encode())
    sig = _b64(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
    return {"token": f"{body}.{sig}", "role": user[1], "expires_in": TOKEN_TTL_S}


def verify_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(good, sig):
            return None
        payload = json.loads(_unb64(body))
        return payload if payload["exp"] > time.time() else None
    except Exception:
        return None


def require_officer(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Officer login required")
    claims = verify_token(authorization.split(" ", 1)[1])
    if not claims or claims.get("role") not in ("officer", "supervisor"):
        raise HTTPException(401, "Invalid or expired token")
    return claims


def optional_officer(authorization: str | None = Header(default=None)) -> dict | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return verify_token(authorization.split(" ", 1)[1])


def require_device(x_device_key: str | None = Header(default=None)) -> str:
    if not x_device_key or x_device_key not in DEVICE_KEYS:
        raise HTTPException(401, "Unknown device key")
    return x_device_key


class RateLimiter:
    """Token bucket per (client IP, bucket name)."""

    def __init__(self, rate_per_min: float, burst: int):
        self.rate = rate_per_min / 60.0
        self.burst = burst
        self.state: dict[tuple, tuple[float, float]] = defaultdict(lambda: (float(burst), time.time()))

    def allow(self, key: tuple) -> bool:
        tokens, last = self.state[key]
        t = time.time()
        tokens = min(self.burst, tokens + (t - last) * self.rate)
        if tokens < 1:
            self.state[key] = (tokens, t)
            return False
        self.state[key] = (tokens - 1, t)
        return True


REPORT_LIMIT = RateLimiter(rate_per_min=float(os.getenv("REPORT_RATE_PER_MIN", 6)), burst=5)
DEVICE_LIMIT = RateLimiter(rate_per_min=float(os.getenv("DEVICE_RATE_PER_MIN", 600)), burst=100)
ROUTE_LIMIT = RateLimiter(rate_per_min=float(os.getenv("ROUTE_RATE_PER_MIN", 120)), burst=30)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def limit(limiter: RateLimiter, name: str, request: Request):
    if not limiter.allow((client_ip(request), name)):
        raise HTTPException(429, "Too many requests, slow down")
