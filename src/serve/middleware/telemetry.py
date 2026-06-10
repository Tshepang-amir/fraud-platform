"""OpenTelemetry instrumentation for the FastAPI scoring service.

Exporter target is controlled by environment variables:

  OTEL_EXPORTER_OTLP_ENDPOINT  — OTLP/HTTP endpoint
                                  e.g. https://otlp-gateway-prod-sa-east-1.grafana.net/otlp
  OTEL_EXPORTER_OTLP_HEADERS   — comma-separated key=value auth headers
                                  e.g. Authorization=Basic <base64(instanceId:token)>
  OTEL_SERVICE_NAME            — overrides default service name "fraud-scorer"

If OTEL_EXPORTER_OTLP_ENDPOINT is not set, traces/metrics are discarded (safe for local dev).

Custom metrics exposed:
  fraud_score        — Histogram of champion fraud probability per request
  fraud_decisions    — Counter of APPROVE/REVIEW/DECLINE decisions (label: decision)
"""

from __future__ import annotations

import logging
import os

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

logger = logging.getLogger(__name__)

_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "fraud-scorer")

# Module-level meter — initialised after configure_telemetry() is called
_meter: metrics.Meter | None = None
_fraud_score_histogram: metrics.Histogram | None = None
_latency_histogram: metrics.Histogram | None = None
_decision_counter: metrics.Counter | None = None


def configure_telemetry() -> None:
    """Set up OTel trace + metrics providers. Called once in app lifespan."""
    global _meter, _fraud_score_histogram, _latency_histogram, _decision_counter

    resource = Resource.create({"service.name": _SERVICE_NAME})
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    # ── Traces ─────────────────────────────────────────────────────────────────
    tracer_provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            tracer_provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces"))
            )
            logger.info("OTel traces → %s", otlp_endpoint)
        except ImportError:
            logger.warning("OTLP HTTP exporter not installed; traces discarded")
    elif os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG":
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(tracer_provider)

    # ── Metrics ────────────────────────────────────────────────────────────────
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{otlp_endpoint}/v1/metrics"),
                export_interval_millis=15_000,
            )
            logger.info("OTel metrics → %s", otlp_endpoint)
        except ImportError:
            logger.warning("OTLP HTTP metric exporter not installed; using console")
            metric_reader = PeriodicExportingMetricReader(
                ConsoleMetricExporter(), export_interval_millis=60_000
            )
    else:
        metric_reader = PeriodicExportingMetricReader(
            ConsoleMetricExporter(), export_interval_millis=60_000
        )

    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    _meter = metrics.get_meter("fraud-scorer")
    _fraud_score_histogram = _meter.create_histogram(
        "fraud_score",
        description="Champion model fraud probability per scored transaction",
        unit="1",
    )
    _latency_histogram = _meter.create_histogram(
        "fraud_score_latency_ms",
        description="Champion scoring endpoint latency in milliseconds",
        unit="1",
    )
    _decision_counter = _meter.create_counter(
        "fraud_decisions_total",
        description="Count of scoring decisions by outcome",
        unit="1",
    )

    logger.info("OpenTelemetry configured (service=%s)", _SERVICE_NAME)


def record_score(fraud_score: float, decision: str, latency_ms: float) -> None:
    """Record a scored transaction into OTel metrics. Called from score router."""
    if _fraud_score_histogram is not None:
        _fraud_score_histogram.record(fraud_score)
    if _latency_histogram is not None:
        _latency_histogram.record(latency_ms)
    if _decision_counter is not None:
        _decision_counter.add(1, {"decision": decision})


def instrument_app(app: object) -> None:
    """Auto-instrument the FastAPI app with OTel HTTP server spans."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
        logger.info("FastAPIInstrumentor applied")
    except Exception:
        logger.warning("FastAPIInstrumentor unavailable — HTTP spans disabled", exc_info=True)
