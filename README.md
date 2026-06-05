# CalendarWorld-Agent

An LLM agent that lives inside a simulated day, observes your schedule, and makes decisions about what to do next -- attend a lecture, finish a report, grab a coffee, or simply tell you to take a break.

This isn't a chatbot that gives planning advice. It's a structured loop where the environment produces observations, the agent chooses actions, and the world enforces the rules. The agent picks one valid action per step, and the world decides if that action is legal.

---

## Why a calendar world?

Most people don't struggle with knowing *what* they need to do. They struggle with *when* to do it, *how much* they can fit before the next commitment, and *whether they should keep pushing or take a break*. **A surprising amount of time gets lost to hesitation and figuring out what to do next.**

CalendarWorld models exactly that tension. The agent has to juggle:

- **Fixed events** that can't be moved (lectures, meetings, a friend's birthday party)
- **Flexible tasks** with deadlines, priorities, and varying time requirements
- **Hobbies and breaks** that restore energy but cost time
- **Travel** between locations on a 100x100 grid -- choosing a nearby task over a distant one might be smarter, even if the distant one is higher priority
- **Stress** that builds from work and eases from rest -- and a user who might report feeling differently than the numbers suggest

---
## What the agent sees

At each step, the agent receives a structured JSON observation, which is a precise snapshot of the world that includes information about current position, how long it takes to get somewhere, how much time is available, and what's due soon...

```json
{
  "current_time": "11:00",
  "current_location": "department",
  "current_coordinate": [65, 75],
  "stress_level": 2,
  "activity_stress": 1,
  "user_mood": 3,
  "next_fixed_event": {
    "title": "Call parents",
    "start": "12:30",
    "minutes_until_start": 90,
    "travel_minutes_from_here": 0
  },
  "free_window_minutes": 90,
  "focus_minutes_since_break": 0,
  "flexible_tasks": [
    {
      "title": "Buy sandwich and milk",
      "urgency": "high",
      "effective_minutes": 15,
      "travel_minutes_from_here": 14,
      "total_minutes_from_here": 29
    }
  ],
  "available_hobbies": [...],
  "valid_actions": ["remind_event", "start_task", "suggest_hobby", "wait"]
}
```

## What the agent can do

The action space is small and strict:

| Action | What it does | Required fields |
|--------|-------------|-----------------|
| `remind_event` | Alert the user about an upcoming fixed event | `target` (event title) |
| `start_task` | Work on a flexible task for a set duration | `target`, `duration_minutes` |
| `suggest_hobby` | Suggest a break or leisure activity | `target`, `duration_minutes` |
| `wait` | Do nothing until the next event | -- |

Every action is validated by the environment. You can't remind an event that already started. You can't work on a task for longer than the free window. You can't suggest a hobby that doesn't exist. If the LLM outputs something invalid, the harness rejects it and retries with feedback.

## What the agent returns

```json
{
  "action": "start_task",
  "target": "Buy sandwich and milk",
  "duration_minutes": 15,
  "reason": "Due soon and supermarket is only 14 min away."
}
```

---

## The world

### Locations and travel

The world is a **100x100 grid**. Every location has coordinates. Travel time is computed from **Manhattan distance** multiplied by a walking pace  that is adjustable (`minutes_per_unit`, default 0.2 min/unit for walking).

```
Named locations on the grid:

  home [15,45]          park [30,58]         campus [52,72]
  supermarket [25,35]   gym [40,55]          library [55,65]
  cinema [33,38]        friends [20,52]      exam hall [60,78]
```

This means:
- Home to campus: Manhattan 70 = **14 min** walk
- Home to supermarket: Manhattan 20 = **4 min** walk
- Campus to friend's flat: Manhattan 52 = **10 min** walk

The agent sees `travel_minutes_from_here` for every task and event, pre-computed from its current position. The `free_window_minutes` already subtracts travel time to the next fixed event, so the agent would not make the user late.

### Stress: two signals, one picture

Stress isn't just a counter that goes up when you work. CalendarWorld separates it into two components:

- **Activity stress** (objective): starts at 1, increases with sustained work (+1 per 30 min, +2 per 60 min), decreases with breaks
- **User mood** (subjective): reported by the user -- "not stressed", "slightly stressed", "stressed", "very stressed"

These combine with configurable weights into an overall stress level (1-5):

```
overall = round(activity_weight x activity_stress + mood_weight x user_mood)
```

*Why separate them?* Because someone who had a relaxing week might feel fine even after hours of work. And someone going through a tough time might feel overwhelmed before the day even starts. The dual system lets the planner respond to both.

The **stress weights** are also configurable per scenario:
```json
"stress_weights": {"activity": 0.5, "mood": 0.5}
```

The default is an even split, but users can adjust it. Someone who had a relaxing week might set `{"activity": 0.7, "mood": 0.3}` to let work fatigue drive the number. Someone going through a difficult period might set `{"activity": 0.3, "mood": 0.7}` so their subjective feeling carries more weight. This keeps the system adaptable without adding complexity to the agent itself.

**Mood events** simulate the user checking in mid-day:
```json
"mood_events": [
  {"time": "16:00", "mood": "stressed"},
  {"time": "21:30", "mood": "not_stressed"}
]
```

The agent adapts: at stress 4-5, it prefers breaks and defers non-urgent tasks. At 1-2, it can push harder.

### Day-start briefing

