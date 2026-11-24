#!/usr/bin/env python3
"""Offline catalog validator and optional Spotify metadata enricher for jazz_vault.json."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import DEFAULT_SAMPLE_PLAYLIST_PATH, DEFAULT_VAULT_PATH, get_settings  # noqa: E402
from src.data_providers.base import JazzVaultSchema, SamplePlaylistSchema  # noqa: E402

REQUIRED_LEVELS = {"beginner", "novice", "intermediate", "connoisseur"}
REQUIRED_FORMATS = {"solo", "small_ensemble", "big_band"}


def validate_catalogs(
    vault_path: Path = DEFAULT_VAULT_PATH,
    sample_playlist_path: Path = DEFAULT_SAMPLE_PLAYLIST_PATH,
) -> tuple[JazzVaultSchema, SamplePlaylistSchema]:
    """Validate jazz_vault.json and sample_playlist.json against strict Pydantic contracts."""
    if not vault_path.exists():
        raise FileNotFoundError(f"Missing catalog file: {vault_path}")
    if not sample_playlist_path.exists():
        raise FileNotFoundError(f"Missing sample playlist file: {sample_playlist_path}")

    raw_vault = json.loads(vault_path.read_text(encoding="utf-8"))
    vault = JazzVaultSchema.model_validate(raw_vault)

    raw_playlist = json.loads(sample_playlist_path.read_text(encoding="utf-8"))
    playlist = SamplePlaylistSchema.model_validate(raw_playlist)

    artist_ids = [a.id for a in vault.artists]
    artist_names = [a.name for a in vault.artists]
    if len(artist_ids) != len(set(artist_ids)):
        raise ValueError("Duplicate artist IDs detected in jazz_vault.json.")
    if len(artist_names) != len(set(artist_names)):
        raise ValueError("Duplicate artist names detected in jazz_vault.json.")
    if len(vault.artists) < 80:
        raise ValueError(f"Expected >= 80 artists, found {len(vault.artists)}.")
    if len(vault.bridges) < 12:
        raise ValueError(f"Expected >= 12 bridges, found {len(vault.bridges)}.")

    observed_artist_levels = {lvl for a in vault.artists for lvl in a.recommended_levels}
    observed_album_levels = {alb.accessibility_level for a in vault.artists for alb in a.albums}
    observed_formats = {alb.group_format for a in vault.artists for alb in a.albums}

    missing_levels = REQUIRED_LEVELS - observed_album_levels
    if missing_levels or (REQUIRED_LEVELS - observed_artist_levels):
        raise ValueError(f"Incomplete knowledge tier coverage; missing: {missing_levels}")

    missing_formats = REQUIRED_FORMATS - observed_formats
    if missing_formats:
        raise ValueError(f"Incomplete ensemble format coverage; missing: {missing_formats}")

    artists_by_name = {a.name: a for a in vault.artists}
    for bridge in vault.bridges:
        if bridge.recommended_artist not in artists_by_name:
            raise ValueError(
                f"Bridge target artist '{bridge.recommended_artist}' not present in vault."
            )
        artist_albums = {alb.title for alb in artists_by_name[bridge.recommended_artist].albums}
        if bridge.recommended_album not in artist_albums:
            raise ValueError(
                f"Bridge target album '{bridge.recommended_album}' missing under '{bridge.recommended_artist}'."
            )

    return vault, playlist


def enrich_vault_from_spotify(vault_path: Path = DEFAULT_VAULT_PATH) -> int:
    """Idempotently populate Spotify URLs and cover images when credentials exist in .env."""
    settings = get_settings()
    pair = settings.get_spotify_client_pair()
    if pair is None:
        sys.stdout.write(
            "Spotify credentials not set in .env; skipping live enrichment (offline catalog valid).\n"
        )
        return 0

    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials

    client_id, client_secret = pair
    sp = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(client_id=client_id, client_secret=client_secret),
        requests_timeout=10,
        retries=3,
    )

    raw_vault = json.loads(vault_path.read_text(encoding="utf-8"))
    updated_count = 0

    for artist in raw_vault.get("artists", []):
        if not artist.get("spotify_artist_url"):
            res = sp.search(q=f'artist:"{artist["name"]}"', type="artist", limit=1)
            items = (res or {}).get("artists", {}).get("items", [])
            if items:
                artist["spotify_artist_url"] = (items[0].get("external_urls") or {}).get("spotify")
                updated_count += 1
            time.sleep(0.08)

        for alb in artist.get("albums", []):
            if not alb.get("spotify_album_url") or not alb.get("cover_image_url"):
                q = f'album:"{alb["title"]}" artist:"{artist["name"]}"'
                res = sp.search(q=q, type="album", limit=1)
                items = (res or {}).get("albums", {}).get("items", [])
                if items:
                    top = items[0]
                    alb["spotify_album_url"] = (top.get("external_urls") or {}).get("spotify")
                    imgs = top.get("images") or []
                    if imgs:
                        alb["cover_image_url"] = imgs[0].get("url")
                    updated_count += 1
                time.sleep(0.08)

    # Re-validate before writing
    JazzVaultSchema.model_validate(raw_vault)
    vault_path.write_text(
        json.dumps(raw_vault, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return updated_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate or enrich data/jazz_vault.json and data/sample_playlist.json."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate static JSON catalogs 100%% offline without external API calls.",
    )
    group.add_argument(
        "--enrich",
        action="store_true",
        help="Validate and idempotently enrich jazz_vault.json via Spotify Client Credentials.",
    )
    args = parser.parse_args()

    vault, playlist = validate_catalogs()
    total_albums = sum(len(a.albums) for a in vault.artists)
    sys.stdout.write(
        f"[OK] Validated jazz_vault.json: {len(vault.artists)} distinct artists, "
        f"{total_albums} albums, {len(vault.bridges)} bridges (all 4 tiers & 3 formats covered).\n"
    )
    sys.stdout.write(
        f"[OK] Validated sample_playlist.json: {len(playlist.tracks)} tracks in '{playlist.playlist_name}'.\n"
    )

    if args.enrich:
        updates = enrich_vault_from_spotify()
        sys.stdout.write(f"[OK] Spotify enrichment completed ({updates} fields updated).\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
