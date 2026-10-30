"""llm_connector.py — Bridge between Ciel's core and the Brain-Worker agent system.

Public methods delegate turn, planning, execution, recovery and finalization to
core.harness services. The optional run manager selects fast_chat, single_step,
or harness_run and persists bounded execution evidence. Model/context/domain
services and compatibility entry points remain here; see runtime_harness.md.
"""
import os
import re
import json
import time
import threading
import traceback
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.messages import SystemMessage, HumanMessage

from .tool_manager import ToolManager
from .router import Router
from .recovery_manager import RecoveryManager
from .continuation import (
    ContinuationPolicy, LoopBudget, StepRecord, build_observation_block,
)
from .parallel import plan_batches, collect_parallel_safe
from .task_state import TaskStore
from .user_model import UserModel, assess_preference, learn_from_turn, estimate_tokens
from .context import (ContextAssembler, P_REQUEST, P_CRITICAL, P_IMPORTANT, P_HELPFUL)
from .permissions import PermissionPolicy, Decision, DeferredStore
from .outbound import OUTBOUND_KEYS, delivery_key, plaintext_to_html, recipients_lowered
from .plan_validation import PlanValidation, validate_plan
from .active_subject import ActiveSubject
from .session_compact import SessionCompact
from . import rag_manager

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_system.models.brain import Brain
from agent_system.models.worker import Worker
from agent_system.models.middleware import Middleware
from agent_system.utils.logger import log
from agent_system.utils.usage import format_usage
from agent_system.config import (
    MIDDLEWARE_ENABLED, MIDDLEWARE_SCOPE, MIDDLEWARE_MAX_PASSES,
    AGENT_LOOP_ENABLED, AGENT_LOOP_MAX_ROUNDS, AGENT_LOOP_MAX_SECONDS,
    AGENT_PARALLEL_ENABLED, AGENT_PARALLEL_MAX_WORKERS,
    USER_MODEL_ENABLED, USER_MODEL_TOKEN_BUDGET,
    USER_MODEL_LEARN_ENABLED, USER_MODEL_LEARN_DAILY_LIMIT,
    CONTEXT_INPUT_BUDGET, CONTEXT_RECALL_BUDGET, ROUTER_PERSONA_MODE,
    CONTEXT_RECENT_TURNS_ENABLED, CONTEXT_RECENT_TURNS_BUDGET,
    ACTIVE_SUBJECT_ENABLED, ACTIVE_SUBJECT_TTL_SECONDS,
    ACTIVE_SUBJECT_MAX_IDLE_TURNS, ACTIVE_SUBJECT_MAX_ENTITIES,
    SESSION_COMPACT_ENABLED, SESSION_COMPACT_REFRESH_TURNS, SESSION_COMPACT_MAX_CHARS,
)
from core.cost import estimate_cost

load_dotenv()


# Re-export legacy signal names for integrations while services use their owner.
from .harness.signals import (
    SELF_CORRECTION_PROMPT,
    RAG_LLM_COMPRESS_CHAR_THRESHOLD,
    RAG_LLM_COMPRESS_INPUT_LIMIT,
    RAG_LLM_COMPRESS_OUTPUT_LIMIT,
    _DANGEROUS_CODE_PATTERNS,
    _EMAIL_RE,
    _SENDER_NOUN_RE,
    _SEND_VERB_RE,
    _SEND_NEGATION_RE,
    _has_send_intent,
    _WRITE_TARGET_PATH_RE,
    _WRITE_VERB_RE,
    _strip_telegram_inbound_metadata,
    _has_telegram_send_intent,
    _has_telegram_delivery_step,
    _has_write_intent,
    _NO_OVERWRITE_RE,
    _KNOWLEDGE_GAP_NEGATION_RE,
    _KNOWLEDGE_NOUN_RE,
    _OWN_DATA_RE,
    _SENTENCE_SPLIT_RE,
    _admits_missing_knowledge,
    _search_results_need_retry,
    _search_results_unusable,
    _normalized_search_query,
    _UNBACKED_CHAT_TOOL_PROMISE_RE,
    _PUBLIC_URL_RE,
    _find_dangerous_code_patterns,
    _UNSYNTHESIZED_PLACEHOLDER_RE,
    _has_unsynthesized_placeholder,
    _RAW_STEP_RESULT_CHARS,
    _HEALING_SKIP_PATTERNS,
    _is_unfixable_tool_error,
    _RISK_DESCRIPTIONS,
)


