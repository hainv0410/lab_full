"""
Order Service — Retail Order Platform Lab
Core business service: creates orders, coordinates inventory, payment, notification
"""
import os, time, uuid, json, asyncio
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg, redis.asyncio as aioredis, aio_pika, httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.propagate import inject, extract
import uvicorn

# ─── Environment ─────────────────────────────────────────────
APP_ENV          = os.getenv("APP_ENV", "lab")
APP_VERSION      = os.getenv("APP_VERSION", "1.0.0")
SERVICE_NAME     = os.getenv("OTEL_SERVICE_NAME", "order-service")
NAMESPACE        = os.getenv("OTEL_SERVICE_NAMESPACE", "retail-order-platform")
OTLP_ENDPOINT    = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
PORT             = int(os.getenv("PORT", "8081"))
INVENTORY_URL    = os.getenv("INVENTORY_SERVICE_URL", "http://inventory-service:8082")
PAYMENT_URL      = os.getenv("PAYMENT_SERVICE_URL", "http://payment-service:8083")
NOTIFICATION_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8084")
POSTGRES_DSN     = (
    f"postgresql://{os.getenv('POSTGRES_USER','lab')}:{os.getenv('POSTGRES_PASSWORD','lab')}"
    f"@{os.getenv('POSTGRES_HOST','postgres')}:{os.getenv('POSTGRES_PORT','5432')}"
    f"/{os.getenv('POSTGRES_DB','orders')}"
)
REDIS_HOST       = os.getenv("REDIS_HOST", "redis")
REDIS_PORT       = int(os.getenv("REDIS_PORT", "6379"))
RABBITMQ_URL     = os.getenv("RABBITMQ_URL", "amqp://lab:lab@rabbitmq:5672/")
PAYMENT_TIMEOUT_MS     = int(os.getenv("PAYMENT_TIMEOUT_MS", "2000"))
PAYMENT_MAX_RETRY      = int(os.getenv("PAYMENT_MAX_RETRY", "2"))
PAYMENT_FALLBACK       = os.getenv("PAYMENT_FALLBACK_ENABLED", "false").lower() == "true"
CIRCUIT_BREAKER_ON     = os.getenv("CIRCUIT_BREAKER_ENABLED", "false").lower() == "true"

# ─── Structured Logger ────────────────────────────────────────
class JSONLogger:
    def __init__(self, service, env, version):
        self.service = service; self.env = env; self.version = version

    def _log(self, level, msg, **extra):
        span = trace.get_current_span()
        ctx  = span.get_span_context()
        rec = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00", time.localtime()),
            "level": level, "service_name": self.service,
            "environment": self.env, "service_version": self.version,
            "trace_id": format(ctx.trace_id, "032x") if ctx.is_valid else "",
            "span_id":  format(ctx.span_id,  "016x") if ctx.is_valid else "",
            "message": msg, **extra,
        }
        print(json.dumps(rec, ensure_ascii=False), flush=True)

    def info(self, m, **kw):  self._log("INFO",  m, **kw)
    def warn(self, m, **kw):  self._log("WARN",  m, **kw)
    def error(self, m, **kw): self._log("ERROR", m, **kw)

logger = JSONLogger(SERVICE_NAME, APP_ENV, APP_VERSION)

# ─── OTel ─────────────────────────────────────────────────────
resource = Resource.create({
    "service.name": SERVICE_NAME, "service.namespace": NAMESPACE,
    "service.version": APP_VERSION, "deployment.environment": APP_ENV,
})
provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(provider)

# ─── OTLP Log Export → OTel Collector → Loki (fix: Day 2 logs) ───
import logging as _logging
from opentelemetry.sdk._logs import LoggerProvider as _LoggerProvider, LoggingHandler as _LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor as _BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter as _OTLPLogExporter

