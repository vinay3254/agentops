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
