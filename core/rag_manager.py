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
import html
import re
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
MAX_MEMORY_SNIPPET_CHARS = 1800

_SELFMATCH_STRIP_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize_for_selfmatch(text: str) -> str:
    """Collapse whitespace/punctuation/case so a recalled question can be compared
    against the current one. Deliberately crude — it only has to catch "the same
    question asked again", not paraphrases; a paraphrase is exactly the case genuine
    recall should still be allowed to help with."""
    return _SELFMATCH_STRIP_RE.sub("", (text or "").strip().lower())

NOISY_TOOL_NAMES = (
    "smart_scrape",
    "git_diff",
    "git_diff_raw",
    "browse_with_stealth",
)

CORE_ACTORS = {"USER", "HUMAN", "WORKER", "AI", "ASSISTANT", "CIEL"}
CORE_ACTIONS = {"REQUEST", "CHAT_RESPONSE", "FORMAT_RESPONSE", "MULTI_TOOL_FORMAT_RESPONSE"}


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

        # NOT DefaultEmbeddingFunction (ONNX) — tried that to cut torch out of the
        # image, but the REAL ciel_data/vector_memory/ collection was created with
        # SentenceTransformerEmbeddingFunction, and ChromaDB persists that choice
        # IN the collection. Passing a different embedding_function here does not
        # migrate an existing collection — it just fails the moment the collection
        # is actually queried/written to ("sentence_transformers ... not installed").
        # See docker/requirements-docker.txt for how the image keeps this cheap
        # (CPU-only torch wheel) despite needing the library back.
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

        # Fetch MORE than top_k, then filter. Bug found live (test_rag_memory amnesia):
        # after the same recall question failed a few times, those failed turns became
        # the top-3 nearest neighbors BY CONSTRUCTION (identical Human: text). Self-match
        # filtering dropped all three, and search_similar returned "" even though the
        # plant fact ("Midnight Falcon / Python and Rust") sat at rank 4–5 with sim>0.70.
        # Over-fetch, drop self-matches + low scores, then keep up to top_k survivors.
        n_fetch = min(max(top_k * 4, 12), _collection.count())
        results = _collection.query(
            query_texts=[query],
            n_results=n_fetch,
            include=["documents", "distances", "metadatas"],
        )

        if not results["documents"] or not results["documents"][0]:
            return ""

        # Filter by relevance score (cosine distance: 0 = identical, 2 = opposite)
        # Convert to similarity: similarity = 1 - (distance / 2)
        #
        # Bug found live: asking "phân tích thêm về tin đó" after Ciel had already failed
        # to resolve a vague follow-up recalled the PREVIOUS occurrence of that exact same
        # question — including its own unhelpful "please specify" reply — as "past
        # context". A near-identical question is the single most similar thing in the
        # store BY CONSTRUCTION, so this is not a rare edge case: any repeated or
        # rephrased follow-up self-recalls its own failure, and the model then repeats
        # the same non-answer. Filtered here rather than downstream, because once this
        # merges into the prompt there is no way to tell "genuine past context" from
        # "the question echoing itself".
        query_norm = _normalize_for_selfmatch(query)
        relevant = []
        for doc, distance, meta in zip(
            results["documents"][0],
            results["distances"][0],
            results["metadatas"][0],
        ):
            similarity = 1 - (distance / 2)
            if similarity < MIN_RELEVANCE_SCORE:
                continue
            human_part = doc.split("|", 1)[0]
            human_text = human_part.split("Human:", 1)[-1]
            if _normalize_for_selfmatch(human_text) == query_norm:
                continue
            date = meta.get("date", "unknown")
            relevant.append(f"[{date}] {doc}")
            if len(relevant) >= top_k:
                break

        if not relevant:
            return ""

        return compress_context(relevant)

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


# ==========================================================
# ADMINISTRATION (pruning) — read/delete primitives only.
# Policy (which cutoff, dry-run vs apply, backups) lives in
# scripts/prune_rag_memory.py, not here — keeps this module's public
# surface to the "simple operations" the module docstring promises.
# ==========================================================
def list_by_age(older_than_days: int) -> list[dict]:
    """Return every memory older than N days: [{"id","date","timestamp","preview"}].
    Read-only — does not delete anything. Empty list if RAG is unavailable."""
    if not _ensure_initialized():
        return []
    try:
        if _collection.count() == 0:
            return []
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=older_than_days)).isoformat()
        data = _collection.get(include=["metadatas", "documents"])
        out = []
        for doc_id, meta, doc in zip(data["ids"], data["metadatas"], data["documents"]):
            ts = meta.get("timestamp", "")
            if ts and ts < cutoff:
                out.append({
                    "id": doc_id,
                    "date": meta.get("date", "unknown"),
                    "timestamp": ts,
                    "preview": (doc or "")[:120],
                })
        return out
    except Exception as e:
        print(f"[Ciel Warning] list_by_age failed: {e}")
        return []


def delete_by_ids(ids: list) -> int:
    """Delete specific memories by id. Returns how many were requested (Chroma's
    delete() doesn't report a count, so this is best-effort — callers should
    re-check get_memory_count() to confirm)."""
    if not ids or not _ensure_initialized():
        return 0
    try:
        _collection.delete(ids=ids)
        return len(ids)
    except Exception as e:
        print(f"[Ciel Warning] delete_by_ids failed: {e}")
        return 0


