"""Execution service extracted from CielCore; compatibility imports are lazy."""
from __future__ import annotations

def execute_tool(self, tool_name: str, tool_args: dict, response_hint: str = "", user_input: str = "",
                 _raw_out: list = None, _skip_format: bool = False) -> str:
    """Execute a Ciel tool and format the result.

        `_raw_out`, if given a list, gets the pre-Worker-formatting result_text
        appended (structured tool output — the JSON search_gmail/stealth_search/etc.
        actually returned) alongside the normal human-facing return value. Internal
        plumbing for `_run_steps`'s {prev}/{step_N} chaining — see its call site.
        """
    import os
    import re
    from core.llm_connector import CONTEXT_RECENT_TURNS_BUDGET
    from core.context import ContextAssembler, P_HELPFUL
    from core.permissions import Decision
    from core.harness.signals import _NO_OVERWRITE_RE, _RAW_STEP_RESULT_CHARS, _find_dangerous_code_patterns, _has_unsynthesized_placeholder, _is_unfixable_tool_error
    from datetime import datetime
    from agent_system.utils.logger import log
    log.tool(f"Executing: {tool_name}({tool_args})")
    # Preserve the caller's action identity before sanitization/rendering may
    # create a replacement args dict. Pending confirmations are keyed to this
    # original tool + args contract, not to delivery-only transformations.
    pending_match_args = dict(tool_args or {})

    if tool_name not in self._tool_map:
        log.error(f"Tool not found: {tool_name}")
        return f"[TOOL_ERROR] Tool '{tool_name}' not found."

    # OUTBOUND IDEMPOTENCE — one delivery per recipient per turn.
    #
    # Two independent mechanisms both complete a plan that is missing its send step:
    # the deterministic workflow safeguard in execute_multi_tool (which appends one),
    # and the Tier-1 loop (which re-plans one when the Brain sets `needs_followup`).
    # Neither knows about the other, so a request like "gửi mail cho X báo cáo Y"
    # delivered the SAME report twice, with two different subjects. Reproduced live.
    #
    # The existing comment at the loop's call site — "extra rounds … can never
    # double-send" — is true only for a send that was in the ORIGINAL plan, which is
    # separated out and run once after synthesis. It never covered a send the loop
    # invents. Guarding here, at the one choke point every path goes through, closes
    # that and any future path too.
    #
    # Checked BEFORE the safety gate on purpose: asking the Master to approve a send
    # that is about to be suppressed is worse than not asking.
    # First delivery wins. That is also the better one structurally: the loop runs
    # before synthesis, so the Brain's properly-subjected message goes out and the
    # generic fallback ("Báo cáo từ Ciel") is the one dropped.
    out_key = self._outbound_key(tool_name, tool_args)
    if out_key:
        with self._log_lock:
            already = out_key in self._sent_this_turn
        if already:
            self._log_thought(
                "SAFETY", "duplicate_send_suppressed",
                f"{tool_name} to the same recipient already succeeded in this turn "
                    f"({out_key}) — suppressed to avoid delivering the message twice.")
            return (f"[SKIPPED] Đã gửi tới người nhận này trong yêu cầu hiện tại rồi, "
                        f"Master. Bỏ qua lần gửi thứ hai để tránh trùng.")

    # STALE-YEAR FIX for web search: the Brain is trained on older data and often
    # injects a past year into "latest news" queries (observed: user asked for the
    # latest AI news in 2026, Brain searched "...breakthroughs 2024" → stale results).
    # If the query carries a past year the user never mentioned, bump it to the
    # current year so "latest" actually means now.
    if tool_name in ("stealth_search", "smart_scrape") and isinstance(tool_args.get("query"), str):
        cur_year = datetime.now().year
        def _bump_year(m):
            y = m.group(0)
            return str(cur_year) if int(y) < cur_year and y not in (user_input or "") else y
        new_q = re.sub(r"\b(20[0-3]\d)\b", _bump_year, tool_args["query"])
        if new_q != tool_args["query"]:
            self._log_thought("TOOL", "query_year_fixed",
                              f"stale year in search query bumped to {cur_year}: {tool_args['query']!r} -> {new_q!r}")
            tool_args = dict(tool_args)
            tool_args["query"] = new_q

    # TELEGRAM INBOUND PROTECT: never write/append into telegram_uploads unless the
    # Master explicitly asks to overwrite that upload. Found live: write-safeguard
    # + "đã lưu tại" false positive clobbered the just-downloaded file with a summary.
    if tool_name in ("write_file", "append_file") and isinstance(tool_args.get("filename"), str):
        _fn_norm = tool_args["filename"].replace("\\", "/").lower()
        if "telegram_uploads/" in _fn_norm or _fn_norm.startswith("telegram_uploads"):
            explicit = bool(re.search(
                r"ghi\s*đè\s*(?:file\s*)?(?:upload|telegram)|overwrite\s+(?:the\s+)?upload|"
                    r"sửa\s+(?:file\s+)?(?:trong\s+)?telegram_uploads",
                user_input or "",
                re.IGNORECASE,
            ))
            if not explicit:
                self._log_thought(
                    "SAFETY", "telegram_upload_write_blocked",
                    f"{tool_name} to {_fn_norm} blocked — inbound uploads are read-only "
                        "unless Master explicitly asks to overwrite.",
                )
                return (
                    f"[SKIPPED] Không ghi đè file inbound Telegram "
                        f"(`{tool_args['filename']}`). Đọc bằng read_file/read_document/"
                        f"describe_image_file; nếu cần lưu báo cáo, ghi ra agent_output/ "
                        f"hoặc path Master chỉ định (không phải telegram_uploads/)."
                )

    # NO-OVERWRITE REQUEST: write_file's own contract is "create or completely
    # overwrite" — it has no refuse-if-exists mode, so honoring "đừng ghi đè, tạo
    # file mới" means picking a DIFFERENT filename before the tool ever runs.
    # Found live: a Master said this 5 times across 5 turns ("tao muốn file mới
    # chứ không phải ghi đè") and every write still landed on the exact same
    # filename, because nothing downstream of the Brain's own tool_args ever
    # checked it. Deterministic and narrow: only fires when (a) the wording
    # explicitly asks for this, and (b) the target already exists — an explicit
    # request for a file that doesn't exist yet has nothing to clobber.
    if tool_name == "write_file" and isinstance(tool_args.get("filename"), str) and _NO_OVERWRITE_RE.search(user_input or ""):
        try:
            from skills.internal.system_ops import _is_safe_path
            existing_path = _is_safe_path(tool_args["filename"])
            if existing_path.exists():
                stem, suffix = existing_path.stem, existing_path.suffix
                parent_rel = tool_args["filename"].rsplit("/", 1)[0] + "/" if "/" in tool_args["filename"] else ""
                n = 2
                while True:
                    candidate = f"{parent_rel}{stem}_v{n}{suffix}"
                    if not _is_safe_path(candidate).exists():
                        break
                    n += 1
                self._log_thought(
                    "SAFETY", "no_overwrite_renamed",
                    f"write_file: Master asked not to overwrite; '{tool_args['filename']}' already "
                        f"exists — writing to '{candidate}' instead.")
                tool_args = dict(tool_args)
                tool_args["filename"] = candidate
        except Exception:
            pass  # never let the rename attempt itself break a legitimate write

    # DANGEROUS CODE GATE: write_file/append_file can save arbitrary Worker-generated
    # code straight to disk with no confirmation (unlike pre-declared _HIGH_RISK_TOOLS).
    # Catch real destructive commands (drive format, mkfs, fork bombs...) here so
    # writing them out still requires the Master's explicit Y/N, same as any other
    # high-risk action. Fail-closed: on denial, nothing is written.
    if tool_name in ("write_file", "append_file") and isinstance(tool_args.get("content"), str):
        matched = _find_dangerous_code_patterns(tool_args["content"])
        if matched:
            disable_gate = os.getenv("DISABLE_SAFETY_GATE", "false").lower() in ("true", "1", "yes")
            if not disable_gate:
                risk = f"Write code containing destructive pattern(s): {', '.join(matched)}"
                if not self._request_confirmation(tool_name, tool_args, risk_override=risk):
                    return f"[CANCELLED] Master denied writing potentially destructive code to '{tool_args.get('filename', '?')}'. No action was taken."

        # HARD BLOCK (symmetric to the email placeholder block below): never let an
        # unresolved multi_tool synthesis placeholder land on disk as the "report" the
        # user asked for. execute_multi_tool's deferred-write detection normally holds
        # this kind of content back until after synthesis — this is the last-resort net
        # for the case where that classification misses a paraphrased marker (or a
        # placeholder reaches write_file/append_file via any other path), so a hollow
        # file is blocked here instead of silently written.
        if _has_unsynthesized_placeholder(tool_args["content"]):
            self._log_thought("SAFETY", "write_placeholder_blocked",
                              f"{tool_name}: content for '{tool_args.get('filename', '?')}' still "
                                  f"contains an unsynthesized placeholder — write blocked.")
            return (f"Lỗi: nội dung ghi vào file chưa được tổng hợp (vẫn còn placeholder) — đã CHẶN ghi. "
                        f"Cần thu thập dữ liệu thật và tổng hợp nội dung trước khi ghi file.")

    # OUTBOUND EMAIL SANITIZATION (single choke-point): every email-sending path
    # — multi_tool synthesis, direct Brain send, content-filter fallback, replies,
    # drafts — funnels through here, so cleaning the body once covers them all.
    body_key = self._EMAIL_BODY_ARGS.get(tool_name)
    if body_key and isinstance(tool_args.get(body_key), str):
        # HARD BLOCK: never let an unsynthesized plan placeholder go out as a real
        # email. This marker is only meant to be replaced by execute_multi_tool's
        # synthesis step; reaching here means the plan skipped synthesis. Matched via
        # _has_unsynthesized_placeholder() (SYNTHES* root) rather than one exact
        # literal, so a Brain-paraphrased placeholder is still caught.
        if _has_unsynthesized_placeholder(tool_args[body_key]):
            self._log_thought("SAFETY", "email_placeholder_blocked",
                              f"{tool_name}: body still contains an unsynthesized placeholder — send blocked.")
            return (f"Lỗi: nội dung email chưa được tổng hợp (vẫn còn placeholder) — đã CHẶN gửi. "
                        f"Cần thu thập dữ liệu thật và tổng hợp nội dung trước khi gửi.")

        cleaned = self._sanitize_outbound_email(tool_args[body_key])
        if cleaned != tool_args[body_key]:
            tool_args = dict(tool_args)
            tool_args[body_key] = cleaned
            self._log_thought("TOOL", "email_sanitized", f"{tool_name}: stripped internal/meta content from '{body_key}'.")

        # THIRD TIER — Middleware semantic review/finalize (relevance, consistency,
        # plausible grounding). Runs BEFORE the safety-gate preview so the Master's
        # Y/N prompt reflects the final body. No-op if MIDDLEWARE_ENABLED=false.
        reviewed = self._middleware_review(user_input, tool_name, tool_args[body_key])
        if reviewed != tool_args[body_key]:
            tool_args = dict(tool_args)
            tool_args[body_key] = reviewed

    # SAFETY GATE (Tier 3): AUTO / ASK / DENY — see core/permissions.py.
    # NOTE: SAFETY_OPEN governs Brain LLM content-filtering, a separate concern —
    # it must NOT influence the destructive-tool confirmation gate.
    decision, why = self.permissions.decide(tool_name, tool_args, attended=not self.unattended)
    if decision == Decision.DENY:
        self._log_thought("SAFETY", "denied", f"{tool_name}: {why}")
        return (f"[CANCELLED] {tool_name} is on the deny list and will not be run, Master. "
                    f"Remove it from CIEL_DENY_TOOLS if that was not intended.")
    if decision == Decision.DEFER:
        # TIER 6 — nobody is at the keyboard, so there is no one to say yes. Silence
        # must not become consent: the action is recorded and raised at the next
        # interaction (see the `deferred_approval` trigger) rather than performed.
        self.deferred.add(tool_name, tool_args, reason=why, source="unattended run")
        self._log_thought("SAFETY", "deferred", f"{tool_name}: {why}")
        return (f"[CANCELLED] {tool_name} needs your approval and nobody was at the "
                    f"keyboard, Master. It has been recorded for you to confirm.")
    if decision == Decision.ASK:
        if not self._request_confirmation(tool_name, tool_args):
            return f"[CANCELLED] Master denied execution of {tool_name}. No action was taken."
    elif why not in ("read-only", "not classified as risky"):
        # Only worth logging when a risky tool was let through for a REASON (a session
        # grant, plan approval, or an open gate) — that is the audit-relevant case.
        self._log_thought("SAFETY", "auto_allowed", f"{tool_name}: {why}")

    # send_gmail_message transmits its body as text/html (langchain), so raw '\n'
    # collapse into a wall of text on the recipient side. Render the clean plain
    # body into simple HTML AFTER the confirmation preview (which stays plain/readable)
    # so the delivered email preserves paragraphs and line breaks.
    if tool_name == "send_gmail_message" and isinstance(tool_args.get("message"), str):
        tool_args = dict(tool_args)
        tool_args["message"] = self._plaintext_to_html(tool_args["message"])

    result = self.tool_manager.execute_tool(tool_name, tool_args)
    result_text = self.tool_manager.format_tool_result(result)
    # Ground truth is the dict's own `success` flag, not a text guess. Bug found
    # live: tool_manager.format_tool_result() renders ANY failure as "[{code}] msg"
    # where `code` is whatever the skill author chose (VISION_LOOP_ERROR,
    # TOOL_NOT_FOUND, INVALID_ARGS, ...) — but this used to re-derive "is this an
    # error?" by checking only for the literal "EXECUTION_ERROR" or a mixed-case
    # "Error" substring. Any other code (all-caps, like every real example above)
    # silently read as a SUCCESS: no healing attempt, no friendly rephrase, the
    # raw "[VISION_LOOP_ERROR] Vision loop crashed: GEMINI_API_KEY not found in
    # .env" went out as if it were the answer — and task_state recorded the step
    # as "done". tool_manager.execute_tool() always returns this normalized dict
    # (see its docstring: "standardized structured result"), so `success` is
    # always present and reliable here.
    is_error = isinstance(result, dict) and result.get("success") is False

    # SELF-HEALING HOOK (UP TO 3 ATTEMPTS)
    max_attempts = 3
    attempt = 1
    current_args = tool_args
    manager = getattr(self, "run_manager", None)
    mutation_in_harness = False
    if manager is not None and manager.active is not None:
        # A failed/uncertain external mutation is not safe to replay merely with
        # different arguments. Ordinary sandbox script repair retains its bounded
        # recovery behavior; outbound/destructive calls require a fresh request.
        mutation_in_harness = tool_name in self._HIGH_RISK_TOOLS or tool_name.startswith("send_")

    if is_error and _is_unfixable_tool_error(tool_name, result_text):
        self._log_thought("HEALING", "skipped_unfixable",
                          f"{tool_name}: error matches a known-unfixable pattern (missing dependency / "
                              f"network / geo-restriction) — no tool_args correction could fix this, skipping retries.")

    while (
        attempt <= max_attempts
        and is_error
        and not mutation_in_harness
        and not _is_unfixable_tool_error(tool_name, result_text)
    ):
        self._log_thought("HEALING", f"attempt_{attempt}", f"Starting heal attempt {attempt}/{max_attempts}")
        
        previous_code = ""
        if tool_name == "run_python_script" and "filename" in current_args:
            try:
                from skills.internal.system_ops import _is_safe_path
                safe_path = _is_safe_path(current_args["filename"])
                if os.path.exists(safe_path):
                    with open(safe_path, "r", encoding="utf-8") as f:
                        previous_code = f.read()
            except Exception:
                pass

        success, action, data = self.recovery.heal_tool_error(tool_name, current_args, result_text, attempt, previous_code)

        if not success:
            result_text += f"\n\n[Self-Healing Failed] {data.get('error', 'Unknown error')}"
            break  # is_error stays True — nothing here fixed it

        if action == "code_fix":
            try:
                from skills.internal.system_ops import _is_safe_path
                safe_path = _is_safe_path(data["filename"])

                # Validate syntax before saving!
                syntax_valid, syntax_error = self.recovery.check_syntax(data["code"])
                if not syntax_valid:
                    self._log_thought("HEALING", "syntax_error", syntax_error)
                    result_text = f"[Syntax Error Validation Failed]\n{syntax_error}"
                    is_error = True
                    attempt += 1
                    continue

                with open(safe_path, "w", encoding="utf-8") as f:
                    f.write(data["code"])
                self._log_thought("HEALING", "apply_fix", f"Code updated for {data['filename']}. Re-running script.")

                # Retry tool
                result = self.tool_manager.execute_tool(tool_name, current_args)
                retry_text = self.tool_manager.format_tool_result(result)
                is_error = isinstance(result, dict) and result.get("success") is False
                result_text = f"[Self-Healing Activated] Analyzed code error, fixed it, and re-ran.\n\nNew Output:\n{retry_text}"

            except Exception as e:
                result_text = f"[Self-Healing Error] {e}"
                is_error = True

        elif action == "retry_tool":
            self._log_thought("HEALING", "retry_tool_args", str(data))
            current_args = data  # Update arguments for the next attempt if it fails
            result = self.tool_manager.execute_tool(tool_name, current_args)
            retry_text = self.tool_manager.format_tool_result(result)
            is_error = isinstance(result, dict) and result.get("success") is False
            result_text = f"[Self-Healing Activated] Analyzed parameter error, corrected args, and re-ran.\n\nNew Output:\n{retry_text}"

        attempt += 1

    if is_error:
        log.error(f"Tool {tool_name} failed after {attempt-1} self-healing attempts.")
        self._log_thought("TOOL", "error", f"{tool_name}: {result_text}")
        # Avoid another provider call for deterministic auth/network/availability
        # failures. It cannot repair the tool and can itself hang when the provider
        # is unhealthy; preserve the original bounded cause for the caller instead.
        if _is_unfixable_tool_error(tool_name, result_text):
            first_line = (result_text or "Unknown tool failure").strip().splitlines()[0]
            return f"[TOOL_ERROR] {tool_name}: {first_line[:800]}"
        # STRUCTURED ERROR: Rephrase raw error for the user
        try:
            friendly = self.worker.generate(
                f"Rephrase this error for the user in one plain, helpful sentence. "
                    f"Do NOT include technical stack traces.\n\nError: {result_text[:500]}"
            )
            self._log_thought("WORKER", "error_rephrase", friendly)
            return f"Sorry, {friendly}"
        except Exception:
            return f"[TOOL_ERROR] {tool_name}: {result_text}"

    # Recorded only on SUCCESS — this branch is past the error return above. A send
    # that failed must stay retryable; marking it delivered would turn one provider
    # hiccup into a message that never goes out and never reports why.
    if out_key:
        with self._log_lock:
            self._sent_this_turn.add(out_key)
    if tool_name in ("send_telegram", "send_telegram_document"):
        self._telegram_delivery_sent_this_turn = True

    # A preview tool may have staged this exact follow-up action. Usually
    # process() clears that slot before a bare "yes" executes it, but
    # self-correction can reach the follow-up directly after judging the
    # preview incomplete. A successful direct execution is still completion;
    # leaving the old slot behind blocks the next request and can re-run an
    # already-completed action (observed with git_confirm_push).
    pending = self._get_pending_action()
    if (pending and pending.get("tool") == tool_name
            and pending.get("args") == pending_match_args):
        self._clear_pending_action()
        self._log_thought(
            "SYSTEM", "pending_action_completed",
            f"{tool_name} completed through the normal tool path â€” cleared matching pending action.",
        )

    self._log_thought("TOOL", "result", f"{tool_name}: {result_text}")

    # Collect only successful, read-only evidence. ActiveSubject owns the allow-list
    # and strips secrets, recipients, paths, URLs and command-like values.
    try:
        self.active_subject.observe_tool(tool_name, current_args, result_text)
    except Exception as subject_err:
        self._log_thought(
            "CONTEXT", "active_subject_observe_failed",
            f"{type(subject_err).__name__}: {str(subject_err)[:160]}",
        )

    # A successful web lookup can ground only its own immediate, explicitly
    # deictic follow-up. Record the source before Worker formatting so the Router
    # receives real URLs even if the prose summary omits one.
    self._remember_active_lookup(tool_name, current_args, result_text, user_input)

    # Chain refs ({prev}/{step_N}) need the STRUCTURED result (e.g. search_gmail's
    # real "id" field) — capture it here, before any Worker humanizing below turns
    # it into a prose sentence a later step's tool_args cannot use as an argument.
    if _raw_out is not None:
        _raw_out.append(result_text)

    # Remember a preview tool's follow-up so a later bare "yes" can execute it.
    # The tool declares this itself via result["confirm"] (see skills/_result.py
    # make_result) — core needs no per-tool registration, so any skill can opt in.
    confirm_spec = result.get("confirm") if isinstance(result, dict) else None
    if confirm_spec and not result_text.startswith(("[TOOL_ERROR", "[CANCELLED")):
        self._set_pending_action(confirm_spec["tool"], confirm_spec.get("args", {}), source=tool_name)

    # A data step inside a plan that ends in a send/write: the plan's single
    # synthesis reads the raw evidence. Formatting each step against the whole
    # request let the Worker narrate "Đã gửi email" before the send had run.
    if _skip_format:
        raw = self._compact_email_result(result_text) if "gmail" in tool_name.lower() else result_text
        return raw[:_RAW_STEP_RESULT_CHARS]

    if tool_name in {"get_fact", "save_fact", "delete_fact"}:
        return self._format_fact_result(tool_name, result_text, user_input)

    if tool_name not in self._TOOLS_NEEDING_FORMAT:
        return result_text

    # GMAIL: Smart truncation — keep all emails visible, trim each body
    if "gmail" in tool_name.lower():
        clean_text = self._compact_email_result(result_text)
    elif tool_name == "smart_scrape":
        # Let the Worker read up to 40,000 characters of the scraped website
        clean_text = result_text[:40000]
    elif tool_name == "stealth_search":
        # Search hits now carry a Published date + Source per result, so a full page
        # of results no longer fits in the generic 2,000-char budget — truncating
        # here silently dropped the last hits before the Worker ever saw them.
        clean_text = result_text[:8000]
    else:
        clean_text = result_text[:2000]

    # Information-retrieval tools return MANY distinct facts; "absolute shortest"
    # collapsed them into content-free generalities and silently dropped items
    # (observed: a search covering an ongoing war summarized as "an outlook focusing
    # on GDP, inflation and risks"). Those tools get a completeness rule instead;
    # every other tool keeps the terse formatting (also keeps latency down, since
    # generation time scales with output length).
    _RETRIEVAL_TOOLS = {
        "stealth_search", "smart_scrape", "read_document",
        "search_gmail", "get_gmail_message", "get_gmail_thread",
    }
    if tool_name in _RETRIEVAL_TOOLS:
        lead_line = ("Turn this tool output into a clear, information-dense answer.\n")
        rule_one = (
            "1. COMPLETENESS OVER BREVITY: cover EVERY distinct item/finding in the raw "
                "result — do not merge them into one vague sentence and do not silently drop any. "
                "Keep the concrete specifics: dates, numbers, names, places, sources. "
                "One short bullet per item; no conversational filler.\n"
                "1b. DATES: state each item's Published date. If an item says Published: UNKNOWN, "
                "say the date is unknown — NEVER guess or infer one. If an item is clearly older "
                "than the user's timeframe, say how old it is instead of implying it is fresh.\n"
                "1c. If an item has no specific content (a section/landing page), omit it rather "
                "than writing a hollow line about it.\n")
    else:
        lead_line = "Format this tool output into the absolute shortest, clearest response possible.\n"
        rule_one = "1. Be extremely concise. Give just the requested data. No conversational filler.\n"

    _rc = ContextAssembler()
    _rc.add("recent_turns", self._recent_turns_block(), P_HELPFUL)
    _recent_block, _recent_rep = _rc.render(budget_tokens=CONTEXT_RECENT_TURNS_BUDGET)
    if _recent_rep.dropped:
        self._log_thought("CONTEXT", "recent_turns_dropped", _recent_rep.summary())
    _recent_section = (f"[RECENT CONVERSATION — context only]:\n{_recent_block}\n\n"
                       if _recent_block else "")

    format_task = (
        f"{self.ciel_persona}\n\n"
            f"{self._profile_block()}"
            f"{lead_line}"
            f"{_recent_section}"
            f"[USER LANGUAGE: {self._detect_language(user_input)}]\n"
            f"User's request: {user_input}\n"
            f"Tool: {tool_name}\n"
            f"Raw result:\n{clean_text}\n\n"
            f"Hint: {response_hint}\n"
            f"RULES:\n"
            f"{rule_one}"
            f"2. Address the Master by name only where it falls naturally in a sentence. Do NOT "
            f"open every response with 'Master,' — mechanical repetition of it is what made "
            f"replies read like a form letter. A bare answer is fine when the answer is one line.\n"
            f"3. ANTI-HALLUCINATION: ONLY use facts present in the Raw result above. "
            f"If the raw result contains an error, 'file not found', 'N/A', or is empty, "
            f"report the error honestly to Master. Say 'the data is unavailable' or 'the tool returned an error'. "
            f"NEVER invent, fabricate, or simulate data that is not in the raw result. "
            f"NEVER generate fake file contents, fake statistics, or fake execution output.\n"
            f"4. NO PROCESS NARRATION: Do NOT write status lines like 'Retrieving...', "
            f"'Scanning...', 'Initiating...', 'Fetching...'. Report ONLY the final data/facts.\n"
            f"5. LANGUAGE: Reply in [USER LANGUAGE] above, no matter what language the Raw "
            f"result itself is written in. Many tools return English status strings ('Fact "
            f"saved successfully', 'No fact found for key...') even for a Vietnamese request — "
            f"translate the MEANING into the Master's language; never mirror the tool's own "
            f"wording/language verbatim, and never echo a raw key name — write a real sentence."
    )
    self._log_thought("WORKER", "format_task", format_task)
    formatted = self.worker.generate(format_task)
    self._log_thought("WORKER", "format_response", formatted)
    return formatted
