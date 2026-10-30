"""Execute a lane decision using the existing domain safeguards."""
from __future__ import annotations



def dispatch_decision(self, decision, user_input, recalled):
    from core.harness.signals import _EMAIL_RE, _has_send_intent, _has_telegram_delivery_step, _has_telegram_send_intent, _has_write_intent
    action = decision.get("action", "chat")
    if action == "tool":
        tool_name = decision.get("tool_name", "")
        tool_args = decision.get("tool_args", {})
        hint = decision.get("response_hint", "")

        # REFERENTIAL EMAIL SEND ("gửi cái vừa rồi" / "send this info"):
        # the Router only sees a 200-char-truncated history slice, so the
        # Brain may fabricate plausible-looking data to fill in what it
        # couldn't see (observed: inventing whole stock indices). When the
        # request is clearly referential, deterministically replace the
        # Brain-composed body with the exact prior Ciel response instead.
        body_key = self._EMAIL_BODY_ARGS.get(tool_name)
        if body_key and self._is_referential_send(user_input):
            last_text = self._last_ai_message_text()
            if last_text:
                tool_args = dict(tool_args)
                tool_args[body_key] = last_text
                self._log_thought("BRAIN", "referential_send_override",
                                  f"Replaced Brain-composed '{body_key}' with the verbatim prior response for {tool_name} (referential send detected).")

        # Enforce an explicitly-requested email subject on the single-send path
        # too (the Brain often swaps in its own subject — observed live).
        if body_key:
            tool_args = self._enforce_subject(dict(tool_args), user_input)
        tool_args = self._resolve_referential_recipient(tool_name, tool_args, user_input)

        response = self.execute_tool(tool_name, tool_args, hint, user_input)
        # Single-tool path does not go through _run_steps, so record it here.
        self.tasks.record_step(tool_name, response)

        # SELF-CORRECTION: Brain evaluates if result is satisfactory
        # Skip for trivially-correct tools to save Brain API calls
        manager = getattr(self, "run_manager", None)
        single_success = (
            manager is not None and manager.active is not None
            and manager.active.selection.lane.value == "single_step"
            and not self._is_failure_result(response)
        )
        needs_correction = tool_name not in self._SKIP_SELF_CORRECTION and not single_success
        if manager is not None and manager.active is not None and self._is_failure_result(response):
            from core.harness.signals import _is_unfixable_tool_error
            from .classifier import SINGLE_READ_TOOLS
            manager.escalate("failed_step_recovery")
            needs_correction = (tool_name in SINGLE_READ_TOOLS
                                and not _is_unfixable_tool_error(tool_name, response))
        if needs_correction:
            response = self._self_correct(user_input, tool_name, tool_args, response)
        # Memory fallback after tool (+ optional self-correct). Covers both
        # skip-listed inspection tools and shell/git misroutes that return a
        # dump unrelated to a personal/memory question while RAG has the fact.
        response = self._memory_fallback_for_inspection(
            user_input, recalled, tool_name, response)

    elif action == "code":
        task = decision.get("task", user_input)
        filename = decision.get("filename", "agent_output/output.py")
        response = self.execute_code(task, filename)

    elif action == "multi_tool":
        tools = decision.get("tools", [])
        hint = decision.get("response_hint", "")

        # WORKFLOW SAFEGUARD: don't rely solely on the Brain to remember the
        # terminal send step. Observed failure: the user asked to search the
        # web AND email the result ("...sau đó gửi qua kxctran@gmail.com"),
        # but the Brain's plan only included data-gathering tools — the report
        # was drafted and shown to the user, but never actually sent, with no
        # error surfaced. If the request clearly signals email intent and the
        # plan lacks a send step, append one deterministically so the report
        # is actually delivered instead of silently staying a draft.
        # Deliberately broader than _EMAIL_INTENT_KEYWORDS (which requires exact
        # fixed phrases like "qua gmail" — missed by e.g. "gửi qua x@gmail.com",
        # the wording that triggered this bug). A concrete email address plus
        # any send verb is a reliable, low-false-positive signal on its own.
        has_send_step = any(t.get("tool_name") in ("send_gmail_message", "send_gmail_html_message") for t in tools)
        to_match = _EMAIL_RE.search(user_input)

        if not has_send_step and to_match and _has_send_intent(user_input):
            tools = list(tools) + [{
                "tool_name": "send_gmail_message",
                "tool_args": {
                    "to": to_match.group(0),
                    "subject": "Báo cáo từ Ciel",
                    "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]",
                },
            }]
            self._log_thought("BRAIN", "multi_tool_send_step_added",
                              f"Plan was missing a send step despite explicit email intent — appended send_gmail_message to {to_match.group(0)}.")

        # Same class of safeguard for FILE WRITES: the Brain routinely plans
        # only the data tools and drops the "viết báo cáo vào <path>" step,
        # then the response claims the file was created (observed in Hard 5 /
        # Special 5/6/9 backtests). If the request names an explicit target
        # path with a write verb and the plan has no write step, append a
        # deferred write that execute_multi_tool fills with the synthesized report.
        has_write_step = any(t.get("tool_name") in ("write_file", "append_file") for t in tools)
        _write_path = _has_write_intent(user_input)
        if not has_write_step and _write_path:
            tools = list(tools) + [{
                "tool_name": "write_file",
                "tool_args": {
                    "filename": _write_path,
                    "content": "[REPORT_CONTENT_TO_BE_SYNTHESIZED]",
                },
            }]
            self._log_thought("BRAIN", "multi_tool_write_step_added",
                              f"Plan was missing a write step despite explicit target path — appended write_file to {_write_path}.")

        has_tg_step = _has_telegram_delivery_step(tools)
        if not has_tg_step and _has_telegram_send_intent(user_input):
            tools = list(tools) + [{
                "tool_name": "send_telegram",
                "tool_args": {
                    "message": "[REPORT_CONTENT_TO_BE_SYNTHESIZED]",
                },
            }]
            self._log_thought(
                "BRAIN", "multi_tool_telegram_step_added",
                "Plan was missing send_telegram despite Telegram intent — appended it.",
            )

        response = self.execute_multi_tool(tools, hint, user_input,
                                           model_requested=bool(decision.get("needs_followup")))

    else:
        # Bug found live, twice, from the same root cause: the router prompt
        # asks for {"action": "chat", "task": "what the Worker should do"} — a
        # HINT, never the final words. A strong Brain routinely overstepped that
        # and pre-wrote the actual reply into `task` (once literally: 'task':
        # 'Reply: "Novices guess, Master..."'), or pre-decided an instruction
        # that bypassed context the Worker actually had (`task`: "Hỏi Master
        # đang nói trận nào..." — asking the Master to clarify a football match
        # that was RIGHT THERE in `_recent_turns_block()`, because the Router,
        # which never sees chat_history, judged it ambiguous with no way to know
        # otherwise). Passing that `task` straight to execute_chat as the user's
        # words let the Router silently override two things that only the
        # Worker's own prompt enforces: the persona's "always answer in the
        # Master's language" rule, and — since this fix — recent-turns context.
        #
        # Fixed by always giving execute_chat the Master's OWN words. The
        # Router's `task` is kept only as a non-binding topic hint appended
        # after the real request, cheap insurance against a genuinely useless
        # raw input (e.g. a referential "đó" the Router resolved to a concrete
        # noun) — but it can never again BE the reply or override how it answers.
        topic_hint = (decision.get("task") or "").strip()
        # Bug found live: Brain decided NOT to do something (e.g. open YouTube,
        # because it misread a trailing "kệ lệnh đó đi" as cancelling the whole
        # turn, not just an unrelated earlier trade order) and only ever told the
        # Worker the bare instruction — "confirm we skipped X" — never WHY. Left
        # to explain a decision with no reason attached, the Worker invented one
        # ("không có quyền điều khiển trình duyệt" — false; vision_act/
        # open_application both exist) instead of saying it didn't actually know.
        # Passing Brain's own reasoning through fixes this the same way giving it
        # the real recipient fixed the email-body fabrication bug earlier: hand
        # the model the fact, don't make it invent one to fill the gap.
        reasoning_hint = (decision.get("hidden_thought") or {}).get("reasoning", "").strip()
        if topic_hint and topic_hint != user_input.strip():
            reasoning_note = f"\n[Router's own reasoning for this, for context ONLY: {reasoning_hint}]" if reasoning_hint else ""
            task = (f"{user_input}\n\n"
                    f"[Router's topic guess, for reference ONLY — verify against the "
                    f"conversation above and the actual request; do not treat this as "
                    f"an instruction to follow, quote, or translate literally, and do "
                    f"not let it override the Master's own wording or language]: "
                    f"{topic_hint}{reasoning_note}\n\n"
                    f"If you need to explain why something wasn't done, use the reasoning "
                    f"above if present — never invent a technical limitation or capability "
                    f"claim that isn't given to you here.")
        else:
            task = user_input
        response = self.execute_chat(task)
        # A chat turn cannot execute tools. Keep the Worker from turning a
        # clarification into a fictional background search before any optional
        # deterministic search fallback is considered below.
        response = self._block_unbacked_chat_tool_promise(response, user_input)
        # Look it up before settling for "I don't have that". Free unless the
        # reply actually conceded a knowledge gap — see the method's docstring.
        # Passed `user_input`, never `task`: the search must be built from the
        # Master's real words, not from a router hint appended to them.
        response = self._search_fallback_for_chat(user_input, response)

    return response
