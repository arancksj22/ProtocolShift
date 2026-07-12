"""
ProtocolShift — Repeated-Trials Benchmark Runner
=================================================
Runs the full benchmark campaign: every (suite x backend x concurrency x
payload) cell is executed N independent trials, with database state flushed
between trials so each trial starts identically. Raw per-request latencies
are written per trial; analyze.py turns them into confidence intervals,
significance tests, plots, and the migration-threshold report.

Prerequisite: the docker-compose stack must already be up
(cd local/infrastructure && docker compose up --build).

Typical usage (from benchmark/):
  python run_trials.py                              # full default campaign
  python run_trials.py --quick                      # 2-min sanity campaign
  python run_trials.py --profile                    # + docker stats & pg_stat_statements
  python run_trials.py --backends redis --trials 3  # narrower sweep

Cloud mode (managed DBs — no local db containers to flush/profile):
  python run_trials.py --skip-flush --compose-dir ../cloud

Output layout:
  results/<run_id>/
    config.json           campaign parameters
    environment.json      host/docker/dependency capture (reproducibility)
    raw/<cell>_t<k>.json  raw per-request latencies per trial
    profiling/            docker_stats.csv + pgstat_*.json (with --profile)
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import capture_env
import profiling
from loadgen import RunConfig, run_load

BENCH_DIR = Path(__file__).resolve().parent
DEFAULT_COMPOSE_DIR = BENCH_DIR.parent / "local" / "infrastructure"


# ──────────────────────────────────────────────
# Database flush between trials
# ──────────────────────────────────────────────
FLUSH_COMMANDS = {
    "postgres": [
        "exec", "-T", "postgres",
        "psql", "-U", "postgres", "-d", "benchmarkdb", "-c",
        "TRUNCATE benchmark_records RESTART IDENTITY;",
    ],
    "mongo": [
        "exec", "-T", "mongo",
        "mongosh", "benchmarkdb", "--quiet", "--eval",
        "db.benchmark_records.deleteMany({}); db.benchmark_counters.deleteMany({});",
    ],
    "redis": [
        "exec", "-T", "redis",
        "redis-cli", "FLUSHDB",
    ],
}


def flush_backend(backend: str, compose_dir: str) -> bool:
    cmd = ["docker", "compose", *FLUSH_COMMANDS[backend]]
    result = subprocess.run(
        cmd, cwd=compose_dir, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print(f"  [warn] flush {backend} failed: {result.stderr.strip()[:200]}")
        return False
    return True


# ──────────────────────────────────────────────
# Campaign
# ──────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ProtocolShift repeated-trials runner")
    p.add_argument("--suites", default="rest,grpc")
    p.add_argument("--backends", default="postgres,mongo,redis")
    p.add_argument("--concurrency", default="10,50,100")
    p.add_argument("--payload-bytes", default="100,1000,10000")
    p.add_argument("--trials", type=int, default=5)
    p.add_argument("--duration", type=float, default=15.0)
    p.add_argument("--warmup", type=float, default=3.0)
    p.add_argument("--op", choices=["create", "read", "mixed"], default="mixed")
    p.add_argument("--host", default="localhost")
    p.add_argument("--results-dir", default=str(BENCH_DIR.parent / "results"))
    p.add_argument("--compose-dir", default=str(DEFAULT_COMPOSE_DIR))
    p.add_argument("--skip-flush", action="store_true",
                   help="Do not flush databases between trials (use for cloud backends)")
    p.add_argument("--profile", action="store_true",
                   help="Capture docker stats + pg_stat_statements per trial")
    p.add_argument("--quick", action="store_true",
                   help="Tiny sanity campaign: 1 trial, 5s runs, c=10, payload=100")
    return p.parse_args()


def cell_name(suite: str, backend: str, conc: int, payload: int) -> str:
    return f"{suite}-{backend}_c{conc}_p{payload}"


def main() -> int:
    args = parse_args()
    if args.quick:
        args.trials = 1
        args.duration = 5.0
        args.warmup = 1.0
        args.concurrency = "10"
        args.payload_bytes = "100"

    suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    concurrencies = [int(c) for c in args.concurrency.split(",")]
    payloads = [int(x) for x in args.payload_bytes.split(",")]

    run_id = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.results_dir) / run_id
    raw_dir = out_dir / "raw"
    prof_dir = out_dir / "profiling"
    raw_dir.mkdir(parents=True, exist_ok=True)

    campaign = {
        "run_id": run_id,
        "suites": suites,
        "backends": backends,
        "concurrency": concurrencies,
        "payload_bytes": payloads,
        "trials": args.trials,
        "duration_s": args.duration,
        "warmup_s": args.warmup,
        "op": args.op,
        "host": args.host,
        "flush_between_trials": not args.skip_flush,
        "profiling": args.profile,
    }
    with open(out_dir / "config.json", "w") as f:
        json.dump(campaign, f, indent=2)

    print(f"Run id      : {run_id}")
    print(f"Results dir : {out_dir}")
    print("Capturing environment...")
    with open(out_dir / "environment.json", "w") as f:
        json.dump(capture_env.capture(args.compose_dir), f, indent=2)

    pg_profiling = False
    if args.profile:
        prof_dir.mkdir(parents=True, exist_ok=True)
        if "postgres" in backends:
            pg_profiling = profiling.pg_stat_available(args.compose_dir)
            if not pg_profiling:
                print("  [warn] pg_stat_statements unavailable — was the stack "
                      "started with the updated docker-compose.yml? "
                      "Continuing with docker stats only.")

    total_runs = len(suites) * len(backends) * len(concurrencies) * len(payloads) * args.trials
    est = total_runs * (args.duration + args.warmup + 5)
    print(f"Campaign    : {total_runs} runs, rough estimate {est/60:.0f} min\n")

    done = 0
    failures = 0
    try:
        for backend in backends:
            for payload in payloads:
                for conc in concurrencies:
                    for suite in suites:
                        for trial in range(1, args.trials + 1):
                            done += 1
                            name = cell_name(suite, backend, conc, payload)
                            tag = f"[{done}/{total_runs}] {name} trial {trial}"

                            if not args.skip_flush:
                                flush_backend(backend, args.compose_dir)
                            if pg_profiling and backend == "postgres":
                                profiling.pg_stat_reset(args.compose_dir)

                            sampler = None
                            if args.profile:
                                sampler = profiling.DockerStatsSampler(
                                    label=f"{name}_t{trial}"
                                )
                                sampler.start()

                            cfg = RunConfig(
                                suite=suite,
                                backend=backend,
                                concurrency=conc,
                                payload_bytes=payload,
                                duration=args.duration,
                                warmup=args.warmup,
                                op=args.op,
                                host=args.host,
                            )
                            try:
                                result = asyncio.run(run_load(cfg))
                            except Exception as exc:
                                failures += 1
                                print(f"{tag}  FAILED: {exc}")
                                if sampler:
                                    sampler.stop()
                                continue
                            finally:
                                if sampler:
                                    sampler.stop()
                                    sampler.join(timeout=30)
                                    sampler.append_csv(prof_dir / "docker_stats.csv")

                            if pg_profiling and backend == "postgres":
                                try:
                                    snap = profiling.pg_stat_snapshot(args.compose_dir)
                                    with open(
                                        prof_dir / f"pgstat_{name}_t{trial}.json", "w"
                                    ) as f:
                                        json.dump(snap, f, indent=2)
                                except RuntimeError as exc:
                                    print(f"  [warn] pg_stat snapshot failed: {exc}")

                            with open(raw_dir / f"{name}_t{trial}.json", "w") as f:
                                json.dump(result.to_dict(), f)

                            n = len(result.latencies_ms)
                            lat = sorted(result.latencies_ms)
                            p99 = lat[int(0.99 * (n - 1))] if n else float("nan")
                            print(
                                f"{tag}  {n} reqs, {result.errors} err, "
                                f"{result.achieved_rps:.0f} req/s, p99={p99:.1f}ms"
                            )
    except KeyboardInterrupt:
        print("\nInterrupted — partial results kept.")

    print(f"\nDone: {done - failures} succeeded, {failures} failed.")
    print(f"Raw results in {raw_dir}")
    print(f"Next: python analyze.py {out_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
