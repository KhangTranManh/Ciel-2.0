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
                """Blocking CLI confirmation prompt for destructive tools."""
                print(Fore.YELLOW + f"\n⚠️  SAFETY CHECK — {tool_name}" + Style.RESET_ALL)
                print(Fore.WHITE + preview + Style.RESET_ALL)
                while True:
                    answer = input(Fore.YELLOW + "Approve? (Y/N): " + Style.RESET_ALL).strip().lower()
                    if answer in ("y", "yes"):
                        return True
                    if answer in ("n", "no"):
                        return False
            ciel.core.confirm_callback = _cli_confirm
        else:
            ciel.core.confirm_callback = lambda n, p, a: True  # auto-approve everything
            print(Fore.YELLOW + "[System] Safety gate open (permissive mode for non-violent categories)" + Style.RESET_ALL)

        scheduler.start_background()
        print(Fore.BLUE + "Ciel: Online. Awaiting your command, Master." + Style.RESET_ALL)
        if voice_default:
            print(Fore.MAGENTA + f"[Voice] Voice input ON (lang={stt_lang}). "
                  "Press Enter on an empty line to speak, or just type to override." + Style.RESET_ALL)
        else:
            print(Fore.MAGENTA + "[Voice] Type ':v' to speak a command by voice." + Style.RESET_ALL)
        if speak_default:
            print(Fore.MAGENTA + "[Speech] Voice output ON — Ciel will read replies aloud." + Style.RESET_ALL)
    except Exception as e:
        print(Fore.RED + f"Ciel [Fatal]: {e}" + Style.RESET_ALL)
        sys.exit(1)

    while True:
        try:
            prompt = "\nMaster (Enter=speak): " if voice_default else "\nMaster: "
            user_input = input(Fore.GREEN + prompt + Style.RESET_ALL).strip()

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