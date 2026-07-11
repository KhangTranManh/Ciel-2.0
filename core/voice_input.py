"""Speech-to-text for the CLI (main.py) — the first voice test on the Python side.

Mirrors the UI's "voice seam" idea (ui/src/io/input/VoiceInput.tsx): capture speech,
turn it into text, then feed that text into the SAME pipeline the keyboard uses
(`AgentLoop.run_step`). Nothing downstream of the transcript knows or cares that the
input arrived by voice.

Design choices for a first, low-friction test:
  - Capture: `sounddevice` (bundles PortAudio; installs cleanly on Windows, unlike
    PyAudio — no compiler needed). No mic library is imported until you actually listen.
  - Transcription is SWAPPABLE via STT_BACKEND (default "google"):
      google  — SpeechRecognition's free Google Web Speech endpoint. No API key,
                supports vi-VN, good enough to judge "does STT work at all". Needs net.
      whisper — faster-whisper if installed (offline, best Vietnamese accuracy).
      gemini  — Gemini via google.genai if GEMINI_API_KEY is set (same client vision uses).
    Unavailable backends fail with a clear message rather than a stack trace.

Run it STANDALONE to test speech-to-text on its own, before touching the whole agent:

    python -m core.voice_input                 # one capture, prints the transcript
    python -m core.voice_input --lang en-US
    STT_BACKEND=whisper python -m core.voice_input
"""
from __future__ import annotations

import os
import sys
import time
import queue

from dotenv import load_dotenv

# Load .env so STT_* are honored both via `python main.py` and the standalone
# `python -m core.voice_input` tester (which otherwise wouldn't read .env).
load_dotenv()

SAMPLE_RATE = 16000  # 16 kHz mono — what every STT backend here expects.


# ── Availability ────────────────────────────────────────────────────────────

def is_available() -> tuple[bool, str]:
    """(True, "") if we can at least capture audio; else (False, reason)."""
    try:
        import sounddevice  # noqa: F401
    except Exception as e:
        return False, f"sounddevice not available ({e}). Voice input disabled."
    return True, ""


# ── Capture ─────────────────────────────────────────────────────────────────

def record_until_silence(max_seconds: float = 15.0,
                         silence_secs: float = 1.3,
                         start_timeout: float = 6.0):
    """Record mono 16-bit audio from the default mic until the speaker goes quiet.

    Endpointing is a simple energy gate: calibrate ambient noise for ~0.3s, then
    treat blocks above ~3x ambient as speech. Stop after `silence_secs` of quiet
    once speech has started, or after `max_seconds` total, or return None if nothing
    is said within `start_timeout`.

    Returns an int16 numpy array (mono) or None. Never raises for "no speech".
    """
    import numpy as np
    import sounddevice as sd

    block = int(0.1 * SAMPLE_RATE)  # 100 ms blocks
    q: "queue.Queue" = queue.Queue()

    def _cb(indata, frames, time_info, status):  # noqa: ANN001
        q.put(indata.copy())

    def _rms(a) -> float:
        a = a.astype(np.float32)
        return float(np.sqrt(np.mean(a * a)) + 1e-9)

    frames = []
    started = False
    silence_run = 0.0
    elapsed = 0.0

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=block, callback=_cb):
        # Ambient calibration (~0.3s) to set the speech threshold.
        ambient = []
        t0 = time.time()
        while time.time() - t0 < 0.3:
            try:
                ambient.append(q.get(timeout=0.5))
            except queue.Empty:
                break
        amb = _rms(np.concatenate(ambient)) if ambient else 60.0
        threshold = max(amb * 3.0, 300.0)

        start_clock = time.time()
        while True:
            try:
                chunk = q.get(timeout=1.0)
            except queue.Empty:
                chunk = None

            if chunk is not None:
                frames.append(chunk)
                level = _rms(chunk)
                dur = len(chunk) / SAMPLE_RATE
                elapsed += dur
                if level >= threshold:
                    started = True
                    silence_run = 0.0
                elif started:
                    silence_run += dur
                if started and silence_run >= silence_secs:
                    break

            if not started and (time.time() - start_clock) > start_timeout:
                return None  # nothing spoken in time
            if elapsed >= max_seconds:
                break

    if not frames:
        return None
    return np.concatenate(frames)


# ── Transcription backends ──────────────────────────────────────────────────

