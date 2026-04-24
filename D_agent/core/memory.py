from __future__ import annotations

from datetime import datetime
from pathlib import Path

from configs.config import ENABLE_MEMORY
from core.files import append_text, read_text, write_text
from core.workspace import get_memory_dir


def get_summary_path() -> str:
    return str(Path(get_memory_dir()) / "summary.txt")


def get_history_path() -> str:
    return str(Path(get_memory_dir()) / "history.log")


def get_final_answer_path() -> str:
    return str(Path(get_memory_dir()) / "final_answer.txt")


def load_memory() -> str:
    if not ENABLE_MEMORY:
        return ""

    summary_path = get_summary_path()
    try:
        return read_text(summary_path)
    except FileNotFoundError:
        write_text(summary_path, "")
        return ""


def save_memory(content: str) -> None:
    if not ENABLE_MEMORY:
        return
    write_text(get_summary_path(), content)


def reset_memory() -> None:
    if ENABLE_MEMORY:
        write_text(get_summary_path(), "")
        write_text(get_history_path(), "")
    write_text(get_final_answer_path(), "")


def append_history(title: str, content: str) -> None:
    if not ENABLE_MEMORY:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {title}\n{content}\n\n"
    append_text(get_history_path(), entry)


def save_final_answer(content: str) -> None:
    write_text(get_final_answer_path(), content)


def append_monitor_event(event: str) -> None:
    if not ENABLE_MEMORY:
        return

    clean_event = event.strip()

    if not clean_event:
        return

    append_history("MONITOR EVENT", clean_event)
