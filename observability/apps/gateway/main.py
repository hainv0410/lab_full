"""
Gateway Service — Retail Order Platform Lab
Day 1-5: Entry point for all client requests
"""
import os, time, uuid, logging, json
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import inject, extract
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
import uvicorn

# ─── Environment ─────────────────────────────────────────────
APP_ENV          = os.getenv("APP_ENV", "lab")
APP_VERSION      = os.getenv("APP_VERSION", "1.0.0")
SERVICE_NAME     = os.getenv("OTEL_SERVICE_NAME", "gateway")
NAMESPACE        = os.getenv("OTEL_SERVICE_NAMESPACE", "retail-order-platform")
OTLP_ENDPOINT    = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
ORDER_SERVICE_URL= os.getenv("ORDER_SERVICE_URL", "http://order-service:8081")
LOG_LEVEL        = os.getenv("LOG_LEVEL", "INFO")
PORT             = int(os.getenv("PORT", "8080"))

# ─── Structured Logger ────────────────────────────────────────
class JSONLogger:
    def __init__(self, service: str, env: str, version: str):
        self.service = service
        self.env = env
        self.version = version

    def _log(self, level: str, message: str, **extra):
        span = trace.get_current_span()
        ctx  = span.get_span_context()
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00", time.localtime()),
            "level":         level,
            "service_name":  self.service,
            "environment":   self.env,
            "service_version": self.version,
            "trace_id": format(ctx.trace_id, "032x") if ctx.is_valid else "",
            "span_id":  format(ctx.span_id, "016x") if ctx.is_valid else "",
            "message":  message,
            **extra,
        }
        print(json.dumps(record, ensure_ascii=False), flush=True)

    def info(self, msg, **kw):  self._log("INFO", msg, **kw)
    def warn(self, msg, **kw):  self._log("WARN", msg, **kw)
    def error(self, msg, **kw): self._log("ERROR", msg, **kw)

logger = JSONLogger(SERVICE_NAME, APP_ENV, APP_VERSION)

# ─── OTel Setup ───────────────────────────────────────────────
resource = Resource.create({
    "service.name":      SERVICE_NAME,
    "service.namespace": NAMESPACE,
    "service.version":   APP_VERSION,
    "deployment.environment": APP_ENV,
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

# ─── Prometheus Metrics ───────────────────────────────────────
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "route", "status", "service"],
)
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "route", "service"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
)

# ─── App ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Gateway starting", port=PORT, version=APP_VERSION, env=APP_ENV)
    yield
    logger.info("Gateway shutting down")

app = FastAPI(title="Gateway", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
FastAPIInstrumentor.instrument_app(app)

# ─── Middleware: request_id + metrics ─────────────────────────
@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start = time.time()

    request.state.request_id = request_id
    response = await call_next(request)
    latency = (time.time() - start) * 1000

    route = request.url.path
    method = request.method
    status = str(response.status_code)

    REQUEST_COUNT.labels(method=method, route=route, status=status, service=SERVICE_NAME).inc()
    REQUEST_DURATION.labels(method=method, route=route, service=SERVICE_NAME).observe(latency / 1000)

    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request completed",
        request_id=request_id,
        method=method,
        endpoint=route,
        status_code=int(status),
        latency_ms=round(latency, 2),
    )
    return response

# ─── Routes ───────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "env": APP_ENV, "version": APP_VERSION}

@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/api/products")
async def list_products(request: Request):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    headers = {"X-Request-ID": request_id}
    inject(headers)
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{ORDER_SERVICE_URL}/api/products", headers=headers)
        return r.json()

@app.post("/api/orders")
async def create_order(request: Request):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    body = await request.json()
    headers = {"X-Request-ID": request_id, "Content-Type": "application/json"}
    inject(headers)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{ORDER_SERVICE_URL}/api/orders",
                json=body,
                headers=headers,
            )
            if r.status_code >= 400:
                logger.error(
                    "order creation failed",
                    request_id=request_id,
                    status_code=r.status_code,
                    error_code="UPSTREAM_ERROR",
                )
            return Response(
                content=r.content,
                status_code=r.status_code,
                media_type="application/json",
            )
    except httpx.TimeoutException:
        logger.error("order service timeout", request_id=request_id, error_code="ORDER_SERVICE_TIMEOUT")
        raise HTTPException(status_code=504, detail="Order service timeout")

@app.get("/api/orders/{order_id}")
async def get_order(order_id: str, request: Request):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    headers = {"X-Request-ID": request_id}
    inject(headers)
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{ORDER_SERVICE_URL}/api/orders/{order_id}", headers=headers)
        return Response(content=r.content, status_code=r.status_code, media_type="application/json")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
