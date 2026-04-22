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

    def _extract_tool_calls_from_text(self, raw_content: str, available_tools: dict) -> list:
        extracted = []

        action_tags = re.findall(r'<ACTION\s*>(.*?)</ACTION\s*>', raw_content, flags=re.I | re.S)
        for action_text in action_tags:
            action_text = action_text.strip()
            tool_name, args_str = None, None

            match = re.match(r"^([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\((.*)\)$", action_text, flags=re.S)
            if match:
                tool_name = match.group(1).strip()
                args_str = match.group(3).strip()

            if not tool_name:
                match_simple = re.match(r"^([a-zA-Z0-9_]+)\s*\((.*)\)$", action_text, flags=re.S)
                if match_simple:
                    tool_name = match_simple.group(1).strip()
                    args_str = match_simple.group(2).strip()

            if not tool_name or tool_name not in available_tools:
                continue
            tool_args = self._safe_parse_tool_args(args_str, available_tools[tool_name])
            if isinstance(tool_args, dict):
                extracted.append({"name": tool_name, "args": tool_args})

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
        if args_str == "":
            return {}

        try:
            parsed = ast.literal_eval(args_str)
            if isinstance(parsed, dict):
                return parsed
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

    def _contains_fake_success(self, text: str) -> bool:
        lowered = text.lower()
        phrases = [
            "i have used the tool",
            "i've used the tool",
            "i have searched",
            "i have checked your",
            "i have fetched",
            "here are your",
            "here are the results",
            "đã sử dụng tool",
            "đã dùng tool",
            "đã kiểm tra",
        ]
        return any(p in lowered for p in phrases)

    def _get_allowed_tools_for_intent(self, user_input: str) -> set | None:
        text = user_input.lower()
        email_kw = ["email", "mail", "hộp thư", "gmail", "inbox", "thư"]
        trading_kw = ["btc", "eth", "sol", "mexc", "giá", "rsi", "ma", "technical", "portfolio", "crypto", "coin"]
        if any(k in text for k in email_kw):
            return {"search_gmail", "get_message", "get_thread", "create_gmail_draft",
                    "send_gmail_message", "trash_email", "mark_email_read", "reply_to_email"}
        if any(k in text for k in trading_kw):
            return {"get_crypto_price", "get_24h_stats", "analyze_technical_indicators", "get_mexc_portfolio"}
        return None

    def _normalize_tool_call(self, tool_call: dict) -> dict:
        name = tool_call["name"].lower()
        args = dict(tool_call.get("args", {}))

        if name == "search_gmail":
            if "count" in args and "max_results" not in args:
                args["max_results"] = args.pop("count")
            if "query" not in args or args.get("query") in ("", "label:new"):
                args["query"] = "category:primary"
            if "max_results" not in args:
                args["max_results"] = 5

        return {"name": name, "args": args}

    def _build_fallback_tool_call(self, user_input: str, allowed_tools: set) -> dict | None:
        text = user_input.lower()

        if "search_gmail" in allowed_tools:
            num = 5
            num_match = re.search(r'(\d+)', user_input)
            if num_match:
                num = min(int(num_match.group(1)), 25)

            query = "category:primary"
            if "unread" in text or "chưa đọc" in text:
                query = "is:unread category:primary"
            elif any(k in text for k in ["sent", "đã gửi"]):
                query = "in:sent"

            return {"name": "search_gmail", "args": {"query": query, "max_results": num}}

        if "get_crypto_price" in allowed_tools:
            for sym in ["btcusdt", "ethusdt", "solusdt", "bnbusdt"]:
                if sym[:3] in text:
                    return {"name": "get_crypto_price", "args": {"symbol": sym.upper()}}
        if "get_mexc_portfolio" in allowed_tools and any(k in text for k in ["portfolio", "tài sản", "assets", "vị thế"]):
            return {"name": "get_mexc_portfolio", "args": {}}

        return None

    def run_step(self, user_input: str) -> str:
        self.core.chat_history.add_user_message(user_input)

        print(f"\n⚡ [Ciel]: Processing with Gemini...")
        ai_msg = self.core.chat_with_tools(user_input)

        tool_calls_to_process = getattr(ai_msg, 'tool_calls', [])

        # ==========================================================
        # STEP 1: EXTRACT RAW CONTENT + ENFORCEMENT
        # ==========================================================
        raw_content = getattr(ai_msg, 'content', str(ai_msg))
        if isinstance(raw_content, list):
            raw_content = "".join([block.get("text", "") for block in raw_content if isinstance(block, dict)])
        elif not isinstance(raw_content, str):
            raw_content = str(raw_content)

        needs_enforcement = (
            not tool_calls_to_process
            and self._looks_like_tool_intent(user_input)
            and (self._contains_refusal_phrase(raw_content) or self._contains_fake_success(raw_content))
        )
        if needs_enforcement:
            allowed = self._get_allowed_tools_for_intent(user_input)
            tool_hint = ""
            if allowed:
                tool_hint = f"\nYou MUST use one of these specific tools: {', '.join(sorted(allowed))}.\nDo NOT use execute_shell_command to echo fake results.\n"

            force_prompt = (
                "[SYSTEM ENFORCEMENT]\n"
                "Your previous answer was INVALID — either a refusal or a hallucinated tool usage without real execution.\n"
                "You MUST produce a REAL tool call now.\n"
                f"{tool_hint}"
                "Do NOT claim you already did it. Do NOT say you lack capability.\n"
                "Return a real tool call with correct arguments.\n\n"
                f"Master command: {user_input}"
            )
            ai_msg = self.core.chat_with_tools(force_prompt)
            tool_calls_to_process = getattr(ai_msg, 'tool_calls', [])
            raw_content = getattr(ai_msg, 'content', str(ai_msg))
            if isinstance(raw_content, list):
                raw_content = "".join([block.get("text", "") for block in raw_content if isinstance(block, dict)])
            elif not isinstance(raw_content, str):
                raw_content = str(raw_content)

        # Text fallback extraction
        if not tool_calls_to_process:
            available_tools = {t.name: t for t in self.core.tool_manager.get_tools()}
            extracted_calls = self._extract_tool_calls_from_text(raw_content, available_tools)
            for call in extracted_calls:
                tool_calls_to_process.append(call)

        # Intent-based tool filter
        allowed_tools = self._get_allowed_tools_for_intent(user_input)
        if allowed_tools and tool_calls_to_process:
            tool_calls_to_process = [
                tc for tc in tool_calls_to_process
                if tc["name"].lower() in allowed_tools
            ]

        # Hard auto-fallback
        if not tool_calls_to_process and allowed_tools:
            fallback = self._build_fallback_tool_call(user_input, allowed_tools)
            if fallback:
                tool_calls_to_process.append(fallback)

        # Normalize all tool calls
        tool_calls_to_process = [self._normalize_tool_call(tc) for tc in tool_calls_to_process]

        # ==========================================================
        # STEP 2: TOOL EXECUTION + REFLECTION
        # ==========================================================
        if tool_calls_to_process:
            all_results = []
            for tool_call in tool_calls_to_process:
                tool_name = tool_call["name"].lower()
                tool_args = tool_call["args"]

                exec_result = self.core.tool_manager.execute_tool(tool_name, tool_args)
                exec_result_text = self.core.tool_manager.format_tool_result(exec_result)

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
                f"4. ANTI-DUPLICATION: Show ONLY the EXACT items returned by the tool. If the tool returned 2 emails, show 2 — NOT 5. NEVER copy-paste the same item multiple times to fill a quota.\n"
                f"5. DECODE: If email subjects contain encoded text (=?utf-8?q?...), decode them into readable text.\n"
                f"CRITICAL RULE: You MUST wrap your internal reasoning in <THOUGHT> and your final answer to the Master in <RESPONSE>."
            )

            ai_msg = self.core.chat_with_tools(reflection_prompt)

        # ==========================================================
        # STEP 3: PARSE + DISPLAY FINAL RESULT
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
