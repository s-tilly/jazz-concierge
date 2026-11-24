"""Unit tests for PIIRedactor scrubbing, AsyncSQLiteMemory, and history compaction."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from src.memory import AsyncSQLiteMemory, compact_conversation_turns
from src.telemetry import PIIRedactor


def test_pii_redactor_scrubs_emails_phones_tokens_ips_and_si_params() -> None:
    redactor = PIIRedactor()
    raw = (
        "Contact listener jane.doe@example.org or +1 (415) 555-2671 from 203.0.113.42 "
        "with key AIzaSyB1234567890abcdefghijklmnop and playlist "
        "https://open.spotify.com/playlist/37i9dQZF1DXbITWG1ZJKYt?si=9f8e7d6c5b&utm_source=share"
    )
    scrubbed = redactor.scrub_text(raw)
    assert "jane.doe@example.org" not in scrubbed
    assert "[REDACTED_EMAIL]" in scrubbed
    assert "555-2671" not in scrubbed
    assert "[REDACTED_PHONE]" in scrubbed
    assert "203.0.113.42" not in scrubbed
    assert "[REDACTED_IP]" in scrubbed
    assert "AIzaSyB1234567890" not in scrubbed
    assert "[REDACTED_SECRET]" in scrubbed
    assert "si=9f8e7d6c5b" not in scrubbed
    assert "utm_source" not in scrubbed
    assert "https://open.spotify.com/playlist/37i9dQZF1DXbITWG1ZJKYt" in scrubbed

    payload = {
        "spotify_client_secret": "top_secret_val",
        "nested": ["email user@domain.io", "https://open.spotify.com/playlist/1111111111111111111111?si=tok"],
    }
    clean_payload = redactor.scrub_payload(payload)
    assert clean_payload["spotify_client_secret"] == "[REDACTED_SECRET]"
    assert "[REDACTED_EMAIL]" in clean_payload["nested"][0]
    assert "?si=" not in clean_payload["nested"][1]


@pytest.mark.asyncio
async def test_async_sqlite_memory_crud_pii_scrubbing_and_reset(tmp_path: Path) -> None:
    db_file = tmp_path / "memory_test.db"
    mem = AsyncSQLiteMemory(db_path=db_file)

    # Email passed as user_id must be scrubbed before hitting SQLite
    prof = await mem.update_taste_profile(
        user_id="listener@private-mail.com",
        knowledge_level="intermediate",
        harmonic_preference="modal_modern",
        formation_preference="small_ensemble",
        liked_artists_add=["Miles Davis", "Wayne Shorter"],
        disliked_artists_add=["Kenny G"],
    )
    assert "private-mail.com" not in prof["user_id"]
    assert prof["knowledge_level"] == "intermediate"
    assert "Miles Davis" in prof["liked_artists"]
    assert "Kenny G" in prof["disliked_artists"]

    # Save playlist audit with ?si= token and verify SQLite table lacks ?si=
    await mem.save_playlist_audit(
        user_id="listener_a",
        report_dict={
            "playlist_id": "37i9dQZF1DXbITWG1ZJKYt",
            "sanitized_playlist_ref": "https://open.spotify.com/playlist/37i9dQZF1DXbITWG1ZJKYt?si=leak123",
            "playlist_title": "Late Night Mix",
            "dominant_genres": ["soul", "funk"],
            "estimated_tempo_bpm": 102.5,
            "inferred_knowledge_level": "novice",
            "suggested_harmonic_preference": "modal_modern",
            "suggested_formation_preference": "small_ensemble",
        },
    )
    async with aiosqlite.connect(db_file) as conn:
        cur = await conn.execute("SELECT playlist_ref, fingerprint_report_json FROM playlist_audits")
        row = await cur.fetchone()
        assert row is not None
        assert "si=leak123" not in row[0]
        assert "si=leak123" not in row[1]

    # Record listening history & verify deduplication
    await mem.record_listening_history("listener_a", "Kind of Blue", "Miles Davis")
    await mem.record_listening_history("listener_a", "Kind of Blue", "Miles Davis")
    loaded = await mem.get_user_profile("listener_a")
    assert len(loaded["listening_history"]) == 1
    assert loaded["listening_history"][0]["album_title"] == "Kind of Blue"

    # Reset profile purges all 4 tables for that user
    deleted = await mem.reset_user_profile("listener_a")
    assert deleted["deleted_history_rows"] == 1
    assert deleted["deleted_audit_rows"] == 1
    assert deleted["deleted_user_rows"] == 1

    post_reset = await mem.get_user_profile("listener_a")
    assert post_reset["knowledge_level"] == "beginner"
    assert post_reset["listening_history"] == []


def test_sliding_window_context_compaction() -> None:
    turns = [
        {"role": "user", "content": f"Turn {i} mentioning secret@mail.com and jazz"}
        for i in range(10)
    ]
    summary, recent = compact_conversation_turns(turns, max_recent_turns=4)
    assert len(recent) == 4
    assert "Turn 0" in summary
    assert "secret@mail.com" not in summary
    assert "[REDACTED_EMAIL]" in summary
