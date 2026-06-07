"""Tests for the LLM integration module.

Covers:
- Groq client (mocked API calls, retry logic, structured prompts)
- Gemini client (mocked API calls, fallback)
- HuggingFace code client (mocked API calls)
- LLM Router (fallback chain, provider detection)
- Node integration (hypothesizer, critic, triage use LLM when available)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from config_detective.llm.groq_client import (
    chat as groq_chat,
    generate_explanation as groq_explain,
    generate_fix_suggestion as groq_fix,
    critique_hypothesis as groq_critique,
    is_available as groq_available,
)
from config_detective.llm.gemini_client import (
    chat as gemini_chat,
    generate_explanation as gemini_explain,
    is_available as gemini_available,
)
from config_detective.llm.hf_code import (
    chat as hf_chat,
    analyse_failure_trace,
    generate_fix_code,
    is_available as hf_available,
)
from config_detective.llm.router import LLMRouter, reset_router


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clean_router():
    """Reset the router singleton between tests."""
    reset_router()
    yield
    reset_router()


@pytest.fixture
def sample_delta() -> dict:
    return {
        "node_id": "env:LANG",
        "node_type": "env_var",
        "delta_type": "value_changed",
        "value_a": "en_US.UTF-8",
        "value_b": "C",
        "suspect_score": 0.9,
    }


@pytest.fixture
def sample_hypothesis() -> dict:
    return {
        "id": "h1",
        "delta_id": "env:LANG",
        "delta_type": "value_changed",
        "explanation": "LANG changed from en_US.UTF-8 to C causing encoding failures.",
        "fix_suggestion": "Set LANG to en_US.UTF-8",
        "fix_code": "export LANG='en_US.UTF-8'",
        "confidence": 0.8,
    }


# =============================================================================
# Groq Client Tests
# =============================================================================


class TestGroqClient:
    """Tests for the Groq client."""

    def test_unavailable_without_key(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        from config_detective.llm.groq_client import _get_client
        import functools
        _get_client.cache_clear() if hasattr(_get_client, 'cache_clear') else None
        assert groq_chat([{"role": "user", "content": "hello"}]) is None

    @patch("config_detective.llm.groq_client._get_client")
    def test_chat_returns_content(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "This is the response"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = groq_chat([{"role": "user", "content": "hello"}])
        assert result == "This is the response"

    @patch("config_detective.llm.groq_client._get_client")
    def test_chat_handles_exception(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API error")
        mock_get_client.return_value = mock_client

        result = groq_chat([{"role": "user", "content": "hello"}])
        assert result is None

    @patch("config_detective.llm.groq_client.chat")
    def test_generate_explanation(self, mock_chat, sample_delta):
        mock_chat.return_value = "The LANG variable changed, causing encoding issues."
        result = groq_explain(sample_delta, "UnicodeDecodeError", "codec error trace")
        assert result == "The LANG variable changed, causing encoding issues."

    @patch("config_detective.llm.groq_client.chat")
    def test_generate_fix(self, mock_chat, sample_delta):
        mock_chat.return_value = "SUGGESTION: Set LANG to en_US.UTF-8\nFIX_CODE: export LANG='en_US.UTF-8'"
        suggestion, code = groq_fix(sample_delta, "UnicodeDecodeError", "LANG changed")
        assert suggestion == "Set LANG to en_US.UTF-8"
        assert code == "export LANG='en_US.UTF-8'"

    @patch("config_detective.llm.groq_client.chat")
    def test_generate_fix_returns_none_on_failure(self, mock_chat, sample_delta):
        mock_chat.return_value = None
        suggestion, code = groq_fix(sample_delta, "Error", "explanation")
        assert suggestion is None
        assert code is None

    @patch("config_detective.llm.groq_client.chat")
    def test_critique_hypothesis(self, mock_chat, sample_hypothesis):
        mock_chat.return_value = "PLAUSIBLE: yes\nREASONING: LANG directly affects encoding\nCONFIDENCE: 0.85"
        result = groq_critique(sample_hypothesis, [{"node_id": "env:LANG"}], "UnicodeDecodeError")
        assert result is not None
        assert result["is_plausible"] is True
        assert result["adjusted_confidence"] == 0.85

    @patch("config_detective.llm.groq_client.chat")
    def test_critique_returns_none_on_failure(self, mock_chat, sample_hypothesis):
        mock_chat.return_value = None
        result = groq_critique(sample_hypothesis, [], "error")
        assert result is None


# =============================================================================
# Gemini Client Tests
# =============================================================================


class TestGeminiClient:
    """Tests for the Gemini client."""

    def test_unavailable_without_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert gemini_chat("hello") is None

    @patch("config_detective.llm.gemini_client._get_model")
    def test_chat_returns_content(self, mock_get_model):
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Gemini response"
        mock_model.generate_content.return_value = mock_response
        mock_get_model.return_value = mock_model

        result = gemini_chat("hello")
        assert result == "Gemini response"

    @patch("config_detective.llm.gemini_client._get_model")
    def test_chat_handles_none_response(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.generate_content.return_value = None
        mock_get_model.return_value = mock_model

        result = gemini_chat("hello")
        assert result is None

    @patch("config_detective.llm.gemini_client.chat")
    def test_generate_explanation(self, mock_chat, sample_delta):
        mock_chat.return_value = "Gemini says LANG causes encoding issues."
        result = gemini_explain(sample_delta, "UnicodeDecodeError", "error trace")
        assert "LANG" in result or "encoding" in result.lower()


# =============================================================================
# HuggingFace Code Client Tests
# =============================================================================


class TestHFCodeClient:
    """Tests for the HuggingFace code client."""

    def test_unavailable_without_token(self, monkeypatch):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        assert hf_chat([{"role": "user", "content": "hello"}]) is None

    @patch("config_detective.llm.hf_code._get_client")
    def test_chat_returns_content(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Code analysis"
        mock_client.chat_completion.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = hf_chat([{"role": "user", "content": "analyse"}])
        assert result == "Code analysis"

    @patch("config_detective.llm.hf_code.chat")
    def test_analyse_failure_trace(self, mock_chat):
        mock_chat.return_value = "The error is caused by missing locale settings."
        result = analyse_failure_trace("UnicodeDecodeError: 'ascii' codec")
        assert result is not None
        assert "locale" in result.lower()

    @patch("config_detective.llm.hf_code.chat")
    def test_generate_fix_code(self, mock_chat):
        mock_chat.return_value = "export LANG='en_US.UTF-8'"
        result = generate_fix_code("Set LANG to en_US.UTF-8", "Dockerfile")
        assert result == "export LANG='en_US.UTF-8'"


# =============================================================================
# LLM Router Tests
# =============================================================================


class TestLLMRouter:
    """Tests for the LLM router."""

    def test_no_providers_available(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)

        router = LLMRouter()
        assert not router.has_reasoning_llm
        assert not router.has_code_llm
        assert router.available_providers == []

    @patch("config_detective.llm.router.groq_client.is_available", return_value=True)
    @patch("config_detective.llm.router.gemini_client.is_available", return_value=False)
    @patch("config_detective.llm.router.hf_code.is_available", return_value=False)
    def test_groq_only(self, *mocks):
        router = LLMRouter()
        assert router.has_reasoning_llm
        assert not router.has_code_llm
        assert "groq" in router.available_providers

    @patch("config_detective.llm.router.groq_client.is_available", return_value=False)
    @patch("config_detective.llm.router.gemini_client.is_available", return_value=True)
    @patch("config_detective.llm.router.hf_code.is_available", return_value=False)
    def test_gemini_only(self, *mocks):
        router = LLMRouter()
        assert router.has_reasoning_llm
        assert "gemini" in router.available_providers

    @patch("config_detective.llm.router.groq_client.is_available", return_value=True)
    @patch("config_detective.llm.router.gemini_client.is_available", return_value=True)
    @patch("config_detective.llm.router.hf_code.is_available", return_value=True)
    def test_all_available(self, *mocks):
        router = LLMRouter()
        assert router.has_reasoning_llm
        assert router.has_code_llm
        assert len(router.available_providers) == 3

    @patch("config_detective.llm.router.groq_client.is_available", return_value=True)
    @patch("config_detective.llm.router.groq_client.generate_explanation", return_value="Groq explanation")
    @patch("config_detective.llm.router.gemini_client.is_available", return_value=True)
    @patch("config_detective.llm.router.hf_code.is_available", return_value=False)
    def test_explanation_uses_groq_first(self, *mocks):
        delta = {"node_id": "env:LANG", "delta_type": "value_changed", "value_a": "en_US.UTF-8", "value_b": "C"}
        router = LLMRouter()
        result = router.generate_explanation(delta, "Error", "trace")
        assert result == "Groq explanation"

    @patch("config_detective.llm.router.groq_client.is_available", return_value=True)
    @patch("config_detective.llm.router.groq_client.generate_explanation", return_value=None)
    @patch("config_detective.llm.router.gemini_client.is_available", return_value=True)
    @patch("config_detective.llm.router.gemini_client.generate_explanation", return_value="Gemini explanation")
    @patch("config_detective.llm.router.hf_code.is_available", return_value=False)
    def test_fallback_to_gemini(self, *mocks):
        delta = {"node_id": "env:LANG", "delta_type": "value_changed", "value_a": "en_US.UTF-8", "value_b": "C"}
        router = LLMRouter()
        result = router.generate_explanation(delta, "Error", "trace")
        assert result == "Gemini explanation"

    @patch("config_detective.llm.router.groq_client.is_available", return_value=False)
    @patch("config_detective.llm.router.gemini_client.is_available", return_value=False)
    @patch("config_detective.llm.router.hf_code.is_available", return_value=False)
    def test_all_unavailable_returns_none(self, *mocks):
        delta = {"node_id": "env:LANG", "delta_type": "value_changed", "value_a": "en_US.UTF-8", "value_b": "C"}
        router = LLMRouter()
        result = router.generate_explanation(delta, "Error", "trace")
        assert result is None

    @patch("config_detective.llm.router.groq_client.is_available", return_value=False)
    @patch("config_detective.llm.router.gemini_client.is_available", return_value=False)
    @patch("config_detective.llm.router.hf_code.is_available", return_value=True)
    @patch("config_detective.llm.router.hf_code.analyse_failure_trace", return_value="Code analysis")
    def test_code_llm_trace_analysis(self, *mocks):
        router = LLMRouter()
        result = router.analyse_trace("UnicodeDecodeError")
        assert result == "Code analysis"

    @patch("config_detective.llm.router.groq_client.is_available", return_value=True)
    @patch("config_detective.llm.router.groq_client.generate_fix_suggestion", return_value=("Fix it", "export LANG=C"))
    @patch("config_detective.llm.router.gemini_client.is_available", return_value=False)
    @patch("config_detective.llm.router.hf_code.is_available", return_value=False)
    def test_generate_fix(self, *mocks):
        delta = {"node_id": "env:LANG", "delta_type": "value_changed", "value_a": "en_US.UTF-8", "value_b": "C"}
        router = LLMRouter()
        suggestion, code = router.generate_fix(delta, "Error", "explanation")
        assert suggestion == "Fix it"
        assert code == "export LANG=C"

    @patch("config_detective.llm.router.groq_client.is_available", return_value=True)
    @patch("config_detective.llm.router.groq_client.critique_hypothesis")
    @patch("config_detective.llm.router.gemini_client.is_available", return_value=False)
    @patch("config_detective.llm.router.hf_code.is_available", return_value=False)
    def test_critique(self, mock_hf, mock_gemini, mock_critique, mock_groq, sample_hypothesis):
        mock_critique.return_value = {"is_plausible": True, "reasoning": "makes sense", "adjusted_confidence": 0.9}
        router = LLMRouter()
        result = router.critique_hypothesis(sample_hypothesis, [], "trace")
        assert result["is_plausible"] is True


# =============================================================================
# Node Integration Tests (LLM graceful degradation)
# =============================================================================


class TestNodeIntegration:
    """Tests that nodes still work when LLM is unavailable."""

    def test_hypothesizer_works_without_llm(self):
        """The hypothesizer must still produce hypotheses without any LLM."""
        from config_detective.agents.nodes.hypothesizer import hypothesizer_node
        from config_detective.agents.trace import reset_trace_store

        reset_trace_store()
        state = {
            "trace_id": "test-llm-1",
            "error_category": "locale",
            "error_type": "UnicodeDecodeError",
            "top_deltas": [
                {"node_id": "env:LANG", "node_type": "env_var",
                 "delta_type": "value_changed", "value_a": "en_US.UTF-8",
                 "value_b": "C", "suspect_score": 0.9},
            ],
            "similar_cases": [],
            "external_evidence": [],
            "iteration": 0,
            "reasoning_chain": [],
            "failure_trace": "UnicodeDecodeError: 'ascii' codec",
        }
        result = hypothesizer_node(state)
        assert len(result["hypotheses"]) == 1
        assert result["hypotheses"][0]["delta_id"] == "env:LANG"
        assert result["hypotheses"][0]["explanation"]
        assert result["hypotheses"][0]["fix_code"]

    def test_critic_works_without_llm(self):
        """The critic must still score hypotheses without any LLM."""
        from config_detective.agents.nodes.critic import critic_node
        from config_detective.agents.trace import reset_trace_store

        reset_trace_store()
        state = {
            "trace_id": "test-llm-2",
            "hypotheses": [
                {"id": "h1", "delta_id": "env:LANG", "delta_type": "value_changed",
                 "explanation": "LANG changed from en_US.UTF-8 to C causing encoding.",
                 "fix_suggestion": "Set LANG", "fix_code": "export LANG='en_US.UTF-8'",
                 "confidence": 0.8},
            ],
            "deltas": [
                {"node_id": "env:LANG", "node_type": "env_var",
                 "delta_type": "value_changed", "suspect_score": 0.9},
            ],
            "similar_cases": [],
            "external_evidence": [],
            "error_category": "locale",
            "confidence_threshold": 0.7,
            "iteration": 1,
            "max_iterations": 3,
            "reasoning_chain": [],
            "failure_trace": "UnicodeDecodeError",
        }
        result = critic_node(state)
        assert "confidence" in result
        assert result["confidence"] > 0
        assert result["selected_hypothesis"]["delta_id"] == "env:LANG"

    def test_triage_works_without_llm(self):
        """The triage node must still classify errors without any LLM."""
        from config_detective.agents.nodes.triage import triage_node
        from config_detective.agents.trace import reset_trace_store

        reset_trace_store()
        state = {
            "trace_id": "test-llm-3",
            "failure_trace": "UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2",
            "reasoning_chain": [],
        }
        result = triage_node(state)
        assert result["error_category"] == "locale"
        assert result["error_type"] == "UnicodeDecodeError"
