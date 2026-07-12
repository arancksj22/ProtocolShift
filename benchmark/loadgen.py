"""
ProtocolShift — Async Load Generator
=====================================
Closed-loop load generator with controlled concurrency, payload size,
warm-up, and duration. Replaces the ad-hoc PowerShell/grpcurl loops.

Protocol clients (each used the way the protocol is used in production):
  REST : one httpx.AsyncClient — persistent HTTP/1.1 connection pool sized
         to the worker count.
  gRPC : one shared grpc.aio channel — persistent HTTP/2 connection with
         stream multiplexing. This fixes the grpcurl confound of the old
         load scripts (grpcurl spawned a new process + new HTTP/2
         connection + reflection lookup per request).

Workload model:
  `concurrency` workers each issue requests back-to-back (closed loop).
  Requests started during the warm-up window are issued but not recorded.
  Per-request wall latency (time.perf_counter) is recorded client-side so
  statistics are computed from raw samples, not histogram buckets.

Operations (--op):
  create : POST /records | Create RPC          (write path)
  read   : GET /records/{id} | Read RPC        (read path, over seeded ids)
  mixed  : alternating create / read per worker (default)

Usage (single run):
  python loadgen.py --suite rest --backend postgres --concurrency 50 \
      --payload-bytes 1000 --duration 15 --warmup 3 --out run.json
"""

import argparse
import asyncio
import json
import random
import string
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

# Make stubs.py / generated benchmark_pb2*.py importable regardless of the
# directory this module is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent))

REST_PORTS = {"postgres": 8001, "mongo": 8002, "redis": 8003}
GRPC_PORTS = {"postgres": 50051, "mongo": 50052, "redis": 50053}


@dataclass
class RunConfig:
    suite: str  # rest | grpc
    backend: str  # postgres | mongo | redis
    concurrency: int = 50
    payload_bytes: int = 1000
    duration: float = 15.0  # measurement window (seconds)
    warmup: float = 3.0  # discarded warm-up window (seconds)
    op: str = "mixed"  # create | read | mixed
    seed_records: int = 200  # records pre-created for read ops
    host: str = "localhost"
    timeout: float = 30.0

    def target(self) -> str:
        port = (REST_PORTS if self.suite == "rest" else GRPC_PORTS)[self.backend]
        return f"{self.host}:{port}"


@dataclass
class RunResult:
    config: dict
    started_at: str
    latencies_ms: list = field(default_factory=list)
    errors: int = 0
    warmup_requests: int = 0
    measured_seconds: float = 0.0

    @property
    def achieved_rps(self) -> float:
        n = len(self.latencies_ms)
        return n / self.measured_seconds if self.measured_seconds > 0 else 0.0

    def to_dict(self) -> dict:
        d = {
            "config": self.config,
            "started_at": self.started_at,
            "requests": len(self.latencies_ms),
            "errors": self.errors,
            "warmup_requests": self.warmup_requests,
            "measured_seconds": round(self.measured_seconds, 3),
            "achieved_rps": round(self.achieved_rps, 2),
            "latencies_ms": [round(x, 4) for x in self.latencies_ms],
        }
        return d


# ──────────────────────────────────────────────
# Protocol clients
# ──────────────────────────────────────────────
class RestClient:
    def __init__(self, cfg: RunConfig) -> None:
        self._cfg = cfg
        self._client = None

    async def open(self) -> None:
        import httpx

        limits = httpx.Limits(
            max_connections=self._cfg.concurrency,
            max_keepalive_connections=self._cfg.concurrency,
        )
        self._client = httpx.AsyncClient(
            base_url=f"http://{self._cfg.target()}",
            limits=limits,
            timeout=self._cfg.timeout,
        )

    async def health(self) -> None:
        r = await self._client.get("/healthz")
        r.raise_for_status()

    async def create(self, payload: str) -> int:
        r = await self._client.post("/records", json={"payload": payload})
        r.raise_for_status()
        return r.json()["id"]

    async def read(self, record_id: int) -> None:
        r = await self._client.get(f"/records/{record_id}")
        r.raise_for_status()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


