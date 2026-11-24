"""ADK Policy/Guardrail BasePlugin, lifecycle callbacks, and history compaction."""

from __future__ import annotations

import re
import time
from typing import Any

from google.adk.apps.app import EventsCompactionConfig
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types

from src.data_providers.base import GuidedErrorResponse
from src.memory import get_memory_store
from src.telemetry import get_pii_redactor, get_structured_logger

logger = get_structured_logger("guardrails")

_PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"(?:reveal|print|dump|leak)\s+(?:your\s+)?system\s+(?:prompt|instructions)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+dan\b", re.IGNORECASE),
    re.compile(r"(?:drop\s+table|union\s+select|;\s*delete\s+from)\s+users", re.IGNORECASE),
    re.compile(r"rm\s+-rf\s+/", re.IGNORECASE),
]

_OFF_TOPIC_PATTERNS = [
    re.compile(
        r"\b(?:write\s+malware|ransomware|exploit\s+payload|synthesize\s+explosives|prescribe\s+antibiotics|file\s+my\s+corporate\s+taxes)\b",
        re.IGNORECASE,
    ),
]


def _extract_latest_user_text(llm_request: LlmRequest) -> str:
    contents = getattr(llm_request, "contents", None) or []
    for content in reversed(contents):
        if getattr(content, "role", None) == "user":
            parts = getattr(content, "parts", None) or []
            texts = [p.text for p in parts if getattr(p, "text", None)]
            if texts:
                return "\n".join(texts)
    return ""


def check_text_against_guardrails(user_text: str) -> tuple[bool, str, str]:
    """Inspect user input for prompt injection or policy violations.

    Returns:
        Tuple `(is_blocked, reason_code, safe_reply)`.
    """
    if not user_text:
        return False, "", ""

    for pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(user_text):
            return (
                True,
                "PROMPT_INJECTION_BLOCKED",
                (
                    "I appreciate the creative improvisation, but I cannot override my system "
                    "constitution or expose internal instructions. I am MilesAgent, your jazz "
                    "concierge—tell me about artists, moods, or playlists you enjoy, and I will "
                    "curate a tailored listening journey."
                ),
            )

    for pattern in _OFF_TOPIC_PATTERNS:
        if pattern.search(user_text):
            return (
                True,
                "OFF_TOPIC_SAFETY_BLOCKED",
                (
                    "I specialize exclusively in music discovery, jazz musicology, and playlist "
                    "curation, so I cannot assist with that request. Share a favorite song, "
                    "genre, or public Spotify playlist whenever you'd like jazz recommendations!"
                ),
            )

    return False, "", ""


async def initialize_jazz_session_state(callback_context: Any) -> genai_types.Content | None:
    """Initialize required ADK session state keys and hydrate persistent SQLite profile."""
    state = callback_context.state
    state["guardrail_blocked"] = False
    if "pending_hitl_confirmation" not in state:
        state["pending_hitl_confirmation"] = None

    user_id = str(state.get("user_id") or "default_listener")
    state["user_id"] = user_id

    if "taste_profile" not in state or not state.get("taste_profile"):
        memory = get_memory_store()
        profile = await memory.get_user_profile(user_id)
        state["taste_profile"] = profile
        state["sonic_fingerprint"] = profile.get("sonic_fingerprint") or {}
        state["listening_history"] = profile.get("listening_history") or []

    state.setdefault("sonic_fingerprint", {})
    state.setdefault("listening_history", [])
    state.setdefault("candidate_albums", [])
    state.setdefault("profiler_brief", "No profiler brief generated yet.")
    state.setdefault("musicologist_candidates", "No musicological candidates selected yet.")
    return None


async def skip_downstream_if_halted(callback_context: Any) -> genai_types.Content | None:
    """Short-circuit downstream pipeline agents when blocked by guardrail or awaiting HITL."""
    state = callback_context.state
    agent_name = getattr(callback_context, "agent_name", "downstream_agent")

    if state.get("guardrail_blocked"):
        logger.info(
            "Skipping downstream agent after guardrail intervention",
            extra={"event": "pipeline_short_circuit", "agent_name": agent_name},
        )
        return genai_types.Content(role="model", parts=[genai_types.Part.from_text(text="")])

    pending = state.get("pending_hitl_confirmation")
    if pending:
        logger.info(
            "Skipping downstream agent while awaiting Human-in-the-Loop confirmation",
            extra={"event": "hitl_pipeline_pause", "agent_name": agent_name},
        )
        if agent_name == "JazzCuratorAgent":
            msg = (
                "⚠️ **Confirmation Required:** Wiping your persistent jazz taste profile and "
                "listening history is permanent. Please reply to confirm if you want me to "
                "proceed with resetting your profile."
            )
            return genai_types.Content(role="model", parts=[genai_types.Part.from_text(text=msg)])
        return genai_types.Content(role="model", parts=[genai_types.Part.from_text(text="")])

    return None


