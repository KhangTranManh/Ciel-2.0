from langchain_core.tools import tool
from core.memory_manager import MemoryManager

memory = MemoryManager()

@tool
def save_fact(key: str, value: str) -> str:
    """Save a specific fact, password, or setting for the Master into the local vault. YOU MUST USE THIS TOOL when asked to remember, save, or store information."""
    memory.save_fact(key, value)
    return f"Fact saved successfully: {key}."

@tool
def get_fact(key: str) -> str:
    """Retrieve a specific fact from the Master's local vault by its key. YOU MUST USE THIS TOOL when asked to recall, look up, or check a previously saved piece of information."""
    result = memory.get_fact(key)
    if result == "NOT_FOUND":
        return f"No fact found for key '{key}' in the vault."
    return f"Fact '{key}': {result}"

@tool
def delete_fact(key: str) -> str:
    """Delete a specific fact from the local vault. YOU MUST USE THIS TOOL when asked to forget, delete, or remove saved information."""
    memory.delete_fact(key)
    return f"Fact deleted successfully: {key}."