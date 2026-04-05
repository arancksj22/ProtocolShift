"""
REST Suite — PostgreSQL Service
================================
Port  : 8001
Driver: asyncpg (async, no ORM so we see raw SQL latency)
Model : BenchmarkRecord { id: int, payload: str }
        ← identical to gRPC suite model

ID strategy: PostgreSQL SERIAL primary key (auto-increment, no UUID overhead)

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
                id      SERIAL  PRIMARY KEY,
                payload TEXT    NOT NULL
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
    return BenchmarkRecord(id=row["id"], payload=row["payload"])


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────
@app.get("/healthz", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "service": "rest-postgres", "backend": "postgresql"}


@app.post("/records", response_model=BenchmarkRecord, status_code=201, tags=["records"])
async def create_record(body: BenchmarkRecordCreate) -> BenchmarkRecord:
    """Create a new BenchmarkRecord. id is assigned by PostgreSQL SERIAL."""
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO benchmark_records (payload) VALUES ($1) RETURNING *",
            body.payload,
        )
    return _row_to_record(row)


@app.get("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def read_record(record_id: int) -> BenchmarkRecord:
    """Fetch a single BenchmarkRecord by integer id."""
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM benchmark_records WHERE id = $1", record_id
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    return _row_to_record(row)


@app.get("/records", response_model=BenchmarkRecordList, tags=["records"])
async def read_all_records(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> BenchmarkRecordList:
    """List BenchmarkRecords with pagination."""
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM benchmark_records ORDER BY id ASC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
        total: int = await conn.fetchval("SELECT COUNT(*) FROM benchmark_records")
    return BenchmarkRecordList(records=[_row_to_record(r) for r in rows], total=total)


@app.put("/records/{record_id}", response_model=BenchmarkRecord, tags=["records"])
async def update_record(record_id: int, body: BenchmarkRecordUpdate) -> BenchmarkRecord:
    """Update the payload of an existing BenchmarkRecord."""
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE benchmark_records SET payload = $2 WHERE id = $1 RETURNING *",
            record_id,
            body.payload,
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    return _row_to_record(row)


@app.delete("/records/{record_id}", tags=["records"])
async def delete_record(record_id: int) -> dict:
    """Delete a BenchmarkRecord by integer id."""
    async with app.state.pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM benchmark_records WHERE id = $1", record_id
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    return {"success": True, "id": record_id}


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("postgres_service:app", host="0.0.0.0", port=8001, reload=False)
