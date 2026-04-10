# PhoneDriver API

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

基于 Python 的手机自动化工具，使用云端视觉-语言模型（Kimi、GPT-4V、Claude 等）通过视觉分析和 ADB 命令与 Android 设备进行交互。

**无需 GPU！** 本项目将原始的本地 Qwen3-VL 模型替换为基于 API 的视觉模型。

[English](./README.md) | 简体中文

## 🌟 特性

- ☁️ **云端视觉模型**：使用 Kimi K2.5、GPT-4V、Claude 3.5 Sonnet 或其他 VLM API
- 🤖 **ADB 集成**：通过 ADB 命令控制 Android 设备
- 📝 **自然语言任务**：用简单的英语或中文描述你想要完成的任务
- 🌐 **Web UI**：内置 Gradio 界面，易于控制
- 📱 **实时反馈**：实时截图和执行日志
- 🔌 **多提供商支持**：Kimi Code、OpenRouter、Moonshot、OpenAI 等

## 📋 系统要求

- Python 3.10+
- 开启 USB 调试和开发者模式的 Android 设备
- 已安装的 ADB (Android Debug Bridge)
- 来自支持提供商的 API 密钥 (Kimi Code、OpenAI、OpenRouter 等)

## 🚀 快速开始

### 1. 安装 ADB

**Windows：**
```bash
# 从 https://developer.android.com/studio/releases/platform-tools 下载
# 添加到 PATH
```

**Linux/Ubuntu：**
```bash
sudo apt update
sudo apt install adb
```

**macOS：**
```bash
brew install android-platform-tools
```

### 2. 克隆并安装

```bash
git clone https://github.com/Yesssssbabe/PhoneDriver-API.git
cd PhoneDriver-API

# 创建虚拟环境
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/macOS
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置 API 提供商

复制示例配置文件并编辑：

```bash
cp .env.example .env
```

编辑 `.env` 文件，选择你喜欢的提供商：

**选项 A: Kimi Code（推荐国内用户使用）**
```env
PROVIDER=kimi_code
KIMI_CODE_API_KEY=sk-kimi-xxxxx
```

**选项 B: OpenRouter（支持多种模型）**
```env
PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-xxxxx
MODEL=moonshotai/kimi-k2.5
```

**选项 C: OpenAI**
```env
PROVIDER=openai
OPENAI_API_KEY=sk-xxxxx
MODEL=gpt-4o
```

**选项 D: Moonshot AI**
```env
PROVIDER=moonshot
MOONSHOT_API_KEY=sk-xxxxx
MODEL=kimi-k2.5
```

### 4. 连接设备

在 Android 设备上启用 USB 调试：
1. 设置 → 关于手机 → 连续点击"版本号"7次
2. 设置 → 开发者选项 → 启用"USB 调试"
3. 通过 USB 连接并允许调试

验证连接：
```bash
adb devices
```

### 5. 运行

**命令行：**
```bash
python phone_agent.py "打开设置"
```

**Web 界面：**
```bash
python ui.py
# 访问 http://localhost:7860
```

## 📁 项目结构

```
PhoneDriver-API/
├── phone_agent.py          # 主 CLI 程序
├── ui.py                   # Gradio 网页界面
├── config.json             # 设备配置
├── .env                    # API 密钥（从 .env.example 创建）
├── requirements.txt        # Python 依赖
├── README.md              # 英文文档
├── README_CN.md           # 中文文档（本文档）
├── providers/             # API 提供商实现
│   ├── kimi_code.py       # Kimi Code API
│   ├── openrouter.py      # OpenRouter API
│   ├── openai_provider.py # OpenAI API
│   └── moonshot.py        # Moonshot AI API
└── utils/                 # 工具函数
    ├── adb.py             # ADB 封装
    └── screenshot.py      # 截图处理
