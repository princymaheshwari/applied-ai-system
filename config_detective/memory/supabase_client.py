"""Supabase client singleton for Memory RAG.

This module provides a thread-safe singleton Supabase client with:
- Lazy initialization (only connects when first used)
- Environment-based configuration
- Graceful degradation when Supabase is unavailable
- SQL schema definitions for the memory tables

Database Schema:
    The memory system requires two tables with pgvector extension:
    - cases: Stores individual investigation cases (episodic memory)
    - pattern_fingerprints: Stores compressed patterns (semantic memory)

Usage:
    from config_detective.memory.supabase_client import get_client
    
    client = get_client()
    if client:
        result = client.table("cases").select("*").execute()
"""

from __future__ import annotations

import logging
import os
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

# Thread-safe singleton
_client: "Client | None" = None
_client_lock = Lock()
_init_attempted = False


# SQL schema for creating the required tables
# Run this in Supabase SQL Editor to set up the database
SCHEMA_SQL = """
-- Enable pgvector extension (required for embeddings)
CREATE EXTENSION IF NOT EXISTS vector;

-- Cases table: episodic memory of past investigations
CREATE TABLE IF NOT EXISTS cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_a_hash TEXT NOT NULL,
    snapshot_b_hash TEXT NOT NULL,
    failure_signature TEXT NOT NULL,
    failure_embedding vector(384),
    root_cause_node_id TEXT NOT NULL,
    root_cause_category TEXT,
    fix_text TEXT NOT NULL,
    fix_verified BOOLEAN DEFAULT false,
    confidence FLOAT DEFAULT 0.5,
    delta_summary JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Pattern fingerprints table: semantic memory of compressed patterns
CREATE TABLE IF NOT EXISTS pattern_fingerprints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category TEXT NOT NULL UNIQUE,
    pattern_description TEXT NOT NULL,
    pattern_embedding vector(384),
    typical_symptoms TEXT[] DEFAULT '{}',
    typical_deltas TEXT[] DEFAULT '{}',
    typical_fixes TEXT[] DEFAULT '{}',
    case_count INT DEFAULT 1,
    last_updated TIMESTAMPTZ DEFAULT now()
);

-- Index for fast vector similarity search on cases
CREATE INDEX IF NOT EXISTS cases_embedding_idx 
ON cases USING ivfflat (failure_embedding vector_cosine_ops)
WITH (lists = 100);

-- Index for fast vector similarity search on patterns
CREATE INDEX IF NOT EXISTS patterns_embedding_idx 
ON pattern_fingerprints USING ivfflat (pattern_embedding vector_cosine_ops)
WITH (lists = 50);

-- Index for filtering cases by category
CREATE INDEX IF NOT EXISTS cases_category_idx ON cases (root_cause_category);

-- Index for time-based queries
CREATE INDEX IF NOT EXISTS cases_created_at_idx ON cases (created_at DESC);

-- Function for vector similarity search on cases
CREATE OR REPLACE FUNCTION match_cases(
    query_embedding vector(384),
    match_count int DEFAULT 5,
    similarity_threshold float DEFAULT 0.5
)
RETURNS TABLE (
    id UUID,
    snapshot_a_hash TEXT,
    snapshot_b_hash TEXT,
    failure_signature TEXT,
    root_cause_node_id TEXT,
    root_cause_category TEXT,
    fix_text TEXT,
    fix_verified BOOLEAN,
    confidence FLOAT,
    delta_summary JSONB,
    created_at TIMESTAMPTZ,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        c.id,
        c.snapshot_a_hash,
        c.snapshot_b_hash,
        c.failure_signature,
        c.root_cause_node_id,
        c.root_cause_category,
        c.fix_text,
        c.fix_verified,
        c.confidence,
        c.delta_summary,
        c.created_at,
        1 - (c.failure_embedding <=> query_embedding) as similarity
    FROM cases c
    WHERE c.failure_embedding IS NOT NULL
      AND 1 - (c.failure_embedding <=> query_embedding) >= similarity_threshold
    ORDER BY c.failure_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Function for vector similarity search on patterns
CREATE OR REPLACE FUNCTION match_patterns(
    query_embedding vector(384),
    match_count int DEFAULT 3,
    similarity_threshold float DEFAULT 0.4
)
RETURNS TABLE (
    id UUID,
    category TEXT,
    pattern_description TEXT,
    typical_symptoms TEXT[],
    typical_deltas TEXT[],
    typical_fixes TEXT[],
    case_count INT,
    last_updated TIMESTAMPTZ,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        p.id,
        p.category,
        p.pattern_description,
        p.typical_symptoms,
        p.typical_deltas,
        p.typical_fixes,
        p.case_count,
        p.last_updated,
        1 - (p.pattern_embedding <=> query_embedding) as similarity
    FROM pattern_fingerprints p
    WHERE p.pattern_embedding IS NOT NULL
      AND 1 - (p.pattern_embedding <=> query_embedding) >= similarity_threshold
    ORDER BY p.pattern_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
"""


def get_client() -> "Client | None":
    """Get the singleton Supabase client.

    Returns None if Supabase credentials are not configured or
    connection fails. This allows the system to gracefully degrade
    when Supabase is unavailable.

    Returns:
        Supabase client instance, or None if unavailable
    """
    global _client, _init_attempted

    with _client_lock:
        if _client is not None:
            return _client

        if _init_attempted:
            # Already tried and failed, don't retry
            return None

        _init_attempted = True
        _client = _create_client()
        return _client


def _create_client() -> "Client | None":
    """Create a new Supabase client from environment variables.

    Required environment variables:
    - SUPABASE_URL: Your Supabase project URL
    - SUPABASE_ANON_KEY or SUPABASE_KEY: Your Supabase anon/public key

    Returns:
        Supabase client, or None if configuration is missing/invalid
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY")

    if not url or not key:
        logger.warning(
            "Supabase credentials not found. Memory features will be disabled. "
            "Set SUPABASE_URL and SUPABASE_ANON_KEY environment variables."
        )
        return None

    try:
        from supabase import create_client

        client = create_client(url, key)
        logger.info("Supabase client initialized successfully")
        return client
    except Exception as e:
        logger.error(f"Failed to create Supabase client: {e}")
        return None


def reset_client() -> None:
    """Reset the singleton client (for testing purposes)."""
    global _client, _init_attempted

    with _client_lock:
        _client = None
        _init_attempted = False


def is_available() -> bool:
    """Check if Supabase is available and configured."""
    return get_client() is not None


def get_schema_sql() -> str:
    """Get the SQL schema for setting up the memory tables.

    Run this SQL in your Supabase SQL Editor to create the
    required tables and functions.
    """
    return SCHEMA_SQL


async def health_check() -> dict[str, bool | str]:
    """Perform a health check on the Supabase connection.

    Returns:
        Dict with 'healthy' bool and optional 'error' message
    """
    client = get_client()
    if not client:
        return {"healthy": False, "error": "Supabase client not initialized"}

    try:
        # Try a simple query to verify connection
        result = client.table("cases").select("id").limit(1).execute()
        return {"healthy": True, "error": None}
    except Exception as e:
        return {"healthy": False, "error": str(e)}
