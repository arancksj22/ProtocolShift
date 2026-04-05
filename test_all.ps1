# test_all.ps1 -- ProtocolShift end-to-end smoke test
#
# Tests all 5 CRUD operations on every service:
#   REST  -- Postgres (:8001), MongoDB (:8002), Redis (:8003)
#   gRPC  -- Postgres (:50051), MongoDB (:50052), Redis (:50053)
#
# Requirements:
#   - REST services must be running (docker compose up OR local uvicorn)
#   - grpcurl.exe on PATH for gRPC tests (https://github.com/fullstorydev/grpcurl/releases)
#     If not found, gRPC tests are skipped automatically.
#
# Usage:
#   .\test_all.ps1
#   .\test_all.ps1 -SkipGrpc
#   .\test_all.ps1 -Verbose

param(
    [switch]$SkipGrpc,
    [switch]$Verbose
)

$PassCount = 0
$FailCount = 0

function Pass($label) {
    Write-Host "  [PASS] $label" -ForegroundColor Green
    $script:PassCount++
}

function Fail($label, $detail) {
    Write-Host "  [FAIL] $label" -ForegroundColor Red
    if ($detail) { Write-Host "         $detail" -ForegroundColor DarkRed }
    $script:FailCount++
}

function Header($title) {
    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host "  $title" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
}

function Section($title) {
    Write-Host ""
    Write-Host "  -- $title" -ForegroundColor Yellow
}

function Invoke-Rest($method, $url, $body) {
    try {
        $params = @{ Method = $method; Uri = $url; ErrorAction = "Stop" }
        if ($body) {
            $params.Body        = ($body | ConvertTo-Json -Compress)
            $params.ContentType = "application/json"
        }
        $resp = Invoke-RestMethod @params
        if ($Verbose) { $resp | ConvertTo-Json | Write-Host -ForegroundColor DarkGray }
        return $resp
    }
    catch {
        return $null
    }
}

# ---------------------------------------------------------
# REST test runner
# ---------------------------------------------------------
function Test-RestService($name, $base) {
    Header "REST -- $name  ($base)"

    Section "GET /healthz"
    $h = Invoke-Rest "GET" "$base/healthz"
    if ($h -and $h.status -eq "ok") {
        Pass "Health check"
    }
    else {
        Fail "Health check" "Service not reachable at $base -- skipping"
        return
    }

    Section "POST /records  (Create)"
    $created = Invoke-Rest "POST" "$base/records" @{ payload = "benchmark-smoke-test" }
    if ($created -and ($null -ne $created.id) -and $created.payload -eq "benchmark-smoke-test") {
        Pass "Create  -->  id=$($created.id)"
    }
    else {
        Fail "Create" "Unexpected response"
        return
    }
    $id = $created.id

    Section "GET /records/$id  (Read)"
    $read = Invoke-Rest "GET" "$base/records/$id"
    if ($read -and $read.id -eq $id -and $read.payload -eq "benchmark-smoke-test") {
        Pass "Read    -->  id=$id  payload='$($read.payload)'"
    }
    else {
        Fail "Read"
    }

    Section "GET /records  (ReadAll)"
    $list = Invoke-Rest "GET" "$base/records?limit=10&offset=0"
    if ($list -and $list.total -ge 1 -and $list.records.Count -ge 1) {
        Pass "ReadAll -->  total=$($list.total)  returned=$($list.records.Count)"
    }
    else {
        Fail "ReadAll"
    }

    Section "PUT /records/$id  (Update)"
    $updated = Invoke-Rest "PUT" "$base/records/$id" @{ payload = "updated-smoke-test" }
    if ($updated -and $updated.id -eq $id -and $updated.payload -eq "updated-smoke-test") {
        Pass "Update  -->  payload='$($updated.payload)'"
    }
    else {
        Fail "Update"
    }

    Section "DELETE /records/$id  (Delete)"
    $deleted = Invoke-Rest "DELETE" "$base/records/$id"
    if ($deleted -and $deleted.success -eq $true) {
        Pass "Delete  -->  id=$id removed"
    }
    else {
        Fail "Delete"
    }

    Section "GET /records/$id  (Verify 404 after delete)"
    try {
        Invoke-RestMethod -Method GET -Uri "$base/records/$id" -ErrorAction Stop | Out-Null
        Fail "404 after delete" "Record still exists after DELETE"
    }
    catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code -eq 404) {
            Pass "404 confirmed -- record correctly removed"
        }
        else {
            Fail "404 after delete" "Got HTTP $code"
        }
    }
}

