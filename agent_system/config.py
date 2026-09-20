# ==========================================================
# CONFIG — Reads from the project's .env file. Does NOT modify it.
# ==========================================================
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the parent Ciel 2.0 directory
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# --- Brain ---
BRAIN_PROVIDER = os.getenv("BRAIN_PROVIDER", "gemini")
BRAIN_MODEL = os.getenv("BRAIN_MODEL", "gemini-2.5-pro")
BRAIN_TEMPERATURE = 0.1
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
VILAO_URL = os.getenv("VILAO_URL", "")
VILAO_API_KEY = os.getenv("VILAO_KEY", "")

# CUSTOM — any single OpenAI-compatible endpoint (Vilao, TEM/hhtechapi.net, a
# future replacement, ...), used by ALL FOUR tiers (Brain/Worker/Middleware/
# Router-assistant) via *_PROVIDER=custom. Swapping providers going forward is a
# 2-variable edit (API_KEY + BASE_URL) plus whichever *_MODEL names change — no
# code change and no new provider branch needed. Kept separate from VILAO_URL/
# VILAO_API_KEY above (not deleted) so the old vilao-specific branch still works
# as a fallback/rollback path if ever needed.
API_KEY = os.getenv("API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "")

# vision_ops.py (vision_act/vision_describe) used to require its own separate
# GEMINI_API_KEY — confirmed live (2026-07-27) that the same API_KEY/BASE_URL/
# model already running Brain also accepts image input and describes screenshots
# accurately, so vision no longer needs a key of its own. Defaults to BRAIN_MODEL
# (already proven vision-capable) but stays independently overridable.
VISION_MODEL = os.getenv("VISION_MODEL", BRAIN_MODEL)

# Skill modules to skip entirely at load time (comma-separated stem names, e.g.
# "vision_ops,os_ops"). Used for server deployments where a capability class
# (screen capture, browser control, ...) should not even be reachable — not
# just discouraged in the prompt. Empty by default (all discovered skills load).
DISABLED_SKILL_MODULES = {s.strip() for s in os.getenv("DISABLED_SKILL_MODULES", "").split(",") if s.strip()}

# --- Router Assistant (fast front-line triage; escalates hard cases to the Brain) ---
# A cheap/fast model that decides "answerable as plain chat" vs "needs the Brain to
# plan tools". Off by default → pure Brain-only routing (unchanged behavior). Only the
# Vilao provider path is wired for now (mirrors Brain/Worker vilao branch).
ROUTER_ASSISTANT_ENABLED = os.getenv("ROUTER_ASSISTANT_ENABLED", "false").lower() in ("true", "1", "yes")
ROUTER_ASSISTANT_PROVIDER = os.getenv("ROUTER_ASSISTANT_PROVIDER", "vilao")
ROUTER_ASSISTANT_MODEL = os.getenv("ROUTER_ASSISTANT_MODEL", "")

# Safety - SAFETY_OPEN=true means open/permissive Brain CONTENT filtering
# (reduces over-blocking on normal tasks like email/text). This is about LLM
# content moderation ONLY — it does NOT control the destructive-tool gate.
SAFETY_OPEN = os.getenv("SAFETY_OPEN", "true").lower() in ("true", "1", "yes")
# DISABLE_SAFETY_GATE controls the destructive-tool confirmation gate independently.
# Default OFF (gate active / fail-safe). Do NOT couple it to SAFETY_OPEN.
DISABLE_SAFETY_GATE = os.getenv("DISABLE_SAFETY_GATE", "false").lower() in ("true", "1", "yes")
# VILAO_SAFETY_BYPASS is a provider-level content flag; it may follow SAFETY_OPEN.
VILAO_SAFETY_BYPASS = os.getenv("VILAO_SAFETY_BYPASS", "false").lower() in ("true", "1", "yes") or SAFETY_OPEN

# --- Worker ---
WORKER_PROVIDER = os.getenv("WORKER_PROVIDER", "gemini")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
WORKER_MODEL = os.getenv("CODER_MODEL", "gemini-2.5-flash")
WORKER_TEMPERATURE = 0.2
WORKER_NUM_CTX = 8192

# --- Middleware (verifier/finalizer for outbound email/report content) ---
# Third tier: reviews Worker output for relevance/consistency/plausible-grounding
# before it leaves the system (e.g. an email send). Deliberately narrow-scoped —
# see core/llm_connector.py's _middleware_review() for the choke-point and
# instructionAI-style rationale (this is a semantic backstop on top of, not a
# replacement for, the deterministic sanitizer/safeguards already in place).
MIDDLEWARE_ENABLED = os.getenv("MIDDLEWARE_ENABLED", "false").lower() in ("true", "1", "yes")
MIDDLEWARE_PROVIDER = os.getenv("MIDDLEWARE_PROVIDER", "gemini")
MIDDLEWARE_MODEL = os.getenv("MIDDLEWARE_MODEL", "gemini-2.5-pro")
MIDDLEWARE_TEMPERATURE = float(os.getenv("MIDDLEWARE_TEMPERATURE", "0.1"))
# "email" = only send_gmail_message/send_gmail_html_message/reply_to_email/create_gmail_draft.
# "external"/"all" are accepted but currently behave like "email" — chat-path and
# non-email external channels are not wired into the review choke-point yet.
MIDDLEWARE_SCOPE = os.getenv("MIDDLEWARE_SCOPE", "email").lower()
MIDDLEWARE_MAX_PASSES = int(os.getenv("MIDDLEWARE_MAX_PASSES", "1"))

# --- Tier-1 agent loop (observe → re-plan → act); see core/continuation.py ---
# Ciel's plan is a flat list of tool calls fixed before anything runs, so a request
# like "check git status, and if it's clean, commit" cannot be expressed at all. The
# loop closes that gap. Whether a round happens is decided by deterministic Python,
# never by asking the model "are we done?", so a plain one-shot request pays zero
# extra tokens and a weaker/swapped model cannot make the loop unsafe or unbounded.
AGENT_LOOP_ENABLED = os.getenv("AGENT_LOOP_ENABLED", "true").lower() in ("true", "1", "yes")
# Extra observe-then-act rounds beyond the first. 2 covers "check → decide → act";
# raising it mostly buys diminishing returns at one planner call each.
AGENT_LOOP_MAX_ROUNDS = int(os.getenv("AGENT_LOOP_MAX_ROUNDS", "2"))
# Wall-clock ceiling for the whole loop, so a slow provider cannot strand a request.
AGENT_LOOP_MAX_SECONDS = float(os.getenv("AGENT_LOOP_MAX_SECONDS", "120"))

# --- Parallel tool execution (see core/parallel.py) ---
# Provably-independent read-only steps in one plan run concurrently instead of one after
# another. Opt-in per tool, so an unknown/new skill stays sequential by default. Helps
# most on fan-out and slow network tools (a run once spent 566s on five sequential
# scrapes); barely moves a 2-3 fast-tool plan, where the LLM calls dominate.
AGENT_PARALLEL_ENABLED = os.getenv("AGENT_PARALLEL_ENABLED", "true").lower() in ("true", "1", "yes")
AGENT_PARALLEL_MAX_WORKERS = int(os.getenv("AGENT_PARALLEL_MAX_WORKERS", "4"))

# --- Tier-6 proactivity (see core/triggers.py + core/notifier.py) ---
# Lets Ciel speak first when a condition it watches becomes true, instead of only ever
# answering. OFF by default, and opt-in per trigger by name: this list will grow, and a
# default-on trigger added later would start talking without anyone choosing it.
# Known names — A (Ciel watching itself): unfinished_task, daily_cost, repeated_failure,
# deferred_approval | B (clock): digest, morning_digest, monthly_plan, weekly_plan,
# reminder_due |
# C (outside world, each needs a threshold below): price_alert, important_email, stale_todo.
PROACTIVE_ENABLED = os.getenv("PROACTIVE_ENABLED", "false").lower() in ("true", "1", "yes")
PROACTIVE_TRIGGERS = [t.strip() for t in os.getenv("PROACTIVE_TRIGGERS", "").split(",") if t.strip()]
# Hard ceiling on interruptions per day. Beyond it, findings still survive — they drop to
# the digest instead of interrupting. Counted in Python, never left to model restraint.
PROACTIVE_DAILY_BUDGET = int(os.getenv("PROACTIVE_DAILY_BUDGET", "8"))
# A live process is not a live human: past this idle time the CLI/app stops counting as
# a channel anyone is watching, and notifications route to the fallback (Telegram).
PROACTIVE_IDLE_SECONDS = float(os.getenv("PROACTIVE_IDLE_SECONDS", "600"))
# An unanswered question re-routes to the fallback channel after this long.
PROACTIVE_ASK_ESCALATE_SECONDS = float(os.getenv("PROACTIVE_ASK_ESCALATE_SECONDS", "1800"))
# Per-trigger thresholds. The two cost limits default to 0 = disabled, because a wrong
# ceiling fires every single day and gets proactivity switched off wholesale.
PROACTIVE_UNFINISHED_MIN_AGE = float(os.getenv("PROACTIVE_UNFINISHED_MIN_AGE", "1800"))
PROACTIVE_COST_USD_LIMIT = float(os.getenv("PROACTIVE_COST_USD_LIMIT", "0"))
PROACTIVE_COST_TOKEN_LIMIT = int(os.getenv("PROACTIVE_COST_TOKEN_LIMIT", "0"))
PROACTIVE_FAILURE_THRESHOLD = int(os.getenv("PROACTIVE_FAILURE_THRESHOLD", "3"))
# Group C thresholds. Each is empty by default, and its trigger is skipped without one —
# a condition trigger with no threshold is just a timer that pretends to be smart.
# Format: "XAU/USD>2400, BTC/USDT<60000"  (>, <, >=, <= all accepted)
PROACTIVE_PRICE_ALERTS = os.getenv("PROACTIVE_PRICE_ALERTS", "")
PROACTIVE_IMPORTANT_SENDERS = [s.strip() for s in
                               os.getenv("PROACTIVE_IMPORTANT_SENDERS", "").split(",") if s.strip()]
PROACTIVE_STALE_TODO_DAYS = float(os.getenv("PROACTIVE_STALE_TODO_DAYS", "7"))
PROACTIVE_DIGEST_HOUR = int(os.getenv("PROACTIVE_DIGEST_HOUR", "8"))
PROACTIVE_DIGEST_MINUTE = int(os.getenv("PROACTIVE_DIGEST_MINUTE", "0"))
# After this many interrupts about the SAME finding, it goes quiet (drops to the digest).
# The Master has evidently decided not to act on it, and repeating only teaches them to
# ignore the channel. Muting is per finding, not per trigger.
PROACTIVE_REPEAT_LIMIT = int(os.getenv("PROACTIVE_REPEAT_LIMIT", "4"))

# --- Monthly/weekly planner --------------------------------------------------------
# Structured plans live in one SQLite file. They are separate from conversational
# memory and from ciel_workspace/todos.json (the immediate checklist). Planner tools
# are always available; automatic overviews remain opt-in through PROACTIVE_TRIGGERS
# using the names `monthly_plan` and/or `weekly_plan`.
_planner_path = Path(os.getenv("PLANNER_DB_PATH", "ciel_data/planner.db")).expanduser()
PLANNER_DB_PATH = (_planner_path if _planner_path.is_absolute()
                   else Path(__file__).resolve().parent.parent / _planner_path)
PLANNER_TIMEZONE = os.getenv("PLANNER_TIMEZONE", "Asia/Ho_Chi_Minh").strip() or "Asia/Ho_Chi_Minh"
PLANNER_MONTHLY_DAY = int(os.getenv("PLANNER_MONTHLY_DAY", "1"))
PLANNER_MONTHLY_HOUR = int(os.getenv("PLANNER_MONTHLY_HOUR", "8"))
PLANNER_MONTHLY_MINUTE = int(os.getenv("PLANNER_MONTHLY_MINUTE", "0"))
PLANNER_WEEKLY_WEEKDAY = int(os.getenv("PLANNER_WEEKLY_WEEKDAY", "0"))  # Monday=0
PLANNER_WEEKLY_HOUR = int(os.getenv("PLANNER_WEEKLY_HOUR", "8"))
PLANNER_WEEKLY_MINUTE = int(os.getenv("PLANNER_WEEKLY_MINUTE", "0"))

# --- Tier-7 user model (see core/user_model.py) ---
# The PUSH side of memory. facts.json is pull-only (the model must guess a key and choose
# to look it up), which is why it is still empty; this small profile is injected into the
# Worker prompts where a preference actually changes the output. It renders to "" while
# empty, so leaving it on costs nothing until it has learned something.
USER_MODEL_ENABLED = os.getenv("USER_MODEL_ENABLED", "true").lower() in ("true", "1", "yes")
# HARD ceiling on the injected block. This is a FIXED tax on every call that carries it —
# the same cost pattern Tier 4 exists to control — so it is capped, not merely tidy.
USER_MODEL_TOKEN_BUDGET = int(os.getenv("USER_MODEL_TOKEN_BUDGET", "250"))
# Tier 7b — learn a preference WITHOUT being told to remember it. A deterministic gate
# (`assess_preference`) runs first and is free, so an ordinary turn costs nothing; only a
# turn containing explicit durable wording ("từ giờ", "luôn", "đừng bao giờ") spends one
# extraction call, on a background thread so the reply is never delayed.
USER_MODEL_LEARN_ENABLED = os.getenv("USER_MODEL_LEARN_ENABLED", "true").lower() in ("true", "1", "yes")
# Ceiling on extraction calls per day, so a chatty session cannot multiply the cost.
USER_MODEL_LEARN_DAILY_LIMIT = int(os.getenv("USER_MODEL_LEARN_DAILY_LIMIT", "20"))

# --- Tier-4 context discipline (see core/context.py) ---
# A Brain call carries ~4,291 FIXED tokens — router prompt 37%, tool list 33%, persona
# 28% — against a ~16-token request. 99% is overhead resent verbatim, and that floor is
# what makes a 4K-context local model unable to run Ciel at all.
# Ceiling on the assembled per-request context (recall + request + language + cwd).
# 0 disables the budget entirely and restores pre-Tier-4 behaviour.
CONTEXT_INPUT_BUDGET = int(os.getenv("CONTEXT_INPUT_BUDGET", "1200"))
# Ceiling on RAG recall specifically. It is the only block whose size depends on
# retrieved data rather than on code, so it is the one that can grow unnoticed.
CONTEXT_RECALL_BUDGET = int(os.getenv("CONTEXT_RECALL_BUDGET", "600"))
# How much persona the ROUTER carries. The router emits JSON and nothing else, yet it is
# sent the full 1,205-token character description — 28% of every Brain call spent on
# voice, for a component that never speaks. "slim" sends a one-line identity instead;
# "none" sends none; "full" is the original. Change only with an A/B to back it.
ROUTER_PERSONA_MODE = os.getenv("ROUTER_PERSONA_MODE", "full").strip().lower()
# Bug found by reading a real transcript: chat_history was stored, persisted and archived
# into RAG, and never once read back into a prompt — so "tại sao lại thế" right after a
# real answer produced a reply with no memory of it. This injects the last few turns into
# the RESPONSE path only (never the Router — that stays out on purpose, see router.py's
# July-2026 note about an old unresolved request bleeding into a new one).
CONTEXT_RECENT_TURNS_ENABLED = os.getenv("CONTEXT_RECENT_TURNS_ENABLED", "true").lower() in ("true", "1", "yes")
CONTEXT_RECENT_TURNS_BUDGET = int(os.getenv("CONTEXT_RECENT_TURNS_BUDGET", "500"))

# Compact session subject passed to Brain before routing. This is structured state
# (topic/entities/last action), not raw history and not durable memory.
ACTIVE_SUBJECT_ENABLED = os.getenv("ACTIVE_SUBJECT_ENABLED", "true").lower() in ("true", "1", "yes")
ACTIVE_SUBJECT_TTL_SECONDS = int(os.getenv("ACTIVE_SUBJECT_TTL_SECONDS", "900"))
ACTIVE_SUBJECT_MAX_IDLE_TURNS = int(os.getenv("ACTIVE_SUBJECT_MAX_IDLE_TURNS", "3"))
ACTIVE_SUBJECT_MAX_ENTITIES = int(os.getenv("ACTIVE_SUBJECT_MAX_ENTITIES", "5"))

# --- Request timeout (applies to every LLM client: Brain, Worker, Middleware) ---
# Without this, a provider that stalls (accepts the connection but never replies —
# different from an outright connection error) hangs the client forever, and the
# tenacity retry logic below never engages because no exception is ever raised to
# retry on. Observed: a live test run sat at 0% CPU for 10+ minutes on a single
# Worker call with no error and no recovery. This bounds the wait so a stall raises
# a real (retryable) timeout instead of hanging indefinitely.
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "90"))  # seconds

