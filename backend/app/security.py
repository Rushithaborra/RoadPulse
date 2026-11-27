"""Officer authentication (signed bearer tokens), device keys and a simple per-IP rate limiter."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import logging
import threading
import time

from fastapi import Header, HTTPException, Request

log = logging.getLogger("roadpulse.security")

SECRET = os.getenv("ROADPULSE_SECRET") or secrets.token_hex(32)  # set in .env so tokens survive restarts
TOKEN_TTL_S = 8 * 3600

# No built-in passwords. If OFFICER_USERS / DEVICE_KEYS are not configured, random ones are generated at
# start-up and printed once in the server log, so an unconfigured server is never open with a known password.
if not os.getenv("OFFICER_USERS"):
    os.environ["OFFICER_USERS"] = f"officer:{secrets.token_urlsafe(9)}"
    log.warning("OFFICER_USERS not set. Generated login for this run -> %s", os.environ["OFFICER_USERS"])
if not os.getenv("DEVICE_KEYS"):
    os.environ["DEVICE_KEYS"] = secrets.token_urlsafe(12)
    log.warning("DEVICE_KEYS not set. Generated device key for this run -> %s", os.environ["DEVICE_KEYS"])
for _var in ("OFFICER_USERS", "DEVICE_KEYS", "ROADPULSE_SECRET"):
    if "change-me" in os.getenv(_var, ""):
        log.warning("%s still contains a 'change-me' placeholder; set real values before sharing the server", _var)

# Trust X-Forwarded-For only behind a known reverse proxy; otherwise any client could fake its IP
# and dodge the rate limits.
TRUST_PROXY = os.getenv("TRUST_PROXY", "0") == "1"


# username -> (password, role). Set OFFICER_USERS="officer1:pass1,supervisor:pass2" in .env.
def _users() -> dict[str, tuple[str, str]]:
    raw = os.environ["OFFICER_USERS"]
    out = {}
    for pair in raw.split(","):
        if ":" in pair:
            u, p = pair.split(":", 1)
            out[u.strip()] = (p.strip(), "supervisor" if u.strip().startswith("supervisor") else "officer")
    return out


DEVICE_KEYS = {k.strip() for k in os.environ["DEVICE_KEYS"].split(",") if k.strip()}


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
    """Thread-safe token bucket per (client IP, bucket name). Idle buckets are pruned so memory stays bounded."""

    def __init__(self, rate_per_min: float, burst: int):
        self.rate = rate_per_min / 60.0
        self.burst = burst
        self.state: dict[tuple, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._last_prune = time.time()

    def allow(self, key: tuple) -> bool:
        t = time.time()
        with self._lock:
            tokens, last = self.state.get(key, (float(self.burst), t))
            tokens = min(self.burst, tokens + (t - last) * self.rate)
            ok = tokens >= 1
            self.state[key] = (tokens - 1 if ok else tokens, t)
            if t - self._last_prune > 300:  # drop buckets idle for 10 min
                self.state = {k: v for k, v in self.state.items() if t - v[1] < 600}
                self._last_prune = t
            return ok


REPORT_LIMIT = RateLimiter(rate_per_min=float(os.getenv("REPORT_RATE_PER_MIN", 6)), burst=5)
DEVICE_LIMIT = RateLimiter(rate_per_min=float(os.getenv("DEVICE_RATE_PER_MIN", 600)), burst=100)
SUBSCRIBE_LIMIT = RateLimiter(rate_per_min=float(os.getenv("SUBSCRIBE_RATE_PER_MIN", 10)), burst=5)
ROUTE_LIMIT = RateLimiter(rate_per_min=float(os.getenv("ROUTE_RATE_PER_MIN", 120)), burst=30)


def client_ip(request: Request) -> str:
    if TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[-1].strip()  # the address our own proxy saw, not a client-supplied one
    return request.client.host if request.client else "unknown"


def limit(limiter: RateLimiter, name: str, request: Request):
    if not limiter.allow((client_ip(request), name)):
        raise HTTPException(429, "Too many requests, slow down")


# ---------------- one-time stream tickets ----------------
# EventSource cannot send an Authorization header, so the officer stream used to carry the 8-hour bearer token in the
# URL (where proxies, browser history and logs can keep it). Instead the browser exchanges its token for a ticket
# that is random, valid for 60 s and usable once; the long-lived token never appears in a URL.
TICKET_TTL_S = 60
_tickets: dict[str, tuple[dict, float]] = {}
_tickets_lock = threading.Lock()


def issue_stream_ticket(claims: dict) -> dict:
    t = time.time()
    ticket = secrets.token_urlsafe(24)
    with _tickets_lock:
        for k in [k for k, (_, exp) in _tickets.items() if exp < t]:  # drop expired tickets
            del _tickets[k]
        _tickets[ticket] = ({"sub": claims["sub"], "role": claims["role"]}, t + TICKET_TTL_S)
    return {"ticket": ticket, "expires_in": TICKET_TTL_S}


def consume_stream_ticket(ticket: str) -> dict | None:
    """Return the officer claims and invalidate the ticket; None if unknown, used or expired."""
    with _tickets_lock:
        item = _tickets.pop(ticket, None)
    if not item:
        return None
    claims, exp = item
    return claims if exp >= time.time() else None
