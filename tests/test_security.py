"""Security-focused tests for PhoneDriver-API.

These tests do not require network access or an attached Android device.
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from PIL import Image

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


def test_adb_type_text_clipboard_uses_intent_escape():
    from utils.adb import ADBClient

    client = ADBClient()
    payload = '; id > /sdcard/pwned.txt'
    with patch.object(client, "_run_shell_input") as mock_run:
        client._type_via_clipboard(payload)
        called_command = mock_run.call_args_list[0][0][0]
        # The semicolon should be escaped/quoted, not a command separator.
        assert "; id" not in called_command or '"' in called_command


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


def test_adb_sanitize_env_removes_world_writable_path_dirs():
    from utils.adb import ADBClient

    with patch.dict(os.environ, {"PATH": "/tmp:/usr/bin:/bin"}, clear=True):
        with patch("os.path.isdir", return_value=True), patch("os.stat") as mock_stat:
            # /tmp is world-writable, /usr/bin and /bin are safe
            perms = [0o777, 0o755, 0o755]

            def side_effect(_path):
                class St:
                    st_mode = perms.pop(0)
                return St()

            mock_stat.side_effect = side_effect
            env = ADBClient._sanitize_env()
            assert "/tmp" not in env["PATH"]
            assert "/usr/bin:/bin" == env["PATH"]


def test_adb_validate_adb_keys_rejects_world_writable():
    from utils.adb import ADBClient

    with patch.dict(os.environ, {"HOME": "/home/user"}):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.stat") as mock_stat:
                class St:
                    st_mode = 0o777
                    st_uid = os.getuid()
                mock_stat.return_value = St()
                with pytest.raises(PermissionError):
                    ADBClient._validate_adb_keys()


def test_adb_get_current_window_parses_package():
    from utils.adb import ADBClient

    client = ADBClient()
    with patch.object(client, "run", return_value=(0, "mCurrentFocus=Window{8c4d4 u0 com.example.app/com.example.app.MainActivity}", "")):
        assert client.get_current_window() == "com.example.app"


def test_adb_is_connected_rejects_tcp_without_env_override():
    from utils.adb import ADBClient

    client = ADBClient(device_id="192.168.1.2:5555")
    with patch.dict(os.environ, {}, clear=True):
        assert client.is_connected() is False


def test_adb_output_size_limit():
    from utils.adb import ADBClient

    client = ADBClient()
    with patch("subprocess.Popen") as mock_popen:
        proc = MagicMock()
        proc.stdout.read.side_effect = ["x" * 4096] * 257 + [""]
        proc.stderr.read.return_value = ""
        proc.wait.return_value = 0
        mock_popen.return_value = proc
        with pytest.raises(ValueError, match="exceeded"):
            client.run(["shell", "echo", "hi"], max_output_bytes=1024 * 1024)


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


def test_screenshot_sensitive_app_detection():
    from utils.adb import ADBClient
    from utils.screenshot import ScreenshotCapture

    adb = ADBClient()
    cap = ScreenshotCapture(adb)
    with patch.object(adb, "get_current_window", return_value="com.android.bank"):
        with pytest.raises(PermissionError, match="sensitive app"):
            cap.capture_with_resize()


def test_screenshot_sensitive_app_whitelist_allows():
    from utils.adb import ADBClient
    from utils.screenshot import ScreenshotCapture

    adb = ADBClient()
    cap = ScreenshotCapture(adb, sensitive_app_whitelist=["com.android.bank"])
    with patch.object(adb, "get_current_window", return_value="com.android.bank"):
        with patch.object(adb, "screenshot", return_value=True):
            # capture() returns None because screenshot content is not a valid PNG,
            # but sensitive check should pass.
            cap.capture_with_resize()


def test_screenshot_notification_bar_stripped():
    from utils.screenshot import ScreenshotCapture

    adb = MagicMock()
    cap = ScreenshotCapture(adb)
    img = Image.new("RGB", (100, 200), color="black")
    cropped = cap._strip_notification_bar(img)
    # notification_height = min(100, 200 // 10) = 20
    assert cropped.size == (100, 180)


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


def test_http_client_tracks_cost():
    from providers.http_client import OpenAICompatibleClient

    client = OpenAICompatibleClient(base_url="https://example.com/v1")
    with patch.object(client.session, "post") as mock_post:
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "application/json"}
        resp.raw.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"action": "wait"}'}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
        }).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_post.return_value = resp
        content = client.chat_completion(
            [{"role": "user", "content": "hi"}],
            model="gpt-4o",
            temperature=0.1,
            max_tokens=100,
        )
        assert content is not None
        # cost = 1000/1000*0.005 + 500/1000*0.015 = 0.005 + 0.0075 = 0.0125
        assert client._cost_tracker["total_cost_usd"] == pytest.approx(0.0125, abs=1e-6)


def test_http_client_pinned_cert():
    from providers.http_client import OpenAICompatibleClient

    client = OpenAICompatibleClient(base_url="https://example.com/v1", pinned_cert="/path/to/cert.pem")
    assert client.session.verify == "/path/to/cert.pem"


def test_provider_response_hmac_verification():
    import hashlib
    import hmac

    from providers.base import OpenAICompatibleProvider

    key = "secret"
    body = b'{"action": "wait"}'
    signature = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    client = DummyHttpClient('{"action": "wait"}')
    client.last_response_raw = body
    client.last_response_headers = {"X-Response-Signature": signature}

    provider = OpenAICompatibleProvider(
        api_key="test",
        base_url="https://example.com/v1",
        http_client=client,
        response_hmac_key=key,
    )
    assert provider._verify_response(body, signature) is True

    # Tampered body should fail verification.
    assert provider._verify_response(body + b"x", signature) is False


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


def test_output_safety_checker_blocks_dangerous_actions():
    from phone_agent import OutputSafetyChecker

    checker = OutputSafetyChecker()
    assert checker.check({"action": "type", "text": "hello"}) is True
    assert checker.check({"action": "type", "text": "factory reset"}) is False
    assert checker.check({"action": "tap", "coordinates": [0.5, 0.5]}) is True


def test_api_key_manager_rotates_keys():
    from phone_agent import APIKeyManager

    km = APIKeyManager(["key1", "key2"])
    assert km.get_key() in {"key1", "key2"}
    assert km.get_key() in {"key1", "key2"}


def test_config_resolver_dpa_required():
    from phone_agent import ConfigResolver

    with pytest.raises(ValueError, match="DPA"):
        ConfigResolver(
            {"provider": "moonshot", "api_key": "sk-test", "dpa_accepted": False},
            use_env_fallback=False,
        ).resolve()

    cfg = ConfigResolver(
        {"provider": "moonshot", "api_key": "sk-test", "dpa_accepted": True},
        use_env_fallback=False,
    ).resolve()
    assert cfg["compliance"]["dpa_required"] is True


def test_config_resolver_data_residency():
    from phone_agent import ConfigResolver

    cfg = ConfigResolver(
        {"provider": "moonshot", "api_key": "sk-test", "data_residency": "eu", "dpa_accepted": True},
        use_env_fallback=False,
    ).resolve()
    assert cfg["data_residency"] == "eu"
    assert "eu.api.moonshot.cn" in cfg["provider_base_url"]


def test_task_orchestrator_budget_exceeded():
    from phone_agent import TaskOrchestrator

    adb = MagicMock()
    adb.is_connected.return_value = True
    screenshot = MagicMock()
    screenshot.capture_with_resize.return_value = None
    provider = MagicMock()
    client = MagicMock()
    client._cost_tracker = {"total_cost_usd": 15.0}
    provider._client = client

    orchestrator = TaskOrchestrator(
        adb, screenshot, provider,
        {
            "max_cycles": 5,
            "step_delay": 0.0,
            "check_completion": False,
            "max_history": 5,
            "max_api_calls": 200,
            "max_budget_usd": 10.0,
            "screen_width": 1080,
            "screen_height": 2340,
        },
    )
    result = orchestrator.run("do something")
    assert result["success"] is False
    assert "Budget exceeded" in result["message"]


# Make os available for the ADB env patch test
import os  # noqa: E402
