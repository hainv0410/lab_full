"""
Payment Service — Retail Order Platform Lab
Authorizes payments. Supports fault injection for Day 4/5 incident simulations.
"""
import os, time, uuid, json, asyncio
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
import uvicorn

APP_ENV      = os.getenv("APP_ENV", "lab")
APP_VERSION  = os.getenv("APP_VERSION", "1.0.0")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "payment-service")
NAMESPACE    = os.getenv("OTEL_SERVICE_NAMESPACE", "retail-order-platform")
OTLP_ENDPOINT= os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
PORT         = int(os.getenv("PORT", "8083"))
POSTGRES_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER','lab')}:{os.getenv('POSTGRES_PASSWORD','lab')}"
    f"@{os.getenv('POSTGRES_HOST','postgres')}:{os.getenv('POSTGRES_PORT','5432')}"
    f"/{os.getenv('POSTGRES_DB','orders')}"
)

# Fault injection state (mutable at runtime)
_fault_enabled  = os.getenv("FAULT_ENABLED", "false").lower() == "true"
_fault_type     = os.getenv("FAULT_TYPE", "none")   # timeout | error | slow
_fault_delay_ms = int(os.getenv("FAULT_DELAY_MS", "0"))
_fault_duration = 0
_fault_start    = 0.0

class JSONLogger:
    def __init__(self, s, e, v): self.s=s; self.e=e; self.v=v
    def _log(self, level, msg, **kw):
        span=trace.get_current_span(); ctx=span.get_span_context()
        print(json.dumps({"timestamp":time.strftime("%Y-%m-%dT%H:%M:%S+07:00",time.localtime()),
            "level":level,"service_name":self.s,"environment":self.e,"service_version":self.v,
            "trace_id":format(ctx.trace_id,"032x") if ctx.is_valid else "",
            "span_id":format(ctx.span_id,"016x") if ctx.is_valid else "",
            "message":msg,**kw},ensure_ascii=False),flush=True)
    def info(self,m,**kw): self._log("INFO",m,**kw)
    def warn(self,m,**kw): self._log("WARN",m,**kw)
    def error(self,m,**kw): self._log("ERROR",m,**kw)

logger = JSONLogger(SERVICE_NAME, APP_ENV, APP_VERSION)

resource = Resource.create({"service.name":SERVICE_NAME,"service.namespace":NAMESPACE,
    "service.version":APP_VERSION,"deployment.environment":APP_ENV})
provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)))
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
AsyncPGInstrumentor().instrument()

REQUEST_COUNT    = Counter("http_requests_total","Total",["method","route","status","service"])
REQUEST_DURATION = Histogram("http_request_duration_seconds","Duration",
    ["method","route","service"],buckets=[.01,.05,.1,.25,.5,1,2,5,10])
PAYMENT_AUTH     = Counter("payment_authorizations_total","Auth",["status"])
PAYMENT_LATENCY  = Histogram("payment_authorization_duration_seconds","Payment duration",
    buckets=[.05,.1,.25,.5,1,2,3,5])

db_pool: Optional[asyncpg.Pool] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    logger.info("Payment-service starting")
    for _ in range(10):
        try:
            db_pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=2, max_size=6)
            break
        except Exception as e:
            logger.warn("DB not ready", error=str(e))
            await asyncio.sleep(3)
    logger.info("Payment-service ready")
    yield
    if db_pool: await db_pool.close()

