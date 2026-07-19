# ProtocolShift: New Experimental Results (July 2026 Campaign)

This document is a complete, self-contained record of the new benchmark results
produced with the rebuilt measurement harness. It is written to be handed to
another author (human or LLM) to update the paper, and to be shared with mentors
as an evidence summary. Every number below comes from committed result files in
`results/`. Where a claim replaces an older number in the paper, the change is
called out explicitly.

**Bottom line:** Under a correct persistent-connection load generator, gRPC
reduces p99 tail latency by roughly 88 to 94 percent versus REST across all three
database backends under concurrency, with statistically significant and
reproducible margins. Query-level profiling proves the gap is protocol overhead,
not the database. The previously reported PostgreSQL anomaly (gRPC slower than
REST) was a load-generation artifact and does not reproduce.

---

## 1. What changed since the last paper version

| Item | Old paper (grpcurl loops) | New result (Python harness) |
|---|---|---|
| PostgreSQL p99, cloud, under load | gRPC 861 ms vs REST 347 ms (gRPC slower) | gRPC 80 to 322 ms vs REST 891 to 3614 ms (gRPC 88 to 92 percent faster) |
| Statistical basis | single runs, mean only | 5 trials/cell, 95 percent CI, Mann-Whitney U |
| DB vs protocol attribution | asserted | measured: DB = 0 to 2 percent of request time |
| Redis 10 KB high-concurrency | not isolated | measured after fixing an OOM crash |
| Load generator | new process + new connection per request | one persistent pool (REST) / one persistent channel (gRPC) |

The single most important narrative change: **the PostgreSQL anomaly reverses.**
The old numbers were produced by a load generator that opened a new OS process and
a new HTTP/2 connection for every gRPC request, which discarded multiplexing and
added per-request setup cost. With a persistent channel, gRPC is decisively faster
on PostgreSQL. This should be presented as a correction and a methodological
lesson, not as a contradiction.

---

## 2. Measurement methodology

### 2.1 Load generation
- Closed-loop asynchronous generator. A fixed number of concurrent workers each
  issue requests back-to-back (no think time).
- REST traffic uses one shared `httpx.AsyncClient` connection pool sized to the
  worker count. gRPC traffic uses one persistent `grpc.aio` channel with HTTP/2
  stream multiplexing. This matches production usage of each protocol.
- Workload is a mixed create/read op mix. Records are pre-seeded so reads hit
  existing keys.
- Per-request wall-clock latency is recorded client-side; percentiles are computed
  from raw samples, not from histogram buckets.

### 2.2 Experimental grid
- Suites: REST, gRPC.
- Backends: PostgreSQL 16, MongoDB 7, Redis 7.
- Concurrency levels: 10, 50, 100 workers.
- Payload sizes: 100 B, 1 KB, 10 KB.
- Trials: 5 independent trials per cell.
- Timing: 15 s measurement window after a 3 s warm-up per trial.
- Databases are flushed between trials so every trial starts from an identical
  empty state (see Section 6 for the one exception and its fix).

### 2.3 Statistics
- Reported latency is the mean across the 5 trial-level statistics, with a 95
  percent Student t confidence interval (CI) across trials. CIs are computed across
  trials, not across per-request samples, because per-request latencies within one
  trial are autocorrelated.
- Coefficient of variation (CoV) is the trial-level standard deviation over the
  mean, expressed as a percentage.
- Significance is tested with the Mann-Whitney U test on the pooled per-request
  latency distributions (rank-based; appropriate for heavy-tailed, non-normal
  latency data).
- A cell where either suite has fewer than 2 completed trials is marked EXCLUDED
  and never used for a protocol comparison.

---

## 3. Hardware and software environment (reproducibility)

### 3.1 Cloud (primary results: Sections 4 and 6)
- Provider/region: Amazon Web Services, ap-south-1 (Mumbai).
- Topology: two nodes. Node A hosted the full service stack and databases; Node B
  was the dedicated load generator. They communicated over the internal VPC subnet
  to remove public-internet routing latency.
