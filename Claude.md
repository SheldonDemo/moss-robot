# MOSS - AI 桌面机器人

基于 [小智](https://github.com/78/xiaozhi-esp32) 开源平台，运行在 Hiwonder ESP32-S3 开发板上。

---

## 项目结构

```
moss-robot/
├── firmware/                # 小智 ESP32 固件 (ESP-IDF v5.4)
│   └── main/boards/HiwonderExploit_S3/  # 硬件板级支持包
├── xiaozhi-esp32-server/    # 小智官方 Python 后端 (音频桥接)
│   └── main/xiaozhi-server/ # 服务器源码
└── Claude.md                # 本文件

~/.openclaw/                 # OpenClaw Agent OS (本地)
├── workspace/skills/moss-robot/  # MOSS 机器人技能
│   ├── SKILL.md             # 技能定义 (ESP32 所有能力)
│   └── scripts/call_tool.py # 工具调用脚本
└── openclaw.json            # OpenClaw 配置
```

---

## 硬件配置

- **MCU**: ESP32-S3 (16MB Flash, PSRAM OPI)
- **音频**: ES8311 编解码芯片 (I2C 地址 0x18), I2S 接口, XL9555 控制功放静音
- **屏幕**: ST7789 320x240 LCD (SPI), LVGL UI, 支持亮色/暗色双主题
- **摄像头**: GC2145/OV2640 (8-bit 并口, QVGA 320x240, XCLK 10MHz)
- **IO 扩展**: XL9555 (I2C 地址 0x20), 控制屏幕背光 + 功放使能
- **按键**: GPIO 0 (BOOT) - 短按对话, 长按重置 WiFi

---

## 系统架构

### 整体数据流

```text
┌─────────────────────────────────────────────────────────┐
│                     用户（语音交互）                       │
└───────────┬─────────────────────────────────┬───────────┘
            │ 按下BOOT键/唤醒词                  │ 语音回复
            ▼                                   │
┌───────────────────────────────────────────────┼─────────┐
│              ESP32-S3 固件 (C++)               │         │
│  ┌─────────┐  ┌──────────┐  ┌───────┐  ┌─────┴──────┐  │
│  │ 音频采集 │  │ Opus编码  │  │ LCD   │  │ Opus解码    │  │
│  │ (I2S)   │→ │ /解码     │  │ 显示  │  │ → I2S播放  │  │
│  └─────────┘  └─────┬────┘  └───────┘  └────────────┘  │
│                     │ WebSocket (Opus帧)                  │
│  ┌─────────┐  ┌─────┴────┐  ┌───────────────────────┐   │
│  │ 摄像头   │  │ MCP Server│  │ 设备控制               │   │
│  │ GC2145  │  │ (JSON-RPC)│  │ (音量/亮度/主题)       │   │
│  └─────────┘  └──────────┘  └───────────────────────┘   │
└─────────────────────┬───────────────────────────────────┘
                      │ ws://172.20.10.6:8000/xiaozhi/v1/
                      ▼
┌─────────────────────────────────────────────────────────┐
│          小智服务器 (Python, 音频桥接 + 工具代理)          │
│                                                         │
│  ┌──────────┐  ┌─────────┐                    ┌───────┐ │
│  │ SileroVAD│→ │ FunASR  │→ 文字 ─────────────→│EdgeTTS│ │
│  │ 语音检测  │  │ 语音识别 │                    │语音合成│ │
│  │ (本地)    │  │ (本地)   │                    │(免费) │ │
│  └──────────┘  └─────────┘                    └───┬───┘ │
│                                                   │     │
│  ┌──────────────────────────────────────────┐     │     │
│  │ HTTP 工具代理 (port 8003)                 │     │     │
│  │ POST /moss/tools/call                     │     │     │
│  │ → 查找设备连接 → MCP JSON-RPC → 返回结果   │     │     │
│  └──────────────────────────────────────────┘     │     │
└──────────┬──────────────────────────────────────┬┘     │
           │ HTTP chat/completions (streaming)     │       │
           │ http://127.0.0.1:18789/v1/            │       │
           ▼                                        ▼       │
┌─────────────────────────────────────────────────────────┐
│              OpenClaw Agent OS (本地, port 18789)         │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │ GLM-5 Agent (zai/glm-5)                         │    │
│  │ 多步推理 + 工具调用 + 记忆管理                     │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ moss-robot   │  │ WhatsApp     │  │ 其他技能      │  │
│  │ 技能 (Skill) │  │ 通道         │  │ (coding等)   │  │
│  │ → 工具代理   │  │ → 同一Agent  │  │              │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 核心业务流程

#### 1. 语音对话流程 (via OpenClaw)

```text
用户按下BOOT键
    → 固件开始录音 (I2S 16kHz → Opus编码)
    → Opus帧通过 WebSocket 发送到服务器
    → SileroVAD 检测语音端点（用户说完一句话）
    → FunASR 将语音转文字 (SenseVoiceSmall, 本地运行)
    → 服务器将文字发送到 OpenClaw chat/completions API
    → OpenClaw Agent 推理并流式生成回复 (GLM-5)
      - 如需调用工具 → OpenClaw 通过 MOSS 技能调用工具代理
      - 工具代理 → MCP → ESP32 执行 → 结果返回 OpenClaw
    → 流式文本分段送入 EdgeTTS 合成语音
    → TTS音频转 Opus 帧通过 WebSocket 发回固件
    → 固件 Opus解码 → I2S → 扬声器播放
```

**关键特性**:
- OpenClaw 作为 Agent OS，管理推理、工具调用和记忆
- xiaozhi-server 作为桥接层，只负责音频处理和 ESP32 工具代理
- 支持多通道访问 (语音/WhatsApp/Telegram 都可以控制机器人)
- 支持中断（用户说话时停止当前播放）

#### 2. 工具调用流程 (OpenClaw 技能)

```text
OpenClaw Agent 判断需要调用工具 (如 "帮我拍张照片")
    → OpenClaw 触发 moss-robot 技能
    → 技能调用 HTTP 工具代理: POST http://127.0.0.1:8003/moss/tools/call
      {"tool": "self_camera_take_photo", "arguments": {"question": "前面有什么"}}
    → 工具代理查找设备 WebSocket 连接
    → 通过 MCP JSON-RPC 发送到固件执行
    → 固件返回结果 → 工具代理返回给 OpenClaw
    → OpenClaw Agent 基于结果生成自然语言回复 → 流式返回
```

**超时**: 工具调用 30 秒超时，防止设备无响应时卡死。

#### 3. 多通道控制

```text
WhatsApp/Telegram 用户: "MOSS，把音量调到50"
    → OpenClaw 接收消息
    → 触发 moss-robot 技能
    → 调用 HTTP 工具代理 → ESP32 执行音量设置
    → 结果返回 OpenClaw → 回复用户
```

已注册的 MCP 工具 (通过工具代理访问):
- `self.get_device_status` — 查询设备状态（音量、电量、网络等）
- `self.audio_speaker.set_volume` — 设置音量 (0-100)
- `self.screen.set_brightness` — 设置屏幕亮度 (0-100)
- `self.screen.set_theme` — 切换主题 (light/dark)
- `self.camera.take_photo` — 拍照 + 视觉分析

---

## 当前服务配置

### 小智服务器 (xiaozhi-server)

| 模块 | 类型 | 说明 |
|------|------|------|
| VAD | SileroVAD | 本地语音活动检测 |
| ASR | FunASR (SenseVoiceSmall) | 本地语音转文字 |
| LLM | OpenClaw (openclaw/default) | 通过 OpenClaw API 代理到 GLM-5 |
| TTS | EdgeTTS | 微软免费语音合成 |
| Intent | function_call | 函数调用意图识别 (OpenClaw 模式下跳过) |

### OpenClaw Agent OS

| 配置项 | 值 |
|--------|-----|
| 端口 | 18789 |
| 模型 | zai/glm-5 (主), ollama/qwen2.5:7b (备用) |
| 技能目录 | `~/.openclaw/workspace/skills/` |
| 通道 | WhatsApp (已启用) |
| MOSS 技能 | `moss-robot/SKILL.md` — ESP32 所有能力的封装 |

### 端口映射

| 端口 | 服务 | 说明 |
|------|------|------|
| 8000 | xiaozhi-server WebSocket | ESP32 音频/控制连接 |
| 8003 | xiaozhi-server HTTP | OTA + 工具代理 (`/moss/tools/call`) |
| 18789 | OpenClaw Gateway | Agent OS API |

---

## 当前状态

### 已完成

- [x] ESP-IDF 环境搭建 (v5.4.1)
- [x] 固件编译烧录 (开发板: HiwonderExploit_S3)
- [x] WiFi 自动连接
- [x] 小智服务器部署（本地 ASR/TTS）
- [x] 端到端语音对话: BOOT键 → STT → LLM → TTS → 播放
- [x] LCD 显示表情和聊天气泡
- [x] MCP 框架: 音量、亮度、主题切换、设备状态查询
- [x] 摄像头驱动 + 质量感知拍照（8帧预热 + 亮度/对比度检测 + 自动重试）
- [x] 摄像头拍照时 LCD 实时预览
- [x] VLLM 视觉分析集成 (glm-4.6v)
- [x] Z.ai API 对接 (glm-5.1 + glm-4.6v)
- [x] 唤醒词引擎可用 (AFE 唤醒词, 默认关闭)
- [x] 项目仓库: https://github.com/SheldonDemo/moss-robot
- [x] OpenClaw Agent OS 集成 (本地 GLM-5 agent)

### 已知问题

- BOOT 键长按会意外触发 WiFi 重置（需要加防抖）
- 服务器需要手动启动 (`python app.py`)
- 摄像头拍照端到端流程尚未完整测试（固件已更新，服务器需重启加载最新配置）

---

## 开发路线图

### 第一阶段：OpenClaw 集成（当前）

- [x] **OpenClaw 本地部署** - Gateway 运行在 port 18789, GLM-5 agent
- [ ] **HTTP 工具代理** - xiaozhi-server 添加 /moss/tools/call 端点
- [ ] **MOSS 技能注册** - SKILL.md 封装所有 ESP32 能力到 OpenClaw
- [ ] **LLM 切换到 OpenClaw** - xiaozhi-server 通过 OpenClaw API 做推理
- [ ] **端到端验证** - 语音→OpenClaw→工具调用→TTS 全流程

### 第二阶段：稳定性打磨

- [ ] **唤醒词激活** - 启用 AFE 唤醒词，用户说"Hi 乐乐"即可开始对话
- [ ] **BOOT键防抖** - 修复长按误触发WiFi重置问题
- [ ] **服务器自启动** - Mac 开机自动启动小智服务器 (launchd)
- [ ] **配置整理** - 统一管理服务器配置文档
- [ ] **提示词调优** - 优化 MOSS 人设，更自然的中文口语

### 第三阶段：视觉与交互

- [ ] **端到端拍照测试** - 验证完整的拍照→上传→VLLM分析→回复流程
- [ ] **主动拍照** - OpenClaw Agent 主动调用摄像头获取环境上下文
- [ ] **显示动画** - 空闲/思考/说话时的 LCD 动画效果

### 第四阶段：多通道与运动控制

- [ ] **WhatsApp 控制** - 通过 OpenClaw WhatsApp 通道控制机器人
- [ ] **电机/舵机控制** - 添加 MCP 工具控制运动
- [ ] **语音控制运动** - "向前走"、"转左" → 直接电机指令
- [ ] **LLM 导航** - 复杂指令如"去桌子那边" → Agent 规划运动序列

### 第五阶段：智能化与个性化

- [ ] **长期记忆** - 利用 OpenClaw 记忆系统持久化对话上下文
- [ ] **意图识别** - 区分指令和闲聊
- [ ] **自定义唤醒词** - "MOSS" 替代默认的 "Hi 乐乐"
- [ ] **多语言** - 中英文自然切换
- [ ] **智能家居** - 通过 MCP 桥接控制智能设备

---

## 快速开始

### 编译烧录固件

```bash
. ~/esp/esp-idf/export.sh
cd firmware && idf.py build
idf.py -p /dev/cu.usbmodem1101 flash monitor
```

### 启动服务器

```bash
cd xiaozhi-esp32-server/main/xiaozhi-server
source venv/bin/activate
python app.py
```

### 关键配置文件

| 文件 | 用途 |
|------|------|
| `firmware/sdkconfig` | 固件配置 (WiFi, WS地址) - gitignored |
| `firmware/main/Kconfig.projbuild` | 配置项定义和默认值 |
| `firmware/main/boards/HiwonderExploit_S3/config.h` | 硬件引脚定义 |
| `xiaozhi-esp32-server/.../data/.config.yaml` | 服务器配置 (LLM, ASR, TTS, 提示词) - gitignored |
| `xiaozhi-esp32-server/.../config.yaml` | 服务器默认配置参考 |

---

## 设计原则

1. **低延迟** — 用户感知响应时间 < 1秒（流式LLM + 实时TTS）
2. **本地优先** — ASR 和 TTS 本地运行；LLM/VLLM 走云端 API
3. **语音优先** — 一切从语音出发，屏幕是辅助
4. **MCP 可扩展** — 新设备能力通过 MCP 工具添加，无需重写固件
5. **质量感知** — 摄像头自动检测图像质量，确保 VLLM 分析的是合格照片
