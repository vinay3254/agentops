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
