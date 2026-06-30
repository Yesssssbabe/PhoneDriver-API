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
├── providers/             # API provider implementations
│   ├── base.py            # Base provider class
│   ├── kimi_code.py       # Kimi Code provider
│   ├── openrouter.py      # OpenRouter provider
│   ├── openai_provider.py # OpenAI provider
│   └── moonshot.py        # Moonshot provider
├── utils/                 # Utility functions
│   ├── adb.py             # ADB wrapper
│   └── screenshot.py      # Screenshot handling
├── phone_agent.py         # Main CLI
├── ui.py                  # Gradio web UI
├── config.example.json    # Example configuration
└── config.json            # User configuration
```

## Adding a New Provider

To add support for a new API provider:

1. Create a new file in `providers/` directory
2. Inherit from `OpenAICompatibleProvider` (or `BaseProvider` for custom protocols)
3. Add the `@register_provider("your_name")` decorator to the class
4. Implement or override required methods if inheriting from `BaseProvider`:
   - `analyze_screenshot()`
   - `check_task_completion()`
5. Update `.env.example` with new provider config
6. Update README.md with documentation

The `providers/__init__.py` auto-discovers provider modules at import time, so
**no manual edits to `PROVIDER_MAP` are required**.

Example:

```python
from . import register_provider
from .base import OpenAICompatibleProvider

@register_provider("my_provider")
class MyProvider(OpenAICompatibleProvider):
    default_model = "my-model"

    def __init__(self, api_key: str, **kwargs):
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        super().__init__(
            api_key=api_key,
            base_url="https://api.example.com/v1",
            headers=headers,
            **kwargs,
        )
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
