"""Live Spotify Web API DataProvider (Client Credentials) with resilient offline fallback."""

from __future__ import annotations

from typing import Any

import requests
import spotipy
from pydantic import ValidationError
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials

from src.data_providers.base import (
    AlbumRecord,
    BridgeRecommendation,
    DataProvider,
    LineageResult,
    PlaylistAnalysisReport,
    PlaylistAuditInput,
    PlaylistNotAccessibleError,
    PlaylistValidationError,
)
from src.data_providers.json_provider import JsonDataProvider, estimate_track_tempo_and_energy
from src.telemetry.logger import get_structured_logger

logger = get_structured_logger("spotify_provider")


class SpotifyDataProvider(DataProvider):
    """Hybrid Spotify Web API provider using server-to-server Client Credentials.

    Enriches curated musicological vault results with live Spotify URLs, cover art,
    and popularity scores, audits public Spotify playlists, and transparently handles
    Spotify's `403 Forbidden` restriction on `audio-features` via genre/duration heuristics.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        fallback_provider: JsonDataProvider | None = None,
        spotify_client: Any | None = None,
    ) -> None:
        self.fallback = fallback_provider or JsonDataProvider()
        if spotify_client is not None:
            self.sp = spotify_client
        else:
            auth_manager = SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret,
            )
            self.sp = spotipy.Spotify(auth_manager=auth_manager, requests_timeout=10, retries=2)

    def search_albums(
        self,
        artist: str,
        group_format: str | None = None,
        era: str | None = None,
        accessibility_level: str | None = None,
        limit: int = 5,
    ) -> list[AlbumRecord]:
        base_records = self.fallback.search_albums(
            artist=artist,
            group_format=group_format,
            era=era,
            accessibility_level=accessibility_level,
            limit=limit,
        )
        enriched: list[AlbumRecord] = []
        for rec in base_records:
            try:
                query = f'album:"{rec.title}" artist:"{rec.artist}"'
                res = self.sp.search(q=query, type="album", limit=1)
                items = (res or {}).get("albums", {}).get("items", [])
                if items:
                    top = items[0]
                    ext_url = (top.get("external_urls") or {}).get("spotify") or rec.spotify_album_url
                    images = top.get("images") or []
                    cover = images[0].get("url") if images else rec.cover_image_url
                    rec = rec.model_copy(
                        update={
                            "spotify_album_url": ext_url,
                            "cover_image_url": cover,
                            "popularity": top.get("popularity"),
                        }
                    )
            except (SpotifyException, requests.RequestException, Exception) as exc:
                logger.warning(
                    "Spotify album enrichment fallback triggered",
                    extra={"album": rec.title, "error_type": type(exc).__name__},
                )
            enriched.append(rec)
        return enriched

    def get_lineage(self, artist: str, relationship: str = "all") -> LineageResult:
        return self.fallback.get_lineage(artist=artist, relationship=relationship)

    def get_bridges(self, anchor: str, vibe: str = "any") -> list[BridgeRecommendation]:
        return self.fallback.get_bridges(anchor=anchor, vibe=vibe)

    def analyze_playlist(self, playlist_url_or_id: str) -> PlaylistAnalysisReport:
        try:
            validated = PlaylistAuditInput(playlist_url_or_id=playlist_url_or_id)
        except ValidationError as exc:
            raise PlaylistValidationError(playlist_url_or_id, str(exc.errors()[0]["msg"])) from exc

        playlist_id = validated.extract_playlist_id()
        if playlist_id == "sample":
            return self.fallback.analyze_playlist("sample")

        try:
            meta = self.sp.playlist(playlist_id, fields="name,owner.display_name")
            playlist_title = (meta or {}).get("name") or f"Spotify Playlist ({playlist_id})"
            items_resp = self.sp.playlist_items(
                playlist_id,
                limit=50,
                additional_types=("track",),
            )
        except SpotifyException as exc:
            if exc.http_status in (400, 404):
                raise PlaylistNotAccessibleError(
                    playlist_ref=validated.playlist_url_or_id,
                    status_code=int(exc.http_status),
                    reason=(
                        f"Spotify returned HTTP {exc.http_status} for playlist '{playlist_id}'. "
                        "The playlist may be private or a Spotify-owned algorithmic/editorial "
                        "playlist restricted by the Spotify Web API."
                    ),
                ) from exc
            logger.warning(
                "Spotify API error during playlist fetch; falling back to offline sample",
                extra={"http_status": exc.http_status},
            )
            return self.fallback.analyze_playlist(validated.playlist_url_or_id)
        except requests.RequestException as exc:
            logger.warning(
                "Network outage contacting Spotify; falling back to offline catalog",
                extra={"error_type": type(exc).__name__},
            )
            return self.fallback.analyze_playlist(validated.playlist_url_or_id)

        raw_items = (items_resp or {}).get("items") or []
        parsed_tracks: list[dict[str, Any]] = []
        artist_ids: list[str] = []
        track_ids: list[str] = []

        for entry in raw_items:
            track = (entry or {}).get("track")
            if not track or not isinstance(track, dict):
                continue
            tid = track.get("id")
            if tid:
                track_ids.append(str(tid))
            artists = track.get("artists") or []
            primary_artist = artists[0] if artists else {}
            aid = primary_artist.get("id")
            if aid and aid not in artist_ids:
                artist_ids.append(str(aid))
            parsed_tracks.append(
                {
                    "track_id": tid,
                    "track_name": track.get("name") or "Unknown Track",
                    "artist_id": aid,
                    "artist": primary_artist.get("name") or "Unknown Artist",
                    "duration_ms": int(track.get("duration_ms") or 240000),
                }
            )

        if not parsed_tracks:
            raise PlaylistNotAccessibleError(
                playlist_ref=validated.playlist_url_or_id,
                status_code=404,
                reason="Playlist contains zero accessible tracks.",
            )

        # Batch fetch artist genres (up to 50 artist IDs per Spotify call)
        genres_by_artist_id: dict[str, list[str]] = {}
        for idx in range(0, len(artist_ids), 50):
            batch = artist_ids[idx : idx + 50]
            try:
                artists_resp = self.sp.artists(batch)
                for art_obj in (artists_resp or {}).get("artists") or []:
                    if art_obj and art_obj.get("id"):
                        genres_by_artist_id[art_obj["id"]] = art_obj.get("genres") or []
            except Exception as exc:
                logger.warning(
                    "Spotify artists genre lookup degraded",
                    extra={"error_type": type(exc).__name__},
                )

        # Attempt exact audio_features extraction with resilient 403 heuristic fallback
        features_by_track_id: dict[str, tuple[float, float]] = {}
        audio_features_source = "spotify_audio_features"
        if track_ids:
            try:
                af_list = self.sp.audio_features(track_ids[:50]) or []
                for feat in af_list:
                    if feat and feat.get("id") and feat.get("tempo") is not None:
                        features_by_track_id[feat["id"]] = (
                            float(feat["tempo"]),
                            float(feat.get("energy") or 0.55),
                        )
                if not features_by_track_id:
                    audio_features_source = "genre_duration_heuristic"
            except SpotifyException as exc:
                audio_features_source = "genre_duration_heuristic_403_fallback"
                logger.info(
                    "Spotify audio-features restricted (HTTP %s); engaging genre/duration heuristic",
                    exc.http_status,
                    extra={"http_status": exc.http_status},
                )
            except Exception:
                audio_features_source = "genre_duration_heuristic"

        normalized_tracks: list[dict[str, Any]] = []
        for tr in parsed_tracks:
            aid = tr.get("artist_id")
            tid = tr.get("track_id")
            tr_genres = genres_by_artist_id.get(aid, []) if aid else []
            if tid and tid in features_by_track_id:
                bpm, energy = features_by_track_id[tid]
            else:
                bpm, energy = estimate_track_tempo_and_energy(tr_genres, tr["duration_ms"])
            normalized_tracks.append(
                {
                    "track_name": tr["track_name"],
                    "artist": tr["artist"],
                    "duration_ms": tr["duration_ms"],
                    "genres": tr_genres,
                    "tempo_bpm": bpm,
                    "energy": energy,
                }
            )

        return self.fallback.build_report_from_tracks(
            playlist_id=playlist_id,
            sanitized_ref=validated.playlist_url_or_id,
            playlist_title=playlist_title,
            tracks=normalized_tracks,
            data_source="spotify_live",
            audio_features_source=audio_features_source,
        )
