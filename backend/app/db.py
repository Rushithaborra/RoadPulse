"""SQLite persistence. The only module that contains SQL. Write-through: in-memory state is the working set,
every change is saved here, and open incidents are reloaded on restart."""
from __future__ import annotations

import json
import os
import sqlite3
import threading

from .models import Incident

DB_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "data", "roadpulse.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, status TEXT NOT NULL, severity TEXT NOT NULL,
  confidence REAL NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, updated REAL NOT NULL, doc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_incidents_status ON incidents(status);
CREATE TABLE IF NOT EXISTS status_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL, status TEXT NOT NULL,
  at REAL NOT NULL, reason TEXT, actor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sensor_readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sensor_id TEXT NOT NULL, depth_cm REAL NOT NULL, at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
  reporter_id TEXT NOT NULL, note TEXT, at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  target TEXT, detail TEXT
);
"""


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = os.path.abspath(path)
        if self.path != ":memory:" and not path == ":memory:":
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path if path == ":memory:" else self.path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL; avoids an fsync per write
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _exec(self, sql: str, args: tuple = ()):
        with self._lock:
            self.conn.execute(sql, args)
            self.conn.commit()

    def save_incident(self, inc: Incident):
        self._exec(
            "INSERT INTO incidents(id,type,status,severity,confidence,lat,lon,updated,doc) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET type=excluded.type,status=excluded.status,severity=excluded.severity,"
            "confidence=excluded.confidence,lat=excluded.lat,lon=excluded.lon,updated=excluded.updated,doc=excluded.doc",
            (inc.id, inc.type, inc.status, inc.severity, inc.confidence, inc.lat, inc.lon, inc.updated,
             inc.model_dump_json(exclude={"signals"}) if len(inc.signals) > 200 else inc.model_dump_json()),
        )

    def load_open_incidents(self) -> list[Incident]:
        with self._lock:
            rows = self.conn.execute("SELECT doc FROM incidents WHERE status != 'Cleared'").fetchall()
        out = []
        for (doc,) in rows:
            try:
                out.append(Incident.model_validate_json(doc))
            except Exception:
                continue
        return out

    def status_change(self, incident_id: str, status: str, at: float, reason: str | None, actor: str):
        self._exec("INSERT INTO status_changes(incident_id,status,at,reason,actor) VALUES(?,?,?,?,?)",
                   (incident_id, status, at, reason, actor))

    def sensor_reading(self, sensor_id: str, depth_cm: float, at: float):
        self._exec("INSERT INTO sensor_readings(sensor_id,depth_cm,at) VALUES(?,?,?)", (sensor_id, depth_cm, at))

    def report(self, type_: str, lat: float, lon: float, reporter_id: str, note: str | None, at: float):
        self._exec("INSERT INTO reports(type,lat,lon,reporter_id,note,at) VALUES(?,?,?,?,?,?)",
                   (type_, lat, lon, reporter_id, note, at))

    def audit(self, at: float, actor: str, action: str, target: str | None = None, detail: dict | None = None):
        self._exec("INSERT INTO audit_log(at,actor,action,target,detail) VALUES(?,?,?,?,?)",
                   (at, actor, action, target, json.dumps(detail) if detail else None))

    def audit_entries(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT at,actor,action,target,detail FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"at": r[0], "actor": r[1], "action": r[2], "target": r[3], "detail": r[4]} for r in rows]

    def count(self, table: str) -> int:
        assert table in {"incidents", "status_changes", "sensor_readings", "reports", "audit_log"}
        with self._lock:
            return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
