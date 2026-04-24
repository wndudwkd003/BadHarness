#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.reporting import generate_comparison_report  # noqa: E402


def _load_mapping(path: str) -> dict[str, str]:
    file_path = Path(path)
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("mapping file must contain a JSON object of experiment_id -> label")
    return {str(key): str(value) for key, value in payload.items()}


def _parse_inline_labels(values: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid --label format: {value} (expected experiment_id=label)")
        experiment_id, label = value.split("=", 1)
        mapping[experiment_id.strip()] = label.strip()
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare multiple D_agent experiments and write combined CSV/SVG artifacts.")
    parser.add_argument("experiment_ids", nargs="+", help="one or more experiment folder names")
    parser.add_argument("--label", action="append", default=[], help="inline label mapping: experiment_id=label")
    parser.add_argument("--mapping", help="path to JSON file containing experiment_id -> label mapping")
    parser.add_argument("--output-dir", help="directory for comparison artifacts")
    args = parser.parse_args()

    label_map: dict[str, str] = {}
    if args.mapping:
        label_map.update(_load_mapping(args.mapping))
    label_map.update(_parse_inline_labels(args.label))

    summary = generate_comparison_report(
        experiment_ids=list(args.experiment_ids),
        label_map=label_map,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
