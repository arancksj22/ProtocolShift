"""
gRPC Suite — PostgreSQL Service
=================================
Port      : 50051  (gRPC)
Port+1    : 50061  (Prometheus metrics sidecar HTTP)
Driver    : asyncpg (async, no ORM)
Model     : BenchmarkRecord { id: int, payload: str }
            ← IDENTICAL to REST suite model / protobuf definition
Proto     : services/protobufs/benchmark.proto
Stubs     : generated via  bash grpc-suite/generate_stubs.sh

ID strategy: PostgreSQL SERIAL primary key (auto-increment)

RPCs implemented (mirror of REST endpoints):
  Create   → POST   /records
  Read     → GET    /records/{id}
  ReadAll  → GET    /records
  Update   → PUT    /records/{id}
  Delete   → DELETE /records/{id}
"""

import asyncio
import logging
import os

import asyncpg
import grpc
from dotenv import load_dotenv
from grpc_reflection.v1alpha import reflection
from prometheus_client import Counter, Histogram, start_http_server

import benchmark_pb2
import benchmark_pb2_grpc

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [grpc-postgres] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────
POSTGRES_DSN: str = os.getenv(
    "POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/benchmarkdb"
)
GRPC_PORT: int = int(os.getenv("GRPC_POSTGRES_PORT", "50051"))
METRICS_PORT: int = int(os.getenv("GRPC_POSTGRES_METRICS_PORT", "50061"))

