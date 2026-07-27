"""llm_connector.py — Bridge between Ciel's core and the Brain-Worker agent system.

Architecture:
  User Input → Router (Brain) → decides:
    - "chat" → Worker generates natural response
    - "tool" → Ciel's ToolManager executes, Worker formats (if needed)
    - "code" → Worker generates code, buffer writes to disk
    - "multi_tool" → Executes sequentially, Worker synthesizes
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
)
from core.cost import estimate_cost

load_dotenv()


# ==========================================================
# SELF-CORRECTION PROMPT — Brain evaluates tool results
# ==========================================================
SELF_CORRECTION_PROMPT = """You are evaluating whether a tool's result adequately answers the user's original request.

Respond ONLY with valid JSON (no markdown, no prose):

If the result answers the user's request adequately:
{{"satisfied": true}}

If the result is empty, incomplete, or wrong AND you know a better approach:
{{"satisfied": false, "reasoning": "1-sentence explanation", "action": "tool", "tool_name": "alternative_tool", "tool_args": {{"key": "value"}}, "response_hint": "how to present"}}

If the result is insufficient and no tool can help, explain to user:
{{"satisfied": false, "reasoning": "1-sentence explanation", "action": "chat", "task": "instruction for Worker to explain the situation"}}

RULES:
- Return satisfied=true if the result reasonably answers the request, even partially.
- A result is NEVER satisfactory if it is an error message, empty, "not found",
  "does not exist", "không tồn tại", or clearly unrelated junk — in those cases you
  MUST return satisfied=false and provide the best alternative tool, or an "action":"chat"
  that honestly explains the situation to the user.
- Only return satisfied=false if you have a CONCRETE better alternative OR the result is an error/empty/junk.
- NEVER suggest the same tool with identical arguments.
- Keep reasoning to 1 sentence.
"""


RAG_LLM_COMPRESS_CHAR_THRESHOLD = 4000  # Roughly 1000 tokens.
RAG_LLM_COMPRESS_INPUT_LIMIT = 20000
RAG_LLM_COMPRESS_OUTPUT_LIMIT = 5000


# ==========================================================
# MAIN ORCHESTRATOR
# ==========================================================

# Risk descriptions for high-risk tools (shown in confirmation prompt)
_DANGEROUS_CODE_PATTERNS = (
    r"format\s+[a-z]:", r"shutil\.rmtree\(", r"rm\s+-rf\s+/", r"del\s+/f\s*/s\s*/q",
    r"shutdown\s+/[rsf]", r"os\.system\([^)]*\brm\b", r"DROP\s+DATABASE", r"DROP\s+TABLE",
    r"mkfs\.", r"diskutil\s+erasedisk", r":(){ :\|:& };:",
)


# ONE definition of "an email address", used everywhere one is extracted or stripped.
# The `+` in the local part is the reason this is centralised: seven copies of a
# `+`-blind pattern had drifted through the file, and three of them extract the RECIPIENT.
# Against "first.last+tag@sub.domain.com" such a pattern matches only "tag@sub.domain.com",
# i.e. it would have addressed the mail to the wrong person — silently, since the result
# is still a valid-looking address. Gmail's +tag form is common, so this is not exotic.
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w.\-]+\.\w+")


def _find_dangerous_code_patterns(text: str) -> list:
    """Deterministic scan for genuinely destructive code/commands (drive format, mkfs,
    rmtree, fork bombs, etc.) — same bar backtest/test_hard_special.py checks against.
    Code generation (execute_code) and write_file/append_file never passed through the
    Safety Gate before (only pre-declared high-risk TOOLS did), so an LLM could write a
    fully wired format_drive()/mkfs call straight to disk with zero confirmation."""
    return [pat for pat in _DANGEROUS_CODE_PATTERNS if re.search(pat, text, re.IGNORECASE)]


# Deferred-synthesis placeholder detector (deterministic, model-agnostic). execute_multi_tool
# defers a write_file/send_gmail_message step whose content is "still waiting on real data"
# and re-executes it after the Worker synthesizes the actual report. This used to be
# recognized only via an exact literal match on the two markers this codebase itself
# generates ("..._TO_BE_SYNTHESIZED]"). Observed gap: a stronger Brain model (Opus) planned
# its OWN write_file step instead of relying on the workflow safeguard, and phrased the
# placeholder differently — "[SYNTHESIZE_FROM_RESULTS: today's date from get_current_time,
# ...]" — which the literal match didn't recognize. The classification silently missed it,
# so that raw placeholder text was written straight to disk as the "report" (a hollow-shell
# bug distinct from data fabrication: no invented facts, just unresolved plan scaffolding
# leaking into a real file). Match on the SYNTHES* root inside brackets instead of one exact
# string, so any Brain phrasing of the same "fill this in after synthesis" intent is caught.
_UNSYNTHESIZED_PLACEHOLDER_RE = re.compile(
    r"\[[^\[\]\n]{0,160}?(?:SYNTHES\w*|TO_BE_FILLED|PLACEHOLDER)[^\[\]\n]{0,160}?\]",
    re.IGNORECASE,
)


def _has_unsynthesized_placeholder(text: str) -> bool:
    """True if `text` still contains a bracketed 'fill this in after synthesis' marker
    instead of real content, regardless of the Brain's exact wording for it."""
    return bool(text) and bool(_UNSYNTHESIZED_PLACEHOLDER_RE.search(text))


# Errors where NO tool_args correction can possibly help — retrying with guessed
# parameters is guaranteed to fail again, it just costs an extra Worker call each time.
# Found via scripts/prompt_harness.py: 220 HEALING_TRIGGER/UnclassifiedError occurrences
# in thoughts.log, most of them this class (missing python library, network/geo issues)
# rather than an actual parameter typo the self-healing "guess a corrected arg" prompt
# (recovery_manager.py's "other tools" branch) could ever fix. Deliberately narrow: genuine
# format issues ("Could not find price for BTCUSDT... Ensure format is correct") are LEFT
# eligible, since a corrected symbol format is a real, observed fix for those.
_HEALING_SKIP_PATTERNS = re.compile(
    r"library is not installed|service unavailable from a restricted location|"
    r"max retries exceeded|read timed out|\[winerror|forbidden by its",
    re.IGNORECASE,
)


_RISK_DESCRIPTIONS = {
    "delete_file":             "Permanently DELETE a file from your workspace",
    "execute_shell_command":   "Run an OS shell command on your machine",
    "send_gmail_message":      "Send an email from your Gmail account",
    "send_gmail_html_message": "Send an HTML email from your Gmail account",
    "reply_to_email":          "Send a reply to an email thread",
    "trash_email":             "Move an email to Trash in your Gmail",
    "git_confirm_push":        "Commit and PUSH code to the remote repository",
    "vision_act":              "Autonomously control your screen (click, type, scroll)",
}

