from __future__ import annotations

import json
import time

from agentops.trace import scrub


class CheckpointStore:
    def __init__(self, conn):
        self.conn = conn

    def save(self, run_id, step, messages, state):
        messages_json = scrub(json.dumps(messages, default=str))
        state_json = scrub(json.dumps(state))
        self.conn.execute(
            "INSERT OR REPLACE INTO checkpoints(run_id, step, messages, state, ts) VALUES(?,?,?,?,?)",
            (run_id, step, messages_json, state_json, time.time()),
        )
        self.conn.commit()

    def latest(self, run_id):
        row = self.conn.execute(
            "SELECT * FROM checkpoints WHERE run_id=? ORDER BY step DESC LIMIT 1", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "step": row["step"],
            "messages": json.loads(row["messages"]),
            "state": json.loads(row["state"]),
        }