# ──────────────────────────────────────────────
# Prometheus metrics
# ──────────────────────────────────────────────
RPC_REQUESTS = Counter(
    "grpc_postgres_requests_total",
    "Total gRPC requests to PostgreSQL service",
    ["method"],
)
RPC_LATENCY = Histogram(
    "grpc_postgres_latency_seconds",
    "gRPC request latency for PostgreSQL service",
    ["method"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)
RPC_ERRORS = Counter(
    "grpc_postgres_errors_total",
    "Total gRPC errors for PostgreSQL service",
    ["method"],
)

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _row_to_proto(row: asyncpg.Record) -> benchmark_pb2.BenchmarkRecord:
    return benchmark_pb2.BenchmarkRecord(id=row["id"], payload=row["payload"])


async def _ensure_table(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS benchmark_records (
                id      SERIAL  PRIMARY KEY,
                payload TEXT    NOT NULL
            );
            """
        )


# ──────────────────────────────────────────────
# gRPC Servicer
# ──────────────────────────────────────────────
class BenchmarkServicer(benchmark_pb2_grpc.BenchmarkServiceServicer):
    """
    Implements the gRPC BenchmarkService backed by PostgreSQL.
    All method signatures intentionally mirror the REST suite's endpoints.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ── Create ──────────────────────────────────
    async def Create(
        self,
        request: benchmark_pb2.CreateRecordRequest,
        context: grpc.aio.ServicerContext,
    ) -> benchmark_pb2.BenchmarkRecord:
        method = "Create"
        RPC_REQUESTS.labels(method=method).inc()
        with RPC_LATENCY.labels(method=method).time():
            try:
                async with self._pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "INSERT INTO benchmark_records (payload) VALUES ($1) RETURNING *",
                        request.payload,
                    )
                return _row_to_proto(row)
            except Exception as exc:
                RPC_ERRORS.labels(method=method).inc()
                log.exception("Create failed")
                await context.abort(grpc.StatusCode.INTERNAL, str(exc))

    # ── Read ────────────────────────────────────
    async def Read(
        self,
        request: benchmark_pb2.GetRecordRequest,
        context: grpc.aio.ServicerContext,
    ) -> benchmark_pb2.BenchmarkRecord:
        method = "Read"
        RPC_REQUESTS.labels(method=method).inc()
        with RPC_LATENCY.labels(method=method).time():
            try:
                async with self._pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT * FROM benchmark_records WHERE id = $1", request.id
                    )
                if row is None:
                    await context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Record {request.id} not found.",
                    )
                return _row_to_proto(row)
            except grpc.aio.AbortError:
                raise
            except Exception as exc:
                RPC_ERRORS.labels(method=method).inc()
                log.exception("Read failed")
                await context.abort(grpc.StatusCode.INTERNAL, str(exc))

    # ── ReadAll ─────────────────────────────────
    async def ReadAll(
        self,
        request: benchmark_pb2.ListRecordsRequest,
        context: grpc.aio.ServicerContext,
    ) -> benchmark_pb2.ListRecordsResponse:
        method = "ReadAll"
        RPC_REQUESTS.labels(method=method).inc()
        limit = request.limit if request.limit > 0 else 100
        offset = request.offset
        with RPC_LATENCY.labels(method=method).time():
            try:
                async with self._pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT * FROM benchmark_records ORDER BY id ASC LIMIT $1 OFFSET $2",
                        limit,
                        offset,
                    )
                    total: int = await conn.fetchval(
                        "SELECT COUNT(*) FROM benchmark_records"
                    )
                return benchmark_pb2.ListRecordsResponse(
                    records=[_row_to_proto(r) for r in rows],
                    total=total,
                )
            except Exception as exc:
                RPC_ERRORS.labels(method=method).inc()
                log.exception("ReadAll failed")
                await context.abort(grpc.StatusCode.INTERNAL, str(exc))

    # ── Update ──────────────────────────────────
    async def Update(
        self,
        request: benchmark_pb2.UpdateRecordRequest,
        context: grpc.aio.ServicerContext,
    ) -> benchmark_pb2.BenchmarkRecord:
        method = "Update"
        RPC_REQUESTS.labels(method=method).inc()
        with RPC_LATENCY.labels(method=method).time():
            try:
                async with self._pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "UPDATE benchmark_records SET payload = $2 WHERE id = $1 RETURNING *",
                        request.id,
                        request.payload,
                    )
                if row is None:
                    await context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Record {request.id} not found.",
                    )
                return _row_to_proto(row)
            except grpc.aio.AbortError:
                raise
            except Exception as exc:
                RPC_ERRORS.labels(method=method).inc()
                log.exception("Update failed")
                await context.abort(grpc.StatusCode.INTERNAL, str(exc))

    # ── Delete ──────────────────────────────────
    async def Delete(
        self,
        request: benchmark_pb2.DeleteRecordRequest,
        context: grpc.aio.ServicerContext,
    ) -> benchmark_pb2.DeleteRecordResponse:
        method = "Delete"
        RPC_REQUESTS.labels(method=method).inc()
        with RPC_LATENCY.labels(method=method).time():
            try:
                async with self._pool.acquire() as conn:
                    result = await conn.execute(
                        "DELETE FROM benchmark_records WHERE id = $1", request.id
                    )
                if result == "DELETE 0":
                    await context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Record {request.id} not found.",
                    )
                return benchmark_pb2.DeleteRecordResponse(success=True, id=request.id)
            except grpc.aio.AbortError:
                raise
            except Exception as exc:
                RPC_ERRORS.labels(method=method).inc()
                log.exception("Delete failed")
                await context.abort(grpc.StatusCode.INTERNAL, str(exc))


# ──────────────────────────────────────────────
# Server bootstrap
# ──────────────────────────────────────────────
async def serve() -> None:
    log.info("Connecting to PostgreSQL...")
    pool = await asyncpg.create_pool(
        dsn=POSTGRES_DSN, min_size=2, max_size=10, command_timeout=60
    )
    await _ensure_table(pool)

    start_http_server(METRICS_PORT)
    log.info(f"Prometheus metrics on :{METRICS_PORT}/metrics")

    server = grpc.aio.server()
    benchmark_pb2_grpc.add_BenchmarkServiceServicer_to_server(
        BenchmarkServicer(pool), server
    )

    service_names = (
        benchmark_pb2.DESCRIPTOR.services_by_name["BenchmarkService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    await server.start()
    log.info(f"gRPC PostgreSQL service listening on :{GRPC_PORT}")

    try:
        await server.wait_for_termination()
    finally:
        await pool.close()
        log.info("Pool closed, server shut down.")


if __name__ == "__main__":
    asyncio.run(serve())
