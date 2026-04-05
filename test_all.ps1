# test_all.ps1 — ProtocolShift end-to-end smoke test
#
# Tests all 5 CRUD operations on every service:
#   REST  → Postgres (:8001), MongoDB (:8002), Redis (:8003)
#   gRPC  → Postgres (:50051), MongoDB (:50052), Redis (:50053)
#
# Requirements:
#   - REST services must be running  (docker compose up  OR  local uvicorn)
#   - grpcurl.exe must be on PATH for gRPC tests  (https://github.com/fullstorydev/grpcurl/releases)
#     If not found, gRPC tests are skipped with a warning.
#
# Usage:
#   .\test_all.ps1
#   .\test_all.ps1 -SkipGrpc        # REST only
#   .\test_all.ps1 -Verbose         # show full response bodies

param(
    [switch]$SkipGrpc,
    [switch]$Verbose
)

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
$PassCount = 0
$FailCount = 0

function Pass($label) {
    Write-Host "  [PASS] $label" -ForegroundColor Green
    $script:PassCount++
}

function Fail($label, $detail = "") {
    Write-Host "  [FAIL] $label" -ForegroundColor Red
    if ($detail) { Write-Host "         $detail" -ForegroundColor DarkRed }
    $script:FailCount++
}

function Header($title) {
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host "  $title" -ForegroundColor Cyan
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
}

function Section($title) {
    Write-Host ""
    Write-Host "  ── $title" -ForegroundColor Yellow
}

# Calls a REST endpoint, returns parsed JSON or $null on failure
function Invoke-Rest($method, $url, $body = $null) {
    try {
        $params = @{ Method = $method; Uri = $url; ErrorAction = "Stop" }
        if ($body) {
            $params.Body        = ($body | ConvertTo-Json -Compress)
            $params.ContentType = "application/json"
        }
        $resp = Invoke-RestMethod @params
        if ($Verbose) { $resp | ConvertTo-Json | Write-Host -ForegroundColor DarkGray }
        return $resp
    } catch {
        return $null
    }
}

# ─────────────────────────────────────────────
# REST test runner
# ─────────────────────────────────────────────
function Test-RestService($name, $base) {
    Header "REST — $name  ($base)"

    # ── Health check ──────────────────────────
    Section "GET /healthz"
    $h = Invoke-Rest "GET" "$base/healthz"
    if ($h -and $h.status -eq "ok") { Pass "Health check" }
    else                             { Fail "Health check" "Service not reachable at $base" ; return }

    # ── CREATE ───────────────────────────────
    Section "POST /records  (Create)"
    $created = Invoke-Rest "POST" "$base/records" @{ payload = "benchmark-smoke-test" }
    if ($created -and $null -ne $created.id -and $created.payload -eq "benchmark-smoke-test") {
        Pass "Create  →  id=$($created.id)"
    } else {
        Fail "Create" "Unexpected response: $created" ; return
    }
    $id = $created.id

    # ── READ ─────────────────────────────────
    Section "GET /records/$id  (Read)"
    $read = Invoke-Rest "GET" "$base/records/$id"
    if ($read -and $read.id -eq $id -and $read.payload -eq "benchmark-smoke-test") {
        Pass "Read    →  id=$id payload='$($read.payload)'"
    } else {
        Fail "Read"
    }

    # ── LIST ─────────────────────────────────
    Section "GET /records  (ReadAll)"
    $list = Invoke-Rest "GET" "$base/records?limit=10&offset=0"
    if ($list -and $list.total -ge 1 -and $list.records.Count -ge 1) {
        Pass "ReadAll →  total=$($list.total), returned=$($list.records.Count)"
    } else {
        Fail "ReadAll"
    }

    # ── UPDATE ───────────────────────────────
    Section "PUT /records/$id  (Update)"
    $updated = Invoke-Rest "PUT" "$base/records/$id" @{ payload = "updated-smoke-test" }
    if ($updated -and $updated.id -eq $id -and $updated.payload -eq "updated-smoke-test") {
        Pass "Update  →  payload='$($updated.payload)'"
    } else {
        Fail "Update"
    }

    # ── DELETE ───────────────────────────────
    Section "DELETE /records/$id  (Delete)"
    $deleted = Invoke-Rest "DELETE" "$base/records/$id"
    if ($deleted -and $deleted.success -eq $true) {
        Pass "Delete  →  id=$id removed"
    } else {
        Fail "Delete"
    }

    # ── VERIFY GONE ──────────────────────────
    Section "GET /records/$id  (Verify 404 after delete)"
    try {
        Invoke-RestMethod -Method GET -Uri "$base/records/$id" -ErrorAction Stop | Out-Null
        Fail "404 after delete" "Record still exists after DELETE"
    } catch {
        if ($_.Exception.Response.StatusCode.value__ -eq 404) {
            Pass "404 confirmed — record correctly removed"
        } else {
            Fail "404 after delete" "Got unexpected status: $($_.Exception.Response.StatusCode)"
        }
    }
}

