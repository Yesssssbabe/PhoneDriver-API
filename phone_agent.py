#!/usr/bin/env python3
"""
PhoneDriver API - Mobile automation using cloud vision models.

Usage:
    python phone_agent.py "Open Settings"
    python phone_agent.py "Search for weather in New York"
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from providers import get_provider
from utils.adb import ADBClient
from utils.screenshot import ScreenshotCapture


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('phone_agent.log', encoding='utf-8')
    ]
)


class PhoneAgent:
    """Main phone automation agent."""

    def __init__(self, config: dict):
        self.config = config
        self.adb = ADBClient()
        self.screenshot = ScreenshotCapture(
            self.adb,
            config.get('screenshot_dir', './screenshots')
        )
        self.vl_agent = self._create_provider()
        self.previous_actions = []

        # Auto-detect screen resolution if enabled or missing
        self._init_screen_resolution()

    def _init_screen_resolution(self):
        """Initialize screen resolution from config or ADB."""
        auto_detect = self.config.get('auto_detect_resolution', True)
        has_resolution = (
            'screen_width' in self.config and 'screen_height' in self.config
        )

        if auto_detect or not has_resolution:
            try:
                width, height = self.adb.get_screen_size()
                self.config['screen_width'] = width
                self.config['screen_height'] = height
                logging.info(f"Auto-detected screen resolution: {width}x{height}")
            except Exception as e:
                logging.warning(f"Failed to auto-detect resolution: {e}")
                if not has_resolution:
                    logging.warning("Using default resolution 1080x2340")
                    self.config['screen_width'] = 1080
                    self.config['screen_height'] = 2340

    def _create_provider(self):
        """Create vision-language provider from environment."""
        provider_name = os.environ.get('PROVIDER', 'kimi_code').lower()

        provider_config = {
            'temperature': float(self.config.get('temperature', 0.1)),
            'max_tokens': int(self.config.get('max_tokens', 512)),
            'max_retries': int(self.config.get('max_retries', 3)),
        }

        # Get API key based on provider
        if provider_name == 'kimi_code':
            api_key = os.environ.get('KIMI_CODE_API_KEY')
            provider_config['model'] = os.environ.get('MODEL', 'kimi-for-coding')
        elif provider_name == 'openrouter':
            api_key = os.environ.get('OPENROUTER_API_KEY')
            provider_config['model'] = os.environ.get('MODEL', 'moonshotai/kimi-k2.5')
        elif provider_name == 'openai':
            api_key = os.environ.get('OPENAI_API_KEY')
            provider_config['model'] = os.environ.get('MODEL', 'gpt-4o')
        elif provider_name == 'moonshot':
            api_key = os.environ.get('MOONSHOT_API_KEY')
            provider_config['model'] = os.environ.get('MODEL', 'kimi-k2.5')
        else:
            raise ValueError(f"Unknown provider: {provider_name}")

        if not api_key:
            raise ValueError(
                f"API key not found for provider: {provider_name}. "
                f"Please set the appropriate environment variable in .env file."
            )

        logging.info(f"Initializing provider: {provider_name}")
        return get_provider(provider_name, api_key=api_key, **provider_config)

    def execute_action(self, action: dict) -> bool:
        """Execute an action on the device."""
        action_type = action.get('action')
        width = self.config['screen_width']
        height = self.config['screen_height']

        try:
            if action_type == 'tap':
                coords = action.get('coordinates', [0, 0])
                x = int(coords[0] * width)
                y = int(coords[1] * height)
                self.adb.tap(x, y)

            elif action_type == 'swipe':
                # Prefer explicit coordinates if provided
                if 'coordinate2' in action and 'coordinates' in action:
                    start = action['coordinates']
                    end = action['coordinate2']
                    x1 = int(start[0] * width)
                    y1 = int(start[1] * height)
                    x2 = int(end[0] * width)
                    y2 = int(end[1] * height)
                    self.adb.swipe(x1, y1, x2, y2)
                else:
                    direction = action.get('direction', 'down')
                    if direction == 'down':
                        self.adb.swipe(width // 2, height * 2 // 3, width // 2, height // 3)
                    elif direction == 'up':
                        self.adb.swipe(width // 2, height // 3, width // 2, height * 2 // 3)
                    elif direction == 'right':
                        self.adb.swipe(width * 2 // 3, height // 2, width // 3, height // 2)
                    elif direction == 'left':
                        self.adb.swipe(width // 3, height // 2, width * 2 // 3, height // 2)

            elif action_type == 'type':
                text = action.get('text', '')
                self.adb.type_text(text)

            elif action_type == 'wait':
                wait_time = action.get('waitTime', 1000)
                time.sleep(wait_time / 1000)

            elif action_type == 'terminate':
                logging.info(f"Task terminated: {action.get('message', '')}")
                return False

            else:
                logging.warning(f"Unknown action type: {action_type}")

        except Exception as e:
            logging.error(f"Action execution failed: {e}")
            return False

        return True

    def _record_action(self, action: dict):
        """Record action in history with a max length."""
        self.previous_actions.append(action)
        max_history = self.config.get('max_history', 20)
        if len(self.previous_actions) > max_history:
            self.previous_actions = self.previous_actions[-max_history:]

    def _check_completion(self, screenshot_path: str, task: str) -> bool:
        """Check if the task appears to be complete."""
        try:
            context = {'previous_actions': self.previous_actions}
            result = self.vl_agent.check_task_completion(screenshot_path, task, context)
            if result.get('complete'):
                logging.info(f"Task completion detected: {result.get('reason', '')}")
                return True
        except Exception as e:
            logging.warning(f"Completion check failed: {e}")
        return False

    def execute_task(self, task: str) -> dict:
        """Execute a task and return result."""
        logging.info(f"Starting task: {task}")

        max_cycles = self.config.get('max_cycles', 15)
        step_delay = self.config.get('step_delay', 1.5)
        check_completion = self.config.get('check_completion', True)

        for cycle in range(1, max_cycles + 1):
            logging.info(f"\n--- Cycle {cycle}/{max_cycles} ---")

            # Capture screenshot
            screenshot_path = self.screenshot.capture_with_resize()
            if not screenshot_path:
                logging.error("Failed to capture screenshot")
                continue

            # Optionally check completion before taking another action
            if check_completion and cycle > 1 and self._check_completion(screenshot_path, task):
                return {'success': True, 'message': 'Task completed'}

            # Get action from model
            context = {'previous_actions': self.previous_actions}
            action = self.vl_agent.analyze_screenshot(screenshot_path, task, context)

            if not action:
                logging.error("Failed to get action from model")
                time.sleep(step_delay)
                continue

            # Record action
            self._record_action(action)

            # Execute action
            should_continue = self.execute_action(action)
            if not should_continue:
                return {'success': True, 'message': 'Task completed'}

            # Wait before next action
            time.sleep(step_delay)

        return {'success': False, 'message': f'Max cycles ({max_cycles}) reached'}


def main():
    parser = argparse.ArgumentParser(description='PhoneDriver API - Mobile Automation')
    parser.add_argument('task', help='Task description (e.g., "Open Settings")')
    parser.add_argument('--config', default='config.json', help='Config file path')
    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
    else:
        logging.warning(f"Config file not found: {config_path}, using defaults")
        config = {}

    # Initialize and run
    try:
        agent = PhoneAgent(config)
        result = agent.execute_task(args.task)

        if result['success']:
            logging.info(f"Task completed successfully: {result['message']}")
        else:
            logging.error(f"Task failed: {result['message']}")
            sys.exit(1)

    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
