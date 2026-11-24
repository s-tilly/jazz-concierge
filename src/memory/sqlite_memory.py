"""Asynchronous SQLite user taste profile, playlist audit, and history store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from src.config.settings import PROJECT_ROOT, get_settings
from src.telemetry.pii_redactor import get_pii_redactor

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    knowledge_level TEXT DEFAULT 'beginner' CHECK(knowledge_level IN ('beginner', 'novice', 'intermediate', 'connoisseur')),
    harmonic_preference TEXT DEFAULT 'standard_swing',
    formation_preference TEXT DEFAULT 'small_ensemble',
    sonic_fingerprint_json TEXT DEFAULT '{}',
    compacted_summary TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS playlist_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    playlist_ref TEXT,
    playlist_name TEXT,
    dominant_genres_json TEXT,
    estimated_tempo_bpm REAL,
    fingerprint_report_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS artist_preferences (
    user_id TEXT,
    artist_name TEXT,
    preference_type TEXT CHECK(preference_type IN ('liked', 'disliked', 'known')),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, artist_name),
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS listening_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    album_title TEXT,
    artist_name TEXT,
    recommended_by_agent TEXT,
    user_feedback TEXT DEFAULT 'neutral',
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);
"""


def compact_conversation_turns(
    turns: list[dict[str, str]],
    max_recent_turns: int = 6,
    existing_summary: str = "",
) -> tuple[str, list[dict[str, str]]]:
    """Sliding-window conversation compactor preserving key preferences while trimming old turns."""
    redactor = get_pii_redactor()
    if len(turns) <= max_recent_turns:
        return redactor.scrub_text(existing_summary), [redactor.scrub_payload(t) for t in turns]

    older = turns[:-max_recent_turns]
    recent = [redactor.scrub_payload(t) for t in turns[-max_recent_turns:]]

    digest_fragments: list[str] = []
    if existing_summary.strip():
        digest_fragments.append(existing_summary.strip())
    for item in older:
        role = item.get("role", "user")
        snippet = redactor.scrub_text(str(item.get("content", "")).strip())
        if snippet:
            compact_line = " ".join(snippet.split())[:160]
            digest_fragments.append(f"[{role}] {compact_line}")

    combined = " | ".join(digest_fragments)[-1800:]
    return combined, recent


