import os
import json
from pathlib import Path
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.chat_message_histories import ChatMessageHistory
from .memory_manager import MemoryManager
from .tool_manager import ToolManager

load_dotenv()

class CielCore:
    def __init__(self):
        self.tool_manager = ToolManager()
        tools = self.tool_manager.get_tools()

        google_api_key = os.getenv("GEMINI_API_KEY")
        if not google_api_key:
            raise RuntimeError("GEMINI_API_KEY is required in .env")

        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

        self.router_llm = ChatGoogleGenerativeAI(
            model=gemini_model,
            google_api_key=google_api_key,
            temperature=0.1,
        ).bind_tools(tools)

        self.coder_llm = self.router_llm
        self.cloud_llm = None
        
        self.chat_history = ChatMessageHistory()
        self.max_history = 20
        self.base_dir = Path(__file__).resolve().parent.parent
        self.chat_memory_file = self.base_dir / "ciel_data" / "memory_bank.json"
        
        self.memory_manager = MemoryManager()
        self.prompt_template = self._build_prompt()
        self._load_chat_memory()

    def _trim_history(self):
        if len(self.chat_history.messages) > self.max_history:
            self.chat_history.messages = self.chat_history.messages[-self.max_history:]

    def _build_prompt(self):
        persona_dir = self.base_dir / "persona"
        
        # 1. Dynamically load modular prompt fragments
        def load_fragment(filename, default_text=""):
            filepath = persona_dir / filename
            return filepath.read_text(encoding="utf-8") if filepath.exists() else default_text

        identity = load_fragment("identity.txt", "You are Ciel.")
        directives = load_fragment("directives.txt")
        format_rules = load_fragment("format.txt")
        
        # ==========================================================
        # BƯỚC VÁ LỖI: Lấy toàn bộ Hướng dẫn sử dụng Tool từ ToolManager
        # ==========================================================
        tool_manuals = self.tool_manager.get_dynamic_prompt()
        
        # 2. Khâu tất cả lại với nhau
        sys_msg = f"{identity}\n\n{directives}\n\n{format_rules}\n{tool_manuals}\n\nCURRENT VAULT FACTS:\n{{fact_vault}}"
        
        return ChatPromptTemplate.from_messages([
            ("system", sys_msg),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}")
        ])

    def _load_chat_memory(self):
        if self.chat_memory_file.exists():
            try:
                data = json.loads(self.chat_memory_file.read_text(encoding="utf-8"))
                # Lọc lịch sử độc (refusal pattern) để tránh lặp lại hành vi sai
                toxic_markers = [
                    "Thư viện CIEL không cung cấp",
                    "I do not have the capability",
                    "As an AI",
                    "I have used the tool",
                    "I've used the tool",
                    "I have searched",
                    "echo I have used",
                    "echo Show me",
                    "Action: search_gmail({'count'",
                    "Action: execute_shell_command({'command': 'echo",
                    "label:new",
                ]
                for msg in data:
                    content = msg.get("content", "")
                    if any(marker in content for marker in toxic_markers):
                        continue
                    if msg.get("type") == "human":
                        self.chat_history.add_user_message(content)
                    else:
                        self.chat_history.add_ai_message(content)
                self._trim_history()
            except: pass

    def _save_chat_memory(self):
        self.chat_memory_file.parent.mkdir(parents=True, exist_ok=True)
        self._trim_history()
        data = [{"type": m.type, "content": m.content} for m in self.chat_history.messages]
        self.chat_memory_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def chat_with_tools(self, user_input: str, use_coder: bool = False):
        facts_context = self.memory_manager.get_all_facts_context()
        inputs = {
            "fact_vault": facts_context,
            "chat_history": self.chat_history.messages,
            "input": user_input
        }
        
        active_llm = self.coder_llm if use_coder else self.router_llm
        chain = self.prompt_template | active_llm
        return chain.invoke(inputs)