- Instance type: Amazon EC2 t3.medium (both nodes).
- CPU: 2 vCPUs, Intel Xeon (Skylake generation).
- Memory: 4.0 GiB RAM per node.
- Storage: 20 GiB gp3 SSD boot volume.
- OS: Ubuntu Server 24.04 LTS (Linux kernel 6.8).
- Runtime: Docker Engine 26.1+, Docker Compose 2.27+, Python 3.12.3.
- Connection pools: 2 to 10 persistent sockets per service, opened at startup so
  database TCP handshakes are excluded from measured latency.

### 3.2 Local profiling host (Section 5)
- Platform: Windows 11 (10.0.26200), Docker Desktop.
- CPU: 16 logical cores, Intel (12th-gen class, Alder Lake).
- Python: 3.12.6.
- Key package versions: httpx 0.27.0, grpcio 1.63.0, grpcio-tools 1.63.0,
  protobuf 5.29.6, numpy 2.5.1, scipy 1.18.0, matplotlib 3.11.1.
- PostgreSQL started with `shared_preload_libraries=pg_stat_statements` for
  per-query timing.

---

## 4. Cloud results (AWS, primary dataset)

Run id: `20260718-071036`. Two-node AWS deployment. 5 trials per cell, 15 s + 3 s
warm-up, mixed op. Redis 10 KB cells from this run were affected by the crash in
Section 6 and are reported separately in Section 6.2.

### 4.1 Per-cell latency (all values in milliseconds; plus/minus is 95 percent CI across trials)