class AsyncSQLiteMemory:
    """Non-blocking `aiosqlite` persistence layer with automatic PII scrubbing."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        raw_path = db_path or get_settings().sqlite_db_path
        path_obj = Path(raw_path)
        if not path_obj.is_absolute():
            path_obj = PROJECT_ROOT / path_obj
        self.db_path = path_obj
        self._initialized = False
        self._redactor = get_pii_redactor()

    async def initialize(self) -> None:
        """Ensure parent directory and SQLite schema tables exist."""
        if self._initialized and self.db_path.exists():
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA_SQL)
            await db.commit()
        self._initialized = True

    async def _ensure_user_row(self, db: aiosqlite.Connection, clean_uid: str) -> None:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (clean_uid,),
        )

    async def get_user_profile(self, user_id: str) -> dict[str, Any]:
        """Fetch full taste profile, artist preferences, and listening history for a user."""
        await self.initialize()
        clean_uid = self._redactor.scrub_user_id(user_id)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await self._ensure_user_row(db, clean_uid)
            await db.commit()

            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (clean_uid,))
            user_row = await cursor.fetchone()

            pref_cursor = await db.execute(
                "SELECT artist_name, preference_type FROM artist_preferences WHERE user_id = ? ORDER BY timestamp ASC",
                (clean_uid,),
            )
            pref_rows = await pref_cursor.fetchall()

            hist_cursor = await db.execute(
                "SELECT album_title, artist_name, recommended_by_agent, user_feedback, timestamp "
                "FROM listening_history WHERE user_id = ? ORDER BY id DESC LIMIT 50",
                (clean_uid,),
            )
            hist_rows = await hist_cursor.fetchall()

        liked = [r["artist_name"] for r in pref_rows if r["preference_type"] == "liked"]
        disliked = [r["artist_name"] for r in pref_rows if r["preference_type"] == "disliked"]
        known = [r["artist_name"] for r in pref_rows if r["preference_type"] == "known"]

        try:
            sonic_fp = json.loads(user_row["sonic_fingerprint_json"] or "{}") if user_row else {}
        except json.JSONDecodeError:
            sonic_fp = {}

        history = [
            {
                "album_title": r["album_title"],
                "artist_name": r["artist_name"],
                "recommended_by_agent": r["recommended_by_agent"],
                "user_feedback": r["user_feedback"],
                "timestamp": r["timestamp"],
            }
            for r in hist_rows
        ]

        return {
            "user_id": clean_uid,
            "knowledge_level": user_row["knowledge_level"] if user_row else "beginner",
            "harmonic_preference": user_row["harmonic_preference"] if user_row else "standard_swing",
            "formation_preference": user_row["formation_preference"] if user_row else "small_ensemble",
            "liked_artists": liked,
            "disliked_artists": disliked,
            "known_artists": known,
            "sonic_fingerprint": sonic_fp,
            "compacted_summary": user_row["compacted_summary"] if user_row else "",
            "listening_history": history,
        }

    async def update_taste_profile(
        self,
        user_id: str,
        knowledge_level: str | None = None,
        harmonic_preference: str | None = None,
        formation_preference: str | None = None,
        liked_artists_add: list[str] | None = None,
        disliked_artists_add: list[str] | None = None,
        compacted_summary: str | None = None,
    ) -> dict[str, Any]:
        """Atomically update user aesthetic profile and artist preferences after PII scrubbing."""
        await self.initialize()
        clean_uid = self._redactor.scrub_user_id(user_id)

        async with aiosqlite.connect(self.db_path) as db:
            await self._ensure_user_row(db, clean_uid)

            updates: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
            params: list[Any] = []

            if knowledge_level in {"beginner", "novice", "intermediate", "connoisseur"}:
                updates.append("knowledge_level = ?")
                params.append(knowledge_level)
            if harmonic_preference:
                updates.append("harmonic_preference = ?")
                params.append(self._redactor.scrub_text(harmonic_preference.strip()))
            if formation_preference:
                updates.append("formation_preference = ?")
                params.append(self._redactor.scrub_text(formation_preference.strip()))
            if compacted_summary is not None:
                updates.append("compacted_summary = ?")
                params.append(self._redactor.scrub_text(compacted_summary))

            params.append(clean_uid)
            await db.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?",
                tuple(params),
            )

            for artist in liked_artists_add or []:
                clean_artist = self._redactor.scrub_text(artist.strip())
                if clean_artist:
                    await db.execute(
                        "INSERT INTO artist_preferences (user_id, artist_name, preference_type) "
                        "VALUES (?, ?, 'liked') "
                        "ON CONFLICT(user_id, artist_name) DO UPDATE SET preference_type = 'liked', timestamp = CURRENT_TIMESTAMP",
                        (clean_uid, clean_artist),
                    )

            for artist in disliked_artists_add or []:
                clean_artist = self._redactor.scrub_text(artist.strip())
                if clean_artist:
                    await db.execute(
                        "INSERT INTO artist_preferences (user_id, artist_name, preference_type) "
                        "VALUES (?, ?, 'disliked') "
                        "ON CONFLICT(user_id, artist_name) DO UPDATE SET preference_type = 'disliked', timestamp = CURRENT_TIMESTAMP",
                        (clean_uid, clean_artist),
                    )

            await db.commit()

        return await self.get_user_profile(clean_uid)

    async def save_playlist_audit(
        self,
        user_id: str,
        report_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a scrubbed Sonic Fingerprint report in `playlist_audits` and `users`."""
        await self.initialize()
        clean_uid = self._redactor.scrub_user_id(user_id)
        scrubbed_report = self._redactor.scrub_payload(report_dict)
        playlist_ref = self._redactor.strip_url_tracking(
            str(scrubbed_report.get("sanitized_playlist_ref") or scrubbed_report.get("playlist_id", "sample"))
        )
        playlist_name = str(scrubbed_report.get("playlist_title", "Audited Playlist"))
        dominant_genres_json = json.dumps(scrubbed_report.get("dominant_genres", []), ensure_ascii=False)
        bpm = float(scrubbed_report.get("estimated_tempo_bpm", 108.0))
        report_json = json.dumps(scrubbed_report, ensure_ascii=False)

        inferred_level = scrubbed_report.get("inferred_knowledge_level")
        suggested_harm = scrubbed_report.get("suggested_harmonic_preference")
        suggested_form = scrubbed_report.get("suggested_formation_preference")

        async with aiosqlite.connect(self.db_path) as db:
            await self._ensure_user_row(db, clean_uid)
            await db.execute(
                "INSERT INTO playlist_audits "
                "(user_id, playlist_ref, playlist_name, dominant_genres_json, estimated_tempo_bpm, fingerprint_report_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (clean_uid, playlist_ref, playlist_name, dominant_genres_json, bpm, report_json),
            )
            await db.execute(
                "UPDATE users SET sonic_fingerprint_json = ?, "
                "knowledge_level = COALESCE(?, knowledge_level), "
                "harmonic_preference = COALESCE(?, harmonic_preference), "
                "formation_preference = COALESCE(?, formation_preference), "
                "updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (
                    report_json,
                    inferred_level if inferred_level in {"beginner", "novice", "intermediate", "connoisseur"} else None,
                    suggested_harm,
                    suggested_form,
                    clean_uid,
                ),
            )
            await db.commit()

        return await self.get_user_profile(clean_uid)

    async def record_listening_history(
        self,
        user_id: str,
        album_title: str,
        artist_name: str,
        recommended_by_agent: str = "JazzCuratorAgent",
        user_feedback: str = "neutral",
    ) -> None:
        """Record a recommended album in `listening_history` to prevent future duplicates."""
        await self.initialize()
        clean_uid = self._redactor.scrub_user_id(user_id)
        clean_album = self._redactor.scrub_text(album_title.strip())
        clean_artist = self._redactor.scrub_text(artist_name.strip())
        clean_agent = self._redactor.scrub_text(recommended_by_agent.strip())
        clean_fb = self._redactor.scrub_text(user_feedback.strip())

        if not clean_album or not clean_artist:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await self._ensure_user_row(db, clean_uid)
            # Avoid duplicate consecutive rows for same user/album
            cursor = await db.execute(
                "SELECT id FROM listening_history WHERE user_id = ? AND lower(album_title) = lower(?) AND lower(artist_name) = lower(?)",
                (clean_uid, clean_album, clean_artist),
            )
            existing = await cursor.fetchone()
            if not existing:
                await db.execute(
                    "INSERT INTO listening_history (user_id, album_title, artist_name, recommended_by_agent, user_feedback) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (clean_uid, clean_album, clean_artist, clean_agent, clean_fb),
                )
                await db.commit()

    async def reset_user_profile(self, user_id: str) -> dict[str, int]:
        """Purge all preferences, playlist audits, and listening history for `user_id`."""
        await self.initialize()
        clean_uid = self._redactor.scrub_user_id(user_id)

        async with aiosqlite.connect(self.db_path) as db:
            c1 = await db.execute("DELETE FROM listening_history WHERE user_id = ?", (clean_uid,))
            c2 = await db.execute("DELETE FROM artist_preferences WHERE user_id = ?", (clean_uid,))
            c3 = await db.execute("DELETE FROM playlist_audits WHERE user_id = ?", (clean_uid,))
            c4 = await db.execute("DELETE FROM users WHERE user_id = ?", (clean_uid,))
            await db.commit()

        return {
            "deleted_history_rows": c1.rowcount or 0,
            "deleted_preference_rows": c2.rowcount or 0,
            "deleted_audit_rows": c3.rowcount or 0,
            "deleted_user_rows": c4.rowcount or 0,
        }


_MEMORY_SINGLETON: AsyncSQLiteMemory | None = None


def get_memory_store() -> AsyncSQLiteMemory:
    """Return active AsyncSQLiteMemory singleton."""
    global _MEMORY_SINGLETON
    if _MEMORY_SINGLETON is None:
        _MEMORY_SINGLETON = AsyncSQLiteMemory()
    return _MEMORY_SINGLETON


def set_memory_store(store: AsyncSQLiteMemory | None) -> None:
    """Override active AsyncSQLiteMemory (used in deterministic unit tests)."""
    global _MEMORY_SINGLETON
    _MEMORY_SINGLETON = store
