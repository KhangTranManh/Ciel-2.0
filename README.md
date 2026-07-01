# Ciel 2.0

Ciel is an autonomous AI assistant and System Sentinel. It runs on a **Brain-Worker** architecture where the Brain (Router) classifies user intent and the Worker (Generator) produces text, code, or formatted output.

The system includes 13+ tools across internal (filesystem, OS, vision) and external (Gmail, trading, Telegram, GitHub) skill packs, hybrid long-term memory via ChromaDB, and a safety gate requiring explicit approval for destructive operations.

Built on LangChain with multi-provider support (Vilao, DeepSeek, Gemini, Ollama).

## Key Features

- **Modular Brain-Worker Architecture**: Brain handles intent routing and planning; Worker generates content/code.
- **Selective Safety Model**: SAFETY_OPEN mode for permissive operation on normal tasks (e.g. email routing). Strict protections retained for violent text, information leaks, harm, and big/destructive changes.
- **Path-Aware File Writing**: The system now asks for destination path (ciel_workspace/ or agent_output/) unless explicitly specified in the query. Direct support for writing to either location.
- **Content Filter Resilience**: Early bypasses and fallbacks for provider-level filters (e.g. Vilao Qwen CONTENT_FILTERED). Input sanitization for Vilao Brain.
- **Hybrid RAG Memory**: Short-term chat memory + long-term ChromaDB semantic memory with compression.
- **Self-Healing**: Multi-attempt autonomous error recovery for tool and code failures.
- **Proactive Scheduling**: Background tasks (e.g. daily digest) that bypass Brain for efficiency.
- **Vision & UI Control**: PyAutoGUI + Gemini Vision for screen interaction.
- **Multi-Provider**: Flexible Brain/Worker provider selection via .env.

## Architecture Overview

See [architect.md](architect.md) for full details.

High-level flow (primary pipeline):

User Input → CielCore.process()
  → RAG Recall + Compression
  → Router (Brain) → action: chat | tool | code | multi_tool
  → (Safety gate for high-risk)
  → Execute + Worker format / Self-healing
  → Response + Memory update

There is also a LangGraph-based pipeline in agent_system/ for more structured multi-step code generation.

## Quick Start

1. Clone the repo and install dependencies:
   pip install -r requirements.txt

2. Configure .env (copy from example if needed):
   - Set BRAIN_PROVIDER=vilao (or deepseek/gemini/ollama)
   - Set BRAIN_MODEL=alic/qwen3.7-max (for Vilao)
   - Provide corresponding API keys
   - SAFETY_OPEN=true (recommended for normal use)
   - VILAO_SAFETY_BYPASS=true
   - DISABLE_SAFETY_GATE=true (for automated flows)

3. Run the CLI:
   python main.py

4. (Optional) Run the API server for external clients:
   python main_api.py

## Configuration Highlights

- SAFETY_OPEN=true: Makes the system permissive for most tasks (avoids over-filtering). Safety remains in for violent content, leaks, harm, and destructive actions.
- File Writing: Always asks for target path if not present in query. Supports both ciel_workspace/ (user data) and agent_output/ (generated artifacts).
- Providers: Brain and Worker can use different providers. Current recommended: Vilao for Brain + DeepSeek for Worker.
- High-Risk Tools: send_gmail_message, execute_shell_command, delete_file, trash_email, git_confirm_push, vision_act — confirmation can be auto when gate is disabled.

## Key Directories

- ciel_workspace/ — Sandbox for user files, data, logs, screenshots.
- agent_output/ — Default location for AI-generated code and artifacts.
- core/ — Main orchestration (CielCore, Router, ToolManager, RAG, etc.).
- skills/ — Tool implementations (internal + external).
- agent_system/ — Brain/Worker models + LangGraph pipeline + buffer writer.
- backtest/ — Integration and stress tests.
- instructionAI/ — AI instruction files (SKILL.md etc.).

## Recent Updates (as of July 2026)

- Migrated Brain to Vilao (alic/qwen3.7-max) while keeping DeepSeek for Worker. Added provider-specific handling (sanitization, bypass).
- Introduced SAFETY_OPEN, VILAO_SAFETY_BYPASS, and DISABLE_SAFETY_GATE flags. Default to open/permissive mode while preserving targeted safety for violent text, info leaks, harm, and big/destructive changes.
- Removed hard-coded forcing of agent_output/ paths in Brain/Router prompts.
- Added proactive path clarification: the system now asks Where should I write this? unless a destination (ciel_workspace/... or agent_output/...) is already mentioned in the query.
- Updated system_ops.py write/append tools to support paths in both ciel_workspace/ and agent_output/.
- Implemented early bypasses (email sends, Vilao input sanitization) and fallback routing to prevent CONTENT_FILTERED errors on legitimate tasks.
- Cleaned backtest/logs/ and unrelated artifacts.
- Softened router prompt language to reduce upstream filter triggers while maintaining routing quality.
- Added graceful degradation when Brain routing is blocked by provider safety.

See architect.md for the full historical roadmap and note.txt for project knowledge guidelines.

## License / Notes

This is an internal research/experimental project. Use at your own risk. The safety mechanisms are configurable but the AI can perform powerful actions when gates are open.

For detailed architecture and instructions for AI assistants working on the code, see the instructionAI/ folder (start with SKILL.md).
