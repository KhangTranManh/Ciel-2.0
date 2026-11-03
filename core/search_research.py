"""Bounded, per-turn evidence policy for guided web research.

Models choose queries and pages. This module owns only observable facts and limits;
web text is never allowed to grant itself another call or become an instruction.
"""
from __future__ import annotations

import ipaddress
import re
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


_URL_RE = re.compile(r"https?://[^\s<>\]\[()\"']+", re.IGNORECASE)
_NO_HITS_RE = re.compile(r"\b(?:no results found|no usable results|0 results found)\b", re.I)
_ERROR_RE = re.compile(r"(?:search api error:|\[tool_error\]|\[execution_error\]|^error:)", re.I)
_SCRAPE_FAILURE_RE = re.compile(
    r"^\s*(?:Scrape Error:|Failed to scrape URL|\[TOOL_ERROR\]|\[EXECUTION_ERROR\])", re.I
)
_CURRENT_RE = re.compile(
    r"\b(?:today|tonight|latest|recent|current|breaking|this week|"
    r"hôm nay|mới nhất|hiện tại|gần đây|tuần này|vừa xảy ra)\b", re.I
)
_PRICE_RE = re.compile(r"\b(?:giá|price|cost|quote|bao nhiêu tiền)\b", re.I)
_PRICE_VALUE_RE = re.compile(
    r"(?:[$€£]\s*\d[\d.,]*|\d[\d.,]*\s*(?:USD|VND|USDT|EUR|đồng|triệu|tỷ|đ))\b", re.I
)
_QUERY_FILLER = {
    "what", "when", "where", "which", "who", "the", "and", "for", "with", "from",
    "today", "latest", "recent", "current", "news", "price", "giá", "bao", "nhiêu",
    "hôm", "nay", "mới", "nhất", "hiện", "tại", "tin", "tức", "cho", "của", "về",
}
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]{1,160})\]\((https?://[^)]+)\)", re.I)


def normalize_query(query: str) -> str:
    return " ".join((query or "").casefold().split())


def canonical_url(raw: str) -> str:
    """Canonical public URL for de-duplication, not a DNS/SSRF guarantee."""
    try:
        parts = urlsplit((raw or "").strip().rstrip(".,;:!?"))
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return ""
        host = parts.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        netloc = host + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))
    except ValueError:
        return ""


def urls_in(text: str) -> list[str]:
    urls = []
    for match in _URL_RE.findall(text or ""):
        url = canonical_url(match)
        if url and url not in urls:
            urls.append(url)
    return urls


def constrain_citations(answer: str, allowed_urls: set[str]) -> str:
    """Never let a generated source link point outside the observed evidence."""
    allowed = {canon for u in allowed_urls if (canon := canonical_url(u))}

    def markdown_link(match):
        return match.group(0) if canonical_url(match.group(2)) in allowed else match.group(1)

    clean = _MARKDOWN_LINK_RE.sub(markdown_link, answer or "")
    return _URL_RE.sub(
        lambda match: match.group(0) if canonical_url(match.group(0)) in allowed
        else "[unverified URL omitted]", clean,
    )


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    publisher: str = ""
    provider: str = ""
    snippet: str = ""
    published_at: str = ""


def parse_search_hits(text: str) -> list[SearchHit]:
    """Read the existing human-readable stealth_search contract without trusting it."""
    provider_match = re.search(r"^Search Results .*?source: (.*?)\):", text or "", re.M)
    provider = provider_match.group(1) if provider_match else ""
    hits: list[SearchHit] = []
    item: dict[str, str] = {}
    for line in (text or "").splitlines():
        title = re.match(r"^\d+\. Title: (.*)$", line)
        if title:
            if item.get("url"):
                hits.append(SearchHit(**{"provider": provider, **item}))
            item = {"title": title.group(1).strip(), "url": ""}
            continue
        if not item:
            continue
        field = re.match(r"^\s+(Published|Source|Provider|URL|Snippet):\s*(.*)$", line)
        if not field:
            continue
        key, value = field.group(1), field.group(2).strip()
        if key == "URL":
            item["url"] = canonical_url(value)
        elif key == "Source":
            item["publisher"] = value[:160]
        elif key == "Provider":
            item["provider"] = value[:160]
        elif key == "Published":
            item["published_at"] = "" if value.startswith("UNKNOWN") else value[:64]
        elif key == "Snippet":
            item["snippet"] = value[:1500]
    if item.get("url"):
        hits.append(SearchHit(**{"provider": provider, **item}))
    return list({hit.url: hit for hit in hits}.values())


