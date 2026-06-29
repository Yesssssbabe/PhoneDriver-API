"""Kimi Code API provider."""

from .base import BaseProvider


class KimiCodeProvider(BaseProvider):
    """Kimi Code API provider for vision-language tasks."""

    default_model = "kimi-for-coding"

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)
        self.api_url = "https://api.kimi.com/coding/v1"
        # Kimi Code API requires specific User-Agent
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Kilo-Code/1.0"
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
