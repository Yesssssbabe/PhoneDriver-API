"""HTTP client abstraction for vision-language providers.

This module separates the transport layer from the provider business layer
(C16 fix). Providers that use the OpenAI-compatible ``/chat/completions``
endpoint can reuse :class:`OpenAICompatibleClient`; providers with a different
protocol can implement their own :class:`HttpClient` without touching the
provider inheritance tree.
"""
from __future__ import annotations

import json
import logging
import math
import random
import threading
import time
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Any, Dict, List, Optional

import requests


MAX_RESPONSE_BYTES = 1024 * 1024  # 1 MB


class CircuitState(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class CircuitBreaker:
    """Simple circuit breaker to fail fast during sustained API outages."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = CircuitState.CLOSED
        self._lock = threading.Lock()
        self._half_open_probe = False

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.state = CircuitState.CLOSED

    def record_failure(self) -> bool:
        """Record a failure. Returns True if the breaker just opened."""
        with self._lock:
            self.failures += 1
            self.last_failure_time = time.monotonic()
            self._half_open_probe = False
            if self.failures >= self.failure_threshold:
                self.state = CircuitState.OPEN
                return True
            return False

    def can_execute(self) -> bool:
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                if time.monotonic() - self.last_failure_time > self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self._half_open_probe = False
                    return True
                return False
            # HALF_OPEN: only a single probe is allowed.
            if self._half_open_probe:
                return False
            self._half_open_probe = True
            return True


class HttpClient(ABC):
    """Abstract HTTP transport for chat-completion-style APIs."""

    @abstractmethod
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> Optional[str]:
        """Call the chat completion endpoint and return raw content text.

        Returns ``None`` when the call fails definitively.
        """
        ...

    def close(self) -> None:
        """Release resources. Subclasses may override."""


class OpenAICompatibleClient(HttpClient):
    """HTTP client for OpenAI-compatible ``/chat/completions`` endpoints."""

    _MODEL_PRICING = {
        "gpt-4o": {"input": 0.005, "output": 0.015},
        "kimi-for-coding": {"input": 0.003, "output": 0.009},
        "kimi-k2.5": {"input": 0.003, "output": 0.009},
        "moonshotai/kimi-k2.5": {"input": 0.003, "output": 0.009},
    }

    def __init__(
        self,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        session: Optional[requests.Session] = None,
        pinned_cert: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.session = session or requests.Session()
        if pinned_cert:
            self.session.verify = pinned_cert
        else:
            self.session.verify = True
        self.circuit_breaker = CircuitBreaker()
        self._session_lock = threading.Lock()
        self._closing = False
        self._cost_tracker = {"total_tokens": 0, "total_cost_usd": 0.0}
        self.last_response_headers: Dict[str, str] = {}
        self.last_response_raw: bytes = b""

    def close(self) -> None:
        """Close the requests session and release connections."""
        with self._session_lock:
            self._closing = True
            session = self.session
            if session is not None:
                session.close()

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> Optional[str]:
        """Call ``/chat/completions`` with retry, backoff and safe parsing."""
        if not self.circuit_breaker.can_execute():
            logging.error("Circuit breaker OPEN - request rejected")
            return None

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        url = f"{self.base_url}/chat/completions"
        last_error: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                with self._session_lock:
                    if self._closing:
                        logging.warning("Request rejected: client is closing")
                        return None
                    resp = self.session.post(
                        url,
                        headers=self.headers,
                        json=payload,
                        timeout=(10.0, timeout),
                        allow_redirects=False,
                        stream=True,
                    )

                with resp:
                    # Block SSRF-style redirects.
                    if 300 <= resp.status_code < 400:
                        logging.error(
                            "Redirect blocked by SSRF policy: %s -> %s",
                            url,
                            resp.headers.get("Location"),
                        )
                        self.circuit_breaker.record_failure()
                        return None

                    if resp.status_code == 429:
                        retry_after = self._retry_after(resp)
                        logging.warning(
                            "Rate limited (429) on attempt %d/%d, retrying after %.1fs",
                            attempt,
                            max_retries,
                            retry_after,
                        )
                        if attempt < max_retries:
                            time.sleep(retry_after)
                            continue
                        # Final 429 counts as a failure to prevent runaway retries.
                        self.circuit_breaker.record_failure()
                        return None

                    # 4xx errors other than 429 are not worth retrying.
                    if 400 <= resp.status_code < 500 and resp.status_code != 429:
                        logging.error(
                            "Non-retriable HTTP error %d from %s. Response body omitted for security.",
                            resp.status_code,
                            url,
                        )
                        self.circuit_breaker.record_failure()
                        return None

                    resp.raise_for_status()
                    self.last_response_headers = dict(resp.headers)

                    content_type = resp.headers.get("Content-Type", "")
                    if "application/json" not in content_type:
                        logging.error("Unexpected Content-Type: %s", content_type)
                        self.circuit_breaker.record_failure()
                        return None

                    raw = resp.raw.read(MAX_RESPONSE_BYTES + 1)
                    self.last_response_raw = raw
                    if len(raw) > MAX_RESPONSE_BYTES:
                        logging.error("Response body too large: %d bytes", len(raw))
                        self.circuit_breaker.record_failure()
                        return None

                    data = json.loads(raw)
                    usage = data.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
                    self._cost_tracker["total_tokens"] += total_tokens
                    pricing = self._MODEL_PRICING.get(model, {"input": 0.01, "output": 0.03})
                    cost = (prompt_tokens / 1000) * pricing["input"] + (completion_tokens / 1000) * pricing["output"]
                    self._cost_tracker["total_cost_usd"] += cost
                    logging.info(
                        "API call cost: $%.4f, total: $%.4f",
                        cost,
                        self._cost_tracker["total_cost_usd"],
                    )

                    choices = data.get("choices", [])
                    if not isinstance(choices, list) or not choices:
                        logging.error("Invalid or empty choices in API response")
                        return None
                    content = choices[0].get("message", {}).get("content")
                    if content is None:
                        logging.error("Missing content in API response")
                        return None
                    self.circuit_breaker.record_success()
                    return content

            except requests.exceptions.Timeout as exc:
                last_error = exc
                logging.warning(
                    "API timeout on attempt %d/%d: %s", attempt, max_retries, exc
                )
                if self.circuit_breaker.record_failure():
                    break
            except (requests.exceptions.ConnectionError, requests.exceptions.SSLError) as exc:
                last_error = exc
                logging.warning(
                    "API connection error on attempt %d/%d: %s",
                    attempt,
                    max_retries,
                    exc,
                )
                if self.circuit_breaker.record_failure():
                    break
            except requests.exceptions.RequestException as exc:
                last_error = exc
                logging.warning(
                    "API request error on attempt %d/%d: %s",
                    attempt,
                    max_retries,
                    exc,
                )
                if self.circuit_breaker.record_failure():
                    break
            except (json.JSONDecodeError, ValueError, AttributeError) as exc:
                last_error = exc
                logging.warning(
                    "API response parsing error on attempt %d/%d: %s",
                    attempt,
                    max_retries,
                    exc,
                )
                if self.circuit_breaker.record_failure():
                    break

            if attempt < max_retries:
                delay = min(2 ** attempt + random.uniform(0, 1), 60.0)
                time.sleep(delay)

        if last_error:
            logging.error("API call failed after %d retries: %s", max_retries, last_error)
        return None

    @staticmethod
    def _retry_after(resp: requests.Response) -> float:
        """Read Retry-After header, defaulting to 1 second and clamping to 60s."""
        try:
            value = float(resp.headers.get("Retry-After", 1.0))
        except (ValueError, TypeError):
            value = 1.0
        if value < 0 or not math.isfinite(value):
            value = 1.0
        return min(value, 60.0)
