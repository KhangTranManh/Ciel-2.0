import sys
import os
import langchain
from langchain_core.globals import set_verbose, set_debug
from colorama import Fore, Style
from core.agent_loop import AgentLoop
from core.scheduler import CielScheduler

langchain.debug = False
langchain.verbose = False
set_debug(False)
set_verbose(False)


def _voice_capture(lang: str):
    """One voice capture → transcript string, or None. Imports lazily so the CLI
    still starts when audio deps (sounddevice/SpeechRecognition) aren't installed."""
    try:
        from core.voice_input import listen_and_transcribe
    except Exception as e:
        print(Fore.RED + f"[Voice] unavailable: {e}" + Style.RESET_ALL)
        return None
    print(Fore.MAGENTA + "🎤 Listening... (speak now)" + Style.RESET_ALL)
    text = listen_and_transcribe(lang=lang)
    if text is None:
        print(Fore.YELLOW + "[Voice] no speech detected." + Style.RESET_ALL)
    elif text == "":
        print(Fore.YELLOW + "[Voice] heard something but couldn't transcribe it." + Style.RESET_ALL)
    else:
        print(Fore.MAGENTA + f"🗣️  You said: {text}" + Style.RESET_ALL)
    return text or None


def _setup_proactive(ciel, scheduler):
    """TIER 6: wire condition triggers to the CLI + Telegram channels.

    Returns (presence, notifier), or (None, None) when proactivity is off. Any failure
    here is swallowed: Ciel answering normally matters more than Ciel speaking first,
    so a misconfigured trigger degrades to the previous behaviour instead of blocking
    start-up.
    """
    from agent_system import config
    if not config.PROACTIVE_ENABLED or not config.PROACTIVE_TRIGGERS:
        return None, None
    try:
        from core.notifier import Presence, CliChannel, TelegramChannel, Notifier
        from core.triggers import TriggerEngine, build_triggers, parse_price_alerts

        presence = Presence(idle_threshold=config.PROACTIVE_IDLE_SECONDS)
        # Order is priority: the terminal in front of the Master first, Telegram as the
        # channel that still reaches them once they have walked away.
        notifier = Notifier(
            state_path=ciel.core.base_dir / "ciel_data" / "state" / "notify.json",
            channels=[CliChannel(presence), TelegramChannel()],
            daily_budget=config.PROACTIVE_DAILY_BUDGET,
            ask_escalate_seconds=config.PROACTIVE_ASK_ESCALATE_SECONDS,
            repeat_limit=config.PROACTIVE_REPEAT_LIMIT,
        )
        triggers = build_triggers(
            enabled_names=config.PROACTIVE_TRIGGERS,
            task_store=ciel.core.tasks,
            log_path=ciel.core.base_dir / "ciel_data" / "logs" / "thoughts.log",
            notifier=notifier,
            deferred_store=ciel.core.deferred,
            todo_path=ciel.core.base_dir / "ciel_workspace" / "todos.json",
            unfinished_min_age=config.PROACTIVE_UNFINISHED_MIN_AGE,
            cost_usd_limit=config.PROACTIVE_COST_USD_LIMIT,
            cost_token_limit=config.PROACTIVE_COST_TOKEN_LIMIT,
            failure_threshold=config.PROACTIVE_FAILURE_THRESHOLD,
            price_alerts=parse_price_alerts(config.PROACTIVE_PRICE_ALERTS),
            important_senders=config.PROACTIVE_IMPORTANT_SENDERS,
            stale_todo_days=config.PROACTIVE_STALE_TODO_DAYS,
            digest_hour=config.PROACTIVE_DIGEST_HOUR,
            digest_minute=config.PROACTIVE_DIGEST_MINUTE,
        )
        if not triggers:
            print(Fore.YELLOW + f"[Proactive] No known trigger in PROACTIVE_TRIGGERS="
                  f"{','.join(config.PROACTIVE_TRIGGERS)} — nothing enabled." + Style.RESET_ALL)
            return None, None
        scheduler.trigger_engine = TriggerEngine(notifier, triggers,
                                                 logger=ciel.core._log_thought)

        def _mark():
            ciel.core.unattended = True     # thread-local; runs inside the daemon thread
        scheduler.mark_unattended = _mark
        return presence, notifier
    except Exception as e:
        print(Fore.YELLOW + f"[Proactive] disabled: {type(e).__name__}: {e}" + Style.RESET_ALL)
        return None, None


