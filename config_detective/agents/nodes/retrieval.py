"""Retrieval node - searches external sources for evidence.

This node uses the Phase 4 multi-source retrieval module to search
GitHub Issues, Stack Overflow, and OSV.dev for relevant information
about the suspected root causes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..state import InvestigationState
from ..trace import NodeTracer


def retrieval_node(state: InvestigationState) -> dict[str, Any]:
    """Search external sources for evidence.

    Args:
        state: Current investigation state

    Returns:
        Updated state fields
    """
    trace_id = state.get("trace_id", "unknown")

    with NodeTracer(trace_id, "retrieval") as tracer:
        tracer.progress("Preparing search queries...")

        # Import here to avoid circular imports
        from config_detective.retrieval import search_all_sources

        # Build search context
        failure_trace = state.get("failure_trace", "")
        error_type = state.get("error_type", "")
        top_deltas = state.get("top_deltas", [])
        packages_with_deltas = state.get("packages_with_deltas", [])

        # Build failure signature for search
        failure_signature = f"{error_type}: {failure_trace[:300]}"

        # Prepare delta items for search
        delta_items = []
        for delta in top_deltas[:5]:  # Limit to top 5
            delta_items.append({
                "node_id": delta.get("node_id", ""),
                "delta_type": delta.get("delta_type", ""),
                "value_a": delta.get("value_a", ""),
                "value_b": delta.get("value_b", ""),
            })

        tracer.progress(f"Searching with {len(delta_items)} deltas and {len(packages_with_deltas)} packages...")

        # Run async search
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're already in an async context, create a new event loop
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        search_all_sources(
                            failure_signature=failure_signature,
                            packages=packages_with_deltas[:5],
                            delta_items=delta_items,
                        )
                    )
                    results = future.result(timeout=60)
            else:
                results = loop.run_until_complete(
                    search_all_sources(
                        failure_signature=failure_signature,
                        packages=packages_with_deltas[:5],
                        delta_items=delta_items,
                    )
                )
        except RuntimeError:
            results = asyncio.run(
                search_all_sources(
                    failure_signature=failure_signature,
                    packages=packages_with_deltas[:5],
                    delta_items=delta_items,
                )
            )

        tracer.progress(f"Found {len(results)} pieces of external evidence")

        # Serialize results
        evidence_serialized = []
        for ev in results[:20]:  # Limit stored evidence
            evidence_serialized.append({
                "id": ev.id,
                "source": ev.source.value if hasattr(ev.source, "value") else str(ev.source),
                "evidence_type": ev.evidence_type.value if hasattr(ev.evidence_type, "value") else str(ev.evidence_type),
                "title": ev.title,
                "content": ev.content[:500] if ev.content else "",
                "url": ev.url,
                "relevance_score": ev.relevance_score,
            })

        # Categorize evidence
        github_count = sum(1 for e in evidence_serialized if e["source"] == "github")
        so_count = sum(1 for e in evidence_serialized if e["source"] == "stackoverflow")
        osv_count = sum(1 for e in evidence_serialized if e["source"] == "osv")

        tracer.set_result({
            "total_evidence": len(evidence_serialized),
            "github": github_count,
            "stackoverflow": so_count,
            "osv": osv_count,
        })

        # Build reasoning
        reasoning_parts = []
        if evidence_serialized:
            top_evidence = evidence_serialized[0]
            reasoning_parts.append(
                f"Retrieval: Found {len(evidence_serialized)} pieces of evidence. "
                f"Top result from {top_evidence['source']}: '{top_evidence['title'][:50]}...'"
            )
        else:
            reasoning_parts.append("Retrieval: No relevant external evidence found")

        if osv_count > 0:
            reasoning_parts.append(f"Retrieval: {osv_count} CVE/vulnerability matches found")

        return {
            "external_evidence": evidence_serialized,
            "reasoning_chain": state.get("reasoning_chain", []) + reasoning_parts,
        }