_log_provider = _LoggerProvider(resource=resource)
_log_provider.add_log_record_processor(
    _BatchLogRecordProcessor(_OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
_otel_pylogger = _logging.getLogger("otel." + SERVICE_NAME)
_otel_pylogger.setLevel(_logging.INFO)
_otel_pylogger.propagate = False
_otel_pylogger.addHandler(_LoggingHandler(level=_logging.INFO, logger_provider=_log_provider))

_orig_jsonlog = logger._log
_LEVELMAP = {"INFO": _logging.INFO, "WARN": _logging.WARNING, "ERROR": _logging.ERROR}
def _log_with_otel(level, msg, **extra):
    _orig_jsonlog(level, msg, **extra)
    try:
        _otel_pylogger.log(_LEVELMAP.get(level, _logging.INFO), msg,
                           extra={"service_name": SERVICE_NAME, **extra})
    except Exception:
        pass
logger._log = _log_with_otel
# ─── end OTLP log export ───
tracer = trace.get_tracer(SERVICE_NAME)
HTTPXClientInstrumentor().instrument()
AsyncPGInstrumentor().instrument()

# ─── Prometheus ───────────────────────────────────────────────
REQUEST_COUNT    = Counter("http_requests_total", "Total requests",
                           ["method","route","status","service"])
REQUEST_DURATION = Histogram("http_request_duration_seconds", "Request duration",
                             ["method","route","service"],
                             buckets=[.01,.05,.1,.25,.5,1,2,5,10])
ORDER_TOTAL      = Counter("orders_total", "Total orders", ["status"])
PAYMENT_TIMEOUT  = Counter("payment_timeouts_total", "Payment timeouts")
RETRY_COUNT      = Counter("payment_retries_total", "Payment retries")
DB_POOL_SIZE     = Gauge("asyncpg_pool_size", "AsyncPG connection pool size", ["service"])

# ─── DB / Redis pool ─────────────────────────────────────────
db_pool: Optional[asyncpg.Pool] = None
redis_client = None
rmq_connection = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, redis_client, rmq_connection
    logger.info("Order-service starting")

    for _ in range(10):
        try:
            db_pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=2, max_size=10)
            break
        except Exception as e:
            logger.warn("DB not ready", error=str(e))
            await asyncio.sleep(3)

    redis_client = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    try:
        rmq_connection = await aio_pika.connect_robust(RABBITMQ_URL)
    except Exception as e:
        logger.warn("RabbitMQ connect failed", error=str(e))

    logger.info("Order-service ready")
    yield

    if db_pool:    await db_pool.close()
    if redis_client: await redis_client.close()
    if rmq_connection: await rmq_connection.close()
    logger.info("Order-service shutdown")

app = FastAPI(title="Order Service", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
FastAPIInstrumentor.instrument_app(app)

# ─── Middleware ───────────────────────────────────────────────
@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.time()
    response = await call_next(request)
    latency = (time.time() - start) * 1000
    route  = request.url.path
    method = request.method
    status = str(response.status_code)
    REQUEST_COUNT.labels(method=method, route=route, status=status, service=SERVICE_NAME).inc()
    REQUEST_DURATION.labels(method=method, route=route, service=SERVICE_NAME).observe(latency/1000)
    if db_pool is not None:
        try: DB_POOL_SIZE.labels(service=SERVICE_NAME).set(db_pool.get_size())
        except Exception: pass
    response.headers["X-Request-ID"] = request_id
    return response

# ─── Helpers ──────────────────────────────────────────────────
def _mask_card(card: str) -> str:
    if not card or len(card) < 8: return "****"
    return card[:6] + "******" + card[-4:]

def _mask_phone(phone: str) -> str:
    if not phone or len(phone) < 6: return "***"
    return phone[:3] + "****" + phone[-3:]

async def _call_payment(order_id, amount, method, request_id, headers, retries_left=None) -> dict:
    if retries_left is None: retries_left = PAYMENT_MAX_RETRY
    timeout = PAYMENT_TIMEOUT_MS / 1000

    for attempt in range(retries_left + 1):
        if attempt > 0:
            backoff = min(0.5 * (2 ** (attempt - 1)), 4)
            import random; backoff += random.uniform(0, 0.1)
            RETRY_COUNT.inc()
            logger.warn("RETRY_ATTEMPT", request_id=request_id, attempt=attempt,
                        event="payment_retry", order_id=order_id)
            await asyncio.sleep(backoff)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"{PAYMENT_URL}/api/payments/authorize",
                    json={"order_id": order_id, "amount": amount,
                          "method": method, "request_id": request_id},
                    headers=headers,
                )
                return r.json()
        except httpx.TimeoutException:
            PAYMENT_TIMEOUT.inc()
            logger.error("PAYMENT_TIMEOUT", request_id=request_id, attempt=attempt,
                         error_code="PAYMENT_TIMEOUT", order_id=order_id)
            if attempt >= retries_left:
                if PAYMENT_FALLBACK:
                    logger.warn("payment fallback activated", request_id=request_id, order_id=order_id)
                    return {"status": "pending", "fallback": True}
                raise
        except Exception as e:
            logger.error("payment error", request_id=request_id, error=str(e), order_id=order_id)
            raise

# ─── Routes ───────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "env": APP_ENV, "version": APP_VERSION}

@app.get("/metrics")
async def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/api/products")
async def list_products():
    rows = await db_pool.fetch("SELECT id, name, description, price FROM products ORDER BY id")
    return [dict(r) for r in rows]

@app.post("/api/orders")
async def create_order(request: Request):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    body = await request.json()

    user_id    = body.get("user_id")
    product_id = body.get("product_id")
    quantity   = body.get("quantity", 1)
    pay_method = body.get("payment_method", "mock_card")
    # Mask sensitive fields
    card_raw   = body.get("card_number", "")
    card_masked= _mask_card(card_raw) if card_raw else None
    phone_raw  = body.get("phone", "")
    phone_masked = _mask_phone(phone_raw) if phone_raw else None

    if not user_id or not product_id:
        raise HTTPException(status_code=400, detail="user_id and product_id required")

    headers = {"X-Request-ID": request_id, "Content-Type": "application/json"}
    inject(headers)

    with tracer.start_as_current_span("create_order") as span:
        span.set_attribute("order.user_id", user_id)
        span.set_attribute("order.product_id", product_id)
        span.set_attribute("order.quantity", quantity)

        start = time.time()
        logger.info("order_received", request_id=request_id,
                    endpoint="/api/orders", method="POST",
                    user_id=user_id, product_id=product_id, quantity=quantity,
                    card_number_masked=card_masked, phone_masked=phone_masked)

        # 1. Get product
        try:
            row = await db_pool.fetchrow(
                "SELECT id, name, price FROM products WHERE id=$1", product_id
            )
        except Exception as e:
            logger.error("db_error", request_id=request_id, error_code="DB_ERROR",
                         error_message=str(e))
            raise HTTPException(status_code=500, detail="Database error")

        if not row:
            logger.error("product_not_found", request_id=request_id,
                         error_code="PRODUCT_NOT_FOUND", product_id=product_id)
            raise HTTPException(status_code=404, detail=f"Product {product_id} not found")

        price = float(row["price"])
        total = price * quantity

        # 2. Reserve inventory
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                inv_r = await client.post(
                    f"{INVENTORY_URL}/api/inventory/reserve",
                    json={"product_id": product_id, "quantity": quantity,
                          "request_id": request_id},
                    headers=headers,
                )
                if inv_r.status_code != 200:
                    logger.error("inventory_reserve_failed", request_id=request_id,
                                 error_code="INVENTORY_ERROR",
                                 status_code=inv_r.status_code)
                    raise HTTPException(status_code=400, detail="Inventory reservation failed")
        except httpx.TimeoutException:
            logger.error("inventory_timeout", request_id=request_id, error_code="INVENTORY_TIMEOUT")
            raise HTTPException(status_code=504, detail="Inventory service timeout")

        # 3. Create order in DB
        order_id = "ord_" + str(uuid.uuid4())[:8]
        span_ctx = trace.get_current_span().get_span_context()
        trace_id_str = format(span_ctx.trace_id, "032x") if span_ctx.is_valid else ""

        await db_pool.execute(
            """INSERT INTO orders (id, user_id, product_id, quantity, total_amount,
               payment_method, status, request_id, trace_id)
               VALUES ($1,$2,$3,$4,$5,$6,'pending',$7,$8)""",
            order_id, user_id, product_id, quantity, total, pay_method, request_id, trace_id_str
        )

        # 4. Authorize payment
        try:
            pay_result = await _call_payment(order_id, total, pay_method, request_id, headers)
        except httpx.TimeoutException:
            await db_pool.execute("UPDATE orders SET status='failed' WHERE id=$1", order_id)
            ORDER_TOTAL.labels(status="failed").inc()
            raise HTTPException(status_code=504, detail="Payment timeout")

        pay_status = pay_result.get("status", "failed")
        order_status = "created" if pay_status in ("approved", "pending") else "failed"

        await db_pool.execute(
            "UPDATE orders SET status=$1, updated_at=NOW() WHERE id=$2",
            order_status, order_id
        )
        ORDER_TOTAL.labels(status=order_status).inc()

        # 5. Publish notification
        try:
            if rmq_connection:
                channel = await rmq_connection.channel()
                await channel.declare_queue("notifications", durable=True)
                msg_body = json.dumps({
                    "event_type": "order_created",
                    "order_id": order_id,
                    "user_id": user_id,
                    "total_amount": total,
                    "status": order_status,
                    "request_id": request_id,
                    "trace_id": trace_id_str,
                })
                await channel.default_exchange.publish(
                    aio_pika.Message(body=msg_body.encode(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
                    routing_key="notifications",
                )
        except Exception as e:
            logger.warn("notification_publish_failed", request_id=request_id, error=str(e))

        latency = round((time.time() - start) * 1000, 2)
        logger.info(
            "order_created", request_id=request_id, endpoint="/api/orders",
            method="POST", status_code=200, latency_ms=latency,
            order_id=order_id, order_status=order_status, total_amount=total,
        )

        span_ctx = trace.get_current_span().get_span_context()
        return {
            "order_id": order_id,
            "status": order_status,
            "total_amount": total,
            "request_id": request_id,
            "trace_id": format(span_ctx.trace_id, "032x") if span_ctx.is_valid else "",
        }

@app.get("/api/orders/{order_id}")
async def get_order(order_id: str, request: Request):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    row = await db_pool.fetchrow("SELECT * FROM orders WHERE id=$1", order_id)
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return dict(row)

@app.post("/admin/config")
async def admin_config(request: Request):
    """Day 4/5: Fault injection config endpoint"""
    global PAYMENT_TIMEOUT_MS, PAYMENT_MAX_RETRY, PAYMENT_FALLBACK, CIRCUIT_BREAKER_ON
    body = await request.json()
    if "payment_timeout_ms" in body:   PAYMENT_TIMEOUT_MS = int(body["payment_timeout_ms"])
    if "max_retry" in body:            PAYMENT_MAX_RETRY = int(body["max_retry"])
    if "payment_fallback_enabled" in body: PAYMENT_FALLBACK = bool(body["payment_fallback_enabled"])
    if "circuit_breaker_enabled" in body:  CIRCUIT_BREAKER_ON = bool(body["circuit_breaker_enabled"])
    logger.info("admin config updated",
                payment_timeout_ms=PAYMENT_TIMEOUT_MS,
                max_retry=PAYMENT_MAX_RETRY,
                fallback=PAYMENT_FALLBACK,
                circuit_breaker=CIRCUIT_BREAKER_ON)
    return {"status": "ok", "config": {
        "payment_timeout_ms": PAYMENT_TIMEOUT_MS,
        "max_retry": PAYMENT_MAX_RETRY,
        "fallback": PAYMENT_FALLBACK,
        "circuit_breaker": CIRCUIT_BREAKER_ON,
    }}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
