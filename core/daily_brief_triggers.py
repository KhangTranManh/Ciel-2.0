"""Deterministic proactive delivery for the local Daily Brief."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .daily_brief import DailyBriefCollector, DailyBriefReferenceStore, render_daily_brief
from .daily_brief_context import DailyBriefExternalContext
from .notifier import NOTIFY, Notification
from .triggers import Trigger


def _local_datetime(now: float, timezone_name: str) -> datetime:
    """Use the configured planner timezone; invalid configuration fails the check."""
    return datetime.fromtimestamp(now, ZoneInfo(timezone_name))


def make_daily_brief_check(
    collector: DailyBriefCollector,
    reference_store: DailyBriefReferenceStore,
    *,
    timezone_name: str,
    hour: int = 8,
    minute: int = 0,
    lookahead_hours: int = 24,
    max_items: int = 7,
    external_context: DailyBriefExternalContext | None = None,
):
    """Return a catch-up check that stays silent when today's brief has no actions."""
    hour = max(0, min(23, int(hour)))
    minute = max(0, min(59, int(minute)))

    def check(now: float):
        local = _local_datetime(now, timezone_name)
        if (local.hour, local.minute) < (hour, minute):
            return None

        brief = collector.collect(
            lookahead_hours=lookahead_hours,
            max_items=max_items,
            now=local,
        )
        if not brief.get("items"):
            # Preserve Phase-3 silence and avoid polling external providers every five
            # minutes on a day with no local actions.
            reference_store.save(brief, now=local)
            return None
        if external_context is not None:
            brief = external_context.enrich(brief, now=local)
        # Save before delivery so every visible D-number resolves immediately. A failed
        # provider send is harmless: this file contains identity only and the next poll
        # atomically replaces it before retrying.
        reference_store.save(brief, now=local)
        return Notification(
            key=f"daily_brief:{brief['date']}",
            title=f"Daily Brief · {local.strftime('%d/%m/%Y')}",
            detail=render_daily_brief(brief, "Vietnamese"),
            action=(
                "Trả lời bằng một mã D (ví dụ: ‘hoàn thành D1’) để Ciel xử lý qua "
                "các công cụ và kiểm tra an toàn bình thường."
            ),
            urgency=NOTIFY,
            trigger="daily_brief",
            created_at=now,
        )

    return check


def build_daily_brief_triggers(
    *,
    enabled_names=(),
    planner_db: str | Path,
    todo_path: str | Path,
    runs_path: str | Path,
    reference_path: str | Path,
    timezone_name: str,
    hour: int = 8,
    minute: int = 0,
    reference_ttl_seconds: int = DailyBriefReferenceStore.DEFAULT_TTL_SECONDS,
    external_context: DailyBriefExternalContext | None = None,
) -> list[Trigger]:
    """Build the named Daily Brief trigger and nothing when it was not opted in."""
    if "daily_brief" not in set(enabled_names or ()):
        return []
    collector = DailyBriefCollector(
        planner_path=planner_db,
        todos_path=todo_path,
        runs_path=runs_path,
        timezone_name=timezone_name,
    )
    references = DailyBriefReferenceStore(
        reference_path,
        timezone_name=timezone_name,
        ttl_seconds=reference_ttl_seconds,
    )
    return [Trigger(
        name="daily_brief",
        check=make_daily_brief_check(
            collector,
            references,
            timezone_name=timezone_name,
            hour=hour,
            minute=minute,
            external_context=external_context,
        ),
        every_seconds=300.0,
        cooldown_seconds=26 * 3600.0,
    )]