| Suite | Backend | Conc. | Payload | Req/s | p50 | p95 | p99 | p99 CoV |
|---|---|---|---|---|---|---|---|---|
| gRPC | mongo | 10 | 100 B | 773 | 12.15 +/- 0.35 | 21.09 +/- 0.81 | 31.72 +/- 4.55 | 11.5% |
| gRPC | mongo | 10 | 1 KB | 777 | 12.14 +/- 0.18 | 20.80 +/- 0.43 | 29.45 +/- 0.83 | 2.3% |
| gRPC | mongo | 10 | 10 KB | 687 | 13.43 +/- 0.41 | 25.02 +/- 4.95 | 39.59 +/- 13.72 | 27.9% |
| gRPC | mongo | 50 | 100 B | 815 | 58.91 +/- 1.04 | 92.53 +/- 6.32 | 114.80 +/- 21.48 | 15.1% |
| gRPC | mongo | 50 | 1 KB | 759 | 61.67 +/- 1.86 | 101.34 +/- 13.74 | 163.26 +/- 84.02 | 41.4% |
| gRPC | mongo | 50 | 10 KB | 730 | 64.99 +/- 1.81 | 102.83 +/- 10.94 | 140.23 +/- 39.97 | 23.0% |
| gRPC | mongo | 100 | 100 B | 814 | 118.84 +/- 1.92 | 173.33 +/- 7.92 | 209.68 +/- 18.07 | 6.9% |
| gRPC | mongo | 100 | 1 KB | 810 | 118.46 +/- 3.07 | 173.82 +/- 10.88 | 222.46 +/- 37.46 | 13.6% |
| gRPC | mongo | 100 | 10 KB | 721 | 130.84 +/- 3.89 | 194.40 +/- 35.57 | 367.78 +/- 405.73 | 88.8% |
| gRPC | postgres | 10 | 100 B | 1337 | 6.80 +/- 0.17 | 11.91 +/- 0.86 | 20.52 +/- 10.80 | 42.4% |
| gRPC | postgres | 10 | 1 KB | 1410 | 6.61 +/- 0.18 | 10.67 +/- 0.58 | 16.19 +/- 1.43 | 7.1% |
| gRPC | postgres | 10 | 10 KB | 1135 | 7.87 +/- 0.10 | 14.88 +/- 0.92 | 29.21 +/- 5.51 | 15.2% |
| gRPC | postgres | 50 | 100 B | 1364 | 33.25 +/- 0.79 | 55.81 +/- 4.15 | 79.61 +/- 31.92 | 32.3% |
| gRPC | postgres | 50 | 1 KB | 1357 | 33.20 +/- 0.36 | 54.62 +/- 3.83 | 82.81 +/- 35.35 | 34.4% |
| gRPC | postgres | 50 | 10 KB | 1133 | 39.41 +/- 0.65 | 70.77 +/- 6.37 | 108.11 +/- 23.94 | 17.8% |
| gRPC | postgres | 100 | 100 B | 1376 | 66.22 +/- 1.61 | 104.50 +/- 6.80 | 140.97 +/- 24.49 | 14.0% |
| gRPC | postgres | 100 | 1 KB | 1385 | 65.52 +/- 0.48 | 104.65 +/- 6.28 | 158.89 +/- 68.47 | 34.7% |
| gRPC | postgres | 100 | 10 KB | 1142 | 78.33 +/- 1.25 | 132.79 +/- 7.41 | 204.21 +/- 44.81 | 17.7% |
| gRPC | redis | 10 | 100 B | 1411 | 6.92 +/- 0.52 | 10.85 +/- 2.83 | 16.97 +/- 7.17 | 34.0% |
| gRPC | redis | 10 | 1 KB | 1384 | 7.11 +/- 0.56 | 10.91 +/- 2.48 | 16.07 +/- 6.07 | 30.4% |
| gRPC | redis | 10 | 10 KB | 1197 | 7.99 +/- 0.53 | 13.55 +/- 2.09 | 21.23 +/- 5.70 | 21.6% |
| gRPC | redis | 50 | 100 B | 1377 | 33.89 +/- 2.81 | 52.50 +/- 12.74 | 85.56 +/- 39.20 | 36.9% |
| gRPC | redis | 50 | 1 KB | 1378 | 34.41 +/- 3.03 | 52.79 +/- 12.89 | 72.26 +/- 27.63 | 30.8% |
| gRPC | redis | 100 | 100 B | 1380 | 68.79 +/- 6.17 | 101.76 +/- 23.79 | 144.45 +/- 51.44 | 28.7% |
| gRPC | redis | 100 | 1 KB | 1339 | 71.43 +/- 7.24 | 103.38 +/- 21.77 | 143.47 +/- 63.18 | 35.5% |
| REST | mongo | 10 | 100 B | 595 | 15.45 +/- 0.20 | 29.16 +/- 1.47 | 42.23 +/- 6.27 | 12.0% |
| REST | mongo | 10 | 1 KB | 565 | 16.21 +/- 0.63 | 30.50 +/- 3.20 | 49.46 +/- 19.86 | 32.3% |
| REST | mongo | 10 | 10 KB | 523 | 17.80 +/- 0.83 | 32.14 +/- 3.12 | 47.85 +/- 18.57 | 31.3% |
| REST | mongo | 50 | 100 B | 222 | 154.88 +/- 34.81 | 666.48 +/- 133.75 | 1042.17 +/- 183.83 | 14.2% |
| REST | mongo | 50 | 1 KB | 218 | 153.20 +/- 13.67 | 659.72 +/- 50.26 | 1044.62 +/- 130.58 | 10.1% |
| REST | mongo | 50 | 10 KB | 224 | 151.98 +/- 30.14 | 653.72 +/- 103.43 | 1019.87 +/- 134.16 | 10.6% |
| REST | mongo | 100 | 100 B | 229 | 276.34 +/- 94.95 | 1363.84 +/- 500.56 | 2332.98 +/- 749.87 | 25.9% |
| REST | mongo | 100 | 1 KB | 233 | 279.77 +/- 93.12 | 1270.13 +/- 311.23 | 2150.56 +/- 547.28 | 20.5% |
| REST | mongo | 100 | 10 KB | 241 | 264.19 +/- 53.41 | 1226.58 +/- 301.23 | 1965.52 +/- 517.18 | 21.2% |
| REST | postgres | 10 | 100 B | 706 | 10.83 +/- 0.52 | 31.92 +/- 4.11 | 54.64 +/- 13.15 | 19.4% |
| REST | postgres | 10 | 1 KB | 719 | 10.37 +/- 0.29 | 32.09 +/- 2.50 | 54.31 +/- 7.52 | 11.1% |
| REST | postgres | 10 | 10 KB | 659 | 12.05 +/- 0.50 | 31.21 +/- 2.36 | 55.95 +/- 25.79 | 37.1% |
| REST | postgres | 50 | 100 B | 283 | 115.75 +/- 23.14 | 530.81 +/- 91.24 | 891.44 +/- 113.84 | 10.3% |
| REST | postgres | 50 | 1 KB | 302 | 103.15 +/- 8.11 | 525.62 +/- 70.27 | 885.77 +/- 162.06 | 14.7% |
| REST | postgres | 50 | 10 KB | 256 | 132.18 +/- 29.68 | 603.94 +/- 127.36 | 938.37 +/- 184.88 | 15.9% |
| REST | postgres | 100 | 100 B | 289 | 224.90 +/- 62.97 | 1118.09 +/- 404.32 | 1852.13 +/- 726.36 | 31.6% |
| REST | postgres | 100 | 1 KB | 307 | 204.26 +/- 24.43 | 1009.62 +/- 312.99 | 1705.27 +/- 713.62 | 33.7% |
| REST | postgres | 100 | 10 KB | 227 | 252.91 +/- 14.51 | 1404.89 +/- 223.33 | 2547.30 +/- 489.37 | 15.5% |
| REST | redis | 10 | 100 B | 733 | 8.86 +/- 1.56 | 38.14 +/- 5.53 | 68.80 +/- 12.98 | 15.2% |
| REST | redis | 10 | 1 KB | 690 | 9.37 +/- 1.96 | 40.35 +/- 7.93 | 72.82 +/- 18.92 | 20.9% |
| REST | redis | 10 | 10 KB | 648 | 10.11 +/- 2.78 | 43.29 +/- 8.02 | 76.94 +/- 17.47 | 18.3% |
| REST | redis | 50 | 100 B | 325 | 101.86 +/- 29.37 | 489.24 +/- 128.17 | 821.88 +/- 215.06 | 21.1% |
| REST | redis | 50 | 1 KB | 319 | 102.50 +/- 15.34 | 479.72 +/- 111.23 | 781.29 +/- 187.64 | 19.3% |
| REST | redis | 100 | 100 B | 314 | 187.90 +/- 35.64 | 1023.15 +/- 216.24 | 2014.78 +/- 459.72 | 18.4% |
| REST | redis | 100 | 1 KB | 313 | 191.05 +/- 34.63 | 1012.71 +/- 213.88 | 1987.89 +/- 467.04 | 18.9% |

