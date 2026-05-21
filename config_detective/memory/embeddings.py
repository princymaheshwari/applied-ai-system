"""Embedding service for Memory RAG.

This module provides text embeddings using HuggingFace's BGE model:
- BAAI/bge-large-en-v1.5: 384-dimensional embeddings
- Excellent semantic similarity performance
- Free via HuggingFace Inference API

Features:
- In-memory caching to avoid redundant API calls
- Batch embedding support for efficiency
- Graceful degradation when HuggingFace is unavailable
- Cosine similarity computation utilities

Usage:
    from config_detective.memory.embeddings import embed_text, embed_batch
    
    # Single text
    embedding = await embed_text("UnicodeDecodeError: 'ascii' codec")
    
    # Batch
    embeddings = await embed_batch(["error 1", "error 2", "error 3"])
"""

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

# Model configuration
EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"
EMBEDDING_DIM = 384

# In-memory cache for embeddings
# Key: hash of text, Value: embedding vector
_embedding_cache: dict[str, list[float]] = {}
_MAX_CACHE_SIZE = 10000


def _text_hash(text: str) -> str:
    """Create a hash key for caching embeddings."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _get_hf_token() -> str | None:
    """Get HuggingFace token from environment."""
    return os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")


@lru_cache(maxsize=1)
def _get_inference_client() -> Any | None:
    """Get a cached HuggingFace InferenceClient.

    Returns None if huggingface_hub is not installed or
    no token is configured.
    """
    try:
        from huggingface_hub import InferenceClient

        token = _get_hf_token()
        if not token:
            logger.warning(
                "HF_TOKEN not set. Embeddings will use fallback mode. "
                "Set HF_TOKEN environment variable for full functionality."
            )
            return None

        client = InferenceClient(token=token)
        logger.info(f"HuggingFace client initialized for {EMBEDDING_MODEL}")
        return client
    except ImportError:
        logger.warning("huggingface_hub not installed. Embeddings unavailable.")
        return None
    except Exception as e:
        logger.error(f"Failed to create HuggingFace client: {e}")
        return None


async def embed_text(text: str, use_cache: bool = True) -> list[float]:
    """Get embedding vector for a single text.

    Args:
        text: The text to embed
        use_cache: Whether to use cached embedding if available

    Returns:
        384-dimensional embedding vector

    Note:
        If HuggingFace is unavailable, returns a deterministic
        fallback embedding based on text hash (useful for testing).
    """
    # Check cache
    cache_key = _text_hash(text)
    if use_cache and cache_key in _embedding_cache:
        return _embedding_cache[cache_key]

    # Get embedding from HuggingFace
    client = _get_inference_client()
    if client is None:
        embedding = _fallback_embedding(text)
    else:
        embedding = await _get_hf_embedding(client, text)

    # Cache result
    if use_cache and len(_embedding_cache) < _MAX_CACHE_SIZE:
        _embedding_cache[cache_key] = embedding

    return embedding


async def embed_batch(texts: list[str], use_cache: bool = True) -> list[list[float]]:
    """Get embedding vectors for multiple texts.

    More efficient than calling embed_text repeatedly when
    embedding many texts at once.

    Args:
        texts: List of texts to embed
        use_cache: Whether to use cached embeddings

    Returns:
        List of 384-dimensional embedding vectors
    """
    results: list[list[float]] = []
    uncached_indices: list[int] = []
    uncached_texts: list[str] = []

    # Check cache for each text
    for i, text in enumerate(texts):
        cache_key = _text_hash(text)
        if use_cache and cache_key in _embedding_cache:
            results.append(_embedding_cache[cache_key])
        else:
            results.append([])  # Placeholder
            uncached_indices.append(i)
            uncached_texts.append(text)

    # Get embeddings for uncached texts
    if uncached_texts:
        client = _get_inference_client()
        if client is None:
            new_embeddings = [_fallback_embedding(t) for t in uncached_texts]
        else:
            new_embeddings = await _get_hf_embeddings_batch(client, uncached_texts)

        # Fill in results and update cache
        for i, embedding in zip(uncached_indices, new_embeddings):
            results[i] = embedding
            if use_cache and len(_embedding_cache) < _MAX_CACHE_SIZE:
                cache_key = _text_hash(texts[i])
                _embedding_cache[cache_key] = embedding

    return results


async def _get_hf_embedding(client: Any, text: str) -> list[float]:
    """Get embedding from HuggingFace Inference API."""
    try:
        # The feature_extraction endpoint returns embeddings
        response = client.feature_extraction(
            text,
            model=EMBEDDING_MODEL,
        )

        # Response is typically a nested list, flatten if needed
        if isinstance(response, list):
            if len(response) > 0 and isinstance(response[0], list):
                # Nested list - take first (sentence embedding)
                embedding = response[0]
            else:
                embedding = response
        else:
            embedding = list(response)

        # Ensure correct dimension
        if len(embedding) != EMBEDDING_DIM:
            logger.warning(
                f"Unexpected embedding dimension: {len(embedding)}, expected {EMBEDDING_DIM}"
            )

        return embedding
    except Exception as e:
        logger.error(f"HuggingFace embedding failed: {e}")
        return _fallback_embedding(text)


async def _get_hf_embeddings_batch(client: Any, texts: list[str]) -> list[list[float]]:
    """Get embeddings for a batch of texts from HuggingFace."""
    # HuggingFace Inference API supports batching
    try:
        responses = client.feature_extraction(
            texts,
            model=EMBEDDING_MODEL,
        )

        embeddings = []
        for response in responses:
            if isinstance(response, list) and len(response) > 0:
                if isinstance(response[0], list):
                    embeddings.append(response[0])
                else:
                    embeddings.append(response)
            else:
                embeddings.append(list(response))

        return embeddings
    except Exception as e:
        logger.error(f"HuggingFace batch embedding failed: {e}")
        return [_fallback_embedding(t) for t in texts]


def _fallback_embedding(text: str) -> list[float]:
    """Generate a deterministic fallback embedding when HuggingFace is unavailable.

    This creates a simple hash-based embedding that preserves some
    similarity properties for testing purposes. Not suitable for
    production semantic search.
    """
    # Use text hash to create deterministic but varied vectors
    hash_bytes = hashlib.sha256(text.encode()).digest()

    # Convert hash bytes to floats in range [-1, 1]
    embedding = []
    for i in range(EMBEDDING_DIM):
        byte_idx = i % len(hash_bytes)
        # Convert byte to float in [-1, 1]
        value = (hash_bytes[byte_idx] / 127.5) - 1.0
        # Add some variation based on position
        value = value * (0.5 + 0.5 * ((i % 10) / 10))
        embedding.append(value)

    # Normalize to unit length
    norm = sum(v * v for v in embedding) ** 0.5
    if norm > 0:
        embedding = [v / norm for v in embedding]

    return embedding


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First embedding vector
        b: Second embedding vector

    Returns:
        Cosine similarity in range [-1, 1], where 1 is identical
    """
    if len(a) != len(b) or len(a) == 0:
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


def euclidean_distance(a: list[float], b: list[float]) -> float:
    """Compute Euclidean distance between two vectors.

    Args:
        a: First embedding vector
        b: Second embedding vector

    Returns:
        Euclidean distance (lower = more similar)
    """
    if len(a) != len(b):
        return float("inf")

    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def clear_cache() -> int:
    """Clear the embedding cache.

    Returns:
        Number of entries cleared
    """
    global _embedding_cache
    count = len(_embedding_cache)
    _embedding_cache = {}
    return count


def get_cache_stats() -> dict[str, int]:
    """Get statistics about the embedding cache."""
    return {
        "size": len(_embedding_cache),
        "max_size": _MAX_CACHE_SIZE,
    }


def is_available() -> bool:
    """Check if embedding service is available.

    Returns True if HuggingFace client is configured,
    or False if only fallback mode is available.
    """
    return _get_inference_client() is not None
