"""MOSS Vision Agent — local real-time person following demo."""

import argparse
import asyncio
import logging
import os
import signal
import sys

# Add vision_agent parent to path so imports work as vision_agent.camera etc.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision_agent.camera.camera_stream import CameraStream
from vision_agent.vision.detector import PersonDetector
from vision_agent.vision.tracker import PersonTracker
from vision_agent.vision.state import VisionStateCache
from vision_agent.agent.vision_loop import VisionAgentLoop
from vision_agent.tools.robot_tools import RobotController
from vision_agent.debug.overlay import DebugOverlay

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def main():
    parser = argparse.ArgumentParser(description="MOSS Vision Agent")
    parser.add_argument("--camera", type=int, default=None, help="Local camera index (e.g., 0)")
    parser.add_argument("--stream", default=None, help="MJPEG stream URL (e.g., http://esp32:81/stream)")
    parser.add_argument("--no-debug", action="store_true", help="Disable debug overlay")
    parser.add_argument("--no-robot", action="store_true", help="Disable robot control (detection only)")
    parser.add_argument("--proxy-url", default=os.environ.get("MOSS_TOOL_PROXY_URL", "http://127.0.0.1:8003/moss/tools/call"))
    parser.add_argument("--device-id", default=os.environ.get("MOSS_DEVICE_ID", ""))
    parser.add_argument("--auth-token", default=os.environ.get("MOSS_PROXY_TOKEN", ""))
    parser.add_argument("--conf", type=float, default=0.5, help="Detection confidence threshold")
    args = parser.parse_args()

    # Determine video source
    if args.stream:
        source = args.stream
        logger.info(f"Using MJPEG stream: {source}")
    elif args.camera is not None:
        source = args.camera
        logger.info(f"Using local camera {source}")
    else:
        source = 0
        logger.info("No source specified, using local camera 0")

    camera = CameraStream(source=source)
    detector = PersonDetector(conf_threshold=args.conf)
    tracker = PersonTracker(frame_width=camera.width, frame_height=camera.height)
    state_cache = VisionStateCache()
    overlay = DebugOverlay(enabled=not args.no_debug)

    if args.no_robot:
        robot = RobotController(proxy_url="http://127.0.0.1:0")
        robot._consecutive_failures = 99
        logger.info("Robot control disabled — detection only mode")
    else:
        robot = RobotController(
            proxy_url=args.proxy_url,
            device_id=args.device_id,
            auth_token=args.auth_token,
        )
        logger.info(f"Robot control via {args.proxy_url}")

    agent = VisionAgentLoop(camera, detector, tracker, state_cache, robot, overlay)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown():
        agent.stop()

    loop.add_signal_handler(signal.SIGINT, shutdown)

    try:
        loop.run_until_complete(agent.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
