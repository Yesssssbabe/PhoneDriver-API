"""API providers for PhoneDriver.

C14 fix: providers register themselves using the :func:`register_provider`
decorator. Adding a new provider only requires creating one new file; no
existing file needs to be edited.
"""
from __future__ import annotations

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
    "register_provider",
    "get_provider",
    "available_providers",
    "KimiCodeProvider",
    "OpenRouterProvider",
    "OpenAIProvider",
    "MoonshotProvider",
]
