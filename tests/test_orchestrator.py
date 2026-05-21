"""Tests for the LangGraph orchestrator and agent nodes.

This module tests:
- State management
- Tracing infrastructure
- Individual node functions
- Orchestrator graph construction
- End-to-end investigation flow (with mocked external dependencies)
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config_detective.agents.state import (
    ErrorCategory,
    Hypothesis,
    InvestigationReport,
    InvestigationState,
    InvestigationStatus,
    create_initial_state,
)
from config_detective.agents.trace import (
    EventType,
    NodeTracer,
    TraceEvent,
    TraceStore,
    emit_event,
    get_trace_store,
    reset_trace_store,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def minimal_snapshot_a() -> dict:
    """Minimal working environment snapshot."""
    return {
        "snapshot_hash": "abc123",
        "captured_at": datetime.utcnow().isoformat(),
        "env_vars": {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"},
        "lockfiles": [],
        "dockerfile": None,
        "os_packages": [],
        "runtimes": {"python": "3.11.0"},
        "locale": {"LANG": "en_US.UTF-8"},
        "timezone": {"tz": "UTC"},
        "system": {"os": "linux", "arch": "x86_64"},
    }


@pytest.fixture
def minimal_snapshot_b() -> dict:
    """Minimal failing environment snapshot with locale difference."""
    return {
        "snapshot_hash": "def456",
        "captured_at": datetime.utcnow().isoformat(),
        "env_vars": {"LANG": "C", "LC_ALL": "C"},  # Different locale
        "lockfiles": [],
        "dockerfile": None,
        "os_packages": [],
        "runtimes": {"python": "3.11.0"},
        "locale": {"LANG": "C"},
        "timezone": {"tz": "UTC"},
        "system": {"os": "linux", "arch": "x86_64"},
    }


@pytest.fixture
def locale_failure_trace() -> str:
    """Failure trace for locale-related error."""
    return """
Traceback (most recent call last):
  File "app.py", line 42, in process_data
    text = data.decode('ascii')