def evidence_gap(user_input: str, result: str, tool_args: dict | None = None) -> str:
    """Structural gap only; semantic relevance remains the Brain's decision."""
    text = result or ""
    if _ERROR_RE.search(text[:300]):
        return "provider_error"
    if _NO_HITS_RE.search(text[:300]):
        return "no_hits"
    hits = parse_search_hits(text)
    if not hits:
        return "no_verifiable_urls"
    args = tool_args or {}
    if (args.get("timelimit") or _CURRENT_RE.search(user_input or "")) and not any(
        h.published_at for h in hits
    ):
        return "no_dated_evidence"
    if not any(len(h.snippet) >= 35 for h in hits):
        return "thin_snippets"
    terms = {w for w in re.findall(r"[\wÀ-ỹ]+", (user_input or "").casefold())
             if len(w) > 2 and w not in _QUERY_FILLER}
    if terms and not any(any(w in (h.title + " " + h.snippet).casefold() for w in terms)
                         for h in hits):
        return "off_topic_hits"
    if _PRICE_RE.search(user_input or "") and not any(
        _PRICE_VALUE_RE.search(h.snippet) for h in hits
    ):
        return "no_price_evidence"
    return ""


def scrape_failed(result: str) -> bool:
    return bool(_SCRAPE_FAILURE_RE.search(result or ""))


class SearchTurnState:
    """One shared cap across initial plans, correction, continuation and fallback."""

    def __init__(self, max_queries: int = 2, max_reads: int = 2, max_calls: int = 4):
        self.max_queries = max_queries
        self.max_reads = max_reads
        self.max_calls = max_calls
        self.queries: set[str] = set()
        self.reads: set[str] = set()
        self.result_urls: set[str] = set()
        self.calls = 0
        self._lock = threading.RLock()

    def reserve(self, tool_name: str, args: dict, user_input: str = "",
                active_urls: list[str] | None = None) -> tuple[dict | None, str]:
        """Return normalized arguments or a reason to skip before invoking a tool."""
        if tool_name not in {"stealth_search", "smart_scrape"}:
            return dict(args or {}), ""
        args = dict(args or {})
        with self._lock:
            if self.calls >= self.max_calls:
                return None, "web-call limit reached"
            if tool_name == "stealth_search":
                query = str(args.get("query") or "").strip()[:200]
                key = normalize_query(query)
                if not key:
                    return None, "empty search query"
                if key in self.queries:
                    return None, "query already searched this turn"
                if len(self.queries) >= self.max_queries:
                    return None, "search-query limit reached"
                args["query"] = query
                self.queries.add(key)
            else:
                allowed = set(self.result_urls)
                allowed.update(urls_in(user_input))
                allowed.update(canonical_url(u) for u in (active_urls or []))
                candidates = urls_in(str(args.get("url") or ""))
                url = next((u for u in candidates if u in allowed), "")
                if not url:
                    return None, "page URL is not a search result or user-supplied URL"
                if url in self.reads:
                    return None, "page already read this turn"
                if len(self.reads) >= self.max_reads:
                    return None, "page-read limit reached"
                args["url"] = url
                self.reads.add(url)
            self.calls += 1
        return args, ""

    def observe(self, tool_name: str, result: str) -> None:
        if tool_name != "stealth_search":
            return
        with self._lock:
            self.result_urls.update(hit.url for hit in parse_search_hits(result))

    def can_continue(self, result: str, user_input: str, args: dict | None = None) -> bool:
        gap = evidence_gap(user_input, result, args)
        if not gap or gap == "provider_error":
            return False
        with self._lock:
            return self.calls < self.max_calls and (
                len(self.queries) < self.max_queries or
                (bool(self.result_urls) and len(self.reads) < self.max_reads)
            )

    def can_recover_scrape(self) -> bool:
        with self._lock:
            return self.calls < self.max_calls and (
                len(self.queries) < self.max_queries or
                (bool(self.result_urls - self.reads) and len(self.reads) < self.max_reads)
            )