Before the step-by-step loop begins, the system scans the full day for problems:

- **Clashes**: two fixed events that overlap
- **Travel risks**: not enough time to get between consecutive events
- **Deadline risks**: a task deadline that falls during a fixed event (impossible to complete on time)
- **Capacity warnings**: more today-due work than available free time

---

## Two agents, one harness

### Mock agent (rule-based)

A deterministic baseline that follows fixed rules: remind events in the window, pick the highest-priority task that fits, suggest a hobby after long focus blocks. It proves the harness works without any LLM. No API key needed.

### Claude agent (LLM-backed)

Uses the Anthropic Claude API with the same observation/action contract. The prompt gives Claude the world state and rules; Claude returns a JSON action. If the response is invalid, the harness retries with validator feedback (up to 2 retries by default).

### Comparison mode

Run both agents on the same scenario and see a side-by-side report. The comparison shows:
- **Metrics table**: events attended, tasks completed, breaks taken, stress levels
- **Task completion**: per-task status for each agent
- **Key differences**: specific moments where the agents made different choices, with both agents' reasoning
- **Stress arc**: stress level at every decision point, side by side
- **Verdict**: auto-generated summary of what each agent did differently

This directly demonstrates whether the LLM adds value beyond fixed rules by making measurably different decisions in the same environment.

---
## Output

Each run produces two markdown files:

- **Debug log** (`*_.md`): full observations, raw JSON actions, world state updates -- for understanding exactly what happened
- **Clean schedule** (`*_schedule.md`): a readable day plan with friendly language, meant to feel like a productivity app

### Example inputs and outputs

Example scenario files (inputs) live in `examples/`. Pre-generated logs (outputs) live in `logs/`.

```
examples/                       <-- inputs
  finals_week.json
  weekend.json
  busy_monday.json

logs/                           <-- outputs
  finals_week.md                   debug log for finals week
  finals_week_schedule.md          clean schedule for finals week
  weekend.md                       debug log for weekend
  weekend_schedule.md              clean schedule for weekend
  busy_monday.md                   debug log for busy monday
  busy_monday_schedule.md          clean schedule for busy monday
  compare/                         comparison mode outputs
    report.md                        side-by-side metrics and key differences
    mock_log.md                      mock agent debug log
    mock_plan.md                     mock agent clean schedule
    claude_log.md                    Claude agent debug log
    claude_plan.md                   Claude agent clean schedule
```

The debug logs show every observation, JSON action, and world update. The schedule files are the clean, human-readable day plans. The comparison report highlights where the two agents made different decisions and why. To regenerate any of them, just run the corresponding scenario (e.g. `python main.py finals` or `python main.py finals -m compare`).

---

## Quick start

**Requirements:** Python 3.10+

```bash
pip install -r requirements.txt
```

**Mock mode (no API key needed):**

```bash
python main.py finals
python main.py weekend
python main.py monday
```

**Claude mode:**

```bash
cp .env.example .env
# Edit .env with your Anthropic API key (or use the existing one)
python main.py finals -m claude
```

**Compare mode:**

```bash
python main.py finals -m compare
```

## Commands

| Command | What it does |
|---|---|
| `python main.py finals` | Finals week, mock agent |
| `python main.py finals -m claude` | Finals week, Claude agent |
| `python main.py finals -m compare` | Finals week, mock vs Claude |
| `python main.py weekend` | Relaxed Saturday, mock agent |
| `python main.py monday` | Busy weekday with part-time job, mock agent |

Shortcuts: `finals`/`exam`, `weekend`/`saturday`, `monday`/`busy`. Or pass any JSON file path directly.

---

## Scenarios

### Finals week (`finals`)

A high-pressure exam day. Mathematics final in the morning, study group after lunch, physics review session in the afternoon. The physics coursework deadline falls *during* the review session. Starting mood: stressed. The agent has to balance exam prep, deadline urgency, and not burning out.

### Weekend (`weekend`)

A relaxed Saturday. Brunch with flatmates, 5-a-side football, movie night at a friend's. Flexible tasks like grocery shopping, calling grandparents, and light reading for Monday's seminar. Starting mood: not stressed. Tests whether the agent can pace a leisure-heavy day without over-scheduling.

### Busy Monday (`monday`)

Back-to-back morning lectures, a meeting with the academic advisor, a remote part-time job shift from 2-6pm, and a photography society meetup in the evening. An internship email must be replied to before the job shift. The prescription pickup deadline falls during the shift. Tests travel optimisation across 8 locations and tight time management.

---

## Project structure

```
calendarworld-agent/
  main.py          -- CLI, simulation loop, comparison mode
  world.py         -- CalendarWorld environment, validation, output rendering
  agent.py         -- MockAgent and ClaudeAgent
  prompts.py       -- LLM prompt construction (action + briefing)
  schemas.py       -- Dataclasses: events, tasks, hobbies, actions, briefing
  examples/
    finals_week.json
    weekend.json
    busy_monday.json
  logs/             -- Generated output (debug logs + clean schedules)
    compare/        -- Comparison mode output
  requirements.txt
  .env.example
```
---

## Future work

- **Interactive mode**: let users add events, adjust mood, and request schedule changes mid-day through a CLI or simple web interface
- **Transport modes**: switch between walking (0.20), cycling (0.08), driving (0.05) and bus (0.10) with different `minutes_per_unit` values
- **Multi-day planning**: carry incomplete tasks and stress across days