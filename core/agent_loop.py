import re
import datetime
import os
from colorama import Fore, Style
from .llm_connector import CielCore # Gọi lõi AI

class AgentLoop:
    def __init__(self):
        self.core = CielCore()
        self.log_file = os.path.join(os.getenv("LOG_DIRECTORY", "./ciel_data/logs"), "thoughts.log")
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)

    def _log_thought(self, thought_text):
        """Ghi âm thầm suy nghĩ vào file log"""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(f"\n[{timestamp}] THOUGHT:\n{thought_text.strip()}\n{'-'*40}")

    def run_step(self, user_input: str) -> str:
        """Vòng lặp xử lý 1 chạm"""
        # 1. Gọi LLM từ llm_connector
        raw_output = self.core.chat(user_input)
        
        # 2. Bóc tách dữ liệu bằng Regex mạnh
        thought_match = re.search(r'<THOUGHT>(.*?)</THOUGHT>', raw_output, re.DOTALL | re.IGNORECASE)
        response_match = re.search(r'<RESPONSE>(.*?)</RESPONSE>', raw_output, re.DOTALL | re.IGNORECASE)
        
        # 3. Xử lý Logic
        if thought_match:
            self._log_thought(thought_match.group(1))
        
        if response_match:
            return response_match.group(1).strip()
        else:
            # Nếu AI vẫn ngoan cố lỗi định dạng, dọn dẹp thẻ rác rồi trả về
            clean_text = re.sub(r'<.*?>', '', raw_output).strip()
            self._log_thought("LỖI ĐỊNH DẠNG: Đã cưỡng chế làm sạch output.")
            return clean_text