"""knowledge_index.py — retrieval over the Master's documents and Ciel's own actions.

This is deliberately NOT conversation memory. Recent turns, the session compact,
`facts.json`, `user_model.json`, and `planner.db` already cover those needs. The old
``rag_manager`` collection (raw Human/AI pairs) is left untouched and read-only.

Two separate collections, one multilingual embedding model:

* ``docs``    — chunks of files the Master or Ciel produced (reports, notes, text).
* ``actions`` — short structured records of what Ciel did (email sent, report built,
                event scheduled), each with a timestamp and an optional ``subject_key``.

Information changes continuously, so every result carries *when it was recorded* and a
``stale`` flag; nothing here is ever evidence of current state (see the "Memory never
upgrades old evidence" rule in instructionAI/data_pipeline.md).

* Documents are re-synced by content hash. A changed file replaces ALL its old chunks, so
  stale text cannot survive an edit; a deleted file is removed by ``remove_missing``.
* Actions are append-only history. Records sharing a ``subject_key`` (for example a
  recurring gold-price report) are collapsed to the newest one at query time, so an old
  value cannot outrank a newer one.
* Volatile subjects (market, weather, ...) get a short TTL; older hits are still
  returned but flagged ``stale`` so the caller can say "as of <date>".

Scores are RAW COSINE SIMILARITY in [-1, 1]. The legacy ``1 - distance/2`` transform
mapped cosine 0.30 to 0.65 and made the old threshold far looser than it looked.
The thresholds below are PROVISIONAL until calibrated on a labelled Vietnamese set.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent / "ciel_data" / "knowledge_index"
DOCS_COLLECTION = "ciel_docs_v1"
ACTIONS_COLLECTION = "ciel_actions_v1"

# Provisional retrieval parameters (calibrate before trusting).
MIN_COSINE = 0.38          # raw-cosine floor; smoke set: related 0.42-0.60, unrelated <=0.27
RELATIVE_MARGIN = 0.08     # keep hits within this of the best hit
FETCH_K = 12               # candidates pulled from the vector store
TOP_K = 3                  # hits returned
CHUNK_CHARS = 700
CHUNK_OVERLAP = 100
ACTION_MAX_CHARS = 300
RECALL_BUDGET_TOKENS = 600

# After this many days a record is flagged stale. Volatile subjects age fast.
STALE_DAYS_DEFAULT = 30.0
STALE_DAYS_VOLATILE = 1.0
VOLATILE_PREFIXES = ("market", "price", "gold", "crypto", "stock", "weather", "news")

_SPACE_RE = re.compile(r"[ \t]+")
_PARA_RE = re.compile(r"\n\s*\n")


def _utc_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).isoformat(
        timespec="seconds"
    )


def _age_days(iso: str, now: float | None = None) -> float:
    try:
        then = datetime.fromisoformat(iso).timestamp()
    except Exception:
        return float("inf")
    return max(0.0, ((now if now is not None else time.time()) - then) / 86400.0)


def _is_volatile(subject_key: str) -> bool:
    key = (subject_key or "").strip().lower()
    return bool(key) and key.startswith(VOLATILE_PREFIXES)


def _redact(text: str) -> str:
    """Strip secrets, emails, URLs, and paths before text is embedded or stored."""
    from core.session_compact import SessionCompact

    return SessionCompact._redact(text)


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split on paragraphs, packing up to ``size`` chars; hard-split oversize paragraphs."""
    paragraphs = [_SPACE_RE.sub(" ", p).strip() for p in _PARA_RE.split(text or "")]
    chunks: list[str] = []
    current = ""
    for para in (p for p in paragraphs if p):
        while len(para) > size:
            cut = para.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:cut].strip())
            para = para[max(cut - overlap, 1):].strip()
        if current and len(current) + len(para) + 2 > size:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = (tail + "\n" + para).strip() if tail else para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


@dataclass
class Hit:
    kind: str
    text: str
    cosine: float
    recorded_at: str
    age_days: float
    stale: bool
    source: str = ""
    subject_key: str = ""
    meta: dict = field(default_factory=dict)


