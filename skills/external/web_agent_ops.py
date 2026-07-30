import os
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlparse, quote

from langchain_core.tools import StructuredTool

# Google News RSS locales. Chosen per the QUERY's language so a Vietnamese query hits
# Vietnamese outlets and an English one hits international ones — the region/language
# targeting DuckDuckGo has no equivalent for.
_GNEWS_LOCALES = {
    "Vietnamese": ("vi", "VN", "VN:vi"),
    "English":    ("en-US", "US", "US:en"),
    "Japanese":   ("ja", "JP", "JP:ja"),
    "Chinese":    ("zh-CN", "CN", "CN:zh-Hans"),
    "Korean":     ("ko", "KR", "KR:ko"),
    "Russian":    ("ru", "RU", "RU:ru"),
}

_VN_DIACRITICS = set("ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụỳýỷỹỵ")

# How stale an item may be for a given timelimit. DuckDuckGo's own `timelimit` proved
# unreliable (a 'd' window still returned 8–16-day-old hits); with real publication
# dates in hand the window is now enforced in code instead of trusted.
_WINDOW_DAYS = {"d": 2, "w": 8, "m": 32, "y": 366}


# Words that only express "give me the news", carrying no actual topic. A query built
# solely from these means "what is happening today" — which must hit Google News' TOP
# STORIES feed, not its keyword search: searching for "top news headlines today" matches
# articles *titled* that (roundups like "School Assembly News Headlines"), whereas the
# feed returns the actual lead stories.
_GENERIC_NEWS_WORDS = set("""
tin tức thời sự nổi bật hôm nay nay ngày mới nhất cập nhật trong của gì có về là các
tổng hợp tóm tắt điểm chính quan trọng gần đây hiện
top news headline headlines today latest breaking current main summary summarize
the a an of in for me please what happening world
""".split())


def _is_generic_news_query(q: str) -> bool:
    words = re.findall(r"[\wÀ-ỹ]+", (q or "").lower())
    if not words:
        return True
    return not [w for w in words if w not in _GENERIC_NEWS_WORDS and not w.isdigit()]


def _detect_query_lang(text: str) -> str:
    """Language of the SEARCH QUERY (self-contained; skills don't import from core/)."""
    t = (text or "").lower()
    if any(ch in _VN_DIACRITICS for ch in t):
        return "Vietnamese"
    for lo, hi, name in (("぀", "ヿ", "Japanese"), ("가", "힯", "Korean"),
                         ("Ѐ", "ӿ", "Russian"), ("一", "鿿", "Chinese")):
        if any(lo <= ch <= hi for ch in t):
            return name
    return "English"


