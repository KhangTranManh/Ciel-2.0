"""Execution service extracted from CielCore; compatibility imports are lazy."""
from __future__ import annotations

def _self_correct(self, user_input: str, tool_name: str, tool_args: dict, result: str, max_attempts: int = 2) -> str:
    """Brain evaluates tool result and tries alternative approach if unsatisfactory."""
    import json
    from core.continuation import StepRecord
    from agent_system.utils.logger import log
    tried_tools = {tool_name}
    original_tool = tool_name
    for attempt in range(max_attempts):
        manager = getattr(self, "run_manager", None)
        if manager is not None and not manager.may_continue():
            return result
        evaluation = self._evaluate_result(user_input, tool_name, tool_args, result)
        if evaluation.get("satisfied", True):
            if attempt > 0:
                log.system(f"Self-Correction satisfied after {attempt} correction(s)")
            return result

        reasoning = evaluation.get("reasoning", "Result was insufficient")
        new_action = evaluation.get("action", "chat")
        self._log_thought("BRAIN", "self_correction",
            f"Attempt {attempt+1}/{max_attempts}: {reasoning}\n"
                f"Previous: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})\n"
                f"Next: {new_action} → {evaluation.get('tool_name', evaluation.get('task', 'N/A'))}")
        log.brain(f"Self-Correction [{attempt+1}]: {reasoning[:80]}")

        if new_action == "tool":
            new_tool = evaluation.get("tool_name", "")
            new_args = evaluation.get("tool_args", {})
            new_hint = evaluation.get("response_hint", "")

            # Prevent infinite loop — don't retry same tool with same args
            if new_tool == tool_name and new_args == tool_args:
                self._log_thought("BRAIN", "self_correction", "Aborted: same tool+args, would loop.")
                return result

            cheaper = self._HEAVY_ESCALATION_TOOLS.get(new_tool)
            if cheaper and cheaper not in tried_tools:
                self._log_thought(
                    "SAFETY", "escalation_deferred",
                    f"Self-correction proposed '{new_tool}' (screen control) but the cheaper "
                        f"'{cheaper}' was never tried in this chain — ending self-correction here "
                        f"instead of auto-escalating. '{new_tool}' only runs on an explicit request.")
                return result

            new_result = self.execute_tool(new_tool, new_args, new_hint, user_input)
            tried_tools.add(new_tool)

            # Found live: a CV read via read_document succeeded (real, complete content)
            # but self-correction judged it "cut off midway" and escalated to
            # execute_shell_command — which itself failed twice (missing lib, then a
            # script bug). The old code unconditionally did `result = new_result` here,
            # discarding the original good result in favour of the new FAILED one, so the
            # Master was told "couldn't read it" about a file Ciel had already read fine.
            # Only replace the original when the escalation attempt actually did better —
            # a failed "fix" must never outrank data that already worked.
            if StepRecord("", {}, new_result).failed() and not StepRecord("", {}, result).failed():
                self._log_thought(
                    "BRAIN", "self_correction",
                    f"'{new_tool}' also failed — keeping the ORIGINAL result from "
                        f"'{tool_name}' instead of reporting failure over data that was fine.")
                return result

            manager = getattr(self, "run_manager", None)
            if manager is not None and not self._is_failure_result(new_result):
                manager.resolve_failures(original_tool)
            result = new_result  # HIDE ERROR: Only return the new successful result to the user
            # Update for next evaluation iteration
            tool_name = new_tool
            tool_args = new_args

        elif new_action == "chat":
            task = evaluation.get("task", user_input)
            # Inject the actual tool result so Worker doesn't hallucinate
            enriched_task = (
                f"{task}\n\n"
                    f"[ACTUAL DATA from previous tool '{tool_name}']:\n"
                    f"{result[:3000]}"
            )
            chat_response = self.execute_chat(enriched_task)
            return chat_response  # HIDE ERROR: Only return the new chat response to the user
        else:
            return result

    return result


