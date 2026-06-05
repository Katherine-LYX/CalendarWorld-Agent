from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from agent import ClaudeAgent, MockAgent
from schemas import AgentAction, StepRecord
from world import CalendarWorld


def load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def choose_agent(mode: str, world: CalendarWorld, model: Optional[str] = None):
    if mode == "mock":
        return MockAgent(reminder_window_minutes=world.reminder_window_minutes)
    if mode == "claude":
        return ClaudeAgent(model=model)
    raise ValueError(f"Unknown mode: {mode}")


def choose_valid_action(
    agent,
    world: CalendarWorld,
    observation: dict,
    retries: int,
) -> AgentAction:
    """Ask the agent for an action and retry if the world validator rejects it."""

    feedback = None
    last_action = None
    attempts = max(1, retries + 1)

    for attempt in range(attempts):
        action = agent.act(observation, feedback=feedback)
        last_action = action
        valid, error = world.validate_action(action)
        if valid:
            return action

        feedback_parts = [error or "Action failed validation."]
        if action.reason:
            feedback_parts.append(f"Agent reason/internal error: {action.reason}")
        feedback_parts.append(f"Attempt {attempt + 1} of {attempts} failed.")
        feedback = " ".join(feedback_parts)

    assert last_action is not None
    return last_action


def _run_agent(
    input_path: str,
    mode: str,
    model: Optional[str] = None,
    max_steps: int = 60,
    llm_retries: int = 2,
) -> CalendarWorld:
    """Run a single agent on a fresh world. Returns the completed world."""
    world = CalendarWorld.from_json(input_path)
    agent = choose_agent(mode, world, model=model)

    # Briefing phase
    env_briefing = world.detect_clashes()
    full_day_obs = world.observe_full_day()
    briefing = agent.briefing(full_day_obs, env_briefing)
    world.briefing = briefing

    # Tick-by-tick loop
    steps = 0
    while not world.is_finished() and steps < max_steps:
        mood_note = world.process_mood_events()
        if mood_note:
            world.records.append(StepRecord(
                time=world.fmt_clock(world.current_minute),
                observation=world.observe(),
                action=None,
                world_update=mood_note,
            ))

        if world.process_fixed_event_if_active():
            steps += 1
            continue

        observation = world.observe()
        retries = llm_retries if mode == "claude" else 0
        action = choose_valid_action(agent, world, observation, retries=retries)
        world.apply_action(observation, action)
        steps += 1

    return world


def run_world(
    input_path: str,
    log_path: str | None = None,
    max_steps: int = 60,
    mode: str = "mock",
    model: Optional[str] = None,
    llm_retries: int = 2,
) -> CalendarWorld:
    load_dotenv_if_available()
    world = _run_agent(input_path, mode, model, max_steps, llm_retries)

    # --- Output ---
    debug_log = world.render_log()
    schedule = world.render_schedule()

    # Print the clean schedule to terminal
    print(schedule)

    if log_path:
        log = Path(log_path)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(debug_log, encoding="utf-8")
        schedule_path = log.with_name(log.stem + "_schedule" + log.suffix)
        schedule_path.write_text(schedule, encoding="utf-8")
        print(f"\nSaved: {log}  (debug log)")
        print(f"Saved: {schedule_path}  (clean schedule)")

    return world


# ------------------------------------------------------------------
# Comparison mode
# ------------------------------------------------------------------

def _extract_decisions(world: CalendarWorld) -> list[dict]:
    """Extract the agent's decisions (not fixed events / mood updates) as a flat list."""
    decisions = []
    for record in world.records:
        if record.action is None:
            continue
        if record.validation_error:
            continue
        decisions.append({
            "time": record.time,
            "action": record.action.get("action", ""),
            "target": record.action.get("target", ""),
            "duration": record.action.get("duration_minutes"),
            "reason": record.action.get("reason", ""),
            "stress": record.observation.get("stress_level", "?"),
            "location": record.observation.get("current_location", "?"),
        })
    return decisions