# ─────────────────────────────────────────────
# gRPC test runner  (requires grpcurl on PATH)
# ─────────────────────────────────────────────
function Invoke-Grpc($host, $rpc, $data) {
    $json = $data | ConvertTo-Json -Compress
    $raw = grpcurl -plaintext -d $json $host "benchmark.BenchmarkService/$rpc" 2>&1
    if ($LASTEXITCODE -ne 0) { return $null }
    try   { return $raw | ConvertFrom-Json }
    catch { return $null }
}

function Test-GrpcService($name, $host) {
    Header "gRPC — $name  ($host)"

    # ── CREATE ───────────────────────────────
    Section "Create RPC"
    $created = Invoke-Grpc $host "Create" @{ payload = "benchmark-smoke-test" }
    if ($created -and $null -ne $created.id -and $created.payload -eq "benchmark-smoke-test") {
        Pass "Create  →  id=$($created.id)"
    } else {
        Fail "Create" "grpcurl response: $created" ; return
    }
    $id = [int]$created.id

    # ── READ ─────────────────────────────────
    Section "Read RPC"
    $read = Invoke-Grpc $host "Read" @{ id = $id }
    if ($read -and [int]$read.id -eq $id -and $read.payload -eq "benchmark-smoke-test") {
        Pass "Read    →  id=$id payload='$($read.payload)'"
    } else {
        Fail "Read"
    }

    # ── READALL ──────────────────────────────
    Section "ReadAll RPC"
    $list = Invoke-Grpc $host "ReadAll" @{ limit = 10; offset = 0 }
    if ($list -and [int]$list.total -ge 1) {
        Pass "ReadAll →  total=$($list.total)"
    } else {
        Fail "ReadAll"
    }

    # ── UPDATE ───────────────────────────────
    Section "Update RPC"
    $updated = Invoke-Grpc $host "Update" @{ id = $id; payload = "updated-smoke-test" }
    if ($updated -and $updated.payload -eq "updated-smoke-test") {
        Pass "Update  →  payload='$($updated.payload)'"
    } else {
        Fail "Update"
    }

    # ── DELETE ───────────────────────────────
    Section "Delete RPC"
    $deleted = Invoke-Grpc $host "Delete" @{ id = $id }
    if ($deleted -and $deleted.success -eq $true) {
        Pass "Delete  →  id=$id removed"
    } else {
        Fail "Delete"
    }

    # ── VERIFY GONE ──────────────────────────
    Section "Read after Delete (expect NOT_FOUND)"
    $gone = grpcurl -plaintext -d "{`"id`": $id}" $host "benchmark.BenchmarkService/Read" 2>&1
    if ($gone -match "NOT_FOUND") {
        Pass "NOT_FOUND confirmed — record correctly removed"
    } else {
        Fail "NOT_FOUND after delete" "Got: $gone"
    }
}

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════╗" -ForegroundColor Magenta
Write-Host "║   ProtocolShift — Full Smoke Test                ║" -ForegroundColor Magenta
Write-Host "╚══════════════════════════════════════════════════╝" -ForegroundColor Magenta
Write-Host "  Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# ── REST suite ────────────────────────────────
Test-RestService "PostgreSQL" "http://localhost:8001"
Test-RestService "MongoDB"    "http://localhost:8002"
Test-RestService "Redis"      "http://localhost:8003"

# ── gRPC suite ────────────────────────────────
if (-not $SkipGrpc) {
    $grpcurl = Get-Command grpcurl -ErrorAction SilentlyContinue
    if (-not $grpcurl) {
        Write-Host ""
        Write-Host "  [WARN] grpcurl not found on PATH — skipping gRPC tests." -ForegroundColor DarkYellow
        Write-Host "         Download from: https://github.com/fullstorydev/grpcurl/releases" -ForegroundColor DarkYellow
    } else {
        Test-GrpcService "PostgreSQL" "localhost:50051"
        Test-GrpcService "MongoDB"    "localhost:50052"
        Test-GrpcService "Redis"      "localhost:50053"
    }
}

# ── Summary ───────────────────────────────────
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
$total = $PassCount + $FailCount
Write-Host "  Results:  $PassCount / $total passed" -ForegroundColor $(if ($FailCount -eq 0) { "Green" } else { "Yellow" })
if ($FailCount -gt 0) {
    Write-Host "  $FailCount test(s) FAILED — check service logs" -ForegroundColor Red
}
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host ""

exit $FailCount   # exit code 0 = all passed, non-zero = failures
