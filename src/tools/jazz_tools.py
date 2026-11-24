"""Domain-specific ADK tools for jazz discovery, playlist auditing, lineage, and curation."""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from google.adk.tools import FunctionTool, ToolContext
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.data_providers import (
    ArtistNotFoundError,
    BridgeNotFoundError,
    GuidedErrorResponse,
    PlaylistAuditInput,
    PlaylistNotAccessibleError,
    PlaylistValidationError,
    get_data_provider,
)
from src.memory import get_memory_store
from src.telemetry import get_pii_redactor, get_tracer, log_tool_intent, log_tool_outcome
from src.telemetry.tracer import set_span_attributes

VALID_FORMATS = {"solo", "small_ensemble", "big_band", "any"}
VALID_LEVELS = {"beginner", "novice", "intermediate", "connoisseur", "any"}
VALID_RELATIONSHIPS = {"influences", "collaborators", "disciples", "all"}
VALID_HARMONIC = {"roots_dixieland", "standard_swing", "modal_modern", "avant_garde"}


# --- Strict Pydantic Tool Input Schemas ---


class DiscographySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artist: str = Field(min_length=1, max_length=120)
    group_format: str | None = None
    era: str | None = None
    accessibility_level: str | None = None
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("group_format")
    @classmethod
    def check_format(cls, val: str | None) -> str | None:
        if val is None or val.strip() == "":
            return None
        norm = val.strip().lower()
        if norm not in VALID_FORMATS:
            raise ValueError(
                f"Unsupported group_format '{val}'. Valid values: solo, small_ensemble, big_band."
            )
        return norm

    @field_validator("accessibility_level")
    @classmethod
    def check_level(cls, val: str | None) -> str | None:
        if val is None or val.strip() == "":
            return None
        norm = val.strip().lower()
        if norm not in VALID_LEVELS:
            raise ValueError(
                f"Unsupported accessibility_level '{val}'. Valid values: beginner, novice, intermediate, connoisseur."
            )
        return norm


class LineageQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artist: str = Field(min_length=1, max_length=120)
    relationship: str = "all"

    @field_validator("relationship")
    @classmethod
    def check_rel(cls, val: str) -> str:
        norm = val.strip().lower()
        if norm not in VALID_RELATIONSHIPS:
            raise ValueError(
                f"Unsupported relationship '{val}'. Valid values: influences, collaborators, disciples, all."
            )
        return norm


class SubgenreBridgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_genre_or_composer: str = Field(min_length=1, max_length=160)
    target_vibe: str = "any"


class TasteProfileSyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    knowledge_level: Literal["beginner", "novice", "intermediate", "connoisseur"] | None = None
    harmonic_preference: str | None = None
    formation_preference: str | None = None
    liked_artists_add: list[str] | None = None
    disliked_artists_add: list[str] | None = None

    @field_validator("formation_preference")
    @classmethod
    def check_formation(cls, val: str | None) -> str | None:
        if val is None or val.strip() == "":
            return None
        norm = val.strip().lower()
        if norm not in {"solo", "small_ensemble", "big_band"}:
            raise ValueError(
                f"Invalid formation_preference '{val}'. Choose solo, small_ensemble, or big_band."
            )
        return norm

    @field_validator("harmonic_preference")
    @classmethod
    def check_harmonic(cls, val: str | None) -> str | None:
        if val is None or val.strip() == "":
            return None
        norm = val.strip().lower()
        if norm not in VALID_HARMONIC:
            raise ValueError(
                f"Invalid harmonic_preference '{val}'. Choose roots_dixieland, standard_swing, modal_modern, or avant_garde."
            )
        return norm


class ProfileResetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=2, max_length=300)
    confirmed: bool = False


class ExportPlaylistInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    export_format: Literal["markdown", "json"] = "markdown"
    playlist_title: str | None = None


def _resolve_user_id(explicit_user_id: str | None, tool_context: ToolContext | None) -> str:
    if explicit_user_id and explicit_user_id.strip():
        return get_pii_redactor().scrub_user_id(explicit_user_id)
    if tool_context is not None:
        state_uid = tool_context.state.get("user_id")
        if state_uid:
            return get_pii_redactor().scrub_user_id(str(state_uid))
    return "default_listener"


