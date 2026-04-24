"""YOLOv8n person detection wrapper."""

from dataclasses import dataclass
import numpy as np
from ultralytics import YOLO


@dataclass
class Detection:
    class_id: int
    class_name: str
    confidence: float
    bbox: tuple  # (x1, y1, x2, y2)
    center_x: float
    center_y: float
    width: float
    height: float


class PersonDetector:
    def __init__(self, model_name="yolov8n.pt", conf_threshold=0.5):
        self.model = YOLO(model_name)
        self.conf_threshold = conf_threshold

    def warmup(self):
        dummy = np.zeros((320, 320, 3), dtype=np.uint8)
        self.model.predict(dummy, verbose=False, classes=[0])

    def detect_persons(self, frame):
        results = self.model.predict(
            frame, verbose=False, conf=self.conf_threshold, classes=[0]
        )
        detections = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append(Detection(
                    class_id=0,
                    class_name="person",
                    confidence=float(box.conf[0]),
                    bbox=(x1, y1, x2, y2),
                    center_x=(x1 + x2) / 2,
                    center_y=(y1 + y2) / 2,
                    width=x2 - x1,
                    height=y2 - y1,
                ))
        detections.sort(key=lambda d: d.confidence, reverse=True)
        return detections
