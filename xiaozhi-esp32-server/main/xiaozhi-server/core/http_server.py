import asyncio
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler
from core.api.tool_proxy_handler import ToolProxyHandler
from core.api.robot_tool_handler import RobotToolHandler
from core.api.mcp_server_handler import McpServerHandler

TAG = __name__


class SimpleHttpServer:
    def __init__(self, config: dict, connections: dict = None):
        self.config = config
        self.logger = setup_logging()
        self.connections = connections if connections is not None else {}
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)
        self.tool_proxy_handler = ToolProxyHandler(config, self.connections)
        self.robot_tool_handler = RobotToolHandler(config, self.connections)
        self.mcp_server_handler = McpServerHandler(config, self.connections)

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        """获取websocket地址

        Args:
            local_ip: 本地IP地址
            port: 端口号

        Returns:
            str: websocket地址
        """
        server_config = self.config["server"]
        websocket_config = server_config.get("websocket")

        if websocket_config and "你" not in websocket_config:
            return websocket_config
        else:
            return f"ws://{local_ip}:{port}/xiaozhi/v1/"

    async def start(self):
        try:
            server_config = self.config["server"]
            read_config_from_api = self.config.get("read_config_from_api", False)
            host = server_config.get("ip", "0.0.0.0")
            port = int(server_config.get("http_port", 8003))

            if port:
                app = web.Application()

                if not read_config_from_api:
                    # 如果没有开启智控台，只是单模块运行，就需要再添加简单OTA接口，用于下发websocket接口
                    app.add_routes(
                        [
                            web.get("/xiaozhi/ota/", self.ota_handler.handle_get),
                            web.post("/xiaozhi/ota/", self.ota_handler.handle_post),
                            web.options(
                                "/xiaozhi/ota/", self.ota_handler.handle_options
                            ),
                            # 下载接口，仅提供 data/bin/*.bin 下载
                            web.get(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_download,
                            ),
                            web.options(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_options,
                            ),
                        ]
                    )
                # 添加路由
                app.add_routes(
                    [
                        web.get("/mcp/vision/explain", self.vision_handler.handle_get),
                        web.post(
                            "/mcp/vision/explain", self.vision_handler.handle_post
                        ),
                        web.options(
                            "/mcp/vision/explain", self.vision_handler.handle_options
                        ),
                        # Photo viewing endpoints
                        web.get("/photos/latest", self.vision_handler.handle_photo_latest),
                        web.get("/photos/list", self.vision_handler.handle_photo_list),
                        # OpenClaw 工具代理
                        web.get("/moss/tools/call", self.tool_proxy_handler.handle_get),
                        web.post("/moss/tools/call", self.tool_proxy_handler.handle_post),
                        web.options("/moss/tools/call", self.tool_proxy_handler.handle_options),
                        # MCP Server (OpenClaw native tools)
                        web.post("/mcp", self.mcp_server_handler.handle_post),
                        web.options("/mcp", self.mcp_server_handler.handle_options),
                        # Robot Tool API
                        web.post("/robot/move", self.robot_tool_handler.handle_move),
                        web.post("/robot/turn", self.robot_tool_handler.handle_turn),
                        web.post("/robot/stop", self.robot_tool_handler.handle_stop),
                        web.post("/robot/camera/capture", self.robot_tool_handler.handle_camera_capture),
                        web.get("/robot/status", self.robot_tool_handler.handle_status),
                        web.get("/robot/health", self.robot_tool_handler.handle_health),
                        web.post("/moss/session/stop", self.robot_tool_handler.handle_session_stop),
                        web.options("/moss/session/stop", self.robot_tool_handler.handle_options),
                        web.options("/robot/move", self.robot_tool_handler.handle_options),
                        web.options("/robot/turn", self.robot_tool_handler.handle_options),
                        web.options("/robot/stop", self.robot_tool_handler.handle_options),
                        web.options("/robot/camera/capture", self.robot_tool_handler.handle_options),
                        web.options("/robot/status", self.robot_tool_handler.handle_options),
                    ]
                )

                # 运行服务
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()

                # 保持服务运行
                while True:
                    await asyncio.sleep(3600)  # 每隔 1 小时检查一次
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"HTTP服务器启动失败: {e}")
            import traceback

            self.logger.bind(tag=TAG).error(f"错误堆栈: {traceback.format_exc()}")
            raise
