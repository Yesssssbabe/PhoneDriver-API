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
        self.screenshot = ScreenshotCapture(self.adb, config.get('screenshot_dir', './screenshots'))
        self.vl_agent = self._create_provider()
        self.previous_actions = []
        
    def _create_provider(self):
        """Create vision-language provider from environment."""
        provider_name = os.environ.get('PROVIDER', 'kimi_code').lower()
        
        provider_config = {
            'temperature': self.config.get('temperature', 0.1),
            'max_tokens': self.config.get('max_tokens', 512),
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
            raise ValueError(f"API key not found for provider: {provider_name}. "
                           f"Please set the appropriate environment variable in .env file.")
        
        logging.info(f"Initializing provider: {provider_name}")
        return get_provider(provider_name, api_key=api_key, **provider_config)

    def execute_action(self, action: dict) -> bool:
        """Execute an action on the device."""
        action_type = action.get('action')
        
        try:
            if action_type == 'tap':
                coords = action.get('coordinates', [0, 0])
                x = int(coords[0] * self.config['screen_width'])
                y = int(coords[1] * self.config['screen_height'])
                self.adb.tap(x, y)
                
            elif action_type == 'swipe':
                direction = action.get('direction', 'down')
                w, h = self.config['screen_width'], self.config['screen_height']
                
                if direction == 'down':
                    self.adb.swipe(w//2, h*2//3, w//2, h//3)
                elif direction == 'up':
                    self.adb.swipe(w//2, h//3, w//2, h*2//3)
                elif direction == 'right':
                    self.adb.swipe(w*2//3, h//2, w//3, h//2)
                elif direction == 'left':
                    self.adb.swipe(w//3, h//2, w*2//3, h//2)
                    
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

    def execute_task(self, task: str) -> dict:
        """Execute a task and return result."""
        logging.info(f"Starting task: {task}")
        
        max_cycles = self.config.get('max_cycles', 15)
        step_delay = self.config.get('step_delay', 1.5)
        
        for cycle in range(1, max_cycles + 1):
            logging.info(f"\n--- Cycle {cycle}/{max_cycles} ---")
            
            # Capture screenshot
            screenshot_path = self.screenshot.capture_with_resize()
            if not screenshot_path:
                logging.error("Failed to capture screenshot")
                continue
            
            # Get action from model
            context = {'previous_actions': self.previous_actions}
            action = self.vl_agent.analyze_screenshot(screenshot_path, task, context)
            
            if not action:
                logging.error("Failed to get action from model")
                time.sleep(step_delay)
                continue
            
            # Record action
            self.previous_actions.append(action)
            
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
