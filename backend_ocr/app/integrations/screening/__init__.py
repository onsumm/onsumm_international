"""Wallet and name screening providers. `get_screening()` returns the configured one."""
from __future__ import annotations

from typing import Optional

from .base import ScreeningProvider, ScreeningResult
from .static_list import StaticListScreening

_provider: Optional[ScreeningProvider] = None


def get_screening() -> ScreeningProvider:
    global _provider
    if _provider is None:
        from ...domain.crypto.settings import get_settings
        _provider = StaticListScreening(get_settings().screening_list_path)
    return _provider


__all__ = ["ScreeningProvider", "ScreeningResult", "StaticListScreening", "get_screening"]