class KnowledgeIndex:
    """Lazy ChromaDB wrapper. ``embedding_fn`` can be injected (tests use a fake)."""

    def __init__(self, db_dir: Path | str | None = None, embedding_fn=None):
        self.db_dir = Path(db_dir) if db_dir else DEFAULT_DB_DIR
        self._embedding_fn = embedding_fn
        self._client = None
        self._cols: dict[str, object] = {}

    # ---- setup -------------------------------------------------------------
    def _collection(self, name: str):
        if name in self._cols:
            return self._cols[name]
        import chromadb

        if self._embedding_fn is None:
            from chromadb.utils import embedding_functions

            self._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=EMBEDDING_MODEL
            )
        if self._client is None:
            self.db_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.db_dir))
        col = self._client.get_or_create_collection(
            name=name,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine", "embedding_model": EMBEDDING_MODEL},
        )
        self._cols[name] = col
        return col

    def count(self, kind: str) -> int:
        return self._collection(DOCS_COLLECTION if kind == "docs" else ACTIONS_COLLECTION).count()

    # ---- documents -----------------------------------------------------------
    def sync_document(self, path: Path | str, *, text: str | None = None) -> str:
        """Index one file. Returns "added", "updated", "unchanged", or "empty".

        A changed file drops every old chunk first, so stale text cannot survive.
        """
        path = Path(path)
        raw = text if text is not None else path.read_text(encoding="utf-8", errors="replace")
        clean = raw  # redacted per chunk below so paragraph structure survives
        digest = hashlib.sha256(clean.encode("utf-8", "replace")).hexdigest()
        col = self._collection(DOCS_COLLECTION)
        key = str(path)
        existing = col.get(where={"path": key}, include=["metadatas"])
        old_hashes = {m.get("content_hash") for m in (existing.get("metadatas") or [])}
        if old_hashes == {digest}:
            return "unchanged"
        if existing["ids"]:
            col.delete(ids=existing["ids"])
        pieces = [c for c in (_redact_keep_lines(p) for p in chunk_text(clean)) if c]
        if not pieces:
            return "empty"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = time.time()
        stamp = _utc_iso(mtime)
        base = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]
        col.add(
            ids=[f"{base}-{i}" for i in range(len(pieces))],
            documents=pieces,
            metadatas=[
                {
                    "path": key,
                    "name": path.name,
                    "chunk": i,
                    "content_hash": digest,
                    "recorded_at": stamp,
                    "indexed_at": _utc_iso(),
                }
                for i in range(len(pieces))
            ],
        )
        return "updated" if existing["ids"] else "added"

    def remove_missing(self, live_paths: set[str]) -> int:
        """Delete chunks whose file is no longer in ``live_paths``. Returns chunks removed."""
        col = self._collection(DOCS_COLLECTION)
        data = col.get(include=["metadatas"])
        stale_ids = [
            i for i, m in zip(data["ids"], data["metadatas"]) if m.get("path") not in live_paths
        ]
        if stale_ids:
            col.delete(ids=stale_ids)
        return len(stale_ids)

    # ---- actions -------------------------------------------------------------
    def record_action(
        self,
        summary: str,
        *,
        action: str,
        subject_key: str = "",
        target: str = "",
        status: str = "ok",
        when: float | None = None,
    ) -> str:
        """Append one action record. ``summary`` is redacted and capped at 300 chars."""
        body = _redact(summary)[:ACTION_MAX_CHARS].strip()
        if not body:
            raise ValueError("action summary is empty")
        stamp = _utc_iso(when)
        ident = hashlib.sha1(f"{stamp}|{action}|{subject_key}|{body}".encode("utf-8", "replace")).hexdigest()[:20]
        self._collection(ACTIONS_COLLECTION).upsert(
            ids=[ident],
            documents=[body],
            metadatas=[
                {
                    "action": action,
                    "subject_key": (subject_key or "").strip().lower(),
                    "target": _redact(target)[:120],
                    "status": status,
                    "recorded_at": stamp,
                }
            ],
        )
        return ident

    # ---- search ----------------------------------------------------------------
    def search(
        self,
        query: str,
        kind: str,
        *,
        top_k: int = TOP_K,
        min_cosine: float = MIN_COSINE,
        margin: float = RELATIVE_MARGIN,
        now: float | None = None,
    ) -> list[Hit]:
        if kind not in ("docs", "actions"):
            raise ValueError("kind must be 'docs' or 'actions'")
        query = (query or "").strip()
        col = self._collection(DOCS_COLLECTION if kind == "docs" else ACTIONS_COLLECTION)
        size = col.count()
        if not query or size == 0:
            return []
        res = col.query(
            query_texts=[query],
            n_results=min(FETCH_K, size),
            include=["documents", "distances", "metadatas"],
        )
        hits: list[Hit] = []
        for doc, dist, meta in zip(res["documents"][0], res["distances"][0], res["metadatas"][0]):
            cosine = 1.0 - float(dist)  # chroma cosine distance = 1 - cos
            if cosine < min_cosine:
                continue
            recorded = str(meta.get("recorded_at", ""))
            age = _age_days(recorded, now)
            subject = str(meta.get("subject_key", ""))
            limit = STALE_DAYS_VOLATILE if _is_volatile(subject) else STALE_DAYS_DEFAULT
            hits.append(
                Hit(
                    kind=kind,
                    text=doc,
                    cosine=round(cosine, 4),
                    recorded_at=recorded,
                    age_days=round(age, 2),
                    stale=age > limit,
                    source=str(meta.get("name") or meta.get("action") or ""),
                    subject_key=subject,
                    meta=dict(meta),
                )
            )
        if not hits:
            return []
        if kind == "actions":
            hits = _newest_per_subject(hits)
        hits.sort(key=lambda h: (-h.cosine, h.age_days))
        best = hits[0].cosine
        hits = [h for h in hits if h.cosine >= best - margin]
        return hits[: max(1, int(top_k))]


