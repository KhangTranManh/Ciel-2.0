from langchain_core.tools import tool, StructuredTool
import json
from pathlib import Path
from threading import Lock

# Setup facts file path
FACTS_FILE = Path(__file__).resolve().parent.parent.parent / "ciel_data" / "facts.json"

FACTS_LOCK = Lock()

# ==========================================
# SYSTEM PROMPT
# ==========================================
MEMORY_OPS_PROMPT = """
[MEMORY VAULT ARMORY]
You possess tools to manage the Master's persistent memory vault.

1. `save_fact`: Save a fact, password, or setting to the vault.
2. `get_fact`: Retrieve a specific fact from the vault.
3. `delete_fact`: Delete a fact from the vault.

[STRICT MEMORY RULES]
1. Use `save_fact` when the Master says to remember, save, or store information.
2. Use `get_fact` when the Master asks to recall, look up, or check saved information.
3. Use `delete_fact` when the Master asks to forget, delete, or remove saved information.
4. Keys should be descriptive snake_case (e.g., "wifi_password", "preferred_language").
"""

# ==========================================
# INTERNAL HELPERS
# ==========================================
def _load_facts() -> dict:
    with FACTS_LOCK:
        if not FACTS_FILE.exists():
            return {}
        try:
            return json.loads(FACTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

def _save_facts(data: dict):
    with FACTS_LOCK:
        FACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        FACTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

# ==========================================
# TOOL FUNCTIONS
# ==========================================
def _save_fact(key: str, value: str) -> str:
    """Save a specific fact, password, or setting for the Master into the local vault."""
    data = _load_facts()
    data[key] = value
    _save_facts(data)
    return f"Fact saved successfully: {key}."

def _get_fact(key: str) -> str:
    """Retrieve a specific fact from the Master's local vault by its key."""
    data = _load_facts()
    if key not in data:
        return f"No fact found for key '{key}' in the vault."
    return f"Fact '{key}': {data[key]}"

def _delete_fact(key: str) -> str:
    """Delete a specific fact from the local vault."""
    data = _load_facts()
    if key in data:
        del data[key]
        _save_facts(data)
    return f"Fact deleted successfully: {key}."

# ==========================================
# FACTORY (matches the standard get_*_tools() pattern)
# ==========================================
def get_memory_tools() -> dict:
    tools = [
        StructuredTool.from_function(
            func=_save_fact,
            name="save_fact",
            description="Save a specific fact, password, or setting for the Master into the local vault. "
                        "YOU MUST USE THIS TOOL when asked to remember, save, or store information."
        ),
        StructuredTool.from_function(
            func=_get_fact,
            name="get_fact",
            description="Retrieve a specific fact from the Master's local vault by its key. "
                        "YOU MUST USE THIS TOOL when asked to recall, look up, or check a previously saved piece of information."
        ),
        StructuredTool.from_function(
            func=_delete_fact,
            name="delete_fact",
            description="Delete a specific fact from the local vault. "
                        "YOU MUST USE THIS TOOL when asked to forget, delete, or remove saved information."
        ),
    ]
    return {"tools": tools, "prompt": MEMORY_OPS_PROMPT}