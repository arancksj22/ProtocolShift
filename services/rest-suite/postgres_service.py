"""
REST Suite — PostgreSQL Service
================================
Port  : 8001
Driver: asyncpg (async, no ORM so we see raw SQL latency)
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
import asyncio
from datetime import datetime, timezone
from typing import Optional, List

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from prometheus_fastapi_instrumentator import Instrumentator

load_dotenv()

# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────
POSTGRES_DSN: str = os.getenv(
    "POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/benchmarkdb"
)

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
    title="ProtocolShift — REST/PostgreSQL Service",
    description=(
        "Benchmarking REST (HTTP/1.1 + JSON) against PostgreSQL. "
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
    app.state.pool = await asyncpg.create_pool(
        dsn=POSTGRES_DSN,
        min_size=2,
        max_size=10,
        command_timeout=60,
    )
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS benchmark_records (
                id          TEXT        PRIMARY KEY,
                name        TEXT        NOT NULL,
                value       DOUBLE PRECISION NOT NULL,
                payload     TEXT        NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )


@app.on_event("shutdown")
async def shutdown() -> None:
    await app.state.pool.close()


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _row_to_record(row: asyncpg.Record) -> BenchmarkRecord:
    return BenchmarkRecord(
        id=row["id"],
        name=row["name"],
        value=row["value"],
        payload=row["payload"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────
@app.get("/healthz", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "service": "rest-postgres", "backend": "postgresql"}


@app.post("/records", response_model=BenchmarkRecord, status_code=201, tags=["records"])
async def create_record(body: BenchmarkRecordCreate) -> BenchmarkRecord:
    """Create a new BenchmarkRecord."""
    record_id = str(uuid.uuid4())
    now = _now()
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO benchmark_records (id, name, value, payload, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            record_id,
            body.name,
            body.value,
            body.payload,
            now,
            now,
        )
    return _row_to_record(row)


@app.get("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def read_record(record_id: str) -> BenchmarkRecord:
    """Fetch a single BenchmarkRecord by UUID."""
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM benchmark_records WHERE id = $1", record_id
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Record '{record_id}' not found.")
    return _row_to_record(row)


@app.get("/records", response_model=BenchmarkRecordList, tags=["records"])
async def read_all_records(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> BenchmarkRecordList:
    """List BenchmarkRecords with pagination."""
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM benchmark_records ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
        total: int = await conn.fetchval("SELECT COUNT(*) FROM benchmark_records")
    return BenchmarkRecordList(
        records=[_row_to_record(r) for r in rows],
        total=total,
    )


@app.put("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def update_record(record_id: str, body: BenchmarkRecordUpdate) -> BenchmarkRecord:
    """Update an existing BenchmarkRecord."""
    now = _now()
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE benchmark_records
               SET name = $2, value = $3, payload = $4, updated_at = $5
             WHERE id = $1
            RETURNING *
            """,
            record_id,
            body.name,
            body.value,
            body.payload,
            now,
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Record '{record_id}' not found.")
    return _row_to_record(row)


@app.delete("/records/{record_id}", tags=["records"])
async def delete_record(record_id: str) -> dict:
    """Delete a BenchmarkRecord by UUID."""
    async with app.state.pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM benchmark_records WHERE id = $1", record_id
        )
    # asyncpg returns "DELETE N" string
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail=f"Record '{record_id}' not found.")
    return {"success": True, "id": record_id}


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("postgres_service:app", host="0.0.0.0", port=8001, reload=False)
