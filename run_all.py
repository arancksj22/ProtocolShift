#!/usr/bin/env python3
"""
ProtocolShift -- one-command runner.
====================================
Does everything needed for a benchmark campaign, in order:

  1. Starts the Docker engine if it isn't running (launches Docker Desktop
     on Windows/macOS and waits for the engine).
  2. Creates the .venv and installs benchmark dependencies (idempotent).
  3. Builds + starts the full docker-compose stack (databases, 6 services,
     Prometheus, Grafana) and waits until every service answers.
  4. Runs the benchmark campaign (quick sanity by default, --full for the
     real 5-trial campaign with profiling).
  5. Runs the statistical analysis (CIs, significance tests, plots,
     migration report) and prints where everything landed.

Uses only the Python standard library itself -- all heavy dependencies live
in the .venv it creates.

Usage (from the repo root, any Python 3.11+):
  python run_all.py                 # quick ~2-minute sanity campaign
  python run_all.py --full          # full campaign + profiling (~1.5-2.5 h)
  python run_all.py --trials 3 --concurrency 50 --payload-bytes 1000
  python run_all.py --stack-only    # just bring everything up, no campaign
  python run_all.py --down          # tear the stack down (keep data)

The stack is left running afterwards so you can explore Grafana at
http://localhost:3000 (admin / protocolshift).
"""

import argparse
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INFRA_DIR = ROOT / "local" / "infrastructure"
BENCH_DIR = ROOT / "benchmark"
RESULTS_DIR = ROOT / "results"
VENV_DIR = ROOT / ".venv"
VENV_PY = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

REST_PORTS = {"postgres": 8001, "mongo": 8002, "redis": 8003}
GRPC_PORTS = {"postgres": 50051, "mongo": 50052, "redis": 50053}

DOCKER_DESKTOP_PATHS = {
    "Windows": [Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")],
    "Darwin": [Path("/Applications/Docker.app")],
}


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def fail(msg: str) -> "NoReturn":  # noqa: F821
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list, cwd: Path = None, check: bool = True, quiet: bool = False) -> int:
    """Run a command, streaming output unless quiet."""
    kwargs = {"cwd": str(cwd) if cwd else None}
    if quiet:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    result = subprocess.run(cmd, **kwargs)
    if check and result.returncode != 0:
        fail(f"Command failed ({result.returncode}): {' '.join(map(str, cmd))}")
    return result.returncode


