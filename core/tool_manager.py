from colorama import Fore, Style

class ToolManager:
    def __init__(self):
        self.tools = []
        self.tool_map = {}
        
        # Load zones independently to prevent cross-corruption
        self._load_internal_tools()
        self._load_external_tools()

    def _load_internal_tools(self):
        try:
            # Import safe, core local operations
            from skills.internal.memory_ops import save_fact, delete_fact
            
            self.tools.extend([save_fact, delete_fact])
            print(Fore.GREEN + "[Ciel System] Internal tools loaded safely." + Style.RESET_ALL)
        except Exception as e:
            print(Fore.RED + f"[Ciel Fatal] Failed to load internal tools: {e}" + Style.RESET_ALL)

    def _load_external_tools(self):
        try:
            # Load Gmail toolkit tools
            from skills.external.gmail_ops import get_gmail_tools
            gmail_tools = get_gmail_tools()
            
            if gmail_tools:
                self.tools.extend(gmail_tools)
                print("[Ciel System] Gmail armory fully loaded and operational.")
            else:
                print(Fore.CYAN + "[Ciel System] No external tools loaded." + Style.RESET_ALL)
                
        except Exception as e:
            print(f"[Ciel Warning] External tool corruption detected. Error: {e}")
            print(Fore.CYAN + "[Ciel System] External tools registry is empty." + Style.RESET_ALL)

    def get_tools(self) -> list:
        """Returns the list of validated tools to bind to the LLM."""
        self.tool_map = {tool.name: tool for tool in self.tools}
        return self.tools

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