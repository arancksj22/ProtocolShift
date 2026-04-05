"""
REST Suite — Redis Service
============================
Port  : 8003
Driver: redis.asyncio (aioredis-compatible async client)
Model : BenchmarkRecord  ← identical to gRPC suite model
Storage strategy:
  - Each record is stored as a Redis hash at key  benchmark:<uuid>
  - A sorted set  benchmark:index  (score=timestamp_ms) tracks all IDs
    so we can do O(log N) paginated listing without scanning all keys.

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
import json
import uuid
from datetime import datetime, timezone
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
INDEX_KEY: str = "benchmark:index"  # sorted set: score = epoch_ms, member = id

# ──────────────────────────────────────────────
# Shared Data Model
# (Pydantic mirror of the protobuf BenchmarkRecord)
# ──────────────────────────────────────────────
class BenchmarkRecordBase(BaseModel):
    name: str = Field(..., description="Human-readable label for this record")
    value: float = Field(..., description="Numeric measurement value")
    payload: str = Field(
        ...,
        description="Variable-length blob that stresses serialization overhead",
    )


class BenchmarkRecordCreate(BenchmarkRecordBase):
    pass


class BenchmarkRecordUpdate(BenchmarkRecordBase):
    pass


class BenchmarkRecord(BenchmarkRecordBase):
    id: str
    created_at: datetime
    updated_at: datetime

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
def _record_key(record_id: str) -> str:
    return f"{RECORD_PREFIX}{record_id}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_epoch_ms(dt: datetime) -> float:
    return dt.timestamp() * 1000


def _hash_to_record(data: dict) -> BenchmarkRecord:
    return BenchmarkRecord(
        id=data["id"],
        name=data["name"],
        value=float(data["value"]),
        payload=data["payload"],
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────
@app.get("/healthz", tags=["ops"])
async def health() -> dict:
    await app.state.redis.ping()
    return {"status": "ok", "service": "rest-redis", "backend": "redis"}


@app.post("/records", response_model=BenchmarkRecord, status_code=201, tags=["records"])
async def create_record(body: BenchmarkRecordCreate) -> BenchmarkRecord:
    """Create a new BenchmarkRecord."""
    now = _now()
    record_id = str(uuid.uuid4())
    data = {
        "id": record_id,
        "name": body.name,
        "value": str(body.value),
        "payload": body.payload,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    r = app.state.redis
    async with r.pipeline(transaction=True) as pipe:
        pipe.hset(_record_key(record_id), mapping=data)
        pipe.zadd(INDEX_KEY, {record_id: _to_epoch_ms(now)})
        await pipe.execute()
    return _hash_to_record(data)


@app.get("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def read_record(record_id: str) -> BenchmarkRecord:
    """Fetch a single BenchmarkRecord by UUID."""
    data = await app.state.redis.hgetall(_record_key(record_id))
    if not data:
        raise HTTPException(status_code=404, detail=f"Record '{record_id}' not found.")
    return _hash_to_record(data)


@app.get("/records", response_model=BenchmarkRecordList, tags=["records"])
async def read_all_records(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> BenchmarkRecordList:
    """List BenchmarkRecords with pagination (ordered by creation time desc)."""
    r = app.state.redis
    # ZREVRANGE gives IDs sorted by score descending (newest first)
    ids = await r.zrevrange(INDEX_KEY, offset, offset + limit - 1)
    total: int = await r.zcard(INDEX_KEY)

    records: List[BenchmarkRecord] = []
    for record_id in ids:
        data = await r.hgetall(_record_key(record_id))
        if data:
            records.append(_hash_to_record(data))

    return BenchmarkRecordList(records=records, total=total)


@app.put("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def update_record(record_id: str, body: BenchmarkRecordUpdate) -> BenchmarkRecord:
    """Update an existing BenchmarkRecord."""
    r = app.state.redis
    key = _record_key(record_id)
    exists = await r.exists(key)
    if not exists:
        raise HTTPException(status_code=404, detail=f"Record '{record_id}' not found.")
    now = _now()
    update_data = {
        "name": body.name,
        "value": str(body.value),
        "payload": body.payload,
        "updated_at": now.isoformat(),
    }
    await r.hset(key, mapping=update_data)
    data = await r.hgetall(key)
    return _hash_to_record(data)


@app.delete("/records/{record_id}", tags=["records"])
async def delete_record(record_id: str) -> dict:
    """Delete a BenchmarkRecord by UUID."""
    r = app.state.redis
    key = _record_key(record_id)
    async with r.pipeline(transaction=True) as pipe:
        pipe.exists(key)
        pipe.delete(key)
        pipe.zrem(INDEX_KEY, record_id)
        results = await pipe.execute()
    if results[0] == 0:
        raise HTTPException(status_code=404, detail=f"Record '{record_id}' not found.")
    return {"success": True, "id": record_id}


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("redis_service:app", host="0.0.0.0", port=8003, reload=False)