def main():
    print(Fore.CYAN + "Ciel [System]: Core initialization..." + Style.RESET_ALL)
    # Voice mode: `--voice` flag or INPUT_MODE=voice makes speech the default input.
    # Regardless of mode, typing ":v"/":voice" captures one utterance by voice.
    voice_default = ("--voice" in sys.argv) or (os.getenv("INPUT_MODE", "").lower() == "voice")
    stt_lang = os.getenv("STT_LANG", "vi-VN")
    # Voice OUTPUT: `--speak` flag or SPEAK=true makes Ciel read each reply aloud
    # (through the speech normalizer). The printed text transcript is unchanged.
    speak_default = ("--speak" in sys.argv) or (os.getenv("SPEAK", "").lower() in ("true", "1", "yes"))

    def _speak(text: str):
        if not speak_default or not text:
            return
        try:
            from core.speech_output import speak as _tts
            _tts(text)
        except Exception as e:
            print(Fore.RED + f"[Speech] unavailable: {e}" + Style.RESET_ALL)
    try:
        scheduler = CielScheduler()
        
        ciel = AgentLoop()
        scheduler.cleanse_callback = ciel.core._brain_cleanse

        # SAFETY GATE: CLI confirmation handler for high-risk tools.
        # Controlled ONLY by DISABLE_SAFETY_GATE (default OFF = gate active).
        # SAFETY_OPEN is unrelated here — it tunes Brain content-filtering, not tool approval.
        disable_gate = os.getenv("DISABLE_SAFETY_GATE", "false").lower() in ("true", "1", "yes")
        if not disable_gate:
            def _cli_confirm(tool_name: str, preview: str, tool_args: dict) -> bool:
                """Blocking CLI confirmation. `tool_name == "plan"` is the Tier-3
                plan-level prompt: one question covering every step that needs approval,
                asked BEFORE anything runs."""
                header = ("PLAN APPROVAL" if tool_name == "plan"
                          else f"SAFETY CHECK — {tool_name}")
                print(Fore.YELLOW + f"\n⚠️  {header}" + Style.RESET_ALL)
                print(Fore.WHITE + preview + Style.RESET_ALL)
                # "A" = approve and stop asking about this tool for the rest of the run.
                # Offered only for a single tool: blanket-approving a whole plan's worth
                # of tools for the session is exactly the over-broad grant to avoid.
                opts = "Approve? (Y/N): " if tool_name == "plan" else "Approve? (Y/N/A=always this tool): "
                while True:
                    answer = input(Fore.YELLOW + opts + Style.RESET_ALL).strip().lower()
                    if answer in ("y", "yes"):
                        return True
                    if answer in ("n", "no"):
                        return False
                    if answer in ("a", "always") and tool_name != "plan":
                        if ciel.core.permissions.grant_for_session(tool_name):
                            print(Fore.YELLOW + f"[Safety] '{tool_name}' approved for this "
                                  f"session only — never saved to disk." + Style.RESET_ALL)
                            return True
                        print(Fore.RED + f"[Safety] '{tool_name}' is deny-listed; cannot grant."
                              + Style.RESET_ALL)
            ciel.core.confirm_callback = _cli_confirm
        else:
            ciel.core.confirm_callback = lambda n, p, a: True  # auto-approve everything
            print(Fore.YELLOW + "[System] Safety gate open (permissive mode for non-violent categories)" + Style.RESET_ALL)

        # Must precede start_background(): the engine rides the scheduler's daemon thread.
        presence, notifier = _setup_proactive(ciel, scheduler)

        scheduler.start_background()
        print(Fore.BLUE + "Ciel: Online. Awaiting your command, Master." + Style.RESET_ALL)

        # TIER 2: a job that stopped without finishing (crash, closed terminal, or one
        # left waiting on a confirmation) is reported instead of vanishing silently.
        try:
            unfinished = ciel.core.describe_unfinished()
            if unfinished:
                print(Fore.YELLOW + f"[Task] Unfinished from a previous session:\n  {unfinished}"
                      + Style.RESET_ALL)
                print(Fore.YELLOW + "       Ask 'đang làm gì' / 'status' any time for details."
                      + Style.RESET_ALL)
        except Exception:
            pass

        # TIER 6: actions a background run wanted to take but could not, because nobody
        # was here to approve them. Reported at the first moment there IS someone here.
        try:
            waiting = ciel.core.deferred.describe()
            if waiting:
                print(Fore.YELLOW + f"[Chờ duyệt] {len(ciel.core.deferred)} hành động nền "
                      f"đã bị hoãn:\n{waiting}" + Style.RESET_ALL)
                print(Fore.YELLOW + "       Ra lệnh lại nếu vẫn muốn làm — Ciel không tự "
                      "chạy lại lệnh cũ trên dữ liệu đã thay đổi." + Style.RESET_ALL)
        except Exception:
            pass
        if voice_default:
            print(Fore.MAGENTA + f"[Voice] Voice input ON (lang={stt_lang}). "
                  "Press Enter on an empty line to speak, or just type to override." + Style.RESET_ALL)
            # Pre-load the STT model (only matters for the whisper backend) so the first
            # spoken command isn't delayed by model load + first-inference compile.
            try:
                from core.voice_input import warmup as _stt_warmup
                _stt_warmup()
            except Exception:
                pass
        else:
            print(Fore.MAGENTA + "[Voice] Type ':v' to speak a command by voice." + Style.RESET_ALL)
        if speak_default:
            print(Fore.MAGENTA + "[Speech] Voice output ON — Ciel will read replies aloud." + Style.RESET_ALL)
    except Exception as e:
        print(Fore.RED + f"Ciel [Fatal]: {e}" + Style.RESET_ALL)
        sys.exit(1)

    while True:
        try:
            # TIER 6: flush what the trigger engine queued while we sat blocked in
            # input(). Printing here — from this thread, between prompts — is what keeps
            # a proactive message out of the middle of a half-typed line.
            if notifier:
                for line in notifier.drain_cli():
                    print(Fore.YELLOW + "\n" + line + Style.RESET_ALL)

            prompt = "\nMaster (Enter=speak): " if voice_default else "\nMaster: "
            user_input = input(Fore.GREEN + prompt + Style.RESET_ALL).strip()

            # Any keystroke proves the Master is here: it refreshes presence (so the CLI
            # keeps counting as a live channel) and clears pending questions from the
            # escalation queue — they have demonstrably been seen, whether or not the
            # Master chose to answer them.
            if presence:
                presence.touch()
                notifier.ack_seen()

            # Voice triggers: explicit ":v"/":voice" anytime, or empty line in voice mode.
            if user_input.lower() in (":v", ":voice") or (voice_default and user_input == ""):
                spoken = _voice_capture(stt_lang)
                if not spoken:
                    continue
                user_input = spoken

            if user_input.lower() in ['exit', 'quit']:
                print(Fore.BLUE + "Ciel: Entering sleep mode." + Style.RESET_ALL)
                break
            if not user_input:
                continue

            output = ciel.run_step(user_input)
            print(Fore.BLUE + f"Ciel: {output}" + Style.RESET_ALL)
            _speak(output)
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()