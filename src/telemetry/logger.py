"""Structured JSON logging with active PII scrubbing and Intent vs. Outcome capture."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from typing import Any

from pythonjsonlogger.json import JsonFormatter

from src.config.settings import get_settings
from src.telemetry.pii_redactor import get_pii_redactor
from src.telemetry.tracer import get_trace_context_ids

_LOGGING_CONFIGURED = False


class JazzJsonFormatter(JsonFormatter):
    """JSON log formatter injecting OpenTelemetry trace context and scrubbing PII."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        redactor = get_pii_redactor()

        log_record["timestamp"] = datetime.now(UTC).isoformat()
        log_record["level"] = record.levelname
        log_record["logger"] = record.name

        trace_ids = get_trace_context_ids()
        log_record.setdefault("trace_id", trace_ids["trace_id"])
        log_record.setdefault("span_id", trace_ids["span_id"])
        log_record.setdefault("agent_name", getattr(record, "agent_name", "MilesAgent"))
        log_record.setdefault("model_id", getattr(record, "model_id", "n/a"))

        if "message" in log_record and isinstance(log_record["message"], str):
            log_record["message"] = redactor.scrub_text(log_record["message"])

        for key in list(log_record.keys()):
            if key not in {"timestamp", "level", "logger", "trace_id", "span_id"}:
                log_record[key] = redactor.scrub_payload(log_record[key])


def configure_logging(level: str | None = None, stream: Any = None) -> logging.Logger:
    """Configure structured JSON logging on the `jazz_concierge` namespace."""
    global _LOGGING_CONFIGURED
    settings = get_settings()
    resolved_level = getattr(logging, (level or settings.log_level).upper(), logging.INFO)

    root_pkg_logger = logging.getLogger("jazz_concierge")
    root_pkg_logger.setLevel(resolved_level)
    root_pkg_logger.propagate = False

    if not _LOGGING_CONFIGURED or stream is not None:
        root_pkg_logger.handlers.clear()
        handler = logging.StreamHandler(stream or sys.stderr)
        formatter = JazzJsonFormatter("%(timestamp)s %(level)s %(name)s %(message)s")
        handler.setFormatter(formatter)
        root_pkg_logger.addHandler(handler)
        _LOGGING_CONFIGURED = True

    return root_pkg_logger


def get_structured_logger(name: str = "jazz_concierge") -> logging.Logger:
    """Return a child logger under `jazz_concierge` configured for JSON output."""
    configure_logging()
    if name == "jazz_concierge" or name.startswith("jazz_concierge."):
        return logging.getLogger(name)
    return logging.getLogger(f"jazz_concierge.{name}")


def log_tool_intent(
    tool_name: str,
    planned_args: dict[str, Any],
    agent_name: str = "MilesAgent",
    model_id: str = "n/a",
) -> None:
    """Emit structured `tool_intent` event before tool execution."""
    redactor = get_pii_redactor()
    log = get_structured_logger("tools")
    log.info(
        f"Intent to invoke tool '{tool_name}'",
        extra={
            "event": "tool_intent",
            "tool_name": tool_name,
            "planned_args": redactor.scrub_payload(planned_args),
            "agent_name": agent_name,
            "model_id": model_id,
        },
    )


def log_tool_outcome(
    tool_name: str,
    status: str,
    outcome_summary: str,
    duration_ms: float,
    agent_name: str = "MilesAgent",
    model_id: str = "n/a",
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    """Emit structured `tool_outcome` event after tool completion."""
    redactor = get_pii_redactor()
    log = get_structured_logger("tools")
    payload: dict[str, Any] = {
        "event": "tool_outcome",
        "tool_name": tool_name,
        "status": status,
        "outcome_summary": redactor.scrub_text(outcome_summary),
        "duration_ms": round(duration_ms, 2),
        "agent_name": agent_name,
        "model_id": model_id,
    }
    if extra_metadata:
        payload["metadata"] = redactor.scrub_payload(extra_metadata)
    log.info(f"Completed tool '{tool_name}' with status '{status}'", extra=payload)
