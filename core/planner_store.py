"""SQLite-backed monthly and weekly planning state for Ciel.

This store is deliberately separate from conversational memory and from the small
``ciel_workspace/todos.json`` checklist.  Monthly goals describe direction; weekly
tasks describe execution for one ISO week; todos remain immediate loose tasks.

Every operation opens its own SQLite connection.  That keeps the store safe when the
scheduler thread reads while a foreground tool writes, without sharing a connection
between threads.  SQLite transactions provide the atomicity that JSON rewrites cannot.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


VALID_STATUSES = frozenset({"active", "completed", "cancelled"})
REMINDER_STATUSES = frozenset({"pending", "delivered", "cancelled"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_month(value: str) -> str:
    """Return a canonical ``YYYY-MM`` month or raise ``ValueError``."""
    raw = str(value or "").strip()
    try:
        parsed = datetime.strptime(raw, "%Y-%m")
    except ValueError as exc:
        raise ValueError("month must use YYYY-MM, for example 2026-08") from exc
    return parsed.strftime("%Y-%m")


def normalize_week_start(value: str) -> str:
    """Normalize any date in a week to that ISO week's Monday."""
    raw = str(value or "").strip()
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("week_start must use YYYY-MM-DD") from exc
    monday = parsed - timedelta(days=parsed.weekday())
    return monday.isoformat()


def normalize_time(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, "%H:%M")
    except ValueError as exc:
        raise ValueError("time must use 24-hour HH:MM, for example 09:30") from exc
    return parsed.strftime("%H:%M")


def normalize_status(value: str) -> str:
    status = str(value or "").strip().lower()
    if status not in VALID_STATUSES:
        raise ValueError("status must be active, completed, or cancelled")
    return status


