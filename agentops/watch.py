from __future__ import annotations

import argparse

from agentops import config
from agentops.checkpoint import CheckpointStore
from agentops.db import connect
from agentops.handlers import make_handler
from agentops.trace import TraceStore
from agentops.watcher import Watcher


def main() -> None:
    p = argparse.ArgumentParser(description="Watch the stack and handle incidents")
    p.add_argument("--mode", choices=["agent", "baseline"], default="agent")
    args = p.parse_args()
    conn = connect(config.DB_PATH)
    trace, cps = TraceStore(conn), CheckpointStore(conn)
    llm = None
    if args.mode == "agent":
        from agentops.llm import OpenRouterClient
        llm = OpenRouterClient()
    handler = make_handler(args.mode, llm=llm, trace=trace, checkpoints=cps)
    print(f"watching stack ({args.mode} mode). Ctrl+C to stop.")
    Watcher(handler, trace, agent_name=args.mode).run_forever()


if __name__ == "__main__":
    main()
