# Benchmark summary

Campaign: 5 trials x 15.0s measurement (+3.0s warm-up), op mix `mixed`. All values in milliseconds; `±` is the 95% Student-t confidence interval across trials.

## Per-cell latency

| Suite | Backend | Conc. | Payload | Trials | Req/s | p50 | p95 | p99 | p99 CoV |
|---|---|---|---|---|---|---|---|---|---|
| grpc | redis | 50 | 10000B | 5 | 1449 | 32.33 ± 0.81 | 48.63 ± 2.28 | 61.25 ± 8.44 | 11.1% |
| grpc | redis | 100 | 10000B | 5 | 1440 | 65.63 ± 1.24 | 96.49 ± 7.25 | 125.50 ± 43.82 | 28.1% |
| rest | redis | 50 | 10000B | 5 | 313 | 104.70 ± 20.08 | 486.32 ± 64.56 | 821.48 ± 63.85 | 6.3% |
| rest | redis | 100 | 10000B | 5 | 253 | 229.45 ± 34.21 | 1253.17 ± 184.28 | 2303.42 ± 507.88 | 17.8% |

## REST vs gRPC (per scenario)

gRPC improvement is relative p99 reduction vs REST (negative = gRPC slower). Mann-Whitney U runs on pooled per-request latencies.

| Backend | Conc. | Payload | REST p99 | gRPC p99 | gRPC improv. | MWU p-value | Trial-CI verdict |
|---|---|---|---|---|---|---|---|
| redis | 50 | 10000B | 821.48 ± 63.85 | 61.25 ± 8.44 | 92.5% | <0.001 | grpc faster (CIs disjoint) |
| redis | 100 | 10000B | 2303.42 ± 507.88 | 125.50 ± 43.82 | 94.6% | <0.001 | grpc faster (CIs disjoint) |
