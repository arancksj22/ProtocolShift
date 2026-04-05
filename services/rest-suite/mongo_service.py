"""
REST Suite — MongoDB Service
==============================
Port  : 8002
Driver: motor (async MongoDB driver)
Model : BenchmarkRecord { id: int, payload: str }
        ← identical to gRPC suite model

ID strategy: an atomic counter document in the same collection
             (findOneAndUpdate on a dedicated counters collection)
             so the integer id behaves like an auto-increment.

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

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from prometheus_fastapi_instrumentator import Instrumentator

load_dotenv()

# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB: str = os.getenv("MONGO_DB", "benchmarkdb")
MONGO_COLLECTION: str = "benchmark_records"
MONGO_COUNTERS: str = "benchmark_counters"

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
    title="ProtocolShift — REST/MongoDB Service",
    description=(
        "Benchmarking REST (HTTP/1.1 + JSON) against MongoDB. "
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
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[MONGO_DB]
    app.state.col = db[MONGO_COLLECTION]
    app.state.counters = db[MONGO_COUNTERS]
    # Unique index on our integer id field
    await app.state.col.create_index("id", unique=True)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
async def _next_id() -> int:
    """Atomically increment and return the next integer id."""
    result = await app.state.counters.find_one_and_update(
        {"_id": "benchmark_records"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result["seq"]


def _doc_to_record(doc: dict) -> BenchmarkRecord:
    return BenchmarkRecord(id=doc["id"], payload=doc["payload"])


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────
@app.get("/healthz", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "service": "rest-mongo", "backend": "mongodb"}


@app.post("/records", response_model=BenchmarkRecord, status_code=201, tags=["records"])
async def create_record(body: BenchmarkRecordCreate) -> BenchmarkRecord:
    """Create a new BenchmarkRecord. id is auto-incremented via a counter document."""
    new_id = await _next_id()
    doc = {"id": new_id, "payload": body.payload}
    await app.state.col.insert_one(doc)
    return _doc_to_record(doc)


@app.get("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def read_record(record_id: int) -> BenchmarkRecord:
    """Fetch a single BenchmarkRecord by integer id."""
    doc = await app.state.col.find_one({"id": record_id}, {"_id": 0})
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    return _doc_to_record(doc)


@app.get("/records", response_model=BenchmarkRecordList, tags=["records"])
async def read_all_records(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> BenchmarkRecordList:
    """List BenchmarkRecords with pagination."""
    col = app.state.col
    cursor = col.find({}, {"_id": 0}).sort("id", 1).skip(offset).limit(limit)
    docs = await cursor.to_list(length=limit)
    total: int = await col.count_documents({})
    return BenchmarkRecordList(records=[_doc_to_record(d) for d in docs], total=total)


@app.put("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def update_record(record_id: int, body: BenchmarkRecordUpdate) -> BenchmarkRecord:
    """Update the payload of an existing BenchmarkRecord."""
    result = await app.state.col.find_one_and_update(
        {"id": record_id},
        {"$set": {"payload": body.payload}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    return _doc_to_record(result)


@app.delete("/records/{record_id}", tags=["records"])
async def delete_record(record_id: int) -> dict:
    """Delete a BenchmarkRecord by integer id."""
    result = await app.state.col.delete_one({"id": record_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    return {"success": True, "id": record_id}


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("mongo_service:app", host="0.0.0.0", port=8002, reload=False)
