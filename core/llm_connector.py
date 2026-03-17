import os
import json
from pathlib import Path
from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.chat_message_histories import ChatMessageHistory
from .memory_manager import MemoryManager

load_dotenv()

class CielCore:
    def __init__(self):
        self.model_name = os.getenv("LOCAL_MODEL", "qwen2.5:7b-instruct")
        
        self.llm = ChatOllama(
            model=self.model_name,
            temperature=0.1,
            num_ctx=4096 
        )
        
        self.chat_history = ChatMessageHistory()
        self.max_history = 20
        self.base_dir = Path(__file__).resolve().parent.parent
        self.chat_memory_file = self.base_dir / "ciel_data" / "memory_bank.json"
        
        self.memory_manager = MemoryManager()
        self.prompt_template = self._build_prompt()
        self._load_chat_memory()

    def _build_prompt(self):
        prompt_path = "persona/system_prompt.txt"
        sys_msg = open(prompt_path, "r", encoding="utf-8").read() if os.path.exists(prompt_path) else "You are Ciel."
        
        # Inject the Fact Vault directly into the system prompt
        sys_msg += "\n\n{fact_vault}"
        
        return ChatPromptTemplate.from_messages([
            ("system", sys_msg),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}")
        ])

    def _load_chat_memory(self):
        if self.chat_memory_file.exists():
            try:
                data = json.loads(self.chat_memory_file.read_text(encoding="utf-8"))
                for msg in data:
                    if msg["type"] == "human": self.chat_history.add_user_message(msg["content"])
                    else: self.chat_history.add_ai_message(msg["content"])
            except: pass

    def _save_chat_memory(self):
        self.chat_memory_file.parent.mkdir(parents=True, exist_ok=True)
        data = [{"type": m.type, "content": m.content} for m in self.chat_history.messages]
        self.chat_memory_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def chat(self, user_input: str) -> str:
        try:
            # Retrieve facts dynamically before every response
            facts_context = self.memory_manager.get_all_facts_context()
            
            chain = self.prompt_template | self.llm
            response = chain.invoke({
                "fact_vault": facts_context,
                "chat_history": self.chat_history.messages,
                "input": user_input
            })
            
            if not response.content: return ""

            self.chat_history.add_user_message(user_input)
            self.chat_history.add_ai_message(response.content)
            
            if len(self.chat_history.messages) > self.max_history:
                self.chat_history.messages = self.chat_history.messages[-self.max_history:]
            
            self._save_chat_memory()
            return response.content
        except Exception as e:
            return f"<THOUGHT>Local API Error: {str(e)}</THOUGHT><RESPONSE>Master, local core execution failed: {str(e)}</RESPONSE>"