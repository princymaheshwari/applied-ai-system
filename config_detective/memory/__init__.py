"""Memory RAG module.

Supabase pgvector-backed episodic memory of past investigations,
plus semantic memory of compressed pattern fingerprints.

Main entry points:
- store_case: Save a completed investigation
- retrieve_similar_cases: Find past cases with similar failures
- get_patterns: Get relevant pattern fingerprints
- run_reflection: Compress recent cases into patterns

Example:
    from config_detective.memory import (
        store_case,
        retrieve_similar_cases,
        get_patterns,
        memory_available,
    )
    
    # Check if memory is available
    if memory_available():
        # Store a case
        case_id = await store_case(
            snapshot_a_hash="abc",
            snapshot_b_hash="def",
            failure_signature="UnicodeDecodeError",
            root_cause_node_id="env:LANG",
            fix_text="ENV LANG=C.UTF-8",
        )
        
        # Retrieve similar cases
        similar = await retrieve_similar_cases("UnicodeDecodeError")
"""

# Models
from .models import (
    CaseRecord,
    DeltaSummary,
    MemoryStats,
    PatternFingerprint,
    SimilarCaseResult,
)

# Supabase client
from .supabase_client import (
    get_client,
    get_schema_sql,
    health_check,
    is_available,
    reset_client,
)

# Embeddings
from .embeddings import (
    clear_cache as clear_embedding_cache,
    cosine_similarity,
    embed_batch,
    embed_text,
    euclidean_distance,
    get_cache_stats as get_embedding_cache_stats,
    is_available as embeddings_available,
)

# Memory RAG operations
from .memory_rag import (
    get_all_patterns,
    get_case_by_id,
    get_cases_since,
    get_patterns,
    get_stats,
    memory_available,
    retrieve_similar_cases,
    store_case,
    upsert_pattern,
)

# Reflection
from .reflection import (
    get_reflection_status,
    run_reflection,
    start_reflection_scheduler,
    stop_reflection_scheduler,
    synthesize_pattern,
)

__all__ = [
    # Models
    "CaseRecord",
    "PatternFingerprint",
    "DeltaSummary",
    "SimilarCaseResult",
    "MemoryStats",
    # Supabase
    "get_client",
    "is_available",
    "reset_client",
    "get_schema_sql",
    "health_check",
    # Embeddings
    "embed_text",
    "embed_batch",
    "cosine_similarity",
    "euclidean_distance",
    "clear_embedding_cache",
    "get_embedding_cache_stats",
    "embeddings_available",
    # Memory RAG
    "store_case",
    "retrieve_similar_cases",
    "get_case_by_id",
    "get_cases_since",
    "get_patterns",
    "get_all_patterns",
    "upsert_pattern",
    "get_stats",
    "memory_available",
    # Reflection
    "run_reflection",
    "synthesize_pattern",
    "get_reflection_status",
    "start_reflection_scheduler",
    "stop_reflection_scheduler",
]
