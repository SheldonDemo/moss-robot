import json
from aiohttp import web
from config.logger import setup_logging
from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

TAG = __name__


class ToolProxyHandler:
    """HTTP tool proxy - allows OpenClaw to call ESP32 MCP tools via HTTP."""

    def __init__(self, config: dict, connections: dict):
        self.config = config
        self.connections = connections
        self.logger = setup_logging()
        self._auth_token = config.get("openclaw", {}).get("tool_proxy_token", "")

    async def handle_post(self, request: web.Request):
        try:
            body = await request.json()
        except Exception:
            return web.Response(
                text=json.dumps({"success": False, "error": "Invalid JSON"}),
                content_type="application/json",
                status=400,
            )

        tool_name = body.get("tool", "")
        arguments = body.get("arguments", {})
        device_id = body.get("device_id") or request.headers.get("Device-Id", "")

        if not tool_name:
            return web.Response(
                text=json.dumps({"success": False, "error": "Missing 'tool' field"}),
                content_type="application/json",
                status=400,
            )

        if not device_id:
            return web.Response(
                text=json.dumps({"success": False, "error": "Missing device_id (header or body)"}),
                content_type="application/json",
                status=400,
            )

        if self._auth_token:
            auth = request.headers.get("Authorization", "")
            token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else auth
            if token != self._auth_token:
                return web.Response(
                    text=json.dumps({"success": False, "error": "Unauthorized"}),
                    content_type="application/json",
                    status=401,
                )

        conn = self.connections.get(device_id)
        if not conn:
            available = list(self.connections.keys())
            return web.Response(
                text=json.dumps({
                    "success": False,
                    "error": f"Device {device_id} not connected",
                    "available_devices": available,
                }),
                content_type="application/json",
                status=404,
            )

        if not hasattr(conn, "mcp_client") or conn.mcp_client is None:
            return web.Response(
                text=json.dumps({"success": False, "error": "Device MCP not initialized"}),
                content_type="application/json",
                status=503,
            )

        try:
            args_str = json.dumps(arguments) if isinstance(arguments, dict) else str(arguments)
            result = await call_mcp_tool(
                conn, conn.mcp_client, tool_name, args_str
            )
            return web.Response(
                text=json.dumps({"success": True, "result": result}),
                content_type="application/json",
            )
        except TimeoutError:
            return web.Response(
                text=json.dumps({"success": False, "error": "Tool call timed out (30s)"}),
                content_type="application/json",
                status=504,
            )
        except ValueError as e:
            return web.Response(
                text=json.dumps({"success": False, "error": str(e)}),
                content_type="application/json",
                status=400,
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Tool proxy error: {e}")
            return web.Response(
                text=json.dumps({"success": False, "error": str(e)}),
                content_type="application/json",
                status=500,
            )

    async def handle_get(self, request: web.Request):
        return web.Response(
            text=json.dumps({
                "status": "ok",
                "connected_devices": list(self.connections.keys()),
            }),
            content_type="application/json",
        )

    async def handle_options(self, request: web.Request):
        response = web.Response(body=b"", content_type="text/plain")
        response.headers["Access-Control-Allow-Headers"] = "content-type, device-id, authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
