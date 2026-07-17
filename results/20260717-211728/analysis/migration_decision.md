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
| mongo | 10 | 100B | PASS (51.0%) | PASS | PASS (not measured) | **MIGRATE** |
| mongo | 10 | 1000B | PASS (48.3%) | PASS | PASS (not measured) | **MIGRATE** |
| mongo | 10 | 10000B | PASS (44.3%) | PASS | PASS (not measured) | **MIGRATE** |
| mongo | 50 | 100B | PASS (91.0%) | PASS | PASS (not measured) | **MIGRATE** |
| mongo | 50 | 1000B | PASS (91.4%) | PASS | PASS (not measured) | **MIGRATE** |
| mongo | 50 | 10000B | PASS (91.6%) | PASS | PASS (not measured) | **MIGRATE** |
| mongo | 100 | 100B | PASS (91.6%) | PASS | PASS (not measured) | **MIGRATE** |
| mongo | 100 | 1000B | PASS (93.1%) | PASS | PASS (not measured) | **MIGRATE** |
| mongo | 100 | 10000B | PASS (91.2%) | PASS | PASS (not measured) | **MIGRATE** |
| postgres | 10 | 100B | PASS (76.1%) | PASS | PASS (1%) | **MIGRATE** |
| postgres | 10 | 1000B | PASS (75.6%) | PASS | PASS (1%) | **MIGRATE** |
| postgres | 10 | 10000B | PASS (77.3%) | PASS | PASS (3%) | **MIGRATE** |
| postgres | 50 | 100B | PASS (95.7%) | PASS | PASS (0%) | **MIGRATE** |
| postgres | 50 | 1000B | PASS (95.2%) | PASS | PASS (0%) | **MIGRATE** |
| postgres | 50 | 10000B | PASS (95.4%) | PASS | PASS (1%) | **MIGRATE** |
| postgres | 100 | 100B | PASS (96.0%) | PASS | PASS (0%) | **MIGRATE** |
| postgres | 100 | 1000B | PASS (95.2%) | PASS | PASS (0%) | **MIGRATE** |
| postgres | 100 | 10000B | PASS (95.7%) | PASS | PASS (0%) | **MIGRATE** |
| redis | 10 | 100B | PASS (76.6%) | PASS | PASS (not measured) | **MIGRATE** |
| redis | 10 | 1000B | PASS (76.1%) | PASS | PASS (not measured) | **MIGRATE** |
| redis | 10 | 10000B | PASS (61.4%) | PASS | PASS (not measured) | **MIGRATE** |
| redis | 50 | 100B | PASS (96.0%) | PASS | PASS (not measured) | **MIGRATE** |
| redis | 50 | 1000B | PASS (95.8%) | PASS | PASS (not measured) | **MIGRATE** |
| redis | 50 | 10000B | PASS (96.6%) | PASS | PASS (not measured) | **MIGRATE** |
| redis | 100 | 100B | PASS (96.5%) | PASS | PASS (not measured) | **MIGRATE** |
| redis | 100 | 1000B | PASS (96.1%) | PASS | PASS (not measured) | **MIGRATE** |
| redis | 100 | 10000B | PASS (96.7%) | PASS | PASS (not measured) | **MIGRATE** |

A scenario where gates 1–2 pass but gate 3 fails is a *database blind spot*: 
the protocol is measurably faster, yet the migration yields little end-to-end 
value because the bottleneck is storage, not transport.

Parameters: --min-improvement 20 --max-db-share 60 
(tunable per organization; defaults are deliberately conservative).
