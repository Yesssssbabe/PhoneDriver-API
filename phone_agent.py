#!/usr/bin/env python3
"""
PhoneDriver API - Mobile automation using cloud vision models.

C13 fix: the monolithic PhoneAgent is split into focused, single-responsibility
components:

* :class:`ConfigResolver` — configuration loading, coercion and API key resolution
* :class:`ActionExecutor` — executes one action dict on the device
* :class:`TaskOrchestrator` — runs the screenshot/analyze/execute loop
* :class:`PhoneAgent` — thin facade that wires the above together

Usage:
    python phone_agent.py "Open Settings"
    python phone_agent.py "Search for weather in New York"
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import logging
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class SecureRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that creates log files with restrictive permissions."""

    def _open(self):
        # Reject symlinks to prevent log redirection attacks.
        if os.path.exists(self.baseFilename) and os.path.islink(self.baseFilename):
            raise ValueError(f"Log file is a symlink: {self.baseFilename}")
        old_umask = os.umask(0o077)
        try:
            stream = super()._open()
        finally:
            os.umask(old_umask)
        if os.name != "nt":
            try:
                os.chmod(self.baseFilename, 0o600)
            except OSError:
                pass
        return stream

from dotenv import load_dotenv

from providers import get_provider
from providers.base import BaseProvider
from utils.adb import ADBClient
from utils.screenshot import ScreenshotCapture


# =============================================================================
# Constants
# =============================================================================
DEFAULT_SCREEN_WIDTH: int = 1080
DEFAULT_SCREEN_HEIGHT: int = 2340
DEFAULT_MAX_CYCLES: int = 15
DEFAULT_STEP_DELAY: float = 1.5
DEFAULT_MAX_HISTORY: int = 20
DEFAULT_TEMPERATURE: float = 0.1
DEFAULT_MAX_TOKENS: int = 512
DEFAULT_MAX_RETRIES: int = 3
DEFAULT_WAIT_TIME_MS: int = 1000
DEFAULT_MAX_API_CALLS: int = 200

MAX_WAIT_TIME_MS: int = 30000
MIN_WAIT_TIME_MS: int = 0
MAX_CONFIG_SIZE: int = 64 * 1024  # 64 KB
MAX_MAX_CYCLES: int = 50
MAX_STEP_DELAY: float = 10.0
MAX_MAX_RETRIES: int = 10
MAX_MAX_HISTORY: int = 100
MAX_SCREEN_DIM: int = 16384

API_KEY_ENV_MAP: Dict[str, str] = {
    "kimi_code": "KIMI_CODE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
}