def _transcribe_google(samples, lang: str) -> str:
    import speech_recognition as sr_lib
    rec = sr_lib.Recognizer()
    audio = sr_lib.AudioData(samples.tobytes(), SAMPLE_RATE, 2)  # 2 bytes/sample (int16)
    try:
        return rec.recognize_google(audio, language=lang).strip()
    except sr_lib.UnknownValueError:
        return ""  # speech present but not understood
    except sr_lib.RequestError as e:
        raise RuntimeError(f"Google STT request failed: {e}")


def _transcribe_whisper(samples, lang: str) -> str:
    from faster_whisper import WhisperModel
    import numpy as np
    global _WHISPER
    try:
        _WHISPER
    except NameError:
        _WHISPER = None
    if _WHISPER is None:
        model_size = os.getenv("WHISPER_MODEL", "small")
        _WHISPER = WhisperModel(model_size, device="cpu", compute_type="int8")
    audio = samples.astype(np.float32) / 32768.0
    wlang = lang.split("-")[0] if lang else None  # "vi-VN" -> "vi"
    segments, _ = _WHISPER.transcribe(audio, language=wlang)
    return " ".join(seg.text for seg in segments).strip()


def _transcribe_gemini(samples, lang: str) -> str:
    import io
    import wave
    from google import genai
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set — cannot use the gemini STT backend.")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    client = genai.Client(api_key=api_key)
    prompt = ("Transcribe this audio verbatim. Return ONLY the spoken words, no "
              "commentary, no timestamps. The language may be Vietnamese or English.")
    resp = client.models.generate_content(
        model=os.getenv("STT_GEMINI_MODEL", "gemini-2.5-flash"),
        contents=[prompt, genai.types.Part.from_bytes(data=buf.getvalue(), mime_type="audio/wav")],
    )
    return (resp.text or "").strip()


_BACKENDS = {
    "google": _transcribe_google,
    "whisper": _transcribe_whisper,
    "gemini": _transcribe_gemini,
}


def transcribe(samples, lang: str = "vi-VN", backend: str | None = None) -> str:
    """Turn captured int16 samples into text using the chosen backend."""
    backend = (backend or os.getenv("STT_BACKEND", "google")).lower()
    fn = _BACKENDS.get(backend)
    if fn is None:
        raise RuntimeError(f"Unknown STT_BACKEND '{backend}'. Choose: {', '.join(_BACKENDS)}.")
    if samples is None or len(samples) == 0:
        return ""
    return fn(samples, lang)


def listen_and_transcribe(lang: str = "vi-VN", backend: str | None = None,
                          max_seconds: float = 15.0) -> str | None:
    """Record one utterance and return its transcript.

    Returns:
      - the transcript string on success,
      - "" if audio was captured but not understood,
      - None if nothing was recorded (silence/timeout) or capture failed.
    """
    ok, reason = is_available()
    if not ok:
        print(f"[Voice] {reason}")
        return None
    try:
        samples = record_until_silence(max_seconds=max_seconds)
    except Exception as e:
        print(f"[Voice] Microphone capture failed: {e}")
        return None
    if samples is None:
        return None
    try:
        return transcribe(samples, lang=lang, backend=backend)
    except Exception as e:
        print(f"[Voice] Transcription failed: {e}")
        return None


# ── Standalone tester ───────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Standalone speech-to-text tester for Ciel.")
    ap.add_argument("--lang", default="vi-VN", help="BCP-47 language (default vi-VN).")
    ap.add_argument("--backend", default=None, help="google | whisper | gemini (default: STT_BACKEND or google).")
    ap.add_argument("--loop", action="store_true", help="Keep listening until Ctrl+C.")
    args = ap.parse_args()

    ok, reason = is_available()
    if not ok:
        print(reason)
        sys.exit(1)

    backend = (args.backend or os.getenv("STT_BACKEND", "google")).lower()
    print(f"[Voice tester] backend={backend}  lang={args.lang}")
    print("Speak after the prompt. (Ctrl+C to quit.)")
    while True:
        try:
            print("\n🎤 Listening...", flush=True)
            t0 = time.time()
            text = listen_and_transcribe(lang=args.lang, backend=backend)
            dt = time.time() - t0
            if text is None:
                print("   (no speech detected)")
            elif text == "":
                print("   (heard something, but couldn't transcribe it)")
            else:
                print(f"   -> \"{text}\"   [{dt:.1f}s]")
            if not args.loop:
                break
        except KeyboardInterrupt:
            print("\n[Voice tester] Done.")
            break


if __name__ == "__main__":
    _main()
