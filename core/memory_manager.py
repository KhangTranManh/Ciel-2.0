import os
import json
from pathlib import Path

class MemoryManager:
    def __init__(self):
        self.base_dir = Path(__file__).resolve().parent.parent / "ciel_data" / "memory_bank"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.fact_file = self.base_dir / "facts.json"
        self._init_vault()

    def _init_vault(self):
        if not self.fact_file.exists():
            self.fact_file.write_text("{}", encoding="utf-8")

    def save_fact(self, key: str, value: str):
        data = json.loads(self.fact_file.read_text(encoding="utf-8"))
        data[key] = value
        self.fact_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_fact(self, key: str) -> str:
        data = json.loads(self.fact_file.read_text(encoding="utf-8"))
        return data.get(key, "NOT_FOUND")

    def delete_fact(self, key: str):
        data = json.loads(self.fact_file.read_text(encoding="utf-8"))
        if key in data:
            del data[key]
            self.fact_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_all_facts_context(self) -> str:
        data = json.loads(self.fact_file.read_text(encoding="utf-8"))
        if not data:
            return ""
        context = "MASTER'S FACTS VAULT:\n"
        for k, v in data.items():
            context += f"- {k}: {v}\n"
        return context

    # ---------------------------------------------------------
    # VECTOR KNOWLEDGE BASE (RAG) - PREPARATION MODULE
    # ---------------------------------------------------------
    def ingest_file(self, file_path: str):
        # Master, this module will chunk and embed files using ChromaDB
        # Requires: pip install chromadb langchain-community
        pass

    def query_file_knowledge(self, query: str) -> str:
        # Retrieves relevant document chunks based on Master's query
        pass