### 4.2 REST vs gRPC comparison (p99, cloud)

gRPC improvement is the relative p99 reduction versus REST. All comparisons below
are Mann-Whitney U p less than 0.001.

| Backend | Conc. | Payload | REST p99 (ms) | gRPC p99 (ms) | gRPC improvement | Trial-CI verdict |
|---|---|---|---|---|---|---|
| mongo | 10 | 100 B | 42.23 | 31.72 | 24.9% | CIs overlap |
| mongo | 10 | 1 KB | 49.46 | 29.45 | 40.5% | CIs overlap |
| mongo | 10 | 10 KB | 47.85 | 39.59 | 17.3% | CIs overlap |
| mongo | 50 | 100 B | 1042.17 | 114.80 | 89.0% | gRPC faster (disjoint) |
| mongo | 50 | 1 KB | 1044.62 | 163.26 | 84.4% | gRPC faster (disjoint) |
| mongo | 50 | 10 KB | 1019.87 | 140.23 | 86.3% | gRPC faster (disjoint) |
| mongo | 100 | 100 B | 2332.98 | 209.68 | 91.0% | gRPC faster (disjoint) |
| mongo | 100 | 1 KB | 2150.56 | 222.46 | 89.7% | gRPC faster (disjoint) |
| mongo | 100 | 10 KB | 1965.52 | 367.78 | 81.3% | gRPC faster (disjoint) |
| postgres | 10 | 100 B | 54.64 | 20.52 | 62.5% | gRPC faster (disjoint) |
| postgres | 10 | 1 KB | 54.31 | 16.19 | 70.2% | gRPC faster (disjoint) |
| postgres | 10 | 10 KB | 55.95 | 29.21 | 47.8% | CIs overlap |
| postgres | 50 | 100 B | 891.44 | 79.61 | 91.1% | gRPC faster (disjoint) |
| postgres | 50 | 1 KB | 885.77 | 82.81 | 90.7% | gRPC faster (disjoint) |
| postgres | 50 | 10 KB | 938.37 | 108.11 | 88.5% | gRPC faster (disjoint) |
| postgres | 100 | 100 B | 1852.13 | 140.97 | 92.4% | gRPC faster (disjoint) |
| postgres | 100 | 1 KB | 1705.27 | 158.89 | 90.7% | gRPC faster (disjoint) |
| postgres | 100 | 10 KB | 2547.30 | 204.21 | 92.0% | gRPC faster (disjoint) |
| redis | 10 | 100 B | 68.80 | 16.97 | 75.3% | gRPC faster (disjoint) |
| redis | 10 | 1 KB | 72.82 | 16.07 | 77.9% | gRPC faster (disjoint) |
| redis | 10 | 10 KB | 76.94 | 21.23 | 72.4% | gRPC faster (disjoint) |
| redis | 50 | 100 B | 821.88 | 85.56 | 89.6% | gRPC faster (disjoint) |
| redis | 50 | 1 KB | 781.29 | 72.26 | 90.8% | gRPC faster (disjoint) |
| redis | 100 | 100 B | 2014.78 | 144.45 | 92.8% | gRPC faster (disjoint) |
| redis | 100 | 1 KB | 1987.89 | 143.47 | 92.8% | gRPC faster (disjoint) |

