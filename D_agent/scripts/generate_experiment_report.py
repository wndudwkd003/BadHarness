#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.reporting import generate_experiment_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate CSV/SVG/JSON report artifacts for one D_agent experiment.")
    parser.add_argument("experiment_id", help="experiment folder name, e.g. 20260424_210206")
    args = parser.parse_args()

    summary = generate_experiment_report(args.experiment_id)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
