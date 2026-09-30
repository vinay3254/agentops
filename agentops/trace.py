from __future__ import annotations

import json
import os
import time


def scrub(text: str | None) -> str | None:
    """Scrub OPENROUTER_API_KEY from text, handling JSON-escaped forms.

    Returns None if text is None.
    If OPENROUTER_API_KEY is at least 8 chars, replaces (in order to avoid invalid escapes):
    1. json.dumps(key, ensure_ascii=False)[1:-1] (JSON-escaped form with ensure_ascii=False)
    2. json.dumps(key)[1:-1] (JSON-escaped form)
    3. The raw key (last, to avoid creating invalid escape sequences)
    """
    if text is None:
        return None

    key = os.getenv("OPENROUTER_API_KEY", "")
    if len(key) < 8:
        return text

    # Replace JSON-escaped forms FIRST to avoid creating invalid escape sequences
    # json.dumps adds quotes around the string and escapes special characters

    # First: Replace with ensure_ascii=False form
    json_escaped_no_ascii = json.dumps(key, ensure_ascii=False)[1:-1]
    if json_escaped_no_ascii:
        text = text.replace(json_escaped_no_ascii, "***")

    # Second: Replace standard json.dumps form
    json_escaped = json.dumps(key)[1:-1]
    if json_escaped and json_escaped != json_escaped_no_ascii:
        text = text.replace(json_escaped, "***")

    # Last: Replace raw key
    text = text.replace(key, "***")

    return text


class TraceStore:
    def __init__(self, conn):
        self.conn = conn

    def event(self, run_id, type, payload, step=0, incident_id=None, tokens=0, cost=0.0):
        body = scrub(json.dumps(payload, default=str))
        self.conn.execute(
            "INSERT INTO events(run_id, incident_id, step, type, payload, tokens, cost, ts)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (run_id, incident_id, step, type, body, tokens, cost, time.time()),
        )
        self.conn.commit()

    def events(self, run_id=None, incident_id=None) -> list[dict]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if run_id is not None:
            sql += " AND run_id=?"
            args.append(run_id)
        if incident_id is not None:
            sql += " AND incident_id=?"
            args.append(incident_id)
        rows = self.conn.execute(sql + " ORDER BY id", args).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    def open_incident(self, incident_id, symptom, agent, fault=None):
        self.conn.execute(
            "INSERT INTO incidents(id, opened, symptom, agent, fault, status) VALUES(?,?,?,?,?,'open')",
            (incident_id, time.time(), scrub(symptom), agent, fault),
        )
        self.conn.commit()

    def close_incident(self, incident_id, status, stop_reason, steps, tokens, cost):
        self.conn.execute(
            "UPDATE incidents SET closed=?, status=?, stop_reason=?, steps=?, tokens=?, cost=? WHERE id=?",
            (time.time(), status, stop_reason, steps, tokens, cost, incident_id),
        )
        self.conn.commit()

    def incident(self, incident_id):
        row = self.conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return dict(row) if row else None

    def incidents(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM incidents ORDER BY opened DESC").fetchall()
        return [dict(r) for r in rows]