# =============================================================================
# Logging scrubber
# =============================================================================
class APIScrubFilter(logging.Filter):
    """Redact API keys and Bearer tokens from log records."""

    # Bounded, linear-time patterns to avoid ReDoS on long log messages.
    _PATTERNS = [
        (re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{0,200}"), "Bearer <REDACTED>"),
        (re.compile(r"[A-Z_]{0,20}API_KEY\s*[:=]?\s*\S{0,400}", re.IGNORECASE), "API_KEY=<REDACTED>"),
        (re.compile(r"Authorization\s*[:=]?\s*\S{0,400}", re.IGNORECASE), "Authorization=<REDACTED>"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.msg)
        for pattern, repl in self._PATTERNS:
            msg = pattern.sub(repl, msg)
        record.msg = msg
        if record.args:
            record.args = tuple(
                self._scrub_arg(arg) for arg in record.args
            )
        return True

    @classmethod
    def _scrub_arg(cls, arg: Any) -> str:
        text = str(arg)
        for pattern, repl in cls._PATTERNS:
            text = pattern.sub(repl, text)
        return text


def _setup_logging() -> None:
    """Configure logging with rotation and API key scrubbing."""
    log_path = Path("phone_agent.log")
    if log_path.exists():
        if log_path.is_symlink():
            raise ValueError(f"Log path is a symlink: {log_path}")
        if not log_path.is_file():
            raise ValueError(f"Log path is not a regular file: {log_path}")
        if os.name != "nt":
            try:
                os.chmod(log_path, 0o600)
            except OSError:
                pass
    else:
        # Atomic creation with restrictive permissions (no symlink following).
        fd = os.open(str(log_path), os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    scrubber = APIScrubFilter()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(scrubber)

    file_handler = SecureRotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(scrubber)

    logging.basicConfig(
        level=logging.INFO,
        handlers=[stream_handler, file_handler],
    )


# =============================================================================
# Exceptions
# =============================================================================
class UnrecoverableTaskError(Exception):
    """Raised when a task cannot continue (e.g. missing configuration)."""


class RecoverableTaskError(Exception):
    """Raised when a transient failure allows the task to retry next cycle."""


class ActionValidationError(Exception):
    """Raised when an action dict fails schema validation."""


# =============================================================================
# ConfigResolver
# =============================================================================
class ConfigResolver:
    """Resolve and coerce configuration from a dict and optional env fallback."""

    def __init__(self, config: Dict[str, Any], use_env_fallback: bool = True):
        self.config = config or {}
        self.use_env_fallback = use_env_fallback

    def resolve(self) -> Dict[str, Any]:
        """Return a normalized configuration dict with safe defaults."""
        cfg = copy.deepcopy(self.config)

        provider = self._resolve_provider(cfg)
        cfg["provider"] = provider

        api_key = self._resolve_api_key(cfg, provider)
        cfg["api_key"] = api_key

        model = self._resolve_model(cfg)
        cfg["model"] = model

        temperature = self._float(cfg.get("temperature"), DEFAULT_TEMPERATURE)
        cfg["temperature"] = max(0.0, min(2.0, temperature))
        cfg["max_tokens"] = max(1, min(4096, self._int(cfg.get("max_tokens"), DEFAULT_MAX_TOKENS)))
        cfg["max_retries"] = max(0, min(MAX_MAX_RETRIES, self._int(cfg.get("max_retries"), DEFAULT_MAX_RETRIES)))
        cfg["max_cycles"] = max(1, min(MAX_MAX_CYCLES, self._int(cfg.get("max_cycles"), DEFAULT_MAX_CYCLES)))

        step_delay = self._float(cfg.get("step_delay"), DEFAULT_STEP_DELAY)
        if not math.isfinite(step_delay) or step_delay < 0.0:
            step_delay = DEFAULT_STEP_DELAY
        cfg["step_delay"] = max(0.0, min(MAX_STEP_DELAY, step_delay))

        cfg["max_history"] = max(1, min(MAX_MAX_HISTORY, self._int(cfg.get("max_history"), DEFAULT_MAX_HISTORY)))
        cfg["check_completion"] = bool(cfg.get("check_completion", True))
        cfg["auto_detect_resolution"] = bool(cfg.get("auto_detect_resolution", True))
        cfg.setdefault("max_api_calls", DEFAULT_MAX_API_CALLS)

        screenshot_dir = cfg.get("screenshot_dir", "./screenshots")
        screenshot_path = Path(screenshot_dir).resolve()
        allowed_base = Path("./screenshots").resolve().parent
        try:
            screenshot_path.relative_to(allowed_base)
        except ValueError as exc:
            raise ValueError(f"Invalid screenshot_dir: {screenshot_dir}") from exc
        cfg["screenshot_dir"] = str(screenshot_path)

        cfg.setdefault("screen_width", DEFAULT_SCREEN_WIDTH)
        cfg.setdefault("screen_height", DEFAULT_SCREEN_HEIGHT)

        return cfg

    def _resolve_provider(self, cfg: Dict[str, Any]) -> str:
        if cfg.get("provider"):
            return str(cfg["provider"]).casefold()
        if self.use_env_fallback:
            return os.environ.get("PROVIDER", "kimi_code").casefold()
        return "kimi_code"

    def _resolve_api_key(self, cfg: Dict[str, Any], provider: str) -> str:
        api_key = cfg.get("api_key")
        if api_key:
            return str(api_key)
        if not self.use_env_fallback:
            return ""
        # Provider-specific env var, then generic API_KEY fallback.
        env_var = API_KEY_ENV_MAP.get(provider, "API_KEY")
        api_key = os.environ.get(env_var) or os.environ.get("API_KEY", "")
        # Best-effort removal from the process environment to reduce /proc exposure.
        if env_var in os.environ:
            del os.environ[env_var]
            os.unsetenv(env_var)
        if "API_KEY" in os.environ:
            del os.environ["API_KEY"]
            os.unsetenv("API_KEY")
        return api_key

    def _resolve_model(self, cfg: Dict[str, Any]) -> Optional[str]:
        if cfg.get("model"):
            return str(cfg["model"])
        if self.use_env_fallback:
            return os.environ.get("MODEL")
        return None

    @staticmethod
    def _int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError, OverflowError):
            return default


# =============================================================================
# Action validation
# =============================================================================
class ActionValidator:
    """Validate action dicts before execution."""

    ALLOWED_ACTIONS = {"tap", "swipe", "type", "wait", "terminate"}
    ALLOWED_DIRECTIONS = {"down", "up", "left", "right"}
    # Reject common shell metacharacters, path traversal, and script tags in typed text.
    _SUSPICIOUS_TEXT_RE = re.compile(r"[;|&`$()<>\\\x00]|(?:\.\./)|(?:<\s*script)", re.IGNORECASE)

    def validate(self, action: Any) -> None:
        if not isinstance(action, dict):
            raise ActionValidationError("Action must be a dict")

        action_type = action.get("action")
        if action_type not in self.ALLOWED_ACTIONS:
            raise ActionValidationError(f"Invalid action type: {action_type}")

        if action_type == "tap":
            self._validate_coordinates(action.get("coordinates"))
        elif action_type == "swipe":
            if "coordinates" in action and "coordinate2" in action:
                self._validate_coordinates(action.get("coordinates"))
                self._validate_coordinates(action.get("coordinate2"))
            else:
                direction = action.get("direction", "down")
                if direction not in self.ALLOWED_DIRECTIONS:
                    raise ActionValidationError(f"Invalid swipe direction: {direction}")
        elif action_type == "type":
            text = action.get("text", "")
            if not isinstance(text, str):
                raise ActionValidationError("Type action text must be a string")
            if len(text) > 1000:
                raise ActionValidationError("Type action text exceeds maximum length")
            if self._SUSPICIOUS_TEXT_RE.search(text):
                raise ActionValidationError("Type text contains suspicious characters")
        elif action_type == "wait":
            wait_time = action.get("waitTime", DEFAULT_WAIT_TIME_MS)
            try:
                wait_ms = int(wait_time)
            except (ValueError, TypeError):
                raise ActionValidationError(f"Invalid waitTime: {wait_time}")
            if not (MIN_WAIT_TIME_MS <= wait_ms <= MAX_WAIT_TIME_MS):
                raise ActionValidationError(f"waitTime out of bounds: {wait_ms}")

    @staticmethod
    def _validate_coordinates(coords: Any) -> None:
        if not isinstance(coords, (list, tuple)) or len(coords) < 2:
            raise ActionValidationError(f"Invalid coordinates: {coords}")
        x_norm, y_norm = coords[0], coords[1]
        if not isinstance(x_norm, (int, float)) or not isinstance(y_norm, (int, float)):
            raise ActionValidationError(f"Coordinates must be numeric: {coords}")
        if not (0.0 <= x_norm <= 1.0 and 0.0 <= y_norm <= 1.0):
            raise ActionValidationError(f"Normalized coordinates must be in [0, 1]: {coords}")


# =============================================================================
# ActionExecutor
# =============================================================================
class ActionExecutor:
    """Execute a single action dict on the Android device."""

    def __init__(self, adb: ADBClient, screen_size: Tuple[int, int]):
        self.adb = adb
        width, height = screen_size
        self.width = max(1, min(int(width), MAX_SCREEN_DIM))
        self.height = max(1, min(int(height), MAX_SCREEN_DIM))

    def execute(self, action: Dict[str, Any]) -> bool:
        """Execute an action. Returns False when the task should terminate."""
        action_type = action.get("action")
        if action_type is None:
            logging.warning("Action missing 'action' key")
            return True

        try:
            if action_type == "tap":
                self._tap(action)
            elif action_type == "swipe":
                self._swipe(action)
            elif action_type == "type":
                self._type(action)
            elif action_type == "wait":
                self._wait(action)
            elif action_type == "terminate":
                logging.info("Task terminated: %s", action.get("message", ""))
                return False
            else:
                logging.warning("Unknown action type: %s", action_type)

        except (subprocess.CalledProcessError, OSError) as exc:
            logging.error("Action execution failed: %s", exc)
            raise RecoverableTaskError(
                f"Action {action_type} failed: {exc}"
            ) from exc

        return True

    def _tap(self, action: Dict[str, Any]) -> None:
        coords = action.get("coordinates", [0, 0])
        if not isinstance(coords, (list, tuple)) or len(coords) < 2:
            raise ValueError(f"Invalid coordinates: {coords}")
        x_norm, y_norm = coords[0], coords[1]
        if not isinstance(x_norm, (int, float)) or not isinstance(y_norm, (int, float)):
            raise ValueError(f"Coordinates must be numeric: {coords}")
        if not (0.0 <= x_norm <= 1.0 and 0.0 <= y_norm <= 1.0):
            raise ValueError(f"Normalized coordinates must be in [0, 1]: {coords}")
        try:
            x = int(x_norm * self.width)
            y = int(y_norm * self.height)
        except OverflowError as exc:
            raise ValueError("Coordinate overflow") from exc
        self.adb.tap(x, y)

    def _swipe(self, action: Dict[str, Any]) -> None:
        # Prefer explicit coordinates if provided.
        if "coordinate2" in action and "coordinates" in action:
            start = action["coordinates"]
            end = action["coordinate2"]
            if (not isinstance(start, (list, tuple)) or len(start) < 2 or
                    not isinstance(end, (list, tuple)) or len(end) < 2):
                raise ValueError("Invalid swipe coordinates")
            try:
                x1 = int(start[0] * self.width)
                y1 = int(start[1] * self.height)
                x2 = int(end[0] * self.width)
                y2 = int(end[1] * self.height)
            except OverflowError as exc:
                raise ValueError("Coordinate overflow") from exc
            self.adb.swipe(x1, y1, x2, y2)
        else:
            direction = action.get("direction", "down")
            if direction not in {"down", "up", "left", "right"}:
                raise ValueError(f"Invalid swipe direction: {direction}")
            self._swipe_direction(direction)

    def _swipe_direction(self, direction: str) -> None:
        width, height = self.width, self.height
        if direction == "down":
            self.adb.swipe(width // 2, height * 2 // 3, width // 2, height // 3)
        elif direction == "up":
            self.adb.swipe(width // 2, height // 3, width // 2, height * 2 // 3)
        elif direction == "right":
            self.adb.swipe(width * 2 // 3, height // 2, width // 3, height // 2)
        elif direction == "left":
            self.adb.swipe(width // 3, height // 2, width * 2 // 3, height // 2)
        else:
            logging.warning("Unknown swipe direction: %s", direction)

    def _type(self, action: Dict[str, Any]) -> None:
        text = action.get("text", "")
        if not isinstance(text, str):
            text = str(text)
        self.adb.type_text(text)

    def _wait(self, action: Dict[str, Any]) -> None:
        wait_time = action.get("waitTime", DEFAULT_WAIT_TIME_MS)
        try:
            wait_ms = int(wait_time)
        except (ValueError, TypeError):
            wait_ms = DEFAULT_WAIT_TIME_MS
        wait_ms = max(MIN_WAIT_TIME_MS, min(wait_ms, MAX_WAIT_TIME_MS))
        time.sleep(wait_ms / 1000)


# =============================================================================
# TaskOrchestrator
# =============================================================================
class TaskOrchestrator:
    """Run the screenshot -> analyze -> execute task loop."""

    def __init__(
        self,
        adb: ADBClient,
        screenshot: ScreenshotCapture,
        provider: BaseProvider,
        config: Dict[str, Any],
    ):
        self.adb = adb
        self.screenshot = screenshot
        self.provider = provider
        self.config = config
        self.max_cycles = config["max_cycles"]
        self.step_delay = config["step_delay"]
        self.check_completion = config["check_completion"]
        self.max_history = config["max_history"]
        self.max_api_calls = max(1, int(config.get("max_api_calls", DEFAULT_MAX_API_CALLS)))
        self._api_calls = 0
        self._per_cycle_timeout = float(config.get("per_cycle_timeout_seconds", 45.0))
        self.previous_actions: List[Dict[str, Any]] = []
        self.executor = ActionExecutor(
            adb, (config["screen_width"], config["screen_height"])
        )
        self._validator = ActionValidator()

    # Dangerous task keywords that require explicit operator override.
    _DANGEROUS_KEYWORDS = {"factory reset", "erase all", "uninstall all", "wipe data"}

    def run(self, task: str) -> Dict[str, Any]:
        """Execute a task and return the result dict."""
        logging.info("Starting task (length=%d, description redacted)", len(task))
        self.previous_actions.clear()
        self._api_calls = 0

        # Block clearly destructive task descriptions unless explicitly allowed.
        task_lower = task.lower()
        if (
            any(kw in task_lower for kw in self._DANGEROUS_KEYWORDS)
            and not self.config.get("allow_dangerous_tasks", False)
        ):
            return {
                "success": False,
                "message": "Task blocked: dangerous keyword detected. Set allow_dangerous_tasks=true to override.",
            }

        try:
            calls_per_cycle = 2 if self.check_completion else 1
            for cycle in range(1, self.max_cycles + 1):
                logging.info("--- Cycle %d/%d ---", cycle, self.max_cycles)
                cycle_start = time.monotonic()
                cycle_deadline = cycle_start + self._per_cycle_timeout

                if self._api_calls + calls_per_cycle > self.max_api_calls:
                    return {"success": False, "message": "Budget exceeded (max_api_calls)"}

                if not self.adb.is_connected():
                    logging.error("Device disconnected")
                    return {"success": False, "message": "Device disconnected"}

                screenshot_path = self.screenshot.capture_with_resize()
                if not screenshot_path:
                    logging.error("Failed to capture screenshot")
                    time.sleep(self.step_delay * 2)
                    continue

                # Completion checks are only trusted after at least one action cycle.
                if (
                    self.check_completion
                    and cycle >= 2
                    and self._check_completion(screenshot_path, task)
                ):
                    return {"success": True, "message": "Task completed"}
                self._api_calls += 1

                if time.monotonic() > cycle_deadline:
                    return {"success": False, "message": "Per-cycle timeout exceeded"}

                context = {"previous_actions": self.previous_actions}
                action = self.provider.analyze_screenshot(screenshot_path, task, context)
                self._api_calls += 1

                if not action:
                    logging.error("Failed to get action from model")
                    time.sleep(self.step_delay)
                    continue

                if time.monotonic() > cycle_deadline:
                    return {"success": False, "message": "Per-cycle timeout exceeded"}

                self._validator.validate(action)
                self._record_action(action)
                if self._detect_action_loop():
                    return {"success": False, "message": "Action loop detected"}

                try:
                    should_continue = self._execute_action(action)
                except RecoverableTaskError as exc:
                    logging.warning("Recoverable error in action, retrying after delay: %s", exc)
                    time.sleep(self.step_delay * 2)
                    continue
                if not should_continue:
                    return {"success": True, "message": "Task completed"}

                time.sleep(self.step_delay)

                # Periodic cleanup to avoid unbounded disk use during long tasks.
                if cycle % 10 == 0:
                    try:
                        self.screenshot.cleanup_old(keep_count=50)
                    except Exception as exc:
                        logging.warning("Periodic screenshot cleanup failed: %s", exc)

            return {
                "success": False,
                "message": f"Max cycles ({self.max_cycles}) reached",
            }
        finally:
            self.screenshot.cleanup_old(keep_count=50)

    def _execute_action(self, action: Dict[str, Any]) -> bool:
        return self.executor.execute(action)

    def _record_action(self, action: Dict[str, Any]) -> None:
        # Store a redacted copy so typed passwords don't linger in memory.
        safe_action = dict(action)
        if safe_action.get("action") == "type" and "text" in safe_action:
            safe_action["text"] = "[REDACTED]"
        self.previous_actions.append(safe_action)
        if len(self.previous_actions) > self.max_history:
            self.previous_actions = self.previous_actions[-self.max_history :]

    def _detect_action_loop(self) -> bool:
        """Detect repeated identical actions or short action cycles."""
        if len(self.previous_actions) < 5:
            return False
        recent = self.previous_actions[-5:]
        if all(a == recent[0] for a in recent):
            return True
        for cycle_len in range(2, 5):
            if len(self.previous_actions) >= cycle_len * 2:
                tail = self.previous_actions[-cycle_len * 2:]
                if tail[:cycle_len] == tail[cycle_len:]:
                    return True
        return False

    def _check_completion(self, screenshot_path: str, task: str) -> bool:
        try:
            context = {"previous_actions": self.previous_actions}
            result = self.provider.check_task_completion(screenshot_path, task, context)
            if result.get("complete"):
                logging.info("Task completion detected: %s", result.get("reason", ""))
                return True
        except (OSError, ValueError, TypeError) as exc:
            logging.warning("Completion check failed: %s", exc)
        return False


# =============================================================================
# PhoneAgent (facade)
# =============================================================================
class PhoneAgent:
    """Main phone automation agent — thin facade over focused components."""

    def __init__(self, config: Dict[str, Any]):
        resolver = ConfigResolver(config)
        self.config = resolver.resolve()

        self.adb = ADBClient()
        self.screenshot = ScreenshotCapture(self.adb, self.config["screenshot_dir"])
        self.vl_agent = self._create_provider()
        # Let the provider coordinate screenshot read references with cleanup.
        self.vl_agent._screenshot_capture = self.screenshot
        self._init_screen_resolution()

        self._orchestrator = TaskOrchestrator(
            self.adb,
            self.screenshot,
            self.vl_agent,
            self.config,
        )

    def _create_provider(self) -> BaseProvider:
        """Create the vision-language provider from the resolved config."""
        provider_name = self.config["provider"]
        api_key = self.config["api_key"]
        if not api_key:
            raise ValueError(
                f"API key not found for provider: {provider_name}. "
                f"Please set it in config or via the appropriate environment variable."
            )

        logging.info("Initializing provider: %s", provider_name)
        return get_provider(
            provider_name,
            api_key=api_key,
            model=self.config.get("model"),
            temperature=self.config["temperature"],
            max_tokens=self.config["max_tokens"],
            max_retries=self.config["max_retries"],
        )

    def _init_screen_resolution(self) -> None:
        """Auto-detect screen resolution unless disabled or already provided."""
        if not self.config.get("auto_detect_resolution"):
            return
        try:
            width, height = self.adb.get_screen_size()
            self.config["screen_width"] = width
            self.config["screen_height"] = height
            logging.info("Auto-detected screen resolution: %dx%d", width, height)
        except Exception as exc:
            logging.warning("Failed to auto-detect resolution: %s", exc)

    def execute_task(self, task: str) -> Dict[str, Any]:
        """Execute a task and return the result."""
        return self._orchestrator.run(task)

    def cleanup(self) -> None:
        """Release all held resources and clear sensitive state."""
        if self.vl_agent and hasattr(self.vl_agent, 'close'):
            try:
                self.vl_agent.close()
            except Exception as exc:
                logging.warning("Provider cleanup failed: %s", exc)

        try:
            self.screenshot.cleanup_old(keep_count=50)
        except Exception as exc:
            logging.warning("Screenshot cleanup failed: %s", exc)

        if self._orchestrator:
            self._orchestrator.previous_actions.clear()

        # Best-effort clear of API key from config
        self.config.pop("api_key", None)


# =============================================================================
# Config loading helpers
# =============================================================================
def _validate_config_path(config_path: Path) -> None:
    """Validate that the config path is safe to load."""
    if not config_path.exists():
        return

    if config_path.is_symlink():
        raise ValueError(f"Config path is a symlink: {config_path}")
    if not config_path.is_file():
        raise ValueError(f"Config path is not a regular file: {config_path}")

    try:
        size = config_path.stat().st_size
    except OSError as exc:
        raise ValueError(f"Cannot stat config file: {config_path}") from exc
    if size > MAX_CONFIG_SIZE:
        raise ValueError(f"Config file too large: {size} bytes")

    st = config_path.stat()
    if st.st_uid != os.getuid():
        raise PermissionError(f"Config file must be owned by current user: {config_path}")
    if stat.S_IWOTH & st.st_mode:
        raise PermissionError(f"Config file must not be world-writable: {config_path}")
    if stat.S_IROTH & st.st_mode:
        raise PermissionError(f"Config file must not be world-readable: {config_path}")


def _load_config(config_path: Path) -> Dict[str, Any]:
    """Load and validate config file."""
    _validate_config_path(config_path)
    if not config_path.exists():
        logging.warning("Config file not found: %s, using defaults", config_path)
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        if not isinstance(config, dict):
            logging.error("Config file must contain a JSON object")
            sys.exit(1)
        return config
    except json.JSONDecodeError as exc:
        logging.error("Failed to parse config file: %s", exc)
        sys.exit(1)
    except OSError as exc:
        logging.error("Failed to read config file: %s", exc)
        sys.exit(1)


# =============================================================================
# CLI entry point
# =============================================================================
def _validate_dotenv_permissions(dotenv_path: Path) -> None:
    """Reject world-readable or world-writable .env files."""
    if not dotenv_path.exists():
        return
    st = dotenv_path.stat()
    if stat.S_IROTH & st.st_mode:
        raise PermissionError(f".env file must not be world-readable: {dotenv_path}")
    if stat.S_IWOTH & st.st_mode:
        raise PermissionError(f".env file must not be world-writable: {dotenv_path}")


def _require_non_root() -> None:
    """Refuse to run as root unless explicitly allowed."""
    if not hasattr(os, "geteuid"):
        return
    if os.geteuid() == 0 and os.environ.get("PHONEDRIVER_ALLOW_ROOT") != "1":
        print(
            "Running as root is not allowed. Set PHONEDRIVER_ALLOW_ROOT=1 to override.",
            file=sys.stderr,
        )
        sys.exit(1)


def _acquire_instance_lock() -> Any:
    """Acquire a cross-process lock to prevent concurrent CLI instances."""
    lock_path = Path(tempfile.gettempdir()) / "phone_agent.lock"
    try:
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except (BlockingIOError, OSError):
        logging.error("Another PhoneDriver instance is already running.")
        sys.exit(1)


def main() -> None:
    _require_non_root()
    lock_file = _acquire_instance_lock()

    try:
        # Load environment variables only at CLI entry point
        dotenv_path = Path(__file__).resolve().parent / '.env'
        _validate_dotenv_permissions(dotenv_path)
        if dotenv_path.exists():
            load_dotenv(dotenv_path=dotenv_path, override=False)

        _setup_logging()

        parser = argparse.ArgumentParser(description="PhoneDriver API - Mobile Automation")
        parser.add_argument("task", help='Task description (e.g., "Open Settings")')
        parser.add_argument("--config", default="config.json", help="Config file path")
        args = parser.parse_args()

        config_path = Path(args.config).resolve()
        allowed_base = Path.cwd().resolve()
        try:
            config_path.relative_to(allowed_base)
        except ValueError as exc:
            logging.error("Config path must be inside the current working directory: %s", config_path)
            sys.exit(1)

        config = _load_config(config_path)

        agent: Optional[PhoneAgent] = None
        try:
            agent = PhoneAgent(config)
            result = agent.execute_task(args.task)

            if result["success"]:
                logging.info("Task completed successfully: %s", result["message"])
            else:
                logging.error("Task failed: %s", result["message"])
                sys.exit(1)

        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            safe_msg = re.sub(r"key=[a-zA-Z0-9_\-\.]+", "key=<REDACTED>", str(exc))
            logging.error("Fatal error: %s", safe_msg)
            sys.exit(1)
        finally:
            if agent is not None:
                agent.cleanup()
    finally:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
