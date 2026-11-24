"""OpenTelemetry distributed tracing setup with optional Google Cloud Trace export."""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

_TRACING_INITIALIZED = False


def setup_tracing(
    service_name: str = "jazz-concierge",
    custom_exporter: SpanExporter | None = None,
    force_reinit: bool = False,
) -> TracerProvider:
    """Initialize OpenTelemetry TracerProvider and optional Cloud Trace exporter."""
    global _TRACING_INITIALIZED
    current = trace.get_tracer_provider()
    if _TRACING_INITIALIZED and not force_reinit and isinstance(current, TracerProvider):
        return current

    settings = get_settings()
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "1.0.0",
            "agent.system": "google-adk",
        }
    )
    provider = TracerProvider(resource=resource)

    if custom_exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(custom_exporter))
    elif settings.otel_exporter_gcp_trace:
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            gcp_exporter = CloudTraceSpanExporter(
                project_id=settings.google_cloud_project or None
            )
            provider.add_span_processor(BatchSpanProcessor(gcp_exporter))
        except Exception as exc:
            logger.warning("Cloud Trace exporter unavailable (%s); continuing locally.", exc)

    if not _TRACING_INITIALIZED:
        trace.set_tracer_provider(provider)
        _TRACING_INITIALIZED = True

    return provider


def get_tracer(module_name: str = "jazz_concierge") -> trace.Tracer:
    """Return an OpenTelemetry Tracer, initializing the provider if needed."""
    setup_tracing()
    return trace.get_tracer(module_name)


def get_trace_context_ids() -> dict[str, str]:
    """Extract current hex `trace_id` and `span_id` from active OpenTelemetry context."""
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx and ctx.is_valid:
        return {
            "trace_id": f"{ctx.trace_id:032x}",
            "span_id": f"{ctx.span_id:016x}",
        }
    return {
        "trace_id": "0" * 32,
        "span_id": "0" * 16,
    }


def set_span_attributes(span: Any, attributes: dict[str, Any]) -> None:
    """Attach safe primitive attributes to an OpenTelemetry span."""
    for key, val in attributes.items():
        if val is None:
            continue
        if isinstance(val, (str, bool, int, float)):
            span.set_attribute(key, val)
        else:
            span.set_attribute(key, str(val))
