from __future__ import annotations

import math
from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .manual_index import list_manual_files, parse_markdown_toc
from .normalization import normalize_text, split_terms
from .path_guard import resolve_inside_root


@dataclass(frozen=True)
class SparseDoc:
    doc_id: int
    manual_id: str
    path: str
    start_line: int
    heading_id: str | None
    title: str
    raw_text: str
    normalized_text: str
    normalized_title: str
    term_freq: dict[str, int]
    doc_len: int
    file_type: str


@dataclass(frozen=True)
class SparseIndex:
    manual_ids: tuple[str, ...]
    fingerprint: str
    docs: list[SparseDoc]
    docs_by_file: dict[tuple[str, str], list[int]]
    postings: dict[str, list[tuple[int, int]]]
    doc_freq: dict[str, int]
    avg_doc_len: float

    @property
    def total_docs(self) -> int:
        return len(self.docs)


class SparseIndexStore:
    """Small in-memory cache keyed by manual-id scope."""

    def __init__(self, manuals_root: Path, max_scopes: int = 8) -> None:
        self.manuals_root = manuals_root
        self.max_scopes = max(1, int(max_scopes))
        self._items: OrderedDict[str, SparseIndex] = OrderedDict()

    def get_or_build(self, *, manual_ids: list[str], fingerprint: str) -> tuple[SparseIndex, bool]:
        scope_key = "\x1f".join(manual_ids)
        cached = self._items.get(scope_key)
        if cached is not None and cached.fingerprint == fingerprint:
            self._items.move_to_end(scope_key)
            return cached, False

        built = build_sparse_index(self.manuals_root, manual_ids=manual_ids, fingerprint=fingerprint)
        self._items[scope_key] = built
        self._items.move_to_end(scope_key)
        while len(self._items) > self.max_scopes:
            self._items.popitem(last=False)
        return built, True


def build_sparse_index(manuals_root: Path, *, manual_ids: list[str], fingerprint: str) -> SparseIndex:
    docs: list[SparseDoc] = []
    docs_by_file: dict[tuple[str, str], list[int]] = {}
    postings: dict[str, list[tuple[int, int]]] = {}

    for manual_id in manual_ids:
        for row in list_manual_files(manuals_root, manual_id=manual_id):
            full_path = resolve_inside_root(manuals_root / manual_id, row.path, must_exist=True)
            key = (manual_id, row.path)
            doc_ids = docs_by_file.setdefault(key, [])
            try:
                text = full_path.read_text(encoding="utf-8")
            except Exception:
                continue

            if row.file_type == "md":
                lines = text.splitlines()
                nodes = parse_markdown_toc(row.path, text)
                for node in nodes:
                    node_lines = lines[node.line_start - 1 : node.line_end]
                    body_text = "\n".join(node_lines[1:]) if len(node_lines) > 1 else ""
                    term_freq = Counter(split_terms(body_text))
                    doc_len = sum(term_freq.values()) if term_freq else 1
                    doc_id = len(docs)
                    doc = SparseDoc(
                        doc_id=doc_id,
                        manual_id=manual_id,
                        path=row.path,
                        start_line=node.line_start,
                        heading_id=node.node_id,
                        title=node.title,
                        raw_text=body_text,
                        normalized_text=normalize_text(body_text),
                        normalized_title=normalize_text(node.title),
                        term_freq=dict(term_freq),
                        doc_len=doc_len,
                        file_type=row.file_type,
                    )
                    docs.append(doc)
                    doc_ids.append(doc_id)
                    for term, tf in term_freq.items():
                        postings.setdefault(term, []).append((doc_id, int(tf)))
            else:
                term_freq = Counter(split_terms(text))
                doc_len = sum(term_freq.values()) if term_freq else 1
                doc_id = len(docs)
                doc = SparseDoc(
                    doc_id=doc_id,
                    manual_id=manual_id,
                    path=row.path,
                    start_line=1,
                    heading_id=None,
                    title=Path(row.path).name,
                    raw_text=text,
                    normalized_text=normalize_text(text),
                    normalized_title=normalize_text(Path(row.path).name),
                    term_freq=dict(term_freq),
                    doc_len=doc_len,
                    file_type=row.file_type,
                )
                docs.append(doc)
                doc_ids.append(doc_id)
                for term, tf in term_freq.items():
                    postings.setdefault(term, []).append((doc_id, int(tf)))

    doc_freq = {term: len(rows) for term, rows in postings.items()}
    avg_doc_len = (sum(doc.doc_len for doc in docs) / len(docs)) if docs else 1.0
    return SparseIndex(
        manual_ids=tuple(manual_ids),
        fingerprint=fingerprint,
        docs=docs,
        docs_by_file=docs_by_file,
        postings=postings,
        doc_freq=doc_freq,
        avg_doc_len=max(1.0, avg_doc_len),
    )


def bm25_scores(
    index: SparseIndex,
    *,
    query_terms: set[str],
    k1: float = 1.2,
    b: float = 0.75,
) -> dict[int, float]:
    if not query_terms or index.total_docs == 0:
        return {}

    n_docs = float(index.total_docs)
    avgdl = max(1.0, float(index.avg_doc_len))
    scores: dict[int, float] = {}
    for term in query_terms:
        postings = index.postings.get(term)
        if not postings:
            continue
        df = float(index.doc_freq.get(term, 0))
        idf = math.log(1.0 + ((n_docs - df + 0.5) / (df + 0.5)))
        for doc_id, tf in postings:
            doc_len = float(index.docs[doc_id].doc_len)
            denom = float(tf) + k1 * (1.0 - b + b * (doc_len / avgdl))
            if denom <= 0.0:
                continue
            score = idf * ((float(tf) * (k1 + 1.0)) / denom)
            scores[doc_id] = scores.get(doc_id, 0.0) + score
    return scores
