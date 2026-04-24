"""Main vision agent loop with event-driven person following."""

import asyncio
import time
import logging

from vision_agent.camera.camera_stream import CameraStream
from vision_agent.vision.detector import PersonDetector
from vision_agent.vision.tracker import PersonTracker, PositionZone, DistanceZone
from vision_agent.vision.state import VisionStateCache, PersonEventType
from vision_agent.tools.robot_tools import RobotController
from vision_agent.debug.overlay import DebugOverlay

logger = logging.getLogger("vision_agent")


class FpsCounter:
    def __init__(self, window=30):
        self._times = []
        self._window = window

    def update(self):
        now = time.time()
        self._times.append(now)
        self._times = self._times[-self._window:]
        if len(self._times) < 2:
            return 0.0
        elapsed = self._times[-1] - self._times[0]
        return (len(self._times) - 1) / elapsed if elapsed > 0 else 0.0


class VisionAgentLoop:
    def __init__(self, camera, detector, tracker, state_cache, robot, overlay):
        self.camera = camera
        self.detector = detector
        self.tracker = tracker
        self.state_cache = state_cache
        self.robot = robot
        self.overlay = overlay
        self._running = False
        self._last_react_time = 0

    async def run(self):
        self._running = True
        await self.robot.start()
        self.camera.start()
        self.detector.warmup()
        logger.info("Vision agent started — camera warmup complete")

        loop = asyncio.get_event_loop()
        fps_counter = FpsCounter()

        try:
            while self._running:
                if self.overlay.is_closed():
                    break

                frame = self.camera.read()
                if frame is None:
                    await asyncio.sleep(0.01)
                    continue

                detections = await loop.run_in_executor(
                    None, self.detector.detect_persons, frame
                )

                tracked = self.tracker.update(detections)
                fps = fps_counter.update()
                events = self.state_cache.update(tracked, fps)

                for event in events:
                    logger.info(f"Event: {event.type.value}")
                    await self._handle_event(event, tracked)

                # Periodic re-evaluation every 500ms
                now = time.monotonic()
                if now - self._last_react_time > 0.5 and self.robot.is_available:
                    await self._react_to_state(tracked)
                    self._last_react_time = now

                state = self.state_cache.current_state
                self.overlay.update(frame, detections, tracked, state)

                await asyncio.sleep(0)

        except KeyboardInterrupt:
            pass
        finally:
            logger.info("Shutting down vision agent...")
            await self.robot.stop()
            await self.robot.close()
            self.camera.stop()
            self.overlay.stop()

    async def _handle_event(self, event, tracked):
        if not self.robot.is_available:
            return

        if event.type == PersonEventType.PERSON_LOST:
            await self.robot.stop()
            return

        if tracked is None:
            return

        await self._react_to_state(tracked)
        self._last_react_time = time.monotonic()

    async def _react_to_state(self, tracked):
        if tracked is None or tracked.frames_lost > 0:
            return

        pos = tracked.position_zone
        dist = tracked.distance_zone
        offset = tracked.offset_ratio

        if pos == PositionZone.LEFT:
            speed = int(30 + 40 * abs(offset - 0.35) / 0.35)
            await self.robot.turn("left", min(speed, 70))
        elif pos == PositionZone.RIGHT:
            speed = int(30 + 40 * abs(offset - 0.65) / 0.35)
            await self.robot.turn("right", min(speed, 70))
        elif pos == PositionZone.CENTER:
            if dist == DistanceZone.FAR:
                await self.robot.move("forward", 50)
            elif dist == DistanceZone.MEDIUM:
                await self.robot.move("forward", 30)
            else:
                await self.robot.stop()

    def stop(self):
        self._running = False
