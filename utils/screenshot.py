"""Screenshot capture utility."""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image

from .adb import ADBClient


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

    def capture(self, filename: Optional[str] = None) -> Optional[str]:
        """Capture screenshot and return path."""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"screen_{timestamp}_{uuid.uuid4().hex[:8]}.png"
        else:
            filename = _sanitize_filename(filename)

        filepath = self.save_dir / filename

        if self.adb.screenshot(str(filepath)):
            # Restrict file permissions on Unix
            if os.name != "nt" and filepath.exists():
                os.chmod(filepath, 0o600)
            logging.info("Screenshot saved: %s", filepath)
            return str(filepath)
        return None

    def capture_with_resize(self, max_size: int = 1280) -> Optional[str]:
        """Capture and resize screenshot."""
        if not isinstance(max_size, int) or max_size < 1:
            logging.error("Invalid max_size: %s, must be >= 1", max_size)
            max_size = 1280

        path = self.capture()
        if not path:
            return None

        # Guard against symlink attacks: the saved file must be a regular file
        if not os.path.isfile(path) or os.path.islink(path):
            logging.error("Screenshot path is not a regular file: %s", path)
            return None

        try:
            with Image.open(path) as img:
                if max(img.size) <= max_size:
                    return path  # Skip unnecessary work
                ratio = max_size / max(img.size)
                new_size = tuple(int(dim * ratio) for dim in img.size)
                # Use BICUBIC for a good balance of speed and quality
                img_resized = img.resize(new_size, Image.Resampling.BICUBIC)
                temp_path = path + ".tmp"
                try:
                    img_resized.save(temp_path, optimize=False)
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

    def cleanup_old(self, keep_count: int = 50) -> None:
        """Remove old screenshots, keep only recent ones."""
        try:
            screenshots = sorted(
                self.save_dir.glob("screen_*.png"),
                key=lambda p: p.lstat().st_mtime,
                reverse=True,
            )
            for old in screenshots[keep_count:]:
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
