"""PII and secret scrubber for LLM-bound text.

Phase 1's env_vars.py scrubs secrets at snapshot capture time. This module
is the **second line of defence**: it scrubs any arbitrary text (failure traces,
user messages, LLM prompts, report outputs) before it is sent to an LLM or
stored in memory. This prevents accidental leakage of API keys, passwords,
tokens, or connection strings that may appear in stack traces, log output,
or user-pasted config snippets.

Three detection strategies, layered:
1. Regex patterns — known token formats (AWS keys, GitHub PATs, JWTs, etc.)
2. Key-value detection — finds KEY=VALUE pairs where the key is sensitive
3. Entropy heuristic — flags long, high-entropy strings as probable secrets
"""

from __future__ import annotations

import math
import re

REDACTED = "[REDACTED]"

# --- Regex patterns for known secret formats ---

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS Access Key", re.compile(r"AKIA[A-Z0-9]{16}")),
    ("AWS Secret Key", re.compile(r"(?:aws_secret_access_key|AWS_SECRET)\s*[=:]\s*\S{20,}")),
    ("GitHub PAT (classic)", re.compile(r"ghp_[A-Za-z0-9]{36,}")),
    ("GitHub PAT (fine-grained)", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("GitHub OAuth", re.compile(r"gho_[A-Za-z0-9]{36,}")),
    ("GitHub User-to-Server", re.compile(r"ghu_[A-Za-z0-9]{36,}")),
    ("GitHub Server-to-Server", re.compile(r"ghs_[A-Za-z0-9]{36,}")),
    ("GitHub Refresh", re.compile(r"ghr_[A-Za-z0-9]{36,}")),
    ("Slack Token", re.compile(r"xox[bporas]-[A-Za-z0-9-]{10,}")),
    ("Slack Webhook", re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_.-]+")),
    ("Bearer Token", re.compile(r"Bearer\s+[A-Za-z0-9_.\-/+=]{20,}")),
    ("Generic API Key Header", re.compile(r"(?:api[_-]?key|apikey)\s*[=:]\s*\S{10,}", re.IGNORECASE)),
    ("Hex Token (32+)", re.compile(r"(?:token|secret|password|key)\s*[=:]\s*[a-fA-F0-9]{32,}", re.IGNORECASE)),
    ("Connection String", re.compile(r"(?:postgres|mysql|mongodb|redis|amqp)://\S+:\S+@\S+")),
    ("Private Key Block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("Base64 Secret (long)", re.compile(r"(?:password|passwd|secret|token)\s*[=:]\s*[A-Za-z0-9+/=]{40,}", re.IGNORECASE)),
]

# --- Key-value pair detection ---

SENSITIVE_KEY_RE = re.compile(
    r"\b("
    r"(?:API_?KEY|SECRET_?KEY|ACCESS_?KEY|AUTH_?TOKEN|BEARER|PASSWORD|PASSWD"
    r"|PRIVATE_?KEY|CREDENTIALS?|CONNECTION_?STRING|DATABASE_?URL|DSN"
    r"|SUPABASE_\w+|GROQ_\w+|HF_TOKEN|OPENAI_\w+|ANTHROPIC_\w+"
    r"|AWS_\w+|AZURE_\w+|GCP_\w+|GOOGLE_\w+|GITHUB_TOKEN"
    r"|SLACK_\w+|STRIPE_\w+|TWILIO_\w+|SENDGRID_\w+|NPM_TOKEN)"
    r")\s*[=:]\s*(\S+)",
    re.IGNORECASE,
)

ENTROPY_THRESHOLD = 3.5
ENTROPY_MIN_LENGTH = 20


def _shannon_entropy(s: str) -> float:
    """Shannon entropy in bits per character."""
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    length = len(s)
    return -sum((cnt / length) * math.log2(cnt / length) for cnt in freq.values())


def _is_high_entropy_secret(token: str) -> bool:
    """Return True if token looks like a random secret."""
    if len(token) < ENTROPY_MIN_LENGTH:
        return False
    if _shannon_entropy(token) < ENTROPY_THRESHOLD:
        return False
    alnum_ratio = sum(1 for c in token if c.isalnum()) / len(token)
    return alnum_ratio > 0.7


def scrub_text(text: str) -> tuple[str, list[str]]:
    """Scrub PII and secrets from arbitrary text.

    Args:
        text: Any string that might contain secrets (failure traces,
              user messages, config snippets, etc.)

    Returns:
        Tuple of (scrubbed_text, list of redaction descriptions).
        The descriptions say *what type* was found but not the value.
    """
    if not text:
        return text, []

    scrubbed = text
    redactions: list[str] = []

    # Pass 1: Known secret format patterns
    for label, pattern in SECRET_PATTERNS:
        matches = list(pattern.finditer(scrubbed))
        for match in reversed(matches):
            scrubbed = scrubbed[:match.start()] + REDACTED + scrubbed[match.end():]
            redactions.append(f"Redacted {label}")

    # Pass 2: Sensitive KEY=VALUE pairs
    for match in reversed(list(SENSITIVE_KEY_RE.finditer(scrubbed))):
        key_name = match.group(1)
        value_start = match.start(2)
        value_end = match.end(2)
        scrubbed = scrubbed[:value_start] + REDACTED + scrubbed[value_end:]
        redactions.append(f"Redacted value for {key_name}")

    # Pass 3: High-entropy tokens not already caught
    words = re.findall(r"\S{20,}", scrubbed)
    for word in words:
        if REDACTED in word:
            continue
        if _is_high_entropy_secret(word):
            scrubbed = scrubbed.replace(word, REDACTED)
            redactions.append("Redacted high-entropy token")

    return scrubbed, redactions


def scrub_dict(data: dict, keys_to_scrub: set[str] | None = None) -> dict:
    """Recursively scrub a dictionary's string values.

    Args:
        data: Dictionary to scrub (not modified in place)
        keys_to_scrub: If provided, only scrub values under these keys.
                       If None, scrub all string values.

    Returns:
        A new dictionary with secrets redacted
    """
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            if keys_to_scrub is None or key in keys_to_scrub:
                result[key], _ = scrub_text(value)
            else:
                result[key] = value
        elif isinstance(value, dict):
            result[key] = scrub_dict(value, keys_to_scrub)
        elif isinstance(value, list):
            result[key] = [
                scrub_dict(item, keys_to_scrub) if isinstance(item, dict)
                else (scrub_text(item)[0] if isinstance(item, str) and (keys_to_scrub is None or key in keys_to_scrub) else item)
                for item in value
            ]
        else:
            result[key] = value
    return result
