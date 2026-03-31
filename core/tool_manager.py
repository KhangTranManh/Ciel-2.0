from colorama import Fore, Style

class ToolManager:
    def __init__(self):
        self.tools = []
        self.tool_map = {}
        self.system_prompts = [] # NEW: We now collect prompts from the tools
        
        # Load zones independently to prevent cross-corruption
        self._load_internal_tools()
        self._load_external_tools()

    def _load_internal_tools(self):
        # 1. NẠP CÁC CÔNG CỤ BỘ NHỚ CŨ (Nếu ngài vẫn đang dùng)
        try:
            from skills.internal.memory_ops import save_fact, delete_fact
            self.tools.extend([save_fact, delete_fact])
            print(Fore.GREEN + "[Ciel System] Internal memory tools loaded safely." + Style.RESET_ALL)
        except Exception as e:
            pass # Bỏ qua nếu ngài đã xóa file memory_ops

        # ==========================================
        # 2. NẠP KHO VŨ KHÍ HỆ THỐNG (QUARANTINE ZONE)
        # ==========================================
        try:
            from skills.internal.system_ops import get_system_tools
            sys_data = get_system_tools()
            
            sys_tools = sys_data.get("tools", [])
            sys_prompt = sys_data.get("prompt", "")
            
            if sys_tools:
                self.tools.extend(sys_tools)
                if sys_prompt:
                    self.system_prompts.append(sys_prompt) # Nạp chỉ thị Paranoia vào não Ciel
                print(Fore.GREEN + "[Ciel System] Local System Armory (Quarantine Zone) locked and loaded." + Style.RESET_ALL)
            else:
                print(Fore.CYAN + "[Ciel System] No System tools loaded." + Style.RESET_ALL)
                
        except Exception as e:
            print(Fore.RED + f"[Ciel Fatal] Failed to load System tools: {e}" + Style.RESET_ALL)

        # ==========================================
        # 3. NẠP KHO VŨ KHÍ OS DIRECT CONTROL
        # ==========================================
        try:
            from skills.internal.os_ops import get_os_tools
            os_data = get_os_tools()

            os_tools = os_data.get("tools", [])
            os_prompt = os_data.get("prompt", "")

            if os_tools:
                self.tools.extend(os_tools)
                if os_prompt:
                    self.system_prompts.append(os_prompt)
                print(Fore.GREEN + "[Ciel System] OS Direct Control Armory loaded." + Style.RESET_ALL)
            else:
                print(Fore.CYAN + "[Ciel System] No OS Direct Control tools loaded." + Style.RESET_ALL)

        except Exception as e:
            print(Fore.RED + f"[Ciel Warning] Failed to load OS Direct Control tools: {e}" + Style.RESET_ALL)

    def _load_external_tools(self):
        try:
            from skills.external.gmail_ops import get_gmail_tools
            gmail_data = get_gmail_tools() # This now returns a dictionary!
            
            # Extract tools and prompt
            gmail_tools = gmail_data.get("tools", [])
            gmail_prompt = gmail_data.get("prompt", "")
            
            if gmail_tools:
                self.tools.extend(gmail_tools)
                if gmail_prompt:
                    self.system_prompts.append(gmail_prompt) # Add the manual to our collection
                print(Fore.GREEN + "[Ciel System] Gmail armory fully loaded and operational." + Style.RESET_ALL)
            else:
                print(Fore.CYAN + "[Ciel System] No external tools loaded." + Style.RESET_ALL)
                
        except Exception as e:
            print(f"[Ciel Warning] External tool corruption detected. Error: {e}")
        try:
            from skills.external.trading_ops import get_trading_tools
            trading_data = get_trading_tools()
            
            trading_tools = trading_data.get("tools", [])
            trading_prompt = trading_data.get("prompt", "")
            
            if trading_tools:
                self.tools.extend(trading_tools)
                if trading_prompt:
                    self.system_prompts.append(trading_prompt)
                print(Fore.GREEN + "[Ciel System] Trading armory fully loaded and operational." + Style.RESET_ALL)
            else:
                print(Fore.CYAN + "[Ciel System] No Trading tools loaded." + Style.RESET_ALL)
                
        except Exception as e:
            print(Fore.RED + f"[Ciel Warning] Trading tool corruption detected. Error: {e}" + Style.RESET_ALL)
            print(Fore.CYAN + "[Ciel System] External tools registry is empty." + Style.RESET_ALL)

    def get_tools(self) -> list:
        """Returns the list of validated tools to bind to the LLM."""
        self.tool_map = {tool.name: tool for tool in self.tools}
        return self.tools

    def get_dynamic_prompt(self) -> str:
        """NEW: Stitches all tool manuals together into one string."""
        if not self.system_prompts:
            return ""
        
        # Join all the prompts we collected with a nice divider
        combined_prompts = "\n\n--- ACTIVE WEAPON MANUALS ---\n\n".join(self.system_prompts)
        return f"\n\n{combined_prompts}"

    def execute_tool(self, name: str, args: dict) -> str:
        """Executes a tool by its name and returns the result."""
        if name in self.tool_map:
            try:
                result = self.tool_map[name].invoke(args)
                if result is None:
                    return f"Successfully executed tool: {name}."
                return result if isinstance(result, str) else str(result)
            except Exception as e:
                return f"Failed to execute {name}. Error: {str(e)}"
        return f"Warning: Tool '{name}' does not exist or was quarantined."