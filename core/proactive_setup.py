"""Shared proactive wiring for CLI, Telegram, and API entry points."""
from __future__ import annotations


def setup_proactive(ciel, scheduler, *, include_cli: bool = False):
    """Attach selected Tier-6 triggers and channels to ``scheduler``.

    Returns ``(presence, notifier)``.  Both values are ``None`` when proactivity is
    disabled or no selected trigger can be built.  All failures are fail-open so a
    planner/configuration problem never prevents the assistant from starting.
    """
    from agent_system import config

    if not config.PROACTIVE_ENABLED or not config.PROACTIVE_TRIGGERS:
        return None, None
    try:
        from core.notifier import CliChannel, Notifier, Presence, TelegramChannel
        from core.planner_triggers import build_planner_triggers
        from core.reminder_triggers import build_reminder_triggers
        from core.triggers import TriggerEngine, build_triggers, parse_price_alerts

        presence = Presence(idle_threshold=config.PROACTIVE_IDLE_SECONDS) if include_cli else None
        channels = []
        if presence is not None:
            channels.append(CliChannel(presence))
        channels.append(TelegramChannel())

        notifier = Notifier(
            state_path=ciel.core.base_dir / "ciel_data" / "state" / "notify.json",
            channels=channels,
            daily_budget=config.PROACTIVE_DAILY_BUDGET,
            ask_escalate_seconds=config.PROACTIVE_ASK_ESCALATE_SECONDS,
            repeat_limit=config.PROACTIVE_REPEAT_LIMIT,
        )
        todo_path = ciel.core.base_dir / "ciel_workspace" / "todos.json"
        triggers = build_triggers(
            enabled_names=config.PROACTIVE_TRIGGERS,
            task_store=ciel.core.tasks,
            log_path=ciel.core.base_dir / "ciel_data" / "logs" / "thoughts.log",
            notifier=notifier,
            deferred_store=ciel.core.deferred,
            todo_path=todo_path,
            unfinished_min_age=config.PROACTIVE_UNFINISHED_MIN_AGE,
            cost_usd_limit=config.PROACTIVE_COST_USD_LIMIT,
            cost_token_limit=config.PROACTIVE_COST_TOKEN_LIMIT,
            failure_threshold=config.PROACTIVE_FAILURE_THRESHOLD,
            price_alerts=parse_price_alerts(config.PROACTIVE_PRICE_ALERTS),
            important_senders=config.PROACTIVE_IMPORTANT_SENDERS,
            stale_todo_days=config.PROACTIVE_STALE_TODO_DAYS,
            digest_hour=config.PROACTIVE_DIGEST_HOUR,
            digest_minute=config.PROACTIVE_DIGEST_MINUTE,
        )
        triggers.extend(build_planner_triggers(
            enabled_names=config.PROACTIVE_TRIGGERS,
            planner_db=config.PLANNER_DB_PATH,
            todo_path=todo_path,
            timezone_name=config.PLANNER_TIMEZONE,
            monthly_day=config.PLANNER_MONTHLY_DAY,
            monthly_hour=config.PLANNER_MONTHLY_HOUR,
            monthly_minute=config.PLANNER_MONTHLY_MINUTE,
            weekly_weekday=config.PLANNER_WEEKLY_WEEKDAY,
            weekly_hour=config.PLANNER_WEEKLY_HOUR,
            weekly_minute=config.PLANNER_WEEKLY_MINUTE,
        ))
        triggers.extend(build_reminder_triggers(
            enabled_names=config.PROACTIVE_TRIGGERS,
            planner_db=config.PLANNER_DB_PATH,
            timezone_name=config.PLANNER_TIMEZONE,
        ))
        if not triggers:
            print("[Proactive] No configured trigger could be built; background notifications disabled.")
            return None, None

        scheduler.trigger_engine = TriggerEngine(notifier, triggers, logger=ciel.core._log_thought)

        def _mark_unattended():
            ciel.core.unattended = True

        scheduler.mark_unattended = _mark_unattended
        return presence, notifier
    except Exception as exc:
        print(f"[Proactive] disabled: {type(exc).__name__}: {exc}")
        return None, None
