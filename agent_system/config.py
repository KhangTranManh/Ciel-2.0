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

# GPT / OpenAI-compatible (for custom models like gx/gpt-5.5)
GPT_API_KEY = os.getenv("GPT_API_KEY", "")
GPT_BASE_URL = os.getenv("GPT_BASE_URL", "")

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
