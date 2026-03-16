import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.chat_message_histories import ChatMessageHistory

load_dotenv()

class CielCore:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Missing GEMINI_API_KEY in .env")

        self.llm = ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
            google_api_key=api_key,
            temperature=float(os.getenv("TEMPERATURE", "0.1")),
            max_output_tokens=int(os.getenv("MAX_TOKENS", "2048")),
        )
        
        self.chat_history = ChatMessageHistory()
        self.max_history = 20
        self.prompt = self._setup_prompt()

    def _setup_prompt(self):
        path = "persona/system_prompt.txt"
        instructions = open(path, "r", encoding="utf-8").read() if os.path.exists(path) else "You are Ciel."
        
        return ChatPromptTemplate.from_messages([
            ("system", instructions),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}")
        ])

    def chat(self, user_input: str) -> str:
        try:
            # Sử dụng invoke trực tiếp với cấu trúc tường minh
            chain = self.prompt | self.llm
            response = chain.invoke({
                "chat_history": self.chat_history.messages,
                "input": user_input
            })
            
            raw_content = response.content
            
            # Kiểm tra nếu AI trả về rỗng
            if not raw_content:
                return "<THOUGHT>Lỗi: AI trả về rỗng.</THOUGHT><RESPONSE>Master, tôi đang gặp trục trặc trong việc kết nối với lõi xử lý Gemini. Ngài hãy kiểm tra lại Internet hoặc API Key.</RESPONSE>"

            # Cập nhật lịch sử hội thoại
            self.chat_history.add_user_message(user_input)
            self.chat_history.add_ai_message(raw_content)
            
            return raw_content
            
        except Exception as e:
            return f"<THOUGHT>Lỗi hệ thống: {str(e)}</THOUGHT><RESPONSE>Cảnh báo: Lõi Gemini bị treo. Lỗi: {str(e)}</RESPONSE>"
            