```

## ⚙️ 配置说明

### 屏幕分辨率

代理会自动检测设备分辨率。如需验证：

```bash
adb shell wm size
```

### 支持的提供商

| 提供商 | 模型 | 视觉支持 | 备注 |
|--------|------|---------|------|
| Kimi Code | kimi-for-coding, kimi-k2.5 | ✅ | 最适合编程任务 |
| OpenRouter | moonshotai/kimi-k2.5, anthropic/claude-3.5-sonnet 等 | ✅ | 多种模型可选 |
| OpenAI | gpt-4o, gpt-4o-mini | ✅ | 稳定，成本较高 |
| Moonshot | kimi-k2.5, kimi-vl | ✅ | 官方 Moonshot API |

### 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `PROVIDER` | API 提供商 (`kimi_code`, `openrouter`, `openai`, `moonshot`) | 是 |
| `KIMI_CODE_API_KEY` | Kimi Code API 密钥 | 使用 Kimi Code 时 |
| `OPENROUTER_API_KEY` | OpenRouter API 密钥 | 使用 OpenRouter 时 |
| `OPENAI_API_KEY` | OpenAI API 密钥 | 使用 OpenAI 时 |
| `MOONSHOT_API_KEY` | Moonshot API 密钥 | 使用 Moonshot 时 |
| `MODEL` | 模型名称（提供商特定） | 可选 |

## 📝 使用示例

### 命令行

```bash
# 打开应用
python phone_agent.py "Open Chrome"

# 执行搜索
python phone_agent.py "Search for weather in New York"

# 更改设置
python phone_agent.py "Open Settings and enable WiFi"

# 拍照
python phone_agent.py "Open camera and take a photo"
```

### Python API

```python
from phone_agent import PhoneAgent

config = {
    "provider": "kimi_code",
    "api_key": "your-api-key",
    "screen_width": 1080,
    "screen_height": 2340
}

agent = PhoneAgent(config)
result = agent.execute_task("Open Settings")
print(result)
```

## 🔧 故障排除

### 设备未检测到

```bash
# 重启 ADB 服务器
adb kill-server
adb start-server
adb devices
```

### 点击位置错误

在 UI 的设置标签页中自动检测分辨率，或手动验证：
```bash
adb shell wm size
```

### API 错误

- 验证你的 API 密钥是否有效
- 检查你是否有足够的额度/积分
- 确保 `PROVIDER` 与 API 密钥类型匹配

### Windows 上的 Unicode 日志错误

如果看到 `UnicodeEncodeError`，以 UTF-8 模式运行 PowerShell：
```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
python phone_agent.py "your task"
```

## 👥 贡献者

<a href="https://github.com/Yesssssbabe">
  <img src="https://github.com/Yesssssbabe.png?size=100" width="50" height="50" style="border-radius:50%">
</a>

- **Yesssssbabe** - 创建者和维护者 ([@Yesssssbabe](https://github.com/Yesssssbabe))

## 💬 联系方式

有问题或建议？欢迎联系！

- **微信**：扫描下方二维码（**加好友备注：phonedriverapi**）
- **GitHub Issues**：[创建 Issue](https://github.com/Yesssssbabe/PhoneDriver-API/issues)

<img src="wechat_qr.jpg" width="300" alt="微信二维码">

> **注意：** 添加好友时请备注 `phonedriverapi`

## 🙏 致谢

### 项目贡献者

- **[@Yesssssbabe](https://github.com/Yesssssbabe)** - PhoneDriver-API 的创建者和维护者

### 原项目

- **[@OminousIndustries](https://github.com/OminousIndustries)** - 原版 [PhoneDriver](https://github.com/OminousIndustries/PhoneDriver) 作者

### API 提供商

- [Kimi](https://kimi.com) by Moonshot AI
- [OpenRouter](https://openrouter.ai) 提供统一 API 访问

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件。

## 🤝 如何贡献

欢迎贡献！请查看 [CONTRIBUTING.md](CONTRIBUTING.md) 了解详情。

## 💡 未来计划

- [ ] 支持更多提供商（Anthropic、Google Gemini 等）
- [ ] 批量任务处理
- [ ] 任务录制和回放
- [ ] iOS 支持（通过 WebDriverAgent）
- [ ] 多设备协调

---

⭐ **如果觉得这个项目有用，请给个 Star！**