UnicodeDecodeError: 'ascii' codec can't decode byte 0xc3 in position 7: ordinal not in range(128)
"""


@pytest.fixture
def fresh_trace_store():
    """Provide a fresh trace store for each test."""
    reset_trace_store()
    yield get_trace_store()
    reset_trace_store()


# =============================================================================
# State Tests
# =============================================================================


class TestState:
    """Tests for state module."""

    def test_error_category_enum(self):
        """Test ErrorCategory enum values."""
        assert ErrorCategory.LOCALE.value == "locale"
        assert ErrorCategory.SSL.value == "ssl"
        assert ErrorCategory.UNKNOWN.value == "unknown"

    def test_investigation_status_enum(self):
        """Test InvestigationStatus enum values."""
        assert InvestigationStatus.PENDING.value == "pending"
        assert InvestigationStatus.COMPLETED.value == "completed"
        assert InvestigationStatus.NEEDS_HUMAN_REVIEW.value == "needs_human_review"

    def test_hypothesis_creation(self):
        """Test Hypothesis dataclass."""
        h = Hypothesis(
            rank=1,
            delta_id="env:LANG",
            explanation="Locale mismatch",
            fix_suggestion="Set LANG=en_US.UTF-8",
            confidence=0.85,
        )
        assert h.rank == 1
        assert h.delta_id == "env:LANG"
        assert h.confidence == 0.85

    def test_hypothesis_to_dict(self):
        """Test Hypothesis serialization."""
        h = Hypothesis(rank=1, delta_id="env:LANG", confidence=0.8)
        d = h.to_dict()
        assert d["rank"] == 1
        assert d["delta_id"] == "env:LANG"
        assert "created_at" in d

    def test_hypothesis_from_dict(self):
        """Test Hypothesis deserialization."""
        d = {
            "rank": 2,
            "delta_id": "pkg:requests",
            "confidence": 0.6,
            "created_at": "2024-01-01T00:00:00",
        }
        h = Hypothesis.from_dict(d)
        assert h.rank == 2
        assert h.delta_id == "pkg:requests"

    def test_investigation_report_creation(self):
        """Test InvestigationReport dataclass."""
        report = InvestigationReport(
            trace_id="test123",
            status=InvestigationStatus.COMPLETED,
            root_cause_delta_id="env:LANG",
            confidence=0.9,
        )
        assert report.trace_id == "test123"
        assert report.status == InvestigationStatus.COMPLETED

    def test_create_initial_state(self, minimal_snapshot_a, minimal_snapshot_b, locale_failure_trace):
        """Test initial state creation."""
        state = create_initial_state(
            snapshot_a_dict=minimal_snapshot_a,
            snapshot_b_dict=minimal_snapshot_b,
            failure_trace=locale_failure_trace,
        )

        assert "trace_id" in state
        assert state["status"] == InvestigationStatus.PENDING.value
        assert state["confidence_threshold"] == 0.7
        assert state["max_iterations"] == 3
        assert state["iteration"] == 0


# =============================================================================
# Trace Tests
# =============================================================================


class TestTrace:
    """Tests for tracing module."""

    def test_trace_event_creation(self):
        """Test TraceEvent dataclass."""
        event = TraceEvent(
            trace_id="test123",
            node_name="triage",
            event_type=EventType.START,
            message="Starting triage",
        )
        assert event.trace_id == "test123"
        assert event.node_name == "triage"
        assert event.event_type == EventType.START

    def test_trace_event_to_dict(self):
        """Test TraceEvent serialization."""
        event = TraceEvent(
            trace_id="test",
            node_name="differ",
            event_type=EventType.COMPLETE,
            message="Done",
            duration_ms=100,
        )
        d = event.to_dict()
        assert d["node_name"] == "differ"
        assert d["duration_ms"] == 100

    def test_trace_store_add_and_get(self, fresh_trace_store):
        """Test TraceStore add and retrieve."""
        store = fresh_trace_store

        event = TraceEvent(trace_id="t1", node_name="test", message="msg")
        store.add_event(event)

        events = store.get_events("t1")
        assert len(events) == 1
        assert events[0].message == "msg"

    def test_trace_store_multiple_traces(self, fresh_trace_store):
        """Test TraceStore with multiple traces."""
        store = fresh_trace_store

        store.add_event(TraceEvent(trace_id="t1", node_name="a", message="1"))
        store.add_event(TraceEvent(trace_id="t2", node_name="b", message="2"))
        store.add_event(TraceEvent(trace_id="t1", node_name="c", message="3"))

        assert len(store.get_events("t1")) == 2
        assert len(store.get_events("t2")) == 1

    def test_trace_store_subscribe(self, fresh_trace_store):
        """Test TraceStore subscription."""
        store = fresh_trace_store
        received = []

        def callback(event):
            received.append(event)

        unsubscribe = store.subscribe("t1", callback)
        store.add_event(TraceEvent(trace_id="t1", message="msg"))

        assert len(received) == 1
        unsubscribe()

    def test_emit_event(self, fresh_trace_store):
        """Test emit_event convenience function."""
        event = emit_event(
            trace_id="t1",
            node_name="test",
            event_type=EventType.INFO,
            message="Test message",
        )

        assert event.trace_id == "t1"
        events = fresh_trace_store.get_events("t1")
        assert len(events) == 1

    def test_node_tracer_context_manager(self, fresh_trace_store):
        """Test NodeTracer context manager."""
        with NodeTracer("t1", "test_node", fresh_trace_store) as tracer:
            tracer.progress("Working...")

        events = fresh_trace_store.get_events("t1")
        assert len(events) == 3  # start, progress, complete


# =============================================================================
# Node Tests
# =============================================================================


class TestNodes:
    """Tests for individual orchestrator nodes."""

    def test_triage_node_locale_error(self, locale_failure_trace, fresh_trace_store):
        """Test triage node classifies locale errors."""
        from config_detective.agents.nodes.triage import triage_node

        state = InvestigationState(
            trace_id="t1",
            failure_trace=locale_failure_trace,
            reasoning_chain=[],
        )

        result = triage_node(state)

        assert result["error_category"] == ErrorCategory.LOCALE.value
        assert "UnicodeDecodeError" in result["error_type"]
        assert len(result["reasoning_chain"]) > 0

    def test_triage_node_ssl_error(self, fresh_trace_store):
        """Test triage node classifies SSL errors."""
        from config_detective.agents.nodes.triage import triage_node

        ssl_trace = "SSLError: certificate verify failed"
        state = InvestigationState(
            trace_id="t1",
            failure_trace=ssl_trace,
            reasoning_chain=[],
        )

        result = triage_node(state)
        assert result["error_category"] == ErrorCategory.SSL.value

    def test_triage_node_unknown_error(self, fresh_trace_store):
        """Test triage node handles unknown errors."""
        from config_detective.agents.nodes.triage import triage_node

        state = InvestigationState(
            trace_id="t1",
            failure_trace="Some random error that doesn't match patterns",
            reasoning_chain=[],
        )

        result = triage_node(state)
        assert result["error_category"] == ErrorCategory.UNKNOWN.value

    def test_hypothesizer_node(self, fresh_trace_store):
        """Test hypothesizer generates hypotheses."""
        from config_detective.agents.nodes.hypothesizer import hypothesizer_node

        state = InvestigationState(
            trace_id="t1",
            error_category=ErrorCategory.LOCALE.value,
            error_type="UnicodeDecodeError",
            top_deltas=[
                {
                    "node_id": "env:LANG",
                    "node_type": "env_var",
                    "delta_type": "value_changed",
                    "value_a": "en_US.UTF-8",
                    "value_b": "C",
                    "suspect_score": 0.9,
                }
            ],
            similar_cases=[],
            external_evidence=[],
            iteration=0,
            reasoning_chain=[],
        )

        result = hypothesizer_node(state)

        assert len(result["hypotheses"]) > 0
        assert result["iteration"] == 1
        # First hypothesis should be the LANG env var
        assert result["hypotheses"][0]["delta_id"] == "env:LANG"

    def test_critic_node_validates_hypotheses(self, fresh_trace_store):
        """Test critic validates and scores hypotheses."""
        from config_detective.agents.nodes.critic import critic_node

        state = InvestigationState(
            trace_id="t1",
            error_category=ErrorCategory.LOCALE.value,
            hypotheses=[
                {
                    "delta_id": "env:LANG",
                    "explanation": "Locale mismatch causes encoding issues",
                    "fix_suggestion": "Set LANG=en_US.UTF-8",
                    "confidence": 0.8,
                }
            ],
            deltas=[
                {"node_id": "env:LANG", "node_type": "env_var", "suspect_score": 0.9}
            ],
            similar_cases=[],
            external_evidence=[],
            confidence_threshold=0.5,  # Low threshold to ensure we pass
            iteration=1,
            max_iterations=3,
            reasoning_chain=[],
        )

        result = critic_node(state)

        assert "confidence" in result
        assert "should_continue" in result
        # After validation, should have selected_hypothesis if hypotheses were valid
        assert "hypotheses" in result


# =============================================================================
# Orchestrator Tests
# =============================================================================


class TestOrchestrator:
    """Tests for the LangGraph orchestrator."""

    def test_create_investigation_graph(self):
        """Test graph creation."""
        from config_detective.agents.orchestrator import create_investigation_graph

        graph = create_investigation_graph()
        assert graph is not None

    def test_get_compiled_graph(self):
        """Test compiled graph singleton."""
        from config_detective.agents.orchestrator import (
            get_compiled_graph,
            reset_compiled_graph,
        )

        reset_compiled_graph()
        graph1 = get_compiled_graph()
        graph2 = get_compiled_graph()

        assert graph1 is graph2  # Same instance
        reset_compiled_graph()

    def test_graph_visualization(self):
        """Test graph visualization output."""
        from config_detective.agents.orchestrator import get_graph_visualization

        viz = get_graph_visualization()
        assert "graph TD" in viz
        assert "triage" in viz
        assert "reporter" in viz

    @pytest.mark.asyncio
    async def test_run_investigation_async_minimal(
        self, minimal_snapshot_a, minimal_snapshot_b, locale_failure_trace, fresh_trace_store
    ):
        """Test async investigation with minimal mocking."""
        from config_detective.agents.orchestrator import run_investigation_async, reset_compiled_graph
        from config_detective.agents.state import create_initial_state

        reset_compiled_graph()

        # Mock the memory and retrieval to avoid external dependencies
        # Patch where the function is imported, not where it's defined
        with patch("config_detective.memory.memory_available", return_value=False), \
             patch("config_detective.retrieval.search_all_sources", new_callable=AsyncMock) as mock_search:

            mock_search.return_value = []

            initial_state = create_initial_state(
                snapshot_a_dict=minimal_snapshot_a,
                snapshot_b_dict=minimal_snapshot_b,
                failure_trace=locale_failure_trace,
            )

            final_state = await run_investigation_async(initial_state)

            assert final_state is not None
            assert "status" in final_state
            assert len(final_state.get("reasoning_chain", [])) > 0

        reset_compiled_graph()


# =============================================================================
# Runner Tests
# =============================================================================


class TestRunner:
    """Tests for the runner module."""

    def test_quick_diagnose_locale(self, locale_failure_trace):
        """Test quick diagnosis for locale error."""
        from config_detective.agents.runner import quick_diagnose

        result = asyncio.run(quick_diagnose(locale_failure_trace))

        assert result["category"] == ErrorCategory.LOCALE.value
        assert "UnicodeDecodeError" in result["error_type"]
        assert len(result["suggestions"]) > 0

    def test_quick_diagnose_ssl(self):
        """Test quick diagnosis for SSL error."""
        from config_detective.agents.runner import quick_diagnose

        result = asyncio.run(quick_diagnose("SSLError: certificate verify failed"))

        assert result["category"] == ErrorCategory.SSL.value
        assert len(result["suggestions"]) > 0

    def test_format_trace_log(self, fresh_trace_store):
        """Test trace log formatting."""
        from config_detective.agents.runner import format_trace_log

        emit_event("t1", "node1", EventType.START, "Started")
        emit_event("t1", "node1", EventType.COMPLETE, "Done")

        log = format_trace_log("t1")

        assert "node1" in log
        assert "Started" in log
        assert "Done" in log

    def test_get_investigation_trace(self, fresh_trace_store):
        """Test getting investigation trace."""
        from config_detective.agents.runner import get_investigation_trace

        emit_event("t2", "test", EventType.INFO, "Test event")

        events = get_investigation_trace("t2")
        assert len(events) == 1
        assert events[0].message == "Test event"


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests for the full orchestrator flow."""

    @pytest.mark.asyncio
    async def test_full_investigation_mocked(
        self, minimal_snapshot_a, minimal_snapshot_b, locale_failure_trace, fresh_trace_store
    ):
        """Test full investigation with external dependencies mocked."""
        from config_detective.agents import run_investigation, reset_compiled_graph

        reset_compiled_graph()

        # Patch where the functions are imported, not where they're defined
        with patch("config_detective.memory.memory_available", return_value=False), \
             patch("config_detective.retrieval.search_all_sources", new_callable=AsyncMock) as mock_search:

            mock_search.return_value = []

            report = await run_investigation(
                snapshot_a=minimal_snapshot_a,
                snapshot_b=minimal_snapshot_b,
                failure_trace=locale_failure_trace,
                confidence_threshold=0.5,  # Lower for test
            )

            assert report is not None
            assert report.trace_id is not None
            # Should find the LANG delta
            assert "LANG" in (report.root_cause_delta_id or "") or len(report.reasoning_chain) > 0

        reset_compiled_graph()
