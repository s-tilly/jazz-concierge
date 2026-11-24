"""Offline DataProvider backed by data/jazz_vault.json and data/sample_playlist.json."""

from __future__ import annotations

import difflib
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.config.settings import DEFAULT_SAMPLE_PLAYLIST_PATH, DEFAULT_VAULT_PATH
from src.data_providers.base import (
    AlbumRecord,
    ArtistNotFoundError,
    BridgeNotFoundError,
    BridgeRecommendation,
    DataProvider,
    GroupFormat,
    HarmonicProfile,
    JazzVaultSchema,
    KnowledgeLevel,
    LineageResult,
    PlaylistAnalysisReport,
    PlaylistAuditInput,
    PlaylistValidationError,
    SamplePlaylistSchema,
    VaultArtistSchema,
)


def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return " ".join(ascii_only.lower().strip().split())


_GENRE_BPM_HEURISTIC: list[tuple[str, tuple[float, float]]] = [
    ("classical", (68.0, 0.22)),
    ("impressionism", (66.0, 0.18)),
    ("ambient", (74.0, 0.20)),
    ("ballad", (72.0, 0.28)),
    ("soul", (96.0, 0.56)),
    ("r&b", (94.0, 0.54)),
    ("funk", (105.0, 0.78)),
    ("hip hop", (90.0, 0.60)),
    ("hip-hop", (90.0, 0.60)),
    ("progressive rock", (124.0, 0.68)),
    ("rock", (118.0, 0.66)),
    ("bebop", (168.0, 0.74)),
    ("free jazz", (142.0, 0.82)),
    ("hard bop", (128.0, 0.65)),
    ("cool jazz", (98.0, 0.38)),
    ("swing", (132.0, 0.60)),
    ("jazz", (114.0, 0.50)),
    ("electronic", (122.0, 0.70)),
    ("pop", (116.0, 0.64)),
]


def estimate_track_tempo_and_energy(genres: list[str], duration_ms: int = 240000) -> tuple[float, float]:
    """Resilient genre-and-duration heuristic used when Spotify restricts `audio-features` (403)."""
    joined = " | ".join(g.lower() for g in genres)
    base_bpm, base_energy = 108.0, 0.55
    for keyword, (bpm, energy) in _GENRE_BPM_HEURISTIC:
        if keyword in joined:
            base_bpm, base_energy = bpm, energy
            break
    # Very long tracks (> 7 min) tend toward expansive suites or meditative dynamics
    if duration_ms > 420000:
        base_bpm = max(60.0, base_bpm - 6.0)
    elif duration_ms < 160000:
        base_bpm = min(175.0, base_bpm + 6.0)
    return round(base_bpm, 1), round(base_energy, 2)


def _classify_jazz_tier(genres_counter: Counter[str], total_tracks: int) -> tuple[float, KnowledgeLevel]:
    if total_tracks <= 0:
        return 0.0, "beginner"

    jazz_mentions = 0
    connoisseur_hits = 0
    intermediate_hits = 0
    novice_hits = 0

    for genre, count in genres_counter.items():
        g = genre.lower()
        if any(k in g for k in ("free jazz", "avant-garde jazz", "spiritual jazz", "ecm")):
            jazz_mentions += count
            connoisseur_hits += count
        elif any(k in g for k in ("bebop", "hard bop", "post-bop", "modal jazz", "jazz fusion")):
            jazz_mentions += count
            intermediate_hits += count
        elif any(k in g for k in ("vocal jazz", "cool jazz", "bossa nova", "swing", "jazz")):
            jazz_mentions += count
            novice_hits += count

    # Approximate track ratio capped at 1.0
    ratio = min(1.0, round(jazz_mentions / max(1, total_tracks * 2), 2))
    if jazz_mentions == 0 or ratio < 0.08:
        return ratio, "beginner"
    if connoisseur_hits >= 2 or (connoisseur_hits >= 1 and ratio >= 0.35):
        return ratio, "connoisseur"
    if intermediate_hits >= 2 or ratio >= 0.35:
        return ratio, "intermediate"
    if novice_hits >= 1:
        return ratio, "novice"
    return ratio, "beginner"