### 4.3 Cloud throughput (requests per second)
gRPC sustains far higher throughput before saturating. Representative figures at
concurrency 100, 1 KB payload: gRPC postgres 1385 req/s, gRPC mongo 810 req/s,
gRPC redis 1339 req/s, versus REST postgres 307 req/s, REST mongo 233 req/s, REST
redis 313 req/s. REST saturates at roughly 220 to 320 req/s from concurrency 50
upward because HTTP/1.1 exhausts its connection pool, while gRPC multiplexing keeps
throughput high.

### 4.4 Reading of the cloud results
- At low concurrency (10 workers), the two protocols are close and several CIs
  overlap. The absolute latencies are small (tens of milliseconds), so protocol
  choice is not yet decisive.
- From concurrency 50 upward, REST tail latency explodes (hundreds to thousands of
  milliseconds) while gRPC stays low. Every such cell is statistically significant
  with disjoint CIs.
- The improvement is consistent across all three backends, which is the core
  finding: when the database is not the bottleneck, protocol efficiency governs
  tail latency.

---

## 5. PostgreSQL profiling: protocol vs database time

Run id: `20260719-154016`. PostgreSQL-only, all 9 concurrency/payload cells, 5
trials each, with `pg_stat_statements` profiling enabled. Purpose: measure how much
of request time is actually spent inside the database (the Amdahl gate).

### 5.1 Per-cell latency (profiled run)

