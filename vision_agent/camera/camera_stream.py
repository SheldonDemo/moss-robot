"""Threaded camera capture supporting both local cameras and MJPEG streams."""

import threading
import time
import cv2
import numpy as np


class CameraStream:
    def __init__(self, source=0, target_width=640, target_height=480):
        self._source = source
        self._target_width = target_width
        self._target_height = target_height
        self._cap = None
        self._frame = None
        self._running = False
        self._lock = threading.Lock()
        self._thread = None

    @property
    def width(self):
        return self._target_width

    @property
    def height(self):
        return self._target_height

    def start(self):
        self._cap = cv2.VideoCapture(self._source)
        if isinstance(self._source, int):
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._target_width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._target_height)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open camera source: {self._source}")

        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _capture_loop(self):
        while self._running:
            ret, frame = self._cap.read()
            if ret:
                with self._lock:
                    self._frame = frame
            else:
                time.sleep(0.01)

    def read(self):
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap:
            self._cap.release()
            self._cap = None
