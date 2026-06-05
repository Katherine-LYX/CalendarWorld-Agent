# Agent Comparison: Mock vs Claude

## Metrics

| Metric | Mock | Claude |
|--------|------|--------|
| Events attended | 4/4 | 4/4 |
| Reminders sent | 4/4 | 4/4 |
| Key tasks completed | 2/3 | 3/3 |
| Breaks taken | 16 | 11 |
| Invalid actions | 0 | 0 |
| Final stress | 2/5 | 2/5 |
| Day ended | 23:00 | 22:15 |

## Task Completion

| Task | Mock | Claude |
|------|------|--------|
| Submit physics coursework online | Done | Done |
| Revise for tomorrow's programming exam | Done | Done |
| Return overdue library books | Open (10 min left) | Done ** |
| Reply to group project emails | Done | Done |
| Buy energy drinks and snacks | Open (10 min left) | Done ** |
| Do laundry | Done | Open (40 min left) ** |

## Key Differences

### 08:00 (stress 3/5)

- **Mock**: `start_task` Revise for tomorrow's programming exam
  - *This task is high urgency and high importance, and 44 minutes fits before the next fixed event.*
- **Claude**: `start_task` Reply to group project emails
  - *The free window is 44 minutes before the Mathematics final exam. Replying to group project emails is a quick 15-minute task due today, requires no travel, and fits comfortably within the window. Stress is balanced at level 3, making this a productive use of pre-exam time without overloading the user.*

### 11:00 (stress 2/5)

- **Mock**: `start_task` Revise for tomorrow's programming exam
  - *This task is high urgency and high importance, and 76 minutes fits before the next fixed event.*
- **Claude**: `start_task` Return overdue library books
  - *This task is due today at 17:00 (high urgency), costs only 14 total minutes (4 travel + 10 task), and the library is also where the next fixed event is at 13:00. Handling it now is efficient and fits well within the 116-minute free window. Stress is low (2), so the user can handle this errand without concern.*

### 17:00 (stress 4/5)

- **Mock**: `start_task` Buy energy drinks and snacks
  - *This is the best remaining task that fits the available 210-minute window.*
- **Claude**: `start_task` Revise for tomorrow's programming exam
  - *Despite stress level 4, the programming exam is tomorrow morning (deadline in 960 min) with only 30 minutes remaining — this is an imminent, high-urgency/high-importance academic task. Completing it now (30 min, well within the 210-min free window, no travel needed) eliminates the risk entirely. After this short burst, a restorative hobby can follow to address stress.*

### 18:43 (stress 3/5)

- **Mock**: `suggest_hobby` Scroll social media
  - *There is no suitable task for the current free window, so a bounded leisure activity is reasonable.*
- **Claude**: `start_task` Buy energy drinks and snacks
  - *The user is already at the supermarket and this task is in-progress with only 10 minutes remaining. It makes sense to finish it now before doing anything else, as it costs no travel and will be completed well within the free window before 'Call mum' at 20:30.*

### 21:00 (stress 2/5)

- **Mock**: `suggest_hobby` Scroll social media
  - *There is no suitable task for the current free window, so a bounded leisure activity is reasonable.*
- **Claude**: `suggest_hobby` Walk around the park
  - *It's 21:00, stress is low (2), no fixed events remain, and the only flexible task (Do laundry) is low priority with no deadline. The user is already at the park after a relaxing evening. A short walk is a fitting, restorative end to the day with zero travel cost.*

### 21:45 (stress 2/5)

- **Mock**: `suggest_hobby` Scroll social media
  - *There is no suitable task for the current free window, so a bounded leisure activity is reasonable.*
- **Claude**: `wait` -
  - *It's late (21:45), stress is low, and the only remaining task is low-priority laundry with no deadline. The user has already taken multiple restorative breaks and the same hobbies would repeat. No fixed events remain tonight. Waiting is the best choice to let the evening wind down naturally.*

> After the first different decision, the worlds diverged. Mock had 16 unique time steps, Claude had 14 unique time steps.

## Stress Over Time

| Time | Mock (/5) | Claude (/5) |
|------|-----------|-------------|
| 07:30 | 3 | 3 |
| 08:00 | 3 | 3 |
| 08:15 | - | 3 |
| 08:35 | - | 3 |
| 08:44 | 4 | - |
| 11:00 | 3 | 2 |
| 11:14 | - | 2 |
| 12:16 | 4 | - |
| 12:31 | 4 | - |
| 12:44 | - | 3 |
| 14:30 | 4 | 3 |
| 17:00 | 4 | 4 |
| 17:18 | 4 | - |
| 17:30 | - | 4 |
| 17:50 | - | 4 |
| 18:02 | 4 | - |
| 18:12 | - | 3 |
| 18:22 | 4 | - |
| 18:32 | - | 3 |
| 18:43 | 4 | 3 |
| 18:48 | - | 3 |
| 18:53 | 3 | 3 |
| 19:13 | 3 | 3 |
| 19:28 | 3 | - |
| 19:34 | - | 3 |
| 19:38 | 3 | - |
| 19:44 | - | 3 |
| 19:58 | 3 | - |
| 20:04 | - | 3 |
| 20:13 | 3 | - |
| 21:00 | 2 | 2 |
| 21:10 | 2 | - |
| 21:15 | - | 2 |
| 21:30 | 2 | - |
| 21:35 | - | 2 |
| 21:45 | 2 | 2 |
| 21:55 | 2 | - |
| 22:15 | 2 | - |
| 22:30 | 2 | - |
| 22:40 | 2 | - |

## Verdict

- Claude completed more high-priority tasks.
- Neither agent produced invalid actions.
- Claude took 11 breaks vs mock's 16.
- The agents diverged at 6 decision point(s), showing Claude applies contextual reasoning where mock follows fixed rules.