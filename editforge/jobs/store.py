"""A small database of jobs that survives restarts (SQLite, guarded by a lock)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.cache import home_dir

FINAL_STATES = ("done", "failed", "cancelled")
COLUMNS = ("id", "created", "updated", "status", "progress", "phase", "message", "eta", "script", "input_path", "output_path",
           "options", "result", "error", "error_fix", "error_details", "plan")


class JobStore:
    """Stores every job. All access goes through one lock, so the web page and the worker can share it."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else home_dir() / "jobs.sqlite3"
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, created REAL, updated REAL, status TEXT, progress REAL, phase TEXT, message TEXT,
                    eta REAL, script TEXT, input_path TEXT, output_path TEXT, options TEXT, result TEXT, error TEXT,
                    error_fix TEXT, error_details TEXT, plan TEXT)""")
                conn.commit()
            finally:
                conn.close()

    def create(self, script: str, input_path: str, output_path: str, options: Dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("INSERT INTO jobs (id, created, updated, status, progress, phase, message, script, input_path, output_path, options)"
                             " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (job_id, now, now, "queued", 0.0, "queued", "Waiting to start", script, input_path, output_path,
                              json.dumps(options)))
                conn.commit()
            finally:
                conn.close()
        return job_id

    def update(self, job_id: str, **fields: Any) -> None:
        bad = set(fields) - set(COLUMNS)
        if bad:
            raise ValueError(f"unknown job fields {bad}")
        for k in ("options", "result", "plan"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k])
        fields["updated"] = time.time()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for k in ("options", "result", "plan"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        return d

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            finally:
                conn.close()
        return self._row(row) if row else None

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
            finally:
                conn.close()
        out = []
        for r in rows:
            d = self._row(r)
            d.pop("script", None)
            plan = d.pop("plan", None)          # large; only its warnings are useful in a list
            d["warnings"] = list(plan.get("warnings") or [])[:6] if isinstance(plan, dict) else []
            out.append(d)
        return out

    def mark_interrupted(self) -> int:
        """Jobs that were running when the program stopped can be resumed; mark them so."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("UPDATE jobs SET status='interrupted', message='The program stopped while this job was running. Press Resume.', "
                                   "updated=? WHERE status IN ('running','queued')", (time.time(),))
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def delete(self, job_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
                conn.commit()
            finally:
                conn.close()