class CielCore:
    @staticmethod
    def _is_timeout_exception(error: BaseException) -> bool:
        """Recognize provider timeouts through wrapper exception chains.

        OpenAI-compatible gateways may wrap ``TimeoutError`` several times
        (httpcore -> httpx -> OpenAI -> LangChain). Depending on dependency versions,
        the outer class is not one stable import, so inspect the complete cause/context
        chain instead of coupling the runtime to one SDK exception class.
        """
        seen = set()
        current = error
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            name = type(current).__name__.casefold()
            message = str(current).casefold()
            if (
                isinstance(current, TimeoutError)
                or "timeout" in name
                or "timed out" in message
                or "read operation timed out" in message
            ):
                return True
            current = current.__cause__ or current.__context__
        return False

    @classmethod
    def _pipeline_error_response(cls, error: BaseException, user_input: str) -> str:
        """Return a bounded, user-facing failure without leaking provider internals."""
        is_vi = cls._detect_language(user_input) == "Vietnamese"
        if cls._is_timeout_exception(error):
            if is_vi:
                return (
                    "Nhà cung cấp AI phản hồi quá chậm nên lượt này đã hết thời gian chờ. "
                    "Yêu cầu chưa hoàn tất; đừng xem trạng thái “Processing…” là đã làm xong. "
                    "Hãy gửi lại sau một lát."
                )
            return (
                "The AI provider timed out, so this turn did not complete. Do not treat "
                "the earlier Processing status as completion; please retry shortly."
            )
        if is_vi:
            return "Lượt xử lý gặp lỗi và chưa hoàn tất. Vui lòng thử lại; lỗi đã được ghi để kiểm tra."
        return "This turn failed before completion. Please retry; the error was recorded for review."

    def __init__(self):
        # Created FIRST: _log_thought serialises on it, and anything constructed below
        # may log during start-up.
        self._log_lock = threading.RLock()
        self.base_dir = Path(__file__).resolve().parent.parent
        self.tool_manager = ToolManager()
        self._tools = self.tool_manager.get_tools()
        # Tools that may share a concurrent batch: the built-in read-only set plus
        # whatever each skill declared via `parallel_safe` in its factory. Unknown
        # tools stay sequential — see core/parallel.py for why this is opt-in.
        self._parallel_safe = collect_parallel_safe(
            getattr(self.tool_manager, "skill_data", []))

        # Load Official Personality
        self.persona_file = self.base_dir / "persona" / "official_ciel_personality.txt"
        self.ciel_persona = "You are Ciel, an AI assistant."
        if self.persona_file.exists():
            self.ciel_persona = self.persona_file.read_text(encoding="utf-8")

        # Build tool name -> schema map
        self._tool_map = {t.name: t for t in self._tools}
        self._tool_list_str = self._build_tool_list()

        # Initialize Brain and Worker from agent_system
        self.brain = Brain()
        self.worker = Worker()
        # COST/USAGE TRACKING: log a [WORKER] [LLM_CALL] entry into thoughts.log every
        # time anything invokes the Worker — covers direct formatting calls here AND
        # recovery_manager.py's healing/syntax-check calls for free (same instance).
        # Call-count only (not token-precise): the harness/dashboards care about WHICH
        # mechanism causes extra calls (e.g. today's healing-waste finding), not exact
        # token math, and this needs no changes to Worker's return type/callers.
        self.worker.on_call = lambda model, usage=None: self._log_thought("WORKER", "LLM_CALL", format_usage(model, usage))

        # Modular Components
        self.router = Router(self.brain, self._log_thought, persona=self.ciel_persona)
        self.recovery = RecoveryManager(self.worker, self._log_thought)

        # Third tier: Middleware (semantic verifier/finalizer for outbound content).
        # Lazily constructed only if enabled, so a disabled Middleware costs nothing
        # (no extra LLM client, no API key requirement).
        self.middleware = None
        if MIDDLEWARE_ENABLED:
            try:
                self.middleware = Middleware()
                self.middleware.on_call = lambda model, usage=None: self._log_thought("MIDDLEWARE", "LLM_CALL", format_usage(model, usage))
            except Exception as e:
                log.error(f"Middleware failed to initialize, continuing without it: {e}")

        active_topology = "Brain-Middleware-Worker" if self.middleware else "Brain-Worker"
        log.system(f"CielCore initialized with Modular {active_topology} architecture")

        # SAFETY GATE: confirmation callback for high-risk tools
        # Set by main.py (CLI) or main_api.py (WebSocket) at startup.
        # Signature: confirm_callback(tool_name: str, preview: str, tool_args: dict) -> bool
        self.confirm_callback = None
        # Generic pending-confirmation slot: any preview-only tool can opt in by
        # returning {"confirm": {"tool": ..., "args": {...}}} (see skills/_result.py
        # make_result) instead of being hand-registered in core. Persisted to disk so
        # it survives a process restart, not just a REPL turn — required for Ciel to
        # eventually run "one process per command" like a real CLI instead of only the
        # long-lived main.py loop. In-memory copy is authoritative during this process;
        # disk is the recovery path if the process dies or restarts mid-confirmation.
        self._pending_state_path = self.base_dir / "ciel_data" / "state" / "pending_action.json"
        self._pending_action = self._load_pending_action_from_disk()
        # TIER 2 — durable record of what Ciel is doing, so an interrupted job leaves a
        # trace the Master can act on instead of vanishing. Deliberately NOT fed to the
        # Brain (see core/task_state.py).
        self.tasks = TaskStore(self.base_dir / "ciel_data" / "state" / "tasks.json")
        # TIER 3 — AUTO / ASK / DENY. Built once here so session grants survive the whole
        # run; `_HIGH_RISK_TOOLS` stays the source of truth for what counts as risky.
        self.permissions = PermissionPolicy(
            risky_names=self._HIGH_RISK_TOOLS,
            gate_disabled=os.getenv("DISABLE_SAFETY_GATE", "false").lower() in ("true", "1", "yes"),
        )
        # TIER 6 — set True only while a background/scheduled run is executing. It flips
        # every risky decision from ASK to DEFER, because "nobody answered" must never
        # resolve to "yes". Default False: an ordinary CLI/API request IS attended.
        #
        # Thread-LOCAL on purpose. A plain attribute would be a race: the scheduler
        # thread would flip it to True while a foreground request was mid-flight on the
        # main thread, and that request's confirmations would silently turn into
        # deferrals. Per-thread, the background run marks only itself. `_run_steps`
        # propagates it into parallel workers explicitly (see `_run`), because a
        # security control that fails open in a worker thread is worse than none.
        self._ctx = threading.local()
        self.deferred = DeferredStore(self.base_dir / "ciel_data" / "state" / "deferred.json")
        # TIER 5 — cooperative cancellation. A long plan used to be escapable only by
        # killing the process (one run sat at 566s), which loses the Tier-2 record along
        # with it. Set by Ctrl+C at the CLI or a `cancel` from the UI; checked at STEP
        # boundaries, never mid-tool: aborting inside a half-written file or a half-sent
        # email is not a cancellation, it is a corruption.
        self._cancel_event = threading.Event()
        # Recipients already delivered to during the CURRENT turn. Reset in process(),
        # so "send X to A" twice in two separate requests still sends twice — it is only
        # a duplicate when one request produces two deliveries. Guarded by _log_lock,
        # which parallel step workers already share.
        self._sent_this_turn = set()
        self._telegram_delivery_sent_this_turn = False
        # Bounded session-only grounding for a deictic follow-up to a successful web
        # lookup. It is neither durable task state, RAG, nor general chat history.
        self._active_lookup = None
        # The only response-side state handed back to Brain routing: one bounded,
        # structured, RAM-only subject. It excludes recipients, paths, secrets and
        # confirmation/destructive state by construction.
        self.active_subject = ActiveSubject(
            enabled=ACTIVE_SUBJECT_ENABLED,
            ttl_seconds=ACTIVE_SUBJECT_TTL_SECONDS,
            max_idle_turns=ACTIVE_SUBJECT_MAX_IDLE_TURNS,
            max_entities=ACTIVE_SUBJECT_MAX_ENTITIES,
        )
        # TIER 7 — the Master's profile, on the PUSH side: unlike facts.json (pull-only,
        # holds credentials, never injected) this small block is added to the prompts
        # where preferences actually change the output. It renders to "" while empty, so
        # the feature costs literally nothing until it has something to say.
        self.user_model = UserModel(self.base_dir / "ciel_data" / "user_model.json")

        # Live per-tier LLM call counter — incremented at the single logging chokepoint
        # (_log_thought) whenever an [LLM_CALL] entry is written, so it covers Brain
        # (via router), Worker, Middleware, and healing calls with no extra wiring.
        # Cheap and accurate for the current session; the UI reads this instead of the
        # old fake "log-size * 0.0001" cost estimate.
        self.llm_call_counts = {"BRAIN": 0, "WORKER": 0, "MIDDLEWARE": 0}
        # Per-tier token totals and estimated USD cost for this session — accumulated at
        # the SAME chokepoint (_log_thought) by parsing the LLM_CALL content line, so any
        # future actor that logs an [LLM_CALL] is counted with zero extra wiring. Token
        # numbers are exact (from the provider); cost is an estimate (see core/cost.py).
        self.llm_token_counts = {t: {"input": 0, "output": 0, "total": 0} for t in ("BRAIN", "WORKER", "MIDDLEWARE")}
        self.llm_cost_usd = {"BRAIN": 0.0, "WORKER": 0.0, "MIDDLEWARE": 0.0}

        self.chat_history = ChatMessageHistory()
        self.max_history = 20
        self.chat_memory_file = self.base_dir / "ciel_data" / "memory_bank.json"
        self.session_compact = SessionCompact(
            self.base_dir / "ciel_data" / "session_compact.json",
            enabled=SESSION_COMPACT_ENABLED,
            refresh_turns=SESSION_COMPACT_REFRESH_TURNS,
            max_source_messages=self.max_history,
            max_chars=SESSION_COMPACT_MAX_CHARS,
        )

        self._load_chat_memory()
        self._check_bootup_cleanse()
        from .harness.runtime import build_manager
        self.run_manager = build_manager(self)

    # Tools that require Master's explicit Y/N approval before execution
    _HIGH_RISK_TOOLS = set(_RISK_DESCRIPTIONS.keys())

    # TWO-STEP TOOLS: a preview-only tool (e.g. `git_commit_and_push`) opts into
    # deterministic pending-confirmation by returning {"confirm": {"tool": ...,
    # "args": {...}}} from skills/_result.py make_result — captured in execute_tool
    # via _set_pending_action(). No per-tool registration lives in core; any skill
    # can declare this.
    #
    # Why this exists at all: `git_commit_and_push` literally ends its output with
    # 'Say "yes" or "confirm" to commit and push' — a promise the pipeline could not
    # keep on its own. The Router deliberately never sees chat_history, and RAG skips
    # inputs under MIN_QUERY_LENGTH (15), so a bare "yes" (3 chars) or "confirm commit"
    # (14) reached the Brain with zero context. Observed live: the Brain answered "what
    # is your command?" and then asked for the repo path it had itself just printed.
    _PENDING_TTL_SECONDS = 600     # a stale "yes" must never fire an old action

    _AFFIRM_RE = re.compile(
        r"^\s*(?:yes|y|ok|okay|sure|yep|yeah|confirm(?:ed)?|proceed|go\s*ahead|do\s*it|"
        r"đồng\s*ý|xác\s*nhận|chốt|ok\s*nhé|làm\s*đi|tiếp\s*tục|được|ừ|uh|oke)"
        r"(?:\s+(?:it|that|now|please|đi|nhé|luôn|commit|push|the\s+commit|and\s+push))*\s*[.!]*\s*$",
        re.IGNORECASE)
    _CANCEL_RE = re.compile(
        r"^\s*(?:no|nope|cancel|abort|stop|don'?t|nevermind|never\s*mind|"
        r"không|khong|hủy|huỷ|thôi|dừng|bỏ\s*qua)"
        r"(?:\s+\w+)*\s*[.!]*\s*$",
        re.IGNORECASE)

    # Email-sending tools and the arg holding the body that must be sanitized
    # before it leaves the system (strips internal reasoning/meta/paths).
    _EMAIL_BODY_ARGS = {
        "send_gmail_message":      "message",
        "send_gmail_html_message": "html_body",
        "reply_to_email":          "reply_text",
        "create_gmail_draft":      "message",
    }

    # Keyword triggers for "the user wants this sent via email" — shared by the
    # proactive router-bypass check, the content-filter fallback, and the
    # multi_tool missing-send-step safeguard.
    _EMAIL_INTENT_KEYWORDS = (
        "gửi email", "send email", "gửi thư", "gửi mail", "email đến", "send to",
        "gửi cho", "qua email", "qua mail", "qua gmail", "gửi báo cáo", "gửi report",
        "send report", "email report", "báo cáo qua", "report qua", "mail cho", "email cho",
    )

    # Generic send verbs used with a concrete email address as a broader, low-false-
    # positive email-intent signal (see _is_email_send_intent).
    _SEND_VERBS = ("gửi", "gởi", "send", "chuyển", "mail")

    @classmethod
    def _is_email_send_intent(cls, user_input: str, lowered: str) -> bool:
        """True if the request is about sending an email — via a fixed keyword phrase
        OR a concrete email address plus any generic send verb.

        The fixed-phrase list alone misses natural phrasings like "gửi qua
        x@gmail.com" (the address sits where "qua gmail" would, so no fixed phrase
        matches). This was an observed gap: such requests skipped the deterministic
        data-first fallback (_fallback_direct_action) and fell through to the Brain
        composing the email body itself with no tool call to ground it — the same
        failure class as the fabricated Dow/Nasdaq/S&P email earlier in this project.
        Address+verb is additive, not a replacement — the fixed list still catches
        phrasings with no literal @-address (e.g. "gửi báo cáo cho anh Nam qua Gmail").
        """
        if any(kw in lowered for kw in cls._EMAIL_INTENT_KEYWORDS):
            return True
        has_address = bool(_EMAIL_RE.search(user_input))
        has_send_verb = any(v in lowered for v in cls._SEND_VERBS)
        return has_address and has_send_verb

    # Hardcoded hints for tools whose auto-generated descriptions are incomplete
    _TOOL_HINTS = {
        "search_gmail": "Search emails. Args: query (required), resource='messages' (required, always use 'messages'), max_results (optional, default 5).",
        # Prefer Google (stealth_search) for forecast / "tra google" / live web — found
        # live 2026-08: Master asked weather tomorrow + "tra google đi" and chat path
        # claimed Google was unavailable while this tool was loaded.
        "stealth_search": (
            "PRIMARY live web / Google search (SerpApi→RSS/DDG). USE for news, "
            "forecast ('ngày mai'/'dự báo'), 'tra google', look-ups, anything not "
            "covered by a specialized tool. Args: query (required), max_results, "
            "timelimit 'd'|'w'|'m'|'y'. Prefer this over inventing an answer."
        ),
        "get_weather": (
            "CURRENT weather only for one city (wttr.in nowcast). NOT multi-day "
            "forecast — for 'ngày mai'/'dự báo' use stealth_search instead. "
            "Args: city (required), e.g. 'Ho Chi Minh City', 'Hanoi'."
        ),
        "smart_scrape": (
            "Read full page text after stealth_search when snippets are thin. "
            "Pass a concrete article URL from search results."
        ),
        "add_monthly_goal": (
            "Add a BIG outcome/milestone to the monthly plan. Args: title; optional "
            "month='YYYY-MM', notes. Do not use for a small immediate todo."
        ),
        "list_monthly_goals": (
            "List monthly goals. Args: optional month='YYYY-MM', include_closed=false."
        ),
        "update_monthly_goal": (
            "Update a monthly goal by goal_id. Optional title, month, notes, status."
        ),
        "complete_monthly_goal": "Complete a monthly goal by goal_id.",
        "add_weekly_task": (
            "Add a CONCRETE action for one week. Args: title; optional week_start "
            "YYYY-MM-DD, weekday Monday..Sunday/0..6, time HH:MM, monthly_goal_id, notes."
        ),
        "list_weekly_plan": (
            "List weekly actions. Args: optional week_start YYYY-MM-DD, include_closed=false."
        ),
        "update_weekly_task": (
            "Update weekly task by task_id; optional title/week_start/weekday/time/"
            "monthly_goal_id/notes/status and clear_schedule/clear_monthly_goal."
        ),
        "complete_weekly_task": "Complete a weekly task by task_id.",
        "add_reminder": (
            "Schedule a ONE-TIME alert. For 'in N minutes', use delay_minutes=N and "
            "leave due_at empty. For an absolute time, use due_at ISO-8601 and optional "
            "timezone_name. Put the reminder text in title; message is accepted only as "
            "a compatibility alias. Never use add_todo for a timed alert."
        ),
        "list_reminders": "List pending reminders; optional include_closed=false.",
        "cancel_reminder": "Cancel a pending reminder by reminder_id.",
    }

    # Only call the Worker to format these tools. Others are already readable.
    _TOOLS_NEEDING_FORMAT = {
        "search_gmail",
        "get_market_price",
        "analyze_crypto_technical",
        "get_gmail_message",
        "get_gmail_thread",
        "stealth_search",
        "smart_scrape"
    }

    # Tools with obviously-correct results — skip Brain self-correction to save API cost
    _SKIP_SELF_CORRECTION = {
        "list_workspace", "read_file", "write_file", "append_file",
        # NOT get_fact — removed deliberately. get_fact("name") missing a fact saved as
        # "Tên" used to dead-end on "no fact found" even though the tool's own result
        # lists the real keys ("Available keys in the vault: Tên, ..."); nothing ever
        # retried with it. get_fact is read-only (no side effects a retry could
        # duplicate/corrupt) and used rarely, so the extra self-correction call's cost
        # is worth automatically recovering from a wrong-language/wrong-case key guess.
        "save_fact", "delete_fact",
        "take_screenshot", "get_file_info", "open_application",
        "get_crypto_stats", "vision_describe",
        # Planner writes are idempotent, but an evaluation retry still wastes a Brain
        # call and can turn a successful local update into a confusing second action.
        "add_monthly_goal", "list_monthly_goals", "update_monthly_goal",
        "complete_monthly_goal", "add_weekly_task", "list_weekly_plan",
        "update_weekly_task", "complete_weekly_task",
        "add_reminder", "list_reminders", "cancel_reminder",
    }

    def _log_thought(self, actor: str, action: str, content: str):
        """Append a record of the Brain/Worker thought process to the thoughts.log file.

        Thread-safe: parallel tool batches (see core/parallel.py) call this from several
        threads at once, and BOTH halves of it are shared mutable state — the counter
        dicts would drop increments under a read-modify-write race, and concurrent
        appends would interleave mid-entry, corrupting the `[ts] [ACTOR] [ACTION]`
        format that scripts/format_thoughts_log.py and cost_report.py parse.
        """
        with self._log_lock:
            self._log_thought_locked(actor, action, content)

    def _log_thought_locked(self, actor: str, action: str, content: str):
        # Single chokepoint for the live LLM-call counter (see __init__): every tier's
        # [LLM_CALL] entry passes through here, so counting here covers all of them.
        if action.upper() == "LLM_CALL":
            self.llm_call_counts[actor] = self.llm_call_counts.get(actor, 0) + 1
            # Parse "model=<id> in=<n> out=<n> total=<n>" (see agent_system.utils.usage
            # .format_usage) to accumulate exact tokens + estimated cost per tier.
            model_m = re.search(r"model=(\S+)", content)
            in_m = re.search(r"\bin=(\d+)", content)
            out_m = re.search(r"\bout=(\d+)", content)
            tot_m = re.search(r"\btotal=(\d+)", content)
            if actor in self.llm_token_counts:
                inp = int(in_m.group(1)) if in_m else 0
                out = int(out_m.group(1)) if out_m else 0
                tot = int(tot_m.group(1)) if tot_m else (inp + out)
                bucket = self.llm_token_counts[actor]
                bucket["input"] += inp
                bucket["output"] += out
                bucket["total"] += tot
                if model_m and (inp or out):
                    self.llm_cost_usd[actor] = round(
                        self.llm_cost_usd.get(actor, 0.0) + estimate_cost(model_m.group(1), inp, out), 6
                    )
        log_file = self.base_dir / "ciel_data" / "logs" / "thoughts.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] [{actor}] [{action.upper()}]\n{content}\n{'-'*60}\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)

    def _build_tool_list(self) -> str:
        """Build a compact tool list string for the Brain's routing prompt."""
        lines = []
        for tool in self._tools:
            name = tool.name
            if name in self._TOOL_HINTS:
                lines.append(f"- {name}: {self._TOOL_HINTS[name]}")
                continue

            desc = tool.description[:80] if tool.description else "No description"
            args_info = ""
            if hasattr(tool, 'args_schema') and tool.args_schema:
                try:
                    schema = tool.args_schema.schema()
                    props = schema.get("properties", {})
                    required = schema.get("required", [])
                    arg_parts = []
                    for arg_name, arg_schema in props.items():
                        arg_type = arg_schema.get("type", "string")
                        req = " (required)" if arg_name in required else ""
                        arg_parts.append(f"{arg_name}: {arg_type}{req}")
                    args_info = ", ".join(arg_parts)
                except Exception:
                    args_info = "(see tool description)"
            lines.append(f"- {name}({args_info}): {desc}")
        return "\n".join(lines)

    def _trim_history(self):
        """Trim chat history to max_history. Archived messages go to long-term RAG memory."""
        if len(self.chat_history.messages) > self.max_history:
            # Archive the messages that are about to be trimmed
            overflow = self.chat_history.messages[:-self.max_history]
            self._archive_to_rag(overflow)
            self.chat_history.messages = self.chat_history.messages[-self.max_history:]

    def _archive_to_rag(self, messages: list):
        """Send trimmed messages to long-term RAG memory as user+assistant pairs."""
        pairs = []
        current_pair = []
        for msg in messages:
            current_pair.append(f"{msg.type.capitalize()}: {msg.content[:300]}")
            if msg.type == "ai":
                pairs.append(" | ".join(current_pair))
                current_pair = []
        # Save any leftover (unpaired user message)
        if current_pair:
            pairs.append(" | ".join(current_pair))

        for pair_text in pairs:
            saved = rag_manager.embed_and_save(pair_text)
            if saved:
                self._log_thought("RAG", "archived", pair_text[:100])

    def _load_chat_memory(self):
        if self.chat_memory_file.exists():
            try:
                data = json.loads(self.chat_memory_file.read_text(encoding="utf-8"))
                toxic_markers = [
                    "Thư viện CIEL không cung cấp",
                    "I do not have the capability",
                    "As an AI",
                    "I have used the tool",
                    "I've used the tool",
                    "I have searched",
                    "echo I have used",
                    "echo Show me",
                    "Action: search_gmail({'count'",
                    "Action: execute_shell_command({'command': 'echo",
                    "label:new",
                ]
                for msg in data:
                    content = msg.get("content", "")
                    if any(marker in content for marker in toxic_markers):
                        continue
                    if msg.get("type") == "human":
                        self.chat_history.add_user_message(content)
                    else:
                        self.chat_history.add_ai_message(content)
                self._trim_history()
            except Exception as e:
                print(f"[Ciel Warning] Failed to load chat memory: {e}")
                traceback.print_exc()
                self.chat_history = ChatMessageHistory()

    def _save_chat_memory(self):
        self.chat_memory_file.parent.mkdir(parents=True, exist_ok=True)
        self._trim_history()
        data = [{"type": m.type, "content": m.content} for m in self.chat_history.messages]
        self.chat_memory_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _check_bootup_cleanse(self):
        import datetime
        import os
        if self.chat_memory_file.exists() and self.chat_history.messages:
            mtime = os.path.getmtime(self.chat_memory_file)
            last_date = datetime.datetime.fromtimestamp(mtime).date()
            today = datetime.datetime.now().date()
            if last_date < today:
                self._brain_cleanse(reason="boot-up date mismatch")

    def _brain_cleanse(self, reason="nightly"):
        if not self.chat_history.messages:
            return

        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"\n[{ts}] [System] Initiating Brain Cleanse ({reason})...")

        transcript = []
        for msg in self.chat_history.messages:
            transcript.append(f"{msg.type.capitalize()}: {msg.content}")
        transcript_text = "\n".join(transcript)

        prompt = (
            "You are Ciel. Write a very concise Daily Summary of the following conversation.\n"
            "Focus only on key facts, decisions, and outcomes. Make it 2-3 paragraphs max.\n"
            "Transcript:\n" + transcript_text[:50000]
        )
        try:
            summary = self.worker.generate(prompt)
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            summary_entry = f"Daily Summary ({date_str}):\n{summary}"

            # 1. Archive everything
            self._archive_to_rag(self.chat_history.messages)
            rag_manager.embed_and_save(summary_entry)
            self._log_thought("RAG", "archived_daily_summary", summary_entry[:100])

            # 2. Clear memory and save
            self.chat_history.messages = []
            self.session_compact.clear()
            self._save_chat_memory()

            # 3. Notify
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [System] Brain Cleanse complete.")
            try:
                from skills.external.telegram_ops import send_telegram_message
                send_telegram_message(f"🧠 Brain Cleanse complete ({reason}). Memory archived successfully.")
            except Exception:
                pass
        except Exception as e:
            print(f"[Brain Cleanse Error] {e}")

    def _learn_from_turn(self, user_input: str):
        """TIER 7b — notice a durable preference without being asked to remember it.

        Runs on a daemon thread so it adds ZERO latency to the reply: the answer is
        already on its way out before this starts. The deterministic gate
        (`assess_preference`) runs first and is free, so an ordinary turn spends nothing
        and never even spawns the thread. Fail-open: the turn has already been answered,
        and nothing about learning may break it.

        Unattended runs are excluded on purpose. A background trigger's text is Ciel's
        own words, not the Master's, and learning "preferences" from itself is how a
        profile drifts away from the person it describes.
        """
        try:
            if not USER_MODEL_ENABLED or not USER_MODEL_LEARN_ENABLED or self.unattended:
                return
            if assess_preference(user_input) is None:
                return              # the common case, decided in free Python
        except Exception:
            return

        def _work():
            try:
                learn_from_turn(self.user_model, user_input, self.worker.generate,
                                daily_limit=USER_MODEL_LEARN_DAILY_LIMIT,
                                logger=self._log_thought)
            except Exception as e:
                self._log_thought("USER_MODEL", "error", f"{type(e).__name__}: {e}")

        threading.Thread(target=_work, daemon=True).start()

    # ------------------------------------------------------------- TIER 5
    def request_cancel(self, reason: str = "Master cancelled"):
        """Ask the running request to stop at its next step boundary.

        Safe to call from any thread, and safe to call when nothing is running. It does
        NOT abort an in-flight tool: a plan stopped between steps leaves a coherent
        world, one stopped inside `send_gmail_message` does not.
        """
        self._cancel_event.set()
        self._cancel_reason = reason

    def clear_cancel(self):
        self._cancel_event.clear()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _abort_if_cancelled(self, where: str) -> bool:
        """Checked at step boundaries. Records WHY the plan stopped, so a cancelled job
        is distinguishable in the task store from one that crashed."""
        if not self._cancel_event.is_set():
            return False
        self._log_thought("SYSTEM", "cancelled", f"stopped at {where}: "
                                                 f"{getattr(self, '_cancel_reason', '')}")
        return True

    @property
    def unattended(self) -> bool:
        """True while THIS thread is running unsupervised work. See __init__."""
        return getattr(self._ctx, "unattended", False)

    @unattended.setter
    def unattended(self, value: bool):
        self._ctx.unattended = bool(value)

    # Bug found by reading a real transcript: "giá vàng XAU/USD giờ bao nhiêu" -> answer
    # -> "tại sao lại thế" produced a reply with NO memory of the price just given. Root
    # cause: chat_history is stored, persisted, and archived into RAG — and never once
    # read back into a prompt. RAG is not a substitute: it only sees ALREADY-archived
    # turns (never the one just completed, which is exactly the one a follow-up refers
    # to), and it is gated by length/relevance thresholds that a short follow-up like
    # "tại sao lại thế" or "phân tích thêm về tin đó" routinely fails to clear.
    _RECENT_TURNS_MAX = 3
    # An explicit "use our chat history" request is different from an ordinary
    # follow-up: the Master has deliberately selected the current session as the
    # source. It may therefore inspect more turns, while each line is shortened so
    # this context remains inside the same 500-token Worker budget.
    _SESSION_HISTORY_REFERENCE_TURNS_MAX = 8
    _SESSION_HISTORY_REFERENCE_LINE_CHARS = 100
    _SESSION_HISTORY_REFERENCE_PATTERNS = (
        r"\b(?:chat|conversation)\s+history\b",
        r"\b(?:our|this|that)\s+(?:chat|conversation)\b",
        r"lịch\s*sử\s*(?:chat|trò\s*chuyện)",
        r"(?:chat|đoạn\s*chat|cuộc\s*trò\s*chuyện)\s*(?:ấy|đó|này|trước)",
    )

    @classmethod
    def _references_session_history(cls, user_input: str) -> bool:
        """Whether the Master explicitly selected this live chat as the source.

        This is deliberately narrower than every pronoun such as "that". It only
        broadens Worker context when the wording names chat/conversation history, so
        an unrelated request cannot be pulled toward a stale subject.
        """
        lowered = (user_input or "").lower()
        return any(re.search(pattern, lowered) for pattern in cls._SESSION_HISTORY_REFERENCE_PATTERNS)

    # Concrete, unambiguous tokens only — a file path or email is a FACT, never an
    # instruction, which is exactly what keeps this safe to show the Router (see
    # _recent_entities_note's docstring for why that distinction matters).
    _RECENT_ENTITY_FILE_RE = re.compile(
        r"\b[\w\-./\\]*\.(?:txt|py|json|csv|pdf|docx?|html?|md|png|jpe?g|log|xlsx|ya?ml|js|ts|css)\b",
        re.IGNORECASE)

    # Trading pairs, as a CLOSED LIST of real instrument codes rather than a generic
    # `[A-Z]{3,5}/[A-Z]{3,5}` shape. This is a software project: the generic form
    # matches HTML/CSS, JSON/YAML and HTTP/HTTPS, which would put meaningless noise in
    # front of the Router on ordinary coding turns. Same precision-over-recall call as
    # _DISTRIBUTIVE_RE in continuation.py — a miss costs one clarifying question, a
    # false positive costs noise on every turn that mentions a tech stack.
    _SYMBOL_CODE = (r"(?:XAU|XAG|XPT|XPD|BTC|ETH|USDT|USDC|BNB|SOL|XRP|ADA|DOGE"
                    r"|USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF|CNY|VND)")
    _RECENT_ENTITY_SYMBOL_RE = re.compile(rf"\b{_SYMBOL_CODE}/{_SYMBOL_CODE}\b", re.IGNORECASE)

    def _recent_entities_note(self) -> str:
        """Deterministic, narrow signal for the Router: concrete entities (file
        paths, email addresses) literally mentioned in the last few turns of
        THIS session. Distinct from both RAG (semantic-similarity, cross-
        session, gated behind _wants_past_recall) and _recent_turns_block
        (free text, Worker-only, deliberately kept out of the Router) — this
        is neither: it is a closed list of tokens extracted by regex, never
        free-form text or an unresolved request. A file path or address is a
        fact, not an instruction, so it cannot bleed a stale REQUEST into a
        new one the way raw chat_history once did (see _recent_turns_block's
        docstring for that bug) — there is nothing here for the Router to
        mistakenly act ON, only something to ground an argument WITH.

        Found live: Brain asked "where should I save this?", the Master named
        ciel_workspace/stuff.txt, then on the NEXT turn (content now supplied)
        Brain asked "where do you want this?" again and Worker fabricated "I
        can't write files in this chat" — the path was sitting right there in
        chat_history, but the Router never saw it at all.

        Found live AGAIN, same shape, different entity type — which is why trading
        pairs joined the list: Ciel offered "em kiểm tra XAU/USD hiện tại", the
        Master answered "thử xem, kiểm tra đi", and the Router — seeing only those
        five words, with no symbol in this note because it extracted files and
        addresses only — logged {"observation": "Master yêu cầu kiểm tra nhưng chưa
        nêu đối tượng", "reasoning": "Cần hỏi lại"} and routed to chat. The Worker,
        which DOES get recent turns, correctly identified XAU/USD but had no tools
        on that path, and invented "bật lại công cụ thị trường" — false, since
        get_market_price was loaded and working. Note the asymmetry that makes a
        symbol safe here where an address needs more care: an address is the TARGET
        of an irreversible send, a symbol is only ever an argument to a read-only
        lookup, so a stale one costs a visibly wrong quote, not a real email to the
        wrong person.
        """
        try:
            if not CONTEXT_RECENT_TURNS_ENABLED:
                return ""
            history = self.chat_history.messages[:-1] if self.chat_history.messages else []
            msgs = history[-(self._RECENT_TURNS_MAX * 2):]
            found = []
            for m in msgs:
                text = m.content or ""
                for match in self._RECENT_ENTITY_FILE_RE.findall(text):
                    if match not in found:
                        found.append(match)
                for match in _EMAIL_RE.findall(text):
                    if match not in found:
                        found.append(match)
                # Uppercased on the way in: "xau/usd" and "XAU/USD" are the same
                # instrument, and uppercase is the form get_market_price expects, so
                # the Router gets a symbol it can pass straight through as an argument.
                for match in self._RECENT_ENTITY_SYMBOL_RE.findall(text):
                    sym = match.upper()
                    if sym not in found:
                        found.append(sym)
                for match in self._RECENT_ENTITY_GMAIL_ID_RE.findall(text):
                    tag = f"gmail_message_id:{match}"
                    if tag not in found:
                        found.append(tag)
            return ", ".join(found[:8])
        except Exception:
            return ""

    def _recent_turns_block(self, max_turns: int = None, line_chars: int = 240) -> str:
        """The last few turns of THIS conversation, or "" when there is nothing yet.

        Deliberately NOT sent to the Router (see `_router_persona`'s reasoning applied
        here too): the 2026-07 decision to keep chat_history out of routing exists
        because it let an old unresolved request bleed into an unrelated new one. This
        block only ever reaches the RESPONSE path (`execute_chat`, tool-result
        formatting), where "what did we just say" is exactly what is missing — never
        "which tool should run".

        Exception to "never routing": `_open_thread_note()` may inject a *gated*,
        truncated open exchange when the CURRENT message is a short slot-fill or
        nudge answering Ciel's last clarifying question — free-form history still
        never reaches the Router.
        """
        try:
            if not CONTEXT_RECENT_TURNS_ENABLED:
                return ""
            # `process()` already appended the CURRENT user message before this runs, and
            # it is shown separately as "User's request: …" — so it is excluded here to
            # avoid sending it twice.
            history = self.chat_history.messages[:-1] if self.chat_history.messages else []
            turns = self._RECENT_TURNS_MAX if max_turns is None else max(1, int(max_turns))
            chars = max(40, int(line_chars))
            msgs = history[-(turns * 2):]
            if not msgs:
                return ""
            lines = []
            for m in msgs:
                role = "Master" if m.type == "human" else "Ciel"
                text = (m.content or "").strip().replace("\n", " ")
                if len(text) > chars:
                    text = text[:chars] + "…"
                lines.append(f"{role}: {text}")
            return "\n".join(lines)
        except Exception:
            return ""

    # --- Open-thread resolution for the Router (Direction A, 2026-08) ---
    # Free-form chat_history stays out of routing (July-2026). But a bare city name
    # or "tra google đi" after Ciel asked "thành phố nào?" is not a new topic — it is
    # the Master filling the slot Ciel just opened. Without a gated note, the Router
    # sees only "Hồ Chí Minh", routes chat, and the Worker (which HAS recent turns but
    # no tools on the chat path) invents "chưa lấy được thời tiết" / "chưa tra Google
    # được" — both false. Found live 2026-08-02 on Telegram.
    #
    # Same shape, files (2026-08-03 persona test): write/read persona_test_note.txt,
    # then "Append … vào file đó" — Router saw only the deictic line (OPEN THREAD did
    # not fire: last AI was a successful read, not a clarifying ask; word count > 8),
    # routed chat, Worker invented "không có công cụ workspace". Path 2 below grounds
    # 'file đó' on the last path named in THIS session.
    _OPEN_THREAD_MAX_CURRENT_CHARS = 160
    _OPEN_THREAD_LINE_CHARS = 200
    # Last AI message looks like it was waiting on the Master.
    _OPEN_THREAD_AI_WAIT_RE = re.compile(
        r"[?？]"
        r"|thành\s*phố\s*nào|địa\s*điểm\s*nào|khu\s*vực\s*nào|ở\s*đâu"
        r"|which\s+city|where\s+(?:do|should|is)|what\s+(?:city|location)"
        r"|muốn\s+(?:xem|tra|làm)\s+gì|Master\s+muốn|file\s*nào|which\s+file"
        r"|chưa\s+(?:lấy|tra|đọc|có)\s+được"
        r"|đường\s*dẫn|lưu\s+(?:file|báo\s*cáo)|output[_\s-]?path|"
        r"could\s+not|couldn't|unable\s+to\s+(?:fetch|get|retrieve)",
        re.IGNORECASE,
    )
    # Current message is a nudge / slot fill, not a self-contained new request.
    _OPEN_THREAD_NUDGE_RE = re.compile(
        r"^(?:tra\s+(?:google|web|đi|net)|lấy\s+đi|làm\s+đi|thử\s+(?:xem|đi)|"
        r"kiểm\s+tra\s+đi|search\s+(?:it|for\s+it|that)|look\s+it\s+up|"
        r"go\s+(?:get|fetch|search)|get\s+it|do\s+it)\b",
        re.IGNORECASE,
    )
    # Deictic / continue work on a file or last tool result ("file đó", append…).
    _OPEN_THREAD_FILE_DEICTIC_RE = re.compile(
        r"(?:file\s+(?:đó|này|đấy)|cái\s+(?:file\s+)?(?:đó|này|đấy)|"
        r"vào\s+(?:file\s+)?(?:đó|này)|that\s+file|this\s+file|the\s+(?:same\s+)?file|"
        r"\bappend\b|ghi\s+thêm|thêm\s+dòng|đọc\s+lại|"
        r"xóa\s+(?:file\s+)?(?:đó|này)|sửa\s+(?:file\s+)?(?:đó|này)|"
        r"ghi\s+(?:tiếp|đè)\s+(?:vào\s+)?(?:file\s+)?(?:đó|này)?)",
        re.IGNORECASE,
    )
    # Dig / open a listed Gmail message ("email đó", "đào sâu", "get_gmail_message").
    # Live failure 2026-08-06: list returned message_id, dig follow-up CHAT-routed and
    # Worker invented "no permission for get_gmail_message" — tools were loaded.
    _OPEN_THREAD_EMAIL_DEICTIC_RE = re.compile(
        r"(?:email\s+(?:đó|này|đấy)|thư\s+(?:đó|này|đấy)|mail\s+(?:đó|này|đấy)|"
        r"đào\s+sâu|đọc\s+k[yỹ]|đọc\s+chi\s+tiết|dig\s+(?:into|deep)|"
        r"get_gmail_message|get_gmail_thread|"
        r"quan\s+trọng\s+nhất\s+vừa|vừa\s+liệt\s+kê|"
        r"that\s+email|this\s+email|the\s+(?:same\s+)?email|"
        r"read\s+(?:that|the|this)\s+(?:email|message|mail))",
        re.IGNORECASE,
    )
    # A short acknowledgement of a just-requested report delivery.  It is intentionally
    # narrower than generic "gửi" so an unrelated message never inherits an old task.
    _OPEN_THREAD_REPORT_DELIVERY_RE = re.compile(
        r"(?:cứ\s+)?(?:gửi|send)(?:\s+\S+){0,4}\s+(?:qua\s+(?:đây|telegram)|lại)"
        r"|(?:cứ\s+)?(?:gửi|send)\s+(?:đi|nó|file\s+(?:đó|này))",
        re.IGNORECASE,
    )
    # Gmail API ids as shown after list formatting / Worker summaries.
    # Allow markdown noise between label and id: **message_id:** `19fd…`
    _RECENT_ENTITY_GMAIL_ID_RE = re.compile(
        r"(?:message[_\s-]?id|messageId)[^\na-fA-F0-9]{0,24}([a-f0-9]{10,24})",
        re.IGNORECASE,
    )
    # Paths mentioned in tool results / user text (broader than extension-only list).
    _OPEN_THREAD_PATH_RE = re.compile(
        r"(?:ciel_workspace|agent_output)[\\/][^\s\"'<>|]+"
        r"|[A-Za-z]:[\\/][^\s\"'<>|]+"
        r"|\b[\w\-./\\]+\.(?:txt|py|json|csv|pdf|docx?|html?|md|png|jpe?g|log|xlsx|ya?ml|js|ts|css)\b",
        re.IGNORECASE,
    )
    # Current message stands alone as a new topic even when short — do NOT open-thread
    # (unless it is also a file-deictic / nudge — those win below).
    _OPEN_THREAD_NEW_TOPIC_RE = re.compile(
        r"(?:mấy\s+giờ|what\s+time|giờ\s+là|thời\s+tiết|weather|dự\s+báo|"
        r"gửi\s+(?:mail|email)|send\s+(?:mail|email)|giá\s+\w|price\s+of|"
        r"liệt\s+kê|list\s+\w|tin\s+tức|news\s+today|tìm\s+kiếm\s+\w{3,})",
        re.IGNORECASE,
    )

    def _is_open_thread_candidate(self, user_input: str) -> bool:
        """True when THIS message likely answers/continues Ciel's last open ask."""
        text = (user_input or "").strip()
        if not text:
            return False
        # File / email dig deictic always qualify (even if wordy or over char cap).
        if self._OPEN_THREAD_FILE_DEICTIC_RE.search(text):
            return True
        if self._OPEN_THREAD_EMAIL_DEICTIC_RE.search(text):
            return True
        if len(text) > self._OPEN_THREAD_MAX_CURRENT_CHARS:
            return False
        if self._OPEN_THREAD_NUDGE_RE.search(text):
            return True
        if self._OPEN_THREAD_NEW_TOPIC_RE.search(text):
            return False
        # Short slot-fill: city name, path fragment, single fact — no '?' of its own.
        if "?" in text or "？" in text:
            return False
        # Cap word count so a short but complete new sentence is less likely.
        words = re.findall(r"\S+", text)
        return 1 <= len(words) <= 10

    def _recent_paths_from_history(self) -> list:
        """Newest-first file paths named in the last few turns of THIS session."""
        try:
            history = self.chat_history.messages[:-1] if self.chat_history.messages else []
            found = []
            for m in reversed(history[-(self._RECENT_TURNS_MAX * 2):]):
                text = m.content or ""
                for match in self._OPEN_THREAD_PATH_RE.findall(text):
                    p = match.strip().rstrip(".,);]")
                    if p and p not in found:
                        found.append(p)
            return found[:6]
        except Exception:
            return []

    def _recent_gmail_ids_from_history(self) -> list:
        """Newest-first Gmail message ids from list/dig summaries in THIS session."""
        try:
            history = self.chat_history.messages[:-1] if self.chat_history.messages else []
            found = []
            for m in reversed(history[-(self._RECENT_TURNS_MAX * 2):]):
                text = m.content or ""
                for match in self._RECENT_ENTITY_GMAIL_ID_RE.findall(text):
                    if match not in found:
                        found.append(match)
            return found[:6]
        except Exception:
            return []

    @staticmethod
    def _clip_open_thread_line(s: str, limit: int = 200) -> str:
        s = (s or "").replace("\n", " ").strip()
        return s if len(s) <= limit else (s[:limit] + "…")

    def _last_human_ai_pair(self):
        history = self.chat_history.messages[:-1] if self.chat_history.messages else []
        last_ai = last_human = None
        for m in reversed(history):
            if last_ai is None and m.type == "ai":
                last_ai = (m.content or "").strip()
            elif last_human is None and m.type == "human":
                last_human = (m.content or "").strip()
            if last_ai is not None and last_human is not None:
                break
        return last_human, last_ai

    # An active lookup is deliberately narrower than general conversation memory. It
    # carries only the query and public source URLs that an already-successful live
    # lookup returned, and only when the next turn plainly refers back to that lookup.
    _ACTIVE_LOOKUP_TTL_SECONDS = 15 * 60
    _ACTIVE_LOOKUP_MAX_URLS = 3
    _LOOKUP_FOLLOWUP_RE = re.compile(
        r"(?:\bscrape\b|\bsmart_scrape\b|\b(?:read|open)\s+(?:that|this|the)\s+"
        r"(?:article|news|story|case)\b|\b(?:continue|dig\s+deeper|investigate\s+more)\b|"
        r"(?:đào\s+sâu|phân\s+tích\s+thêm|đọc\s+kỹ|scrape|tiếp\s+tục|"
        r"tin\s+(?:đó|này)|bài\s+(?:đó|này)|vụ\s+(?:đó|này)))",
        re.IGNORECASE,
    )
    # A terse imperative is natural immediately after Ciel returned a link, but is
    # unusable as general Router context. It is accepted only while _active_lookup is
    # still live, and the resolution below remains read-only and URL-grounded.
    _LOOKUP_GENERIC_FOLLOWUP_RE = re.compile(
        r"^\s*(?:(?:ok(?:ay)?|uh|ừ|ừm|được|rồi)[,\s]+)?"
        r"(?:(?:làm|mở|xem|đọc|tiếp)\s*(?:đi|nó|cái\s+đó|link\s+đó|bài\s+đó)?|"
        r"(?:do|open|read|continue)\s*(?:it|that|this)?)\s*[.!?]*\s*$",
        re.IGNORECASE,
    )
    _LOOKUP_MULTI_FOLLOWUP_RE = re.compile(
        r"(?:\b(?:all|both|sources?)\b|tất\s+cả|cả\s+hai|các\s+(?:bài|link|nguồn)|"
        r"những\s+(?:bài|link|nguồn))",
        re.IGNORECASE,
    )

    @classmethod
    def _is_generic_lookup_followup(cls, user_input: str) -> bool:
        """Whether a short imperative can continue a *live* lookup only."""
        return bool(cls._LOOKUP_GENERIC_FOLLOWUP_RE.search((user_input or "").strip()))

    @classmethod
    def _is_lookup_followup(cls, user_input: str) -> bool:
        """Whether a message explicitly or tersely continues a live lookup."""
        text = (user_input or "").strip()
        return bool(cls._LOOKUP_FOLLOWUP_RE.search(text) or
                    cls._is_generic_lookup_followup(text) or
                    cls._LOOKUP_MULTI_FOLLOWUP_RE.search(text))

    @staticmethod
    def _public_urls(text: str) -> list[str]:
        """Extract a small stable set of public HTTP(S) URLs from tool output."""
        urls = []
        for raw in _PUBLIC_URL_RE.findall(text or ""):
            url = raw.rstrip(".,;:!?")
            if url and url not in urls:
                urls.append(url)
        return urls

    def _remember_active_lookup(self, tool_name: str, tool_args: dict,
                                result_text: str, user_input: str) -> None:
        """Cache successful live-lookup facts for one bounded follow-up.

        It is called only after ``execute_tool`` has established success. The cache is
        memory-only so a restart cannot resurrect an old article as the meaning of
        "that article" in a later conversation.
        """
        if tool_name not in {"stealth_search", "smart_scrape"}:
            return
        args = tool_args or {}
        urls = self._public_urls(str(args.get("url", "")) + "\n" + (result_text or ""))
        if not urls:
            return
        with self._log_lock:
            previous = self._active_lookup or {}
            query = str(args.get("query") or previous.get("query") or user_input or "").strip()
            self._active_lookup = {
                "tool": tool_name,
                "query": query[:240],
                "urls": urls[:self._ACTIVE_LOOKUP_MAX_URLS],
                "created_at": time.time(),
            }
        self._log_thought(
            "CONTEXT", "active_lookup_set",
            f"{tool_name}: cached {len(urls[:self._ACTIVE_LOOKUP_MAX_URLS])} public source URL(s) "
            "for one short-lived deictic follow-up.",
        )

    def _active_lookup_note(self, user_input: str) -> str:
        """Return a Router-safe active lookup note, or an empty string.

        A broad new message never receives this context. Expiry is enforced at read
        time as well as at new-turn handling, making stale source reuse impossible
        even if this helper is called independently in a future entry point.
        """
        if not self._is_lookup_followup(user_input):
            return ""
        with self._log_lock:
            active = self._active_lookup
            if not active:
                return ""
            age = time.time() - float(active.get("created_at", 0))
            if age > self._ACTIVE_LOOKUP_TTL_SECONDS:
                self._active_lookup = None
                expired = True
            else:
                expired = False
                active = dict(active)
        if expired:
            self._log_thought("CONTEXT", "active_lookup_expired",
                              "Active live lookup exceeded its 15-minute TTL.")
            return ""
        urls = active.get("urls") or []
        if not urls:
            return ""
        generic = self._is_generic_lookup_followup(user_input)
        asks_for_many = bool(self._LOOKUP_MULTI_FOLLOWUP_RE.search(user_input or ""))
        if len(urls) > 1 and not asks_for_many:
            sources = "\n".join(f"- {url}" for url in urls)
            return (
                f"Active live lookup from THIS session (age {int(age)}s) [TARGET CHOICE REQUIRED]:\n"
                f"- Query: {active.get('query')}\n"
                f"- Exact public source URL(s):\n{sources}\n"
                f"Master (now): {self._clip_open_thread_line(user_input)}\n"
                "→ The Master is continuing this lookup, but the request names no one source. "
                "Ask one concise question identifying these links; do NOT run a tool, invent a URL, "
                "or claim a lookup is running."
            )
        selected_urls = urls if asks_for_many else urls[:1]
        sources = "\n".join(f"- {url}" for url in selected_urls)
        continuation = "short imperative" if generic else "explicit reference"
        tool_instruction = (
            "Use multi_tool with one smart_scrape step per exact URL below."
            if asks_for_many else
            "Call smart_scrape with the one exact URL below."
        )
        return (
            f"Active live lookup from THIS session (age {int(age)}s):\n"
            f"- Tool: {active.get('tool')}\n"
            f"- Query: {active.get('query')}\n"
            f"- Exact public source URL(s):\n{sources}\n"
            f"Master (now): {self._clip_open_thread_line(user_input)}\n"
            f"→ This {continuation} continues that lookup. {tool_instruction} Never invent "
            "a URL or claim the lookup is still running."
        )

    def _clear_active_lookup_for_new_turn(self, user_input: str) -> None:
        """Forget an active lookup as soon as the Master changes subject."""
        if self._is_lookup_followup(user_input):
            return
        with self._log_lock:
            if not self._active_lookup:
                return
            self._active_lookup = None
        self._log_thought("CONTEXT", "active_lookup_cleared",
                          "New non-follow-up turn cleared the prior live lookup context.")

    def _apply_active_lookup_route_override(self, decision: dict, open_thread: str) -> dict:
        """Deterministically execute a safe, URL-grounded lookup continuation.

        The Router receives the same bounded context as a hint, but a short imperative
        such as ``làm đi`` should not depend on a model correctly emitting a tool plan.
        This override is deliberately limited to the read-only ``smart_scrape`` tool and
        URLs already embedded in the active-lookup note. It never applies to files,
        email, shell, or any other deictic request.
        """
        if not (open_thread or "").startswith("Active live lookup from THIS session"):
            return decision
        if "smart_scrape" not in self._tool_map:
            return decision
        urls = self._public_urls(open_thread)
        if not urls:
            return decision
        if "[TARGET CHOICE REQUIRED]" in open_thread:
            self._log_thought(
                "ROUTER", "active_lookup_choice_required",
                f"Preserved action=chat: {len(urls)} cached URLs need an explicit selection.",
            )
            return {
                "action": "chat",
                "task": (
                    "Ask the Master to choose one source before reading it. Offer only these "
                    f"grounded URLs: {', '.join(urls)}. Do not claim a tool has run."
                ),
            }
        steps = [{"tool_name": "smart_scrape", "tool_args": {"url": url}} for url in urls]
        overridden = (
            {"action": "tool", "tool_name": "smart_scrape", "tool_args": steps[0]["tool_args"],
             "response_hint": "Read the grounded article and answer from its extracted content."}
            if len(steps) == 1 else
            {"action": "multi_tool", "tools": steps,
             "response_hint": "Read only the grounded articles and summarize their extracted content."}
        )
        self._log_thought(
            "ROUTER", "active_lookup_route_override",
            f"Replaced model route with read-only smart_scrape over {len(steps)} grounded URL(s).",
        )
        return overridden

    def _open_thread_note(self, user_input: str) -> str:
        """Bounded prior exchange for the Router when the Master is slot-filling.

        Three gates (any one can open a thread):
          1) Classic: short follow-up + last AI was a clarifying ask / "couldn't fetch".
          2) File deictic: "file đó" / append / đọc lại + a path named in recent turns.
          3) Nudge after unfulfilled: "tra google đi" when last AI admitted failure
             (same as (1) via AI_WAIT).

        Never dumps free-form multi-topic history — only prior line(s) + optional
        grounded path.
        """
        try:
            text = (user_input or "").strip()
            # A live lookup is not recent-turn context. It contains only URLs produced
            # by a successful tool in this process, so disabling history injection must
            # not also disable a safe, deterministic scrape continuation.
            active_lookup = self._active_lookup_note(text)
            if active_lookup:
                return active_lookup
            if not CONTEXT_RECENT_TURNS_ENABLED:
                return ""
            if not text or not self._is_open_thread_candidate(text):
                return ""
            last_human, last_ai = self._last_human_ai_pair()
            if not last_ai or not last_human:
                return ""

            clip = self._clip_open_thread_line
            file_deictic = bool(self._OPEN_THREAD_FILE_DEICTIC_RE.search(text))
            email_deictic = bool(self._OPEN_THREAD_EMAIL_DEICTIC_RE.search(text))
            paths = self._recent_paths_from_history()
            gmail_ids = self._recent_gmail_ids_from_history()
            ai_waiting = bool(self._OPEN_THREAD_AI_WAIT_RE.search(last_ai))
            report_delivery = bool(self._OPEN_THREAD_REPORT_DELIVERY_RE.search(text))

            # The prior exchange already names the source and asks for an HTML report;
            # "cứ gửi qua đây" accepts delivery, it is not a new ambiguous request.
            # Ground the router in the source path and the safe output convention.
            if report_delivery and "html" in (last_human + " " + last_ai).lower() and paths:
                active = paths[0]
                return (
                    f"Master (prior): {clip(last_human)}\n"
                    f"Ciel (last): {clip(last_ai)}\n"
                    f"Active source file for the HTML report (from THIS conversation): {active}\n"
                    f"Master (now): {clip(text)}\n"
                    "→ This confirms delivery of the requested HTML report. Do NOT ask for "
                    "an output path. Read the source if its facts are not in this turn; build the "
                    "report at agent_output/<source>_summary.html (or omit output_path for the "
                    "safe default), then send it with send_telegram_document. Never write into "
                    "telegram_uploads/ and do not substitute plain send_telegram for the attachment."
                )

            # --- Path 2b: dig / open email with a known message_id from THIS session ---
            if email_deictic and gmail_ids:
                active = gmail_ids[0]
                return (
                    f"Master (prior): {clip(last_human)}\n"
                    f"Ciel (last): {clip(last_ai)}\n"
                    f"Active Gmail message_id for dig / 'email đó' (from THIS conversation): "
                    f"{active}\n"
                    f"Master (now): {clip(text)}\n"
                    f"→ Call get_gmail_message(message_id=\"{active}\") now (or "
                    f"get_gmail_thread if a thread_id is known). Gmail tools ARE loaded — "
                    f"NEVER claim no permission / no get_gmail_message. Do NOT ask Master "
                    f"for the id again. Do NOT use stealth_search as a substitute for Gmail."
                )

            if email_deictic and not gmail_ids:
                return (
                    f"Master (prior): {clip(last_human)}\n"
                    f"Ciel (last): {clip(last_ai)}\n"
                    f"Master (now): {clip(text)}\n"
                    f"→ Dig email but no message_id in recent turns. Re-run search_gmail "
                    f"(subject/from from context or category:primary), then "
                    f"get_gmail_message on the top match. Never invent 'no Gmail tools'."
                )

            # --- Path 2: deictic file op with a known path from THIS session ---
            if file_deictic and paths:
                active = paths[0]
                return (
                    f"Master (prior): {clip(last_human)}\n"
                    f"Ciel (last): {clip(last_ai)}\n"
                    f"Active file path for 'file đó' / 'that file' (from THIS conversation): "
                    f"{active}\n"
                    f"Master (now): {clip(text)}\n"
                    f"→ Call append_file / read_file / write_file / delete_file with that "
                    f"path as needed. Workspace tools ARE loaded — never claim they are "
                    f"unavailable in this session."
                )

            # File deictic but no path recovered — still open so Router asks path via
            # chat only after trying entities; prefer not inventing "no tools".
            if file_deictic and not paths:
                return (
                    f"Master (prior): {clip(last_human)}\n"
                    f"Ciel (last): {clip(last_ai)}\n"
                    f"Master (now): {clip(text)}\n"
                    f"→ Deictic file reference but no path found in recent turns. "
                    f"Ask which file, or use a path from [RECENT ENTITIES] if present. "
                    f"Do NOT claim workspace tools are missing."
                )

            # --- Path 1: clarifying ask / failed fetch ---
            if not ai_waiting:
                return ""

            return (
                f"Master (prior): {clip(last_human)}\n"
                f"Ciel (asked/waiting): {clip(last_ai)}\n"
                f"Master (now answering): {clip(text)}"
            )
        except Exception:
            return ""

    def _profile_block(self) -> str:
        """TIER 7 — the Master's profile as a prompt block, or "" when it adds nothing.

        Gated by USER_MODEL_ENABLED and hard-capped by USER_MODEL_TOKEN_BUDGET, because
        anything injected here is a FIXED tax on every call that carries it — the exact
        cost pattern Tier 4 exists to control. Fail-open: any error yields "", which is
        byte-for-byte the pre-Tier-7 prompt.

        Deliberately NOT injected into the Router: it is already the most bloated prompt
        in the system (37% of a Brain call), and a profile changes *how* an answer reads
        far more often than it changes *which tool* is right. Revisit in Tier 4, once a
        single context assembler owns the budget.
        """
        try:
            if not USER_MODEL_ENABLED:
                return ""
            block = self.user_model.render(budget_tokens=USER_MODEL_TOKEN_BUDGET)
            return f"{block}\n\n" if block else ""
        except Exception:
            return ""

    # Bug found by reading a real transcript: a 4,051-char pasted conversation was
    # answered with "Đã rõ." — the model was not wrong, it was OBEYING "shortest answer
    # possible" applied uniformly to a document-sized input with no clear ask in it.
    # Below this many tokens, "answer immediately" is the right instinct (a greeting, a
    # one-line question). Above it, the right move for a chat-routed wall of text with no
    # clear instruction is to say what was read and ask what to do with it — not to
    # silently guess the task, and not to compress it into two words.
    _CHAT_LONG_INPUT_TOKENS = 220

    def execute_chat(self, task: str) -> str:
        """Worker generates a natural language response."""
        session_history_reference = self._references_session_history(task)
        capabilities_context = ""
        task_lower = task.lower()
        if any(kw in task_lower for kw in ["capabilit", "what can you do", "able to do", "list your features"]):
            capabilities_context = (
                f"\n\nCRITICAL: The user is asking what you can do. You MUST ONLY list capabilities "
                f"from this exact tool list. Do NOT invent other skills like translation or research "
                f"unless explicitly covered by these tools:\n{self._tool_list_str}"
            )

        if estimate_tokens(task) > self._CHAT_LONG_INPUT_TOKENS:
            # Long input, routed as chat (so the Brain found no clear tool intent in it) —
            # a pasted conversation or document, not a question. "Shortest answer possible"
            # is the wrong instinct here: it produces a two-word non-answer instead of
            # engaging with what was actually sent.
            style_rule = (
                "The Master just sent a LONG block of text with no clear single instruction in "
                "it — likely a pasted conversation, article, or document. Do NOT compress this "
                "into a one-line acknowledgement, and do NOT silently guess a task and act on it. "
                "Instead: briefly state what you understood from it (2-3 sentences, naming the "
                "actual topic/content — not a generic placeholder), then ask what the Master wants "
                "done with it. Address the user as 'Master'."
            )
        else:
            style_rule = (
                "Respond EXTREMELY concisely. Give the absolute shortest, clearest answer possible. "
                "No filler, no pleasantries. Address the Master by name where it falls naturally in "
                "a sentence — it does not belong on the front of every reply, and a one-line answer "
                "usually needs no address at all."
            )

        # TIER 4-STYLE ASSEMBLY — recent turns bounded and ordered like every other
        # context block. Placed via ContextAssembler (not string concatenation) so it
        # gets a budget and drops WHOLE under pressure rather than being truncated
        # mid-turn, which would put a half a Master's sentence in front of the model.
        recent_ctx = ContextAssembler()
        recent_ctx.add("session_compact", self.session_compact.render(), P_IMPORTANT)
        if session_history_reference:
            recent_turns = self._recent_turns_block(
                max_turns=self._SESSION_HISTORY_REFERENCE_TURNS_MAX,
                line_chars=self._SESSION_HISTORY_REFERENCE_LINE_CHARS,
            )
            history_rule = (
                "The Master explicitly asked you to use this session's chat history. "
                "Resolve the topic from the conversation below when it is clear; do not "
                "ask for a topic already stated there."
            )
        else:
            recent_turns = self._recent_turns_block()
            history_rule = (
                "If information is missing, ask one precise clarifying question; if the "
                "Router's reference hint says the topic is missing, do not infer a topic "
                "from recent conversation."
            )
        recent_ctx.add("recent_turns", recent_turns, P_HELPFUL)
        recent_block, recent_report = recent_ctx.render(budget_tokens=CONTEXT_RECENT_TURNS_BUDGET)
        if recent_report.dropped:
            self._log_thought("CONTEXT", "recent_turns_dropped", recent_report.summary())
        recent_section = (
            f"[RECENT CONVERSATION — for context only; answer the CURRENT request below]:\n"
            f"{recent_block}\n\n"
        ) if recent_block else ""

        persona_task = (
            f"{self.ciel_persona}\n\n"
            f"{self._profile_block()}"
            f"{style_rule}{capabilities_context}\n\n"
            "[CHAT EXECUTION BOUNDARY]\n"
            "This turn was routed as chat: no tool has run, and your response cannot "
            "start one. Do NOT say or imply that you are running, about to run, or "
            "waiting on a tool/search (including stealth_search, smart_scrape, Gmail, "
            f"or weather). Do not promise results later. {history_rule}\n\n"
            f"{recent_section}"
            f"User's request: {task}"
        )
        self._log_thought("WORKER", "chat_task", persona_task)
        response = self.worker.generate(persona_task)
        self._log_thought("WORKER", "chat_response", response)
        return response

    def _block_unbacked_chat_tool_promise(self, response: str, user_input: str) -> str:
        """Replace an impossible Worker promise on an ``action=chat`` turn.

        The prompt above reduces these claims, but it cannot be the safety boundary:
        the Worker has no tool handle in this path, so Python must prevent a model
        variant from making the Master wait for a nonexistent background operation.
        This deliberately does not create a pending task.  A pending task represents
        work that really started or awaits approval, neither of which happened here.
        """
        if not _UNBACKED_CHAT_TOOL_PROMISE_RE.search(response or ""):
            return response

        self._log_thought(
            "SAFETY", "unbacked_chat_tool_promise_blocked",
            "Worker claimed a current/future tool action on an action=chat turn; "
            "replaced with an honest clarification instead of creating fake pending work.",
        )
        if self._detect_language(user_input) == "Vietnamese":
            return (
                "Mình chưa chạy tra cứu nào ở lượt này. Bạn muốn mình kiểm tra chính xác "
                "chủ đề hoặc vụ việc nào?"
            )
        return (
            "I have not run a search in this turn. What exact topic or case should I "
            "look into?"
        )

    def _request_confirmation(self, tool_name: str, tool_args: dict, risk_override: str = None) -> bool:
        """Request Master's approval before executing a high-risk tool."""
        risk = risk_override or _RISK_DESCRIPTIONS.get(tool_name, f"Execute {tool_name}")
        args_preview = json.dumps(tool_args, ensure_ascii=False, indent=2)
        preview = (
            f"Action: {risk}\n"
            f"Tool:   {tool_name}\n"
            f"Args:   {args_preview}"
        )
        self._log_thought("SAFETY", "confirm_requested",
                          f"{tool_name}({args_preview})")

        if self.confirm_callback is None:
            # No callback wired means nobody can be asked, and silence is never consent:
            # a front end that forgot to set one must not auto-run a high-risk tool.
            self._log_thought("SAFETY", "confirm_denied_no_callback",
                              f"No confirm_callback set — denied {tool_name}.")
            return False

        try:
            approved = self._confirm_with_run_status(tool_name, preview, tool_args)
        except Exception as e:
            self._log_thought("SAFETY", "confirm_error", str(e))
            approved = False

        tag = "confirm_approved" if approved else "confirm_denied"
        self._log_thought("SAFETY", tag, tool_name)
        return approved

    def _confirm_with_run_status(self, tool_name: str, preview: str, tool_args: dict):
        """Keep the existing front-end callback and expose its waiting state."""
        manager = getattr(self, "run_manager", None)
        if manager is None:
            return self.confirm_callback(tool_name, preview, tool_args)
        with manager.approval_wait():
            return self.confirm_callback(tool_name, preview, tool_args)

    # Outbound tools whose second execution in one turn is a duplicate delivered to a
    # real person, not a retry. Keyed by RECIPIENT, not by the full argument signature:
    # the whole problem is that two mechanisms compose *different* subjects and bodies
    # for the same intended message, so a signature over all args would never match.
    _OUTBOUND_KEYS = OUTBOUND_KEYS

    def _outbound_key(self, tool_name: str, tool_args: dict):
        """Identity of the message this call would deliver, or None if not outbound."""
        return delivery_key(tool_name, tool_args)

    def execute_tool(self, tool_name: str, tool_args: dict, response_hint: str = "", user_input: str = "",
                     _raw_out: list = None, _skip_format: bool = False) -> str:
        """Compatibility entry point for core.harness.tool_execution."""
        from .harness.tool_execution import execute_tool
        manager = getattr(self, "run_manager", None)
        raw_box = _raw_out if _raw_out is not None else []
        def operation():
            return execute_tool(self, tool_name=tool_name, tool_args=tool_args,
                                response_hint=response_hint, user_input=user_input,
                                _raw_out=raw_box, _skip_format=_skip_format)
        if manager is not None:
            return manager.track_step(tool_name, tool_args, operation,
                                      evidence=lambda: raw_box[-1] if raw_box else "")
        return operation()

    @staticmethod
    def _strip_html(text: str) -> str:
        """Strip HTML tags, zero-width chars, and collapse whitespace."""
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'[\u200b\u200c\u200d\ufeff\xa0]', '', text)  # zero-width + nbsp
        text = re.sub(r'&nbsp;', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    # 150 chars/body protects the prompt budget when search_gmail returns a whole
    # inbox digest (10 emails) — found live: it applied just as hard to a SINGLE
    # result ("đọc kỹ thư OSC và tóm tắt"), chopping the only email off mid-sentence
    # even though the raw tool result already had the full body and nothing else
    # needed protecting. The Worker then reported the text as truncated, and Brain's
    # self-correction burned two attempts chasing data that was already in hand —
    # first re-running search_gmail (same cap, same cut), then escalating to
    # vision_act to "open the email properly". Scale the cap to how many emails are
    # actually in the result instead of one constant for every case.
    _EMAIL_BODY_CAP_SINGLE = 6000
    _EMAIL_BODY_CAP_DIGEST = 150

    def _compact_email_result(self, text: str) -> str:
        """Parse email JSON or formatted search_gmail blocks; keep ALL hits visible.

        After gmail_ops wraps search as prose with `message_id:` lines, the old
        JSON-only path fell through to text[:2000] and silently dropped emails 2–N
        (live P0.3: list max_results=5 but Worker only saw Render).
        """
        if not text:
            return text
        try:
            emails = json.loads(text)
            if not isinstance(emails, list):
                return self._strip_html(text)[:8000]

            per_body_cap = self._EMAIL_BODY_CAP_SINGLE if len(emails) <= 1 else self._EMAIL_BODY_CAP_DIGEST
            compact = []
            for em in emails:
                body_raw = em.get("body", "")
                body_clean = self._strip_html(body_raw)[:per_body_cap]
                compact.append({
                    "id": em.get("id") or em.get("message_id") or "",
                    "sender": em.get("sender") or em.get("from") or "",
                    "subject": em.get("subject", ""),
                    "body": body_clean,
                })
            return json.dumps(compact, ensure_ascii=False, indent=1)
        except (json.JSONDecodeError, TypeError):
            pass

        # Formatted multi-block from skills.external.gmail_ops._format_gmail_search_results
        if re.search(r"\[\d+\]\s*message_id:", text):
            parts = re.split(r"\n(?=\[\d+\]\s*message_id:)", text.strip())
            n = len(parts)
            per_cap = self._EMAIL_BODY_CAP_SINGLE if n <= 1 else max(self._EMAIL_BODY_CAP_DIGEST, 220)
            out = []
            for p in parts:
                p = p.strip()
                if not p:
                    continue
                # Soft-cap body line only; keep header lines (id/from/subject)
                lines = p.splitlines()
                kept = []
                body_budget = per_cap
                for ln in lines:
                    if re.match(r"\s*body\s*:", ln, re.I):
                        prefix, _, rest = ln.partition(":")
                        rest = rest.strip()
                        if len(rest) > body_budget:
                            rest = rest[:body_budget] + "…"
                        kept.append(f"{prefix}: {rest}")
                    else:
                        kept.append(ln)
                out.append("\n".join(kept))
            joined = "\n\n".join(out)
            # Hard ceiling so Worker still sees many short items
            return joined[:10000] if len(joined) > 10000 else joined

        return self._strip_html(text)[:8000]

    def _format_fact_result(self, tool_name: str, result_text: str, user_input: str = "") -> str:
        """Convert memory vault tool output into clean user-facing text.

        Deterministic Python, zero LLM calls — which is exactly why it used to be a
        real persona bug hiding in plain sight: it hardcoded ENGLISH templates
        ("Master, I saved your...") regardless of the Master's actual language. Every
        other reply path gets a language fix via the Worker's prompt (see
        `_detect_language` + `[USER LANGUAGE: ...]` elsewhere in this file); this path
        never reaches the Worker at all, so it has to pick its own template here.
        """
        is_vi = self._detect_language(user_input) == "Vietnamese"

        if tool_name == "get_fact":
            match = re.match(r"Fact '([^']+)':\s*(.*)", result_text, flags=re.DOTALL)
            if match:
                key, value = match.group(1), match.group(2).strip()
                return f"{key}: {value}" if is_vi else f"Master, your {key} is {value}."
            match = re.match(r"No fact found for key '([^']+)'", result_text)
            if match:
                key = match.group(1)
                return f"Chưa có thông tin đã lưu cho '{key}'." if is_vi else f"Master, I do not have a saved {key}."
            return result_text

        if tool_name == "save_fact":
            match = re.match(r"Fact saved successfully:\s*([^.]+)\.", result_text)
            if match:
                key = match.group(1)
                return f"Đã lưu: {key}." if is_vi else f"Master, I saved your {key}."
            return result_text

        if tool_name == "delete_fact":
            match = re.match(r"Fact deleted successfully:\s*([^.]+)\.", result_text)
            if match:
                key = match.group(1)
                return f"Đã xoá: {key}." if is_vi else f"Master, I deleted your {key}."
            return result_text

        return result_text

    def _refine_recalled_context(self, recalled: str) -> str:
        """Second-stage RAG compression using the Worker only when needed."""
        if not recalled or len(recalled) <= RAG_LLM_COMPRESS_CHAR_THRESHOLD:
            return recalled

        prompt = (
            "You are a context compressor for Ciel's long-term memory.\n"
            "Read the recalled memory logs below and compress them into factual core interactions.\n"
            "Strip raw HTML, scraped webpage text, long code blocks, raw diffs, stack traces, and redundant chatter.\n"
            "Keep only facts that could help answer the current user request.\n"
            "Use this exact format, one interaction per line:\n"
            "[YYYY-MM-DD] Human: ... | Ai: ...\n\n"
            "Rules:\n"
            "- Do not invent facts.\n"
            "- Keep dates when present; use [unknown] only if no date exists.\n"
            "- Keep file names, decisions, preferences, and final outcomes.\n"
            "- Output only the compressed context lines.\n\n"
            f"RECALLED MEMORY LOGS:\n{recalled[:RAG_LLM_COMPRESS_INPUT_LIMIT]}"
        )

        try:
            self._log_thought("RAG", "compress_task", recalled[:1000])
            compressed = self.worker.generate(prompt).strip()
            if not compressed:
                return recalled
            compressed = compressed[:RAG_LLM_COMPRESS_OUTPUT_LIMIT].strip()
            self._log_thought("RAG", "compressed", compressed[:1000])
            return compressed
        except Exception as e:
            self._log_thought("RAG", "compress_error", str(e))
            return recalled

    def execute_code(self, task: str, filename: str) -> str:
        """Compatibility entry point for code generation."""
        from .harness.code_generation import execute_code
        return execute_code(self, task, filename)

    def _build_market_html_from_results(self, combined_results: str, synthesized: str) -> str:
        """Parse market tool outputs and build the styled HTML dashboard.

        Uses real numbers from the tool results only. Missing values become 'N/A'.
        The Worker-synthesized text supplies risk/conclusion prose.
        """
        import re
        from datetime import date
        from skills.external.trading_ops import build_market_report_html

        text = combined_results or ""

        def _search(pattern, default="N/A"):
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(1).strip() if m else default

        # BTC from get_crypto_stats: "Stats BTCUSDT: Price=..., Change=...%, High=..., Low=..."
        btc_price = _search(r"Price=([\d.,]+)")
        btc_change = _search(r"Change=(-?[\d.,]+%?)")
        if btc_change != "N/A" and not btc_change.endswith("%"):
            btc_change += "%"
        # BTC technicals: "... RSI: 33.64 (...), MA(5,30): ..., Xu h?????ng: ..."
        btc_rsi = _search(r"RSI:\s*([\d.]+)")
        btc_trend = _search(r"Xu h\S+ng:\s*([^\n,]+)")
        # XAU from get_market_price: "Gi?? XAU/USD: 2350.1"
        xau_price = _search(r"XAU/?USD:\s*([\d.,]+)")

        # Risk level heuristic from synthesized prose (explicit risk phrases only,
        # never bare "cao" which also appears in "Cao nhat" = 24h high).
        low = (synthesized or "").lower()
        if any(w in low for w in ["rui ro cao", "r\u1ee7i ro cao", "high risk", "nguy c\u01a1 cao"]):
            risk_level = "High"
        elif any(w in low for w in ["rui ro thap", "r\u1ee7i ro th\u1ea5p", "low risk", "an toan", "an to\u00e0n"]):
            risk_level = "Low"
        else:
            risk_level = "Medium"

        # Build CLEAN prose for the HTML card. Do NOT reuse the chat-facing
        # synthesized text: it contains the "Master," greeting, a [MARKET_DATA]
        # dump and email-status meta that must never leak into the report body.
        def _clean_prose(raw: str) -> str:
            import re as _re
            s = raw or ""
            # Drop bracketed tags like [MARKET_DATA], [EMAIL], [COGNITION].
            s = _re.sub(r"\[[A-Z_]+\]", " ", s)
            # Drop email-status / send meta lines.
            drop = ("send_gmail", "message id", "tr\u1ea1ng th\u00e1i g\u1eedi", "ch\u01b0a th\u1ec3 g\u1eedi",
                    "ch\u01b0a \u0111\u01b0\u1ee3c th\u1ef1c thi", "email sent", "\u0111\u00e3 g\u1eedi",
                    "b\u00e1o c\u00e1o \u0111\u00e3 s\u1eb5n s\u00e0ng", "vui l\u00f2ng y\u00eau c\u1ea7u")
            kept = []
            for ln in s.splitlines():
                low_ln = ln.lower()
                if any(d in low_ln for d in drop):
                    continue
                kept.append(ln)
            s = "\n".join(kept)
            # Strip leading greeting.
            s = _re.sub(r"^\s*master[,:\s]*", "", s, flags=_re.IGNORECASE)
            s = _re.sub(r"[ \t]+", " ", s)
            s = _re.sub(r"\n{3,}", "\n\n", s).strip()
            return s

        clean = _clean_prose(synthesized)
        risk_factors = (clean[:600] or "N/A")
        conclusion = (clean[:800] or "N/A")

        return build_market_report_html(
            report_date=str(date.today()),
            btc_price=btc_price,
            btc_change_pct=btc_change,
            btc_rsi=btc_rsi,
            btc_trend=btc_trend,
            xau_price=xau_price,
            xau_change_pct="N/A",
            xau_trend="N/A",
            risk_level=risk_level,
            risk_factors=risk_factors,
            conclusion=conclusion,
            xau_rsi="N/A",
        )

    # Reference tokens a later multi_tool step may use to consume an EARLIER step's
    # raw output: {{prev}} = the immediately preceding step, {{step_N}} /
    # {{step_N.output}} = the N-th executed step (1-indexed). See _resolve_step_refs.
    #
    # ONE OR TWO BRACES, deliberately. The router prompt asks for {{…}}, but models do
    # not reliably produce it: two entirely different families (Claude and GPT, and every
    # model tried since) emit {step_1}. Requiring the exact double-brace form meant the
    # substitution silently no-op'd and the LITERAL text reached the tool — observed
    # writing '{prev}' and '{prev} tỷ' into real files, with no error raised anywhere.
    # This is the recurring lesson in this codebase: a guard keyed to an exact
    # model-emitted string is fragile; match the intent instead.
    _STEP_REF_RE = re.compile(r"\{{1,2}\s*(prev|step[_ ]?(\d+)(?:\.output)?)\s*\}{1,2}", re.IGNORECASE)
    _STEP_REF_MAXLEN = 4000
    # A ref filling an *_id-shaped arg almost never wants the whole raw blob — it wants
    # ONE field out of it (the common search -> fetch-by-id chain: search_gmail's JSON
    # list has an "id" per result; get_gmail_message's message_id wants exactly that).
    _ID_KEY_RE = re.compile(r"(^|_)id$", re.IGNORECASE)

    @classmethod
    def _referenced_step_indexes(cls, value, prior_count: int) -> set[int]:
        """Return zero-based source indexes referenced anywhere in nested args."""
        found = set()
        if isinstance(value, dict):
            for item in value.values():
                found.update(cls._referenced_step_indexes(item, prior_count))
        elif isinstance(value, (list, tuple)):
            for item in value:
                found.update(cls._referenced_step_indexes(item, prior_count))
        elif isinstance(value, str):
            for match in cls._STEP_REF_RE.finditer(value):
                found.add(int(match.group(2)) - 1 if match.group(2) else prior_count - 1)
        return found

    @staticmethod
    def _step_output_unusable(raw: str) -> bool:
        """True when a referenced step has no value safe to feed into another tool."""
        text = (raw or "").strip()
        if not text or text.lower() in {"[]", "{}", "null", "none"}:
            return True
        low = text.lower()
        if low.startswith(("[tool_error]", "[cancelled]", "[skipped_dependency]", "sorry,")):
            return True
        return bool(re.match(
            r"^(?:no|zero|0)\s+(?:emails?|messages?|results?|items?|records?|files?)\s+"
            r"(?:were\s+)?found\b",
            low,
        ))

    @classmethod
    def _failed_dependency(cls, args, step_outputs: list):
        """Describe the first failed/empty source referenced by ``args``, if any."""
        for idx in sorted(cls._referenced_step_indexes(args, len(step_outputs))):
            if 0 <= idx < len(step_outputs) and cls._step_output_unusable(step_outputs[idx]):
                return idx, (step_outputs[idx] or "empty output").strip().splitlines()[0][:160]
        return None

    def _extract_id_field(self, raw: str):
        """Pull a bare "id" out of a step's raw JSON output, for an *_id-shaped arg.
        Returns None (caller falls back to the raw text) on anything that doesn't
        parse as the expected shape — this only ever narrows a value, never invents
        one that was not already in the data."""
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            parsed = parsed[0]
        if isinstance(parsed, dict) and "id" in parsed:
            return str(parsed["id"])
        return None

    def _resolve_step_refs(self, value, step_outputs: list, _key: str = None):
        """Substitute {{prev}} / {{step_N}} tokens in a multi_tool step's args with the
        RAW output of an earlier step — enabling DEPENDENT chains (step N feeds step
        N+1) that the plan's static, decided-up-front args could not express (the
        long-standing 'multi_tool = independent tools only' limitation).

        Deterministic string substitution, no LLM. Recurses through dict/list; only
        rewrites str values, and only when a token is present. An out-of-range or
        not-yet-run reference is left as the literal token AND logged, so a bad
        reference is visible rather than silently blanked into wrong tool input.

        Bug found live: search_gmail -> get_gmail_message({"message_id": "{prev}"})
        substituted the STRUCTURED result_text (a JSON list with the real Gmail "id")
        wholesale into message_id — the API rejects a whole JSON blob as an id just as
        surely as it rejected the Worker's prose summary this replaced. For an
        *_id-shaped key, try to pull just the "id" field first; only the unnarrowed
        raw text falls back for every other arg shape.
        """
        if isinstance(value, dict):
            return {k: self._resolve_step_refs(v, step_outputs, _key=k) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_step_refs(v, step_outputs, _key=_key) for v in value]
        # Fast path: bail out on any string with no brace at all. It must test "{" and
        # NOT "{{" — gating on the double brace re-introduced the exact bug _STEP_REF_RE
        # was widened to fix, since a model-emitted "{step_1}" never reached the regex.
        if not isinstance(value, str) or "{" not in value:
            return value

        def _sub(m):
            token = m.group(0)
            idx = int(m.group(2)) - 1 if m.group(2) else len(step_outputs) - 1
            if 0 <= idx < len(step_outputs):
                raw = step_outputs[idx] or ""
                if _key and self._ID_KEY_RE.search(_key):
                    extracted = self._extract_id_field(raw)
                    if extracted is not None:
                        return extracted
                return raw[:self._STEP_REF_MAXLEN]
            self._log_thought(
                "TOOL", "step_ref_unresolved",
                f"multi_tool step referenced {token} but only {len(step_outputs)} "
                f"prior step(s) had run — left literal.")
            return token

        return self._STEP_REF_RE.sub(_sub, value)

    # Match an explicitly-requested email subject in the user's own words. The Brain
    # routinely substitutes its own descriptive subject even when the user says
    # "subject exactly '...'" (observed live on both the single-send and multi_tool
    # paths), so a deterministic override enforces it instead of trusting the Brain.
    _SUBJECT_RE = re.compile(
        r"(?:subject|tiêu đề|chủ đề)\s*(?:line)?\s*"
        r"(?:exactly|exact|is|should be|chính xác|phải là|:)?\s*"
        r"['\"“”‘’](.+?)['\"“”‘’]",
        re.IGNORECASE)

    def _extract_requested_subject(self, user_input: str):
        m = self._SUBJECT_RE.search(user_input or "")
        if not m:
            return None
        subj = m.group(1).strip()
        # Reject a runaway capture (a mis-placed quote swallowing the whole request).
        return subj if 0 < len(subj) <= 200 else None

    def _enforce_subject(self, send_args: dict, user_input: str) -> dict:
        """Overwrite a Brain-composed subject with the user's explicitly-requested one.
        Only touches an existing 'subject' arg (so reply_to_email without a subject is
        unaffected). Deterministic — does not rely on the Brain to comply."""
        requested = self._extract_requested_subject(user_input)
        if requested and "subject" in send_args and send_args.get("subject", "").strip() != requested:
            old = send_args.get("subject", "")
            send_args["subject"] = requested
            self._log_thought("BRAIN", "subject_override",
                              f"User asked for an exact subject; replaced '{old}' with '{requested}'.")
        return send_args

    def _review_plan_permissions(self, tools: list) -> str:
        """TIER 3 plan-level approval. Returns a refusal string to abort, or "" to run.

        Called BEFORE the first step, which is the whole point: the old per-call gate
        asked about step 3 only once steps 1-2 had already happened, so declining left
        the work half-done. Here the Master sees every step needing approval, answers
        once, and a "no" means nothing ran at all.
        """
        # Fresh slate: a grant from an earlier plan must never carry into this one.
        self.permissions.clear_plan_grants()

        review = self.permissions.review_plan(tools, attended=not self.unattended)
        denied, asked = review[Decision.DENY], review[Decision.ASK]
        deferred = review[Decision.DEFER]

        if deferred:
            # A whole plan cannot be half-approved by a machine. If any step needs the
            # Master and the Master is not here, the plan does not start.
            for st in tools or []:
                if isinstance(st, dict) and (st.get("tool_name") or "") in deferred:
                    self.deferred.add(st.get("tool_name"), st.get("tool_args") or {},
                                      reason="plan step needed approval", source="unattended plan")
            self._log_thought("SAFETY", "plan_deferred", f"steps needing approval: {deferred}")
            return (f"[CANCELLED] This plan needs your approval for "
                    f"{', '.join(sorted(set(deferred)))} and nobody was at the keyboard, "
                    f"Master. Nothing was run; it has been recorded.")

        if denied:
            self._log_thought("SAFETY", "plan_denied", f"deny-listed steps: {denied}")
            return (f"[CANCELLED] This plan needs {', '.join(sorted(set(denied)))}, which "
                    f"is on the deny list, Master. Nothing was run.")

        if not asked:
            return ""       # nothing to approve

        if self.confirm_callback is None:
            # Cancel the whole plan up front rather than letting the safe steps run and
            # the per-call gate deny the risky one halfway through.
            self._log_thought("SAFETY", "plan_denied_no_callback", f"steps needing approval: {asked}")
            return (f"[CANCELLED] This plan needs your approval for "
                    f"{', '.join(sorted(set(asked)))}, but no confirmation channel is "
                    f"connected, Master. Nothing was run.")

        lines = ["This plan needs your approval before anything runs:", ""]
        for i, t in enumerate(tools or [], 1):
            name = (t.get("tool_name") or "?") if isinstance(t, dict) else "?"
            mark = "!" if name in asked else " "
            args = json.dumps((t.get("tool_args") or {}) if isinstance(t, dict) else {},
                              ensure_ascii=False)[:120]
            lines.append(f" {mark} {i}. {name}({args})")
        lines += ["", f"Steps marked ! need approval: {', '.join(sorted(set(asked)))}"]
        preview = "\n".join(lines)

        self._log_thought("SAFETY", "plan_confirm_requested", f"steps needing approval: {asked}")
        try:
            approved = self._confirm_with_run_status("plan", preview, {"steps": asked})
        except Exception as e:
            self._log_thought("SAFETY", "plan_confirm_error", f"{type(e).__name__}: {e}")
            approved = False

        if not approved:
            self._log_thought("SAFETY", "plan_denied_by_master", str(asked))
            return "[CANCELLED] Master declined the plan. Nothing was run."

        # Grant the exact STEPS reviewed, not their tool names: a later step the Master
        # never saw (e.g. one the Tier-1 loop invents) must still be asked about, even if
        # it happens to use a tool that appeared in this plan.
        approved_steps = [t for t in (tools or [])
                          if isinstance(t, dict) and (t.get("tool_name") or "").strip() in asked]
        granted = self.permissions.grant_for_plan(approved_steps)
        self._log_thought("SAFETY", "plan_approved",
                          f"{len(granted)} exact step(s) approved for this plan only")
        return ""

    def _validate_tool_plan(self, tools, source: str) -> PlanValidation:
        """Validate an LLM plan before permissions or tool execution.

        The validator is intentionally separate from this orchestrator so its rules
        can be unit-tested without loading models, tools, or persistent state.  This
        boundary contributes the live tool catalog and each tool's Pydantic schema.
        """
        checked = validate_plan(
            tools,
            known_tools=self._tool_map.keys(),
            validate_args=self.tool_manager.validate_tool_args,
        )
        for repair in checked.repairs:
            self._log_thought(
                "PLAN", "repair",
                f"{source}: step {repair.step} {repair.code} — {repair.message}",
            )
        for issue in checked.errors:
            self._log_thought(
                "PLAN", "rejected",
                f"{source}: step {issue.step} {issue.code} — {issue.message}",
            )
        return checked

    def _run_steps(self, steps: list, response_hint: str, user_input: str,
                   results: list, step_outputs: list, records: list, label: str = "",
                   skip_format: bool = False) -> bool:
        """Compatibility entry point for core.harness.runner."""
        from .harness.runner import _run_steps
        return _run_steps(self, steps=steps, response_hint=response_hint, user_input=user_input, results=results, step_outputs=step_outputs, records=records, label=label, skip_format=skip_format)

    def _continue_until_done(self, user_input: str, response_hint: str, records: list,
                             results: list, step_outputs: list, model_requested: bool = False,
                             skip_format: bool = False):
        """Compatibility entry point for core.harness.planner."""
        from .harness.planner import _continue_until_done
        return _continue_until_done(self, user_input=user_input, response_hint=response_hint, records=records, results=results, step_outputs=step_outputs, model_requested=model_requested, skip_format=skip_format)

    def execute_multi_tool(self, tools: list, response_hint: str, user_input: str = "",
                           model_requested: bool = False) -> str:
        """Compatibility entry point for core.harness.finalizer."""
        from .harness.finalizer import execute_multi_tool
        return execute_multi_tool(self, tools=tools, response_hint=response_hint, user_input=user_input, model_requested=model_requested)

    # A claim that the mail has NOT gone out. Matched by INTENT (a negation next to a
    # send word), not by any exact sentence the model produced — the recurring lesson in
    # this codebase is that a guard keyed to an exact model string breaks the moment a
    # stronger model paraphrases it.
    #
    # Only negative claims are stripped, never positive ones. The asymmetry is deliberate:
    # this runs solely on the branch where a real Message Id came back, so a "not sent"
    # sentence is provably false, while a "sent" sentence is provably true and must
    # survive. Anti-fabrication is never weakened by this — it only ever removes a
    # statement the tool result has already disproved.
    _STALE_SEND_STATUS_RE = re.compile(
        r"[^.!?\n]*?"
        r"(?:chưa\s+(?:được\s+|thể\s+)?gửi"
        r"|chưa\s+có\s+kết\s+quả\s+gửi"
        r"|chưa\s+(?:được\s+)?xác\s+nhận\s+(?:đã\s+)?gửi"
        r"|không\s+thể\s+xác\s+nhận\s+(?:đã\s+)?gửi"
        r"|(?:has\s+)?not\s+(?:yet\s+)?been\s+sent"
        r"|not\s+(?:yet\s+)?sent"
        r"|no\s+confirmation\s+of\s+(?:the\s+)?send"
        r"|unable\s+to\s+confirm\s+(?:the\s+)?send)"
        r"[^.!?\n]*[.!?]?",
        re.IGNORECASE)

    _MESSAGE_ID_RE = re.compile(r"Message Id:\s*([0-9A-Za-z]+)")

    def _verify_sent_email(self, message_id: str):
        """Fetch the just-sent message back from Gmail by id.

        True: Gmail returned it. False: the lookup failed. None: no reader is loaded.
        """
        if "get_gmail_message" not in self._tool_map:
            return None
        try:
            res = self.tool_manager.execute_tool("get_gmail_message", {"message_id": message_id})
        except Exception:
            return False
        if isinstance(res, dict) and res.get("success") is False:
            return False
        return message_id in self.tool_manager.format_tool_result(res)

    def _email_delivery_note(self, send_args: dict, send_res: str, user_input: str) -> str:
        """The only place the Master is told an email went out — built from the real
        send result and a Gmail re-check, never from model prose."""
        to = send_args.get("to")
        to = ", ".join(map(str, to)) if isinstance(to, list) else str(to or "").strip()
        subject = str(send_args.get("subject") or "").strip()
        vi = self._detect_language(user_input) == "Vietnamese"
        send_res = send_res or ""

        match = self._MESSAGE_ID_RE.search(send_res)
        if not match:
            if send_res.lstrip().startswith("[CANCELLED]"):
                return (f"❌ Chưa gửi email tới {to} — Master đã từ chối." if vi
                        else f"❌ Email to {to} was not sent — you declined it.")
            reason = send_res.strip().splitlines()[0][:200] if send_res.strip() else "no Message Id"
            return (f"❌ Email tới {to} CHƯA được gửi: {reason}" if vi
                    else f"❌ Email to {to} was NOT sent: {reason}")

        mid = match.group(1)
        verified = self._verify_sent_email(mid)
        self._log_thought("TOOL", "email_send_verified", f"{mid}: {verified}")
        subj = ((f" — tiêu đề “{subject}”" if vi else f' — subject "{subject}"') if subject else "")
        label = "Nội dung đã gửi:" if vi else "Content sent:"
        if verified:
            note = (f"✅ Đã gửi email tới {to}{subj}. Đã kiểm tra lại trong Gmail (Message Id: {mid})."
                    if vi else f"✅ Email sent to {to}{subj}. Re-checked in Gmail (Message Id: {mid}).")
        elif verified is None:
            note = (f"✅ Gmail đã nhận email tới {to}{subj} (Message Id: {mid}); chưa kiểm tra lại được "
                    f"vì công cụ đọc Gmail không khả dụng." if vi else
                    f"✅ Gmail accepted the email to {to}{subj} (Message Id: {mid}); it could not be "
                    f"re-checked because the Gmail reader is unavailable.")
        else:
            note = (f"⚠️ Gmail báo đã gửi email tới {to}{subj} (Message Id: {mid}), nhưng kiểm tra lại "
                    f"không thấy thư. Master nên xem lại mục Đã gửi." if vi else
                    f"⚠️ Gmail reported the email to {to}{subj} as sent (Message Id: {mid}), but the "
                    f"re-check could not find it. Please check your Sent folder.")
        return f"{note}\n\n{label}"

    def _strip_stale_send_status(self, text: str) -> str:
        """Remove "not sent yet" claims from a report whose send has since succeeded.

        Called ONLY after a real Message Id is in hand, so every sentence it deletes is
        one the tool result has already contradicted. Whitespace is tidied afterwards so
        the removal does not leave an orphaned blank line where a sentence used to be.
        """
        try:
            cleaned = self._STALE_SEND_STATUS_RE.sub("", text or "")
            if cleaned == text:
                return text
            # Collapse the gaps the removal leaves behind.
            cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
            cleaned = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", cleaned)
            cleaned = "\n".join(ln.rstrip() for ln in cleaned.splitlines())
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
            self._log_thought(
                "SYSTEM", "stale_send_status_stripped",
                "Report was synthesized before the send ran and claimed it had not been "
                "sent; the send then returned a Message Id, so the stale claim was removed.")
            return cleaned or text
        except Exception:
            return text             # never let tidying break a report that is otherwise fine

    # SELF_CORRECTION_PROMPT lets the model name ANY tool as its "better approach" —
    # no cost/risk ordering at all. Found live: a stale stealth_search result on a
    # data-lookup request escalated straight to vision_act (real screen control, its
    # own Gemini call) on the FINAL self-correction attempt, having never tried the
    # cheaper, no-screen-control alternative (smart_scrape, a plain URL fetch). The
    # safety gate still asks the Master to confirm vision_act, but "asked and approved"
    # is not the same as "was the right next step" — this stops the silent escalation
    # BEFORE that prompt ever fires, deterministically, rather than trusting the model
    # to weigh cost on its own.
    _HEAVY_ESCALATION_TOOLS = {"vision_act": "smart_scrape"}

    def _self_correct(self, user_input: str, tool_name: str, tool_args: dict, result: str, max_attempts: int = 2) -> str:
        """Compatibility entry point for core.harness.recovery."""
        from .harness.recovery import _self_correct
        return _self_correct(self, user_input=user_input, tool_name=tool_name, tool_args=tool_args, result=result, max_attempts=max_attempts)

    # Inspection tools eligible for the memory-fallback below. list_workspace /
    # read_file / get_file_info sit in _SKIP_SELF_CORRECTION (raw results, no eval).
    # execute_shell_command + git_* are NOT skip-listed, but the Brain still often
    # routes "what languages for my secret project?" to git ls-files / shell — a
    # verify-first instinct that returns a repo dump unrelated to the personal fact
    # sitting in RAG (reproduced by test_rag_memory + smoke after flood). Those tools
    # need the same net after self-correction: if the dump shares no content words
    # with the question and recalled memory does, answer from RAG (labeled unverified).
    _MEMORY_FALLBACK_TOOLS = {
        "list_workspace", "read_file", "get_file_info",
        "execute_shell_command", "git_status", "git_diff", "git_list_repos",
    }

    _QUESTION_MARKERS = (
        "?", "what ", "which ", "who ", "when ", "where ", "why ", "how ",
        " gì", "gì?", " nào", "bao nhiêu", "là ai", "ở đâu", "khi nào", "thế nào", "làm sao",
    )

    _FALLBACK_STOPWORDS = {
        # en
        "the", "and", "for", "are", "was", "were", "you", "your", "our", "with", "from",
        "this", "that", "have", "has", "had", "can", "could", "should", "would", "will",
        "using", "use", "used", "please", "tell", "check", "about", "into", "onto",
        # vi
        "của", "cho", "các", "những", "được", "trong", "với", "này", "kia", "đang",
        "hãy", "cần", "phải", "một", "chúng", "mình", "tôi", "bạn", "anh", "chị",
    }

    def _content_words(self, text: str) -> set:
        return {w for w in re.findall(r"[\wÀ-ỹ]{3,}", (text or "").lower())
                if w not in self._FALLBACK_STOPWORDS}

    def _memory_fallback_for_inspection(self, user_input: str, recalled: str,
                                        tool_name: str, result: str) -> str:
        """Deterministic net for the observed 'amnesia' failure (test_rag_memory):
        the Brain's verify-first instinct routes a memory question to an inspection
        tool (list_workspace/read_file) — a REASONABLE choice, ground truth beats
        trusting RAG — but when that inspection comes back empty/unrelated, the raw
        listing used to be returned as the "answer" verbatim (these tools are in
        _SKIP_SELF_CORRECTION, so no evaluation pass ever ran). The recalled RAG
        context that could have answered the question was simply dropped.

        This keeps the verify-first routing intact and only adds the missing rung:
        IF this turn had recalled context relevant to the question AND the user asked
        a question AND the inspection result shares not a single content word with
        it, ask the WORKER (~1.6s — deliberately not a ~14s Brain call) to answer
        from the recalled memory, explicitly labeled as unverified memory. Honest
        framing over confident recall: RAG may be stale, so the reply must say it
        could not confirm from the workspace. All trigger conditions are code, not
        model judgment; on any doubt (imperatives like "list my files", results that
        do mention the asked-about things, no recall this turn) it returns the tool
        result untouched.
        """
        if not recalled or tool_name not in self._MEMORY_FALLBACK_TOOLS:
            return result
        lowered = user_input.lower()
        if not any(m in lowered for m in self._QUESTION_MARKERS):
            return result  # imperative request ("list my files") — listing IS the answer

        q_words = self._content_words(user_input)
        if not q_words:
            return result
        result_words = self._content_words(result)
        recalled_words = self._content_words(recalled)
        # Inconclusive = the inspection result addresses none of the question's terms;
        # relevant = the recalled memory addresses at least one of them.
        if q_words & result_words or not (q_words & recalled_words):
            return result

        self._log_thought("RAG", "memory_fallback_triggered",
                          f"{tool_name} result shares no content word with the question; "
                          f"answering from recalled context (labeled unverified).")
        fallback_task = (
            f"The Master asked: \"{user_input}\"\n\n"
            f"A workspace inspection ({tool_name}) found nothing related:\n{result[:800]}\n\n"
            f"[RECALLED MEMORY (long-term, may be outdated)]:\n{recalled[:2000]}\n\n"
            f"Answer the Master's question using ONLY the recalled memory above. "
            f"State clearly that this comes from your long-term memory of past "
            f"conversations and could not be verified from the current workspace. "
            f"If the recalled memory does not actually contain the answer, say honestly "
            f"that you could not determine it. Do not invent anything."
        )
        return self.execute_chat(fallback_task)

    def _search_fallback_for_chat(self, user_input: str, response: str) -> str:
        """Compatibility entry point for core.harness.recovery."""
        from .harness.recovery import _search_fallback_for_chat
        return _search_fallback_for_chat(self, user_input=user_input, response=response)

    def _evaluate_result(self, user_input: str, tool_name: str, tool_args: dict, result: str) -> dict:
        """Compatibility entry point for core.harness.recovery."""
        from .harness.recovery import _evaluate_result
        return _evaluate_result(self, user_input=user_input, tool_name=tool_name, tool_args=tool_args, result=result)

    @staticmethod
    def _is_failure_result(result: str) -> bool:
        """Deterministic detector for obviously-failed tool results (error/empty/not-found).

        Unicode-robust: normalizes to NFC and strips diacritics so Vietnamese error
        strings ("Lỗi", "không tồn tại") match regardless of NFC/NFD encoding.
        """
        text = (result or "").strip()
        if not text:
            return True
        # NFC-normalize, then strip combining marks → ASCII-fold for robust matching.
        nfkd = unicodedata.normalize("NFKD", text)
        ascii_fold = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()

        # Diacritic-free markers (match against the folded text).
        failure_markers = (
            "loi:", "loi ", "error", "traceback", "[tool_error]", "execution_error",
            "khong ton tai", "khong tim thay", "not found", "does not exist",
            "no such file", "no search results", "unavailable", "file not found",
        )
        head = ascii_fold[:150]
        return any(m in head for m in failure_markers)

    @staticmethod
    def _sanitize_outbound_email(body: str, keep_paths: bool = False) -> str:
        """Strip internal/meta content from a synthesized body.

        Removes chain-of-thought (`[COGNITION]`), persona structural tag prefixes,
        signature placeholders, and self-referential "email sent / Message Id /
        email body:" scaffolding. Operates line-by-line so the real report content
        (prices, evaluation, greeting) is preserved.

        keep_paths=False (default, for outbound EMAIL): also strips internal file
        paths — they must never leak to external recipients.
        keep_paths=True (for synthesized FILE reports written into the workspace):
        internal paths are harmless inside a local file, so they are left intact.
        """
        if not body:
            return body

        # 1a) Remove the [COGNITION] LINE only (tag + any same-line reasoning). We do
        #     NOT consume following lines: the Worker often uses [COGNITION] as a bare
        #     header immediately followed by real report data (prices, dates), and the
        #     old paragraph-wide removal ate that data. Rule 8 of the synthesis prompt
        #     already discourages multi-line reasoning here.
        text = re.sub(r"(?m)^[^\n]*\[COGNITION\][^\n]*\n?", "", body)

        # 1b) Remove remaining persona structural tag prefixes: "📊 [MARKET_DATA]", "📈 [TECHNICAL]", etc.
        text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]*\s*\[[A-Z_]+\]\s*", "", text)

        # 1c) Replace signature placeholders the Worker/Brain sometimes leaves in
        #     ("[Your Name]", "[Ký tên]") with the actual sender name.
        text = re.sub(
            r"\[\s*(?:your name|k[yý] t[eê]n|ch[uữ] k[yý]|t[eê]n c[uủ]a b[aạ]n|name|signature|sender)\s*\]",
            "Ciel", text, flags=re.IGNORECASE)

        # Diacritic-insensitive helper for matching Vietnamese meta phrases.
        # NOTE: "đ"/"Đ" are distinct letters, not composed diacritics, so NFKD does
        # NOT reduce them to "d" — map them explicitly, otherwise "Chủ đề" folds to
        # "chu đe" and never matches the "chu de" marker (leaving a "Subject:" line).
        def _fold(s: str) -> str:
            s = s.replace("đ", "d").replace("Đ", "D")
            nfkd = unicodedata.normalize("NFKD", s)
            return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()

        # Internal file paths: strip just the path TOKEN (keep the rest of the line),
        # so a legitimate sentence that merely mentions a path is not lost entirely.
        # Skipped entirely for file reports (keep_paths=True).
        path_re = None if keep_paths else re.compile(r"\b(?:agent_output|ciel_workspace)/[\w./\-]+")

        # 2) Drop lines that are purely internal meta / self-reference.
        drop_markers = (
            "message id", "email sent", "email da duoc", "da gui den", "email da gui",
            "da duoc soan va gui", "chua duoc gui", "khong co tool", "no tool available",
            "noi dung email la",
        )
        # Label / scaffold lines to drop: nested-email labels, internal notes, and a
        # redundant "Subject:" line (the real subject is a separate header field).
        label_re = re.compile(r"^\**\s*(email body|email tom tat|noi dung email|luu y|subject|chu de)\b\s*:?", re.IGNORECASE)

        kept = []
        for line in text.splitlines():
            if path_re is not None:
                line = path_re.sub("", line)             # remove internal path tokens
                line = re.sub(r"[ \t]{2,}", " ", line)   # tidy gaps left behind
            folded = _fold(line)
            if any(m in folded for m in drop_markers):
                continue
            if label_re.match(folded):
                continue
            kept.append(line)

        # 3) Collapse leftover blank runs and leading separators/blank lines.
        cleaned = "\n".join(kept)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"^\s*(-{3,}\s*\n)+", "", cleaned)
        return cleaned.strip()

    # Tools covered by MIDDLEWARE_SCOPE="email" (also the current behavior for
    # "external"/"all" until non-email channels and the chat path are wired in).
    _MIDDLEWARE_EMAIL_TOOLS = frozenset(_EMAIL_BODY_ARGS.keys())

    def _middleware_review(self, user_input: str, tool_name: str, body: str) -> str:
        """Third-tier semantic check on outbound content (see agent_system/models/middleware.py).

        Runs AFTER the deterministic sanitizer, BEFORE the safety-gate preview —
        so if Middleware revises the body, the Master's Y/N prompt shows the final
        version that will actually be sent, not a stale draft.

        Fail-open by design: disabled, out-of-scope, or erroring Middleware all
        return the body unchanged. This is a backstop ON TOP OF the deterministic
        rules, never a replacement — a Middleware hiccup must not block delivery.
        """
        if not self.middleware or not body:
            return body
        if MIDDLEWARE_SCOPE in ("email", "external", "all") and tool_name not in self._MIDDLEWARE_EMAIL_TOOLS:
            return body

        max_passes = max(1, MIDDLEWARE_MAX_PASSES)
        current = body
        for attempt in range(max_passes):
            pass_tag = f"{tool_name} (pass {attempt+1}/{max_passes})"
            try:
                verdict = self.middleware.review(user_input, current)
            except Exception as e:
                self._log_thought("MIDDLEWARE", "review_error", f"{pass_tag}: {e} — approving as-is (fail-open).\nBODY SENT UNCHANGED:\n{current}")
                break

            if verdict.get("approved", True):
                # Always log, even on silent approval — otherwise "middleware ran and
                # approved" is indistinguishable in thoughts.log from "middleware never
                # ran" (disabled/out-of-scope), which defeats the point of an audit trail.
                note = "approved, no changes." if attempt == 0 else f"approved after {attempt} revision(s)."
                self._log_thought("MIDDLEWARE", "reviewed", f"{pass_tag}: {note}\nFINAL BODY SENT:\n{current}")
                break

            reasoning = verdict.get("reasoning", "no reason given")
            revised = verdict.get("revised_body")
            if revised:
                # Full before/after diff so the audit trail can reconstruct exactly
                # what Middleware changed and why — not just the one-line reasoning.
                self._log_thought("MIDDLEWARE", "revised",
                                  f"{pass_tag}: {reasoning}\n"
                                  f"--- BEFORE ---\n{current}\n"
                                  f"--- AFTER ---\n{revised}")
                log.middleware(f"Revised {tool_name} body — {reasoning[:80]}")
                current = revised
            else:
                self._log_thought("MIDDLEWARE", "flagged_unfixable",
                                  f"{pass_tag}: {reasoning} — sending original (fail-open).\nBODY SENT UNCHANGED:\n{current}")
                log.middleware(f"Flagged but could not auto-fix {tool_name} — sending as-is: {reasoning[:80]}")
                break

        return current

    # Phrases indicating the user wants a PRIOR response sent, not new content
    # (e.g. "gửi thông tin này", "gửi cái vừa rồi", "send this", "send that report").
    # NOTE: "email"/"thư"/"mail" are deliberately EXCLUDED — "gửi qua email đó" means
    # "send to THAT email ADDRESS" (recipient), not "resend the previous content".
    # Including them mis-fired the referential override and sent stale content.
    _REFERENTIAL_SEND_PATTERNS = (
        r"th[oô]ng tin (n[aà]y|đ[oó]|v[uừ]a r[oồ]i)",
        r"(c[aá]i|n[oộ]i dung|b[aá]o c[aá]o|k[eế]t qu[aả]) (n[aà]y|đ[oó]|v[uừ]a r[oồ]i)",
        r"v[uừ]a (r[oồ]i|n[aã]y)",
        r"như (tr[eê]n|đ[aã] (n[oó]i|b[aà]n))",
        r"\bthis (info|information|report|content|summary)\b",
        r"\bthat (info|information|report|content|summary)\b",
        r"\bwhat we (just )?(discussed|talked about)\b",
        r"\bthe above\b",
        r"\bsend it\b",
    )

    # RAG recall used to be injected into the Router's prompt on EVERY turn,
    # regardless of whether the request actually needed anything from a past
    # session. Found live: "compare it with the price you just checked for gold"
    # (referring to EUR/USD asked ONE turn ago, in the live conversation) got a
    # topically-similar-but-unrelated days-old "Gold & BTC" recall injected
    # anyway, and the Brain fetched Bitcoin data instead — recalled context is
    # semantic-similarity-based, not recency-based, so injecting it on every turn
    # gives the model something irrelevant to latch onto exactly when a request
    # is ambiguous and most needs the REAL last turn (which the Router never
    # sees) rather than a plausible-looking old one. Now only injected when the
    # wording itself signals the Master is asking about something from an
    # earlier session — a deliberate "look back" request, not silent background
    # dressing on ordinary conversation.
    _PAST_RECALL_SIGNAL_PATTERNS = (
        r"l[aầ]n\s*tr[uướ]?[oớ]c", r"h[oô]m\s*qua", r"h[oô]m\s*kia",
        r"tu[aầ]n\s*tr[uướ]?[oớ]c", r"th[aá]ng\s*tr[uướ]?[oớ]c", r"tr[uướ]?[oớ]c\s*đ[aâ]y",
        r"đ[aã]\s*t[uừ]ng", r"h[oồ]i\s*tr[uướ]?[oớ]c", r"nh[uư]\s*(đ[aã]|t[oô]i\s*đ[aã])\s*n[oó]i",
        r"nh[aắ]c\s*l[aạ]i", r"nh[oớ]\s*l[aạ]i",
        r"\blast\s+(time|week|month|year)\b", r"\byesterday\b", r"\bpreviously\b",
        r"\bearlier\s+(you|we|i)\b", r"\byou\s+mentioned\b", r"\bwe\s+(discussed|talked\s+about)\b",
        r"\bin\s+(our\s+)?(the\s+)?previous\s+conversation\b",
        # Personal / project memory (test_rag_memory amnesia question and similar):
        # "What languages are we using for my secret project?" is not "yesterday"
        # wording but clearly refers to something stated earlier in the relationship.
        r"\bsecret\s+project\b", r"\bmy\s+secret\b", r"\bour\s+secret\b",
        r"\blanguages?\s+(are\s+we|we\s+are|for\s+(my|our|the))\b",
        r"d[uự]\s*[aá]n\s+b[ií] mật", r"project\s+b[ií] mật",
    )

    @staticmethod
    def _wants_past_recall(user_input: str) -> bool:
        """True only when the wording itself signals the Master is deliberately
        asking about an earlier session ("lần trước", "you mentioned", ...) —
        the gate for whether RAG recall reaches the Router at all. Same-session
        continuity ("lúc nãy", "vừa rồi") is intentionally NOT included here: that
        is what `_recent_turns_block()` already covers, from the real chat_history
        instead of a similarity search — recall is for further back than that."""
        lowered = (user_input or "").lower()
        return any(re.search(p, lowered) for p in CielCore._PAST_RECALL_SIGNAL_PATTERNS)

    # The counterpart the comment above calls out but never got its own guard:
    # "gửi qua email đó" / "cứ gửi qua email đó đi" refers to a RECIPIENT already
    # established earlier in the conversation, not to resending prior content. Found
    # live: the Master asked to send an insulting email to prokxcpro@gmail.com, Ciel
    # declined the wording, the Master replied "không không, cứ gửi qua email đó đi" —
    # and the Router, which never sees chat_history, filled `to` with kxctran@gmail.com
    # (a DIFFERENT address from an earlier, already-closed request) instead of the
    # address actually under discussion. A real email went to the wrong person.
    _RECIPIENT_REFERENTIAL_PATTERNS = (
        r"email\s*(n[aà]y|đ[oó])", r"đ[iị]a\s*ch[iỉ]\s*(n[aà]y|đ[oó])",
        r"(mail|th[uư])\s*đ[oó]", r"g[uử]i\s*qua\s*đ[oó]", r"g[uử]i\s*(cho|t[oớ]i)\s*đ[oó]",
        r"\bthat email\b", r"\bthat address\b", r"\bsend it there\b",
    )

    @staticmethod
    def _is_recipient_referential(user_input: str) -> bool:
        """True if the request refers to a recipient already established earlier
        ("gửi qua email đó") rather than naming one fresh. Distinct from
        `_is_referential_send`, which protects the BODY — this protects the `to`."""
        lowered = (user_input or "").lower()
        return any(re.search(p, lowered) for p in CielCore._RECIPIENT_REFERENTIAL_PATTERNS)

    def _last_mentioned_email(self) -> str:
        """The most recent explicit email address in THIS conversation, scanning
        chat_history newest-first — the same ground truth `_last_ai_message_text()`
        uses for content, applied to a recipient instead. Skips the just-added CURRENT
        turn (it is the referential one asking to reuse an address, not naming one).
        """
        history = self.chat_history.messages[:-1] if self.chat_history.messages else []
        for msg in reversed(history):
            m = _EMAIL_RE.search(msg.content or "")
            if m:
                return m.group(0)
        return ""

    @staticmethod
    def _to_as_lower_list(to_val) -> list:
        """`to` can be a single address or a list (GmailSendMessage's real schema).
        Normalize to a lowercase list so membership checks don't compare an address
        string against a Python list's str() representation (always False, which
        silently made the guard think ANY list-valued `to` needed overriding)."""
        return recipients_lowered(to_val)

    def _resolve_referential_recipient(self, tool_name: str, tool_args: dict,
                                       user_input: str) -> dict:
        """Ground `to` in the real conversation when the Master says "that email"
        instead of naming one. The Router never sees chat_history, so a deictic
        recipient reference is exactly the case it has to guess at — and a wrong
        guess here does not corrupt a reply, it sends a real email to the wrong
        person. Deterministic and narrow: only fires when (a) the tool actually
        sends somewhere, (b) THIS turn's own wording is referential, and (c) THIS
        turn names no address of its own to override — a fresh, explicit address
        always wins over any history lookup.

        Candidate priority: the address already sitting in THIS turn's own message/
        html_body (already synthesized against the real request) is a closer, more
        specific ground truth than reaching back into chat_history — history is only
        consulted when the body itself names nothing. Reduces the odds of "most
        recent mention in history wins" picking up an unrelated address (e.g. the
        Master's own email, casually mentioned in an unrelated aside) over the one
        the body was actually written for.
        """
        if tool_name not in ("send_gmail_message", "send_gmail_html_message"):
            return tool_args
        if not self._is_recipient_referential(user_input):
            return tool_args
        if _EMAIL_RE.search(user_input or ""):
            return tool_args   # this turn names its own address — trust it, not history
        current_to = self._to_as_lower_list(tool_args.get("to"))
        body_text = str(tool_args.get("message") or tool_args.get("html_body") or "")
        body_match = _EMAIL_RE.search(body_text)
        if body_match and body_match.group(0).lower() not in current_to:
            candidate, source = body_match.group(0), "this turn's own message body"
        else:
            candidate, source = self._last_mentioned_email(), "chat history"
        if not candidate or candidate.lower() in current_to:
            return tool_args
        self._log_thought(
            "SAFETY", "recipient_referential_override",
            f"{tool_name}: Master's wording referred to a recipient already discussed "
            f"('{user_input[:60]}'), but the Router filled 'to' with "
            f"'{tool_args.get('to')}' — overriding with the address found in "
            f"{source}, '{candidate}'.")
        tool_args = dict(tool_args)
        tool_args["to"] = candidate
        return tool_args

    # Characters/words that identify the language the user actually wrote in. Needed
    # because the Vilao path translates the request to English before routing — after
    # which the Brain has no way to know a "tin tức hôm nay" request came from a
    # Vietnamese speaker, and builds an ENGLISH search query. Observed live: the same
    # tool returns local VN coverage for "tin tức hôm nay" but Astoria/Nigeria trivia
    # for "top news headlines July 25 2026".
    _VN_DIACRITICS = set("ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụỳýỷỹỵ")
    _VN_ASCII_HINTS = ("tin tuc", "hom nay", "gia vang", "the gioi", "giup toi",
                       "cho toi", "thoi tiet", "bao cao", "tim kiem")

    @classmethod
    def _detect_language(cls, text: str) -> str:
        """Best-effort, zero-cost language label for the ORIGINAL request."""
        t = (text or "").lower()
        if any(ch in cls._VN_DIACRITICS for ch in t) or any(h in t for h in cls._VN_ASCII_HINTS):
            return "Vietnamese"
        # Kana first: it is unique to Japanese, whereas kanji share the CJK ideograph
        # block with Chinese (checking Chinese first mislabels Japanese text).
        for lo, hi, name in (("぀", "ヿ", "Japanese"), ("가", "힯", "Korean"),
                             ("Ѐ", "ӿ", "Russian"), ("一", "鿿", "Chinese")):
            if any(lo <= ch <= hi for ch in t):
                return name
        return "English"

    # Literals the Master typed that a paraphrase must never alter: absolute/relative
    # filesystem paths, URLs and email addresses. These are ADDRESSES — a rewritten one
    # does not point at a slightly different thing, it points at nothing.
    _LITERAL_TOKEN_RE = re.compile(
        r"[A-Za-z]:[\\/][^\s\"'<>|]+"                    # C:\Users\… / D:/proj/…
        r"|(?:https?://|www\.)[^\s\"'<>]+"               # URLs
        r"|[\w.\-+]+@[\w.\-]+\.\w+"                      # emails
        r"|(?:ciel_workspace|agent_output)[\\/][^\s\"'<>|]+"   # sandbox paths
    )

    @classmethod
    def _lost_literals(cls, original: str, rewritten: str) -> list:
        """Literals present in `original` that did not survive into `rewritten`.

        Case-insensitive and separator-insensitive, so C:\\Users\\x and c:/users/x count
        as the same address; only a genuine change (a redaction, a truncation, an
        invented substitute) is reported.
        """
        def norm(s):
            return (s or "").replace("\\", "/").lower()
        hay = norm(rewritten)
        return [tok for tok in cls._LITERAL_TOKEN_RE.findall(original or "")
                if norm(tok) not in hay]

    def _load_pending_action_from_disk(self):
        """Recover a still-valid pending action after a process restart. Returns None
        (and wipes a stale file) if the record is missing, unreadable, or expired —
        never let a corrupt/ancient file resurrect a confirmation the Master forgot
        about days ago."""
        try:
            if not self._pending_state_path.exists():
                return None
            p = json.loads(self._pending_state_path.read_text(encoding="utf-8"))
            if not isinstance(p, dict) or time.time() - p.get("ts", 0) > self._PENDING_TTL_SECONDS:
                self._pending_state_path.unlink(missing_ok=True)
                return None
            return p
        except Exception:
            return None

    def _set_pending_action(self, tool: str, args: dict, source: str):
        """Stage a follow-up action a bare affirmation should resolve to. Written to
        disk immediately (not just held in RAM) so the confirmation survives a crash
        or restart, not only the remainder of this REPL session."""
        self._pending_action = {"tool": tool, "args": args, "ts": time.time(), "from": source}
        try:
            self._pending_state_path.parent.mkdir(parents=True, exist_ok=True)
            self._pending_state_path.write_text(
                json.dumps(self._pending_action, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception as e:
            log.error(f"Failed to persist pending action to disk: {e}")
        self._log_thought("SYSTEM", "pending_action_set",
                          f"{source} → awaiting confirmation for {tool}({args}).")

    def _clear_pending_action(self):
        """Resolve (or drop) the pending slot in both RAM and on disk."""
        self._pending_action = None
        try:
            self._pending_state_path.unlink(missing_ok=True)
        except Exception:
            pass

    def _get_pending_action(self):
        """The still-valid pending action, or None. Expires by TTL so a "yes" typed
        long after the preview can never fire a stale destructive action."""
        p = self._pending_action
        if not p:
            return None
        if time.time() - p.get("ts", 0) > self._PENDING_TTL_SECONDS:
            self._log_thought("SYSTEM", "pending_action_expired",
                              f"{p['tool']} pending confirmation expired after "
                              f"{self._PENDING_TTL_SECONDS}s — cleared.")
            self._clear_pending_action()
            return None
        return p

    # "What were you doing?" — answerable from the task store alone. The Router never
    # sees chat_history, so without this the Brain would have to guess, and short inputs
    # like these fall under RAG's MIN_QUERY_LENGTH too. Deterministic, zero LLM calls.
    _STATUS_QUERY_RE = re.compile(
        r"^\s*(?:(?:bạn|cậu|mày)?\s*(?:đang|vừa)\s*làm\s*(?:gì|cái\s*gì)|"
        r"làm\s*(?:tới|đến)\s*đâu\s*(?:rồi)?|tiến\s*độ(?:\s*(?:sao|thế\s*nào|ra\s*sao))?|"
        r"status|progress|what\s+(?:were|are)\s+you\s+doing|where\s+(?:were|are)\s+we)"
        r"\s*[?.!]*\s*$", re.IGNORECASE)

    def describe_unfinished(self) -> str:
        """One line about a job that stopped without completing, or ''. Surfaced at
        start-up so an interrupted task is visible instead of silently lost."""
        rec = self.tasks.unfinished()
        return rec.describe() if rec else ""

    def _answer_status_query(self) -> str:
        """Report current/recent work straight from the store."""
        manager = getattr(self, "run_manager", None)
        if manager is not None:
            runs = manager.store.recent(5)
            if runs:
                return "Recent runs:\n" + "\n".join(
                    f"  [{run['state']}] {run['lane']}: {run['goal'][:120]}"
                    for run in runs
                )
        act = self.tasks.active
        lines = []
        if act:
            lines.append(f"Currently working on: {act.describe()}")
        recent = [r for r in self.tasks.recent(4) if r is not act]
        if recent:
            lines.append("Recent:")
            lines += [f"  • {r.describe()}" for r in recent]
        if not lines:
            return "Nothing in progress, Master — no task has been recorded yet."
        return "\n".join(lines)

    @staticmethod
    def _is_referential_send(user_input: str) -> bool:
        """True if the request refers to previously-generated content ("send this/
        that") rather than asking for a fresh report. Used to stop the Brain from
        re-authoring (and potentially fabricating) content it only saw truncated
        to 200 chars in the router's history window (see router.py route())."""
        lowered = (user_input or "").lower()
        return any(re.search(p, lowered) for p in CielCore._REFERENTIAL_SEND_PATTERNS)

    def _last_ai_message_text(self) -> str:
        """Full (untruncated) text of the most recent Ciel response in chat history.
        Router.route() truncates history to 200 chars per message for its own
        context window, but self.chat_history itself always holds the full text."""
        for msg in reversed(self.chat_history.messages):
            if msg.type == "ai":
                return msg.content
        return ""

    @staticmethod
    def _plaintext_to_html(text: str) -> str:
        """Render a clean plain-text/markdown email body into simple HTML so line
        breaks and paragraphs survive (send_gmail_message transmits as text/html)."""
        return plaintext_to_html(text)

    def process(self, user_input: str) -> str:
        """Run a conversation turn through the modular runtime pipeline."""
        from .harness.turn import process_turn
        return process_turn(self, user_input)

    def _fallback_direct_action(self, user_input: str) -> dict:
        """Fallback when Brain router is blocked by provider content/safety filter.
        Uses simple heuristics + Worker to generate proper content for common actions
        like sending Gmail (the main case that triggers filters on Vilao).
        """
        lowered = user_input.lower()

        # Gmail / email send intent (very common trigger for content filter on Brain)
        if self._is_email_send_intent(user_input, lowered):
            # Extract recipient email if present, otherwise default to the known test address
            import re
            match = _EMAIL_RE.search(user_input)
            to_addr = match.group(0) if match else "kxctran@gmail.com"

            # Detect which assets the user ACTUALLY named — do not hardcode XAU/BTC.
            # Each entry: (keywords, display label, price symbol, crypto symbol or None).
            # crypto symbol enables 24h stats + technical analysis (Binance); None = price only.
            asset_catalog = [
                (("vàng", "vang", "gold", "xauusd", "xau"), "XAU/USD", "XAU/USD", None),
                (("bạc", "bac", "silver", "xagusd", "xag"), "XAG/USD", "XAG/USD", None),
                (("bitcoin", "btc"),                         "BTC/USD", "BTC/USD", "BTCUSDT"),
                (("ethereum", "eth"),                        "ETH/USD", "ETH/USD", "ETHUSDT"),
                (("eurusd", "eur/usd", "eur"),               "EUR/USD", "EUR/USD", None),
                (("gbpusd", "gbp/usd", "gbp"),               "GBP/USD", "GBP/USD", None),
                (("usdjpy", "jpy"),                          "USD/JPY", "USD/JPY", None),
            ]
            detected = [(lbl, price, crypto) for keys, lbl, price, crypto in asset_catalog
                        if any(k in lowered for k in keys)]

            # Market intent = a concrete asset was named, or an explicit market word used.
            strong_market_words = ("thị trường", "market", "forex", "crypto", "chứng khoán", "cổ phiếu")
            is_market_email = bool(detected) or any(w in lowered for w in strong_market_words)

            # Build a sensible subject (market/research branches override this below).
            # The football-practice subject only applies to an actual schedule/reminder
            # request — NOT to news like "tổng hợp tin World Cup" (which contains "bóng đá"
            # but is a news summary, not a practice reminder).
            is_practice_reminder = (("đá bóng" in lowered or "bóng đá" in lowered)
                                    and any(w in lowered for w in ("lịch", "tập", "luyện", "nhắc", "buổi", "practice", "training")))
            if is_practice_reminder:
                subject = "Nhắc nhở: Lịch tập đá bóng"
            elif "lịch" in lowered and not any(w in lowered for w in ("tổng hợp", "tổng kết", "tin tức", "báo cáo", "phân tích")):
                subject = "Thông báo lịch"
            else:
                subject = "Email từ Ciel"

            if is_market_email:
                # No specific asset but general market intent → a broad snapshot.
                if not detected:
                    detected = [("XAU/USD", "XAU/USD", None), ("BTC/USD", "BTC/USD", "BTCUSDT")]

                labels = " & ".join(lbl for lbl, _, _ in detected)
                subject = f"Báo cáo thị trường {labels} – Đánh giá rủi ro"

                # Build the data-tool plan for EXACTLY the detected assets.
                tools_plan = []
                for _lbl, price_sym, crypto_sym in detected:
                    tools_plan.append({"tool_name": "get_market_price", "tool_args": {"symbol": price_sym}})
                    if crypto_sym:
                        tools_plan.append({"tool_name": "get_crypto_stats", "tool_args": {"symbol": crypto_sym}})
                        tools_plan.append({"tool_name": "analyze_crypto_technical", "tool_args": {"symbol": crypto_sym, "interval": "1d"}})
                tools_plan.append({"tool_name": "send_gmail_message", "tool_args": {"to": to_addr, "subject": subject, "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]"}})

                return {
                    "action": "multi_tool",
                    "tools": tools_plan,
                    "response_hint": ("Gather real prices, stats, and technicals first using the data tools. "
                                      "Then synthesize a clean professional email body that directly answers the user's request "
                                      "using ONLY the real data collected. Report ONLY the assets that were actually queried — "
                                      "never attribute one asset's technicals (RSI/MA) to a different asset, and if an asset has no "
                                      "technical data, say so honestly instead of borrowing another's. Make it clear, well-structured, "
                                      "polite, and useful like a proper sent email. Use Vietnamese if the request is in Vietnamese. "
                                      "Never use placeholders, meta tags, or internal paths. Send via the send tool. Never claim sent without a real Message Id.")
                }

            # DOCUMENT-BASED EMAIL: the request references a concrete file ("đọc file
            # X.pdf và gửi báo cáo..."). Observed failure: the old direct-send branch
            # composed a polite shell email WITHOUT ever reading the file, so the
            # recipient got "báo cáo đã được chuẩn bị... [Your Name]" with no content.
            # Plan the read step first, then let multi_tool synthesis fill the body
            # from the document's actual text before the send re-executes.
            file_match = re.search(
                r'((?:[A-Za-z]:[\\/])?[\w.\\/ -]*?[\w-]+\.(pdf|docx|txt|md|json|csv|log))\b',
                user_input, re.IGNORECASE)
            if file_match:
                fname = file_match.group(1).strip()
                ext = file_match.group(2).lower()
                read_tool = "read_document" if ext in ("pdf", "docx") else "read_file"
                return {
                    "action": "multi_tool",
                    "tools": [
                        {"tool_name": read_tool, "tool_args": {"filename": fname}},
                        {"tool_name": "send_gmail_message", "tool_args": {"to": to_addr, "subject": subject, "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]"}},
                    ],
                    "response_hint": ("Read the document FIRST, then synthesize a professional email that reports the "
                                      "document's ACTUAL content (summary, key points, data). If reading failed or the file "
                                      "has no extractable text, say so honestly instead of sending an empty shell email. "
                                      "Match the language of the request. Never leave placeholders like [Your Name] — sign as Ciel."),
                }

            # RESEARCH-BASED EMAIL: a summary/report/news request with no market asset
            # and no file. Observed failure (thoughts.log): the direct-send branch below
            # asked the Worker to "write an email" from a request that contains NO data,
            # so it produced a hollow shell ("báo cáo đã chuẩn bị và đính kèm... [Your Name]")
            # and sent it. Instead, gather real data via stealth_search first, then let
            # multi_tool synthesis build the body from actual results (or report honestly
            # if search failed) before sending.
            research_words = ("tổng kết", "tình hình", "báo cáo", "cập nhật", "tin tức", "phân tích",
                              "tổng hợp", "summary", "report", "situation", "news", "update", "analysis", "overview")
            # Skip when referential ("gửi báo cáo VỪA RỒI") — that means resend prior
            # content, handled by the referential-send override in process(), not a fresh search.
            if any(w in lowered for w in research_words) and not self._is_referential_send(user_input):
                # Build a search query from the request minus the send/recipient noise.
                #
                # This used to SPLIT on the first send-verb and keep only what came
                # BEFORE it, assuming the shape "<topic> … then send it to X". That is
                # only one of the two natural phrasings. Observed live: "viết một email
                # ngắn gửi tới <addr> … tóm tắt tình hình kinh tế Việt Nam hôm nay" put
                # the word "email" third, so the query became literally "viết một" — the
                # search returned essay-writing tutorials and the email that went out
                # said it had found no economic data at all.
                #
                # Subtracting the noise instead of splitting on it keeps the topic
                # wherever the Master happened to put it.
                query = _EMAIL_RE.sub(' ', user_input)
                query = re.sub(r'["\'“”‘’][^"\'“”‘’]{0,80}["\'“”‘’]', ' ', query)   # quoted subject
                query = re.sub(
                    r'\b(?:viết|soạn|tạo|compose|write|draft)\s+(?:một|1|an?|the)?\s*'
                    r'(?:email|mail|thư|message)\b'                       # "viết một email"
                    r'|\b(?:gửi|gởi|send|forward|chuyển)\s*(?:tới|cho|đến|to|qua)?\b'   # "gửi tới"
                    r'|\b(?:email|mail|thư)\b'
                    r'|\b(?:với|có)?\s*(?:tiêu\s*đề|chủ\s*đề|subject|title)\b'
                    r'|\b(?:ngắn|gọn|ngắn\s*gọn|brief|short)\b',
                    ' ', query, flags=re.IGNORECASE)
                query = re.sub(r'\s{2,}', ' ', query).strip(" ,.-–—:;")
                # If subtraction ate almost everything, the request was mostly noise —
                # fall back to the raw text rather than searching for a fragment.
                if len(query) < 8:
                    query = _EMAIL_RE.sub(' ', user_input).strip()
                self._log_thought("BRAIN", "fallback_search_query",
                                  f"topic extracted from email request: {query!r}")
                # Topic-based subject beats the generic/football default for a news summary.
                topic = re.sub(r'^(vậy|hãy|please|xin|làm ơn)\s+', '', query, flags=re.IGNORECASE).strip()
                research_subject = f"Tổng hợp thông tin: {topic[:70]}" if topic else subject
                return {
                    "action": "multi_tool",
                    "tools": [
                        {"tool_name": "stealth_search", "tool_args": {"query": query, "max_results": 5}},
                        {"tool_name": "send_gmail_message", "tool_args": {"to": to_addr, "subject": research_subject, "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]"}},
                    ],
                    "response_hint": ("Search the web FIRST, then synthesize a professional email that reports the ACTUAL "
                                      "findings from the search results (facts, figures, key points with brief context). "
                                      "If the search returned an error or no usable results, say so honestly and do NOT send "
                                      "an empty shell email claiming a report is 'attached' or 'prepared'. Never claim a file is "
                                      "attached — put the content directly in the body. Match the request's language. Sign as Ciel."),
                }

            # Non-market or simple email: original Worker body gen + direct send
            try:
                body_task = (
                    f"Viết một email lịch sự, rõ ràng, nội dung đàng hoàng bằng tiếng Việt "
                    f"cho yêu cầu của Master sau: \"{user_input}\". "
                    f"Chủ đề ngắn gọn, thân thiện. Giữ giọng điệu chuyên nghiệp và lịch sự. "
                    f"Địa chỉ người nhận nếu có trong yêu cầu thì giữ nguyên. "
                    f"Không thêm thông tin bịa đặt. "
                    f"QUAN TRỌNG: TUYỆT ĐỐI KHÔNG đề cập bất kỳ đường dẫn file nội bộ nào (agent_output/, ciel_workspace/...) trong email. Sử dụng ngôn ngữ chung chung chuyên nghiệp như 'báo cáo chi tiết đã được chuẩn bị' hoặc đưa nội dung trực tiếp vào email. Nếu cần, đề cập file dưới dạng 'file đính kèm' mà không tiết lộ vị trí lưu trữ nội bộ. "
                    f"If this is market data + evaluation, follow the Market Report structure in note.txt. Fill ONLY with real tool data; never invent numbers."
                )
                generated_body = self.worker.generate(body_task)
            except Exception:
                generated_body = user_input  # last resort

            return {
                "action": "tool",
                "tool_name": "send_gmail_message",
                "tool_args": {
                    "to": to_addr,
                    "subject": subject,
                    "message": generated_body
                },
                "response_hint": "Confirm the email ONLY if the send tool returned a real Message Id. Otherwise say the email was prepared but not confirmed sent. Never claim it was sent without a Message Id."
            }

        # Default fallback: let it go to normal chat path
        return {
            "action": "chat",
            "task": f"Provider safety filter blocked advanced routing. Please respond helpfully to: {user_input}"
        }

    # ==========================================================
    # LEGACY COMPATIBILITY
    # ==========================================================
    def chat_with_tools(self, user_input: str, use_coder: bool = False):
        response = self.process(user_input)
        class FakeAIMessage:
            def __init__(self, content):
                self.content = content
                self.tool_calls = []
        return FakeAIMessage(f"<RESPONSE>{response}</RESPONSE>")


def build_worker_prompt(user_request: str, tool_results_string: str) -> str:
    """Format the input for the Local Worker model based on tool results."""
    return f"""[USER REQUEST]
{user_request}

[TOOL RESULTS]
{tool_results_string}

Task: Based ONLY on the [TOOL RESULTS] above, answer the [USER REQUEST]. Be extremely concise. Address user as 'Master'."""
