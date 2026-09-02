"""File IO helpers for private traces and public artifacts."""

from __future__ import annotations

import json
import re
import gzip
from pathlib import Path
from typing import Any, Iterable, Iterator

META_KEY_RE = re.compile(r'(?<!\\)"meta"\s*:')
JSON_DECODER = json.JSONDecoder()


def ensure_dir(path: str | Path) -> Path:
    out = Path(path)
    out.mkdir(parents=True, exist_ok=True)
    return out


def find_trace_files(trace_path: str | Path) -> list[Path]:
    raw_input = str(trace_path)
    if any(char in raw_input for char in "*?[]"):
        return [Path(p) for p in sorted(Path().glob(raw_input) if not raw_input.startswith("/") else glob_absolute(raw_input))]

    root = Path(trace_path)
    if root.is_file():
        return [root]

    direct = root / "raw_data.jsonl"
    if direct.exists():
        return [direct]

    daily_nested: list[Path] = []
    daily_direct: list[Path] = []
    for path in sorted(root.glob("data_*/**/raw_data.jsonl")):
        rel_parts = path.relative_to(root).parts
        if len(rel_parts) == 2:
            daily_direct.append(path)
        elif len(rel_parts) > 2:
            daily_nested.append(path)

    if daily_nested:
        partition_keys = {path.relative_to(root).parts[1:-1] for path in daily_nested}
        if len(partition_keys) > 1:
            raise ValueError(
                "Input root contains multiple private workload partitions. "
                "Point --trace to the single benchmark trace directory, or pass a glob that matches only it."
            )
        return daily_nested

    if daily_direct:
        return daily_direct

    return sorted(root.glob("**/raw_data.jsonl"))


def glob_absolute(pattern: str) -> list[Path]:
    import glob

    return [Path(p) for p in sorted(glob.glob(pattern, recursive=True))]


def iter_jsonl(paths: Iterable[str | Path], limit: int | None = None) -> Iterator[tuple[Path, dict[str, Any]]]:
    seen = 0
    for path_like in paths:
        path = Path(path_like)
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    yield path, json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON at {path}:{line_no}: {exc}") from exc
                seen += 1
                if limit is not None and seen >= limit:
                    return


def iter_jsonl_lines(paths: Iterable[str | Path], limit: int | None = None) -> Iterator[tuple[Path, int, str]]:
    seen = 0
    for path_like in paths:
        path = Path(path_like)
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                yield path, line_no, line
                seen += 1
                if limit is not None and seen >= limit:
                    return


def extract_meta_from_line(line: str) -> dict[str, Any]:
    match = META_KEY_RE.search(line)
    if not match:
        return {}
    try:
        obj, _ = JSON_DECODER.raw_decode(line[match.end() :].lstrip())
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def write_json(path: str | Path, obj: Any, *, pretty: bool = False) -> None:
    target = Path(path)
    ensure_dir(target.parent)
    with target.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(obj, f, indent=2, sort_keys=True)
        else:
            json.dump(obj, f, sort_keys=True, separators=(",", ":"))
        f.write("\n")


def write_json_gzip(path: str | Path, obj: Any) -> None:
    target = Path(path)
    ensure_dir(target.parent)
    with gzip.open(target, "wt", encoding="utf-8") as f:
        json.dump(obj, f, sort_keys=True, separators=(",", ":"))
        f.write("\n")


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    target = Path(path)
    ensure_dir(target.parent)
    count = 0
    with target.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=True))
            f.write("\n")
            count += 1
    return count