def _strip_html(s: str) -> str:
    """Tags out, entities decoded (RSS descriptions arrive as escaped HTML)."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()

# Queries that mean "what happened recently" — these get the news endpoint (which carries
# a real publication date + source) instead of plain text search (which carries neither).
_RECENCY_TERMS = ("latest", "recent", "news", "today", "now", "breaking", "update",
                  "mới nhất", "hiện tại", "cập nhật", "hôm nay", "gần đây", "tin tức",
                  "this week", "tuần này", "diễn biến")

# Landing/index pages ("Tin tức 24h", "Global Economic Prospects" overview) match a query
# but describe a SECTION, not an event — their snippets are generic blurbs. Observed live:
# such a hit produced the content-free summary line "World Bank published an outlook
# focusing on GDP, inflation and risks". Detected so they can be dropped/flagged.
_INDEX_SNIPPET_HINTS = (
    "learn about", "read the latest", "stay updated", "latest news", "breaking news",
    "trang chủ", "home page", "homepage", "tin tức 24h", "tin nhanh 24h",
    "cập nhật tin tức", "đọc báo", "chuyên trang", "all the latest",
)


def _parse_dt(raw: str):
    """datetime (UTC) from an ISO-8601 OR RFC-822 string (RSS uses the latter)."""
    if not raw:
        return None
    for parse in (lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
                  parsedate_to_datetime):
        try:
            dt = parse(str(raw))
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except Exception:
            continue
    return None


def _fmt_date(raw: str):
    """('2026-07-24', 'today'/'3 days ago', dt) — ('', '', None) if unparseable."""
    dt = _parse_dt(raw)
    if dt is None:
        return (str(raw) if raw else ""), "", None
    days = (datetime.now(timezone.utc) - dt).days
    age = "today" if days <= 0 else ("1 day ago" if days == 1 else f"{days} days ago")
    return dt.strftime("%Y-%m-%d"), age, dt


def _google_news_rss(query: str, max_results: int, lang: str):
    """Primary news source: Google News RSS. Free, no API key, returns a real pubDate +
    outlet per item and supports language/region targeting. Verified far fresher than
    the DuckDuckGo news index (hours old vs 8–16 days) and free of tabloid filler.
    Unofficial endpoint, so callers must fall back to ddgs on any failure."""
    hl, gl, ceid = _GNEWS_LOCALES.get(lang, _GNEWS_LOCALES["English"])
    if _is_generic_news_query(query):
        url = f"https://news.google.com/rss?hl={hl}&gl={gl}&ceid={ceid}"      # top stories
    else:
        url = (f"https://news.google.com/rss/search?q={quote(query)}"
               f"&hl={hl}&gl={gl}&ceid={ceid}")
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    rows = []
    for item in root.findall(".//item")[: max_results * 4]:   # extra, recency filter prunes
        title = (item.findtext("title") or "").strip()
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if source and title.endswith(f" - {source}"):         # RSS repeats the outlet
            title = title[: -len(f" - {source}")].strip()
        date, age, dt = _fmt_date(item.findtext("pubDate") or "")
        body = _strip_html(item.findtext("description") or "")
        if len(body) < 30 or body[:40] == title[:40]:         # RSS body is often just a link
            body = title
        rows.append({"title": title, "url": (item.findtext("link") or "").strip(),
                     "body": body, "date": date, "age": age, "dt": dt, "source": source})
    return rows


_SERPAPI_TBS = {"d": "qdr:d", "w": "qdr:w", "m": "qdr:m", "y": "qdr:y"}

_RELATIVE_AGE_RE = re.compile(
    r"^\s*(\d+)\s*(phút|giờ|ngày|tuần|tháng|năm|"
    r"minute|min|hour|hr|day|week|month|year)s?\s*(?:ago|trước)?\s*$",
    re.IGNORECASE,
)
_RELATIVE_AGE_UNIT_DAYS = {
    "phút": 0, "minute": 0, "min": 0,
    "giờ": 0, "hour": 0, "hr": 0,
    "ngày": 1, "day": 1,
    "tuần": 7, "week": 7,
    "tháng": 30, "month": 30,
    "năm": 365, "year": 365,
}


def _parse_relative_age(raw: str):
    """SerpApi's Google engine reports freshness as a relative string on the result
    itself ("12 hours ago", "2 ngày trước") rather than a real timestamp — this is
    exactly the freshness signal visible on a real google.com/search page that
    Google News RSS / DDG's separate index don't carry for ordinary web results.
    Returns a real UTC datetime, or None if the string isn't a recognized relative
    age (e.g. it's an absolute date instead — caller should try _parse_dt for that)."""
    if not raw:
        return None
    m = _RELATIVE_AGE_RE.match(raw)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit in ("phút", "minute", "min", "giờ", "hour", "hr"):
        return datetime.now(timezone.utc) - timedelta(hours=n if "gi" in unit or "hour" in unit or "hr" in unit else 0,
                                                        minutes=n if unit in ("phút", "minute", "min") else 0)
    days = _RELATIVE_AGE_UNIT_DAYS.get(unit, 0) * n
    return datetime.now(timezone.utc) - timedelta(days=days)


def _serpapi_search(query: str, max_results: int, timelimit: str) -> list:
    """Real Google web search results via SerpApi — the same index/ranking/freshness
    a human sees on google.com/search (verified live: fresh, on-topic Vietnamese
    finance results a Vietnamese-locale DuckDuckGo query missed entirely), instead of
    DuckDuckGo's separate, often thinner index or Google News RSS's narrower "News"
    vertical. Needs SEARCH_API_KEY (+ API_ENDPOINT, defaults to SerpApi's Google
    engine) in .env — returns [] (never raises) when the key is missing or the
    request fails, so callers fall back to the pre-existing RSS/DDG chain unchanged.
    `tbs=qdr:X` is Google's own native recency filter, mapped from `timelimit`."""
    api_key = os.getenv("SEARCH_API_KEY", "")
    if not api_key:
        return []
    endpoint = os.getenv("API_ENDPOINT", "https://serpapi.com/search?engine=google")

    params = {"api_key": api_key, "q": query, "num": min(max(max_results, 1), 10)}
    if timelimit in _SERPAPI_TBS:
        params["tbs"] = _SERPAPI_TBS[timelimit]

    try:
        resp = requests.get(endpoint, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    rows = []
    for item in data.get("organic_results", []):
        raw_date = (item.get("date") or "").strip()
        dt = _parse_relative_age(raw_date) or _parse_dt(raw_date)
        date = dt.strftime("%Y-%m-%d") if dt else (raw_date or "")
        age = raw_date if (raw_date and dt) else ""
        rows.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "body": item.get("snippet", ""),
            "date": date, "age": age, "dt": dt,
            "source": item.get("source") or (urlparse(item.get("link", "")).netloc or ""),
        })
    return rows


