from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.chat_message_histories import ChatMessageHistory
import os
from dotenv import load_dotenv

load_dotenv()

class CielCore:
    def __init__(self):
        # 1. KHỞI TẠO LÕI MÔ HÌNH VỚI CÁC CHỈ SỐ BẢO VỆ PHẦN CỨNG
        self.llm = ChatOllama(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("REASONING_MODEL", "qwen2.5:7b-instruct"),
            temperature=0.1, # Rất thấp để câu trả lời luôn ổn định, logic, không bịa đặt
            num_predict=2048, # Giới hạn số token đầu ra
            top_p=0.9,
        )
        
        # 2. KHỞI TẠO TRÍ NHỚ (Chỉ nhớ 10 lượt hội thoại gần nhất để tối ưu tốc độ)
        self.chat_history = ChatMessageHistory()
        self.max_history_length = 20  # 10 cặp hỏi-đáp (20 tin nhắn)
        
        # 3. NẠP NHÂN CÁCH VÀ KHIÊN BẢO VỆ
        self.prompt = self._build_secure_prompt()

    def _build_secure_prompt(self):
        """Đọc file nhân cách và khóa chặt nó lại"""
        try:
            with open("persona/system_prompt.txt", "r", encoding="utf-8") as f:
                system_instructions = f.read()
        except FileNotFoundError:
            system_instructions = "You are a helpful AI." # Fallback an toàn

        return ChatPromptTemplate.from_messages([
            ("system", system_instructions),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}")
        ])

    def chat(self, user_input: str) -> str:
        """Hàm giao tiếp chính - trả về raw output cho agent_loop xử lý"""
        chain = self.prompt | self.llm
        history = self.chat_history.messages
        
        # Gọi LLM và lấy raw response
        raw_response = chain.invoke({"chat_history": history, "input": user_input}).content
        
        # Lưu vào chat history (lưu raw để giữ nguyên context đầy đủ)
        self.chat_history.add_user_message(user_input)
        self.chat_history.add_ai_message(raw_response)
        
        # Giới hạn lịch sử để tối ưu RAM
        if len(self.chat_history.messages) > self.max_history_length:
            self.chat_history.messages = self.chat_history.messages[-self.max_history_length:]
        
        # Trả về raw response để agent_loop xử lý THOUGHT/RESPONSE
        return raw_response