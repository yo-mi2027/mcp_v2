from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass
class TraceEntry:
    created_at: float
    payload: dict[str, Any]


class TraceStore:
    def __init__(self, max_keep: int, ttl_sec: int) -> None:
        self.max_keep = max_keep
        self.ttl_sec = ttl_sec
        self._items: OrderedDict[str, TraceEntry] = OrderedDict()

    def _cleanup(self) -> None:
        now = time.time()
        expired = [key for key, entry in self._items.items() if now - entry.created_at > self.ttl_sec]
        for key in expired:
            self._items.pop(key, None)
        while len(self._items) > self.max_keep:
            self._items.popitem(last=False)

    def create(self, payload: dict[str, Any]) -> str:
        self._cleanup()
        trace_id = uuid.uuid4().hex
        self._items[trace_id] = TraceEntry(created_at=time.time(), payload=payload)
        self._cleanup()
        return trace_id

    def get(self, trace_id: str) -> dict[str, Any] | None:
        self._cleanup()
        entry = self._items.get(trace_id)
        if entry is None:
            return None
        return entry.payload
