"""OpenRouter API provider."""
from __future__ import annotations

import os

from . import register_provider
from .base import OpenAICompatibleProvider


@register_provider("openrouter")
class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter API provider - provides access to multiple models."""

    default_model = "moonshotai/kimi-k2.5"

    def __init__(self, api_key: str, **kwargs):
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.environ.get(
                "OPENROUTER_REFERER", "https://localhost"
            ),
            "X-Title": os.environ.get("OPENROUTER_TITLE", "PhoneDriver API"),
        }
        super().__init__(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            headers=headers,
            **kwargs,
        )
