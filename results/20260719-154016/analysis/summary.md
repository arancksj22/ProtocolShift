# Benchmark summary

Campaign: 5 trials x 15.0s measurement (+3.0s warm-up), op mix `mixed`. All values in milliseconds; `±` is the 95% Student-t confidence interval across trials.

## Per-cell latency

| Suite | Backend | Conc. | Payload | Trials | Req/s | p50 | p95 | p99 | p99 CoV |
|---|---|---|---|---|---|---|---|---|---|
| grpc | postgres | 10 | 100B | 5 | 1436 | 6.21 ± 1.24 | 14.76 ± 11.51 | 20.87 ± 15.01 | 57.9% |
| grpc | postgres | 10 | 1000B | 5 | 900 | 8.95 ± 2.65 | 26.13 ± 8.96 | 42.86 ± 20.98 | 39.4% |
| grpc | postgres | 10 | 10000B | 5 | 716 | 11.94 ± 4.87 | 32.80 ± 10.85 | 54.66 ± 18.25 | 26.9% |
| grpc | postgres | 50 | 100B | 5 | 1430 | 31.12 ± 2.94 | 57.59 ± 12.31 | 115.07 ± 118.18 | 82.7% |
| grpc | postgres | 50 | 1000B | 5 | 1055 | 39.45 ± 4.87 | 102.34 ± 46.89 | 160.37 ± 105.57 | 53.0% |
| grpc | postgres | 50 | 10000B | 5 | 1002 | 45.26 ± 3.46 | 83.20 ± 17.33 | 111.27 ± 33.30 | 24.1% |
| grpc | postgres | 100 | 100B | 5 | 1137 | 73.67 ± 9.33 | 177.46 ± 57.65 | 285.08 ± 126.96 | 35.9% |
| grpc | postgres | 100 | 1000B | 5 | 1025 | 84.74 ± 8.53 | 176.97 ± 63.88 | 321.83 ± 275.91 | 69.0% |
| grpc | postgres | 100 | 10000B | 5 | 1016 | 92.66 ± 22.98 | 156.59 ± 22.64 | 225.84 ± 93.08 | 33.2% |
| rest | postgres | 10 | 100B | 5 | 322 | 31.28 ± 13.69 | 65.01 ± 13.78 | 80.03 ± 35.03 | 35.3% |
| rest | postgres | 10 | 1000B | 5 | 322 | 45.06 ± 1.05 | 62.03 ± 7.99 | 74.04 ± 12.65 | 13.8% |
| rest | postgres | 10 | 10000B | 5 | 292 | 47.30 ± 2.42 | 67.84 ± 10.60 | 89.28 ± 21.31 | 19.2% |
| rest | postgres | 50 | 100B | 5 | 156 | 200.20 ± 32.62 | 978.94 ± 121.40 | 1521.51 ± 202.69 | 10.7% |
| rest | postgres | 50 | 1000B | 5 | 156 | 194.64 ± 28.66 | 992.65 ± 114.11 | 1551.33 ± 217.16 | 11.3% |
| rest | postgres | 50 | 10000B | 5 | 142 | 216.80 ± 95.88 | 1177.07 ± 455.28 | 1904.64 ± 870.53 | 36.8% |
| rest | postgres | 100 | 100B | 5 | 143 | 421.65 ± 33.08 | 1990.04 ± 220.02 | 3301.63 ± 523.65 | 12.8% |
| rest | postgres | 100 | 1000B | 5 | 141 | 445.51 ± 132.92 | 2264.99 ± 879.38 | 3613.93 ± 1584.18 | 35.3% |
| rest | postgres | 100 | 10000B | 5 | 162 | 389.34 ± 80.23 | 1821.82 ± 382.73 | 2777.49 ± 519.92 | 15.1% |

## REST vs gRPC (per scenario)

gRPC improvement is relative p99 reduction vs REST (negative = gRPC slower). Mann-Whitney U runs on pooled per-request latencies.

| Backend | Conc. | Payload | REST p99 | gRPC p99 | gRPC improv. | MWU p-value | Trial-CI verdict |
|---|---|---|---|---|---|---|---|
| postgres | 10 | 100B | 80.03 ± 35.03 | 20.87 ± 15.01 | 73.9% | <0.001 | grpc faster (CIs disjoint) |
| postgres | 10 | 1000B | 74.04 ± 12.65 | 42.86 ± 20.98 | 42.1% | <0.001 | CIs overlap |
| postgres | 10 | 10000B | 89.28 ± 21.31 | 54.66 ± 18.25 | 38.8% | <0.001 | CIs overlap |
| postgres | 50 | 100B | 1521.51 ± 202.69 | 115.07 ± 118.18 | 92.4% | <0.001 | grpc faster (CIs disjoint) |
| postgres | 50 | 1000B | 1551.33 ± 217.16 | 160.37 ± 105.57 | 89.7% | <0.001 | grpc faster (CIs disjoint) |
| postgres | 50 | 10000B | 1904.64 ± 870.53 | 111.27 ± 33.30 | 94.2% | <0.001 | grpc faster (CIs disjoint) |
| postgres | 100 | 100B | 3301.63 ± 523.65 | 285.08 ± 126.96 | 91.4% | <0.001 | grpc faster (CIs disjoint) |
| postgres | 100 | 1000B | 3613.93 ± 1584.18 | 321.83 ± 275.91 | 91.1% | <0.001 | grpc faster (CIs disjoint) |
| postgres | 100 | 10000B | 2777.49 ± 519.92 | 225.84 ± 93.08 | 91.9% | <0.001 | grpc faster (CIs disjoint) |
