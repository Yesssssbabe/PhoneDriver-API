"""ADB client wrapper."""

import concurrent.futures
import hashlib
import logging
import os
import re
import shlex
import shutil
import stat
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Optional, Tuple


# Resolve the absolute adb binary path once at import time and validate it is not
# world-writable. Fall back to the bare command name if adb is not on PATH so
# tests and exotic environments keep working.
_ADB_PATH: str = shutil.which("adb") or "adb"


# Constants
MAX_TEXT_LENGTH = 1000
MAX_COORD = 10000
MAX_DURATION = 30000
MAX_KEYCODE = 300
_REMOTE_SCREENSHOT_DIR = "/data/local/tmp"
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9:_\-.]+$")


class ADBClient:
    """Wrapper for ADB commands."""

    def __init__(
        self,
        device_id: Optional[str] = None,
        trusted_fingerprint: str = "",
    ):
        self.device_id = device_id
        self._trusted_fingerprint = trusted_fingerprint
        self._lock = threading.Lock()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._validate_adb_keys()
        if self._is_root() and os.environ.get("PHONEDRIVER_ALLOW_ADB_ROOT") != "1":
            raise PermissionError(
                "ADB is running in root mode. Set PHONEDRIVER_ALLOW_ADB_ROOT=1 to override."
            )

    def _cmd(self, command: list) -> list:
        """Build ADB command with device ID if specified."""
        adb_bin = _ADB_PATH
        if adb_bin != "adb" and os.path.exists(adb_bin):
            st = os.stat(adb_bin)
            if stat.S_IWOTH & st.st_mode:
                raise RuntimeError(f"adb binary is world-writable: {adb_bin}")
        cmd = [adb_bin]
        if self.device_id:
            if not _DEVICE_ID_RE.match(self.device_id):
                raise ValueError(f"Invalid device_id: {self.device_id}")
            cmd.extend(['-s', self.device_id])
        cmd.extend(command)
        return cmd

    @staticmethod
    def _sanitize_env() -> dict:
        """Return a minimal, hardened environment for ADB subprocesses."""
        allowed = {
            'PATH', 'HOME', 'USER', 'SHELL', 'TERM', 'DISPLAY', 'XAUTHORITY',
            'ANDROID_SDK_HOME', 'ANDROID_HOME', 'TMPDIR', 'TEMP',
        }
        safe_env = {k: v for k, v in os.environ.items() if k in allowed}
        # Strip any dynamic-linker variables that may have survived the whitelist.
        for key in list(safe_env.keys()):
            if key.startswith('LD_'):
                del safe_env[key]
        # Ensure a sane, minimal PATH so the real adb binary is found.
        if 'PATH' not in safe_env or not safe_env.get('PATH'):
            safe_env['PATH'] = '/usr/bin:/bin:/usr/local/bin'
        # Remove world-writable directories from PATH to prevent binary hijacking.
        path = safe_env.get('PATH', '')
        safe_dirs = []
        for d in path.split(':'):
            if os.path.isdir(d):
                try:
                    st = os.stat(d)
                    if stat.S_IWOTH & st.st_mode:
                        logging.warning("World-writable PATH directory removed: %s", d)
                        continue
                except OSError:
                    pass
            safe_dirs.append(d)
        safe_env['PATH'] = ':'.join(safe_dirs) or '/usr/bin:/bin'
        return safe_env

    @staticmethod
    def _validate_adb_keys() -> None:
        """Validate that adb_keys file is not world-writable or owned by another user."""
        home = os.environ.get('HOME', '')
        if not home:
            return
        adb_keys = Path(home) / '.android' / 'adb_keys'
        if not adb_keys.exists():
            return
        try:
            st = adb_keys.stat()
        except OSError:
            return
        if stat.S_IWOTH & st.st_mode:
            raise PermissionError(f"adb_keys file is world-writable: {adb_keys}")
        if st.st_uid != os.getuid():
            raise PermissionError(f"adb_keys file is not owned by current user: {adb_keys}")

    @staticmethod
    def _sanitize_cmd(cmd: list) -> str:
        """Redact sensitive data from command strings for logging."""
        cmd_str = ' '.join(cmd)
        # Redact text after known sensitive patterns
        if 'input text' in cmd_str or 'clipper.set' in cmd_str:
            return '<ADB command containing sensitive text redacted>'
        return cmd_str

    def run(
        self,
        command: list,
        check: bool = True,
        capture: bool = True,
        timeout: int = 30,
        max_output_bytes: int = 1024 * 1024,
    ) -> Tuple[int, str, str]:
        """Run ADB command and return result.

        Output is capped at ``max_output_bytes`` to avoid memory exhaustion from
        unexpectedly large command output.
        """
        with self._lock:
            cmd = self._cmd(command)
            safe_env = self._sanitize_env()
            try:
                if capture:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        env=safe_env,
                    )
                    stdout_chunks = []
                    total_size = 0
                    while True:
                        chunk = proc.stdout.read(4096)
                        if not chunk:
                            break
                        total_size += len(chunk)
                        if total_size > max_output_bytes:
                            proc.kill()
                            proc.wait()
                            raise ValueError(f"ADB output exceeded {max_output_bytes} bytes")
                        stdout_chunks.append(chunk)
                    stdout = "".join(stdout_chunks)
                    stderr = proc.stderr.read()[:1000]
                    returncode = proc.wait()
                    if check and returncode != 0:
                        raise subprocess.CalledProcessError(returncode, cmd, stdout, stderr)
                    return returncode, stdout, stderr
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
                safe_cmd = self._sanitize_cmd(cmd)
                logging.error("ADB command failed: %s - %s", safe_cmd, safe_stderr)
                if check:
                    # Re-raise with a sanitized command string so the exception
                    # object does not leak sensitive typed text.
                    raise subprocess.CalledProcessError(
                        e.returncode, safe_cmd, e.stdout, safe_stderr
                    ) from e
                return e.returncode, e.stdout, e.stderr

    def run_with_timeout(self, command: list, timeout: int = 30) -> Tuple[int, str, str]:
        """Run ADB command with a hard timeout via future.cancel()."""
        future = self._executor.submit(self.run, command, timeout=timeout)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            logging.error("ADB command forcefully timed out: %s", self._sanitize_cmd(command))
            raise subprocess.TimeoutExpired(command, timeout)

    def close(self) -> None:
        """Release the executor used for hard timeouts."""
        self._executor.shutdown(wait=False)

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

    def _run_shell_input(self, command: str, check: bool = True, timeout: int = 30) -> Tuple[int, str, str]:
        """Run a single shell command via `adb shell` using stdin.

        Passing the command through stdin keeps sensitive text out of the host
        process command line (``ps`` / ``/proc/*/cmdline``).
        """
        with self._lock:
            cmd = self._cmd(['shell'])
            safe_env = self._sanitize_env()
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                env=safe_env,
            )
            try:
                stdout, stderr = proc.communicate(input=command + "\n", timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                logging.error("ADB shell command timed out: %s", self._sanitize_cmd(cmd))
                if check:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                return -1, stdout, stderr

            if check and proc.returncode != 0:
                safe_stderr = stderr[:200].replace("\n", " ")
                safe_cmd = self._sanitize_cmd(cmd)
                raise subprocess.CalledProcessError(proc.returncode, safe_cmd, stdout, safe_stderr)
            return proc.returncode, stdout, stderr

    def type_text(self, text: str) -> None:
        """Type text via ADB.

        Prefer `input text` for ASCII input to avoid global clipboard exposure.
        Spaces are encoded as %s which is the ADB convention. Non-ASCII
        characters fall back to the clipboard and the clipboard is cleared
        afterwards.
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
            # Use stdin to avoid exposing text in the host process list.
            self._run_shell_input(f'input text {quoted}', check=True)
            logging.info("Typed text (length=%d, redacted)", len(text))
        except (subprocess.CalledProcessError, OSError, UnicodeEncodeError) as e:
            safe_cmd = self._sanitize_cmd(['shell', 'input text <redacted>'])
            logging.warning("input text failed (%s), trying clipboard fallback", safe_cmd)
            self._type_via_clipboard(text)
        finally:
            # Best-effort clear of the clipboard to limit exposure to other apps.
            try:
                self.run(['shell', 'am broadcast -a clipper.set -e text ""'], check=False)
            except Exception:
                pass

    def _type_via_clipboard(self, text: str) -> None:
        """Fallback: type text using clipboard paste.

        The text is escaped for Intent extras to reduce the risk of shell/Intent
        injection through ``am broadcast``.
        """
        try:
            # Escape backslashes and quotes, then escape non-printable characters.
            safe_text = text.replace('\\', '\\\\').replace('"', '\\"').replace("'", "\\'")
            safe_text = "".join(
                c if c.isprintable() or c in '\t\n ' else '\\u' + format(ord(c), '04x')
                for c in safe_text
            )
            self._run_shell_input(
                f'am broadcast -a clipper.set -e text "{safe_text}"',
                check=True,
            )
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

        # Reject symlinks and hardlinks to prevent arbitrary overwrites.
        if target.exists():
            if os.path.islink(str(target)) or os.lstat(str(target)).st_nlink != 1:
                logging.error("Screenshot target is a symlink or hardlink: %s", path)
                return False
            if not os.path.isfile(str(target)):
                logging.error("Screenshot target is not a regular file: %s", path)
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

    def get_current_window(self) -> str:
        """Get current foreground window package name."""
        try:
            _, stdout, _ = self.run(['shell', 'dumpsys window | grep mCurrentFocus'])
            match = re.search(r'([a-zA-Z0-9._]+)/', stdout)
            return match.group(1) if match else ""
        except Exception as exc:
            logging.warning("Failed to get current window: %s", exc)
            return ""

    def _get_device_fingerprint(self) -> str:
        """Get device build fingerprint."""
        _, stdout, _ = self.run(['shell', 'getprop ro.build.fingerprint'])
        return stdout.strip()

    def verify_device(self) -> bool:
        """Verify device fingerprint against a trusted value."""
        if not self._trusted_fingerprint:
            return True
        actual = self._get_device_fingerprint()
        expected_hash = hashlib.sha256(self._trusted_fingerprint.encode()).hexdigest()[:16]
        actual_hash = hashlib.sha256(actual.encode()).hexdigest()[:16]
        if expected_hash != actual_hash:
            raise PermissionError("Device fingerprint mismatch.")
        return True

    def _is_tcp_device(self) -> bool:
        """Return True if the configured device ID looks like a TCP endpoint."""
        if not self.device_id:
            return False
        return ":" in self.device_id

    def _is_root(self) -> bool:
        """Return True if ADB shell is running as root (uid 0)."""
        try:
            _, stdout, _ = self.run(['shell', 'id -u'])
            return stdout.strip() == "0"
        except Exception:
            return False

    def is_connected(self) -> bool:
        """Check if device is connected and authorized."""
        if self._is_tcp_device() and os.environ.get("PHONEDRIVER_ALLOW_TCP") != "1":
            logging.error("TCP ADB connection rejected. Set PHONEDRIVER_ALLOW_TCP=1.")
            return False
        if self.device_id:
            return self.get_device_id() == self.device_id and self.verify_device()
        return self.get_device_id() is not None and self.verify_device()
