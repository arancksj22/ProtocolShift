#!/usr/bin/env python3
"""
ProtocolShift -- one-command runner for the Redis-gap rerun (HOW_TO_RUN_2.md).

Three modes, one per machine/role:

  ON THE AWS SERVER NODE (the EC2 machine running the Docker stack):
    python3 run_all_2.py server
      -> captures the Redis OOM crash evidence to results/oom_evidence.txt
      -> restarts the stack with database ports published (for direct flushing)
      -> prints the security-group rule you must add and this node's IP

  ON THE AWS CLIENT NODE (the load-generator machine):
    python3 run_all_2.py client --server-ip <SERVER_PRIVATE_IP>
      -> creates/updates the .venv and dependencies
      -> reruns ONLY the lost Redis 10KB cells (c=50,100 x 5 trials,
         ~10 minutes) with per-trial direct flushing so Redis cannot OOM again
      -> analyzes the rerun, auto-merges it with the original AWS campaign
         (replacing the contaminated dying-Redis trial), analyzes the merge
      -> prints the exact git commands to push everything

  ON A LOCAL MACHINE WITH DOCKER DESKTOP (Part D -- postgres profiling):
    python run_all_2.py local-profile
      -> full stack up (via run_all.py), then a postgres-only campaign with
         pg_stat_statements + docker-stats profiling, then analysis. This
         fills the "Amdahl gate: not measured" column in the migration report.

Stdlib-only, like run_all.py. Works on Ubuntu and Windows.
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INFRA_DIR = ROOT / "local" / "infrastructure"
BENCH_DIR = ROOT / "benchmark"
RESULTS_DIR = ROOT / "results"
VENV_DIR = ROOT / ".venv"
VENV_PY = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

# The trial that measured a dying Redis (p99 ~4.3s) -- must not survive a merge
CONTAMINATED_GLOB = "grpc-redis_c50_p10000_t*.json"


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def fail(msg: str) -> None:
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list, cwd: Path = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if check and result.returncode != 0:
        fail(f"Command failed ({result.returncode}): {' '.join(map(str, cmd))}")
    return result


def capture(cmd: list, cwd: Path = None) -> str:
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=60)
        return r.stdout.strip() if r.returncode == 0 else f"<failed: {r.stderr.strip()[:200]}>"
    except Exception as exc:
        return f"<unavailable: {exc}>"


def ensure_venv() -> None:
    step("Preparing Python environment (.venv)")
    if not VENV_PY.exists():
        run([sys.executable, "-m", "venv", str(VENV_DIR)])
    run([str(VENV_PY), "-m", "pip", "install", "--quiet", "--default-timeout=100",
         "-r", str(BENCH_DIR / "requirements.txt")])
    print("Environment ready.")


def private_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "<could not detect>"


# ──────────────────────────────────────────────
# server mode
# ──────────────────────────────────────────────
def mode_server() -> None:
    step("1/3 Capturing Redis OOM crash evidence")
    RESULTS_DIR.mkdir(exist_ok=True)
    evidence = []

    cid = capture(["docker", "compose", "ps", "-q", "redis"], cwd=INFRA_DIR)
    if cid and not cid.startswith("<"):
        evidence.append("--- docker inspect (redis container) ---")
        evidence.append(capture(
            ["docker", "inspect", cid, "--format",
             "OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}} "
             "FinishedAt={{.State.FinishedAt}} StartedAt={{.State.StartedAt}}"]
        ))
    else:
        evidence.append(f"redis container not found via docker compose ps: {cid}")

    evidence.append("\n--- dmesg OOM lines (kernel log) ---")
    dmesg = capture(["sudo", "dmesg", "-T"])
    if dmesg.startswith("<"):
        dmesg = capture(["dmesg", "-T"])  # some systems allow unprivileged dmesg
    if dmesg.startswith("<"):
        evidence.append(f"dmesg unavailable ({dmesg}) -- run manually: sudo dmesg -T | grep -i oom")
    else:
        oom_lines = [ln for ln in dmesg.splitlines()
                     if "oom" in ln.lower() or "out of memory" in ln.lower()]
        evidence.append("\n".join(oom_lines[-30:]) if oom_lines
                        else "(no OOM lines found in current kernel log -- the "
                             "instance may have rebooted since the crash)")

    out = RESULTS_DIR / "oom_evidence.txt"
    out.write_text("\n".join(evidence) + "\n", encoding="utf-8")
    print("\n".join(evidence))
    print(f"\nSaved to {out}")

    step("2/3 Restarting stack with database ports published")
    run(["docker", "compose",
         "-f", "docker-compose.yml", "-f", "docker-compose.expose-dbs.yml",
         "up", "-d", "--build"], cwd=INFRA_DIR)

    step("3/3 Manual step: AWS security group")
    ip = private_ip()
    print(f"This node's private IP: {ip}")
    print("In the EC2 console, allow inbound TCP 6379 on THIS node's security")
    print("group, source = the CLIENT node's private IP only (e.g. 172.31.x.x/32).")
    print("\nThen, on the CLIENT node run:")
    print(f"  python3 run_all_2.py client --server-ip {ip}")


# ──────────────────────────────────────────────
# client mode
# ──────────────────────────────────────────────
def find_original_run(exclude: Path = None) -> Path:
    """The original full AWS campaign = the run dir with the most raw files."""
    best, best_n = None, 0
    if not RESULTS_DIR.exists():
        return None
    for d in RESULTS_DIR.iterdir():
        if not d.is_dir() or d == exclude or d.name == "aws-merged":
            continue
        n = len(list((d / "raw").glob("*.json"))) if (d / "raw").exists() else 0
        if n > best_n:
            best, best_n = d, n
    return best if best_n >= 40 else None  # a full campaign has 250+ files


def newest_run_dir() -> Path:
    runs = sorted(
        (d for d in RESULTS_DIR.iterdir() if d.is_dir() and (d / "raw").exists()),
        key=lambda d: d.name,
    )
    return runs[-1] if runs else None


def mode_client(server_ip: str) -> None:
    ensure_venv()

    original = find_original_run()
    if original:
        print(f"Original campaign found: {original.name} "
              f"({len(list((original / 'raw').glob('*.json')))} raw files)")
    else:
        print("[note] No original campaign with raw/ found under results/ -- "
              "the rerun will still work, but there will be nothing to merge.")

    step("Rerunning the lost Redis 10KB cells (~10 min)")
    run([str(VENV_PY), "run_trials.py",
         "--backends", "redis", "--concurrency", "50,100",
         "--payload-bytes", "10000", "--trials", "5",
         "--host", server_ip, "--flush-mode", "direct",
         "--results-dir", str(RESULTS_DIR)], cwd=BENCH_DIR)

    new_run = newest_run_dir()
    if new_run is None:
        fail("Rerun produced no results -- check the output above.")
    n_new = len(list((new_run / "raw").glob("*.json")))
    if n_new < 20:
        print(f"[warn] Only {n_new}/20 trials completed. Check for flush "
              "warnings above (port 6379 reachable? security group?). "
              "Continuing with what we have.")

    step(f"Analyzing rerun {new_run.name}")
    run([str(VENV_PY), "analyze.py", str(new_run)], cwd=BENCH_DIR)

    merged = None
    if original:
        step("Merging rerun with the original campaign")
        merged = RESULTS_DIR / "aws-merged"
        if merged.exists():
            shutil.rmtree(merged)
        (merged / "raw").mkdir(parents=True)
        for f in (original / "raw").glob("*.json"):
            shutil.copy2(f, merged / "raw" / f.name)
        # Drop the trial that measured a dying Redis; the rerun's files
        # (copied next, same names) are the clean replacements.
        for f in (merged / "raw").glob(CONTAMINATED_GLOB):
            f.unlink()
        for f in (new_run / "raw").glob("*.json"):
            shutil.copy2(f, merged / "raw" / f.name)
        if (original / "config.json").exists():
            shutil.copy2(original / "config.json", merged / "config.json")

        step("Analyzing merged campaign")
        run([str(VENV_PY), "analyze.py", str(merged)], cwd=BENCH_DIR)

    step("Done -- now push to GitHub")
    print("Run these commands:\n")
    print("  git add results/oom_evidence.txt   # if Part A created it on this repo copy")
    print(f"  git add results/{new_run.name}")
    if merged:
        print("  git add results/aws-merged")
    print('  git commit -m "redis 10KB rerun with per-trial flushing + merged AWS analysis"')
    print("  git push")
    if merged:
        print(f"\nPaper-ready outputs: results/aws-merged/analysis/ "
              "(summary.md, comparisons.csv, migration_decision.md, plots/)")


# ──────────────────────────────────────────────
# local-profile mode (Part D)
# ──────────────────────────────────────────────
def mode_local_profile() -> None:
    import run_all  # reuse the stack bootstrap from run_all.py

    run_all.ensure_docker()
    run_all.ensure_venv()
    run_all.compose_up()
    run_all.wait_for_services()

    step("Postgres-only profiled campaign (~25 min)")
    run([str(VENV_PY), "run_trials.py",
         "--backends", "postgres", "--profile",
         "--results-dir", str(RESULTS_DIR)], cwd=BENCH_DIR)

    new_run = newest_run_dir()
    if new_run is None:
        fail("Campaign produced no results -- check the output above.")

    step(f"Analyzing {new_run.name}")
    run([str(VENV_PY), "analyze.py", str(new_run)], cwd=BENCH_DIR)

    step("Done")
    print(f"Results: {new_run}")
    print(f"Amdahl-gate evidence: {new_run / 'analysis' / 'migration_decision.md'}")
    print(f"Per-query DB times  : {new_run / 'profiling'} (pgstat_*.json)")
    print("Commit the run directory to git to share it.")


# ──────────────────────────────────────────────
# main
# ──────────────────────────────────────────────
def main() -> None:
    p = argparse.ArgumentParser(
        description="Redis-gap rerun runner (see HOW_TO_RUN_2.md)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="mode", required=True)
    sub.add_parser("server", help="AWS server node: OOM evidence + expose DB ports")
    pc = sub.add_parser("client", help="AWS client node: rerun redis 10KB cells + merge")
    pc.add_argument("--server-ip", required=True,
                    help="Private IP of the server node (printed by 'server' mode)")
    sub.add_parser("local-profile",
                   help="Local machine: postgres campaign with pg_stat profiling")
    args = p.parse_args()

    if args.mode == "server":
        mode_server()
    elif args.mode == "client":
        mode_client(args.server_ip)
    else:
        mode_local_profile()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
