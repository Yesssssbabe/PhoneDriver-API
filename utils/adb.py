"""ADB client wrapper."""

import logging
import shlex
import subprocess
from typing import Optional, Tuple


class ADBClient:
    """Wrapper for ADB commands."""

    def __init__(self, device_id: Optional[str] = None):
        self.device_id = device_id

    def _cmd(self, command: list) -> list:
        """Build ADB command with device ID if specified."""
        cmd = ['adb']
        if self.device_id:
            cmd.extend(['-s', self.device_id])
        cmd.extend(command)
        return cmd

    def run(self, command: list, check: bool = True, capture: bool = True, timeout: int = 30) -> Tuple[int, str, str]:
        """Run ADB command and return result."""
        cmd = self._cmd(command)
        try:
            if capture:
                result = subprocess.run(
                    cmd, check=check, capture_output=True, text=True, timeout=timeout
                )
                return result.returncode, result.stdout, result.stderr
            else:
                result = subprocess.run(cmd, check=check, timeout=timeout)
                return result.returncode, "", ""
        except subprocess.TimeoutExpired:
            logging.error(f"ADB command timed out: {' '.join(cmd)}")
            return -1, "", "Timeout"
        except subprocess.CalledProcessError as e:
            logging.error(f"ADB command failed: {' '.join(cmd)} - {e.stderr}")
            if check:
                raise
            return e.returncode, e.stdout, e.stderr

    def shell(self, command: str) -> str:
        """Run ADB shell command."""
        _, stdout, _ = self.run(['shell', command])
        return stdout.strip()

    def tap(self, x: int, y: int) -> None:
        """Tap at screen coordinates."""
        self.run(['shell', f'input tap {x} {y}'], check=False)
        logging.info(f"Tapped at ({x}, {y})")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> None:
        """Swipe from one point to another."""
        self.run(['shell', f'input swipe {x1} {y1} {x2} {y2} {duration}'], check=False)
        logging.info(f"Swiped from ({x1}, {y1}) to ({x2}, {y2})")

    def type_text(self, text: str) -> None:
        """Type text via ADB.

        Uses `input text` for ASCII input. Spaces are encoded as %s which is
        the ADB convention. Non-ASCII characters (e.g. Chinese) may not work
        with `input text`; a clipboard-based fallback is attempted.
        """
        if not text:
            return

        try:
            # Encode spaces using ADB's %s convention
            safe_text = text.replace(' ', '%s')
            # Properly escape the argument for the shell
            quoted = shlex.quote(safe_text)
            self.run(['shell', f'input text {quoted}'], check=True)
            logging.info(f"Typed text: {text[:50]}...")
        except Exception as e:
            logging.warning(f"input text failed ({e}), trying clipboard fallback")
            self._type_via_clipboard(text)

    def _type_via_clipboard(self, text: str) -> None:
        """Fallback: type text using clipboard paste."""
        try:
            # Escape shell-sensitive characters for the broadcast intent
            safe_text = text.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`')
            self.run(['shell', f'am broadcast -a clipper.set -e text "{safe_text}"'], check=False)
            # KEYCODE_PASTE (279) attempts to paste into focused field
            self.run(['shell', 'input keyevent 279'], check=False)
            logging.info(f"Typed text via clipboard: {text[:50]}...")
        except Exception as e:
            logging.error(f"Clipboard fallback also failed: {e}")

    def keyevent(self, keycode: int) -> None:
        """Send keyevent."""
        self.run(['shell', f'input keyevent {keycode}'], check=False)

    def screenshot(self, path: str) -> bool:
        """Capture screenshot to local path."""
        try:
            # Take screenshot on device
            self.run(['shell', 'screencap -p /sdcard/screen.png'], check=True)
            # Pull to local
            self.run(['pull', '/sdcard/screen.png', path], check=True)
            return True
        except Exception as e:
            logging.error(f"Screenshot failed: {e}")
            return False

    def get_screen_size(self) -> Tuple[int, int]:
        """Get device screen size."""
        output = self.shell('wm size')
        # Parse "Physical size: 1080x2340" or "Override size: 1080x2340"
        if 'size:' in output:
            size_str = output.split('size:')[-1].strip()
            width, height = map(int, size_str.split('x'))
            return width, height
        return 1080, 2340  # Default fallback

    def get_device_id(self) -> Optional[str]:
        """Get connected device ID."""
        try:
            _, stdout, _ = self.run(['devices'], check=True)
            lines = stdout.strip().split('\n')[1:]  # Skip header
            for line in lines:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == 'device':
                    return parts[0]
        except Exception as e:
            logging.error(f"Failed to get device ID: {e}")
        return None

    def is_connected(self) -> bool:
        """Check if device is connected."""
        return self.get_device_id() is not None
