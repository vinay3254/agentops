from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "agentops"
APP_SERVICES = ("gateway", "api", "worker")
SERVICES = ("gateway", "api", "worker", "redis")
PORTS = {"gateway": 8000, "api": 8001, "worker": 8002}
HOST = "127.0.0.1"

COMPOSE_FILE = ROOT / "docker-compose.yml"
ENV_FILE = ROOT / ".agentops.env"
DB_PATH = Path(os.getenv("AGENTOPS_DB", ROOT / "agentops.db"))

LATENCY_THRESHOLD_S = 1.0
ORDER_DONE_TIMEOUT_S = 5.0

MAX_STEPS = 15
MAX_FAILED_CALLS = 3
RUN_TIMEOUT_S = 120
TOKEN_BUDGET = 60000
TOOL_RESULT_MAX_CHARS = 3000

POLL_INTERVAL_S = 2.0
POLLS_TO_OPEN = 2
POLLS_TO_CLOSE = 2


def container_name(service: str) -> str:
    return f"{PROJECT}-{service}"


def service_url(service: str) -> str:
    return f"http://{HOST}:{PORTS[service]}"
