from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class AdaptiveStatsWriter:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def tail(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                rows.append(payload)
        if limit <= 0:
            return rows
        return rows[-limit:]

    def manual_find_thresholds(
        self,
        *,
        base_candidate_low: int,
        base_file_bias: float,
        adaptive_tuning: bool,
        min_recall: float = 0.90,
    ) -> tuple[int, float]:
        candidate_low = base_candidate_low
        file_bias = base_file_bias
        if not adaptive_tuning:
            return candidate_low, file_bias

        rows = self.tail(limit=220)
        if not rows:
            return candidate_low, file_bias

        last = rows[-1]
        candidate_low = self._safe_int(last.get("candidate_low_threshold"), candidate_low)
        file_bias = self._safe_float(last.get("file_bias_threshold"), file_bias)

        now_ms = int(time.time() * 1000)
        recent_24h = [
            r
            for r in rows
            if now_ms - self._safe_int(r.get("ts"), now_ms) <= 24 * 60 * 60 * 1000
        ]
        # Spec requires threshold updates to move at most once per 24h.
        can_adjust_now = True
        if recent_24h:
            candidate_values = {
                self._safe_int(r.get("candidate_low_threshold"), candidate_low)
                for r in recent_24h
            }
            file_bias_values = {
                round(self._safe_float(r.get("file_bias_threshold"), file_bias), 2)
                for r in recent_24h
            }
            if len(candidate_values) > 1 or len(file_bias_values) > 1:
                can_adjust_now = False
        if recent_24h and can_adjust_now:
            cutoff_rate = sum(1 for r in recent_24h if r.get("cutoff_reason")) / len(recent_24h)
            if cutoff_rate > 0.20:
                candidate_low -= 1
                file_bias -= 0.03
            elif cutoff_rate < 0.05:
                candidate_low += 1
                file_bias += 0.03

        # Rollback guard: if recall proxy drops by >3% or cutoff rate worsens by >5%
        # over the last two 100-run windows, reset to defaults.
        if len(rows) >= 200:
            prev = rows[-200:-100]
            curr = rows[-100:]
            prev_rate = sum(1 for r in prev if r.get("cutoff_reason")) / len(prev)
            curr_rate = sum(1 for r in curr if r.get("cutoff_reason")) / len(curr)
            prev_recall = self._recall_proxy(prev)
            curr_recall = self._recall_proxy(curr)
            if (prev_recall - curr_recall) > 0.03 or (curr_rate - prev_rate) > 0.05 or curr_recall < min_recall:
                candidate_low = base_candidate_low
                file_bias = base_file_bias

        candidate_low = max(2, min(6, candidate_low))
        file_bias = round(max(0.70, min(0.90, file_bias)), 2)
        return candidate_low, file_bias

    @staticmethod
    def _recall_proxy(rows: list[dict[str, Any]]) -> float:
        if not rows:
            return 0.0
        good = 0
        for row in rows:
            if row.get("cutoff_reason"):
                continue
            if AdaptiveStatsWriter._safe_int(row.get("candidates"), 0) > 0:
                good += 1
                continue
            if AdaptiveStatsWriter._safe_int(row.get("added_evidence_count"), 0) > 0:
                good += 1
        return good / len(rows)

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
