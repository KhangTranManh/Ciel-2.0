from langchain_core.tools import tool
from core.memory_manager import MemoryManager

memory = MemoryManager()

@tool
def save_fact(key: str, value: str) -> str:
    """Save a specific fact, password, or setting for the Master into the local vault. YOU MUST USE THIS TOOL when asked to remember, save, or store information."""
    memory.save_fact(key, value)
    return f"Fact saved successfully: {key}."

@tool
def delete_fact(key: str) -> str:
    """Delete a specific fact from the local vault. YOU MUST USE THIS TOOL when asked to forget, delete, or remove saved information."""
    memory.delete_fact(key)
    return f"Fact deleted successfully: {key}."