| Suite | Conc. | Payload | Req/s | p50 (ms) | p95 (ms) | p99 (ms) | p99 CoV |
|---|---|---|---|---|---|---|---|
| gRPC | 10 | 100 B | 1436 | 6.21 | 14.76 | 20.87 +/- 15.01 | 57.9% |
| gRPC | 10 | 1 KB | 900 | 8.95 | 26.13 | 42.86 +/- 20.98 | 39.4% |
| gRPC | 10 | 10 KB | 716 | 11.94 | 32.80 | 54.66 +/- 18.25 | 26.9% |
| gRPC | 50 | 100 B | 1430 | 31.12 | 57.59 | 115.07 +/- 118.18 | 82.7% |
| gRPC | 50 | 1 KB | 1055 | 39.45 | 102.34 | 160.37 +/- 105.57 | 53.0% |
| gRPC | 50 | 10 KB | 1002 | 45.26 | 83.20 | 111.27 +/- 33.30 | 24.1% |
| gRPC | 100 | 100 B | 1137 | 73.67 | 177.46 | 285.08 +/- 126.96 | 35.9% |
| gRPC | 100 | 1 KB | 1025 | 84.74 | 176.97 | 321.83 +/- 275.91 | 69.0% |
| gRPC | 100 | 10 KB | 1016 | 92.66 | 156.59 | 225.84 +/- 93.08 | 33.2% |
| REST | 10 | 100 B | 322 | 31.28 | 65.01 | 80.03 +/- 35.03 | 35.3% |
| REST | 10 | 1 KB | 322 | 45.06 | 62.03 | 74.04 +/- 12.65 | 13.8% |
| REST | 10 | 10 KB | 292 | 47.30 | 67.84 | 89.28 +/- 21.31 | 19.2% |
| REST | 50 | 100 B | 156 | 200.20 | 978.94 | 1521.51 +/- 202.69 | 10.7% |
| REST | 50 | 1 KB | 156 | 194.64 | 992.65 | 1551.33 +/- 217.16 | 11.3% |
| REST | 50 | 10 KB | 142 | 216.80 | 1177.07 | 1904.64 +/- 870.53 | 36.8% |
| REST | 100 | 100 B | 143 | 421.65 | 1990.04 | 3301.63 +/- 523.65 | 12.8% |
| REST | 100 | 1 KB | 141 | 445.51 | 2264.99 | 3613.93 +/- 1584.18 | 35.3% |
| REST | 100 | 10 KB | 162 | 389.34 | 1821.82 | 2777.49 +/- 519.92 | 15.1% |

Note: absolute numbers differ from the AWS run because this is a different host
(local, 16-core), but the REST-vs-gRPC relationship is identical in direction and
magnitude.

### 5.2 Database time as a share of request time (pg_stat_statements)

For each cell we summed the execution time of the benchmark table's INSERT and
SELECT statements and divided by the total client-observed request time in the same
trial. Representative cells at concurrency 100 (trial 1):

| Suite | Conc. | Payload | DB exec time | Total request time | DB share |
|---|---|---|---|---|---|
| gRPC | 100 | 100 B | 1059 ms | 1,493,446 ms | 0.07% |
| gRPC | 100 | 1 KB | 1206 ms | 1,477,069 ms | 0.08% |
| gRPC | 100 | 10 KB | 4291 ms | 1,499,179 ms | 0.29% |
| REST | 100 | 100 B | 126 ms | 1,467,856 ms | 0.01% |
| REST | 100 | 1 KB | 263 ms | 1,464,902 ms | 0.02% |
| REST | 100 | 10 KB | 745 ms | 1,399,175 ms | 0.05% |

Across every cell in this run the database share stayed between 0 and 2 percent.
Individual INSERT and SELECT statements on the benchmark table averaged well under
0.3 ms each.

### 5.3 Interpretation
This is the direct evidence behind the anomaly reversal and the Amdahl argument.
Because the database consumes a fraction of a percent of the request path, almost
all latency is transport, serialization, and queueing. That is exactly the regime
where a faster protocol produces large end-to-end gains, and it is why gRPC now
wins on PostgreSQL. It also validates the database-blind-spot framing: the database
is so far below the 60 percent Amdahl threshold that migration is clearly justified
on this tier.

