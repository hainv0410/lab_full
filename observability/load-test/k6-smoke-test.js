/**
 * k6 Smoke Test — Day 5
 * Mục tiêu: Xác nhận hệ thống hoạt động cơ bản trước khi load test
 * Chạy: k6 run load-test/k6-smoke-test.js
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";

export const options = {
  vus: 1,
  duration: "30s",
  thresholds: {
    http_req_failed:   ["rate<0.01"],
    http_req_duration: ["p(95)<1000"],
  },
};

const errorRate = new Rate("errors");
const orderLatency = new Trend("order_latency_ms");

export default function () {
  // Health check
  const healthRes = http.get(`${BASE_URL}/health`);
  check(healthRes, { "gateway health OK": (r) => r.status === 200 });

  // Create order
  const payload = JSON.stringify({
    user_id: "u001",
    product_id: "p001",
    quantity: 1,
    payment_method: "mock_card",
  });
  const headers = {
    "Content-Type": "application/json",
    "X-Request-ID": `smoke-${Date.now()}-${Math.random().toString(36).substr(2,6)}`,
  };

  const orderRes = http.post(`${BASE_URL}/api/orders`, payload, { headers });
  const ok = check(orderRes, {
    "order status 200": (r) => r.status === 200,
    "order has order_id": (r) => {
      try { return JSON.parse(r.body).order_id !== undefined; }
      catch { return false; }
    },
    "order has trace_id": (r) => {
      try { return JSON.parse(r.body).trace_id !== ""; }
      catch { return false; }
    },
  });

  errorRate.add(!ok);
  orderLatency.add(orderRes.timings.duration);

  sleep(1);
}

export function handleSummary(data) {
  return {
    stdout: `
╔══════════════════════════════════════════╗
║         SMOKE TEST SUMMARY               ║
╠══════════════════════════════════════════╣
║ Duration:    ${data.state.testRunDurationMs}ms
║ Iterations:  ${data.metrics.iterations ? data.metrics.iterations.values.count : 0}
║ Error Rate:  ${data.metrics.http_req_failed ? (data.metrics.http_req_failed.values.rate * 100).toFixed(2) : 0}%
║ p95 Latency: ${data.metrics.http_req_duration ? data.metrics.http_req_duration.values["p(95)"].toFixed(0) : 0}ms
╚══════════════════════════════════════════╝
`,
  };
}
