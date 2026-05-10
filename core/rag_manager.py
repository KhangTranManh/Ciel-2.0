"""rag_manager.py — Long-Term Memory via ChromaDB + Sentence-Transformers.

This module handles the RAG (Retrieval-Augmented Generation) layer for Ciel.
It provides two simple operations:
  1. embed_and_save(text) — Store a conversation snippet for future recall.
  2. search_similar(query, k) — Find past conversations relevant to a query.

Architecture:
  - Embedding model: all-MiniLM-L6-v2 (local, fast, ~80MB)
  - Vector DB: ChromaDB (local, persistent, file-based)
  - Storage: ciel_data/vector_memory/

Design Decisions:
  - This module is OPTIONAL. If chromadb or sentence-transformers are missing,
    it degrades gracefully — CielCore still works with JSON-only memory.
  - Each stored document is a single conversation turn (user + assistant pair).
  - Metadata includes timestamps for future cleanup/expiry features.
"""

import datetime
from pathlib import Path

# Lazy imports — these are heavy libraries, only load when actually used
_chromadb = None
_embedding_fn = None
_collection = None
_initialized = False
_available = True  # Set to False if imports fail


# ==========================================================
# CONFIGURATION
# ==========================================================
VECTOR_DB_DIR = Path(__file__).resolve().parent.parent / "ciel_data" / "vector_memory"
COLLECTION_NAME = "ciel_long_term_memory"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
DEFAULT_TOP_K = 3              # How many past conversations to retrieve
MIN_RELEVANCE_SCORE = 0.65     # Ignore results below this similarity threshold
MIN_QUERY_LENGTH = 15          # Skip RAG for very short/trivial inputs


# ==========================================================
# INITIALIZATION (lazy — only runs on first use)
# ==========================================================
def _ensure_initialized():
    """Lazy-load ChromaDB and the embedding model on first use."""
    global _chromadb, _embedding_fn, _collection, _initialized, _available

    if _initialized:
        return _available

    _initialized = True

    try:
        import chromadb
        from chromadb.utils import embedding_functions

        _chromadb = chromadb

        # Create persistent storage directory
        VECTOR_DB_DIR.mkdir(parents=True, exist_ok=True)

        # Initialize embedding function (downloads model on first run)
        _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL
        )

        # Create/open persistent ChromaDB client
        client = chromadb.PersistentClient(path=str(VECTOR_DB_DIR))
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=_embedding_fn,
            metadata={"hnsw:space": "cosine"},  # Use cosine similarity
        )

        print(f"[Ciel System] Long-term memory loaded: {_collection.count()} memories in vault.")
        _available = True

    except ImportError as e:
        print(f"[Ciel Warning] RAG disabled — missing dependency: {e}")
        _available = False
    except Exception as e:
        print(f"[Ciel Warning] RAG disabled — initialization error: {e}")
        _available = False

    return _available


# ==========================================================
# PUBLIC API
# ==========================================================
def embed_and_save(text: str, metadata: dict = None) -> bool:
    """Store a conversation snippet in long-term memory.

    Args:
        text: The conversation text to store (typically "User: ... | Ciel: ...").
        metadata: Optional metadata dict (timestamp is added automatically).

    Returns:
        True if saved successfully, False if RAG is unavailable.
    """
    if not _ensure_initialized():
        return False

    try:
        # Generate a unique ID based on timestamp
        now = datetime.datetime.now()
        doc_id = f"mem_{now.strftime('%Y%m%d_%H%M%S_%f')}"

        # Build metadata
        doc_metadata = {
            "timestamp": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
        }
        if metadata:
            doc_metadata.update(metadata)

        # Store in ChromaDB
        _collection.add(
            documents=[text],
            metadatas=[doc_metadata],
            ids=[doc_id],
        )
        return True

    except Exception as e:
        print(f"[Ciel Warning] Failed to save to long-term memory: {e}")
        return False


def search_similar(query: str, top_k: int = DEFAULT_TOP_K) -> str:
    """Search long-term memory for conversations relevant to the query.

    Args:
        query: The user's current input to search against.
        top_k: Maximum number of results to return.

    Returns:
        A formatted string of relevant past conversations, or empty string
        if nothing relevant is found or RAG is unavailable.
    """
    if not _ensure_initialized():
        return ""

    # Skip RAG for short or trivial queries
    if len(query.strip()) < MIN_QUERY_LENGTH:
        return ""

    try:
        # Skip search if the database is empty
        if _collection.count() == 0:
            return ""

        results = _collection.query(
            query_texts=[query],
            n_results=min(top_k, _collection.count()),
            include=["documents", "distances", "metadatas"],
        )

        if not results["documents"] or not results["documents"][0]:
            return ""

        # Filter by relevance score (cosine distance: 0 = identical, 2 = opposite)
        # Convert to similarity: similarity = 1 - (distance / 2)
        relevant = []
        for doc, distance, meta in zip(
            results["documents"][0],
            results["distances"][0],
            results["metadatas"][0],
        ):
            similarity = 1 - (distance / 2)
            if similarity >= MIN_RELEVANCE_SCORE:
                date = meta.get("date", "unknown")
                relevant.append(f"[{date}] {doc}")

        if not relevant:
            return ""

        return "\n".join(relevant)

    except Exception as e:
        print(f"[Ciel Warning] Long-term memory search failed: {e}")
        return ""


def get_memory_count() -> int:
    """Return the number of stored memories, or 0 if unavailable."""
    if not _ensure_initialized():
        return 0
    try:
        return _collection.count()
    except Exception:
        return 0
