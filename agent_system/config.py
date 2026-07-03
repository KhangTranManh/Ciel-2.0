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
