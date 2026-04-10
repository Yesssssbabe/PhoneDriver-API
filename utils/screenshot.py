"""Screenshot capture utility."""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image

from .adb import ADBClient


class ScreenshotCapture:
    """Handle screenshot capture and management."""

    def __init__(self, adb: ADBClient, save_dir: str = "./screenshots"):
        self.adb = adb
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(exist_ok=True)

    def capture(self, filename: Optional[str] = None) -> Optional[str]:
        """Capture screenshot and return path."""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screen_{timestamp}.png"
        
        filepath = self.save_dir / filename
        
        if self.adb.screenshot(str(filepath)):
            logging.info(f"Screenshot saved: {filepath}")
            return str(filepath)
        return None

    def capture_with_resize(self, max_size: int = 1280) -> Optional[str]:
        """Capture and resize screenshot."""
        path = self.capture()
        if not path:
            return None
        
        try:
            with Image.open(path) as img:
                if max(img.size) > max_size:
                    ratio = max_size / max(img.size)
                    new_size = tuple(int(dim * ratio) for dim in img.size)
                    img_resized = img.resize(new_size, Image.Resampling.LANCZOS)
                    img_resized.save(path, quality=85)
                    logging.info(f"Resized screenshot to {new_size}")
        except Exception as e:
            logging.warning(f"Failed to resize screenshot: {e}")
        
        return path

    def cleanup_old(self, keep_count: int = 50) -> None:
        """Remove old screenshots, keep only recent ones."""
        try:
            screenshots = sorted(
                self.save_dir.glob("screen_*.png"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            for old in screenshots[keep_count:]:
                old.unlink()
                logging.debug(f"Removed old screenshot: {old}")
        except Exception as e:
            logging.warning(f"Cleanup failed: {e}")
