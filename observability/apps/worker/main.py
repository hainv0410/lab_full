"""
Worker Service — Retail Order Platform Lab
Consumes notification jobs from RabbitMQ. Supports slow_consumer fault for Day 4/5.
"""
import os, time, uuid, json, asyncio
from contextlib import asynccontextmanager

import aio_pika
from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.propagate import extract
import uvicorn

APP_ENV      = os.getenv("APP_ENV", "lab")
APP_VERSION  = os.getenv("APP_VERSION", "1.0.0")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "worker")
NAMESPACE    = os.getenv("OTEL_SERVICE_NAMESPACE", "retail-order-platform")
OTLP_ENDPOINT= os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
PORT         = int(os.getenv("PORT", "8085"))
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://lab:lab@rabbitmq:5672/")
SLOW_CONSUMER       = os.getenv("SLOW_CONSUMER", "false").lower() == "true"
SLOW_CONSUMER_DELAY = int(os.getenv("SLOW_CONSUMER_DELAY_MS", "0"))

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

JOBS_PROCESSED  = Counter("notification_jobs_processed_total","Processed",["status"])
JOBS_COMPLETED  = Counter("notification_jobs_completed_total","Completed")
JOB_DURATION    = Histogram("notification_job_duration_seconds","Duration",
    buckets=[.01,.05,.1,.25,.5,1,2,5,10])
QUEUE_BACKLOG   = Gauge("worker_queue_backlog","Queue backlog estimate")

_slow_consumer = SLOW_CONSUMER
_slow_delay_ms = SLOW_CONSUMER_DELAY
rmq_connection = None
consumer_task  = None

async def process_message(message: aio_pika.abc.AbstractIncomingMessage):
    global _slow_consumer, _slow_delay_ms
    async with message.process():
        start = time.time()
        try:
            payload = json.loads(message.body.decode())
            request_id = payload.get("request_id", str(uuid.uuid4()))
            trace_id   = payload.get("trace_id", "")
            event_type = payload.get("event_type", "unknown")
            order_id   = payload.get("order_id", "")

            # Extract trace context from headers if present
            ctx = extract(dict(message.headers) if message.headers else {})

            with tracer.start_as_current_span("process_notification", context=ctx) as span:
                span.set_attribute("notification.event_type", event_type)
                span.set_attribute("notification.order_id", str(order_id))
                span.set_attribute("notification.request_id", request_id)

                logger.info("processing_notification", request_id=request_id,
                            event_type=event_type, order_id=order_id)

                # Fault injection: slow consumer
                if _slow_consumer:
                    delay = (_slow_delay_ms or 3000) / 1000
                    logger.warn("SLOW_CONSUMER_DELAY", request_id=request_id,
                                delay_s=delay, event="slow_consumer")
                    await asyncio.sleep(delay)
                else:
                    await asyncio.sleep(0.05)  # Normal processing

                duration = time.time() - start
                JOB_DURATION.observe(duration)
                JOBS_COMPLETED.inc()
                JOBS_PROCESSED.labels(status="success").inc()

                logger.info("notification_processed", request_id=request_id,
                            event_type=event_type, order_id=order_id,
                            duration_ms=round(duration*1000, 2),
                            event="notification_processed")
        except Exception as e:
            JOBS_PROCESSED.labels(status="error").inc()
            logger.error("notification_processing_error", error=str(e),
                         error_code="NOTIFICATION_PROCESSING_ERROR")

async def start_consumer():
    global rmq_connection
    while True:
        try:
            rmq_connection = await aio_pika.connect_robust(RABBITMQ_URL)
            channel = await rmq_connection.channel()
            await channel.set_qos(prefetch_count=10)
            queue = await channel.declare_queue("notifications", durable=True)
            await queue.consume(process_message)
            logger.info("consumer started, waiting for messages")
            await asyncio.Future()  # Run forever
        except Exception as e:
            logger.error("consumer error, reconnecting", error=str(e))
            await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer_task
    logger.info("Worker starting")
    consumer_task = asyncio.create_task(start_consumer())
    logger.info("Worker ready")
    yield
    if consumer_task: consumer_task.cancel()
    if rmq_connection: await rmq_connection.close()

app = FastAPI(title="Worker", version=APP_VERSION, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)

@app.get("/health")
async def health():
    return {"status":"ok","service":SERVICE_NAME,"env":APP_ENV,"version":APP_VERSION,
            "slow_consumer":_slow_consumer}

@app.get("/metrics")
async def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/admin/faults")
async def set_fault(request: Request):
    global _slow_consumer, _slow_delay_ms
    body = await request.json()
    fault = body.get("fault","none")
    if fault == "none" or fault == "clear":
        _slow_consumer = False
        logger.info("worker fault cleared")
        return {"status":"ok","fault":"cleared"}
    if fault == "slow_consumer":
        _slow_consumer = True
        _slow_delay_ms = int(body.get("delay_ms", 3000))
        logger.warn("SLOW_CONSUMER_ENABLED", delay_ms=_slow_delay_ms)
        return {"status":"ok","fault":"slow_consumer","delay_ms":_slow_delay_ms}
    return {"status":"error","reason":"unknown fault"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
