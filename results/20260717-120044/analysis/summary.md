# Benchmark summary

Campaign: 1 trials x 5.0s measurement (+1.0s warm-up), op mix `mixed`. All values in milliseconds; `±` is the 95% Student-t confidence interval across trials.

## Per-cell latency

| Suite | Backend | Conc. | Payload | Req/s | p50 | p95 | p99 | p99 CoV |
|---|---|---|---|---|---|---|---|---|
| grpc | mongo | 10 | 100B | 647 | 14.97 ± — | 23.56 ± — | 27.96 ± — | —% |
| grpc | postgres | 10 | 100B | 1449 | 6.62 ± — | 9.60 ± — | 12.58 ± — | —% |
| grpc | redis | 10 | 100B | 1556 | 6.22 ± — | 9.05 ± — | 13.94 ± — | —% |
| rest | mongo | 10 | 100B | 340 | 30.72 ± — | 56.99 ± — | 60.54 ± — | —% |
| rest | postgres | 10 | 100B | 371 | 43.67 ± — | 53.02 ± — | 56.81 ± — | —% |
| rest | redis | 10 | 100B | 390 | 26.78 ± — | 50.36 ± — | 52.88 ± — | —% |

## REST vs gRPC (per scenario)

gRPC improvement is relative p99 reduction vs REST (negative = gRPC slower). Mann-Whitney U runs on pooled per-request latencies.

| Backend | Conc. | Payload | REST p99 | gRPC p99 | gRPC improv. | MWU p-value | Trial-CI verdict |
|---|---|---|---|---|---|---|---|
| mongo | 10 | 100B | 60.54 ± — | 27.96 ± — | 53.8% | 0.016 | n/a (single trial) |
| postgres | 10 | 100B | 56.81 ± — | 12.58 ± — | 77.9% | <0.001 | n/a (single trial) |
| redis | 10 | 100B | 52.88 ± — | 13.94 ± — | 73.6% | <0.001 | n/a (single trial) |
