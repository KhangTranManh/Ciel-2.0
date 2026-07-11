"""Text-to-speech for the CLI (main.py) — Ciel speaks its replies.

The other half of the voice seam (STT is core/voice_input.py). Design principle,
decided deliberately: DO NOT dumb down the model's prompts/persona to be
speech-friendly. The text UI, the HUD transcript, and outbound EMAIL all want the
rich formatting (bold, headers, [COGNITION]/[NETWORK_SCAN] tags, bullets). Voice is
just one more output modality, so it gets its OWN deterministic normalizer —
`to_speech()` — exactly like outbound email gets `_sanitize_outbound_email()`. The
transcript keeps every character; only what's handed to the TTS engine is cleaned.

Backends are swappable via TTS_BACKEND (default "edge"):
    edge    — edge-tts, Microsoft neural voices. Free, no API key, excellent
              Vietnamese (vi-VN-HoaiMyNeural / vi-VN-NamMinhNeural). Needs internet.
    pyttsx3 — offline system voices (Windows SAPI5). No net, but Vietnamese voices
              are usually absent/poor — fine for English or offline fallback.

Playback of edge-tts MP3 uses the built-in Windows MCI (winmm.dll) via ctypes, so no
extra audio-playback package is required.

Run STANDALONE to test the voice before touching the agent:

    python -m core.speech_output "Xin chào Master, thị trường vàng đang tăng."
    python -m core.speech_output --voice vi-VN-NamMinhNeural "Chào buổi sáng."
    python -m core.speech_output --raw "**bold** 🧠 [COGNITION] test"   # hear it WITHOUT normalizing
    echo "some text" | python -m core.speech_output
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

from dotenv import load_dotenv

# Load .env so TTS_*/RVC_* are honored in EVERY entry path — both `python main.py`
# and the standalone `python -m core.speech_output` tester. Without this, the
# standalone tester would silently ignore .env and use the hardcoded defaults below.
load_dotenv()

DEFAULT_VOICE = os.getenv("TTS_VOICE", "vi-VN-HoaiMyNeural")

# edge-tts prosody knobs — customizable via .env with zero code changes. Formats are
# edge-tts's own: rate/volume as signed percent ("+10%", "-15%"), pitch as signed Hz
# ("+5Hz", "-10Hz"). Vietnamese neural voices: vi-VN-HoaiMyNeural (F), vi-VN-NamMinhNeural (M).
DEFAULT_RATE = os.getenv("TTS_RATE", "+0%")
DEFAULT_VOLUME = os.getenv("TTS_VOLUME", "+0%")
DEFAULT_PITCH = os.getenv("TTS_PITCH", "+0Hz")


# ── Normalizer: display text -> speakable text ──────────────────────────────

# Broad emoji / pictograph ranges — enough to drop the persona's 🧠🔍 headers and
# any stray symbols without pulling in a dependency.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols, pictographs, emoji
    "\U00002600-\U000027BF"  # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U00002190-\U000021FF"  # arrows
    "]+",
    flags=re.UNICODE,
)


def to_speech(text: str) -> str:
    """Convert Ciel's rich display text into something a TTS engine reads cleanly.

    Deterministic and lossy BY DESIGN — the original text is untouched; this is only
    what the speaker consumes. Removes: fenced code blocks, inline-code backticks,
    markdown emphasis/headers/bullets, bracketed ALL-CAPS tags ([COGNITION] …),
    emojis, and bare URLs. Preserves words, Vietnamese diacritics, and sentence
    punctuation so the neural voice keeps natural prosody.
    """
    if not text:
        return ""
    t = text

    # Fenced code blocks -> spoken placeholder (reading code aloud is noise).
    t = re.sub(r"```.*?```", " (đoạn mã) ", t, flags=re.DOTALL)
    # Inline code: keep the words, drop the backticks.
    t = t.replace("`", "")
    # Bracketed ALL-CAPS internal tags: [COGNITION], [NETWORK_SCAN], [TOOL_RESULT]...
    t = re.sub(r"\[[A-Z0-9_ ]{2,}\]", " ", t)
    # Bare URLs -> a short spoken token instead of reading the whole address.
    t = re.sub(r"https?://\S+", " liên kết ", t)
    # Markdown emphasis / inline markers.
    t = re.sub(r"[*_]{1,3}", "", t)
    # Header hashes and blockquote markers at line starts.
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", t)
    t = re.sub(r"(?m)^\s{0,3}>\s?", "", t)
    # Bullet / list markers at line starts ("- ", "* ", "1. ").
    t = re.sub(r"(?m)^\s*[-*•]\s+", "", t)
    t = re.sub(r"(?m)^\s*\d+\.\s+", "", t)
    # Emojis and stray pictographs.
    t = _EMOJI_RE.sub(" ", t)
    # Table pipes and leftover markdown punctuation that reads badly.
    t = t.replace("|", " ")
    # Collapse whitespace; keep newlines as sentence breaks (period for a pause).
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", ". ", t)
    t = t.replace("\n", ". ")
    t = re.sub(r"\.\s*\.", ".", t)
    return t.strip()


# ── Playback ────────────────────────────────────────────────────────────────

def _play_audio_file(path: str) -> None:
    """Play an audio file and block until it finishes. Windows uses the built-in MCI
    (winmm) — no extra package. Other OSes try a common CLI player."""
    if sys.platform.startswith("win"):
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        alias = "ciel_tts"
        # mpegvideo device handles MP3 on Windows.
        mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, None)
        try:
            mci(f"play {alias} wait", None, 0, None)
        finally:
            mci(f"close {alias}", None, 0, None)
        return
    # macOS / Linux best-effort.
    import shutil
    import subprocess
    for player in ("afplay", "aplay", "mpg123", "ffplay"):
        exe = shutil.which(player)
        if exe:
            args = [exe, path] if player != "ffplay" else [exe, "-nodisp", "-autoexit", path]
            subprocess.run(args, check=False)
            return
    print(f"[Speech] No audio player found; file saved at {path}")


# ── Backends ────────────────────────────────────────────────────────────────

def _speak_edge(text: str, voice: str) -> None:
    import asyncio
    import edge_tts

    tmp = tempfile.NamedTemporaryFile(prefix="ciel_tts_", suffix=".mp3", delete=False)
    tmp.close()
    try:
        async def _synth():
            com = edge_tts.Communicate(text, voice, rate=DEFAULT_RATE,
                                       volume=DEFAULT_VOLUME, pitch=DEFAULT_PITCH)
            await com.save(tmp.name)
        asyncio.run(_synth())
        _play_audio_file(tmp.name)
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


def _speak_pyttsx3(text: str, voice: str) -> None:
    import pyttsx3
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()


# ── RVC character voice via a Hugging Face Space (experimental) ──────────────
# mikuTTS = edge-tts (base speech) -> RVC voice conversion (Hatsune Miku timbre).
# This backend calls the Space's Gradio /tts endpoint. It is EXPERIMENTAL: a free
# shared Space can be asleep, slow, rate-limited, or change its API — not suitable as
# Ciel's default voice. The base language is still edge-tts, so Vietnamese words are
# spoken by vi-VN then re-timbred (may sound odd, since the models are trained on
# Japanese/English). All knobs are env-configurable.
_SPACE_CLIENT = None  # cached gradio_client.Client (connecting is slow)

# edge voice name -> mikuTTS's "<voice>-<Gender>" dropdown value.
_RVC_VOICE_GENDER = {
    "vi-VN-HoaiMyNeural": "vi-VN-HoaiMyNeural-Female",
    "vi-VN-NamMinhNeural": "vi-VN-NamMinhNeural-Male",
}


def _rvc_synthesize(text: str, voice: str) -> str:
    """Send text to the mikuTTS Space, return a local path to the converted audio."""
    global _SPACE_CLIENT
    from gradio_client import Client

    space = os.getenv("RVC_SPACE", "John6666/mikuTTS")
    if _SPACE_CLIENT is None:
        _SPACE_CLIENT = Client(space)

    # Resolve the tts_voice dropdown value: explicit env wins, else map the edge voice.
    tts_voice = os.getenv("RVC_TTS_VOICE") or _RVC_VOICE_GENDER.get(voice, "vi-VN-HoaiMyNeural-Female")

    result = _SPACE_CLIENT.predict(
        os.getenv("RVC_MODEL", "1a_miku_default_rvc_(aple)"),  # model_name
        0,                                                     # speed
        0,                                                     # volume
        0,                                                     # pitch
        text,                                                  # tts_text
        tts_voice,                                             # tts_voice
        int(os.getenv("RVC_F0_UP", "0")),                      # f0_up_key (transpose)
        os.getenv("RVC_F0_METHOD", "rmvpe"),                   # f0_method
        float(os.getenv("RVC_INDEX_RATE", "0.75")),            # index_rate
        float(os.getenv("RVC_PROTECT", "0.33")),               # protect
        api_name="/tts",
    )
    # Returns (output_info, edge_voice_audio, converted_audio). Take the converted one.
    audio = result[2] if isinstance(result, (list, tuple)) and len(result) >= 3 else result
    if isinstance(audio, dict):          # some gradio versions wrap files in a dict
        audio = audio.get("value") or audio.get("path") or audio.get("name")
    if not audio or not os.path.exists(audio):
        raise RuntimeError(f"Space returned no playable audio (got: {audio!r}).")
    return audio


def _speak_space(text: str, voice: str) -> None:
    _play_audio_file(_rvc_synthesize(text, voice))


_BACKENDS = {
    "edge": _speak_edge,
    "pyttsx3": _speak_pyttsx3,
    "space": _speak_space,
    "rvc": _speak_space,  # alias
}


def synth_to_file(text: str, path: str, voice: str | None = None,
                  normalize: bool = True) -> str:
    """edge-tts only: render speech to an MP3 file (used by tests / callers that want
    the audio without playing it). Returns the path written, or "" if nothing to say."""
    import asyncio
    import edge_tts

    spoken = to_speech(text) if normalize else text
    if not spoken:
        return ""

    async def _synth():
        com = edge_tts.Communicate(spoken, voice or DEFAULT_VOICE, rate=DEFAULT_RATE,
                                   volume=DEFAULT_VOLUME, pitch=DEFAULT_PITCH)
        await com.save(path)
    asyncio.run(_synth())
    return path


def speak(text: str, voice: str | None = None, backend: str | None = None,
          normalize: bool = True) -> bool:
    """Speak text aloud (blocking). Returns True if something was spoken.

    `normalize=True` runs `to_speech()` first (the normal path). Set False only to
    hear the raw text for comparison. Never raises — a TTS failure just prints and
    returns False so it can't break the agent loop.
    """
    spoken = to_speech(text) if normalize else (text or "")
    if not spoken.strip():
        return False
    backend = (backend or os.getenv("TTS_BACKEND", "edge")).lower()
    fn = _BACKENDS.get(backend)
    if fn is None:
        print(f"[Speech] Unknown TTS_BACKEND '{backend}'. Choose: {', '.join(_BACKENDS)}.")
        return False
    try:
        fn(spoken, voice or DEFAULT_VOICE)
        return True
    except Exception as e:
        print(f"[Speech] TTS failed ({backend}): {e}")
        return False


# ── Standalone tester ───────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Standalone text-to-speech tester for Ciel.")
    ap.add_argument("text", nargs="*", help="Text to speak (or pipe via stdin).")
    ap.add_argument("--voice", default=DEFAULT_VOICE, help=f"Voice name (default {DEFAULT_VOICE}).")
    ap.add_argument("--backend", default=None, help="edge | pyttsx3 (default: TTS_BACKEND or edge).")
    ap.add_argument("--raw", action="store_true", help="Speak WITHOUT normalizing (hear the raw markdown/tags).")
    ap.add_argument("--show", action="store_true", help="Print the normalized text without speaking.")
    args = ap.parse_args()

    text = " ".join(args.text).strip() or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
    if not text:
        text = "Xin chào Master. Đây là bài kiểm tra giọng nói của Ciel."

    if args.show:
        print("RAW      :", text)
        print("NORMALIZED:", to_speech(text))
        return

    print(f"[TTS tester] backend={(args.backend or os.getenv('TTS_BACKEND','edge'))}  voice={args.voice}")
    if not args.raw:
        print(f"[speaking] {to_speech(text)}")
    ok = speak(text, voice=args.voice, backend=args.backend, normalize=not args.raw)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    _main()
