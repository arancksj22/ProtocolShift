# RUNNING.md — How to Run ProtocolShift

A step-by-step guide to standing up the full benchmark testbed, running load, and reading results.

---

## Prerequisites

| Tool | Minimum version | Check |
|---|---|---|
| Docker Desktop | 24+ | `docker --version` |
| Docker Compose | v2 (bundled with Desktop) | `docker compose version` |
| Python | 3.11+ (only for local runs / stub gen) | `python --version` |
| `grpcurl` (optional) | any | `grpcurl --version` |
| `curl` or Postman (optional) | any | manual testing |

> [!NOTE]
> If you only want to run via Docker, Python is **not required** on your host.
> The gRPC proto stubs are generated automatically inside the Docker build.

---

## Repository layout (quick reference)

```
.
├── services/
│   ├── rest-suite/          # 3 × FastAPI services (postgres, mongo, redis)
│   │   ├── postgres_service.py
│   │   ├── mongo_service.py
│   │   ├── redis_service.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── grpc-suite/          # 3 × gRPC services (postgres, mongo, redis)
│   │   ├── postgres_service.py
│   │   ├── mongo_service.py
│   │   ├── redis_service.py
│   │   ├── generate_stubs.sh
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── protobufs/
│       └── benchmark.proto  # source of truth for gRPC contract
├── infrastructure/
│   └── docker-compose.yml   # spins up everything
└── monitoring/
    ├── prometheus/
    │   └── prometheus.yml   # scrape configs
    └── grafana/
        ├── provisioning/    # auto-wires datasource + dashboards
        └── dashboards/
            └── protocolshift_benchmark.json
```

---

## Option A — Docker (recommended, no Python needed)

### Step 1 — Build and start all services

```bash
cd infrastructure
docker compose up --build
```

This command starts:

| Container | What it is | Port(s) |
|---|---|---|
| `postgres` | PostgreSQL 16 database | internal |
| `mongo` | MongoDB 7 database | internal |
| `redis` | Redis 7 in-memory store | internal |
| `rest-postgres` | REST FastAPI → Postgres | `8001` |
| `rest-mongo` | REST FastAPI → MongoDB | `8002` |
| `rest-redis` | REST FastAPI → Redis | `8003` |
| `grpc-postgres` | gRPC server → Postgres | `50051` (gRPC), `50061` (metrics) |
| `grpc-mongo` | gRPC server → MongoDB | `50052` (gRPC), `50062` (metrics) |
| `grpc-redis` | gRPC server → Redis | `50053` (gRPC), `50063` (metrics) |
| `prometheus` | Metrics scraper | `9090` |
| `grafana` | Dashboard UI | `3000` |

Wait ~30 seconds for databases to initialise. You will see log lines like:

```
grpc-postgres  | gRPC PostgreSQL service listening on :50051
rest-postgres  | Uvicorn running on http://0.0.0.0:8001
```

### Step 2 — Verify all services are healthy

```bash
# REST health checks
curl http://localhost:8001/healthz
curl http://localhost:8002/healthz
curl http://localhost:8003/healthz

# REST OpenAPI docs (interactive, browser)
# http://localhost:8001/docs
# http://localhost:8002/docs
# http://localhost:8003/docs
```

```bash
# gRPC health check via reflection (requires grpcurl)
grpcurl -plaintext localhost:50051 list
grpcurl -plaintext localhost:50052 list
grpcurl -plaintext localhost:50053 list
```

---

## Option B — Local Python (no Docker, databases must be running separately)

### Step 1 — Start databases however you like

```bash
# Example with Docker (individual containers, no compose):
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=benchmarkdb postgres:16-alpine
docker run -d -p 27017:27017 mongo:7
docker run -d -p 6379:6379 redis:7-alpine
```

### Step 2 — Install REST suite dependencies

```bash
cd services/rest-suite
pip install -r requirements.txt
```

### Step 3 — Run REST services

Open three terminals (or use `&` to background):

```bash
# Terminal 1
cd services/rest-suite
uvicorn postgres_service:app --host 0.0.0.0 --port 8001

# Terminal 2
uvicorn mongo_service:app --host 0.0.0.0 --port 8002

# Terminal 3
uvicorn redis_service:app --host 0.0.0.0 --port 8003
```

### Step 4 — Generate gRPC stubs (one-time)

```bash
cd services
pip install grpcio-tools
bash grpc-suite/generate_stubs.sh
# Produces: grpc-suite/benchmark_pb2.py  +  grpc-suite/benchmark_pb2_grpc.py
```

### Step 5 — Install gRPC suite dependencies

```bash
cd services/grpc-suite
pip install -r requirements.txt
```

### Step 6 — Run gRPC services

```bash
# Terminal 4
cd services/grpc-suite
python postgres_service.py

# Terminal 5
python mongo_service.py

# Terminal 6
python redis_service.py
```

---

## Sending test requests

### REST — `curl` examples

```bash
# Create a record
curl -s -X POST http://localhost:8001/records \
  -H "Content-Type: application/json" \
  -d '{"payload": "hello world"}' | python -m json.tool

# Read by id
curl -s http://localhost:8001/records/1 | python -m json.tool

# List all (with pagination)
curl -s "http://localhost:8001/records?limit=10&offset=0" | python -m json.tool

# Update
curl -s -X PUT http://localhost:8001/records/1 \
  -H "Content-Type: application/json" \
  -d '{"payload": "updated payload"}' | python -m json.tool

# Delete
curl -s -X DELETE http://localhost:8001/records/1 | python -m json.tool
```

