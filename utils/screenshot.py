"""Screenshot capture utility."""

import logging
import os
import stat
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image

from .adb import ADBClient


# Limit decompression bombs: refuse to process images claiming more than 10Mpx.
Image.MAX_IMAGE_PIXELS = 10_000_000


# Allowed base directory for screenshots (relative to project root)
DEFAULT_SAVE_DIR = "./screenshots"


def _validate_save_dir(save_dir: Path) -> Path:
    """Resolve save_dir and ensure it is within the allowed base directory."""
    resolved = save_dir.resolve()
    allowed_base = Path(DEFAULT_SAVE_DIR).resolve().parent
    try:
        resolved.relative_to(allowed_base)
    except ValueError as exc:
        raise ValueError(f"Invalid screenshot directory: {save_dir}") from exc
    return resolved


def _sanitize_filename(name: str) -> str:
    """Return a safe filename stripped of path separators and parent references."""
    base = os.path.basename(name)
    base = base.replace("..", "")
    if not base or base.startswith("."):
        raise ValueError(f"Invalid screenshot filename: {name}")
    return base


class ScreenshotCapture:
    """Handle screenshot capture and management."""

    def __init__(self, adb: ADBClient, save_dir: str = DEFAULT_SAVE_DIR):
        self.adb = adb
        self.save_dir = _validate_save_dir(Path(save_dir))

        # Reject symlinked directories to prevent arbitrary file writes
        if self.save_dir.is_symlink():
            raise PermissionError(f"Screenshot directory is a symlink: {self.save_dir}")

        self.save_dir.mkdir(parents=True, exist_ok=True)
        # Restrict directory permissions on Unix
        if os.name != "nt":
            os.chmod(self.save_dir, 0o700)

        self._lock = threading.RLock()
        self._cleanup_lock = threading.Lock()
        self._active_reads: set = set()

    def capture(self, filename: Optional[str] = None) -> Optional[str]:
        """Capture screenshot and return path."""
        with self._lock:
            if filename is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                filename = f"screen_{timestamp}_{uuid.uuid4().hex[:8]}.png"
            else:
                filename = _sanitize_filename(filename)

            filepath = self.save_dir / filename

            # Create the target file atomically so it cannot be a symlink/hardlink.
            # If a safe regular file already exists (e.g. caller-supplied filename),
            # truncate it instead of failing.
            try:
                if filepath.exists():
                    if filepath.is_symlink() or os.lstat(str(filepath)).st_nlink != 1:
                        logging.error("Screenshot file is a symlink/hardlink: %s", filepath)
                        return None
                    fd = os.open(str(filepath), os.O_WRONLY | os.O_TRUNC)
                    os.close(fd)
                else:
                    fd = os.open(str(filepath), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(fd)
            except OSError as exc:
                logging.error("Failed to create screenshot file: %s", exc)
                return None

            if os.lstat(str(filepath)).st_nlink != 1:
                os.unlink(str(filepath))
                logging.error("Hardlink detected for screenshot file: %s", filepath)
                return None

            if self.adb.screenshot(str(filepath)):
                # Restrict file permissions on Unix
                if os.name != "nt" and filepath.exists():
                    os.chmod(filepath, 0o600)
                logging.info("Screenshot saved: %s", filepath)
                return str(filepath)

            # Best-effort cleanup if ADB pull failed.
            try:
                os.unlink(str(filepath))
            except OSError:
                pass
            return None

    def _open_image_nofollow(self, path: str):
        """Open an image file without following symlinks."""
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError(f"Not a regular file: {path}")
            return os.fdopen(fd, 'rb')
        except Exception:
            os.close(fd)
            raise

    def capture_with_resize(self, max_size: int = 1280) -> Optional[str]:
        """Capture and resize screenshot."""
        if not isinstance(max_size, int) or max_size < 1:
            logging.error("Invalid max_size: %s, must be >= 1", max_size)
            max_size = 1280

        with self._lock:
            path = self.capture()
            if not path:
                return None

            # Guard against symlink attacks: the saved file must be a regular file
            if not os.path.isfile(path) or os.path.islink(path):
                logging.error("Screenshot path is not a regular file: %s", path)
                return None

            try:
                with self._open_image_nofollow(path) as f:
                    with Image.open(f) as img:
                        original_max = max(img.size)
                        if original_max == 0:
                            logging.error("Screenshot has zero dimensions, skipping resize")
                            return path

                        # Reject decompression-bomb-sized images.
                        width, height = img.size
                        if width * height > Image.MAX_IMAGE_PIXELS:
                            logging.error("Screenshot dimensions too large: %dx%d", width, height)
                            return None

                        if original_max <= max_size:
                            return path  # Skip unnecessary work
                        ratio = max_size / original_max
                        new_size = tuple(int(dim * ratio) for dim in img.size)
                        # Use BICUBIC for a good balance of speed and quality
                        img_resized = img.resize(new_size, Image.Resampling.BICUBIC)

                        # Create a uniquely-named temp file with restrictive permissions.
                        fd, temp_path = tempfile.mkstemp(dir=self.save_dir, suffix=".tmp")
                        os.close(fd)
                        os.chmod(temp_path, 0o600)
                        try:
                            img_resized.save(temp_path, optimize=False)
                            # Reject if the destination became a hardlink.
                            if os.path.exists(path) and os.lstat(path).st_nlink > 1:
                                logging.error("Hardlink detected, refusing to replace: %s", path)
                                return None
                            os.replace(temp_path, path)
                            logging.info("Resized screenshot to %s", new_size)
                        finally:
                            img_resized.close()
                            try:
                                if os.path.exists(temp_path):
                                    os.unlink(temp_path)
                            except OSError:
                                pass
            except Exception as e:
                logging.warning("Failed to resize screenshot: %s", e)

            return path

    def acquire_read(self, path: str) -> None:
        """Mark a screenshot as being read so cleanup does not delete it."""
        with self._cleanup_lock:
            self._active_reads.add(path)

    def release_read(self, path: str) -> None:
        """Unmark a screenshot read reference."""
        with self._cleanup_lock:
            self._active_reads.discard(path)

    def cleanup_old(self, keep_count: int = 50) -> None:
        """Remove old screenshots, keep only recent ones."""
        with self._cleanup_lock:
            try:
                screenshots = sorted(
                    self.save_dir.glob("screen_*.png"),
                    key=lambda p: p.lstat().st_mtime,
                    reverse=True,
                )
                for old in screenshots[keep_count:]:
                    # Skip files that are currently being read by the provider.
                    if str(old) in self._active_reads:
                        continue
                    # Skip symlinks to avoid deleting unexpected targets
                    if old.is_symlink():
                        logging.warning("Skipping symlink during cleanup: %s", old)
                        continue
                    if not old.is_file():
                        continue
                    old.unlink()
                    logging.debug("Removed old screenshot: %s", old)
            except Exception as e:
                logging.warning("Cleanup failed: %s", e)
