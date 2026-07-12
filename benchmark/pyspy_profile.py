"""
Flame-graph profiling of a running service container via py-spy.

Answers "where does the CPU go — serialization, driver, or event loop?" for
the PostgreSQL-anomaly investigation. Attaches py-spy to the service's main
process (PID 1) inside the container and records a flame graph SVG.

Requires the containers to run with cap_add: SYS_PTRACE (the repo's
docker-compose files grant this to all six app services).

Typical session (two terminals):
  T1:  python pyspy_profile.py --service grpc-postgres --duration 30
  T2:  python loadgen.py --suite grpc --backend postgres --concurrency 100 \
           --duration 40 --warmup 2

Then compare against the REST equivalent:
  T1:  python pyspy_profile.py --service rest-postgres --duration 30
  T2:  python loadgen.py --suite rest --backend postgres --concurrency 100 ...
"""

import argparse
import subprocess
import sys
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
DEFAULT_COMPOSE_DIR = BENCH_DIR.parent / "local" / "infrastructure"

SERVICES = [
    "rest-postgres", "rest-mongo", "rest-redis",
    "grpc-postgres", "grpc-mongo", "grpc-redis",
]


def compose(compose_dir: str, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=compose_dir, text=True, timeout=timeout,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="py-spy flame graph for a service container")
    p.add_argument("--service", required=True, choices=SERVICES)
    p.add_argument("--duration", type=int, default=30, help="Sampling seconds (default 30)")
    p.add_argument("--out", default=None, help="Output SVG path (default flame_<service>.svg)")
    p.add_argument("--compose-dir", default=str(DEFAULT_COMPOSE_DIR))
    args = p.parse_args()

    out = args.out or str(BENCH_DIR / f"flame_{args.service}.svg")

    print(f"[1/3] Installing py-spy inside {args.service} (one-time per container)...")
    r = compose(args.compose_dir, "exec", "-T", args.service,
                "pip", "install", "--quiet", "py-spy")
    if r.returncode != 0:
        print("pip install py-spy failed — is the stack up?")
        return 1

    print(f"[2/3] Recording {args.duration}s flame graph — generate load NOW "
          f"(e.g. loadgen.py against {args.service})...")
    r = compose(
        args.compose_dir, "exec", "-T", args.service,
        "py-spy", "record", "-o", "/tmp/flame.svg",
        "-d", str(args.duration), "--pid", "1", "--subprocesses",
        timeout=args.duration + 120,
    )
    if r.returncode != 0:
        print("py-spy record failed. Check that the compose file grants "
              "cap_add: SYS_PTRACE and the stack was restarted after pulling it.")
        return 1

    print("[3/3] Copying flame graph out of the container...")
    r = compose(args.compose_dir, "cp", f"{args.service}:/tmp/flame.svg", out)
    if r.returncode != 0:
        return 1
    print(f"Done → {out}  (open in a browser; widths = CPU time share)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
