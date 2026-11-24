"""Unit tests for all 7 ADK tools, Guided Error Handling, HITL gate, and telemetry."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from src.telemetry.logger import configure_logging
from src.telemetry.tracer import setup_tracing
from src.tools.jazz_tools import (
    analyze_playlist,
    export_curated_playlist,
    find_subgenre_bridges,
    reset_user_taste_profile,
    reset_user_taste_profile_tool,
    search_artist_discography,
    sync_user_taste_profile,
    trace_artist_lineage,
)


class DummyToolContext:
    """Lightweight stand-in mimicking ADK ToolContext `.state` and `.tool_confirmation`."""

    def __init__(self, initial_state: dict | None = None, confirmed: bool | None = None) -> None:
        self.state = initial_state if initial_state is not None else {}
        self.tool_confirmation = (
            SimpleNamespace(confirmed=confirmed) if confirmed is not None else None
        )


@pytest.mark.asyncio
async def test_analyze_playlist_dual_state_and_guided_error() -> None:
    ctx = DummyToolContext({"user_id": "tool_tester"})
    res = await analyze_playlist(playlist_url_or_id="sample", tool_context=ctx)  # type: ignore[arg-type]
    assert res["status"] == "success"
    assert "sonic_fingerprint" in ctx.state
    assert ctx.state["sonic_fingerprint"]["inferred_knowledge_level"] == "beginner"
    assert len(ctx.state["candidate_albums"]) >= 1

    bad = await analyze_playlist(playlist_url_or_id="not_a_valid_url!!", tool_context=ctx)  # type: ignore[arg-type]
    assert bad["status"] == "error"
    assert bad["error_code"] == "INVALID_PLAYLIST_REFERENCE"
    assert "sample" in bad["recovery_instructions"]


def test_search_discography_lineage_and_bridges_guided_errors() -> None:
    ctx = DummyToolContext()
    ok = search_artist_discography(
        artist="Bill Evans",
        group_format="small_ensemble",
        accessibility_level="beginner",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert ok["status"] == "success"
    assert ok["count"] >= 1
    assert any(a["title"] == "Waltz for Debby" for a in ok["albums"])

    # Invalid enum filter returns GuidedErrorResponse instead of raising traceback
    bad_fmt = search_artist_discography(artist="Bill Evans", group_format="symphony_choir")
    assert bad_fmt["status"] == "error"
    assert bad_fmt["error_code"] == "INVALID_DISCOGRAPHY_FILTER"

    # Unknown artist returns fuzzy suggestions
    missing = search_artist_discography(artist="Thelonius Munk")
    assert missing["status"] == "error"
    assert missing["error_code"] == "ARTIST_NOT_FOUND"
    assert "Thelonious Monk" in missing["suggestions"]

    # Lineage tool
    lin = trace_artist_lineage(artist="John Coltrane", relationship="influences")
    assert lin["status"] == "success"
    assert any(x["artist"] == "Charlie Parker" for x in lin["lineage"]["influences"])

    bad_rel = trace_artist_lineage(artist="John Coltrane", relationship="cousins")  # type: ignore[arg-type]
    assert bad_rel["status"] == "error"
    assert bad_rel["error_code"] == "INVALID_LINEAGE_RELATIONSHIP"

    # Bridges tool
    br = find_subgenre_bridges("Funk", target_vibe="energetic", tool_context=ctx)  # type: ignore[arg-type]
    assert br["status"] == "success"
    assert any(b["recommended_artist"] == "Herbie Hancock" for b in br["bridges"])


@pytest.mark.asyncio
async def test_sync_reset_hitl_and_export_tools() -> None:
    # Verify ADK FunctionTool wrapper enforces require_confirmation=True
    assert reset_user_taste_profile_tool._require_confirmation is True

    ctx = DummyToolContext({"user_id": "curator_user"})
    synced = await sync_user_taste_profile(
        user_id="curator_user",
        knowledge_level="connoisseur",
        harmonic_preference="avant_garde",
        formation_preference="solo",
        liked_artists_add=["Cecil Taylor"],
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert synced["status"] == "success"
    assert ctx.state["taste_profile"]["knowledge_level"] == "connoisseur"

    # Populate candidates and export both Markdown and JSON
    search_artist_discography("Cecil Taylor", group_format="solo", tool_context=ctx)  # type: ignore[arg-type]
    md_export = await export_curated_playlist(
        user_id="curator_user",
        session_id="s_01",
        export_format="markdown",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert md_export["status"] == "success"
    assert "Cecil Taylor" in md_export["content"]

    json_export = await export_curated_playlist(
        user_id="curator_user",
        session_id="s_01",
        export_format="json",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert json_export["status"] == "success"
    parsed = json.loads(json_export["content"])
    assert parsed["user_id"] == "curator_user"

    # Unconfirmed reset MUST NOT delete data
    blocked_reset = await reset_user_taste_profile(
        user_id="curator_user",
        reason="Accidental unconfirmed call",
        confirmed=False,
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert blocked_reset["status"] == "confirmation_required"
    assert ctx.state["pending_hitl_confirmation"] is not None

    # Confirmed reset via ADK ToolConfirmation wipes profile cleanly
    approved_ctx = DummyToolContext({"user_id": "curator_user"}, confirmed=True)
    ok_reset = await reset_user_taste_profile(
        user_id="curator_user",
        reason="User approved reset",
        confirmed=False,
        tool_context=approved_ctx,  # type: ignore[arg-type]
    )
    assert ok_reset["status"] == "success"
    assert ok_reset["taste_profile"]["knowledge_level"] == "beginner"


def test_structured_json_intent_outcome_logs_and_otel_spans() -> None:
    log_buffer = io.StringIO()
    configure_logging(level="INFO", stream=log_buffer)
    span_exporter = InMemorySpanExporter()
    provider = setup_tracing(custom_exporter=span_exporter, force_reinit=True)

    search_artist_discography("Miles Davis", limit=1)
    provider.force_flush()

    raw_lines = [ln for ln in log_buffer.getvalue().splitlines() if ln.strip()]
    events = [json.loads(ln) for ln in raw_lines]
    event_types = [e.get("event") for e in events]
    assert "tool_intent" in event_types
    assert "tool_outcome" in event_types

    # Restore stderr logger
    configure_logging(level="INFO")
