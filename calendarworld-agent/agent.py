from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from prompts import build_action_prompt, build_briefing_prompt
from schemas import AgentAction, BriefingItem, DayBriefing


def _parse_json_object(text: str) -> Dict[str, Any]:
    """Extract and parse the first JSON object from an LLM response string."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty response")

    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise ValueError("no JSON object found")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError(str(exc)) from exc

    if not isinstance(parsed, dict):
        raise ValueError("top-level JSON value is not an object")
    return parsed


class MockAgent:
    """A deterministic baseline agent.

    The mock agent proves the environment/action harness works before adding an LLM.
    It follows simple rules that an LLM should later be able to improve on.
    """

    def __init__(self, reminder_window_minutes: int = 30, min_task_block_minutes: int = 15) -> None:
        self.reminder_window_minutes = reminder_window_minutes
        self.min_task_block_minutes = min_task_block_minutes
        self.hobby_counts: dict[str, int] = {}

    def act(self, observation: Dict[str, Any], feedback: Optional[str] = None) -> AgentAction:
        next_event = observation.get("next_fixed_event")
        free_window = int(observation.get("free_window_minutes", 0))
        focus_minutes = int(observation.get("focus_minutes_since_break", 0))
        tasks = observation.get("flexible_tasks", [])
        hobbies = observation.get("available_hobbies", [])

        if next_event and not next_event.get("reminder_sent"):
            minutes_until = int(next_event["minutes_until_start"])
            if 0 <= minutes_until <= self.reminder_window_minutes:
                return AgentAction(
                    action="remind_event",
                    target=next_event["title"],
                    reason=f"The fixed event starts in {minutes_until} minutes and cannot be moved.",
                )

        urgent_task = self._best_task_that_fits(tasks, free_window, require_urgent=True)
        if urgent_task is not None:
            duration = self._choose_task_duration(urgent_task, free_window)
            return AgentAction(
                action="start_task",
                target=urgent_task["title"],
                duration_minutes=duration,
                reason=(
                    f"This task is {urgent_task['urgency']} urgency and {urgent_task['importance']} importance, "
                    f"and {duration} minutes fits before the next fixed event."
                ),
            )

        if focus_minutes >= 90 and hobbies and free_window >= 15:
            hobby = self._hobby_that_fits(hobbies, free_window, restorative_preferred=True)
            if hobby:
                self._mark_hobby_used(hobby["title"])
                return AgentAction(
                    action="suggest_hobby",
                    target=hobby["title"],
                    duration_minutes=min(int(hobby["estimated_minutes"]), free_window),
                    reason="The user has had a long focus block and no urgent task is at immediate risk.",
                )

        task = self._best_task_that_fits(tasks, free_window, require_urgent=False)
        if task is not None:
            duration = self._choose_task_duration(task, free_window)
            return AgentAction(
                action="start_task",
                target=task["title"],
                duration_minutes=duration,
                reason=f"This is the best remaining task that fits the available {free_window}-minute window.",
            )

        if hobbies and free_window >= 15:
            hobby = self._hobby_that_fits(hobbies, free_window, restorative_preferred=False)
            if hobby:
                self._mark_hobby_used(hobby["title"])
                return AgentAction(
                    action="suggest_hobby",
                    target=hobby["title"],
                    duration_minutes=min(int(hobby["estimated_minutes"]), free_window),
                    reason="There is no suitable task for the current free window, so a bounded leisure activity is reasonable.",
                )

        return AgentAction(
            action="wait",
            reason="No task or hobby fits the current window.",
        )

    def _best_task_that_fits(
        self,
        tasks: list[Dict[str, Any]],
        free_window: int,
        require_urgent: bool,
    ) -> Optional[Dict[str, Any]]:
        candidates = []
        for task in tasks:
            remaining = int(task.get("remaining_minutes") or 0)
            if remaining <= 0:
                continue
            if free_window < self.min_task_block_minutes:
                continue
            if require_urgent and not (task.get("urgency") == "high" or task.get("importance") == "high"):
                continue
            if free_window <= 0:
                continue
            # Partial progress is allowed. The task only needs a meaningful work block to fit.
            if min(remaining, free_window) >= self.min_task_block_minutes:
                candidates.append(task)

        if not candidates:
            return None

        def score(task: Dict[str, Any]) -> tuple[int, int, int, int]:
            urgency = {"high": 0, "medium": 1, "low": 2}.get(task.get("urgency"), 1)
            importance = {"high": 0, "medium": 1, "low": 2}.get(task.get("importance"), 1)
            deadline = task.get("minutes_until_deadline")
            deadline_score = int(deadline) if deadline is not None else 99999
            remaining = int(task.get("remaining_minutes") or 0)
            return urgency, importance, deadline_score, remaining

        return sorted(candidates, key=score)[0]

    def _choose_task_duration(self, task: Dict[str, Any], free_window: int) -> int:
        remaining = int(task.get("remaining_minutes") or 0)
        if remaining <= free_window:
            return remaining
        # Leave a small buffer before fixed events instead of filling the whole window.
        if free_window >= 45:
            return free_window - 10
        return free_window

    def _hobby_that_fits(
        self,
        hobbies: list[Dict[str, Any]],
        free_window: int,
        restorative_preferred: bool,
    ) -> Optional[Dict[str, Any]]:
        fitting = [hobby for hobby in hobbies if int(hobby.get("estimated_minutes", 0)) <= free_window]
        if not fitting:
            return None

        if restorative_preferred:
            restorative = [hobby for hobby in fitting if hobby.get("category") == "restorative"]
            pool = restorative or fitting
            return sorted(
                pool,
                key=lambda hobby: (self.hobby_counts.get(hobby["title"], 0), int(hobby["estimated_minutes"])),
            )[0]

        return sorted(
            fitting,
            key=lambda hobby: (self.hobby_counts.get(hobby["title"], 0), -int(hobby["estimated_minutes"])),
        )[0]

    def _mark_hobby_used(self, title: str) -> None:
        self.hobby_counts[title] = self.hobby_counts.get(title, 0) + 1

    def briefing(self, full_day_obs: Dict[str, Any], env_briefing: DayBriefing) -> DayBriefing:
        """Mock agent returns the environment-detected clashes as-is."""
        return env_briefing


class ClaudeAgent:
    """Anthropic Claude-backed agent using the same observation/action contract as MockAgent."""

    def __init__(self, model: Optional[str] = None) -> None:
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        try:
            import anthropic as _anthropic
            self.client = _anthropic.Anthropic()
        except ImportError as exc:
            raise RuntimeError(
                "The anthropic package is not installed. Run `pip install -r requirements.txt`, "
                "or use `--mode mock`."
            ) from exc

    def briefing(self, full_day_obs: Dict[str, Any], env_briefing: DayBriefing) -> DayBriefing:
        """Ask Claude to produce a day-start briefing, seeded with environment-detected issues."""
        clash_items = [{"type": item.type, "title": item.title, "detail": item.detail} for item in env_briefing.items]
        prompt = build_briefing_prompt(full_day_obs, clash_items)
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text if response.content else ""
        except Exception as exc:
            # Fall back to env-detected briefing on API failure
            env_briefing.suggested_plan = f"(Briefing API call failed: {exc})"
            return env_briefing

        try:
            data = _parse_json_object(raw_text)
            items = [
                BriefingItem(type=item.get("type", "suggestion"), title=item.get("title", ""), detail=item.get("detail", ""))
                for item in data.get("items", [])
            ]
            return DayBriefing(items=items, suggested_plan=data.get("suggested_plan", ""))
        except (ValueError, KeyError):
            env_briefing.suggested_plan = f"(Could not parse briefing. Raw: {raw_text[:200]})"
            return env_briefing

    def act(self, observation: Dict[str, Any], feedback: Optional[str] = None) -> AgentAction:
        prompt = build_action_prompt(observation, feedback=feedback)
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text if response.content else ""
        except Exception as exc:
            return AgentAction(
                action="llm_api_error",
                reason=f"Claude API call failed: {exc}",
            )

        try:
            data = _parse_json_object(raw_text)
            return AgentAction.from_dict(data)
        except ValueError as exc:
            return AgentAction(
                action="invalid_json",
                reason=f"Could not parse Claude output as a JSON action: {exc}. Raw output: {raw_text[:300]}",
            )
