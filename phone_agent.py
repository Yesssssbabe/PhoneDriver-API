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
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

from providers import get_provider
from providers.base import BaseProvider
from utils.adb import ADBClient
from utils.screenshot import ScreenshotCapture

# Load environment variables
load_dotenv()


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

API_KEY_ENV_MAP: Dict[str, str] = {
    "kimi_code": "KIMI_CODE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
}


# =============================================================================
# Logging
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("phone_agent.log", encoding="utf-8"),
    ],
)


# =============================================================================
# Exceptions
# =============================================================================
class UnrecoverableTaskError(Exception):
    """Raised when a task cannot continue (e.g. missing configuration)."""


class RecoverableTaskError(Exception):
    """Raised when a transient failure allows the task to retry next cycle."""


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
        cfg = dict(self.config)

        provider = self._resolve_provider(cfg)
        cfg["provider"] = provider

        api_key = self._resolve_api_key(cfg, provider)
        cfg["api_key"] = api_key

        model = self._resolve_model(cfg)
        cfg["model"] = model

        cfg["temperature"] = self._float(cfg.get("temperature"), DEFAULT_TEMPERATURE)
        cfg["max_tokens"] = max(1, self._int(cfg.get("max_tokens"), DEFAULT_MAX_TOKENS))
        cfg["max_retries"] = max(0, self._int(cfg.get("max_retries"), DEFAULT_MAX_RETRIES))
        cfg["max_cycles"] = max(1, self._int(cfg.get("max_cycles"), DEFAULT_MAX_CYCLES))
        cfg["step_delay"] = max(0.0, self._float(cfg.get("step_delay"), DEFAULT_STEP_DELAY))
        cfg["max_history"] = max(1, self._int(cfg.get("max_history"), DEFAULT_MAX_HISTORY))
        cfg["check_completion"] = bool(cfg.get("check_completion", True))
        cfg["auto_detect_resolution"] = bool(cfg.get("auto_detect_resolution", True))
        cfg["screenshot_dir"] = cfg.get("screenshot_dir", "./screenshots")

        cfg.setdefault("screen_width", DEFAULT_SCREEN_WIDTH)
        cfg.setdefault("screen_height", DEFAULT_SCREEN_HEIGHT)

        return cfg

    def _resolve_provider(self, cfg: Dict[str, Any]) -> str:
        if cfg.get("provider"):
            return str(cfg["provider"]).lower()
        if self.use_env_fallback:
            return os.environ.get("PROVIDER", "kimi_code").lower()
        return "kimi_code"

    def _resolve_api_key(self, cfg: Dict[str, Any], provider: str) -> str:
        api_key = cfg.get("api_key")
        if api_key:
            return str(api_key)
        if not self.use_env_fallback:
            return ""
        # Provider-specific env var, then generic API_KEY fallback.
        env_var = API_KEY_ENV_MAP.get(provider, "API_KEY")
        return os.environ.get(env_var) or os.environ.get("API_KEY", "")

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
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


# =============================================================================
# ActionExecutor
# =============================================================================
class ActionExecutor:
    """Execute a single action dict on the Android device."""

    def __init__(self, adb: ADBClient, screen_size: Tuple[int, int]):
        self.adb = adb
        self.width, self.height = screen_size

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

        except Exception as exc:
            logging.error("Action execution failed: %s", exc)
            raise RecoverableTaskError(
                f"Action {action_type} failed: {exc}"
            ) from exc

        return True

    def _tap(self, action: Dict[str, Any]) -> None:
        coords = action.get("coordinates", [0, 0])
        x = int(coords[0] * self.width)
        y = int(coords[1] * self.height)
        self.adb.tap(x, y)

    def _swipe(self, action: Dict[str, Any]) -> None:
        # Prefer explicit coordinates if provided.
        if "coordinate2" in action and "coordinates" in action:
            start = action["coordinates"]
            end = action["coordinate2"]
            x1 = int(start[0] * self.width)
            y1 = int(start[1] * self.height)
            x2 = int(end[0] * self.width)
            y2 = int(end[1] * self.height)
            self.adb.swipe(x1, y1, x2, y2)
        else:
            direction = action.get("direction", "down")
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
        self.adb.type_text(text)

    def _wait(self, action: Dict[str, Any]) -> None:
        wait_time = action.get("waitTime", DEFAULT_WAIT_TIME_MS)
        time.sleep(int(wait_time) / 1000)


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
        self.previous_actions: List[Dict[str, Any]] = []

    def run(self, task: str) -> Dict[str, Any]:
        """Execute a task and return the result dict."""
        logging.info("Starting task: %s", task)
        self.previous_actions.clear()

        for cycle in range(1, self.max_cycles + 1):
            logging.info("--- Cycle %d/%d ---", cycle, self.max_cycles)

            if not self.adb.is_connected():
                logging.error("Device disconnected")
                return {"success": False, "message": "Device disconnected"}

            screenshot_path = self.screenshot.capture_with_resize()
            if not screenshot_path:
                logging.error("Failed to capture screenshot")
                time.sleep(self.step_delay * 2)
                continue

            if self.check_completion and self._check_completion(screenshot_path, task):
                return {"success": True, "message": "Task completed"}

            context = {"previous_actions": self.previous_actions}
            action = self.provider.analyze_screenshot(screenshot_path, task, context)

            if not action:
                logging.error("Failed to get action from model")
                time.sleep(self.step_delay)
                continue

            self._record_action(action)
            should_continue = self._execute_action(action)
            if not should_continue:
                return {"success": True, "message": "Task completed"}

            time.sleep(self.step_delay)

        return {
            "success": False,
            "message": f"Max cycles ({self.max_cycles}) reached",
        }

    def _execute_action(self, action: Dict[str, Any]) -> bool:
        executor = ActionExecutor(
            self.adb,
            (self.config["screen_width"], self.config["screen_height"]),
        )
        return executor.execute(action)

    def _record_action(self, action: Dict[str, Any]) -> None:
        self.previous_actions.append(action)
        if len(self.previous_actions) > self.max_history:
            self.previous_actions = self.previous_actions[-self.max_history :]

    def _check_completion(self, screenshot_path: str, task: str) -> bool:
        try:
            context = {"previous_actions": self.previous_actions}
            result = self.provider.check_task_completion(screenshot_path, task, context)
            if result.get("complete"):
                logging.info("Task completion detected: %s", result.get("reason", ""))
                return True
        except Exception as exc:
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


# =============================================================================
# CLI entry point
# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="PhoneDriver API - Mobile Automation")
    parser.add_argument("task", help='Task description (e.g., "Open Settings")')
    parser.add_argument("--config", default="config.json", help="Config file path")
    args = parser.parse_args()

    config_path = Path(args.config)
    config: Dict[str, Any] = {}
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                logging.error("Config file must contain a JSON object")
                sys.exit(1)
        except json.JSONDecodeError as exc:
            logging.error("Failed to parse config file: %s", exc)
            sys.exit(1)
        except OSError as exc:
            logging.error("Failed to read config file: %s", exc)
            sys.exit(1)
    else:
        logging.warning("Config file not found: %s, using defaults", config_path)

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
        logging.exception("Fatal error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
