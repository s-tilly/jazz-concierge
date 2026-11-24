"""DataProvider factory selecting SpotifyDataProvider when configured or JsonDataProvider."""

from __future__ import annotations

from src.config.settings import Settings, get_settings
from src.data_providers.base import (
    AlbumRecord,
    ArtistNotFoundError,
    BridgeNotFoundError,
    BridgeRecommendation,
    DataProvider,
    GuidedErrorResponse,
    LineageResult,
    PlaylistAnalysisReport,
    PlaylistAuditInput,
    PlaylistNotAccessibleError,
    PlaylistValidationError,
)
from src.data_providers.json_provider import JsonDataProvider
from src.data_providers.spotify_provider import SpotifyDataProvider
from src.telemetry.logger import get_structured_logger

logger = get_structured_logger("data_providers")

_PROVIDER_INSTANCE: DataProvider | None = None


def build_data_provider(settings: Settings | None = None) -> DataProvider:
    """Instantiate SpotifyDataProvider if credentials exist, otherwise JsonDataProvider."""
    cfg = settings or get_settings()
    json_fallback = JsonDataProvider(
        vault_path=cfg.jazz_vault_path,
        sample_playlist_path=cfg.sample_playlist_path,
    )
    pair = cfg.get_spotify_client_pair()
    if pair is not None:
        client_id, client_secret = pair
        try:
            return SpotifyDataProvider(
                client_id=client_id,
                client_secret=client_secret,
                fallback_provider=json_fallback,
            )
        except Exception as exc:
            logger.warning(
                "SpotifyDataProvider initialization failed; falling back to JsonDataProvider",
                extra={"error_type": type(exc).__name__},
            )
    return json_fallback


def get_data_provider(force_reload: bool = False) -> DataProvider:
    """Return active singleton DataProvider."""
    global _PROVIDER_INSTANCE
    if _PROVIDER_INSTANCE is None or force_reload:
        _PROVIDER_INSTANCE = build_data_provider()
    return _PROVIDER_INSTANCE


def set_data_provider(provider: DataProvider | None) -> None:
    """Override active DataProvider (used in deterministic tests)."""
    global _PROVIDER_INSTANCE
    _PROVIDER_INSTANCE = provider


__all__ = [
    "AlbumRecord",
    "ArtistNotFoundError",
    "BridgeNotFoundError",
    "BridgeRecommendation",
    "DataProvider",
    "GuidedErrorResponse",
    "JsonDataProvider",
    "LineageResult",
    "PlaylistAnalysisReport",
    "PlaylistAuditInput",
    "PlaylistNotAccessibleError",
    "PlaylistValidationError",
    "SpotifyDataProvider",
    "build_data_provider",
    "get_data_provider",
    "set_data_provider",
]
