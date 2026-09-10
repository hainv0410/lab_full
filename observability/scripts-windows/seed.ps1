# scripts-windows/seed.ps1 — Seed dữ liệu mẫu (Windows)
# Chạy: .\scripts-windows\seed.ps1

if ($env:BASE_URL) { $BASE_URL = $env:BASE_URL } else { $BASE_URL = "http://localhost:8080" }
$ErrorActionPreference = "Continue"

Write-Host "════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Seeding lab data..." -ForegroundColor Cyan
Write-Host "════════════════════════════════════" -ForegroundColor Cyan

# Wait for gateway
Write-Host "`n⏳ Waiting for Gateway..."
for ($i = 1; $i -le 20; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "$BASE_URL/health" -TimeoutSec 3 -EA Stop
        if ($resp.StatusCode -eq 200) {
            Write-Host "✅ Gateway is up" -ForegroundColor Green
            break
        }
    } catch {
        Write-Host "   Attempt $i/20..."
        Start-Sleep -Seconds 3
    }
}

# List products
Write-Host "`n📦 Products available:"
try {
    $products = Invoke-RestMethod -Uri "$BASE_URL/api/products"
    $products | ForEach-Object { Write-Host "  - $($_.id): $($_.name) ($($_.price) USD)" }
} catch {
    Write-Host "  Could not list products: $_" -ForegroundColor Yellow
}

# Send smoke order
Write-Host "`n🛒 Sending smoke order..."
$body = @{
    user_id        = "u001"
    product_id     = "p001"
    quantity       = 1
    payment_method = "mock_card"
} | ConvertTo-Json

try {
    $result = Invoke-RestMethod `
        -Method Post `
        -Uri "$BASE_URL/api/orders" `
        -Headers @{ "X-Request-ID" = "seed-win-001" } `
        -ContentType "application/json" `
        -Body $body
    Write-Host "✅ Order created: $($result.order_id)" -ForegroundColor Green
    Write-Host "   Trace ID: $($result.trace_id)"
} catch {
    Write-Host "❌ Order failed: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "────────────────────────────────────────"
Write-Host "Next steps:"
Write-Host "  Grafana:      http://localhost:3000  (admin/admin)"
Write-Host "  Prometheus:   http://localhost:9090"
Write-Host "  Alertmanager: http://localhost:9093"
Write-Host "  RabbitMQ:     http://localhost:15672 (lab/lab)"
Write-Host "────────────────────────────────────────"
