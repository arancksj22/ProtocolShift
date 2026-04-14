"""
gRPC Suite — Redis Service
============================
Port      : 50053  (gRPC)
Port+1    : 50063  (Prometheus metrics sidecar HTTP)
Driver    : redis.asyncio
Model     : BenchmarkRecord { id: int, payload: str }
            ← IDENTICAL to REST suite model / protobuf definition
Proto     : services/protobufs/benchmark.proto
Stubs     : generated via  bash grpc-suite/generate_stubs.sh

Storage strategy (mirrors REST redis_service.py exactly):
  - ID        : atomic INCR on key  benchmark:counter
  - Each record: Redis hash at key  benchmark:<id>
  - Sorted set : benchmark:index  (score=id) for O(log N) pagination

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

import grpc
import redis.asyncio as aioredis
from dotenv import load_dotenv
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
COUNTER_KEY: str = "benchmark:counter"
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
def _record_key(record_id: int) -> str:
    return f"{RECORD_PREFIX}{record_id}"


def _hash_to_proto(data: dict) -> benchmark_pb2.BenchmarkRecord:
    return benchmark_pb2.BenchmarkRecord(
        id=int(data["id"]),
        payload=data["payload"],
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
                new_id: int = await self._r.incr(COUNTER_KEY)
                data = {"id": str(new_id), "payload": request.payload}
                async with self._r.pipeline(transaction=True) as pipe:
                    pipe.hset(_record_key(new_id), mapping=data)
                    pipe.zadd(INDEX_KEY, {str(new_id): new_id})
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
                        f"Record {request.id} not found.",
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
                ids = await self._r.zrange(INDEX_KEY, offset, offset + limit - 1)
                total: int = await self._r.zcard(INDEX_KEY)
                records = []
                for record_id in ids:
                    data = await self._r.hgetall(_record_key(int(record_id)))
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
                if not await self._r.exists(key):
                    await context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Record {request.id} not found.",
                    )
                await self._r.hset(key, "payload", request.payload)
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
                    pipe.zrem(INDEX_KEY, str(request.id))
                    results = await pipe.execute()
                if results[0] == 0:
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
    log.info("Connecting to Redis...")
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    await r.ping()

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
