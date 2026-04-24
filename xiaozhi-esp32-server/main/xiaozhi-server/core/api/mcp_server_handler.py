"""MCP Server endpoint — exposes ESP32 tools to OpenClaw via streamable-HTTP transport.

OpenClaw connects to POST /mcp and calls tools/list, tools/call etc.
Tool calls are forwarded to the ESP32 device via the existing MCP-over-WebSocket bridge.
"""

import json
from aiohttp import web
from config.logger import setup_logging
from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
from core.utils.util import sanitize_tool_name

TAG = __name__

MCP_PROTOCOL_VERSION = "2024-11-05"

# Server-side virtual tools (not on ESP32, handled directly by server)
VIRTUAL_TOOLS = {
    "stop_listening": {
        "name": "stop_listening",
        "description": (
            "End the voice session and close the device connection. "
            "Call this when the user says anything that indicates they want to stop talking, "
            "leave, or end the conversation — e.g. '退出', '闭嘴', '安静', '退下', '再见', "
            "'拜拜', '别说了', '行了行了', '不聊了', 'stop', 'shut up', 'goodbye', 'bye', "
            "or any similar farewell/exit expression."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goodbye_message": {
                    "type": "string",
                    "description": "A short farewell message to speak before disconnecting",
                }
            },
        },
    },
}


class McpServerHandler:
    def __init__(self, config: dict, connections: dict):
        self.config = config
        self.connections = connections
        self.logger = setup_logging()

    def _json_response(self, data: dict, status: int = 200) -> web.Response:
        return web.Response(
            text=json.dumps(data),
            content_type="application/json",
            status=status,
        )

    def _find_device(self, tool_name: str = None):
        """Return (conn, device_id) for a connected device with MCP ready.

        If tool_name is given, prefer the device that owns that tool.
        Otherwise return the first ready device.
        """
        for device_id, conn in self.connections.items():
            if not hasattr(conn, "mcp_client") or conn.mcp_client is None:
                continue
            if not conn.mcp_client.ready:
                continue
            if tool_name:
                sanitized = sanitize_tool_name(tool_name)
                if conn.mcp_client.has_tool(sanitized):
                    return conn, device_id
            else:
                return conn, device_id
        return None, ""

    # ── JSON-RPC method handlers ──

    def _handle_initialize(self, request_id) -> web.Response:
        return self._json_response({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "xiaozhi-server",
                    "version": "0.9.2",
                },
            },
        })

    def _handle_tools_list(self, request_id) -> web.Response:
        tools = []
        # Add server-side virtual tools
        for tool_def in VIRTUAL_TOOLS.values():
            tools.append(tool_def)
        # Add device MCP tools
        for device_id, conn in self.connections.items():
            if not hasattr(conn, "mcp_client") or conn.mcp_client is None:
                continue
            if not conn.mcp_client.ready:
                continue
            for sanitized_name, tool_data in conn.mcp_client.tools.items():
                original_name = conn.mcp_client.name_mapping.get(
                    sanitized_name, sanitized_name
                )
                tools.append({
                    "name": original_name,
                    "description": tool_data.get("description", ""),
                    "inputSchema": tool_data.get(
                        "inputSchema",
                        {"type": "object", "properties": {}},
                    ),
                })
        return self._json_response({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": tools},
        })

    async def _handle_tools_call(self, request_id, tool_name: str, arguments: dict) -> web.Response:
        # Handle server-side virtual tools first
        if tool_name in VIRTUAL_TOOLS:
            return await self._handle_virtual_tool(request_id, tool_name, arguments)

        conn, device_id = self._find_device(tool_name)
        if not conn:
            return self._json_response({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": "No device connected"}],
                    "isError": True,
                },
            })

        sanitized = sanitize_tool_name(tool_name)
        try:
            args_str = json.dumps(arguments) if arguments else "{}"
            result_text = await call_mcp_tool(
                conn, conn.mcp_client, sanitized, args_str
            )
            return self._json_response({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                },
            })
        except TimeoutError:
            return self._json_response({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": "Tool call timed out (30s)"}],
                    "isError": True,
                },
            })
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"MCP server tool call error: {e}")
            return self._json_response({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": str(e)}],
                    "isError": True,
                },
            })

    async def _handle_virtual_tool(self, request_id, tool_name: str, arguments: dict) -> web.Response:
        """Handle server-side virtual tools (not forwarded to ESP32)."""
        if tool_name == "stop_listening":
            # Find any connected device
            conn, device_id = None, ""
            for did, c in self.connections.items():
                conn, device_id = c, did
                break
            if not conn:
                return self._json_response({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": "No device connected"}],
                        "isError": True,
                    },
                })
            self.logger.bind(tag=TAG).info(f"Virtual tool stop_listening called for device {device_id}")
            conn.is_exiting = True
            conn.close_after_chat = True
            try:
                await conn.close(conn.websocket)
            except Exception as e:
                self.logger.bind(tag=TAG).warning(f"Error closing connection via virtual tool: {e}")
            return self._json_response({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": "Session stopped"}],
                },
            })
        return self._json_response({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Unknown virtual tool: {tool_name}"},
        })

    # ── Route handlers ──

    async def handle_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                status=400,
            )

        method = body.get("method", "")
        request_id = body.get("id")

        # Notification — no response needed
        if method == "notifications/initialized":
            return web.Response(status=202)

        if method == "initialize":
            return self._handle_initialize(request_id)
        elif method == "tools/list":
            return self._handle_tools_list(request_id)
        elif method == "tools/call":
            params = body.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            return await self._handle_tools_call(request_id, tool_name, arguments)
        else:
            return self._json_response({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })

    async def handle_options(self, request: web.Request) -> web.Response:
        response = web.Response(body=b"", content_type="text/plain")
        response.headers["Access-Control-Allow-Headers"] = "content-type"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
