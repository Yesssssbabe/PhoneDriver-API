"""Shared security helpers for error handling and data scrubbing."""

from __future__ import annotations

import re


def redact_error_message(message: str) -> str:
    """Redact sensitive values from exception messages before logging/display.

    Covers API keys, URLs, filesystem paths, and ADB-style device identifiers.
    """
    safe = re.sub(r"key=[a-zA-Z0-9_\-\.]+", "key=<REDACTED>", message)
    safe = re.sub(r"(?i)sk-[a-zA-Z0-9]{20,}", "<API_KEY_REDACTED>", safe)
    safe = re.sub(r"https?://[^\s\"']+", "<URL>", safe)
    safe = re.sub(r"(/[a-zA-Z0-9_\-\.]+)+", "<PATH>", safe)
    # Redact likely ADB device identifiers (alphanumeric, colons, dots, dashes).
    safe = re.sub(r"\b[A-Za-z0-9]{4,}:[0-9]{2,5}\b", "<DEVICE>", safe)
    return safe
