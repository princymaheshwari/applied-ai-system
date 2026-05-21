"""Triage node - classifies the error type from the failure trace.

This is the first node in the investigation workflow. It analyzes
the failure trace to determine what category of configuration error
we're dealing with (locale, SSL, timezone, etc.).

The classification helps downstream nodes prioritize deltas and
focus their search on relevant areas.
"""

from __future__ import annotations

import re
from typing import Any

from ..state import ErrorCategory, InvestigationState, InvestigationStatus
from ..trace import NodeTracer, get_trace_store


# Error patterns for classification
ERROR_PATTERNS: dict[ErrorCategory, list[str]] = {
    ErrorCategory.LOCALE: [
        r"UnicodeDecodeError",
        r"UnicodeEncodeError",
        r"'ascii' codec",
        r"'utf-8' codec",
        r"charmap",
        r"codec can't",
        r"LANG",
        r"LC_ALL",
        r"LC_CTYPE",
        r"locale",
        r"encoding",
    ],
    ErrorCategory.SSL: [
        r"SSLError",
        r"SSL:",
        r"CERTIFICATE_VERIFY_FAILED",
        r"certificate verify failed",
        r"ssl\.SSLCertVerificationError",
        r"cryptography",
        r"OpenSSL",
        r"libssl",
        r"OPENSSL_CONF",
        r"SSL_CERT",
    ],
    ErrorCategory.TIMEZONE: [
        r"timezone",
        r"timedelta",
        r"datetime",
        r"tzinfo",
        r"pytz",
        r"zoneinfo",
        r"TZ=",
        r"UTC",
        r"localtime",
    ],
    ErrorCategory.PYTHON_VERSION: [
        r"SyntaxError",
        r"TypeError.*'type'",
        r"AttributeError.*has no attribute",
        r"ModuleNotFoundError",
        r"match.*case",
        r"walrus.*operator",
        r"f-string",
        r"async.*await",
        r"python3\.",
        r"Python \d+\.\d+",
    ],
    ErrorCategory.MISSING_PACKAGE: [
        r"ModuleNotFoundError",
        r"ImportError",
        r"No module named",
        r"cannot import name",
        r"DLL load failed",
        r"shared object",
        r"\.so\.",
        r"libpq",
        r"libssl",
    ],
    ErrorCategory.VERSION_MISMATCH: [
        r"version.*mismatch",
        r"incompatible",
        r"requires.*version",
        r">=\d+\.\d+",
        r"<\d+\.\d+",
        r"ABI",
        r"binary incompatible",
    ],
    ErrorCategory.ENV_VAR: [
        r"environment variable",
        r"env var",
        r"getenv",
        r"os\.environ",
        r"KeyError.*environ",
        r"PATH",
        r"HOME",
    ],
}


def _classify_error(failure_trace: str) -> tuple[ErrorCategory, str, float]:
    """Classify the error based on pattern matching.

    Args:
        failure_trace: The error message/stack trace

    Returns:
        Tuple of (category, matched_pattern, confidence)
    """
    trace_lower = failure_trace.lower()
    best_category = ErrorCategory.UNKNOWN
    best_pattern = ""
    best_score = 0.0

    for category, patterns in ERROR_PATTERNS.items():
        matches = 0
        matched_pattern = ""

        for pattern in patterns:
            if re.search(pattern, failure_trace, re.IGNORECASE):
                matches += 1
                if not matched_pattern:
                    matched_pattern = pattern

        if matches > 0:
            # Score based on number of pattern matches
            score = min(1.0, matches * 0.25)
            if score > best_score:
                best_score = score
                best_category = category
                best_pattern = matched_pattern

    return best_category, best_pattern, best_score


def _extract_error_type(failure_trace: str) -> str:
    """Extract the primary error type from the trace.

    Args:
        failure_trace: The error message/stack trace

    Returns:
        Error type (e.g., "UnicodeDecodeError")
    """
    # Common Python error patterns
    patterns = [
        r"(\w+Error):",
        r"(\w+Exception):",
        r"(\w+Warning):",
        r"Error: (\w+)",
        r"fatal error: (.+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, failure_trace)
        if match:
            return match.group(1)

    # Fallback: first line that looks like an error
    for line in failure_trace.split("\n"):
        if "error" in line.lower() or "exception" in line.lower():
            return line[:100].strip()

    return "Unknown Error"


def _generate_triage_summary(
    category: ErrorCategory,
    error_type: str,
    confidence: float,
) -> str:
    """Generate a human-readable triage summary.

    Args:
        category: The classified error category
        error_type: The extracted error type
        confidence: Classification confidence

    Returns:
        Summary string
    """
    category_descriptions = {
        ErrorCategory.LOCALE: "encoding/locale configuration issue",
        ErrorCategory.SSL: "SSL/TLS certificate or cryptography issue",
        ErrorCategory.TIMEZONE: "timezone configuration issue",
        ErrorCategory.PYTHON_VERSION: "Python version compatibility issue",
        ErrorCategory.MISSING_PACKAGE: "missing system library or package",
        ErrorCategory.VERSION_MISMATCH: "package version incompatibility",
        ErrorCategory.ENV_VAR: "environment variable configuration issue",
        ErrorCategory.UNKNOWN: "unclassified configuration issue",
    }

    desc = category_descriptions.get(category, "configuration issue")
    return f"{error_type} - classified as {desc} (confidence: {confidence:.0%})"


def triage_node(state: InvestigationState) -> dict[str, Any]:
    """Triage node - classifies the error type.

    Args:
        state: Current investigation state

    Returns:
        Updated state fields
    """
    trace_id = state.get("trace_id", "unknown")

    with NodeTracer(trace_id, "triage") as tracer:
        tracer.progress("Analyzing failure trace...")

        failure_trace = state.get("failure_trace", "")

        # Classify the error
        category, matched_pattern, confidence = _classify_error(failure_trace)
        tracer.progress(f"Matched pattern: {matched_pattern}")

        # Extract error type
        error_type = _extract_error_type(failure_trace)
        tracer.progress(f"Error type: {error_type}")

        # Generate summary
        summary = _generate_triage_summary(category, error_type, confidence)
        tracer.set_result({
            "category": category.value,
            "error_type": error_type,
            "confidence": confidence,
        })

        # Add to reasoning chain
        reasoning = [f"Triage: {summary}"]

        return {
            "error_category": category.value,
            "error_type": error_type,
            "triage_summary": summary,
            "status": InvestigationStatus.IN_PROGRESS.value,
            "reasoning_chain": state.get("reasoning_chain", []) + reasoning,
        }
