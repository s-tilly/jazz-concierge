"""Application settings, .env loading, and Gemini 4 strategic model routing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from src.config.secrets import SecretManagerResolver

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VAULT_PATH = PROJECT_ROOT / "data" / "jazz_vault.json"
DEFAULT_SAMPLE_PLAYLIST_PATH = PROJECT_ROOT / "data" / "sample_playlist.json"


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().upper() in {"TRUE", "1", "YES", "ON"}


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings for jazz-concierge (`MilesAgent`)."""

    # Google Cloud / Vertex AI
    google_genai_use_vertexai: bool
    google_cloud_project: str
    google_cloud_location: str
    google_api_key: str
    use_secret_manager: bool

    # Strategic Gemini 4 Model Routing
    gemini_model_override: str
    profiler_model: str
    musicologist_model: str
    curator_model: str

    # Optional Spotify Web API
    spotify_api_key: str
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str

    # Persistence & Observability
    sqlite_db_path: str
    log_level: str
    otel_exporter_gcp_trace: bool

    # Catalog Paths
    jazz_vault_path: Path = DEFAULT_VAULT_PATH
    sample_playlist_path: Path = DEFAULT_SAMPLE_PLAYLIST_PATH

    @property
    def has_spotify_credentials(self) -> bool:
        """Return True when live Spotify Client Credentials or API key are configured."""
        if self.spotify_client_id and self.spotify_client_secret:
            return True
        if self.spotify_api_key and ":" in self.spotify_api_key:
            return True
        return False

    def get_spotify_client_pair(self) -> tuple[str, str] | None:
        """Extract (client_id, client_secret) from dedicated vars or `id:secret` SPOTIFY_API_KEY."""
        if self.spotify_client_id and self.spotify_client_secret:
            return self.spotify_client_id, self.spotify_client_secret
        if self.spotify_api_key and ":" in self.spotify_api_key:
            cid, csec = self.spotify_api_key.split(":", 1)
            if cid.strip() and csec.strip():
                return cid.strip(), csec.strip()
        return None


def load_settings(dotenv_path: Path | None = None) -> Settings:
    """Load Settings from `.env` (unless isolated in offline test mode) and Secret Manager."""
    offline_isolated = os.getenv("JAZZ_CONCIERGE_OFFLINE_TEST", "").strip() == "1"
    if not offline_isolated:
        target_env = dotenv_path or (PROJECT_ROOT / ".env")
        if target_env.exists():
            load_dotenv(dotenv_path=target_env, override=False)

    use_vertexai = _parse_bool(os.getenv("GOOGLE_GENAI_USE_VERTEXAI"), default=True)
    gcp_project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    gcp_location = os.getenv("GOOGLE_CLOUD_LOCATION", "").strip() or "global"
    use_sm = _parse_bool(os.getenv("USE_SECRET_MANAGER"), default=False)

    resolver = SecretManagerResolver(project_id=gcp_project, enabled=use_sm)

    google_api_key = resolver.resolve("GOOGLE_API_KEY", default="")
    spotify_api_key = resolver.resolve("SPOTIFY_API_KEY", default="")
    spotify_client_id = resolver.resolve("SPOTIFY_CLIENT_ID", default="")
    spotify_client_secret = resolver.resolve("SPOTIFY_CLIENT_SECRET", default="")
    spotify_redirect_uri = (
        os.getenv("SPOTIFY_REDIRECT_URI", "").strip() or "http://127.0.0.1:8501/callback"
    )

    # Strategic Gemini 4 model routing per spec.md Section 4:
    # Agent specific env var -> global GEMINI_MODEL override -> cognitive-tier default
    gemini_override = os.getenv("GEMINI_MODEL", "").strip()
    profiler_model = (
        os.getenv("PROFILER_MODEL", "").strip() or gemini_override or "gemini-4-hydrogen"
    )
    musicologist_model = (
        os.getenv("MUSICOLOGIST_MODEL", "").strip() or gemini_override or "gemini-4-neon"
    )
    curator_model = (
        os.getenv("CURATOR_MODEL", "").strip() or gemini_override or "gemini-4-carbon"
    )

    sqlite_db_path = os.getenv("SQLITE_DB_PATH", "").strip() or "src/memory/user_profile.db"
    log_level = (os.getenv("LOG_LEVEL", "").strip() or "INFO").upper()
    otel_gcp = _parse_bool(os.getenv("OTEL_EXPORTER_GCP_TRACE"), default=False)

    if not offline_isolated:
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE" if use_vertexai else "FALSE")
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", gcp_location)

    return Settings(
        google_genai_use_vertexai=use_vertexai,
        google_cloud_project=gcp_project,
        google_cloud_location=gcp_location,
        google_api_key=google_api_key,
        use_secret_manager=use_sm,
        gemini_model_override=gemini_override,
        profiler_model=profiler_model,
        musicologist_model=musicologist_model,
        curator_model=curator_model,
        spotify_api_key=spotify_api_key,
        spotify_client_id=spotify_client_id,
        spotify_client_secret=spotify_client_secret,
        spotify_redirect_uri=spotify_redirect_uri,
        sqlite_db_path=sqlite_db_path,
        log_level=log_level,
        otel_exporter_gcp_trace=otel_gcp,
    )


_SETTINGS_CACHE: Settings | None = None


def get_settings() -> Settings:
    """Return cached Settings singleton."""
    global _SETTINGS_CACHE
    if _SETTINGS_CACHE is None:
        _SETTINGS_CACHE = load_settings()
    return _SETTINGS_CACHE


def reload_settings(dotenv_path: Path | None = None) -> Settings:
    """Force reload of Settings (useful after env var changes in tests)."""
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = load_settings(dotenv_path=dotenv_path)
    return _SETTINGS_CACHE
