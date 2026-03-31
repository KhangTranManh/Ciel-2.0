import re
import os
import ast
import json
import inspect
from datetime import datetime
from pathlib import Path
from .llm_connector import CielCore

class AgentLoop:
    def __init__(self):
        self.core = CielCore()
        base_dir = Path(__file__).resolve().parent.parent
        self.log_path = base_dir / "ciel_data" / "logs" / "thoughts.log"
        self.debug_mode = os.getenv("DEBUG", "false").lower() == "true"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log_interaction(self, user_input: str, thought: str, response: str, tool_used: str = None):
        if not self.debug_mode: return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}]\n[MASTER] : {user_input.strip()}\n")
            f.write(f"[THOUGHT]: {thought.strip()}\n")
            if tool_used: f.write(f"[TOOL]   : {tool_used.strip()}\n")
            f.write(f"[RESPONSE]: {response.strip()}\n{'-'*50}\n")

    def _is_coding_task(self, user_input: str) -> bool:
        """Trạm kiểm soát: Nhờ Hermes đánh giá xem lệnh này có cần Qwen không."""
        prompt = (
            "Analyze the intent of this command. "
            "If it asks to write code, create files, run scripts, or manipulate the system, answer ONLY 'CODE'. "
            "Otherwise, answer ONLY 'CHAT'.\n"
            f"Command: {user_input}"
        )
        try:
            # Gọi Router để phân loại nhanh (chỉ mất ~1 giây)
            response = self.core.router_llm.invoke(prompt)
            return "CODE" in str(response.content).upper()
        except:
            # Nếu lỗi, dùng heuristic bắt từ khóa
            return any(k in user_input.lower() for k in ['tạo file', 'code', 'script', 'python', 'viết'])

    def _extract_tool_calls_from_text(self, raw_content: str, available_tools: dict) -> list:
        """Bắt tool call từ text fallback: hỗ trợ cả 'Action:' và '<ACTION>...</ACTION>'"""
        extracted = []

        # 1) Ưu tiên bắt <ACTION>tool(args)</ACTION>
        action_tags = re.findall(r'<ACTION\s*>(.*?)</ACTION\s*>', raw_content, flags=re.I | re.S)
        for action_text in action_tags:
            action_text = action_text.strip()
            match = re.match(r"^([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\((.*)\)$", action_text, flags=re.S)
            if not match:
                continue
            tool_name = match.group(1).strip()
            args_str = match.group(3).strip()
            if tool_name not in available_tools:
                continue
            tool_args = self._safe_parse_tool_args(args_str, available_tools[tool_name])
            if isinstance(tool_args, dict):
                extracted.append({"name": tool_name, "args": tool_args})

        # 2) Bắt Action: tool(args) (case-insensitive)
        action_lines = re.findall(r'(?i)action\s*:\s*([a-zA-Z0-9_]+)\s*\((.*?)\)', raw_content, flags=re.S)
        for tool_name, args_str in action_lines:
            tool_name = tool_name.strip()
            if tool_name not in available_tools:
                continue
            tool_args = self._safe_parse_tool_args(args_str.strip(), available_tools[tool_name])
            if isinstance(tool_args, dict):
                extracted.append({"name": tool_name, "args": tool_args})

        return extracted

    def _safe_parse_tool_args(self, args_str: str, target_tool) -> dict:
        """Parse args linh hoạt và an toàn cho tool fallback parser."""
        if args_str == "":
            return {}

        try:
            parsed = ast.literal_eval(args_str)
            if isinstance(parsed, dict):
                return parsed
            # Nếu là scalar -> map vào tham số đầu tiên
            sig = inspect.signature(target_tool.func)
            params = list(sig.parameters.keys())
            if params:
                return {params[0]: parsed}
            return {}
        except Exception:
            pass

        try:
            json_str = args_str.replace("'", '"')
            parsed = json.loads(json_str)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        # Fallback: chuỗi thuần
        if args_str.startswith(("'", '"')) and args_str.endswith(("'", '"')):
            clean_str = args_str[1:-1]
            try:
                sig = inspect.signature(target_tool.func)
                params = list(sig.parameters.keys())
                if params:
                    return {params[0]: clean_str}
            except Exception:
                return {}

        return {}

    def _looks_like_tool_intent(self, user_input: str) -> bool:
        text = user_input.lower()
        keywords = [
            "email", "mail", "hộp thư", "gmail",
            "file", "thư mục", "workspace", "list", "đọc file", "dung lượng",
            "btc", "eth", "sol", "mexc", "giá", "rsi", "ma", "technical",
            "run", "script", "python"
        ]
        return any(k in text for k in keywords)

    def _contains_refusal_phrase(self, text: str) -> bool:
        lowered = text.lower()
        phrases = [
            "thư viện ciel không cung cấp",
            "i cannot",
            "i do not have the capability",
            "không thể",
            "chưa có khả năng"
        ]
        return any(p in lowered for p in phrases)

    def run_step(self, user_input: str) -> str:
        self.core.chat_history.add_user_message(user_input)
        
        # ==========================================================
        # BƯỚC 1: TRẠM PHÂN LUỒNG (ROUTING GATE)
        # ==========================================================
        is_coder = self._is_coding_task(user_input)
        agent_name = "CODER (Qwen 2.5)" if is_coder else "ROUTER (Hermes 3)"
        print(f"\n⚡ [Trạm Điều Phối]: Đang bàn giao nhiệm vụ cho {agent_name}...")

        # Gọi não bộ tương ứng xử lý
        ai_msg = self.core.chat_with_tools(user_input, use_coder=is_coder)
        
        tool_calls_to_process = getattr(ai_msg, 'tool_calls', [])

        # ==========================================================
        # BƯỚC 2: CỖ MÁY BẮT LỆNH TỐI THƯỢNG (BRACKET COUNTING)
        # ==========================================================
        raw_content = getattr(ai_msg, 'content', str(ai_msg))
        if isinstance(raw_content, list):
            raw_content = "".join([block.get("text", "") for block in raw_content if isinstance(block, dict)])
        elif not isinstance(raw_content, str):
            raw_content = str(raw_content)

        # Nếu model trả lời kiểu từ chối trong khi intent cần tool -> ép 1 lượt retry bắt buộc gọi tool
        if (not tool_calls_to_process
            and self._looks_like_tool_intent(user_input)
            and self._contains_refusal_phrase(raw_content)):
            force_prompt = (
                "[SYSTEM ENFORCEMENT]\n"
                "Your previous answer was refusal-style and violates policy.\n"
                "You MUST call an available bound tool now if relevant.\n"
                "Do NOT claim lack of capability.\n"
                "Return either a real tool call (preferred) or a strict <THOUGHT>/<RESPONSE> if truly no matching tool exists.\n\n"
                f"Master command: {user_input}"
            )
            ai_msg = self.core.chat_with_tools(force_prompt, use_coder=is_coder)
            tool_calls_to_process = getattr(ai_msg, 'tool_calls', [])
            raw_content = getattr(ai_msg, 'content', str(ai_msg))
            if isinstance(raw_content, list):
                raw_content = "".join([block.get("text", "") for block in raw_content if isinstance(block, dict)])
            elif not isinstance(raw_content, str):
                raw_content = str(raw_content)

        if not tool_calls_to_process:
            available_tools = {t.name: t for t in self.core.tool_manager.get_tools()}

            extracted_calls = self._extract_tool_calls_from_text(raw_content, available_tools)
            for call in extracted_calls:
                tool_calls_to_process.append(call)
        # ==========================================================
        # BƯỚC 3: VÒNG LẶP XỬ LÝ TOOL (REFLECTION LOOP)
        # ==========================================================
        if tool_calls_to_process:
            all_results = []
            for tool_call in tool_calls_to_process:
                tool_name = tool_call["name"].lower()
                tool_args = tool_call["args"]
                
                exec_result = self.core.tool_manager.execute_tool(tool_name, tool_args)
                exec_result_text = str(exec_result)
                
                self.core.chat_history.add_ai_message(f"Action: {tool_name}({tool_args})")
                all_results.append(f"[{tool_name} OUTPUT]:\n{exec_result_text}")

            combined_results = "\n\n".join(all_results)
            reflection_prompt = (
                f"[SYSTEM OBSERVATION - CONFIDENTIAL]\n"
                f"Tool execution results:\n{combined_results}\n\n"
                f"Master's Command: '{user_input}'\n\n"
                f"CRITICAL OVERRIDE DIRECTIVE:\n"
                f"1. You MUST answer the Master using ONLY the real data provided in the Tool execution results above.\n"
                f"2. DO NOT apologize. DO NOT add safety warnings. DO NOT say you lack capabilities.\n"
                f"3. Act confidently, as if you have always possessed this capability.\n"
                f"CRITICAL RULE: You MUST wrap your internal reasoning in <THOUGHT> and your final answer to the Master in <RESPONSE>."
            )
            
            # Gọi lại đúng bộ não đang xử lý (Router hoặc Coder) để đọc kết quả
            ai_msg = self.core.chat_with_tools(reflection_prompt, use_coder=is_coder)
                
        # ==========================================================
        # BƯỚC 4: BÓC TÁCH & HIỂN THỊ KẾT QUẢ CUỐI
        # ==========================================================
        raw = getattr(ai_msg, 'content', str(ai_msg))
        if isinstance(raw, list):
            raw = "".join([block.get("text", "") for block in raw if isinstance(block, dict)])
        else:
            raw = str(raw)

        self.core.chat_history.add_ai_message(raw)
        self.core._save_chat_memory()

        thought_match = re.search(r'<THOUGHT>(.*?)</THOUGHT>', raw, re.S | re.I)
        response_match = re.search(r'<RESPONSE>(.*?)(?:</RESPONSE>|$)', raw, re.S | re.I)
        
        thought_text = thought_match.group(1).strip() if thought_match else "EVALUATING."
        response_text = response_match.group(1).strip() if response_match else re.sub(r'<.*?>', '', raw, flags=re.S).strip()

        self._log_interaction(user_input, thought_text, response_text)
        return response_text if response_text else "Master, core formatting disrupted."