# ---------------------------------------------------------
# gRPC test runner (requires grpcurl on PATH)
# ---------------------------------------------------------
function Invoke-Grpc($address, $rpc, $data) {
    $json = $data | ConvertTo-Json -Compress
    # Wrap in single quotes so PowerShell doesn't strip the double quotes inside the JSON
    $jsonArg = "'$json'"
    $raw = Invoke-Expression "grpcurl -plaintext -d $jsonArg $address `"benchmark.BenchmarkService/$rpc`" 2>&1"
    if ($LASTEXITCODE -ne 0) { return "ERROR: $raw" }
    try   { return $raw | ConvertFrom-Json }
    catch { return "JSON PARSE ERROR: $raw" }
}

function Test-GrpcService($name, $address) {
    Header "gRPC -- $name  ($address)"

    Section "Create RPC"
    $created = Invoke-Grpc $address "Create" @{ payload = "benchmark-smoke-test" }
    if ($created -and ($null -ne $created.id) -and $created.payload -eq "benchmark-smoke-test") {
        Pass "Create  -->  id=$($created.id)"
    }
    else {
        Fail "Create" "grpcurl response: $created"
        return
    }
    $id = [int]$created.id

    Section "Read RPC"
    $read = Invoke-Grpc $address "Read" @{ id = $id }
    if ($read -and [int]$read.id -eq $id -and $read.payload -eq "benchmark-smoke-test") {
        Pass "Read    -->  id=$id  payload='$($read.payload)'"
    }
    else {
        Fail "Read"
    }

    Section "ReadAll RPC"
    $list = Invoke-Grpc $address "ReadAll" @{ limit = 10; offset = 0 }
    if ($list -and [int]$list.total -ge 1) {
        Pass "ReadAll -->  total=$($list.total)"
    }
    else {
        Fail "ReadAll"
    }

    Section "Update RPC"
    $updated = Invoke-Grpc $address "Update" @{ id = $id; payload = "updated-smoke-test" }
    if ($updated -and $updated.payload -eq "updated-smoke-test") {
        Pass "Update  -->  payload='$($updated.payload)'"
    }
    else {
        Fail "Update"
    }

    Section "Delete RPC"
    $deleted = Invoke-Grpc $address "Delete" @{ id = $id }
    if ($deleted -and $deleted.success -eq $true) {
        Pass "Delete  -->  id=$id removed"
    }
    else {
        Fail "Delete"
    }

    Section "Read after Delete (expect NOT_FOUND)"
    $gone = grpcurl -plaintext -d "{`"id`": $id}" $address "benchmark.BenchmarkService/Read" 2>&1
    if ($gone -match "NOT_FOUND") {
        Pass "NOT_FOUND confirmed -- record correctly removed"
    }
    else {
        Fail "NOT_FOUND after delete" "Got: $gone"
    }
}

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
Write-Host ""
Write-Host "================================================" -ForegroundColor Magenta
Write-Host "   ProtocolShift -- Full Smoke Test             " -ForegroundColor Magenta
Write-Host "================================================" -ForegroundColor Magenta
Write-Host "  Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# REST suite
Test-RestService "PostgreSQL" "http://localhost:8001"
Test-RestService "MongoDB"    "http://localhost:8002"
Test-RestService "Redis"      "http://localhost:8003"

# gRPC suite
if ($SkipGrpc) {
    Write-Host ""
    Write-Host "  [INFO] gRPC tests skipped (-SkipGrpc flag)." -ForegroundColor DarkYellow
}
elseif (-not (Get-Command grpcurl -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "  [WARN] grpcurl not found on PATH -- skipping gRPC tests." -ForegroundColor DarkYellow
    Write-Host "         Download: https://github.com/fullstorydev/grpcurl/releases" -ForegroundColor DarkYellow
}
else {
    Test-GrpcService "PostgreSQL" "localhost:50051"
    Test-GrpcService "MongoDB"    "localhost:50052"
    Test-GrpcService "Redis"      "localhost:50053"
}

# Summary
$total = $PassCount + $FailCount
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
if ($FailCount -eq 0) {
    Write-Host "  Results:  $PassCount / $total passed -- ALL GOOD" -ForegroundColor Green
}
else {
    Write-Host "  Results:  $PassCount / $total passed" -ForegroundColor Yellow
    Write-Host "  $FailCount FAILED -- check service logs" -ForegroundColor Red
}
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

exit $FailCount