app = FastAPI(title="Payment Service", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
FastAPIInstrumentor.instrument_app(app)

@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = rid
    start = time.time()
    resp = await call_next(request)
    latency = (time.time()-start)*1000
    REQUEST_COUNT.labels(method=request.method,route=request.url.path,
                          status=str(resp.status_code),service=SERVICE_NAME).inc()
    REQUEST_DURATION.labels(method=request.method,route=request.url.path,
                              service=SERVICE_NAME).observe(latency/1000)
    resp.headers["X-Request-ID"] = rid
    return resp

def _fault_active() -> bool:
    global _fault_enabled, _fault_start, _fault_duration
    if not _fault_enabled: return False
    if _fault_duration > 0 and (time.time() - _fault_start) > _fault_duration:
        _fault_enabled = False
        logger.info("fault cleared (duration expired)")
        return False
    return True

@app.get("/health")
async def health():
    return {"status":"ok","service":SERVICE_NAME,"env":APP_ENV,"version":APP_VERSION,
            "fault_enabled":_fault_enabled,"fault_type":_fault_type}

@app.get("/metrics")
async def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/api/payments/authorize")
async def authorize_payment(request: Request):
    rid = getattr(request.state,"request_id",str(uuid.uuid4()))
    body = await request.json()
    order_id= body.get("order_id")
    amount  = body.get("amount", 0)
    method  = body.get("payment_method", body.get("method","mock_card"))

    with tracer.start_as_current_span("authorize_payment") as span:
        span.set_attribute("payment.order_id", str(order_id))
        span.set_attribute("payment.amount", float(amount))
        span.set_attribute("payment.method", str(method))

        start = time.time()

        # ── Fault injection ──────────────────────────────────
        if _fault_active():
            if _fault_type == "timeout":
                delay = (_fault_delay_ms or 3000) / 1000
                logger.warn("FAULT_INJECTION_TIMEOUT", request_id=rid,
                            fault_type="timeout", delay_s=delay, order_id=order_id)
                await asyncio.sleep(delay)
            elif _fault_type == "error":
                PAYMENT_AUTH.labels(status="error").inc()
                logger.error("FAULT_INJECTION_ERROR", request_id=rid,
                             fault_type="error", error_code="PAYMENT_FORCED_ERROR")
                raise HTTPException(status_code=500, detail="Payment service error (injected)")
            elif _fault_type == "slow":
                delay = (_fault_delay_ms or 1500) / 1000
                await asyncio.sleep(delay)

        # ── Normal processing ─────────────────────────────────
        await asyncio.sleep(0.05)  # Simulate processing

        payment_id = "pay_" + str(uuid.uuid4())[:8]
        if db_pool:
            try:
                await db_pool.execute(
                    "INSERT INTO payments (id,order_id,amount,method,status,request_id,trace_id)"
                    " VALUES ($1,$2,$3,$4,'approved',$5,$6)",
                    payment_id, order_id, amount, method, rid,
                    format(trace.get_current_span().get_span_context().trace_id,"032x")
                    if trace.get_current_span().get_span_context().is_valid else ""
                )
            except Exception as e:
                logger.warn("payment db write failed", error=str(e))

        latency = round((time.time()-start)*1000, 2)
        PAYMENT_AUTH.labels(status="approved").inc()
        PAYMENT_LATENCY.observe(latency/1000)

        logger.info("payment_authorized", request_id=rid, order_id=order_id,
                    payment_id=payment_id, amount=amount, method=method,
                    latency_ms=latency, status_code=200, event="payment_authorized")

        return {"status":"approved","payment_id":payment_id,"order_id":order_id,"amount":amount}

@app.post("/admin/faults")
async def set_fault(request: Request):
    """Day 4/5 fault injection endpoint"""
    global _fault_enabled, _fault_type, _fault_delay_ms, _fault_duration, _fault_start
    body = await request.json()
    fault = body.get("fault","none")
    if fault == "none" or fault == "clear":
        _fault_enabled = False
        _fault_type = "none"
        logger.info("fault cleared via admin")
        return {"status":"ok","fault":"cleared"}
    _fault_enabled  = True
    _fault_type     = fault
    _fault_delay_ms = int(body.get("delay_ms", 3000))
    _fault_duration = int(body.get("duration_seconds", 900))
    _fault_start    = time.time()
    logger.warn("FAULT_INJECTION_SET", fault_type=_fault_type,
                delay_ms=_fault_delay_ms, duration_seconds=_fault_duration)
    return {"status":"ok","fault":_fault_type,"delay_ms":_fault_delay_ms,
            "duration_seconds":_fault_duration}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
