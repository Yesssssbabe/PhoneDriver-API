"""OpenRouter API provider."""

from .base import BaseProvider


class OpenRouterProvider(BaseProvider):
    """OpenRouter API provider - provides access to multiple models."""

    default_model = "moonshotai/kimi-k2.5"

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)
        self.api_url = "https://openrouter.ai/api/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/yourusername/PhoneDriver-API",
            "X-Title": "PhoneDriver API"
        }

    def _get_system_prompt(self) -> str:
        """Get system prompt for mobile automation."""
        return """You are a mobile phone automation assistant. Analyze screenshots and decide actions.

Available actions:
- click: Tap at coordinates (x, y)
- swipe: Swipe from (x, y) to (x2, y2)
- type: Type text
- wait: Wait for UI
- terminate: Task done

Coordinates: 0-999 range, (0,0)=top-left, (999,999)=bottom-right.

Respond in JSON:
{
    "thought": "what you see and plan to do",
    "action": "click|swipe|type|wait|terminate",
    "coordinate": [x, y],
    "coordinate2": [x2, y2],
    "text": "text to type",
    "status": "success|failure"
}"""
