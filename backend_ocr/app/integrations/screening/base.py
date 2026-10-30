"""Screening provider interface: is this wallet (or person) safe to pay?

A provider answers CLEAR, HIT or ERROR. ERROR is a real answer, not an
exception: callers fail closed on it (SCREENING_UNAVAILABLE), because paying an
unscreened wallet is worse than a delayed payment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass(frozen=True)
class ScreeningResult:
    result: str                      # CLEAR | HIT | ERROR
    provider: str
    risk_score: Optional[float] = None
    categories: tuple = field(default_factory=tuple)
    raw: dict = field(default_factory=dict)

    @property
    def is_hit(self) -> bool:
        return self.result == "HIT"


class ScreeningProvider(Protocol):
    name: str

    def screen(self, chain: str, address: str) -> ScreeningResult: ...

    def screen_name(self, name: str, country_code: Optional[str] = None) -> ScreeningResult: ...
