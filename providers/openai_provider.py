"""OpenAI API provider."""
from __future__ import annotations

from . import register_provider
from .base import OpenAICompatibleProvider


@register_provider("openai")
class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI API provider (GPT-4V, GPT-4o, etc.)."""

    default_model = "gpt-4o"

    def __init__(self, api_key: str, **kwargs):
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        super().__init__(
            api_key=api_key,
            base_url="https://api.openai.com/v1",
            headers=headers,
            **kwargs,
        )
