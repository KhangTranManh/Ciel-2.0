"""Execution service extracted from CielCore; compatibility imports are lazy."""
from __future__ import annotations

def execute_multi_tool(self, tools: list, response_hint: str, user_input: str = "",
                       model_requested: bool = False) -> str:
    """Execute multiple tools sequentially and synthesize the result.

        Special handling for send_gmail_message: execute other tools first, synthesize
        the final message body, then execute send with the synthesized body so the
        actual email contains the real content (not a placeholder from the initial plan).

        `model_requested` is the plan's optional `needs_followup` hint, forwarded to the
        Tier-1 loop. It can only ADD a reason to look again — never override a budget.
        """
    from core.harness.signals import _has_unsynthesized_placeholder
    from agent_system.utils.logger import log
    checked = self._validate_tool_plan(tools, source="initial plan")
    if not checked.ok:
        return checked.error_text()
    tools = checked.steps

    # Separate the send step if present (usually the last step for email requests).
    # Supports plain send_gmail_message and rich send_gmail_html_message.
    # Also separate send_telegram (same "synthesize then deliver" shape — live
    # 2026-08-06: Brain planned only read_file while user asked "gửi … qua Telegram").
    # Also separate a DEFERRED WRITE step: a write_file/append_file whose content
    # is a synthesis marker — it must run AFTER the report is synthesized so the
    # file receives the real report, not the placeholder.
    send_tool = None
    telegram_tool = None
    deferred_write = None
    other_tools = []
    for t in tools:
        name = t.get("tool_name")
        if name in ("send_gmail_message", "send_gmail_html_message"):
            send_tool = t
        elif name == "send_telegram":
            telegram_tool = t
        elif name in ("write_file", "append_file") and _has_unsynthesized_placeholder(str(t.get("tool_args", {}).get("content", ""))):
            deferred_write = t
        else:
            other_tools.append(t)

    # Resolve a referential recipient ("gửi qua email đó đi") HERE, at the top,
    # not just before the send call below. Found live: correcting `to` only right
    # before sending was too late — the report body had ALREADY been synthesized
    # from the stale pre-correction context, so a real email to the CORRECTED
    # recipient (prokxcpro@gmail.com) carried the sentence "Đã gửi báo cáo giá vàng
    # ... đến kxctran@gmail.com" — a body that talks about a different address
    # entirely, sent to a real person. Fixing `to` without fixing what the body
    # says about `to` is not actually fixed.
    recipient_override_note = ""
    if send_tool:
        original_to = str(send_tool.get("tool_args", {}).get("to", "")).strip()
        corrected_args = self._resolve_referential_recipient(
            send_tool.get("tool_name", "send_gmail_message"),
            send_tool.get("tool_args", {}), user_input)
        if corrected_args is not send_tool.get("tool_args"):
            send_tool = dict(send_tool)
            send_tool["tool_args"] = corrected_args
            # The override fixes WHO the mail goes to, but the report-body
            # synthesis below still runs on the same `response_hint` the Router
            # wrote while confused about which recipient/topic "đó" meant — found
            # live producing a body that told prokxcpro@gmail.com "Đã gửi báo cáo
            # giá vàng ... đến kxctran@gmail.com". Fixing `to` alone is not a fix
            # if the BODY still talks about a different address. Ground the
            # synthesis prompt in the corrected fact instead of trusting it to
            # have followed the same reasoning.
            recipient_override_note = (
                f"\n9. CONFIRMED RECIPIENT: this email's real recipient is "
                    f"{corrected_args.get('to')} — NOT {original_to} or any other "
                    f"address that may appear elsewhere in this prompt. Do not "
                    f"mention, address, or narrate the status of any email to a "
                    f"different address in the body.")

    results = []
    step_outputs = []   # raw result of each executed step, for {{prev}}/{{step_N}} refs
    records = []        # (tool, args, result) per step — the agent loop's observations

    # TIER 3 — review the WHOLE plan before running any of it, so the Master sees
    # what is about to happen instead of being stopped after step 2 of 4 with the
    # first two already carried out. Returns early on refusal: nothing has run yet.
    blocked = self._review_plan_permissions(tools)
    if blocked:
        return blocked

    # Execute non-send tools first. Dependent chains ({{prev}}/{{step_N}}) are
    # resolved per step inside _run_steps, which also batches provably-independent
    # steps to run concurrently.
    # A multi-tool plan has exactly one response-side synthesis boundary. Passing
    # every data step through the Worker first turns raw evidence into separate
    # opinions; the final Worker then reconciles opinions instead of original facts.
    # Keep step outputs raw for every multi-tool plan, not only delivery plans.
    self._run_steps(other_tools, response_hint, user_input, results, step_outputs, records,
                    skip_format=True)

    # TIER-1 AGENT LOOP — observe what actually came back and, only when a
    # deterministic signal says the plan could not have been complete, plan again
    # with those results in view. Placed HERE on purpose: any send/deferred-write
    # step was separated out above and still runs once, after synthesis, so extra
    # rounds can enrich the data a report is built from but can never double-send.
    self._continue_until_done(user_input, response_hint, records, results, step_outputs,
                              model_requested=model_requested, skip_format=True)

    manager = getattr(self, "run_manager", None)
    delivery_blocked = False
    if manager is not None and manager.active is not None:
        if self.cancelled:
            return "[CANCELLED] Work stopped before final synthesis or delivery."
        if manager.active.failures or manager.active.limited or self._pending_action:
            # A synthesized paragraph is not proof that missing source data or an
            # unexecuted approval step succeeded. Preserve the partial answer but
            # never dispatch a dependent outbound action from it.
            delivery_blocked = bool(send_tool or telegram_tool or deferred_write)
            send_tool = telegram_tool = deferred_write = None

    combined_results = "\n\n".join(results)

    format_task = f"""{self.ciel_persona}

{self._profile_block()}

Synthesize the following data from multiple tools into a cohesive report.
User's request: {user_input}
{combined_results}

Hint: {response_hint}
RULES:
1. Be concise. Deliver a unified report without conversational filler.
2. For chat or internal reports: address the Master by name only where it falls naturally in a sentence — never as a mandatory opener on every response. For email body, do NOT include ANY "Master" greeting or internal addressing — that is addressed to an outside recipient, and internal forms of address must never leak into it. Make it a clean professional email.
3. ONLY use facts present in the tool outputs above. NEVER invent data. NEVER copy any
   numbers, prices, or values from the persona/system-prompt EXAMPLES — those are
   illustrative placeholders. Every number in your output must come from a tool result in THIS run.
4. If any tool returned an error, 'file not found', or empty result, report that honestly. Do NOT fabricate fake data, fake file contents, or fake execution output.
5. NEVER disclose internal file paths (agent_output/, ciel_workspace/, etc.) in the final report or email body sent to external parties. Use only generic professional language such as 'the detailed evaluation has been prepared' or provide the content directly in the message. Do not reference storage locations.
6. ONLY claim that an email was sent (e.g. "Đã gửi", "email sent", "Message sent") if there is a successful send_gmail_message tool result with a Message Id in the outputs above. If there is no such result, do NOT narrate delivery status AT ALL — do not write "chưa gửi", "not sent yet", "chưa có kết quả gửi" or any equivalent. A send step in this plan may still be pending and will run AFTER you write this; the system appends the real outcome itself once it knows. Just write the report.
7. For any email send (market or other), synthesize a professional email body based on the user's exact request and the real data from tools. Make it clear, well-structured, polite and useful like a proper sent email (use Vietnamese if appropriate). Do not force any specific dashboard template or HTML structure unless the user explicitly requested visual/dashboard style. Use ONLY real data from this run's tool results. Never leave [brackets], meta tags, or invent numbers.
8. If this is an email send, your ENTIRE output IS the email body and will be sent verbatim. Output ONLY the email body — start directly with the subject/greeting. Do NOT include: chain-of-thought or "[COGNITION]"/"[MARKET_DATA]"-style tag prefixes; any statement that the email was/wasn't sent or any "Message Id"; any label like "Email body:", "Nội dung email:", "Lưu ý:"; any nested/duplicated copy of the email; any note about tools, file writes, or storage paths.{recipient_override_note}
"""
    self._log_thought("WORKER", "multi_tool_format_task", format_task)
    formatted = self.worker.generate(format_task)
    self._log_thought("WORKER", "multi_tool_format_response", formatted)

    # The pure synthesized report — used for the file write and the email body.
    # `formatted` accumulates [FILE]/[EMAIL] status notes for the Master's display
    # only; those notes must never end up inside the written file or sent email.
    report_body = formatted

    # Re-execute the deferred write step with the synthesized report, so the
    # file the user asked for actually receives real content. Clean it first:
    # strip [COGNITION]/tag prefixes and signature placeholders (keep internal
    # paths — harmless inside a local workspace file).
    if deferred_write:
        w_name = deferred_write.get("tool_name", "write_file")
        w_args = dict(deferred_write.get("tool_args", {}))
        w_args["content"] = self._sanitize_outbound_email(report_body, keep_paths=True)
        log.tool(f"Re-executing {w_name} with synthesized report content")
        w_res = self.execute_tool(w_name, w_args, response_hint=response_hint, user_input=user_input)
        self._log_thought("TOOL", f"result_{w_name}", w_res)
        results.append(f"--- Output from {w_name} ---\n{w_res}")
        formatted = formatted.rstrip() + f"\n\n[FILE] {w_res}"

    # Re-execute the send step with real content built after the data tools ran.
    if send_tool:
        send_name = send_tool.get("tool_name", "send_gmail_message")
        send_args = dict(send_tool.get("tool_args", {}))
        send_args = self._enforce_subject(send_args, user_input)
        send_args = self._resolve_referential_recipient(send_name, send_args, user_input)

        # The display copy `formatted` (which Master sees) keeps persona markers;
        # the outbound body is sanitized centrally in execute_tool, so just pass
        # the synthesized content through as-is here.
        if send_name == "send_gmail_html_message":
            # Use the synthesized professional content as HTML body (synthesis already made it clean/professional)
            # Only use special dashboard builder if explicitly planned with build_market_report_html earlier
            send_args["html_body"] = report_body
            send_args.pop("message", None)
        else:
            send_args["message"] = report_body

        log.tool(f"Re-executing {send_name} with real content")
        send_res = self.execute_tool(send_name, send_args, response_hint=response_hint, user_input=user_input)
        self._log_thought("TOOL", f"result_{send_name}", send_res)
        results.append(f"--- Output from {send_name} ---\n{send_res}")
        # Claim sent ONLY when a real Message Id is present (no loose 'sent' substring).
        if "Message Id" in send_res:
            # The body was synthesized BEFORE this send ran, so at that moment there
            # was no Message Id and the anti-fabrication rule correctly made the
            # Worker write "chưa gửi … chưa có kết quả gửi thực tế". That sentence is
            # now stale, and appending the truth underneath it left the Master reading
            # both — a denial and a confirmation of the same send. Observed live: the
            # mail went out (Message Id 19f9efdcfee2657d) while the reply opened with
            # "Chưa gửi email tới kxctran@gmail.com".
            #
            # Removed deterministically, and only here, where the real outcome is
            # already known from the tool result — never by asking the model to
            # remember not to mention it.
            formatted = self._strip_stale_send_status(formatted)
        formatted = self._email_delivery_note(send_args, send_res, user_input) + "\n\n" + formatted.rstrip()

    # Telegram: same synthesize-then-send as email (plain text, capped length).
    if telegram_tool:
        tg_args = dict(telegram_tool.get("tool_args") or {})
        # Prefer short body for chat app; keep MARKER/facts from synthesis
        body = (report_body or "").strip()
        if len(body) > 3500:
            body = body[:3490] + "…"
        tg_args["message"] = body or tg_args.get("message") or "(empty report)"
        log.tool("Re-executing send_telegram with synthesized content")
        tg_res = self.execute_tool(
            "send_telegram", tg_args, response_hint=response_hint, user_input=user_input
        )
        self._log_thought("TOOL", "result_send_telegram", tg_res)
        results.append(f"--- Output from send_telegram ---\n{tg_res}")
        if "successfully" in (tg_res or "").lower() or "message_id=" in (tg_res or "").lower():
            formatted = formatted.rstrip() + "\n\n[TELEGRAM] Sent successfully."
        else:
            formatted = formatted.rstrip() + f"\n\n[TELEGRAM] Not confirmed: {tg_res}"

    if delivery_blocked:
        formatted = formatted.rstrip() + "\n\n[RUN_BLOCKED] Delivery/write was skipped because a source step failed, the run reached its limit, or approval is pending."
    return formatted
