from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

VALID_ACTIONS = {
    "remind_event",
    "start_task",
    "suggest_hobby",
    "wait",
}

Importance = Literal["low", "medium", "high"]
Urgency = Literal["low", "medium", "high"]


Coordinate = Optional[Tuple[int, int]]


@dataclass
class FixedEvent:
    title: str
    start: str
    end: str
    location: str = ""
    coordinate: Coordinate = None
    reminder_sent: bool = False
    attended: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FixedEvent":
        coord = data.get("coordinate")
        return cls(
            title=data["title"],
            start=data["start"],
            end=data["end"],
            location=data.get("location", ""),
            coordinate=tuple(coord) if coord else None,
        )


@dataclass
class FlexibleTask:
    title: str
    deadline: str
    estimated_minutes: int
    importance: Importance
    urgency: Urgency
    category: str = "general"
    location: str = "anywhere"
    coordinate: Coordinate = None
    status: str = "not_started"
    remaining_minutes: Optional[int] = None
    base_minutes: Optional[Dict[str, int]] = None

    def __post_init__(self) -> None:
        if self.remaining_minutes is None:
            self.remaining_minutes = self.estimated_minutes

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FlexibleTask":
        coord = data.get("coordinate")
        return cls(
            title=data["title"],
            deadline=data.get("deadline", "none"),
            estimated_minutes=int(data["estimated_minutes"]),
            importance=data.get("importance", "medium"),
            urgency=data.get("urgency", "medium"),
            category=data.get("category", "general"),
            location=data.get("location", "anywhere"),
            coordinate=tuple(coord) if coord else None,
            status=data.get("status", "not_started"),
            remaining_minutes=data.get("remaining_minutes"),
            base_minutes=data.get("base_minutes"),
        )

    def effective_minutes(self, current_location: str) -> int:
        """Return the task duration adjusted for the user's current location."""
        if self.base_minutes and current_location in self.base_minutes:
            return self.base_minutes[current_location]
        return self.estimated_minutes

    def to_observation(self, minutes_until_deadline: Optional[int], *, effective_minutes: Optional[int] = None) -> Dict[str, Any]:
        return {
            "title": self.title,
            "deadline": self.deadline,
            "minutes_until_deadline": minutes_until_deadline,
            "estimated_minutes": self.estimated_minutes,
            "effective_minutes": effective_minutes if effective_minutes is not None else self.estimated_minutes,
            "remaining_minutes": self.remaining_minutes,
            "importance": self.importance,
            "urgency": self.urgency,
            "category": self.category,
            "location": self.location,
            "status": self.status,
        }


@dataclass
class Hobby:
    title: str
    estimated_minutes: int
    category: str = "entertainment"
    location: str = "anywhere"
    coordinate: Coordinate = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Hobby":
        coord = data.get("coordinate")
        return cls(
            title=data["title"],
            estimated_minutes=int(data["estimated_minutes"]),
            category=data.get("category", "entertainment"),
            location=data.get("location", "anywhere"),
            coordinate=tuple(coord) if coord else None,
        )


@dataclass
class AgentAction:
    action: str
    target: Optional[str] = None
    duration_minutes: Optional[int] = None
    reason: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentAction":
        return cls(
            action=data.get("action", ""),
            target=data.get("target"),
            duration_minutes=data.get("duration_minutes"),
            reason=data.get("reason", ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "duration_minutes": self.duration_minutes,
            "reason": self.reason,
        }


@dataclass
class BriefingItem:
    """One finding from the day-start briefing."""
    type: str  # "clash", "travel_risk", "deadline_risk", "suggestion"
    title: str
    detail: str


@dataclass
class DayBriefing:
    """Full day-start briefing produced before the tick-by-tick loop."""
    items: List[BriefingItem] = field(default_factory=list)
    suggested_plan: str = ""

    def has_issues(self) -> bool:
        return any(item.type in {"clash", "travel_risk", "deadline_risk"} for item in self.items)

    def render(self) -> str:
        if not self.items and not self.suggested_plan:
            return "No issues detected. The day looks manageable."
        lines: List[str] = []
        for item in self.items:
            icon = {"clash": "[!]", "travel_risk": "[~]", "deadline_risk": "[*]", "suggestion": "[>]"}.get(item.type, "[-]")
            lines.append(f"{icon} [{item.type.upper()}] {item.title}")
            lines.append(f"    {item.detail}")
        if self.suggested_plan:
            lines.append("")
            lines.append(f"Suggested plan: {self.suggested_plan}")
        return "\n".join(lines)


@dataclass
class StepRecord:
    time: str
    observation: Dict[str, Any]
    action: Optional[Dict[str, Any]] = None
    world_update: str = ""
    validation_error: Optional[str] = None


@dataclass
class RunSummary:
    fixed_events_attended: int
    fixed_events_total: int
    fixed_events_reminded: int
    high_priority_tasks_completed: int
    high_priority_tasks_total: int
    hobby_suggestions: int
    invalid_actions: int
    final_time: str
    stress_end: int = 3
    notes: List[str] = field(default_factory=list)
