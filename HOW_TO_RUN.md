# How to Run — ProtocolShift Benchmark Campaign

End-to-end instructions: from a cold machine to paper-ready statistics, plots,
profiling evidence, and the migration-threshold report.

---

## TL;DR — one command

From the repo root, with any Python 3.11+:

```powershell
python run_all.py            # quick ~2-minute sanity campaign
python run_all.py --full     # the real thing: 5 trials × all cells + profiling (~1.5–2.5 h)
```

`run_all.py` does everything below automatically: starts Docker Desktop if the
engine is down, creates the `.venv` and installs dependencies, builds and
starts the compose stack, waits for all six services, runs the campaign, and
runs the analysis. It prints where the results and plots landed, and leaves
the stack running for Grafana. Other modes:

```powershell
python run_all.py --stack-only                          # just bring everything up
python run_all.py --trials 3 --concurrency 50 --profile # custom campaign
python run_all.py --down                                # tear the stack down
```

The sections below describe the same steps manually, for when you want finer
control (flame graphs, cloud mode, re-analysis with different gates).

---

## 0. Prerequisites

| Requirement | Notes |
|---|---|
| Docker Desktop | Engine must be running (whale icon → "Engine running") |
| Python 3.11+ | On PATH as `python` |
| ~2 GB disk | Docker images + results |

`grpcurl` is **no longer required** — the Python harness drives gRPC through a
persistent channel (the old grpcurl loops spawned a new process and connection
per request, which distorted gRPC results).

---

## 1. One-time setup

From the repo root:

```powershell
# Python environment for the benchmark harness
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r benchmark\requirements.txt
```

---

## 2. Start the testbed

```powershell
cd local\infrastructure
docker compose up --build
```

> If you had the stack running before this harness was added, restart it with
> `docker compose up --build --force-recreate` — the compose file now starts
> PostgreSQL with `pg_stat_statements` preloaded and grants the app containers
> `SYS_PTRACE` (both needed for profiling).

Wait for all six "listening" log lines, then verify with the smoke test in a
second terminal:

```powershell
cd local
.\test_all.ps1 -SkipGrpc
```

---

## 3. Sanity-check the harness (~1 minute)

From the repo root, with the venv active:

```powershell
cd benchmark
python run_trials.py --quick
```

This runs 1 short trial per suite/backend and prints live per-run stats. If
every line reports requests and a p99, you're ready for the real campaign.

You can also fire a single ad-hoc run:

```powershell
python loadgen.py --suite grpc --backend redis --concurrency 50 --duration 10
```

---

## 4. Run the full campaign (~1.5–2.5 hours)

```powershell
python run_trials.py --profile
```

Defaults: 2 suites × 3 backends × concurrency {10, 50, 100} × payload
{100 B, 1 KB, 10 KB} × **5 trials** of 15 s (+3 s warm-up), mixed
create/read workload. Databases are flushed between trials so every trial
starts from identical state. `--profile` additionally records:

- `docker stats` samples (CPU / memory / network per container) for every run
- `pg_stat_statements` snapshots per PostgreSQL trial (DB execution time,
  separated from protocol/serialization time)

Everything is configurable, e.g.:

```powershell
python run_trials.py --trials 10 --concurrency 25,100,200 --payload-bytes 1000 --backends postgres --profile
```

Output lands in `results/<run_id>/`:

```
results/20260712-140102/
├── config.json          # campaign parameters
├── environment.json     # host, Docker engine, image digests, package versions
├── raw/                 # per-trial raw latencies (one JSON per run)
└── profiling/           # docker_stats.csv, pgstat_*.json
```

---

## 5. Analyze

```powershell
python analyze.py ..\results\<run_id>
```

Writes `results/<run_id>/analysis/`:

| File | Contents |
|---|---|
| `summary.md` / `summary.csv` | Per-cell p50/p95/p99 with 95% CIs, CoV, req/s |
| `comparisons.csv` | REST vs gRPC per scenario: p99 delta, Mann-Whitney U p-value, CI-overlap verdict |
| `migration_decision.md` | Formal 3-gate Phase-Gate model (improvement / robustness / Amdahl) applied to the data |
| `plots/p99_vs_concurrency_*.png` | p99 scaling curves with CI error bars |
| `plots/cdf_*.png` | Full latency CDFs (pooled across trials) |
| `plots/p99_bars_*.png` | p99 bar charts with CI error bars |

Migration-gate parameters are tunable:
`python analyze.py ..\results\<run_id> --min-improvement 25 --max-db-share 50`

---

## 6. Flame graphs (PostgreSQL-anomaly investigation)

To see *where the CPU goes* (serialization vs driver vs event loop) in the two
PostgreSQL services, run in two terminals:

```powershell
# Terminal 1 — attach the profiler (30s sample)
python pyspy_profile.py --service grpc-postgres --duration 30

# Terminal 2 — generate load while it samples
python loadgen.py --suite grpc --backend postgres --concurrency 100 --duration 40 --warmup 2
```

Repeat with `--service rest-postgres` / `--suite rest` and compare the two
SVGs side by side in a browser.

---

## 7. Cloud campaign (managed databases)

Start the cloud stack (needs `cloud/.env` with `POSTGRES_DSN`, `MONGO_URI`,
`REDIS_URL` — see `cloud/RUNNING.md`):

```powershell
cd cloud
docker compose --env-file .env up --build
```

Then run the campaign without local-DB flushing/profiling:

```powershell
cd benchmark
python run_trials.py --skip-flush --compose-dir ..\cloud
```

Note: without flushing, tables grow across trials; keep cloud campaigns short
or clear the managed databases between runs from their consoles.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `health()` fails at campaign start | Stack not up or DB still initialising — check `docker compose` logs, wait 15 s |
| `[warn] pg_stat_statements unavailable` | Stack started from an old compose file — `docker compose up --force-recreate` |
| `py-spy record failed` | Containers lack `SYS_PTRACE` — rebuild/recreate with the current compose file |
| Very low req/s on REST at high concurrency | Expected under saturation — that's the measurement, not a bug |
| `flush ... failed` warnings | DB container name mismatch — run from the correct `--compose-dir` |
