"""Groq client — primary reasoning LLM (Llama 3.3 70B).

Groq's free tier provides fast inference for Llama models. This module
wraps the Groq chat completions API with:
- Retry logic for rate-limit (429) errors
- Structured prompt templates for investigation tasks
- Graceful fallback when the API key is missing or the service is down
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama-3.3-70b-versatile"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2.0


def _get_client() -> Any | None:
    """Get a Groq client, or None if unavailable."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key.startswith("your_"):
        logger.debug("GROQ_API_KEY not configured")
        return None
    try:
        from groq import Groq
        return Groq(api_key=api_key)
    except Exception as e:
        logger.warning(f"Failed to create Groq client: {e}")
        return None


def is_available() -> bool:
    """Check whether the Groq client can be instantiated."""
    return _get_client() is not None


def chat(
    messages: list[dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str | None:
    """Send a chat completion request to Groq.

    Args:
        messages: OpenAI-format message list
        model: Model identifier
        temperature: Sampling temperature
        max_tokens: Maximum response tokens

    Returns:
        The assistant's response text, or None on failure
    """
    client = _get_client()
    if client is None:
        return None

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content
            return content.strip() if content else None
        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            if "429" in error_str or "rate" in error_str:
                wait = RETRY_DELAY_SECONDS * attempt
                logger.warning(f"Groq rate-limited, retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
            else:
                logger.error(f"Groq request failed: {e}")
                return None

    logger.error(f"Groq exhausted retries: {last_error}")
    return None


def generate_explanation(
    delta: dict[str, Any],
    error_type: str,
    failure_trace: str,
) -> str | None:
    """Use the LLM to generate a natural-language explanation for a delta.

    Args:
        delta: The suspected root-cause delta
        error_type: The classified error type
        failure_trace: The failure trace snippet

    Returns:
        Explanation string, or None if LLM unavailable
    """
    node_id = delta.get("node_id", "")
    delta_type = delta.get("delta_type", "")
    value_a = delta.get("value_a", "")
    value_b = delta.get("value_b", "")

    messages = [
        {
            "role": "system",
            "content": (
                "You are CONFIG DETECTIVE, an expert at diagnosing 'works on my machine' bugs. "
                "Given a configuration difference between a working and failing environment, "
                "explain concisely (2-4 sentences) why this difference likely causes the observed error. "
                "Be specific and technical."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Error type: {error_type}\n"
                f"Failure trace snippet: {failure_trace[:500]}\n\n"
                f"Configuration difference:\n"
                f"  Item: {node_id}\n"
                f"  Change type: {delta_type}\n"
                f"  Working value: {value_a}\n"
                f"  Failing value: {value_b}\n\n"
                f"Why does this difference cause the error?"
            ),
        },
    ]
    return chat(messages, temperature=0.3, max_tokens=300)


def generate_fix_suggestion(
    delta: dict[str, Any],
    error_type: str,
    explanation: str,
) -> tuple[str | None, str | None]:
    """Use the LLM to generate a fix suggestion and concrete fix code.

    Args:
        delta: The suspected root-cause delta
        error_type: The classified error type
        explanation: The previously generated explanation

    Returns:
        Tuple of (fix_suggestion, fix_code), or (None, None) if LLM unavailable
    """
    node_id = delta.get("node_id", "")
    value_a = delta.get("value_a", "")

    messages = [
        {
            "role": "system",
            "content": (
                "You are CONFIG DETECTIVE. Given a root-cause explanation, generate:\n"
                "1. A plain-English fix suggestion (one sentence)\n"
                "2. A concrete shell command to apply the fix\n\n"
                "Reply in exactly this format:\n"
                "SUGGESTION: <one sentence>\n"
                "FIX_CODE: <shell command>"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Root cause: {node_id}\n"
                f"Working value: {value_a}\n"
                f"Error type: {error_type}\n"
                f"Explanation: {explanation}\n\n"
                f"What is the fix?"
            ),
        },
    ]
    response = chat(messages, temperature=0.2, max_tokens=200)
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


def critique_hypothesis(
    hypothesis: dict[str, Any],
    deltas: list[dict[str, Any]],
    failure_trace: str,
) -> dict[str, Any] | None:
    """Use the LLM to critique a hypothesis.

    Args:
        hypothesis: The hypothesis to critique
        deltas: All environment deltas
        failure_trace: The failure trace

    Returns:
        Dict with 'is_plausible', 'reasoning', 'adjusted_confidence', or None
    """
    delta_summary = "\n".join(
        f"  - {d.get('node_id')}: {d.get('delta_type')} ({d.get('value_a', '?')} -> {d.get('value_b', '?')})"
        for d in deltas[:10]
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are CONFIG DETECTIVE's critic. Evaluate whether a hypothesis about a config bug's "
                "root cause is plausible. Reply in exactly this format:\n"
                "PLAUSIBLE: yes or no\n"
                "REASONING: <one sentence>\n"
                "CONFIDENCE: <float 0.0-1.0>"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Failure trace: {failure_trace[:400]}\n\n"
                f"Available deltas:\n{delta_summary}\n\n"
                f"Hypothesis:\n"
                f"  Root cause: {hypothesis.get('delta_id')}\n"
                f"  Explanation: {hypothesis.get('explanation', '')[:200]}\n"
                f"  Fix: {hypothesis.get('fix_code', 'N/A')}\n"
                f"  Initial confidence: {hypothesis.get('confidence', 0):.0%}\n\n"
                f"Is this hypothesis plausible?"
            ),
        },
    ]
    response = chat(messages, temperature=0.2, max_tokens=200)
    if not response:
        return None

    result: dict[str, Any] = {"is_plausible": True, "reasoning": "", "adjusted_confidence": 0.5}
    for line in response.splitlines():
        line = line.strip()
        if line.upper().startswith("PLAUSIBLE:"):
            val = line.split(":", 1)[1].strip().lower()
            result["is_plausible"] = val in ("yes", "true", "1")
        elif line.upper().startswith("REASONING:"):
            result["reasoning"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("CONFIDENCE:"):
            try:
                result["adjusted_confidence"] = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass

    return result
