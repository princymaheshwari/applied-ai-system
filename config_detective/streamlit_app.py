"""Streamlit UI for CONFIG DETECTIVE.

Final layout (built incrementally across phases):

    Tab 1 - Investigate          (Phase 9)
    Tab 2 - Live Trace Viewer    (Phase 9, surfaces Phase 5 orchestrator state)
    Tab 3 - Memory Dashboard     (Phase 9, surfaces Phase 3 memory store)
    Tab 4 - Eval Harness         (Phase 9, surfaces Phase 10 benchmark)
    Tab 5 - MCP Export           (Phase 9, surfaces Phase 8b MCP server)

This file is currently a placeholder with a single landing page.
"""

from __future__ import annotations

import streamlit as st

from config_detective import __version__


def main() -> None:
    st.set_page_config(
        page_title="CONFIG DETECTIVE",
        page_icon=":mag:",
        layout="wide",
    )
    st.title("CONFIG DETECTIVE")
    st.caption(
        f"Works-on-my-machine forensics agent - v{__version__} - scaffolding phase"
    )

    st.markdown(
        """
        > This is a scaffolding placeholder. The full 5-tab UI lands in
        > **Phase 9** of the build. See [`README.md`](https://github.com/princymaheshwari/applied-ai-system)
        > for the project plan.
        """
    )

    st.subheader("Build status")
    st.markdown(
        """
        - Phase 0: project scaffolding - done
        - Phase 1: snapshot module - pending
        - Phase 2: environment graph + differ - pending
        - Phase 3: memory RAG - pending
        - Phase 4: multi-source retrieval - pending
        - Phase 5: LangGraph orchestrator - pending
        - Phase 6: sandbox verifier - pending
        - Phase 7: guardrails - pending
        - Phase 8a: patcher (propose / apply / undo) - pending
        - Phase 8b: MCP server - pending
        - Phase 9: Streamlit UI tabs - pending
        - Phase 10: 15-case eval benchmark - pending
        - Phase 11: tests, docs, Loom recording - pending
        """
    )


if __name__ == "__main__":
    main()
