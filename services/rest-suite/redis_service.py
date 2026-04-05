"""
REST Suite — Redis Service
============================
Port  : 8003
Driver: redis.asyncio
Model : BenchmarkRecord { id: int, payload: str }
        ← identical to gRPC suite model

ID strategy : INCR on key  benchmark:counter  (atomic, sequential int)
Storage     : each record is a Redis hash at key  benchmark:<id>
Listing     : a sorted set  benchmark:index  (score=id) for O(log N) pagination

Endpoints:
  POST   /records          → Create
  GET    /records/{id}     → Read
  GET    /records          → ReadAll
  PUT    /records/{id}     → Update
  DELETE /records/{id}     → Delete
  GET    /metrics          → Prometheus scrape target
  GET    /healthz          → Health-check
"""

import os
from typing import List

import redis.asyncio as aioredis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from prometheus_fastapi_instrumentator import Instrumentator

load_dotenv()

# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RECORD_PREFIX: str = "benchmark:"
INDEX_KEY: str = "benchmark:index"    # sorted set: score=id, member=id
COUNTER_KEY: str = "benchmark:counter"

# ──────────────────────────────────────────────
# Shared Data Model
# (Pydantic mirror of the protobuf BenchmarkRecord)
# ──────────────────────────────────────────────
class BenchmarkRecordCreate(BaseModel):
    payload: str = Field(..., description="Arbitrary string payload to benchmark serialisation")


class BenchmarkRecordUpdate(BaseModel):
    payload: str = Field(..., description="Replacement payload value")


class BenchmarkRecord(BaseModel):
    id: int
    payload: str

    model_config = {"from_attributes": True}


class BenchmarkRecordList(BaseModel):
    records: List[BenchmarkRecord]
    total: int


# ──────────────────────────────────────────────
# App bootstrap
# ──────────────────────────────────────────────
app = FastAPI(
    title="ProtocolShift — REST/Redis Service",
    description=(
        "Benchmarking REST (HTTP/1.1 + JSON) against Redis in-memory store. "
        "Part of the ProtocolShift distributed computing testbed."
    ),
    version="1.0.0",
)

Instrumentator().instrument(app).expose(app)


# ──────────────────────────────────────────────
# DB lifecycle
# ──────────────────────────────────────────────
@app.on_event("startup")
async def startup() -> None:
    app.state.redis = aioredis.from_url(REDIS_URL, decode_responses=True)


@app.on_event("shutdown")
async def shutdown() -> None:
    await app.state.redis.aclose()


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _record_key(record_id: int) -> str:
    return f"{RECORD_PREFIX}{record_id}"


def _hash_to_record(data: dict) -> BenchmarkRecord:
    return BenchmarkRecord(id=int(data["id"]), payload=data["payload"])


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────
@app.get("/healthz", tags=["ops"])
async def health() -> dict:
    await app.state.redis.ping()
    return {"status": "ok", "service": "rest-redis", "backend": "redis"}


@app.post("/records", response_model=BenchmarkRecord, status_code=201, tags=["records"])
async def create_record(body: BenchmarkRecordCreate) -> BenchmarkRecord:
    """Create a new BenchmarkRecord. id is assigned by atomic INCR."""
    r = app.state.redis
    new_id: int = await r.incr(COUNTER_KEY)
    data = {"id": str(new_id), "payload": body.payload}
    async with r.pipeline(transaction=True) as pipe:
        pipe.hset(_record_key(new_id), mapping=data)
        pipe.zadd(INDEX_KEY, {str(new_id): new_id})
        await pipe.execute()
    return _hash_to_record(data)


@app.get("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def read_record(record_id: int) -> BenchmarkRecord:
    """Fetch a single BenchmarkRecord by integer id."""
    data = await app.state.redis.hgetall(_record_key(record_id))
    if not data:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    return _hash_to_record(data)


@app.get("/records", response_model=BenchmarkRecordList, tags=["records"])
async def read_all_records(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> BenchmarkRecordList:
    """List BenchmarkRecords with pagination (ordered by id asc)."""
    r = app.state.redis
    # ZRANGE with scores gives ids in ascending order
    ids = await r.zrange(INDEX_KEY, offset, offset + limit - 1)
    total: int = await r.zcard(INDEX_KEY)
    records: List[BenchmarkRecord] = []
    for record_id in ids:
        data = await r.hgetall(_record_key(int(record_id)))
        if data:
            records.append(_hash_to_record(data))
    return BenchmarkRecordList(records=records, total=total)


@app.put("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def update_record(record_id: int, body: BenchmarkRecordUpdate) -> BenchmarkRecord:
    """Update the payload of an existing BenchmarkRecord."""
    r = app.state.redis
    key = _record_key(record_id)
    if not await r.exists(key):
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    await r.hset(key, "payload", body.payload)
    data = await r.hgetall(key)
    return _hash_to_record(data)


@app.delete("/records/{record_id}", tags=["records"])
async def delete_record(record_id: int) -> dict:
    """Delete a BenchmarkRecord by integer id."""
    r = app.state.redis
    key = _record_key(record_id)
    async with r.pipeline(transaction=True) as pipe:
        pipe.exists(key)
        pipe.delete(key)
        pipe.zrem(INDEX_KEY, str(record_id))
        results = await pipe.execute()
    if results[0] == 0:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    return {"success": True, "id": record_id}


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("redis_service:app", host="0.0.0.0", port=8003, reload=False)