def _find_highlights(mock_decisions: list[dict], claude_decisions: list[dict]) -> list[dict]:
    """Find moments where the two agents made meaningfully different choices."""
    highlights = []

    # Build a time-indexed lookup for each agent
    mock_by_time: dict[str, dict] = {}
    for d in mock_decisions:
        mock_by_time.setdefault(d["time"], d)

    claude_by_time: dict[str, dict] = {}
    for d in claude_decisions:
        claude_by_time.setdefault(d["time"], d)

    # Compare decisions at matching timestamps
    for time_key, mock_d in mock_by_time.items():
        claude_d = claude_by_time.get(time_key)
        if claude_d is None:
            continue
        if mock_d["action"] != claude_d["action"] or mock_d["target"] != claude_d["target"]:
            highlights.append({
                "time": time_key,
                "mock_action": mock_d["action"],
                "mock_target": mock_d["target"] or "-",
                "mock_reason": mock_d["reason"],
                "claude_action": claude_d["action"],
                "claude_target": claude_d["target"] or "-",
                "claude_reason": claude_d["reason"],
                "stress": claude_d["stress"],
            })

    # Also find decisions Claude made at times mock didn't reach (diverged worlds)
    claude_only_times = set(claude_by_time.keys()) - set(mock_by_time.keys())
    mock_only_times = set(mock_by_time.keys()) - set(claude_by_time.keys())

    if claude_only_times or mock_only_times:
        highlights.append({
            "time": "diverged",
            "note": (
                f"After the first different decision, the worlds diverged. "
                f"Mock had {len(mock_only_times)} unique time steps, "
                f"Claude had {len(claude_only_times)} unique time steps."
            ),
        })

    return highlights


