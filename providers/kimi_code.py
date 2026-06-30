"""Kimi Code API provider."""
from __future__ import annotations

from . import register_provider
from .base import OpenAICompatibleProvider


@register_provider("kimi_code")
class KimiCodeProvider(OpenAICompatibleProvider):
    """Kimi Code API provider for vision-language tasks."""

    default_model = "kimi-for-coding"

    def __init__(self, api_key: str, **kwargs):
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Kilo-Code/1.0",
        }
        super().__init__(
            api_key=api_key,
            base_url="https://api.kimi.com/coding/v1",
            headers=headers,
            **kwargs,
        )
