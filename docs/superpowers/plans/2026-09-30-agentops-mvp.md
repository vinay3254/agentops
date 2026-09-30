# AgentOps MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an agent runtime plus an autonomous Incident Responder that repairs 5 chaos-injected fault types in a Docker Compose stack, compared against a rule-based baseline, with a trace dashboard and an eval report.

**Architecture:** A Python package `agentops` holds the runtime (LLM client, tool registry, policy-enforced Docker executor, agent loop, checkpoint/resume, SQLite trace store, incident watcher). A separate `target/` tree holds the Dockerized microservices under attack. A non-LLM verifier decides whether the stack is healthy. Everything Docker-related goes through one executor; the agent never gets a host shell.

**Tech Stack:** Python 3.11+ (local venv), FastAPI + uvicorn + redis-py (target services, Python 3.12 images), Docker SDK for Python, `docker compose` CLI, OpenAI-compatible client pointed at OpenRouter, SQLite, Streamlit, pandas, matplotlib, pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-agentops-design.md`

**Scope of this plan:** MVP only. Stretch items (Dev Agent, Reviewer, learning layer, parallel incidents) get a separate plan after the MVP is green.

## Global Constraints

- Deadline: Sunday 2026-10-04. MVP must be complete by Saturday 2026-10-03. Cut stretch items, never MVP items.
- Compose project name and container prefix: `agentops`. Container names: `agentops-gateway`, `agentops-api`, `agentops-worker`, `agentops-redis`.
- Ports (published on 127.0.0.1): gateway 8000, api 8001, worker 8002. Redis is not published.
- Every app service returns `200 {"status":"ok"}` on `/health` when healthy and non-200 otherwise.
- Fault types (exactly five): `crash`, `bad_config`, `dependency_down`, `disk_full`, `cpu_hog`.
- Agent limits: max 15 steps, max 3 consecutive failed tool calls, max token budget per run 60000, wall-clock timeout 120 s.
- Watcher: polls every 2 s; opens an incident after 2 consecutive failed polls; closes only after 2 consecutive healthy polls; one active incident at a time.
- Verifier is pure Python, no LLM. The agent cannot declare its own success: `finish` counts only if the verifier reports healthy.
- `run_diagnostic` allowlist: `ps`, `top -bn1`, `df -h`, `ls`, `cat` (paths under `/app`, `/data`, `/etc`), `env` (secret-like values redacted), `tail`. No shell metacharacters. Arguments pass as an argv list, never through a shell.
- `cleanup_files` path must be under `/data/`. `kill_process` must never touch PID 1. `set_env` only accepts allowlisted (service, key) pairs.
- LLM: OpenRouter via env `OPENROUTER_API_KEY` and `AGENTOPS_MODEL`. The API key must never appear in traces or logs.
- Tool results are truncated to 3000 characters before returning to the LLM.
- Eval: 5 fault types x 3 trials x 2 modes (baseline, agent). Outputs: `eval/results.csv`, `eval/summary.md`, plots in `eval/`.
- Docker-dependent tests carry `@pytest.mark.docker` and run only with `pytest -m docker`. Plain `pytest` runs unit tests only.
- Commit style: `git commit -m "<type>: <subject>" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"`.
- All work happens in `/home/vinay/agentops` (its own git repo, branch `main`).

## Review Focus

Inputs and conditions the spec implies but no happy-path test covers, most likely first. Each has a pinning test in the task named in brackets.

1. **Agent calls `finish` while the stack is still unhealthy.** Must not count as resolved; the loop continues and the agent is told why. [Task 10]
2. **Shell injection and path traversal.** `run_diagnostic("ps; rm -rf /")`, `cat /data/../etc/shadow`, `cleanup_files("/data/../etc/passwd")`, `set_env` with `;` in value, `kill_process(1)` must all be denied with a clear reason. [Tasks 4 and 5]
3. **Malformed or disallowed tool calls from the LLM.** Invalid JSON arguments, unknown tool, tool outside the agent's allowlist, missing or extra arguments: each becomes a failed call, never a crash; 3 in a row stops the run. [Tasks 8 and 10]
4. **Huge tool output.** A 200-line log or large file must be truncated so the agent context does not blow up. [Task 8]
5. **Secrets in traces.** The OpenRouter key appearing in LLM text or a tool result must be scrubbed before it reaches SQLite; `env` output must redact secret-like names. [Tasks 4 and 7]

Also pinned: dashboard renders with an empty database and no eval files [Task 15]; one failed trial (for example reset failure) is recorded and the eval continues [Task 14].

---

## File Structure

```
agentops/
  pyproject.toml
  .gitignore
  .env.example
  docker-compose.yml
  README.md
  target/
    requirements.txt
    common.py                 # /data quota helpers shared by api and worker
    gateway/{app.py,Dockerfile}
    api/{app.py,spin.py,Dockerfile}
    worker/{app.py,Dockerfile}
  agentops/
    __init__.py
    config.py                 # constants, service names, limits
    compose.py                # docker compose CLI wrapper, env-file helpers
    verifier.py               # health + synthetic transaction check
    policy.py                 # allowlists, validation, redaction
    executor.py               # only code that touches Docker
    chaos.py                  # inject(), reset(), CLI
    db.py                     # SQLite schema and connect()
    trace.py                  # TraceStore: events and incidents
    checkpoint.py             # CheckpointStore
    tools.py                  # ToolRegistry, truncate()
    responder_tools.py        # build_registry(executor, verify_fn)
    llm.py                    # OpenRouter client, LLMResponse
    agent.py                  # Agent loop, RunResult
    baseline.py               # rule-based runbook
    responder.py              # system prompt, build_responder()
    handlers.py               # make_handler(mode, ...)
    watcher.py                # Watcher
    watch.py                  # CLI: python -m agentops.watch
    trial.py                  # run_trial()
    run_one.py                # CLI: one incident, resume demo
    eval_run.py               # eval harness, summary, plots
    timeline.py               # format_event() for dashboard
    dashboard.py              # Streamlit app
  scripts/pick_model.py
  tests/
    conftest.py fakes.py
    test_*.py
    integration/test_*.py
  eval/                       # results.csv, summary.md, *.png (generated)
  docs/screenshots/           # captured by hand
```

---

### Task 1: Scaffold and config

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `agentops/__init__.py`, `agentops/config.py`, `tests/__init__.py` (empty), `tests/integration/__init__.py` (empty). The `__init__.py` files let tests import `tests.fakes`.
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.ROOT, PROJECT, APP_SERVICES, SERVICES, PORTS, HOST, COMPOSE_FILE, ENV_FILE, DB_PATH, LATENCY_THRESHOLD_S, ORDER_DONE_TIMEOUT_S, MAX_STEPS, MAX_FAILED_CALLS, RUN_TIMEOUT_S, TOKEN_BUDGET, TOOL_RESULT_MAX_CHARS, POLL_INTERVAL_S, POLLS_TO_OPEN, POLLS_TO_CLOSE`, `config.container_name(service) -> str`, `config.service_url(service) -> str`. Every later task imports from here and reads constants as `config.NAME` (attribute access, so tests can monkeypatch).

- [ ] **Step 1: Create project files and venv**

`pyproject.toml`:

```toml
[project]
name = "agentops"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "docker>=7.0",
  "httpx>=0.27",
  "openai>=1.40",
  "streamlit>=1.38",
  "pandas>=2.2",
  "matplotlib>=3.9",
  "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.setuptools.packages.find]
