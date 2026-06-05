from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def build_briefing_prompt(full_day_obs: Dict[str, Any], clash_items: List[Dict[str, str]]) -> str:
    """Prompt for the day-start briefing phase.

    The LLM receives the full day at a glance plus any clashes the environment
    detected, and produces a structured briefing with suggestions.
    """
    clash_block = ""
    if clash_items:
        clash_block = f"""
The environment has already detected these scheduling issues:
{json.dumps(clash_items, indent=2)}

Incorporate them into your briefing. Add any additional issues you notice.
"""

    return f"""You are the briefing analyst for CalendarWorld-Agent.
You receive a full-day overview BEFORE the day begins.
Your job is to scan for problems and produce a structured briefing.

Look for:
- Time clashes between fixed events.
- Tasks whose deadlines fall during fixed events (impossible to complete on time).
- Tight travel gaps between consecutive events.
- Tasks that may not fit into the available free windows.
- Opportunities to batch nearby tasks to save travel time.
- Stress level concerns — if starting stress is high (4-5), recommend a lighter schedule.

{clash_block}
Output format:
Return one JSON object only. No markdown, no explanation outside the JSON.
{{
  "items": [
    {{
      "type": "clash | travel_risk | deadline_risk | suggestion",
      "title": "short title",
      "detail": "one-sentence explanation"
    }}
  ],
  "suggested_plan": "A 2-3 sentence high-level plan for the day."
}}

If no issues are found, return:
{{
  "items": [],
  "suggested_plan": "The day looks manageable. ..."
}}

Full day observation:
{json.dumps(full_day_obs, indent=2)}
"""


def build_action_prompt(observation: Dict[str, Any], feedback: Optional[str] = None) -> str:
    """Build the prompt sent to the LLM agent.

    The environment, not the LLM, owns the rules. The LLM receives a structured
    observation and must choose one JSON action that the environment can validate.
    """

    feedback_block = ""
    if feedback:
        feedback_block = f"""

Your previous action was rejected by the environment validator.
Validator feedback: {feedback}
Return a corrected action that obeys the observation and valid action rules.
"""

    return f"""You are the decision policy for CalendarWorld-Agent.
CalendarWorld is a simulated day-planning world. You do not chat with the user.
You observe the world state and choose exactly one valid action for the next step.

Goal:
- Help the user attend fixed events on time.
- Remind the user before fixed events when possible.
- Prioritize urgent and important flexible tasks, especially those due today.
- Tasks with deadlines beyond today do NOT need to be completed today. Deprioritize them when today is full.
- Do not choose work or leisure blocks that exceed free_window_minutes.
- free_window_minutes already accounts for travel time to the next fixed event, so respect it strictly.
- Suggest restorative hobbies after long focus blocks when urgent work is not at risk.

Stress awareness:
- stress_level ranges from 1 (relaxed) to 5 (overwhelmed).
- At stress 4-5: STRONGLY prefer suggesting a hobby or break. Only start work if a deadline is imminent (< 60 min away).
- At stress 3: normal balanced behavior.
- At stress 1-2: the user can handle more intensive work and longer focus blocks.

Location awareness:
- The user has a current_location. Tasks and hobbies may have a specific location.
- travel_minutes_from_here shows how long it takes to reach the target from the current location.
- effective_minutes is the task duration adjusted for the current location (it may be shorter or longer than estimated_minutes depending on where the user is).
- total_minutes_from_here = travel + effective duration. This is the real cost of doing a task now.
- When choosing between tasks, factor in travel overhead. A nearby task may be better than a distant one even if the distant one is higher priority, if the time difference is significant.
- "anywhere" locations require no travel.

Valid actions:
1. remind_event
   Required: target = exact fixed event title.
   Use only when a fixed event is inside the reminder window and no reminder was sent.

2. start_task
   Required: target = exact flexible task title, duration_minutes = positive integer.
   duration_minutes must not exceed free_window_minutes.
   Use for flexible tasks. Partial task progress is allowed.

3. suggest_hobby
   Required: target = exact hobby title, duration_minutes = positive integer.
   duration_minutes must not exceed free_window_minutes.
   Use for rest/entertainment when appropriate.

4. wait
   Required: no target and no duration_minutes.
   Use only when no useful task, reminder, or hobby fits.

Output format:
Return one JSON object only. Do not include markdown, comments, or explanation outside the JSON.
The JSON object must have this shape:
{{
  "action": "start_task | remind_event | suggest_hobby | wait",
  "target": "exact title or null",
  "duration_minutes": 30,
  "reason": "brief reason grounded in the observation"
}}

For wait, use:
{{
  "action": "wait",
  "target": null,
  "duration_minutes": null,
  "reason": "brief reason"
}}
{feedback_block}
Observation JSON:
{json.dumps(observation, indent=2)}
"""
