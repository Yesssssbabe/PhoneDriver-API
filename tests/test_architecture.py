"""Tests verifying the C13/C14/C16 architecture fixes.

These tests do not require network access or an attached Android device.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from phone_agent import ActionExecutor, ConfigResolver, PhoneAgent
from providers import available_providers, get_provider, register_provider
from providers.base import BaseProvider, OpenAICompatibleProvider
from providers.http_client import HttpClient


class DummyHttpClient(HttpClient):
    """Fake HTTP client that returns a canned action JSON string."""

    def __init__(self, content: str):
        self.content = content

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> Optional[str]:
        return self.content


class DummyProvider(BaseProvider):
    """Minimal provider used to test the abstract interface."""

    def analyze_screenshot(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return {"action": "terminate", "message": "done"}

    def check_task_completion(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {"complete": True, "reason": "test", "confidence": 1.0}


def test_config_resolver_coerces_types():
    cfg = ConfigResolver(
        {
            "provider": "openai",
            "api_key": "sk-test",
            "model": "gpt-4o",
            "max_cycles": "20",
            "temperature": "0.5",
            "max_history": "5",
        },
        use_env_fallback=False,
    ).resolve()

    assert cfg["provider"] == "openai"
    assert cfg["api_key"] == "sk-test"
    assert cfg["model"] == "gpt-4o"
    assert cfg["max_cycles"] == 20
    assert cfg["temperature"] == 0.5
    assert cfg["max_history"] == 5


def test_action_executor_executes_tap():
    adb = MagicMock()
    executor = ActionExecutor(adb, (1000, 2000))
    result = executor.execute({"action": "tap", "coordinates": [0.5, 0.25]})

    assert result is True
    adb.tap.assert_called_once_with(500, 500)


def test_action_executor_terminate_returns_false():
    adb = MagicMock()
    executor = ActionExecutor(adb, (1000, 2000))
    result = executor.execute({"action": "terminate", "message": "done"})

    assert result is False


def test_provider_auto_discovery():
    names = available_providers()
    assert "kimi_code" in names
    assert "openai" in names
    assert "moonshot" in names
    assert "openrouter" in names


def test_add_new_provider_without_editing_init():
    """C14: a new provider file can register itself with zero old-file changes."""

    @register_provider("test_provider")
    class TestProvider(DummyProvider):
        pass

    assert "test_provider" in available_providers()
    instance = get_provider("test_provider")
    assert isinstance(instance, TestProvider)


def _make_fake_screenshot() -> str:
    """Create a temporary file inside the allowed screenshot directory."""
    screenshot_dir = Path("./screenshots")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=str(screenshot_dir), suffix=".png", delete=False
    ) as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        return f.name


def test_openai_compatible_provider_uses_injected_http_client():
    """C16: OpenAICompatibleProvider delegates HTTP to the injected HttpClient."""
    client = DummyHttpClient('{"action": "tap", "coordinate": [500, 500]}')
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        headers={"Authorization": "Bearer test-key"},
        http_client=client,
    )

    screenshot_path = _make_fake_screenshot()
    action = provider.analyze_screenshot(screenshot_path, "tap the icon", {})
    assert action is not None
    assert action["action"] == "tap"


def test_phone_agent_wires_components():
    """C13: PhoneAgent is a thin facade that wires focused sub-components."""
    agent = PhoneAgent(
        {
            "provider": "openai",
            "api_key": "sk-test",
            "model": "gpt-4o",
            "auto_detect_resolution": False,
        }
    )

    assert hasattr(agent, "_orchestrator")
    assert hasattr(agent, "config")
    assert agent.config["provider"] == "openai"


if __name__ == "__main__":
    test_config_resolver_coerces_types()
    test_action_executor_executes_tap()
    test_action_executor_terminate_returns_false()
    test_provider_auto_discovery()
    test_add_new_provider_without_editing_init()
    test_openai_compatible_provider_uses_injected_http_client()
    test_phone_agent_wires_components()
    print("All architecture tests passed.")