include = ["agentops*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["docker: needs the Docker Compose stack running"]
addopts = "-m 'not docker'"
```

`.gitignore`:

```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
.env
.agentops.env
*.db
```

`.env.example`:

```
OPENROUTER_API_KEY=
AGENTOPS_MODEL=
```

`agentops/__init__.py`: empty file.

Run:

```bash
cd /home/vinay/agentops
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

Expected: install succeeds. If a wheel fails to build on Python 3.14 (pandas, matplotlib, or streamlit dependency), recreate the venv with Python 3.12: `rm -rf .venv && uv venv --python 3.12 .venv && . .venv/bin/activate && uv pip install -e '.[dev]'`.

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`:

```python
from agentops import config


def test_container_name_and_url():
    assert config.container_name("api") == "agentops-api"
    assert config.service_url("gateway") == "http://127.0.0.1:8000"


def test_services_cover_app_services():
    assert set(config.APP_SERVICES) < set(config.SERVICES)
    assert set(config.PORTS) == set(config.APP_SERVICES)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'config'`

- [ ] **Step 4: Write minimal implementation**

`agentops/config.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: scaffold project and config" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Target stack and compose helper

**Files:**
- Create: `docker-compose.yml`, `target/requirements.txt`, `target/common.py`, `target/gateway/app.py`, `target/gateway/Dockerfile`, `target/api/app.py`, `target/api/spin.py`, `target/api/Dockerfile`, `target/worker/app.py`, `target/worker/Dockerfile`, `agentops/compose.py`
- Test: `tests/conftest.py`, `tests/integration/test_stack.py`

**Interfaces:**
- Consumes: `config.COMPOSE_FILE, ENV_FILE, PROJECT, PORTS, APP_SERVICES, service_url`.
- Produces: `compose.ensure_env_file() -> None`, `compose.compose(*args, check=True, timeout=300) -> subprocess.CompletedProcess`, `compose.set_env_var(name: str, value: str) -> None` (writes `NAME=value` into `.agentops.env`, replacing an existing line), `compose.clear_env() -> None`. pytest fixture `stack` (session scope; builds and starts the stack, waits for three `/health` 200s). Compose variables read from `.agentops.env`: `API_REDIS_URL`, `API_DEBUG_SPIN`, `WORKER_REDIS_URL`, `GATEWAY_API_URL`. Target HTTP contract: gateway `POST /orders`, `GET /orders/{id}`; api same plus `GET /metrics -> {"requests","p95_ms","last_cpu_probe_ms"}`; order status goes `pending` then `done` (worker).

- [ ] **Step 1: Write the failing test**

`tests/conftest.py`:

```python
import time

import httpx
import pytest

from agentops import config
from agentops.compose import compose


def _wait_ports(timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    pending = set(config.APP_SERVICES)
    while pending and time.monotonic() < deadline:
        for svc in list(pending):
            try:
                if httpx.get(f"{config.service_url(svc)}/health", timeout=2).status_code == 200:
                    pending.discard(svc)
            except httpx.HTTPError:
                pass
        time.sleep(1)
    if pending:
        raise RuntimeError(f"services not healthy: {sorted(pending)}")


@pytest.fixture(scope="session")
def stack():
    compose("up", "-d", "--build")
    _wait_ports()
    yield
```

`tests/integration/test_stack.py`:

```python
import time

import httpx
import pytest

from agentops import config

pytestmark = pytest.mark.docker


def test_health_endpoints(stack):
    for svc in config.APP_SERVICES:
        r = httpx.get(f"{config.service_url(svc)}/health", timeout=3)
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_order_flows_through_worker(stack):
    gw = config.service_url("gateway")
    r = httpx.post(f"{gw}/orders", json={"item": "book", "qty": 2}, timeout=5)
    assert r.status_code == 200
    oid = r.json()["id"]
    deadline = time.monotonic() + 10
    data = {}
    while time.monotonic() < deadline:
        data = httpx.get(f"{gw}/orders/{oid}", timeout=3).json()
        if data.get("status") == "done":
            break
        time.sleep(0.3)
    assert data["status"] == "done"
    assert data["item"] == "book"


def test_api_metrics(stack):
    m = httpx.get(f"{config.service_url('api')}/metrics", timeout=3).json()
    assert {"requests", "p95_ms", "last_cpu_probe_ms"} <= set(m)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -m docker tests/integration/test_stack.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'agentops.compose'`

- [ ] **Step 3: Write the compose helper**

`agentops/compose.py`:

```python
from __future__ import annotations

import subprocess

from agentops import config


def ensure_env_file() -> None:
    config.ENV_FILE.touch(exist_ok=True)


def compose(*args: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run `docker compose` for the agentops project. argv list, never a shell."""
    ensure_env_file()
    cmd = [
        "docker", "compose",
        "-p", config.PROJECT,
        "-f", str(config.COMPOSE_FILE),
        "--env-file", str(config.ENV_FILE),
        *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)


def set_env_var(name: str, value: str) -> None:
    ensure_env_file()
    lines = [
        line for line in config.ENV_FILE.read_text().splitlines()
        if not line.startswith(f"{name}=")
    ]
    lines.append(f"{name}={value}")
    config.ENV_FILE.write_text("\n".join(lines) + "\n")


def clear_env() -> None:
    config.ENV_FILE.write_text("")
```

- [ ] **Step 4: Write the target stack**

`target/requirements.txt`:

```
fastapi==0.115.*
uvicorn==0.32.*
redis==5.*
httpx==0.27.*
```

`target/common.py`:

```python
import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
QUOTA = int(os.getenv("DATA_QUOTA_BYTES", "8000000"))


def usage_bytes() -> int:
    if not DATA_DIR.exists():
        return 0
    return sum(p.stat().st_size for p in DATA_DIR.rglob("*") if p.is_file())


def check_data() -> None:
    used = usage_bytes()
    if used >= QUOTA:
        raise OSError(f"data quota exceeded: {used} of {QUOTA} bytes used in {DATA_DIR}")


def append_line(name: str, line: str) -> None:
    check_data()
    with open(DATA_DIR / name, "a") as f:
        f.write(line + "\n")
```

`target/api/spin.py`:

```python
import os
import sys
import time

workers = int(sys.argv[1])
for _ in range(workers):
    if os.fork() == 0:
        while True:
            pass
while True:
    time.sleep(60)
```

`target/api/app.py`:

```python
import logging
import os
import subprocess
import sys
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import common

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s api: %(message)s")
log = logging.getLogger("api")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
r = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=1, decode_responses=True)

latencies: deque = deque(maxlen=100)
request_count = 0
last_probe_ms = 0.0


@asynccontextmanager
async def lifespan(app):
    spin = int(os.getenv("DEBUG_SPIN", "0"))
    if spin > 0:
        log.warning("DEBUG_SPIN=%s set: starting spin workers", spin)
        subprocess.Popen([sys.executable, "/app/api/spin.py", str(spin)])
    yield


app = FastAPI(lifespan=lifespan)


def cpu_probe_ms() -> float:
    """Burn 20 ms of CPU time and report how long that took on the wall clock."""
    wall = time.perf_counter()
    cpu = time.thread_time()
    while time.thread_time() - cpu < 0.02:
        pass
    return (time.perf_counter() - wall) * 1000


@app.get("/health")
def health():
    global last_probe_ms
    try:
        r.ping()
    except Exception as e:
        log.error("redis unreachable at %s: %s", REDIS_URL, e)
        return JSONResponse({"status": "fail", "reason": f"redis: {e}"}, status_code=503)
    try:
        common.check_data()
    except OSError as e:
        log.error("%s", e)
        return JSONResponse({"status": "fail", "reason": str(e)}, status_code=503)
    ms = cpu_probe_ms()
    last_probe_ms = ms
    if ms > 150:
        log.error("degraded: cpu probe took %.0f ms (expected about 25 ms)", ms)
        return JSONResponse({"status": "fail", "reason": f"degraded: cpu probe {ms:.0f} ms"}, status_code=503)
    return {"status": "ok"}


class OrderIn(BaseModel):
    item: str
    qty: int = 1


@app.post("/orders")
def create_order(o: OrderIn):
    global request_count
    t = time.perf_counter()
    oid = uuid.uuid4().hex[:12]
    try:
        common.append_line("audit.log", f"{time.time()} create {oid} {o.item} {o.qty}")
        r.hset(f"order:{oid}", mapping={"item": o.item, "qty": o.qty, "status": "pending"})
        r.rpush("queue", oid)
    except OSError as e:
        log.error("audit write failed: %s", e)
        raise HTTPException(507, str(e))
    except redis.RedisError as e:
        log.error("redis error: %s", e)
        raise HTTPException(503, f"redis: {e}")
    finally:
        request_count += 1
        latencies.append((time.perf_counter() - t) * 1000)
    return {"id": oid}


@app.get("/orders/{oid}")
def get_order(oid: str):
    try:
        data = r.hgetall(f"order:{oid}")
    except redis.RedisError as e:
        log.error("redis error: %s", e)
        raise HTTPException(503, f"redis: {e}")
    if not data:
        raise HTTPException(404, "order not found")
    return data


@app.get("/metrics")
def metrics():
    ordered = sorted(latencies)
    p95 = ordered[int(len(ordered) * 0.95) - 1] if ordered else 0.0
    return {"requests": request_count, "p95_ms": round(p95, 2), "last_cpu_probe_ms": round(last_probe_ms, 1)}
```

`target/gateway/app.py`:

```python
import os

import httpx
from fastapi import Body, FastAPI, HTTPException, Response

API_URL = os.getenv("API_URL", "http://api:8001")
app = FastAPI()
client = httpx.Client(base_url=API_URL, timeout=5.0)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/orders")
def create_order(body: dict = Body(...)):
    try:
        r = client.post("/orders", json=body)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"api unreachable: {type(e).__name__}")
    return Response(r.content, status_code=r.status_code, media_type="application/json")


@app.get("/orders/{oid}")
def get_order(oid: str):
    try:
        r = client.get(f"/orders/{oid}")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"api unreachable: {type(e).__name__}")
    return Response(r.content, status_code=r.status_code, media_type="application/json")
```

`target/worker/app.py`:

```python
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import common

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s worker: %(message)s")
log = logging.getLogger("worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
r = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=3, decode_responses=True)
heartbeat = time.time()


def requeue(oid):
    if oid:
        try:
            r.lpush("queue", oid)
        except redis.RedisError:
            pass


def consume():
    global heartbeat
    while True:
        heartbeat = time.time()
        oid = None
        try:
            item = r.blpop("queue", timeout=1)
            if not item:
                continue
            oid = item[1]
            common.append_line("processed.log", f"{time.time()} processed {oid}")
            r.hset(f"order:{oid}", "status", "done")
        except OSError as e:
            log.error("cannot write processed log: %s", e)
            requeue(oid)
            time.sleep(1)
        except redis.RedisError as e:
            log.error("redis error at %s: %s", REDIS_URL, e)
            time.sleep(1)


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=consume, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    try:
        r.ping()
    except Exception as e:
        return JSONResponse({"status": "fail", "reason": f"redis: {e}"}, status_code=503)
    try:
        common.check_data()
    except OSError as e:
        return JSONResponse({"status": "fail", "reason": str(e)}, status_code=503)
    if time.time() - heartbeat > 5:
        return JSONResponse({"status": "fail", "reason": "consumer stalled"}, status_code=503)
    return {"status": "ok"}
```

Dockerfiles. `target/api/Dockerfile` (gateway and worker are identical except the `COPY` directory and the `CMD` line shown below):

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common.py .
COPY api/ api/
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8001"]
```

- `target/gateway/Dockerfile`: replace `COPY api/ api/` with `COPY gateway/ gateway/` and `CMD` with `["uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]`.
- `target/worker/Dockerfile`: replace `COPY api/ api/` with `COPY worker/ worker/` and `CMD` with `["uvicorn", "worker.app:app", "--host", "0.0.0.0", "--port", "8002"]`.

`docker-compose.yml` (repo root):

```yaml
name: agentops

services:
  redis:
    image: redis:7-alpine
    container_name: agentops-redis

  api:
    build:
      context: ./target
      dockerfile: api/Dockerfile
    container_name: agentops-api
    environment:
      REDIS_URL: ${API_REDIS_URL:-redis://redis:6379/0}
      DEBUG_SPIN: ${API_DEBUG_SPIN:-0}
      DATA_QUOTA_BYTES: "8000000"
    volumes:
      - api_data:/data
    ports:
      - "127.0.0.1:8001:8001"
    cpus: 0.5
    depends_on:
      - redis

  worker:
    build:
      context: ./target
      dockerfile: worker/Dockerfile
    container_name: agentops-worker
    environment:
      REDIS_URL: ${WORKER_REDIS_URL:-redis://redis:6379/0}
      DATA_QUOTA_BYTES: "8000000"
    volumes:
      - worker_data:/data
    ports:
      - "127.0.0.1:8002:8002"
    depends_on:
      - redis

  gateway:
    build:
      context: ./target
      dockerfile: gateway/Dockerfile
    container_name: agentops-gateway
    environment:
      API_URL: ${GATEWAY_API_URL:-http://api:8001}
    ports:
      - "127.0.0.1:8000:8000"
    depends_on:
      - api

volumes:
  api_data: {}
  worker_data: {}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest -m docker tests/integration/test_stack.py -v`
Expected: 3 passed (first run builds images, allow up to 3 minutes).

If `test_health_endpoints` fails on `api`, run `docker logs agentops-api` and fix the traceback before continuing.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add dockerized target stack and compose helper" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Verifier

**Files:**
- Create: `agentops/verifier.py`
- Test: `tests/test_verifier.py`, `tests/integration/test_verifier_live.py`

**Interfaces:**
- Consumes: `config.APP_SERVICES, service_url, LATENCY_THRESHOLD_S, ORDER_DONE_TIMEOUT_S`.
- Produces: `VerifyResult(healthy: bool, reason: str = "", latency_s: float | None = None)` (frozen dataclass). `verify(client: httpx.Client | None = None) -> VerifyResult`. `wait_healthy(timeout: float = 60.0, interval: float = 1.0, verify_fn=None) -> VerifyResult` (returns the last result, healthy or not). Failure `reason` strings start with the failing service name for service-specific failures: `"api /health 503: ..."`, `"gateway unreachable: ConnectError"`; synthetic failures start with `"synthetic order ..."` or `"order not processed ..."`.

- [ ] **Step 1: Write the failing tests**

`tests/test_verifier.py`:

```python
import httpx

from agentops import config
from agentops.verifier import VerifyResult, verify, wait_healthy


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def healthy_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    if request.url.path == "/orders" and request.method == "POST":
        return httpx.Response(200, json={"id": "abc"})
    if request.url.path == "/orders/abc":
        return httpx.Response(200, json={"status": "done"})
    return httpx.Response(404)


def test_healthy():
    result = verify(make_client(healthy_handler))
    assert result.healthy
    assert result.latency_s is not None


def test_service_health_failure_names_service():
    def handler(request):
        if request.url.port == 8001 and request.url.path == "/health":
            return httpx.Response(503, json={"status": "fail", "reason": "redis: down"})
        return healthy_handler(request)

    result = verify(make_client(handler))
    assert not result.healthy
    assert result.reason.startswith("api /health 503")


def test_unreachable_service():
    def handler(request):
        if request.url.port == 8000:
            raise httpx.ConnectError("refused")
        return healthy_handler(request)

    result = verify(make_client(handler))
    assert not result.healthy
    assert result.reason.startswith("gateway unreachable")


def test_slow_synthetic_order(monkeypatch):
    monkeypatch.setattr(config, "LATENCY_THRESHOLD_S", -1.0)
    result = verify(make_client(healthy_handler))
    assert not result.healthy
    assert "slow" in result.reason


def test_order_never_processed(monkeypatch):
    monkeypatch.setattr(config, "ORDER_DONE_TIMEOUT_S", 0.3)

    def handler(request):
        if request.url.path == "/orders/abc":
            return httpx.Response(200, json={"status": "pending"})
        return healthy_handler(request)

    result = verify(make_client(handler))
    assert not result.healthy
    assert result.reason.startswith("order not processed")


def test_wait_healthy_returns_when_healthy():
    seq = iter([VerifyResult(False, "x"), VerifyResult(False, "x"), VerifyResult(True)])
    result = wait_healthy(timeout=5, interval=0, verify_fn=lambda: next(seq))
    assert result.healthy


def test_wait_healthy_times_out():
    result = wait_healthy(timeout=0.3, interval=0.05, verify_fn=lambda: VerifyResult(False, "down"))
    assert not result.healthy
    assert result.reason == "down"
```

`tests/integration/test_verifier_live.py`:

```python
import pytest

from agentops.verifier import verify

pytestmark = pytest.mark.docker


def test_live_stack_is_healthy(stack):
    result = verify()
    assert result.healthy, result.reason
    assert result.latency_s < 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_verifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentops.verifier'`

- [ ] **Step 3: Write minimal implementation**

`agentops/verifier.py`:

```python
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from agentops import config


@dataclass(frozen=True)
class VerifyResult:
    healthy: bool
    reason: str = ""
    latency_s: float | None = None


def verify(client: httpx.Client | None = None) -> VerifyResult:
    own = client is None
    client = client or httpx.Client(timeout=3.0)
    try:
        return _verify(client)
    finally:
        if own:
            client.close()


def _verify(client: httpx.Client) -> VerifyResult:
    for svc in config.APP_SERVICES:
        try:
            resp = client.get(f"{config.service_url(svc)}/health")
        except httpx.HTTPError as e:
            return VerifyResult(False, f"{svc} unreachable: {type(e).__name__}")
        if resp.status_code != 200:
            return VerifyResult(False, f"{svc} /health {resp.status_code}: {resp.text[:200]}")

    gateway = config.service_url("gateway")
    start = time.perf_counter()
    try:
        resp = client.post(f"{gateway}/orders", json={"item": "probe", "qty": 1})
    except httpx.HTTPError as e:
        return VerifyResult(False, f"synthetic order failed: {type(e).__name__}")
    latency = time.perf_counter() - start
    if resp.status_code != 200:
        return VerifyResult(False, f"synthetic order failed: {resp.status_code} {resp.text[:200]}", latency)
    if latency > config.LATENCY_THRESHOLD_S:
        return VerifyResult(False, f"synthetic order slow: {latency:.2f}s", latency)

    oid = resp.json()["id"]
    deadline = time.monotonic() + config.ORDER_DONE_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            r2 = client.get(f"{gateway}/orders/{oid}")
            if r2.status_code == 200 and r2.json().get("status") == "done":
                return VerifyResult(True, "", latency)
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return VerifyResult(False, "order not processed by worker within timeout", latency)


def wait_healthy(timeout: float = 60.0, interval: float = 1.0, verify_fn=None) -> VerifyResult:
    verify_fn = verify_fn or verify
    deadline = time.monotonic() + timeout
    while True:
        result = verify_fn()
        if result.healthy or time.monotonic() >= deadline:
            return result
        time.sleep(interval)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_verifier.py -v` then `pytest -m docker tests/integration/test_verifier_live.py -v`
Expected: 7 passed, then 1 passed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add non-LLM health verifier" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Policy

**Files:**
- Create: `agentops/policy.py`
- Test: `tests/test_policy.py`

**Interfaces:**
- Consumes: `config.SERVICES`.
- Produces: `PolicyError(Exception)`. `check_service(name: str) -> str`. `parse_diagnostic(command: str) -> list[str]` (argv list). `check_data_path(path: str) -> str` (normalized, strictly under `/data/`). `check_env(service: str, key: str, value: str) -> str` (returns the compose variable name). `check_pid(pid: int | str) -> int`. `redact_env(text: str) -> str`. `ENV_ALLOWLIST: dict[tuple[str, str], str]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_policy.py`:

```python
import pytest

from agentops import policy
from agentops.policy import PolicyError


@pytest.mark.parametrize("cmd", [
    "ps aux", "ps -eo pid,ppid,%cpu,cmd", "top -bn1", "df -h", "df", "env",
    "ls -la /data", "ls /etc", "cat /app/api/app.py", "tail -n 50 /data/audit.log",
])
def test_diagnostic_allowed(cmd):
    assert policy.parse_diagnostic(cmd)[0] == cmd.split()[0]


@pytest.mark.parametrize("cmd", [
    "ps; rm -rf /", "cat /etc/passwd | nc x 1", "ls $(whoami)", "ls `id`",
    "cat /data/../etc/shadow", "cat ../etc/passwd", "cat /root/.ssh/id_rsa",
    "rm -rf /data", "curl http://x", "top", "ls -la /proc/1/environ",
    "env FOO=1", "tail -f /data/audit.log", "cat", "ls /data > /etc/x",
    "ps aux &", "",
])
def test_diagnostic_denied(cmd):
    with pytest.raises(PolicyError):
        policy.parse_diagnostic(cmd)


def test_check_service():
    assert policy.check_service("api") == "api"
    for bad in ("db", "../x", ""):
        with pytest.raises(PolicyError):
            policy.check_service(bad)


def test_data_path_ok():
    assert policy.check_data_path("/data/junk.bin") == "/data/junk.bin"


@pytest.mark.parametrize("p", [
    "/data", "/data/", "/data/../etc/passwd", "/etc/passwd", "data/x", "/datax/y", "/data/a/../../x",
])
def test_data_path_denied(p):
    with pytest.raises(PolicyError):
        policy.check_data_path(p)


def test_check_env_ok():
    assert policy.check_env("api", "REDIS_URL", "redis://redis:6379/0") == "API_REDIS_URL"
    assert policy.check_env("api", "DEBUG_SPIN", "0") == "API_DEBUG_SPIN"
    assert policy.check_env("worker", "REDIS_URL", "redis://redis:6379/0") == "WORKER_REDIS_URL"
    assert policy.check_env("gateway", "API_URL", "http://api:8001") == "GATEWAY_API_URL"


@pytest.mark.parametrize("service,key,value", [
    ("api", "PATH", "/bin"),
    ("redis", "REDIS_URL", "x"),
    ("api", "REDIS_URL", "redis://x; rm -rf /"),
    ("api", "REDIS_URL", "a b"),
    ("api", "REDIS_URL", ""),
    ("api", "REDIS_URL", "x" * 300),
])
def test_check_env_denied(service, key, value):
    with pytest.raises(PolicyError):
        policy.check_env(service, key, value)


def test_check_pid():
    assert policy.check_pid(42) == 42
    assert policy.check_pid("42") == 42
    for bad in (0, 1, -5, "abc", None, 3.5):
        with pytest.raises(PolicyError):
            policy.check_pid(bad)


def test_redact_env():
    text = "PATH=/bin\nOPENAI_API_KEY=abc\nDB_PASSWORD=x\nAUTH_TOKEN=t\nREDIS_URL=redis://redis:6379/0"
    out = policy.redact_env(text)
    assert "abc" not in out and "DB_PASSWORD=***" in out and "AUTH_TOKEN=***" in out
    assert "REDIS_URL=redis://redis:6379/0" in out and "PATH=/bin" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_policy.py -v`
Expected: FAIL with `ImportError: cannot import name 'policy'`

- [ ] **Step 3: Write minimal implementation**

`agentops/policy.py`:

```python
from __future__ import annotations

import posixpath
import re
import shlex

from agentops import config


class PolicyError(Exception):
    """An action was refused by the sandbox policy."""


FORBIDDEN_CHARS = set(";&|`$<>\\\n()*?[]{}!")
READ_PREFIXES = ("/app", "/data", "/etc")

ENV_ALLOWLIST = {
    ("api", "REDIS_URL"): "API_REDIS_URL",
    ("api", "DEBUG_SPIN"): "API_DEBUG_SPIN",
    ("worker", "REDIS_URL"): "WORKER_REDIS_URL",
    ("gateway", "API_URL"): "GATEWAY_API_URL",
}
ENV_VALUE_RE = re.compile(r"^[A-Za-z0-9:/._-]{1,200}$")
SECRET_NAME_RE = re.compile(r"(SECRET|PASSWORD|TOKEN|KEY|PASS)")


def check_service(name: str) -> str:
    if name not in config.SERVICES:
        raise PolicyError(f"unknown service {name!r}. Valid: {', '.join(config.SERVICES)}")
    return name


def _check_read_path(path: str) -> None:
    if not path.startswith("/") or ".." in path.split("/"):
        raise PolicyError(f"path not allowed: {path}")
    norm = posixpath.normpath(path)
    if not any(norm == p or norm.startswith(p + "/") for p in READ_PREFIXES):
        raise PolicyError(f"path outside allowed directories {READ_PREFIXES}: {path}")


def _check_file_args(args: list[str], flag_re: str, need_path: bool) -> None:
    paths = 0
    for a in args:
        if a.startswith("-"):
            if not re.fullmatch(flag_re, a):
                raise PolicyError(f"flag not allowed: {a}")
        elif a.isdigit():
            continue
        else:
            _check_read_path(a)
            paths += 1
    if need_path and paths == 0:
        raise PolicyError("a file path is required")


def _ps(args):
    for a in args:
        if not re.fullmatch(r"-?[A-Za-z0-9,=%]+", a):
            raise PolicyError(f"ps argument not allowed: {a}")


def _exact(allowed: list[list[str]]):
    def check(args):
        if args not in allowed:
            raise PolicyError(f"arguments not allowed: {' '.join(args)}")
    return check


_VALIDATORS = {
    "ps": _ps,
    "top": _exact([["-bn1"]]),
    "df": _exact([[], ["-h"]]),
    "env": _exact([[]]),
    "ls": lambda a: _check_file_args(a, r"-[alhtrRS1]+", need_path=False),
    "cat": lambda a: _check_file_args(a, r"(?!)", need_path=True),
    "tail": lambda a: _check_file_args(a, r"-n|-\d+", need_path=True),
}


def parse_diagnostic(command: str) -> list[str]:
    if any(c in FORBIDDEN_CHARS for c in command):
        raise PolicyError("shell metacharacters are not allowed in diagnostic commands")
    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise PolicyError(f"cannot parse command: {e}")
    if not argv:
        raise PolicyError("empty command")
    cmd, args = argv[0], argv[1:]
    if cmd not in _VALIDATORS:
        raise PolicyError(f"command not allowed: {cmd}. Allowed: {', '.join(sorted(_VALIDATORS))}")
    _VALIDATORS[cmd](args)
    return argv


def check_data_path(path: str) -> str:
    if not path.startswith("/") or ".." in path.split("/"):
        raise PolicyError(f"path not allowed: {path}")
    norm = posixpath.normpath(path)
    if not norm.startswith("/data/"):
        raise PolicyError(f"path must be inside /data/: {path}")
    return norm


def check_env(service: str, key: str, value: str) -> str:
    var = ENV_ALLOWLIST.get((service, key))
    if var is None:
        allowed = ", ".join(f"{s}.{k}" for s, k in ENV_ALLOWLIST)
        raise PolicyError(f"env var {service}.{key} is not settable. Allowed: {allowed}")
    if not ENV_VALUE_RE.fullmatch(value or ""):
        raise PolicyError("env value must match [A-Za-z0-9:/._-]{1,200}")
    return var


def check_pid(pid) -> int:
    if isinstance(pid, bool) or pid is None:
        raise PolicyError(f"invalid pid: {pid!r}")
    try:
        n = int(pid) if not isinstance(pid, float) else None
    except (TypeError, ValueError):
        raise PolicyError(f"invalid pid: {pid!r}")
    if n is None or n <= 1:
        raise PolicyError(f"pid not allowed: {pid!r} (PID 1 and invalid pids are protected)")
    return n


def redact_env(text: str) -> str:
    out = []
    for line in text.splitlines():
        name, sep, _ = line.partition("=")
        if sep and SECRET_NAME_RE.search(name.upper()):
            out.append(f"{name}=***")
        else:
            out.append(line)
    return "\n".join(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_policy.py -v`
Expected: all passed. If `ls -la /data` fails, check the `ls` flag regex `-[alhtrRS1]+` matches `-la`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add sandbox policy and redaction" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Executor

**Files:**
- Create: `agentops/executor.py`
- Test: `tests/integration/test_executor.py`

**Interfaces:**
- Consumes: `config.*`, `compose.compose`, `compose.set_env_var`, `policy.*`.
- Produces: `ExecutorError(Exception)`. `Executor(client=None)` with: `status() -> dict[str, dict]` (`{"api": {"state": "running", "exit_code": 0}, ...}`, state `"missing"` when the container does not exist); `logs(service, tail=50) -> str` (tail clamped to 1..200); `exec(service, argv: list[str]) -> tuple[int, str]`; `diagnostic(service, command: str) -> str`; `restart(service) -> None`; `start(service) -> None` (unpauses a paused container); `set_env(service, key, value) -> str`; `cleanup_files(service, path) -> str`; `kill_process(service, pid) -> str` (kills the process and all its descendants). Attribute `client` is the `docker.DockerClient`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_executor.py`:

```python
import re
import time

import pytest

from agentops import config
from agentops.compose import clear_env, compose
from agentops.executor import Executor
from agentops.policy import PolicyError
from agentops.verifier import wait_healthy

pytestmark = pytest.mark.docker


@pytest.fixture
def ex(stack):
    e = Executor()
    yield e
    clear_env()
    compose("up", "-d", "--no-deps", "api")
    assert wait_healthy(timeout=90).healthy


def test_status_lists_all_services(ex):
    status = ex.status()
    assert set(status) == set(config.SERVICES)
    assert all(v["state"] == "running" for v in status.values())


def test_logs_and_tail_clamp(ex):
    assert "Uvicorn running" in ex.logs("api", tail=500)
    with pytest.raises(PolicyError):
        ex.logs("nope")


def test_diagnostic_allowed_and_denied(ex):
    assert "uvicorn" in ex.diagnostic("api", "ps aux")
    assert "REDIS_URL=" in ex.diagnostic("api", "env")
    for bad in ("ps; rm -rf /", "cat /data/../etc/shadow", "curl http://x"):
        with pytest.raises(PolicyError):
            ex.diagnostic("api", bad)


def test_restart_and_start(ex):
    ex.restart("api")
    assert wait_healthy(timeout=90).healthy
    ex.client.containers.get(config.container_name("api")).stop(timeout=2)
    ex.start("api")
    assert wait_healthy(timeout=90).healthy


def test_set_env_recreates_service(ex):
    msg = ex.set_env("api", "DEBUG_SPIN", "0")
    assert "recreated" in msg
    assert wait_healthy(timeout=90).healthy
    with pytest.raises(PolicyError):
        ex.set_env("api", "REDIS_URL", "x; rm -rf /")
    with pytest.raises(PolicyError):
        ex.set_env("api", "PATH", "/bin")


def test_cleanup_files(ex):
    ex.exec("api", ["sh", "-c", "echo hi > /data/tmp.txt"])
    assert "tmp.txt" in ex.diagnostic("api", "ls /data")
    ex.cleanup_files("api", "/data/tmp.txt")
    assert "tmp.txt" not in ex.diagnostic("api", "ls /data")
    for bad in ("/data/../etc/passwd", "/etc/passwd", "/data"):
        with pytest.raises(PolicyError):
            ex.cleanup_files("api", bad)
    with pytest.raises(PolicyError):
        ex.cleanup_files("redis", "/data/x")


def test_kill_process_tree_and_pid1_denied(ex):
    api = ex.client.containers.get(config.container_name("api"))
    api.exec_run(["sleep", "300"], detach=True)
    time.sleep(0.5)
    ps = ex.diagnostic("api", "ps -eo pid,ppid,cmd")
    pid = int(re.search(r"^\s*(\d+)\s+\d+\s+sleep 300", ps, re.M).group(1))
    assert "killed" in ex.kill_process("api", pid)
    assert "sleep 300" not in ex.diagnostic("api", "ps -eo pid,ppid,cmd")
    with pytest.raises(PolicyError):
        ex.kill_process("api", 1)
    with pytest.raises(PolicyError):
        ex.kill_process("api", 999999)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -m docker tests/integration/test_executor.py -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'agentops.executor'`

- [ ] **Step 3: Write minimal implementation**

`agentops/executor.py`:

```python
from __future__ import annotations

import docker
from docker.errors import NotFound

from agentops import config
from agentops.compose import compose, set_env_var
from agentops.policy import (
    PolicyError,
    check_data_path,
    check_env,
    check_pid,
    check_service,
    parse_diagnostic,
    redact_env,
)


class ExecutorError(Exception):
    """A Docker operation failed or is not possible in the current state."""


class Executor:
    def __init__(self, client=None):
        self.client = client or docker.from_env()

    def _container(self, service: str):
        check_service(service)
        try:
            return self.client.containers.get(config.container_name(service))
        except NotFound:
            raise ExecutorError(f"container for {service} does not exist")

    def status(self) -> dict[str, dict]:
        out = {}
        for svc in config.SERVICES:
            try:
                c = self.client.containers.get(config.container_name(svc))
                out[svc] = {"state": c.status, "exit_code": c.attrs["State"].get("ExitCode")}
            except NotFound:
                out[svc] = {"state": "missing", "exit_code": None}
        return out

    def logs(self, service: str, tail: int = 50) -> str:
        n = max(1, min(int(tail), 200))
        return self._container(service).logs(tail=n).decode("utf-8", "replace")

    def exec(self, service: str, argv: list[str]) -> tuple[int, str]:
        c = self._container(service)
        if c.status != "running":
            raise ExecutorError(f"{service} is {c.status}; cannot run commands in it")
        res = c.exec_run(argv)
        return res.exit_code, res.output.decode("utf-8", "replace")

    def diagnostic(self, service: str, command: str) -> str:
        argv = parse_diagnostic(command)
        code, out = self.exec(service, argv)
        if argv[0] == "env":
            out = redact_env(out)
        return out if code == 0 else f"[exit {code}]\n{out}"

    def restart(self, service: str) -> None:
        self._container(service).restart(timeout=5)

    def start(self, service: str) -> None:
        c = self._container(service)
        if c.status == "paused":
            c.unpause()
        elif c.status != "running":
            c.start()

    def set_env(self, service: str, key: str, value: str) -> str:
        check_service(service)
        var = check_env(service, key, value)
        set_env_var(var, value)
        compose("up", "-d", "--force-recreate", "--no-deps", service)
        return f"set {key}={value} for {service} and recreated the container"

    def cleanup_files(self, service: str, path: str) -> str:
        if service not in ("api", "worker"):
            raise PolicyError("cleanup_files only applies to api and worker")
        p = check_data_path(path)
        code, out = self.exec(service, ["rm", "-f", "--", p])
        if code != 0:
            raise ExecutorError(out.strip() or f"rm exited {code}")
        return f"removed {p}"

    def kill_process(self, service: str, pid) -> str:
        pid = check_pid(pid)
        code, out = self.exec(service, ["ps", "-eo", "pid=,ppid="])
        if code != 0:
            raise ExecutorError(out.strip())
        children: dict[int, list[int]] = {}
        pids: set[int] = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                p, pp = int(parts[0]), int(parts[1])
                pids.add(p)
                children.setdefault(pp, []).append(p)
        if pid not in pids:
            raise PolicyError(f"pid {pid} is not running in {service}")
        tree, stack = [], [pid]
        while stack:
            cur = stack.pop()
            tree.append(cur)
            stack.extend(children.get(cur, []))
        if 1 in tree:
            raise PolicyError("refusing to kill PID 1")
        self.exec(service, ["kill", "-9", *map(str, tree)])
        return f"killed {len(tree)} process(es): {sorted(tree)}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -m docker tests/integration/test_executor.py -v`
Expected: 7 passed. If `test_kill_process_tree_and_pid1_denied` fails because `sleep 300` did not show, increase the `time.sleep(0.5)` to `1`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add policy-enforced docker executor" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Chaos injector

**Files:**
- Create: `agentops/chaos.py`
- Modify: `tests/conftest.py` (add nothing; tests call `reset()` themselves)
- Test: `tests/test_chaos_unit.py`, `tests/integration/test_chaos.py`

**Interfaces:**
- Consumes: `Executor` (methods `client`, `exec`), `compose.compose/set_env_var/clear_env`, `verifier.wait_healthy`, `config.container_name`.
- Produces: `FAULTS: tuple[str, ...]` = `("crash", "bad_config", "dependency_down", "disk_full", "cpu_hog")`. `ChaosError(Exception)`. `inject(fault: str, executor: Executor | None = None) -> None` (raises `ValueError` for unknown fault before touching Docker). `reset(timeout: float = 90.0) -> None` (restores a clean healthy stack or raises `ChaosError`). CLI: `python -m agentops.chaos inject <fault>` and `python -m agentops.chaos reset`.

- [ ] **Step 1: Write the failing tests**

`tests/test_chaos_unit.py`:

```python
import pytest

from agentops import chaos


def test_faults_are_the_five_spec_faults():
    assert chaos.FAULTS == ("crash", "bad_config", "dependency_down", "disk_full", "cpu_hog")


def test_unknown_fault_rejected_before_docker():
    with pytest.raises(ValueError):
        chaos.inject("meteor_strike", executor=object())
```

`tests/integration/test_chaos.py`:

```python
import time

import pytest

from agentops import chaos
from agentops.verifier import verify, wait_healthy

pytestmark = pytest.mark.docker


def _unhealthy_within(timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not verify().healthy:
            return True
        time.sleep(1)
    return False


def test_reset_gives_healthy_stack(stack):
    chaos.reset()
    assert verify().healthy


@pytest.mark.parametrize("fault", chaos.FAULTS)
def test_fault_breaks_stack_and_reset_heals(stack, fault):
    chaos.reset()
    chaos.inject(fault)
    assert _unhealthy_within(), f"{fault} did not make the verifier unhealthy"
    chaos.reset()
    assert wait_healthy(timeout=30).healthy
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_chaos_unit.py -v`
Expected: FAIL with `ImportError: cannot import name 'chaos'`

- [ ] **Step 3: Write minimal implementation**

`agentops/chaos.py`:

```python
from __future__ import annotations

import sys
import time

import docker

from agentops import config
from agentops.compose import clear_env, compose, set_env_var
from agentops.executor import Executor, ExecutorError
from agentops.verifier import wait_healthy

FAULTS = ("crash", "bad_config", "dependency_down", "disk_full", "cpu_hog")


class ChaosError(Exception):
    pass


def inject(fault: str, executor: Executor | None = None) -> None:
    if fault not in FAULTS:
        raise ValueError(f"unknown fault {fault!r}. Valid: {', '.join(FAULTS)}")
    ex = executor or Executor()
    if fault == "crash":
        ex.client.containers.get(config.container_name("api")).kill()
    elif fault == "bad_config":
        set_env_var("API_REDIS_URL", "redis://redis-wrong:6379/0")
        compose("up", "-d", "--force-recreate", "--no-deps", "api")
    elif fault == "dependency_down":
        ex.client.containers.get(config.container_name("redis")).stop(timeout=2)
    elif fault == "disk_full":
        ex.exec("api", ["dd", "if=/dev/zero", "of=/data/junk.bin", "bs=1M", "count=10"])
    elif fault == "cpu_hog":
        set_env_var("API_DEBUG_SPIN", "8")
        compose("up", "-d", "--force-recreate", "--no-deps", "api")


def _wipe_data(ex: Executor, service: str, tries: int = 15) -> None:
    for _ in range(tries):
        try:
            ex.exec(service, ["sh", "-c", "rm -rf /data/* /data/.[!.]*"])
            return
        except (ExecutorError, docker.errors.APIError):
            time.sleep(1)
    raise ChaosError(f"could not wipe /data in {service}")


def reset(timeout: float = 90.0) -> None:
    clear_env()
    compose("up", "-d", "--force-recreate")
    ex = Executor()
    for svc in ("api", "worker"):
        _wipe_data(ex, svc)
    result = wait_healthy(timeout=timeout)
    if not result.healthy:
        raise ChaosError(f"reset failed: {result.reason}")


def main(argv: list[str]) -> int:
    if argv[:1] == ["reset"]:
        reset()
        print("stack reset and healthy")
        return 0
    if len(argv) == 2 and argv[0] == "inject":
        inject(argv[1])
        print(f"injected {argv[1]}")
        return 0
    print("usage: python -m agentops.chaos reset | inject <fault>")
    print("faults:", ", ".join(FAULTS))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chaos_unit.py -v` then `pytest -m docker tests/integration/test_chaos.py -v` (takes several minutes).
Expected: unit 2 passed; integration 6 passed.

Calibration fallbacks (apply only if the named test fails):
- `cpu_hog` stays healthy: in `chaos.py` change `API_DEBUG_SPIN` from `"8"` to `"16"`; if still healthy, lower the probe threshold in `target/api/app.py` from `150` to `120`, then `compose("up","-d","--build")`.
- `disk_full` stays healthy: confirm `docker exec agentops-api ls -la /data` shows `junk.bin` of 10 MB; the quota is 8000000 bytes.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add chaos injector with five faults and reset" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Trace store and checkpoints

**Files:**
- Create: `agentops/db.py`, `agentops/trace.py`, `agentops/checkpoint.py`
- Test: `tests/test_trace.py`, `tests/test_checkpoint.py`

**Interfaces:**
- Consumes: `config.DB_PATH`.
- Produces:
  - `db.connect(path=None) -> sqlite3.Connection` (creates tables `events`, `checkpoints`, `incidents`; `row_factory = sqlite3.Row`).
  - `TraceStore(conn)`: `event(run_id: str, type: str, payload: dict, step: int = 0, incident_id: str | None = None, tokens: int = 0, cost: float = 0.0) -> None` (scrubs the OpenRouter API key from the payload); `events(run_id: str | None = None, incident_id: str | None = None) -> list[dict]` (each dict has `id, run_id, incident_id, step, type, payload (dict), tokens, cost, ts`, oldest first); `open_incident(incident_id: str, symptom: str, agent: str, fault: str | None = None) -> None`; `close_incident(incident_id: str, status: str, stop_reason: str, steps: int, tokens: int, cost: float) -> None` (status `"resolved"` or `"unresolved"`); `incident(incident_id) -> dict | None`; `incidents() -> list[dict]` (newest first). Incident dict keys: `id, opened, closed, symptom, agent, fault, status, stop_reason, steps, tokens, cost`; status is `"open"` until closed.
  - `CheckpointStore(conn)`: `save(run_id: str, step: int, messages: list, state: dict) -> None`; `latest(run_id: str) -> dict | None` (keys `run_id, step, messages, state`).

- [ ] **Step 1: Write the failing tests**

`tests/test_trace.py`:

```python
from agentops.db import connect
from agentops.trace import TraceStore


def make(tmp_path):
    conn = connect(tmp_path / "t.db")
    return conn, TraceStore(conn)


def test_event_roundtrip(tmp_path):
    _, store = make(tmp_path)
    store.event("r1", "tool_call", {"name": "x"}, step=1, incident_id="i1", tokens=10, cost=0.01)
    store.event("r2", "tool_call", {"name": "y"})
    evs = store.events(run_id="r1")
    assert len(evs) == 1
    assert evs[0]["payload"] == {"name": "x"}
    assert evs[0]["tokens"] == 10 and evs[0]["incident_id"] == "i1"
    assert len(store.events(incident_id="i1")) == 1


def test_api_key_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-123456789")
    conn, store = make(tmp_path)
    store.event("r", "llm_call", {"text": "the key is sk-or-secret-123456789"})
    raw = conn.execute("SELECT payload FROM events").fetchone()[0]
    assert "sk-or-secret" not in raw
    assert "***" in raw


def test_incident_lifecycle(tmp_path):
    _, store = make(tmp_path)
    store.open_incident("i1", "api down", "agent", fault="crash")
    row = store.incident("i1")
    assert row["status"] == "open" and row["closed"] is None and row["fault"] == "crash"
    store.close_incident("i1", "resolved", "finished", steps=4, tokens=900, cost=0.02)
    row = store.incident("i1")
    assert row["status"] == "resolved" and row["steps"] == 4 and row["closed"] >= row["opened"]
    assert store.incidents()[0]["id"] == "i1"
    assert store.incident("missing") is None
```

`tests/test_checkpoint.py`:

```python
from agentops.checkpoint import CheckpointStore
from agentops.db import connect


def test_latest_returns_highest_step(tmp_path):
    store = CheckpointStore(connect(tmp_path / "c.db"))
    store.save("r", 1, [{"role": "user", "content": "a"}], {"step": 1})
    store.save("r", 2, [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}], {"step": 2})
    cp = store.latest("r")
    assert cp["step"] == 2
    assert len(cp["messages"]) == 2
    assert cp["state"] == {"step": 2}


def test_latest_none_for_unknown_run(tmp_path):
    assert CheckpointStore(connect(tmp_path / "c.db")).latest("nope") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_trace.py tests/test_checkpoint.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentops.db'`

- [ ] **Step 3: Write minimal implementation**

`agentops/db.py`:

```python
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
```

`agentops/trace.py`:

```python
from __future__ import annotations

import json
import os
import time


def _scrub(text: str) -> str:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if len(key) >= 8:
        text = text.replace(key, "***")
    return text


class TraceStore:
    def __init__(self, conn):
        self.conn = conn

    def event(self, run_id, type, payload, step=0, incident_id=None, tokens=0, cost=0.0):
        body = _scrub(json.dumps(payload, default=str))
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
            (incident_id, time.time(), _scrub(symptom), agent, fault),
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
```

`agentops/checkpoint.py`:

```python
from __future__ import annotations

import json
import time


class CheckpointStore:
    def __init__(self, conn):
        self.conn = conn

    def save(self, run_id, step, messages, state):
        self.conn.execute(
            "INSERT OR REPLACE INTO checkpoints(run_id, step, messages, state, ts) VALUES(?,?,?,?,?)",
            (run_id, step, json.dumps(messages, default=str), json.dumps(state), time.time()),
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_trace.py tests/test_checkpoint.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add sqlite trace store and checkpoints" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Tool registry and responder tools

**Files:**
- Create: `agentops/tools.py`, `agentops/responder_tools.py`, `tests/fakes.py`
- Test: `tests/test_tools.py`, `tests/test_responder_tools.py`

**Interfaces:**
- Consumes: `config.TOOL_RESULT_MAX_CHARS, service_url`, `Executor` method names, `VerifyResult`.
- Produces:
  - `tools.ToolError(Exception)`; `tools.obj(properties: dict, required: list[str]) -> dict` (JSON-schema object, `additionalProperties: False`); `tools.truncate(text: str, limit: int | None = None) -> str` (keeps head and tail, inserts `...[truncated N chars]...`, result length at most `limit + 60`).
  - `ToolRegistry`: decorator `tool(name, description, parameters, permission)` where permission is `"read"`, `"mutate"` or `"control"`; `schemas(allowed: Iterable[str]) -> list[dict]` (OpenAI function-tool format); `call(name: str, args: dict, allowed: Iterable[str]) -> str` (raises `ToolError` for unknown tool, tool not in `allowed`, missing required argument, unexpected argument, wrong type; integers accept int or digit string; returns `str(result)`); `permission(name) -> str | None`.
  - `responder_tools.build_registry(executor, verify_fn) -> ToolRegistry` registering: `get_service_status`, `read_logs(service, tail=50)`, `get_metrics(service)`, `run_diagnostic(service, command)`, `restart_service(service)`, `start_service(service)`, `set_env(service, key, value)`, `cleanup_files(service, path)`, `kill_process(service, pid)`, `verify_health()`, `finish(summary)`.
  - `tests/fakes.py` (used by later tasks): `reply(text="", calls=())`, `ScriptedLLM(replies)`, `FakeExecutor`.

- [ ] **Step 1: Write the failing tests and fakes**

`tests/fakes.py`:

```python
import itertools
import json

from agentops.llm import LLMResponse, ToolCall

_ids = itertools.count(1)


def reply(text: str = "", calls=()) -> LLMResponse:
    """Build an LLMResponse. calls: iterable of (tool_name, args_dict_or_raw_json_string)."""
    tcs = []
    for name, args in calls:
        raw = args if isinstance(args, str) else json.dumps(args)
        tcs.append(ToolCall(id=f"call_{next(_ids)}", name=name, arguments=raw))
    message = {"role": "assistant", "content": text or None}
    if tcs:
        message["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in tcs
        ]
    return LLMResponse(text=text, tool_calls=tcs, prompt_tokens=100, completion_tokens=20,
                       cost=0.001, raw_message=message)


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []  # number of messages seen at each call

    def complete(self, messages, tools):
        self.calls.append(len(messages))
        if not self.replies:
            raise RuntimeError("script exhausted")
        return self.replies.pop(0)


class FakeExecutor:
    def __init__(self, states=None):
        self.states = states or {"gateway": "running", "api": "running", "worker": "running", "redis": "running"}
        self.actions = []
        self.raise_on = {}

    def _act(self, name, *args):
        self.actions.append((name, *args))
        if name in self.raise_on:
            raise self.raise_on[name]

    def status(self):
        return {s: {"state": st, "exit_code": 0} for s, st in self.states.items()}

    def logs(self, service, tail=50):
        self._act("logs", service, tail)
        return "log line\n" * 200

    def diagnostic(self, service, command):
        self._act("diagnostic", service, command)
        return f"ran {command}"

    def restart(self, service):
        self._act("restart", service)

    def start(self, service):
        self._act("start", service)

    def set_env(self, service, key, value):
        self._act("set_env", service, key, value)
        return "ok"

    def cleanup_files(self, service, path):
        self._act("cleanup_files", service, path)
        return f"removed {path}"

    def kill_process(self, service, pid):
        self._act("kill_process", service, pid)
        return f"killed {pid}"
```

`tests/test_tools.py`:

```python
import pytest

from agentops.tools import ToolError, ToolRegistry, obj, truncate


def make_registry():
    reg = ToolRegistry()

    @reg.tool("echo", "Echo text", obj({"text": {"type": "string"}}, ["text"]), "read")
    def echo(text):
        return text

    @reg.tool("kill", "Kill pid", obj({"pid": {"type": "integer"}}, ["pid"]), "mutate")
    def kill(pid):
        return f"pid={pid!r}"

    return reg


def test_schema_format():
    schema = make_registry().schemas(["echo"])[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert schema["function"]["parameters"]["required"] == ["text"]


def test_call_ok_and_integer_coercion():
    reg = make_registry()
    assert reg.call("echo", {"text": "hi"}, ["echo"]) == "hi"
    assert reg.call("kill", {"pid": "42"}, ["kill"]) == "pid=42"
    assert reg.permission("kill") == "mutate"
    assert reg.permission("nope") is None


@pytest.mark.parametrize("name,args,allowed", [
    ("missing", {}, ["missing"]),
    ("echo", {"text": "x"}, ["kill"]),
    ("echo", {}, ["echo"]),
    ("echo", {"text": "x", "extra": 1}, ["echo"]),
    ("echo", {"text": 5}, ["echo"]),
    ("kill", {"pid": "abc"}, ["kill"]),
    ("kill", {"pid": True}, ["kill"]),
])
def test_call_errors(name, args, allowed):
    with pytest.raises(ToolError):
        make_registry().call(name, args, allowed)


def test_truncate_keeps_head_and_tail():
    text = "A" * 5000 + "B" * 5000
    out = truncate(text, limit=3000)
    assert len(out) <= 3060
    assert out.startswith("A") and out.endswith("B")
    assert "truncated" in out
    assert truncate("short", limit=3000) == "short"
```

`tests/test_responder_tools.py`:

```python
import json

import pytest

from agentops import config
from agentops.responder_tools import build_registry
from agentops.tools import ToolError, truncate
from agentops.verifier import VerifyResult
from tests.fakes import FakeExecutor

ALL = ["get_service_status", "read_logs", "get_metrics", "run_diagnostic", "restart_service",
       "start_service", "set_env", "cleanup_files", "kill_process", "verify_health", "finish"]


def make():
    ex = FakeExecutor()
    reg = build_registry(ex, lambda: VerifyResult(False, "api /health 503"))
    return ex, reg


def test_all_tools_registered_with_permissions():
    _, reg = make()
    assert len(reg.schemas(ALL)) == len(ALL)
    assert reg.permission("read_logs") == "read"
    assert reg.permission("restart_service") == "mutate"
    assert reg.permission("finish") == "control"


def test_tools_delegate_to_executor():
    ex, reg = make()
    reg.call("restart_service", {"service": "api"}, ALL)
    reg.call("start_service", {"service": "redis"}, ALL)
    reg.call("set_env", {"service": "api", "key": "DEBUG_SPIN", "value": "0"}, ALL)
    reg.call("cleanup_files", {"service": "api", "path": "/data/junk.bin"}, ALL)
    reg.call("kill_process", {"service": "api", "pid": "77"}, ALL)
    assert ex.actions == [
        ("restart", "api"), ("start", "redis"), ("set_env", "api", "DEBUG_SPIN", "0"),
        ("cleanup_files", "api", "/data/junk.bin"), ("kill_process", "api", 77),
    ]


def test_verify_health_returns_json():
    _, reg = make()
    data = json.loads(reg.call("verify_health", {}, ALL))
    assert data == {"healthy": False, "reason": "api /health 503"}


def test_status_is_json():
    _, reg = make()
    assert "gateway" in json.loads(reg.call("get_service_status", {}, ALL))


def test_get_metrics_only_for_api():
    _, reg = make()
    with pytest.raises(ToolError):
        reg.call("get_metrics", {"service": "worker"}, ALL)


def test_large_logs_truncate_at_limit():
    _, reg = make()
    out = truncate(reg.call("read_logs", {"service": "api", "tail": 200}, ALL))
    assert len(out) <= config.TOOL_RESULT_MAX_CHARS + 60
    assert "truncated" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py tests/test_responder_tools.py -v`
Expected: FAIL with `ModuleNotFoundError` (`agentops.tools`, then `agentops.llm` via `tests/fakes.py`). `tests/fakes.py` imports `agentops.llm`, created in Task 9; to keep this task self-contained, create a stub now in Step 3.

- [ ] **Step 3: Write minimal implementation**

`agentops/llm.py` (stub data classes only; Task 9 adds the client):

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as sent by the model


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    prompt_tokens: int
    completion_tokens: int
    cost: float
    raw_message: dict = field(default_factory=dict)
```

`agentops/tools.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from agentops import config

PERMISSIONS = ("read", "mutate", "control")


class ToolError(Exception):
    """The model called a tool incorrectly."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable
    permission: str


def obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def truncate(text: str, limit: int | None = None) -> str:
    limit = limit or config.TOOL_RESULT_MAX_CHARS
    if len(text) <= limit:
        return text
    half = limit // 2
    dropped = len(text) - 2 * half
    return f"{text[:half]}\n...[truncated {dropped} chars]...\n{text[-half:]}"


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def tool(self, name: str, description: str, parameters: dict, permission: str):
        assert permission in PERMISSIONS, permission

        def deco(fn):
            self._tools[name] = Tool(name, description, parameters, fn, permission)
            return fn

        return deco

    def permission(self, name: str) -> str | None:
        t = self._tools.get(name)
        return t.permission if t else None

    def schemas(self, allowed: Iterable[str]) -> list[dict]:
        return [
            {"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in (self._tools[n] for n in allowed)
        ]

    def call(self, name: str, args: dict, allowed: Iterable[str]) -> str:
        if name not in self._tools:
            raise ToolError(f"unknown tool: {name}")
        if name not in set(allowed):
            raise ToolError(f"tool not available to this agent: {name}")
        tool = self._tools[name]
        props = tool.parameters.get("properties", {})
        for key in args:
            if key not in props:
                raise ToolError(f"unexpected argument {key!r} for {name}")
        for key in tool.parameters.get("required", []):
            if key not in args:
                raise ToolError(f"missing required argument {key!r} for {name}")
        clean = {k: self._coerce(name, k, v, props[k]) for k, v in args.items()}
        return str(tool.func(**clean))

    @staticmethod
    def _coerce(tool: str, key: str, value, spec: dict):
        kind = spec.get("type")
        if kind == "string":
            if not isinstance(value, str):
                raise ToolError(f"argument {key!r} of {tool} must be a string")
            return value
        if kind == "integer":
            if isinstance(value, bool):
                raise ToolError(f"argument {key!r} of {tool} must be an integer")
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip().isdigit():
                return int(value)
            raise ToolError(f"argument {key!r} of {tool} must be an integer")
        return value
```

`agentops/responder_tools.py`:

```python
from __future__ import annotations

import json

import httpx

from agentops import config
from agentops.tools import ToolError, ToolRegistry, obj

SERVICE = {"type": "string", "description": "Service name: gateway, api, worker or redis"}


def build_registry(executor, verify_fn) -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool("get_service_status", "List every container and its Docker state.", obj({}, []), "read")
    def get_service_status():
        return json.dumps(executor.status())

    @reg.tool("read_logs", "Read the last N log lines of a service (default 50, max 200).",
              obj({"service": SERVICE, "tail": {"type": "integer"}}, ["service"]), "read")
    def read_logs(service, tail=50):
        return executor.logs(service, tail)

    @reg.tool("get_metrics", "Read /metrics of the api service (request count, p95 latency, cpu probe).",
              obj({"service": SERVICE}, ["service"]), "read")
    def get_metrics(service):
        if service != "api":
            raise ToolError("only the api service exposes /metrics")
        return httpx.get(f"{config.service_url('api')}/metrics", timeout=3).text

    @reg.tool("run_diagnostic",
              "Run a read-only command inside a service container. Allowed: ps, top -bn1, df -h, ls, cat, env, tail. "
              "No pipes or shell syntax. Paths must be under /app, /data or /etc.",
              obj({"service": SERVICE, "command": {"type": "string"}}, ["service", "command"]), "read")
    def run_diagnostic(service, command):
        return executor.diagnostic(service, command)

    @reg.tool("restart_service", "Restart a container. Does not change its configuration or data.",
              obj({"service": SERVICE}, ["service"]), "mutate")
    def restart_service(service):
        executor.restart(service)
        return f"restarted {service}"

    @reg.tool("start_service", "Start a stopped or paused container.",
              obj({"service": SERVICE}, ["service"]), "mutate")
    def start_service(service):
        executor.start(service)
        return f"started {service}"

    @reg.tool("set_env",
              "Change an environment variable and recreate the service. Settable: api.REDIS_URL, api.DEBUG_SPIN, "
              "worker.REDIS_URL, gateway.API_URL. Value characters: letters, digits and : / . _ -",
              obj({"service": SERVICE, "key": {"type": "string"}, "value": {"type": "string"}},
                  ["service", "key", "value"]), "mutate")
    def set_env(service, key, value):
        return executor.set_env(service, key, value)

    @reg.tool("cleanup_files", "Delete one file under /data/ inside api or worker.",
              obj({"service": SERVICE, "path": {"type": "string"}}, ["service", "path"]), "mutate")
    def cleanup_files(service, path):
        return executor.cleanup_files(service, path)

    @reg.tool("kill_process", "Kill a process and all its children inside a service. PID 1 is protected.",
              obj({"service": SERVICE, "pid": {"type": "integer"}}, ["service", "pid"]), "mutate")
    def kill_process(service, pid):
        return executor.kill_process(service, pid)

    @reg.tool("verify_health", "Run the independent health check. Returns healthy true or false and the reason.",
              obj({}, []), "read")
    def verify_health():
        r = verify_fn()
        return json.dumps({"healthy": r.healthy, "reason": r.reason})

    @reg.tool("finish", "End the incident. Call only after verify_health reports healthy.",
              obj({"summary": {"type": "string", "description": "Root cause and fix in one paragraph"}},
                  ["summary"]), "control")
    def finish(summary):
        return summary

    return reg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py tests/test_responder_tools.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add tool registry and incident responder tools" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: LLM client

**Files:**
- Modify: `agentops/llm.py` (add client on top of the Task 8 data classes)
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: the `ToolCall` and `LLMResponse` dataclasses from Task 8.
- Produces: `LLMError(Exception)`. `OpenRouterClient(model: str | None = None, client=None, sleep=time.sleep)`; `complete(messages: list[dict], tools: list[dict]) -> LLMResponse`. Model defaults to env `AGENTOPS_MODEL`; key from env `OPENROUTER_API_KEY`; `.env` is loaded with python-dotenv without overriding real environment variables. Retries 3 times (sleep 1, 2, 4 s) on connection errors, rate limits and 5xx, then raises `LLMError`. `LLMResponse.cost` comes from OpenRouter usage accounting (0.0 when absent).

- [ ] **Step 1: Write the failing tests**

`tests/test_llm.py`:

```python
from types import SimpleNamespace

import httpx
import openai
import pytest

from agentops.llm import LLMError, OpenRouterClient


def fake_response(content="hello", tool_calls=None, cost=0.002):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=50, completion_tokens=10, model_extra={"cost": cost})
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class FakeOpenAI:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.kwargs = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs.append(kwargs)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def conn_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "http://x"))


def test_parses_text_tool_calls_and_usage():
    tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="read_logs", arguments='{"service":"api"}'))
    fake = FakeOpenAI([fake_response(content=None, tool_calls=[tc])])
    llm = OpenRouterClient(model="m", client=fake, sleep=lambda s: None)
    resp = llm.complete([{"role": "user", "content": "x"}], [{"type": "function"}])
    assert resp.tool_calls[0].name == "read_logs"
    assert resp.tool_calls[0].arguments == '{"service":"api"}'
    assert resp.prompt_tokens == 50 and resp.completion_tokens == 10 and resp.cost == 0.002
    assert resp.raw_message["tool_calls"][0]["function"]["name"] == "read_logs"
    assert fake.kwargs[0]["model"] == "m" and fake.kwargs[0]["temperature"] == 0


