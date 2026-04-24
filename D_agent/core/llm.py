from __future__ import annotations

import requests
from typing import Any

from configs.config import LLM_BASE_URL, LLM_TIMEOUT
from core.telemetry import log_llm_call

BASE_URL = LLM_BASE_URL
REQUEST_TIMEOUT = LLM_TIMEOUT

MODEL_4B = "gemma-4-e4b-uncensored-hauhaucs-aggressive"


def call_llm(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    trace_role: str = "",
    trace_skills: list[str] | None = None,
    trace_extra: dict[str, Any] | None = None,
) -> str:
    response = requests.post(
        BASE_URL,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    output = data["choices"][0]["message"]["content"]

    if trace_role:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        log_llm_call(
            role=trace_role,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output=output,
            temperature=temperature,
            max_tokens=max_tokens,
            skills=trace_skills or [],
            usage=usage,
            extra=trace_extra or {},
        )

    return output
