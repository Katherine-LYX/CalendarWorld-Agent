from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from schemas import (
    AgentAction, BriefingItem, DayBriefing, FixedEvent, FlexibleTask,
    Hobby, RunSummary, StepRecord, VALID_ACTIONS,
)


class CalendarWorld:
    """A small simulated day-planning world for an LLM agent harness."""

    def __init__(
        self,
        start_time: str,
        end_time: str,
        step_minutes: int,
        fixed_events: List[FixedEvent],
        flexible_tasks: List[FlexibleTask],
        hobbies: List[Hobby],
        reminder_window_minutes: int = 30,
        long_work_threshold_minutes: int = 90,
        start_location: str = "home",
        start_coordinate: Tuple[int, int] = (0, 0),
        grid_size: int = 100,
        minutes_per_unit: float = 0.2,
        named_locations: Optional[Dict[str, List[int]]] = None,
    ) -> None:
        self.current_minute = self.parse_clock(start_time)
        self.end_minute = self.parse_clock(end_time)
        self.step_minutes = step_minutes
        self.fixed_events = sorted(fixed_events, key=lambda e: self.parse_clock(e.start))
        self.flexible_tasks = flexible_tasks
        self.hobbies = hobbies
        self.reminder_window_minutes = reminder_window_minutes
        self.long_work_threshold_minutes = long_work_threshold_minutes

        # --- Grid & location state ---
        self.grid_size: int = grid_size
        self.minutes_per_unit: float = minutes_per_unit
        self.current_location: str = start_location
        self.current_coordinate: Tuple[int, int] = tuple(start_coordinate)
        # Named locations: name -> (x, y).  Populated from JSON.
        self._named_coords: Dict[str, Tuple[int, int]] = {}
        for name, coord in (named_locations or {}).items():
            self._named_coords[name] = (coord[0], coord[1])
        # Also index every fixed event / task / hobby that carries a coordinate.
        for item in list(self.fixed_events) + list(self.flexible_tasks) + list(self.hobbies):  # type: ignore[operator]
            if getattr(item, "coordinate", None) and item.location:
                self._named_coords.setdefault(item.location, item.coordinate)

        self.focus_minutes_since_break = 0
        self.recent_activity: List[str] = []
        self.records: List[StepRecord] = []
        self.invalid_actions = 0
        self.hobby_suggestions = 0

        # --- Dual stress system ---
        # Activity stress: objective fatigue from work done today (1=fresh, 5=exhausted)
        self.activity_stress: int = 1
        # User mood: subjective feeling reported by the user (1=relaxed, 5=overwhelmed)
        self.user_mood: int = 3
        # Weights for combining into overall stress
        self.stress_weight_activity: float = 0.5
        self.stress_weight_mood: float = 0.5
        # Mood events: list of (minute, mood_value) for mid-day mood changes
        self._mood_events: List[Tuple[int, int]] = []
        self._mood_events_fired: int = 0  # index of next event to process
        self.briefing: Optional[DayBriefing] = None

    # ------------------------------------------------------------------
    # Mood label mapping
    # ------------------------------------------------------------------

    MOOD_LABELS: Dict[str, int] = {
        "not_stressed": 1,
        "neutral": 2,
        "slightly_stressed": 3,
        "stressed": 4,
        "very_stressed": 5,
    }

    @classmethod
    def mood_from_label(cls, label: str) -> int:
        """Convert a mood label string to its numeric value."""
        return cls.MOOD_LABELS.get(label.lower().strip(), 3)

    @property
    def stress_level(self) -> int:
        """Overall stress: weighted combination of activity stress and user mood, clamped [1,5]."""
        raw = self.stress_weight_activity * self.activity_stress + self.stress_weight_mood * self.user_mood
        return max(1, min(5, round(raw)))

    # ------------------------------------------------------------------
    # Grid / travel helpers
    # ------------------------------------------------------------------

    @staticmethod
    def manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
        """Manhattan distance between two grid coordinates."""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def coord_of(self, location: str) -> Optional[Tuple[int, int]]:
        """Look up the grid coordinate for a named location."""
        return self._named_coords.get(location)

    def _resolve_coord(self, location: str, item_coord: Optional[Tuple[int, int]] = None) -> Optional[Tuple[int, int]]:
        """Return a coordinate for a location, checking the item first, then the registry."""
        if item_coord is not None:
            return item_coord
        return self.coord_of(location)

    def travel_time(self, origin_coord: Tuple[int, int], dest_coord: Tuple[int, int]) -> int:
        """Travel time in minutes between two grid coordinates (Manhattan × pace)."""
        dist = self.manhattan(origin_coord, dest_coord)
        return round(dist * self.minutes_per_unit)

    def travel_time_to(self, destination: str, dest_coord: Optional[Tuple[int, int]] = None) -> int:
        """Travel time from the user's current position to a named destination."""
        if destination == "anywhere":
            return 0
        resolved = self._resolve_coord(destination, dest_coord)
        if resolved is None:
            return 0  # Unknown location treated as co-located
        return self.travel_time(self.current_coordinate, resolved)

    @classmethod
    def from_json(cls, path: str | Path) -> "CalendarWorld":
        data = json.loads(Path(path).read_text())
        start_coord = data.get("start_coordinate", [0, 0])
        world = cls(
            start_time=data["start_time"],
            end_time=data["end_time"],
            step_minutes=int(data.get("step_minutes", 15)),
            reminder_window_minutes=int(data.get("reminder_window_minutes", 30)),
            long_work_threshold_minutes=int(data.get("long_work_threshold_minutes", 90)),
            fixed_events=[FixedEvent.from_dict(item) for item in data.get("fixed_events", [])],
            flexible_tasks=[FlexibleTask.from_dict(item) for item in data.get("flexible_tasks", [])],
            hobbies=[Hobby.from_dict(item) for item in data.get("hobbies", [])],
            start_location=data.get("start_location", "home"),
            start_coordinate=tuple(start_coord),
            grid_size=int(data.get("grid_size", 100)),
            minutes_per_unit=float(data.get("minutes_per_unit", 0.2)),
            named_locations=data.get("named_locations"),
        )
        # Dual stress config
        mood_raw = data.get("user_mood", "slightly_stressed")
        if isinstance(mood_raw, int):
            world.user_mood = max(1, min(5, mood_raw))
        else:
            world.user_mood = cls.mood_from_label(str(mood_raw))

        weights = data.get("stress_weights", {})
        world.stress_weight_activity = float(weights.get("activity", 0.5))
        world.stress_weight_mood = float(weights.get("mood", 0.5))

        # Mood events: user changes mood mid-day
        for me in data.get("mood_events", []):
            minute = cls.parse_clock(me["time"])
            mood_val = me.get("mood", "neutral")
            if isinstance(mood_val, int):
                world._mood_events.append((minute, max(1, min(5, mood_val))))
            else:
                world._mood_events.append((minute, cls.mood_from_label(str(mood_val))))
        world._mood_events.sort(key=lambda x: x[0])

        return world

    @staticmethod
    def parse_clock(value: str) -> int:
        hours, minutes = value.split(":")
        return int(hours) * 60 + int(minutes)

    @staticmethod
    def fmt_clock(minute: int) -> str:
        minute = minute % (24 * 60)
        return f"{minute // 60:02d}:{minute % 60:02d}"

    def deadline_to_minute(self, deadline: str) -> Optional[int]:
        if not deadline or deadline.lower() in {"none", "no deadline"}:
            return None
        match = re.search(r"(today|tomorrow)\s+(\d{1,2}:\d{2})", deadline.lower())
        if not match:
            return None
        day, clock = match.groups()
        minute = self.parse_clock(clock)
        if day == "tomorrow":
            minute += 24 * 60
        return minute

    def current_absolute_minute(self) -> int:
        # The MVP simulates one day. If current time wraps past midnight later, this method is where that would expand.
        return self.current_minute

    def next_fixed_event(self) -> Optional[FixedEvent]:
        for event in self.fixed_events:
            if self.parse_clock(event.start) >= self.current_minute and not event.attended:
                return event
        return None

    def active_fixed_event(self) -> Optional[FixedEvent]:
        for event in self.fixed_events:
            start = self.parse_clock(event.start)
            end = self.parse_clock(event.end)
            if start <= self.current_minute < end:
                return event
        return None

    def free_window_minutes(self) -> int:
        """Minutes of free time before the next hard boundary.

        Travel time to the next fixed event is subtracted so the agent never
        schedules work that would make the user late.
        """
        next_event = self.next_fixed_event()
        if next_event is None:
            return max(0, self.end_minute - self.current_minute)
        travel = self.travel_time_to(next_event.location, next_event.coordinate)
        return max(0, self.parse_clock(next_event.start) - self.current_minute - travel)

    def unfinished_tasks(self) -> List[FlexibleTask]:
        return [task for task in self.flexible_tasks if task.status != "completed"]

    # ------------------------------------------------------------------
    # Stress system
    # ------------------------------------------------------------------

    def _clamp_activity_stress(self) -> None:
        self.activity_stress = max(1, min(5, self.activity_stress))

    def _stress_after_work(self, duration: int) -> None:
        """Increase activity stress after focused work."""
        if duration >= 60:
            self.activity_stress += 2
        elif duration >= 30:
            self.activity_stress += 1
        self._clamp_activity_stress()

    def _stress_after_break(self) -> None:
        """Decrease activity stress after a hobby/break."""
        self.activity_stress = max(1, self.activity_stress - 1)

    def process_mood_events(self) -> Optional[str]:
        """Fire any mood events whose time has arrived. Returns a log note or None."""
        fired = None
        while self._mood_events_fired < len(self._mood_events):
            event_minute, mood_val = self._mood_events[self._mood_events_fired]
            if self.current_minute >= event_minute:
                old_mood = self.user_mood
                self.user_mood = mood_val
                label = next((k for k, v in self.MOOD_LABELS.items() if v == mood_val), str(mood_val))
                fired = (
                    f"User mood changed from {old_mood}/5 to {mood_val}/5 ({label}) "
                    f"at {self.fmt_clock(event_minute)}. Overall stress now {self.stress_level}/5."
                )
                self._mood_events_fired += 1
            else:
                break
        return fired

    # ------------------------------------------------------------------
    # Day-start briefing & clash detection
    # ------------------------------------------------------------------

    def detect_clashes(self) -> DayBriefing:
        """Scan the full day for scheduling issues before the loop starts."""
        items: List[BriefingItem] = []

        # 1. Check for overlapping fixed events
        for i, ev_a in enumerate(self.fixed_events):
            for ev_b in self.fixed_events[i + 1:]:
                a_end = self.parse_clock(ev_a.end)
                b_start = self.parse_clock(ev_b.start)
                if a_end > b_start:
                    items.append(BriefingItem(
                        type="clash",
                        title=f"'{ev_a.title}' overlaps with '{ev_b.title}'",
                        detail=(
                            f"{ev_a.title} ends at {ev_a.end} but {ev_b.title} starts at {ev_b.start}. "
                            f"The user cannot attend both fully."
                        ),
                    ))

        # 2. Check travel feasibility between consecutive fixed events
        for i in range(len(self.fixed_events) - 1):
            ev_a = self.fixed_events[i]
            ev_b = self.fixed_events[i + 1]
            a_end = self.parse_clock(ev_a.end)
            b_start = self.parse_clock(ev_b.start)
            gap = b_start - a_end
            coord_a = self._resolve_coord(ev_a.location, ev_a.coordinate)
            coord_b = self._resolve_coord(ev_b.location, ev_b.coordinate)
            if coord_a and coord_b:
                travel = self.travel_time(coord_a, coord_b)
                if travel > gap:
                    items.append(BriefingItem(
                        type="travel_risk",
                        title=f"Tight travel: '{ev_a.title}' -> '{ev_b.title}'",
                        detail=(
                            f"Only {gap} min gap but {travel} min travel needed "
                            f"({ev_a.location} -> {ev_b.location}). User will be late."
                        ),
                    ))

        # 3. Check tasks with deadlines that fall during a fixed event (impossible)
        for task in self.flexible_tasks:
            dl = self.deadline_to_minute(task.deadline)
            if dl is None:
                continue
            for event in self.fixed_events:
                ev_start = self.parse_clock(event.start)
                ev_end = self.parse_clock(event.end)
                if ev_start <= dl <= ev_end:
                    items.append(BriefingItem(
                        type="deadline_risk",
                        title=f"'{task.title}' deadline falls during '{event.title}'",
                        detail=(
                            f"Task deadline is {task.deadline} but '{event.title}' "
                            f"runs {event.start}-{event.end}. The task must be done earlier."
                        ),
                    ))

        # 4. Check if today-due tasks have enough free time
        total_free = 0
        cursor = self.current_minute
        for event in self.fixed_events:
            ev_start = self.parse_clock(event.start)
            ev_end = self.parse_clock(event.end)
            if ev_start > cursor:
                total_free += ev_start - cursor
            cursor = max(cursor, ev_end)
        total_free += max(0, self.end_minute - cursor)

        today_work = sum(
            t.estimated_minutes for t in self.flexible_tasks
            if self._task_due_today(t) and t.status != "completed"
        )
        if today_work > total_free:
            items.append(BriefingItem(
                type="deadline_risk",
                title="Not enough free time for all today-due tasks",
                detail=(
                    f"Today-due tasks need {today_work} min but only {total_free} min "
                    f"of free time available. Prioritize the most critical ones."
                ),
            ))

        briefing = DayBriefing(items=items)
        return briefing

    def observe_full_day(self) -> Dict[str, Any]:
        """Full-day snapshot for the briefing phase (before the loop starts)."""
        events = []
        for event in self.fixed_events:
            events.append({
                "title": event.title,
                "start": event.start,
                "end": event.end,
                "location": event.location,
                "coordinate": list(event.coordinate) if event.coordinate else None,
            })

        tasks = []
        for task in self.flexible_tasks:
            dl = self.deadline_to_minute(task.deadline)
            minutes_until = (dl - self.current_minute) if dl is not None else None
            tasks.append({
                "title": task.title,
                "deadline": task.deadline,
                "minutes_until_deadline": minutes_until,
                "estimated_minutes": task.estimated_minutes,
                "importance": task.importance,
                "urgency": task.urgency,
                "location": task.location,
                "coordinate": list(task.coordinate) if task.coordinate else None,
            })

        return {
            "day_overview": True,
            "start_time": self.fmt_clock(self.current_minute),
            "end_time": self.fmt_clock(self.end_minute),
            "start_location": self.current_location,
            "start_coordinate": list(self.current_coordinate),
            "grid_size": self.grid_size,
            "minutes_per_unit": self.minutes_per_unit,
            "stress_level": self.stress_level,
            "activity_stress": self.activity_stress,
            "user_mood": self.user_mood,
            "stress_weights": {"activity": self.stress_weight_activity, "mood": self.stress_weight_mood},
            "mood_events_scheduled": len(self._mood_events),
            "fixed_events": events,
            "flexible_tasks": tasks,
            "hobbies": [{"title": h.title, "estimated_minutes": h.estimated_minutes, "category": h.category, "location": h.location} for h in self.hobbies],
        }

    def observe(self) -> Dict[str, Any]:
        next_event = self.next_fixed_event()
        active_event = self.active_fixed_event()
        free_window = self.free_window_minutes()

        upcoming_events = []
        for event in self.fixed_events:
            start = self.parse_clock(event.start)
            if start >= self.current_minute and not event.attended:
                upcoming_events.append(
                    {
                        "title": event.title,
                        "start": event.start,
                        "end": event.end,
                        "location": event.location,
                        "coordinate": list(event.coordinate) if event.coordinate else None,
                        "minutes_until_start": start - self.current_minute,
                        "travel_minutes_from_here": self.travel_time_to(event.location, event.coordinate),
                        "reminder_sent": event.reminder_sent,
                    }
                )

        tasks = []
        for task in self.unfinished_tasks():
            deadline_minute = self.deadline_to_minute(task.deadline)
            minutes_until_deadline = None
            if deadline_minute is not None:
                minutes_until_deadline = deadline_minute - self.current_absolute_minute()
            effective = task.effective_minutes(self.current_location)
            travel_to_task = self.travel_time_to(task.location, task.coordinate)
            obs = task.to_observation(minutes_until_deadline, effective_minutes=effective)
            obs["travel_minutes_from_here"] = travel_to_task
            obs["total_minutes_from_here"] = travel_to_task + effective
            tasks.append(obs)

        tasks.sort(
            key=lambda item: (
                {"high": 0, "medium": 1, "low": 2}.get(item["urgency"], 1),
                {"high": 0, "medium": 1, "low": 2}.get(item["importance"], 1),
                item["minutes_until_deadline"] if item["minutes_until_deadline"] is not None else 99999,
            )
        )

        # Build hobby observations with location info
        hobby_obs = []
        for hobby in self.hobbies:
            h = {
                "title": hobby.title,
                "estimated_minutes": hobby.estimated_minutes,
                "category": hobby.category,
                "location": hobby.location,
                "coordinate": list(hobby.coordinate) if hobby.coordinate else None,
                "travel_minutes_from_here": self.travel_time_to(hobby.location, hobby.coordinate),
            }
            hobby_obs.append(h)

        return {
            "current_time": self.fmt_clock(self.current_minute),
            "current_location": self.current_location,
            "current_coordinate": list(self.current_coordinate),
            "grid_size": self.grid_size,
            "minutes_per_unit": self.minutes_per_unit,
            "stress_level": self.stress_level,
            "activity_stress": self.activity_stress,
            "user_mood": self.user_mood,
            "stress_weights": {"activity": self.stress_weight_activity, "mood": self.stress_weight_mood},
            "active_fixed_event": None
            if active_event is None
            else {
                "title": active_event.title,
                "start": active_event.start,
                "end": active_event.end,
                "location": active_event.location,
            },
            "next_fixed_event": None
            if next_event is None
            else {
                "title": next_event.title,
                "start": next_event.start,
                "end": next_event.end,
                "location": next_event.location,
                "coordinate": list(next_event.coordinate) if next_event.coordinate else None,
                "minutes_until_start": self.parse_clock(next_event.start) - self.current_minute,
                "travel_minutes_from_here": self.travel_time_to(next_event.location, next_event.coordinate),
                "reminder_sent": next_event.reminder_sent,
            },
            "free_window_minutes": free_window,
            "focus_minutes_since_break": self.focus_minutes_since_break,
            "recent_activity": self.recent_activity[-3:],
            "flexible_tasks": tasks,
            "available_hobbies": hobby_obs,
            "valid_actions": sorted(VALID_ACTIONS),
            "rules": [
                "Fixed events cannot be moved.",
                "Remind the user before fixed events when possible.",
                "Do not start work or leisure that exceeds the free window (which already accounts for travel time to the next fixed event).",
                "Prefer urgent and important tasks before leisure.",
                "Suggest rest or entertainment after long focus blocks when no urgent task is at risk.",
                "Tasks with deadlines beyond today do not need to be completed today; deprioritize them if today's schedule is full.",
                "The world is a 100x100 grid. Travel time = Manhattan distance × minutes_per_unit. travel_minutes_from_here is pre-computed for you.",
                "effective_minutes is the task duration adjusted for your current location (may differ from estimated_minutes).",
                "stress_level = weighted(activity_stress, user_mood). Range 1 (relaxed) to 5 (overwhelmed). At 4-5: strongly prefer breaks/hobbies and defer non-urgent tasks. At 1-2: user can handle more work. user_mood reflects subjective feeling; activity_stress reflects work fatigue today.",
            ],
        }

    def validate_action(self, action: AgentAction) -> Tuple[bool, Optional[str]]:
        if action.action not in VALID_ACTIONS:
            return False, f"Invalid action '{action.action}'. Expected one of {sorted(VALID_ACTIONS)}."

        if action.action in {"remind_event", "start_task", "suggest_hobby"} and not action.target:
            return False, f"Action '{action.action}' requires a target."

        if action.action == "remind_event":
            event = self.find_event(action.target or "")
            if event is None:
                return False, f"Cannot remind unknown event '{action.target}'."
            start = self.parse_clock(event.start)
            if event.reminder_sent:
                return False, f"Reminder already sent for '{event.title}'."
            if start < self.current_minute:
                return False, f"Cannot remind event '{event.title}' after it started."
            if start - self.current_minute > self.reminder_window_minutes:
                return False, f"Too early to remind '{event.title}'."

        if action.action == "start_task":
            task = self.find_task(action.target or "")
            if task is None:
                return False, f"Cannot start unknown task '{action.target}'."
            if task.status == "completed":
                return False, f"Task '{task.title}' is already completed."
            if not action.duration_minutes or action.duration_minutes <= 0:
                return False, "start_task requires a positive duration_minutes value."
            if action.duration_minutes > self.free_window_minutes() and self.next_fixed_event() is not None:
                return False, "Task duration exceeds free window before the next fixed event."

        if action.action == "suggest_hobby":
            hobby = self.find_hobby(action.target or "")
            if hobby is None:
                return False, f"Cannot suggest unknown hobby '{action.target}'."
            if not action.duration_minutes or action.duration_minutes <= 0:
                return False, "suggest_hobby requires a positive duration_minutes value."
            if action.duration_minutes > self.free_window_minutes() and self.next_fixed_event() is not None:
                return False, "Hobby duration exceeds free window before the next fixed event."

        return True, None

    def _move_to(self, location: str, coord: Optional[Tuple[int, int]] = None) -> None:
        """Update the user's position on the grid."""
        resolved = self._resolve_coord(location, coord)
        if location != "anywhere":
            self.current_location = location
        if resolved is not None:
            self.current_coordinate = resolved

    def find_event(self, title: str) -> Optional[FixedEvent]:
        return next((event for event in self.fixed_events if event.title == title), None)

    def find_task(self, title: str) -> Optional[FlexibleTask]:
        return next((task for task in self.flexible_tasks if task.title == title), None)

    def find_hobby(self, title: str) -> Optional[Hobby]:
        return next((hobby for hobby in self.hobbies if hobby.title == title), None)

    def apply_action(self, observation: Dict[str, Any], action: AgentAction) -> None:
        valid, error = self.validate_action(action)
        record = StepRecord(
            time=self.fmt_clock(self.current_minute),
            observation=observation,
            action=action.to_dict(),
        )

        if not valid:
            self.invalid_actions += 1
            record.validation_error = error
            record.world_update = "Invalid action rejected. The world advances by one small step."
            self.current_minute = min(self.end_minute, self.current_minute + self.step_minutes)
            self.records.append(record)
            return

        if action.action == "remind_event":
            event = self.find_event(action.target or "")
            assert event is not None
            event.reminder_sent = True
            event_start = self.parse_clock(event.start)
            advance = max(1, min(self.step_minutes, event_start - self.current_minute))
            record.world_update = (
                f"Reminder sent for '{event.title}'. Time advances {advance} minutes toward the event."
            )
            self.current_minute += advance

        elif action.action == "start_task":
            task = self.find_task(action.target or "")
            assert task is not None
            # Travel to task location first
            travel = self.travel_time_to(task.location, task.coordinate)
            if travel > 0:
                dest_coord = self._resolve_coord(task.location, task.coordinate)
                self.recent_activity.append(
                    f"Travelled from {self.current_location}{list(self.current_coordinate)} "
                    f"to {task.location}{list(dest_coord) if dest_coord else '?'} ({travel} min)"
                )
                self.current_minute += travel
                self._move_to(task.location, task.coordinate)

            effective = task.effective_minutes(self.current_location)
            duration = min(action.duration_minutes or self.step_minutes, task.remaining_minutes or 0, effective)
            duration = max(1, duration)
            task.remaining_minutes = max(0, (task.remaining_minutes or 0) - duration)
            task.status = "completed" if task.remaining_minutes == 0 else "in_progress"
            self.focus_minutes_since_break += duration
            travel_note = f" (incl. {travel} min travel)" if travel > 0 else ""
            self.recent_activity.append(f"Worked on '{task.title}' for {duration} minutes{travel_note}")
            self._stress_after_work(duration)
            record.world_update = (
                f"User works on '{task.title}' for {duration} minutes{travel_note}. "
                f"Remaining: {task.remaining_minutes} minutes. Status: {task.status}. "
                f"Position: {self.current_location}{list(self.current_coordinate)}. "
                f"Stress: {self.stress_level}/5."
            )
            self.current_minute += duration

        elif action.action == "suggest_hobby":
            hobby = self.find_hobby(action.target or "")
            assert hobby is not None
            # Travel to hobby location first
            travel = self.travel_time_to(hobby.location, hobby.coordinate)
            if travel > 0:
                dest_coord = self._resolve_coord(hobby.location, hobby.coordinate)
                self.recent_activity.append(
                    f"Travelled from {self.current_location}{list(self.current_coordinate)} "
                    f"to {hobby.location}{list(dest_coord) if dest_coord else '?'} ({travel} min)"
                )
                self.current_minute += travel
                self._move_to(hobby.location, hobby.coordinate)

            duration = min(action.duration_minutes or hobby.estimated_minutes, hobby.estimated_minutes)
            duration = max(1, duration)
            self.focus_minutes_since_break = 0
            self.hobby_suggestions += 1
            travel_note = f" (incl. {travel} min travel)" if travel > 0 else ""
            self.recent_activity.append(f"Took a restorative break: '{hobby.title}' for {duration} minutes{travel_note}")
            self._stress_after_break()
            record.world_update = (
                f"User does '{hobby.title}' for {duration} minutes{travel_note}. "
                f"Focus fatigue resets. Stress: {self.stress_level}/5. "
                f"Position: {self.current_location}{list(self.current_coordinate)}."
            )
            self.current_minute += duration

        elif action.action == "wait":
            next_event = self.next_fixed_event()
            if next_event is not None:
                minutes_until = self.parse_clock(next_event.start) - self.current_minute
                advance = max(1, min(self.step_minutes, minutes_until))
            else:
                advance = self.step_minutes
            record.world_update = f"No useful action chosen. Time advances by {advance} minutes."
            self.current_minute = min(self.end_minute, self.current_minute + advance)

        self.records.append(record)

    def process_fixed_event_if_active(self) -> bool:
        event = self.active_fixed_event()
        if event is None:
            return False

        start = self.parse_clock(event.start)
        end = self.parse_clock(event.end)
        event.attended = True
        self.focus_minutes_since_break = 0
        self._move_to(event.location, event.coordinate)
        self.recent_activity.append(
            f"Attended fixed event '{event.title}' at {event.location}{list(self.current_coordinate)}"
        )
        self.records.append(
            StepRecord(
                time=self.fmt_clock(self.current_minute),
                observation=self.observe(),
                action=None,
                world_update=(
                    f"Fixed event '{event.title}' is in progress at {event.location}{list(self.current_coordinate)}. "
                    f"World advances from {self.fmt_clock(max(self.current_minute, start))} to {self.fmt_clock(end)}."
                ),
            )
        )
        self.current_minute = end
        return True

    def _task_due_today(self, task: FlexibleTask) -> bool:
        """Return True if a task's deadline falls within today (before end_minute)."""
        deadline_minute = self.deadline_to_minute(task.deadline)
        if deadline_minute is None:
            return False  # No deadline -> not due today
        return deadline_minute <= self.end_minute

    def is_finished(self) -> bool:
        # Only tasks that are both high-priority AND due today must be completed.
        today_urgent = [
            task for task in self.flexible_tasks
            if (task.urgency == "high" or task.importance == "high") and self._task_due_today(task)
        ]
        no_urgent_today = all(task.status == "completed" for task in today_urgent) if today_urgent else True
        no_events = all(event.attended for event in self.fixed_events)
        return self.current_minute >= self.end_minute or (no_urgent_today and no_events and self.current_minute >= self.parse_clock("21:50"))

    def summary(self) -> RunSummary:
        high_tasks = [task for task in self.flexible_tasks if task.importance == "high" or task.urgency == "high"]
        high_done = [task for task in high_tasks if task.status == "completed"]
        notes = []
        if self.invalid_actions == 0:
            notes.append("All agent actions passed environment validation.")
        if self.hobby_suggestions > 0:
            notes.append("The agent suggested at least one restorative activity after work blocks.")
        if all(event.reminder_sent for event in self.fixed_events):
            notes.append("The agent reminded the user before every fixed event.")
        return RunSummary(
            fixed_events_attended=sum(1 for event in self.fixed_events if event.attended),
            fixed_events_total=len(self.fixed_events),
            fixed_events_reminded=sum(1 for event in self.fixed_events if event.reminder_sent),
            high_priority_tasks_completed=len(high_done),
            high_priority_tasks_total=len(high_tasks),
            hobby_suggestions=self.hobby_suggestions,
            invalid_actions=self.invalid_actions,
            final_time=self.fmt_clock(self.current_minute),
            stress_end=self.stress_level,
            notes=notes,
        )

    def render_log(self) -> str:
        lines = ["# CalendarWorld-Agent run log", ""]

        # Briefing section
        if self.briefing:
            lines.append("## Day-Start Briefing")
            lines.append(self.briefing.render())
            lines.append("")

        for record in self.records:
            lines.append(f"## {record.time}")
            if record.action is None:
                lines.append(f"**World update:** {record.world_update}")
            else:
                lines.append(f"**Observation:** {self.short_observation(record.observation)}")
                lines.append(f"**Agent action:** `{json.dumps(record.action)}`")
                if record.validation_error:
                    lines.append(f"**Validation error:** {record.validation_error}")
                lines.append(f"**World update:** {record.world_update}")
            lines.append("")

        summary = self.summary()
        lines.extend(
            [
                "# Summary",
                f"Fixed events attended: {summary.fixed_events_attended}/{summary.fixed_events_total}",
                f"Fixed event reminders sent: {summary.fixed_events_reminded}/{summary.fixed_events_total}",
                f"High-priority or urgent tasks completed: {summary.high_priority_tasks_completed}/{summary.high_priority_tasks_total}",
                f"Hobby/rest suggestions: {summary.hobby_suggestions}",
                f"Invalid actions: {summary.invalid_actions}",
                f"Final stress: {summary.stress_end}/5 (activity={self.activity_stress}/5, mood={self.user_mood}/5, weights={self.stress_weight_activity:.1f}/{self.stress_weight_mood:.1f})",
                f"Final time: {summary.final_time}",
            ]
        )
        if summary.notes:
            lines.append("")
            lines.append("Notes:")
            for note in summary.notes:
                lines.append(f"- {note}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Friendly schedule helpers
    # ------------------------------------------------------------------

    _step_counter: int = 0  # rotates through message variants

    def _friendly_reason(self, action_type: str, reason: str, duration: Optional[int], obs: Dict[str, Any] = None, target: str = "") -> str:
        """Turn a raw agent reason into natural, varied language."""
        dur = f"{duration} min" if duration else ""
        rl = reason.lower()
        obs = obs or {}
        self._step_counter += 1
        pick = self._step_counter  # use to rotate phrasing

        next_ev = obs.get("next_fixed_event")
        next_title = next_ev.get("title", "") if next_ev else ""
        next_mins = next_ev.get("minutes_until_start") if next_ev else None
        free_window = obs.get("free_window_minutes", 0)
        focus = obs.get("focus_minutes_since_break", 0)

        # Resolve travel
        travel = 0
        item_loc = ""
        for t in obs.get("flexible_tasks", []):
            if t.get("title") == target:
                travel = t.get("travel_minutes_from_here", 0)
                item_loc = t.get("location", "")
                break
        if not travel:
            for h in obs.get("available_hobbies", []):
                if h.get("title") == target:
                    travel = h.get("travel_minutes_from_here", 0)
                    item_loc = h.get("location", "")
                    break

        travel_bit = ""
        if travel > 0 and item_loc and item_loc != "anywhere":
            travel_bit = f" ({travel} min to {item_loc})"

        # Short name for the target -- only use in details if it's concise.
        # Long names are already visible in the "What" column, so we say "this" instead.
        target = target or ""
        short = target if len(target) <= 25 else "this"

        if action_type == "start_task":
            if "high urgency" in rl and "high importance" in rl:
                return f"{dur}{travel_bit}. Top priority right now."
            if "high urgency" in rl or "urgent" in rl:
                variants = [
                    f"{dur}{travel_bit}. Due soon -- good to get it out of the way.",
                    f"{dur}{travel_bit}. Deadline approaching{', fits well before ' + next_title if next_title else ''}.",
                    f"{dur}{travel_bit}. You have {free_window} min free and this is time-sensitive.",
                ]
                return variants[pick % len(variants)]
            if "high importance" in rl or "important" in rl:
                variants = [
                    f"{dur}{travel_bit}. Nice block of time for focused work.",
                    f"{dur}{travel_bit}. {free_window} min window, good stretch to make progress." if free_window > duration else f"{dur}{travel_bit}. Worth diving in while you can.",
                    f"{dur}{travel_bit}. You have the space, worth diving in.",
                ]
                return variants[pick % len(variants)]
            if "best remaining" in rl or "fits" in rl:
                variants = [
                    f"{dur}{travel_bit}. Fits the gap well.",
                    f"{dur}{travel_bit}. Quick one while you have {free_window} min free.",
                    f"{dur}{travel_bit}. Slot's open, good time for {short}." if short != "this" else f"{dur}{travel_bit}. Slot's open, might as well.",
                ]
                return variants[pick % len(variants)]
            return f"{dur}{travel_bit}."

        if action_type == "suggest_hobby":
            if "long focus" in rl or "focus block" in rl:
                variants = [
                    f"{dur}{travel_bit}. You've been at it for {focus} min. Step away for a bit.",
                    f"{dur}{travel_bit}. {focus} min of work so far. A short break before you continue.",
                    f"{dur}{travel_bit}. Been focused for a while, good time to reset.",
                ]
                return variants[pick % len(variants)]
            if "no suitable task" in rl or "no task" in rl:
                variants = [
                    f"{dur}{travel_bit}. Nothing pressing. Enjoy.",
                    f"{dur}{travel_bit}. Free window, no rush.",
                    f"{dur}{travel_bit}. {next_title} is later at {next_ev.get('start', '')}. Relax until then." if next_ev else f"{dur}{travel_bit}. Rest of the evening is yours.",
                ]
                return variants[pick % len(variants)]
            if "stress" in rl:
                return f"{dur}{travel_bit}. Stress is up. Stepping away for a bit will help."
            return f"{dur}{travel_bit}."

        if action_type == "wait":
            if next_title and next_mins and next_mins <= 15:
                return f"{next_title} in {next_mins} min. Sit tight."
            variants = [
                "Small gap. Stretch, grab water.",
                "Nothing fits here. Take a breather.",
                "Short pause before the next thing.",
            ]
            return variants[pick % len(variants)]

        return reason

    def _stress_narrative(self) -> str:
        """Generate a warm, human summary of the day's stress arc."""
        # Collect stress snapshots from records
        stress_points: List[Tuple[str, int]] = []
        for record in self.records:
            sl = record.observation.get("stress_level")
            if sl is not None:
                stress_points.append((record.time, int(sl)))

        if not stress_points:
            return "No stress data recorded."

        peak = max(stress_points, key=lambda x: x[1])
        low = min(stress_points, key=lambda x: x[1])
        final = self.stress_level

        parts: List[str] = []

        # Opening
        if final <= 2:
            parts.append("You're ending the day feeling calm and recharged.")
        elif final == 3:
            parts.append("You're wrapping up the day in a balanced state.")
        else:
            parts.append("It's been a demanding day, but you got through it.")

        # Peak moment
        if peak[1] >= 4:
            parts.append(f"Things got intense around {peak[0]} (stress peaked at {peak[1]}/5),")
            if final <= 2:
                parts.append("but the breaks you took really helped bring it back down.")
            elif final <= 3:
                parts.append("and you managed to ease off a bit by the end.")
            else:
                parts.append("so make sure to take it easy tonight.")
        elif peak[1] <= 2:
            parts.append("Your stress stayed low throughout -- great pacing!")

        # Mood events
        mood_shifts = [r for r in self.records if r.action is None and "mood changed" in (r.world_update or "").lower()]
        if mood_shifts:
            parts.append(f"You adjusted your mood {len(mood_shifts)} time(s) during the day, and the planner adapted accordingly.")

        # Closing
        if final <= 2:
            parts.append("Rest well tonight!")
        elif final == 3:
            parts.append("A good night's sleep will set you up for tomorrow.")
        else:
            parts.append("Consider a lighter schedule tomorrow to recover.")

        return " ".join(parts)

    def render_schedule(self) -> str:
        """Render a clean, friendly day schedule that feels like a productivity app."""
        lines: List[str] = []
        lines.append("# Your Day Plan")
        lines.append("")

        # --- Briefing ---
        if self.briefing and self.briefing.items:
            lines.append("## Heads Up")
            lines.append("")
            for item in self.briefing.items:
                tag = item.type.replace("_", " ").title()
                lines.append(f"- **{tag}**: {item.title} -- {item.detail}")
            lines.append("")
        if self.briefing and self.briefing.suggested_plan:
            lines.append(f"> {self.briefing.suggested_plan}")
            lines.append("")

        # --- Schedule table ---
        lines.append("## Schedule")
        lines.append("")
        lines.append("| Time | What | Where | Details |")
        lines.append("|------|------|-------|---------|")

        for record in self.records:
            time = record.time
            obs = record.observation
            location = obs.get("current_location", "")

            if record.action is None:
                update = record.world_update
                if "is in progress" in update:
                    active = obs.get("active_fixed_event")
                    event_title = active["title"] if active else "Event"
                    event_loc = active.get("location", location) if active else location
                    start = active.get("start", "")
                    end = active.get("end", "")
                    dur_text = ""
                    if start and end:
                        s = self.parse_clock(start)
                        e = self.parse_clock(end)
                        dur_text = f"{e - s} min"
                    lines.append(f"| {time} | **{event_title}** | {event_loc} | {dur_text}. Scheduled event |")
                elif "mood changed" in update.lower():
                    # Make mood update friendly
                    new_mood = obs.get("user_mood", "?")
                    label = next((k.replace("_", " ") for k, v in self.MOOD_LABELS.items() if v == new_mood), str(new_mood))
                    lines.append(f"| {time} | *Feeling: {label}* | - | Mood check-in |")
            else:
                action = record.action or {}
                act_type = action.get("action", "")
                target = action.get("target", "")
                reason = action.get("reason", "")
                duration = action.get("duration_minutes")

                if record.validation_error:
                    continue

                friendly = self._friendly_reason(act_type, reason, duration, obs=obs, target=target)

                if act_type == "remind_event":
                    # Find how many minutes until the event
                    next_ev = obs.get("next_fixed_event")
                    mins = next_ev.get("minutes_until_start", "?") if next_ev else "?"
                    lines.append(f"| {time} | **{target}** in {mins} min | - | Get ready to head out |")
                elif act_type == "start_task":
                    lines.append(f"| {time} | **{target}** | {location} | {friendly} |")
                elif act_type == "suggest_hobby":
                    lines.append(f"| {time} | **{target}** | {location} | {friendly} |")
                elif act_type == "wait":
                    lines.append(f"| {time} | *Free time* | {location} | {friendly} |")

        lines.append("")

        # --- Task results ---
        lines.append("## Task Results")
        lines.append("")
        lines.append("| Task | Status | Deadline |")
        lines.append("|------|--------|----------|")
        for task in self.flexible_tasks:
            if task.status == "completed":
                status = "Done"
            else:
                status = f"Open ({task.remaining_minutes} min left)"
            deadline = task.deadline if task.deadline.lower() not in {"none", "no deadline"} else "/"
            lines.append(f"| {task.title} | {status} | {deadline} |")
        lines.append("")

        # --- Summary ---
        summary = self.summary()
        lines.append("## How Did It Go?")
        lines.append("")
        lines.append(f"- **Events attended**: {summary.fixed_events_attended}/{summary.fixed_events_total}")
        lines.append(f"- **Key tasks completed**: {summary.high_priority_tasks_completed}/{summary.high_priority_tasks_total}")
        lines.append(f"- **Breaks taken**: {summary.hobby_suggestions}")
        lines.append("")
        lines.append(f"> {self._stress_narrative()}")

        return "\n".join(lines)

    @staticmethod
    def short_observation(observation: Dict[str, Any]) -> str:
        next_event = observation.get("next_fixed_event")
        tasks = observation.get("flexible_tasks", [])
        first_task = tasks[0]["title"] if tasks else "none"
        location = observation.get("current_location", "?")
        coord = observation.get("current_coordinate", "?")
        if next_event:
            event_text = f"next event '{next_event['title']}' in {next_event['minutes_until_start']} min"
        else:
            event_text = "no upcoming fixed event"
        stress = observation.get("stress_level", "?")
        return (
            f"time={observation['current_time']}, pos={location}{coord}, stress={stress}/5, "
            f"free_window={observation['free_window_minutes']} min, "
            f"{event_text}, focus_since_break={observation['focus_minutes_since_break']} min, "
            f"top_task={first_task}"
        )
