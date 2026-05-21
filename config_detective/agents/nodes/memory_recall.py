"""Memory recall node - queries past cases and patterns from memory.

This node uses the Phase 3 Memory RAG module to find similar past
investigations and known patterns that might help diagnose the
current issue.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..state import InvestigationState
from ..trace import NodeTracer


def memory_recall_node(state: InvestigationState) -> dict[str, Any]:
    """Query memory for similar cases and patterns.

    Args:
        state: Current investigation state

    Returns:
        Updated state fields
    """
    trace_id = state.get("trace_id", "unknown")

    with NodeTracer(trace_id, "memory") as tracer:
        tracer.progress("Checking memory availability...")

        # Import here to avoid circular imports
        from config_detective.memory import (
            memory_available,
            retrieve_similar_cases,
            get_patterns,
        )

        if not memory_available():
            tracer.warning("Memory system unavailable (Supabase not configured)")
            return {
                "similar_cases": [],
                "reasoning_chain": state.get("reasoning_chain", []) + [
                    "Memory Recall: Skipped - Supabase not configured"
                ],
            }

        # Build search context from state
        failure_trace = state.get("failure_trace", "")
        error_category = state.get("error_category", "unknown")
        top_deltas = state.get("top_deltas", [])

        # Build failure signature
        failure_signature = failure_trace[:500]  # Truncate for embedding

        tracer.progress("Searching for similar past cases...")

        # Run async function in sync context
        try:
            similar_cases = asyncio.get_event_loop().run_until_complete(
                retrieve_similar_cases(
                    failure_signature=failure_signature,
                    limit=5,
                )
            )
        except RuntimeError:
            # No event loop running, create one
            similar_cases = asyncio.run(
                retrieve_similar_cases(
                    failure_signature=failure_signature,
                    limit=5,
                )
            )

        tracer.progress(f"Found {len(similar_cases)} similar cases")

        # Also check for known patterns
        tracer.progress(f"Checking patterns for category: {error_category}")

        try:
            patterns = asyncio.get_event_loop().run_until_complete(
                get_patterns(category=error_category, limit=3)
            )
        except RuntimeError:
            patterns = asyncio.run(
                get_patterns(category=error_category, limit=3)
            )

        tracer.progress(f"Found {len(patterns)} relevant patterns")

        # Serialize results
        similar_cases_serialized = []
        for case in similar_cases:
            similar_cases_serialized.append({
                "case_id": case.case_id,
                "failure_signature": case.failure_signature[:200],
                "root_cause": case.root_cause,
                "fix_applied": case.fix_applied,
                "similarity": case.similarity,
            })

        # Build reasoning
        if similar_cases:
            best_match = similar_cases[0]
            reasoning = [
                f"Memory Recall: Found {len(similar_cases)} similar cases. "
                f"Best match ({best_match.similarity:.0%} similarity): "
                f"root cause was '{best_match.root_cause}'"
            ]
        else:
            reasoning = ["Memory Recall: No similar past cases found"]

        if patterns:
            reasoning.append(
                f"Memory Recall: Found {len(patterns)} known patterns for {error_category}"
            )

        tracer.set_result({
            "similar_cases": len(similar_cases),
            "patterns": len(patterns),
        })

        return {
            "similar_cases": similar_cases_serialized,
            "reasoning_chain": state.get("reasoning_chain", []) + reasoning,
        }