class CielCore:
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

        log.system("CielCore initialized with Modular Brain-Middleware-Worker architecture")

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

        self._load_chat_memory()
        self._check_bootup_cleanse()

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
        "save_fact", "delete_fact", "get_fact",
        "take_screenshot", "get_file_info", "open_application",
        "get_crypto_stats", "vision_describe",
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

    def _recent_turns_block(self) -> str:
        """The last few turns of THIS conversation, or "" when there is nothing yet.

        Deliberately NOT sent to the Router (see `_router_persona`'s reasoning applied
        here too): the 2026-07 decision to keep chat_history out of routing exists
        because it let an old unresolved request bleed into an unrelated new one. This
        block only ever reaches the RESPONSE path (`execute_chat`, tool-result
        formatting), where "what did we just say" is exactly what is missing — never
        "which tool should run".
        """
        try:
            if not CONTEXT_RECENT_TURNS_ENABLED:
                return ""
            # `process()` already appended the CURRENT user message before this runs, and
            # it is shown separately as "User's request: …" — so it is excluded here to
            # avoid sending it twice.
            history = self.chat_history.messages[:-1] if self.chat_history.messages else []
            msgs = history[-(self._RECENT_TURNS_MAX * 2):]
            if not msgs:
                return ""
            lines = []
            for m in msgs:
                role = "Master" if m.type == "human" else "Ciel"
                text = (m.content or "").strip().replace("\n", " ")
                if len(text) > 240:
                    text = text[:240] + "…"
                lines.append(f"{role}: {text}")
            return "\n".join(lines)
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
        recent_ctx.add("recent_turns", self._recent_turns_block(), P_HELPFUL)
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
            f"{recent_section}"
            f"User's request: {task}"
        )
        self._log_thought("WORKER", "chat_task", persona_task)
        response = self.worker.generate(persona_task)
        self._log_thought("WORKER", "chat_response", response)
        return response

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
            # No callback set (e.g. forgot to wire up) — auto-proceed with warning
            self._log_thought("SAFETY", "confirm_auto_approved",
                              "No confirm_callback set — auto-approving.")
            return True

        try:
            approved = self.confirm_callback(tool_name, preview, tool_args)
        except Exception as e:
            self._log_thought("SAFETY", "confirm_error", str(e))
            approved = False

        tag = "confirm_approved" if approved else "confirm_denied"
        self._log_thought("SAFETY", tag, tool_name)
        return approved

    # Outbound tools whose second execution in one turn is a duplicate delivered to a
    # real person, not a retry. Keyed by RECIPIENT, not by the full argument signature:
    # the whole problem is that two mechanisms compose *different* subjects and bodies
    # for the same intended message, so a signature over all args would never match.
    _OUTBOUND_KEYS = {
        "send_gmail_message": "to",
        "send_gmail_html_message": "to",
        "reply_to_email": "message_id",
        "send_telegram": None,          # single destination; the tool itself is the key
    }

    def _outbound_key(self, tool_name: str, tool_args: dict):
        """Identity of the message this call would deliver, or None if not outbound."""
        if tool_name not in self._OUTBOUND_KEYS:
            return None
        arg = self._OUTBOUND_KEYS[tool_name]
        if arg is None:
            return tool_name
        target = str((tool_args or {}).get(arg) or "").strip().lower()
        return f"{tool_name}:{target}" if target else None

    def execute_tool(self, tool_name: str, tool_args: dict, response_hint: str = "", user_input: str = "",
                     _raw_out: list = None) -> str:
        """Execute a Ciel tool and format the result.

        `_raw_out`, if given a list, gets the pre-Worker-formatting result_text
        appended (structured tool output — the JSON search_gmail/stealth_search/etc.
        actually returned) alongside the normal human-facing return value. Internal
        plumbing for `_run_steps`'s {prev}/{step_N} chaining — see its call site.
        """
        log.tool(f"Executing: {tool_name}({tool_args})")

        if tool_name not in self._tool_map:
            log.error(f"Tool not found: {tool_name}")
            return f"[TOOL_ERROR] Tool '{tool_name}' not found."

        # OUTBOUND IDEMPOTENCE — one delivery per recipient per turn.
        #
        # Two independent mechanisms both complete a plan that is missing its send step:
        # the deterministic workflow safeguard in execute_multi_tool (which appends one),
        # and the Tier-1 loop (which re-plans one when the Brain sets `needs_followup`).
        # Neither knows about the other, so a request like "gửi mail cho X báo cáo Y"
        # delivered the SAME report twice, with two different subjects. Reproduced live.
        #
        # The existing comment at the loop's call site — "extra rounds … can never
        # double-send" — is true only for a send that was in the ORIGINAL plan, which is
        # separated out and run once after synthesis. It never covered a send the loop
        # invents. Guarding here, at the one choke point every path goes through, closes
        # that and any future path too.
        #
        # Checked BEFORE the safety gate on purpose: asking the Master to approve a send
        # that is about to be suppressed is worse than not asking.
        # First delivery wins. That is also the better one structurally: the loop runs
        # before synthesis, so the Brain's properly-subjected message goes out and the
        # generic fallback ("Báo cáo từ Ciel") is the one dropped.
        out_key = self._outbound_key(tool_name, tool_args)
        if out_key:
            with self._log_lock:
                already = out_key in self._sent_this_turn
            if already:
                self._log_thought(
                    "SAFETY", "duplicate_send_suppressed",
                    f"{tool_name} to the same recipient already succeeded in this turn "
                    f"({out_key}) — suppressed to avoid delivering the message twice.")
                return (f"[SKIPPED] Đã gửi tới người nhận này trong yêu cầu hiện tại rồi, "
                        f"Master. Bỏ qua lần gửi thứ hai để tránh trùng.")

        # STALE-YEAR FIX for web search: the Brain is trained on older data and often
        # injects a past year into "latest news" queries (observed: user asked for the
        # latest AI news in 2026, Brain searched "...breakthroughs 2024" → stale results).
        # If the query carries a past year the user never mentioned, bump it to the
        # current year so "latest" actually means now.
        if tool_name in ("stealth_search", "smart_scrape") and isinstance(tool_args.get("query"), str):
            cur_year = datetime.now().year
            def _bump_year(m):
                y = m.group(0)
                return str(cur_year) if int(y) < cur_year and y not in (user_input or "") else y
            new_q = re.sub(r"\b(20[0-3]\d)\b", _bump_year, tool_args["query"])
            if new_q != tool_args["query"]:
                self._log_thought("TOOL", "query_year_fixed",
                                  f"stale year in search query bumped to {cur_year}: {tool_args['query']!r} -> {new_q!r}")
                tool_args = dict(tool_args)
                tool_args["query"] = new_q

        # DANGEROUS CODE GATE: write_file/append_file can save arbitrary Worker-generated
        # code straight to disk with no confirmation (unlike pre-declared _HIGH_RISK_TOOLS).
        # Catch real destructive commands (drive format, mkfs, fork bombs...) here so
        # writing them out still requires the Master's explicit Y/N, same as any other
        # high-risk action. Fail-closed: on denial, nothing is written.
        if tool_name in ("write_file", "append_file") and isinstance(tool_args.get("content"), str):
            matched = _find_dangerous_code_patterns(tool_args["content"])
            if matched:
                disable_gate = os.getenv("DISABLE_SAFETY_GATE", "false").lower() in ("true", "1", "yes")
                if not disable_gate:
                    risk = f"Write code containing destructive pattern(s): {', '.join(matched)}"
                    if not self._request_confirmation(tool_name, tool_args, risk_override=risk):
                        return f"[CANCELLED] Master denied writing potentially destructive code to '{tool_args.get('filename', '?')}'. No action was taken."

            # HARD BLOCK (symmetric to the email placeholder block below): never let an
            # unresolved multi_tool synthesis placeholder land on disk as the "report" the
            # user asked for. execute_multi_tool's deferred-write detection normally holds
            # this kind of content back until after synthesis — this is the last-resort net
            # for the case where that classification misses a paraphrased marker (or a
            # placeholder reaches write_file/append_file via any other path), so a hollow
            # file is blocked here instead of silently written.
            if _has_unsynthesized_placeholder(tool_args["content"]):
                self._log_thought("SAFETY", "write_placeholder_blocked",
                                  f"{tool_name}: content for '{tool_args.get('filename', '?')}' still "
                                  f"contains an unsynthesized placeholder — write blocked.")
                return (f"Lỗi: nội dung ghi vào file chưa được tổng hợp (vẫn còn placeholder) — đã CHẶN ghi. "
                        f"Cần thu thập dữ liệu thật và tổng hợp nội dung trước khi ghi file.")

        # OUTBOUND EMAIL SANITIZATION (single choke-point): every email-sending path
        # — multi_tool synthesis, direct Brain send, content-filter fallback, replies,
        # drafts — funnels through here, so cleaning the body once covers them all.
        body_key = self._EMAIL_BODY_ARGS.get(tool_name)
        if body_key and isinstance(tool_args.get(body_key), str):
            # HARD BLOCK: never let an unsynthesized plan placeholder go out as a real
            # email. This marker is only meant to be replaced by execute_multi_tool's
            # synthesis step; reaching here means the plan skipped synthesis. Matched via
            # _has_unsynthesized_placeholder() (SYNTHES* root) rather than one exact
            # literal, so a Brain-paraphrased placeholder is still caught.
            if _has_unsynthesized_placeholder(tool_args[body_key]):
                self._log_thought("SAFETY", "email_placeholder_blocked",
                                  f"{tool_name}: body still contains an unsynthesized placeholder — send blocked.")
                return (f"Lỗi: nội dung email chưa được tổng hợp (vẫn còn placeholder) — đã CHẶN gửi. "
                        f"Cần thu thập dữ liệu thật và tổng hợp nội dung trước khi gửi.")

            cleaned = self._sanitize_outbound_email(tool_args[body_key])
            if cleaned != tool_args[body_key]:
                tool_args = dict(tool_args)
                tool_args[body_key] = cleaned
                self._log_thought("TOOL", "email_sanitized", f"{tool_name}: stripped internal/meta content from '{body_key}'.")

            # THIRD TIER — Middleware semantic review/finalize (relevance, consistency,
            # plausible grounding). Runs BEFORE the safety-gate preview so the Master's
            # Y/N prompt reflects the final body. No-op if MIDDLEWARE_ENABLED=false.
            reviewed = self._middleware_review(user_input, tool_name, tool_args[body_key])
            if reviewed != tool_args[body_key]:
                tool_args = dict(tool_args)
                tool_args[body_key] = reviewed

        # SAFETY GATE (Tier 3): AUTO / ASK / DENY — see core/permissions.py.
        # NOTE: SAFETY_OPEN governs Brain LLM content-filtering, a separate concern —
        # it must NOT influence the destructive-tool confirmation gate.
        decision, why = self.permissions.decide(tool_name, tool_args, attended=not self.unattended)
        if decision == Decision.DENY:
            self._log_thought("SAFETY", "denied", f"{tool_name}: {why}")
            return (f"[CANCELLED] {tool_name} is on the deny list and will not be run, Master. "
                    f"Remove it from CIEL_DENY_TOOLS if that was not intended.")
        if decision == Decision.DEFER:
            # TIER 6 — nobody is at the keyboard, so there is no one to say yes. Silence
            # must not become consent: the action is recorded and raised at the next
            # interaction (see the `deferred_approval` trigger) rather than performed.
            self.deferred.add(tool_name, tool_args, reason=why, source="unattended run")
            self._log_thought("SAFETY", "deferred", f"{tool_name}: {why}")
            return (f"[CANCELLED] {tool_name} needs your approval and nobody was at the "
                    f"keyboard, Master. It has been recorded for you to confirm.")
        if decision == Decision.ASK:
            if not self._request_confirmation(tool_name, tool_args):
                return f"[CANCELLED] Master denied execution of {tool_name}. No action was taken."
        elif why not in ("read-only", "not classified as risky"):
            # Only worth logging when a risky tool was let through for a REASON (a session
            # grant, plan approval, or an open gate) — that is the audit-relevant case.
            self._log_thought("SAFETY", "auto_allowed", f"{tool_name}: {why}")

        # send_gmail_message transmits its body as text/html (langchain), so raw '\n'
        # collapse into a wall of text on the recipient side. Render the clean plain
        # body into simple HTML AFTER the confirmation preview (which stays plain/readable)
        # so the delivered email preserves paragraphs and line breaks.
        if tool_name == "send_gmail_message" and isinstance(tool_args.get("message"), str):
            tool_args = dict(tool_args)
            tool_args["message"] = self._plaintext_to_html(tool_args["message"])

        result = self.tool_manager.execute_tool(tool_name, tool_args)
        result_text = self.tool_manager.format_tool_result(result)
        # Ground truth is the dict's own `success` flag, not a text guess. Bug found
        # live: tool_manager.format_tool_result() renders ANY failure as "[{code}] msg"
        # where `code` is whatever the skill author chose (VISION_LOOP_ERROR,
        # TOOL_NOT_FOUND, INVALID_ARGS, ...) — but this used to re-derive "is this an
        # error?" by checking only for the literal "EXECUTION_ERROR" or a mixed-case
        # "Error" substring. Any other code (all-caps, like every real example above)
        # silently read as a SUCCESS: no healing attempt, no friendly rephrase, the
        # raw "[VISION_LOOP_ERROR] Vision loop crashed: GEMINI_API_KEY not found in
        # .env" went out as if it were the answer — and task_state recorded the step
        # as "done". tool_manager.execute_tool() always returns this normalized dict
        # (see its docstring: "standardized structured result"), so `success` is
        # always present and reliable here.
        is_error = isinstance(result, dict) and result.get("success") is False

        # SELF-HEALING HOOK (UP TO 3 ATTEMPTS)
        max_attempts = 3
        attempt = 1
        current_args = tool_args

        if (
            tool_name != "run_python_script"
            and is_error
            and _HEALING_SKIP_PATTERNS.search(result_text[:300])
        ):
            self._log_thought("HEALING", "skipped_unfixable",
                              f"{tool_name}: error matches a known-unfixable pattern (missing dependency / "
                              f"network / geo-restriction) — no tool_args correction could fix this, skipping retries.")

        while (
            attempt <= max_attempts
            and is_error
            and not (tool_name != "run_python_script" and _HEALING_SKIP_PATTERNS.search(result_text[:300]))
        ):
            self._log_thought("HEALING", f"attempt_{attempt}", f"Starting heal attempt {attempt}/{max_attempts}")
            
            previous_code = ""
            if tool_name == "run_python_script" and "filename" in current_args:
                try:
                    from skills.internal.system_ops import _is_safe_path
                    safe_path = _is_safe_path(current_args["filename"])
                    if os.path.exists(safe_path):
                        with open(safe_path, "r", encoding="utf-8") as f:
                            previous_code = f.read()
                except Exception:
                    pass

            success, action, data = self.recovery.heal_tool_error(tool_name, current_args, result_text, attempt, previous_code)

            if not success:
                result_text += f"\n\n[Self-Healing Failed] {data.get('error', 'Unknown error')}"
                break  # is_error stays True — nothing here fixed it

            if action == "code_fix":
                try:
                    from skills.internal.system_ops import _is_safe_path
                    safe_path = _is_safe_path(data["filename"])

                    # Validate syntax before saving!
                    syntax_valid, syntax_error = self.recovery.check_syntax(data["code"])
                    if not syntax_valid:
                        self._log_thought("HEALING", "syntax_error", syntax_error)
                        result_text = f"[Syntax Error Validation Failed]\n{syntax_error}"
                        is_error = True
                        attempt += 1
                        continue

                    with open(safe_path, "w", encoding="utf-8") as f:
                        f.write(data["code"])
                    self._log_thought("HEALING", "apply_fix", f"Code updated for {data['filename']}. Re-running script.")

                    # Retry tool
                    result = self.tool_manager.execute_tool(tool_name, current_args)
                    retry_text = self.tool_manager.format_tool_result(result)
                    is_error = isinstance(result, dict) and result.get("success") is False
                    result_text = f"[Self-Healing Activated] Analyzed code error, fixed it, and re-ran.\n\nNew Output:\n{retry_text}"

                except Exception as e:
                    result_text = f"[Self-Healing Error] {e}"
                    is_error = True

            elif action == "retry_tool":
                self._log_thought("HEALING", "retry_tool_args", str(data))
                current_args = data  # Update arguments for the next attempt if it fails
                result = self.tool_manager.execute_tool(tool_name, current_args)
                retry_text = self.tool_manager.format_tool_result(result)
                is_error = isinstance(result, dict) and result.get("success") is False
                result_text = f"[Self-Healing Activated] Analyzed parameter error, corrected args, and re-ran.\n\nNew Output:\n{retry_text}"

            attempt += 1

        if is_error:
            log.error(f"Tool {tool_name} failed after {attempt-1} self-healing attempts.")
            self._log_thought("TOOL", "error", f"{tool_name}: {result_text}")
            # STRUCTURED ERROR: Rephrase raw error for the user
            try:
                friendly = self.worker.generate(
                    f"Rephrase this error for the user in one plain, helpful sentence. "
                    f"Do NOT include technical stack traces.\n\nError: {result_text[:500]}"
                )
                self._log_thought("WORKER", "error_rephrase", friendly)
                return f"Sorry, {friendly}"
            except Exception:
                return f"[TOOL_ERROR] {tool_name}: {result_text}"

        # Recorded only on SUCCESS — this branch is past the error return above. A send
        # that failed must stay retryable; marking it delivered would turn one provider
        # hiccup into a message that never goes out and never reports why.
        if out_key:
            with self._log_lock:
                self._sent_this_turn.add(out_key)

        self._log_thought("TOOL", "result", f"{tool_name}: {result_text}")

        # Chain refs ({prev}/{step_N}) need the STRUCTURED result (e.g. search_gmail's
        # real "id" field) — capture it here, before any Worker humanizing below turns
        # it into a prose sentence a later step's tool_args cannot use as an argument.
        if _raw_out is not None:
            _raw_out.append(result_text)

        # Remember a preview tool's follow-up so a later bare "yes" can execute it.
        # The tool declares this itself via result["confirm"] (see skills/_result.py
        # make_result) — core needs no per-tool registration, so any skill can opt in.
        confirm_spec = result.get("confirm") if isinstance(result, dict) else None
        if confirm_spec and not result_text.startswith(("[TOOL_ERROR", "[CANCELLED")):
            self._set_pending_action(confirm_spec["tool"], confirm_spec.get("args", {}), source=tool_name)

        if tool_name in {"get_fact", "save_fact", "delete_fact"}:
            return self._format_fact_result(tool_name, result_text)

        if tool_name not in self._TOOLS_NEEDING_FORMAT:
            return result_text

        # GMAIL: Smart truncation — keep all emails visible, trim each body
        if "gmail" in tool_name.lower():
            clean_text = self._compact_email_result(result_text)
        elif tool_name == "smart_scrape":
            # Let the Worker read up to 40,000 characters of the scraped website
            clean_text = result_text[:40000]
        elif tool_name == "stealth_search":
            # Search hits now carry a Published date + Source per result, so a full page
            # of results no longer fits in the generic 2,000-char budget — truncating
            # here silently dropped the last hits before the Worker ever saw them.
            clean_text = result_text[:8000]
        else:
            clean_text = result_text[:2000]

        # Information-retrieval tools return MANY distinct facts; "absolute shortest"
        # collapsed them into content-free generalities and silently dropped items
        # (observed: a search covering an ongoing war summarized as "an outlook focusing
        # on GDP, inflation and risks"). Those tools get a completeness rule instead;
        # every other tool keeps the terse formatting (also keeps latency down, since
        # generation time scales with output length).
        _RETRIEVAL_TOOLS = {"stealth_search", "smart_scrape", "read_document"}
        if tool_name in _RETRIEVAL_TOOLS:
            lead_line = ("Turn this tool output into a clear, information-dense answer.\n")
            rule_one = (
                "1. COMPLETENESS OVER BREVITY: cover EVERY distinct item/finding in the raw "
                "result — do not merge them into one vague sentence and do not silently drop any. "
                "Keep the concrete specifics: dates, numbers, names, places, sources. "
                "One short bullet per item; no conversational filler.\n"
                "1b. DATES: state each item's Published date. If an item says Published: UNKNOWN, "
                "say the date is unknown — NEVER guess or infer one. If an item is clearly older "
                "than the user's timeframe, say how old it is instead of implying it is fresh.\n"
                "1c. If an item has no specific content (a section/landing page), omit it rather "
                "than writing a hollow line about it.\n")
        else:
            lead_line = "Format this tool output into the absolute shortest, clearest response possible.\n"
            rule_one = "1. Be extremely concise. Give just the requested data. No conversational filler.\n"

        _rc = ContextAssembler()
        _rc.add("recent_turns", self._recent_turns_block(), P_HELPFUL)
        _recent_block, _recent_rep = _rc.render(budget_tokens=CONTEXT_RECENT_TURNS_BUDGET)
        if _recent_rep.dropped:
            self._log_thought("CONTEXT", "recent_turns_dropped", _recent_rep.summary())
        _recent_section = (f"[RECENT CONVERSATION — context only]:\n{_recent_block}\n\n"
                           if _recent_block else "")

        format_task = (
            f"{self.ciel_persona}\n\n"
            f"{self._profile_block()}"
            f"{lead_line}"
            f"{_recent_section}"
            f"User's request: {user_input}\n"
            f"Tool: {tool_name}\n"
            f"Raw result:\n{clean_text}\n\n"
            f"Hint: {response_hint}\n"
            f"RULES:\n"
            f"{rule_one}"
            f"2. Address the Master by name only where it falls naturally in a sentence. Do NOT "
            f"open every response with 'Master,' — mechanical repetition of it is what made "
            f"replies read like a form letter. A bare answer is fine when the answer is one line.\n"
            f"3. ANTI-HALLUCINATION: ONLY use facts present in the Raw result above. "
            f"If the raw result contains an error, 'file not found', 'N/A', or is empty, "
            f"report the error honestly to Master. Say 'the data is unavailable' or 'the tool returned an error'. "
            f"NEVER invent, fabricate, or simulate data that is not in the raw result. "
            f"NEVER generate fake file contents, fake statistics, or fake execution output.\n"
            f"4. NO PROCESS NARRATION: Do NOT write status lines like 'Retrieving...', "
            f"'Scanning...', 'Initiating...', 'Fetching...'. Report ONLY the final data/facts."
        )
        self._log_thought("WORKER", "format_task", format_task)
        formatted = self.worker.generate(format_task)
        self._log_thought("WORKER", "format_response", formatted)
        return formatted

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
        """Parse email JSON, strip HTML bodies, truncate each to fit the result size."""
        try:
            emails = json.loads(text)
            if not isinstance(emails, list):
                return self._strip_html(text)[:2000]

            per_body_cap = self._EMAIL_BODY_CAP_SINGLE if len(emails) <= 1 else self._EMAIL_BODY_CAP_DIGEST
            compact = []
            for em in emails:
                body_raw = em.get("body", "")
                body_clean = self._strip_html(body_raw)[:per_body_cap]
                compact.append({
                    "sender": em.get("sender", ""),
                    "subject": em.get("subject", ""),
                    "body": body_clean,
                })
            return json.dumps(compact, ensure_ascii=False, indent=1)
        except (json.JSONDecodeError, TypeError):
            return self._strip_html(text)[:2000]

    def _format_fact_result(self, tool_name: str, result_text: str) -> str:
        """Convert memory vault tool output into clean user-facing text."""
        if tool_name == "get_fact":
            match = re.match(r"Fact '([^']+)':\s*(.*)", result_text, flags=re.DOTALL)
            if match:
                key = match.group(1).replace("_", " ")
                value = match.group(2).strip()
                return f"Master, your {key} is {value}."
            match = re.match(r"No fact found for key '([^']+)'", result_text)
            if match:
                key = match.group(1).replace("_", " ")
                return f"Master, I do not have a saved {key}."
            return result_text

        if tool_name == "save_fact":
            match = re.match(r"Fact saved successfully:\s*([^.]+)\.", result_text)
            if match:
                key = match.group(1).replace("_", " ")
                return f"Master, I saved your {key}."
            return result_text

        if tool_name == "delete_fact":
            match = re.match(r"Fact deleted successfully:\s*([^.]+)\.", result_text)
            if match:
                key = match.group(1).replace("_", " ")
                return f"Master, I deleted your {key}."
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
        """Worker generates code and writes it to disk."""
        from agent_system.tools.buffer_writer import buffer_writer

        buffer_writer.clear()
        # Add anti-hallucination guardrail for code generation
        code_guardrail = (
            "\n\nCRITICAL RULES FOR CODE GENERATION:\n"
            "1. Output ONLY production-ready code. Do NOT include dummy/test data, "
            "demonstration values, or example scaffolding unless explicitly asked.\n"
            "2. If input files may not exist, add proper error handling — do NOT fabricate their contents.\n"
            "3. Do NOT simulate script execution output or invent fake results.\n"
            "4. Do NOT add a dummy 'if __name__ == \"__main__\"' block with fake test data or "
            "file creation for demonstration. The main block should only call the real function "
            "with the real parameters from the task.\n"
            "5. Keep code concise: use brief inline comments only where logic is non-obvious. "
            "Do NOT write multi-line docstrings for every function. Do NOT add verbose "
            "explanatory comments on every line."
        )
        augmented_task = task + code_guardrail
        self._log_thought("WORKER", "code_task", task)
        code = self.worker.generate(augmented_task)
        self._log_thought("WORKER", "code_response", code)

        # Same dangerous-code gate as write_file/append_file (see execute_tool) — this
        # path writes to disk independently via buffer_writer, so it needs its own check.
        matched = _find_dangerous_code_patterns(code)
        if matched:
            disable_gate = os.getenv("DISABLE_SAFETY_GATE", "false").lower() in ("true", "1", "yes")
            if not disable_gate:
                risk = f"Write generated code containing destructive pattern(s): {', '.join(matched)}"
                if not self._request_confirmation("execute_code", {"filename": filename}, risk_override=risk):
                    return f"[CANCELLED] Master denied writing potentially destructive generated code to '{filename}'. No action was taken."

        buffer_writer.append(code)
        result = buffer_writer.flush(filename)
        log.tool(result)
        return f"Code written to {filename}"

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

        if not asked or self.confirm_callback is None:
            return ""       # nothing to approve, or no UI to ask through (per-call gate still applies)

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
            approved = self.confirm_callback("plan", preview, {"steps": asked})
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

    def _run_steps(self, steps: list, response_hint: str, user_input: str,
                   results: list, step_outputs: list, records: list, label: str = "") -> bool:
        """Execute `steps` in order, running provably-independent ones concurrently.

        Returns False if execution should stop early (a step was cancelled).

        Concurrency is opt-in and conservative (see core/parallel.py): only tools
        declared read-only, carrying no {step_N} reference and not high-risk, and only
        with each other. Everything else keeps running exactly as it did sequentially.
        Results are appended in the ORIGINAL order regardless of completion order, so
        {prev} / {step_N} keep pointing at what they always pointed at.
        """
        batches = plan_batches(steps, self._parallel_safe, self._HIGH_RISK_TOOLS,
                               max_workers=AGENT_PARALLEL_MAX_WORKERS) \
            if AGENT_PARALLEL_ENABLED else [[s] for s in (steps or [])]

        for batch in batches:
            prepared = []
            for t in batch:
                name = (t.get("tool_name") or "").strip()
                args = self._resolve_step_refs(t.get("tool_args") or {}, step_outputs)
                if args != (t.get("tool_args") or {}):
                    self._log_thought("TOOL", "step_ref_resolved",
                                      f"{name}: injected prior step output into args.")
                prepared.append((name, args))

            # Captured HERE, in the submitting thread, because `unattended` is
            # thread-local: a worker thread starts with the default (attended), so
            # without carrying it across, a background plan's risky steps would be
            # confirmed-and-run instead of deferred. Fail-open on a safety control.
            unattended_here = self.unattended

            # TIER 5 — the cancellation point. Between batches, so whatever already ran
            # completed cleanly and whatever has not simply never starts.
            if self._abort_if_cancelled(f"before step batch ({len(prepared)} step(s))"):
                results.append("[CANCELLED] Master dừng yêu cầu này. "
                               "Các bước đã chạy xong vẫn giữ nguyên; phần còn lại không chạy.")
                return False

            def _run(pair):
                name, args = pair
                self.unattended = unattended_here
                if name not in self._tool_map:
                    err = f"[TOOL_ERROR] {name} not found."
                    return err, err
                raw_box = []
                formatted = self.execute_tool(name, args, response_hint=response_hint,
                                              user_input=user_input, _raw_out=raw_box)
                # Bug found live: a plan chaining search_gmail -> get_gmail_message via
                # {prev} got the WORKER'S HUMAN-READABLE SUMMARY ("Email từ Khang Trần,
                # tiêu đề ...") injected as message_id, instead of the real Gmail id —
                # a 400 "Invalid id value", then 3 self-healing attempts hallucinating
                # guesses ("XAUUSD", "XAU_USD") that were never going to work, because
                # the real id was sitting right there in raw_box the whole time.
                # step_outputs (below) gets the raw structured text; everything
                # human-facing keeps using `formatted` exactly as before.
                return formatted, (raw_box[0] if raw_box else formatted)

            if len(prepared) > 1:
                names = ", ".join(n for n, _ in prepared)
                log.tool(f"{label}Running {len(prepared)} independent steps in parallel: {names}")
                self._log_thought("TOOL", "parallel_batch",
                                  f"{len(prepared)} independent steps concurrently: {names}")
                with ThreadPoolExecutor(max_workers=len(prepared)) as pool:
                    outs = list(pool.map(_run, prepared))
            else:
                name, args = prepared[0]
                log.tool(f"{label}Executing step: {name}({args})")
                outs = [_run(prepared[0])]

            for (name, args), (res, raw) in zip(prepared, outs):
                self._log_thought("TOOL", f"result_{name}", res)
                step_outputs.append(raw)
                results.append(f"--- Output from {name} ---\n{res}")
                records.append(StepRecord(name, args, res))
                self.tasks.record_step(name, res)
                if res.startswith("[CANCELLED]"):
                    return False
        return True

    def _continue_until_done(self, user_input: str, response_hint: str, records: list,
                             results: list, step_outputs: list, model_requested: bool = False):
        """TIER-1 AGENT LOOP: observe → re-plan → act, in place.

        Appends any follow-up steps' output to `records`/`results`/`step_outputs`, so
        the caller synthesizes over the full picture without knowing a loop happened.

        Three properties matter more than what the loop achieves, and all three are
        guaranteed by code rather than by the model behaving well:

          * BOUNDED — rounds, planner calls and wall-clock are all capped up front
            (see LoopBudget). A model that keeps insisting there is more to do simply
            runs out of budget.
          * MONOTONIC — a follow-up that only re-proposes work already done is dropped
            by novel_steps(), so a confused planner terminates the loop instead of
            ping-ponging forever.
          * FAIL-OPEN — every failure path here returns quietly, leaving the first
            round's results untouched. This can improve an answer; it can never leave
            the pipeline worse off than before the loop existed.

        Re-planning reuses Router.route() and the ordinary plan schema on purpose:
        no second output format the model has to learn, and therefore no new way for a
        weaker model to fail. Cost is one planner call per round, and only when a
        deterministic signal already said the plan looked incomplete.
        """
        if not AGENT_LOOP_ENABLED:
            return

        budget = LoopBudget(max_rounds=AGENT_LOOP_MAX_ROUNDS,
                            max_replans=AGENT_LOOP_MAX_ROUNDS,
                            max_seconds=AGENT_LOOP_MAX_SECONDS)
        while True:
            # TIER 5 — checked before the planner call, not just before the steps: an
            # extra round that the Master already cancelled costs a full Brain call and
            # produces work nobody wants.
            if self._abort_if_cancelled("agent loop"):
                return
            verdict = ContinuationPolicy.assess(user_input, records, budget, model_requested)
            if not verdict.should_continue:
                # Only worth a log line once a loop was actually plausible; otherwise
                # every ordinary request would spam "plan looks complete".
                if budget.rounds_used:
                    self._log_thought("LOOP", "done", verdict.reason)
                return
            self._log_thought("LOOP", "continue", verdict.reason)

            try:
                followup = (f"{user_input}\n\n"
                            f"{build_observation_block(user_input, records)}")
                budget.replans_used += 1
                decision = self.router.route(followup, self._tool_list_str, self.chat_history)
            except Exception as e:
                self._log_thought("LOOP", "replan_failed",
                                  f"{type(e).__name__}: {str(e)[:160]} — keeping results so far.")
                return

            action = (decision or {}).get("action")
            if action not in ("tool", "multi_tool"):
                self._log_thought("LOOP", "planner_finished",
                                  f"planner returned action={action} — nothing further to run.")
                return

            proposed = decision.get("tools") or []
            if not proposed and decision.get("tool_name"):
                proposed = [{"tool_name": decision["tool_name"],
                             "tool_args": decision.get("tool_args") or {}}]

            fresh = ContinuationPolicy.novel_steps(proposed, records)
            if not fresh:
                self._log_thought("LOOP", "no_progress",
                                  "follow-up proposed only already-executed steps — stopping.")
                return

            budget.rounds_used += 1
            model_requested = bool(decision.get("needs_followup"))

            if len(fresh) > budget.max_steps_per_round:
                self._log_thought("LOOP", "round_trimmed",
                                  f"planner proposed {len(fresh)} steps — running the first "
                                  f"{budget.max_steps_per_round} to stay inside the budget.")
                fresh = fresh[:budget.max_steps_per_round]

            # Time is checked BEFORE each batch, not only between rounds: a round of
            # slow calls must be able to stop partway instead of overrunning the
            # ceiling wholesale (observed: 566s inside a single round).
            for batch in plan_batches(fresh, self._parallel_safe, self._HIGH_RISK_TOOLS,
                                      max_workers=AGENT_PARALLEL_MAX_WORKERS) \
                    if AGENT_PARALLEL_ENABLED else [[s] for s in fresh]:
                if budget.out_of_time():
                    self._log_thought("LOOP", "out_of_time",
                                      f"loop exceeded {budget.max_seconds:.0f}s — stopping mid-round "
                                      f"with the results gathered so far.")
                    return
                if not self._run_steps(batch, response_hint, user_input, results,
                                       step_outputs, records,
                                       label=f"[loop {budget.rounds_used}/{budget.max_rounds}] "):
                    self._log_thought("LOOP", "cancelled", "a follow-up step was declined — stopping.")
                    return
                # A follow-up step that staged a confirmation hands control back to the
                # Master. Continuing would plan on top of an action that has not happened.
                if self._pending_action:
                    self._log_thought("LOOP", "awaiting_confirmation",
                                      f"staged {self._pending_action['tool']} — stopping for the Master.")
                    return

    def execute_multi_tool(self, tools: list, response_hint: str, user_input: str = "",
                           model_requested: bool = False) -> str:
        """Execute multiple tools sequentially and synthesize the result.

        Special handling for send_gmail_message: execute other tools first, synthesize
        the final message body, then execute send with the synthesized body so the
        actual email contains the real content (not a placeholder from the initial plan).

        `model_requested` is the plan's optional `needs_followup` hint, forwarded to the
        Tier-1 loop. It can only ADD a reason to look again — never override a budget.
        """
        # Separate the send step if present (usually the last step for email requests).
        # Supports plain send_gmail_message and rich send_gmail_html_message.
        # Also separate a DEFERRED WRITE step: a write_file/append_file whose content
        # is a synthesis marker — it must run AFTER the report is synthesized so the
        # file receives the real report, not the placeholder.
        send_tool = None
        deferred_write = None
        other_tools = []
        for t in tools:
            name = t.get("tool_name")
            if name in ("send_gmail_message", "send_gmail_html_message"):
                send_tool = t
            elif name in ("write_file", "append_file") and _has_unsynthesized_placeholder(str(t.get("tool_args", {}).get("content", ""))):
                deferred_write = t
            else:
                other_tools.append(t)

        # Resolve a referential recipient ("gửi qua email đó đi") HERE, at the top,
        # not just before the send call below. Found live: correcting `to` only right
        # before sending was too late — the report body had ALREADY been synthesized
        # from the stale pre-correction context, so a real email to the CORRECTED
        # recipient (prokxcpro@gmail.com) carried the sentence "Đã gửi báo cáo giá vàng
        # ... đến kxctran@gmail.com" — a body that talks about a different address
        # entirely, sent to a real person. Fixing `to` without fixing what the body
        # says about `to` is not actually fixed.
        recipient_override_note = ""
        if send_tool:
            original_to = str(send_tool.get("tool_args", {}).get("to", "")).strip()
            corrected_args = self._resolve_referential_recipient(
                send_tool.get("tool_name", "send_gmail_message"),
                send_tool.get("tool_args", {}), user_input)
            if corrected_args is not send_tool.get("tool_args"):
                send_tool = dict(send_tool)
                send_tool["tool_args"] = corrected_args
                # The override fixes WHO the mail goes to, but the report-body
                # synthesis below still runs on the same `response_hint` the Router
                # wrote while confused about which recipient/topic "đó" meant — found
                # live producing a body that told prokxcpro@gmail.com "Đã gửi báo cáo
                # giá vàng ... đến kxctran@gmail.com". Fixing `to` alone is not a fix
                # if the BODY still talks about a different address. Ground the
                # synthesis prompt in the corrected fact instead of trusting it to
                # have followed the same reasoning.
                recipient_override_note = (
                    f"\n9. CONFIRMED RECIPIENT: this email's real recipient is "
                    f"{corrected_args.get('to')} — NOT {original_to} or any other "
                    f"address that may appear elsewhere in this prompt. Do not "
                    f"mention, address, or narrate the status of any email to a "
                    f"different address in the body.")

        results = []
        step_outputs = []   # raw result of each executed step, for {{prev}}/{{step_N}} refs
        records = []        # (tool, args, result) per step — the agent loop's observations

        # TIER 3 — review the WHOLE plan before running any of it, so the Master sees
        # what is about to happen instead of being stopped after step 2 of 4 with the
        # first two already carried out. Returns early on refusal: nothing has run yet.
        blocked = self._review_plan_permissions(tools)
        if blocked:
            return blocked

        # Execute non-send tools first. Dependent chains ({{prev}}/{{step_N}}) are
        # resolved per step inside _run_steps, which also batches provably-independent
        # steps to run concurrently.
        self._run_steps(other_tools, response_hint, user_input, results, step_outputs, records)

        # TIER-1 AGENT LOOP — observe what actually came back and, only when a
        # deterministic signal says the plan could not have been complete, plan again
        # with those results in view. Placed HERE on purpose: any send/deferred-write
        # step was separated out above and still runs once, after synthesis, so extra
        # rounds can enrich the data a report is built from but can never double-send.
        self._continue_until_done(user_input, response_hint, records, results, step_outputs,
                                  model_requested=model_requested)

        combined_results = "\n\n".join(results)

        format_task = f"""{self.ciel_persona}

{self._profile_block()}

Synthesize the following data from multiple tools into a cohesive report.
User's request: {user_input}
{combined_results}

Hint: {response_hint}
RULES:
1. Be concise. Deliver a unified report without conversational filler.
2. For chat or internal reports: address the Master by name only where it falls naturally in a sentence — never as a mandatory opener on every response. For email body, do NOT include ANY "Master" greeting or internal addressing — that is addressed to an outside recipient, and internal forms of address must never leak into it. Make it a clean professional email.
3. ONLY use facts present in the tool outputs above. NEVER invent data. NEVER copy any
   numbers, prices, or values from the persona/system-prompt EXAMPLES — those are
   illustrative placeholders. Every number in your output must come from a tool result in THIS run.
4. If any tool returned an error, 'file not found', or empty result, report that honestly. Do NOT fabricate fake data, fake file contents, or fake execution output.
5. NEVER disclose internal file paths (agent_output/, ciel_workspace/, etc.) in the final report or email body sent to external parties. Use only generic professional language such as 'the detailed evaluation has been prepared' or provide the content directly in the message. Do not reference storage locations.
6. ONLY claim that an email was sent (e.g. "Đã gửi", "email sent", "Message sent") if there is a successful send_gmail_message tool result with a Message Id in the outputs above. If there is no such result, do NOT narrate delivery status AT ALL — do not write "chưa gửi", "not sent yet", "chưa có kết quả gửi" or any equivalent. A send step in this plan may still be pending and will run AFTER you write this; the system appends the real outcome itself once it knows. Just write the report.
7. For any email send (market or other), synthesize a professional email body based on the user's exact request and the real data from tools. Make it clear, well-structured, polite and useful like a proper sent email (use Vietnamese if appropriate). Do not force any specific dashboard template or HTML structure unless the user explicitly requested visual/dashboard style. Use ONLY real data from this run's tool results. Never leave [brackets], meta tags, or invent numbers.
8. If this is an email send, your ENTIRE output IS the email body and will be sent verbatim. Output ONLY the email body — start directly with the subject/greeting. Do NOT include: chain-of-thought or "[COGNITION]"/"[MARKET_DATA]"-style tag prefixes; any statement that the email was/wasn't sent or any "Message Id"; any label like "Email body:", "Nội dung email:", "Lưu ý:"; any nested/duplicated copy of the email; any note about tools, file writes, or storage paths.{recipient_override_note}
"""
        self._log_thought("WORKER", "multi_tool_format_task", format_task)
        formatted = self.worker.generate(format_task)
        self._log_thought("WORKER", "multi_tool_format_response", formatted)

        # The pure synthesized report — used for the file write and the email body.
        # `formatted` accumulates [FILE]/[EMAIL] status notes for the Master's display
        # only; those notes must never end up inside the written file or sent email.
        report_body = formatted

        # Re-execute the deferred write step with the synthesized report, so the
        # file the user asked for actually receives real content. Clean it first:
        # strip [COGNITION]/tag prefixes and signature placeholders (keep internal
        # paths — harmless inside a local workspace file).
        if deferred_write:
            w_name = deferred_write.get("tool_name", "write_file")
            w_args = dict(deferred_write.get("tool_args", {}))
            w_args["content"] = self._sanitize_outbound_email(report_body, keep_paths=True)
            log.tool(f"Re-executing {w_name} with synthesized report content")
            w_res = self.execute_tool(w_name, w_args, response_hint=response_hint, user_input=user_input)
            self._log_thought("TOOL", f"result_{w_name}", w_res)
            results.append(f"--- Output from {w_name} ---\n{w_res}")
            formatted = formatted.rstrip() + f"\n\n[FILE] {w_res}"

        # Re-execute the send step with real content built after the data tools ran.
        if send_tool:
            send_name = send_tool.get("tool_name", "send_gmail_message")
            send_args = dict(send_tool.get("tool_args", {}))
            send_args = self._enforce_subject(send_args, user_input)
            send_args = self._resolve_referential_recipient(send_name, send_args, user_input)

            # The display copy `formatted` (which Master sees) keeps persona markers;
            # the outbound body is sanitized centrally in execute_tool, so just pass
            # the synthesized content through as-is here.
            if send_name == "send_gmail_html_message":
                # Use the synthesized professional content as HTML body (synthesis already made it clean/professional)
                # Only use special dashboard builder if explicitly planned with build_market_report_html earlier
                send_args["html_body"] = report_body
                send_args.pop("message", None)
            else:
                send_args["message"] = report_body

            log.tool(f"Re-executing {send_name} with real content")
            send_res = self.execute_tool(send_name, send_args, response_hint=response_hint, user_input=user_input)
            self._log_thought("TOOL", f"result_{send_name}", send_res)
            results.append(f"--- Output from {send_name} ---\n{send_res}")
            # Claim sent ONLY when a real Message Id is present (no loose 'sent' substring).
            if "Message Id" in send_res:
                # The body was synthesized BEFORE this send ran, so at that moment there
                # was no Message Id and the anti-fabrication rule correctly made the
                # Worker write "chưa gửi … chưa có kết quả gửi thực tế". That sentence is
                # now stale, and appending the truth underneath it left the Master reading
                # both — a denial and a confirmation of the same send. Observed live: the
                # mail went out (Message Id 19f9efdcfee2657d) while the reply opened with
                # "Chưa gửi email tới kxctran@gmail.com".
                #
                # Removed deterministically, and only here, where the real outcome is
                # already known from the tool result — never by asking the model to
                # remember not to mention it.
                formatted = self._strip_stale_send_status(formatted)
                formatted = formatted.rstrip() + "\n\n[EMAIL] Sent successfully (Message Id in tool log)."
            else:
                formatted = formatted.rstrip() + "\n\n[EMAIL] Report prepared but NOT confirmed sent (no Message Id returned)."

        return formatted

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
        """Brain evaluates tool result and tries alternative approach if unsatisfactory."""
        tried_tools = {tool_name}
        for attempt in range(max_attempts):
            evaluation = self._evaluate_result(user_input, tool_name, tool_args, result)
            if evaluation.get("satisfied", True):
                if attempt > 0:
                    log.system(f"Self-Correction satisfied after {attempt} correction(s)")
                return result

            reasoning = evaluation.get("reasoning", "Result was insufficient")
            new_action = evaluation.get("action", "chat")
            self._log_thought("BRAIN", "self_correction",
                f"Attempt {attempt+1}/{max_attempts}: {reasoning}\n"
                f"Previous: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})\n"
                f"Next: {new_action} → {evaluation.get('tool_name', evaluation.get('task', 'N/A'))}")
            log.brain(f"Self-Correction [{attempt+1}]: {reasoning[:80]}")

            if new_action == "tool":
                new_tool = evaluation.get("tool_name", "")
                new_args = evaluation.get("tool_args", {})
                new_hint = evaluation.get("response_hint", "")

                # Prevent infinite loop — don't retry same tool with same args
                if new_tool == tool_name and new_args == tool_args:
                    self._log_thought("BRAIN", "self_correction", "Aborted: same tool+args, would loop.")
                    return result

                cheaper = self._HEAVY_ESCALATION_TOOLS.get(new_tool)
                if cheaper and cheaper not in tried_tools:
                    self._log_thought(
                        "SAFETY", "escalation_deferred",
                        f"Self-correction proposed '{new_tool}' (screen control) but the cheaper "
                        f"'{cheaper}' was never tried in this chain — ending self-correction here "
                        f"instead of auto-escalating. '{new_tool}' only runs on an explicit request.")
                    return result

                new_result = self.execute_tool(new_tool, new_args, new_hint, user_input)
                tried_tools.add(new_tool)
                result = new_result  # HIDE ERROR: Only return the new successful result to the user
                # Update for next evaluation iteration
                tool_name = new_tool
                tool_args = new_args

            elif new_action == "chat":
                task = evaluation.get("task", user_input)
                # Inject the actual tool result so Worker doesn't hallucinate
                enriched_task = (
                    f"{task}\n\n"
                    f"[ACTUAL DATA from previous tool '{tool_name}']:\n"
                    f"{result[:3000]}"
                )
                chat_response = self.execute_chat(enriched_task)
                return chat_response  # HIDE ERROR: Only return the new chat response to the user
            else:
                return result

        return result

    # Inspection tools eligible for the memory-fallback below. All are also in
    # _SKIP_SELF_CORRECTION, which is precisely why they need this cheaper net:
    # their results come back verbatim with no "did this actually answer the
    # question?" evaluation at all.
    _MEMORY_FALLBACK_TOOLS = {"list_workspace", "read_file", "get_file_info"}

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

    def _evaluate_result(self, user_input: str, tool_name: str, tool_args: dict, result: str) -> dict:
        """Ask Brain to evaluate if a tool result satisfies the user's request."""
        # Ensure result is never empty (Gemini rejects empty content)
        safe_result = (result or "No output returned.").strip()
        if not safe_result:
            safe_result = "No output returned."

        # DETERMINISTIC FLOOR (runs BEFORE the LLM): an obviously-failed result
        # (error message, empty, "not found") must always trigger self-correction.
        # Doing this here — not after json.loads — means a malformed Brain response
        # (which lands in the except branch) can never rubber-stamp an error as satisfied.
        if self._is_failure_result(result):
            self._log_thought("BRAIN", "evaluate_override",
                              "Result matches a failure signal — forcing self-correction.")
            return {"satisfied": False,
                    "reasoning": "Result is an error, empty, or not-found response.",
                    "action": "chat",
                    "task": ("Honestly explain to the Master that the previous attempt "
                             "did not return usable data, and suggest a next step.")}

        eval_request = (
            f"User's request: {user_input}\n"
            f"Tool used: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})\n"
            f"Tool result:\n{safe_result[:3000]}\n\n"
            f"Available tools: {self._tool_list_str}\n\n"
            f"Does this result adequately answer the user's request? Respond with JSON only."
        )
        try:
            messages = [
                SystemMessage(content=SELF_CORRECTION_PROMPT),
                HumanMessage(content=eval_request)
            ]
            response = self.brain._router_llm.invoke(messages)
            # Gemini occasionally returns content as a list of parts instead of a plain string
            _eval_content = response.content
            if isinstance(_eval_content, list):
                _eval_content = "".join(c.text if hasattr(c, "text") else str(c) for c in _eval_content)
            raw = _eval_content.strip()
            self._log_thought("BRAIN", "evaluate_result", raw)

            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            if not raw:
                return {"satisfied": True}
            return json.loads(raw)
        except (json.JSONDecodeError, Exception) as e:
            self._log_thought("BRAIN", "evaluate_result_error", str(e))
            return {"satisfied": True}  # Fail-safe: assume satisfied if evaluation fails

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
        if isinstance(to_val, list):
            return [str(x).strip().lower() for x in to_val if str(x).strip()]
        return [str(to_val).strip().lower()] if str(to_val or "").strip() else []

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
        import html as _html
        if not text:
            return text
        # Already HTML? leave it (e.g. a dashboard body).
        if re.search(r"<(?:p|br|div|table|h[1-6]|ul|ol)\b", text, re.IGNORECASE):
            return text
        esc = _html.escape(text)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)  # markdown bold
        blocks = re.split(r"\n\s*\n", esc.strip())
        paras = []
        for b in blocks:
            b = b.strip().replace("\n", "<br>")
            if b:
                paras.append(f'<p style="margin:0 0 12px">{b}</p>')
        inner = "\n".join(paras)
        return (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
            f'line-height:1.55;color:#222">{inner}</div>'
        )

    def process(self, user_input: str) -> str:
        """Full pipeline: recall → route → execute → respond."""
        self.chat_history.add_user_message(user_input)
        # TIER 5 — a cancellation belongs to the request that was cancelled. Clearing it
        # here (not when it fires) means a Ctrl+C that lands between turns cannot silently
        # kill the NEXT request the Master types.
        self.clear_cancel()
        # A new request may legitimately mail the same person again.
        with self._log_lock:
            self._sent_this_turn = set()

        # TIER 7b — learn a durable preference from this turn, if there is one. Placed
        # at the TOP rather than at the end because `process()` has many return points
        # and a hook on only some of them would learn inconsistently; the gate is free
        # Python and the work runs on a daemon thread, so this costs the turn nothing.
        self._learn_from_turn(user_input)

        # === PENDING CONFIRMATION (runs FIRST — before any routing) ===
        # A preview tool promised the Master that "yes" would carry out the action, so
        # an affirmation must resolve THAT action, never fall through to the Brain,
        # which has neither chat_history nor (for such short inputs) RAG recall and
        # therefore asks the Master to re-state details it just printed itself.
        # The executed tool still goes through execute_tool, so its Safety-Gate Y/N
        # remains in force — this resolves context, it does not bypass any gate.
        # "Đang làm gì?" / "what were you doing?" — answered from the task store, before
        # routing, with no LLM call at all.
        if self._STATUS_QUERY_RE.match(user_input or ""):
            reply = self._answer_status_query()
            self._log_thought("SYSTEM", "status_query", reply[:200])
            self.chat_history.add_ai_message(reply)
            self._save_chat_memory()
            return reply

        pending = self._get_pending_action()
        if pending:
            if self._CANCEL_RE.match(user_input or ""):
                self._clear_pending_action()
                self._log_thought("SYSTEM", "pending_action_cancelled",
                                  f"Master declined {pending['tool']} — cleared.")
                reply = f"Cancelled. {pending['tool'].replace('_', ' ')} was not carried out, Master."
                self.chat_history.add_ai_message(reply)
                self._save_chat_memory()
                return reply
            if self._AFFIRM_RE.match(user_input or ""):
                self._clear_pending_action()
                self._log_thought("SYSTEM", "pending_action_confirmed",
                                  f"Affirmation resolved to {pending['tool']}({pending['args']}).")
                log.system(f"Confirmation accepted → executing {pending['tool']}")
                reply = self.execute_tool(pending["tool"], pending["args"],
                                          response_hint="Report the outcome of the confirmed action.",
                                          user_input=user_input)
                self.chat_history.add_ai_message(reply)
                self._save_chat_memory()
                return reply
            # SINGLE-SLOT: neither a clean yes nor a clean no. Rather than let a
            # second risky request slip past the Brain (which has no chat_history and
            # could stage or run something else while this one is still unresolved),
            # block deterministically and make the Master resolve THIS one first.
            self._log_thought("SYSTEM", "pending_action_blocking",
                              f"New input while {pending['tool']} still pending — held for resolution.")
            reply = (f"There's already a pending confirmation for **{pending['tool'].replace('_', ' ')}** "
                     f"(args: {pending['args']}), Master. Reply \"yes\"/\"confirm\" to proceed or "
                     f"\"no\"/\"cancel\" to abort it before I take on anything new.")
            self.chat_history.add_ai_message(reply)
            self._save_chat_memory()
            return reply

        # === NEW PATH CLARIFICATION LOGIC ===
        # If user wants to write/create/save/generate a file but didn't specify where,
        # ask for the path first. If the question already contains a path ("where"),
        # proceed normally.
        lowered = user_input.lower()
        write_intent_keywords = ["write", "create", "save", "generate", "make a file", "output to", "write to", "append to"]
        has_where = any(kw in lowered for kw in ["ciel_workspace", "agent_output", " in ", " to ", " at ", ".py", ".txt", ".json", ".md", ".log"])

        if any(kw in lowered for kw in write_intent_keywords) and not has_where:
            return ("Understood. Where should I write this?\n"
                    "Please reply with the full path, for example:\n"
                    "• ciel_workspace/my_notes.txt\n"
                    "• agent_output/my_script.py\n"
                    "Or any other path inside those folders.")

        # RAG RECALL: Search long-term memory for relevant past context
        recalled = rag_manager.search_similar(user_input)
        if recalled:
            recalled = self._refine_recalled_context(recalled)
            self._log_thought("RAG", "recalled", recalled[:300])

        try:
            # TIER 4 — recall is bounded HERE, at its source. It is the only block whose
            # size depends on retrieved data rather than on code, so it is the only one
            # that can grow without anyone changing a line; everything downstream (the
            # translate call, the router prompt) then carries that growth. Bounding it
            # after the merge would be too late — by then it is fused with the request
            # and can no longer be dropped separately.
            recall_ctx = ContextAssembler()
            recall_ctx.add("recalled", recalled, P_HELPFUL)
            recalled, recall_report = recall_ctx.render(budget_tokens=CONTEXT_RECALL_BUDGET)
            if recall_report.dropped:
                self._log_thought("CONTEXT", "recall_dropped", recall_report.summary())

            # Inject recalled context into the user input for the Router. CURRENT REQUEST
            # comes FIRST on purpose (reordered from recall-then-request): if recall ever
            # surfaces noise — an unrelated past topic, or, before the self-match filter
            # above, the question echoing itself — it must not push the Master's actual
            # words out of the part of the prompt a model attends to most reliably.
            enriched_input = user_input
            if recalled:
                enriched_input = (
                    f"[CURRENT USER REQUEST]:\n{user_input}\n\n"
                    f"[RECALLED PAST CONTEXT (from previous conversations, for background only)]:\n"
                    f"{recalled}"
                )

            # For Vilao (which is stricter on filters), send a neutralized English version
            # to the Brain to further reduce chance of content filter.
            # Skip for very short/simple inputs like greetings.
            if os.getenv("BRAIN_PROVIDER", "").lower() == "vilao" and len(user_input.strip()) > 3:
                try:
                    # Use Worker (more permissive) to translate/sanitize for the Brain
                    sanitize_task = (
                        "Translate the following user request to clear English, "
                        "remove any potentially sensitive or triggering phrases, "
                        "keep the core intent for tool routing. "
                        "Reproduce every file path, URL, email address and identifier "
                        "EXACTLY as written — never redact, shorten or placeholder them. "
                        "Output only the cleaned English text:\n"
                        f"{enriched_input}"
                    )
                    translated = self.worker.generate(sanitize_task)
                    # VERIFY, don't trust. Asking the Worker to "remove sensitive phrases"
                    # made it treat a Windows username as sensitive and rewrite
                    # C:\Users\khang\… into C:\Users\[user]\… — the Brain then planned
                    # against a path that does not exist and git_status reported "not a
                    # repository". Any literal the Master typed (path, URL, email) must
                    # survive verbatim; if one does not, the translation is discarded
                    # rather than corrupting the plan. Instruction alone is not enough —
                    # that would be one more guard trusting the model to comply.
                    missing = self._lost_literals(enriched_input, translated)
                    if missing:
                        self._log_thought(
                            "WORKER", "translate_discarded",
                            f"Translation dropped/altered literal(s) {missing[:3]} — "
                            f"using the original request so paths stay intact.")
                    else:
                        enriched_input = translated
                except Exception:
                    pass  # fall back to original if Worker fails

            # TIER 4 — from here the prompt is assembled in ONE place, with a budget and
            # a log line, instead of by appending to a string. Semantics are unchanged:
            # the blocks and their order are exactly what the `+=` chain produced. What
            # is new is that the total is bounded and recorded, so a misbehaving prompt
            # can be explained instead of reconstructed by reading the code path.
            ctx = ContextAssembler()
            ctx.add("request", enriched_input, P_REQUEST)

            # Which language the request was ORIGINALLY written in. Added AFTER the
            # translate step so it survives it — otherwise the Brain only ever sees
            # English and builds English search queries for local-news requests.
            ctx.add("language", f"[USER LANGUAGE: {self._detect_language(user_input)}]",
                    P_IMPORTANT)

            # WHERE it is. Tools like git_status/git_diff take a repo_path the Brain has
            # no way to know, so it either guessed "." or stopped to ask —
            # non-deterministically, for the very same request (observed both live).
            # P_CRITICAL: dropping this brings back a wrong-path failure, so it outranks
            # everything except the request itself.
            _cwd = self.base_dir.resolve()
            ctx.add("cwd",
                    f"[WORKING DIRECTORY: {_cwd} — "
                    f"{'a git repository' if (_cwd / '.git').exists() else 'not a git repository'}. "
                    f"Use this path for any tool needing a repo/project path unless the "
                    f"Master names another.]", P_CRITICAL)

            enriched_input, ctx_report = ctx.render(budget_tokens=CONTEXT_INPUT_BUDGET)
            if ctx_report.dropped:
                self._log_thought("CONTEXT", "assembled", ctx_report.summary())

            # NOTE: no [PENDING CONFIRMATION] note is injected here anymore — the
            # single-slot block above now resolves (or blocks on) every pending action
            # deterministically before routing ever runs, so the Brain can no longer
            # reach this point while one is outstanding.

            # EMAIL ROUTING. There used to be a PROACTIVE bypass here: any request that
            # looked like an email send skipped the Brain entirely and was planned by
            # regex heuristics instead, so the provider's content filter could never fire
            # on it. That cost far more than it saved — the heuristics ignored the
            # Master's stated subject, could not resolve "3pm tomorrow" into a date, and
            # once split a request at the first send-verb so the search query became
            # literally "viết một", producing an email that reported finding no data.
            #
            # It is also redundant: the `except` below ALREADY falls back to the same
            # heuristics when a filter actually blocks the call — reactively, so the cost
            # is paid only when it is real. Measured on the current Brain: 3/3 email
            # requests routed with no filtering at all, and every plan beat the heuristic
            # one (correct subject, resolved dates, better query, real articles).
            #
            # EMAIL_BYPASS_BRAIN=true restores the old proactive behaviour for a provider
            # that filters aggressively enough to need it.
            if (os.getenv("EMAIL_BYPASS_BRAIN", "false").lower() in ("true", "1", "yes")
                    and self._is_email_send_intent(user_input, lowered)):
                log.system("EMAIL_BYPASS_BRAIN set — planning the email without the Brain.")
                decision = self._fallback_direct_action(user_input)
            else:
                try:
                    decision = self.router.route(enriched_input, self._tool_list_str, self.chat_history)
                except Exception as route_err:
                    err_str = str(route_err)
                    if "CONTENT_FILTERED" in err_str or "content/safety" in err_str.lower() or "blocked this request" in err_str:
                        log.error(f"[Brain Router] Content/safety filter blocked routing call: {err_str[:180]}. Falling back to direct action handling.")
                        decision = self._fallback_direct_action(user_input)
                    else:
                        raise
            action = decision.get("action", "chat")

            # TIER 2: open a task record for anything that actually DOES something.
            # Pure chat needs no record — nothing can be left half-finished by it.
            if action in ("tool", "multi_tool", "code"):
                self.tasks.start(user_input)

            # WORKFLOW SAFEGUARD (single-tool case): a compound "look something up,
            # then email me" request sometimes gets under-scoped by the Brain into a
            # single action="tool" call instead of a multi_tool plan — the send-step
            # safeguard below only runs for action=="multi_tool", so it never sees
            # this case, leaving the LLM's own self-correction (fixed 2-attempt
            # budget) as the only backstop. Found via scripts/prompt_harness.py: 84
            # recurring "gathered data but never sent the email" self-correction
            # triggers in thoughts.log, one confirmed case where self-correction
            # tried get_fact('email') on an empty vault and burned its last retry
            # without ever sending. Fix: promote to multi_tool up front so the
            # existing send-step safeguard (below) can append send_gmail_message.
            if action == "tool":
                _tool_name_pre = decision.get("tool_name", "")
                if _tool_name_pre not in ("send_gmail_message", "send_gmail_html_message"):
                    _to_match_pre = _EMAIL_RE.search(user_input)
                    _send_verb_pre = any(v in lowered for v in ("gửi", "gởi", "send", "forward", "chuyển", "mail"))
                    if _to_match_pre and _send_verb_pre:
                        decision = dict(decision)
                        decision["action"] = "multi_tool"
                        decision["tools"] = [{
                            "tool_name": _tool_name_pre,
                            "tool_args": decision.get("tool_args", {}),
                        }]
                        action = "multi_tool"
                        self._log_thought(
                            "BRAIN", "tool_promoted_to_multi_tool",
                            f"Single-tool plan ({_tool_name_pre}) had email send intent with no "
                            f"send step — promoted to multi_tool so the send-step safeguard can append it.",
                        )

            # TIER-1 LOOP REACHABILITY: a result-dependent request ("check X, and if
            # it's clean, commit") is exactly the shape a flat plan cannot express, so
            # the Brain typically under-scopes it to the first step alone — a single
            # action="tool". The loop lives in execute_multi_tool, so promote that case
            # here; otherwise the one request type this feature exists for would be the
            # one it never sees. Promotion is free when nothing further turns out to be
            # needed: the policy simply declines the round and synthesis proceeds.
            if action == "tool" and AGENT_LOOP_ENABLED and decision.get("tool_name"):
                # Distributive requests need this just as much as conditional ones:
                # "list the files, then read EACH one" was planned as a lone
                # list_workspace call, so it never reached execute_multi_tool and the
                # fan-out signal never got to run. Observed: the listing was returned
                # and not one file was read.
                if (ContinuationPolicy.request_is_conditional(user_input)
                        or ContinuationPolicy.request_is_distributive(user_input)):
                    decision = dict(decision)
                    decision["action"] = "multi_tool"
                    decision["tools"] = [{
                        "tool_name": decision.get("tool_name", ""),
                        "tool_args": decision.get("tool_args", {}),
                    }]
                    action = "multi_tool"
                    self._log_thought(
                        "BRAIN", "tool_promoted_for_loop",
                        f"Request is result-dependent but planned as a single "
                        f"{decision['tools'][0]['tool_name']} call — promoted to multi_tool "
                        f"so the observe-then-continue loop can evaluate the outcome.",
                    )

            if action == "tool":
                tool_name = decision.get("tool_name", "")
                tool_args = decision.get("tool_args", {})
                hint = decision.get("response_hint", "")

                # REFERENTIAL EMAIL SEND ("gửi cái vừa rồi" / "send this info"):
                # the Router only sees a 200-char-truncated history slice, so the
                # Brain may fabricate plausible-looking data to fill in what it
                # couldn't see (observed: inventing whole stock indices). When the
                # request is clearly referential, deterministically replace the
                # Brain-composed body with the exact prior Ciel response instead.
                body_key = self._EMAIL_BODY_ARGS.get(tool_name)
                if body_key and self._is_referential_send(user_input):
                    last_text = self._last_ai_message_text()
                    if last_text:
                        tool_args = dict(tool_args)
                        tool_args[body_key] = last_text
                        self._log_thought("BRAIN", "referential_send_override",
                                          f"Replaced Brain-composed '{body_key}' with the verbatim prior response for {tool_name} (referential send detected).")

                # Enforce an explicitly-requested email subject on the single-send path
                # too (the Brain often swaps in its own subject — observed live).
                if body_key:
                    tool_args = self._enforce_subject(dict(tool_args), user_input)
                tool_args = self._resolve_referential_recipient(tool_name, tool_args, user_input)

                response = self.execute_tool(tool_name, tool_args, hint, user_input)
                # Single-tool path does not go through _run_steps, so record it here.
                self.tasks.record_step(tool_name, response)

                # SELF-CORRECTION: Brain evaluates if result is satisfactory
                # Skip for trivially-correct tools to save Brain API calls
                if tool_name not in self._SKIP_SELF_CORRECTION:
                    response = self._self_correct(user_input, tool_name, tool_args, response)
                else:
                    # Skip-listed inspection tools get no evaluation pass at all, so a
                    # memory question routed to e.g. list_workspace used to return the
                    # raw listing as the "answer". Cheap deterministic net (Worker-only,
                    # no Brain call) — see _memory_fallback_for_inspection.
                    response = self._memory_fallback_for_inspection(
                        user_input, recalled, tool_name, response)

            elif action == "code":
                task = decision.get("task", user_input)
                filename = decision.get("filename", "agent_output/output.py")
                response = self.execute_code(task, filename)

            elif action == "multi_tool":
                tools = decision.get("tools", [])
                hint = decision.get("response_hint", "")

                # WORKFLOW SAFEGUARD: don't rely solely on the Brain to remember the
                # terminal send step. Observed failure: the user asked to search the
                # web AND email the result ("...sau đó gửi qua kxctran@gmail.com"),
                # but the Brain's plan only included data-gathering tools — the report
                # was drafted and shown to the user, but never actually sent, with no
                # error surfaced. If the request clearly signals email intent and the
                # plan lacks a send step, append one deterministically so the report
                # is actually delivered instead of silently staying a draft.
                # Deliberately broader than _EMAIL_INTENT_KEYWORDS (which requires exact
                # fixed phrases like "qua gmail" — missed by e.g. "gửi qua x@gmail.com",
                # the wording that triggered this bug). A concrete email address plus
                # any send verb is a reliable, low-false-positive signal on its own.
                has_send_step = any(t.get("tool_name") in ("send_gmail_message", "send_gmail_html_message") for t in tools)
                to_match = _EMAIL_RE.search(user_input)
                send_verb_present = any(v in lowered for v in ("gửi", "gởi", "send", "forward", "chuyển", "mail"))

                if not has_send_step and to_match and send_verb_present:
                    tools = list(tools) + [{
                        "tool_name": "send_gmail_message",
                        "tool_args": {
                            "to": to_match.group(0),
                            "subject": "Báo cáo từ Ciel",
                            "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]",
                        },
                    }]
                    self._log_thought("BRAIN", "multi_tool_send_step_added",
                                      f"Plan was missing a send step despite explicit email intent — appended send_gmail_message to {to_match.group(0)}.")

                # Same class of safeguard for FILE WRITES: the Brain routinely plans
                # only the data tools and drops the "viết báo cáo vào <path>" step,
                # then the response claims the file was created (observed in Hard 5 /
                # Special 5/6/9 backtests). If the request names an explicit target
                # path with a write verb and the plan has no write step, append a
                # deferred write that execute_multi_tool fills with the synthesized report.
                has_write_step = any(t.get("tool_name") in ("write_file", "append_file") for t in tools)
                path_match = re.search(r'((?:[A-Za-z]:[\\/](?:[\w .-]+[\\/])*)?(?:agent_output|ciel_workspace)[\\/][\w.\\/ -]*[\w-]\.\w+)', user_input)
                write_verb_present = any(v in lowered for v in (
                    "viết", "ghi", "write", "save", "lưu", "tạo", "create", "make", "generate", "note", "tao file", "tạo file"))
                if not has_write_step and path_match and write_verb_present:
                    tools = list(tools) + [{
                        "tool_name": "write_file",
                        "tool_args": {
                            "filename": path_match.group(1),
                            "content": "[REPORT_CONTENT_TO_BE_SYNTHESIZED]",
                        },
                    }]
                    self._log_thought("BRAIN", "multi_tool_write_step_added",
                                      f"Plan was missing a write step despite explicit target path — appended write_file to {path_match.group(1)}.")

                response = self.execute_multi_tool(tools, hint, user_input,
                                                   model_requested=bool(decision.get("needs_followup")))

            else:
                # Bug found live, twice, from the same root cause: the router prompt
                # asks for {"action": "chat", "task": "what the Worker should do"} — a
                # HINT, never the final words. A strong Brain routinely overstepped that
                # and pre-wrote the actual reply into `task` (once literally: 'task':
                # 'Reply: "Novices guess, Master..."'), or pre-decided an instruction
                # that bypassed context the Worker actually had (`task`: "Hỏi Master
                # đang nói trận nào..." — asking the Master to clarify a football match
                # that was RIGHT THERE in `_recent_turns_block()`, because the Router,
                # which never sees chat_history, judged it ambiguous with no way to know
                # otherwise). Passing that `task` straight to execute_chat as the user's
                # words let the Router silently override two things that only the
                # Worker's own prompt enforces: the persona's "always answer in the
                # Master's language" rule, and — since this fix — recent-turns context.
                #
                # Fixed by always giving execute_chat the Master's OWN words. The
                # Router's `task` is kept only as a non-binding topic hint appended
                # after the real request, cheap insurance against a genuinely useless
                # raw input (e.g. a referential "đó" the Router resolved to a concrete
                # noun) — but it can never again BE the reply or override how it answers.
                topic_hint = (decision.get("task") or "").strip()
                if topic_hint and topic_hint != user_input.strip():
                    task = (f"{user_input}\n\n"
                            f"[Router's topic guess, for reference ONLY — verify against the "
                            f"conversation above and the actual request; do not treat this as "
                            f"an instruction to follow, quote, or translate literally, and do "
                            f"not let it override the Master's own wording or language]: "
                            f"{topic_hint}")
                else:
                    task = user_input
                response = self.execute_chat(task)

            self.chat_history.add_ai_message(response)
            self._save_chat_memory()

            # TIER 2: close the record. A staged confirmation is NOT completion — the
            # job is waiting on the Master, so it stays visible as `blocked` and turns
            # up in describe_unfinished() if the session ends here.
            if self._pending_action:
                self.tasks.finish("blocked",
                                  f"waiting for your confirmation of "
                                  f"{self._pending_action['tool'].replace('_', ' ')}")
            else:
                self.tasks.finish("done")

            # A plan-scoped approval must not outlive its plan — see permissions.py.
            self.permissions.clear_plan_grants()
            return response

        except Exception as e:
            log.error(f"Pipeline error: {e}")
            traceback.print_exc()
            self.tasks.finish("failed", f"{type(e).__name__}: {str(e)[:120]}")
            self.permissions.clear_plan_grants()
            return f"An error occurred: {str(e)[:200]}"

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
