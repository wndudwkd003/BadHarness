from __future__ import annotations

import shutil
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
EXPERIMENTS_DIR = BASE_DIR / "experiments"


def ensure_safe_target(path: Path) -> None:
    resolved_base = BASE_DIR.resolve()
    resolved_target = path.resolve()
    if resolved_target != (resolved_base / "experiments").resolve():
        raise RuntimeError(f"unsafe target directory: {resolved_target}")


def clean_experiments() -> int:
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_safe_target(EXPERIMENTS_DIR)

    removed = 0
    for child in EXPERIMENTS_DIR.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
            print(f"removed directory: {child}")
            removed += 1
        elif child.is_file():
            child.unlink()
            print(f"removed file: {child}")
            removed += 1

    return removed


if __name__ == "__main__":
    removed_count = clean_experiments()
    print(f"done: removed {removed_count} experiment entries from {EXPERIMENTS_DIR}")
