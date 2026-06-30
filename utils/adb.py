"""ADB client wrapper."""

import logging
import os
import re
import shlex
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Optional, Tuple


# Constants
MAX_TEXT_LENGTH = 1000
MAX_COORD = 10000
MAX_DURATION = 30000
MAX_KEYCODE = 300
_REMOTE_SCREENSHOT_DIR = "/data/local/tmp"
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9:_\-.]+$")


class ADBClient:
    """Wrapper for ADB commands."""

    def __init__(self, device_id: Optional[str] = None):
        self.device_id = device_id
        self._lock = threading.Lock()

    def _cmd(self, command: list) -> list:
        """Build ADB command with device ID if specified."""
        cmd = ['adb']
        if self.device_id:
            if not _DEVICE_ID_RE.match(self.device_id):
                raise ValueError(f"Invalid device_id: {self.device_id}")
            cmd.extend(['-s', self.device_id])
        cmd.extend(command)
        return cmd

    @staticmethod
    def _sanitize_env() -> dict:
        """Return a copy of the environment with API keys stripped."""
        safe_env = os.environ.copy()
        for key in list(safe_env.keys()):
            if key.endswith('_API_KEY') or key == 'API_KEY':
                del safe_env[key]
        return safe_env

    @staticmethod
    def _sanitize_cmd(cmd: list) -> str:
        """Redact sensitive data from command strings for logging."""
        cmd_str = ' '.join(cmd)
        # Redact text after known sensitive patterns
        if 'input text' in cmd_str or 'clipper.set' in cmd_str:
            return '<ADB command containing sensitive text redacted>'
        return cmd_str

    def run(self, command: list, check: bool = True, capture: bool = True, timeout: int = 30) -> Tuple[int, str, str]:
        """Run ADB command and return result."""
        with self._lock:
            cmd = self._cmd(command)
            safe_env = self._sanitize_env()
            try:
                if capture:
                    result = subprocess.run(
                        cmd, check=check, capture_output=True, text=True,
                        encoding='utf-8', errors='replace', timeout=timeout,
                        env=safe_env,
                    )
                    return result.returncode, result.stdout, result.stderr
                else:
                    result = subprocess.run(
                        cmd, check=check, timeout=timeout, env=safe_env,
                    )
                    return result.returncode, "", ""
            except subprocess.TimeoutExpired:
                logging.error("ADB command timed out: %s", self._sanitize_cmd(cmd))
                return -1, "", "Timeout"
            except subprocess.CalledProcessError as e:
                safe_stderr = (e.stderr or "")[:200].replace("\n", " ")
                logging.error("ADB command failed: %s - %s", self._sanitize_cmd(cmd), safe_stderr)
                if check:
                    raise
                return e.returncode, e.stdout, e.stderr

    def _shell(self, command: str) -> str:
        """Run an allowed ADB shell command.

        This method is intentionally private and restricted to a small
        allow-list. Never call it with user-controlled input.
        """
        allowed = {"wm size", "wm density", "getprop ro.build.version.release"}
        if command.strip() not in allowed:
            raise ValueError(f"Command not allowed: {command}")
        _, stdout, _ = self.run(['shell', command])
        return stdout.strip()

    def tap(self, x: int, y: int) -> None:
        """Tap at screen coordinates."""
        if not isinstance(x, int) or not isinstance(y, int):
            raise TypeError("Coordinates must be integers")
        if x < 0 or y < 0 or x > MAX_COORD or y > MAX_COORD:
            raise ValueError(f"Coordinates out of bounds: ({x}, {y})")
        self.run(['shell', f'input tap {x} {y}'], check=True)
        logging.info("Tapped at (%d, %d)", x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> None:
        """Swipe from one point to another."""
        if not all(isinstance(v, int) for v in [x1, y1, x2, y2, duration]):
            raise TypeError("All swipe parameters must be integers")
        if any(v < 0 for v in [x1, y1, x2, y2]):
            raise ValueError("Coordinates must be non-negative")
        if duration < 0 or duration > MAX_DURATION:
            raise ValueError(f"Duration must be between 0 and {MAX_DURATION}")
        self.run(['shell', f'input swipe {x1} {y1} {x2} {y2} {duration}'], check=True)
        logging.info("Swiped from (%d, %d) to (%d, %d)", x1, y1, x2, y2)

    def type_text(self, text: str) -> None:
        """Type text via ADB.

        Uses `input text` for ASCII input. Spaces are encoded as %s which is
        the ADB convention. Non-ASCII characters (e.g. Chinese) may not work
        with `input text`; a clipboard-based fallback is attempted.
        """
        if not text:
            return
        if not isinstance(text, str):
            text = str(text)
        if len(text) > MAX_TEXT_LENGTH:
            logging.error("Text too long: %d chars (max %d)", len(text), MAX_TEXT_LENGTH)
            raise ValueError(f"Text exceeds maximum length of {MAX_TEXT_LENGTH}")
        if '\x00' in text:
            raise ValueError("Null bytes are not allowed in text input")

        try:
            # Encode spaces using ADB's %s convention
            safe_text = text.replace(' ', '%s')
            # Properly escape the argument for the shell
            quoted = shlex.quote(safe_text)
            self.run(['shell', f'input text {quoted}'], check=True)
            logging.info("Typed text (length=%d, redacted)", len(text))
        except (subprocess.CalledProcessError, OSError) as e:
            logging.warning("input text failed (%s), trying clipboard fallback", e)
            self._type_via_clipboard(text)

    def _type_via_clipboard(self, text: str) -> None:
        """Fallback: type text using clipboard paste."""
        try:
            # Use shlex.quote for robust shell escaping instead of manual replacement
            quoted = shlex.quote(text)
            self.run(['shell', f'am broadcast -a clipper.set -e text {quoted}'], check=False)
            # KEYCODE_PASTE (279) attempts to paste into focused field
            self.run(['shell', 'input keyevent 279'], check=False)
            logging.info("Typed text via clipboard (length=%d, redacted)", len(text))
        except Exception as e:
            logging.error("Clipboard fallback also failed: %s", e)

    def keyevent(self, keycode: int) -> None:
        """Send keyevent."""
        if not isinstance(keycode, int) or keycode < 0 or keycode > MAX_KEYCODE:
            raise ValueError(f"Invalid keycode: {keycode}")
        self.run(['shell', f'input keyevent {keycode}'], check=True)

    def screenshot(self, path: str) -> bool:
        """Capture screenshot to local path."""
        # Validate local path is within the current working directory
        target = Path(path).resolve()
        allowed_base = Path("./screenshots").resolve().parent
        try:
            target.relative_to(allowed_base)
        except ValueError:
            logging.error("Invalid screenshot path: %s", path)
            return False

        remote_path = f"{_REMOTE_SCREENSHOT_DIR}/screen_{uuid.uuid4().hex}.png"
        try:
            # Take screenshot on device using a unique temp path
            self.run(['shell', f'screencap -p {remote_path}'], check=True)
            # Pull to local
            self.run(['pull', remote_path, str(target)], check=True)
            return True
        except Exception as e:
            logging.error("Screenshot failed: %s", e)
            return False
        finally:
            # Best-effort cleanup of remote file
            try:
                self.run(['shell', f'rm -f {remote_path}'], check=False)
            except Exception:
                pass

    def get_screen_size(self) -> Tuple[int, int]:
        """Get device screen size."""
        output = self._shell('wm size')
        # Parse "Physical size: 1080x2340" or "Override size: 1080x2340"
        if 'size:' in output:
            size_str = output.split('size:')[-1].strip()
            parts = size_str.split('x')
            if len(parts) == 2:
                try:
                    width, height = int(parts[0]), int(parts[1])
                    return width, height
                except ValueError:
                    logging.warning("Invalid screen size numbers: %s", size_str)
        return 1080, 2340  # Default fallback

    def get_device_id(self) -> Optional[str]:
        """Get connected device ID."""
        try:
            _, stdout, _ = self.run(['devices'], check=True)
            lines = stdout.strip().split('\n')[1:]  # Skip header
            for line in lines:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == 'device':
                    if self.device_id is None or parts[0] == self.device_id:
                        return parts[0]
        except Exception as e:
            logging.error("Failed to get device ID: %s", e)
        return None

    def is_connected(self) -> bool:
        """Check if device is connected."""
        if self.device_id:
            return self.get_device_id() == self.device_id
        return self.get_device_id() is not None
