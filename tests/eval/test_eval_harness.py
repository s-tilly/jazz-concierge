"""Static/offline golden-dataset regression harness for MilesAgent (`tests/eval/`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from src.agents.guardrails import check_text_against_guardrails
from src.tools.jazz_tools import (
    ALL_JAZZ_TOOLS,
    analyze_playlist,
    export_curated_playlist,
    find_subgenre_bridges,
    reset_user_taste_profile,
    search_artist_discography,
    sync_user_taste_profile,
    trace_artist_lineage,
)

EVAL_DIR = Path(__file__).resolve().parent
CONFIG_PATH = EVAL_DIR / "eval_config.yaml"
DATASET_PATH = EVAL_DIR / "golden_dataset.json"


def test_eval_config_schema_and_thresholds() -> None:
    """Verify eval_config.yaml defines metrics, custom metrics, and strict pass thresholds."""
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "metrics_to_run" in cfg and len(cfg["metrics_to_run"]) >= 3
    assert "custom_metrics" in cfg and len(cfg["custom_metrics"]) >= 2
    assert "thresholds" in cfg
    assert cfg["thresholds"]["safety"] >= 0.90
    assert cfg["thresholds"]["jazz_curation_rubric"] >= 4.0


def test_golden_dataset_structure_and_tool_declarations() -> None:
    """Ensure every golden scenario follows the ADK EvaluationDataset contract and valid tools."""
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    cases = dataset.get("eval_cases", [])
    assert len(cases) >= 6

    declared_tool_names = {t.name for t in ALL_JAZZ_TOOLS}
    seen_ids: set[str] = set()

    for case in cases:
        cid = case["eval_case_id"]
        assert cid not in seen_ids
        seen_ids.add(cid)
        assert case["prompt"]["role"] == "user"
        prompt_text = case["prompt"]["parts"][0]["text"]
        assert len(prompt_text) > 10
        assert "rubric_groups" in case

        traj = case["expected_trajectory"]
        for tool_name in traj.get("expected_tools", []):
            assert tool_name in declared_tool_names, f"Unknown tool '{tool_name}' in {cid}"


@pytest.mark.asyncio
async def test_golden_dataset_offline_trajectory_simulation() -> None:
    """Execute deterministic tool trajectories for every golden case 100% offline."""
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    for case in dataset["eval_cases"]:
        cid = case["eval_case_id"]
        prompt_text = case["prompt"]["parts"][0]["text"]
        traj = case["expected_trajectory"]

        if traj.get("guardrail_expected"):
            blocked, reason_code, safe_reply = check_text_against_guardrails(prompt_text)
            assert blocked is True, f"Expected guardrail block for {cid}"
            assert reason_code == traj["guardrail_expected"]
            assert "MilesAgent" in safe_reply
            continue

        if traj.get("requires_hitl_confirmation"):
            hitl_res = await reset_user_taste_profile(
                user_id=f"eval_{cid}",
                reason="Golden dataset HITL check",
                confirmed=False,
            )
            assert hitl_res["status"] == "confirmation_required"
            assert hitl_res["error_code"] == "HITL_CONFIRMATION_REQUIRED"
            continue

        user_id = f"eval_{cid}"
        target_level = traj["target_knowledge_level"]
        sync_res = await sync_user_taste_profile(
            user_id=user_id,
            knowledge_level=target_level,
        )
        assert sync_res["status"] == "success"
        assert sync_res["taste_profile"]["knowledge_level"] == target_level

        observed_artists: set[str] = set()

        if "playlist_ref" in traj:
            audit_res = await analyze_playlist(
                playlist_url_or_id=traj["playlist_ref"],
                user_id=user_id,
            )
            assert audit_res["status"] == "success"
            assert audit_res["report"]["inferred_knowledge_level"] == target_level
            for b in audit_res["report"]["bridge_recommendations"]:
                observed_artists.add(b["recommended_artist"])

        for anchor in traj.get("bridge_anchors", []):
            bridge_res = find_subgenre_bridges(anchor_genre_or_composer=anchor)
            assert bridge_res["status"] == "success"
            for b in bridge_res["bridges"]:
                observed_artists.add(b["recommended_artist"])

        if "lineage_artist" in traj:
            lin_res = trace_artist_lineage(artist=traj["lineage_artist"], relationship="all")
            assert lin_res["status"] == "success"
            observed_artists.add(lin_res["lineage"]["artist"])
            for group in ("influences", "collaborators", "disciples"):
                for entry in lin_res["lineage"][group]:
                    observed_artists.add(entry["artist"])

            disc_res = search_artist_discography(artist=traj["lineage_artist"], limit=5)
            assert disc_res["status"] == "success"
            assert disc_res["count"] >= 1

        expected_candidates = set(traj.get("expected_candidate_artists", []))
        if expected_candidates:
            assert expected_candidates & observed_artists, (
                f"Case '{cid}' expected overlap with {expected_candidates}, got {observed_artists}"
            )

        export_res = await export_curated_playlist(
            user_id=user_id,
            session_id=f"sess_{cid}",
            export_format="markdown",
        )
        assert export_res["status"] == "success"
        assert export_res["item_count"] >= 1
