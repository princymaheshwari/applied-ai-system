"""LangGraph orchestrator - assembles nodes into an investigation workflow.

This module creates the LangGraph state machine that orchestrates the
entire CONFIG DETECTIVE investigation flow:

    Triage -> Build_Graph -> Diff -> Memory_Recall -> Retrieval ->
    Hypothesize -> Verify_in_Sandbox -> Critique -> [loop or finalize] -> Report

Features:
- Conditional routing based on confidence scores
- Iteration limits to prevent infinite loops
- Observable trace events at each step
- Graceful degradation when components are unavailable
- Sandbox verification of candidate fixes (Docker or subprocess fallback)
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from .nodes import (
    triage_node,
    graph_builder_node,
    differ_node,
    memory_recall_node,
    retrieval_node,
    hypothesizer_node,
    verifier_node,
    critic_node,
    reporter_node,
)
from .state import InvestigationState, InvestigationStatus
from .trace import emit_event, EventType, get_trace_store

logger = logging.getLogger(__name__)


def should_continue(state: InvestigationState) -> Literal["hypothesizer", "reporter"]:
    """Routing function: decide whether to loop or finalize.

    Args:
        state: Current investigation state

    Returns:
        "hypothesizer" to loop back, "reporter" to finalize
    """
    should_loop = state.get("should_continue", False)
    iteration = state.get("iteration", 0)
    max_iterations = state.get("max_iterations", 3)

    # Safety check: don't exceed max iterations
    if iteration >= max_iterations:
        return "reporter"

    if should_loop:
        logger.info(f"Looping back to hypothesizer (iteration {iteration})")
        return "hypothesizer"
    else:
        return "reporter"


def create_investigation_graph() -> StateGraph:
    """Create the LangGraph state machine for investigations.

    Returns:
        Compiled StateGraph
    """
    # Create the graph with our state schema
    workflow = StateGraph(InvestigationState)

    # Add all nodes
    workflow.add_node("triage", triage_node)
    workflow.add_node("graph_builder", graph_builder_node)
    workflow.add_node("differ", differ_node)
    workflow.add_node("memory_recall", memory_recall_node)
    workflow.add_node("retrieval", retrieval_node)
    workflow.add_node("hypothesizer", hypothesizer_node)
    workflow.add_node("verifier", verifier_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("reporter", reporter_node)

    # Define the flow
    # Start with triage
    workflow.set_entry_point("triage")

    # Linear flow through analysis
    workflow.add_edge("triage", "graph_builder")
    workflow.add_edge("graph_builder", "differ")
    workflow.add_edge("differ", "memory_recall")
    workflow.add_edge("memory_recall", "retrieval")
    workflow.add_edge("retrieval", "hypothesizer")
    workflow.add_edge("hypothesizer", "verifier")
    workflow.add_edge("verifier", "critic")

    # Conditional edge from critic
    workflow.add_conditional_edges(
        "critic",
        should_continue,
        {
            "hypothesizer": "hypothesizer",  # Loop back
            "reporter": "reporter",  # Finalize
        },
    )

    # Reporter is the end
    workflow.add_edge("reporter", END)

    return workflow


# Compiled graph singleton
_compiled_graph: Any = None


def get_compiled_graph():
    """Get the compiled investigation graph.

    Returns:
        Compiled LangGraph
    """
    global _compiled_graph
    if _compiled_graph is None:
        workflow = create_investigation_graph()
        _compiled_graph = workflow.compile()
    return _compiled_graph


def reset_compiled_graph() -> None:
    """Reset the compiled graph (for testing)."""
    global _compiled_graph
    _compiled_graph = None


async def run_investigation_async(
    initial_state: InvestigationState,
) -> InvestigationState:
    """Run an investigation asynchronously.

    Args:
        initial_state: The initial state with snapshots and failure trace

    Returns:
        Final investigation state with report
    """
    graph = get_compiled_graph()
    trace_id = initial_state.get("trace_id", "unknown")

    # Emit start event
    emit_event(
        trace_id=trace_id,
        node_name="orchestrator",
        event_type=EventType.START,
        message="Investigation started",
        data={"trace_id": trace_id},
    )

    try:
        # Run the graph
        final_state = await graph.ainvoke(initial_state)

        # Emit completion event
        emit_event(
            trace_id=trace_id,
            node_name="orchestrator",
            event_type=EventType.COMPLETE,
            message="Investigation completed",
            data={
                "status": final_state.get("status"),
                "confidence": final_state.get("confidence"),
            },
        )

        return final_state

    except Exception as e:
        logger.exception("Investigation failed")

        # Emit error event
        emit_event(
            trace_id=trace_id,
            node_name="orchestrator",
            event_type=EventType.ERROR,
            message=f"Investigation failed: {e}",
        )

        # Return error state
        return InvestigationState(
            **initial_state,
            status=InvestigationStatus.FAILED.value,
            reasoning_chain=initial_state.get("reasoning_chain", []) + [
                f"Orchestrator: Investigation failed with error: {e}"
            ],
        )


def run_investigation_sync(
    initial_state: InvestigationState,
) -> InvestigationState:
    """Run an investigation synchronously.

    Args:
        initial_state: The initial state with snapshots and failure trace

    Returns:
        Final investigation state with report
    """
    graph = get_compiled_graph()
    trace_id = initial_state.get("trace_id", "unknown")

    # Emit start event
    emit_event(
        trace_id=trace_id,
        node_name="orchestrator",
        event_type=EventType.START,
        message="Investigation started",
        data={"trace_id": trace_id},
    )

    try:
        # Run the graph synchronously
        final_state = graph.invoke(initial_state)

        # Emit completion event
        emit_event(
            trace_id=trace_id,
            node_name="orchestrator",
            event_type=EventType.COMPLETE,
            message="Investigation completed",
            data={
                "status": final_state.get("status"),
                "confidence": final_state.get("confidence"),
            },
        )

        return final_state

    except Exception as e:
        logger.exception("Investigation failed")

        # Emit error event
        emit_event(
            trace_id=trace_id,
            node_name="orchestrator",
            event_type=EventType.ERROR,
            message=f"Investigation failed: {e}",
        )

        # Return error state
        error_state = dict(initial_state)
        error_state["status"] = InvestigationStatus.FAILED.value
        error_state["reasoning_chain"] = initial_state.get("reasoning_chain", []) + [
            f"Orchestrator: Investigation failed with error: {e}"
        ]
        return InvestigationState(**error_state)


async def stream_investigation(
    initial_state: InvestigationState,
):
    """Stream investigation progress as it runs.

    This is an async generator that yields state updates after each node.

    Args:
        initial_state: The initial state

    Yields:
        Updated state after each node execution
    """
    graph = get_compiled_graph()
    trace_id = initial_state.get("trace_id", "unknown")

    emit_event(
        trace_id=trace_id,
        node_name="orchestrator",
        event_type=EventType.START,
        message="Investigation started (streaming)",
    )

    try:
        async for state_update in graph.astream(initial_state):
            # state_update is a dict with node name as key
            for node_name, node_output in state_update.items():
                yield {
                    "node": node_name,
                    "output": node_output,
                }

    except Exception as e:
        logger.exception("Streaming investigation failed")
        emit_event(
            trace_id=trace_id,
            node_name="orchestrator",
            event_type=EventType.ERROR,
            message=f"Investigation failed: {e}",
        )
        raise


def get_graph_visualization() -> str:
    """Get a Mermaid diagram of the investigation graph.

    Returns:
        Mermaid diagram string
    """
    return """
graph TD
    START((Start)) --> triage
    triage[Triage] --> graph_builder[Graph Builder]
    graph_builder --> differ[Differ]
    differ --> memory_recall[Memory Recall]
    memory_recall --> retrieval[Retrieval]
    retrieval --> hypothesizer[Hypothesizer]
    hypothesizer --> verifier[Sandbox Verifier]
    verifier --> critic[Critic]
    critic -->|confidence >= threshold| reporter[Reporter]
    critic -->|confidence < threshold| hypothesizer
    reporter --> END((End))

    style triage fill:#e1f5fe
    style graph_builder fill:#e1f5fe
    style differ fill:#e1f5fe
    style memory_recall fill:#fff3e0
    style retrieval fill:#fff3e0
    style hypothesizer fill:#e8f5e9
    style verifier fill:#e8f5e9
    style critic fill:#fce4ec
    style reporter fill:#f3e5f5
"""
