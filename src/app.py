"""Interactive Streamlit Web UI for Jazz Concierge (`MilesAgent`)."""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from google.adk.sessions import InMemorySessionService  # noqa: E402

from src.agent import create_jazz_app, run_concierge_turn  # noqa: E402
from src.config.settings import get_settings  # noqa: E402
from src.data_providers import SpotifyDataProvider, get_data_provider  # noqa: E402
from src.memory import compact_conversation_turns, get_memory_store  # noqa: E402
from src.tools.jazz_tools import (  # noqa: E402
    analyze_playlist,
    export_curated_playlist,
    reset_user_taste_profile,
)


def _run_async(coro: Any) -> Any:
    """Run an async coroutine safely from Streamlit's synchronous execution thread."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _init_ui_state() -> None:
    if "user_id" not in st.session_state:
        st.session_state.user_id = "vinyl_listener_01"
    if "session_id" not in st.session_state:
        st.session_state.session_id = f"st_{uuid.uuid4().hex[:10]}"
    if "session_service" not in st.session_state:
        st.session_state.session_service = InMemorySessionService()
    if "adk_app" not in st.session_state:
        st.session_state.adk_app = create_jazz_app()
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    "Welcome to **MilesAgent** (`jazz-concierge`)! 🎷\n\n"
                    "Whether you have **never listened to jazz before** or collect rare post-bop "
                    "and avant-garde pressings, tell me a few songs, classical composers, bands, "
                    "or moods you enjoy—or audit a **public Spotify playlist** in the sidebar!"
                ),
            }
        ]
    if "latest_audit" not in st.session_state:
        st.session_state.latest_audit = None
    if "confirm_reset_pending" not in st.session_state:
        st.session_state.confirm_reset_pending = False
    if "latest_export" not in st.session_state:
        st.session_state.latest_export = None


def render_app() -> None:
    """Render the Streamlit concierge interface."""
    st.set_page_config(
        page_title="MilesAgent — Jazz Concierge",
        page_icon="🎷",
        layout="wide",
    )
    _init_ui_state()
    settings = get_settings()
    provider = get_data_provider()
    memory = get_memory_store()
    profile = _run_async(memory.get_user_profile(st.session_state.user_id))

    # --- Sidebar: Sonic Fingerprint, Taste Profile, Export & HITL Controls ---
    with st.sidebar:
        st.title("🎷 MilesAgent Studio")
        provider_badge = (
            "🟢 Spotify Live + Vault"
            if isinstance(provider, SpotifyDataProvider)
            else "🟡 Offline Curated Vault"
        )
        st.caption(
            f"**Data Layer:** {provider_badge}  \n"
            f"**Models:** `{settings.profiler_model}` → `{settings.musicologist_model}` → `{settings.curator_model}`"
        )

        st.divider()
        new_uid = st.text_input("Listener ID", value=st.session_state.user_id)
        if new_uid.strip() and new_uid.strip() != st.session_state.user_id:
            st.session_state.user_id = new_uid.strip()
            st.rerun()

        st.subheader("🎧 Analyze My Playlist")
        playlist_ref = st.text_input(
            "Public Spotify Playlist URL / URI",
            placeholder="https://open.spotify.com/playlist/...",
        )
        col_a, col_b = st.columns(2)
        with col_a:
            audit_clicked = st.button("Analyze URL", use_container_width=True)
        with col_b:
            sample_clicked = st.button("Test Sample", use_container_width=True)

        if audit_clicked and playlist_ref.strip():
            res = _run_async(
                analyze_playlist(
                    playlist_url_or_id=playlist_ref.strip(),
                    user_id=st.session_state.user_id,
                )
            )
            st.session_state.latest_audit = res
            profile = _run_async(memory.get_user_profile(st.session_state.user_id))

        if sample_clicked:
            res = _run_async(
                analyze_playlist(
                    playlist_url_or_id="sample",
                    user_id=st.session_state.user_id,
                )
            )
            st.session_state.latest_audit = res
            profile = _run_async(memory.get_user_profile(st.session_state.user_id))

        if st.session_state.latest_audit:
            audit = st.session_state.latest_audit
            if audit.get("status") == "success":
                rep = audit["report"]
                st.success(
                    f"**{rep['playlist_title']}** ({rep['analyzed_track_count']} tracks)\n\n"
                    f"- **Tier:** `{rep['inferred_knowledge_level']}`\n"
                    f"- **Tempo:** {rep['tempo_profile']}\n"
                    f"- **Genres:** {', '.join(rep['dominant_genres'][:4])}"
                )
            else:
                st.warning(f"{audit.get('message')}\n\n*{audit.get('recovery_instructions')}*")

        st.divider()
        st.subheader("🧭 Stored Taste Profile")
        st.markdown(
            f"- **Knowledge Tier:** `{profile['knowledge_level']}`\n"
            f"- **Harmonic Appetite:** `{profile['harmonic_preference']}`\n"
            f"- **Ensemble Format:** `{profile['formation_preference']}`\n"
            f"- **Liked Artists:** {', '.join(profile['liked_artists']) or 'None yet'}\n"
            f"- **History Count:** {len(profile['listening_history'])} album(s)"
        )

        st.divider()
        st.subheader("📤 Export Curated Playlist")
        exp_fmt = st.selectbox("Export Format", options=["markdown", "json"])
        if st.button("Generate Export", use_container_width=True):
            exp_res = _run_async(
                export_curated_playlist(
                    user_id=st.session_state.user_id,
                    session_id=st.session_state.session_id,
                    export_format=exp_fmt,  # type: ignore[arg-type]
                )
            )
            if exp_res.get("status") == "success":
                st.session_state.latest_export = exp_res

        if st.session_state.latest_export:
            exp = st.session_state.latest_export
            ext = "json" if exp["export_format"] == "json" else "md"
            st.download_button(
                label=f"⬇️ Download .{ext}",
                data=exp["content"],
                file_name=f"miles_agent_playlist.{ext}",
                mime="application/json" if ext == "json" else "text/markdown",
                use_container_width=True,
            )

        st.divider()
        st.subheader("🛡️ Human-in-the-Loop Reset")
        if not st.session_state.confirm_reset_pending:
            if st.button("Reset Taste Profile...", type="secondary", use_container_width=True):
                st.session_state.confirm_reset_pending = True
                st.rerun()
        else:
            st.warning("Confirm permanent deletion of your profile and listening history?")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Confirm Wipe", use_container_width=True):
                    _run_async(
                        reset_user_taste_profile(
                            user_id=st.session_state.user_id,
                            reason="Explicit Streamlit HITL user confirmation",
                            confirmed=True,
                        )
                    )
                    st.session_state.confirm_reset_pending = False
                    st.session_state.latest_audit = None
                    st.session_state.latest_export = None
                    st.success("Profile and listening history wiped.")
                    st.rerun()
            with c2:
                if st.button("✖ Cancel", use_container_width=True):
                    st.session_state.confirm_reset_pending = False
                    st.rerun()

    # --- Main Area: Quick Starter Scenarios & Chat ---
    st.title("🎷 Jazz Concierge (`MilesAgent`)")
    st.caption(
        "Cooperative Multi-Agent Jazz Discovery • Powered by Google ADK-Python & Gemini 4"
    )

    q_col1, q_col2, q_col3 = st.columns(3)
    quick_prompt: str | None = None
    with q_col1:
        if st.button("🌱 Complete Beginner (Pink Floyd & Debussy)", use_container_width=True):
            quick_prompt = (
                "I know nothing about jazz at all. In everyday life I love Pink Floyd "
                "and Claude Debussy. What warm, melodic jazz albums should I start with?"
            )
    with q_col2:
        if st.button("🎺 Audit Sample Eclectic Playlist", use_container_width=True):
            quick_prompt = (
                "Please audit the 'sample' playlist to build my Sonic Fingerprint and "
                "recommend 3 gateway jazz albums that bridge my tastes."
            )
    with q_col3:
        if st.button("🔥 Connoisseur (Boulez -> Avant-Garde)", use_container_width=True):
            quick_prompt = (
                "I'm a jazz connoisseur fascinated by Pierre Boulez, Olivier Messiaen, "
                "and Eric Dolphy. Trace that avant-garde lineage and curate 3 deep cuts."
            )

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    typed_prompt = st.chat_input(
        "Ask about jazz, name non-jazz favorites, or paste a public Spotify playlist URL..."
    )
    active_prompt = quick_prompt or typed_prompt

    if active_prompt:
        st.session_state.messages.append({"role": "user", "content": active_prompt})
        with st.chat_message("user"):
            st.markdown(active_prompt)

        with st.chat_message("assistant"):
            with st.spinner("Profiler → Musicologist → Curator collaborating..."):
                try:
                    turn_result = _run_async(
                        run_concierge_turn(
                            active_prompt,
                            user_id=st.session_state.user_id,
                            session_id=st.session_state.session_id,
                            session_service=st.session_state.session_service,
                            adk_app=st.session_state.adk_app,
                        )
                    )
                    reply = turn_result["response"] or "Curated turn completed."
                    st.markdown(reply)
                    with st.expander("🔍 AgentOps Trace (Models, Agents & Tools)"):
                        st.json(
                            {
                                "agents_invoked": list(turn_result["agent_outputs"].keys()),
                                "tools_called": turn_result["tool_calls"],
                                "latency_ms": turn_result["latency_ms"],
                            }
                        )
                    st.session_state.messages.append({"role": "assistant", "content": reply})

                    # Async sliding-window history compaction in SQLite
                    summary, compacted_msgs = compact_conversation_turns(
                        st.session_state.messages,
                        max_recent_turns=10,
                        existing_summary=profile.get("compacted_summary", ""),
                    )
                    st.session_state.messages = compacted_msgs
                    _run_async(
                        memory.update_taste_profile(
                            user_id=st.session_state.user_id,
                            compacted_summary=summary,
                        )
                    )
                except Exception as exc:
                    err_text = (
                        f"⚠️ **Model Connection Error (`{type(exc).__name__}`):** {exc}\n\n"
                        "Ensure `.env` has `GOOGLE_CLOUD_PROJECT` configured and run "
                        "`gcloud auth application-default login` (or set `GOOGLE_API_KEY`)."
                    )
                    st.error(err_text)


if __name__ == "__main__":
    render_app()