class JazzPolicyGuardrailPlugin(BasePlugin):
    """Runner-wide ADK plugin enforcing safety guardrails, PII scrubbing, and anti-duplication."""

    def __init__(self, name: str = "jazz_policy_guardrail") -> None:
        super().__init__(name=name)
        self._redactor = get_pii_redactor()

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        latest_user_text = _extract_latest_user_text(llm_request)
        blocked, code, reply = check_text_against_guardrails(latest_user_text)
        if blocked:
            callback_context.state["guardrail_blocked"] = True
            logger.warning(
                "Guardrail intercepted unsafe or off-topic prompt",
                extra={
                    "event": "guardrail_triggered",
                    "reason_code": code,
                    "agent_name": getattr(callback_context, "agent_name", "MilesAgent"),
                },
            )
            return LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(text=reply)],
                )
            )
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: Any,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        content = getattr(llm_response, "content", None)
        if not content or not getattr(content, "parts", None):
            return None

        agent_name = getattr(callback_context, "agent_name", "MilesAgent")
        history = callback_context.state.get("listening_history") or []
        heard_titles = {str(h.get("album_title", "")).strip().lower() for h in history if h.get("album_title")}

        modified = False
        new_parts: list[genai_types.Part] = []
        for part in content.parts:
            txt = getattr(part, "text", None)
            if txt:
                scrubbed = self._redactor.scrub_text(txt)
                if agent_name == "JazzCuratorAgent" and heard_titles:
                    repeated = [t for t in heard_titles if t and t in scrubbed.lower()]
                    if repeated:
                        logger.info(
                            "Anti-duplicate guardrail detected previously recommended album mention",
                            extra={
                                "event": "anti_duplicate_check",
                                "agent_name": agent_name,
                                "repeated_albums": repeated,
                            },
                        )
                if scrubbed != txt:
                    modified = True
                    new_parts.append(genai_types.Part.from_text(text=scrubbed))
                else:
                    new_parts.append(part)
            else:
                new_parts.append(part)

        if modified:
            return LlmResponse(
                content=genai_types.Content(role=content.role or "model", parts=new_parts)
            )
        return None

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        tool_context.state["temp:tool_start_ns"] = time.perf_counter_ns()
        if "playlist_url_or_id" in tool_args and isinstance(tool_args["playlist_url_or_id"], str):
            tool_args["playlist_url_or_id"] = self._redactor.strip_url_tracking(
                tool_args["playlist_url_or_id"]
            )
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        err_text = str((result or {}).get("error", ""))
        if tool.name == "reset_user_taste_profile" and "requires confirmation" in err_text.lower():
            tool_context.state["pending_hitl_confirmation"] = {
                "action": "reset_user_taste_profile",
                "user_id": tool_args.get("user_id", "default_listener"),
                "reason": tool_args.get("reason", "User requested profile reset"),
            }
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict[str, Any] | None:
        logger.error(
            "Unhandled tool exception intercepted by guardrail plugin",
            extra={
                "event": "tool_error_recovered",
                "tool_name": tool.name,
                "error_type": type(error).__name__,
            },
        )
        return GuidedErrorResponse(
            error_code="TOOL_EXECUTION_RECOVERED",
            message=f"Tool '{tool.name}' encountered an unexpected condition: {type(error).__name__}.",
            recovery_instructions="Inspect tool arguments and retry with simpler filters or fallback parameters.",
        ).model_dump()


def build_events_compaction_config(
    token_threshold: int = 24000,
    event_retention_size: int = 6,
) -> EventsCompactionConfig:
    """Create ADK token-based EventsCompactionConfig to prevent multi-turn context bloat."""
    return EventsCompactionConfig(
        token_threshold=token_threshold,
        event_retention_size=event_retention_size,
    )
