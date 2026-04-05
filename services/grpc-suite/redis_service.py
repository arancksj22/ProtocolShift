"""
gRPC Suite — Redis Service
============================
Port      : 50053  (gRPC)
Port+1    : 50063  (Prometheus metrics sidecar HTTP)
Driver    : redis.asyncio
Model     : BenchmarkRecord  ← IDENTICAL to REST suite model / protobuf definition
Proto     : services/protobufs/benchmark.proto
Stubs     : generated via  bash grpc-suite/generate_stubs.sh

Storage strategy (mirrors REST redis_service.py exactly):
  - Each record: Redis hash at key  benchmark:<uuid>
  - Sorted set   benchmark:index  (score=epoch_ms) for O(log N) pagination

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
import uuid
from datetime import datetime, timezone

import grpc
import redis.asyncio as aioredis
from dotenv import load_dotenv
from google.protobuf.timestamp_pb2 import Timestamp
from grpc_reflection.v1alpha import reflection
from prometheus_client import Counter, Histogram, start_http_server

import benchmark_pb2
import benchmark_pb2_grpc

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [grpc-redis] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RECORD_PREFIX: str = "benchmark:"
INDEX_KEY: str = "benchmark:index"
GRPC_PORT: int = int(os.getenv("GRPC_REDIS_PORT", "50053"))
METRICS_PORT: int = int(os.getenv("GRPC_REDIS_METRICS_PORT", "50063"))

# ──────────────────────────────────────────────
# Prometheus metrics
# ──────────────────────────────────────────────
RPC_REQUESTS = Counter(
    "grpc_redis_requests_total",
    "Total gRPC requests to Redis service",
    ["method"],
)
RPC_LATENCY = Histogram(
    "grpc_redis_latency_seconds",
    "gRPC request latency for Redis service",
    ["method"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)
RPC_ERRORS = Counter(
    "grpc_redis_errors_total",
    "Total gRPC errors for Redis service",
    ["method"],
)

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _record_key(record_id: str) -> str:
    return f"{RECORD_PREFIX}{record_id}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_epoch_ms(dt: datetime) -> float:
    return dt.timestamp() * 1000


def _dt_to_proto_ts(dt: datetime) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(dt)
    return ts


def _hash_to_proto(data: dict) -> benchmark_pb2.BenchmarkRecord:
    created_at = datetime.fromisoformat(data["created_at"])
    updated_at = datetime.fromisoformat(data["updated_at"])
    return benchmark_pb2.BenchmarkRecord(
        id=data["id"],
        name=data["name"],
        value=float(data["value"]),
        payload=data["payload"],
        created_at=_dt_to_proto_ts(created_at),
        updated_at=_dt_to_proto_ts(updated_at),
    )


# ──────────────────────────────────────────────
# gRPC Servicer
# ──────────────────────────────────────────────
class BenchmarkServicer(benchmark_pb2_grpc.BenchmarkServiceServicer):
    """
    Implements the gRPC BenchmarkService backed by Redis.
    Storage approach mirrors REST redis_service.py exactly for
    apples-to-apples benchmarking.
    """

    def __init__(self, r: aioredis.Redis) -> None:
        self._r = r

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
                now = _now()
                record_id = str(uuid.uuid4())
                data = {
                    "id": record_id,
                    "name": request.name,
                    "value": str(request.value),
                    "payload": request.payload,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
                async with self._r.pipeline(transaction=True) as pipe:
                    pipe.hset(_record_key(record_id), mapping=data)
                    pipe.zadd(INDEX_KEY, {record_id: _to_epoch_ms(now)})
                    await pipe.execute()
                return _hash_to_proto(data)
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
                data = await self._r.hgetall(_record_key(request.id))
                if not data:
                    await context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Record '{request.id}' not found.",
                    )
                return _hash_to_proto(data)
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
                ids = await self._r.zrevrange(INDEX_KEY, offset, offset + limit - 1)
                total: int = await self._r.zcard(INDEX_KEY)
                records = []
                for record_id in ids:
                    data = await self._r.hgetall(_record_key(record_id))
                    if data:
                        records.append(_hash_to_proto(data))
                return benchmark_pb2.ListRecordsResponse(records=records, total=total)
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
                key = _record_key(request.id)
                exists = await self._r.exists(key)
                if not exists:
                    await context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Record '{request.id}' not found.",
                    )
                now = _now()
                update_data = {
                    "name": request.name,
                    "value": str(request.value),
                    "payload": request.payload,
                    "updated_at": now.isoformat(),
                }
                await self._r.hset(key, mapping=update_data)
                data = await self._r.hgetall(key)
                return _hash_to_proto(data)
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
                key = _record_key(request.id)
                async with self._r.pipeline(transaction=True) as pipe:
                    pipe.exists(key)
                    pipe.delete(key)
                    pipe.zrem(INDEX_KEY, request.id)
                    results = await pipe.execute()
                if results[0] == 0:
                    await context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Record '{request.id}' not found.",
                    )
                return benchmark_pb2.DeleteRecordResponse(
                    success=True, id=request.id
                )
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
    log.info("Connecting to Redis...")
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    await r.ping()

    # Prometheus sidecar
    start_http_server(METRICS_PORT)
    log.info(f"Prometheus metrics on :{METRICS_PORT}/metrics")

    server = grpc.aio.server()
    benchmark_pb2_grpc.add_BenchmarkServiceServicer_to_server(
        BenchmarkServicer(r), server
    )

    service_names = (
        benchmark_pb2.DESCRIPTOR.services_by_name["BenchmarkService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    await server.start()
    log.info(f"gRPC Redis service listening on :{GRPC_PORT}")

    try:
        await server.wait_for_termination()
    finally:
        await r.aclose()
        log.info("Redis client closed, server shut down.")


if __name__ == "__main__":
    asyncio.run(serve())