def normalize_reminder_due(value: str, timezone_name: str) -> tuple[str, str]:
    """Return ``(UTC ISO timestamp, canonical timezone)`` for one reminder.

    Naive timestamps are interpreted in ``timezone_name``. Offset-aware timestamps
    retain their instant while the named timezone is kept for user-facing rendering.
    Relative phrases are deliberately not parsed here; the tool converts an explicit
    minute delay using the real wall clock before calling this storage boundary.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("due_at is required when delay_minutes is not used")
    zone_name = str(timezone_name or "").strip()
    if not zone_name:
        raise ValueError("timezone cannot be empty")
    try:
        zone = ZoneInfo(zone_name)
    except Exception as exc:
        raise ValueError("timezone must be a valid IANA name, for example Asia/Ho_Chi_Minh") from exc
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("due_at must be ISO-8601, for example 2026-09-05T18:30") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds"), zone.key


class PlannerStore:
    """Small repository around ``ciel_data/planner.db``.

    The class contains no LLM, notifier, or tool-manager dependency, so storage and
    date behavior remain independently testable.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS monthly_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    month TEXT NOT NULL,
                    title TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'completed', 'cancelled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_monthly_goal_identity
                    ON monthly_goals(month, lower(title));

                CREATE TABLE IF NOT EXISTS weekly_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    week_start TEXT NOT NULL,
                    title TEXT NOT NULL,
                    weekday INTEGER NULL CHECK (weekday BETWEEN 0 AND 6),
                    local_time TEXT NULL,
                    monthly_goal_id INTEGER NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'completed', 'cancelled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (monthly_goal_id) REFERENCES monthly_goals(id)
                        ON DELETE SET NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_weekly_task_identity
                    ON weekly_tasks(
                        week_start,
                        lower(title),
                        ifnull(weekday, -1),
                        ifnull(local_time, '')
                    );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    due_at_utc TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'delivered', 'cancelled')),
                    notified_at TEXT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_reminder_identity
                    ON reminders(lower(title), due_at_utc);

                PRAGMA user_version = 2;
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _title(value: str) -> str:
        title = str(value or "").strip()
        if not title:
            raise ValueError("title cannot be empty")
        if len(title) > 300:
            raise ValueError("title must be 300 characters or fewer")
        return title

    # ---------------------------------------------------------------- monthly goals
    def add_monthly_goal(self, month: str, title: str, notes: str = "") -> tuple[dict, bool]:
        month = normalize_month(month)
        title = self._title(title)
        notes = str(notes or "").strip()
        stamp = _utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO monthly_goals
                   (month, title, notes, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'active', ?, ?)""",
                (month, title, notes, stamp, stamp),
            )
            created = cur.rowcount == 1
            row = conn.execute(
                "SELECT * FROM monthly_goals WHERE month = ? AND lower(title) = lower(?)",
                (month, title),
            ).fetchone()
        return self._row(row), created

    def list_monthly_goals(self, month: str, include_closed: bool = False) -> list[dict]:
        month = normalize_month(month)
        sql = "SELECT * FROM monthly_goals WHERE month = ?"
        args: list[Any] = [month]
        if not include_closed:
            sql += " AND status = 'active'"
        sql += " ORDER BY status != 'active', id"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def get_monthly_goal(self, goal_id: int) -> dict | None:
        with self._connect() as conn:
            return self._row(conn.execute(
                "SELECT * FROM monthly_goals WHERE id = ?", (int(goal_id),)
            ).fetchone())

    def update_monthly_goal(
        self,
        goal_id: int,
        *,
        month: str | None = None,
        title: str | None = None,
        notes: str | None = None,
        status: str | None = None,
    ) -> dict:
        fields: list[str] = []
        values: list[Any] = []
        if month is not None:
            fields.append("month = ?")
            values.append(normalize_month(month))
        if title is not None:
            fields.append("title = ?")
            values.append(self._title(title))
        if notes is not None:
            fields.append("notes = ?")
            values.append(str(notes).strip())
        if status is not None:
            fields.append("status = ?")
            values.append(normalize_status(status))
        if not fields:
            current = self.get_monthly_goal(goal_id)
            if current is None:
                raise ValueError(f"monthly goal #{goal_id} was not found")
            return current
        fields.append("updated_at = ?")
        values.append(_utc_now())
        values.append(int(goal_id))
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    f"UPDATE monthly_goals SET {', '.join(fields)} WHERE id = ?", values
                )
                if cur.rowcount != 1:
                    raise ValueError(f"monthly goal #{goal_id} was not found")
                row = conn.execute(
                    "SELECT * FROM monthly_goals WHERE id = ?", (int(goal_id),)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError("an equivalent monthly goal already exists") from exc
        return self._row(row)

    # ---------------------------------------------------------------- weekly tasks
    def add_weekly_task(
        self,
        week_start: str,
        title: str,
        *,
        weekday: int | None = None,
        local_time: str | None = None,
        monthly_goal_id: int | None = None,
        notes: str = "",
    ) -> tuple[dict, bool]:
        week_start = normalize_week_start(week_start)
        title = self._title(title)
        if weekday is not None and int(weekday) not in range(7):
            raise ValueError("weekday must be between 0 (Monday) and 6 (Sunday)")
        weekday = None if weekday is None else int(weekday)
        local_time = normalize_time(local_time)
        if local_time is not None and weekday is None:
            raise ValueError("weekday is required when time is set")
        monthly_goal_id = int(monthly_goal_id) if monthly_goal_id else None
        notes = str(notes or "").strip()
        stamp = _utc_now()
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO weekly_tasks
                       (week_start, title, weekday, local_time, monthly_goal_id, notes,
                        status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                    (week_start, title, weekday, local_time, monthly_goal_id, notes,
                     stamp, stamp),
                )
                created = cur.rowcount == 1
                row = conn.execute(
                    """SELECT w.*, m.title AS monthly_goal_title
                       FROM weekly_tasks w
                       LEFT JOIN monthly_goals m ON m.id = w.monthly_goal_id
                       WHERE w.week_start = ? AND lower(w.title) = lower(?)
                         AND ifnull(w.weekday, -1) = ifnull(?, -1)
                         AND ifnull(w.local_time, '') = ifnull(?, '')""",
                    (week_start, title, weekday, local_time),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError("monthly_goal_id does not identify an existing monthly goal") from exc
        return self._row(row), created

    def list_weekly_tasks(self, week_start: str, include_closed: bool = False) -> list[dict]:
        week_start = normalize_week_start(week_start)
        sql = """SELECT w.*, m.title AS monthly_goal_title
                 FROM weekly_tasks w
                 LEFT JOIN monthly_goals m ON m.id = w.monthly_goal_id
                 WHERE w.week_start = ?"""
        args: list[Any] = [week_start]
        if not include_closed:
            sql += " AND w.status = 'active'"
        sql += " ORDER BY w.status != 'active', ifnull(w.weekday, 7), ifnull(w.local_time, ''), w.id"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def get_weekly_task(self, task_id: int) -> dict | None:
        with self._connect() as conn:
            return self._row(conn.execute(
                """SELECT w.*, m.title AS monthly_goal_title
                   FROM weekly_tasks w LEFT JOIN monthly_goals m ON m.id = w.monthly_goal_id
                   WHERE w.id = ?""",
                (int(task_id),),
            ).fetchone())

    def update_weekly_task(
        self,
        task_id: int,
        *,
        week_start: str | None = None,
        title: str | None = None,
        weekday: int | None = None,
        local_time: str | None = None,
        monthly_goal_id: int | None = None,
        notes: str | None = None,
        status: str | None = None,
        clear_schedule: bool = False,
        clear_monthly_goal: bool = False,
    ) -> dict:
        current = self.get_weekly_task(task_id)
        if current is None:
            raise ValueError(f"weekly task #{task_id} was not found")

        fields: list[str] = []
        values: list[Any] = []
        if week_start is not None:
            fields.append("week_start = ?")
            values.append(normalize_week_start(week_start))
        if title is not None:
            fields.append("title = ?")
            values.append(self._title(title))
        if clear_schedule:
            fields.extend(["weekday = NULL", "local_time = NULL"])
        else:
            resulting_weekday = current.get("weekday") if weekday is None else int(weekday)
            if weekday is not None:
                if resulting_weekday not in range(7):
                    raise ValueError("weekday must be between 0 (Monday) and 6 (Sunday)")
                fields.append("weekday = ?")
                values.append(resulting_weekday)
            if local_time is not None:
                normalized_time = normalize_time(local_time)
                if normalized_time is not None and resulting_weekday is None:
                    raise ValueError("weekday is required when time is set")
                fields.append("local_time = ?")
                values.append(normalized_time)
        if clear_monthly_goal:
            fields.append("monthly_goal_id = NULL")
        elif monthly_goal_id is not None:
            fields.append("monthly_goal_id = ?")
            values.append(int(monthly_goal_id) if int(monthly_goal_id) > 0 else None)
        if notes is not None:
            fields.append("notes = ?")
            values.append(str(notes).strip())
        if status is not None:
            fields.append("status = ?")
            values.append(normalize_status(status))
        if not fields:
            return current
        fields.append("updated_at = ?")
        values.append(_utc_now())
        values.append(int(task_id))
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    f"UPDATE weekly_tasks SET {', '.join(fields)} WHERE id = ?", values
                )
                if cur.rowcount != 1:
                    raise ValueError(f"weekly task #{task_id} was not found")
        except sqlite3.IntegrityError as exc:
            raise ValueError("the update conflicts with another task or monthly goal") from exc
        updated = self.get_weekly_task(task_id)
        if updated is None:  # defensive; the row was just updated in one transaction
            raise ValueError(f"weekly task #{task_id} was not found")
        return updated

    # ---------------------------------------------------------------- reminders
    def add_reminder(
        self,
        title: str,
        due_at: str,
        *,
        timezone_name: str,
    ) -> tuple[dict, bool]:
        """Create one scheduled notification; identical retries are idempotent."""
        title = self._title(title)
        due_at_utc, zone_name = normalize_reminder_due(due_at, timezone_name)
        stamp = _utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO reminders
                   (title, due_at_utc, timezone, status, notified_at, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', NULL, ?, ?)""",
                (title, due_at_utc, zone_name, stamp, stamp),
            )
            created = cur.rowcount == 1
            row = conn.execute(
                """SELECT * FROM reminders
                   WHERE lower(title) = lower(?) AND due_at_utc = ?""",
                (title, due_at_utc),
            ).fetchone()
        return self._row(row), created

    def get_reminder(self, reminder_id: int) -> dict | None:
        with self._connect() as conn:
            return self._row(conn.execute(
                "SELECT * FROM reminders WHERE id = ?", (int(reminder_id),)
            ).fetchone())

    def list_reminders(self, include_closed: bool = False, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM reminders"
        if not include_closed:
            sql += " WHERE status = 'pending'"
        sql += " ORDER BY status != 'pending', due_at_utc, id LIMIT ?"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, (max(1, int(limit)),)).fetchall()]

    def list_due_reminders(self, now_utc: str, limit: int = 10) -> list[dict]:
        due_at_utc, _ = normalize_reminder_due(now_utc, "UTC")
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT * FROM reminders
                   WHERE status = 'pending' AND due_at_utc <= ?
                   ORDER BY due_at_utc, id LIMIT ?""",
                (due_at_utc, max(1, int(limit))),
            ).fetchall()]

    def cancel_reminder(self, reminder_id: int) -> dict:
        reminder_id = int(reminder_id)
        stamp = _utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE reminders SET status = 'cancelled', updated_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (stamp, reminder_id),
            )
            if cur.rowcount != 1:
                current = conn.execute(
                    "SELECT status FROM reminders WHERE id = ?", (reminder_id,)
                ).fetchone()
                if current is None:
                    raise ValueError(f"reminder #{reminder_id} was not found")
                raise ValueError(f"reminder #{reminder_id} is already {current['status']}")
            row = conn.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
        return self._row(row)

    def mark_reminder_delivered(self, reminder_id: int, notified_at: str | None = None) -> dict:
        """Close a reminder only after Notifier proves delivery or persisted dedupe."""
        reminder_id = int(reminder_id)
        delivered_at, _ = normalize_reminder_due(notified_at or _utc_now(), "UTC")
        stamp = _utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE reminders
                   SET status = 'delivered', notified_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (delivered_at, stamp, reminder_id),
            )
            row = conn.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"reminder #{reminder_id} was not found")
            if cur.rowcount != 1 and row["status"] != "delivered":
                raise ValueError(f"reminder #{reminder_id} is {row['status']}")
        return self._row(row)