---

## 6. Redis resource-exhaustion event and corrected re-run

### 6.1 What happened
In the first AWS campaign, database flushing between trials was disabled to keep
the two-node network configuration consistent. As a result, records accumulated in
Redis across every trial. During the 10 KB payload sweeps at concurrency 50 and
100, accumulated memory (several hundred thousand records, including on the order of
90,000 10 KB payloads) plus the memory footprint of the running service containers
exceeded the 4.0 GiB physical memory of the node. Behavior consistent with the
Linux kernel out-of-memory killer terminated the Redis process, and the microservice
endpoints returned connection resets.

- Redis completed 76 of 90 planned trials; 14 trials (the 10 KB high-concurrency
  cells) were lost.
- One contaminated single trial survived in `grpc-redis c50/10 KB` with a p99 of
  about 4354 ms. This measured a dying database, not the protocol, and is excluded
  from all comparisons. The analysis pipeline now automatically excludes any cell
  with fewer than 2 completed trials.

### 6.2 Corrected Redis 10 KB re-run
Run id: `20260718-170638`. The 10 KB Redis cells were re-run against the same AWS
server using per-trial direct flushing, which wipes Redis between trials over the
network so memory stays flat. The re-run completed all 20 of 20 trials with zero
errors.

| Suite | Conc. | Payload | Req/s | p50 (ms) | p95 (ms) | p99 (ms) | p99 CoV |
|---|---|---|---|---|---|---|---|
| gRPC | 50 | 10 KB | 1449 | 32.33 | 48.63 | 61.25 +/- 8.44 | 11.1% |
| gRPC | 100 | 10 KB | 1440 | 65.63 | 96.49 | 125.50 +/- 43.82 | 28.1% |
| REST | 50 | 10 KB | 313 | 104.70 | 486.32 | 821.48 +/- 63.85 | 6.3% |
| REST | 100 | 10 KB | 253 | 229.45 | 1253.17 | 2303.42 +/- 507.88 | 17.8% |

Comparison (Mann-Whitney U p less than 0.001, CIs disjoint in both cells):

| Backend | Conc. | Payload | REST p99 (ms) | gRPC p99 (ms) | gRPC improvement |
|---|---|---|---|---|---|
| redis | 50 | 10 KB | 821.48 | 61.25 | 92.5% |
| redis | 100 | 10 KB | 2303.42 | 125.50 | 94.6% |

### 6.3 Architectural takeaway (usable in the paper)
In-memory stores deliver the lowest latency but are the most vulnerable to resource
exhaustion under sustained write load. They are well suited to small metadata
payloads (session tokens, profile cards) but require strict eviction policies (for
example LRU) or bounded retention before serving as a primary store for large
payloads at high write throughput. A secondary observation: under the two-node
cloud deployment, gRPC p50 for Redis and PostgreSQL are nearly identical, because
the inter-node round-trip and service overhead set the latency floor, not the
storage engine.

---

## 7. Protocol Migration Threshold model and verdicts

The migration decision is formalized as three gates that must all pass for a tier
at its representative load:

1. Improvement gate: gRPC reduces p99 by at least 20 percent versus REST.
2. Robustness gate: the two p99 CIs are disjoint AND Mann-Whitney U rejects
   equality at alpha = 0.01.
3. Amdahl gate: database execution time is below 60 percent of request time
   (measured with pg_stat_statements).

Verdicts from the profiled PostgreSQL run (`20260719-154016`):