# ----------------------------------------------
# 1. Docker engine
# ----------------------------------------------
def docker_engine_up() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            ).returncode
            == 0
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def ensure_docker(timeout: int = 240) -> None:
    step("Checking Docker engine")
    if docker_engine_up():
        print("Docker engine is running.")
        return

    system = platform.system()
    launched = False
    for candidate in DOCKER_DESKTOP_PATHS.get(system, []):
        if candidate.exists():
            print(f"Docker engine not running -- launching {candidate.name} ...")
            if system == "Windows":
                os.startfile(str(candidate))  # noqa: S606
            else:
                subprocess.Popen(["open", "-a", str(candidate)])
            launched = True
            break

    if not launched:
        fail(
            "Docker engine is not running and Docker Desktop was not found.\n"
            "Start Docker manually, then re-run this script."
        )

    print(f"Waiting up to {timeout}s for the engine ", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if docker_engine_up():
            print("\nDocker engine is up.")
            return
        print(".", end="", flush=True)
        time.sleep(5)
    fail("Docker engine did not come up in time. Open Docker Desktop and retry.")


# ----------------------------------------------
# 2. Python environment
# ----------------------------------------------
def ensure_venv() -> None:
    step("Preparing Python environment (.venv)")
    if not VENV_PY.exists():
        print("Creating virtual environment ...")
        run([sys.executable, "-m", "venv", str(VENV_DIR)])
    print("Installing/validating benchmark dependencies ...")
    run(
        [str(VENV_PY), "-m", "pip", "install", "--quiet",
         "-r", str(BENCH_DIR / "requirements.txt")],
    )
    print("Environment ready.")


# ----------------------------------------------
# 3. Stack up + readiness
# ----------------------------------------------
def compose_up() -> None:
    step("Building & starting the testbed (docker compose up -d --build)")
    print("First build downloads base images (~200 MB) -- later runs are cached.\n")
    run(["docker", "compose", "up", "-d", "--build"], cwd=INFRA_DIR)


def compose_down() -> None:
    step("Stopping the stack (docker compose down)")
    run(["docker", "compose", "down"], cwd=INFRA_DIR)
    print("Stack stopped. Database volumes kept -- add -v manually to wipe.")


def rest_healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/healthz", timeout=3) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def tcp_open(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=3):
            return True
    except OSError:
        return False


def wait_for_services(timeout: int = 300) -> None:
    step("Waiting for all 6 services to become ready")
    targets = [(f"rest-{b}", p, rest_healthy) for b, p in REST_PORTS.items()]
    targets += [(f"grpc-{b}", p, tcp_open) for b, p in GRPC_PORTS.items()]
    pending = dict((name, (port, probe)) for name, port, probe in targets)

    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        for name in list(pending):
            port, probe = pending[name]
            if probe(port):
                print(f"  [ready] {name} (:{port})")
                del pending[name]
        if pending:
            time.sleep(4)
    if pending:
        fail(
            f"Services not ready after {timeout}s: {', '.join(pending)}.\n"
            "Check logs with:  docker compose logs  (in local/infrastructure)"
        )
    print("All services ready.")


# ----------------------------------------------
# 4 & 5. Campaign + analysis
# ----------------------------------------------
def run_campaign(args: argparse.Namespace) -> None:
    step("Running benchmark campaign")
    cmd = [str(VENV_PY), "run_trials.py",
           "--results-dir", str(RESULTS_DIR)]
    if args.full:
        cmd += ["--profile"]
    else:
        cmd += ["--quick"] if not args.custom else []
    if args.custom:
        if args.trials:
            cmd += ["--trials", str(args.trials)]
        if args.concurrency:
            cmd += ["--concurrency", args.concurrency]
        if args.payload_bytes:
            cmd += ["--payload-bytes", args.payload_bytes]
        if args.duration:
            cmd += ["--duration", str(args.duration)]
        if args.profile:
            cmd += ["--profile"]
    run(cmd, cwd=BENCH_DIR, check=False)  # partial campaigns still analyzable


def newest_run_dir() -> Path:
    runs = sorted(
        (d for d in RESULTS_DIR.iterdir() if d.is_dir() and (d / "raw").exists()),
        key=lambda d: d.name,
    )
    if not runs:
        fail("No results produced -- check the campaign output above.")
    return runs[-1]


def run_analysis(run_dir: Path) -> None:
    step(f"Analyzing {run_dir.name}")
    run([str(VENV_PY), "analyze.py", str(run_dir)], cwd=BENCH_DIR)


# ----------------------------------------------
# Main
# ----------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="ProtocolShift one-command runner (stack + campaign + analysis)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage", 1)[0],
    )
    p.add_argument("--full", action="store_true",
                   help="Full 5-trial campaign with profiling (~1.5-2.5 h). "
                        "Default is a ~2-minute quick sanity campaign.")
    p.add_argument("--stack-only", action="store_true",
                   help="Bring the stack up and stop -- no campaign.")
    p.add_argument("--down", action="store_true",
                   help="Tear the stack down and exit.")
    p.add_argument("--skip-analysis", action="store_true")
    # Custom campaign knobs (any of these switches off --quick)
    p.add_argument("--trials", type=int, default=None)
    p.add_argument("--concurrency", default=None, help="e.g. 10,50,100")
    p.add_argument("--payload-bytes", default=None, help="e.g. 100,1000,10000")
    p.add_argument("--duration", type=float, default=None)
    p.add_argument("--profile", action="store_true",
                   help="Enable docker-stats + pg_stat profiling on a custom campaign")
    args = p.parse_args()
    args.custom = any(
        v is not None for v in (args.trials, args.concurrency, args.payload_bytes, args.duration)
    )
    return args


def main() -> None:
    args = parse_args()

    if args.down:
        ensure_docker()
        compose_down()
        return

    ensure_docker()
    ensure_venv()
    compose_up()
    wait_for_services()

    if args.stack_only:
        step("Stack is up -- no campaign requested")
    else:
        run_campaign(args)
        run_dir = newest_run_dir()
        if not args.skip_analysis:
            run_analysis(run_dir)
        step("Campaign complete")
        print(f"Results   : {run_dir}")
        print(f"Analysis  : {run_dir / 'analysis'}  (summary.md, plots/, migration_decision.md)")

    print("\nStack is still running:")
    print("  Grafana    : http://localhost:3000  (admin / protocolshift)")
    print("  Prometheus : http://localhost:9090")
    print("Tear down with:  python run_all.py --down")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Stack (if started) is still running -- "
              "use 'python run_all.py --down' to stop it.")
        sys.exit(130)
