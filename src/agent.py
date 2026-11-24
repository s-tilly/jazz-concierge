"""Canonical Google ADK entrypoint exposing `root_agent`, `app`, and async execution helper."""

from __future__ import annotations

import time
import warnings
from typing import Any

from google.adk.agents import SequentialAgent
from google.adk.apps.app import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.genai import types as genai_types

from src.agents import (
    JazzPolicyGuardrailPlugin,
    build_events_compaction_config,
    create_curator_agent,
    create_musicologist_agent,
    create_profiler_agent,
)
from src.telemetry import configure_logging, get_structured_logger, get_tracer, setup_tracing

configure_logging()
setup_tracing()
logger = get_structured_logger("orchestrator")


def create_root_agent() -> SequentialAgent:
    """Create a fresh cooperative SequentialAgent pipeline (`Profiler -> Musicologist -> Curator`)."""
    profiler = create_profiler_agent()
    musicologist = create_musicologist_agent()
    curator = create_curator_agent()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return SequentialAgent(
            name="MilesAgent",
            description=(
                "Autonomous multi-agent jazz concierge orchestrating conversational profiling, "
                "Spotify/JSON playlist auditing, musicological lineage research, and curation."
            ),
            sub_agents=[profiler, musicologist, curator],
        )


def create_jazz_app(name: str = "jazz_concierge") -> App:
    """Build the ADK `App` configured with guardrails, HITL resumability, and compaction."""
    return App(
        name=name,
        root_agent=create_root_agent(),
        plugins=[JazzPolicyGuardrailPlugin()],
        events_compaction_config=build_events_compaction_config(),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


# Canonical module-level exports for `adk run`, `adk web`, and `agents-cli`
root_agent = create_root_agent()
app = App(
    name="jazz_concierge",
    root_agent=root_agent,
    plugins=[JazzPolicyGuardrailPlugin()],
    events_compaction_config=build_events_compaction_config(),
    resumability_config=ResumabilityConfig(is_resumable=True),
)


async def run_concierge_turn(
    user_message: str,
    *,
    user_id: str = "default_listener",
    session_id: str = "session_default",
    session_service: BaseSessionService | None = None,
    adk_app: App | None = None,
) -> dict[str, Any]:
    """Execute one full multi-agent turn and return final response, agent traces, and state."""
    active_app = adk_app or create_jazz_app()
    svc = session_service or InMemorySessionService()

    existing = await svc.get_session(
        app_name=active_app.name,
        user_id=user_id,
        session_id=session_id,
    )
    if existing is None:
        await svc.create_session(
            app_name=active_app.name,
            user_id=user_id,
            session_id=session_id,
            state={"user_id": user_id},
        )

    runner = Runner(app=active_app, session_service=svc)
    tracer = get_tracer("orchestrator.turn")
    start = time.perf_counter()

    agent_outputs: dict[str, str] = {}
    tool_calls_observed: list[str] = []

    with tracer.start_as_current_span("miles_agent.turn") as span:
        span.set_attribute("user.id", user_id)
        span.set_attribute("session.id", session_id)

        msg = genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=user_message)],
        )

        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=msg,
        ):
            author = getattr(event, "author", "unknown")
            content = getattr(event, "content", None)
            if content and getattr(content, "parts", None):
                text_chunks: list[str] = []
                for part in content.parts:
                    fc = getattr(part, "function_call", None)
                    if fc and getattr(fc, "name", None):
                        tool_calls_observed.append(fc.name)
                    txt = getattr(part, "text", None)
                    if txt and txt.strip():
                        text_chunks.append(txt.strip())
                if text_chunks:
                    agent_outputs[author] = "\n".join(text_chunks)

    updated_session = await svc.get_session(
        app_name=active_app.name,
        user_id=user_id,
        session_id=session_id,
    )
    final_state = dict(updated_session.state) if updated_session else {}

    # Prefer JazzCuratorAgent's polished response unless short-circuited by guardrail or HITL
    final_text = (
        agent_outputs.get("JazzCuratorAgent")
        or agent_outputs.get("JazzProfilerAgent")
        or agent_outputs.get("JazzMusicologistAgent")
        or next(iter(reversed(list(agent_outputs.values()))), "")
    )

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.info(
        "Completed MilesAgent turn",
        extra={
            "event": "turn_completed",
            "latency_ms": latency_ms,
            "agents_invoked": list(agent_outputs.keys()),
            "tools_called": tool_calls_observed,
        },
    )

    return {
        "response": final_text,
        "agent_outputs": agent_outputs,
        "tool_calls": tool_calls_observed,
        "state": final_state,
        "latency_ms": latency_ms,
    }
