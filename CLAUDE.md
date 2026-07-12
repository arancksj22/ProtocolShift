# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

ProtocolShift is a research benchmarking testbed (research paper in `docs/FINALDOCUMENT.txt`, results in `docs/local_results/` and `docs/cloud_results/`) that isolates the performance difference between HTTP/1.1 REST (FastAPI + JSON) and gRPC (HTTP/2 + Protobuf). The same 5 CRUD operations are implemented twice — once per protocol — against three database paradigms: PostgreSQL (heavy compute), Redis (in-memory, isolates pure protocol overhead), and MongoDB (document store). Everything is designed for apples-to-apples comparison, so **parity between the two suites is the core invariant** of the codebase.

## Commands

All orchestration is Docker Compose; there is no package.json / Makefile / pytest suite.

```powershell
# Local benchmark (databases run in Docker)
cd local/infrastructure
docker compose up --build          # start everything
docker compose down                # stop, keep DB data
docker compose down -v             # stop + wipe data

# Smoke test (from local/ or cloud/; run against a live stack)
.\test_all.ps1                     # tests REST + gRPC (needs grpcurl on PATH)
.\test_all.ps1 -SkipGrpc           # REST only
# test_all.sh is the bash equivalent; exit code = number of failures

# Cloud benchmark (app layer local, DBs on Supabase/Atlas/Upstash)
cd cloud
docker compose --env-file .env up --build   # requires cloud/.env with POSTGRES_DSN, MONGO_URI, REDIS_URL

# Regenerate gRPC stubs after editing benchmark.proto (only needed for
# running services outside Docker — the grpc-suite Dockerfile regenerates
# stubs at build time)
cd local/services
bash grpc-suite/generate_stubs.sh  # writes benchmark_pb2.py / benchmark_pb2_grpc.py into grpc-suite/
```

Manual gRPC calls use grpcurl (server reflection is enabled):
`grpcurl -plaintext -d '{"payload":"x"}' localhost:50051 benchmark.BenchmarkService/Create`

### Port map

| Service | REST | gRPC | gRPC metrics sidecar |
|---|---|---|---|
| PostgreSQL | 8001 | 50051 | 50061 |
| MongoDB | 8002 | 50052 | 50062 |
| Redis | 8003 | 50053 | 50063 |

Grafana: http://localhost:3000 (admin / protocolshift, dashboard "ProtocolShift → REST vs gRPC Benchmark"). Prometheus: http://localhost:9090.

## Architecture

Two mutually exclusive service suites in `local/services/`, each containing three standalone single-file services (one per database). There is no shared application code between files — each service is deliberately self-contained.

- `rest-suite/`: FastAPI + Uvicorn, Pydantic models, `prometheus-fastapi-instrumentator` auto-exposes `/metrics`. Endpoints: POST/GET/PUT/DELETE `/records`, `/healthz`, `/metrics`.
- `grpc-suite/`: `grpc.aio` servers implementing `BenchmarkService` (Create/Read/ReadAll/Update/Delete) with server reflection enabled. Each starts a **separate Prometheus HTTP sidecar** via `start_http_server(METRICS_PORT)` on gRPC-port+10 (50051→50061 etc.).
- `protobufs/benchmark.proto`: **single source of truth** for the data contract. `BenchmarkRecord {id: int32, payload: string}` is mirrored manually by the Pydantic models in rest-suite.

Deliberate design constraints (do not "improve" these away — they exist to keep the benchmark clean):
- **No ORM**: raw SQL via asyncpg, raw commands via motor / redis.asyncio, to expose raw serialization cost.
- **Fully async everywhere** (async/await, connection pools of 2–10 created at startup).
- **Identical logic across suites**: any change to a REST service's queries, storage layout, error semantics, or metrics granularity must be applied to the corresponding gRPC service (and vice versa), or the benchmark comparison is invalidated. E.g. both Redis services use the same key scheme: atomic `INCR benchmark:counter` for ids, hash at `benchmark:<id>`, sorted set `benchmark:index` for pagination; both Mongo services use a `benchmark_counters` collection with `find_one_and_update` upsert for auto-increment ids.
- **Consistent metric naming**: `{suite}_{backend}_requests_total`, `..._latency_seconds` (Histogram with fixed buckets 0.001–2.5s, labeled by `method`), `..._errors_total`. The Grafana dashboard (`local/monitoring/grafana/dashboards/protocolshift_benchmark.json`) and Prometheus scrape jobs (`local/monitoring/prometheus/prometheus.yml`, labels `suite`/`backend`) depend on these names — change them in lockstep.

### local/ vs cloud/

`cloud/docker-compose.yml` reuses the **same images and code** (`build context: ../local/services`) and same monitoring config; the only differences are (1) no database containers — connection strings come from `cloud/.env` (Supabase, MongoDB Atlas, Upstash Redis), and (2) no healthcheck-gated `depends_on`. Code changes under `local/services/` therefore affect both environments.

Docker Compose details worth knowing: build context is `../services` (not the suite dir) so Dockerfiles can reach `protobufs/`; DB connection env vars are shared via YAML anchors (`&postgres-env` etc.); app containers wait on database healthchecks locally.

### Configuration

Everything is env-var driven with localhost defaults, loaded via python-dotenv: `POSTGRES_DSN`, `MONGO_URI`+`MONGO_DB`, `REDIS_URL`, `GRPC_<BACKEND>_PORT`, `GRPC_<BACKEND>_METRICS_PORT`. This lets services run bare (`python postgres_service.py`, `uvicorn postgres_service:app --port 8001`) against local DBs without Docker.

### Dependency pins

`motor==3.4.0` requires `pymongo<4.9` — pymongo is pinned to 4.8.0 in both requirements.txt files to avoid the `_QUERY_OPTIONS` ImportError from pymongo 4.9. grpcio-tools 1.63.0 requires `protobuf>=5.26.1,<6.0`.

## Research context (from docs/FINALDOCUMENT.txt)

Key empirical findings that shape the project narrative — useful when editing docs or dashboards:
- gRPC dominates when the DB is fast (local Redis p99: gRPC ~4.9 ms vs REST ~99 ms), validating that protocol overhead only matters when the database isn't the bottleneck (Amdahl's Law framing).
- The PostgreSQL tier is the deliberate counter-example: gRPC's p99 was *worse* than REST there (local mean 130 ms vs 104 ms; cloud mean 861 ms vs 347 ms).
- Analysis centers on p95/p99 tail latency, not means; the Grafana dashboard's differential overlays of REST vs gRPC are the primary evidence artifact.
