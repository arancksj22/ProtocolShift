# Benchmark summary

Campaign: 1 trials x 5.0s measurement (+1.0s warm-up), op mix `mixed`. All values in milliseconds; `±` is the 95% Student-t confidence interval across trials.

## Per-cell latency

| Suite | Backend | Conc. | Payload | Req/s | p50 | p95 | p99 | p99 CoV |
|---|---|---|---|---|---|---|---|---|
| grpc | mongo | 10 | 100B | 586 | 16.56 ± — | 25.78 ± — | 30.24 ± — | —% |
| grpc | postgres | 10 | 100B | 1373 | 6.97 ± — | 10.11 ± — | 14.48 ± — | —% |
| grpc | redis | 10 | 100B | 1535 | 6.46 ± — | 8.59 ± — | 10.72 ± — | —% |
| rest | mongo | 10 | 100B | 339 | 44.43 ± — | 56.79 ± — | 60.58 ± — | —% |
| rest | postgres | 10 | 100B | 365 | 30.65 ± — | 52.98 ± — | 54.90 ± — | —% |
| rest | redis | 10 | 100B | 376 | 13.87 ± — | 51.71 ± — | 55.48 ± — | —% |

## REST vs gRPC (per scenario)

gRPC improvement is relative p99 reduction vs REST (negative = gRPC slower). Mann-Whitney U runs on pooled per-request latencies.

| Backend | Conc. | Payload | REST p99 | gRPC p99 | gRPC improv. | MWU p-value | Trial-CI verdict |
|---|---|---|---|---|---|---|---|
| mongo | 10 | 100B | 60.58 ± — | 30.24 ± — | 50.1% | 0.092 | n/a (single trial) |
| postgres | 10 | 100B | 54.90 ± — | 14.48 ± — | 73.6% | <0.001 | n/a (single trial) |
| redis | 10 | 100B | 55.48 ± — | 10.72 ± — | 80.7% | <0.001 | n/a (single trial) |
