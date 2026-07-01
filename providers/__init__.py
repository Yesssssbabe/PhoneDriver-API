"""API providers for PhoneDriver.

C14 fix: providers register themselves using the :func:`register_provider`
decorator. Adding a new provider only requires creating one new file; no
existing file needs to be edited.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Type

from .base import BaseProvider, OpenAICompatibleProvider

_PROVIDER_MAP: Dict[str, Type[BaseProvider]] = {}


def register_provider(name: str):
    """Decorator to register a provider under a canonical name.

    Usage::

        @register_provider("my_provider")
        class MyProvider(BaseProvider):
            ...
    """

    def decorator(cls: Type[BaseProvider]) -> Type[BaseProvider]:
        if not issubclass(cls, BaseProvider):
            raise TypeError(
                f"Provider {cls.__name__} must inherit from BaseProvider"
            )
        _PROVIDER_MAP[name.casefold()] = cls
        return cls

    return decorator


class FailoverProvider(BaseProvider):
    """Wrap multiple providers and fail over on error."""

    def __init__(self, providers: List[BaseProvider]):
        self.providers = providers
        self._current = 0

    def analyze_screenshot(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        for i in range(len(self.providers)):
            provider = self.providers[(self._current + i) % len(self.providers)]
            try:
                result = provider.analyze_screenshot(screenshot_path, user_request, context)
                if result:
                    self._current = (self._current + i) % len(self.providers)
                    return result
            except Exception as exc:
                logging.warning("Provider %d failed: %s", i, exc)
        return None

    def check_task_completion(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        for i in range(len(self.providers)):
            provider = self.providers[(self._current + i) % len(self.providers)]
            try:
                result = provider.check_task_completion(screenshot_path, user_request, context)
                if result and result.get("complete"):
                    self._current = (self._current + i) % len(self.providers)
                    return result
            except Exception as exc:
                logging.warning("Provider %d failed: %s", i, exc)
        return {"complete": False, "reason": "All providers failed", "confidence": 0.0}


def get_provider(provider_name: str, **kwargs: Any) -> BaseProvider:
    """Get provider instance by name."""
    provider_class = _PROVIDER_MAP.get(provider_name.casefold())
    if not provider_class:
        raise ValueError(
            f"Unknown provider: {provider_name}. "
            f"Available: {list(_PROVIDER_MAP.keys())}"
        )
    return provider_class(**kwargs)


def available_providers() -> List[str]:
    """Return a sorted list of registered provider names."""
    return sorted(_PROVIDER_MAP.keys())


# Auto-discover / register providers by importing their modules.
from . import kimi_code
from . import moonshot
from . import openai_provider
from . import openrouter

# Re-export concrete provider classes for convenience.
from .kimi_code import KimiCodeProvider
from .moonshot import MoonshotProvider
from .openai_provider import OpenAIProvider
from .openrouter import OpenRouterProvider


__all__ = [
    "BaseProvider",
    "OpenAICompatibleProvider",
    "FailoverProvider",
    "register_provider",
    "get_provider",
    "available_providers",
    "KimiCodeProvider",
    "OpenRouterProvider",
    "OpenAIProvider",
    "MoonshotProvider",
]
