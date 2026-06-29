"""Base provider interface."""

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import requests


class BaseProvider(ABC):
    """Base class for vision-language model providers."""

    default_model: str = ""

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 512,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.model = model or self.default_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.api_url = ""
        self.headers = {}
        self.system_prompt = self._get_system_prompt()

    @abstractmethod
    def _get_system_prompt(self) -> str:
        """Get system prompt for mobile automation."""
        pass

    def _build_history(self, context: Optional[Dict[str, Any]]) -> str:
        """Build action history string."""
        if not context:
            return "No previous actions"

        history = []
        previous_actions = context.get('previous_actions', [])
        for i, act in enumerate(previous_actions[-5:], 1):
            action_type = act.get('action', 'unknown')
            element = act.get('elementName', '')
            history.append(f"Step {i}: {action_type} {element}".strip())
        return "; ".join(history) if history else "No previous actions"

    def _encode_image(self, image_path: str) -> str:
        """Encode image to base64."""
        import base64
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')

    def _call_api(
        self,
        screenshot_path: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Call provider API with retry logic."""
        system_prompt = system_prompt or self.system_prompt
        max_tokens = max_tokens or self.max_tokens
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                base64_image = self._encode_image(screenshot_path)

                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}},
                        {"type": "text", "text": user_prompt}
                    ]
                })

                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "max_tokens": max_tokens,
                }

                resp = requests.post(
                    f"{self.api_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=60
                )
                resp.raise_for_status()

                content = resp.json()['choices'][0]['message']['content']
                return self._parse_response(content)

            except requests.exceptions.HTTPError as e:
                last_error = e
                logging.error(f"API HTTP error (attempt {attempt}/{self.max_retries}): {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logging.error(f"Response: {e.response.text}")
                if attempt == self.max_retries:
                    return None
            except Exception as e:
                last_error = e
                logging.error(f"API call failed (attempt {attempt}/{self.max_retries}): {e}")
                if attempt == self.max_retries:
                    return None

        return None

    def _parse_response(self, content: str) -> Optional[Dict[str, Any]]:
        """Parse API response into action dict."""
        try:
            # Extract JSON from markdown or raw text
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Match outermost braces containing "action" key
                json_match = re.search(r'\{[\s\S]*?"action"[\s\S]*?\}', content)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    # Try to parse the whole content as JSON
                    json_str = content.strip()

            data = json.loads(json_str)
            return self._normalize_action(data)

        except (json.JSONDecodeError, Exception) as e:
            logging.error(f"Failed to parse response: {e}")
            logging.debug(f"Content: {content}")
            return None

    def _normalize_action(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize action to internal format."""
        action_type = data.get('action', 'wait')
        action = {
            'action': action_type,
            'reasoning': data.get('thought', data.get('reasoning', ''))
        }

        # Handle coordinates
        if 'coordinate' in data:
            coord = data['coordinate']
            action['coordinates'] = [coord[0] / 999.0, coord[1] / 999.0]

        if 'coordinate2' in data:
            coord2 = data['coordinate2']
            action['coordinate2'] = [coord2[0] / 999.0, coord2[1] / 999.0]

        # Map action types
        if action_type == 'click':
            action['action'] = 'tap'
        elif action_type == 'swipe' and 'coordinate2' in action:
            start = action['coordinates']
            end = action['coordinate2']
            dx, dy = end[0] - start[0], end[1] - start[1]
            action['direction'] = 'down' if abs(dy) > abs(dx) and dy > 0 else \
                                 'up' if abs(dy) > abs(dx) else \
                                 'right' if dx > 0 else 'left'
        elif action_type == 'type':
            action['text'] = data.get('text', '')
        elif action_type == 'terminate':
            action['status'] = data.get('status', 'success')
            action['message'] = data.get('thought', 'Task ended')

        return action

    def analyze_screenshot(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Analyze screenshot and return action."""
        try:
            history = self._build_history(context)
            prompt = f"Task: {user_request}\nHistory: {history}\n\nAnalyze screenshot and decide next action."
            return self._call_api(screenshot_path, prompt)
        except Exception as e:
            logging.error(f"Error analyzing screenshot: {e}")
            return None

    def check_task_completion(
        self,
        screenshot_path: str,
        user_request: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check if task is completed."""
        try:
            count = len(context.get('previous_actions', []))
            prompt = (
                f"Task: {user_request}\nActions taken: {count}\n\n"
                "Is the task complete? Reply with JSON only: "
                '{"completed": true/false, "reason": "..."}'
            )

            result = self._call_api(
                screenshot_path,
                prompt,
                system_prompt=None,
                max_tokens=256
            )

            if result and 'completed' in result:
                completed = result['completed']
                return {
                    "complete": completed,
                    "reason": result.get('reason', ''),
                    "confidence": 0.9 if completed else 0.7
                }

            # Fallback: try to parse from reasoning text
            content = result.get('reasoning', '') if result else ''
            return {
                "complete": 'true' in content.lower(),
                "reason": content[:200],
                "confidence": 0.5
            }

        except Exception as e:
            logging.error(f"Error checking completion: {e}")
            return {"complete": False, "reason": str(e), "confidence": 0.0}
