from __future__ import annotations

from pathlib import Path

from core.files import read_text
from configs.config import (
    ALLOWED_SKILLS,
    ALLOWED_TOOLS,
    ENABLE_SKILLS,
    ENABLE_TOOLS,
)
from tools.registry import render_tool_catalog


BASE_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = BASE_DIR / "tools"
SKILLS_DIR = BASE_DIR / "skills"


def load_tool_documents() -> str:
    if not ENABLE_TOOLS:
        return ""
    return render_tool_catalog(ALLOWED_TOOLS)


def load_skill_documents() -> str:
    if not ENABLE_SKILLS:
        return ""

    parts: list[str] = []
    for skill_name in ALLOWED_SKILLS:
        path = SKILLS_DIR / f"{skill_name}.md"
        if path.exists():
            parts.append(read_text(str(path.relative_to(BASE_DIR))))

    return "\n\n".join(parts).strip()
