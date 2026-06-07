"""Resource caps and refusal patterns for the investigation pipeline.

This module enforces hard limits on:
1. **Iteration caps** — max hypothesis/verify/critique loops
2. **Time caps** — wall-clock time budget per investigation
3. **Refusal patterns** — blocks requests to execute arbitrary shell commands

The caps are read from environment variables (with sensible defaults) and
checked at two points:
- Before each orchestrator loop iteration (pre-check)
- Inside the guardrails node (enforced check)
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any

# Default resource caps
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_MAX_WALL_CLOCK_SECONDS = 300
DEFAULT_MAX_HYPOTHESES_PER_ITERATION = 5
DEFAULT_MAX_SANDBOX_RUNS = 10

# --- Refusal patterns ---
# Commands that the agent should never execute or propose as fixes.
# These are shell injection, data exfiltration, and destructive commands.

REFUSAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("arbitrary shell execution", re.compile(
        r"\b(?:os\.system|subprocess\.(?:call|run|Popen)|exec|eval)\s*\(",
        re.IGNORECASE,
    )),
    ("curl/wget data exfiltration", re.compile(
        r"\b(?:curl|wget)\s+.*\s+-[dX]?\s+.*(?:POST|PUT)",
        re.IGNORECASE,
    )),
    ("reverse shell", re.compile(
        r"(?:bash\s+-i|/dev/tcp/|nc\s+-[el]|ncat\s+-|mkfifo)",
        re.IGNORECASE,
    )),
    ("disk wipe / destructive", re.compile(
        r"(?:rm\s+-rf\s+/|mkfs\.|dd\s+if=.*of=/dev/|:>\s*/)",
        re.IGNORECASE,
    )),
    ("chmod 777 root", re.compile(
        r"chmod\s+777\s+/",
        re.IGNORECASE,
    )),
    ("password file access", re.compile(
        r"(?:cat|less|more|head|tail)\s+/etc/(?:shadow|passwd|sudoers)",
        re.IGNORECASE,
    )),
    ("environment dump to network", re.compile(
        r"(?:env|printenv|set)\s*[|>].*(?:curl|wget|nc|netcat)",
        re.IGNORECASE,
    )),
    ("pip install from arbitrary URL", re.compile(
        r"pip\s+install\s+(?:--index-url|--extra-index-url|-i)\s+http",
        re.IGNORECASE,
    )),
]


@dataclass
class LimitsConfig:
    """Resource caps for an investigation."""

    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_wall_clock_seconds: int = DEFAULT_MAX_WALL_CLOCK_SECONDS
    max_hypotheses_per_iteration: int = DEFAULT_MAX_HYPOTHESES_PER_ITERATION
    max_sandbox_runs: int = DEFAULT_MAX_SANDBOX_RUNS

    @classmethod
    def from_env(cls) -> "LimitsConfig":
        """Load from environment variables with defaults."""
        return cls(
            max_iterations=int(os.getenv("MAX_ITERATIONS", str(DEFAULT_MAX_ITERATIONS))),
            max_wall_clock_seconds=int(os.getenv("MAX_INVESTIGATION_SECONDS", str(DEFAULT_MAX_WALL_CLOCK_SECONDS))),
            max_hypotheses_per_iteration=int(os.getenv("MAX_HYPOTHESES_PER_ITERATION", str(DEFAULT_MAX_HYPOTHESES_PER_ITERATION))),
            max_sandbox_runs=int(os.getenv("MAX_SANDBOX_RUNS", str(DEFAULT_MAX_SANDBOX_RUNS))),
        )


@dataclass
class LimitCheckResult:
    """Result of checking resource limits."""

    within_limits: bool = True
    reason: str = ""
    limit_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "within_limits": self.within_limits,
            "reason": self.reason,
            "limit_type": self.limit_type,
        }


def check_iteration_limit(
    current_iteration: int,
    config: LimitsConfig | None = None,
) -> LimitCheckResult:
    """Check if the iteration limit has been reached.

    Args:
        current_iteration: Current iteration number (1-based)
        config: Limits config; uses env-based defaults if None

    Returns:
        LimitCheckResult
    """
    config = config or LimitsConfig.from_env()
    if current_iteration > config.max_iterations:
        return LimitCheckResult(
            within_limits=False,
            reason=f"Iteration limit reached: {current_iteration}/{config.max_iterations}",
            limit_type="iteration",
        )
    return LimitCheckResult()


def check_time_limit(
    start_time_iso: str,
    config: LimitsConfig | None = None,
) -> LimitCheckResult:
    """Check if the wall-clock time limit has been reached.

    Args:
        start_time_iso: ISO timestamp of investigation start
        config: Limits config

    Returns:
        LimitCheckResult
    """
    config = config or LimitsConfig.from_env()

    if not start_time_iso:
        return LimitCheckResult()

    from datetime import datetime
    try:
        start_dt = datetime.fromisoformat(start_time_iso)
        elapsed = (datetime.utcnow() - start_dt).total_seconds()
    except (ValueError, TypeError):
        return LimitCheckResult()

    if elapsed > config.max_wall_clock_seconds:
        return LimitCheckResult(
            within_limits=False,
            reason=f"Time limit reached: {elapsed:.0f}s / {config.max_wall_clock_seconds}s",
            limit_type="time",
        )
    return LimitCheckResult()


def check_refusal_patterns(text: str) -> LimitCheckResult:
    """Check text for dangerous command patterns that should be refused.

    Args:
        text: Any text (fix_code, user message, etc.)

    Returns:
        LimitCheckResult with within_limits=False if a refusal pattern matches
    """
    if not text:
        return LimitCheckResult()

    for label, pattern in REFUSAL_PATTERNS:
        if pattern.search(text):
            return LimitCheckResult(
                within_limits=False,
                reason=f"Refused: detected {label} pattern",
                limit_type="refusal",
            )
    return LimitCheckResult()


def check_all_limits(
    state: dict[str, Any],
    config: LimitsConfig | None = None,
) -> LimitCheckResult:
    """Run all limit checks against the current investigation state.

    Args:
        state: InvestigationState dict
        config: Limits config

    Returns:
        First failing LimitCheckResult, or a passing result if all ok
    """
    config = config or LimitsConfig.from_env()

    # Check iteration
    iteration = state.get("iteration", 0)
    result = check_iteration_limit(iteration, config)
    if not result.within_limits:
        return result

    # Check time
    start_time = state.get("start_time", "")
    result = check_time_limit(start_time, config)
    if not result.within_limits:
        return result

    # Check fix_code in hypotheses for refusal patterns
    hypotheses = state.get("hypotheses", [])
    for h in hypotheses:
        fix_code = h.get("fix_code", "") or ""
        result = check_refusal_patterns(fix_code)
        if not result.within_limits:
            return result

    # Check fix_code in selected hypothesis
    selected = state.get("selected_hypothesis")
    if selected:
        fix_code = selected.get("fix_code", "") or ""
        result = check_refusal_patterns(fix_code)
        if not result.within_limits:
            return result

    return LimitCheckResult()
