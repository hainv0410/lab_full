"""
Notification Service — Retail Order Platform Lab
Publishes notifications to RabbitMQ queue
"""
import os, time, uuid, json, asyncio
from contextlib import asynccontextmanager

import aio_pika
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.propagate import inject
import uvicorn

APP_ENV      = os.getenv("APP_ENV", "lab")
APP_VERSION  = os.getenv("APP_VERSION", "1.0.0")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "notification-service")
NAMESPACE    = os.getenv("OTEL_SERVICE_NAMESPACE", "retail-order-platform")
OTLP_ENDPOINT= os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
PORT         = int(os.getenv("PORT", "8084"))
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://lab:lab@rabbitmq:5672/")

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

REQUEST_COUNT = Counter("http_requests_total","Total",["method","route","status","service"])
NOTIFY_COUNT  = Counter("notifications_published_total","Notifications published",["status"])

rmq_connection = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global rmq_connection
    logger.info("Notification-service starting")
    for _ in range(10):
        try:
            rmq_connection = await aio_pika.connect_robust(RABBITMQ_URL)
            break
        except Exception as e:
            logger.warn("RabbitMQ not ready", error=str(e))
            await asyncio.sleep(3)
    logger.info("Notification-service ready")
    yield
    if rmq_connection: await rmq_connection.close()

app = FastAPI(title="Notification Service", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
FastAPIInstrumentor.instrument_app(app)

@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = rid
    resp = await call_next(request)
    REQUEST_COUNT.labels(method=request.method, route=request.url.path,
                          status=str(resp.status_code), service=SERVICE_NAME).inc()
    resp.headers["X-Request-ID"] = rid
    return resp

@app.get("/health")
async def health():
    return {"status":"ok","service":SERVICE_NAME,"env":APP_ENV,"version":APP_VERSION}

@app.get("/metrics")
async def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/api/notifications/publish")
async def publish_notification(request: Request):
    rid = getattr(request.state,"request_id",str(uuid.uuid4()))
    body = await request.json()

    with tracer.start_as_current_span("publish_message") as span:
        span_ctx = span.get_span_context()
        trace_id_str = format(span_ctx.trace_id,"032x") if span_ctx.is_valid else ""
        span.set_attribute("notification.event_type", body.get("event_type","unknown"))
        span.set_attribute("notification.order_id", str(body.get("order_id","")))

        msg_payload = {**body, "request_id": rid, "trace_id": trace_id_str}
        headers_carrier = {}
        inject(headers_carrier)

        try:
            if rmq_connection:
                channel = await rmq_connection.channel()
                queue = await channel.declare_queue("notifications", durable=True)
                await channel.default_exchange.publish(
                    aio_pika.Message(
                        body=json.dumps(msg_payload).encode(),
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        headers=headers_carrier,
                    ),
                    routing_key="notifications",
                )
                NOTIFY_COUNT.labels(status="published").inc()
                logger.info("notification_published", request_id=rid,
                            event_type=body.get("event_type"), order_id=body.get("order_id"),
                            event="notification_published", status_code=200)
                return {"status":"published","order_id":body.get("order_id")}
            else:
                NOTIFY_COUNT.labels(status="failed").inc()
                logger.error("rmq_not_ready", request_id=rid, error_code="RMQ_UNAVAILABLE")
                return {"status":"failed","reason":"rabbitmq unavailable"}
        except Exception as e:
            NOTIFY_COUNT.labels(status="error").inc()
            logger.error("notification_error", request_id=rid, error=str(e),
                         error_code="NOTIFICATION_ERROR")
            return {"status":"error","reason":str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
