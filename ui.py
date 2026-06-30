"""
PhoneDriver API - Web UI using Gradio.

Updated to work with the refactored PhoneAgent: provider, API key and model are
passed through the config dict instead of being written to the global process
environment.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore

import gradio as gr
from dotenv import load_dotenv

from phone_agent import API_KEY_ENV_MAP, APIScrubFilter, PhoneAgent
from providers import available_providers


# Configure module logger with API key scrubbing
logger = logging.getLogger(__name__)
logger.addFilter(APIScrubFilter())


# =============================================================================
# Constants
# =============================================================================
CONFIG_PATH = Path("config.json")
DEFAULT_PROVIDER = "kimi_code"
DEFAULT_MODEL = "kimi-for-coding"
DEFAULT_TEMPERATURE = 0.1
DEFAULT_MAX_TOKENS = 512
DEFAULT_MAX_CYCLES = 15
DEFAULT_STEP_DELAY = 1.5

# Prevent multiple concurrent tasks from operating the same physical device.
_device_lock = threading.Lock()
_config_lock = threading.Lock()


# =============================================================================
# Configuration I/O
# =============================================================================
def load_config() -> Dict[str, Any]:
    """Load configuration from disk."""
    with _config_lock:
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    if fcntl is not None:
                        fcntl.flock(f, fcntl.LOCK_SH)
                    try:
                        data = json.load(f)
                    finally:
                        if fcntl is not None:
                            fcntl.flock(f, fcntl.LOCK_UN)
                if isinstance(data, dict):
                    return data
                logger.warning("Config file is not a JSON object; using defaults.")
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Config load failed: %s", exc)
        return {}


def save_config(config: Dict[str, Any]) -> None:
    """Save configuration to disk, stripping sensitive fields."""
    safe_config = {k: v for k, v in config.items() if k != "api_key"}
    with _config_lock:
        fd, tmp_path = tempfile.mkstemp(dir=CONFIG_PATH.parent, suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(safe_config, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, CONFIG_PATH)
        except OSError as exc:
            logger.warning("Config save failed: %s", exc)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# =============================================================================
# Input validation
# =============================================================================
def _validate_inputs(
    task: Any,
    temperature: Any,
    max_tokens: Any,
    max_cycles: Any,
    step_delay: Any,
) -> str | None:
    """Validate user inputs. Returns an error message or None if valid."""
    if not task or not str(task).strip():
        return "Error: Task description is required."

    if len(str(task)) > 500:
        return "Error: Task description must not exceed 500 characters."

    try:
        temp = float(temperature)
    except (ValueError, TypeError):
        return "Error: Temperature must be a number between 0 and 1."
    if not (0.0 <= temp <= 1.0):
        return "Error: Temperature must be between 0 and 1."

    try:
        tokens = int(max_tokens)
    except (ValueError, TypeError):
        return "Error: Max Tokens must be an integer between 256 and 2048."
    if not (256 <= tokens <= 2048):
        return "Error: Max Tokens must be between 256 and 2048."

    try:
        cycles = int(max_cycles)
    except (ValueError, TypeError):
        return "Error: Max Cycles must be an integer between 1 and 50."
    if not (1 <= cycles <= 50):
        return "Error: Max Cycles must be between 1 and 50."

    try:
        delay = float(step_delay)
    except (ValueError, TypeError):
        return "Error: Step Delay must be a non-negative number."
    if delay < 0:
        return "Error: Step Delay must be non-negative."

    return None


# =============================================================================
# Config builder
# =============================================================================
def _build_config(
    provider: str,
    api_key: str,
    model: str,
    temperature: Any,
    max_tokens: Any,
    max_cycles: Any,
    step_delay: Any,
) -> Dict[str, Any]:
    """Build a configuration dict for PhoneAgent."""
    base = load_config()

    base["provider"] = provider
    base["api_key"] = api_key
    base["model"] = model
    base["temperature"] = float(temperature)
    base["max_tokens"] = int(max_tokens)
    base["max_cycles"] = int(max_cycles)
    base["step_delay"] = float(step_delay)
    base["auto_detect_resolution"] = True
    base["check_completion"] = True

    return base


def _resolve_api_key(provider: str, ui_key: str) -> str:
    """Prefer environment variable API keys over UI-supplied keys."""
    env_var = API_KEY_ENV_MAP.get(provider, "API_KEY")
    env_key = os.environ.get(env_var) or os.environ.get("API_KEY", "")
    if env_key:
        return env_key
    return ui_key


# =============================================================================
# Task execution
# =============================================================================
def _execute_task(config: Dict[str, Any], task: str) -> str:
    """Instantiate PhoneAgent and execute the task."""
    config["api_key"] = _resolve_api_key(config.get("provider", ""), config.get("api_key", ""))
    agent = PhoneAgent(config)
    try:
        result = agent.execute_task(task)
        return f"Result: {result['message']}"
    finally:
        cleanup = getattr(agent, "cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                logger.exception("Agent cleanup failed")


def run_task(
    task: Any,
    provider: Any,
    api_key: Any,
    model: Any,
    temperature: Any,
    max_tokens: Any,
    max_cycles: Any,
    step_delay: Any,
) -> str:
    """Gradio click handler."""
    validation_error = _validate_inputs(
        task, temperature, max_tokens, max_cycles, step_delay
    )
    if validation_error:
        return validation_error

    config = _build_config(
        str(provider),
        str(api_key),
        str(model),
        temperature,
        max_tokens,
        max_cycles,
        step_delay,
    )

    acquired = _device_lock.acquire(timeout=300)
    if not acquired:
        return "Error: Another task is running and the device is locked."
    try:
        return _execute_task(config, str(task))
    except Exception as exc:
        safe_msg = re.sub(r"key=[a-zA-Z0-9_\-\.]+", "key=<REDACTED>", str(exc))
        logger.error("Task execution failed: %s", safe_msg)
        return f"Error: {type(exc).__name__}: {safe_msg}"
    finally:
        _device_lock.release()


# =============================================================================
# Gradio UI factory
# =============================================================================
def build_ui() -> gr.Blocks:
    """Build and return the Gradio UI."""
    with gr.Blocks(title="PhoneDriver API") as demo:
        gr.Markdown("# PhoneDriver API")
        gr.Markdown("Mobile automation using cloud vision models")

        with gr.Row():
            with gr.Column():
                gr.Markdown("### Configuration")

                provider = gr.Dropdown(
                    choices=available_providers(),
                    value=DEFAULT_PROVIDER,
                    label="Provider",
                )

                api_key = gr.Textbox(
                    label="API Key",
                    type="password",
                    placeholder="Enter your API key",
                )

                model = gr.Textbox(
                    label="Model",
                    value=DEFAULT_MODEL,
                    placeholder="e.g., kimi-for-coding, gpt-4o",
                )

                temperature = gr.Slider(
                    minimum=0.0,
                    maximum=1.0,
                    value=DEFAULT_TEMPERATURE,
                    step=0.1,
                    label="Temperature",
                )

                max_tokens = gr.Slider(
                    minimum=256,
                    maximum=2048,
                    value=DEFAULT_MAX_TOKENS,
                    step=64,
                    label="Max Tokens",
                )

                max_cycles = gr.Slider(
                    minimum=1,
                    maximum=50,
                    value=DEFAULT_MAX_CYCLES,
                    step=1,
                    label="Max Cycles",
                )

                step_delay = gr.Slider(
                    minimum=0.0,
                    maximum=10.0,
                    value=DEFAULT_STEP_DELAY,
                    step=0.5,
                    label="Step Delay (seconds)",
                )

            with gr.Column():
                gr.Markdown("### Task Execution")

                task = gr.Textbox(
                    label="Task",
                    placeholder="e.g., Open Settings, Search for...",
                    lines=3,
                )

                run_btn = gr.Button("Run Task", variant="primary")

                output = gr.Textbox(
                    label="Output",
                    lines=10,
                    interactive=False,
                )

        gr.Markdown("""
        ### Quick Start
        1. Select your provider
        2. Enter your API key (or set it via environment variable)
        3. Describe the task
        4. Click "Run Task"

        ### Security Notice
        - For production use, set API keys via environment variables instead of typing them here.
        - Screenshots are sent to cloud AI providers. Avoid tasks while sensitive information is visible.
        - Enable USB debugging only when actively using this tool and disable it afterward.

        ### Provider Setup
        - **Kimi Code**: Get key from https://kimi.com/code/console
        - **OpenRouter**: Get key from https://openrouter.ai
        - **OpenAI**: Get key from https://platform.openai.com
        - **Moonshot**: Get key from https://platform.moonshot.cn
        """)

        run_btn.click(
            fn=run_task,
            inputs=[
                task,
                provider,
                api_key,
                model,
                temperature,
                max_tokens,
                max_cycles,
                step_delay,
            ],
            outputs=output,
            concurrency_limit=1,
        )

    return demo


# =============================================================================
# Module-level UI (kept for backward compatibility, built lazily via factory)
# =============================================================================
demo = build_ui()


if __name__ == "__main__":
    # Load environment variables only at UI entry point
    dotenv_path = Path(__file__).resolve().parent / '.env'
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path, override=False)

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
    for handler in logging.getLogger().handlers:
        handler.addFilter(APIScrubFilter())

    auth_user = os.environ.get("PHONEDRIVER_USER")
    auth_pass = os.environ.get("PHONEDRIVER_PASS")
    auth = (auth_user, auth_pass) if auth_user and auth_pass else None

    demo.launch(
        server_name="127.0.0.1",
        server_port=int(os.environ.get("PHONEDRIVER_PORT", "7860")),
        show_api=False,
        auth=auth,
    )
