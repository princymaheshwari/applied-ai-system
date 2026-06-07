"""HuggingFace code LLM — Qwen2.5-Coder-32B for code understanding.

Used for:
- Analysing code snippets in failure traces
- Understanding Dockerfile semantics
- Interpreting pip/requirements.txt patterns
- Generating targeted fix code from natural-language descriptions

Uses the free HuggingFace Inference API (same HF_TOKEN as embeddings).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"


def _get_client() -> Any | None:
    """Get a HuggingFace InferenceClient, or None if unavailable."""
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if not token or token.startswith("your_"):
        logger.debug("HF_TOKEN not configured for code LLM")
        return None
    try:
        from huggingface_hub import InferenceClient
        return InferenceClient(token=token)
    except Exception as e:
        logger.warning(f"Failed to create HF InferenceClient: {e}")
        return None


def is_available() -> bool:
    """Check whether the HF code client can be instantiated."""
    return _get_client() is not None


def chat(
    messages: list[dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> str | None:
    """Send a chat completion request to HuggingFace Inference.

    Args:
        messages: OpenAI-format message list
        model: Model identifier
        temperature: Sampling temperature
        max_tokens: Maximum response tokens

    Returns:
        The response text, or None on failure
    """
    client = _get_client()
    if client is None:
        return None

    try:
        response = client.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content
        return content.strip() if content else None
    except Exception as e:
        logger.error(f"HF code LLM request failed: {e}")
        return None


def analyse_failure_trace(
    trace: str,
    context: str = "",
) -> str | None:
    """Use Qwen2.5-Coder to analyse a failure trace and extract key insights.

    Args:
        trace: The failure trace / stack trace
        context: Additional context (e.g. suspected config item)

    Returns:
        Analysis text, or None if unavailable
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are a code debugging expert. Analyse the following failure trace and "
                "identify: (1) the exact error type, (2) which configuration or dependency "
                "most likely caused it, (3) a one-line fix suggestion. Be concise."
            ),
        },
        {
            "role": "user",
            "content": f"Failure trace:\n```\n{trace[:1500]}\n```\n\n{context}",
        },
    ]
    return chat(messages, temperature=0.1, max_tokens=400)


def generate_fix_code(
    description: str,
    target_file_type: str = "generic",
) -> str | None:
    """Use Qwen2.5-Coder to generate concrete fix code.

    Args:
        description: Natural-language description of the desired fix
        target_file_type: Type of file being fixed (Dockerfile, requirements.txt, etc.)

    Returns:
        Fix code snippet, or None if unavailable
    """
    messages = [
        {
            "role": "system",
            "content": (
                f"You are a code generation expert. Generate a single-line shell command or "
                f"config file change to fix the described issue in a {target_file_type}. "
                f"Reply with ONLY the command or config line, no explanation."
            ),
        },
        {
            "role": "user",
            "content": description,
        },
    ]
    return chat(messages, temperature=0.1, max_tokens=100)
