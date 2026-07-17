# Protocol Migration Threshold — Phase-Gate decision model

## Formal decision rule

For a service tier, **migrate REST → gRPC** when ALL of the following hold
for the tier's production-representative scenario (concurrency, payload):

1. **Improvement gate** — gRPC reduces p99 latency by at least **20%** relative to REST.
2. **Robustness gate** — the trial-level 95% confidence intervals of the
   two p99 estimates do not overlap, AND the Mann-Whitney U test on the
   pooled latency distributions rejects equality at α = 0.01.
3. **Amdahl gate** — database execution time accounts for less than **60%** of total request time (when measurable via pg_stat_statements). If the database dominates the request path, protocol migration cannot yield macroscopic improvement regardless of gates 1–2.

## Measured verdicts

| Backend | Conc. | Payload | Improv. gate | Robustness gate | Amdahl gate (DB share) | **Decision** |
|---|---|---|---|---|---|---|
| mongo | 10 | 100B | PASS (57.9%) | fail | PASS (not measured) | HOLD |
| postgres | 10 | 100B | PASS (72.3%) | fail | PASS (not measured) | HOLD |
| redis | 10 | 100B | PASS (75.2%) | fail | PASS (not measured) | HOLD |

A scenario where gates 1–2 pass but gate 3 fails is a *database blind spot*: 
the protocol is measurably faster, yet the migration yields little end-to-end 
value because the bottleneck is storage, not transport.

Parameters: --min-improvement 20 --max-db-share 60 
(tunable per organization; defaults are deliberately conservative).
