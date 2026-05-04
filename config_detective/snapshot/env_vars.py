"""Environment variable capture with PII/secret scrubbing.

This module captures all environment variables from os.environ and redacts
values that look like secrets (API keys, tokens, passwords, etc.) before
they are stored or sent to an LLM.
"""

from __future__ import annotations

import math
import os
import re

from config_detective.snapshot.models import EnvVarEntry


# Keys that are always considered sensitive (case-insensitive matching)
SENSITIVE_KEY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r".*KEY.*", re.IGNORECASE),
    re.compile(r".*SECRET.*", re.IGNORECASE),
    re.compile(r".*TOKEN.*", re.IGNORECASE),
    re.compile(r".*PASSWORD.*", re.IGNORECASE),
    re.compile(r".*PASSWD.*", re.IGNORECASE),
    re.compile(r".*CREDENTIAL.*", re.IGNORECASE),
    re.compile(r".*AUTH.*", re.IGNORECASE),
    re.compile(r".*PRIVATE.*", re.IGNORECASE),
    re.compile(r".*CONNECTION.*STRING.*", re.IGNORECASE),
    re.compile(r".*DSN.*", re.IGNORECASE),
    re.compile(r".*DATABASE.*URL.*", re.IGNORECASE),
    re.compile(r".*MONGO.*URI.*", re.IGNORECASE),
    re.compile(r".*REDIS.*URL.*", re.IGNORECASE),
    re.compile(r".*SUPABASE.*", re.IGNORECASE),
    re.compile(r".*OPENAI.*", re.IGNORECASE),
    re.compile(r".*ANTHROPIC.*", re.IGNORECASE),
    re.compile(r".*GROQ.*", re.IGNORECASE),
    re.compile(r".*HF_.*", re.IGNORECASE),
    re.compile(r".*HUGGING.*FACE.*", re.IGNORECASE),
    re.compile(r".*AWS.*", re.IGNORECASE),
    re.compile(r".*AZURE.*", re.IGNORECASE),
    re.compile(r".*GCP.*", re.IGNORECASE),
    re.compile(r".*GOOGLE.*", re.IGNORECASE),
    re.compile(r".*GITHUB.*TOKEN.*", re.IGNORECASE),
    re.compile(r".*GITLAB.*TOKEN.*", re.IGNORECASE),
    re.compile(r".*SLACK.*", re.IGNORECASE),
    re.compile(r".*STRIPE.*", re.IGNORECASE),
    re.compile(r".*TWILIO.*", re.IGNORECASE),
    re.compile(r".*SENDGRID.*", re.IGNORECASE),
    re.compile(r".*NPM_TOKEN.*", re.IGNORECASE),
    re.compile(r".*PYPI.*TOKEN.*", re.IGNORECASE),
]

# Value patterns that look like secrets regardless of key name
SECRET_VALUE_PATTERNS: list[re.Pattern[str]] = [
    # AWS access key IDs start with AKIA
    re.compile(r"^AKIA[A-Z0-9]{16}$"),
    # GitHub tokens
    re.compile(r"^gh[pousr]_[A-Za-z0-9_]{36,}$"),
    re.compile(r"^github_pat_[A-Za-z0-9_]{22,}$"),
    # Generic base64-ish long strings (likely tokens)
    re.compile(r"^[A-Za-z0-9+/=_-]{40,}$"),
    # JWT-like patterns (three base64 segments separated by dots)
    re.compile(r"^eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"),
    # Bearer tokens
    re.compile(r"^Bearer\s+.+$", re.IGNORECASE),
    # Hex strings that could be API keys (32+ chars)
    re.compile(r"^[a-fA-F0-9]{32,}$"),
]

# Keys to always exclude entirely (not even show redacted)
EXCLUDED_KEYS: set[str] = {
    # These are too noisy and rarely relevant to config bugs
    "LS_COLORS",
    "LESS_TERMCAP_mb",
    "LESS_TERMCAP_md",
    "LESS_TERMCAP_me",
    "LESS_TERMCAP_se",
    "LESS_TERMCAP_so",
    "LESS_TERMCAP_ue",
    "LESS_TERMCAP_us",
}

# Minimum entropy (bits per character) to consider a value possibly random/secret
ENTROPY_THRESHOLD = 3.5

REDACTED_VALUE = "[REDACTED]"


def _calculate_entropy(value: str) -> float:
    """Calculate Shannon entropy of a string in bits per character.

    High entropy (>3.5) suggests random/generated content like tokens.
    """
    if not value:
        return 0.0

    freq: dict[str, int] = {}
    for char in value:
        freq[char] = freq.get(char, 0) + 1

    length = len(value)
    entropy = 0.0
    for count in freq.values():
        prob = count / length
        entropy -= prob * math.log2(prob)

    return entropy


def _is_sensitive_key(key: str) -> bool:
    """Check if the key name suggests a sensitive value."""
    for pattern in SENSITIVE_KEY_PATTERNS:
        if pattern.match(key):
            return True
    return False


def _is_secret_value(value: str) -> bool:
    """Check if the value looks like a secret based on its content."""
    # Skip very short values - unlikely to be secrets
    if len(value) < 10:
        return False

    # Check against known secret patterns
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.match(value):
            return True

    # Check entropy - random strings have high entropy
    if len(value) >= 20 and _calculate_entropy(value) > ENTROPY_THRESHOLD:
        # Additional heuristic: mostly alphanumeric with some special chars
        alnum_ratio = sum(1 for c in value if c.isalnum()) / len(value)
        if alnum_ratio > 0.7:
            return True

    return False


def _should_redact(key: str, value: str) -> bool:
    """Determine if a key-value pair should be redacted."""
    if _is_sensitive_key(key):
        return True
    if _is_secret_value(value):
        return True
    return False


def capture_env_vars(
    env: dict[str, str] | None = None,
    include_redacted: bool = True,
) -> list[EnvVarEntry]:
    """Capture environment variables with PII/secret scrubbing.

    Args:
        env: Environment dict to capture. Defaults to os.environ.
        include_redacted: If False, completely omit sensitive vars instead
            of including them with [REDACTED] values.

    Returns:
        List of EnvVarEntry objects, sorted by key name.
    """
    if env is None:
        env = dict(os.environ)

    entries: list[EnvVarEntry] = []

    for key, value in sorted(env.items()):
        # Skip excluded keys entirely
        if key in EXCLUDED_KEYS:
            continue

        should_redact = _should_redact(key, value)

        if should_redact:
            if include_redacted:
                entries.append(
                    EnvVarEntry(key=key, value=REDACTED_VALUE, redacted=True)
                )
            # If include_redacted is False, we just skip this entry entirely
        else:
            entries.append(EnvVarEntry(key=key, value=value, redacted=False))

    return entries


def get_env_var(key: str, default: str | None = None) -> str | None:
    """Get a single environment variable value (not redacted).

    This is a convenience function for internal use where we need
    the actual value.
    """
    return os.environ.get(key, default)
