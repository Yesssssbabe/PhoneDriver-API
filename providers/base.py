"""Base provider interface and OpenAI-compatible implementation.

C16 fix: :class:`BaseProvider` is now a pure abstract interface. All OpenAI
compatible HTTP details live in :class:`OpenAICompatibleProvider`, which
delegates the actual HTTP work to an injectable :class:`HttpClient`.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from .http_client import HttpClient, OpenAICompatibleClient


MAX_RESPONSE_LENGTH = 100000  # 100 KB
MAX_TASK_LENGTH = 2000
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB


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

    def close(self) -> None:
        """Release resources held by the provider. Subclasses may override."""


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

        # Simple file-hash keyed cache for encoded screenshots
        self._image_cache: Dict[str, str] = {}
        self._max_cache_size = 8

    def close(self) -> None:
        """Close the underlying HTTP client if it supports close()."""
        if hasattr(self._client, 'close'):
            self._client.close()

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
            act = dict(act)
            action_type = act.get("action", "unknown")
            element = act.get("elementName", "")
            # Redact sensitive typed text from history sent to cloud providers
            if action_type == "type" and "text" in act:
                act["text"] = "[REDACTED]"
                element = f"[REDACTED] {element}".strip()
            history.append(f"Step {i}: {action_type} {element}".strip())
        return "; ".join(history) if history else "No previous actions"

    def _encode_image(self, image_path: str) -> str:
        """Encode image to base64 with path validation."""
        target = Path(image_path).resolve()
        allowed_base = Path("./screenshots").resolve().parent
        try:
            target.relative_to(allowed_base)
        except ValueError as exc:
            raise ValueError(f"Invalid image path: {image_path}") from exc
        if target.suffix.lower() != ".png":
            raise ValueError(f"Invalid image extension: {image_path}")
        if not target.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
        if not target.is_file():
            raise ValueError(f"Image path is not a file: {image_path}")

        size = os.path.getsize(target)
        if size > MAX_IMAGE_SIZE:
            raise ValueError(f"Image too large: {size} bytes (max {MAX_IMAGE_SIZE})")

        # Cache keyed by path + mtime to avoid re-reading unchanged screenshots
        mtime = os.path.getmtime(target)
        cache_key = f"{target}:{mtime}"
        if cache_key in self._image_cache:
            return self._image_cache[cache_key]

        with open(target, "rb") as f:
            data = f.read()
        try:
            encoded = base64.b64encode(data).decode("utf-8")
        finally:
            del data

        self._image_cache[cache_key] = encoded
        if len(self._image_cache) > self._max_cache_size:
            self._image_cache.pop(next(iter(self._image_cache)))
        return encoded

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
        except (OSError, IOError, ValueError) as exc:
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

        try:
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
        finally:
            del base64_image

    @staticmethod
    def _extract_json(content: str) -> Optional[str]:
        """Extract the outermost JSON object from content using bracket balancing."""
        start = content.find('{')
        if start == -1:
            return None
        depth = 0
        for i, ch in enumerate(content[start:], start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return content[start:i + 1]
        return None

    def _parse_response(self, content: str) -> Optional[Dict[str, Any]]:
        """Parse API response into an action dict."""
        if len(content) > MAX_RESPONSE_LENGTH:
            logging.error("Response too large: %d bytes", len(content))
            return None
        try:
            # Prefer markdown fence content if present
            json_match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = self._extract_json(content)
                if json_str is None:
                    json_str = content.strip()

            data = json.loads(json_str)
            return self._normalize_action(data)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logging.error("Failed to parse response: %s", exc)
            logging.debug("Content: %s", content)
            return None

    def _normalize_action(self, data: Any) -> Dict[str, Any]:
        """Normalize action to internal format."""
        if not isinstance(data, dict):
            logging.error("Expected dict from JSON, got %s", type(data).__name__)
            return {"action": "wait"}

        action_type = data.get("action", "wait")
        allowed_actions = {"click", "swipe", "type", "wait", "terminate", "tap"}
        if action_type not in allowed_actions:
            logging.warning("Unknown action type: %s", action_type)
            action_type = "wait"

        action: Dict[str, Any] = {
            "action": action_type,
            "reasoning": str(data.get("thought", data.get("reasoning", "")))[:500],
        }

        # Handle coordinates
        coord = data.get("coordinate")
        if isinstance(coord, (list, tuple)) and len(coord) >= 2:
            try:
                x, y = float(coord[0]), float(coord[1])
                if 0.0 <= x <= 999.0 and 0.0 <= y <= 999.0:
                    action["coordinates"] = [x / 999.0, y / 999.0]
                else:
                    logging.warning("Coordinate out of range: %s", coord)
            except (TypeError, ValueError):
                logging.warning("Non-numeric coordinate values: %s", coord)
        elif coord is not None:
            logging.warning("Invalid coordinate format: %s", coord)

        coord2 = data.get("coordinate2")
        if isinstance(coord2, (list, tuple)) and len(coord2) >= 2:
            try:
                x2, y2 = float(coord2[0]), float(coord2[1])
                if 0.0 <= x2 <= 999.0 and 0.0 <= y2 <= 999.0:
                    action["coordinate2"] = [x2 / 999.0, y2 / 999.0]
                else:
                    logging.warning("Coordinate2 out of range: %s", coord2)
            except (TypeError, ValueError):
                logging.warning("Non-numeric coordinate2 values: %s", coord2)
        elif coord2 is not None:
            logging.warning("Invalid coordinate2 format: %s", coord2)

        # Map action types
        if action_type == "click":
            action["action"] = "tap"
        elif action_type == "swipe" and "coordinates" in action and "coordinate2" in action:
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
            text = data.get("text", "")
            action["text"] = str(text) if text is not None else ""
        elif action_type == "terminate":
            action["status"] = data.get("status", "success")
            action["message"] = str(data.get("thought", "Task ended"))[:500]

        return action

    def analyze_screenshot(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Analyze screenshot and return action."""
        if len(user_request) > MAX_TASK_LENGTH:
            logging.error("Task too long: %d (max %d)", len(user_request), MAX_TASK_LENGTH)
            raise ValueError(f"Task exceeds maximum length of {MAX_TASK_LENGTH}")

        history = self._build_history(context)
        safe_task = user_request.replace('\n', ' ').replace('\r', '')
        prompt = (
            f"Task: {safe_task}\nHistory: {history}\n\n"
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

            if result and isinstance(result, dict):
                completed = result.get("completed", False)
                if isinstance(completed, str):
                    completed = completed.lower() == "true"
                return {
                    "complete": bool(completed),
                    "reason": str(result.get("reason", "")),
                    "confidence": 0.9 if completed else 0.7,
                }

            return {"complete": False, "reason": "", "confidence": 0.0}

        except (OSError, ValueError, AttributeError) as exc:
            logging.error("Error checking completion: %s", exc)
            return {"complete": False, "reason": "Completion check failed", "confidence": 0.0}