def _evaluate_result(self, user_input: str, tool_name: str, tool_args: dict, result: str) -> dict:
    """Ask Brain to evaluate if a tool result satisfies the user's request."""
    import json
    from langchain_core.messages import HumanMessage, SystemMessage
    from core.harness.signals import SELF_CORRECTION_PROMPT
    # Ensure result is never empty (Gemini rejects empty content)
    safe_result = (result or "No output returned.").strip()
    if not safe_result:
        safe_result = "No output returned."

    # DETERMINISTIC FLOOR (runs BEFORE the LLM): an obviously-failed result
    # (error message, empty, "not found") must always trigger self-correction.
    # Doing this here — not after json.loads — means a malformed Brain response
    # (which lands in the except branch) can never rubber-stamp an error as satisfied.
    # "Must never be called satisfied" is NOT the same as "may only be explained".
    # This used to `return` here with action="chat" hardcoded, which short-circuited
    # the evaluation below — the ONE path that is handed `Available tools:` and can
    # therefore name a working alternative. So the single moment recovery mattered
    # most was the single moment it was impossible: a failed tool could only ever be
    # narrated, never retried differently.
    #
    # Found live: get_market_price("DXY") -> "Error: Could not find price for DXY"
    # -> forced action="chat" -> the Worker, told to "suggest a next step" with no
    # tool context of its own, invented "Mình chưa gọi được tra cứu web trực tiếp"
    # — false: stealth_search was loaded, working, and would have answered it.
    #
    # Now the evaluation still runs and may propose a real alternative, while
    # `satisfied` is forced False afterwards no matter what it replies — so the
    # floor's actual guarantee (a malformed reply can never rubber-stamp an error
    # as success) is preserved without also blocking every recovery.
    forced_unsatisfied = self._is_failure_result(result)
    if forced_unsatisfied:
        self._log_thought("BRAIN", "evaluate_override",
                          "Result matches a failure signal — forcing self-correction "
                              "(asking Brain for a better tool, not only an explanation).")

    honest_explain = {
        "satisfied": False,
        "reasoning": "Result is an error, empty, or not-found response.",
        "action": "chat",
        "task": ("Honestly explain to the Master that the previous attempt "
                     "did not return usable data, and suggest a next step."),
    }

    eval_request = (
        f"User's request: {user_input}\n"
            f"Tool used: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})\n"
            f"Tool result:\n{safe_result[:3000]}\n\n"
            f"Available tools: {self._tool_list_str}\n\n"
            f"Does this result adequately answer the user's request? Respond with JSON only."
    )
    try:
        messages = [
            SystemMessage(content=SELF_CORRECTION_PROMPT),
            HumanMessage(content=eval_request)
        ]
        response = self.brain._router_llm.invoke(messages)
        # Gemini occasionally returns content as a list of parts instead of a plain string
        _eval_content = response.content
        if isinstance(_eval_content, list):
            _eval_content = "".join(c.text if hasattr(c, "text") else str(c) for c in _eval_content)
        raw = _eval_content.strip()
        self._log_thought("BRAIN", "evaluate_result", raw)

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        if not raw:
            return dict(honest_explain) if forced_unsatisfied else {"satisfied": True}
        evaluation = json.loads(raw)
        if forced_unsatisfied:
            # The floor holds regardless of what Brain replied.
            evaluation["satisfied"] = False
            if evaluation.get("action") not in ("tool", "chat"):
                # It found no concrete alternative — the Master is still owed an
                # honest account of the failure rather than silence.
                return dict(honest_explain)
        return evaluation
    except (json.JSONDecodeError, Exception) as e:
        self._log_thought("BRAIN", "evaluate_result_error", str(e))
        # Fail-safe: assume satisfied — EXCEPT when the result was provably a
        # failure, where "satisfied" would hand the Master an error as an answer.
        return dict(honest_explain) if forced_unsatisfied else {"satisfied": True}


