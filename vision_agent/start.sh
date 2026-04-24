#!/bin/bash
# MOSS Vision Agent 启动脚本

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 默认使用 ESP32 MJPEG 视频流
STREAM_URL="${MOSS_STREAM_URL:-http://172.20.10.4:81/stream}"
PROXY_URL="${MOSS_TOOL_PROXY_URL:-http://127.0.0.1:8003/moss/tools/call}"

cd "$PROJECT_DIR"
python -m vision_agent.main --stream "$STREAM_URL" --proxy-url "$PROXY_URL" "$@"
