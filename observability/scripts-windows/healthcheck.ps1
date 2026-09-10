# scripts-windows/healthcheck.ps1 — Health check toàn hệ thống (Windows)
# Chạy: .\scripts-windows\healthcheck.ps1

if ($env:BASE_URL) { $BASE_URL = $env:BASE_URL } else { $BASE_URL = "http://localhost:8080" }
$Pass = 0; $Fail = 0

function Check-URL {
    param($Name, $Url, $Expected = 200)
    try {
        $resp = Invoke-WebRequest -Uri $Url -TimeoutSec 5 -EA Stop
        if ($resp.StatusCode -eq $Expected) {
            Write-Host "  ✅ $Name ($Url) → $($resp.StatusCode)" -ForegroundColor Green
            $script:Pass++
        } else {
            Write-Host "  ❌ $Name ($Url) → $($resp.StatusCode) (expected $Expected)" -ForegroundColor Red
            $script:Fail++
        }
    } catch {
        Write-Host "  ❌ $Name ($Url) → ERROR: $_" -ForegroundColor Red
        $script:Fail++
    }
}

Write-Host ""
Write-Host "════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Health Check — Retail Order Platform Lab" -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════" -ForegroundColor Cyan

Write-Host "`n── Application Services ─────────────────────"
Check-URL "Gateway"              "$BASE_URL/health"
Check-URL "Order Service"        "http://localhost:8081/health"
Check-URL "Inventory Service"    "http://localhost:8082/health"
Check-URL "Payment Service"      "http://localhost:8083/health"
Check-URL "Notification Service" "http://localhost:8084/health"
Check-URL "Worker"               "http://localhost:8085/health"

Write-Host "`n── Observability Stack ──────────────────────"
Check-URL "Prometheus"    "http://localhost:9090/-/healthy"
Check-URL "Grafana"       "http://localhost:3000/api/health"
Check-URL "Alertmanager"  "http://localhost:9093/-/healthy"
Check-URL "Loki"          "http://localhost:3100/ready"
Check-URL "Tempo"         "http://localhost:3200/ready"

Write-Host "`n── Infrastructure ───────────────────────────"
Check-URL "RabbitMQ Mgmt" "http://localhost:15672"

Write-Host ""
Write-Host "════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Result: $Pass passed, $Fail failed" -ForegroundColor $(if ($Fail -eq 0) { "Green" } else { "Yellow" })
Write-Host "════════════════════════════════════════════" -ForegroundColor Cyan

# Smoke request
Write-Host "`n── Smoke Request ────────────────────────────"
$body = @{
    user_id="u001"; product_id="p001"; quantity=1; payment_method="mock_card"
} | ConvertTo-Json

try {
    $r = Invoke-RestMethod -Method Post -Uri "$BASE_URL/api/orders" `
        -Headers @{"X-Request-ID"="hc-$(Get-Date -Format 'yyyyMMddHHmmss')"} `
        -ContentType "application/json" -Body $body
    Write-Host "  ✅ Smoke request: order $($r.order_id)" -ForegroundColor Green
} catch {
    Write-Host "  ❌ Smoke request failed: $_" -ForegroundColor Red
}
Write-Host ""
