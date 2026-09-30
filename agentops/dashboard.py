from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from agentops import chaos, config
from agentops.db import connect
from agentops.eval_run import summarize
from agentops.timeline import format_event
from agentops.trace import TraceStore

OFFLINE = os.getenv("AGENTOPS_DASHBOARD_OFFLINE") == "1"
ICONS = {"thought": "💭", "action": "🔧", "result": "📄", "denied": "⛔", "verify": "✅", "end": "🏁", "error": "❗"}

st.set_page_config(page_title="AgentOps", layout="wide")
st.title("AgentOps")
st.caption("Autonomous incident response on a chaos-injected Docker stack. "
           "Run `python -m agentops.watch` in another terminal so injected faults get handled.")

trace = TraceStore(connect(config.DB_PATH))
live_tab, traces_tab, eval_tab = st.tabs(["Live", "Traces", "Eval"])
auto = False

with live_tab:
    if OFFLINE:
        st.info("Offline mode: live stack checks are disabled.")
    else:
        from agentops.executor import Executor
        from agentops.verifier import verify

        try:
            status = Executor().status()
            cols = st.columns(len(status))
            for col, (svc, info) in zip(cols, status.items()):
                col.metric(svc, info["state"])
        except Exception as e:
            st.error(f"Docker unavailable: {e}")
        result = verify()
        if result.healthy:
            st.success("Stack healthy")
        else:
            st.error(f"Stack unhealthy: {result.reason}")
        c1, c2, c3 = st.columns([2, 1, 1])
        fault = c1.selectbox("Fault", chaos.FAULTS)
        if c2.button("Inject fault"):
            chaos.inject(fault)
            st.toast(f"Injected {fault}")
        if c3.button("Reset stack"):
            chaos.reset()
            st.toast("Stack reset")
        auto = st.toggle("Auto-refresh every 3 s")

with traces_tab:
    incidents = trace.incidents()
    if not incidents:
        st.info("No incidents recorded yet.")
    else:
        table = pd.DataFrame(incidents)[["id", "fault", "agent", "status", "stop_reason", "steps", "tokens", "cost"]]
        st.dataframe(table, width="stretch", hide_index=True)
        chosen = st.selectbox("Incident", [i["id"] for i in incidents])
        for ev in trace.events(incident_id=chosen):
            item = format_event(ev)
            if item is None:
                continue
            with st.expander(f"{ICONS[item['kind']]} {item['label']}", expanded=item["kind"] in ("end", "denied")):
                st.code(item["body"] or "(empty)", language="text")

with eval_tab:
    out = Path(config.ROOT) / "eval"
    results = out / "results.csv"
    if not results.exists():
        st.info("No eval results yet. Run `python -m agentops.eval_run`.")
    else:
        st.dataframe(summarize(pd.read_csv(results)), width="stretch", hide_index=True)
        for name in ("success_rate.png", "mttr.png"):
            if (out / name).exists():
                st.image(str(out / name))

if auto:
    time.sleep(3)
    st.rerun()
