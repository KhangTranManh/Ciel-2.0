"""Conversation lifecycle; public CielCore.process delegates here."""
from __future__ import annotations

from .turn_context import prepare_decision
from .dispatch import dispatch_decision
from .plan_normalization import normalize_decision


def process_turn(self, user_input: str) -> str:
    """Full pipeline: recall → route → execute → respond."""
    import json
    from core import rag_manager
    import traceback
    from agent_system.utils.logger import log
    self.chat_history.add_user_message(user_input)
    # TIER 5 — a cancellation belongs to the request that was cancelled. Clearing it
    # here (not when it fires) means a Ctrl+C that lands between turns cannot silently
    # kill the NEXT request the Master types.
    self.clear_cancel()
    # A new request may legitimately mail the same person again.
    with self._log_lock:
        self._sent_this_turn = set()
        self._telegram_delivery_sent_this_turn = False
    self._clear_active_lookup_for_new_turn(user_input)
    try:
        active_subject_note = self.active_subject.begin_turn()
    except Exception as subject_err:
        active_subject_note = ""
        self._log_thought(
            "CONTEXT", "active_subject_begin_failed",
            f"{type(subject_err).__name__}: {str(subject_err)[:160]}",
        )

    # TIER 7b — learn a durable preference from this turn, if there is one. Placed
    # at the TOP rather than at the end because `process()` has many return points
    # and a hook on only some of them would learn inconsistently; the gate is free
    # Python and the work runs on a daemon thread, so this costs the turn nothing.
    self._learn_from_turn(user_input)

    # === PENDING CONFIRMATION (runs FIRST — before any routing) ===
    # A preview tool promised the Master that "yes" would carry out the action, so
    # an affirmation must resolve THAT action, never fall through to the Brain,
    # which has neither chat_history nor (for such short inputs) RAG recall and
    # therefore asks the Master to re-state details it just printed itself.
    # The executed tool still goes through execute_tool, so its Safety-Gate Y/N
    # remains in force — this resolves context, it does not bypass any gate.
    # "Đang làm gì?" / "what were you doing?" — answered from the task store, before
    # routing, with no LLM call at all.
    if self._STATUS_QUERY_RE.match(user_input or ""):
        reply = self._answer_status_query()
        self._log_thought("SYSTEM", "status_query", reply[:200])
        self.chat_history.add_ai_message(reply)
        self._save_chat_memory()
        return reply

    pending = self._get_pending_action()
    if pending:
        if self._CANCEL_RE.match(user_input or ""):
            self._clear_pending_action()
            manager = getattr(self, "run_manager", None)
            if manager is not None:
                manager.store.resolve_pending(pending, cancelled=True)
            self._log_thought("SYSTEM", "pending_action_cancelled",
                              f"Master declined {pending['tool']} — cleared.")
            reply = f"Cancelled. {pending['tool'].replace('_', ' ')} was not carried out, Master."
            self.chat_history.add_ai_message(reply)
            self._save_chat_memory()
            return reply
        if self._AFFIRM_RE.match(user_input or ""):
            pending_source = str(pending.get("from") or "")
            self._clear_pending_action()
            if pending_source.startswith("deferred-reissue:"):
                # The Master has explicitly taken ownership of this old background
                # request.  From here it becomes a normal attended action and still
                # passes through execute_tool's full safety confirmation.
                self.deferred.resolve(pending_source.split(":", 1)[1])
            self._log_thought("SYSTEM", "pending_action_confirmed",
                              f"Affirmation resolved to {pending['tool']}({pending['args']}).")
            log.system(f"Confirmation accepted → executing {pending['tool']}")
            def confirmed_action():
                return self.execute_tool(pending["tool"], pending["args"],
                                         response_hint="Report the outcome of the confirmed action.",
                                         user_input=user_input)
            manager = getattr(self, "run_manager", None)
            if manager is not None:
                reply = manager.execute(
                    user_input,
                    {"action": "tool", "tool_name": pending["tool"], "tool_args": pending["args"]},
                    confirmed_action,
                )
                manager.store.resolve_pending(pending, cancelled=False, followup_run=manager.last_run_id)
            else:
                reply = confirmed_action()
            self.chat_history.add_ai_message(reply)
            self._save_chat_memory()
            return reply
        # SINGLE-SLOT: neither a clean yes nor a clean no. Rather than let a
        # second risky request slip past the Brain (which has no chat_history and
        # could stage or run something else while this one is still unresolved),
        # block deterministically and make the Master resolve THIS one first.
        self._log_thought("SYSTEM", "pending_action_blocking",
                          f"New input while {pending['tool']} still pending — held for resolution.")
        reply = (f"There's already a pending confirmation for **{pending['tool'].replace('_', ' ')}** "
                 f"(args: {pending['args']}), Master. Reply \"yes\"/\"confirm\" to proceed or "
                 f"\"no\"/\"cancel\" to abort it before I take on anything new.")
        self.chat_history.add_ai_message(reply)
        self._save_chat_memory()
        return reply

    # A deferred action deliberately never runs merely because the process was
    # restarted.  When the Master names its exact tool and asks to continue it,
    # restore the saved context into the regular pending-confirmation path instead
    # of asking the Brain to guess recipient, subject, or body from a one-line
    # timestamp.  A subsequent affirmative answer still reaches the normal safety
    # preview before the action can execute.
    deferred_item = self.deferred.match_explicit_reissue(user_input)
    if deferred_item:
        tool_name = str(deferred_item.get("tool") or "")
        tool_args = dict(deferred_item.get("args") or {})
        if tool_name and tool_args:
            self._set_pending_action(
                tool_name, tool_args,
                source=f"deferred-reissue:{deferred_item.get('id')}",
            )
            preview = json.dumps(tool_args, ensure_ascii=False, indent=2)
            self._log_thought(
                "SYSTEM", "deferred_reissue_staged",
                f"Restored {tool_name} from deferred item {deferred_item.get('id')} for attended review.",
            )
            reply = (
                f"Đã khôi phục ngữ cảnh đã lưu cho {tool_name}:\n{preview}\n\n"
                "Đây vẫn là yêu cầu cũ, nên chưa có gì được gửi. Trả lời “yes”/“confirm” "
                "để dùng nó như một yêu cầu mới; Ciel sẽ hiện bước duyệt an toàn cuối cùng. "
                "Trả lời “no”/“cancel” để giữ nguyên mục đã hoãn."
            )
            self.chat_history.add_ai_message(reply)
            self._save_chat_memory()
            return reply

    # === NEW PATH CLARIFICATION LOGIC ===
    # If user wants to write/create/save/generate a file but didn't specify where,
    # ask for the path first. If the question already contains a path ("where"),
    # proceed normally.
    lowered = user_input.lower()
    write_intent_keywords = ["write", "create", "save", "generate", "make a file", "output to", "write to", "append to"]
    has_where = any(kw in lowered for kw in ["ciel_workspace", "agent_output", " in ", " to ", " at ", ".py", ".txt", ".json", ".md", ".log"])

    if any(kw in lowered for kw in write_intent_keywords) and not has_where:
        return ("Understood. Where should I write this?\n"
                "Please reply with the full path, for example:\n"
                "• ciel_workspace/my_notes.txt\n"
                "• agent_output/my_script.py\n"
                "Or any other path inside those folders.")

    # RAG RECALL: Search long-term memory for relevant past context
    recalled = rag_manager.search_similar(user_input)
    if recalled:
        recalled = self._refine_recalled_context(recalled)
        self._log_thought("RAG", "recalled", recalled[:300])

    try:
        decision, recalled = prepare_decision(self, user_input, recalled, active_subject_note)
        action = decision.get("action", "chat")

        # TIER 2: open a task record for anything that actually DOES something.
        # Pure chat needs no record — nothing can be left half-finished by it.
        if action in ("tool", "multi_tool", "code"):
            self.tasks.start(user_input)

        decision = normalize_decision(self, decision, user_input)
        action = decision.get("action", "chat")

        manager = getattr(self, "run_manager", None)
        if manager is not None:
            response = manager.execute(
                user_input, decision,
                lambda: dispatch_decision(self, decision, user_input, recalled),
            )
        else:
            response = dispatch_decision(self, decision, user_input, recalled)

        self.chat_history.add_ai_message(response)
        self._save_chat_memory()
        self.session_compact.schedule_refresh(
            self.chat_history.messages, self.worker.generate, self._log_thought,
        )
        try:
            updated_subject = self.active_subject.complete_turn(user_input, response, action)
            if updated_subject:
                self._log_thought(
                    "CONTEXT", "active_subject_updated",
                    f"topic={updated_subject.topic[:120]!r} "
                    f"action={updated_subject.last_action!r} idle={updated_subject.idle_turns}",
                )
        except Exception as subject_err:
            self._log_thought(
                "CONTEXT", "active_subject_commit_failed",
                f"{type(subject_err).__name__}: {str(subject_err)[:160]}",
            )

        # TIER 2: close the record. A staged confirmation is NOT completion — the
        # job is waiting on the Master, so it stays visible as `blocked` and turns
        # up in describe_unfinished() if the session ends here.
        if self._pending_action:
            self.tasks.finish("blocked",
                              f"waiting for your confirmation of "
                              f"{self._pending_action['tool'].replace('_', ' ')}")
        else:
            self.tasks.finish(manager.legacy_task_state() if manager is not None else "done")

        # A plan-scoped approval must not outlive its plan — see permissions.py.
        self.permissions.clear_plan_grants()
        return response

    except Exception as e:
        log.error(f"Pipeline error: {e}")
        traceback.print_exc()
        self.tasks.finish("failed", f"{type(e).__name__}: {str(e)[:120]}")
        self.permissions.clear_plan_grants()
        action = "provider_timeout" if self._is_timeout_exception(e) else "pipeline_error"
        self._log_thought("SYSTEM", action, f"{type(e).__name__}: {str(e)[:200]}")
        return self._pipeline_error_response(e, user_input)
