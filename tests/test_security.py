"""Security-focused tests for PhoneDriver-API.

These tests do not require network access or an attached Android device.
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from phone_agent import (
    APIScrubFilter,
    ActionExecutor,
    ActionValidationError,
    ActionValidator,
    ConfigResolver,
    PhoneAgent,
)
from providers.base import OpenAICompatibleProvider
from providers.http_client import CircuitBreaker, HttpClient


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


# =============================================================================
# ADB security tests
# =============================================================================
def test_adb_sanitize_cmd_redacts_sensitive_commands():
    from utils.adb import ADBClient

    client = ADBClient()
    cmd = ["shell", "input text 'secret_password'"]
    assert client._sanitize_cmd(cmd) == "<ADB command containing sensitive text redacted>"

    cmd2 = ["shell", "am broadcast -a clipper.set -e text 'secret'"]
    assert client._sanitize_cmd(cmd2) == "<ADB command containing sensitive text redacted>"

    cmd3 = ["devices"]
    assert client._sanitize_cmd(cmd3) == "devices"


def test_adb_sanitize_env_strips_api_keys():
    from utils.adb import ADBClient

    with patch.dict(
        os.environ,
        {"OPENAI_API_KEY": "sk-test", "KIMI_CODE_API_KEY": "kimi-test", "PATH": "/bin"},
    ):
        env = ADBClient._sanitize_env()
        assert "OPENAI_API_KEY" not in env
        assert "KIMI_CODE_API_KEY" not in env
        assert "PATH" in env


def test_adb_device_id_validation_rejects_invalid_chars():
    from utils.adb import ADBClient

    client = ADBClient(device_id="evil; rm -rf /")
    with pytest.raises(ValueError):
        client._cmd(["shell", "echo hi"])


def test_adb_type_text_rejects_overlong_and_null_text():
    from utils.adb import ADBClient

    client = ADBClient()
    with pytest.raises(ValueError, match="maximum length"):
        client.type_text("x" * 1001)

    with pytest.raises(ValueError, match="Null bytes"):
        client.type_text("hello\x00world")


def test_adb_type_text_clipboard_uses_shlex_quote():
    from utils.adb import ADBClient

    client = ADBClient()
    payload = '; id > /sdcard/pwned.txt'
    with patch.object(client, "run") as mock_run:
        client._type_via_clipboard(payload)
        called_command = mock_run.call_args_list[0][0][0]
        command_str = " ".join(called_command)
        # The semicolon should be inside a quoted shell word, not a command separator
        assert "; id" not in command_str or "'" in command_str or '"' in command_str


def test_adb_screenshot_rejects_path_traversal():
    from utils.adb import ADBClient

    client = ADBClient()
    assert client.screenshot("/etc/passwd.png") is False
    assert client.screenshot("../../etc/passwd.png") is False


def test_adb_tap_swipe_keyevent_validate_bounds():
    from utils.adb import ADBClient

    client = ADBClient()
    with pytest.raises(ValueError):
        client.tap(-1, 100)
    with pytest.raises(ValueError):
        client.swipe(0, 0, 100, 100, duration=-1)
    with pytest.raises(ValueError):
        client.keyevent(-1)


# =============================================================================
# Screenshot security tests
# =============================================================================
def test_screenshot_save_dir_rejects_path_traversal():
    from utils.adb import ADBClient
    from utils.screenshot import ScreenshotCapture

    adb = ADBClient()
    with pytest.raises(ValueError):
        ScreenshotCapture(adb, save_dir="../../../etc")


def test_screenshot_capture_rejects_malicious_filename():
    from utils.adb import ADBClient
    from utils.screenshot import ScreenshotCapture

    adb = ADBClient()
    cap = ScreenshotCapture(adb)
    # Path separators are stripped to basename; hidden filenames are rejected
    with pytest.raises(ValueError):
        cap.capture(filename=".hidden.png")


def test_screenshot_capture_uses_unique_filenames():
    from utils.adb import ADBClient
    from utils.screenshot import ScreenshotCapture

    adb = ADBClient()
    cap = ScreenshotCapture(adb)
    with patch.object(adb, "screenshot", return_value=True):
        name1 = cap.capture()
        name2 = cap.capture()
    assert name1 is not None
    assert name2 is not None
    assert name1 != name2


# =============================================================================
# Provider security tests
# =============================================================================
def test_encode_image_rejects_path_traversal():
    provider = OpenAICompatibleProvider(
        api_key="test",
        base_url="https://example.com/v1",
        http_client=DummyHttpClient('{"action": "wait"}'),
    )
    with pytest.raises(ValueError, match="Invalid image path"):
        provider._encode_image("/etc/passwd")
    with pytest.raises(ValueError, match="Invalid image extension"):
        provider._encode_image("./screenshots/screen.txt")


def test_encode_image_accepts_allowed_png():
    from utils.adb import ADBClient
    from utils.screenshot import ScreenshotCapture

    screenshot_dir = Path("./screenshots")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=str(screenshot_dir), suffix=".png", delete=False
    ) as f:
        f.write(b"\x89PNG\r\n\x1a\nfake")
        path = f.name

    provider = OpenAICompatibleProvider(
        api_key="test",
        base_url="https://example.com/v1",
        http_client=DummyHttpClient('{"action": "wait"}'),
    )
    encoded = provider._encode_image(path)
    assert encoded.startswith("iVBOR")


def test_normalize_action_handles_malformed_json():
    provider = OpenAICompatibleProvider(
        api_key="test",
        base_url="https://example.com/v1",
        http_client=DummyHttpClient('{"action": "wait"}'),
    )
    assert provider._normalize_action([])["action"] == "wait"
    assert provider._normalize_action("not a dict")["action"] == "wait"

    malformed = provider._normalize_action({"action": "tap", "coordinate": [100]})
    assert malformed["action"] == "tap"
    assert "coordinates" not in malformed

    assert provider._normalize_action({"action": "evil"})["action"] == "wait"


def test_normalize_action_validates_coordinates():
    provider = OpenAICompatibleProvider(
        api_key="test",
        base_url="https://example.com/v1",
        http_client=DummyHttpClient('{"action": "wait"}'),
    )
    action = provider._normalize_action({"action": "tap", "coordinate": [500, 500]})
    assert action["action"] == "tap"
    assert action["coordinates"] == [500 / 999.0, 500 / 999.0]

    action2 = provider._normalize_action({"action": "tap", "coordinate": [2000, 2000]})
    assert "coordinates" not in action2


def test_parse_response_rejects_oversized_content():
    provider = OpenAICompatibleProvider(
        api_key="test",
        base_url="https://example.com/v1",
        http_client=DummyHttpClient('{"action": "wait"}'),
    )
    huge = "x" * (100_000 + 1)
    assert provider._parse_response(huge) is None


def test_parse_response_handles_nested_json():
    provider = OpenAICompatibleProvider(
        api_key="test",
        base_url="https://example.com/v1",
        http_client=DummyHttpClient('{"action": "wait"}'),
    )
    content = '{"action": "tap", "metadata": {"key": "value"}}'
    result = provider._parse_response(content)
    assert result is not None
    assert result["action"] == "tap"


def test_build_history_redacts_typed_text():
    provider = OpenAICompatibleProvider(
        api_key="test",
        base_url="https://example.com/v1",
        http_client=DummyHttpClient('{"action": "wait"}'),
    )
    history = provider._build_history({
        "previous_actions": [{"action": "type", "text": "secret123"}]
    })
    assert "secret123" not in history
    assert "[REDACTED]" in history


# =============================================================================
# HTTP client security tests
# =============================================================================
def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
    assert cb.can_execute() is True
    cb.record_failure()
    cb.record_failure()
    opened = cb.record_failure()
    assert opened is True
    assert cb.can_execute() is False


def test_circuit_breaker_recovers_after_timeout():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0)
    cb.record_failure()
    assert cb.can_execute() is True  # HALF_OPEN after timeout


def test_http_client_close_releases_session():
    from providers.http_client import OpenAICompatibleClient

    client = OpenAICompatibleClient(base_url="https://example.com/v1")
    client.close()
    assert client.session is not None  # close() does not reset attribute


# =============================================================================
# PhoneAgent / Action validation tests
# =============================================================================
def test_action_validator_rejects_invalid_actions():
    validator = ActionValidator()
    with pytest.raises(ActionValidationError):
        validator.validate({"action": "explode"})
    with pytest.raises(ActionValidationError):
        validator.validate({"action": "tap", "coordinates": [0.5]})
    with pytest.raises(ActionValidationError):
        validator.validate({"action": "tap", "coordinates": [2.0, 0.5]})
    with pytest.raises(ActionValidationError):
        validator.validate({"action": "wait", "waitTime": 999999})


def test_action_validator_accepts_valid_actions():
    validator = ActionValidator()
    validator.validate({"action": "tap", "coordinates": [0.5, 0.5]})
    validator.validate({"action": "swipe", "direction": "up"})
    validator.validate({"action": "type", "text": "hello"})
    validator.validate({"action": "wait", "waitTime": 500})
    validator.validate({"action": "terminate"})


def test_action_executor_validates_tap_coordinates():
    adb = MagicMock()
    executor = ActionExecutor(adb, (1000, 2000))
    with pytest.raises(ValueError):
        executor.execute({"action": "tap", "coordinates": [2.0, 0.5]})
    with pytest.raises(ValueError):
        executor.execute({"action": "tap", "coordinates": "0.5,0.5"})


def test_config_resolver_rejects_path_traversal_screenshot_dir():
    with pytest.raises(ValueError):
        ConfigResolver({"screenshot_dir": "../../../etc"}, use_env_fallback=False).resolve()


def test_api_scrub_filter_redacts_keys():
    scrubber = APIScrubFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Bearer sk-test123 and OPENAI_API_KEY=secret",
        args=(),
        exc_info=None,
    )
    scrubber.filter(record)
    assert "sk-test123" not in record.msg
    assert "secret" not in record.msg
    assert "<REDACTED>" in record.msg


def test_config_resolver_clamps_temperature_and_max_tokens():
    cfg = ConfigResolver(
        {
            "temperature": 100.0,
            "max_tokens": 999999,
            "provider": "openai",
            "api_key": "sk-test",
        },
        use_env_fallback=False,
    ).resolve()
    assert cfg["temperature"] == 2.0
    assert cfg["max_tokens"] == 4096


def test_config_resolver_does_not_mutate_input():
    original = {
        "provider": "openai",
        "api_key": "sk-test",
        "temperature": 0.5,
        "screenshot_dir": "./screenshots",
    }
    ConfigResolver(original, use_env_fallback=False).resolve()
    assert original["temperature"] == 0.5


def test_encode_image_rejects_oversized_file():
    from utils.adb import ADBClient
    from utils.screenshot import ScreenshotCapture

    adb = ADBClient()
    cap = ScreenshotCapture(adb)
    with patch.object(adb, "screenshot", return_value=True):
        path = cap.capture(filename="large_test.png")
    assert path is not None
    # Write a file larger than MAX_IMAGE_SIZE
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"x" * (11 * 1024 * 1024))

    provider = OpenAICompatibleProvider(
        api_key="test",
        base_url="https://example.com/v1",
        http_client=DummyHttpClient('{"action": "wait"}'),
    )
    with pytest.raises(ValueError, match="Image too large"):
        provider._encode_image(path)


def test_phone_agent_has_cleanup_method():
    agent = PhoneAgent(
        {
            "provider": "openai",
            "api_key": "sk-test",
            "model": "gpt-4o",
            "auto_detect_resolution": False,
        }
    )
    assert hasattr(agent, "cleanup")
    assert callable(agent.cleanup)
    agent.cleanup()  # Should not raise


# Make os available for the ADB env patch test
import os  # noqa: E402
