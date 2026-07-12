"""
Profiling helpers for the PostgreSQL-anomaly investigation.

Two independent instruments, both orchestrated by run_trials.py --profile:

1. DockerStatsSampler — background thread sampling `docker stats` for every
   container (CPU %, memory, network I/O) during a load run. Answers the
   "CPU, memory, and network utilization profiling" requirement.

2. pg_stat_statements — per-query execution-time accounting inside
   PostgreSQL. Requires the extension to be preloaded (the local
   docker-compose starts postgres with shared_preload_libraries set).
   Snapshot deltas separate database execution time from protocol /
   serialization time, which is the key to explaining the gRPC-vs-REST
   PostgreSQL anomaly.

Both talk to containers through `docker compose exec`, so no database ports
need to be published on the host.
"""

import csv
import json
import re
import subprocess
import threading
import time
from pathlib import Path


def _compose(compose_dir: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=compose_dir,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ──────────────────────────────────────────────
# docker stats sampler
# ──────────────────────────────────────────────
class DockerStatsSampler(threading.Thread):
    """Samples `docker stats --no-stream` in a loop until stop() is called.

    Each sample of `docker stats` takes ~1-2 s to collect, which sets the
    effective sampling resolution.
    """

    def __init__(self, label: str = "") -> None:
        super().__init__(daemon=True)
        self.label = label
        self.rows: list = []
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            try:
                out = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except Exception:
                continue
            for line in out.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    s = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.rows.append(
                    {
                        "timestamp": ts,
                        "label": self.label,
                        "container": s.get("Name", ""),
                        "cpu_percent": s.get("CPUPerc", ""),
                        "mem_usage": s.get("MemUsage", ""),
                        "mem_percent": s.get("MemPerc", ""),
                        "net_io": s.get("NetIO", ""),
                        "block_io": s.get("BlockIO", ""),
                    }
                )

    def stop(self) -> None:
        self._stop.set()

    def append_csv(self, path: Path) -> None:
        if not self.rows:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(self.rows[0].keys()))
            if write_header:
                writer.writeheader()
            writer.writerows(self.rows)


# ──────────────────────────────────────────────
# pg_stat_statements
# ──────────────────────────────────────────────
def _psql(compose_dir: str, sql: str) -> str:
    result = _compose(
        compose_dir,
        "exec", "-T", "postgres",
        "psql", "-U", "postgres", "-d", "benchmarkdb", "-tA", "-c", sql,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def pg_stat_available(compose_dir: str) -> bool:
    """True if pg_stat_statements can be enabled (extension preloaded)."""
    try:
        _psql(compose_dir, "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;")
        return True
    except RuntimeError:
        return False


def pg_stat_reset(compose_dir: str) -> None:
    _psql(compose_dir, "SELECT pg_stat_statements_reset();")


def pg_stat_snapshot(compose_dir: str, limit: int = 25) -> list:
    """Top statements by total execution time since the last reset."""
    raw = _psql(
        compose_dir,
        "SELECT COALESCE(json_agg(t), '[]'::json) FROM ("
        "  SELECT query, calls, "
        "         round(total_exec_time::numeric, 3) AS total_exec_ms, "
        "         round(mean_exec_time::numeric, 4) AS mean_exec_ms, "
        "         rows "
        "  FROM pg_stat_statements "
        "  WHERE query NOT ILIKE '%pg_stat_statements%' "
        "  ORDER BY total_exec_time DESC "
        f"  LIMIT {int(limit)}"
        ") t;",
    )
    return json.loads(raw) if raw else []


def db_exec_time_ms(snapshot: list) -> float:
    """Total DB execution time (ms) across the snapshot's statements,
    counting only the benchmark table's queries."""
    total = 0.0
    for row in snapshot:
        if re.search(r"benchmark_records", row.get("query", ""), re.IGNORECASE):
            total += float(row.get("total_exec_ms", 0.0))
    return total
