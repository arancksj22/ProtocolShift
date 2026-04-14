# ProtocolShift — Getting Started

> **Goal:** From a cold machine to a live Grafana dashboard showing REST vs gRPC latency metrics — in under 5 minutes.

---

## Prerequisites

All you need is **Docker Desktop** running on Windows.

> [!IMPORTANT]
> Open Docker Desktop and confirm the whale icon in the system tray says **"Engine running"** before continuing.
> Download it at [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) if not installed.

---

## Step 1 — Open a terminal at the repo root

Open PowerShell and navigate here:

```powershell
cd e:\CodingVacation\PracticeRepos\ProtocolShift_DistributedComputing
```

---

## Step 2 — Start everything

```powershell
cd infrastructure
docker compose up --build
```

This single command will:

| Phase | What happens |
|---|---|
| **Build** (~2 min first time) | Docker builds the REST and gRPC images, compiles proto stubs inside the gRPC image |
| **Databases start** | PostgreSQL, MongoDB, Redis spin up and pass their health checks |
| **Services start** | All 6 services start once their database is healthy |
| **Observability starts** | Prometheus begins scraping, Grafana loads the dashboard |

✅ **You're ready** when you see these lines in the log:

```
rest-postgres  | Uvicorn running on http://0.0.0.0:8001
rest-mongo     | Uvicorn running on http://0.0.0.0:8002
rest-redis     | Uvicorn running on http://0.0.0.0:8003
grpc-postgres  | gRPC PostgreSQL service listening on :50051
grpc-mongo     | gRPC MongoDB service listening on :50052
grpc-redis     | gRPC Redis service listening on :50053
```

**Leave this terminal open.** It streams all service logs.

---

## Step 3 — Run the smoke test

Open a **second PowerShell** at the repo root and run:

```powershell
cd e:\CodingVacation\PracticeRepos\ProtocolShift_DistributedComputing
.\test_all.ps1 -SkipGrpc
```

This automatically runs all 5 CRUD operations (Create, Read, List, Update, Delete) against all 3 REST backends and prints a pass/fail result per step. You should see:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  REST — PostgreSQL  (http://localhost:8001)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  [PASS] Health check
  [PASS] Create  →  id=1
  [PASS] Read    →  id=1 payload='benchmark-smoke-test'
  [PASS] ReadAll →  total=1, returned=1
  [PASS] Update  →  payload='updated-smoke-test'
  [PASS] Delete  →  id=1 removed
  [PASS] 404 confirmed — record correctly removed
  ...

  Results:  21 / 21 passed ✓
```

> [!WARNING]
> If you see `[FAIL]` on the health check for any service, look at the logs in your first terminal. The most common cause is the database still initialising — wait 15 seconds and re-run the script.

---

## Step 4 — Generate load for the dashboard

The dashboard needs traffic data to draw graphs. Run this in your second terminal — it uses background jobs to fire 200 requests at all REST and gRPC backends simultaneously:

```powershell
# Generate REST and gRPC load simultaneously
$restJob = Start-Job {
    1..200 | ForEach-Object {
        Invoke-RestMethod -Method POST -Uri "http://localhost:8001/records" -ContentType "application/json" -Body '{"payload":"load-test-payload-goes-here-to-stress-serialisation"}' | Out-Null
        Invoke-RestMethod -Method POST -Uri "http://localhost:8002/records" -ContentType "application/json" -Body '{"payload":"load-test-payload-goes-here-to-stress-serialisation"}' | Out-Null
        Invoke-RestMethod -Method POST -Uri "http://localhost:8003/records" -ContentType "application/json" -Body '{"payload":"load-test-payload-goes-here-to-stress-serialisation"}' | Out-Null
    }
}

$grpcJob = Start-Job {
    1..200 | ForEach-Object {
        grpcurl -plaintext -d '{\"payload\": \"load-test-payload-goes-here-to-stress-serialisation\"}' localhost:50051 benchmark.BenchmarkService/Create
        grpcurl -plaintext -d '{\"payload\": \"load-test-payload-goes-here-to-stress-serialisation\"}' localhost:50052 benchmark.BenchmarkService/Create
        grpcurl -plaintext -d '{\"payload\": \"load-test-payload-goes-here-to-stress-serialisation\"}' localhost:50053 benchmark.BenchmarkService/Create
    } | Out-Null
}

# Wait for both protocols to finish blasting traffic
Wait-Job -Job $restJob, $grpcJob
Receive-Job -Job $restJob, $grpcJob
Write-Host "Done — open Grafana!"
```

This takes ~30–60 seconds. Once it finishes you have enough data for meaningful graphs.

---

## Step 5 — Open the Grafana dashboard

Go to **http://localhost:3000** in your browser.

```
Username: admin
Password: protocolshift
```

Navigate to: **Dashboards (left sidebar) → ProtocolShift → REST vs gRPC Benchmark**

### What you'll see

| Panel | What you're looking at |
|---|---|
| **p99 Latency — all services** | All 6 services overlaid — the gap between REST and gRPC lines is the pure protocol overhead |
| **p50 / p95 Latency** | Postgres vs Redis tail latency — shows how much DB I/O dominates vs. protocol |
| **Request Rate** | Confirms your load is hitting the right services |
| **Error Rate** | Should be flat 0% during healthy runs; spikes = misconfiguration |
| **Total Requests** | Running counters for each of the 6 services |
| **Latency Snapshot table** | p50 / p95 / p99 for all services at a glance, sortable |

> [!TIP]
> Click any legend entry to isolate a single service. Use the time picker (top right) to zoom into the load period. Prometheus raw queries are at **http://localhost:9090**.

---

## Step 6 — Tear down when done

Back in the first terminal press `Ctrl+C`, then:

```powershell
# Keep DB data for next run
docker compose down

# Wipe everything back to a clean slate
docker compose down -v
```

---

## Quick-reference cheatsheet

```
┌─────────────────────────────────────────────────────────────────┐
│  cd infrastructure                                             │
│  docker compose up --build          ← start everything         │
│                                                                │
│  .\test_all.ps1 -SkipGrpc           ← verify all endpoints     │
│                                                                │
│  http://localhost:3000              ← Grafana dashboard         │
│     login: admin / protocolshift                               │
│                                                                │
│  http://localhost:9090              ← Prometheus raw metrics    │
│                                                                │
│  Ctrl+C  →  docker compose down     ← stop                     │
│  Ctrl+C  →  docker compose down -v  ← stop + wipe data         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Port map

| Service | Port | Purpose |
|---|---|---|
| REST → PostgreSQL | `8001` | API + `/metrics` |
| REST → MongoDB | `8002` | API + `/metrics` |
| REST → Redis | `8003` | API + `/metrics` |
| gRPC → PostgreSQL | `50051` / `50061` | gRPC / Prometheus |
| gRPC → MongoDB | `50052` / `50062` | gRPC / Prometheus |
| gRPC → Redis | `50053` / `50063` | gRPC / Prometheus |
| Prometheus | `9090` | Metrics storage |
| Grafana | `3000` | Dashboard UI |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Service shows `[FAIL]` on health check | Wait 15 s and re-run `test_all.ps1` — DB may still be initialising |
| Grafana dashboard shows "No data" | Run the Step 4 load loop first; check `http://localhost:9090/targets` that all 6 jobs show **UP** |
| `docker compose up` fails with "port already in use" | Another process is using that port — run `netstat -ano \| findstr :8001` to find and stop it |
| `docker compose up --build` is very slow | First build downloads Python base images (~200 MB) — subsequent builds are cached and fast |
