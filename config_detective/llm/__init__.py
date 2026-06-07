"""LLM clients — Groq (primary), Gemini (fallback), HuggingFace (code understanding).

Three providers, one router:
- **Groq** (Llama 3.3 70B) — primary reasoning LLM for explanations, fixes, and critiques
- **Gemini** (2.0 Flash) — fallback reasoning LLM when Groq is rate-limited
- **HuggingFace** (Qwen2.5-Coder-32B) — code understanding and fix generation

The LLMRouter tries providers in order and gracefully falls back when
APIs are unavailable. The orchestrator nodes use the router — if no LLM
is configured, they fall back to their existing template-based heuristics.

Usage:
    from config_detective.llm import get_router

    router = get_router()

    # Check availability
    if router.has_reasoning_llm:
        explanation = router.generate_explanation(delta, error_type, trace)

    # Or just call — returns None if no LLM available
    fix_suggestion, fix_code = router.generate_fix(delta, error_type, explanation)
"""

from .router import LLMRouter, get_router, reset_router
from . import groq_client, gemini_client, hf_code

__all__ = [
    "LLMRouter",
    "gemini_client",
    "get_router",
    "groq_client",
    "hf_code",
    "reset_router",
]
