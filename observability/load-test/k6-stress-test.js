/**
 * k6 Stress Test — Day 5
 * Mục tiêu: Tăng tải dần để tìm điểm gãy của hệ thống
 * Chạy: k6 run load-test/k6-stress-test.js
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend, Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";

export const options = {
  stages: [
    { duration: "1m",  target: 5  },   // Warm-up
    { duration: "1m",  target: 10 },   // Normal load
    { duration: "1m",  target: 20 },   // Medium load
    { duration: "1m",  target: 40 },   // High load
    { duration: "1m",  target: 60 },   // Stress
    { duration: "1m",  target: 80 },   // Heavy stress
    { duration: "2m",  target: 80 },   // Sustain — find breaking point
    { duration: "1m",  target: 0  },   // Recovery
  ],
  thresholds: {
    http_req_failed:   ["rate<0.2"],   // Relaxed threshold for stress test
    http_req_duration: ["p(99)<5000"],
  },
};

const errorRate    = new Rate("errors");
const orderLatency = new Trend("order_latency_ms", true);
const orderCount   = new Counter("total_orders");

const PRODUCTS = ["p001", "p002", "p003", "p004", "p005"];
const USERS    = ["u001", "u002", "u003", "u004", "u005"];

export default function () {
  const requestId = `stress-${__VU}-${__ITER}-${Date.now()}`;
  const payload = JSON.stringify({
    user_id:        USERS[__VU % USERS.length],
    product_id:     PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)],
    quantity:       1,
    payment_method: "mock_card",
  });

  const headers = {
    "Content-Type": "application/json",
    "X-Request-ID": requestId,
  };

  const res = http.post(`${BASE_URL}/api/orders`, payload, { headers, timeout: "15s" });

  const success = check(res, {
    "status 2xx": (r) => r.status >= 200 && r.status < 300,
  });

  orderCount.add(1);
  errorRate.add(!success);
  orderLatency.add(res.timings.duration);

  sleep(0.1);  // Minimal think time during stress test
}

export function handleSummary(data) {
  const dur  = data.metrics.http_req_duration?.values;
  const fail = data.metrics.http_req_failed?.values;
  const rps  = data.metrics.http_reqs?.values;

  return {
    stdout: `
╔═══════════════════════════════════════════════════════╗
║              STRESS TEST SUMMARY                      ║
╠═══════════════════════════════════════════════════════╣
║ Total Requests:  ${rps?.count || 0}
║ RPS (avg):       ${rps?.rate?.toFixed(2) || 0}
║ Error Rate:      ${((fail?.rate || 0) * 100).toFixed(2)}%
║ p50 Latency:     ${dur?.["p(50)"]?.toFixed(0) || 0}ms
║ p95 Latency:     ${dur?.["p(95)"]?.toFixed(0) || 0}ms
║ p99 Latency:     ${dur?.["p(99)"]?.toFixed(0) || 0}ms
║ Max Latency:     ${dur?.max?.toFixed(0) || 0}ms
╚═══════════════════════════════════════════════════════╝

ANALYSIS NOTES:
  - Compare p95 across stages to spot degradation point
  - Check Grafana for DB connection pool saturation
  - Check payment service latency increase
  - Check queue backlog growth
  - Check error rate inflection point (VU count when errors start)
`,
  };
}
