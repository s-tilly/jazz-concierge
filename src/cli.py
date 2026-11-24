"""Interactive Terminal CLI for Jazz Concierge (`MilesAgent`)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from google.adk.sessions import InMemorySessionService  # noqa: E402

from src.agent import create_jazz_app, run_concierge_turn  # noqa: E402
from src.config.settings import get_settings  # noqa: E402
from src.data_providers import SpotifyDataProvider, get_data_provider  # noqa: E402
from src.memory import get_memory_store  # noqa: E402
from src.tools.jazz_tools import (  # noqa: E402
    analyze_playlist,
    export_curated_playlist,
    reset_user_taste_profile,
)

BANNER = """
========================================================================
  🎷  MilesAgent — Autonomous Multi-Agent Jazz Concierge (Google ADK)
========================================================================
Commands:
  /sample              Audit the built-in eclectic sample playlist
  /profile             Inspect your stored SQLite taste profile
  /export [md|json]    Export your curated session playlist
  /reset               Reset your taste profile (Human-in-the-Loop gate)
  /quit                Exit MilesAgent CLI
------------------------------------------------------------------------
"""


async def _handle_cli_command(cmd: str, user_id: str, session_id: str) -> bool:
    """Process built-in slash commands. Returns False when `/quit` is issued."""
    parts = cmd.strip().split()
    verb = parts[0].lower()
    memory = get_memory_store()

    if verb in {"/quit", "/exit", "quit", "exit"}:
        sys.stdout.write("Happy listening! 🎷\n")
        return False

    if verb == "/sample":
        res = await analyze_playlist(playlist_url_or_id="sample", user_id=user_id)
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        return True

    if verb == "/profile":
        prof = await memory.get_user_profile(user_id)
        sys.stdout.write(json.dumps(prof, indent=2, ensure_ascii=False) + "\n")
        return True

    if verb == "/export":
        fmt = "json" if (len(parts) > 1 and parts[1].lower() == "json") else "markdown"
        exp = await export_curated_playlist(
            user_id=user_id,
            session_id=session_id,
            export_format=fmt,  # type: ignore[arg-type]
        )
        sys.stdout.write(str(exp.get("content", exp)) + "\n")
        return True

    if verb == "/reset":
        sys.stdout.write(
            f"[HITL Gate] Permanently wipe stored profile and history for '{user_id}'? [y/N]: "
        )
        sys.stdout.flush()
        answer = sys.stdin.readline().strip().lower()
        if answer in {"y", "yes"}:
            out = await reset_user_taste_profile(
                user_id=user_id,
                reason="Confirmed via interactive CLI HITL prompt",
                confirmed=True,
            )
            sys.stdout.write(json.dumps(out, indent=2) + "\n")
        else:
            sys.stdout.write("Profile reset cancelled.\n")
        return True

    sys.stdout.write(f"Unknown command '{verb}'. Try /sample, /profile, /export, /reset, or /quit.\n")
    return True


async def run_cli(
    user_id: str,
    single_prompt: str | None = None,
    playlist_to_audit: str | None = None,
) -> int:
    """Run MilesAgent in single-turn or interactive REPL mode."""
    settings = get_settings()
    provider = get_data_provider()
    session_id = f"cli_{uuid.uuid4().hex[:10]}"

    if playlist_to_audit:
        res = await analyze_playlist(playlist_url_or_id=playlist_to_audit, user_id=user_id)
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        if not single_prompt:
            return 0

    session_service = InMemorySessionService()
    adk_app = create_jazz_app()

    if single_prompt:
        turn = await run_concierge_turn(
            single_prompt,
            user_id=user_id,
            session_id=session_id,
            session_service=session_service,
            adk_app=adk_app,
        )
        sys.stdout.write(turn["response"] + "\n")
        return 0

    provider_mode = "SpotifyDataProvider (live)" if isinstance(provider, SpotifyDataProvider) else "JsonDataProvider (offline vault)"
    sys.stdout.write(BANNER)
    sys.stdout.write(
        f"Active Data Layer : {provider_mode}\n"
        f"Model Routing     : {settings.profiler_model} -> {settings.musicologist_model} -> {settings.curator_model}\n"
        f"Listener ID       : {user_id}\n\n"
    )

    while True:
        sys.stdout.write("\nYou > ")
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            break
        query = line.strip()
        if not query:
            continue
        if query.startswith("/"):
            keep_going = await _handle_cli_command(query, user_id=user_id, session_id=session_id)
            if not keep_going:
                break
            continue

        turn = await run_concierge_turn(
            query,
            user_id=user_id,
            session_id=session_id,
            session_service=session_service,
            adk_app=adk_app,
        )
        sys.stdout.write(f"\nMilesAgent >\n{turn['response']}\n")

    return 0


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="MilesAgent — Multi-Agent Jazz Concierge CLI")
    parser.add_argument("--user-id", default="cli_listener", help="Persistent listener identifier.")
    parser.add_argument("--prompt", default=None, help="Execute a single concierge turn and exit.")
    parser.add_argument(
        "--audit-playlist",
        default=None,
        help="Audit a public Spotify playlist URL/URI or 'sample' offline/live.",
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run_cli(
                user_id=args.user_id,
                single_prompt=args.prompt,
                playlist_to_audit=args.audit_playlist,
            )
        )
    )


if __name__ == "__main__":
    main()
