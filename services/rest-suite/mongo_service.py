"""
REST Suite — MongoDB Service
==============================
Port  : 8002
Driver: motor (async MongoDB driver)
Model : BenchmarkRecord  ← identical to gRPC suite model
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
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorClient
from prometheus_fastapi_instrumentator import Instrumentator

load_dotenv()

# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB: str = os.getenv("MONGO_DB", "benchmarkdb")
MONGO_COLLECTION: str = "benchmark_records"

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
    app.state.collection = db[MONGO_COLLECTION]
    # Ensure unique index on 'id' for O(1) lookup by UUID
    await app.state.collection.create_index("id", unique=True)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _doc_to_record(doc: dict) -> BenchmarkRecord:
    return BenchmarkRecord(
        id=doc["id"],
        name=doc["name"],
        value=doc["value"],
        payload=doc["payload"],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────
@app.get("/healthz", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "service": "rest-mongo", "backend": "mongodb"}


@app.post("/records", response_model=BenchmarkRecord, status_code=201, tags=["records"])
async def create_record(body: BenchmarkRecordCreate) -> BenchmarkRecord:
    """Create a new BenchmarkRecord."""
    now = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "value": body.value,
        "payload": body.payload,
        "created_at": now,
        "updated_at": now,
    }
    await app.state.collection.insert_one(doc)
    return _doc_to_record(doc)


@app.get("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def read_record(record_id: str) -> BenchmarkRecord:
    """Fetch a single BenchmarkRecord by UUID."""
    doc = await app.state.collection.find_one({"id": record_id}, {"_id": 0})
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Record '{record_id}' not found.")
    return _doc_to_record(doc)


@app.get("/records", response_model=BenchmarkRecordList, tags=["records"])
async def read_all_records(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> BenchmarkRecordList:
    """List BenchmarkRecords with pagination."""
    col = app.state.collection
    cursor = col.find({}, {"_id": 0}).sort("created_at", -1).skip(offset).limit(limit)
    docs = await cursor.to_list(length=limit)
    total: int = await col.count_documents({})
    return BenchmarkRecordList(
        records=[_doc_to_record(d) for d in docs],
        total=total,
    )


@app.put("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def update_record(record_id: str, body: BenchmarkRecordUpdate) -> BenchmarkRecord:
    """Update an existing BenchmarkRecord."""
    now = _now()
    result = await app.state.collection.find_one_and_update(
        {"id": record_id},
        {
            "$set": {
                "name": body.name,
                "value": body.value,
                "payload": body.payload,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=True,  # motor uses True (pymongo.ReturnDocument.AFTER)
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Record '{record_id}' not found.")
    return _doc_to_record(result)


@app.delete("/records/{record_id}", tags=["records"])
async def delete_record(record_id: str) -> dict:
    """Delete a BenchmarkRecord by UUID."""
    result = await app.state.collection.delete_one({"id": record_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail=f"Record '{record_id}' not found.")
    return {"success": True, "id": record_id}


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("mongo_service:app", host="0.0.0.0", port=8002, reload=False)
