"""Base provider interface and OpenAI-compatible implementation.

C16 fix: :class:`BaseProvider` is now a pure abstract interface. All OpenAI
compatible HTTP details live in :class:`OpenAICompatibleProvider`, which
delegates the actual HTTP work to an injectable :class:`HttpClient`.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .http_client import HttpClient, OpenAICompatibleClient


class BaseProvider(ABC):
    """Abstract interface for vision-language model providers."""

    default_model: str = ""

    @abstractmethod
    def analyze_screenshot(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Analyze a screenshot and return the next action dict."""
        ...

    @abstractmethod
    def check_task_completion(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Check whether the task appears to be complete."""
        ...


class OpenAICompatibleProvider(BaseProvider):
    """Provider that uses an OpenAI-compatible ``/chat/completions`` endpoint."""

    default_model: str = ""

    DEFAULT_SYSTEM_PROMPT: str = (
        "You are a mobile phone automation assistant. Analyze screenshots and decide actions.\n\n"
        "Available actions:\n"
        "- click: Tap at coordinates (x, y)\n"
        "- swipe: Swipe from (x, y) to (x2, y2)\n"
        "- type: Type text\n"
        "- wait: Wait for UI\n"
        "- terminate: Task done\n\n"
        "Coordinates: 0-999 range, (0,0)=top-left, (999,999)=bottom-right.\n\n"
        "Respond in JSON:\n"
        "{\n"
        '    "thought": "what you see and plan to do",\n'
        '    "action": "click|swipe|type|wait|terminate",\n'
        '    "coordinate": [x, y],\n'
        '    "coordinate2": [x2, y2],\n'
        '    "text": "text to type",\n'
        '    "status": "success|failure"\n'
        "}"
    )

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 512,
        max_retries: int = 3,
        base_url: str = "",
        headers: Optional[Dict[str, str]] = None,
        http_client: Optional[HttpClient] = None,
    ):
        self.api_key = api_key
        self.model = model or self.default_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.system_prompt = self._get_system_prompt()

        if http_client is not None:
            self._client = http_client
        else:
            self._client = OpenAICompatibleClient(
                base_url=base_url,
                headers=headers or {},
            )

    def _get_system_prompt(self) -> str:
        """Return the system prompt. Subclasses may override for customization."""
        return self.DEFAULT_SYSTEM_PROMPT

    def _build_history(self, context: Optional[Dict[str, Any]]) -> str:
        """Build action history string."""
        if not context:
            return "No previous actions"

        history: List[str] = []
        previous_actions = context.get("previous_actions", [])
        for i, act in enumerate(previous_actions[-5:], 1):
            action_type = act.get("action", "unknown")
            element = act.get("elementName", "")
            history.append(f"Step {i}: {action_type} {element}".strip())
        return "; ".join(history) if history else "No previous actions"

    def _encode_image(self, image_path: str) -> str:
        """Encode image to base64."""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _call_api(
        self,
        screenshot_path: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Call provider API through the HTTP client."""
        system_prompt = system_prompt or self.system_prompt
        max_tokens = max_tokens or self.max_tokens

        try:
            base64_image = self._encode_image(screenshot_path)
        except (OSError, IOError) as exc:
            logging.error("Failed to read/encode screenshot %s: %s", screenshot_path, exc)
            return None

        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64_image}"},
                },
                {"type": "text", "text": user_prompt},
            ],
        })

        content = self._client.chat_completion(
            messages,
            model=self.model,
            temperature=self.temperature,
            max_tokens=max_tokens,
            max_retries=self.max_retries,
        )
        if content is None:
            return None
        return self._parse_response(content)

    def _parse_response(self, content: str) -> Optional[Dict[str, Any]]:
        """Parse API response into an action dict."""
        try:
            json_match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_match = re.search(r"\{[\s\S]*?\"action\"[\s\S]*?\}", content)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    json_str = content.strip()

            data = json.loads(json_str)
            return self._normalize_action(data)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logging.error("Failed to parse response: %s", exc)
            logging.debug("Content: %s", content)
            return None

    def _normalize_action(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize action to internal format."""
        action_type = data.get("action", "wait")
        action: Dict[str, Any] = {
            "action": action_type,
            "reasoning": data.get("thought", data.get("reasoning", "")),
        }

        # Handle coordinates
        if "coordinate" in data:
            coord = data["coordinate"]
            action["coordinates"] = [coord[0] / 999.0, coord[1] / 999.0]

        if "coordinate2" in data:
            coord2 = data["coordinate2"]
            action["coordinate2"] = [coord2[0] / 999.0, coord2[1] / 999.0]

        # Map action types
        if action_type == "click":
            action["action"] = "tap"
        elif action_type == "swipe" and "coordinate2" in action:
            start = action["coordinates"]
            end = action["coordinate2"]
            dx, dy = end[0] - start[0], end[1] - start[1]
            action["direction"] = (
                "down" if abs(dy) > abs(dx) and dy > 0 else
                "up" if abs(dy) > abs(dx) else
                "right" if dx > 0 else
                "left"
            )
        elif action_type == "type":
            action["text"] = data.get("text", "")
        elif action_type == "terminate":
            action["status"] = data.get("status", "success")
            action["message"] = data.get("thought", "Task ended")

        return action

    def analyze_screenshot(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Analyze screenshot and return action."""
        history = self._build_history(context)
        prompt = (
            f"Task: {user_request}\nHistory: {history}\n\n"
            "Analyze screenshot and decide next action."
        )
        return self._call_api(screenshot_path, prompt)

    def check_task_completion(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Check if task is completed."""
        try:
            count = len(context.get("previous_actions", [])) if context else 0
            prompt = (
                f"Task: {user_request}\nActions taken: {count}\n\n"
                "Is the task complete? Reply with JSON only: "
                '{"completed": true/false, "reason": "..."}'
            )

            result = self._call_api(
                screenshot_path,
                prompt,
                system_prompt=None,
                max_tokens=256,
            )

            if result and isinstance(result, dict) and "completed" in result:
                completed = result["completed"]
                if isinstance(completed, str):
                    completed = completed.lower() == "true"
                return {
                    "complete": bool(completed),
                    "reason": result.get("reason", ""),
                    "confidence": 0.9 if completed else 0.7,
                }

            content = result.get("reasoning", "") if isinstance(result, dict) else ""
            return {
                "complete": "true" in content.lower(),
                "reason": content[:200],
                "confidence": 0.5,
            }

        except Exception as exc:
            logging.error("Error checking completion: %s", exc)
            return {"complete": False, "reason": str(exc), "confidence": 0.0}
