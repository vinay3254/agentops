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