def compress_context(raw_contexts) -> str:
    """Compress recalled memories with zero-token structural filtering.

    This removes large raw tool artifacts before the Router sees recalled
    context. It keeps the core interaction shape whenever possible:
    [YYYY-MM-DD] Human: ... | Ai: ...
    """
    if not raw_contexts:
        return ""

    if isinstance(raw_contexts, str):
        contexts = [raw_contexts]
    else:
        contexts = list(raw_contexts)

    compressed = []
    for raw in contexts:
        if not raw:
            continue

        text = str(raw)
        text = _remove_noisy_tool_sections(text)

        core = _extract_human_ai_pair(text)
        if core:
            compressed.append(core)
            continue

        core = _extract_tagged_core_blocks(text)
        if core:
            compressed.append(core)
            continue

        cleaned = _clean_memory_text(text)
        if cleaned:
            compressed.append(_clip(cleaned, MAX_MEMORY_SNIPPET_CHARS))

    return "\n".join(item for item in compressed if item).strip()


def _remove_noisy_tool_sections(text: str) -> str:
    """Drop raw scrape/diff/browser artifacts that tend to poison recall."""
    noisy = "|".join(re.escape(name) for name in NOISY_TOOL_NAMES)

    patterns = [
        # Multi-tool output blocks.
        rf"(?is)---\s*Output from\s+(?:{noisy})\s*---.*?(?=\n---\s*Output from|\n\[\d{{4}}-\d{{2}}-\d{{2}}|\Z)",
        # Thought-log style tool results.
        rf"(?is)^\[\d{{4}}-\d{{2}}-\d{{2}}[^\]]*\]\s+\[TOOL\]\s+\[[^\]]*\]\s*.*?(?:{noisy}).*?(?=^-{{20,}}\s*$|\Z)",
        # Inline action/result fragments from old memories.
        rf"(?is)(?:Action|Tool|Output|Result)\s*:\s*(?:{noisy})\b.*?(?=\s+\|\s*(?:Human|User|Ai|Assistant|Ciel):|\n\[\d{{4}}-\d{{2}}-\d{{2}}|\Z)",
    ]

    for pattern in patterns:
        text = re.sub(pattern, " ", text, flags=re.MULTILINE)

    # Remove very large fenced blocks, especially HTML/diff/code payloads.
    text = re.sub(
        r"(?is)```(?:html|diff|patch|json|javascript|js|python|py)?\s*\n.{1500,}?```",
        " ",
        text,
    )

    # Remove obvious raw HTML bodies before generic cleanup.
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    return text


def _extract_human_ai_pair(text: str) -> str:
    """Extract archived Human/Ai memory pairs and discard attached payloads."""
    date = _extract_date(text)

    human = _extract_labeled_value(text, ("Human", "User"))
    ai = _extract_labeled_value(text, ("Ai", "AI", "Assistant", "Ciel", "Worker"))

    if not human and not ai:
        return ""

    human = _clip(_clean_memory_text(human), 600)
    ai = _clip(_clean_memory_text(ai), 900)

    parts = []
    if human:
        parts.append(f"Human: {human}")
    if ai:
        parts.append(f"Ai: {ai}")

    return f"[{date}] " + " | ".join(parts)


def _extract_tagged_core_blocks(text: str) -> str:
    """Keep only core [USER]/[WORKER] log blocks from structured logs."""
    date = _extract_date(text)
    kept = []

    block_re = re.compile(
        r"(?ms)^\[(?P<ts>\d{4}-\d{2}-\d{2}[^\]]*)\]\s+"
        r"\[(?P<actor>[^\]]+)\]\s+\[(?P<action>[^\]]+)\]\s*"
        r"(?P<body>.*?)(?=^-{20,}\s*$|^\[\d{4}-\d{2}-\d{2}|\Z)"
    )

    for match in block_re.finditer(text):
        actor = match.group("actor").strip().upper()
        action = match.group("action").strip().upper()
        if actor not in CORE_ACTORS and action not in CORE_ACTIONS:
            continue

        body = _clip(_clean_memory_text(match.group("body")), 900)
        if not body:
            continue

        if actor in {"USER", "HUMAN"} or action == "REQUEST":
            kept.append(f"Human: {body}")
        elif actor in {"WORKER", "AI", "ASSISTANT", "CIEL"}:
            kept.append(f"Ai: {body}")

    if not kept:
        return ""

    return f"[{date}] " + " | ".join(kept[:2])


def _extract_labeled_value(text: str, labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    boundary_labels = (
        "Human|User|Ai|AI|Assistant|Ciel|Worker|Tool|Action|Result|Output"
    )
    pattern = (
        rf"(?is)(?:^|[\|\n])\s*(?:\[\d{{4}}-\d{{2}}-\d{{2}}[^\]]*\]\s*)?"
        rf"(?:{label_pattern})\s*:\s*"
        rf"(.*?)(?=\s*(?:\||\n)\s*(?:\[\d{{4}}-\d{{2}}-\d{{2}}[^\]]*\]\s*)?(?:{boundary_labels})\s*:|\s*\n\[\d{{4}}-\d{{2}}-\d{{2}}|\Z)"
    )
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def _extract_date(text: str) -> str:
    match = re.search(r"\[(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    return "unknown"


def _clean_memory_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[[^\]]{0,80}\]\((?:data:|https?://)[^)]+\)", " ", text)
    text = re.sub(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=]+", " ", text)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\xa0]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."
