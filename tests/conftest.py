import os
import sys

# configure before the app is imported (importing the app opens no database; the connection is made in its lifespan)
os.environ.setdefault("CLEAR_WINDOW_S", "0")
os.environ.setdefault("OFFICER_USERS", "officer:test-pass")
os.environ.setdefault("DEVICE_KEYS", "test-key")
os.environ.setdefault("POLICE_USERS", "unit12:police-pass,unit7:police-pass-2")
os.environ.setdefault("REQUIRE_FIELD_CONFIRMATION", "0")  # tests/test_field.py turns the two-step validation on
os.environ.setdefault("REPORT_RATE_PER_MIN", "600")
os.environ.setdefault("ROADPULSE_SECRET", "test-secret")
os.environ.setdefault("GRAPHML_PATH", "/nonexistent.graphml")  # always use the synthetic grid in tests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # every test gets its own database file; pytest removes tmp_path later, after the app has closed the connection
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    from fastapi.testclient import TestClient
    from app.main import S, app
    from app.store import store
    S.saved = {}
    store.reset()
    from app import security
    for lim in (security.REPORT_LIMIT, security.DEVICE_LIMIT, security.ROUTE_LIMIT, security.SUBSCRIBE_LIMIT):
        lim.state.clear()  # buckets are per IP and global: a previous test must not rate-limit this one
    with TestClient(app) as c:  # lifespan opens the database on enter and closes it on exit
        yield c
    store.reset()


@pytest.fixture()
def officer_headers(client):
    t = client.post("/api/auth/login", json={"username": "officer", "password": "test-pass"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


DEV = {"X-Device-Key": "test-key"}
