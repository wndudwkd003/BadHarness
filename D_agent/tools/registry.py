from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from configs.config import ALLOWED_TOOLS
from .curl import send_curl_request
from .nmap import run_nmap_scan
from .start_experiment import start_experiment_tool
from .submit_flag import submit_flag_tool
from .tshark import tshark_tool


@dataclass(frozen=True)
class ToolSpec:
    name: str
    summary: str
    signatures: tuple[str, ...]
    callable_by_agent: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)


TOOL_SPECS: dict[str, ToolSpec] = {
    "start_experiment": ToolSpec(
        name="start_experiment",
        summary="Synchronize the attacker harness with the C server and create a new experiment.",
        signatures=("start_experiment_tool() -> dict",),
    ),
    "submit_flag": ToolSpec(
        name="submit_flag",
        summary="Submit a candidate flag to the C server and return the validation response.",
        signatures=("submit_flag_tool(flag: str) -> dict",),
    ),
    "curl": ToolSpec(
        name="curl",
        summary="Send an HTTP request to the configured C server and inspect status, headers, and body.",
        signatures=(
            "send_curl_request(path: str = '/', method: str = 'GET', data: dict | str | None = None, "
            "cookies: dict[str, str] | None = None, headers: dict[str, str] | None = None, "
            "follow_redirects: bool = False, use_session: bool = True, json_mode: bool = False) -> dict",
        ),
        notes=(
            "Restricted to the configured C server base URL.",
            "Persists cookies per experiment when use_session=True.",
            "Dictionary payloads are sent as form data by default unless json_mode=True.",
        ),
    ),
    "nmap": ToolSpec(
        name="nmap",
        summary="Run a tightly scoped service scan against the configured target host and configured target port.",
        signatures=(
            "run_nmap_scan(host: str | None = None, ports: str | None = None, service_version: bool = True) -> dict",
        ),
        notes=(
            "Restricted to the configured target host and target port.",
        ),
    ),
    "tshark": ToolSpec(
        name="tshark",
        summary="Analyze an existing pcap file and optionally grep for keywords in decoded traffic.",
        signatures=(
            "analyze_pcap(file_path: str, display_filter: str = 'http') -> dict",
            "grep_in_traffic(file_path: str, keyword: str, display_filter: str = 'http') -> dict",
        ),
        notes=(
            "Offline analysis only. Does not capture traffic.",
        ),
    ),
}


TOOL_HANDLERS: dict[str, Callable[..., Any]] = {
    "start_experiment": start_experiment_tool,
    "submit_flag": submit_flag_tool,
    "curl": send_curl_request,
    "nmap": run_nmap_scan,
    "tshark": tshark_tool,
}


def render_tool_catalog(allowed_tools: list[str]) -> str:
    parts: list[str] = []
    for tool_name in allowed_tools:
        spec = TOOL_SPECS.get(tool_name)
        if not spec:
            continue

        callable_line = "yes" if spec.callable_by_agent else "manual-only"
        notes = "\n".join(f"- {note}" for note in spec.notes) or "- none"
        signatures = "\n".join(f"- {signature}" for signature in spec.signatures)
        parts.append(
            "\n".join(
                [
                    f"[tool] {spec.name}",
                    f"summary: {spec.summary}",
                    f"callable_by_agent: {callable_line}",
                    "signatures:",
                    signatures,
                    "notes:",
                    notes,
                ]
            )
        )

    return "\n\n".join(parts).strip()


def invoke_tool(name: str, kwargs: dict[str, Any] | None = None) -> Any:
    spec = TOOL_SPECS.get(name)
    if not spec:
        raise RuntimeError(f"unknown tool: {name}")

    if name not in ALLOWED_TOOLS:
        raise RuntimeError(f"tool '{name}' is not allowed by configuration")

    if not spec.callable_by_agent:
        raise RuntimeError(f"tool '{name}' is manual-only and cannot be called directly by the agent")

    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise RuntimeError(f"tool handler missing: {name}")

    return handler(**(kwargs or {}))