def _is_index_page(url: str, title: str, body: str, source: str = "") -> bool:
    """True when a hit looks like a site/section landing page rather than one article."""
    blob = f"{title or ''} {body or ''}".lower()
    if any(h in blob for h in _INDEX_SNIPPET_HINTS):
        return True
    # An outlet's own front/section page titles itself after the outlet
    # ("NBC News - Breaking Headlines and Video Reports on..."). Language-agnostic and
    # more reliable than keyword lists, which only catch the phrasings seen so far.
    t, s = (title or "").strip().lower(), (source or "").strip().lower()
    if s and len(s) > 2 and t.startswith(s) and len(t) > len(s) + 5:
        return True
    try:                                    # bare domain / one shallow segment
        path = (urlparse(url or "").path or "/").strip("/")
        return path == "" or (path.count("/") == 0 and len(path) <= 12)
    except Exception:
        return False

WEB_AGENT_SYSTEM_PROMPT = """
[CIEL STEALTH WEB AGENT]
Role: You have live internet access to search and read websites.

TOOLS:
1. `stealth_search`: Search the web for a query. Returns a list of URLs and snippets.
2. `smart_scrape`: Read the full content of a specific URL. It converts the webpage into clean Markdown.

RULES OF ENGAGEMENT:
1. ALWAYS use `stealth_search` first if you don't know the answer to a current event, news, or technical documentation question.
2. If the search snippets are not enough, use `smart_scrape` on the most relevant URL to read the full article.
3. NEVER guess information if it's recent (post-2024). Search the web.
4. When summarizing scraped data, keep it extremely concise.
5. Provide URLs as citations if requested.
"""

