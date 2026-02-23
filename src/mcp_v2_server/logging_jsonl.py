from __future__ import annotations

import json
import sys
import time
from typing import Any


class JsonlLogger:

    def emit(self, *, tool: str, ok: bool, elapsed_ms: int, level: str = "info", **fields: Any) -> None:
        if level != "error":
            return
        payload: dict[str, Any] = {
            "ts": int(time.time() * 1000),
            "level": level,
            "tool": tool,
            "ok": ok,
            "elapsed_ms": elapsed_ms,
        }
        payload.update(fields)
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
