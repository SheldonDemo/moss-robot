"""Simple single-person tracker with position and distance zone estimation."""

from enum import Enum
from dataclasses import dataclass, field
from .detector import Detection


class PositionZone(Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class DistanceZone(Enum):
    CLOSE = "close"
    MEDIUM = "medium"
    FAR = "far"


@dataclass
class TrackedPerson:
    detection: Detection
    position_zone: PositionZone
    distance_zone: DistanceZone
    offset_ratio: float
    height_ratio: float
    frames_tracked: int = 1
    frames_lost: int = 0


class PersonTracker:
    def __init__(self, frame_width, frame_height,
                 position_thresholds=(0.35, 0.65),
                 distance_thresholds=(0.30, 0.60),
                 match_distance=0.3):
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.pos_low, self.pos_high = position_thresholds
        self.dist_low, self.dist_high = distance_thresholds
        self.match_distance = match_distance
        self._tracked = None

    def update(self, detections: list[Detection]) -> TrackedPerson | None:
        if not detections:
            if self._tracked is not None:
                self._tracked.frames_lost += 1
                self._tracked.frames_tracked = 0
            return self._tracked

        best = None
        if self._tracked is not None and self._tracked.frames_lost < 30:
            prev_cx = self._tracked.offset_ratio
            for det in detections:
                cx = det.center_x / self.frame_width
                if abs(cx - prev_cx) < self.match_distance:
                    best = det
                    break

        if best is None:
            best = detections[0]

        offset = best.center_x / self.frame_width
        height_ratio = best.height / self.frame_height

        if offset < self.pos_low:
            pos = PositionZone.LEFT
        elif offset > self.pos_high:
            pos = PositionZone.RIGHT
        else:
            pos = PositionZone.CENTER

        if height_ratio > self.dist_high:
            dist = DistanceZone.CLOSE
        elif height_ratio > self.dist_low:
            dist = DistanceZone.MEDIUM
        else:
            dist = DistanceZone.FAR

        prev_tracked = self._tracked.frames_tracked if self._tracked else 0
        self._tracked = TrackedPerson(
            detection=best,
            position_zone=pos,
            distance_zone=dist,
            offset_ratio=offset,
            height_ratio=height_ratio,
            frames_tracked=prev_tracked + 1,
            frames_lost=0,
        )
        return self._tracked

    def reset(self):
        self._tracked = None

    @property
    def has_target(self):
        return self._tracked is not None and self._tracked.frames_lost == 0
