from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class RerankDiagnostics:
    applied: bool
    mode: str
    reason: str | None
    scored: int


class _HFPairReranker:
    def __init__(self, *, model_id: str, device: str) -> None:
        self.model_id = model_id
        self.device = device
        self._lock = threading.Lock()
        self._loaded = False
        self._torch = None
        self._tokenizer = None
        self._model = None
        self._resolved_device = "cpu"

    def _load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            import torch  # type: ignore[import-not-found]
            from transformers import AutoModelForSequenceClassification, AutoTokenizer  # type: ignore[import-not-found]

            tokenizer = AutoTokenizer.from_pretrained(self.model_id, trust_remote_code=True)
            model = AutoModelForSequenceClassification.from_pretrained(self.model_id, trust_remote_code=True)

            resolved = self.device.strip().lower() if self.device else "auto"
            if resolved == "auto":
                resolved = "cuda" if torch.cuda.is_available() else "cpu"
            if resolved.startswith("cuda") and not torch.cuda.is_available():
                resolved = "cpu"
            model.to(resolved)
            model.eval()

            self._torch = torch
            self._tokenizer = tokenizer
            self._model = model
            self._resolved_device = resolved
            self._loaded = True

    def score_pairs(
        self,
        *,
        query: str,
        documents: list[str],
        max_length: int,
        batch_size: int,
    ) -> list[float]:
        self._load()
        assert self._torch is not None
        assert self._tokenizer is not None
        assert self._model is not None

        out: list[float] = []
        step = max(1, int(batch_size))
        for offset in range(0, len(documents), step):
            batch = documents[offset : offset + step]
            encoded = self._tokenizer(
                [query] * len(batch),
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=max(32, int(max_length)),
            )
            encoded = {k: v.to(self._resolved_device) for k, v in encoded.items()}
            with self._torch.inference_mode():
                logits = self._model(**encoded).logits
            if logits.dim() == 1:
                values = logits
            elif logits.dim() == 2 and logits.size(-1) == 1:
                values = logits.squeeze(-1)
            elif logits.dim() == 2:
                values = logits[:, -1]
            else:
                values = logits.reshape(logits.size(0), -1)[:, -1]
            out.extend(float(v) for v in values.detach().cpu().tolist())
        return out


_MODEL_CACHE_LOCK = threading.Lock()
_MODEL_CACHE: dict[tuple[str, str], _HFPairReranker] = {}


def _get_model(model_id: str, device: str) -> _HFPairReranker:
    key = (model_id, device)
    with _MODEL_CACHE_LOCK:
        model = _MODEL_CACHE.get(key)
        if model is None:
            model = _HFPairReranker(model_id=model_id, device=device)
            _MODEL_CACHE[key] = model
        return model


def score_query_documents(
    *,
    query: str,
    documents: list[str],
    model_id: str,
    device: str,
    max_length: int,
    batch_size: int,
) -> tuple[list[float] | None, RerankDiagnostics]:
    if not documents:
        return [], RerankDiagnostics(applied=False, mode="empty", reason=None, scored=0)
    try:
        model = _get_model(model_id=model_id, device=device)
        scores = model.score_pairs(
            query=query,
            documents=documents,
            max_length=max_length,
            batch_size=batch_size,
        )
    except ImportError as exc:
        return None, RerankDiagnostics(
            applied=False,
            mode="import_error",
            reason=f"{type(exc).__name__}: {exc}",
            scored=0,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        return None, RerankDiagnostics(
            applied=False,
            mode="runtime_error",
            reason=f"{type(exc).__name__}: {exc}",
            scored=0,
        )
    if len(scores) != len(documents):
        return None, RerankDiagnostics(
            applied=False,
            mode="invalid_scores",
            reason="score count mismatch",
            scored=0,
        )
    return scores, RerankDiagnostics(applied=True, mode="applied", reason=None, scored=len(scores))
