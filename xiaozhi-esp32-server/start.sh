#!/bin/bash
# MOSS 一键启动小智服务器

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/main/xiaozhi-server"
VENV_DIR="$SERVER_DIR/venv"

# 检查是否已在运行
if lsof -i :8000 >/dev/null 2>&1; then
    echo "小智服务器已在运行 (端口 8000)"
    exit 0
fi

# 检查 OpenClaw Agent OS
if ! curl -sf http://127.0.0.1:18789/ >/dev/null 2>&1; then
    echo "⚠ OpenClaw 未运行 (端口 18789)，语音对话将无法正常工作"
    echo "  请先启动 OpenClaw: openclaw start"
    exit 1
fi
echo "✓ OpenClaw Agent OS 在线"

# 激活 venv 并启动
cd "$SERVER_DIR"
source "$VENV_DIR/bin/activate"
echo "启动小智服务器..."
python app.py