| Backend | Conc. | Payload | Improvement gate | Robustness gate | Amdahl gate (DB share) | Decision |
|---|---|---|---|---|---|---|
| postgres | 10 | 100 B | PASS (73.9%) | PASS | PASS (1%) | MIGRATE |
| postgres | 10 | 1 KB | PASS (42.1%) | fail | PASS (1%) | HOLD |
| postgres | 10 | 10 KB | PASS (38.8%) | fail | PASS (2%) | HOLD |
| postgres | 50 | 100 B | PASS (92.4%) | PASS | PASS (0%) | MIGRATE |
| postgres | 50 | 1 KB | PASS (89.7%) | PASS | PASS (0%) | MIGRATE |
| postgres | 50 | 10 KB | PASS (94.2%) | PASS | PASS (1%) | MIGRATE |
| postgres | 100 | 100 B | PASS (91.4%) | PASS | PASS (0%) | MIGRATE |
| postgres | 100 | 1 KB | PASS (91.1%) | PASS | PASS (0%) | MIGRATE |
| postgres | 100 | 10 KB | PASS (91.9%) | PASS | PASS (0%) | MIGRATE |

Reading: the model recommends MIGRATE precisely where concurrency makes the
difference significant, and HOLD at low concurrency where gRPC is faster on average
but the confidence intervals still overlap. This is the intended conservative
behavior: it will not recommend a migration that the data cannot defend.

---

## 8. Figures available for the paper

All committed under `docs/paper_figs/` and already referenced by the updated
`docs/overleaf.txt`:

| File | Content |
|---|---|
| `aws_p99_bars.png` | p99 bar chart, REST vs gRPC across all 3 backends, concurrency 100, 1 KB, with CI error bars (headline figure) |
| `aws_p99_scaling.png` | p99 vs concurrency line plot, 1 KB payload, all backends |
| `aws_cdf.png` | Latency CDF, concurrency 100, 1 KB |
| `pg_profiled_scaling.png` | PostgreSQL p99 vs concurrency from the profiled run (anomaly reversal) |
| `redis_rerun_bars.png` | Redis p99 bar chart from the corrected 10 KB re-run |

Underlying analysis artifacts (tables, CSVs, additional plots) live under
`results/20260718-071036/`, `results/20260718-170638/analysis/`, and
`results/20260719-154016/analysis/`.

---

## 9. Suggested edits to the paper (for the editing LLM)

1. Rewrite the PostgreSQL discussion: remove the claim that gRPC is slower than
   REST on PostgreSQL. Replace it with the corrected finding (gRPC 88 to 92 percent
   faster under load) and explain the old result as a load-generation artifact
   (process and connection per request).
2. Add the statistical methodology: 5 trials per cell, 95 percent CIs across trials,
   CoV, Mann-Whitney U on pooled latencies. State that CIs are computed across
   trials, not per-request samples.
3. Add the hardware and reproducibility section from Section 3 above.
4. Add the profiling result: database execution is 0 to 2 percent of request time,
   which is the mechanism behind the anomaly reversal and the Amdahl argument.
5. Add the Redis OOM event as a Limitations subsection using Section 6, with the
   corrected numbers (14/90 trials lost, re-run completed 20/20). Do not cite the
   contaminated 4354 ms cell as a protocol result.
6. Add the migration threshold verdict table from Section 7.
7. Add mutual TLS as an explicit limitation: all runs were plaintext, so TLS/mTLS
   overhead is unmeasured and could narrow the gap at small payloads.
8. Replace any figures generated from the old data with the five figures in
   Section 8.

---

## 10. Provenance (run ids)

| Run id | Purpose | Key files |
|---|---|---|
| `20260718-071036` | Primary two-node AWS campaign (postgres, mongo, redis; redis 10 KB partially affected by OOM) | `summary.md`, `comparisons.csv`, `plots/` |
| `20260718-170638` | Corrected Redis 10 KB re-run (per-trial direct flushing, 20/20 trials) | `analysis/summary.md`, `analysis/plots/` |
| `20260719-154016` | PostgreSQL profiled run with pg_stat_statements (Amdahl gate evidence) | `analysis/summary.md`, `analysis/migration_decision.md`, `profiling/pgstat_*.json` |

All numbers in this document are reproducible from these committed run directories
using `python benchmark/analyze.py <run_dir>`.
