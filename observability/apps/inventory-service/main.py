"""
Inventory Service — Retail Order Platform Lab
Manages product stock: reserve, release, query
"""
import os, time, uuid, json, asyncio
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg, redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
import uvicorn

APP_ENV     = os.getenv("APP_ENV", "lab")
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
SERVICE_NAME= os.getenv("OTEL_SERVICE_NAME", "inventory-service")
NAMESPACE   = os.getenv("OTEL_SERVICE_NAMESPACE", "retail-order-platform")
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
PORT        = int(os.getenv("PORT", "8082"))
POSTGRES_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER','lab')}:{os.getenv('POSTGRES_PASSWORD','lab')}"
    f"@{os.getenv('POSTGRES_HOST','postgres')}:{os.getenv('POSTGRES_PORT','5432')}"
    f"/{os.getenv('POSTGRES_DB','orders')}"
)
REDIS_HOST  = os.getenv("REDIS_HOST", "redis")
REDIS_PORT  = int(os.getenv("REDIS_PORT", "6379"))
CACHE_TTL   = int(os.getenv("INVENTORY_CACHE_TTL", "30"))

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
RESERVE_COUNT    = Counter("inventory_reserve_total","Reserves",["status"])
CACHE_HIT        = Counter("cache_hits_total","Cache hits",["service"])
CACHE_MISS       = Counter("cache_misses_total","Cache misses",["service"])

db_pool: Optional[asyncpg.Pool] = None
redis_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, redis_client
    logger.info("Inventory-service starting")
    for _ in range(10):
        try:
            db_pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=2, max_size=8)
            break
        except Exception as e:
            logger.warn("DB not ready", error=str(e))
            await asyncio.sleep(3)
    redis_client = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    logger.info("Inventory-service ready")
    yield
    if db_pool: await db_pool.close()
    if redis_client: await redis_client.close()

app = FastAPI(title="Inventory Service", version=APP_VERSION, lifespan=lifespan)
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

@app.get("/health")
async def health():
    return {"status":"ok","service":SERVICE_NAME,"env":APP_ENV,"version":APP_VERSION}

@app.get("/metrics")
async def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/api/inventory/{product_id}")
async def get_inventory(product_id: str, request: Request):
    rid = getattr(request.state,"request_id",str(uuid.uuid4()))
    cache_key = f"inventory:{product_id}"
    cached = await redis_client.get(cache_key)
    if cached:
        CACHE_HIT.labels(service=SERVICE_NAME).inc()
        return {"product_id":product_id,"quantity":int(cached),"cached":True}
    CACHE_MISS.labels(service=SERVICE_NAME).inc()
    row = await db_pool.fetchrow("SELECT quantity FROM inventory WHERE product_id=$1", product_id)
    if not row:
        raise HTTPException(status_code=404, detail="Product not in inventory")
    qty = row["quantity"]
    await redis_client.setex(cache_key, CACHE_TTL, str(qty))
    return {"product_id":product_id,"quantity":qty,"cached":False}

@app.post("/api/inventory/reserve")
async def reserve_inventory(request: Request):
    rid = getattr(request.state,"request_id",str(uuid.uuid4()))
    body = await request.json()
    product_id = body.get("product_id")
    quantity   = body.get("quantity", 1)

    if not product_id:
        raise HTTPException(status_code=400, detail="product_id required")

    with tracer.start_as_current_span("reserve_stock") as span:
        span.set_attribute("inventory.product_id", product_id)
        span.set_attribute("inventory.quantity", quantity)
        start = time.time()

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT quantity FROM inventory WHERE product_id=$1 FOR UPDATE", product_id
                )
                if not row:
                    RESERVE_COUNT.labels(status="not_found").inc()
                    logger.error("product_not_found", request_id=rid,
                                 error_code="PRODUCT_NOT_FOUND", product_id=product_id)
                    raise HTTPException(status_code=404, detail="Product not found")
                if row["quantity"] < quantity:
                    RESERVE_COUNT.labels(status="insufficient").inc()
                    logger.error("insufficient_stock", request_id=rid,
                                 error_code="INSUFFICIENT_STOCK",
                                 product_id=product_id,
                                 available=row["quantity"], requested=quantity)
                    raise HTTPException(status_code=400,
                                        detail=f"Insufficient stock: {row['quantity']} available")
                await conn.execute(
                    "UPDATE inventory SET quantity=quantity-$1, updated_at=NOW() WHERE product_id=$2",
                    quantity, product_id
                )

        # Invalidate cache
        await redis_client.delete(f"inventory:{product_id}")

        latency = round((time.time()-start)*1000, 2)
        RESERVE_COUNT.labels(status="success").inc()
        logger.info("stock_reserved", request_id=rid, product_id=product_id,
                    quantity=quantity, latency_ms=latency,
                    event="stock_reserved", status_code=200)
        return {"status":"reserved","product_id":product_id,"quantity":quantity}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
