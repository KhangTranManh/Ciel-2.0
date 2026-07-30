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

def _find_key_ci(data: dict, key: str) -> str | None:
    """Case-insensitive lookup. Found live: a fact was saved as "Tên" (Vietnamese,
    capitalized) but get_fact("tên") / get_fact("name") both missed it — dict lookup
    is exact-match, and the tool's own prompt tells the model to use snake_case
    English-ish keys ("name"), which is a different string entirely from how a key
    may have actually been saved. Case alone is cheap and safe to normalize; this does
    NOT translate "name" -> "Tên" (that would be guessing), it only catches the same
    word typed in a different case."""
    if key in data:
        return key
    lowered = key.strip().lower()
    for real_key in data:
        if real_key.strip().lower() == lowered:
            return real_key
    return None

def _get_fact(key: str) -> str:
    """Retrieve a specific fact from the Master's local vault by its key."""
    data = _load_facts()
    found = _find_key_ci(data, key)
    if found is not None:
        return f"Fact '{found}': {data[found]}"
    if not data:
        return f"No fact found for key '{key}' — the vault is empty."
    # Hand back the REAL keys rather than a bare miss — lets a retry (self-correction,
    # or the same turn if the model reads this result) use the actual key instead of
    # guessing a different spelling/language/case again.
    return (f"No fact found for key '{key}'. Available keys in the vault: "
            f"{', '.join(data.keys())}. Retry get_fact with the exact key shown above.")

def _delete_fact(key: str) -> str:
    """Delete a specific fact from the local vault."""
    data = _load_facts()
    found = _find_key_ci(data, key)
    if found is not None:
        del data[found]
        _save_facts(data)
        return f"Fact deleted successfully: {found}."
    return f"No fact found for key '{key}' — nothing deleted."

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