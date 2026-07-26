"""user_model.py — Tier-7a: what Ciel knows about the Master, and what it may say back.

WHAT THIS FIXES
---------------
A fact vault already exists (`skills/internal/memory_ops.py` + `ciel_data/facts.json`)
and after months of development it is still `{}` — empty. That is not an accident, it is
the design: the vault is **pull-only**. Nothing injects it into context, so recalling a
preference requires the model to guess an exact snake_case key AND choose to call
`get_fact`; and writing requires the Master to say "remember this" out loud. A memory
that only works when someone remembers to use it is not memory.

This module is the push side: a small, bounded profile that goes INTO context on its own,
so Ciel behaves like something that knows you rather than something you have to re-brief.

WHY IT IS A SEPARATE STORE FROM facts.json
------------------------------------------
`memory_ops`' own prompt invites the Master to store `wifi_password` there. The moment a
store is auto-injected into every prompt, everything in it is sent to the provider on
every call — including third-party gateways. So the two stores are split by what happens
to them, not by what they contain:

    facts.json        secrets, credentials   pull-only, NEVER injected
    user_model.json   preferences, profile   injected, and refuses to hold credentials

The refusal is deterministic (`looks_like_secret`), not a request for the model to be
careful.

THE THREE RULES THAT KEEP A PROFILE FROM ROTTING
------------------------------------------------
1. **Authority.** What the Master *said* outranks what Ciel *inferred*. A lower-authority
   write can never silently overwrite a higher-authority one — otherwise one bad
   inference quietly replaces a real instruction and there is no way to notice.
2. **Decay.** `stated` traits never fade. Inferred and observed ones lose confidence with
   age unless re-observed, so "I'm busy today" cannot harden into a permanent trait.
3. **A hard token ceiling.** This text is a FIXED tax on every call that carries it, so
   `render()` takes a budget and truncates. Tier 4 exists because 99% of a Brain call is
   already fixed overhead; a profile that grows without bound would make that worse
   forever.

DELIBERATELY NOT HERE
---------------------
No LLM. This is the store, the decay policy and the read path — all plain Python. The
write path that *notices* a preference without being told is Tier 7b; it will call
`remember()` exactly like anything else, behind its own deterministic gate.
"""
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field, asdict


# --- kinds, in ascending authority ------------------------------------------------
OBSERVED = "observed"       # Ciel noticed a repeated behaviour
INFERRED = "inferred"       # Ciel concluded it from something the Master said
STATED = "stated"           # the Master said it outright

_AUTHORITY = {OBSERVED: 1, INFERRED: 2, STATED: 3}

# Defaults per kind: (starting confidence, half-life in days; 0 = never decays).
_KIND_DEFAULTS = {
    STATED:   (0.95, 0.0),
    INFERRED: (0.60, 30.0),
    OBSERVED: (0.40, 14.0),
}

_MIN_CONFIDENCE = 0.15      # below this a trait stops being rendered (it can recover)
_MAX_VALUE_CHARS = 160
_MAX_HISTORY = 5
_MAX_TRAITS = 60            # a profile, not a database
_DAY = 86400.0

_KEY_CLEAN_RE = re.compile(r"[^a-z0-9_]+")

