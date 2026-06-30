"""Moonshot AI API provider."""
from __future__ import annotations

from . import register_provider
from .base import OpenAICompatibleProvider


@register_provider("moonshot")
class MoonshotProvider(OpenAICompatibleProvider):
    """Moonshot AI API provider (Official)."""

    default_model = "kimi-k2.5"

    def __init__(self, api_key: str, **kwargs):
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        super().__init__(
            api_key=api_key,
            base_url="https://api.moonshot.cn/v1",
            headers=headers,
            **kwargs,
        )
