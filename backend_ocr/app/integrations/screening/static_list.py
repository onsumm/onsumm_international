"""StaticListScreening: wallet and name screening against a local JSON list.

Hackathon provider. The list (sanctioned_addresses.json, path from
SCREENING_LIST_PATH) holds a few publicly listed sanctioned addresses plus
clearly labelled demo entries. Production swaps this for an API provider behind
the same `ScreeningProvider` interface.

A list that cannot be read answers ERROR, never CLEAR: an empty list would
silently pass every wallet.
"""
from __future__ import annotations

import json
import threading
from typing import Optional

from .base import ScreeningResult


class StaticListScreening:
    name = "static_list"

    def __init__(self, list_path: str):
        self.list_path = list_path
        self._lock = threading.Lock()
        self._addresses: Optional[dict[str, dict]] = None
        self._names: Optional[dict[str, dict]] = None
        self._error: Optional[str] = None

    def reload(self) -> int:
        with self._lock:
            try:
                with open(self.list_path, encoding="utf-8") as fh:
                    data = json.load(fh)
                self._addresses = {e["address"].lower(): e for e in data.get("addresses", [])}
                self._names = {e["name"].strip().lower(): e for e in data.get("names", [])}
                self._error = None
            except Exception as e:  # missing file, bad JSON
                self._addresses, self._names, self._error = None, None, str(e)
            return len(self._addresses or {})

    def _ensure_loaded(self) -> None:
        if self._addresses is None and self._error is None:
            self.reload()

    def screen(self, chain: str, address: str) -> ScreeningResult:
        self._ensure_loaded()
        if self._addresses is None:
            return ScreeningResult("ERROR", self.name, raw={"error": self._error or "list not loaded"})
        hit = self._addresses.get((address or "").lower())
        if hit:
            return ScreeningResult("HIT", self.name, risk_score=100.0,
                                   categories=(hit.get("category", "sanctions"),),
                                   raw={"label": hit.get("label"), "chain": chain})
        return ScreeningResult("CLEAR", self.name, risk_score=0.0, raw={"chain": chain})

    def screen_name(self, name: str, country_code: Optional[str] = None) -> ScreeningResult:
        self._ensure_loaded()
        if self._names is None:
            return ScreeningResult("ERROR", self.name, raw={"error": self._error or "list not loaded"})
        hit = self._names.get((name or "").strip().lower())
        if hit:
            return ScreeningResult("HIT", self.name, risk_score=100.0,
                                   categories=(hit.get("category", "sanctions"),),
                                   raw={"label": hit.get("label"), "country_code": country_code})
        return ScreeningResult("CLEAR", self.name, risk_score=0.0, raw={"country_code": country_code})