> Swap `localhost:8001` → `8002` (MongoDB) or `8003` (Redis) to hit other backends.

### gRPC — `grpcurl` examples

```bash
# Create
grpcurl -plaintext -d '{"payload": "hello world"}' \
  localhost:50051 benchmark.BenchmarkService/Create

# Read
grpcurl -plaintext -d '{"id": 1}' \
  localhost:50051 benchmark.BenchmarkService/Read

# List (limit=10, offset=0)
grpcurl -plaintext -d '{"limit": 10, "offset": 0}' \
  localhost:50051 benchmark.BenchmarkService/ReadAll

# Update
grpcurl -plaintext -d '{"id": 1, "payload": "updated payload"}' \
  localhost:50051 benchmark.BenchmarkService/Update

# Delete
grpcurl -plaintext -d '{"id": 1}' \
  localhost:50051 benchmark.BenchmarkService/Delete
```

> Swap `50051` → `50052` (MongoDB) or `50053` (Redis).

---

## Viewing the dashboard

1. Open **http://localhost:3000** in your browser
2. Log in with `admin` / `protocolshift`
3. Go to **Dashboards → ProtocolShift → REST vs gRPC Benchmark**

The dashboard shows the **Four Golden Signals** in real time:

| Signal | What to look for |
|---|---|
| **Latency** | p99 gap between REST and gRPC panels — wider gap = bigger protocol overhead |
| **Traffic** | Confirms your load generator is actually hitting the service |
| **Errors** | Should be 0% during normal benchmarking; spikes indicate misconfiguration |
| **Saturation** | Rising in-flight count = service can't keep up with load |

Prometheus raw metrics are at **http://localhost:9090**.

---

## Running a benchmark load test

You need a load generator. The simplest options:

### Option 1 — `hey` (Go, single binary)

```bash
# Install
go install github.com/rakyll/hey@latest

# Hammer REST/Postgres Create endpoint — 1000 requests, 50 concurrent
hey -n 1000 -c 50 -m POST \
  -H "Content-Type: application/json" \
  -d '{"payload":"benchmarkpayload"}' \
  http://localhost:8001/records

# Then repeat against the other ports for comparison
hey -n 1000 -c 50 -m POST \
  -H "Content-Type: application/json" \
  -d '{"payload":"benchmarkpayload"}' \
  http://localhost:8002/records

hey -n 1000 -c 50 -m POST \
  -H "Content-Type: application/json" \
  -d '{"payload":"benchmarkpayload"}' \
  http://localhost:8003/records
```

### Option 2 — `k6` (recommended for detailed stats)

```bash
# Install k6: https://k6.io/docs/getting-started/installation/

# Create a quick script k6_test.js:
cat > /tmp/k6_test.js << 'EOF'
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  vus: 50,
  duration: '30s',
};

const BASE = __ENV.BASE_URL || 'http://localhost:8001';

export default function () {
  const res = http.post(
    `${BASE}/records`,
    JSON.stringify({ payload: 'benchmarkpayload' }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  check(res, { 'status 201': (r) => r.status === 201 });
}
EOF

# Run against each REST backend
k6 run -e BASE_URL=http://localhost:8001 /tmp/k6_test.js   # Postgres
k6 run -e BASE_URL=http://localhost:8002 /tmp/k6_test.js   # MongoDB
k6 run -e BASE_URL=http://localhost:8003 /tmp/k6_test.js   # Redis
```

> Watch the Grafana dashboard live while `k6` is running — you'll see traffic, latency, and error panels all update in real time.

---

## Stopping everything

```bash
cd infrastructure

# Stop all containers (keeps data volumes)
docker compose down

# Stop AND wipe all database data (clean slate for next run)
docker compose down -v
```

---

## Port reference

| Service | Protocol | Port | Purpose |
|---|---|---|---|
| REST → Postgres | HTTP | `8001` | CRUD API + `/metrics` |
| REST → MongoDB | HTTP | `8002` | CRUD API + `/metrics` |
| REST → Redis | HTTP | `8003` | CRUD API + `/metrics` |
| gRPC → Postgres | gRPC (HTTP/2) | `50051` | RPC endpoint |
| gRPC → MongoDB | gRPC (HTTP/2) | `50052` | RPC endpoint |
| gRPC → Redis | gRPC (HTTP/2) | `50053` | RPC endpoint |
| gRPC/Postgres metrics | HTTP | `50061` | Prometheus scrape |
| gRPC/MongoDB metrics | HTTP | `50062` | Prometheus scrape |
| gRPC/Redis metrics | HTTP | `50063` | Prometheus scrape |
| Prometheus | HTTP | `9090` | Metrics storage + query |
| Grafana | HTTP | `3000` | Dashboard UI |

---

## Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `connection refused` on startup | DB not ready yet | Wait 10–20 s; services have health-check `depends_on` |
| gRPC `UNIMPLEMENTED` | Stubs out of date with proto | Rebuild Docker image or re-run `generate_stubs.sh` |
| Grafana shows "No data" | Services not scraped yet | Check Prometheus targets at `http://localhost:9090/targets` |
| Metrics endpoint 404 on gRPC service | Wrong metrics port | Use `506x` ports, not `505x` (those are gRPC ports) |
| `benchmark_pb2` import error (local run) | Stubs not generated | Run `bash grpc-suite/generate_stubs.sh` from `services/` |
