/**
 * k6 Baseline Load Test — Day 5
 * Mục tiêu: Đo baseline hiệu năng ở tải bình thường
 * Chạy: k6 run load-test/k6-create-order.js
 * Tùy chỉnh: k6 run --vus 10 --duration 3m load-test/k6-create-order.js
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend, Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";

export const options = {
  stages: [
    { duration: "30s", target: 5  },   // Ramp up
    { duration: "2m",  target: 10 },   // Steady state
    { duration: "30s", target: 0  },   // Ramp down
  ],
  thresholds: {
    http_req_failed:   ["rate<0.05"],
    http_req_duration: ["p(95)<500", "p(99)<1000"],
    order_success_rate: ["rate>0.95"],
  },
};

const errorRate        = new Rate("errors");
const orderSuccessRate = new Rate("order_success_rate");
const orderLatency     = new Trend("order_latency_ms", true);
const orderCount       = new Counter("total_orders");

const PRODUCTS  = ["p001", "p002", "p003", "p004", "p005"];
const USERS     = ["u001", "u002", "u003", "u004", "u005"];

function randomItem(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

export default function () {
  const requestId = `load-${__VU}-${__ITER}-${Date.now()}`;
  const payload = JSON.stringify({
    user_id:        randomItem(USERS),
    product_id:     randomItem(PRODUCTS),
    quantity:       Math.floor(Math.random() * 3) + 1,
    payment_method: "mock_card",
  });

  const headers = {
    "Content-Type": "application/json",
    "X-Request-ID": requestId,
  };

  const res = http.post(`${BASE_URL}/api/orders`, payload, { headers, timeout: "10s" });

  const success = check(res, {
    "status 200": (r) => r.status === 200,
    "has order_id": (r) => {
      try { return Boolean(JSON.parse(r.body).order_id); }
      catch { return false; }
    },
  });

  orderCount.add(1);
  errorRate.add(!success);
  orderSuccessRate.add(success);
  orderLatency.add(res.timings.duration);

  if (!success) {
    console.log(`[FAIL] VU=${__VU} ITER=${__ITER} status=${res.status} body=${res.body.substring(0,200)}`);
  }

  sleep(Math.random() * 0.5 + 0.1);  // 0.1–0.6s think time
}

export function handleSummary(data) {
  const dur  = data.metrics.http_req_duration?.values;
  const fail = data.metrics.http_req_failed?.values;
  const iter = data.metrics.iterations?.values;

  return {
    stdout: `
╔══════════════════════════════════════════════════╗
║          BASELINE LOAD TEST SUMMARY              ║
╠══════════════════════════════════════════════════╣
║ Total Requests:  ${iter?.count || 0}
║ Error Rate:      ${((fail?.rate || 0) * 100).toFixed(2)}%
║ p50 Latency:     ${dur?.["p(50)"]?.toFixed(0) || 0}ms
║ p95 Latency:     ${dur?.["p(95)"]?.toFixed(0) || 0}ms
║ p99 Latency:     ${dur?.["p(99)"]?.toFixed(0) || 0}ms
║ Max Latency:     ${dur?.max?.toFixed(0) || 0}ms
║ Avg Latency:     ${dur?.avg?.toFixed(0) || 0}ms
╚══════════════════════════════════════════════════╝
`,
  };
}
