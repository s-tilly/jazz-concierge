"""Abstract DataProvider interface, domain exceptions, and strict Pydantic v2 models."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.telemetry.pii_redactor import get_pii_redactor

KnowledgeLevel = Literal["beginner", "novice", "intermediate", "connoisseur"]
GroupFormat = Literal["solo", "small_ensemble", "big_band"]
HarmonicProfile = Literal["roots_dixieland", "standard_swing", "modal_modern", "avant_garde"]
LineageRelationship = Literal["influences", "collaborators", "disciples", "all"]

_SPOTIFY_URL_RE = re.compile(
    r"^https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?playlist/([A-Za-z0-9]{22})(?:[/?].*)?$"
)
_SPOTIFY_URI_RE = re.compile(r"^spotify:playlist:([A-Za-z0-9]{22})$")
_SPOTIFY_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")
_SAMPLE_ALIASES = {"sample", "demo", "sample_playlist", "sample_eclectic_bridge_01"}


class DataProviderError(Exception):
    """Base domain exception for DataProvider errors."""


class ArtistNotFoundError(DataProviderError):
    """Raised when an artist cannot be matched in the catalog."""

    def __init__(self, artist: str, suggestions: list[str] | None = None) -> None:
        self.artist = artist
        self.suggestions = suggestions or []
        super().__init__(f"Artist '{artist}' not found in catalog.")


class BridgeNotFoundError(DataProviderError):
    """Raised when no stylistic bridge matches the requested anchor."""

    def __init__(self, anchor: str, available_anchors: list[str] | None = None) -> None:
        self.anchor = anchor
        self.available_anchors = available_anchors or []
        super().__init__(f"No genre/composer bridge found for '{anchor}'.")


class PlaylistValidationError(DataProviderError):
    """Raised when a Spotify playlist URL, URI, or ID is malformed."""

    def __init__(self, raw_input: str, reason: str) -> None:
        self.raw_input = raw_input
        self.reason = reason
        super().__init__(reason)


class PlaylistNotAccessibleError(DataProviderError):
    """Raised when Spotify restricts access (404/private/algorithmic playlist)."""

    def __init__(self, playlist_ref: str, status_code: int, reason: str) -> None:
        self.playlist_ref = playlist_ref
        self.status_code = status_code
        self.reason = reason
        super().__init__(reason)


class GuidedErrorResponse(BaseModel):
    """Structured error response guiding the LLM toward valid recovery actions."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["error"] = "error"
    error_code: str
    message: str
    recovery_instructions: str
    suggestions: list[str] = Field(default_factory=list)


class AlbumRecord(BaseModel):
    """Validated jazz album record returned by DataProviders."""

    model_config = ConfigDict(extra="forbid")

    artist: str
    title: str
    year: int
    decade: str
    label: str
    group_format: GroupFormat
    key_track: str
    personnel: list[str]
    accessibility_level: KnowledgeLevel
    why_listen: str
    styles: list[str] = Field(default_factory=list)
    spotify_album_url: str | None = None
    cover_image_url: str | None = None
    popularity: int | None = None


class LineageEntry(BaseModel):
    """Single genealogical connection between two jazz artists."""

    model_config = ConfigDict(extra="forbid")

    artist: str
    justification: str


class LineageResult(BaseModel):
    """Genealogical tree for a jazz musician."""

    model_config = ConfigDict(extra="forbid")

    artist: str
    era: str
    styles: list[str]
    relationship_filter: LineageRelationship
    influences: list[LineageEntry] = Field(default_factory=list)
    collaborators: list[LineageEntry] = Field(default_factory=list)
    disciples: list[LineageEntry] = Field(default_factory=list)


class BridgeRecommendation(BaseModel):
    """Stylistic bridge linking a non-jazz or historical anchor to a gateway jazz album."""

    model_config = ConfigDict(extra="forbid")

    anchor_matched: str
    target_subgenre: str
    recommended_artist: str
    recommended_album: str
    year: int
    group_format: GroupFormat
    accessibility_level: KnowledgeLevel
    bridge_rationale: str
    vibes: list[str] = Field(default_factory=list)


