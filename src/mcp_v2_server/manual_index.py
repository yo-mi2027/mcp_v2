from __future__ import annotations

import json
import re
import os
from dataclasses import dataclass
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass
class ManualFile:
    manual_id: str
    path: str
    file_type: str


@dataclass
class MdNode:
    kind: str
    node_id: str
    path: str
    title: str
    level: int
    parent_id: str | None
    line_start: int
    line_end: int


def discover_manual_ids(manuals_root: Path) -> list[str]:
    if not manuals_root.exists():
        return []
    items = [p.name for p in manuals_root.iterdir() if p.is_dir()]
    return sorted(items)


def list_manual_files(manuals_root: Path, manual_id: str | None = None) -> list[ManualFile]:
    manual_ids = [manual_id] if manual_id else discover_manual_ids(manuals_root)
    rows: list[ManualFile] = []
    for mid in manual_ids:
        root = manuals_root / mid
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(dirpath)
            dirnames[:] = [d for d in dirnames if not (base / d).is_symlink()]
            for name in filenames:
                path = base / name
                if path.is_symlink() or not path.is_file():
                    continue
                suffix = path.suffix.casefold()
                if suffix not in {".md", ".json"}:
                    continue
                rel = path.relative_to(root).as_posix()
                rows.append(ManualFile(manual_id=mid, path=rel, file_type=suffix[1:]))
    rows.sort(key=lambda x: (x.manual_id, x.path))
    return rows


def _compute_line_end(lines: list[str], headings: list[tuple[int, int, str]]) -> list[int]:
    if not headings:
        return [len(lines)]
    ends: list[int] = []
    for idx, (start, level, _) in enumerate(headings):
        end = len(lines)
        for j in range(idx + 1, len(headings)):
            next_start, next_level, _ = headings[j]
            if next_level <= level:
                end = next_start - 1
                break
        ends.append(max(start, end))
    return ends


def parse_markdown_toc(relative_path: str, text: str) -> list[MdNode]:
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []
    for line_no, line in enumerate(lines, start=1):
        m = HEADING_RE.match(line)
        if m:
            headings.append((line_no, len(m.group(1)), m.group(2).strip()))

    if not headings:
        return [
            MdNode(
                kind="heading",
                node_id=f"{relative_path}#L1",
                path=relative_path,
                title=Path(relative_path).stem or relative_path,
                level=1,
                parent_id=None,
                line_start=1,
                line_end=max(1, len(lines)),
            )
        ]

    ends = _compute_line_end(lines, headings)
    nodes: list[MdNode] = []
    stack: list[MdNode] = []
    for (line_start, level, title), line_end in zip(headings, ends):
        while stack and stack[-1].level >= level:
            stack.pop()
        parent_id = stack[-1].node_id if stack else None
        node = MdNode(
            kind="heading",
            node_id=f"{relative_path}#L{line_start}",
            path=relative_path,
            title=title,
            level=level,
            parent_id=parent_id,
            line_start=line_start,
            line_end=line_end,
        )
        nodes.append(node)
        stack.append(node)
    return nodes


def load_manual_file(manuals_root: Path, manual_id: str, relative_path: str) -> str:
    path = manuals_root / manual_id / relative_path
    return path.read_text(encoding="utf-8")


def json_line_count(text: str) -> int:
    try:
        pretty = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        return len(pretty.splitlines()) or 1
    except json.JSONDecodeError:
        return len(text.splitlines()) or 1
