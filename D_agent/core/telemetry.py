from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from core.workspace import get_runtime_file


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_runtime_jsonl(name: str, payload: dict[str, Any], experiment_id: str | None = None) -> None:
    try:
        path = get_runtime_file(name, experiment_id)
    except RuntimeError:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def log_llm_call(
    *,
    role: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    output: str,
    temperature: float,
    max_tokens: int,
    skills: list[str] | None = None,
    usage: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    experiment_id: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "time": _now(),
        "role": role,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "skills": list(skills or []),
        "system_chars": len(system_prompt),
        "user_chars": len(user_prompt),
        "output_chars": len(output),
    }
    if usage:
        payload["usage"] = usage
    if extra:
        payload["extra"] = extra
    append_runtime_jsonl("llm_trace.jsonl", payload, experiment_id)


def log_skill_usage(
    *,
    skill: str,
    phase: str,
    detail: str = "",
    experiment_id: str | None = None,
) -> None:
    append_runtime_jsonl(
        "skill_trace.jsonl",
        {
            "time": _now(),
            "skill": skill,
            "phase": phase,
            "detail": detail,
        },
        experiment_id,
    )


def log_action_trace(
    *,
    action_type: str,
    status: str,
    experiment_id: str | None = None,
    **fields: Any,
) -> None:
    payload = {
        "time": _now(),
        "action_type": action_type,
        "status": status,
    }
    payload.update(fields)
    append_runtime_jsonl("action_trace.jsonl", payload, experiment_id)