def _append_candidate_albums(tool_context: ToolContext | None, albums: list[dict[str, Any]]) -> None:
    if tool_context is None or not albums:
        return
    existing: list[dict[str, Any]] = list(tool_context.state.get("candidate_albums") or [])
    seen = {(a.get("artist", "").lower(), a.get("title", "").lower()) for a in existing}
    for alb in albums:
        key = (str(alb.get("artist", "")).lower(), str(alb.get("title", "")).lower())
        if key not in seen and key[1]:
            seen.add(key)
            existing.append(alb)
    tool_context.state["candidate_albums"] = existing[-15:]


# --- 7 ADK Domain Tools ---


async def analyze_playlist(
    playlist_url_or_id: str,
    user_id: str | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Audit a public Spotify playlist URL, URI, ID, or 'sample' to compute a Sonic Fingerprint.

    Extracts artists, dominant genres, estimated tempo (BPM), and energy dynamics to
    automatically calibrate the listener's jazz knowledge tier (`beginner` to `connoisseur`),
    suggest harmonic and ensemble preferences, and generate stylistic bridges into jazz.
    Strips `?si=` tracking parameters before logging or SQLite persistence.

    Args:
        playlist_url_or_id: Public Spotify playlist URL (`https://open.spotify.com/playlist/<id>`),
            URI (`spotify:playlist:<id>`), 22-char playlist ID, or `'sample'` for the demo catalog.
        user_id: Unique listener identifier used to persist the Sonic Fingerprint in SQLite.

    Returns:
        Dictionary containing `status='success'` and `report` (`PlaylistAnalysisReport`),
        or `GuidedErrorResponse` with recovery instructions if malformed or restricted.
    """
    start = time.perf_counter()
    tracer = get_tracer("tools.analyze_playlist")
    effective_uid = _resolve_user_id(user_id, tool_context)
    planned = {"playlist_url_or_id": playlist_url_or_id, "user_id": effective_uid}
    log_tool_intent("analyze_playlist", planned, agent_name="JazzProfilerAgent")

    with tracer.start_as_current_span("tool.analyze_playlist") as span:
        try:
            validated = PlaylistAuditInput(
                playlist_url_or_id=playlist_url_or_id,
                user_id=effective_uid,
            )
            set_span_attributes(
                span,
                {"playlist.ref": validated.playlist_url_or_id, "user.id": effective_uid},
            )
            provider = get_data_provider()
            report = provider.analyze_playlist(validated.playlist_url_or_id)
            report_dict = report.model_dump()

            memory = get_memory_store()
            updated_profile = await memory.save_playlist_audit(effective_uid, report_dict)

            if tool_context is not None:
                tool_context.state["sonic_fingerprint"] = report_dict
                tool_context.state["taste_profile"] = updated_profile
                bridge_albums = [
                    {
                        "artist": b["recommended_artist"],
                        "title": b["recommended_album"],
                        "year": b["year"],
                        "group_format": b["group_format"],
                        "accessibility_level": b["accessibility_level"],
                        "why_listen": b["bridge_rationale"],
                    }
                    for b in report_dict.get("bridge_recommendations", [])
                ]
                _append_candidate_albums(tool_context, bridge_albums)

            elapsed = (time.perf_counter() - start) * 1000
            log_tool_outcome(
                "analyze_playlist",
                status="success",
                outcome_summary=(
                    f"Audited '{report.playlist_title}' ({report.analyzed_track_count} tracks, "
                    f"tier={report.inferred_knowledge_level})"
                ),
                duration_ms=elapsed,
                agent_name="JazzProfilerAgent",
            )
            return {"status": "success", "report": report_dict}

        except (ValidationError, PlaylistValidationError) as exc:
            msg = exc.reason if isinstance(exc, PlaylistValidationError) else str(exc.errors()[0]["msg"])
            err = GuidedErrorResponse(
                error_code="INVALID_PLAYLIST_REFERENCE",
                message=msg,
                recovery_instructions=(
                    "Ask the listener for a valid public Spotify playlist URL "
                    "('https://open.spotify.com/playlist/<22-char-id>') or invoke "
                    "analyze_playlist(playlist_url_or_id='sample') to run the eclectic demo audit."
                ),
                suggestions=["sample"],
            ).model_dump()
            log_tool_outcome(
                "analyze_playlist",
                status="error",
                outcome_summary=msg,
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzProfilerAgent",
            )
            return err

        except PlaylistNotAccessibleError as exc:
            err = GuidedErrorResponse(
                error_code="PLAYLIST_RESTRICTED_OR_PRIVATE",
                message=exc.reason,
                recovery_instructions=(
                    "Spotify restricts private playlists and Spotify-owned algorithmic playlists "
                    "(such as Discover Weekly or Today's Top Hits) via HTTP 404. Advise the user "
                    "to share a user-created public playlist URL, or call analyze_playlist('sample')."
                ),
                suggestions=["sample"],
            ).model_dump()
            log_tool_outcome(
                "analyze_playlist",
                status="error",
                outcome_summary=exc.reason,
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzProfilerAgent",
            )
            return err


def search_artist_discography(
    artist: str,
    group_format: str | None = None,
    era: str | None = None,
    accessibility_level: str | None = None,
    limit: int = 5,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Search landmark albums, key tracks, and personnel for a jazz artist with optional filters.

    Queries the active DataProvider (live Spotify metadata enrichment or offline curated vault)
    to retrieve historically vetted jazz albums matching ensemble size, decade, and listener tier.

    Args:
        artist: Name of the jazz musician or band (e.g., `'Miles Davis'`, `'Bill Evans'`, `'Sun Ra'`).
        group_format: Optional ensemble filter (`'solo'`, `'small_ensemble'`, `'big_band'`, or `'any'`).
        era: Optional decade or period substring (e.g., `'1950s'`, `'1960s'`, `'1970s'`).
        accessibility_level: Optional initiation tier (`'beginner'`, `'novice'`, `'intermediate'`, `'connoisseur'`).
        limit: Maximum number of albums to return (1 to 20, default 5).

    Returns:
        Dictionary with `status='success'`, `artist`, `count`, and `albums` list,
        or `GuidedErrorResponse` with fuzzy artist matches / valid enum hints on error.
    """
    start = time.perf_counter()
    tracer = get_tracer("tools.search_artist_discography")
    planned = {
        "artist": artist,
        "group_format": group_format,
        "era": era,
        "accessibility_level": accessibility_level,
        "limit": limit,
    }
    log_tool_intent("search_artist_discography", planned, agent_name="JazzMusicologistAgent")

    with tracer.start_as_current_span("tool.search_artist_discography") as span:
        try:
            validated = DiscographySearchInput(
                artist=artist,
                group_format=group_format,
                era=era,
                accessibility_level=accessibility_level,
                limit=limit,
            )
            set_span_attributes(span, {"jazz.artist": validated.artist, "jazz.limit": validated.limit})
            provider = get_data_provider()
            albums = provider.search_albums(
                artist=validated.artist,
                group_format=validated.group_format,
                era=validated.era,
                accessibility_level=validated.accessibility_level,
                limit=validated.limit,
            )
            serialized = [alb.model_dump() for alb in albums]
            _append_candidate_albums(tool_context, serialized)

            hint = ""
            if not serialized:
                hint = (
                    "Zero albums matched the strict filter combination; retry with "
                    "group_format=None or accessibility_level=None to inspect full artist catalog."
                )

            log_tool_outcome(
                "search_artist_discography",
                status="success",
                outcome_summary=f"Found {len(serialized)} album(s) for '{validated.artist}'",
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzMusicologistAgent",
            )
            return {
                "status": "success",
                "artist": serialized[0]["artist"] if serialized else validated.artist,
                "count": len(serialized),
                "albums": serialized,
                "filter_note": hint,
            }

        except ValidationError as exc:
            msg = str(exc.errors()[0]["msg"])
            err = GuidedErrorResponse(
                error_code="INVALID_DISCOGRAPHY_FILTER",
                message=msg,
                recovery_instructions=(
                    "Retry search_artist_discography using group_format in "
                    "['solo', 'small_ensemble', 'big_band'] and accessibility_level in "
                    "['beginner', 'novice', 'intermediate', 'connoisseur']."
                ),
                suggestions=["solo", "small_ensemble", "big_band"],
            ).model_dump()
            log_tool_outcome(
                "search_artist_discography",
                status="error",
                outcome_summary=msg,
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzMusicologistAgent",
            )
            return err

        except ArtistNotFoundError as exc:
            err = GuidedErrorResponse(
                error_code="ARTIST_NOT_FOUND",
                message=str(exc),
                recovery_instructions=(
                    f"Artist '{artist}' is not in the catalog. Retry search_artist_discography "
                    f"with one of these close catalog matches: {', '.join(exc.suggestions)}, "
                    "or use find_subgenre_bridges if the artist is a non-jazz reference."
                ),
                suggestions=exc.suggestions,
            ).model_dump()
            log_tool_outcome(
                "search_artist_discography",
                status="error",
                outcome_summary=str(exc),
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzMusicologistAgent",
            )
            return err


def trace_artist_lineage(
    artist: str,
    relationship: Literal["influences", "collaborators", "disciples", "all"] = "all",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Explore a jazz musician's genealogical tree of influences, collaborators, and disciples.

    Provides musicological justifications linking eras—for example tracing Charlie Parker ->
    Miles Davis -> Herbie Hancock -> Robert Glasper.

    Args:
        artist: Jazz artist name to trace (e.g., `'Miles Davis'`, `'John Coltrane'`, `'Duke Ellington'`).
        relationship: Lineage branch to inspect (`'influences'`, `'collaborators'`, `'disciples'`, or `'all'`).

    Returns:
        Dictionary with `status='success'` and `lineage` (`LineageResult`),
        or `GuidedErrorResponse` with close artist suggestions when unrecognized.
    """
    start = time.perf_counter()
    tracer = get_tracer("tools.trace_artist_lineage")
    planned = {"artist": artist, "relationship": relationship}
    log_tool_intent("trace_artist_lineage", planned, agent_name="JazzMusicologistAgent")

    with tracer.start_as_current_span("tool.trace_artist_lineage") as span:
        try:
            validated = LineageQueryInput(artist=artist, relationship=relationship)
            set_span_attributes(
                span,
                {"jazz.artist": validated.artist, "jazz.relationship": validated.relationship},
            )
            provider = get_data_provider()
            lineage = provider.get_lineage(
                artist=validated.artist,
                relationship=validated.relationship,
            )
            payload = lineage.model_dump()
            if tool_context is not None:
                tool_context.state["last_lineage"] = payload

            log_tool_outcome(
                "trace_artist_lineage",
                status="success",
                outcome_summary=f"Traced '{validated.relationship}' lineage for '{lineage.artist}'",
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzMusicologistAgent",
            )
            return {"status": "success", "lineage": payload}

        except ValidationError as exc:
            msg = str(exc.errors()[0]["msg"])
            err = GuidedErrorResponse(
                error_code="INVALID_LINEAGE_RELATIONSHIP",
                message=msg,
                recovery_instructions=(
                    "Use relationship='all', 'influences', 'collaborators', or 'disciples'."
                ),
                suggestions=["all", "influences", "collaborators", "disciples"],
            ).model_dump()
            log_tool_outcome(
                "trace_artist_lineage",
                status="error",
                outcome_summary=msg,
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzMusicologistAgent",
            )
            return err

        except ArtistNotFoundError as exc:
            err = GuidedErrorResponse(
                error_code="ARTIST_NOT_FOUND",
                message=str(exc),
                recovery_instructions=(
                    f"Could not find '{artist}'. Retry trace_artist_lineage with one of: "
                    f"{', '.join(exc.suggestions)}."
                ),
                suggestions=exc.suggestions,
            ).model_dump()
            log_tool_outcome(
                "trace_artist_lineage",
                status="error",
                outcome_summary=str(exc),
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzMusicologistAgent",
            )
            return err


def find_subgenre_bridges(
    anchor_genre_or_composer: str,
    target_vibe: str = "any",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Map non-jazz genres, artists, or classical composers to accessible gateway jazz albums.

    Ideal for beginners and cross-genre explorers who mention touchstones such as
    `'Progressive Rock'`, `'Pink Floyd'`, `'Funk'`, `'Soul'`, `'Claude Debussy'`,
    `'Pierre Boulez'`, `'Hip-Hop'`, `'Ambient'`, `'Blues'`, `'Film Scores'`, or `'Dixieland'`.

    Args:
        anchor_genre_or_composer: Non-jazz genre, band, or classical composer anchor.
        target_vibe: Desired mood or energy (e.g., `'meditative'`, `'energetic'`, `'warm'`, `'any'`).

    Returns:
        Dictionary with `status='success'` and `bridges` (`BridgeRecommendation` list),
        or `GuidedErrorResponse` listing supported anchor categories on mismatch.
    """
    start = time.perf_counter()
    tracer = get_tracer("tools.find_subgenre_bridges")
    planned = {"anchor_genre_or_composer": anchor_genre_or_composer, "target_vibe": target_vibe}
    log_tool_intent("find_subgenre_bridges", planned, agent_name="JazzMusicologistAgent")

    with tracer.start_as_current_span("tool.find_subgenre_bridges") as span:
        try:
            validated = SubgenreBridgeInput(
                anchor_genre_or_composer=anchor_genre_or_composer,
                target_vibe=target_vibe,
            )
            set_span_attributes(
                span,
                {
                    "jazz.anchor": validated.anchor_genre_or_composer,
                    "jazz.vibe": validated.target_vibe,
                },
            )
            provider = get_data_provider()
            bridges = provider.get_bridges(
                anchor=validated.anchor_genre_or_composer,
                vibe=validated.target_vibe,
            )
            serialized = [b.model_dump() for b in bridges]
            _append_candidate_albums(
                tool_context,
                [
                    {
                        "artist": b["recommended_artist"],
                        "title": b["recommended_album"],
                        "year": b["year"],
                        "group_format": b["group_format"],
                        "accessibility_level": b["accessibility_level"],
                        "why_listen": b["bridge_rationale"],
                    }
                    for b in serialized
                ],
            )
            log_tool_outcome(
                "find_subgenre_bridges",
                status="success",
                outcome_summary=(
                    f"Matched {len(serialized)} bridge(s) for '{validated.anchor_genre_or_composer}'"
                ),
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzMusicologistAgent",
            )
            return {
                "status": "success",
                "anchor": validated.anchor_genre_or_composer,
                "count": len(serialized),
                "bridges": serialized,
            }

        except (ValidationError, BridgeNotFoundError) as exc:
            suggestions = (
                exc.available_anchors
                if isinstance(exc, BridgeNotFoundError)
                else ["progressive rock", "soul", "funk", "classical", "hip-hop", "ambient", "blues"]
            )
            msg = str(exc) if isinstance(exc, BridgeNotFoundError) else str(exc.errors()[0]["msg"])
            err = GuidedErrorResponse(
                error_code="BRIDGE_ANCHOR_NOT_FOUND",
                message=msg,
                recovery_instructions=(
                    "Map the user's taste to one of the supported stylistic bridge anchors and "
                    f"retry find_subgenre_bridges: {', '.join(suggestions[:10])}."
                ),
                suggestions=suggestions[:10],
            ).model_dump()
            log_tool_outcome(
                "find_subgenre_bridges",
                status="error",
                outcome_summary=msg,
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzMusicologistAgent",
            )
            return err


async def sync_user_taste_profile(
    user_id: str,
    knowledge_level: Literal["beginner", "novice", "intermediate", "connoisseur"] | None = None,
    harmonic_preference: str | None = None,
    formation_preference: str | None = None,
    liked_artists_add: list[str] | None = None,
    disliked_artists_add: list[str] | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Persist and synchronize the listener's PII-scrubbed jazz taste profile in SQLite.

    Atomically updates initiation tier, harmonic preference, ensemble format preference,
    and liked/disliked artists in `src/memory/user_profile.db` and `tool_context.state`.

    Args:
        user_id: Listener identifier (automatically scrubbed of PII before storage).
        knowledge_level: Calibrated jazz tier (`'beginner'`, `'novice'`, `'intermediate'`, `'connoisseur'`).
        harmonic_preference: Preferred harmonic language (`'roots_dixieland'`, `'standard_swing'`, `'modal_modern'`, `'avant_garde'`).
        formation_preference: Preferred ensemble texture (`'solo'`, `'small_ensemble'`, `'big_band'`).
        liked_artists_add: Optional list of artists appreciated by the user.
        disliked_artists_add: Optional list of artists to avoid in recommendations.

    Returns:
        Dictionary with `status='success'` and the synchronized `taste_profile`,
        or `GuidedErrorResponse` if an enum value is invalid.
    """
    start = time.perf_counter()
    tracer = get_tracer("tools.sync_user_taste_profile")
    effective_uid = _resolve_user_id(user_id, tool_context)
    planned = {
        "user_id": effective_uid,
        "knowledge_level": knowledge_level,
        "harmonic_preference": harmonic_preference,
        "formation_preference": formation_preference,
        "liked_artists_add": liked_artists_add,
        "disliked_artists_add": disliked_artists_add,
    }
    log_tool_intent("sync_user_taste_profile", planned, agent_name="JazzProfilerAgent")

    with tracer.start_as_current_span("tool.sync_user_taste_profile") as span:
        try:
            validated = TasteProfileSyncInput(
                user_id=effective_uid,
                knowledge_level=knowledge_level,
                harmonic_preference=harmonic_preference,
                formation_preference=formation_preference,
                liked_artists_add=liked_artists_add,
                disliked_artists_add=disliked_artists_add,
            )
            set_span_attributes(
                span,
                {
                    "user.id": validated.user_id,
                    "profile.level": validated.knowledge_level or "unchanged",
                },
            )
            memory = get_memory_store()
            profile = await memory.update_taste_profile(
                user_id=validated.user_id,
                knowledge_level=validated.knowledge_level,
                harmonic_preference=validated.harmonic_preference,
                formation_preference=validated.formation_preference,
                liked_artists_add=validated.liked_artists_add,
                disliked_artists_add=validated.disliked_artists_add,
            )
            if tool_context is not None:
                tool_context.state["taste_profile"] = profile
                tool_context.state["listening_history"] = profile.get("listening_history", [])

            log_tool_outcome(
                "sync_user_taste_profile",
                status="success",
                outcome_summary=(
                    f"Synced profile for '{validated.user_id}' "
                    f"(tier={profile['knowledge_level']}, format={profile['formation_preference']})"
                ),
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzProfilerAgent",
            )
            return {"status": "success", "taste_profile": profile}

        except ValidationError as exc:
            msg = str(exc.errors()[0]["msg"])
            err = GuidedErrorResponse(
                error_code="INVALID_TASTE_PROFILE_FIELD",
                message=msg,
                recovery_instructions=(
                    "Ensure knowledge_level is one of ['beginner', 'novice', 'intermediate', 'connoisseur'], "
                    "harmonic_preference is one of ['roots_dixieland', 'standard_swing', 'modal_modern', 'avant_garde'], "
                    "and formation_preference is one of ['solo', 'small_ensemble', 'big_band']."
                ),
                suggestions=["beginner", "novice", "intermediate", "connoisseur"],
            ).model_dump()
            log_tool_outcome(
                "sync_user_taste_profile",
                status="error",
                outcome_summary=msg,
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzProfilerAgent",
            )
            return err


async def reset_user_taste_profile(
    user_id: str,
    reason: str,
    confirmed: bool = False,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Purge persistent user taste preferences, Sonic Fingerprint, and listening history (HITL-gated).

    Destructive action protected by both ADK `FunctionTool(..., require_confirmation=True)` and
    explicit human confirmation verification. Never wipes SQLite records without approval.

    Args:
        user_id: Identifier of the listener whose profile is being reset.
        reason: Brief explanation of why the reset was requested.
        confirmed: Must be True (or approved via ADK ToolConfirmation) to execute deletion.

    Returns:
        Dictionary confirming deletion counts once approved, or `GuidedErrorResponse` /
        `confirmation_required` payload when awaiting explicit human confirmation.
    """
    start = time.perf_counter()
    tracer = get_tracer("tools.reset_user_taste_profile")
    effective_uid = _resolve_user_id(user_id, tool_context)
    planned = {"user_id": effective_uid, "reason": reason, "confirmed": confirmed}
    log_tool_intent("reset_user_taste_profile", planned, agent_name="JazzProfilerAgent")

    with tracer.start_as_current_span("tool.reset_user_taste_profile") as span:
        try:
            validated = ProfileResetInput(
                user_id=effective_uid,
                reason=reason,
                confirmed=confirmed,
            )
            adk_confirmed = bool(
                tool_context is not None
                and getattr(tool_context, "tool_confirmation", None) is not None
                and tool_context.tool_confirmation.confirmed
            )
            is_approved = validated.confirmed or adk_confirmed
            set_span_attributes(
                span,
                {"user.id": validated.user_id, "hitl.approved": is_approved},
            )

            if not is_approved:
                if tool_context is not None:
                    tool_context.state["pending_hitl_confirmation"] = {
                        "action": "reset_user_taste_profile",
                        "user_id": validated.user_id,
                        "reason": get_pii_redactor().scrub_text(validated.reason),
                    }
                log_tool_outcome(
                    "reset_user_taste_profile",
                    status="confirmation_required",
                    outcome_summary=f"Paused profile reset for '{validated.user_id}' pending HITL approval",
                    duration_ms=(time.perf_counter() - start) * 1000,
                    agent_name="JazzProfilerAgent",
                )
                return {
                    "status": "confirmation_required",
                    "error_code": "HITL_CONFIRMATION_REQUIRED",
                    "message": (
                        f"Wiping taste profile and listening history for '{validated.user_id}' "
                        "is permanent and requires explicit human confirmation."
                    ),
                    "recovery_instructions": (
                        "Ask the listener clearly to confirm profile deletion before invoking "
                        "reset_user_taste_profile with confirmed=True."
                    ),
                }

            memory = get_memory_store()
            deleted_counts = await memory.reset_user_profile(validated.user_id)
            fresh_profile = await memory.get_user_profile(validated.user_id)

            if tool_context is not None:
                tool_context.state["pending_hitl_confirmation"] = None
                tool_context.state["taste_profile"] = fresh_profile
                tool_context.state["sonic_fingerprint"] = {}
                tool_context.state["listening_history"] = []
                tool_context.state["candidate_albums"] = []

            log_tool_outcome(
                "reset_user_taste_profile",
                status="success",
                outcome_summary=f"Purged persistent profile for '{validated.user_id}'",
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzProfilerAgent",
                extra_metadata=deleted_counts,
            )
            return {
                "status": "success",
                "user_id": validated.user_id,
                "deleted": deleted_counts,
                "taste_profile": fresh_profile,
            }

        except ValidationError as exc:
            msg = str(exc.errors()[0]["msg"])
            return GuidedErrorResponse(
                error_code="INVALID_RESET_REQUEST",
                message=msg,
                recovery_instructions="Provide non-empty user_id and reason (at least 2 characters).",
            ).model_dump()


async def export_curated_playlist(
    user_id: str,
    session_id: str,
    export_format: Literal["markdown", "json"] = "markdown",
    playlist_title: str | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Generate a shareable Markdown listening guide or JSON playlist manifest and log history.

    Consolidates curated session candidate albums, records them into `listening_history` in
    SQLite so subsequent turns avoid duplicate recommendations, and formats the export.

    Args:
        user_id: Listener identifier.
        session_id: Active conversation session identifier.
        export_format: Output format (`'markdown'` or `'json'`).
        playlist_title: Optional custom title for the curated listening session.

    Returns:
        Dictionary with `status='success'`, `export_format`, `item_count`, and `content`,
        or `GuidedErrorResponse` if `export_format` is invalid.
    """
    start = time.perf_counter()
    tracer = get_tracer("tools.export_curated_playlist")
    effective_uid = _resolve_user_id(user_id, tool_context)
    planned = {
        "user_id": effective_uid,
        "session_id": session_id,
        "export_format": export_format,
        "playlist_title": playlist_title,
    }
    log_tool_intent("export_curated_playlist", planned, agent_name="JazzCuratorAgent")

    with tracer.start_as_current_span("tool.export_curated_playlist") as span:
        try:
            validated = ExportPlaylistInput(
                user_id=effective_uid,
                session_id=session_id,
                export_format=export_format,
                playlist_title=playlist_title,
            )
            set_span_attributes(
                span,
                {"user.id": validated.user_id, "export.format": validated.export_format},
            )
            memory = get_memory_store()
            profile = await memory.get_user_profile(validated.user_id)
            already_heard = {
                (h["artist_name"].lower(), h["album_title"].lower())
                for h in profile.get("listening_history", [])
            }

            candidates: list[dict[str, Any]] = []
            if tool_context is not None:
                candidates = list(tool_context.state.get("candidate_albums") or [])

            # Filter out duplicates already in listening_history if fresh candidates exist
            fresh = [
                c
                for c in candidates
                if (str(c.get("artist", "")).lower(), str(c.get("title", "")).lower())
                not in already_heard
            ]
            selected = (fresh or candidates)[:5]

            if not selected:
                # Fallback starter curation calibrated to user's stored knowledge_level
                provider = get_data_provider()
                tier = profile.get("knowledge_level", "beginner")
                seed_artist = {
                    "beginner": "Miles Davis",
                    "novice": "Bill Evans",
                    "intermediate": "Wayne Shorter",
                    "connoisseur": "Eric Dolphy",
                }.get(tier, "Miles Davis")
                albums = provider.search_albums(artist=seed_artist, limit=3)
                selected = [a.model_dump() for a in albums]

            for item in selected:
                await memory.record_listening_history(
                    user_id=validated.user_id,
                    album_title=str(item.get("title", "")),
                    artist_name=str(item.get("artist", "")),
                    recommended_by_agent="JazzCuratorAgent",
                )

            if tool_context is not None:
                refreshed = await memory.get_user_profile(validated.user_id)
                tool_context.state["listening_history"] = refreshed.get("listening_history", [])

            title = validated.playlist_title or (
                f"MilesAgent Curated Session ({profile.get('knowledge_level', 'beginner').title()} Tier)"
            )

            if validated.export_format == "json":
                manifest = {
                    "playlist_title": title,
                    "user_id": validated.user_id,
                    "session_id": validated.session_id,
                    "knowledge_level": profile.get("knowledge_level", "beginner"),
                    "recommendations": [
                        {
                            "artist": x.get("artist"),
                            "album": x.get("title"),
                            "year": x.get("year"),
                            "key_track": x.get("key_track"),
                            "group_format": x.get("group_format"),
                            "accessibility_level": x.get("accessibility_level"),
                            "listening_note": x.get("why_listen"),
                            "spotify_album_url": x.get("spotify_album_url"),
                        }
                        for x in selected
                    ],
                }
                rendered = json.dumps(manifest, indent=2, ensure_ascii=False)
            else:
                lines = [
                    f"# 🎷 {title}",
                    f"*Curated for `{validated.user_id}` | Initiation Tier: **{profile.get('knowledge_level', 'beginner')}***",
                    "",
                ]
                for idx, x in enumerate(selected, 1):
                    key_track = f" — Key Track: *{x.get('key_track')}*" if x.get("key_track") else ""
                    fmt_badge = x.get("group_format", "small_ensemble")
                    year_str = f" ({x.get('year')})" if x.get("year") else ""
                    lines.append(
                        f"{idx}. **{x.get('artist')} — *{x.get('title')}***{year_str} [`{fmt_badge}`]{key_track}"
                    )
                    if x.get("why_listen"):
                        lines.append(f"   - {x.get('why_listen')}")
                    if x.get("spotify_album_url"):
                        lines.append(f"   - Listen: {x.get('spotify_album_url')}")
                rendered = "\n".join(lines)

            if tool_context is not None:
                tool_context.state["last_exported_playlist"] = rendered

            log_tool_outcome(
                "export_curated_playlist",
                status="success",
                outcome_summary=f"Exported {len(selected)} album(s) as {validated.export_format}",
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzCuratorAgent",
            )
            return {
                "status": "success",
                "export_format": validated.export_format,
                "playlist_title": title,
                "item_count": len(selected),
                "content": rendered,
            }

        except ValidationError as exc:
            msg = str(exc.errors()[0]["msg"])
            err = GuidedErrorResponse(
                error_code="INVALID_EXPORT_FORMAT",
                message=msg,
                recovery_instructions="Pass export_format='markdown' or export_format='json'.",
                suggestions=["markdown", "json"],
            ).model_dump()
            log_tool_outcome(
                "export_curated_playlist",
                status="error",
                outcome_summary=msg,
                duration_ms=(time.perf_counter() - start) * 1000,
                agent_name="JazzCuratorAgent",
            )
            return err


# --- ADK FunctionTool Wrappers ---

analyze_playlist_tool = FunctionTool(func=analyze_playlist)
search_artist_discography_tool = FunctionTool(func=search_artist_discography)
trace_artist_lineage_tool = FunctionTool(func=trace_artist_lineage)
find_subgenre_bridges_tool = FunctionTool(func=find_subgenre_bridges)
sync_user_taste_profile_tool = FunctionTool(func=sync_user_taste_profile)
reset_user_taste_profile_tool = FunctionTool(
    func=reset_user_taste_profile,
    require_confirmation=True,
)
export_curated_playlist_tool = FunctionTool(func=export_curated_playlist)

PROFILER_TOOLS = [
    analyze_playlist_tool,
    sync_user_taste_profile_tool,
    reset_user_taste_profile_tool,
]

MUSICOLOGIST_TOOLS = [
    search_artist_discography_tool,
    trace_artist_lineage_tool,
    find_subgenre_bridges_tool,
]

CURATOR_TOOLS = [
    export_curated_playlist_tool,
    sync_user_taste_profile_tool,
]

ALL_JAZZ_TOOLS = [
    analyze_playlist_tool,
    search_artist_discography_tool,
    trace_artist_lineage_tool,
    find_subgenre_bridges_tool,
    sync_user_taste_profile_tool,
    reset_user_taste_profile_tool,
    export_curated_playlist_tool,
]