# --- Retry: Gemini (cloud — fewer attempts, longer waits) ---
GEMINI_RETRY_MAX_ATTEMPTS = 5
GEMINI_RETRY_INITIAL_WAIT = 2   # seconds
GEMINI_RETRY_MAX_WAIT = 60      # seconds

# --- Retry: Ollama (local — more attempts, shorter waits) ---
OLLAMA_RETRY_MAX_ATTEMPTS = 8
OLLAMA_RETRY_INITIAL_WAIT = 1   # seconds
OLLAMA_RETRY_MAX_WAIT = 30      # seconds

# --- Allowed Tools (Brain can only route to these) ---
ALLOWED_TOOL_NAMES = {"buffer_write"}

# --- Output ---
DEFAULT_OUTPUT_DIR = "./agent_output"

# --- Convenience: pick the right retry profile based on provider ---
if BRAIN_PROVIDER.lower() == "ollama":
    RETRY_MAX_ATTEMPTS = OLLAMA_RETRY_MAX_ATTEMPTS
    RETRY_INITIAL_WAIT = OLLAMA_RETRY_INITIAL_WAIT
    RETRY_MAX_WAIT = OLLAMA_RETRY_MAX_WAIT
else:
    RETRY_MAX_ATTEMPTS = GEMINI_RETRY_MAX_ATTEMPTS
    RETRY_INITIAL_WAIT = GEMINI_RETRY_INITIAL_WAIT
    RETRY_MAX_WAIT = GEMINI_RETRY_MAX_WAIT