def _render_comparison(
    mock_world: CalendarWorld,
    claude_world: CalendarWorld,
    highlights: list[dict],
) -> str:
    """Render the comparison report as markdown."""
    mock_s = mock_world.summary()
    claude_s = claude_world.summary()
    lines: list[str] = []

    lines.append("# Agent Comparison: Mock vs Claude")
    lines.append("")

    # --- Metrics table ---
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Mock | Claude |")
    lines.append("|--------|------|--------|")
    lines.append(f"| Events attended | {mock_s.fixed_events_attended}/{mock_s.fixed_events_total} | {claude_s.fixed_events_attended}/{claude_s.fixed_events_total} |")
    lines.append(f"| Reminders sent | {mock_s.fixed_events_reminded}/{mock_s.fixed_events_total} | {claude_s.fixed_events_reminded}/{claude_s.fixed_events_total} |")
    lines.append(f"| Key tasks completed | {mock_s.high_priority_tasks_completed}/{mock_s.high_priority_tasks_total} | {claude_s.high_priority_tasks_completed}/{claude_s.high_priority_tasks_total} |")
    lines.append(f"| Breaks taken | {mock_s.hobby_suggestions} | {claude_s.hobby_suggestions} |")
    lines.append(f"| Invalid actions | {mock_s.invalid_actions} | {claude_s.invalid_actions} |")
    lines.append(f"| Final stress | {mock_s.stress_end}/5 | {claude_s.stress_end}/5 |")
    lines.append(f"| Day ended | {mock_s.final_time} | {claude_s.final_time} |")
    lines.append("")

    # --- Task completion comparison ---
    lines.append("## Task Completion")
    lines.append("")
    lines.append("| Task | Mock | Claude |")
    lines.append("|------|------|--------|")
    for m_task, c_task in zip(mock_world.flexible_tasks, claude_world.flexible_tasks):
        m_status = "Done" if m_task.status == "completed" else f"Open ({m_task.remaining_minutes} min left)"
        c_status = "Done" if c_task.status == "completed" else f"Open ({c_task.remaining_minutes} min left)"
        marker = " **" if m_task.status != c_task.status else ""
        lines.append(f"| {m_task.title} | {m_status} | {c_status}{marker} |")
    lines.append("")

    # --- Decision highlights ---
    real_highlights = [h for h in highlights if h.get("time") != "diverged"]
    diverge_note = next((h for h in highlights if h.get("time") == "diverged"), None)

    if real_highlights:
        lines.append("## Key Differences")
        lines.append("")
        for h in real_highlights:
            lines.append(f"### {h['time']} (stress {h.get('stress', '?')}/5)")
            lines.append("")
            lines.append(f"- **Mock**: `{h['mock_action']}` {h['mock_target']}")
            lines.append(f"  - *{h['mock_reason']}*")
            lines.append(f"- **Claude**: `{h['claude_action']}` {h['claude_target']}")
            lines.append(f"  - *{h['claude_reason']}*")
            lines.append("")

    if diverge_note:
        lines.append(f"> {diverge_note['note']}")
        lines.append("")

    if not real_highlights and not diverge_note:
        lines.append("## Key Differences")
        lines.append("")
        lines.append("Both agents made identical decisions at every matching time step.")
        lines.append("")

    # --- Stress arc comparison ---
    lines.append("## Stress Over Time")
    lines.append("")
    lines.append("| Time | Mock (/5) | Claude (/5) |")
    lines.append("|------|-----------|-------------|")

    mock_stress = [(r.time, r.observation.get("stress_level", "?")) for r in mock_world.records if r.action is not None]
    claude_stress = [(r.time, r.observation.get("stress_level", "?")) for r in claude_world.records if r.action is not None]

    # Merge unique timestamps
    all_times: list[str] = []
    seen: set[str] = set()
    for t, _ in mock_stress + claude_stress:
        if t not in seen:
            all_times.append(t)
            seen.add(t)
    all_times.sort()

    mock_stress_map = {t: s for t, s in mock_stress}
    claude_stress_map = {t: s for t, s in claude_stress}

    for t in all_times:
        ms = mock_stress_map.get(t, "-")
        cs = claude_stress_map.get(t, "-")
        lines.append(f"| {t} | {ms} | {cs} |")

    lines.append("")

    # --- Verdict ---
    lines.append("## Verdict")
    lines.append("")

    verdicts: list[str] = []
    if claude_s.high_priority_tasks_completed > mock_s.high_priority_tasks_completed:
        verdicts.append("Claude completed more high-priority tasks.")
    elif claude_s.high_priority_tasks_completed == mock_s.high_priority_tasks_completed:
        verdicts.append("Both agents completed the same number of key tasks.")

    if claude_s.invalid_actions < mock_s.invalid_actions:
        verdicts.append("Claude had fewer invalid actions.")
    elif claude_s.invalid_actions == 0 and mock_s.invalid_actions == 0:
        verdicts.append("Neither agent produced invalid actions.")

    if claude_s.stress_end < mock_s.stress_end:
        verdicts.append("Claude ended the day at a lower stress level.")
    elif claude_s.stress_end > mock_s.stress_end:
        verdicts.append("Mock ended the day at a lower stress level.")

    if claude_s.hobby_suggestions > 0 and mock_s.hobby_suggestions > 0:
        if claude_s.hobby_suggestions != mock_s.hobby_suggestions:
            verdicts.append(f"Claude took {claude_s.hobby_suggestions} breaks vs mock's {mock_s.hobby_suggestions}.")

    if real_highlights:
        verdicts.append(f"The agents diverged at {len(real_highlights)} decision point(s), showing Claude applies contextual reasoning where mock follows fixed rules.")

    for v in verdicts:
        lines.append(f"- {v}")

    return "\n".join(lines)


