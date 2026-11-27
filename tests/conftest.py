import os
import sys
import tempfile

# configure before the app is imported
os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("CLEAR_WINDOW_S", "0")
os.environ.setdefault("OFFICER_USERS", "officer:test-pass")
os.environ.setdefault("DEVICE_KEYS", "test-key")
os.environ.setdefault("REPORT_RATE_PER_MIN", "600")
os.environ.setdefault("ROADPULSE_SECRET", "test-secret")
os.environ.setdefault("GRAPHML_PATH", "/nonexistent.graphml")  # always use the synthetic grid in tests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest  # noqa: E402


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import S, app
    from app.store import store
    for suffix in ("", "-wal", "-shm"):  # fresh database per test
        p = os.environ["DATABASE_PATH"] + suffix
        if os.path.exists(p):
            os.remove(p)
    S.saved = {}
    store.reset()
    with TestClient(app) as c:
        yield c
    store.reset()


@pytest.fixture()
def officer_headers(client):
    t = client.post("/api/auth/login", json={"username": "officer", "password": "test-pass"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


DEV = {"X-Device-Key": "test-key"}
