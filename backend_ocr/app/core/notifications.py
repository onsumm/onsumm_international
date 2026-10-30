"""Cache behind the notification badge count.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations


_notif_cache: dict = {}   # key → (count, expires_at)

_NOTIF_TTL = 30           # 30-second cache — count changes only when bills are actioned
