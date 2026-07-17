# Benchmark summary

Campaign: 1 trials x 5.0s measurement (+1.0s warm-up), op mix `mixed`. All values in milliseconds; `±` is the 95% Student-t confidence interval across trials.

## Per-cell latency

| Suite | Backend | Conc. | Payload | Req/s | p50 | p95 | p99 | p99 CoV |
|---|---|---|---|---|---|---|---|---|
| grpc | mongo | 10 | 100B | 644 | 15.21 ± — | 23.10 ± — | 26.89 ± — | —% |
| grpc | postgres | 10 | 100B | 1385 | 6.76 ± — | 11.03 ± — | 16.07 ± — | —% |
| grpc | redis | 10 | 100B | 1536 | 6.27 ± — | 9.49 ± — | 13.80 ± — | —% |
| rest | mongo | 10 | 100B | 335 | 37.41 ± — | 57.57 ± — | 63.87 ± — | —% |
| rest | postgres | 10 | 100B | 354 | 44.25 ± — | 54.62 ± — | 57.91 ± — | —% |
| rest | redis | 10 | 100B | 374 | 43.42 ± — | 51.85 ± — | 55.61 ± — | —% |

## REST vs gRPC (per scenario)

gRPC improvement is relative p99 reduction vs REST (negative = gRPC slower). Mann-Whitney U runs on pooled per-request latencies.

| Backend | Conc. | Payload | REST p99 | gRPC p99 | gRPC improv. | MWU p-value | Trial-CI verdict |
|---|---|---|---|---|---|---|---|
| mongo | 10 | 100B | 63.87 ± — | 26.89 ± — | 57.9% | <0.001 | n/a (single trial) |
| postgres | 10 | 100B | 57.91 ± — | 16.07 ± — | 72.3% | <0.001 | n/a (single trial) |
| redis | 10 | 100B | 55.61 ± — | 13.80 ± — | 75.2% | <0.001 | n/a (single trial) |
