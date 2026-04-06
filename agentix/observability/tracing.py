"""
OpenTelemetry instrumentation for Agentix.

Instruments:
  - trigger.received    → root span per trigger
  - agent.spawn         → child span
  - llm.call            → child span (model, tokens, latency)
  - tool.call           → child span (tool name, input size, result size)
  - agent.complete      → closes root span with status

Exporters configured via environment:
  OTEL_EXPORTER_OTLP_ENDPOINT  → OTLP (Jaeger/Tempo/etc.)
  OTEL_EXPORTER_CONSOLE=true   → stdout (dev)
  (neither)                    → no-op tracer (zero overhead)

Usage:
    from agentix.observability.tracing import get_tracer, instrument_watchdog
    tracer = get_tracer()
    with tracer.start_as_current_span("my.span") as span:
        span.set_attribute("key", "value")
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# No-op fallbacks (when opentelemetry not installed)
# ---------------------------------------------------------------------------

class _NoOpSpan:
    def set_attribute(self, *a, **kw): pass
    def set_status(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


class _NoOpTracer:
    def start_as_current_span(self, name, **kwargs): return _NoOpSpan()
    def start_span(self, name, **kwargs): return _NoOpSpan()


class _NoOpCounter:
    def add(self, *a, **kw): pass
    def record(self, *a, **kw): pass


class _NoOpMeter:
    def create_counter(self, *a, **kw): return _NoOpCounter()
    def create_histogram(self, *a, **kw): return _NoOpCounter()


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def _setup_otel() -> Any:
    """
    Configure the OTel tracer provider. Returns the tracer provider or None
    if opentelemetry packages are not installed.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME

        resource = Resource.create({SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "agentix")})
        provider = TracerProvider(resource=resource)

        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                provider.add_span_processor(BatchSpanProcessor(exporter))
                logger.info("OTel: OTLP exporter configured → %s", otlp_endpoint)
            except ImportError:
                logger.warning("OTel: opentelemetry-exporter-otlp not installed")

        if os.environ.get("OTEL_EXPORTER_CONSOLE", "").lower() in ("1", "true"):
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            logger.info("OTel: console exporter enabled")

        trace.set_tracer_provider(provider)
        return provider

    except ImportError:
        logger.info("opentelemetry-sdk not installed — tracing disabled. pip install opentelemetry-sdk")
        return None


_provider = _setup_otel()


def get_tracer(name: str = "agentix"):
    """Return an OTel tracer (no-op if SDK not installed)."""
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------

def record_trigger(envelope: dict):
    """Create a root span for a received trigger. Returns a context manager."""
    tracer = get_tracer()
    span_name = f"trigger.{envelope['channel']}"
    return tracer.start_as_current_span(
        span_name,
        attributes={
            "trigger.id": envelope["id"],
            "trigger.channel": envelope["channel"],
            "agent.id": envelope["agent_id"],
            "caller.identity_id": envelope["caller"]["identity_id"],
            "caller.tenant_id": envelope["caller"].get("tenant_id", "default"),
        },
    )


def record_llm_call(model_id: str, input_tokens: int, output_tokens: int, latency_ms: float):
    """Record an LLM call as a child span with token metrics."""
    tracer = get_tracer()
    with tracer.start_as_current_span("llm.call") as span:
        try:
            span.set_attribute("llm.model", model_id)
            span.set_attribute("llm.input_tokens", input_tokens)
            span.set_attribute("llm.output_tokens", output_tokens)
            span.set_attribute("llm.total_tokens", input_tokens + output_tokens)
            span.set_attribute("llm.latency_ms", latency_ms)
        except Exception:
            pass


def record_tool_call(tool_name: str, input_size: int, result_size: int, error: str | None = None):
    """Record a tool invocation as a child span."""
    tracer = get_tracer()
    with tracer.start_as_current_span("tool.call") as span:
        try:
            span.set_attribute("tool.name", tool_name)
            span.set_attribute("tool.input_size", input_size)
            span.set_attribute("tool.result_size", result_size)
            if error:
                span.set_attribute("tool.error", error)
                span.set_status(_error_status(error))
        except Exception:
            pass


def record_agent_complete(trigger_id: str, agent_id: str, success: bool, response_len: int):
    """Record agent completion metrics."""
    tracer = get_tracer()
    with tracer.start_as_current_span("agent.complete") as span:
        try:
            span.set_attribute("trigger.id", trigger_id)
            span.set_attribute("agent.id", agent_id)
            span.set_attribute("agent.success", success)
            span.set_attribute("agent.response_len", response_len)
            if not success:
                span.set_status(_error_status("agent failed"))
        except Exception:
            pass


def _error_status(msg: str):
    try:
        from opentelemetry.trace import StatusCode, Status
        return Status(StatusCode.ERROR, msg)
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Metrics (Prometheus-compatible counters/histograms)
# ---------------------------------------------------------------------------

def _setup_metrics():
    try:
        from opentelemetry import metrics
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader

        readers = []
        if os.environ.get("OTEL_EXPORTER_CONSOLE", "").lower() in ("1", "true"):
            readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=60000))

        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
                readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=otlp_endpoint)))
            except ImportError:
                pass

        provider = MeterProvider(metric_readers=readers)
        metrics.set_meter_provider(provider)
        return metrics.get_meter("agentix")
    except ImportError:
        return _NoOpMeter()


_meter = _setup_metrics()


def get_meter():
    return _meter


