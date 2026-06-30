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
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import requests


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


class OpenAICompatibleClient(HttpClient):
    """HTTP client for OpenAI-compatible ``/chat/completions`` endpoints."""

    def __init__(
        self,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.session = session or requests.Session()

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
                resp = self.session.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=(10.0, timeout),
                )

                if resp.status_code == 429:
                    retry_after = self._retry_after(resp)
                    logging.warning(
                        "Rate limited (429) on attempt %d/%d, retrying after %.1fs",
                        attempt,
                        max_retries,
                        retry_after,
                    )
                    time.sleep(retry_after)
                    continue

                # 4xx errors other than 429 are not worth retrying.
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    logging.error(
                        "Non-retriable HTTP error %d: %s",
                        resp.status_code,
                        resp.text[:500],
                    )
                    return None

                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    logging.error("Empty choices in API response")
                    return None
                content = choices[0].get("message", {}).get("content")
                if content is None:
                    logging.error("Missing content in API response")
                    return None
                return content

            except requests.exceptions.Timeout as exc:
                last_error = exc
                logging.warning(
                    "API timeout on attempt %d/%d: %s", attempt, max_retries, exc
                )
            except (requests.exceptions.ConnectionError, requests.exceptions.SSLError) as exc:
                last_error = exc
                logging.warning(
                    "API connection error on attempt %d/%d: %s",
                    attempt,
                    max_retries,
                    exc,
                )
            except requests.exceptions.RequestException as exc:
                last_error = exc
                logging.warning(
                    "API request error on attempt %d/%d: %s",
                    attempt,
                    max_retries,
                    exc,
                )
            except (json.JSONDecodeError, ValueError, AttributeError) as exc:
                last_error = exc
                logging.warning(
                    "API response parsing error on attempt %d/%d: %s",
                    attempt,
                    max_retries,
                    exc,
                )

            if attempt < max_retries:
                delay = min(2 ** attempt + random.uniform(0, 1), 60.0)
                time.sleep(delay)

        if last_error:
            logging.error("API call failed after %d retries: %s", max_retries, last_error)
        return None

    @staticmethod
    def _retry_after(resp: requests.Response) -> float:
        """Read Retry-After header, defaulting to 1 second."""
        try:
            return float(resp.headers.get("Retry-After", 1.0))
        except (ValueError, TypeError):
            return 1.0
