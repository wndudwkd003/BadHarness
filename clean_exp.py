from __future__ import annotations

import shutil
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TARGET_DIRS = [
    BASE_DIR / "B_Admin" / "experiments",
    BASE_DIR / "C_Server" / "experiments",
    BASE_DIR / "D_agent" / "experiments",
]


def ensure_safe_target(path: Path) -> None:
    resolved_base = BASE_DIR.resolve()
    resolved_target = path.resolve()

    allowed_targets = {
        (resolved_base / "B_Admin" / "experiments").resolve(),
        (resolved_base / "C_Server" / "experiments").resolve(),
        (resolved_base / "D_agent" / "experiments").resolve(),
    }

    if resolved_target not in allowed_targets:
        raise RuntimeError(f"unsafe target directory: {resolved_target}")


def clean_experiment_dir(path: Path) -> int:
    path.mkdir(parents=True, exist_ok=True)
    ensure_safe_target(path)

    removed = 0
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
            print(f"removed directory: {child}")
            removed += 1
        elif child.is_file():
            child.unlink()
            print(f"removed file: {child}")
            removed += 1

    return removed


def main() -> None:
    total_removed = 0
    for target_dir in TARGET_DIRS:
        removed = clean_experiment_dir(target_dir)
        total_removed += removed
        print(f"done: removed {removed} experiment entries from {target_dir}")

    print(f"all done: removed {total_removed} total experiment entries")


if __name__ == "__main__":
    main()