def _search_fallback_for_chat(self, user_input: str, response: str) -> str:
    """Look it up before giving up. Sibling of _memory_fallback_for_inspection —
        same shape, different missing rung: that one covers an inspection tool coming
        back empty, this one covers the CHAT path, where the Worker has no tools at
        all and an honest "I don't have that data" simply ends the turn.

        Observed live: "Biết game LOL không" -> "Biết chứ" -> "đang có chế độ gì mới
        ko" -> "Mình chưa có dữ liệu live để xác nhận chính xác." -> the Master had to
        type "tra cứu đi" himself, for a question stealth_search answers in one call.

        Every trigger condition is free Python, so an ordinary turn — where the model
        answered normally — costs exactly nothing. Only a reply that already conceded
        a knowledge gap pays for the search. Bails out (returning the original honest
        answer untouched) on: a capability refusal rather than a knowledge gap, a
        non-question, a question about the Master's own stored data, stealth_search
        not being loaded, a search error, or a search that found nothing — because a
        fabricated answer is worse than the honest "I don't know" it would replace.
        """
    from core.harness.signals import _OWN_DATA_RE, _admits_missing_knowledge, _normalized_search_query, _search_results_need_retry, _search_results_unusable
    if not response or not _admits_missing_knowledge(response):
        return response
    lowered = (user_input or "").lower()
    if not any(m in lowered for m in self._QUESTION_MARKERS):
        return response
    if _OWN_DATA_RE.search(lowered):
        return response
    if not any(t.name == "stealth_search" for t in self.tool_manager.tools):
        return response
    manager = getattr(self, "run_manager", None)
    if manager is not None and not manager.may_continue():
        return response

    def search_once(query):
        if manager is not None and manager.active is not None:
            return self.execute_tool("stealth_search", {"query": query, "max_results": 5},
                                     user_input=user_input, _skip_format=True)
        raw = self.tool_manager.execute_tool("stealth_search", {"query": query, "max_results": 5})
        return self.tool_manager.format_tool_result(raw)

    # The question alone is often an unusable query — "đang có chế độ gì mới ko"
    # never names LoL; the subject lived one turn back. Python decided to search;
    # the model only phrases the query, from the same recent-turns context the
    # chat reply already had.
    recent = self._recent_turns_block()
    query = self.worker.generate(
        (f"[RECENT CONVERSATION]:\n{recent}\n\n" if recent else "")
        + f"The Master asked: \"{user_input}\"\n\n"
            f"Write ONE short web-search query, in the Master's own language, that would "
            f"find the answer. Resolve any pronoun or implied subject from the conversation "
            f"above — the question by itself may be missing its topic. "
            f"Output ONLY the query text: no quotes, no explanation, no label."
    ).strip().splitlines()[0].strip().strip('"\'')[:200]
    if not query:
        return response

    self._log_thought("SEARCH", "chat_fallback_triggered",
                      f"Reply conceded a knowledge gap; searching before reporting "
                          f"it as unknown.\nQuestion: {user_input}\nQuery: {query}")
    try:
        search_text = search_once(query)
    except Exception as e:
        self._log_thought("SEARCH", "chat_fallback_failed", f"{type(e).__name__}: {e}")
        return response
    # One changed-query retry is useful for a vague first query, but more
    # attempts become an invisible search loop. Only retry a completed
    # zero-hit result; provider/tool errors need diagnosis, not repetition.
    if _search_results_need_retry(search_text) and (manager is None or manager.may_continue()):
        retry_query = self.worker.generate(
            (f"[RECENT CONVERSATION]:\n{recent}\n\n" if recent else "")
            + f"Master question: \"{user_input}\"\n"
            + f"First web query: \"{query}\"\n"
            + "The first search returned no usable results. Write ONE different, short fallback "
                "query. Change strategy by naming the concrete subject, removing an overly narrow "
                "constraint, or using a source/type term. Do not repeat the first query. "
                "Output ONLY the query text: no quotes, no explanation, no label."
        ).strip().splitlines()[0].strip().strip('"\'')[:200]
        if retry_query and _normalized_search_query(retry_query) != _normalized_search_query(query):
            self._log_thought(
                "SEARCH", "chat_fallback_retry",
                f"First query returned no usable hits; retrying once with changed query.\n"
                    f"First: {query}\nRetry: {retry_query}",
            )
            try:
                retry_text = search_once(retry_query)
                if retry_text:
                    search_text = retry_text
            except Exception as e:
                self._log_thought("SEARCH", "chat_fallback_retry_failed", f"{type(e).__name__}: {e}")

    if _search_results_unusable(search_text):
        self._log_thought("SEARCH", "chat_fallback_empty",
                          "Search returned no usable result after at most one changed-query retry.")
        return response

    return self.execute_chat(
        f"The Master asked: \"{user_input}\"\n\n"
            f"[WEB SEARCH RESULTS just retrieved for this question]:\n{search_text[:8000]}\n\n"
            f"Answer the Master's question using ONLY these results. Cite the date and "
            f"source for anything time-sensitive. If the results do not actually answer "
            f"it, say honestly that you looked it up and still could not find the answer "
            f"— never invent one."
    )
