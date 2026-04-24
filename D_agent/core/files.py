from __future__ import annotations

from pathlib import Path


def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write_text(path: str, content: str) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def append_text(path: str, content: str) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as f:
        f.write(content)
