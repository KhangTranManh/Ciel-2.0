"""Execution service extracted from CielCore; compatibility imports are lazy."""
from __future__ import annotations

def _run_steps(self, steps: list, response_hint: str, user_input: str,
               results: list, step_outputs: list, records: list, label: str = "",
               skip_format: bool = False) -> bool:
    """Execute `steps` in order, running provably-independent ones concurrently.

        Returns False if execution should stop early (a step was cancelled).

        Concurrency is opt-in and conservative (see core/parallel.py): only tools
        declared read-only, carrying no {step_N} reference and not high-risk, and only
        with each other. Everything else keeps running exactly as it did sequentially.
        Results are appended in the ORIGINAL order regardless of completion order, so
        {prev} / {step_N} keep pointing at what they always pointed at.
        """
    from core.llm_connector import AGENT_PARALLEL_ENABLED, AGENT_PARALLEL_MAX_WORKERS
    from core.continuation import StepRecord
    from concurrent.futures import ThreadPoolExecutor
    from agent_system.utils.logger import log
    from core.parallel import plan_batches
    batches = plan_batches(steps, self._parallel_safe, self._HIGH_RISK_TOOLS,
                           max_workers=AGENT_PARALLEL_MAX_WORKERS) \
        if AGENT_PARALLEL_ENABLED else [[s] for s in (steps or [])]
    manager = getattr(self, "run_manager", None)

    for batch in batches:
        prepared = []
        for t in batch:
            name = (t.get("tool_name") or "").strip()
            original_args = t.get("tool_args") or {}
            failed_dependency = self._failed_dependency(original_args, step_outputs)
            if failed_dependency:
                source_idx, reason = failed_dependency
                skipped = (
                    f"[SKIPPED_DEPENDENCY] {name} was not run because step "
                        f"{source_idx + 1} returned no usable value: {reason}"
                )
                if manager is not None:
                    manager.track_step(name, original_args, lambda: skipped)
                self._log_thought("TOOL", "dependency_skipped", skipped)
                step_outputs.append(skipped)
                results.append(f"--- Output from {name} ---\n{skipped}")
                records.append(StepRecord(name, original_args, skipped))
                self.tasks.record_step(name, skipped)
                continue
            args = self._resolve_step_refs(original_args, step_outputs)
            if args != (t.get("tool_args") or {}):
                self._log_thought("TOOL", "step_ref_resolved",
                                  f"{name}: injected prior step output into args.")
            prepared.append((name, args))

        if not prepared:
            continue

        # Captured HERE, in the submitting thread, because `unattended` is
        # thread-local: a worker thread starts with the default (attended), so
        # without carrying it across, a background plan's risky steps would be
        # confirmed-and-run instead of deferred. Fail-open on a safety control.
        unattended_here = self.unattended
        active_run = manager.active if manager is not None else None

        # TIER 5 — the cancellation point. Between batches, so whatever already ran
        # completed cleanly and whatever has not simply never starts.
        if self._abort_if_cancelled(f"before step batch ({len(prepared)} step(s))"):
            results.append("[CANCELLED] Master dừng yêu cầu này. "
                               "Các bước đã chạy xong vẫn giữ nguyên; phần còn lại không chạy.")
            return False

        def _run_tool(pair):
            name, args = pair
            self.unattended = unattended_here
            if name not in self._tool_map:
                err = f"[TOOL_ERROR] {name} not found."
                return err, err
            raw_box = []
            formatted = self.execute_tool(name, args, response_hint=response_hint,
                                          user_input=user_input, _raw_out=raw_box,
                                          _skip_format=skip_format)
            # Bug found live: a plan chaining search_gmail -> get_gmail_message via
            # {prev} got the WORKER'S HUMAN-READABLE SUMMARY ("Email từ Khang Trần,
            # tiêu đề ...") injected as message_id, instead of the real Gmail id —
            # a 400 "Invalid id value", then 3 self-healing attempts hallucinating
            # guesses ("XAUUSD", "XAU_USD") that were never going to work, because
            # the real id was sitting right there in raw_box the whole time.
            # step_outputs (below) gets the raw structured text; everything
            # human-facing keeps using `formatted` exactly as before.
            return formatted, (raw_box[0] if raw_box else formatted)

        def _run(pair):
            if manager is None:
                return _run_tool(pair)
            with manager.bind(active_run):
                return _run_tool(pair)

        if len(prepared) > 1:
            names = ", ".join(n for n, _ in prepared)
            log.tool(f"{label}Running {len(prepared)} independent steps in parallel: {names}")
            self._log_thought("TOOL", "parallel_batch",
                              f"{len(prepared)} independent steps concurrently: {names}")
            with ThreadPoolExecutor(max_workers=len(prepared)) as pool:
                outs = list(pool.map(_run, prepared))
        else:
            name, args = prepared[0]
            log.tool(f"{label}Executing step: {name}({args})")
            outs = [_run(prepared[0])]

        for (name, args), (res, raw) in zip(prepared, outs):
            self._log_thought("TOOL", f"result_{name}", res)
            step_outputs.append(raw)
            results.append(f"--- Output from {name} ---\n{res}")
            records.append(StepRecord(name, args, res))
            self.tasks.record_step(name, res)
            if res.startswith("[CANCELLED]"):
                return False
    return True
