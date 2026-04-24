"""Async robot control via the xiaozhi-server tool proxy."""

import time
import aiohttp


class RobotController:
    def __init__(self, proxy_url="http://127.0.0.1:8003/moss/tools/call",
                 device_id="", auth_token="", min_interval_ms=250):
        self.proxy_url = proxy_url
        self.device_id = device_id
        self.auth_token = auth_token
        self.min_interval = min_interval_ms / 1000.0
        self._last_cmd_time = 0.0
        self._last_cmd_type = None
        self._session = None
        self._consecutive_failures = 0

    async def start(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        )

    async def _call_tool(self, tool_name, arguments):
        now = time.monotonic()
        if now - self._last_cmd_time < self.min_interval:
            return {"success": True, "throttled": True}

        cmd_type = tool_name.split(".")[-1]
        if cmd_type != self._last_cmd_type and self._last_cmd_type is not None:
            if cmd_type != "stop" and self._last_cmd_type != "stop":
                # Direction change: stop first
                await self._send("self.forklift.stop", {})
                await _sleep_short()

        payload = {"tool": tool_name, "arguments": arguments}
        headers = {"Content-Type": "application/json"}
        if self.device_id:
            headers["Device-Id"] = self.device_id
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            async with self._session.post(self.proxy_url, json=payload, headers=headers) as resp:
                result = await resp.json()
                self._last_cmd_time = time.monotonic()
                self._last_cmd_type = cmd_type
                self._consecutive_failures = 0
                return result
        except Exception as e:
            self._consecutive_failures += 1
            return {"success": False, "error": str(e)}

    async def move(self, direction, speed=50):
        return await self._call_tool("self.forklift.move", {"direction": direction, "speed": speed})

    async def turn(self, direction, speed=50):
        return await self._call_tool("self.forklift.turn", {"direction": direction, "speed": speed})

    async def stop(self):
        return await self._call_tool("self.forklift.stop", {})

    @property
    def is_available(self):
        return self._consecutive_failures < 5

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None


async def _sleep_short():
    import asyncio
    await asyncio.sleep(0.05)
