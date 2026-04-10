# PhoneDriver API

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A Python-based mobile automation agent that uses **Cloud Vision-Language APIs** (Kimi, GPT-4V, Claude, etc.) to understand and interact with Android devices through visual analysis and ADB commands.

**No GPU required!** This fork replaces the original local Qwen3-VL model with API-based vision models.

English | [简体中文](./README_CN.md)

## 🌟 Features

- ☁️ **Cloud Vision Models**: Use Kimi K2.5, GPT-4V, Claude 3.5 Sonnet, or other VLM APIs
- 🤖 **ADB Integration**: Controls Android devices via ADB commands
- 📝 **Natural language tasks**: Describe what you want in plain English
- 🌐 **Web UI**: Built-in Gradio interface for easy control
- 📱 **Real-time feedback**: Live screenshots and execution logs
- 🔌 **Multi-Provider Support**: Kimi Code, OpenRouter, Moonshot, OpenAI, and more

## 📋 Requirements

- Python 3.10+
- Android device with USB debugging & Developer Mode enabled
- ADB (Android Debug Bridge) installed
- API key from supported providers (Kimi Code, OpenAI, OpenRouter, etc.)

## 🚀 Quick Start

### 1. Install ADB

**Windows:**
```bash
# Download from https://developer.android.com/studio/releases/platform-tools
# Add to PATH
```

**Linux/Ubuntu:**
```bash
sudo apt update
sudo apt install adb
```

**macOS:**
```bash
brew install android-platform-tools
```

### 2. Clone & Install

```bash
git clone https://github.com/yourusername/PhoneDriver-API.git
cd PhoneDriver-API

# Create virtual environment
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/macOS
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure API Provider

Copy the example config and edit:

```bash
cp .env.example .env
```

Edit `.env` with your preferred provider:

**Option A: Kimi Code (Recommended for China)**
```env
PROVIDER=kimi_code
KIMI_CODE_API_KEY=sk-kimi-xxxxx
```

**Option B: OpenRouter (Supports multiple models)**
```env
PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-xxxxx
MODEL=moonshotai/kimi-k2.5
```

**Option C: OpenAI**
```env
PROVIDER=openai
OPENAI_API_KEY=sk-xxxxx
MODEL=gpt-4o
```

**Option D: Moonshot AI**
```env
PROVIDER=moonshot
MOONSHOT_API_KEY=sk-xxxxx
MODEL=kimi-k2.5
```

### 4. Connect Your Device

Enable USB debugging on your Android device:
1. Settings → About Phone → Tap "Build Number" 7 times
2. Settings → Developer Options → Enable "USB Debugging"
3. Connect via USB and allow debugging

Verify connection:
```bash
adb devices
```

### 5. Run

**Command Line:**
```bash
python phone_agent.py "Open Settings"
```

**Web UI:**
```bash
python ui.py
# Navigate to http://localhost:7860
```

## 📁 Project Structure

```
PhoneDriver-API/
├── phone_agent.py          # Main CLI agent
├── ui.py                   # Gradio web interface
├── config.json             # Device configuration
├── .env                    # API keys (create from .env.example)
├── requirements.txt        # Python dependencies
├── README.md              # This file
├── LICENSE                # MIT License
├── providers/             # API provider implementations
│   ├── __init__.py
│   ├── base.py            # Base provider interface
│   ├── kimi_code.py       # Kimi Code API
│   ├── openrouter.py      # OpenRouter API
│   ├── openai_provider.py # OpenAI API
│   └── moonshot.py        # Moonshot AI API
└── utils/                 # Utility functions
    ├── __init__.py
    ├── adb.py             # ADB wrapper
    └── screenshot.py      # Screenshot capture
```

## ⚙️ Configuration

### Screen Resolution

The agent auto-detects your device resolution. To verify:

```bash
adb shell wm size
```

### Supported Providers

| Provider | Model | Vision | Notes |
|----------|-------|--------|-------|
| Kimi Code | kimi-for-coding, kimi-k2.5 | ✅ | Best for coding tasks |
| OpenRouter | moonshotai/kimi-k2.5, anthropic/claude-3.5-sonnet, etc. | ✅ | Multiple models |
| OpenAI | gpt-4o, gpt-4o-mini | ✅ | Reliable, higher cost |
| Moonshot | kimi-k2.5, kimi-vl | ✅ | Official Moonshot API |

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `PROVIDER` | API provider (`kimi_code`, `openrouter`, `openai`, `moonshot`) | Yes |
| `KIMI_CODE_API_KEY` | Kimi Code API key | If using Kimi Code |
| `OPENROUTER_API_KEY` | OpenRouter API key | If using OpenRouter |
| `OPENAI_API_KEY` | OpenAI API key | If using OpenAI |
| `MOONSHOT_API_KEY` | Moonshot API key | If using Moonshot |
| `MODEL` | Model name (provider-specific) | Optional |

## 📝 Usage Examples

### Command Line

```bash
# Open an app
python phone_agent.py "Open Chrome"

# Perform a search
python phone_agent.py "Search for weather in New York"

# Change settings
python phone_agent.py "Open Settings and enable WiFi"

# Take a photo
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

## 🔧 Troubleshooting

### Device not detected

```bash
# Restart ADB server
adb kill-server
adb start-server
adb devices
```

### Wrong tap locations

Auto-detect resolution in UI Settings tab, or manually verify:
```bash
adb shell wm size
```

### API errors

- Verify your API key is valid
- Check if you have sufficient quota/credits
- Ensure `PROVIDER` matches the API key type

### Unicode logging errors on Windows

If you see `UnicodeEncodeError` in logs, run PowerShell as UTF-8:
```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
python phone_agent.py "your task"
```

## 👥 Contributors

<a href="https://github.com/Yesssssbabe">
  <img src="https://github.com/Yesssssbabe.png?size=100" width="50" height="50" style="border-radius:50%">
</a>

- **Yesssssbabe** - Creator & Maintainer ([@Yesssssbabe](https://github.com/Yesssssbabe))

## 💬 Contact

Have questions or suggestions? Feel free to reach out!

- **WeChat**: Scan the QR code below (add note: **phonedriverapi**)
- **GitHub Issues**: [Create an issue](https://github.com/Yesssssbabe/PhoneDriver-API/issues)

<img src="wechat_qr.jpg" width="300" alt="WeChat QR Code">

> **Note:** Please add `phonedriverapi` when sending friend request.

## 🙏 Acknowledgments

### Project Contributors

- **[@Yesssssbabe](https://github.com/Yesssssbabe)** - Creator & Maintainer of PhoneDriver-API

### Original Project

- **[@OminousIndustries](https://github.com/OminousIndustries)** - Original [PhoneDriver](https://github.com/OminousIndustries/PhoneDriver) author

### API Providers

- [Kimi](https://kimi.com) by Moonshot AI
- [OpenRouter](https://openrouter.ai) for unified API access

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 💡 Future Improvements

- [ ] Support for more providers (Anthropic, Google Gemini, etc.)
- [ ] Batch task processing
- [ ] Task recording and replay
- [ ] iOS support (via WebDriverAgent)
- [ ] Multi-device coordination

---

⭐ **Star this repo if you find it useful!**
