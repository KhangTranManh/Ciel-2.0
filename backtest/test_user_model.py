"""Tier-7a suite — the Master's profile: authority, decay, contradiction, token ceiling.

Zero LLM calls. Like the Tier-6 suite, every time-dependent decision takes `now` as a
parameter, so months of decay are simulated instantly.

The four properties worth protecting, in the order they matter:

  1. A credential can never enter the injected store. Everything here is sent to the
     provider on every call that carries it, so this is a security boundary, not tidiness.
  2. What the Master SAID outranks what Ciel GUESSED — permanently, in both directions.
  3. Decay: an offhand remark must not harden into a permanent trait.
  4. The rendered block never exceeds its token budget, and is empty when empty.

Run from the Ciel 2.0 directory:
    ./myenv/Scripts/python.exe -m backtest.test_user_model
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.user_model import (        # noqa: E402
    INFERRED, OBSERVED, STATED, UserModel, assess_preference, estimate_tokens,
    learn_from_turn, looks_like_secret, normalize_key, parse_extraction,
)

DAY = 86400.0
T0 = time.mktime((2026, 7, 26, 10, 0, 0, 0, 0, -1))

_passed, _failed = 0, []
_seq = [0]


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f"  -- {detail}" if detail else ""))


def model(tmp, **kw):
    _seq[0] += 1
    return UserModel(path=Path(tmp) / f"um_{_seq[0]}.json", **kw)


# ─────────────────────────────────────────────────────────────────────────────
def test_secret_boundary(tmp):
    print("\n[1] Credentials can never enter an injected store")
    m = model(tmp)

    for key, val in [("wifi_password", "hunter2"), ("api_key", "abc"),
                     ("gmail_token", "x"), ("card_cvv", "123"),
                     ("my_secret_plan", "invade"), ("bank_pin", "0000")]:
        r = m.remember(key, val, STATED, now=T0)
        check(f"'{key}' is refused", not r.stored, r.reason)

    # The dangerous case is the innocently-named key holding a real token.
    r = m.remember("work_note", "sk-abcdefghijklmnopqrstuvwxyz0123456789", STATED, now=T0)
    check("a harmless key holding an API token is caught by the VALUE test",
          not r.stored, r.reason)
    r = m.remember("note2", "a" * 48, STATED, now=T0)
    check("a long base64-looking blob is refused", not r.stored)
    r = m.remember("note3", "deadbeef" * 6, STATED, now=T0)
    check("a long hex blob is refused", not r.stored)

    check("the refusal points at the right store",
          "facts.json" in m.remember("password", "x", now=T0).reason)

    r = m.remember("preferred_language", "tiếng Việt", STATED, now=T0)
    check("an ordinary preference is stored", r.stored and len(m) == 1)
    check("looks_like_secret is a pure function",
          looks_like_secret("api_key", "x") and not looks_like_secret("tone", "ngắn gọn"))


def test_authority(tmp):
    print("\n[2] What the Master said outranks what Ciel guessed")
    m = model(tmp)
    m.remember("report_style", "ngắn, có số liệu", STATED, source="Master nói", now=T0)

    r = m.remember("report_style", "dài, nhiều giải thích", INFERRED, now=T0 + 60)
    check("an inference cannot overwrite a stated preference", not r.stored, r.reason)
    check("the stated value survives untouched",
          m.live_traits(T0 + 60)[0].value == "ngắn, có số liệu")
    check("the refusal explains which authority blocked it",
          "stated" in r.reason and "inferred" in r.reason)

    r = m.remember("report_style", "cực ngắn", STATED, now=T0 + 120)
    check("the Master CAN change their own mind", r.stored and r.reason == "replaced")
    check("the previous value is archived, not erased",
          m.explain("report_style", T0 + 120).count("trước đó") == 1)

    m2 = model(tmp)
    m2.remember("wake_time", "07:00", OBSERVED, now=T0)
    r = m2.remember("wake_time", "06:30", INFERRED, now=T0 + 60)
    check("a higher-authority guess CAN correct a lower-authority one", r.stored)
    check("and takes the higher kind", m2.live_traits(T0 + 60)[0].kind == INFERRED)


def test_reinforcement_and_decay(tmp):
    print("\n[3] Decay — an offhand remark must not become a permanent trait")
    m = model(tmp)
    m.remember("busy_today", "đang bận", OBSERVED, now=T0)
    t = m.live_traits(T0)[0]

    check("an observation starts weak", t.confidence < 0.5)
    check("it is live right away", t.is_live(T0))
    check("after two half-lives it has faded out of the profile",
          not t.is_live(T0 + 28 * DAY), f"{t.effective_confidence(T0 + 28 * DAY):.3f}")
    check("a faded trait is simply not rendered", m.render(T0 + 28 * DAY) == "")

    m.remember("busy_today", "đang bận", OBSERVED, now=T0 + 10 * DAY)
    t = m.live_traits(T0 + 10 * DAY)[0]
    check("re-observing reinforces confidence and resets the clock",
          t.hits == 2 and t.is_live(T0 + 30 * DAY), f"hits={t.hits}")

    m.remember("preferred_language", "tiếng Việt", STATED, now=T0)
    stated = [x for x in m.live_traits(T0) if x.key == "preferred_language"][0]
    check("what the Master stated never decays",
          stated.effective_confidence(T0 + 3650 * DAY) == stated.confidence)

    n = m.prune(T0 + 400 * DAY)
    check("prune drops the decayed one", n == 1)
    check("...and never the stated one",
          any(x.key == "preferred_language" for x in m.live_traits(T0 + 400 * DAY)))


def test_render_budget(tmp):
    print("\n[4] The rendered block is bounded, and empty when it should be")
    m = model(tmp)
    check("an empty profile renders to nothing at all", m.render(T0) == "")
    check("...so the caller injects zero tokens", estimate_tokens(m.render(T0)) == 0)

    for i in range(30):
        m.remember(f"trait_{i}", f"giá trị số {i} " + "x" * 60, STATED, now=T0 + i)

    for budget in (40, 120, 250, 600):
        block = m.render(T0 + 100, budget_tokens=budget)
        got = estimate_tokens(block)
        check(f"budget {budget} is respected (got {got})", got <= budget, block[:80])

    block = m.render(T0 + 100, budget_tokens=600, max_items=3)
    check("max_items caps the line count independently",
          len([l for l in block.split("\n") if l.startswith("- ")]) == 3)

    tiny = m.render(T0 + 100, budget_tokens=5)
    check("a budget too small for even one line yields nothing, not a bare header",
          tiny == "", tiny)

    m2 = model(tmp)
    m2.remember("a_low", "x", OBSERVED, now=T0)
    m2.remember("b_high", "y", STATED, now=T0)
    first_line = m2.render(T0).split("\n")[1]
    check("the strongest trait is rendered first, so truncation drops the weakest",
          "b_high" in first_line, first_line)

    check("the block tells the model this is background, not an order",
          "KHÔNG" in m2.render(T0) and "yêu cầu hiện tại" in m2.render(T0))


def test_bounds_and_hygiene(tmp):
    print("\n[5] Bounds — a profile, not a database")
    m = model(tmp, max_traits=5)
    m.remember("keep_me", "quan trọng", STATED, now=T0)
    for i in range(20):
        m.remember(f"weak_{i}", f"v{i}", OBSERVED, now=T0)
    check("the store is capped", len(m) == 5, str(len(m)))
    check("eviction drops the weakest, not the stated one",
          any(t.key == "keep_me" for t in m.live_traits(T0)))

    m2 = model(tmp)
    m2.remember("  Preferred   Language!! ", "vi", STATED, now=T0)
    check("keys are normalised to snake_case",
          m2.live_traits(T0)[0].key == "preferred_language",
          m2.live_traits(T0)[0].key)
    check("normalize_key is exposed and total",
          normalize_key("A B-c") == "a_b_c" and normalize_key("") == "")
    # If two spellings of the same idea landed on two keys, the authority and
    # contradiction rules would never see each other and both values would be rendered.
    m2.remember("Preferred-Language", "en", STATED, now=T0 + 60)
    check("spelling variants of one idea collapse to a single trait",
          len([t for t in m2.live_traits(T0 + 60) if t.key == "preferred_language"]) == 1
          and [t for t in m2.live_traits(T0 + 60)
               if t.key == "preferred_language"][0].value == "en")

    m2.remember("long_one", "y" * 500, STATED, now=T0)
    check("values are truncated so one trait cannot eat the budget",
          all(len(t.value) <= 160 for t in m2.live_traits(T0)))

    m2.remember("multi", "dòng 1\ndòng 2", STATED, now=T0)
    check("newlines are flattened so a value cannot forge profile lines",
          "\n" not in [t for t in m2.live_traits(T0) if t.key == "multi"][0].value)

    check("empty writes are rejected", not m2.remember("", "x", now=T0).stored
          and not m2.remember("k", "  ", now=T0).stored)
    check("an unknown kind degrades to the weakest, never the strongest",
          m2.remember("odd", "v", kind="wishful", now=T0).trait.kind == OBSERVED)


def test_forget_and_explain(tmp):
    print("\n[6] Erasure and provenance — a profile you can audit and delete")
    m = model(tmp)
    m.remember("preferred_language", "tiếng Việt", STATED, source="Master nói 26/07", now=T0)
    m.remember("tone", "ngắn", INFERRED, source="suy ra từ 12 lượt", now=T0)

    e = m.explain("preferred_language", T0)
    check("explain names the value, the kind and the source",
          "tiếng Việt" in e and "stated" in e and "26/07" in e, e)
    check("a stated trait says outright that it does not fade", "không phai" in e)
    check("an inferred trait shows its decayed confidence and age",
          "ngày" in m.explain("tone", T0 + 5 * DAY))
    check("explaining an unknown key is an answer, not a crash",
          "Chưa có" in m.explain("nonexistent"))

    check("forget actually deletes", m.forget("tone") and len(m) == 1)
    check("forgetting twice is not an error", not m.forget("tone"))
    check("forget_all clears everything", m.forget_all() == 1 and len(m) == 0)


def test_persistence(tmp):
    print("\n[7] Persistence — human-readable, and never fatal")
    p = Path(tmp) / "profile.json"
    m1 = UserModel(path=p)
    m1.remember("preferred_language", "tiếng Việt", STATED, source="Master", now=T0)
    m1.remember("wake_time", "07:00", OBSERVED, now=T0)

    raw = p.read_text(encoding="utf-8")
    check("the file is a person-readable description of a person",
          "tiếng Việt" in raw and "\n  " in raw, raw[:60])

    m2 = UserModel(path=p)
    check("traits survive a restart", len(m2) == 2)
    t = [x for x in m2.live_traits(T0) if x.key == "preferred_language"][0]
    check("kind and provenance survive too", t.kind == STATED and t.source == "Master")
    check("decay is measured from the stored timestamp, not from load time",
          not [x for x in m2.live_traits(T0 + 60 * DAY) if x.key == "wake_time"])

    p.write_text("{ not json at all", encoding="utf-8")
    m3 = UserModel(path=p)
    check("a corrupt profile starts empty instead of crashing start-up", len(m3) == 0)
    check("...and is still writable afterwards",
          m3.remember("k", "v", STATED, now=T0).stored)

    m4 = UserModel(path=None)
    check("a store with no path still works in memory",
          m4.remember("k", "v", STATED, now=T0).stored and m4.render(T0) != "")


def test_estimate_tokens():
    print("\n[8] Token estimation errs high, never low")
    check("empty text costs nothing", estimate_tokens("") == 0)
    vi = "Master thích báo cáo ngắn, có số liệu, không lan man."
    check("a Vietnamese line is counted at a plausible rate",
          5 <= estimate_tokens(vi) <= len(vi), str(estimate_tokens(vi)))
    check("longer text costs more", estimate_tokens(vi * 3) > estimate_tokens(vi))


def test_preference_gate():
    print("\n[9] The gate — free Python decides whether a call is worth spending")
    durable = ["từ giờ trả lời ngắn thôi", "luôn dùng tiếng Việt khi báo cáo",
               "đừng bao giờ gửi mail sau 10 giờ tối", "lần sau nhớ kèm số liệu",
               "from now on keep answers short", "always cite the source"]
    for t in durable:
        v = assess_preference(t)
        check(f"durable: {t[:34]!r}", v is not None and v[0] == STATED, str(v))

    leaning = ["t thích báo cáo có bảng hơn", "tôi muốn câu trả lời gọn",
               "i prefer bullet points"]
    for t in leaning:
        v = assess_preference(t)
        check(f"leaning: {t[:34]!r} → inferred", v is not None and v[0] == INFERRED, str(v))

    # The failure mode this whole tier has to avoid.
    oneoff = ["hôm nay t bận nên trả lời ngắn thôi", "lần này thì luôn dùng tiếng Anh",
              "tạm thời t thích kiểu này", "just for now keep it short"]
    for t in oneoff:
        check(f"one-off is skipped entirely: {t[:34]!r}", assess_preference(t) is None)

    ordinary = ["đọc file a.txt", "giá vàng bao nhiêu", "commit giúp t",
                "gửi mail cho kx@gmail.com", "2+2 bằng mấy"]
    for t in ordinary:
        check(f"ordinary work spends nothing: {t[:34]!r}", assess_preference(t) is None)

    check("too short is skipped", assess_preference("luôn") is None)
    check("absurdly long is skipped", assess_preference("luôn " * 800) is None)
    check("empty input is safe", assess_preference("") is None and assess_preference(None) is None)


def test_extraction_parsing():
    print("\n[10] Parsing the model's reply — tolerant, because silence is the bug")
    good = '[{"key": "report_style", "value": "ngắn gọn"}]'
    check("plain JSON parses", parse_extraction(good) == [("report_style", "ngắn gọn")])
    check("a markdown fence does not defeat it",
          parse_extraction("```json\n" + good + "\n```") == [("report_style", "ngắn gọn")])
    check("chatty preamble does not either",
          parse_extraction("Đây là kết quả:\n" + good) == [("report_style", "ngắn gọn")])
    check("an empty array is a valid answer", parse_extraction("[]") == [])
    check("garbage yields nothing rather than raising", parse_extraction("no json here") == [])
    check("malformed JSON yields nothing", parse_extraction('[{"key": ') == [])
    check("non-string fields are dropped",
          parse_extraction('[{"key": 5, "value": "x"}, {"key": "a", "value": "b"}]')
          == [("a", "b")])
    check("at most 3 traits come out of one turn",
          len(parse_extraction(json.dumps([{"key": f"k{i}", "value": "v"}
                                           for i in range(9)]))) == 3)


def test_learn_from_turn(tmp):
    print("\n[11] End to end — gate, one call, and Python owns the authority")
    calls = []

    def fake_llm(prompt):
        calls.append(prompt)
        return '[{"key": "report_style", "value": "ngắn, có số liệu"}]'

    m = model(tmp)
    stored = learn_from_turn(m, "đọc file a.txt", fake_llm, now=T0)
    check("an ordinary turn spends NO call at all", calls == [] and stored == [])

    stored = learn_from_turn(m, "từ giờ báo cáo phải ngắn và có số liệu", fake_llm, now=T0)
    check("a qualifying turn spends exactly one call", len(calls) == 1)
    check("and the trait is written", len(stored) == 1 and len(m) == 1)
    check("durable wording is recorded as STATED",
          m.live_traits(T0)[0].kind == STATED, m.live_traits(T0)[0].kind)
    check("provenance says it was self-learned", "tự học" in m.live_traits(T0)[0].source)
    check("the prompt carries the Master's actual words",
          "ngắn và có số liệu" in calls[0])

    # The model must not be able to claim authority for itself.
    m2 = model(tmp)
    m2.remember("report_style", "dài, nhiều giải thích", STATED, now=T0)
    learn_from_turn(m2, "t thích báo cáo ngắn hơn", fake_llm, now=T0 + 10)
    check("a LEANING cannot overwrite something the Master stated outright",
          m2.live_traits(T0 + 10)[0].value == "dài, nhiều giải thích")

    # A credential must not survive the round trip even if the model emits one.
    m3 = model(tmp)
    leaky = lambda p: '[{"key": "wifi_password", "value": "hunter2"}]'   # noqa: E731
    learn_from_turn(m3, "từ giờ nhớ mật khẩu wifi của t", leaky, now=T0)
    check("an extracted credential is refused at the store boundary", len(m3) == 0)

    m4 = model(tmp)
    boom = lambda p: (_ for _ in ()).throw(RuntimeError("provider down"))   # noqa: E731
    check("a provider failure is swallowed — the turn is already answered",
          learn_from_turn(m4, "từ giờ trả lời ngắn", boom, now=T0) == [])

    m5 = model(tmp)
    check("an empty extraction writes nothing",
          learn_from_turn(m5, "từ giờ trả lời ngắn", lambda p: "[]", now=T0) == []
          and len(m5) == 0)

    logged = []
    m6 = model(tmp)
    learn_from_turn(m6, "từ giờ trả lời ngắn", fake_llm, now=T0,
                    logger=lambda a, b, c: logged.append((a, b)))
    check("learning is written to the audit log",
          any(x == ("USER_MODEL", "learned") for x in logged), str(logged))


def test_extraction_budget(tmp):
    print("\n[12] Extraction budget — a chatty day cannot multiply the cost")
    calls = []
    llm = lambda p: (calls.append(p), "[]")[1]       # noqa: E731
    m = model(tmp)

    for i in range(3):
        learn_from_turn(m, f"từ giờ quy tắc số {i} là vậy", llm, now=T0 + i, daily_limit=3)
    check("the allowance is spent", len(calls) == 3)

    learn_from_turn(m, "từ giờ thêm một quy tắc nữa", llm, now=T0 + 10, daily_limit=3)
    check("past the limit, no further call is made", len(calls) == 3)

    learn_from_turn(m, "từ giờ thêm quy tắc", llm, now=T0 + 25 * 3600, daily_limit=3)
    check("the allowance resets the next day", len(calls) == 4)

    check("a limit of 0 disables learning outright",
          not m.can_extract(T0, 0)
          and learn_from_turn(m, "từ giờ trả lời ngắn", llm, now=T0, daily_limit=0) == []
          and len(calls) == 4)

    m2 = UserModel(path=Path(tmp) / "budget_persist.json")
    m2.note_extraction(T0)
    m3 = UserModel(path=Path(tmp) / "budget_persist.json")
    check("the count survives a restart, so the cap is real",
          not m3.can_extract(T0, 1))


def main():
    tmp = tempfile.mkdtemp(prefix="ciel_usermodel_")
    print("=" * 72)
    print("TIER 7a — USER MODEL SUITE (no LLM, simulated clock)")
    print("=" * 72)
    try:
        for fn in (test_secret_boundary, test_authority, test_reinforcement_and_decay,
                   test_render_budget, test_bounds_and_hygiene, test_forget_and_explain,
                   test_persistence, test_learn_from_turn, test_extraction_budget):
            fn(tmp)
        test_estimate_tokens()
        test_preference_gate()
        test_extraction_parsing()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 72)
    total = _passed + len(_failed)
    print(f"RESULT: {_passed}/{total} passed")
    for name in _failed:
        print(f"  - {name}")
    print("=" * 72)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
