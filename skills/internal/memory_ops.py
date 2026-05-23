from langchain_core.tools import tool
import os
import json
from pathlib import Path
from threading import Lock

# Setup facts file path
FACTS_FILE = Path(__file__).resolve().parent.parent.parent / "ciel_data" / "facts.json"

FACTS_LOCK = Lock()

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

@tool
def save_fact(key: str, value: str) -> str:
    """Save a specific fact, password, or setting for the Master into the local vault. YOU MUST USE THIS TOOL when asked to remember, save, or store information."""
    data = _load_facts()
    data[key] = value
    _save_facts(data)
    return f"Fact saved successfully: {key}."

@tool
def get_fact(key: str) -> str:
    """Retrieve a specific fact from the Master's local vault by its key. YOU MUST USE THIS TOOL when asked to recall, look up, or check a previously saved piece of information."""
    data = _load_facts()
    if key not in data:
        return f"No fact found for key '{key}' in the vault."
    return f"Fact '{key}': {data[key]}"

@tool
def delete_fact(key: str) -> str:
    """Delete a specific fact from the local vault. YOU MUST USE THIS TOOL when asked to forget, delete, or remove saved information."""
    data = _load_facts()
    if key in data:
        del data[key]
        _save_facts(data)
    return f"Fact deleted successfully: {key}."