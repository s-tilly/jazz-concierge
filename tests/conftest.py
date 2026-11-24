"""Pytest configuration guaranteeing 100% offline isolation from local .env credentials."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Enforce offline isolation before test collection imports application settings
os.environ["JAZZ_CONCIERGE_OFFLINE_TEST"] = "1"

from src.config.settings import reload_settings  # noqa: E402
from src.data_providers import JsonDataProvider, set_data_provider  # noqa: E402
from src.memory import AsyncSQLiteMemory, set_memory_store  # noqa: E402

_ISOLATED_ENV_KEYS = (
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_API_KEY",
    "GEMINI_MODEL",
    "PROFILER_MODEL",
    "MUSICOLOGIST_MODEL",
    "CURATOR_MODEL",
    "SPOTIFY_API_KEY",
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "USE_SECRET_MANAGER",
    "OTEL_EXPORTER_GCP_TRACE",
)


@pytest.fixture(autouse=True)
def isolate_offline_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate every test from local `.env`, real cloud credentials, and shared SQLite files."""
    monkeypatch.setenv("JAZZ_CONCIERGE_OFFLINE_TEST", "1")
    for key in _ISOLATED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    temp_db = tmp_path / "test_user_profile.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(temp_db))

    reload_settings()
    set_data_provider(JsonDataProvider())
    set_memory_store(AsyncSQLiteMemory(db_path=temp_db))

    yield

    set_data_provider(None)
    set_memory_store(None)
