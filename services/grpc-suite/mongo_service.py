"""
gRPC Suite — MongoDB Service
===============================
Port      : 50052  (gRPC)
Port+1    : 50062  (Prometheus metrics sidecar HTTP)
Driver    : motor (async MongoDB driver)
Model     : BenchmarkRecord { id: int, payload: str }
            ← IDENTICAL to REST suite model / protobuf definition
Proto     : services/protobufs/benchmark.proto
Stubs     : generated via  bash grpc-suite/generate_stubs.sh

ID strategy: atomic counter document via findOneAndUpdate (upsert)
             giving auto-increment integer ids.

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
from dotenv import load_dotenv
from grpc_reflection.v1alpha import reflection
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from prometheus_client import Counter, Histogram, start_http_server

import benchmark_pb2
import benchmark_pb2_grpc

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [grpc-mongo] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB: str = os.getenv("MONGO_DB", "benchmarkdb")
MONGO_COLLECTION: str = "benchmark_records"
MONGO_COUNTERS: str = "benchmark_counters"
GRPC_PORT: int = int(os.getenv("GRPC_MONGO_PORT", "50052"))
METRICS_PORT: int = int(os.getenv("GRPC_MONGO_METRICS_PORT", "50062"))

# ──────────────────────────────────────────────
# Prometheus metrics
# ──────────────────────────────────────────────
RPC_REQUESTS = Counter(
    "grpc_mongo_requests_total",
    "Total gRPC requests to MongoDB service",
    ["method"],
)
RPC_LATENCY = Histogram(
    "grpc_mongo_latency_seconds",
    "gRPC request latency for MongoDB service",
    ["method"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)
RPC_ERRORS = Counter(
    "grpc_mongo_errors_total",
    "Total gRPC errors for MongoDB service",
    ["method"],
)

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _doc_to_proto(doc: dict) -> benchmark_pb2.BenchmarkRecord:
    return benchmark_pb2.BenchmarkRecord(id=int(doc["id"]), payload=doc["payload"])


# ──────────────────────────────────────────────
# gRPC Servicer
# ──────────────────────────────────────────────
class BenchmarkServicer(benchmark_pb2_grpc.BenchmarkServiceServicer):
    """
    Implements the gRPC BenchmarkService backed by MongoDB.
    All method signatures intentionally mirror the REST suite's endpoints.
    """

    def __init__(self, col: AsyncIOMotorCollection, counters: AsyncIOMotorCollection) -> None:
        self._col = col
        self._counters = counters

    async def _next_id(self) -> int:
        result = await self._counters.find_one_and_update(
            {"_id": "benchmark_records"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=True,
        )
        return result["seq"]

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
                new_id = await self._next_id()
                doc = {"id": new_id, "payload": request.payload}
                await self._col.insert_one(doc)
                return _doc_to_proto(doc)
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
                doc = await self._col.find_one({"id": request.id}, {"_id": 0})
                if doc is None:
                    await context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Record {request.id} not found.",
                    )
                return _doc_to_proto(doc)
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
                cursor = (
                    self._col.find({}, {"_id": 0})
                    .sort("id", 1)
                    .skip(offset)
                    .limit(limit)
                )
                docs = await cursor.to_list(length=limit)
                total: int = await self._col.count_documents({})
                return benchmark_pb2.ListRecordsResponse(
                    records=[_doc_to_proto(d) for d in docs],
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
                result = await self._col.find_one_and_update(
                    {"id": request.id},
                    {"$set": {"payload": request.payload}},
                    projection={"_id": 0},
                    return_document=True,
                )
                if result is None:
                    await context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"Record {request.id} not found.",
                    )
                return _doc_to_proto(result)
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
                result = await self._col.delete_one({"id": request.id})
                if result.deleted_count == 0:
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
    log.info("Connecting to MongoDB...")
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[MONGO_DB]
    col = db[MONGO_COLLECTION]
    counters = db[MONGO_COUNTERS]
    await col.create_index("id", unique=True)

    start_http_server(METRICS_PORT)
    log.info(f"Prometheus metrics on :{METRICS_PORT}/metrics")

    server = grpc.aio.server()
    benchmark_pb2_grpc.add_BenchmarkServiceServicer_to_server(
        BenchmarkServicer(col, counters), server
    )

    service_names = (
        benchmark_pb2.DESCRIPTOR.services_by_name["BenchmarkService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    await server.start()
    log.info(f"gRPC MongoDB service listening on :{GRPC_PORT}")

    try:
        await server.wait_for_termination()
    finally:
        client.close()
        log.info("MongoDB client closed, server shut down.")


if __name__ == "__main__":
    asyncio.run(serve())
