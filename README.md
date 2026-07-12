# ProtocolShift: Evaluating Protocol Efficiency and Serialization Latency

ProtocolShift is a controlled experimental testbed designed to isolate the performance difference between HTTP/1.1 REST and gRPC. By separating transport and serialization overhead from application logic and database I/O, the project quantifies the serialization tax and head of line blocking in high concurrency environments.

## Architecture Diagram

![ProtocolShift Architecture](docs/architecture%20diagram%20protocal%20shift.jfif)

## Research Objectives

The project evaluates protocol efficiency across three distinct database paradigms to address critical research gaps:
* Database Blind Spot: Determining if protocol optimizations provide macroscopic improvements when the database is the primary bottleneck, guided by Amdahls Law.
* Tail Latency Analysis: Focusing on p95 and p99 metrics to understand system degradation under extreme CPU stress and garbage collection sweeps.
* Migration Thresholds: Establishing a metric-validated framework using the Strangler Fig pattern to prove the value of protocol migration.
* Local to Cloud Coefficient: Factoring in cloud-native complexities like network jitter and TLS overhead that are often missing from local benchmarks.

## System Architecture

The testbed employs parallel service layers converging on three storage paradigms:
* PostgreSQL: Represents the heavy compute tier. It uses a zero-ORM pipeline with asyncpg to reveal raw serialization costs.
* Redis: Represents in-memory persistence. By removing the disk storage bottleneck, it provides the purest measure of network and protocol efficiency.
* MongoDB: Represents document-oriented storage. It evaluates how gRPC handles unstructured payloads compared to the self-descriptive verbosity of JSON.

## Technical Stack

* REST Suite: FastAPI and Uvicorn utilizing ASGI for asynchronous, non-blocking execution.
* gRPC Suite: Python grpcio implementation leveraging HTTP/2 binary framing and multiplexing.
* Observability: Prometheus and Grafana stack capturing the Four Golden Signals (Latency, Traffic, Errors, and Saturation).
* Schema Management: Protocol Buffers serve as the single source of truth for both suites.

## API Contracts and Endpoints

The system implements a consistent CRUD interface across both protocol suites. The following operations are supported:

### REST Endpoints (JSON)
* POST /records: Create a new benchmark record.
* GET /records/{id}: Fetch a single record by integer id.
* GET /records: List records with limit and offset pagination.
* PUT /records/{id}: Update the string payload of an existing record.
* DELETE /records/{id}: Remove a record from the database.
* GET /healthz: Service health check.
* GET /metrics: Prometheus metrics scrape target.

### gRPC Service (Protobuf)
* rpc Create(CreateRecordRequest) returns (BenchmarkRecord)
* rpc Read(GetRecordRequest) returns (BenchmarkRecord)
* rpc ReadAll(ListRecordsRequest) returns (ListRecordsResponse)
* rpc Update(UpdateRecordRequest) returns (BenchmarkRecord)
* rpc Delete(DeleteRecordRequest) returns (DeleteRecordResponse)

### Shared Data Model
The core BenchmarkRecord object is mirrored exactly in both suites:
* id (int32): Unique identifier assigned by the storage backend.
* payload (string): Arbitrary string data used to test serialization overhead.

## Benchmark Harness

The `benchmark/` directory contains the load-generation and analysis toolchain
used to produce statistically rigorous results (see `HOW_TO_RUN.md` for the
full walkthrough):

* Controlled load generation: configurable concurrency levels, payload sizes,
  warm-up, and duration. gRPC is driven over a persistent multiplexed HTTP/2
  channel and REST over a persistent connection pool, so protocol behavior is
  measured rather than client-tooling overhead.
* Repeated trials with database flushing between runs, producing 95%
  confidence intervals, variance/CoV, and Mann-Whitney U significance tests
  for every REST-vs-gRPC comparison.
* Profiling evidence: per-query database execution time via
  pg_stat_statements, container-level CPU/memory/network sampling, and py-spy
  flame graphs — used to investigate the PostgreSQL anomaly (gRPC
  underperforming REST on the heavy-compute tier).
* Reproducibility capture: every campaign records host hardware, Docker engine
  resources, image digests, and dependency versions in `environment.json`.
* A formal Protocol Migration Threshold model: three numerical gates
  (p99 improvement, statistical robustness, Amdahl DB-share) evaluated
  directly against the measured data.

## Repository Structure

```
.
├── run_all.py                  # One-command runner: Docker + stack + campaign + analysis
├── HOW_TO_RUN.md               # Step-by-step benchmark campaign guide
├── benchmark/                  # Load-generation & analysis harness
│   ├── loadgen.py              # Async closed-loop load generator (REST + gRPC)
│   ├── run_trials.py           # Repeated-trials campaign runner
│   ├── analyze.py              # CIs, significance tests, plots, migration report
│   ├── profiling.py            # docker stats + pg_stat_statements instruments
│   ├── pyspy_profile.py        # Flame graphs of running service containers
│   └── capture_env.py          # Hardware/software environment capture
├── cloud/                      # Cloud benchmark environment
│   ├── docker-compose.yml      # Orchestration for cloud connected services
│   ├── test_all.ps1            # Validation scripts for cloud tests
│   └── RUNNING.md              # Cloud specific setup instructions
├── docs/                       # Research documentation and results
│   ├── local_results/          # Local execution latency plots
│   ├── cloud_results/          # Cloud execution latency plots
│   └── FINALDOCUMENT.txt       # Comprehensive research paper
├── results/                    # Benchmark campaign outputs (gitignored)
└── local/                      # Local benchmark environment
    ├── RUNNING.md              # Local specific setup instructions
    ├── test_all.ps1            # Smoke test for all local services
    ├── infrastructure/         # Docker orchestration for local DBs and apps
    ├── monitoring/             # Observability stack configuration
    │   ├── grafana/            # Provisioning and dashboard JSONs
    │   └── prometheus/         # Scrape intervals and job targets
    └── services/               # Implementation logic
        ├── grpc-suite/         # gRPC server implementations
        ├── rest-suite/         # FastAPI server implementations
        └── protobufs/          # Source of truth .proto definitions
```

## Getting Started

Fastest path — one command from the repo root (starts Docker, installs
dependencies, builds the stack, runs a benchmark campaign, analyzes it):

```
python run_all.py           # quick sanity campaign
python run_all.py --full    # full 5-trial campaign with profiling
```

For the full benchmark workflow (repeated trials, statistics, profiling), see
**HOW_TO_RUN.md**. To just bring the testbed up manually:

1. Navigate to the local infrastructure directory: cd local/infrastructure
2. Start the testbed: docker compose up --build
3. Port Map:
    * REST Services: 8001 (Postgres), 8002 (Mongo), 8003 (Redis)
    * gRPC Services: 50051 (Postgres), 50052 (Mongo), 50053 (Redis)
    * Grafana: http://localhost:3000 (Credentials: admin / protocolshift)
    * Prometheus: http://localhost:9090
