"""Compare candidate OpenRouter models on two faults before choosing AGENTOPS_MODEL."""
import os
import sys

from agentops import config
from agentops.checkpoint import CheckpointStore
from agentops.db import connect
from agentops.handlers import make_handler
from agentops.llm import OpenRouterClient
from agentops.trace import TraceStore
from agentops.trial import run_trial

DEFAULT = "openai/gpt-4o-mini,google/gemini-2.5-flash,qwen/qwen-2.5-72b-instruct,meta-llama/llama-3.3-70b-instruct"


def main() -> None:
    candidates = [m.strip() for m in os.getenv("AGENTOPS_CANDIDATES", DEFAULT).split(",") if m.strip()]
    conn = connect(config.ROOT / "pick_model.db")
    trace, cps = TraceStore(conn), CheckpointStore(conn)
    print(f"{'model':45} {'fault':12} {'ok':5} {'steps':6} {'mttr':7} {'cost':8}")
    for model in candidates:
        try:
            llm = OpenRouterClient(model=model)
        except Exception as e:
            print(f"{model:45} skipped: {e}")
            continue
        for fault in ("crash", "bad_config"):
            try:
                handler = make_handler("agent", llm=llm, trace=trace, checkpoints=cps)
                r = run_trial(fault, "agent", 1, handler, trace)
                print(f"{model:45} {fault:12} {str(r.success):5} {r.steps:<6} {r.mttr_s:<7.1f} {r.cost:<8.4f}")
            except Exception as e:
                print(f"{model:45} {fault:12} error: {type(e).__name__}: {e}")
    print("\nSet the winner: echo 'AGENTOPS_MODEL=<model>' >> .env")


if __name__ == "__main__":
    sys.exit(main())