class PlaylistAuditInput(BaseModel):
    """Strict input validator for Spotify playlist URLs, URIs, 22-char IDs, or 'sample'."""

    model_config = ConfigDict(extra="forbid")

    playlist_url_or_id: str = Field(min_length=1, max_length=512)
    user_id: str | None = None

    @field_validator("playlist_url_or_id")
    @classmethod
    def validate_and_scrub_playlist_ref(cls, value: str) -> str:
        redactor = get_pii_redactor()
        cleaned = redactor.strip_url_tracking(value.strip())
        if cleaned.lower() in _SAMPLE_ALIASES:
            return "sample"
        if _SPOTIFY_URL_RE.match(cleaned):
            return cleaned.rstrip("/")
        if _SPOTIFY_URI_RE.match(cleaned):
            return cleaned
        if _SPOTIFY_ID_RE.match(cleaned):
            return cleaned
        raise ValueError(
            "Invalid Spotify playlist identifier. Provide a public Spotify playlist URL "
            "('https://open.spotify.com/playlist/<22-char-id>'), URI ('spotify:playlist:<id>'), "
            "22-character playlist ID, or 'sample' for the built-in eclectic playlist."
        )

    def extract_playlist_id(self) -> str:
        """Return 'sample' or the canonical 22-character Base62 Spotify playlist ID."""
        ref = self.playlist_url_or_id
        if ref == "sample":
            return "sample"
        for pattern in (_SPOTIFY_URL_RE, _SPOTIFY_URI_RE):
            match = pattern.match(ref)
            if match:
                return match.group(1)
        return ref


class PlaylistAnalysisReport(BaseModel):
    """Structured Sonic Fingerprint report generated from a playlist audit."""

    model_config = ConfigDict(extra="forbid")

    playlist_id: str
    sanitized_playlist_ref: str
    playlist_title: str
    analyzed_track_count: int
    top_artists: list[str]
    dominant_genres: list[str]
    estimated_tempo_bpm: float
    energy_profile: str
    tempo_profile: str
    jazz_track_ratio: float
    inferred_knowledge_level: KnowledgeLevel
    suggested_harmonic_preference: HarmonicProfile
    suggested_formation_preference: GroupFormat
    sonic_fingerprint_summary: str
    data_source: str
    audio_features_source: str
    bridge_recommendations: list[BridgeRecommendation]


# --- Static JSON Catalog Schemas (used by JsonDataProvider & validation script) ---


class VaultAlbumSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    year: int = Field(ge=1900, le=2030)
    decade: str
    label: str
    group_format: GroupFormat
    key_track: str
    personnel: list[str] = Field(min_length=1)
    accessibility_level: KnowledgeLevel
    why_listen: str
    spotify_album_url: str | None = None
    cover_image_url: str | None = None


class VaultLineageSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    influences: list[LineageEntry] = Field(min_length=1)
    collaborators: list[LineageEntry] = Field(min_length=1)
    disciples: list[LineageEntry] = Field(min_length=1)


class VaultArtistSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    era: str
    styles: list[str] = Field(min_length=1)
    instruments: list[str] = Field(min_length=1)
    harmonic_profile: HarmonicProfile
    recommended_levels: list[KnowledgeLevel] = Field(min_length=1)
    bio_summary: str
    spotify_artist_url: str | None = None
    albums: list[VaultAlbumSchema] = Field(min_length=1)
    lineage: VaultLineageSchema


class VaultBridgeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_tags: list[str] = Field(min_length=1)
    vibes: list[str] = Field(min_length=1)
    target_subgenre: str
    recommended_artist: str
    recommended_album: str
    year: int
    group_format: GroupFormat
    accessibility_level: KnowledgeLevel
    bridge_rationale: str


class JazzVaultSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    artists: list[VaultArtistSchema] = Field(min_length=80)
    bridges: list[VaultBridgeSchema] = Field(min_length=12)


class SamplePlaylistTrackSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_name: str
    artist: str
    album: str
    release_year: int
    duration_ms: int = Field(gt=0)
    genres: list[str] = Field(min_length=1)
    tempo_bpm: float = Field(gt=0)
    energy: float = Field(ge=0.0, le=1.0)


class SamplePlaylistSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: str
    playlist_name: str
    owner: str
    description: str
    tracks: list[SamplePlaylistTrackSchema] = Field(min_length=16, max_length=20)


class DataProvider(ABC):
    """Strategy interface for live Spotify or offline static JSON musicology queries."""

    @abstractmethod
    def search_albums(
        self,
        artist: str,
        group_format: str | None = None,
        era: str | None = None,
        accessibility_level: str | None = None,
        limit: int = 5,
    ) -> list[AlbumRecord]:
        """Search landmark albums for an artist with optional format, era, and tier filters."""

    @abstractmethod
    def get_lineage(self, artist: str, relationship: str = "all") -> LineageResult:
        """Retrieve genealogical influences, collaborators, and disciples for an artist."""

    @abstractmethod
    def get_bridges(self, anchor: str, vibe: str = "any") -> list[BridgeRecommendation]:
        """Map non-jazz genres or composers to gateway jazz album recommendations."""

    @abstractmethod
    def analyze_playlist(self, playlist_url_or_id: str) -> PlaylistAnalysisReport:
        """Audit a playlist and compute a structured Sonic Fingerprint."""
