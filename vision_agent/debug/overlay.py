"""OpenCV debug overlay with detection boxes, zones, and status."""

import cv2
import numpy as np
from vision_agent.vision.detector import Detection
from vision_agent.vision.tracker import TrackedPerson, PositionZone, DistanceZone
from vision_agent.vision.state import VisionState


class DebugOverlay:
    def __init__(self, enabled=True, window_name="MOSS Vision Agent"):
        self.enabled = enabled
        self.window_name = window_name
        if enabled:
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    def update(self, frame, detections, tracked, state):
        if not self.enabled:
            return
        display = frame.copy()
        h, w = display.shape[:2]

        # Zone lines
        cv2.line(display, (int(w * 0.35), 0), (int(w * 0.35), h), (100, 100, 100), 1)
        cv2.line(display, (int(w * 0.65), 0), (int(w * 0.65), h), (100, 100, 100), 1)
        cv2.line(display, (0, int(h * 0.60)), (w, int(h * 0.60)), (100, 100, 100), 1)
        cv2.line(display, (0, int(h * 0.30)), (w, int(h * 0.30)), (100, 100, 100), 1)

        # Zone labels
        cv2.putText(display, "LEFT", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 100, 100), 1)
        cv2.putText(display, "RIGHT", (w - 70, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 100, 100), 1)

        # Detection boxes (green)
        for det in detections:
            x1, y1, x2, y2 = [int(v) for v in det.bbox]
            cv2.rectangle(display, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(display, f"person {det.confidence:.2f}", (x1, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        # Tracked person highlight (cyan)
        if tracked and tracked.frames_lost == 0:
            x1, y1, x2, y2 = [int(v) for v in tracked.detection.bbox]
            cv2.rectangle(display, (x1, y1), (x2, y2), (255, 255, 0), 3)
            cx = int(tracked.detection.center_x)
            cy = int(tracked.detection.center_y)
            cv2.circle(display, (cx, cy), 5, (255, 255, 0), -1)

        # Status panel
        if state:
            y = h - 80
            fps_color = (0, 255, 0) if state.fps >= 10 else (0, 0, 255)
            cv2.putText(display, f"FPS: {state.fps:.1f}", (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, fps_color, 2)

            if state.has_person:
                cv2.putText(display, "PERSON DETECTED", (10, y + 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                pos_text = f"POS: {state.position_zone.value}" if state.position_zone else "POS: --"
                dist_text = f"DIST: {state.distance_zone.value}" if state.distance_zone else "DIST: --"
                cv2.putText(display, f"{pos_text}  {dist_text}", (10, y + 50),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            else:
                cv2.putText(display, "NO PERSON", (10, y + 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        cv2.imshow(self.window_name, display)
        cv2.waitKey(1)

    def is_closed(self):
        if not self.enabled:
            return False
        try:
            return cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) < 1
        except Exception:
            return True

    def stop(self):
        if self.enabled:
            cv2.destroyAllWindows()
