import requests
from langchain_core.tools import StructuredTool

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

            timelimit restricts results by recency: 'd' (day), 'w' (week), 'm' (month),
            'y' (year). Leave empty for all-time. When the query signals a "latest/recent/
            news" intent and no timelimit is given, a recent window is applied automatically
            so results reflect NOW, not stale indexed pages.
            """
            from datetime import datetime
            try:
                from ddgs import DDGS

                # Auto-apply a recency window for "latest news" style queries.
                q_low = query.lower()
                recency_terms = ("latest", "recent", "news", "today", "now", "breaking", "update",
                                 "mới nhất", "hiện tại", "cập nhật", "hôm nay", "gần đây", "tin tức")
                if not timelimit and any(t in q_low for t in recency_terms):
                    timelimit = "m"  # past month

                kwargs = {"max_results": max_results}
                if timelimit:
                    kwargs["timelimit"] = timelimit

                with DDGS() as ddgs:
                    results = list(ddgs.text(query, **kwargs))

                today = datetime.now().strftime("%Y-%m-%d")
                if not results:
                    return f"No results found for '{query}' (as of {today}, timelimit={timelimit or 'all'})."

                window = {"d": "past day", "w": "past week", "m": "past month", "y": "past year"}.get(timelimit, "all time")
                output = f"Search Results for '{query}' (as of {today}, recency: {window}):\n"
                for i, res in enumerate(results):
                    output += f"{i+1}. Title: {res.get('title')}\n"
                    output += f"   URL: {res.get('href')}\n"
                    output += f"   Snippet: {res.get('body')}\n\n"

                output += "[SYSTEM HINT: If you need the full live data (like exact current weather, prices, or article text), use 'smart_scrape' on the most relevant URL above!]"
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
