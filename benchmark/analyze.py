"""
ProtocolShift — Statistical Analysis & Reporting
=================================================
Consumes a results/<run_id>/ directory produced by run_trials.py and emits:

  analysis/summary.csv            per-cell metrics: per-trial mean/p50/p95/p99
                                  aggregated across trials with 95% CIs and CoV
  analysis/comparisons.csv        REST-vs-gRPC per cell: p99 delta %, Mann-
                                  Whitney U p-value on pooled latencies,
                                  CI-overlap verdict
  analysis/summary.md             the same, as paper-ready markdown tables
  analysis/migration_decision.md  the formal Phase-Gate decision model applied
                                  to the measured data (with DB-time share
                                  from pg_stat_statements when available)
  analysis/plots/*.png            p99-vs-concurrency lines, latency CDFs,
                                  p99 bars with CI error bars

Statistics notes:
  * CIs are Student-t intervals across trial-level statistics (each trial is
    one independent sample; per-request latencies within a trial are
    autocorrelated so they are not treated as i.i.d. for CIs).
  * Mann-Whitney U runs on pooled per-request latencies (REST vs gRPC per
    cell); latency distributions are heavy-tailed, so a rank test is
    appropriate where a t-test is not.

Usage:
  python analyze.py ../results/<run_id> [--min-improvement 20] [--max-db-share 60]
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats as sps

# ──────────────────────────────────────────────
# Chart style (dataviz reference palette, light mode)
# ──────────────────────────────────────────────
COLORS = {"rest": "#2a78d6", "grpc": "#1baf7a"}  # categorical slots 1 & 2
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

BACKENDS_ORDER = ["postgres", "mongo", "redis"]


def style_axes(ax) -> None:
    ax.set_facecolor(SURFACE)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(BASELINE)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(True, axis="y", color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)
    ax.xaxis.label.set_color(MUTED)
    ax.yaxis.label.set_color(MUTED)
    ax.title.set_color(INK)


# ──────────────────────────────────────────────
# Loading
# ──────────────────────────────────────────────
def load_runs(run_dir: Path) -> list:
    raw_dir = run_dir / "raw"
    runs = []
    for path in sorted(raw_dir.glob("*.json")):
        with open(path) as f:
            data = json.load(f)
        m = re.match(r"(rest|grpc)-(\w+)_c(\d+)_p(\d+)_t(\d+)", path.stem)
        if not m:
            continue
        data["_suite"], data["_backend"] = m.group(1), m.group(2)
        data["_conc"], data["_payload"] = int(m.group(3)), int(m.group(4))
        data["_trial"] = int(m.group(5))
        runs.append(data)
    return runs


def load_pgstat(run_dir: Path) -> dict:
    """{(suite, conc, payload, trial): db_exec_time_ms} for postgres trials."""
    out = {}
    prof_dir = run_dir / "profiling"
    if not prof_dir.exists():
        return out
    for path in prof_dir.glob("pgstat_*.json"):
        m = re.match(r"pgstat_(rest|grpc)-postgres_c(\d+)_p(\d+)_t(\d+)", path.stem)
        if not m:
            continue
        with open(path) as f:
            snap = json.load(f)
        total = sum(
            float(row.get("total_exec_ms", 0.0))
            for row in snap
            if re.search(r"benchmark_records", row.get("query", ""), re.IGNORECASE)
        )
        out[(m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)))] = total
    return out


# ──────────────────────────────────────────────
# Statistics
# ──────────────────────────────────────────────
def trial_metrics(latencies: list) -> dict:
    arr = np.asarray(latencies)
    return {
        "n": len(arr),
        "mean": float(np.mean(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
    }


def ci95(values: list) -> tuple:
    """(mean, half_width) — Student-t 95% CI across trials."""
    arr = np.asarray(values, dtype=float)
    m = float(np.mean(arr))
    if len(arr) < 2:
        return m, float("nan")
    sem = sps.sem(arr)
    half = float(sem * sps.t.ppf(0.975, len(arr) - 1))
    return m, half


def aggregate_cells(runs: list) -> dict:
    """{(suite, backend, conc, payload): {metric: (mean, ci_half), trials, pooled, ...}}"""
    grouped = defaultdict(list)
    for r in runs:
        grouped[(r["_suite"], r["_backend"], r["_conc"], r["_payload"])].append(r)

    cells = {}
    for key, trials in grouped.items():
        per_trial = [trial_metrics(t["latencies_ms"]) for t in trials if t["latencies_ms"]]
        if not per_trial:
            continue
        cell = {"trials": len(per_trial)}
        for metric in ("mean", "p50", "p95", "p99"):
            vals = [t[metric] for t in per_trial]
            m, half = ci95(vals)
            sd = float(np.std(vals, ddof=1)) if len(vals) > 1 else float("nan")
            cell[metric] = {
                "mean": m,
                "ci95": half,
                "sd": sd,
                "cov_pct": (sd / m * 100.0) if m and not np.isnan(sd) else float("nan"),
            }
        cell["rps"] = ci95([t["achieved_rps"] for t in trials])
        cell["errors"] = sum(t["errors"] for t in trials)
        cell["requests"] = sum(len(t["latencies_ms"]) for t in trials)
        cell["pooled"] = np.concatenate([np.asarray(t["latencies_ms"]) for t in trials])
        cells[key] = cell
    return cells


def compare_cells(cells: dict) -> list:
    """REST vs gRPC per (backend, conc, payload)."""
    comparisons = []
    scenarios = sorted({(b, c, p) for (_, b, c, p) in cells})
    for backend, conc, payload in scenarios:
        rest = cells.get(("rest", backend, conc, payload))
        grpc = cells.get(("grpc", backend, conc, payload))
        if not rest or not grpc:
            continue
        u_stat, p_value = sps.mannwhitneyu(
            rest["pooled"], grpc["pooled"], alternative="two-sided"
        )
        rest_p99, grpc_p99 = rest["p99"], grpc["p99"]
        improvement_pct = (
            (rest_p99["mean"] - grpc_p99["mean"]) / rest_p99["mean"] * 100.0
            if rest_p99["mean"]
            else float("nan")
        )
        # Non-overlapping trial-level CIs = the difference is robust across trials
        rest_lo = rest_p99["mean"] - rest_p99["ci95"]
        rest_hi = rest_p99["mean"] + rest_p99["ci95"]
        grpc_lo = grpc_p99["mean"] - grpc_p99["ci95"]
        grpc_hi = grpc_p99["mean"] + grpc_p99["ci95"]
        if np.isnan(rest_p99["ci95"]) or np.isnan(grpc_p99["ci95"]):
            ci_verdict = "n/a (single trial)"
        elif grpc_hi < rest_lo:
            ci_verdict = "grpc faster (CIs disjoint)"
        elif rest_hi < grpc_lo:
            ci_verdict = "rest faster (CIs disjoint)"
        else:
            ci_verdict = "CIs overlap"
        # A side with <2 trials cannot be compared robustly — and a lone
        # surviving trial usually means the system was failing (e.g. a dying
        # database), so its latencies measure the failure, not the protocol.
        insufficient = rest["trials"] < 2 or grpc["trials"] < 2
        comparisons.append(
            {
                "backend": backend,
                "concurrency": conc,
                "payload_bytes": payload,
                "rest_trials": rest["trials"],
                "grpc_trials": grpc["trials"],
                "rest_p99_ms": rest_p99["mean"],
                "rest_p99_ci95": rest_p99["ci95"],
                "grpc_p99_ms": grpc_p99["mean"],
                "grpc_p99_ci95": grpc_p99["ci95"],
                "grpc_improvement_pct": improvement_pct,
                "mannwhitney_p": float(p_value),
                "ci_verdict": ci_verdict,
                "insufficient_trials": insufficient,
            }
        )
    return comparisons


def db_share_by_scenario(runs: list, pgstat: dict) -> dict:
    """{(suite, conc, payload): mean DB-time share of total request time}."""
    shares = defaultdict(list)
    for r in runs:
        if r["_backend"] != "postgres":
            continue
        key = (r["_suite"], r["_conc"], r["_payload"], r["_trial"])
        if key not in pgstat or not r["latencies_ms"]:
            continue
        total_request_ms = float(np.sum(r["latencies_ms"]))
        if total_request_ms > 0:
            shares[key[:3]].append(pgstat[key] / total_request_ms)
    return {k: float(np.mean(v)) for k, v in shares.items()}


# ──────────────────────────────────────────────
# Reports
# ──────────────────────────────────────────────
def fmt(x, nd=2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:.{nd}f}"


def write_summary_csv(cells: dict, path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["suite", "backend", "concurrency", "payload_bytes", "trials",
             "requests", "errors", "rps_mean", "rps_ci95",
             "mean_ms", "mean_ci95", "p50_ms", "p50_ci95",
             "p95_ms", "p95_ci95", "p99_ms", "p99_ci95", "p99_sd", "p99_cov_pct"]
        )
        for (suite, backend, conc, payload), c in sorted(cells.items()):
            w.writerow(
                [suite, backend, conc, payload, c["trials"], c["requests"], c["errors"],
                 fmt(c["rps"][0]), fmt(c["rps"][1]),
                 fmt(c["mean"]["mean"]), fmt(c["mean"]["ci95"]),
                 fmt(c["p50"]["mean"]), fmt(c["p50"]["ci95"]),
                 fmt(c["p95"]["mean"]), fmt(c["p95"]["ci95"]),
                 fmt(c["p99"]["mean"]), fmt(c["p99"]["ci95"]),
                 fmt(c["p99"]["sd"]), fmt(c["p99"]["cov_pct"], 1)]
            )


def write_comparisons_csv(comparisons: list, path: Path) -> None:
    if not comparisons:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(comparisons[0].keys()))
        w.writeheader()
        for row in comparisons:
            w.writerow({k: (fmt(v, 4) if isinstance(v, float) else v) for k, v in row.items()})


def write_summary_md(cells: dict, comparisons: list, campaign: dict, path: Path) -> None:
    lines = [
        "# Benchmark summary",
        "",
        f"Campaign: {campaign.get('trials', '?')} trials x {campaign.get('duration_s', '?')}s "
        f"measurement (+{campaign.get('warmup_s', '?')}s warm-up), op mix `{campaign.get('op', '?')}`. "
        "All values in milliseconds; `±` is the 95% Student-t confidence interval across trials.",
        "",
        "## Per-cell latency",
        "",
        "| Suite | Backend | Conc. | Payload | Trials | Req/s | p50 | p95 | p99 | p99 CoV |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (suite, backend, conc, payload), c in sorted(cells.items()):
        lines.append(
            f"| {suite} | {backend} | {conc} | {payload}B "
            f"| {c['trials']} "
            f"| {fmt(c['rps'][0], 0)} "
            f"| {fmt(c['p50']['mean'])} ± {fmt(c['p50']['ci95'])} "
            f"| {fmt(c['p95']['mean'])} ± {fmt(c['p95']['ci95'])} "
            f"| {fmt(c['p99']['mean'])} ± {fmt(c['p99']['ci95'])} "
            f"| {fmt(c['p99']['cov_pct'], 1)}% |"
        )
    lines += [
        "",
        "## REST vs gRPC (per scenario)",
        "",
        "gRPC improvement is relative p99 reduction vs REST (negative = gRPC slower). "
        "Mann-Whitney U runs on pooled per-request latencies.",
        "",
        "| Backend | Conc. | Payload | REST p99 | gRPC p99 | gRPC improv. | MWU p-value | Trial-CI verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    excluded = []
    for r in comparisons:
        if r["insufficient_trials"]:
            lines.append(
                f"| {r['backend']} | {r['concurrency']} | {r['payload_bytes']}B "
                f"| — | — | — | — | **EXCLUDED** (rest n={r['rest_trials']}, "
                f"grpc n={r['grpc_trials']} trials) |"
            )
            excluded.append(r)
            continue
        p = r["mannwhitney_p"]
        p_str = "<0.001" if p < 0.001 else f"{p:.3f}"
        lines.append(
            f"| {r['backend']} | {r['concurrency']} | {r['payload_bytes']}B "
            f"| {fmt(r['rest_p99_ms'])} ± {fmt(r['rest_p99_ci95'])} "
            f"| {fmt(r['grpc_p99_ms'])} ± {fmt(r['grpc_p99_ci95'])} "
            f"| {fmt(r['grpc_improvement_pct'], 1)}% | {p_str} | {r['ci_verdict']} |"
        )
    if excluded:
        lines += [
            "",
            "**Excluded scenarios**: a side with <2 completed trials cannot be "
            "compared — a lone surviving trial typically measures a failing "
            "system (e.g. a database dying mid-run), not protocol performance. "
            "Report these cells as availability failures and re-run them for "
            "latency numbers.",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_migration_decision(
    comparisons: list,
    db_shares: dict,
    min_improvement: float,
    max_db_share: float,
    path: Path,
) -> None:
    lines = [
        "# Protocol Migration Threshold — Phase-Gate decision model",
        "",
        "## Formal decision rule",
        "",
        "For a service tier, **migrate REST → gRPC** when ALL of the following hold",
        "for the tier's production-representative scenario (concurrency, payload):",
        "",
        f"1. **Improvement gate** — gRPC reduces p99 latency by at least "
        f"**{min_improvement:.0f}%** relative to REST.",
        "2. **Robustness gate** — the trial-level 95% confidence intervals of the",
        "   two p99 estimates do not overlap, AND the Mann-Whitney U test on the",
        "   pooled latency distributions rejects equality at α = 0.01.",
        f"3. **Amdahl gate** — database execution time accounts for less than "
        f"**{max_db_share:.0f}%** of total request time (when measurable via "
        "pg_stat_statements). If the database dominates the request path, protocol "
        "migration cannot yield macroscopic improvement regardless of gates 1–2.",
        "",
        "## Measured verdicts",
        "",
        "| Backend | Conc. | Payload | Improv. gate | Robustness gate | Amdahl gate (DB share) | **Decision** |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in comparisons:
        if r["insufficient_trials"]:
            lines.append(
                f"| {r['backend']} | {r['concurrency']} | {r['payload_bytes']}B "
                f"| — | — | — | EXCLUDED (insufficient trials) |"
            )
            continue
        g1 = r["grpc_improvement_pct"] >= min_improvement
        g2 = r["ci_verdict"] == "grpc faster (CIs disjoint)" and r["mannwhitney_p"] < 0.01
        share = None
        if r["backend"] == "postgres":
            # DB share measured on the gRPC side (the candidate being migrated to)
            share = db_shares.get(("grpc", r["concurrency"], r["payload_bytes"]))
        if share is None:
            g3, share_str = True, "not measured"
        else:
            g3 = share * 100.0 < max_db_share
            share_str = f"{share * 100.0:.0f}%"
        decision = "**MIGRATE**" if (g1 and g2 and g3) else "HOLD"
        lines.append(
            f"| {r['backend']} | {r['concurrency']} | {r['payload_bytes']}B "
            f"| {'PASS' if g1 else 'fail'} ({fmt(r['grpc_improvement_pct'], 1)}%) "
            f"| {'PASS' if g2 else 'fail'} "
            f"| {'PASS' if g3 else 'fail'} ({share_str}) "
            f"| {decision} |"
        )
    lines += [
        "",
        "A scenario where gates 1–2 pass but gate 3 fails is a *database blind spot*: ",
        "the protocol is measurably faster, yet the migration yields little end-to-end ",
        "value because the bottleneck is storage, not transport.",
        "",
        f"Parameters: --min-improvement {min_improvement:.0f} --max-db-share {max_db_share:.0f} ",
        "(tunable per organization; defaults are deliberately conservative).",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ──────────────────────────────────────────────
# Plots
# ──────────────────────────────────────────────
def plot_p99_vs_concurrency(cells: dict, payload: int, out: Path) -> None:
    import matplotlib.pyplot as plt

    backends = [b for b in BACKENDS_ORDER if any(k[1] == b and k[3] == payload for k in cells)]
    if not backends:
        return
    fig, axes = plt.subplots(
        1, len(backends), figsize=(4.2 * len(backends), 3.6), facecolor=SURFACE
    )
    axes = np.atleast_1d(axes)
    for ax, backend in zip(axes, backends):
        for suite in ("rest", "grpc"):
            pts = sorted(
                (k[2], c["p99"]["mean"], c["p99"]["ci95"])
                for k, c in cells.items()
                if k[0] == suite and k[1] == backend and k[3] == payload
            )
            if not pts:
                continue
            xs, ys, cis = zip(*pts)
            cis = [0 if np.isnan(c) else c for c in cis]
            ax.errorbar(
                xs, ys, yerr=cis,
                color=COLORS[suite], linewidth=2, marker="o", markersize=5,
                capsize=3, label=suite.upper(),
            )
            ax.annotate(
                suite.upper(), (xs[-1], ys[-1]),
                textcoords="offset points", xytext=(6, 0),
                color=COLORS[suite], fontsize=9, fontweight="bold", va="center",
            )
        style_axes(ax)
        ax.set_title(backend, fontsize=11)
        ax.set_xlabel("concurrency (workers)")
        ax.margins(x=0.15)
    axes[0].set_ylabel("p99 latency (ms)")
    axes[-1].legend(frameon=False, labelcolor=INK, fontsize=9)
    fig.suptitle(
        f"p99 latency vs concurrency — {payload}B payload (error bars: 95% CI across trials)",
        fontsize=12, color=INK,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_cdf(cells: dict, conc: int, payload: int, out: Path) -> None:
    import matplotlib.pyplot as plt

    backends = [
        b for b in BACKENDS_ORDER
        if any(k[1] == b and k[2] == conc and k[3] == payload for k in cells)
    ]
    if not backends:
        return
    fig, axes = plt.subplots(
        1, len(backends), figsize=(4.2 * len(backends), 3.6), facecolor=SURFACE
    )
    axes = np.atleast_1d(axes)
    for ax, backend in zip(axes, backends):
        for suite in ("rest", "grpc"):
            cell = cells.get((suite, backend, conc, payload))
            if cell is None:
                continue
            lat = np.sort(cell["pooled"])
            cdf = np.arange(1, len(lat) + 1) / len(lat)
            ax.plot(lat, cdf, color=COLORS[suite], linewidth=2, label=suite.upper())
        style_axes(ax)
        ax.set_title(backend, fontsize=11)
        ax.set_xlabel("latency (ms, log scale)")
        ax.set_xscale("log")
        from matplotlib.ticker import NullFormatter
        ax.xaxis.set_minor_formatter(NullFormatter())  # avoid tick-label collisions
        ax.set_ylim(0, 1.02)
        ax.axhline(0.99, color=MUTED, linewidth=0.8, linestyle=(0, (4, 4)))
        ax.annotate("p99", (ax.get_xlim()[0], 0.99), color=MUTED, fontsize=8,
                    textcoords="offset points", xytext=(2, 3))
    axes[0].set_ylabel("fraction of requests")
    axes[-1].legend(frameon=False, labelcolor=INK, fontsize=9, loc="lower right")
    fig.suptitle(
        f"Latency CDF — concurrency {conc}, {payload}B payload (pooled across trials)",
        fontsize=12, color=INK,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_p99_bars(cells: dict, conc: int, payload: int, out: Path) -> None:
    import matplotlib.pyplot as plt

    backends = [
        b for b in BACKENDS_ORDER
        if any(k[1] == b and k[2] == conc and k[3] == payload for k in cells)
    ]
    if not backends:
        return
    fig, ax = plt.subplots(figsize=(1.6 + 2.2 * len(backends), 3.8), facecolor=SURFACE)
    width = 0.36
    x = np.arange(len(backends))
    for i, suite in enumerate(("rest", "grpc")):
        vals, errs = [], []
        for b in backends:
            cell = cells.get((suite, b, conc, payload))
            vals.append(cell["p99"]["mean"] if cell else np.nan)
            e = cell["p99"]["ci95"] if cell else np.nan
            errs.append(0 if np.isnan(e) else e)
        bars = ax.bar(
            x + (i - 0.5) * (width + 0.02), vals, width,
            color=COLORS[suite], label=suite.upper(),
            yerr=errs, capsize=4, error_kw={"ecolor": INK, "elinewidth": 1},
        )
        for rect, v, e in zip(bars, vals, errs):
            if not np.isnan(v):
                # place the label above the error-bar cap, not the bar top
                ax.annotate(
                    f"{v:.1f}", (rect.get_x() + rect.get_width() / 2, v + e),
                    textcoords="offset points", xytext=(0, 4),
                    ha="center", color=INK, fontsize=8,
                )
    style_axes(ax)
    ax.set_xticks(x, backends)
    ax.tick_params(axis="x", labelcolor=INK)
    ax.set_ylabel("p99 latency (ms)")
    ax.set_title(
        f"p99 latency — concurrency {conc}, {payload}B payload (95% CI)",
        fontsize=12,
    )
    ax.legend(frameon=False, labelcolor=INK, fontsize=9)
    ax.margins(y=0.18)
    fig.tight_layout()
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="Analyze a ProtocolShift benchmark run")
    p.add_argument("run_dir", help="results/<run_id> directory from run_trials.py")
    p.add_argument("--min-improvement", type=float, default=20.0,
                   help="Migration gate: min %% p99 improvement (default 20)")
    p.add_argument("--max-db-share", type=float, default=60.0,
                   help="Migration gate: max %% of request time spent in DB (default 60)")
    args = p.parse_args()

    run_dir = Path(args.run_dir)
    runs = load_runs(run_dir)
    if not runs:
        print(f"No raw results found under {run_dir / 'raw'}")
        return 1

    campaign = {}
    config_path = run_dir / "config.json"
    if config_path.exists():
        campaign = json.loads(config_path.read_text())

    out_dir = run_dir / "analysis"
    plots_dir = out_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(runs)} trial files.")
    cells = aggregate_cells(runs)
    comparisons = compare_cells(cells)
    pgstat = load_pgstat(run_dir)
    db_shares = db_share_by_scenario(runs, pgstat)
    if pgstat:
        print(f"pg_stat_statements profiling found for {len(pgstat)} postgres trials.")

    write_summary_csv(cells, out_dir / "summary.csv")
    write_comparisons_csv(comparisons, out_dir / "comparisons.csv")
    write_summary_md(cells, comparisons, campaign, out_dir / "summary.md")
    write_migration_decision(
        comparisons, db_shares, args.min_improvement, args.max_db_share,
        out_dir / "migration_decision.md",
    )

    # Plots only show robust cells: a single-trial cell usually captured a
    # failing system and would distort every axis it appears on.
    plot_cells = {k: v for k, v in cells.items() if v["trials"] >= 2}
    dropped = len(cells) - len(plot_cells)
    if dropped:
        print(f"[note] {dropped} cell(s) with <2 trials excluded from plots "
              "(still listed in summary tables, marked EXCLUDED in comparisons).")

    payloads = sorted({k[3] for k in plot_cells})
    concs = sorted({k[2] for k in plot_cells})
    if concs and payloads:
        max_conc = concs[-1]
        for payload in payloads:
            plot_p99_vs_concurrency(plot_cells, payload, plots_dir / f"p99_vs_concurrency_p{payload}.png")
            plot_cdf(plot_cells, max_conc, payload, plots_dir / f"cdf_c{max_conc}_p{payload}.png")
            plot_p99_bars(plot_cells, max_conc, payload, plots_dir / f"p99_bars_c{max_conc}_p{payload}.png")

    print(f"Analysis written to {out_dir}")
    for f in sorted(out_dir.rglob("*")):
        if f.is_file():
            print(f"  {f.relative_to(run_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
