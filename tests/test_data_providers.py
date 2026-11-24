"""Unit tests for JsonDataProvider and mocked SpotifyDataProvider resilience."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests
from spotipy.exceptions import SpotifyException
from src.data_providers import (
    ArtistNotFoundError,
    BridgeNotFoundError,
    JsonDataProvider,
    PlaylistAuditInput,
    PlaylistNotAccessibleError,
    PlaylistValidationError,
    SpotifyDataProvider,
)


def test_json_provider_vault_coverage_and_filters() -> None:
    provider = JsonDataProvider()
    assert len(provider.vault.artists) >= 80
    assert len(provider.vault.bridges) >= 12

    miles_albums = provider.search_albums("Miles Davis", accessibility_level="beginner")
    assert any(a.title == "Kind of Blue" for a in miles_albums)

    solo_albums = provider.search_albums("Keith Jarrett", group_format="solo")
    assert len(solo_albums) >= 1
    assert all(a.group_format == "solo" for a in solo_albums)

    big_band_albums = provider.search_albums("Duke Ellington", group_format="big_band")
    assert len(big_band_albums) >= 1
    assert all(a.group_format == "big_band" for a in big_band_albums)


def test_json_provider_fuzzy_artist_and_lineage() -> None:
    provider = JsonDataProvider()
    with pytest.raises(ArtistNotFoundError) as exc_info:
        provider.search_albums("Myles Daviss")
    assert "Miles Davis" in exc_info.value.suggestions

    lineage = provider.get_lineage("Miles Davis", relationship="disciples")
    assert lineage.artist == "Miles Davis"
    assert len(lineage.disciples) >= 2
    assert lineage.influences == []
    assert lineage.collaborators == []


def test_json_provider_bridges_and_sample_playlist() -> None:
    provider = JsonDataProvider()
    bridges = provider.get_bridges("Claude Debussy", vibe="intimate")
    assert len(bridges) >= 1
    assert bridges[0].recommended_artist == "Bill Evans"

    with pytest.raises(BridgeNotFoundError):
        provider.get_bridges("nonexistent_mars_genre_xyz")

    report = provider.analyze_playlist("sample")
    assert 16 <= report.analyzed_track_count <= 20
    assert report.inferred_knowledge_level == "beginner"
    assert len(report.bridge_recommendations) >= 2

    # Valid Spotify URL with ?si= tracking parameter when offline falls back cleanly
    url_report = provider.analyze_playlist(
        "https://open.spotify.com/playlist/37i9dQZF1DXbITWG1ZJKYt?si=secretTrackingToken99"
    )
    assert "?si=" not in url_report.sanitized_playlist_ref
    assert url_report.data_source == "offline_sample_fallback"


def test_playlist_input_rejects_malformed_urls() -> None:
    provider = JsonDataProvider()
    with pytest.raises(PlaylistValidationError):
        provider.analyze_playlist("https://example.com/not-a-spotify-playlist")

    cleaned = PlaylistAuditInput(
        playlist_url_or_id="https://open.spotify.com/playlist/37i9dQZF1DXbITWG1ZJKYt?si=abc123&utm_source=copy"
    )
    assert cleaned.playlist_url_or_id == "https://open.spotify.com/playlist/37i9dQZF1DXbITWG1ZJKYt"
    assert cleaned.extract_playlist_id() == "37i9dQZF1DXbITWG1ZJKYt"


def test_spotify_provider_403_audio_features_heuristic_fallback() -> None:
    mock_sp = MagicMock()
    mock_sp.playlist.return_value = {"name": "Public Rock & Soul Mix"}
    mock_sp.playlist_items.return_value = {
        "items": [
            {
                "track": {
                    "id": "tr1",
                    "name": "Time",
                    "duration_ms": 413000,
                    "artists": [{"id": "ar1", "name": "Pink Floyd"}],
                }
            },
            {
                "track": {
                    "id": "tr2",
                    "name": "Superstition",
                    "duration_ms": 266000,
                    "artists": [{"id": "ar2", "name": "Stevie Wonder"}],
                }
            },
        ]
    }
    mock_sp.artists.return_value = {
        "artists": [
            {"id": "ar1", "genres": ["progressive rock", "psychedelic rock"]},
            {"id": "ar2", "genres": ["funk", "soul"]},
        ]
    }
    # Simulate post-Nov-2024 Spotify Development Mode 403 Forbidden on audio-features
    mock_sp.audio_features.side_effect = SpotifyException(
        http_status=403, code=-1, msg="Forbidden: audio-features restricted"
    )

    sp_provider = SpotifyDataProvider(
        client_id="dummy_id",
        client_secret="dummy_secret",
        spotify_client=mock_sp,
    )
    report = sp_provider.analyze_playlist("spotify:playlist:37i9dQZF1DXbITWG1ZJKYt")
    assert report.data_source == "spotify_live"
    assert report.audio_features_source == "genre_duration_heuristic_403_fallback"
    assert report.analyzed_track_count == 2
    assert report.estimated_tempo_bpm > 80.0
    assert report.inferred_knowledge_level == "beginner"


def test_spotify_provider_404_restricted_and_network_fallback() -> None:
    mock_sp = MagicMock()
    mock_sp.playlist.side_effect = SpotifyException(http_status=404, code=-1, msg="Not found")
    sp_provider = SpotifyDataProvider(
        client_id="dummy_id",
        client_secret="dummy_secret",
        spotify_client=mock_sp,
    )
    with pytest.raises(PlaylistNotAccessibleError):
        sp_provider.analyze_playlist("37i9dQZF1DXcBWIGoYBM5M")

    # Simulate transient network outage -> transparent fallback to JsonDataProvider
    mock_sp.playlist.side_effect = requests.ConnectionError("DNS offline")
    fallback_report = sp_provider.analyze_playlist("37i9dQZF1DXbITWG1ZJKYt")
    assert fallback_report.data_source == "offline_sample_fallback"


def test_spotify_provider_enriches_album_metadata() -> None:
    mock_sp = MagicMock()
    mock_sp.search.return_value = {
        "albums": {
            "items": [
                {
                    "external_urls": {"spotify": "https://open.spotify.com/album/1weenld61qoidwYuZ1GESA"},
                    "images": [{"url": "https://i.scdn.co/image/kind_of_blue.jpg"}],
                    "popularity": 79,
                }
            ]
        }
    }
    sp_provider = SpotifyDataProvider(
        client_id="dummy_id",
        client_secret="dummy_secret",
        spotify_client=mock_sp,
    )
    albums = sp_provider.search_albums("Miles Davis", accessibility_level="beginner", limit=1)
    assert len(albums) == 1
    assert albums[0].spotify_album_url == "https://open.spotify.com/album/1weenld61qoidwYuZ1GESA"
    assert albums[0].cover_image_url == "https://i.scdn.co/image/kind_of_blue.jpg"
    assert albums[0].popularity == 79
