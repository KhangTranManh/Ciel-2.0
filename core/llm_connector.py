import os
import json
from pathlib import Path
from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_google_genai import ChatGoogleGenerativeAI, HarmCategory, HarmBlockThreshold
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.chat_message_histories import ChatMessageHistory
from .memory_manager import MemoryManager
from .tool_manager import ToolManager

load_dotenv()

class CielCore:
    def __init__(self):
        self.tool_manager = ToolManager()
        tools = self.tool_manager.get_tools()

        # 1. Initialize Local Core (Primary)
        self.local_model_name = os.getenv("LOCAL_MODEL", "qwen2.5:7b-instruct")
        self.local_llm = ChatOllama(
            model=self.local_model_name,
            temperature=0.1,
            num_ctx=4096 
        ).bind_tools(tools)
        
        # 2. Initialize Cloud Core (Fallback)
        google_api_key = os.getenv("GEMINI_API_KEY")
        self.cloud_llm = None
        if google_api_key:
            self.cloud_llm = ChatGoogleGenerativeAI(
                model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
                google_api_key=google_api_key,
                temperature=0.1,
                safety_settings={
                    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                }
            ).bind_tools(tools)
        
        self.chat_history = ChatMessageHistory()
        self.max_history = 20
        self.base_dir = Path(__file__).resolve().parent.parent
        self.chat_memory_file = self.base_dir / "ciel_data" / "memory_bank.json"
        
        self.memory_manager = MemoryManager()
        self.prompt_template = self._build_prompt()
        self._load_chat_memory()

    def _build_prompt(self):
        persona_dir = self.base_dir / "persona"
        
        # 1. Dynamically load modular prompt fragments
        def load_fragment(filename, default_text=""):
            filepath = persona_dir / filename
            return filepath.read_text(encoding="utf-8") if filepath.exists() else default_text

        identity = load_fragment("identity.txt", "You are Ciel.")
        directives = load_fragment("directives.txt")
        format_rules = load_fragment("format.txt")
        
        # 2. Stitch them together logically
        sys_msg = f"{identity}\n\n{directives}\n\n{format_rules}\n\nCURRENT VAULT FACTS:\n{{fact_vault}}"
        
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

    def chat_with_tools(self, user_input: str):
        facts_context = self.memory_manager.get_all_facts_context()
        
        inputs = {
            "fact_vault": facts_context,
            "chat_history": self.chat_history.messages,
            "input": user_input
        }
        
        # ATTEMPT 1: Execute Local Core
        try:
            chain = self.prompt_template | self.local_llm
            return chain.invoke(inputs)
            
        except Exception as local_error:
            # ATTEMPT 2: Fallback to Cloud Core
            if self.cloud_llm:
                print(f"\n[CIEL SYSTEM WARNING]: Local core unreachable ({local_error}). Rerouting through Gemini Cloud...")
                chain = self.prompt_template | self.cloud_llm
                return chain.invoke(inputs)
            else:
                # If both fail
                raise RuntimeError(f"Local core failed and GEMINI_API_KEY is missing. I cannot process the request, Master.")