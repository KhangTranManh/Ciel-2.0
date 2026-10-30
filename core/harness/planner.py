"""Execution service extracted from CielCore; compatibility imports are lazy."""
from __future__ import annotations

def _continue_until_done(self, user_input: str, response_hint: str, records: list,
                         results: list, step_outputs: list, model_requested: bool = False,
                         skip_format: bool = False):
    """TIER-1 AGENT LOOP: observe → re-plan → act, in place.

        Appends any follow-up steps' output to `records`/`results`/`step_outputs`, so
        the caller synthesizes over the full picture without knowing a loop happened.

        Three properties matter more than what the loop achieves, and all three are
        guaranteed by code rather than by the model behaving well:

          * BOUNDED — rounds, planner calls and wall-clock are all capped up front
            (see LoopBudget). A model that keeps insisting there is more to do simply
            runs out of budget.
          * MONOTONIC — a follow-up that only re-proposes work already done is dropped
            by novel_steps(), so a confused planner terminates the loop instead of
            ping-ponging forever.
          * FAIL-OPEN — every failure path here returns quietly, leaving the first
            round's results untouched. This can improve an answer; it can never leave
            the pipeline worse off than before the loop existed.

        Re-planning reuses Router.route() and the ordinary plan schema on purpose:
        no second output format the model has to learn, and therefore no new way for a
        weaker model to fail. Cost is one planner call per round, and only when a
        deterministic signal already said the plan looked incomplete.
        """
    from core.llm_connector import AGENT_LOOP_ENABLED, AGENT_LOOP_MAX_ROUNDS, AGENT_LOOP_MAX_SECONDS, AGENT_PARALLEL_ENABLED, AGENT_PARALLEL_MAX_WORKERS
    from core.continuation import ContinuationPolicy, LoopBudget, build_observation_block
    from core.parallel import plan_batches
    if not AGENT_LOOP_ENABLED:
        return

    budget = LoopBudget(max_rounds=AGENT_LOOP_MAX_ROUNDS,
                        max_replans=AGENT_LOOP_MAX_ROUNDS,
                        max_seconds=AGENT_LOOP_MAX_SECONDS)
    while True:
        # TIER 5 — checked before the planner call, not just before the steps: an
        # extra round that the Master already cancelled costs a full Brain call and
        # produces work nobody wants.
        if self._abort_if_cancelled("agent loop"):
            return
        verdict = ContinuationPolicy.assess(user_input, records, budget, model_requested)
        if not verdict.should_continue:
            # Only worth a log line once a loop was actually plausible; otherwise
            # every ordinary request would spam "plan looks complete".
            if budget.rounds_used:
                self._log_thought("LOOP", "done", verdict.reason)
            return
        manager = getattr(self, "run_manager", None)
        if manager is not None and not manager.may_continue():
            return
        self._log_thought("LOOP", "continue", verdict.reason)

        try:
            followup = (f"{user_input}\n\n"
                            f"{build_observation_block(user_input, records)}")
            budget.replans_used += 1
            decision = self.router.route(followup, self._tool_list_str, self.chat_history)
        except Exception as e:
            self._log_thought("LOOP", "replan_failed",
                              f"{type(e).__name__}: {str(e)[:160]} — keeping results so far.")
            return

        action = (decision or {}).get("action")
        if action not in ("tool", "multi_tool"):
            self._log_thought("LOOP", "planner_finished",
                              f"planner returned action={action} — nothing further to run.")
            return

        proposed = decision.get("tools") or []
        if not proposed and decision.get("tool_name"):
            proposed = [{"tool_name": decision["tool_name"],
                         "tool_args": decision.get("tool_args") or {}}]

        fresh = ContinuationPolicy.novel_steps(proposed, records)
        if not fresh:
            self._log_thought("LOOP", "no_progress",
                              "follow-up proposed only already-executed steps — stopping.")
            return

        budget.rounds_used += 1
        model_requested = bool(decision.get("needs_followup"))

        if len(fresh) > budget.max_steps_per_round:
            self._log_thought("LOOP", "round_trimmed",
                              f"planner proposed {len(fresh)} steps — running the first "
                                  f"{budget.max_steps_per_round} to stay inside the budget.")
            fresh = fresh[:budget.max_steps_per_round]

        checked = self._validate_tool_plan(fresh, source=f"loop round {budget.rounds_used}")
        if not checked.ok:
            self._log_thought("LOOP", "plan_rejected", checked.error_text())
            return
        fresh = checked.steps

        # A follow-up is a new plan.  It may contain a risky action that was not
        # part of the initial approval, so it gets its own deterministic review.
        blocked = self._review_plan_permissions(fresh)
        if blocked:
            self._log_thought("LOOP", "plan_blocked", blocked)
            return

        # Time is checked BEFORE each batch, not only between rounds: a round of
        # slow calls must be able to stop partway instead of overrunning the
        # ceiling wholesale (observed: 566s inside a single round).
        for batch in plan_batches(fresh, self._parallel_safe, self._HIGH_RISK_TOOLS,
                                  max_workers=AGENT_PARALLEL_MAX_WORKERS) \
                if AGENT_PARALLEL_ENABLED else [[s] for s in fresh]:
            if budget.out_of_time():
                self._log_thought("LOOP", "out_of_time",
                                  f"loop exceeded {budget.max_seconds:.0f}s — stopping mid-round "
                                      f"with the results gathered so far.")
                return
            if not self._run_steps(batch, response_hint, user_input, results,
                                   step_outputs, records,
                                   label=f"[loop {budget.rounds_used}/{budget.max_rounds}] ",
                                   skip_format=skip_format):
                self._log_thought("LOOP", "cancelled", "a follow-up step was declined — stopping.")
                return
            # A follow-up step that staged a confirmation hands control back to the
            # Master. Continuing would plan on top of an action that has not happened.
            if self._pending_action:
                self._log_thought("LOOP", "awaiting_confirmation",
                                  f"staged {self._pending_action['tool']} — stopping for the Master.")
                return
