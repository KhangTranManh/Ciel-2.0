"""Preserve missing-terminal-action and conditional-plan safeguards."""
from __future__ import annotations



def normalize_decision(self, decision, user_input):
    from core.llm_connector import AGENT_LOOP_ENABLED
    from core.continuation import ContinuationPolicy
    from core.harness.signals import _EMAIL_RE, _has_send_intent, _has_telegram_send_intent, _has_write_intent
    action = decision.get("action", "chat")
    # WORKFLOW SAFEGUARD (single-tool case): a compound "look something up,
    # then email me" request sometimes gets under-scoped by the Brain into a
    # single action="tool" call instead of a multi_tool plan — the send-step
    # safeguard below only runs for action=="multi_tool", so it never sees
    # this case, leaving the LLM's own self-correction (fixed 2-attempt
    # budget) as the only backstop. Found via scripts/prompt_harness.py: 84
    # recurring "gathered data but never sent the email" self-correction
    # triggers in thoughts.log, one confirmed case where self-correction
    # tried get_fact('email') on an empty vault and burned its last retry
    # without ever sending. Fix: promote to multi_tool up front so the
    # existing send-step safeguard (below) can append send_gmail_message.
    if action == "tool":
        _tool_name_pre = decision.get("tool_name", "")
        if _tool_name_pre not in ("send_gmail_message", "send_gmail_html_message"):
            _to_match_pre = _EMAIL_RE.search(user_input)
            if _to_match_pre and _has_send_intent(user_input):
                decision = dict(decision)
                decision["action"] = "multi_tool"
                decision["tools"] = [{
                    "tool_name": _tool_name_pre,
                    "tool_args": decision.get("tool_args", {}),
                }]
                action = "multi_tool"
                self._log_thought(
                    "BRAIN", "tool_promoted_to_multi_tool",
                    f"Single-tool plan ({_tool_name_pre}) had email send intent with no "
                    f"send step — promoted to multi_tool so the send-step safeguard can append it.",
                )

    # WORKFLOW SAFEGUARD (single-tool case), same shape as the email one just
    # above: "read X, add these skills, save as Y" routinely gets under-scoped
    # to a lone read_file — read_file is in _SKIP_SELF_CORRECTION, so nothing
    # ever notices the save never happened; Ciel just re-shows the unmodified
    # file. Promote to multi_tool so the existing write-step safeguard (in the
    # multi_tool branch below) can append the missing write_file.
    if action == "tool":
        _tool_name_pre2 = decision.get("tool_name", "")
        if _tool_name_pre2 not in ("write_file", "append_file") and _has_write_intent(user_input):
            decision = dict(decision)
            decision["action"] = "multi_tool"
            decision["tools"] = [{
                "tool_name": _tool_name_pre2,
                "tool_args": decision.get("tool_args", {}),
            }]
            action = "multi_tool"
            self._log_thought(
                "BRAIN", "tool_promoted_to_multi_tool",
                f"Single-tool plan ({_tool_name_pre2}) had save/write intent with no "
                f"write step — promoted to multi_tool so the write-step safeguard can append it.",
            )

    # Telegram deliver: "đọc file … rồi gửi tóm tắt qua Telegram" was planned as
    # lone read_file (SKIP_SELF_CORRECTION) — Master never got the TG message.
    # Promote so multi_tool can append send_telegram after data tools.
    if action == "tool":
        _tn = decision.get("tool_name", "")
        if _tn != "send_telegram" and _has_telegram_send_intent(user_input):
            decision = dict(decision)
            decision["action"] = "multi_tool"
            decision["tools"] = [{
                "tool_name": _tn,
                "tool_args": decision.get("tool_args", {}),
            }]
            action = "multi_tool"
            self._log_thought(
                "BRAIN", "tool_promoted_for_telegram",
                f"Single-tool plan ({_tn}) had Telegram-send intent — promoted to multi_tool.",
            )

    # TIER-1 LOOP REACHABILITY: a result-dependent request ("check X, and if
    # it's clean, commit") is exactly the shape a flat plan cannot express, so
    # the Brain typically under-scopes it to the first step alone — a single
    # action="tool". The loop lives in execute_multi_tool, so promote that case
    # here; otherwise the one request type this feature exists for would be the
    # one it never sees. Promotion is free when nothing further turns out to be
    # needed: the policy simply declines the round and synthesis proceeds.
    if action == "tool" and AGENT_LOOP_ENABLED and decision.get("tool_name"):
        # Distributive requests need this just as much as conditional ones:
        # "list the files, then read EACH one" was planned as a lone
        # list_workspace call, so it never reached execute_multi_tool and the
        # fan-out signal never got to run. Observed: the listing was returned
        # and not one file was read.
        if (ContinuationPolicy.request_is_conditional(user_input)
                or ContinuationPolicy.request_is_distributive(user_input)):
            decision = dict(decision)
            decision["action"] = "multi_tool"
            decision["tools"] = [{
                "tool_name": decision.get("tool_name", ""),
                "tool_args": decision.get("tool_args", {}),
            }]
            action = "multi_tool"
            self._log_thought(
                "BRAIN", "tool_promoted_for_loop",
                f"Request is result-dependent but planned as a single "
                f"{decision['tools'][0]['tool_name']} call — promoted to multi_tool "
                f"so the observe-then-continue loop can evaluate the outcome.",
            )

    return decision
