"""Tests for the Memory RAG module (Phase 3).

These tests verify:
- Model serialization and deserialization
- Embedding generation and caching
- Case storage and retrieval (with mocked Supabase)
- Pattern synthesis and reflection
- Similarity scoring

All tests use mocked Supabase to avoid requiring a real database.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from config_detective.memory.embeddings import (
    _fallback_embedding,
    clear_cache,
    cosine_similarity,
    embed_text,
    euclidean_distance,
    get_cache_stats,
)
from config_detective.memory.models import (
    CaseRecord,
    DeltaSummary,
    MemoryStats,
    PatternFingerprint,
    SimilarCaseResult,
)
from config_detective.memory.reflection import (
    _extract_symptoms,
    _extract_typical_deltas,
    _extract_typical_fixes,
    _simplify_error,
    synthesize_pattern,
)


# ============================================================================
# Model Tests
# ============================================================================


class TestModels:
    """Tests for Pydantic models."""

    def test_case_record_creation(self) -> None:
        """Test CaseRecord can be created with required fields."""
        case = CaseRecord(
            snapshot_a_hash="abc123",
            snapshot_b_hash="def456",
            failure_signature="UnicodeDecodeError: 'ascii' codec",
            root_cause_node_id="env:LANG",
            fix_text="ENV LANG=C.UTF-8",
        )

        assert case.snapshot_a_hash == "abc123"
        assert case.root_cause_node_id == "env:LANG"
        assert case.confidence == 0.5  # Default
        assert case.fix_verified is False  # Default
        assert isinstance(case.id, UUID)

    def test_case_record_to_supabase_row(self) -> None:
        """Test CaseRecord serializes to Supabase format."""
        case = CaseRecord(
            snapshot_a_hash="abc",
            snapshot_b_hash="def",
            failure_signature="error",
            root_cause_node_id="env:TZ",
            fix_text="fix it",
            confidence=0.85,
            fix_verified=True,
        )

        row = case.to_supabase_row()

        assert row["snapshot_a_hash"] == "abc"
        assert row["confidence"] == 0.85
        assert row["fix_verified"] is True
        assert isinstance(row["id"], str)
        assert isinstance(row["created_at"], str)

    def test_case_record_from_supabase_row(self) -> None:
        """Test CaseRecord deserializes from Supabase row."""
        row = {
            "id": str(uuid4()),
            "snapshot_a_hash": "hash_a",
            "snapshot_b_hash": "hash_b",
            "failure_signature": "test error",
            "root_cause_node_id": "os_pkg:libssl3",
            "root_cause_category": "ssl",
            "fix_text": "upgrade openssl",
            "fix_verified": True,
            "confidence": 0.92,
            "delta_summary": [
                {"node_id": "os_pkg:libssl3", "node_type": "os_pkg", 
                 "delta_type": "version_changed", "name": "libssl3"}
            ],
            "created_at": "2024-01-15T10:30:00",
        }

        case = CaseRecord.from_supabase_row(row)

        assert case.snapshot_a_hash == "hash_a"
        assert case.root_cause_category == "ssl"
        assert case.fix_verified is True
        assert len(case.delta_summary) == 1
        assert case.delta_summary[0].node_id == "os_pkg:libssl3"

    def test_pattern_fingerprint_creation(self) -> None:
        """Test PatternFingerprint creation and defaults."""
        pattern = PatternFingerprint(
            category="locale",
            pattern_description="Locale bugs from LANG=C",
            typical_symptoms=["UnicodeDecodeError", "ascii codec"],
            typical_deltas=["env:LANG", "locale:LC_ALL"],
            typical_fixes=["ENV LANG=C.UTF-8"],
        )

        assert pattern.category == "locale"
        assert len(pattern.typical_symptoms) == 2
        assert pattern.case_count == 1  # Default

    def test_similar_case_result_sorting(self) -> None:
        """Test SimilarCaseResult sorts by similarity descending."""
        case1 = CaseRecord(
            snapshot_a_hash="a", snapshot_b_hash="b",
            failure_signature="err", root_cause_node_id="x", fix_text="f",
        )
        case2 = CaseRecord(
            snapshot_a_hash="c", snapshot_b_hash="d",
            failure_signature="err2", root_cause_node_id="y", fix_text="g",
        )

        result_low = SimilarCaseResult(case=case1, similarity=0.5)
        result_high = SimilarCaseResult(case=case2, similarity=0.9)

        sorted_results = sorted([result_low, result_high])

        assert sorted_results[0].similarity == 0.9  # Higher first
        assert sorted_results[1].similarity == 0.5

    def test_case_record_similarity_score(self) -> None:
        """Test CaseRecord.similarity_score computes cosine similarity."""
        embedding = [1.0, 0.0, 0.0, 0.0]  # Simplified 4-dim embedding
        case = CaseRecord(
            snapshot_a_hash="a", snapshot_b_hash="b",
            failure_signature="err", root_cause_node_id="x", fix_text="f",
            failure_embedding=embedding,
        )

        # Identical embedding should have similarity 1.0
        assert case.similarity_score(embedding) == pytest.approx(1.0)

        # Orthogonal embedding should have similarity 0.0
        orthogonal = [0.0, 1.0, 0.0, 0.0]
        assert case.similarity_score(orthogonal) == pytest.approx(0.0)


# ============================================================================
# Embedding Tests
# ============================================================================


class TestEmbeddings:
    """Tests for the embedding service."""

    def test_fallback_embedding_dimension(self) -> None:
        """Test fallback embedding has correct dimension."""
        embedding = _fallback_embedding("test text")
        assert len(embedding) == 384

    def test_fallback_embedding_deterministic(self) -> None:
        """Test same text produces same fallback embedding."""
        text = "UnicodeDecodeError: 'ascii' codec"
        embedding1 = _fallback_embedding(text)
        embedding2 = _fallback_embedding(text)

        assert embedding1 == embedding2

    def test_fallback_embedding_different_texts(self) -> None:
        """Test different texts produce different embeddings."""
        embedding1 = _fallback_embedding("error one")
        embedding2 = _fallback_embedding("error two")

        assert embedding1 != embedding2

    def test_fallback_embedding_normalized(self) -> None:
        """Test fallback embedding is unit normalized."""
        embedding = _fallback_embedding("test")
        norm = sum(v * v for v in embedding) ** 0.5
        assert norm == pytest.approx(1.0, abs=0.01)

    def test_cosine_similarity_identical(self) -> None:
        """Test cosine similarity of identical vectors is 1."""
        vec = [0.5, 0.5, 0.5, 0.5]
        assert cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal(self) -> None:
        """Test cosine similarity of orthogonal vectors is 0."""
        vec1 = [1.0, 0.0, 0.0, 0.0]
        vec2 = [0.0, 1.0, 0.0, 0.0]
        assert cosine_similarity(vec1, vec2) == pytest.approx(0.0)

    def test_euclidean_distance_identical(self) -> None:
        """Test Euclidean distance of identical vectors is 0."""
        vec = [0.5, 0.5, 0.5, 0.5]
        assert euclidean_distance(vec, vec) == pytest.approx(0.0)

    def test_euclidean_distance_different(self) -> None:
        """Test Euclidean distance of different vectors is positive."""
        vec1 = [0.0, 0.0, 0.0, 0.0]
        vec2 = [1.0, 0.0, 0.0, 0.0]
        assert euclidean_distance(vec1, vec2) == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_embed_text_uses_cache(self) -> None:
        """Test embed_text caches results."""
        clear_cache()

        text = "cache test"
        await embed_text(text, use_cache=True)
        stats1 = get_cache_stats()

        await embed_text(text, use_cache=True)  # Should hit cache
        stats2 = get_cache_stats()

        assert stats1["size"] == 1
        assert stats2["size"] == 1  # No new entry

    @pytest.mark.asyncio
    async def test_embed_text_returns_correct_dim(self) -> None:
        """Test embed_text returns 384-dim vector."""
        embedding = await embed_text("test")
        assert len(embedding) == 384


# ============================================================================
# Reflection Tests
# ============================================================================


class TestReflection:
    """Tests for the reflection agent."""

    def test_simplify_error_removes_paths(self) -> None:
        """Test _simplify_error removes file paths."""
        error = "File '/home/user/project/main.py', line 42"
        simplified = _simplify_error(error)
        assert "/home/user" not in simplified
        assert "line N" in simplified

    def test_simplify_error_removes_hex(self) -> None:
        """Test _simplify_error removes hex addresses."""
        error = "at memory address 0x7fff5fbff8a0"
        simplified = _simplify_error(error)
        assert "0x..." in simplified

    def test_extract_symptoms_deduplicates(self) -> None:
        """Test _extract_symptoms removes duplicates."""
        cases = [
            CaseRecord(
                snapshot_a_hash="a", snapshot_b_hash="b",
                failure_signature="UnicodeDecodeError: 'ascii'",
                root_cause_node_id="x", fix_text="f",
            ),
            CaseRecord(
                snapshot_a_hash="c", snapshot_b_hash="d",
                failure_signature="UnicodeDecodeError: 'ascii'",  # Same
                root_cause_node_id="y", fix_text="g",
            ),
        ]

        symptoms = _extract_symptoms(cases)
        assert len(symptoms) == 1  # Deduplicated

    def test_extract_typical_deltas_weights_root_cause(self) -> None:
        """Test _extract_typical_deltas weights root cause higher."""
        cases = [
            CaseRecord(
                snapshot_a_hash="a", snapshot_b_hash="b",
                failure_signature="err",
                root_cause_node_id="env:LANG",
                fix_text="f",
                delta_summary=[
                    DeltaSummary(node_id="env:PATH", node_type="env",
                                delta_type="value_changed", name="PATH"),
                ],
            ),
        ]

        deltas = _extract_typical_deltas(cases)
        # Root cause should appear first (weighted higher)
        assert deltas[0] == "env:LANG"

    def test_extract_typical_fixes_prioritizes_verified(self) -> None:
        """Test _extract_typical_fixes prioritizes verified fixes."""
        cases = [
            CaseRecord(
                snapshot_a_hash="a", snapshot_b_hash="b",
                failure_signature="err",
                root_cause_node_id="x",
                fix_text="unverified fix",
                fix_verified=False,
            ),
            CaseRecord(
                snapshot_a_hash="c", snapshot_b_hash="d",
                failure_signature="err",
                root_cause_node_id="y",
                fix_text="verified fix",
                fix_verified=True,
            ),
        ]

        fixes = _extract_typical_fixes(cases)
        assert fixes[0] == "verified fix"

    @pytest.mark.asyncio
    async def test_synthesize_pattern_creates_fingerprint(self) -> None:
        """Test synthesize_pattern creates a PatternFingerprint."""
        cases = [
            CaseRecord(
                snapshot_a_hash="a", snapshot_b_hash="b",
                failure_signature="UnicodeDecodeError",
                root_cause_node_id="env:LANG",
                root_cause_category="locale",
                fix_text="ENV LANG=C.UTF-8",
                fix_verified=True,
                confidence=0.9,
            ),
            CaseRecord(
                snapshot_a_hash="c", snapshot_b_hash="d",
                failure_signature="UnicodeEncodeError",
                root_cause_node_id="env:LC_ALL",
                root_cause_category="locale",
                fix_text="export LC_ALL=en_US.UTF-8",
                fix_verified=True,
                confidence=0.85,
            ),
        ]

        pattern = await synthesize_pattern("locale", cases)

        assert pattern is not None
        assert pattern.category == "locale"
        assert pattern.case_count == 2
        assert len(pattern.typical_symptoms) > 0
        assert len(pattern.pattern_embedding) == 384


# ============================================================================
# Memory RAG Integration Tests (with mocked Supabase)
# ============================================================================


class TestMemoryRAGWithMockedSupabase:
    """Integration tests with mocked Supabase client."""

    @pytest.fixture
    def mock_supabase(self, monkeypatch):
        """Mock the Supabase client."""
        mock_client = MagicMock()

        # Mock table operations
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table

        # Mock insert chain
        mock_insert = MagicMock()
        mock_table.insert.return_value = mock_insert
        mock_insert.execute.return_value = MagicMock(
            data=[{"id": str(uuid4())}]
        )

        # Mock select chain
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_select.eq.return_value = mock_select
        mock_select.gte.return_value = mock_select
        mock_select.order.return_value = mock_select
        mock_select.limit.return_value = mock_select
        mock_select.execute.return_value = MagicMock(data=[], count=0)

        # Mock RPC
        mock_rpc = MagicMock()
        mock_client.rpc.return_value = mock_rpc
        mock_rpc.execute.return_value = MagicMock(data=[])

        # Patch get_client to return our mock
        monkeypatch.setattr(
            "config_detective.memory.memory_rag.get_client",
            lambda: mock_client,
        )
        monkeypatch.setattr(
            "config_detective.memory.memory_rag.is_available",
            lambda: True,
        )

        return mock_client

    @pytest.mark.asyncio
    async def test_store_case_calls_insert(self, mock_supabase) -> None:
        """Test store_case calls Supabase insert."""
        from config_detective.memory.memory_rag import store_case

        result = await store_case(
            snapshot_a_hash="abc",
            snapshot_b_hash="def",
            failure_signature="test error",
            root_cause_node_id="env:TEST",
            fix_text="test fix",
        )

        assert result is not None
        mock_supabase.table.assert_called_with("cases")
        mock_supabase.table().insert.assert_called_once()

    @pytest.mark.asyncio
    async def test_retrieve_similar_cases_calls_rpc(self, mock_supabase) -> None:
        """Test retrieve_similar_cases calls Supabase RPC."""
        from config_detective.memory.memory_rag import retrieve_similar_cases

        results = await retrieve_similar_cases("test error")

        assert isinstance(results, list)
        mock_supabase.rpc.assert_called_once()
        call_args = mock_supabase.rpc.call_args
        assert call_args[0][0] == "match_cases"

    @pytest.mark.asyncio
    async def test_get_patterns_with_category(self, mock_supabase) -> None:
        """Test get_patterns with category filter."""
        from config_detective.memory.memory_rag import get_patterns

        mock_supabase.table().select().eq().limit().execute.return_value = MagicMock(
            data=[]
        )

        results = await get_patterns(category="locale")

        assert isinstance(results, list)
        mock_supabase.table.assert_called_with("pattern_fingerprints")


# ============================================================================
# Memory Unavailable Tests
# ============================================================================


class TestMemoryUnavailable:
    """Tests for graceful degradation when memory is unavailable."""

    @pytest.fixture
    def no_supabase(self, monkeypatch):
        """Mock Supabase as unavailable."""
        monkeypatch.setattr(
            "config_detective.memory.memory_rag.get_client",
            lambda: None,
        )
        monkeypatch.setattr(
            "config_detective.memory.memory_rag.is_available",
            lambda: False,
        )

    @pytest.mark.asyncio
    async def test_store_case_returns_none_when_unavailable(self, no_supabase) -> None:
        """Test store_case returns None when Supabase unavailable."""
        from config_detective.memory.memory_rag import store_case

        result = await store_case(
            snapshot_a_hash="abc",
            snapshot_b_hash="def",
            failure_signature="test",
            root_cause_node_id="x",
            fix_text="y",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_retrieve_similar_returns_empty_when_unavailable(
        self, no_supabase
    ) -> None:
        """Test retrieve_similar_cases returns empty list when unavailable."""
        from config_detective.memory.memory_rag import retrieve_similar_cases

        results = await retrieve_similar_cases("test error")

        assert results == []

    @pytest.mark.asyncio
    async def test_get_stats_returns_empty_when_unavailable(self, no_supabase) -> None:
        """Test get_stats returns empty MemoryStats when unavailable."""
        from config_detective.memory.memory_rag import get_stats

        stats = await get_stats()

        assert isinstance(stats, MemoryStats)
        assert stats.total_cases == 0
        assert stats.total_patterns == 0