def get_web_tools() -> dict:
    try:
        tools = []
        
        def stealth_search(query: str, max_results: int = 5, timelimit: str = "") -> str:
            """Search the internet for current information, news, or documentation.

            Returns each hit with its PUBLICATION DATE and SOURCE when available, so the
            answer can state when something happened instead of guessing.

            timelimit restricts results by recency: 'd' (day), 'w' (week), 'm' (month),
            'y' (year). Leave empty for all-time. A "latest/recent/news" style query
            automatically gets a recent window AND is served from the news index, which
            carries real dates; plain web results have no date field at all.
            """
            try:
                from ddgs import DDGS

                q_low = (query or "").lower()
                is_news_intent = any(t in q_low for t in _RECENCY_TERMS)
                if not timelimit and is_news_intent:
                    timelimit = "m"  # past month

                kwargs = {"max_results": max_results}
                if timelimit:
                    kwargs["timelimit"] = timelimit

                rows = []          # normalized: title, url, body, date, age, dt, source
                source_used = "web"

                # PRIMARY, unconditional: real Google web search via SerpApi — the
                # same index/ranking a human typing into google.com/search hits, a
                # different and for most queries stronger index than DuckDuckGo's.
                # Silently returns [] when SEARCH_API_KEY isn't set or the request
                # fails — everything below is unchanged and only runs when this
                # produced nothing, so it's a pure fallback chain now, not a parallel
                # path.
                rows = _serpapi_search(query, max_results, timelimit)
                if rows:
                    source_used = "Google (SerpApi)"

                if not rows:
                    # PRIMARY for news-intent (fallback tier 1): Google News RSS.
                    if is_news_intent:
                        try:
                            rows = _google_news_rss(query, max_results, _detect_query_lang(query))
                            if rows:
                                source_used = "Google News RSS"
                        except Exception:
                            rows = []   # unofficial endpoint — fall through to DuckDuckGo

                    with DDGS() as ddgs:
                        # FALLBACK: DuckDuckGo news index (also dated) when RSS gave nothing.
                        if is_news_intent and not rows:
                            try:
                                for r in ddgs.news(query, **kwargs):
                                    d, age, dt = _fmt_date(r.get("date", ""))
                                    rows.append({
                                        "title": r.get("title"), "url": r.get("url"),
                                        "body": r.get("body"), "date": d, "age": age, "dt": dt,
                                        "source": r.get("source", ""),
                                    })
                                if rows:
                                    source_used = "DDG news (RSS fallback)"
                            except Exception:
                                pass

                        # FALLBACK / non-news: plain web results (no dates available).
                        if len(rows) < max_results:
                            seen = {r["url"] for r in rows}
                            for r in ddgs.text(query, **kwargs):
                                if r.get("href") in seen:
                                    continue
                                rows.append({
                                    "title": r.get("title"), "url": r.get("href"),
                                    "body": r.get("body"), "date": "", "age": "", "dt": None,
                                    "source": "",
                                })
                                if len(rows) >= max_results:
                                    break
                            if rows and source_used == "web":
                                source_used = "web"

                # Enforce the recency window on real dates (DuckDuckGo's own timelimit
                # proved unreliable). Undated items are kept — they are not provably stale.
                #
                # Found live: this used to require `len(fresh) >= 3` before applying the
                # filter, so a niche/low-coverage query (e.g. Vietnamese "world news
                # roundup") with only 1-2 genuinely fresh hits fell through to the FULL
                # unfiltered pool — a real request for `timelimit="d"` (past 2 days)
                # silently returned an article 216 days old. The tool was still honest
                # (real Published date + computed age shown), but the recency request
                # itself was defeated. Now: use whatever fresh results exist, even just
                # one — fewer honest results beats padding with old ones. Only fall back
                # to the unfiltered pool when NOTHING survives the window at all.
                if timelimit in _WINDOW_DAYS:
                    cutoff = datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS[timelimit])
                    fresh = [r for r in rows if r.get("dt") is None or r["dt"] >= cutoff]
                    if fresh:
                        rows = fresh
                # NOTE: do NOT truncate to max_results yet — the landing-page filter below
                # needs the larger pool, otherwise its "keep everything if <3 survive"
                # guard trips and lets index pages back in.

                today = datetime.now().strftime("%Y-%m-%d")
                if not rows:
                    return f"No results found for '{query}' (as of {today}, timelimit={timelimit or 'all'})."

                # Drop section/landing pages when enough real articles remain (they only
                # yield content-free summary lines); otherwise keep but flag them.
                keep = [r for r in rows
                        if not _is_index_page(r["url"], r["title"], r["body"], r.get("source", ""))]
                dropped = len(rows) - len(keep)
                if len(keep) < 3:
                    keep, dropped = rows, 0
                keep = keep[:max_results]          # trim only after filtering

                window = {"d": "past day", "w": "past week", "m": "past month",
                          "y": "past year"}.get(timelimit, "all time")
                output = (f"Search Results for '{query}' (as of {today}, recency: {window}, "
                          f"source: {source_used}):\n")
                for i, r in enumerate(keep, 1):
                    output += f"{i}. Title: {r['title']}\n"
                    if r["date"]:
                        output += f"   Published: {r['date']}" + (f" ({r['age']})\n" if r["age"] else "\n")
                    else:
                        output += "   Published: UNKNOWN (web result — no date available; do NOT state a date for this item)\n"
                    if r["source"]:
                        output += f"   Source: {r['source']}\n"
                    output += f"   URL: {r['url']}\n"
                    # RSS carries no real summary — its <description> is just the headline
                    # again. Emit the snippet only when it adds something beyond the title.
                    _b, _t = (r["body"] or "").strip(), (r["title"] or "").strip()
                    if _b and _t and not _b.startswith(_t) and _b != _t:
                        output += f"   Snippet: {r['body']}\n"
                    output += "\n"

                if dropped:
                    output += f"[{dropped} section/landing page(s) omitted — they contained no specific event.]\n"
                output += ("[SYSTEM HINT: Report each item's Published date. Never invent a date for an "
                           "UNKNOWN item. If you need full article text, use 'smart_scrape' on a URL above.]")
                return output
            except ImportError:
                return "Error: The 'ddgs' library is not installed. Please run: pip install ddgs"
            except Exception as e:
                return f"Search API Error: {e}"

        search_tool = StructuredTool.from_function(
            func=stealth_search,
            name="stealth_search",
            description="Search the internet using DuckDuckGo. YOU MUST USE THIS to look up live information, news, or facts you do not know."
        )

        def smart_scrape(url: str) -> str:
            """Extract clean Markdown content from any website URL."""
            try:
                # Use Jina Reader API to bypass bot protections and get clean markdown
                jina_url = f"https://r.jina.ai/{url}"
                headers = {
                    "X-Return-Format": "markdown",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                response = requests.get(jina_url, headers=headers, timeout=15)
                
                if response.status_code == 200:
                    text = response.text
                    return text[:30000] + ("\n...[Truncated for length]" if len(text) > 30000 else "")
                else:
                    return f"Failed to scrape URL. Status code: {response.status_code}. The site might have advanced bot protection."
            except Exception as e:
                return f"Scrape Error: {e}"

        scrape_tool = StructuredTool.from_function(
            func=smart_scrape,
            name="smart_scrape",
            description="Read the full content of a webpage. Pass the exact URL returned by stealth_search to read the full article."
        )

        tools.extend([search_tool, scrape_tool])
        return {"tools": tools, "prompt": WEB_AGENT_SYSTEM_PROMPT}

    except Exception as e:
        print(f"[Ciel Warning] Failed to load Web Agent tools: {e}")
        return {"tools": [], "prompt": ""}
