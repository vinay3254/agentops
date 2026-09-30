from __future__ import annotations

import sqlite3

from agentops import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT, incident_id TEXT, step INTEGER, type TEXT,
  payload TEXT, tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0, ts REAL
);
CREATE TABLE IF NOT EXISTS checkpoints(
  run_id TEXT, step INTEGER, messages TEXT, state TEXT, ts REAL,
  PRIMARY KEY(run_id, step)
);
CREATE TABLE IF NOT EXISTS incidents(
  id TEXT PRIMARY KEY, opened REAL, closed REAL, symptom TEXT, agent TEXT,
  fault TEXT, status TEXT, stop_reason TEXT, steps INTEGER, tokens INTEGER, cost REAL
);
"""


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or config.DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
