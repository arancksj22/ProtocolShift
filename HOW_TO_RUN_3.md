# HOW TO RUN 3 — Merge the AWS campaign with the clean Redis rerun

The Redis 10 KB rerun is done and clean. This last step combines it with the
original full AWS campaign into **one paper-ready dataset**, replacing the
crashed Redis cell with the good data.

**Where to run this:** the **client (load-generator) node** — that's the only
machine that still has BOTH campaigns' raw per-request files on disk. (They
were stripped from GitHub to keep the repo small, so this cannot be done from
a fresh clone.) The VM is on, so do it now before it's torn down.

---

## One command

```bash
cd ~/ProtocolShift          # adjust to the repo path
git pull                    # get run_all_3.py
python3 run_all_3.py
```

That's it. The script:
1. Finds the original full campaign and the Redis 10 KB rerun automatically.
2. Builds `results/aws-merged/`, dropping the contaminated dying-Redis trial
   and overlaying the clean rerun.
3. Analyzes the merged set (tables + plots).
4. Prints the exact `git` commands to push — including the original raw data,
   so the merge is reproducible from GitHub afterwards.

Then copy-paste the `git add / commit / push` lines it prints.

**Paper-ready outputs** land in `results/aws-merged/analysis/`:
`summary.md`, `comparisons.csv`, `migration_decision.md`, and `plots/`.

---

## If auto-detection picks the wrong runs

List the runs and pass the ids explicitly:

```bash
for d in results/*/; do echo "$d -> $(ls "$d/raw" 2>/dev/null | wc -l) raw files"; done

python3 run_all_3.py --original <full-campaign-id> --rerun 20260718-170638
```

- **original** = the run with ~250 raw files (the full campaign).
- **rerun** = `20260718-170638` (the 20-file redis-only 10 KB run).

---

## Troubleshooting

| Message | What it means / fix |
|---|---|
| `Largest run ... has only N raw files -- that isn't a full campaign` | The original raw data was deleted on this machine too. If it's truly gone, don't merge — cite the rerun (`20260718-170638`) as a separate clean campaign for the two Redis 10 KB cells in the paper. |
| `Could not auto-detect the Redis 10KB rerun` | Pass `--rerun 20260718-170638` explicitly. |
| `venv python not found` | `python3 -m venv .venv && .venv/bin/pip install -r benchmark/requirements.txt`, then re-run. |
| `analyze.py failed` | Scroll up for the Python error; usually a corrupt/empty raw JSON — check the run folders. |

---

That completes the data side. What remains is paper editing only:
rewrite the PostgreSQL-anomaly section around the reversal, fold in the
corrected OOM write-up, and cite the new clean Redis 10 KB numbers from
`results/aws-merged/analysis/summary.md`.