def test_plain_text_response():
    llm = OpenRouterClient(model="m", client=FakeOpenAI([fake_response("done")]), sleep=lambda s: None)
    resp = llm.complete([], [])
    assert resp.text == "done" and resp.tool_calls == []
    assert resp.raw_message == {"role": "assistant", "content": "done"}


def test_retries_then_succeeds():
    sleeps = []
    fake = FakeOpenAI([conn_error(), conn_error(), fake_response()])
    llm = OpenRouterClient(model="m", client=fake, sleep=sleeps.append)
    assert llm.complete([], []).text == "hello"
    assert sleeps == [1, 2]


def test_gives_up_after_three_attempts():
    fake = FakeOpenAI([conn_error(), conn_error(), conn_error()])
    llm = OpenRouterClient(model="m", client=fake, sleep=lambda s: None)
    with pytest.raises(LLMError):
        llm.complete([], [])


def test_missing_key_and_model(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("AGENTOPS_MODEL", "m")
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        OpenRouterClient()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k" * 10)
    monkeypatch.delenv("AGENTOPS_MODEL", raising=False)
    with pytest.raises(LLMError, match="AGENTOPS_MODEL"):
        OpenRouterClient()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm.py -v`
Expected: FAIL with `ImportError: cannot import name 'LLMError'`

- [ ] **Step 3: Write minimal implementation**

Replace `agentops/llm.py` with:

```python
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import openai
from dotenv import load_dotenv
from openai import OpenAI


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as sent by the model


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    prompt_tokens: int
    completion_tokens: int
    cost: float
    raw_message: dict = field(default_factory=dict)


class OpenRouterClient:
    def __init__(self, model: str | None = None, client=None, sleep=time.sleep):
        load_dotenv()
        self.model = model or os.getenv("AGENTOPS_MODEL")
        if not self.model:
            raise LLMError("AGENTOPS_MODEL is not set")
        if client is None:
            key = os.getenv("OPENROUTER_API_KEY")
            if not key:
                raise LLMError("OPENROUTER_API_KEY is not set")
            client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
        self.client = client
        self._sleep = sleep

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    temperature=0,
                    extra_body={"usage": {"include": True}},
                )
                return self._parse(resp)
            except (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError) as e:
                last = e
                if attempt < 2:
                    self._sleep(2 ** attempt)
        raise LLMError(f"LLM call failed after 3 attempts: {type(last).__name__}")

    @staticmethod
    def _parse(resp) -> LLMResponse:
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
        ]
        extra = getattr(resp.usage, "model_extra", None) or {}
        raw: dict = {"role": "assistant", "content": msg.content}
        if calls:
            raw["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in calls
            ]
        return LLMResponse(
            text=msg.content or "",
            tool_calls=calls,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            cost=float(extra.get("cost") or 0.0),
            raw_message=raw,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm.py tests/test_tools.py tests/test_responder_tools.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add OpenRouter LLM client with retry" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Agent loop

**Files:**
- Create: `agentops/agent.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: `LLMResponse`/`ToolCall`, `ToolRegistry` (`call`, `schemas`, `permission`), `TraceStore.event`, `CheckpointStore.save/latest`, `VerifyResult`, `policy.PolicyError`, `tools.ToolError`, `tools.truncate`, `config.MAX_STEPS, MAX_FAILED_CALLS, RUN_TIMEOUT_S, TOKEN_BUDGET`; test helpers `reply`, `ScriptedLLM`, `FakeExecutor`, `build_registry`.
- Produces: `RunResult(run_id: str, success: bool, stop_reason: str, steps: int, tokens: int, cost: float, summary: str)`. `Agent(llm, registry, allowed_tools, verify_fn, trace, checkpoints, system_prompt, max_steps=..., max_failed=..., timeout_s=..., token_budget=..., step_hook=None)`; `run(incident_id: str, context: str, run_id: str | None = None) -> RunResult`; `resume(run_id: str) -> RunResult`. Stop reasons: `"finished"`, `"step_limit"`, `"timeout"`, `"token_budget"`, `"llm_error"`, `"too_many_failures"`. `step_hook(step: int)` is called after each step's checkpoint is saved. Trace event types written: `llm_call`, `tool_call`, `tool_result`, `policy_denied`, `verify`, `llm_error`, `run_end`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agent.py`:

```python
import pytest

from agentops.agent import Agent
from agentops.checkpoint import CheckpointStore
from agentops.db import connect
from agentops.policy import PolicyError
from agentops.responder_tools import build_registry
from agentops.trace import TraceStore
from agentops.verifier import VerifyResult
from tests.fakes import FakeExecutor, ScriptedLLM, reply

ALLOWED = ["get_service_status", "read_logs", "run_diagnostic", "restart_service", "verify_health", "finish"]
HEALTHY = VerifyResult(True)
UNHEALTHY = VerifyResult(False, "api /health 503")


class Crash(Exception):
    pass


def make_agent(tmp_path, replies, verify=lambda: HEALTHY, executor=None, llm=None, **kw):
    conn = connect(tmp_path / "a.db")
    trace, cps = TraceStore(conn), CheckpointStore(conn)
    ex = executor or FakeExecutor()
    llm = llm or ScriptedLLM(replies)
    agent = Agent(llm, build_registry(ex, verify), ALLOWED, verify, trace, cps, "You are an SRE.", **kw)
    return agent, trace, cps, llm


def seq(results):
    it = iter(results)
    return lambda: next(it)


def test_success_path(tmp_path):
    agent, trace, _, _ = make_agent(tmp_path, [
        reply(calls=[("get_service_status", {})]),
        reply(calls=[("restart_service", {"service": "api"})]),
        reply(calls=[("finish", {"summary": "restarted api"})]),
    ])
    res = agent.run("inc1", "api is down", run_id="r1")
    assert res.success and res.stop_reason == "finished"
    assert res.steps == 3 and res.tokens == 360
    assert res.summary == "restarted api"
    types = [e["type"] for e in trace.events(run_id="r1")]
    assert types.count("llm_call") == 3 and "run_end" in types


def test_finish_while_unhealthy_does_not_resolve(tmp_path):
    agent, trace, _, _ = make_agent(
        tmp_path,
        [reply(calls=[("finish", {"summary": "done"})]), reply(calls=[("finish", {"summary": "really done"})])],
        verify=seq([UNHEALTHY, HEALTHY]),
    )
    res = agent.run("inc1", "x", run_id="r1")
    assert res.success and res.steps == 2 and res.summary == "really done"
    results = [e["payload"] for e in trace.events(run_id="r1") if e["type"] == "tool_result"]
    assert results[0]["ok"] is False and "Not resolved" in results[0]["result"]


def test_step_limit(tmp_path):
    agent, _, _, _ = make_agent(tmp_path, [reply(calls=[("get_service_status", {})])] * 5, max_steps=3)
    res = agent.run("i", "x")
    assert not res.success and res.stop_reason == "step_limit" and res.steps == 3


def test_malformed_json_arguments_count_as_failed_calls(tmp_path):
    bad = reply(calls=[("get_service_status", "{not json")])
    agent, trace, _, _ = make_agent(tmp_path, [bad, bad, bad], max_failed=3)
    res = agent.run("i", "x", run_id="r1")
    assert res.stop_reason == "too_many_failures" and not res.success
    msgs = [e["payload"]["result"] for e in trace.events(run_id="r1") if e["type"] == "tool_result"]
    assert all("invalid JSON" in m for m in msgs)


def test_failed_call_counter_resets_after_success(tmp_path):
    bad = reply(calls=[("nope", {})])
    good = reply(calls=[("get_service_status", {})])
    agent, _, _, _ = make_agent(
        tmp_path, [bad, bad, good, bad, bad, reply(calls=[("finish", {"summary": "s"})])], max_failed=3)
    assert agent.run("i", "x").success


def test_unknown_and_disallowed_tools(tmp_path):
    agent, trace, _, _ = make_agent(tmp_path, [
        reply(calls=[("set_env", {"service": "api", "key": "DEBUG_SPIN", "value": "0"})]),
        reply(calls=[("finish", {"summary": "s"})]),
    ])
    res = agent.run("i", "x", run_id="r1")
    first = [e for e in trace.events(run_id="r1") if e["type"] == "tool_result"][0]["payload"]
    assert res.success and first["ok"] is False and "not available" in first["result"]


def test_policy_denial_is_traced_and_loop_continues(tmp_path):
    ex = FakeExecutor()
    ex.raise_on["diagnostic"] = PolicyError("command not allowed: rm")
    agent, trace, _, _ = make_agent(tmp_path, [
        reply(calls=[("run_diagnostic", {"service": "api", "command": "rm -rf /"})]),
        reply(calls=[("finish", {"summary": "s"})]),
    ], executor=ex)
    res = agent.run("i", "x", run_id="r1")
    assert res.success
    denied = [e for e in trace.events(run_id="r1") if e["type"] == "policy_denied"]
    assert len(denied) == 1 and "command not allowed" in denied[0]["payload"]["reason"]


def test_large_tool_output_is_truncated_in_messages(tmp_path):
    agent, _, cps, _ = make_agent(tmp_path, [
        reply(calls=[("read_logs", {"service": "api", "tail": 200})]),
        reply(calls=[("finish", {"summary": "s"})]),
    ])
    agent.run("i", "x", run_id="r1")
    tool_msgs = [m for m in cps.latest("r1")["messages"] if m["role"] == "tool"]
    assert "truncated" in tool_msgs[0]["content"] and len(tool_msgs[0]["content"]) < 3200


def test_text_only_replies_are_nudged_then_fail(tmp_path):
    agent, _, _, _ = make_agent(tmp_path, [reply("thinking")] * 3, max_failed=3)
    res = agent.run("i", "x")
    assert res.stop_reason == "too_many_failures"


def test_token_budget(tmp_path):
    agent, _, _, _ = make_agent(
        tmp_path, [reply(calls=[("get_service_status", {})])] * 5, token_budget=200)
    res = agent.run("i", "x")
    assert res.stop_reason == "token_budget" and res.steps == 2


def test_timeout(tmp_path):
    agent, _, _, llm = make_agent(tmp_path, [reply(calls=[("get_service_status", {})])], timeout_s=0)
    res = agent.run("i", "x")
    assert res.stop_reason == "timeout" and res.steps == 0 and llm.calls == []


def test_llm_error(tmp_path):
    agent, trace, _, _ = make_agent(tmp_path, [])  # script exhausted raises RuntimeError
    res = agent.run("i", "x", run_id="r1")
    assert res.stop_reason == "llm_error"
    assert any(e["type"] == "llm_error" for e in trace.events(run_id="r1"))


def test_resume_after_crash(tmp_path):
    def hook(step):
        if step == 2:
            raise Crash()

    replies = [
        reply(calls=[("get_service_status", {})]),
        reply(calls=[("restart_service", {"service": "api"})]),
        reply(calls=[("finish", {"summary": "recovered"})]),
    ]
    agent, trace, cps, _ = make_agent(tmp_path, replies, step_hook=hook)
    with pytest.raises(Crash):
        agent.run("inc1", "api is down", run_id="r1")
    assert cps.latest("r1")["step"] == 2

    llm2 = ScriptedLLM(replies[2:])
    agent2, _, _, _ = make_agent(tmp_path, [], llm=llm2)
    res = agent2.resume("r1")
    assert res.success and res.steps == 3 and res.summary == "recovered"
    assert llm2.calls[0] == 6  # system, user, 2 assistant, 2 tool messages restored


def test_resume_unknown_run(tmp_path):
    agent, _, _, _ = make_agent(tmp_path, [])
    with pytest.raises(ValueError):
        agent.resume("missing")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentops.agent'`

- [ ] **Step 3: Write minimal implementation**

`agentops/agent.py`:

```python
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from agentops import config
from agentops.policy import PolicyError
from agentops.tools import ToolError, truncate


@dataclass
class RunResult:
    run_id: str
    success: bool
    stop_reason: str
    steps: int
    tokens: int
    cost: float
    summary: str


class Agent:
    def __init__(self, llm, registry, allowed_tools, verify_fn, trace, checkpoints, system_prompt,
                 max_steps=None, max_failed=None, timeout_s=None, token_budget=None, step_hook=None):
        self.llm = llm
        self.registry = registry
        self.allowed_tools = list(allowed_tools)
        self.verify_fn = verify_fn
        self.trace = trace
        self.checkpoints = checkpoints
        self.system_prompt = system_prompt
        self.max_steps = config.MAX_STEPS if max_steps is None else max_steps
        self.max_failed = config.MAX_FAILED_CALLS if max_failed is None else max_failed
        self.timeout_s = config.RUN_TIMEOUT_S if timeout_s is None else timeout_s
        self.token_budget = config.TOKEN_BUDGET if token_budget is None else token_budget
        self.step_hook = step_hook

    def run(self, incident_id: str, context: str, run_id: str | None = None) -> RunResult:
        run_id = run_id or uuid.uuid4().hex[:8]
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": context},
        ]
        state = {"incident_id": incident_id, "step": 0, "tokens": 0, "cost": 0.0, "failed": 0, "elapsed": 0.0}
        return self._loop(run_id, messages, state)

    def resume(self, run_id: str) -> RunResult:
        cp = self.checkpoints.latest(run_id)
        if cp is None:
            raise ValueError(f"no checkpoint for run {run_id}")
        return self._loop(run_id, cp["messages"], cp["state"])

    def _loop(self, run_id: str, messages: list, state: dict) -> RunResult:
        started = time.monotonic() - state["elapsed"]
        schemas = self.registry.schemas(self.allowed_tools)
        inc = state["incident_id"]
        while True:
            state["elapsed"] = time.monotonic() - started
            if state["step"] >= self.max_steps:
                return self._end(run_id, state, "step_limit")
            if state["elapsed"] >= self.timeout_s:
                return self._end(run_id, state, "timeout")
            if state["tokens"] >= self.token_budget:
                return self._end(run_id, state, "token_budget")
            try:
                resp = self.llm.complete(messages, schemas)
            except Exception as e:
                self.trace.event(run_id, "llm_error", {"error": f"{type(e).__name__}: {e}"},
                                 step=state["step"], incident_id=inc)
                return self._end(run_id, state, "llm_error")

            state["step"] += 1
            used = resp.prompt_tokens + resp.completion_tokens
            state["tokens"] += used
            state["cost"] += resp.cost
            self.trace.event(
                run_id, "llm_call",
                {"text": resp.text, "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in resp.tool_calls]},
                step=state["step"], incident_id=inc, tokens=used, cost=resp.cost)
            messages.append(resp.raw_message)

            finished_summary = None
            if not resp.tool_calls:
                messages.append({"role": "user", "content":
                                 "No tool call received. Call a tool, or call finish once verify_health reports healthy."})
                state["failed"] += 1
            for call in resp.tool_calls:
                if finished_summary is not None:
                    messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": "skipped: run already finished"})
                    continue
                content, ok, summary = self._execute(run_id, state, call)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
                state["failed"] = 0 if ok else state["failed"] + 1
                if summary is not None:
                    finished_summary = summary

            self.checkpoints.save(run_id, state["step"], messages, dict(state))
            if self.step_hook:
                self.step_hook(state["step"])
            if finished_summary is not None:
                return self._end(run_id, state, "finished", success=True, summary=finished_summary)
            if state["failed"] >= self.max_failed:
                return self._end(run_id, state, "too_many_failures")

    def _execute(self, run_id: str, state: dict, call):
        inc, step = state["incident_id"], state["step"]

        def record(ok: bool, result: str):
            self.trace.event(run_id, "tool_result", {"name": call.name, "ok": ok, "result": result},
                             step=step, incident_id=inc)

        try:
            args = json.loads(call.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as e:
            self.trace.event(run_id, "tool_call", {"name": call.name, "args": call.arguments},
                             step=step, incident_id=inc)
            msg = f"error: invalid JSON arguments: {e}"
            record(False, msg)
            return msg, False, None

        self.trace.event(
            run_id, "tool_call",
            {"name": call.name, "args": args, "permission": self.registry.permission(call.name)},
            step=step, incident_id=inc)

        if call.name == "finish" and "finish" in self.allowed_tools:
            verdict = self.verify_fn()
            self.trace.event(run_id, "verify", {"healthy": verdict.healthy, "reason": verdict.reason},
                             step=step, incident_id=inc)
            if not verdict.healthy:
                msg = f"Not resolved. Verifier reports: {verdict.reason}. Keep working."
                record(False, msg)
                return msg, False, None
            record(True, "Incident closed.")
            return "Incident closed.", True, str(args.get("summary", "")) or "(no summary)"

        try:
            result = truncate(self.registry.call(call.name, args, self.allowed_tools))
            ok = True
        except PolicyError as e:
            result, ok = f"denied: {e}", False
            self.trace.event(run_id, "policy_denied", {"name": call.name, "reason": str(e)},
                             step=step, incident_id=inc)
        except ToolError as e:
            result, ok = f"error: {e}", False
        except Exception as e:
            result, ok = f"error: {type(e).__name__}: {e}", False
        record(ok, result)
        return result, ok, None

    def _end(self, run_id, state, reason, success=False, summary="") -> RunResult:
        self.trace.event(
            run_id, "run_end",
            {"stop_reason": reason, "success": success, "steps": state["step"],
             "tokens": state["tokens"], "cost": state["cost"], "summary": summary},
            step=state["step"], incident_id=state["incident_id"])
        return RunResult(run_id, success, reason, state["step"], state["tokens"], state["cost"], summary)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent.py -v`
Expected: 15 passed. If `test_failed_call_counter_resets_after_success` fails, confirm `state["failed"]` resets to 0 on every ok call.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add agent loop with limits, verify gate and resume" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Baseline runbook

**Files:**
- Create: `agentops/baseline.py`
- Test: `tests/test_baseline.py`

**Interfaces:**
- Consumes: executor methods `status()`, `start(service)`, `restart(service)`; `TraceStore.event`; `verify` / `VerifyResult`; `config.SERVICES, APP_SERVICES`.
- Produces: `BaselineResult(success: bool, steps: int, stop_reason: str)` with stop reasons `"healthy"` and `"max_attempts"`. `run(executor, trace, incident_id, verify_fn=verify, max_attempts=3, settle_s=4.0, sleep=time.sleep) -> BaselineResult`. Rules: (1) any container whose state is not `running` gets `start`; wait `settle_s`; verify. (2) If still unhealthy, restart the service named at the start of the verifier reason (or all app services if the reason names none); wait; verify. Repeat up to `max_attempts`. Each action writes a `tool_call` event with `run_id = incident_id` and payload `{"name": "start_service" | "restart_service", "args": {"service": ...}}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_baseline.py`:

```python
from agentops import baseline
from agentops.db import connect
from agentops.trace import TraceStore
from agentops.verifier import VerifyResult
from tests.fakes import FakeExecutor

H = VerifyResult(True)


def U(reason):
    return VerifyResult(False, reason)


def run(tmp_path, executor, results, **kw):
    it = iter(results)
    trace = TraceStore(connect(tmp_path / "b.db"))
    out = baseline.run(executor, trace, "inc1", verify_fn=lambda: next(it),
                       settle_s=0, sleep=lambda s: None, **kw)
    return out, trace


def test_starts_exited_container_and_succeeds(tmp_path):
    ex = FakeExecutor(states={"gateway": "running", "api": "exited", "worker": "running", "redis": "running"})
    out, trace = run(tmp_path, ex, [H])
    assert out.success and out.steps == 1 and out.stop_reason == "healthy"
    assert ex.actions == [("start", "api")]
    ev = trace.events(run_id="inc1")[0]
    assert ev["type"] == "tool_call" and ev["payload"]["name"] == "start_service"


def test_restarts_named_unhealthy_service(tmp_path):
    ex = FakeExecutor()
    out, _ = run(tmp_path, ex, [U("api /health 503: redis down"), H])
    assert out.success and ex.actions == [("restart", "api")]


def test_restarts_all_app_services_when_reason_names_none(tmp_path):
    ex = FakeExecutor()
    out, _ = run(tmp_path, ex, [U("synthetic order failed: 502"), H])
    assert out.success
    assert ex.actions == [("restart", "gateway"), ("restart", "api"), ("restart", "worker")]


def test_gives_up_after_max_attempts(tmp_path):
    ex = FakeExecutor()
    out, _ = run(tmp_path, ex, [U("api /health 503")] * 6, max_attempts=3)
    assert not out.success and out.stop_reason == "max_attempts"
    assert ex.actions == [("restart", "api")] * 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_baseline.py -v`
Expected: FAIL with `ImportError: cannot import name 'baseline'`

- [ ] **Step 3: Write minimal implementation**

`agentops/baseline.py`:

```python
from __future__ import annotations

import time
from dataclasses import dataclass

from agentops import config
from agentops.verifier import verify


@dataclass
class BaselineResult:
    success: bool
    steps: int
    stop_reason: str


def run(executor, trace, incident_id, verify_fn=verify, max_attempts=3, settle_s=4.0,
        sleep=time.sleep) -> BaselineResult:
    steps = 0

    def act(name: str, service: str) -> None:
        nonlocal steps
        steps += 1
        trace.event(incident_id, "tool_call", {"name": name, "args": {"service": service}},
                    step=steps, incident_id=incident_id)
        if name == "start_service":
            executor.start(service)
        else:
            executor.restart(service)

    for _ in range(max_attempts):
        for svc, info in executor.status().items():
            if info["state"] != "running":
                act("start_service", svc)
        sleep(settle_s)
        result = verify_fn()
        if result.healthy:
            return BaselineResult(True, steps, "healthy")

        first = result.reason.split(" ", 1)[0]
        targets = [first] if first in config.SERVICES else list(config.APP_SERVICES)
        for svc in targets:
            act("restart_service", svc)
        sleep(settle_s)
        result = verify_fn()
        if result.healthy:
            return BaselineResult(True, steps, "healthy")
    return BaselineResult(False, steps, "max_attempts")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_baseline.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add rule-based baseline runbook" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Responder, handlers and watcher

**Files:**
- Create: `agentops/responder.py`, `agentops/handlers.py`, `agentops/watcher.py`, `agentops/watch.py`
- Test: `tests/test_handlers.py`, `tests/test_watcher.py`

**Interfaces:**
- Consumes: `Agent`, `build_registry`, `baseline.run`, `Executor`, `TraceStore`, `CheckpointStore`, `verify`, `OpenRouterClient`, `config.POLLS_TO_OPEN, POLLS_TO_CLOSE, POLL_INTERVAL_S`.
- Produces:
  - `responder.SYSTEM_PROMPT: str`, `responder.ALLOWED_TOOLS: list[str]`, `responder.incident_context(incident_id: str, symptom: str) -> str`, `responder.build_responder(llm, executor, trace, checkpoints, verify_fn=verify, **agent_kwargs) -> Agent`.
  - `handlers.HandlerResult(steps: int, tokens: int, cost: float, stop_reason: str)`; `handlers.make_handler(mode: str, *, llm=None, executor=None, trace=None, checkpoints=None, verify_fn=verify) -> Callable[[str, str], HandlerResult]` where `mode` is `"agent"` or `"baseline"` (other values raise `ValueError`); handler signature `handler(incident_id, symptom)`. `"agent"` mode requires `llm`, `trace`, `checkpoints`. Both modes accept a fake `executor`; when `executor` is `None` a real `Executor()` is created.
  - `watcher.Watcher(handler, trace, agent_name="agent", fault=None, verify_fn=verify, sleep=time.sleep)`: `tick() -> str | None` (returns the incident id if one was handled), `handle(symptom: str) -> str` (opens, runs handler, confirms recovery, closes; returns incident id), `run_forever(stop_event=None) -> None`. Incident ids look like `"{agent_name}-{fault or 'live'}-{epoch_ms}"`. A handler exception closes the incident as `unresolved` with stop reason `"error:<ExceptionName>"`.
  - CLI `python -m agentops.watch --mode agent|baseline`.

- [ ] **Step 1: Write the failing tests**

`tests/test_handlers.py`:

```python
import pytest

from agentops.checkpoint import CheckpointStore
from agentops.db import connect
from agentops.handlers import HandlerResult, make_handler
from agentops.responder import ALLOWED_TOOLS, SYSTEM_PROMPT, incident_context
from agentops.responder_tools import build_registry
from agentops.trace import TraceStore
from agentops.verifier import VerifyResult
from tests.fakes import FakeExecutor, ScriptedLLM, reply


def stores(tmp_path):
    conn = connect(tmp_path / "h.db")
    return TraceStore(conn), CheckpointStore(conn)


def test_allowed_tools_are_all_registered():
    reg = build_registry(FakeExecutor(), lambda: VerifyResult(True))
    assert len(reg.schemas(ALLOWED_TOOLS)) == len(ALLOWED_TOOLS)
    assert "finish" in ALLOWED_TOOLS


def test_prompt_and_context():
    assert "verify_health" in SYSTEM_PROMPT and "finish" in SYSTEM_PROMPT
    assert "api /health 503" in incident_context("inc1", "api /health 503")


def test_agent_handler(tmp_path):
    trace, cps = stores(tmp_path)
    llm = ScriptedLLM([
        reply(calls=[("restart_service", {"service": "api"})]),
        reply(calls=[("finish", {"summary": "restarted"})]),
    ])
    handler = make_handler("agent", llm=llm, executor=FakeExecutor(), trace=trace, checkpoints=cps,
                           verify_fn=lambda: VerifyResult(True))
    out = handler("inc1", "api down")
    assert out == HandlerResult(steps=2, tokens=240, cost=0.002, stop_reason="finished")


def test_baseline_handler(tmp_path):
    trace, cps = stores(tmp_path)
    ex = FakeExecutor(states={"gateway": "running", "api": "exited", "worker": "running", "redis": "running"})
    handler = make_handler("baseline", executor=ex, trace=trace, checkpoints=cps,
                           verify_fn=lambda: VerifyResult(True))
    out = handler("inc1", "api down")
    assert out.steps == 1 and out.tokens == 0 and out.stop_reason == "healthy"


def test_unknown_mode():
    with pytest.raises(ValueError):
        make_handler("magic")
```

`tests/test_watcher.py`:

```python
from agentops.db import connect
from agentops.handlers import HandlerResult
from agentops.trace import TraceStore
from agentops.verifier import VerifyResult
from agentops.watcher import Watcher

H = VerifyResult(True)


def U(reason="api down"):
    return VerifyResult(False, reason)


def make(tmp_path, results, handler=None):
    it = iter(results)
    trace = TraceStore(connect(tmp_path / "w.db"))
    calls = []

    def default_handler(incident_id, symptom):
        calls.append((incident_id, symptom))
        return HandlerResult(steps=3, tokens=100, cost=0.01, stop_reason="finished")

    w = Watcher(handler or default_handler, trace, agent_name="agent", fault="crash",
                verify_fn=lambda: next(it), sleep=lambda s: None)
    return w, trace, calls


def test_opens_incident_after_two_failed_polls_and_resolves(tmp_path):
    w, trace, calls = make(tmp_path, [H, U(), U(), H, H])
    assert w.tick() is None      # healthy
    assert w.tick() is None      # first failure, no incident yet
    inc = w.tick()               # second failure opens the incident
    assert inc is not None and len(calls) == 1 and calls[0][1] == "api down"
    row = trace.incident(inc)
    assert row["status"] == "resolved" and row["steps"] == 3 and row["fault"] == "crash"
    assert inc.startswith("agent-crash-")


def test_single_blip_opens_nothing(tmp_path):
    w, trace, calls = make(tmp_path, [U(), H, U(), H])
    for _ in range(4):
        assert w.tick() is None
    assert calls == [] and trace.incidents() == []


def test_unresolved_when_not_healthy_after_handler(tmp_path):
    w, trace, _ = make(tmp_path, [U(), U(), H, U()])
    w.tick()
    inc = w.tick()
    assert trace.incident(inc)["status"] == "unresolved"


def test_handler_exception_is_recorded_as_stop_reason(tmp_path):
    def boom(incident_id, symptom):
        raise RuntimeError("kaput")

    w, trace, _ = make(tmp_path, [U(), U(), H, H], handler=boom)
    w.tick()
    inc = w.tick()
    row = trace.incident(inc)
    assert row["status"] == "resolved" and row["stop_reason"] == "error:RuntimeError"


def test_run_forever_stops_on_event(tmp_path):
    import threading
    w, _, _ = make(tmp_path, [H] * 50)
    stop = threading.Event()
    stop.set()
    w.run_forever(stop)  # returns immediately
```

Note for `test_handler_exception_closes_incident_unresolved`: the handler raises but the verifier confirms healthy, so the incident status is `resolved` and only `stop_reason` carries the error. That is intentional: status follows the verifier, not the handler.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_handlers.py tests/test_watcher.py -v`
Expected: FAIL with `ImportError` for `agentops.responder`

- [ ] **Step 3: Write minimal implementation**

`agentops/responder.py`:

```python
from __future__ import annotations

from agentops.agent import Agent
from agentops.responder_tools import build_registry
from agentops.verifier import verify

SYSTEM_PROMPT = """You are an on-call SRE agent operating a Docker Compose stack.
Topology: gateway (port 8000) calls api (port 8001), api uses redis, worker (port 8002) consumes the redis queue.
Every app service exposes /health. A separate verifier decides whether the stack is healthy.

Method:
1. Call get_service_status and verify_health to see what is broken.
2. Use read_logs and run_diagnostic on the suspect service to find the root cause.
3. Apply the smallest fix with a mutating tool.
4. Call verify_health.
5. Call finish with one paragraph: root cause and fix.

Rules:
- Do not restart services blindly. A restart does not fix configuration, disk or process faults.
- Only call finish after verify_health reports healthy. The verifier has the final word.
- You have at most 15 steps. Prefer few, well-chosen actions.
- run_diagnostic takes one plain command. No pipes or shell syntax."""

ALLOWED_TOOLS = [
    "get_service_status", "read_logs", "get_metrics", "run_diagnostic", "restart_service",
    "start_service", "set_env", "cleanup_files", "kill_process", "verify_health", "finish",
]


def incident_context(incident_id: str, symptom: str) -> str:
    return (f"Incident {incident_id}. Monitoring symptom: {symptom}. "
            "Diagnose the root cause, fix it, confirm with verify_health, then call finish.")


def build_responder(llm, executor, trace, checkpoints, verify_fn=verify, **agent_kwargs) -> Agent:
    registry = build_registry(executor, verify_fn)
    return Agent(llm, registry, ALLOWED_TOOLS, verify_fn, trace, checkpoints, SYSTEM_PROMPT, **agent_kwargs)
```

`agentops/handlers.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from agentops import baseline
from agentops.responder import build_responder, incident_context
from agentops.verifier import verify


@dataclass
class HandlerResult:
    steps: int
    tokens: int
    cost: float
    stop_reason: str


def make_handler(mode: str, *, llm=None, executor=None, trace=None, checkpoints=None, verify_fn=verify):
    if mode not in ("agent", "baseline"):
        raise ValueError(f"unknown mode {mode!r}; use 'agent' or 'baseline'")

    def handler(incident_id: str, symptom: str) -> HandlerResult:
        ex = executor
        if ex is None:
            from agentops.executor import Executor
            ex = Executor()
        if mode == "agent":
            agent = build_responder(llm, ex, trace, checkpoints, verify_fn=verify_fn)
            res = agent.run(incident_id, incident_context(incident_id, symptom), run_id=incident_id)
            return HandlerResult(res.steps, res.tokens, res.cost, res.stop_reason)
        out = baseline.run(ex, trace, incident_id, verify_fn=verify_fn)
        return HandlerResult(out.steps, 0, 0.0, out.stop_reason)

    return handler
```

`agentops/watcher.py`:

```python
from __future__ import annotations

import time

from agentops import config
from agentops.handlers import HandlerResult
from agentops.verifier import verify


class Watcher:
    def __init__(self, handler, trace, agent_name="agent", fault=None, verify_fn=verify, sleep=time.sleep):
        self.handler = handler
        self.trace = trace
        self.agent_name = agent_name
        self.fault = fault
        self.verify_fn = verify_fn
        self.sleep = sleep
        self._fail = 0

    def tick(self) -> str | None:
        result = self.verify_fn()
        if result.healthy:
            self._fail = 0
            return None
        self._fail += 1
        if self._fail < config.POLLS_TO_OPEN:
            return None
        self._fail = 0
        return self.handle(result.reason)

    def handle(self, symptom: str) -> str:
        incident_id = f"{self.agent_name}-{self.fault or 'live'}-{int(time.time() * 1000)}"
        self.trace.open_incident(incident_id, symptom, self.agent_name, self.fault)
        try:
            out = self.handler(incident_id, symptom)
        except Exception as e:
            out = HandlerResult(0, 0, 0.0, f"error:{type(e).__name__}")
        resolved = self._confirm_healthy()
        self.trace.close_incident(incident_id, "resolved" if resolved else "unresolved",
                                  out.stop_reason, out.steps, out.tokens, out.cost)
        return incident_id

    def _confirm_healthy(self) -> bool:
        for i in range(config.POLLS_TO_CLOSE):
            if not self.verify_fn().healthy:
                return False
            if i < config.POLLS_TO_CLOSE - 1:
                self.sleep(1.0)
        return True

    def run_forever(self, stop_event=None) -> None:
        while not (stop_event and stop_event.is_set()):
            self.tick()
            self.sleep(config.POLL_INTERVAL_S)
```

`agentops/watch.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_handlers.py tests/test_watcher.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add responder, handlers and incident watcher" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Trial runner, single-incident CLI and model picker

**Files:**
- Create: `agentops/trial.py`, `agentops/run_one.py`, `scripts/pick_model.py`
- Test: `tests/test_trial.py`

**Interfaces:**
- Consumes: `chaos.reset/inject/ChaosError`, `Watcher.handle`, `make_handler`, `verify`, `TraceStore.incident`, `Agent.resume`, `OpenRouterClient`.
- Produces:
  - `trial.TrialResult(fault, mode, trial, success, mttr_s, steps, tokens, cost, stop_reason, incident_id)` dataclass.
  - `trial.wait_unhealthy(timeout=40.0, verify_fn=verify, sleep=time.sleep) -> str` (returns the failure reason; raises `chaos.ChaosError("fault had no effect: ...")` if still healthy at timeout).
  - `trial.run_trial(fault, mode, trial_no, handler, trace, reset_fn=chaos.reset, inject_fn=chaos.inject, verify_fn=verify, sleep=time.sleep) -> TrialResult`. MTTR is `closed - opened` of the incident row; `success` is `status == "resolved"`.
  - CLI: `python -m agentops.run_one --fault <fault> [--mode agent|baseline] [--crash-after-step N]` and `python -m agentops.run_one --resume <run_id>`.
  - `scripts/pick_model.py`: tries candidate models on `crash` and `bad_config` and prints a comparison.

- [ ] **Step 1: Write the failing tests**

`tests/test_trial.py`:

```python
import pytest

from agentops import chaos
from agentops.db import connect
from agentops.handlers import HandlerResult
from agentops.trace import TraceStore
from agentops.trial import run_trial, wait_unhealthy
from agentops.verifier import VerifyResult

H = VerifyResult(True)


def U(reason="api /health 503"):
    return VerifyResult(False, reason)


def test_wait_unhealthy_returns_reason():
    it = iter([H, H, U("api unreachable")])
    assert wait_unhealthy(timeout=5, verify_fn=lambda: next(it), sleep=lambda s: None) == "api unreachable"


def test_wait_unhealthy_raises_when_fault_has_no_effect():
    with pytest.raises(chaos.ChaosError, match="no effect"):
        wait_unhealthy(timeout=0.2, verify_fn=lambda: H, sleep=lambda s: __import__("time").sleep(0.05))


def test_run_trial_records_result(tmp_path):
    trace = TraceStore(connect(tmp_path / "t.db"))
    calls = []
    results = iter([U(), H, H])  # wait_unhealthy, then two confirm polls

    def handler(incident_id, symptom):
        calls.append(symptom)
        return HandlerResult(steps=2, tokens=300, cost=0.01, stop_reason="finished")

    res = run_trial(
        "crash", "agent", 1, handler, trace,
        reset_fn=lambda: calls.append("reset"), inject_fn=lambda f: calls.append(f"inject:{f}"),
        verify_fn=lambda: next(results), sleep=lambda s: None)
    assert calls == ["reset", "inject:crash", "api /health 503"]
    assert res.success and res.steps == 2 and res.tokens == 300 and res.stop_reason == "finished"
    assert res.mttr_s >= 0 and res.fault == "crash" and res.mode == "agent" and res.trial == 1
    assert trace.incident(res.incident_id)["fault"] == "crash"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_trial.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentops.trial'`

- [ ] **Step 3: Write minimal implementation**

`agentops/trial.py`:

```python
from __future__ import annotations

import time
from dataclasses import dataclass

from agentops import chaos
from agentops.verifier import verify
from agentops.watcher import Watcher


@dataclass
class TrialResult:
    fault: str
    mode: str
    trial: int
    success: bool
    mttr_s: float
    steps: int
    tokens: int
    cost: float
    stop_reason: str
    incident_id: str


def wait_unhealthy(timeout: float = 40.0, verify_fn=verify, sleep=time.sleep) -> str:
    deadline = time.monotonic() + timeout
    while True:
        result = verify_fn()
        if not result.healthy:
            return result.reason
        if time.monotonic() >= deadline:
            raise chaos.ChaosError("fault had no effect: stack still healthy after injection")
        sleep(1.0)


def run_trial(fault, mode, trial_no, handler, trace, reset_fn=chaos.reset, inject_fn=chaos.inject,
              verify_fn=verify, sleep=time.sleep) -> TrialResult:
    reset_fn()
    inject_fn(fault)
    symptom = wait_unhealthy(verify_fn=verify_fn, sleep=sleep)
    watcher = Watcher(handler, trace, agent_name=mode, fault=fault, verify_fn=verify_fn, sleep=sleep)
    incident_id = watcher.handle(symptom)
    row = trace.incident(incident_id)
    return TrialResult(
        fault=fault, mode=mode, trial=trial_no, success=row["status"] == "resolved",
        mttr_s=row["closed"] - row["opened"], steps=row["steps"], tokens=row["tokens"],
        cost=row["cost"], stop_reason=row["stop_reason"], incident_id=incident_id)
```

`agentops/run_one.py`:

```python
from __future__ import annotations

import argparse
import os

from agentops import chaos, config
from agentops.checkpoint import CheckpointStore
from agentops.db import connect
from agentops.executor import Executor
from agentops.handlers import make_handler
from agentops.responder import build_responder
from agentops.trace import TraceStore
from agentops.trial import run_trial
from agentops.verifier import verify, wait_healthy


def _resume(run_id: str, trace, cps) -> None:
    from agentops.llm import OpenRouterClient
    agent = build_responder(OpenRouterClient(), Executor(), trace, cps)
    res = agent.resume(run_id)
    healthy = wait_healthy(timeout=30).healthy
    incident_id = cps.latest(run_id)["state"]["incident_id"]
    trace.close_incident(incident_id, "resolved" if healthy else "unresolved",
                         res.stop_reason, res.steps, res.tokens, res.cost)
    print(f"resumed {run_id}: success={res.success} steps={res.steps} healthy={healthy}")
    print(res.summary)


def main() -> None:
    p = argparse.ArgumentParser(description="Run one incident end to end")
    p.add_argument("--fault", choices=chaos.FAULTS)
    p.add_argument("--mode", choices=["agent", "baseline"], default="agent")
    p.add_argument("--crash-after-step", type=int, help="kill this process after step N (resume demo)")
    p.add_argument("--resume", metavar="RUN_ID")
    args = p.parse_args()

    conn = connect(config.DB_PATH)
    trace, cps = TraceStore(conn), CheckpointStore(conn)

    if args.resume:
        _resume(args.resume, trace, cps)
        return
    if not args.fault:
        p.error("--fault is required unless --resume is given")

    llm = None
    if args.mode == "agent":
        from agentops.llm import OpenRouterClient
        llm = OpenRouterClient()

    step_hook = None
    if args.crash_after_step:
        def step_hook(step):
            if step >= args.crash_after_step:
                print(f"simulated crash after step {step}; resume with --resume <incident id shown in dashboard>")
                os._exit(1)

    handler = make_handler(args.mode, llm=llm, trace=trace, checkpoints=cps)
    if step_hook and args.mode == "agent":
        inner = handler

        def handler(incident_id, symptom, _inner=inner):
            from agentops.responder import incident_context
            agent = build_responder(llm, Executor(), trace, cps, step_hook=step_hook)
            res = agent.run(incident_id, incident_context(incident_id, symptom), run_id=incident_id)
            from agentops.handlers import HandlerResult
            return HandlerResult(res.steps, res.tokens, res.cost, res.stop_reason)

    result = run_trial(args.fault, args.mode, 1, handler, trace)
    print(result)
    print("final health:", verify())


if __name__ == "__main__":
    main()
```

`scripts/pick_model.py`:

```python
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

DEFAULT = "openai/gpt-4o-mini,anthropic/claude-3.5-haiku,google/gemini-2.0-flash-001,qwen/qwen-2.5-72b-instruct"


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
```

Add `pick_model.db` to `.gitignore` (already covered by `*.db`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_trial.py -v`
Expected: 3 passed

- [ ] **Step 5: Manual smoke test with a real model (needs Docker and the OpenRouter key)**

Run: `python scripts/pick_model.py`
Expected: a table with one row per model and fault. Pick the model with `ok True` on both faults at the lowest cost, then run:

```bash
echo 'AGENTOPS_MODEL=<winning model id>' >> .env
python -m agentops.run_one --fault crash
```

Expected output: a `TrialResult(... success=True ...)` line and `final health: VerifyResult(healthy=True ...)`. If every candidate fails on model-not-found errors, check the exact model IDs at `https://openrouter.ai/models` and set `AGENTOPS_CANDIDATES` with valid IDs.

Then run each remaining fault once (`bad_config`, `dependency_down`, `disk_full`, `cpu_hog`). If the agent fails a fault for a reason that looks like a prompt or tool-description gap (not model quality), fix `SYSTEM_PROMPT` in `agentops/responder.py` or the tool descriptions in `agentops/responder_tools.py`, rerun the unit tests, and retry.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add trial runner, single-incident CLI and model picker" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Eval harness and report

**Files:**
- Create: `agentops/eval_run.py`
- Test: `tests/test_eval_run.py`

**Interfaces:**
- Consumes: `run_trial`, `TrialResult`, `make_handler`, `chaos.FAULTS`, `TraceStore`, `CheckpointStore`, `OpenRouterClient`, `config.ROOT`.
- Produces: `FIELDS` (CSV columns: `fault, mode, trial, success, mttr_s, steps, tokens, cost, stop_reason, incident_id, error`). `run_eval(trials, modes, faults, out_dir, trial_fn) -> None` (appends one CSV row after every trial; if `trial_fn(fault, mode, trial_no)` raises, records a row with `success=False` and the error text and continues). `summarize(df) -> pd.DataFrame` (columns `mode, fault, trials, success_rate, steps, tokens, cost, mttr_s`; `mttr_s` averages successful trials only). `to_markdown(summary, overall) -> str`. `write_report(csv_path, out_dir) -> None` (writes `summary.md`, `success_rate.png`, `mttr.png`). CLI: `python -m agentops.eval_run --trials 3 --modes baseline agent`, plus `--report-only`.

- [ ] **Step 1: Write the failing tests**

`tests/test_eval_run.py`:

```python
import csv

import pandas as pd

from agentops import eval_run
from agentops.trial import TrialResult


def fake_trial(fault, mode, trial_no):
    if fault == "crash" and trial_no == 2:
        raise RuntimeError("reset failed: boom")
    ok = mode == "agent" or fault == "crash"
    return TrialResult(fault, mode, trial_no, ok, 10.0 if ok else 40.0, 3, 500 if mode == "agent" else 0,
                       0.01 if mode == "agent" else 0.0, "finished" if ok else "step_limit", f"{mode}-{fault}-{trial_no}")


def run(tmp_path, **kw):
    eval_run.run_eval(trials=2, modes=["baseline", "agent"], faults=["crash", "bad_config"],
                      out_dir=tmp_path, trial_fn=fake_trial, **kw)
    return tmp_path / "results.csv"


def test_run_eval_writes_rows_and_survives_failed_trial(tmp_path):
    path = run(tmp_path)
    rows = list(csv.DictReader(open(path)))
    assert len(rows) == 8
    assert list(rows[0].keys()) == eval_run.FIELDS
    errors = [r for r in rows if r["error"]]
    assert len(errors) == 2  # crash trial 2, both modes
    assert all(r["success"] == "False" and "boom" in r["error"] for r in errors)


def test_summarize_excludes_failed_trials_from_mttr(tmp_path):
    df = pd.read_csv(run(tmp_path))
    s = eval_run.summarize(df).set_index(["mode", "fault"])
    assert s.loc[("agent", "bad_config"), "success_rate"] == 1.0
    assert s.loc[("baseline", "bad_config"), "success_rate"] == 0.0
    assert pd.isna(s.loc[("baseline", "bad_config"), "mttr_s"])
    assert s.loc[("agent", "bad_config"), "mttr_s"] == 10.0
    assert s.loc[("agent", "crash"), "trials"] == 2


def test_report_files(tmp_path):
    path = run(tmp_path)
    eval_run.write_report(path, tmp_path)
    md = (tmp_path / "summary.md").read_text()
    assert "| mode | fault |" in md and "Overall" in md
    assert (tmp_path / "success_rate.png").stat().st_size > 0
    assert (tmp_path / "mttr.png").stat().st_size > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_eval_run.py -v`
Expected: FAIL with `ImportError: cannot import name 'eval_run'`

- [ ] **Step 3: Write minimal implementation**

`agentops/eval_run.py`:

```python
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from agentops import chaos, config

FIELDS = ["fault", "mode", "trial", "success", "mttr_s", "steps", "tokens", "cost",
          "stop_reason", "incident_id", "error"]


def _append(path: Path, row: dict) -> None:
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def run_eval(trials, modes, faults, out_dir, trial_fn) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.csv"
    for fault in faults:
        for n in range(1, trials + 1):
            for mode in modes:
                print(f"trial {fault} #{n} [{mode}]", flush=True)
                try:
                    r = trial_fn(fault, mode, n)
                    row = {"fault": r.fault, "mode": r.mode, "trial": r.trial, "success": r.success,
                           "mttr_s": round(r.mttr_s, 2), "steps": r.steps, "tokens": r.tokens,
                           "cost": round(r.cost, 5), "stop_reason": r.stop_reason,
                           "incident_id": r.incident_id, "error": ""}
                except Exception as e:
                    row = {"fault": fault, "mode": mode, "trial": n, "success": False, "mttr_s": "",
                           "steps": 0, "tokens": 0, "cost": 0, "stop_reason": "error",
                           "incident_id": "", "error": f"{type(e).__name__}: {e}"}
                _append(path, row)


def _as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true")


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["success"] = _as_bool(df["success"])
    df["mttr_s"] = pd.to_numeric(df["mttr_s"], errors="coerce")
    keys = ["mode", "fault"]
    base = df.groupby(keys).agg(trials=("success", "size"), success_rate=("success", "mean"),
                                steps=("steps", "mean"), tokens=("tokens", "mean"), cost=("cost", "mean"))
    mttr = df[df["success"]].groupby(keys)["mttr_s"].mean().rename("mttr_s")
    return base.join(mttr).reset_index()


def overall(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["success"] = _as_bool(df["success"])
    df["mttr_s"] = pd.to_numeric(df["mttr_s"], errors="coerce")
    g = df.groupby("mode")
    out = g.agg(trials=("success", "size"), success_rate=("success", "mean"),
                tokens=("tokens", "mean"), cost=("cost", "sum"))
    out["mttr_s"] = df[df["success"]].groupby("mode")["mttr_s"].mean()
    return out.reset_index()


def _table(frame: pd.DataFrame) -> str:
    cols = list(frame.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in frame.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                v = "-" if pd.isna(v) else (f"{v:.0%}" if c == "success_rate" else f"{v:.2f}")
            cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def to_markdown(summary: pd.DataFrame, overall_df: pd.DataFrame) -> str:
    return ("# AgentOps eval summary\n\n## Per fault\n\n" + _table(summary) +
            "\n\n## Overall\n\n" + _table(overall_df) + "\n\n"
            "MTTR averages successful trials only. Cost is the mean per trial (per-fault table) "
            "or the total (overall table).\n")


def _grouped_bar(summary: pd.DataFrame, column: str, ylabel: str, title: str, path: Path) -> None:
    pivot = summary.pivot(index="fault", columns="mode", values=column)
    ax = pivot.plot(kind="bar", figsize=(8, 4.5), rot=20)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xlabel("")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def write_report(csv_path, out_dir) -> None:
    out_dir = Path(out_dir)
    df = pd.read_csv(csv_path)
    summary = summarize(df)
    (out_dir / "summary.md").write_text(to_markdown(summary, overall(df)))
    _grouped_bar(summary, "success_rate", "success rate", "Success rate by fault: baseline vs agent",
                 out_dir / "success_rate.png")
    _grouped_bar(summary, "mttr_s", "seconds (successful trials)", "Mean time to recovery by fault",
                 out_dir / "mttr.png")


def main() -> None:
    p = argparse.ArgumentParser(description="Run the AgentOps evaluation")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--modes", nargs="+", default=["baseline", "agent"], choices=["baseline", "agent"])
    p.add_argument("--faults", nargs="+", default=list(chaos.FAULTS), choices=chaos.FAULTS)
    p.add_argument("--out", default=str(config.ROOT / "eval"))
    p.add_argument("--report-only", action="store_true")
    args = p.parse_args()
    out = Path(args.out)

    if not args.report_only:
        from agentops.checkpoint import CheckpointStore
        from agentops.db import connect
        from agentops.handlers import make_handler
        from agentops.trace import TraceStore
        from agentops.trial import run_trial

        conn = connect(config.DB_PATH)
        trace, cps = TraceStore(conn), CheckpointStore(conn)
        llm = None
        if "agent" in args.modes:
            from agentops.llm import OpenRouterClient
            llm = OpenRouterClient()
        handlers = {m: make_handler(m, llm=llm, trace=trace, checkpoints=cps) for m in args.modes}

        def trial_fn(fault, mode, n):
            return run_trial(fault, mode, n, handlers[mode], trace)

        run_eval(args.trials, args.modes, args.faults, out, trial_fn)
    write_report(out / "results.csv", out)
    print((out / "summary.md").read_text())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_eval_run.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add eval harness, summary table and plots" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Dashboard

**Files:**
- Create: `agentops/timeline.py`, `agentops/dashboard.py`
- Test: `tests/test_timeline.py`, `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `TraceStore`, `connect`, `config.DB_PATH`, `chaos.FAULTS/inject/reset`, `Executor.status`, `verify`, `eval_run.summarize`, eval output files.
- Produces: `timeline.format_event(ev: dict) -> dict | None` returning `{"kind": str, "label": str, "body": str}` (kind is one of `thought`, `action`, `result`, `denied`, `verify`, `end`, `error`); returns `None` for event types with nothing to show. Streamlit app with tabs `Live`, `Traces`, `Eval`. Env var `AGENTOPS_DASHBOARD_OFFLINE=1` skips all Docker and verifier calls (used by tests).

- [ ] **Step 1: Write the failing tests**

`tests/test_timeline.py`:

```python
from agentops.timeline import format_event


def ev(type, payload, step=1):
    return {"type": type, "payload": payload, "step": step, "ts": 0.0}


def test_llm_call_with_text_becomes_thought():
    out = format_event(ev("llm_call", {"text": "api looks down", "tool_calls": []}))
    assert out["kind"] == "thought" and "api looks down" in out["body"]


def test_llm_call_without_text_is_skipped():
    assert format_event(ev("llm_call", {"text": "", "tool_calls": [{"name": "x", "arguments": "{}"}]})) is None


def test_tool_call_and_result():
    call = format_event(ev("tool_call", {"name": "restart_service", "args": {"service": "api"}, "permission": "mutate"}))
    assert call["kind"] == "action" and "restart_service" in call["label"] and "api" in call["body"]
    res = format_event(ev("tool_result", {"name": "read_logs", "ok": False, "result": "denied: nope"}))
    assert res["kind"] == "result" and "denied: nope" in res["body"]


def test_other_kinds():
    assert format_event(ev("policy_denied", {"name": "run_diagnostic", "reason": "bad"}))["kind"] == "denied"
    assert format_event(ev("verify", {"healthy": True, "reason": ""}))["kind"] == "verify"
    end = format_event(ev("run_end", {"stop_reason": "finished", "success": True, "steps": 3, "summary": "fixed"}))
    assert end["kind"] == "end" and "fixed" in end["body"]
    assert format_event(ev("llm_error", {"error": "boom"}))["kind"] == "error"
    assert format_event(ev("mystery", {})) is None
```

`tests/test_dashboard.py`:

```python
from pathlib import Path

from streamlit.testing.v1 import AppTest

from agentops import config
from agentops.db import connect
from agentops.trace import TraceStore

APP = str(Path(__file__).resolve().parent.parent / "agentops" / "dashboard.py")


def test_renders_with_empty_database(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOPS_DASHBOARD_OFFLINE", "1")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "empty.db")
    monkeypatch.setattr(config, "ROOT", tmp_path)  # no eval/ files here
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Live", "Traces", "Eval"]


def test_renders_seeded_incident(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOPS_DASHBOARD_OFFLINE", "1")
    db = tmp_path / "seed.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    trace = TraceStore(connect(db))
    trace.open_incident("agent-crash-1", "api unreachable", "agent", "crash")
    trace.event("agent-crash-1", "tool_call", {"name": "restart_service", "args": {"service": "api"}},
                step=1, incident_id="agent-crash-1")
    trace.close_incident("agent-crash-1", "resolved", "finished", 2, 300, 0.01)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_timeline.py tests/test_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentops.timeline'`

- [ ] **Step 3: Write minimal implementation**

`agentops/timeline.py`:

```python
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
```

`agentops/dashboard.py`:

```python
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
        st.dataframe(table, use_container_width=True, hide_index=True)
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
        st.dataframe(summarize(pd.read_csv(results)), use_container_width=True, hide_index=True)
        for name in ("success_rate.png", "mttr.png"):
            if (out / name).exists():
                st.image(str(out / name))

if auto:
    time.sleep(3)
    st.rerun()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_timeline.py tests/test_dashboard.py -v`
Expected: 7 passed. If `AppTest` reports an exception, print `at.exception[0].message` to see the traceback.

- [ ] **Step 5: Manual look at the live dashboard**

Run: `streamlit run agentops/dashboard.py` with the stack up. Confirm the three tabs render, the Live tab shows service tiles, and an injected fault flips the banner to unhealthy.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add streamlit dashboard and trace timeline" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Final eval, screenshots and README

**Files:**
- Create: `README.md`
- Create (generated): `eval/results.csv`, `eval/summary.md`, `eval/success_rate.png`, `eval/mttr.png`, `docs/screenshots/*.png`

**Interfaces:**
- Consumes: everything above.
- Produces: the deliverables the mini project document needs: results table, plots, screenshots, run instructions.

- [ ] **Step 1: Run the full unit suite and the full Docker suite**

Run: `pytest -v` then `pytest -m docker -v`
Expected: all unit tests pass; all Docker tests pass. Fix any failure before the eval.

- [ ] **Step 2: Run the final evaluation**

Run: `rm -rf eval && python -m agentops.eval_run --trials 3 --modes baseline agent`
Expected: 30 trials (5 faults x 3 trials x 2 modes), about 25 to 40 minutes. The script prints `trial <fault> #<n> [<mode>]` per trial and ends by printing `eval/summary.md`. Expected pattern: baseline succeeds on `crash` and `dependency_down` only; agent succeeds on most or all faults. Report whatever numbers come out; do not tune the eval to flatter the agent. If a trial shows `error` in the CSV, run `python -m agentops.chaos reset` and rerun only the affected fault with `--faults <fault>` into a scratch `--out` directory, then merge rows by hand.

- [ ] **Step 3: Capture screenshots into `docs/screenshots/`**

Use these exact filenames so the project document can reference them.

| File | How to capture |
|---|---|
| `01-stack-healthy.png` | Dashboard Live tab, stack healthy (sample input context) |
| `02-fault-injected.png` | Live tab after `Inject fault` with `bad_config`, banner unhealthy |
| `03-agent-trace.png` | Traces tab, resolved `bad_config` incident expanded (reasoning, tool calls, results) |
| `04-policy-denied.png` | Traces tab on an incident that shows a denied action (or run `python -m agentops.run_one --fault disk_full` and screenshot the terminal) |
| `05-eval-table.png` | Eval tab table |
| `06-eval-plots.png` | Eval tab with both plots |
| `07-resume-demo.png` | Terminal: `python -m agentops.run_one --fault cpu_hog --crash-after-step 2`, then `python -m agentops.run_one --resume <incident id>` finishing with `success=True` (the incident id is the run id; find it in the Traces tab) |
| `08-baseline-vs-agent.png` | `eval/summary.md` rendered or the overall table |

- [ ] **Step 4: Write `README.md`**

```markdown
# AgentOps

Autonomous incident response for a Docker Compose microservice stack. An LLM agent on a custom agent runtime
diagnoses and repairs injected faults; a rule-based baseline gives the comparison.

## Setup

    python3 -m venv .venv && . .venv/bin/activate
    pip install -e '.[dev]'
    cp .env.example .env    # set OPENROUTER_API_KEY and AGENTOPS_MODEL
    docker compose up -d --build

## Run

    python -m agentops.chaos inject bad_config    # break the stack
    python -m agentops.watch --mode agent         # watcher + agent fixes it
    streamlit run agentops/dashboard.py           # live view, traces, eval
    python -m agentops.run_one --fault cpu_hog    # one incident end to end
    python -m agentops.eval_run --trials 3        # full evaluation
    python -m agentops.chaos reset                # restore a clean stack

## Tests

    pytest               # unit tests, no Docker
    pytest -m docker     # integration tests, needs Docker

## Design

See `docs/superpowers/specs/2026-09-30-agentops-design.md` and the plan in `docs/superpowers/plans/`.

## Results

See `eval/summary.md` and the plots in `eval/`.
```

- [ ] **Step 5: Commit results and docs**

```bash
git add -A
git commit -m "docs: add README, eval results and screenshots" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Hand off to the project document**

Map the repository to the 12-section Learning Block 1 template using section 9 of the spec. Screenshot filenames above cover section 8; `eval/summary.md` supplies the results for sections 9 and 10; the layer diagram in the spec supplies section 6.

---

## Self-Review

**Spec coverage**
- Target stack, health contract, quota, CPU probe, `DEBUG_SPIN`: Task 2.
- Chaos injector, 5 faults, reset: Task 6.
- Verifier: Task 3.
- LLM client: Task 9. Tool registry and permissions: Task 8. Executor and policy: Tasks 4 and 5.
- Agent loop, limits, finish gate, checkpoint and resume: Task 10 (with Task 7 stores).
- Trace store: Task 7. Watcher: Task 12. Baseline: Task 11.
- Incident Responder: Task 12. Eval harness and plots: Task 14. Dashboard: Task 15.
- Model selection risk: Task 13. Screenshots and doc mapping: Task 16.
- Stretch items are intentionally not in this plan.

**Placeholder scan:** no TBD or TODO items. Two conditional steps exist on purpose (calibration fallbacks in Task 6, model IDs in Task 13) and each states the exact alternative value or action.

**Type consistency:** `VerifyResult(healthy, reason, latency_s)`, `HandlerResult(steps, tokens, cost, stop_reason)`, `RunResult`, `TrialResult`, `Executor` method names, `TraceStore` methods and `make_handler` signatures match across tasks. The responder `run_id` equals the incident id in `make_handler`, which is what `run_one --resume <incident id>` relies on.

**Known deviation from the spec:** dependency_down is fixed by the baseline too (a stopped container is started); the spec was updated to match.
