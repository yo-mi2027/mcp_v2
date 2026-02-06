from __future__ import annotations

import re
import unicodedata

SPACE_RE = re.compile(r"\s+")
HYPHEN_RE = re.compile(r"[‐‑–—−]")
DOT_RE = re.compile(r"[･]")
OPEN_PAREN_RE = re.compile(r"[（]")
CLOSE_PAREN_RE = re.compile(r"[）]")
SLASH_RE = re.compile(r"[／]")


def normalize_text(text: str) -> str:
    out = unicodedata.normalize("NFKC", text)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = HYPHEN_RE.sub("-", out)
    out = DOT_RE.sub("・", out)
    out = OPEN_PAREN_RE.sub("(", out)
    out = CLOSE_PAREN_RE.sub(")", out)
    out = SLASH_RE.sub("/", out)
    out = out.casefold()
    out = SPACE_RE.sub(" ", out).strip()
    return out


def split_terms(query: str) -> list[str]:
    normalized = normalize_text(query)
    if not normalized:
        return []
    return [part for part in normalized.split(" ") if part]


def loose_pattern(term: str) -> re.Pattern[str]:
    escaped = [re.escape(ch) for ch in term if ch.strip()]
    glue = r"[\s\-・/()（）]*"
    pattern = glue.join(escaped)
    return re.compile(pattern, flags=re.IGNORECASE)


def loose_contains(term: str, text: str) -> bool:
    if not term.strip():
        return False
    return bool(loose_pattern(term).search(normalize_text(text)))
