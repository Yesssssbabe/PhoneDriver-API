# Contributing to PhoneDriver API

Thank you for your interest in contributing to PhoneDriver API! This document provides guidelines for contributing to the project.

## How to Contribute

### Reporting Bugs

If you find a bug, please create an issue with:
- A clear description of the problem
- Steps to reproduce
- Expected vs actual behavior
- Your environment (OS, Python version, provider used)

### Suggesting Features

Feature suggestions are welcome! Please:
- Check if the feature has already been suggested
- Provide a clear use case
- Explain why it would be useful

### Pull Requests

1. Fork the repository
2. Create a new branch (`git checkout -b feature/your-feature`)
3. Make your changes
4. Add tests if applicable
5. Update documentation
6. Commit with clear messages
7. Push to your fork
8. Create a Pull Request

## Project Structure

```
PhoneDriver-API/
├── providers/        # API provider implementations
│   ├── base.py       # Base provider class
│   ├── kimi_code.py  # Kimi Code provider
│   ├── openrouter.py # OpenRouter provider
│   ├── openai_provider.py  # OpenAI provider
│   └── moonshot.py   # Moonshot provider
├── utils/            # Utility functions
│   ├── adb.py        # ADB wrapper
│   └── screenshot.py # Screenshot handling
├── phone_agent.py    # Main CLI
├── ui.py             # Gradio web UI
└── config.json       # Configuration
```

## Adding a New Provider

To add support for a new API provider:

1. Create a new file in `providers/` directory
2. Inherit from `BaseProvider`
3. Implement required methods:
   - `analyze_screenshot()`
   - `check_task_completion()`
4. Add to `PROVIDER_MAP` in `providers/__init__.py`
5. Update `.env.example` with new provider config
6. Update README.md with documentation

Example:

```python
from .base import BaseProvider

class MyProvider(BaseProvider):
    default_model = "my-model"
    
    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)
        self.api_url = "https://api.example.com/v1"
        self.headers = {"Authorization": f"Bearer {api_key}"}
    
    def analyze_screenshot(self, screenshot_path, user_request, context=None):
        # Implementation
        pass
    
    def check_task_completion(self, screenshot_path, user_request, context):
        # Implementation
        pass
```

## Code Style

- Follow PEP 8
- Use type hints where appropriate
- Add docstrings to functions and classes
- Keep functions focused and small

## Testing

Before submitting a PR:
- Test your changes with different providers
- Ensure ADB commands work correctly
- Check that the UI still functions

## Commit Messages

Use clear commit messages:
- `feat: Add support for XYZ provider`
- `fix: Fix swipe direction calculation`
- `docs: Update README with new examples`
- `refactor: Simplify screenshot capture`

## Questions?

Feel free to open an issue for any questions or discussion!

## Code of Conduct

Be respectful and constructive in all interactions.
