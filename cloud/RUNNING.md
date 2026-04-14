# ProtocolShift — Cloud Backends (Latency Testbed)

> **Goal:** Measure network latency boundaries by running the REST and gRPC application layers locally on your machine, while persisting the data to managed cloud databases (Supabase, MongoDB Atlas, Upstash Redis).

---

## Prerequisites

Ensure you have your fully configured `.env` file inside the `cloud/` directory containing:

```env
POSTGRES_DSN=postgresql://...
MONGO_URI=mongodb+srv://...
REDIS_URL=rediss://...
```

## Step 1 — Start the services

Open PowerShell, navigate to the `cloud` folder, and spin up the modified architecture:

```powershell
cd e:\CodingVacation\PracticeRepos\ProtocolShift_DistributedComputing\cloud
docker compose --env-file .env up --build
```

Unlike the local version, this architecture natively connects to external databases meaning there is no startup delay waiting for local databases to initialize.

Wait until you see the standard uvicorn and gRPC messages confirming the services are listening:

```
rest-postgres  | Uvicorn running on http://0.0.0.0:8001
rest-mongo     | Uvicorn running on http://0.0.0.0:8002
rest-redis     | Uvicorn running on http://0.0.0.0:8003
grpc-postgres  | gRPC PostgreSQL service listening on :50051
grpc-mongo     | gRPC MongoDB service listening on :50052
grpc-redis     | gRPC Redis service listening on :50053
```

---

## Step 2 — Run the test scripts

Open a second terminal window (ensure you are inside the `cloud/` folder) and execute the tests just like the local variant. Since the ports mapped are identical (`8001-8003`, `50051-50053`), the test scripts function exactly the same:

```powershell
# Run REST tests
.\test_all.ps1 -SkipGrpc
```

```powershell
# Generate REST and gRPC load simultaneously
$restJob = Start-Job {
    1..200 | ForEach-Object {
        Invoke-RestMethod -Method POST -Uri "http://localhost:8001/records" -ContentType "application/json" -Body '{"payload":"stress-test-cloud-rest"}' | Out-Null
        Invoke-RestMethod -Method POST -Uri "http://localhost:8002/records" -ContentType "application/json" -Body '{"payload":"stress-test-cloud-rest"}' | Out-Null
        Invoke-RestMethod -Method POST -Uri "http://localhost:8003/records" -ContentType "application/json" -Body '{"payload":"stress-test-cloud-rest"}' | Out-Null
    }
}

$grpcJob = Start-Job {
    1..200 | ForEach-Object {
        grpcurl -plaintext -d '{\"payload\": \"stress-test-cloud-grpc\"}' localhost:50051 benchmark.BenchmarkService/Create
        grpcurl -plaintext -d '{\"payload\": \"stress-test-cloud-grpc\"}' localhost:50052 benchmark.BenchmarkService/Create
        grpcurl -plaintext -d '{\"payload\": \"stress-test-cloud-grpc\"}' localhost:50053 benchmark.BenchmarkService/Create
    } | Out-Null
}

# Wait for both protocols to finish blasting traffic
Wait-Job -Job $restJob, $grpcJob
Receive-Job -Job $restJob, $grpcJob
```

```powershell
1..200 | ForEach-Object {
    grpcurl -plaintext -d '{\"payload\": \"stress-test-grpc\"}' localhost:50051 benchmark.BenchmarkService/Create
    grpcurl -plaintext -d '{\"payload\": \"stress-test-grpc\"}' localhost:50052 benchmark.BenchmarkService/Create
    grpcurl -plaintext -d '{\"payload\": \"stress-test-grpc\"}' localhost:50053 benchmark.BenchmarkService/Create
} | Out-Null
```

---

## Step 3 — View Latency Over Cloud

Head over to **Grafana** at http://localhost:3000 to measure the network serialization differences across REST and gRPC over real physical WAN connections. You should see much more distinct margins of latency compared to running against `localhost` databases!