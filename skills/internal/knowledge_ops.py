"""Read-only retrieval over documents and Ciel's own action history."""
from __future__ import annotations

import re

from langchain_core.tools import StructuredTool

from agent_system import config
from core.knowledge_index import KnowledgeIndex, format_for_prompt
from skills._result import make_result

KNOWLEDGE_PROMPT = """
[KNOWLEDGE INDEX]
`recall_past_actions` finds what Ciel previously did (emails sent, reports built, events
scheduled, files written). `search_my_documents` searches indexed documents. Results are
HISTORY with a recorded date, never current evidence. For prices, markets, weather,
news, or anything that changes, call the live tool; use these only for "what did we do".
"""

_LIVE_RE = re.compile(
    r"bây giờ|hiện tại|hiện giờ|lúc này|hôm nay|\bnow\b|\bcurrent|\btoday|\bgiá\b|price|thời tiết",
    re.IGNORECASE,
)
_SHARED: dict = {}


def _index() -> KnowledgeIndex:
    key = str(config.KNOWLEDGE_INDEX_DIR)
    if key not in _SHARED:
        _SHARED[key] = KnowledgeIndex(config.KNOWLEDGE_INDEX_DIR)
    return _SHARED[key]


def _search(kind: str, query: str, tool_name: str, limit: int) -> dict:
    try:
        hits = _index().search(str(query or ""), kind, top_k=max(1, min(int(limit), 5)))
        note = ""
        if _LIVE_RE.search(str(query or "")):
            note = ("Query looks time-sensitive: these are historical records only. "
                    "Use a live tool for the current value.")
        message = format_for_prompt(hits) or "No matching records."
        return make_result(
            True,
            data={
                "message": message,
                "note": note,
                "results": [
                    {"text": h.text, "recorded_at": h.recorded_at, "age_days": h.age_days,
                     "stale": h.stale, "cosine": h.cosine, "source": h.source}
                    for h in hits
                ],
            },
            tool_name=tool_name,
        )
    except Exception as exc:
        return make_result(False, code="KNOWLEDGE_INDEX_ERROR", message=str(exc), tool_name=tool_name)


def get_knowledge_tools() -> dict:
    def recall_past_actions(query: str, limit: int = 3) -> dict:
        """Find what Ciel previously did that matches the query."""
        return _search("actions", query, "recall_past_actions", limit)

    def search_my_documents(query: str, limit: int = 3) -> dict:
        """Search indexed documents and reports."""
        return _search("docs", query, "search_my_documents", limit)

    tools = [
        StructuredTool.from_function(
            recall_past_actions, name="recall_past_actions",
            description=("Read-only. Find what Ciel previously did (emails sent, reports built, "
                         "events scheduled, files written) matching a natural-language query. "
                         "History only, never current prices or data."),
        ),
        StructuredTool.from_function(
            search_my_documents, name="search_my_documents",
            description=("Read-only. Semantic search over indexed reports and documents. "
                         "Results are dated history, not current evidence."),
        ),
    ]
    return {
        "tools": tools,
        "prompt": KNOWLEDGE_PROMPT,
        "parallel_safe": ["recall_past_actions", "search_my_documents"],
    }