def _redact_keep_lines(text: str) -> str:
    """Redact per line so paragraph structure survives; drop fully-sensitive lines."""
    out = []
    for line in text.splitlines():
        cleaned = _redact(line)
        if cleaned and cleaned != "[sensitive content omitted]":
            out.append(cleaned)
    return "\n".join(out).strip()


def _newest_per_subject(hits: list[Hit]) -> list[Hit]:
    """Collapse hits sharing a subject_key to the newest, so an old value never outranks it."""
    newest: dict[str, Hit] = {}
    rest: list[Hit] = []
    for h in hits:
        if not h.subject_key:
            rest.append(h)
        elif h.subject_key not in newest or h.recorded_at > newest[h.subject_key].recorded_at:
            newest[h.subject_key] = h
    return rest + list(newest.values())


def format_for_prompt(hits: list[Hit], budget_tokens: int = RECALL_BUDGET_TOKENS) -> str:
    """Render hits as one labelled block that FITS the budget (truncate, never drop all).

    Each line states when it was recorded, and flags stale items, because none of this
    is current evidence.
    """
    from core.user_model import estimate_tokens

    if not hits:
        return ""
    header = "[RECORDED CONTEXT — historical, may be out of date; not current evidence]"
    lines = [header]
    used = estimate_tokens(header)
    for h in hits:
        day = h.recorded_at[:10] or "unknown date"
        tag = " (stale)" if h.stale else ""
        src = f" {h.source}" if h.source else ""
        prefix = f"- [{h.kind}{src} · {day}{tag}] "
        room = budget_tokens - used - estimate_tokens(prefix)
        if room <= 20:
            break
        body = h.text.strip()
        while body and estimate_tokens(body) > room:
            body = body[: int(len(body) * 0.8)].rstrip()
        if not body:
            break
        line = prefix + body
        lines.append(line)
        used += estimate_tokens(line)
    return "\n".join(lines) if len(lines) > 1 else ""
