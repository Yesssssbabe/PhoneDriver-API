"""OpenRouter API provider."""
from __future__ import annotations

import os
import re

from . import register_provider
from .base import OpenAICompatibleProvider


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _strip_control(value: str) -> str:
    return _CONTROL_CHAR_RE.sub("", value)


@register_provider("openrouter")
class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter API provider - provides access to multiple models."""

    default_model = "moonshotai/kimi-k2.5"

    def __init__(self, api_key: str, **kwargs):
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _strip_control(os.environ.get(
                "OPENROUTER_REFERER", "https://localhost"
            )),
            "X-Title": _strip_control(os.environ.get("OPENROUTER_TITLE", "PhoneDriver API")),
        }
        super().__init__(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            headers=headers,
            **kwargs,
        )
