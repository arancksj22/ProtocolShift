#!/usr/bin/env python3
"""
ProtocolShift -- one-command MERGE (HOW_TO_RUN_3.md).

Run this on the CLIENT (load-generator) node, where BOTH campaigns' raw
per-request files still exist on disk. It merges the original full AWS
campaign with the clean Redis 10KB rerun into one paper-ready dataset:

  python3 run_all_3.py

What it does (no benchmarking, no reruns -- just merging & analysis):
  1. Auto-detects the original full campaign (the run with the most raw
     files) and the Redis 10KB rerun (backends=redis, payload=10000).
  2. Copies the original raw files into results/aws-merged/, DROPS the
     contaminated dying-Redis cell (grpc-redis_c50_p10000_t*), and overlays
     the clean rerun files.
  3. Runs analyze.py on the merged set.
  4. Prints the exact git commands to push everything (including raw data).

Override auto-detection if needed:
  python3 run_all_3.py --original <run_id> --rerun <run_id>

Stdlib-only. Works on Ubuntu and Windows.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BENCH_DIR = ROOT / "benchmark"
RESULTS_DIR = ROOT / "results"
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

MERGED_NAME = "aws-merged"
CONTAMINATED_GLOB = "grpc-redis_c50_p10000_t*.json"


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def fail(msg: str) -> None:
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def raw_count(run_dir: Path) -> int:
    raw = run_dir / "raw"
    return len(list(raw.glob("*.json"))) if raw.exists() else 0


def all_runs() -> list:
    if not RESULTS_DIR.exists():
        return []
    return sorted(
        d for d in RESULTS_DIR.iterdir()
        if d.is_dir() and d.name != MERGED_NAME and (d / "raw").exists()
    )


def looks_like_redis_rerun(run_dir: Path) -> bool:
    """True if this run is a redis-only 10KB rerun (by config or by files)."""
    cfg = run_dir / "config.json"
    if cfg.exists():
        try:
            c = json.loads(cfg.read_text())
            if c.get("backends") == ["redis"] and 10000 in (c.get("payload_bytes") or []):
                return True
        except (json.JSONDecodeError, OSError):
            pass
    files = list((run_dir / "raw").glob("*.json"))
    return bool(files) and all("redis" in f.name and "p10000" in f.name for f in files)


def detect_runs(args) -> tuple:
    runs = all_runs()
    if not runs:
        fail(f"No result runs with raw/ found under {RESULTS_DIR}. "
             "Run this on the client node where the campaigns were executed.")

    if args.original:
        original = RESULTS_DIR / args.original
        if not (original / "raw").exists():
            fail(f"--original {args.original} has no raw/ folder.")
    else:
        # The original full campaign = the run with the most raw files.
        original = max(runs, key=raw_count)
        if raw_count(original) < 40:
            fail(f"Largest run ({original.name}) has only {raw_count(original)} raw "
                 "files -- that isn't a full campaign. The original raw data may have "
                 "been deleted on this machine. Pass --original explicitly, or if it's "
                 "truly gone, cite the rerun as a separate campaign instead of merging.")

    if args.rerun:
        rerun = RESULTS_DIR / args.rerun
        if not (rerun / "raw").exists():
            fail(f"--rerun {args.rerun} has no raw/ folder.")
    else:
        candidates = [r for r in runs if r != original and looks_like_redis_rerun(r)]
        if not candidates:
            fail("Could not auto-detect the Redis 10KB rerun. Pass --rerun <run_id>. "
                 f"Available runs: {', '.join(r.name for r in runs)}")
        rerun = max(candidates, key=lambda r: r.name)  # newest by timestamp id
    return original, rerun


def main() -> None:
    p = argparse.ArgumentParser(
        description="Merge the original AWS campaign with the clean Redis rerun",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--original", help="Run id of the full campaign (auto-detected if omitted)")
    p.add_argument("--rerun", help="Run id of the redis 10KB rerun (auto-detected if omitted)")
    args = p.parse_args()

    if not VENV_PY.exists():
        fail(f"venv python not found at {VENV_PY}. Create it first:\n"
             "  python3 -m venv .venv && .venv/bin/pip install -r benchmark/requirements.txt")

    original, rerun = detect_runs(args)
    step("Detected inputs")
    print(f"Original campaign : {original.name}  ({raw_count(original)} raw files)")
    print(f"Redis 10KB rerun  : {rerun.name}  ({raw_count(rerun)} raw files)")

    merged = RESULTS_DIR / MERGED_NAME
    step(f"Building {MERGED_NAME}/")
    if merged.exists():
        print("Existing aws-merged/ removed and rebuilt.")
        shutil.rmtree(merged)
    (merged / "raw").mkdir(parents=True)

    for f in (original / "raw").glob("*.json"):
        shutil.copy2(f, merged / "raw" / f.name)
    dropped = 0
    for f in (merged / "raw").glob(CONTAMINATED_GLOB):
        f.unlink()
        dropped += 1
    print(f"Copied {raw_count(original)} original files; dropped {dropped} "
          "contaminated dying-Redis trial(s).")
    added = 0
    for f in (rerun / "raw").glob("*.json"):
        shutil.copy2(f, merged / "raw" / f.name)
        added += 1
    print(f"Overlaid {added} clean rerun files.")
    print(f"Merged dataset: {raw_count(merged)} raw files.")

    if (original / "config.json").exists():
        shutil.copy2(original / "config.json", merged / "config.json")

    step(f"Analyzing {MERGED_NAME}/")
    r = subprocess.run([str(VENV_PY), "analyze.py", str(merged)], cwd=str(BENCH_DIR))
    if r.returncode != 0:
        fail("analyze.py failed -- see output above.")

    step("Done -- push to GitHub")
    print("Copy-paste:\n")
    print(f"  git add results/{MERGED_NAME} results/{rerun.name} results/{original.name}/raw")
    if (RESULTS_DIR / "oom_evidence.txt").exists():
        print("  git add results/oom_evidence.txt")
    print('  git commit -m "merge AWS campaign with clean redis 10KB rerun; restore raw data"')
    print("  git push")
    print(f"\nPaper-ready outputs: results/{MERGED_NAME}/analysis/")
    print("  summary.md, comparisons.csv, migration_decision.md, plots/")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
