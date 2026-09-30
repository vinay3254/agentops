from __future__ import annotations

import json


def format_event(ev: dict) -> dict | None:
    t, p, step = ev["type"], ev["payload"], ev.get("step", 0)
    if t == "llm_call":
        text = (p.get("text") or "").strip()
        if not text:
            return None
        return {"kind": "thought", "label": f"step {step}: reasoning", "body": text}
    if t == "tool_call":
        args = p.get("args")
        body = args if isinstance(args, str) else json.dumps(args)
        perm = f" [{p['permission']}]" if p.get("permission") else ""
        return {"kind": "action", "label": f"step {step}: {p['name']}{perm}", "body": body}
    if t == "tool_result":
        status = "ok" if p.get("ok") else "failed"
        return {"kind": "result", "label": f"step {step}: {p['name']} {status}", "body": p.get("result", "")}
    if t == "policy_denied":
        return {"kind": "denied", "label": f"step {step}: policy denied {p['name']}", "body": p.get("reason", "")}
    if t == "verify":
        label = "verifier: healthy" if p.get("healthy") else "verifier: unhealthy"
        return {"kind": "verify", "label": f"step {step}: {label}", "body": p.get("reason", "")}
    if t == "run_end":
        return {"kind": "end", "label": f"run ended: {p['stop_reason']} (success={p['success']})",
                "body": p.get("summary", "")}
    if t == "llm_error":
        return {"kind": "error", "label": f"step {step}: LLM error", "body": p.get("error", "")}
    return None
