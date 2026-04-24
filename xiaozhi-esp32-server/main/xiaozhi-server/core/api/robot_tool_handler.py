import json
import math
from aiohttp import web
from config.logger import setup_logging
from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

TAG = __name__


class RobotToolHandler:
    """Robot Tool API — 原子化、无状态、统一返回格式。"""

    def __init__(self, config: dict, connections: dict):
        self.config = config
        self.connections = connections
        self.logger = setup_logging()

    def _find_device(self, request: web.Request):
        """按 Device-Id header 查设备，找不到取第一个。"""
        device_id = request.headers.get("Device-Id", "")
        if device_id:
            conn = self.connections.get(device_id)
            if conn:
                return conn, device_id
        # fallback: 取第一个在线设备
        if self.connections:
            device_id = next(iter(self.connections))
            return self.connections[device_id], device_id
        return None, ""

    def _ok(self, data=None) -> web.Response:
        return web.Response(
            text=json.dumps({"success": True, "data": data}),
            content_type="application/json",
        )

    def _fail(self, error: str, status: int = 400) -> web.Response:
        return web.Response(
            text=json.dumps({"success": False, "error": error}),
            content_type="application/json",
            status=status,
        )

    async def _call_tool(self, tool_name: str, args: dict = None):
        """直接调 ESP32 MCP tool（不经 HTTP 代理）。"""
        # 在 route 里调 _find_device 拿 conn，这里只做 MCP 调用
        raise NotImplementedError("use _call_with_request instead")

    async def _call_with_request(self, request: web.Request, tool_name: str, args: dict = None):
        """从 request 找设备 + 调 MCP tool。"""
        conn, device_id = self._find_device(request)
        if not conn:
            return None, "设备不在线", 503
        if not hasattr(conn, "mcp_client") or conn.mcp_client is None:
            return None, "设备 MCP 未初始化", 503
        try:
            args_str = json.dumps(args) if args else "{}"
            result = await call_mcp_tool(conn, conn.mcp_client, tool_name, args_str)
            return result, None, 0
        except TimeoutError:
            return None, "工具调用超时 (30s)", 504
        except ValueError as e:
            return None, str(e), 400
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Robot tool error: {e}")
            return None, str(e), 500

    # ── Movement (mock) ──

    async def handle_move(self, request: web.Request):
        try:
            body = await request.json()
        except Exception:
            return self._fail("Invalid JSON")
        direction = body.get("direction", "")
        if direction not in ("forward", "backward"):
            return self._fail("direction must be 'forward' or 'backward'")
        distance = body.get("distance_cm", 10)
        speed = body.get("speed", 50)
        return self._ok({
            "action": "move",
            "direction": direction,
            "distance_cm": distance,
            "speed": speed,
            "mock": True,
        })

    async def handle_turn(self, request: web.Request):
        try:
            body = await request.json()
        except Exception:
            return self._fail("Invalid JSON")
        direction = body.get("direction", "")
        if direction not in ("left", "right"):
            return self._fail("direction must be 'left' or 'right'")
        angle = body.get("angle_degrees", 90)
        speed = body.get("speed", 50)
        return self._ok({
            "action": "turn",
            "direction": direction,
            "angle_degrees": angle,
            "speed": speed,
            "mock": True,
        })

    async def handle_stop(self, request: web.Request):
        return self._ok({"action": "stop", "mock": True})

    # ── Camera ──

    async def handle_camera_capture(self, request: web.Request):
        result, error, status = await self._call_with_request(
            request, "self.camera.take_photo"
        )
        if error:
            return self._fail(error, status)
        return self._ok({"action": "capture", "image": result})

    # ── Status ──

    async def handle_status(self, request: web.Request):
        result, error, status = await self._call_with_request(
            request, "self.get_device_status"
        )
        if error:
            return self._fail(error, status)
        return self._ok(result)

    async def handle_health(self, request: web.Request):
        return self._ok({"status": "ok"})

    # ── Session control (for OpenClaw) ──

    async def handle_session_stop(self, request: web.Request):
        """Close the device's WebSocket connection, stopping the listening session."""
        conn, device_id = self._find_device(request)
        if not conn:
            return self._fail("设备不在线", 503)
        self.logger.bind(tag=TAG).info(f"OpenClaw requested session stop for device {device_id}")
        conn.is_exiting = True
        conn.close_after_chat = True
        try:
            await conn.close(conn.websocket)
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"Error closing connection: {e}")
        return self._ok({"action": "session_stop", "device_id": device_id})

    # ── CORS ──

    async def handle_options(self, request: web.Request):
        response = web.Response(body=b"", content_type="text/plain")
        response.headers["Access-Control-Allow-Headers"] = "content-type, device-id"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
