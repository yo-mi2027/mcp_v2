from __future__ import annotations

from typing import Any, Literal

try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - runtime fallback when deps are not installed
    class BaseModel:  # type: ignore[override]
        def __init__(self, **kwargs: Any) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def Field(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        return kwargs.get("default_factory", lambda: None)()


class Ref(BaseModel):
    target: Literal["manual", "vault"]
    manual_id: str | None = None
    path: str
    start_line: int | None = None
    json_path: str | None = None
    title: str | None = None
    signals: list[str] = Field(default_factory=list)


class NextAction(BaseModel):
    type: str
    confidence: float | None = None
    params: dict[str, Any] | None = None


class ToolErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
