"""OpenRouter API provider."""

import json
import logging
import re
from typing import Any, Dict, Optional

import requests

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
        self.system_prompt = self._get_system_prompt()

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

    def _call_api(self, screenshot_path: str, user_prompt: str) -> Optional[Dict[str, Any]]:
        """Call OpenRouter API."""
        try:
            base64_image = self._encode_image(screenshot_path)
            
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}},
                            {"type": "text", "text": user_prompt}
                        ]
                    }
                ],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens
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
            logging.error(f"API HTTP error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logging.error(f"Response: {e.response.text}")
            return None
        except Exception as e:
            logging.error(f"API call failed: {e}")
            return None

    def _parse_response(self, content: str) -> Optional[Dict[str, Any]]:
        """Parse API response into action dict."""
        try:
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_match = re.search(r'\{[\s\S]*"action"[\s\S]*\}', content)
                json_str = json_match.group(0) if json_match else content
            
            data = json.loads(json_str)
            return self._normalize_action(data)
            
        except (json.JSONDecodeError, Exception) as e:
            logging.error(f"Failed to parse response: {e}")
            return None

    def _normalize_action(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize action to internal format."""
        action_type = data.get('action', 'wait')
        action = {
            'action': action_type,
            'reasoning': data.get('thought', '')
        }
        
        if 'coordinate' in data:
            coord = data['coordinate']
            action['coordinates'] = [coord[0] / 999.0, coord[1] / 999.0]
        
        if 'coordinate2' in data:
            coord2 = data['coordinate2']
            action['coordinate2'] = [coord2[0] / 999.0, coord2[1] / 999.0]
        
        if action_type == 'click':
            action['action'] = 'tap'
        elif action_type == 'swipe' and 'coordinate2' in action:
            start, end = action['coordinates'], action['coordinate2']
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

    def check_task_completion(
        self,
        screenshot_path: str,
        user_request: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check if task is completed."""
        try:
            count = len(context.get('previous_actions', []))
            prompt = f"Task: {user_request}\nActions: {count}\n\nIs task complete? Reply JSON: {{\"completed\": true/false, \"reason\": \"...\"}}"
            
            base64_image = self._encode_image(screenshot_path)
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}},
                            {"type": "text", "text": prompt}
                        ]
                    }
                ],
                "max_tokens": 256
            }
            
            resp = requests.post(
                f"{self.api_url}/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=30
            )
            resp.raise_for_status()
            content = resp.json()['choices'][0]['message']['content']
            
            try:
                data = json.loads(content)
                completed = data.get('completed', False)
                return {
                    "complete": completed,
                    "reason": data.get('reason', ''),
                    "confidence": 0.9 if completed else 0.7
                }
            except:
                return {
                    "complete": 'true' in content.lower(),
                    "reason": content[:200],
                    "confidence": 0.5
                }
                
        except Exception as e:
            logging.error(f"Error checking completion: {e}")
            return {"complete": False, "reason": str(e), "confidence": 0.0}