def run_compare(
    input_path: str,
    log_path: str | None = None,
    max_steps: int = 60,
    model: Optional[str] = None,
    llm_retries: int = 2,
) -> None:
    load_dotenv_if_available()

    print("Running mock agent...")
    mock_world = _run_agent(input_path, "mock", model=None, max_steps=max_steps, llm_retries=0)
    print(f"  Done. Tasks completed: {sum(1 for t in mock_world.flexible_tasks if t.status == 'completed')}/{len(mock_world.flexible_tasks)}")

    print("Running Claude agent...")
    claude_world = _run_agent(input_path, "claude", model=model, max_steps=max_steps, llm_retries=llm_retries)
    print(f"  Done. Tasks completed: {sum(1 for t in claude_world.flexible_tasks if t.status == 'completed')}/{len(claude_world.flexible_tasks)}")

    mock_decisions = _extract_decisions(mock_world)
    claude_decisions = _extract_decisions(claude_world)
    highlights = _find_highlights(mock_decisions, claude_decisions)

    report = _render_comparison(mock_world, claude_world, highlights)
    print("")
    print(report)

    if log_path:
        out = Path(log_path).parent / "compare"
        out.mkdir(parents=True, exist_ok=True)

        files = {
            out / "report.md": report,
            out / "mock_log.md": mock_world.render_log(),
            out / "mock_plan.md": mock_world.render_schedule(),
            out / "claude_log.md": claude_world.render_log(),
            out / "claude_plan.md": claude_world.render_schedule(),
        }
        for path, content in files.items():
            path.write_text(content, encoding="utf-8")

        print(f"\nSaved to {out}/:")
        for path in files:
            print(f"  {path.name}")


# ------------------------------------------------------------------
# Scenario shortcuts
# ------------------------------------------------------------------

SCENARIOS: dict[str, str] = {
    "finals":  "examples/finals_week.json",
    "exam":    "examples/finals_week.json",
    "weekend": "examples/weekend.json",
    "saturday":"examples/weekend.json",
    "monday":  "examples/busy_monday.json",
    "busy":    "examples/busy_monday.json",
}


def _resolve_input(raw: str) -> str:
    """Resolve a scenario shortcut or pass through a file path."""
    return SCENARIOS.get(raw.lower(), raw)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run CalendarWorld-Agent.",
        epilog="Scenario shortcuts: " + ", ".join(sorted(set(SCENARIOS.values()), key=lambda v: v)),
    )
    parser.add_argument("input", nargs="?", default="finals",
                        help="Scenario shortcut (finals, weekend, monday) or path to a JSON file.")
    parser.add_argument("-m", "--mode", choices=["mock", "claude", "compare"], default="mock",
                        help="mock (rule-based), claude (Anthropic), or compare (run both).")
    parser.add_argument("-s", "--scenario", default=None,
                        help="Alternative way to pick a scenario (same shortcuts as the positional arg).")
    parser.add_argument("--log", default=None, help="Where to save the run log. Auto-generated if omitted.")
    parser.add_argument("--max-steps", type=int, default=60, help="Safety cap to avoid infinite loops.")
    parser.add_argument("--model", default=None, help="Model override for claude/compare mode.")
    parser.add_argument("--llm-retries", type=int, default=2, help="Retries on invalid LLM output.")
    args = parser.parse_args()

    # Resolve scenario: -s flag takes priority, then positional arg
    raw_input = args.scenario or args.input
    input_path = _resolve_input(raw_input)

    # Auto-generate log path from scenario name if not specified
    if args.log:
        log_path = args.log
    else:
        from pathlib import PurePath
        stem = PurePath(input_path).stem
        log_path = f"logs/{stem}.md"

    if args.mode == "compare":
        run_compare(
            input_path=input_path,
            log_path=log_path,
            max_steps=args.max_steps,
            model=args.model,
            llm_retries=args.llm_retries,
        )
    else:
        run_world(
            input_path=input_path,
            log_path=log_path,
            max_steps=args.max_steps,
            mode=args.mode,
            model=args.model,
            llm_retries=args.llm_retries,
        )


if __name__ == "__main__":
    main()
