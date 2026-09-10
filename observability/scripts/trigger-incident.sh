#!/usr/bin/env bash
# scripts/trigger-incident.sh — Fault injection cho Day 4/5
# Usage:
#   ./scripts/trigger-incident.sh payment-timeout
#   ./scripts/trigger-incident.sh retry-storm
#   ./scripts/trigger-incident.sh queue-backlog
#   ./scripts/trigger-incident.sh clear

INCIDENT="${1:-help}"

echo ""
echo "════════════════════════════════════════════"
echo "  Incident Trigger: $INCIDENT"
echo "════════════════════════════════════════════"

case "$INCIDENT" in

  payment-timeout)
    echo "⚡ Injecting: Payment Service TIMEOUT (3000ms delay, 15min)"
    curl -s -X POST http://localhost:8083/admin/faults \
      -H "Content-Type: application/json" \
      -d '{"fault":"timeout","delay_ms":3000,"duration_seconds":900}' | python3 -m json.tool
    echo ""
    echo "📊 Expected symptoms:"
    echo "  - p95/p99 latency spike on Order Service"
    echo "  - HighOrderLatencyP95 alert fires after 5min"
    echo "  - HighPaymentTimeouts alert fires"
    echo "  - Traces show payment-service span very slow"
    echo "  - Logs show PAYMENT_TIMEOUT errors"
    ;;

  payment-error)
    echo "⚡ Injecting: Payment Service ERROR (500 responses)"
    curl -s -X POST http://localhost:8083/admin/faults \
      -H "Content-Type: application/json" \
      -d '{"fault":"error","duration_seconds":900}' | python3 -m json.tool
    echo ""
    echo "📊 Expected symptoms:"
    echo "  - Error rate spike on Create Order journey"
    echo "  - HighCreateOrderErrorRate alert fires"
    echo "  - HighPaymentErrorRate alert fires"
    ;;

  retry-storm)
    echo "⚡ Injecting: Retry Storm (high retry + payment slow)"
    # Set aggressive retry config on order-service
    curl -s -X POST http://localhost:8081/admin/config \
      -H "Content-Type: application/json" \
      -d '{"max_retry":5,"payment_timeout_ms":5000,"circuit_breaker_enabled":false}' | python3 -m json.tool
    # Add slow payment
    curl -s -X POST http://localhost:8083/admin/faults \
      -H "Content-Type: application/json" \
      -d '{"fault":"slow","delay_ms":1500,"duration_seconds":900}' | python3 -m json.tool
    echo ""
    echo "📊 Expected symptoms:"
    echo "  - Request volume spike (retries amplifying load)"
    echo "  - Payment service gets hammered"
    echo "  - Traces show multiple payment spans per order"
    echo "  - Logs show RETRY_ATTEMPT many times"
    echo "  - HighRetryRate alert fires"
    ;;

  queue-backlog)
    echo "⚡ Injecting: Queue Backlog (slow worker consumer)"
    curl -s -X POST http://localhost:8085/admin/faults \
      -H "Content-Type: application/json" \
      -d '{"fault":"slow_consumer","delay_ms":3000}' | python3 -m json.tool
    echo ""
    echo "📊 Expected symptoms:"
    echo "  - API still returns 200 (HTTP layer fine)"
    echo "  - Queue depth grows steadily"
    echo "  - NotificationQueueBacklog alert fires"
    echo "  - Worker throughput drops in dashboard"
    echo "  - WorkerDown NOT firing (worker still alive)"
    ;;

  slow-payment)
    echo "⚡ Injecting: Slow Payment (1500ms delay)"
    curl -s -X POST http://localhost:8083/admin/faults \
      -H "Content-Type: application/json" \
      -d '{"fault":"slow","delay_ms":1500,"duration_seconds":900}' | python3 -m json.tool
    ;;

  clear)
    echo "🔄 Clearing all faults..."
    curl -s -X POST http://localhost:8083/admin/faults \
      -H "Content-Type: application/json" \
      -d '{"fault":"none"}' > /dev/null && echo "  ✅ Payment service: cleared"

    curl -s -X POST http://localhost:8085/admin/faults \
      -H "Content-Type: application/json" \
      -d '{"fault":"none"}' > /dev/null && echo "  ✅ Worker: cleared"

    curl -s -X POST http://localhost:8081/admin/config \
      -H "Content-Type: application/json" \
      -d '{"max_retry":2,"payment_timeout_ms":2000,"circuit_breaker_enabled":false,"payment_fallback_enabled":false}' \
      > /dev/null && echo "  ✅ Order service config: reset"

    echo ""
    echo "✅ All faults cleared. System should recover within 1-2 minutes."
    ;;

  workaround-payment)
    echo "🛠️  Applying workaround: fallback + reduced retry + circuit breaker"
    curl -s -X POST http://localhost:8081/admin/config \
      -H "Content-Type: application/json" \
      -d '{"payment_timeout_ms":1500,"max_retry":1,"payment_fallback_enabled":true,"circuit_breaker_enabled":true}' \
      | python3 -m json.tool
    echo ""
    echo "📊 Monitoring: check if error rate drops within 2 minutes"
    ;;

  help|*)
    echo "Usage: $0 <incident>"
    echo ""
    echo "Incidents:"
    echo "  payment-timeout    Payment service timeout (3s delay)"
    echo "  payment-error      Payment service 500 errors"
    echo "  retry-storm        Aggressive retry + slow payment"
    echo "  queue-backlog      Slow worker consumer → queue grows"
    echo "  slow-payment       Payment slow but not timeout"
    echo ""
    echo "Recovery:"
    echo "  clear              Clear all faults"
    echo "  workaround-payment Apply fallback + circuit breaker"
    ;;
esac

echo ""
