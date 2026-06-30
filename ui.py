"""
PhoneDriver API - Web UI using Gradio.

Updated to work with the refactored PhoneAgent: provider, API key and model are
passed through the config dict instead of being written to the global process
environment.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Dict

import gradio as gr
from dotenv import load_dotenv

from phone_agent import PhoneAgent
from providers import available_providers

load_dotenv()

# Configure logging only if not already configured
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

logger = logging.getLogger(__name__)


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


# =============================================================================
# Configuration I/O
# =============================================================================
def load_config() -> Dict[str, Any]:
    """Load configuration from disk."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            logger.warning("Config file is not a JSON object; using defaults.")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Config load failed: %s", exc)
    return {}


def save_config(config: Dict[str, Any]) -> None:
    """Save configuration to disk, stripping sensitive fields."""
    safe_config = {k: v for k, v in config.items() if k != "api_key"}
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(safe_config, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Config save failed: %s", exc)


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


# =============================================================================
# Task execution
# =============================================================================
def _execute_task(config: Dict[str, Any], task: str) -> str:
    """Instantiate PhoneAgent and execute the task."""
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

    try:
        with _device_lock:
            return _execute_task(config, str(task))
    except Exception:
        logger.exception("Task execution failed")
        return "Error: Task execution failed. Please check logs or try again."


# =============================================================================
# Gradio UI
# =============================================================================
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
    2. Enter your API key
    3. Describe the task
    4. Click "Run Task"

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


if __name__ == "__main__":
    demo.launch(
        server_name="127.0.0.1",
        show_api=False,
    )
