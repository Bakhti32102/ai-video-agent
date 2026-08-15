"""Streamlit UI for the AI Video Agent.

Run with:
    streamlit run app/ui.py

Provides a form to enter a documentary script, configure duration, and
produce a 16:9 documentary-style video through the full MCP pipeline.
"""

from __future__ import annotations

import asyncio

import streamlit as st

from app.agents.supervisor import SupervisorAgent
from app.config import get_settings
from app.database import init_db
from app.mcp.client import McpClient
from app.utils.ids import new_id


def main() -> None:
    st.set_page_config(page_title="AI Video Agent", page_icon="🎬", layout="wide")
    st.title("AI Video Agent")
    st.caption("Production-oriented documentary video generator (Phase 4)")

    # Ensure DB is initialized.
    settings = get_settings()
    settings.ensure_runtime_dirs()
    init_db(settings)

    col1, col2 = st.columns([2, 1])

    with col1:
        script_text = st.text_area(
            "Documentary Script",
            height=200,
            placeholder="Enter your documentary script here...",
            help="The script will be analyzed, split into scenes, and used to generate a video.",
        )

    with col2:
        duration = st.slider("Target Duration (seconds)", min_value=5.0, max_value=120.0, value=30.0, step=5.0)
        voiceover_path = st.text_input(
            "Voiceover Audio Path (optional)",
            placeholder="/path/to/voiceover.mp3",
            help="Path to a voiceover audio file. If omitted, silent video is produced.",
        )
        project_id = st.text_input("Project ID (optional)", placeholder="auto-generated if blank")

    if st.button("🎬 Produce Video", type="primary", disabled=not script_text or len(script_text) < 10):
        pid = project_id or new_id("proj_")
        with st.status("Running video production pipeline...", expanded=True) as status:
            st.write(f"**Project ID:** {pid}")
            st.write(f"**Duration:** {duration}s")

            async def _run() -> dict:
                client = McpClient()
                sup = SupervisorAgent(client)
                return await sup.run_project(
                    project_id=pid,
                    script_text=script_text,
                    voiceover_path=voiceover_path or None,
                    total_duration_sec=duration,
                )

            result = asyncio.run(_run())

            status.update(
                label=f"Pipeline finished: {result['final_state'].upper()}",
                state="complete" if not result["failed"] else "error",
            )

        # Display results.
        st.subheader("Results")

        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("Scenes", len(result["scenes"]))
        col_b.metric("Text Overlays", len(result.get("text_overlays", [])))
        col_c.metric("Transitions", len(result.get("transitions", [])))
        col_d.metric("QA Passed", "✅" if result.get("qa_report", {}).get("passed") else "❌")

        render_output = result.get("results", {}).get("render", {}).get("output", {}).get("output_path")
        if render_output:
            st.success(f"Video produced: `{render_output}`")
            # Show video player if file exists.
            import os
            if os.path.exists(render_output):
                st.video(render_output)

        # QA findings.
        qa = result.get("qa_report", {})
        if qa and qa.get("findings"):
            with st.expander(f"QA Findings ({len(qa['findings'])})"):
                for f in qa["findings"]:
                    sev = f.get("severity", "info").upper()
                    icon = {"critical": "🔴", "error": "🟠", "warning": "🟡", "info": "🔵"}.get(sev.lower(), "⚪")
                    st.write(f"{icon} **[{sev}]** {f.get('category', '')}: {f.get('message', '')}")

        # Scenes detail.
        if result["scenes"]:
            with st.expander(f"Scenes ({len(result['scenes'])})"):
                for scene in result["scenes"]:
                    st.write(f"**Scene {scene.get('index', 0)}:** {scene.get('title', 'Untitled')}")
                    st.write(f"  - Time: {scene.get('start_time', 0):.1f}s - {scene.get('end_time', 0):.1f}s")
                    if scene.get("narration"):
                        st.write(f"  - Narration: {scene['narration'][:100]}...")
                    st.write("")


if __name__ == "__main__":
    main()