def _infer_harmonic_and_formation(
    dominant_genres: list[str], avg_energy: float
) -> tuple[HarmonicProfile, GroupFormat]:
    joined = " ".join(g.lower() for g in dominant_genres)
    if any(k in joined for k in ("avant-garde", "serialism", "free jazz", "contemporary classical")):
        harmonic: HarmonicProfile = "avant_garde"
    elif any(k in joined for k in ("progressive", "impressionism", "electronic", "modal", "neo soul", "funk")):
        harmonic = "modal_modern"
    elif any(k in joined for k in ("dixieland", "ragtime", "new orleans", "traditional")):
        harmonic = "roots_dixieland"
    else:
        harmonic = "standard_swing"

    if any(k in joined for k in ("solo piano", "solo guitar", "minimalism")) and avg_energy < 0.30:
        formation: GroupFormat = "solo"
    elif any(k in joined for k in ("orchestral", "symphonic", "big band", "soundtrack")):
        formation = "big_band"
    else:
        formation = "small_ensemble"

    return harmonic, formation


class JsonDataProvider(DataProvider):
    """100% offline DataProvider serving data/jazz_vault.json and data/sample_playlist.json."""

    def __init__(
        self,
        vault_path: Path | None = None,
        sample_playlist_path: Path | None = None,
    ) -> None:
        self.vault_path = vault_path or DEFAULT_VAULT_PATH
        self.sample_playlist_path = sample_playlist_path or DEFAULT_SAMPLE_PLAYLIST_PATH

        raw_vault = json.loads(self.vault_path.read_text(encoding="utf-8"))
        self.vault = JazzVaultSchema.model_validate(raw_vault)

        raw_playlist = json.loads(self.sample_playlist_path.read_text(encoding="utf-8"))
        self.sample_playlist = SamplePlaylistSchema.model_validate(raw_playlist)

        self._artists_by_norm: dict[str, VaultArtistSchema] = {}
        self._all_canonical_names: list[str] = []
        for artist in self.vault.artists:
            self._all_canonical_names.append(artist.name)
            self._artists_by_norm[_normalize(artist.name)] = artist
            self._artists_by_norm[_normalize(artist.id.replace("-", " "))] = artist
            for alias in artist.aliases:
                self._artists_by_norm[_normalize(alias)] = artist

    def resolve_artist(self, query: str) -> VaultArtistSchema:
        """Resolve an artist by exact name, alias, substring, or raise ArtistNotFoundError."""
        norm_q = _normalize(query)
        if not norm_q:
            raise ArtistNotFoundError(query, suggestions=self._all_canonical_names[:5])

        if norm_q in self._artists_by_norm:
            return self._artists_by_norm[norm_q]

        # Substring match (e.g., "Coltrane" -> "John Coltrane" or "Miles" -> "Miles Davis")
        substring_hits = [
            artist
            for artist in self.vault.artists
            if norm_q in _normalize(artist.name)
            or any(norm_q in _normalize(al) for al in artist.aliases)
        ]
        if len(substring_hits) == 1:
            return substring_hits[0]
        if len(substring_hits) > 1:
            # Prefer exact last-name / first-name token match
            for hit in substring_hits:
                if norm_q in _normalize(hit.name).split():
                    return hit
            return substring_hits[0]

        norm_map = {_normalize(name): name for name in self._all_canonical_names}
        close_norms = difflib.get_close_matches(norm_q, list(norm_map.keys()), n=5, cutoff=0.45)
        suggestions = [norm_map[k] for k in close_norms]
        if not suggestions:
            suggestions = ["Miles Davis", "Bill Evans", "John Coltrane", "Ella Fitzgerald", "Herbie Hancock"]
        raise ArtistNotFoundError(query, suggestions=suggestions)

    def search_albums(
        self,
        artist: str,
        group_format: str | None = None,
        era: str | None = None,
        accessibility_level: str | None = None,
        limit: int = 5,
    ) -> list[AlbumRecord]:
        matched_artist = self.resolve_artist(artist)
        records: list[AlbumRecord] = []

        norm_fmt = group_format.strip().lower() if group_format else None
        norm_era = era.strip().lower() if era else None
        norm_level = accessibility_level.strip().lower() if accessibility_level else None

        for alb in matched_artist.albums:
            if norm_fmt and norm_fmt != "any" and alb.group_format != norm_fmt:
                continue
            if norm_level and norm_level != "any" and alb.accessibility_level != norm_level:
                continue
            if norm_era and norm_era != "any":
                if norm_era not in alb.decade.lower() and norm_era not in str(alb.year) and norm_era not in matched_artist.era.lower():
                    continue
            records.append(
                AlbumRecord(
                    artist=matched_artist.name,
                    title=alb.title,
                    year=alb.year,
                    decade=alb.decade,
                    label=alb.label,
                    group_format=alb.group_format,
                    key_track=alb.key_track,
                    personnel=alb.personnel,
                    accessibility_level=alb.accessibility_level,
                    why_listen=alb.why_listen,
                    styles=matched_artist.styles,
                    spotify_album_url=alb.spotify_album_url,
                    cover_image_url=alb.cover_image_url,
                )
            )

        # If strict filters yielded zero albums for a valid artist, return unfiltered artist albums
        # only when era was overly narrow, or return empty list so caller can relax filters
        return records[: max(1, limit)]

    def get_lineage(self, artist: str, relationship: str = "all") -> LineageResult:
        matched = self.resolve_artist(artist)
        rel = relationship.strip().lower()
        if rel not in {"influences", "collaborators", "disciples", "all"}:
            rel = "all"

        return LineageResult(
            artist=matched.name,
            era=matched.era,
            styles=matched.styles,
            relationship_filter=rel,  # type: ignore[arg-type]
            influences=matched.lineage.influences if rel in {"influences", "all"} else [],
            collaborators=matched.lineage.collaborators if rel in {"collaborators", "all"} else [],
            disciples=matched.lineage.disciples if rel in {"disciples", "all"} else [],
        )

    def get_bridges(self, anchor: str, vibe: str = "any") -> list[BridgeRecommendation]:
        norm_anchor = _normalize(anchor)
        norm_vibe = _normalize(vibe) if vibe else "any"

        scored: list[tuple[int, BridgeRecommendation]] = []
        for b in self.vault.bridges:
            score = 0
            matched_tag = b.anchor_tags[0]
            for tag in b.anchor_tags:
                nt = _normalize(tag)
                if norm_anchor == nt:
                    score = max(score, 10)
                    matched_tag = tag
                elif norm_anchor in nt or nt in norm_anchor:
                    score = max(score, 6)
                    matched_tag = tag
            if norm_anchor in _normalize(b.target_subgenre) or norm_anchor in _normalize(b.recommended_artist):
                score = max(score, 5)

            if score > 0 and norm_vibe not in {"", "any"}:
                if any(norm_vibe in _normalize(v) or _normalize(v) in norm_vibe for v in b.vibes):
                    score += 3

            if score > 0:
                scored.append(
                    (
                        score,
                        BridgeRecommendation(
                            anchor_matched=matched_tag,
                            target_subgenre=b.target_subgenre,
                            recommended_artist=b.recommended_artist,
                            recommended_album=b.recommended_album,
                            year=b.year,
                            group_format=b.group_format,
                            accessibility_level=b.accessibility_level,
                            bridge_rationale=b.bridge_rationale,
                            vibes=b.vibes,
                        ),
                    )
                )

        if not scored:
            available = sorted({b.anchor_tags[0] for b in self.vault.bridges})
            raise BridgeNotFoundError(anchor, available_anchors=available)

        scored.sort(key=lambda item: item[0], reverse=True)
        return [rec for _, rec in scored[:5]]

    def build_report_from_tracks(
        self,
        *,
        playlist_id: str,
        sanitized_ref: str,
        playlist_title: str,
        tracks: list[dict[str, Any]],
        data_source: str,
        audio_features_source: str,
    ) -> PlaylistAnalysisReport:
        """Compute a complete Sonic Fingerprint report from normalized track dicts."""
        artist_counter: Counter[str] = Counter()
        genre_counter: Counter[str] = Counter()
        bpms: list[float] = []
        energies: list[float] = []

        for tr in tracks:
            artist_name = str(tr.get("artist", "Unknown Artist")).strip()
            if artist_name:
                artist_counter[artist_name] += 1
            tr_genres = [str(g).strip().lower() for g in tr.get("genres", []) if str(g).strip()]
            for g in tr_genres:
                genre_counter[g] += 1
            bpm = tr.get("tempo_bpm")
            energy = tr.get("energy")
            if bpm is None or energy is None:
                est_bpm, est_energy = estimate_track_tempo_and_energy(
                    tr_genres, int(tr.get("duration_ms", 240000))
                )
                bpms.append(float(bpm) if bpm is not None else est_bpm)
                energies.append(float(energy) if energy is not None else est_energy)
            else:
                bpms.append(float(bpm))
                energies.append(float(energy))

        avg_bpm = round(sum(bpms) / len(bpms), 1) if bpms else 108.0
        avg_energy = round(sum(energies) / len(energies), 2) if energies else 0.52

        if avg_energy < 0.35:
            energy_profile = f"Intimate & contemplative (avg energy {avg_energy:.2f})"
        elif avg_energy < 0.65:
            energy_profile = f"Balanced & dynamic groove (avg energy {avg_energy:.2f})"
        else:
            energy_profile = f"High-voltage & propulsive (avg energy {avg_energy:.2f})"

        if avg_bpm < 88:
            tempo_profile = f"Slow, spacious pulse (~{avg_bpm} BPM)"
        elif avg_bpm < 122:
            tempo_profile = f"Mid-tempo walking/groove pocket (~{avg_bpm} BPM)"
        else:
            tempo_profile = f"Up-tempo driving momentum (~{avg_bpm} BPM)"

        top_artists = [a for a, _ in artist_counter.most_common(6)]
        dominant_genres = [g for g, _ in genre_counter.most_common(6)] or ["eclectic"]

        jazz_ratio, inferred_tier = _classify_jazz_tier(genre_counter, len(tracks))
        harmonic_pref, formation_pref = _infer_harmonic_and_formation(dominant_genres, avg_energy)

        # Collect distinct stylistic bridges matching dominant genres and top artists
        seen_albums: set[str] = set()
        bridges: list[BridgeRecommendation] = []
        for candidate in [*dominant_genres, *top_artists]:
            try:
                matches = self.get_bridges(candidate)
                for m in matches:
                    if m.recommended_album not in seen_albums:
                        seen_albums.add(m.recommended_album)
                        bridges.append(m)
                        break
            except BridgeNotFoundError:
                continue
            if len(bridges) >= 4:
                break

        if not bridges:
            bridges = self.get_bridges("classic rock")[:2]

        summary = (
            f"Audited {len(tracks)} tracks from '{playlist_title}'. "
            f"Dominant palette: {', '.join(dominant_genres[:4])} ({tempo_profile}; {energy_profile}). "
            f"Calibrated jazz initiation tier: '{inferred_tier}' "
            f"(jazz track presence ~{int(jazz_ratio * 100)}%), favoring '{harmonic_pref}' harmony "
            f"and '{formation_pref}' textures."
        )

        return PlaylistAnalysisReport(
            playlist_id=playlist_id,
            sanitized_playlist_ref=sanitized_ref,
            playlist_title=playlist_title,
            analyzed_track_count=len(tracks),
            top_artists=top_artists,
            dominant_genres=dominant_genres,
            estimated_tempo_bpm=avg_bpm,
            energy_profile=energy_profile,
            tempo_profile=tempo_profile,
            jazz_track_ratio=jazz_ratio,
            inferred_knowledge_level=inferred_tier,
            suggested_harmonic_preference=harmonic_pref,
            suggested_formation_preference=formation_pref,
            sonic_fingerprint_summary=summary,
            data_source=data_source,
            audio_features_source=audio_features_source,
            bridge_recommendations=bridges,
        )

    def analyze_playlist(self, playlist_url_or_id: str) -> PlaylistAnalysisReport:
        try:
            validated = PlaylistAuditInput(playlist_url_or_id=playlist_url_or_id)
        except ValidationError as exc:
            raise PlaylistValidationError(playlist_url_or_id, str(exc.errors()[0]["msg"])) from exc

        extracted_id = validated.extract_playlist_id()
        source_label = (
            "offline_sample" if extracted_id == "sample" else "offline_sample_fallback"
        )
        tracks_dicts = [t.model_dump() for t in self.sample_playlist.tracks]
        return self.build_report_from_tracks(
            playlist_id=self.sample_playlist.playlist_id if extracted_id == "sample" else extracted_id,
            sanitized_ref=validated.playlist_url_or_id,
            playlist_title=self.sample_playlist.playlist_name,
            tracks=tracks_dicts,
            data_source=source_label,
            audio_features_source="catalog_reference_bpm",
        )
