"""API providers for PhoneDriver."""

from .base import BaseProvider
from .kimi_code import KimiCodeProvider
from .openrouter import OpenRouterProvider
from .openai_provider import OpenAIProvider
from .moonshot import MoonshotProvider

__all__ = [
    'BaseProvider',
    'KimiCodeProvider',
    'OpenRouterProvider',
    'OpenAIProvider',
    'MoonshotProvider',
]

PROVIDER_MAP = {
    'kimi_code': KimiCodeProvider,
    'openrouter': OpenRouterProvider,
    'openai': OpenAIProvider,
    'moonshot': MoonshotProvider,
}


def get_provider(provider_name: str, **kwargs):
    """Get provider instance by name."""
    provider_class = PROVIDER_MAP.get(provider_name.lower())
    if not provider_class:
        raise ValueError(f"Unknown provider: {provider_name}. "
                        f"Available: {list(PROVIDER_MAP.keys())}")
    return provider_class(**kwargs)
