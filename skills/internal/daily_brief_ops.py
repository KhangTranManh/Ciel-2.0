"""Read-only tool surface for the deterministic Daily Brief collector."""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import StructuredTool

from agent_system import config
from core.daily_brief import DailyBriefCollector, DailyBriefReferenceStore
from core.daily_brief_context import DailyBriefExternalContext
from skills._result import make_result


DAILY_BRIEF_PROMPT = """
[DAILY BRIEF]
`get_daily_brief` combines today's weekly actions, open todos, upcoming reminders,
and interrupted/waiting runs. It is read-only. Use it for questions such as "what do
I need to do today?" rather than calling each planning source separately.
"""

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TODO_FILE = BASE_DIR / "ciel_workspace" / "todos.json"
RUNS_DB_PATH = BASE_DIR / "ciel_data" / "runs.db"
REFERENCE_PATH = BASE_DIR / "ciel_data" / "state" / "daily_brief.json"


def get_daily_brief_tools() -> dict:
    def get_daily_brief(
        date: str = "",
        lookahead_hours: int = 24,
        max_items: int = 7,
    ) -> dict:
        """Return one bounded, prioritized, read-only view of today's work."""
        try:
            brief = DailyBriefCollector(
                planner_path=config.PLANNER_DB_PATH,
                todos_path=TODO_FILE,
                runs_path=RUNS_DB_PATH,
                timezone_name=config.PLANNER_TIMEZONE,
            ).collect(date, lookahead_hours, max_items)
            brief = DailyBriefExternalContext(
                gmail_enabled=config.DAILY_BRIEF_GMAIL_ENABLED,
                gmail_max_results=config.DAILY_BRIEF_GMAIL_MAX_RESULTS,
                weather_enabled=config.DAILY_BRIEF_WEATHER_ENABLED,
                weather_city=config.DAILY_BRIEF_WEATHER_CITY,
                calendar_enabled=config.DAILY_BRIEF_CALENDAR_ENABLED,
                calendar_max_results=config.DAILY_BRIEF_CALENDAR_MAX_RESULTS,
                calendar_lookahead_hours=config.DAILY_BRIEF_CALENDAR_LOOKAHEAD_HOURS,
                base_dir=BASE_DIR,
            ).enrich(brief)
            # This is identity-only state for resolving the visible D-numbers.
            # It deliberately excludes due/status metadata, run steps, artifacts,
            # arguments and every form of confirmation or permission grant.
            DailyBriefReferenceStore(
                REFERENCE_PATH,
                timezone_name=config.PLANNER_TIMEZONE,
                ttl_seconds=config.DAILY_BRIEF_REFERENCE_TTL_SECONDS,
            ).save(brief)
            return make_result(True, data=brief, tool_name="get_daily_brief")
        except Exception as exc:
            return make_result(False, code="DAILY_BRIEF_ERROR", message=str(exc),
                               tool_name="get_daily_brief")

    tool = StructuredTool.from_function(
        get_daily_brief,
        name="get_daily_brief",
        description=(
            "Get today's prioritized read-only work brief from weekly tasks, todos, "
            "reminders, and interrupted/waiting runs. Use for 'what should I do today?'."
        ),
    )
    return {"tools": [tool], "prompt": DAILY_BRIEF_PROMPT,
            "parallel_safe": ["get_daily_brief"]}
