"""Vision state cache and event generation."""

from enum import Enum
from dataclasses import dataclass
import time
from .tracker import TrackedPerson, PositionZone, DistanceZone


class PersonEventType(Enum):
    PERSON_APPEARED = "person_appeared"
    POSITION_CHANGED = "position_changed"
    DISTANCE_CHANGED = "distance_changed"
    PERSON_LOST = "person_lost"


@dataclass
class VisionEvent:
    type: PersonEventType
    person: TrackedPerson | None
    previous_zone: PositionZone | DistanceZone | None
    current_zone: PositionZone | DistanceZone | None
    timestamp: float


@dataclass
class VisionState:
    has_person: bool
    position_zone: PositionZone | None
    distance_zone: DistanceZone | None
    offset_ratio: float
    height_ratio: float
    confidence: float
    fps: float
    timestamp: float


class VisionStateCache:
    def __init__(self, appear_frames=10, lost_frames=30):
        self.appear_frames = appear_frames
        self.lost_frames = lost_frames
        self._prev_has_person = False
        self._prev_position = None
        self._prev_distance = None
        self._person_confirmed = False
        self._state = None

    def update(self, tracked: TrackedPerson | None, fps: float) -> list[VisionEvent]:
        events = []
        now = time.time()

        has_person = tracked is not None and tracked.frames_lost == 0
        position = tracked.position_zone if tracked and has_person else None
        distance = tracked.distance_zone if tracked and has_person else None
        offset = tracked.offset_ratio if tracked else 0.0
        height = tracked.height_ratio if tracked else 0.0
        confidence = tracked.detection.confidence if tracked else 0.0

        # Person appeared
        if has_person and tracked.frames_tracked >= self.appear_frames:
            if not self._person_confirmed:
                self._person_confirmed = True
                events.append(VisionEvent(
                    type=PersonEventType.PERSON_APPEARED,
                    person=tracked,
                    previous_zone=None,
                    current_zone=position,
                    timestamp=now,
                ))

        # Person lost
        if not has_person and self._person_confirmed:
            if tracked and tracked.frames_lost >= self.lost_frames:
                self._person_confirmed = False
                events.append(VisionEvent(
                    type=PersonEventType.PERSON_LOST,
                    person=None,
                    previous_zone=self._prev_position,
                    current_zone=None,
                    timestamp=now,
                ))

        # Position changed
        if has_person and position != self._prev_position and self._person_confirmed:
            events.append(VisionEvent(
                type=PersonEventType.POSITION_CHANGED,
                person=tracked,
                previous_zone=self._prev_position,
                current_zone=position,
                timestamp=now,
            ))

        # Distance changed
        if has_person and distance != self._prev_distance and self._person_confirmed:
            events.append(VisionEvent(
                type=PersonEventType.DISTANCE_CHANGED,
                person=tracked,
                previous_zone=self._prev_distance,
                current_zone=distance,
                timestamp=now,
            ))

        self._prev_has_person = has_person
        if has_person:
            self._prev_position = position
            self._prev_distance = distance

        self._state = VisionState(
            has_person=has_person,
            position_zone=position,
            distance_zone=distance,
            offset_ratio=offset,
            height_ratio=height,
            confidence=confidence,
            fps=fps,
            timestamp=now,
        )
        return events

    @property
    def current_state(self) -> VisionState:
        return self._state
