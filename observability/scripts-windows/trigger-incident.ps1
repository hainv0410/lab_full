# scripts-windows/trigger-incident.ps1 — Fault injection (Windows)
# Usage: .\scripts-windows\trigger-incident.ps1 payment-timeout
param([string]$Incident = "help")

$ErrorActionPreference = "Continue"

function Invoke-Post($Url, $Body) {
    try {
        $r = Invoke-RestMethod -Method Post -Uri $Url `
            -ContentType "application/json" -Body ($Body | ConvertTo-Json) -EA Stop
        $r | ConvertTo-Json
    } catch { Write-Host "  ERROR: $_" -ForegroundColor Red }
}

Write-Host ""
Write-Host "════════════════════════════════════════════" -ForegroundColor Yellow
Write-Host "  Incident Trigger: $Incident" -ForegroundColor Yellow
Write-Host "════════════════════════════════════════════" -ForegroundColor Yellow

switch ($Incident) {
    "payment-timeout" {
        Write-Host "⚡ Injecting: Payment Timeout (3000ms, 15min)" -ForegroundColor Red
        Invoke-Post "http://localhost:8083/admin/faults" @{fault="timeout";delay_ms=3000;duration_seconds=900}
        Write-Host "`n📊 Expected: p95 spike, PAYMENT_TIMEOUT logs, alert firing" -ForegroundColor Cyan
    }
    "payment-error" {
        Write-Host "⚡ Injecting: Payment Error (500 responses)" -ForegroundColor Red
        Invoke-Post "http://localhost:8083/admin/faults" @{fault="error";duration_seconds=900}
    }
    "retry-storm" {
        Write-Host "⚡ Injecting: Retry Storm" -ForegroundColor Red
        Invoke-Post "http://localhost:8081/admin/config" @{max_retry=5;payment_timeout_ms=5000;circuit_breaker_enabled=$false}
        Invoke-Post "http://localhost:8083/admin/faults" @{fault="slow";delay_ms=1500;duration_seconds=900}
        Write-Host "`n📊 Expected: multiple RETRY_ATTEMPT logs per trace, request volume spike" -ForegroundColor Cyan
    }
    "queue-backlog" {
        Write-Host "⚡ Injecting: Slow Consumer → Queue Backlog" -ForegroundColor Red
        Invoke-Post "http://localhost:8085/admin/faults" @{fault="slow_consumer";delay_ms=3000}
        Write-Host "`n📊 Expected: API still 200, but queue depth grows, backlog alert fires" -ForegroundColor Cyan
    }
    "clear" {
        Write-Host "🔄 Clearing all faults..." -ForegroundColor Green
        Invoke-Post "http://localhost:8083/admin/faults" @{fault="none"} | Out-Null
        Write-Host "  ✅ Payment service: cleared" -ForegroundColor Green
        Invoke-Post "http://localhost:8085/admin/faults" @{fault="none"} | Out-Null
        Write-Host "  ✅ Worker: cleared" -ForegroundColor Green
        Invoke-Post "http://localhost:8081/admin/config" @{max_retry=2;payment_timeout_ms=2000;circuit_breaker_enabled=$false;payment_fallback_enabled=$false} | Out-Null
        Write-Host "  ✅ Order service config: reset" -ForegroundColor Green
    }
    "workaround-payment" {
        Write-Host "🛠️  Applying workaround: fallback + reduced retry" -ForegroundColor Green
        Invoke-Post "http://localhost:8081/admin/config" @{
            payment_timeout_ms=1500; max_retry=1
            payment_fallback_enabled=$true; circuit_breaker_enabled=$true
        }
    }
    default {
        Write-Host "Usage: .\trigger-incident.ps1 <incident>"
        Write-Host ""
        Write-Host "Incidents:"
        Write-Host "  payment-timeout    Payment 3s delay"
        Write-Host "  payment-error      Payment 500 errors"
        Write-Host "  retry-storm        Aggressive retry + slow payment"
        Write-Host "  queue-backlog      Slow worker consumer"
        Write-Host ""
        Write-Host "Recovery:"
        Write-Host "  clear              Clear all faults"
        Write-Host "  workaround-payment Apply fallback + circuit breaker"
    }
}
Write-Host ""