# Anything matching either pattern belongs in facts.json, never in an injected profile.
# Short words are anchored with \b so `pin` does not match `shopping`; separators in the
# key are normalised to spaces FIRST, because `_` and `-` are word characters to `\b` —
# without that step `card_cvv` and `bank_pin` sail straight through.
_SECRET_KEY_RE = re.compile(
    r"pass(word|wd|code)?|\bpwd\b|token|api ?key|access ?key|private ?key|secret|"
    r"credential|\botp\b|\bcvv\b|\bcvc\b|\bpin\b|\bmfa\b|\b2fa\b|seed ?phrase|auth",
    re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(
    r"^(sk-|ghp_|gho_|github_pat_|xox[baprs]-|AIza|ya29\.)"          # known key prefixes
    r"|^[A-Za-z0-9+/]{40,}={0,2}$"                                   # long base64 blob
    r"|^[0-9a-fA-F]{32,}$")                                          # long hex blob
_SEPARATORS_RE = re.compile(r"[_\-.\s]+")


def looks_like_secret(key: str, value: str) -> bool:
    """True if this pair should go to the credential vault instead.

    Checked on BOTH sides, because the two failure modes are different: a key literally
    called `api_key` is obvious, while an innocently-named key holding a real token is
    the one that would otherwise be injected into every prompt. Only the value test
    catches the second.
    """
    flat = _SEPARATORS_RE.sub(" ", (key or "").lower())
    return bool(_SECRET_KEY_RE.search(flat) or _SECRET_VALUE_RE.match((value or "").strip()))


# --- token accounting ---------------------------------------------------------------
_encoder = [None]           # cached; None = not tried, False = unavailable


def estimate_tokens(text: str) -> int:
    """Token count for `text`, exact when tiktoken is installed, over-estimated when not.

    The fallback divides by 3, not the usual 4, on purpose: this codebase is driven in
    Vietnamese, which packs fewer characters per token than English. An over-estimate
    spends a little of the budget unnecessarily; an under-estimate breaks the ceiling
    the whole design rests on.
    """
    if not text:
        return 0
    if _encoder[0] is None:
        try:
            import tiktoken
            _encoder[0] = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoder[0] = False
    if _encoder[0]:
        try:
            return len(_encoder[0].encode(text))
        except Exception:
            pass
    return int(math.ceil(len(text) / 3))


# --- the record ---------------------------------------------------------------------

@dataclass
class Trait:
    key: str
    value: str
    kind: str = STATED
    confidence: float = 0.95
    half_life_days: float = 0.0
    source: str = ""                    # short provenance, shown by explain()
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    hits: int = 1
    history: list = field(default_factory=list)     # [{"value":…, "until": ts}]

    def authority(self) -> int:
        return _AUTHORITY.get(self.kind, 1)

    def effective_confidence(self, now: float) -> float:
        """Confidence after decay. What the Master stated does not fade with time."""
        if self.half_life_days <= 0:
            return self.confidence
        age_days = max(0.0, (now - self.last_seen) / _DAY)
        return self.confidence * (0.5 ** (age_days / self.half_life_days))

    def is_live(self, now: float) -> bool:
        return self.effective_confidence(now) >= _MIN_CONFIDENCE

    def line(self) -> str:
        return f"- {self.key}: {self.value}"


@dataclass
class WriteResult:
    stored: bool
    reason: str = ""
    trait: Trait = None


def _day_of(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def normalize_key(key: str) -> str:
    """Fold anything the caller passes into one canonical snake_case key.

    Collapsing runs matters more than it looks: `preferred_language` and
    `preferred___language` would otherwise be two separate traits, and the whole
    contradiction/authority mechanism only works when the same idea lands on one key.
    """
    k = _KEY_CLEAN_RE.sub("_", (key or "").strip().lower())
    return re.sub(r"_{2,}", "_", k).strip("_")[:64]


# --- the store ----------------------------------------------------------------------

class UserModel:
    """The Master's profile. Thread-safe; every public method is total (never raises).

    The file is written indented and unescaped on purpose. This is a description of a
    real person, so it must be something they can open, read, correct and delete by hand
    — a profile you cannot audit is a profile you cannot trust.
    """

    def __init__(self, path=None, max_traits: int = _MAX_TRAITS):
        self.path = path
        self.max_traits = max_traits
        self._lock = threading.RLock()
        self._traits = {}
        # (day, calls) — the Tier-7b extraction allowance, so a chatty session cannot
        # multiply the cost of learning without bound.
        self._extractions = ("", 0)
        self._load()

    # ---------------------------------------------------------------- io
    def _load(self):
        try:
            if not self.path or not self.path.exists():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for k, d in (raw.get("traits") or {}).items():
                if not isinstance(d, dict):
                    continue
                d.pop("key", None)
                self._traits[k] = Trait(key=k, **{
                    f: d[f] for f in ("value", "kind", "confidence", "half_life_days",
                                      "source", "first_seen", "last_seen", "hits", "history")
                    if f in d})
            ex = raw.get("extractions")
            if isinstance(ex, (list, tuple)) and len(ex) == 2:
                self._extractions = (str(ex[0]), int(ex[1]))
        except Exception:
            self._traits = {}       # a corrupt profile must never block start-up
            self._extractions = ("", 0)

    def _save_locked(self):
        try:
            if not self.path:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"traits": {k: {kk: vv for kk, vv in asdict(t).items() if kk != "key"}
                                  for k, t in self._traits.items()},
                       "extractions": list(self._extractions)}
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except Exception:
            pass                    # best-effort, exactly as TaskStore and Notifier

    # ------------------------------------------------------------- writing
    def remember(self, key: str, value: str, kind: str = STATED, source: str = "",
                 now: float = None) -> WriteResult:
        """Record or update one trait. Returns why, when it declines."""
        now = time.time() if now is None else now
        key = normalize_key(key)
        value = (value or "").strip().replace("\n", " ")[:_MAX_VALUE_CHARS]
        if not key or not value:
            return WriteResult(False, "empty key or value")
        if kind not in _AUTHORITY:
            kind = OBSERVED
        if looks_like_secret(key, value):
            return WriteResult(False, "looks like a credential — belongs in facts.json "
                                      "(save_fact), which is never injected into prompts")

        conf, half_life = _KIND_DEFAULTS[kind]
        with self._lock:
            old = self._traits.get(key)

            if old is None:
                t = Trait(key=key, value=value, kind=kind, confidence=conf,
                          half_life_days=half_life, source=source,
                          first_seen=now, last_seen=now, hits=1)
                self._traits[key] = t
                self._evict_locked(now)
                self._save_locked()
                return WriteResult(True, "new", t)

            if old.value == value:
                # Reinforcement: seeing the same thing again is evidence, and it resets
                # the decay clock. Confidence rises, but a re-observation can never
                # promote a guess to the authority of something the Master actually said.
                old.hits += 1
                old.last_seen = now
                old.confidence = min(1.0, max(old.confidence, conf) + 0.05)
                if _AUTHORITY[kind] > old.authority():
                    old.kind, old.half_life_days = kind, half_life
                    old.source = source or old.source
                self._save_locked()
                return WriteResult(True, "reinforced", old)

            # Contradiction. Authority decides who wins, so one bad inference cannot
            # quietly replace an instruction the Master gave in words.
            if _AUTHORITY[kind] < old.authority() and old.is_live(now):
                return WriteResult(False,
                                   f"'{key}' was {old.kind} ('{old.value}'); a {kind} "
                                   f"guess cannot overwrite it")
            old.history.insert(0, {"value": old.value, "until": now, "kind": old.kind})
            del old.history[_MAX_HISTORY:]
            old.value, old.kind = value, kind
            old.confidence, old.half_life_days = conf, half_life
            old.source, old.last_seen, old.hits = source, now, 1
            self._save_locked()
            return WriteResult(True, "replaced", old)

    def _evict_locked(self, now: float):
        """Keep the profile small by dropping the weakest traits, not the oldest."""
        if len(self._traits) <= self.max_traits:
            return
        ranked = sorted(self._traits.values(),
                        key=lambda t: (t.effective_confidence(now), t.last_seen))
        for t in ranked[: len(self._traits) - self.max_traits]:
            self._traits.pop(t.key, None)

    def forget(self, key: str) -> bool:
        """Actually delete. A profile you cannot erase is not one you consented to."""
        with self._lock:
            gone = self._traits.pop(normalize_key(key), None) is not None
            if gone:
                self._save_locked()
            return gone

    def forget_all(self) -> int:
        with self._lock:
            n = len(self._traits)
            self._traits.clear()
            self._save_locked()
            return n

    def prune(self, now: float = None) -> int:
        """Drop traits that have decayed past usefulness. Never touches `stated` ones."""
        now = time.time() if now is None else now
        with self._lock:
            dead = [k for k, t in self._traits.items()
                    if t.half_life_days > 0 and not t.is_live(now)]
            for k in dead:
                self._traits.pop(k, None)
            if dead:
                self._save_locked()
            return len(dead)

    # ------------------------------------------------------------- reading
    def live_traits(self, now: float = None) -> list:
        """Traits worth showing, strongest first."""
        now = time.time() if now is None else now
        with self._lock:
            live = [t for t in self._traits.values() if t.is_live(now)]
        return sorted(live, key=lambda t: (-t.effective_confidence(now), -t.last_seen, t.key))

    def render(self, now: float = None, budget_tokens: int = 250,
               max_items: int = 12) -> str:
        """The block that goes into a prompt, or "" when there is nothing worth saying.

        Returning an empty string on an empty profile matters: the caller skips the
        injection entirely, so this feature costs exactly zero tokens until it has
        something to contribute.
        """
        now = time.time() if now is None else now
        traits = self.live_traits(now)[:max_items]
        if not traits:
            return ""
        header = ("[MASTER PROFILE] Ciel đã học được những điều sau. Đây là nền, KHÔNG "
                  "phải mệnh lệnh: nếu yêu cầu hiện tại mâu thuẫn, luôn theo yêu cầu hiện tại.")
        out, used = [header], estimate_tokens(header)
        for t in traits:
            line = t.line()
            cost = estimate_tokens(line) + 1
            if used + cost > budget_tokens:
                break
            out.append(line)
            used += cost
        if len(out) == 1:           # the header alone helps nobody
            return ""
        return "\n".join(out)

    def explain(self, key: str, now: float = None) -> str:
        """Provenance for one trait — "why do you think that?" answered honestly."""
        now = time.time() if now is None else now
        with self._lock:
            t = self._traits.get(normalize_key(key))
        if not t:
            return f"Chưa có ghi nhận nào về '{key}'."
        age_days = (now - t.last_seen) / _DAY
        bits = [f"'{t.key}' = {t.value}",
                f"nguồn: {t.kind}" + (f" ({t.source})" if t.source else ""),
                f"độ tin: {t.effective_confidence(now):.2f}"
                + (f" (gốc {t.confidence:.2f}, đã {age_days:.1f} ngày)"
                   if t.half_life_days > 0 else " (Master nói trực tiếp, không phai)"),
                f"gặp lại {t.hits} lần"]
        if t.history:
            prev = ", ".join(h.get("value", "?") for h in t.history[:3])
            bits.append(f"trước đó là: {prev}")
        return " · ".join(bits)

    # ------------------------------------------------------- extraction budget
    def can_extract(self, now: float, daily_limit: int) -> bool:
        """Has today's allowance of extraction calls been used up?"""
        if daily_limit <= 0:
            return False
        with self._lock:
            day, used = self._extractions
            return used < daily_limit or day != _day_of(now)

    def note_extraction(self, now: float):
        with self._lock:
            day, used = self._extractions
            today = _day_of(now)
            self._extractions = (today, (used + 1) if day == today else 1)
            self._save_locked()

    def __len__(self):
        with self._lock:
            return len(self._traits)


# ══════════════════════════════════════════════════════════════════════════════
#  TIER 7b — noticing a preference without being told
# ══════════════════════════════════════════════════════════════════════════════
#
# The gate below is the whole reason this is affordable. `save_fact` never fires
# organically because it depends on the Master remembering to ask; asking the model
# "was there a preference in that?" on every turn would fix that and cost a call per
# turn forever. So the same shape as ContinuationPolicy.assess(): free Python decides
# whether a turn could plausibly contain a durable preference, and only then is one
# extraction call spent. An ordinary turn pays nothing.
#
# Python also decides the KIND. Letting the model self-report confidence would make the
# authority rule meaningless — it would simply claim `stated` and overwrite anything.

# "Do it this way from now on" — a rule, not a request.
_DURABLE_RE = re.compile(
    r"từ giờ|từ nay|kể từ|lần sau|về sau|luôn luôn|\bluôn\b|đừng bao giờ|"
    r"không bao giờ|mặc định|nhớ là|nhớ giùm|nhớ cho|quy tắc|"
    r"from now on|always|never|by default|remember that|going forward",
    re.IGNORECASE)

# "I like X" — a leaning, worth recording more tentatively.
_PREFERENCE_RE = re.compile(
    r"\bt thích|tôi thích|mình thích|\bt muốn|tôi muốn|mình muốn|t hay |tôi hay |"
    r"t thường|tôi thường|ưa thích|\bthích\b.{0,20}\bhơn\b|ghét |không thích|"
    r"i (?:prefer|like|hate|usually|always want)|\bprefer\b",
    re.IGNORECASE)

# "…just for today" — explicitly scoped to now, so it must NOT become a trait. Checked
# first and wins outright: this is the exact failure mode a naive user model has, where
# "I'm busy today" hardens into a permanent fact about the person.
_ONEOFF_RE = re.compile(
    r"hôm nay|bữa nay|lần này|lúc này|bây giờ|hiện tại|tạm thời|riêng lần|"
    r"just (?:this once|for now|today)|for now|right now|this time",
    re.IGNORECASE)

_MIN_TURN_CHARS = 8
_MAX_TURN_CHARS = 2000


def assess_preference(text: str):
    """(kind, reason) if this turn is worth one extraction call, else None. Pure Python.

    Deliberately conservative. A false negative costs nothing — the preference is simply
    learned later, or when the Master says it outright. A false positive costs a call
    AND risks writing a wrong trait that then shapes every future answer, so the bar is
    an explicit signal in the Master's own words, never a hunch about tone.
    """
    t = (text or "").strip()
    if not (_MIN_TURN_CHARS <= len(t) <= _MAX_TURN_CHARS):
        return None
    if _ONEOFF_RE.search(t):
        return None
    if _DURABLE_RE.search(t):
        return STATED, "durable wording ('từ giờ' / 'luôn' / 'đừng bao giờ')"
    if _PREFERENCE_RE.search(t):
        return INFERRED, "preference wording ('thích' / 'muốn' / 'prefer')"
    return None


EXTRACTION_PROMPT = """Bạn là bộ trích xuất sở thích. Đọc câu của Master và rút ra các sở thích LÂU DÀI về cách làm việc/giao tiếp.

Câu của Master:
{text}

QUY TẮC:
1. CHỈ trả về JSON array, không giải thích, không markdown fence.
2. Mỗi phần tử: {{"key": "snake_case_ngắn", "value": "mô tả ngắn gọn"}}
3. Tối đa 3 phần tử. Không có sở thích lâu dài nào thì trả về [].
4. KHÔNG trích xuất: mệnh lệnh một lần, dữ liệu tạm, tên file, nội dung công việc cụ thể.
5. KHÔNG BAO GIỜ trích xuất mật khẩu, token, khoá API hay bất kỳ thông tin đăng nhập nào.
6. `key` mô tả LOẠI sở thích (vd: report_style, preferred_language, email_tone).
7. `value` ngắn, dưới 100 ký tự.

JSON:"""

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_MAX_TRAITS_PER_TURN = 3


def parse_extraction(raw: str) -> list:
    """Tolerant read of the model's reply → [(key, value), …]. Never raises.

    Tolerant because the alternative is worse: a stricter parser would drop a perfectly
    good extraction over a stray code fence, and the failure would be invisible — the
    profile would just never fill, exactly the bug this tier exists to fix.
    """
    m = _JSON_ARRAY_RE.search(raw or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        key, value = item.get("key"), item.get("value")
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            out.append((key.strip(), value.strip()))
        if len(out) >= _MAX_TRAITS_PER_TURN:
            break
    return out


def learn_from_turn(model: UserModel, text: str, generate, now: float = None,
                    daily_limit: int = 20, logger=None) -> list:
    """Gate → one extraction call → write. Returns the WriteResults that stored something.

    `generate` is any callable taking a prompt and returning text, so this stays testable
    without a provider and works with whichever tier the caller decides should pay for it.
    Fail-open throughout: learning is a bonus, and nothing here may break a turn that has
    already been answered.
    """
    now = time.time() if now is None else now

    def log(action, msg):
        if logger:
            try:
                logger("USER_MODEL", action, msg)
            except Exception:
                pass

    verdict = assess_preference(text)
    if not verdict:
        return []                       # the common case, and it costs nothing
    kind, reason = verdict

    if not model.can_extract(now, daily_limit):
        log("skipped", f"daily extraction limit {daily_limit} reached")
        return []

    try:
        raw = generate(EXTRACTION_PROMPT.format(text=text.strip()[:_MAX_TURN_CHARS]))
        model.note_extraction(now)
    except Exception as e:
        log("error", f"{type(e).__name__}: {e}")
        return []

    stored = []
    for key, value in parse_extraction(raw):
        # The KIND comes from the deterministic gate, never from the model. Otherwise
        # every extraction would arrive claiming `stated` and could overwrite something
        # the Master actually said.
        res = model.remember(key, value, kind=kind, source=f"tự học: {reason}", now=now)
        if res.stored:
            stored.append(res)
        else:
            log("rejected", f"{key}: {res.reason}")
    if stored:
        log("learned", "; ".join(f"{r.trait.key}={r.trait.value}" for r in stored))
    return stored
