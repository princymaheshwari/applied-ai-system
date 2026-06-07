"""Gemini client — fallback reasoning LLM (Gemini 2.0 Flash).

Used when Groq is rate-limited or unavailable. Google's free tier
provides generous quotas for Gemini Flash models.

Note: google-generativeai is deprecated; this module handles the
deprecation warning gracefully. A future migration to google.genai
is recommended.
"""

from __future__ import annotations

import logging
import os
import warnings
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.0-flash"


def _get_model(model_name: str = DEFAULT_MODEL) -> Any | None:
    """Get a Gemini GenerativeModel, or None if unavailable."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key or api_key.startswith("your_"):
        logger.debug("GOOGLE_API_KEY not configured")
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            return genai.GenerativeModel(model_name)
    except Exception as e:
        logger.warning(f"Failed to create Gemini model: {e}")
        return None


def is_available() -> bool:
    """Check whether the Gemini client can be instantiated."""
    return _get_model() is not None


def chat(
    prompt: str,
    model_name: str = DEFAULT_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str | None:
    """Send a generation request to Gemini.

    Args:
        prompt: The full prompt text (Gemini uses a single-turn model)
        model_name: Model identifier
        temperature: Sampling temperature
        max_tokens: Maximum response tokens

    Returns:
        The response text, or None on failure
    """
    model = _get_model(model_name)
    if model is None:
        return None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            import google.generativeai as genai
            config = genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            response = model.generate_content(prompt, generation_config=config)
            if response and response.text:
                return response.text.strip()
            return None
    except Exception as e:
        logger.error(f"Gemini request failed: {e}")
        return None


def generate_explanation(
    delta: dict[str, Any],
    error_type: str,
    failure_trace: str,
) -> str | None:
    """Use Gemini to generate a natural-language explanation for a delta."""
    node_id = delta.get("node_id", "")
    delta_type = delta.get("delta_type", "")
    value_a = delta.get("value_a", "")
    value_b = delta.get("value_b", "")

    prompt = (
        "You are CONFIG DETECTIVE, an expert at diagnosing 'works on my machine' bugs.\n\n"
        f"Error type: {error_type}\n"
        f"Failure trace snippet: {failure_trace[:500]}\n\n"
        f"Configuration difference:\n"
        f"  Item: {node_id}\n"
        f"  Change type: {delta_type}\n"
        f"  Working value: {value_a}\n"
        f"  Failing value: {value_b}\n\n"
        f"Explain concisely (2-4 sentences) why this difference likely causes the observed error. "
        f"Be specific and technical."
    )
    return chat(prompt, temperature=0.3, max_tokens=300)


def generate_fix_suggestion(
    delta: dict[str, Any],
    error_type: str,
    explanation: str,
) -> tuple[str | None, str | None]:
    """Use Gemini to generate a fix suggestion."""
    node_id = delta.get("node_id", "")
    value_a = delta.get("value_a", "")

    prompt = (
        "You are CONFIG DETECTIVE. Given a root-cause explanation, generate:\n"
        "1. A plain-English fix suggestion (one sentence)\n"
        "2. A concrete shell command to apply the fix\n\n"
        "Reply in exactly this format:\n"
        "SUGGESTION: <one sentence>\n"
        "FIX_CODE: <shell command>\n\n"
        f"Root cause: {node_id}\n"
        f"Working value: {value_a}\n"
        f"Error type: {error_type}\n"
        f"Explanation: {explanation}\n\n"
        f"What is the fix?"
    )
    response = chat(prompt, temperature=0.2, max_tokens=200)
    if not response:
        return None, None

    suggestion = None
    fix_code = None
    for line in response.splitlines():
        line = line.strip()
        if line.upper().startswith("SUGGESTION:"):
            suggestion = line.split(":", 1)[1].strip()
        elif line.upper().startswith("FIX_CODE:"):
            fix_code = line.split(":", 1)[1].strip()

    return suggestion, fix_code
