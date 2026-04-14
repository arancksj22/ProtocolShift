# ProtocolShift — Distributed Computing (REST vs gRPC Testbed)

This repository is a **controlled experimental testbed** to measure the impact of migrating from **REST (HTTP/1.1 + JSON)** to **gRPC (HTTP/2 + Protobuf)** while keeping **application/business logic constant**.

The goal is to isolate protocol-level effects (serialization overhead, HTTP/2 multiplexing, header compression) across different storage backends, and validate migration decisions using **observability metrics** rather than anecdotes.

## Architecture diagram

![ProtocolShift architecture diagram](docs/architecture%20diagram%20protocal%20shift.jfif)

## What this project measures

We benchmark the same CRUD-style operations across:

- **REST suite**: HTTP/1.1 endpoints using JSON serialization (measures the “serialization tax” under load).
- **gRPC suite**: Protobuf contracts over HTTP/2 (tests multiplexing effects and binary serialization).

Against three database paradigms:

- **PostgreSQL (relational / “heavy compute”)**: tests whether protocol wins matter when DB/ORM mapping dominates.
- **Redis (in-memory / “fast tier”)**: minimizes DB I/O so protocol/network overhead is the primary variable.
- **MongoDB (document / unstructured payloads)**: tests larger and more flexible payload shapes and fetch patterns.

And we track the **Four Golden Signals** via Prometheus + Grafana:

- **Latency** (focus on tail latency: p95/p99)
- **Traffic**
- **Errors**
- **Saturation**

## Repository structure (initial scaffold)

```
.
├── docs/                   # PDFs of progress report & diagrams
├── services/
│   ├── rest-suite/         # REST (HTTP/1.1 + JSON) service(s)
│   ├── grpc-suite/         # gRPC (HTTP/2 + Protobuf) service(s)
│   └── protobufs/          # .proto files (source of truth)
├── infrastructure/
│   └── aws-lambda/         # Deployment scripts/notes for AWS experiments
├── monitoring/
│   ├── prometheus/         # Scrape configs for golden signals
│   └── grafana/            # Dashboard JSON exports
└── README.md
```

## How you’ll run it (planned)

This repo is currently scaffolded; the following will be added next:

- `infrastructure/docker-compose.yml` to stand up **Postgres + Mongo + Redis** (and optionally Prometheus + Grafana).
- A **REST service** and a **gRPC service** implementing the *same* endpoints/operations.
- Instrumentation endpoints for Prometheus scraping and Grafana dashboards that overlay REST vs gRPC.

## Experiment design notes (from progress report)

- **Amdahl’s Law framing**: total latency is \(network + app processing + DB I/O\). If DB dominates, protocol gains may be negligible.
- **Tail at scale**: averages can hide pain; migration decisions should use **p99** under meaningful load.
- **Strangler Fig migration idea**: route a small percentage of traffic to gRPC, compare metrics, roll back if p99 worsens (future phase).
- **Local vs cloud**: AWS experiments matter because real environments introduce jitter, TLS/mTLS overhead, and network path variability (future phase).

## Next implementation milestones

- Define `.proto` contracts in `services/protobufs/` and generate server/client stubs.
- Implement a consistent CRUD workload in both `services/rest-suite/` and `services/grpc-suite/`.
- Add docker-compose + observability stack and publish baseline dashboards.

