# HOW TO RUN 2 — Fixing the Redis Gap (AWS rerun guide)

This guide is for re-running **only the Redis 10KB cells** that were lost when
Redis crashed (OOM) during the first AWS campaign, plus collecting the crash
evidence and the missing PostgreSQL profiling data. Everything is copy-paste.

**What went wrong last time:** the campaign ran with `--skip-flush`, so data
piled up in Redis across every trial until the 4 GB server ran out of memory
during the 10KB sweeps. The harness now supports `--flush-mode direct`, which
wipes the database between trials **over the network** — so the two-node setup
works without accumulating data, and this can't happen again.

**Before starting, on BOTH machines:** `git pull` in the repo.

---

## TL;DR — one command per machine

Everything below is automated by `run_all_2.py`. If you just want it done:

```bash
# 1. On the SERVER node (the EC2 machine running Docker):
python3 run_all_2.py server
#    -> captures OOM evidence, restarts the stack with DB ports published,
#       prints this node's IP and the security-group rule to add.
#    Then do the ONE manual step it prints: allow TCP 6379 from the client's
#    private IP in the server's security group (EC2 console).

# 2. On the CLIENT node (the load-generator machine):
python3 run_all_2.py client --server-ip <IP printed by step 1>
#    -> installs deps, reruns the lost Redis cells (~10 min), analyzes,
#       auto-merges with the original campaign (dropping the contaminated
#       trial), and prints the exact git commands to push.

# 3. On a LOCAL machine with Docker Desktop (Part D, postgres profiling):
python run_all_2.py local-profile
```

The sections below explain the same steps manually — read them if a command
fails or you want to know what's happening.

---

## Part A — On the SERVER node (the EC2 machine running Docker)

### A1. Grab the crash evidence (2 minutes, do this FIRST)

This proves the OOM story for the paper. Run these and save the output:

```bash
cd ~/ProtocolShift/local/infrastructure     # adjust path to where the repo is

# Was the redis container killed by the kernel OOM killer?
docker inspect $(docker compose ps -q redis) --format 'OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}} FinishedAt={{.State.FinishedAt}}'

# Kernel log evidence (works even after the container restarted)
sudo dmesg -T | grep -i -E "oom|out of memory" | tail -20

# Save both outputs to a file and commit it later:
{
  docker inspect $(docker compose ps -q redis) --format 'OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}} FinishedAt={{.State.FinishedAt}}'
  sudo dmesg -T | grep -i -E "oom|out of memory" | tail -20
} > ~/ProtocolShift/results/oom_evidence.txt 2>&1
```

> If the container/instance was recreated since the crash, `OOMKilled` may show
> `false` — the `dmesg` lines are the stronger evidence. If both are empty,
> tell the team; we'll soften the wording in the paper instead.

### A2. Restart the stack with database ports published

The client node needs to reach Redis directly to flush it between trials:

```bash
cd ~/ProtocolShift/local/infrastructure
docker compose -f docker-compose.yml -f docker-compose.expose-dbs.yml up -d --build
```

### A3. Open the ports in the AWS Security Group

In the EC2 console, edit the **server's** security group: allow inbound TCP
**6379** (Redis) — and optionally 5432/27017 for future reruns — **only from
the client node's private IP** (e.g. `172.31.x.x/32`). Never from 0.0.0.0/0.

---

## Part B — On the CLIENT node (the load-generator machine)

### B1. Update dependencies (the harness gained new ones)

```bash
cd ~/ProtocolShift
source .venv/bin/activate            # or: python3 -m venv .venv && source .venv/bin/activate
pip install -r benchmark/requirements.txt
```

### B2. Run the Redis rerun (~10 minutes)

Replace `<SERVER_IP>` with the server's **private** IP (same as last time):

```bash
cd benchmark
python run_trials.py --backends redis --concurrency 50,100 --payload-bytes 10000 \
    --trials 5 --host <SERVER_IP> --flush-mode direct
```

You should see `[1/20] ... [20/20]` lines, each reporting requests and a p99.
**No `[warn] direct flush redis failed` lines should appear** — if they do,
stop and check A2/A3.

### B3. Analyze the rerun on its own

```bash
python analyze.py ../results/<new_run_id>       # the id printed at the start of B2
```

### B4. (Recommended) Merge with the original AWS run for one combined report

The original run folder still exists on this machine (`results/20260718-071036`
or similar — the one with `raw/` in it). Merge like this:

```bash
cd ~/ProtocolShift
mkdir -p results/aws-merged/raw
cp results/<ORIGINAL_RUN_ID>/raw/*.json  results/aws-merged/raw/
cp results/<NEW_RUN_ID>/raw/*.json       results/aws-merged/raw/

# IMPORTANT: delete the contaminated dying-Redis trial from the merge —
# it measured a crashing database, not the protocol:
rm results/aws-merged/raw/grpc-redis_c50_p10000_t1.json

cp results/<ORIGINAL_RUN_ID>/config.json results/aws-merged/  # campaign metadata
cd benchmark && python analyze.py ../results/aws-merged
```

`results/aws-merged/analysis/` now has the complete paper-ready tables and
plots for all 54 cells.

---

## Part C — Push everything to GitHub

This time include the raw data and environment capture (reviewers need them):

```bash
cd ~/ProtocolShift
git add results/oom_evidence.txt
git add results/<NEW_RUN_ID>          # includes raw/ + environment.json
git add results/aws-merged
git commit -m "redis 10KB rerun with per-trial flushing + OOM evidence + merged AWS analysis"
git push
```

---

## Part D — PostgreSQL profiling run (whoever has the local machine)

This fills the "Amdahl gate: not measured" column in the migration report —
it needs the databases local to the harness, so run it on a laptop/desktop
with Docker Desktop, **not** on the two-node AWS setup:

```powershell
cd ProtocolShift
python run_all.py --stack-only                  # brings the stack up
.\.venv\Scripts\Activate.ps1
cd benchmark
python run_trials.py --backends postgres --profile
python analyze.py ..\results\<run_id>
```

The resulting `migration_decision.md` will show real DB-share percentages for
postgres, and `profiling/pgstat_*.json` contains the per-query execution times
for the paper's protocol-vs-database time breakdown.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `direct flush redis failed: ConnectionError` | Port 6379 not reachable: redo A2 (override file) and A3 (security group), confirm `<SERVER_IP>` is the private IP |
| `health()` fails at campaign start | Services not up on the server — on the server: `docker compose ps` and check all 6 are running |
| `pip install` fails on the client | Use Python 3.11+; on Ubuntu: `sudo apt install python3-venv` first |
| Redis crashes again | It shouldn't (flushing now keeps memory flat), but if it does: on the server `docker compose restart redis`, then re-run B2 — completed trials are kept per-file |
| Old `--skip-flush` scripts | Still works — it now maps to `--flush-mode none`. Don't use it for reruns. |
