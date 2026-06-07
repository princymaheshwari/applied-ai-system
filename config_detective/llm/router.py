"""LLM router — unified interface with automatic fallback chain.

The router tries providers in order:
1. Groq (Llama 3.3 70B) — fast, free, primary
2. Gemini (2.0 Flash) — fallback if Groq is down or rate-limited
3. HuggingFace (Qwen2.5-Coder) — code-specific tasks only
4. Template fallback — deterministic heuristics (always available)

Every node in the pipeline calls the router instead of a specific provider.
If all LLMs are unavailable, the router returns None and the calling node
falls back to its existing template-based logic. This guarantees the system
always works — LLMs enhance quality but are never required.
"""

from __future__ import annotations

import logging
from typing import Any

from . import groq_client, gemini_client, hf_code

logger = logging.getLogger(__name__)


class LLMRouter:
    """Routes LLM requests through the fallback chain."""

    def __init__(self) -> None:
        self._providers_checked = False
        self._groq_available = False
        self._gemini_available = False
        self._hf_available = False

    def _check_providers(self) -> None:
        """Lazily check which providers are available."""
        if self._providers_checked:
            return
        self._groq_available = groq_client.is_available()
        self._gemini_available = gemini_client.is_available()
        self._hf_available = hf_code.is_available()
        self._providers_checked = True

        available = []
        if self._groq_available:
            available.append("Groq")
        if self._gemini_available:
            available.append("Gemini")
        if self._hf_available:
            available.append("HuggingFace")

        if available:
            logger.info(f"LLM providers available: {', '.join(available)}")
        else:
            logger.info("No LLM providers configured — using template fallbacks")

    @property
    def has_reasoning_llm(self) -> bool:
        """Whether a reasoning LLM (Groq or Gemini) is available."""
        self._check_providers()
        return self._groq_available or self._gemini_available

    @property
    def has_code_llm(self) -> bool:
        """Whether the code LLM (HuggingFace Qwen) is available."""
        self._check_providers()
        return self._hf_available

    @property
    def available_providers(self) -> list[str]:
        """List of available provider names."""
        self._check_providers()
        providers = []
        if self._groq_available:
            providers.append("groq")
        if self._gemini_available:
            providers.append("gemini")
        if self._hf_available:
            providers.append("huggingface")
        return providers

    def generate_explanation(
        self,
        delta: dict[str, Any],
        error_type: str,
        failure_trace: str,
    ) -> str | None:
        """Generate a root-cause explanation using the best available LLM.

        Tries Groq first, then Gemini. Returns None if neither is available.
        """
        self._check_providers()

        if self._groq_available:
            result = groq_client.generate_explanation(delta, error_type, failure_trace)
            if result:
                return result
            logger.warning("Groq explanation failed, trying Gemini")

        if self._gemini_available:
            result = gemini_client.generate_explanation(delta, error_type, failure_trace)
            if result:
                return result

        return None

    def generate_fix(
        self,
        delta: dict[str, Any],
        error_type: str,
        explanation: str,
    ) -> tuple[str | None, str | None]:
        """Generate a fix suggestion using the best available LLM.

        Tries Groq first, then Gemini. Returns (None, None) if neither is available.
        """
        self._check_providers()

        if self._groq_available:
            result = groq_client.generate_fix_suggestion(delta, error_type, explanation)
            if result[0] is not None:
                return result
            logger.warning("Groq fix generation failed, trying Gemini")

        if self._gemini_available:
            result = gemini_client.generate_fix_suggestion(delta, error_type, explanation)
            if result[0] is not None:
                return result

        return None, None

    def critique_hypothesis(
        self,
        hypothesis: dict[str, Any],
        deltas: list[dict[str, Any]],
        failure_trace: str,
    ) -> dict[str, Any] | None:
        """Critique a hypothesis using the reasoning LLM.

        Only Groq supports this (structured output format).
        Returns None if unavailable.
        """
        self._check_providers()

        if self._groq_available:
            return groq_client.critique_hypothesis(hypothesis, deltas, failure_trace)
        return None

    def analyse_trace(
        self,
        trace: str,
        context: str = "",
    ) -> str | None:
        """Analyse a failure trace using the code LLM.

        Uses HuggingFace Qwen2.5-Coder for code understanding.
        Returns None if unavailable.
        """
        self._check_providers()

        if self._hf_available:
            return hf_code.analyse_failure_trace(trace, context)
        return None

    def generate_fix_code(
        self,
        description: str,
        target_file_type: str = "generic",
    ) -> str | None:
        """Generate concrete fix code using the code LLM.

        Uses HuggingFace Qwen2.5-Coder. Returns None if unavailable.
        """
        self._check_providers()

        if self._hf_available:
            return hf_code.generate_fix_code(description, target_file_type)
        return None


_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    """Get the global LLM router singleton."""
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router


def reset_router() -> None:
    """Reset the global router (for testing)."""
    global _router
    _router = None