class GrpcClient:
    def __init__(self, cfg: RunConfig) -> None:
        self._cfg = cfg
        self._channel = None
        self._stub = None

    async def open(self) -> None:
        import grpc
        from stubs import ensure_stubs

        ensure_stubs()
        import benchmark_pb2  # noqa: F401  (registers descriptors)
        import benchmark_pb2_grpc

        self._pb2 = benchmark_pb2
        self._channel = grpc.aio.insecure_channel(self._cfg.target())
        self._stub = benchmark_pb2_grpc.BenchmarkServiceStub(self._channel)

    async def health(self) -> None:
        # A cheap RPC doubles as readiness probe (ReadAll on empty DB is valid).
        await asyncio.wait_for(
            self._stub.ReadAll(self._pb2.ListRecordsRequest(limit=1, offset=0)),
            timeout=self._cfg.timeout,
        )

    async def create(self, payload: str) -> int:
        resp = await self._stub.Create(self._pb2.CreateRecordRequest(payload=payload))
        return resp.id

    async def read(self, record_id: int) -> None:
        await self._stub.Read(self._pb2.GetRecordRequest(id=record_id))

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()


def make_client(cfg: RunConfig):
    return RestClient(cfg) if cfg.suite == "rest" else GrpcClient(cfg)


# ──────────────────────────────────────────────
# Load loop
# ──────────────────────────────────────────────
def make_payload(size: int) -> str:
    # Deterministic payload so every run/trial serializes identical bytes.
    rng = random.Random(1337)
    return "".join(rng.choices(string.ascii_letters + string.digits, k=size))


async def run_load(cfg: RunConfig) -> RunResult:
    payload = make_payload(cfg.payload_bytes)
    client = make_client(cfg)
    await client.open()
    result = RunResult(
        config=asdict(cfg),
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    try:
        await client.health()

        # Seed records so read ops have targets.
        seeded_ids = []
        if cfg.op in ("read", "mixed"):
            seed_tasks = [client.create(payload) for _ in range(cfg.seed_records)]
            seeded_ids = list(await asyncio.gather(*seed_tasks))

        t_start = time.perf_counter()
        t_measure = t_start + cfg.warmup
        t_end = t_measure + cfg.duration

        async def worker(widx: int) -> None:
            rng = random.Random(widx)
            i = 0
            while True:
                t0 = time.perf_counter()
                if t0 >= t_end:
                    return
                do_create = cfg.op == "create" or (cfg.op == "mixed" and i % 2 == 0)
                i += 1
                try:
                    if do_create:
                        await client.create(payload)
                    else:
                        await client.read(rng.choice(seeded_ids))
                    ok = True
                except Exception:
                    ok = False
                t1 = time.perf_counter()
                if t0 >= t_measure:  # inside measurement window
                    if ok:
                        result.latencies_ms.append((t1 - t0) * 1000.0)
                    else:
                        result.errors += 1
                else:
                    result.warmup_requests += 1

        await asyncio.gather(*(worker(w) for w in range(cfg.concurrency)))
        result.measured_seconds = time.perf_counter() - t_measure
    finally:
        await client.close()
    return result


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ProtocolShift load generator (single run)")
    p.add_argument("--suite", required=True, choices=["rest", "grpc"])
    p.add_argument("--backend", required=True, choices=["postgres", "mongo", "redis"])
    p.add_argument("--concurrency", type=int, default=50)
    p.add_argument("--payload-bytes", type=int, default=1000)
    p.add_argument("--duration", type=float, default=15.0)
    p.add_argument("--warmup", type=float, default=3.0)
    p.add_argument("--op", choices=["create", "read", "mixed"], default="mixed")
    p.add_argument("--seed-records", type=int, default=200)
    p.add_argument("--host", default="localhost")
    p.add_argument("--out", default=None, help="Write raw result JSON here")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = RunConfig(
        suite=args.suite,
        backend=args.backend,
        concurrency=args.concurrency,
        payload_bytes=args.payload_bytes,
        duration=args.duration,
        warmup=args.warmup,
        op=args.op,
        seed_records=args.seed_records,
        host=args.host,
    )
    result = asyncio.run(run_load(cfg))
    lat = sorted(result.latencies_ms)

    def pct(p: float) -> float:
        if not lat:
            return float("nan")
        return lat[min(len(lat) - 1, int(round(p / 100 * (len(lat) - 1))))]

    print(
        f"{cfg.suite}-{cfg.backend} c={cfg.concurrency} payload={cfg.payload_bytes}B: "
        f"{len(lat)} reqs, {result.errors} errors, {result.achieved_rps:.1f} req/s | "
        f"p50={pct(50):.2f}ms p95={pct(95):.2f}ms p99={pct(99):.2f}ms"
    )
    if args.out:
        with open(args.out, "w") as f:
            json.dump(result.to_dict(), f)
        print(f"Raw result written to {args.out}")


if __name__ == "__main__":
    main()
