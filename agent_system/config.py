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
BRAIN_PROVIDER = os.getenv("BRAIN_PROVIDER", "deepseek")
BRAIN_MODEL = os.getenv("BRAIN_MODEL", "deepseek-v4-pro")
BRAIN_TEMPERATURE = 0.1
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# --- Worker ---
WORKER_PROVIDER = os.getenv("WORKER_PROVIDER", "ollama")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
WORKER_MODEL = os.getenv("CODER_MODEL", "qwen2.5-coder:14b")
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
