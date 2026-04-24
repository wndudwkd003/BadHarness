from __future__ import annotations

import re

from core.files import read_text


GLOBAL_PLAN_PATH = "global_plan.md"


def load_global_plans() -> list[str]:
    text = read_text(GLOBAL_PLAN_PATH)
    plans: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = re.match(r"^\d+\.\s*(.+)$", line)
        if match:
            plans.append(match.group(1).strip())

    if not plans:
        raise ValueError("global_plan.md 에서 글로벌 플랜 목록을 찾지 못했습니다.")

    return plans
