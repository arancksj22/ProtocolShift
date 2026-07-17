# Benchmark summary

Campaign: 1 trials x 5.0s measurement (+1.0s warm-up), op mix `mixed`. All values in milliseconds; `±` is the 95% Student-t confidence interval across trials.

## Per-cell latency

| Suite | Backend | Conc. | Payload | Req/s | p50 | p95 | p99 | p99 CoV |
|---|---|---|---|---|---|---|---|---|
| grpc | mongo | 10 | 100B | 529 | 17.99 ± — | 29.74 ± — | 35.34 ± — | —% |
| grpc | postgres | 10 | 100B | 1284 | 7.48 ± — | 11.10 ± — | 13.79 ± — | —% |
| grpc | redis | 10 | 100B | 1188 | 7.96 ± — | 12.80 ± — | 19.33 ± — | —% |
| rest | mongo | 10 | 100B | 327 | 27.48 ± — | 58.10 ± — | 62.71 ± — | —% |
| rest | postgres | 10 | 100B | 356 | 45.02 ± — | 54.00 ± — | 57.52 ± — | —% |
| rest | redis | 10 | 100B | 367 | 33.92 ± — | 52.21 ± — | 55.38 ± — | —% |

## REST vs gRPC (per scenario)

gRPC improvement is relative p99 reduction vs REST (negative = gRPC slower). Mann-Whitney U runs on pooled per-request latencies.

| Backend | Conc. | Payload | REST p99 | gRPC p99 | gRPC improv. | MWU p-value | Trial-CI verdict |
|---|---|---|---|---|---|---|---|
| mongo | 10 | 100B | 62.71 ± — | 35.34 ± — | 43.6% | 0.086 | n/a (single trial) |
| postgres | 10 | 100B | 57.52 ± — | 13.79 ± — | 76.0% | <0.001 | n/a (single trial) |
| redis | 10 | 100B | 55.38 ± — | 19.33 ± — | 65.1% | <0.001 | n/a (single trial) |
