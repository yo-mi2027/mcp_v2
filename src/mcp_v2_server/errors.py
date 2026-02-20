from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ALLOWED_ERROR_CODES = {
    "invalid_parameter",
    "not_found",
    "invalid_path",
    "out_of_scope",
    "needs_narrow_scope",
    "forbidden",
    "invalid_scope",
    "conflict",
}


@dataclass
class ToolError(Exception):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.code not in ALLOWED_ERROR_CODES:
            self.code = "invalid_parameter"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            data["details"] = self.details
        return data


def ensure(condition: bool, code: str, message: str, details: dict[str, Any] | None = None) -> None:
    if not condition:
        raise ToolError(code=code, message=message, details=details)
