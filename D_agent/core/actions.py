from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

from configs.config import ALLOWED_TOOLS, ENABLE_SUBMITTED_FLAG_DEDUP, ENABLE_TOOLS
from core.harness import submit_flag
from core.memory import save_final_answer
from core.telemetry import log_action_trace
from core.workspace import get_submitted_flags_path
from tools.registry import invoke_tool


FLAG_PATTERN = re.compile(r"flag\{[^}\n]+\}")
TOOL_BINARIES = {
    "nmap": "nmap",
    "curl": "curl",
    "tshark": "tshark",
}


def extract_json_payload(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.startswith("```")]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or start >= end:
            raise
        return json.loads(text[start : end + 1])


def build_allowed_binaries() -> set[str]:
    return {
        binary
        for tool_name, binary in TOOL_BINARIES.items()
        if tool_name in ALLOWED_TOOLS
    }


def run_shell(command: str) -> str:
    print(f"\n[EXEC] {command}")

    if not ENABLE_TOOLS:
        return (
            f"[command]\n{command}\n\n"
            "[returncode]\n1\n\n"
            "[stderr]\ntool execution is disabled by configuration"
        )

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return (
            f"[command]\n{command}\n\n"
            "[returncode]\n1\n\n"
            f"[stderr]\ninvalid shell command: {exc}"
        )

    if not tokens:
        return (
            f"[command]\n{command}\n\n"
            "[returncode]\n1\n\n"
            "[stderr]\nempty command"
        )

    binary = Path(tokens[0]).name
    allowed_binaries = build_allowed_binaries()
    if binary not in allowed_binaries:
        return (
            f"[command]\n{command}\n\n"
            "[returncode]\n1\n\n"
            f"[stderr]\ncommand '{binary}' is not allowed by configuration"
        )

    result = subprocess.run(
        tokens,
        capture_output=True,
        text=True,
    )
    log_action_trace(
        action_type="shell",
        status="success" if result.returncode == 0 else "error",
        command=command,
        binary=binary,
        returncode=result.returncode,
    )

    return (
        f"[command]\n{command}\n\n"
        f"[returncode]\n{result.returncode}\n\n"
        f"[stdout]\n{result.stdout}\n\n"
        f"[stderr]\n{result.stderr}"
    )


def load_submitted_flags() -> set[str]:
    try:
        path = get_submitted_flags_path()
    except RuntimeError:
        return set()

    if not path.exists():
        return set()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()

    if not isinstance(payload, list):
        return set()

    return {str(item).strip() for item in payload if str(item).strip()}


def save_submitted_flags(flags: set[str]) -> None:
    try:
        path = get_submitted_flags_path()
    except RuntimeError:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(flags), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def submit_flag_action(flag: str) -> str:
    clean_flag = flag.strip()
    if not FLAG_PATTERN.fullmatch(clean_flag):
        log_action_trace(
            action_type="submit_flag",
            status="invalid_flag_format",
            flag=clean_flag,
        )
        return (
            f"[action]\nsubmit_flag\n\n"
            f"[flag]\n{clean_flag}\n\n"
            "[status]\ninvalid_flag_format\n\n"
            "[reason]\nsubmit_flag accepts only a concrete flag{...} string"
        )

    submitted_flags = load_submitted_flags()
    if ENABLE_SUBMITTED_FLAG_DEDUP and clean_flag in submitted_flags:
        log_action_trace(
            action_type="submit_flag",
            status="skipped_duplicate",
            flag=clean_flag,
        )
        return (
            f"[action]\nsubmit_flag\n\n"
            f"[flag]\n{clean_flag}\n\n"
            "[status]\nskipped_duplicate\n\n"
            "[reason]\nthis flag was already submitted in the current experiment"
        )

    try:
        save_final_answer(clean_flag)
    except RuntimeError:
        pass

    try:
        response = submit_flag(clean_flag)
        if ENABLE_SUBMITTED_FLAG_DEDUP:
            submitted_flags.add(clean_flag)
            save_submitted_flags(submitted_flags)
        result_status = str(response.get("result", response.get("status", "ok")))
        log_action_trace(
            action_type="submit_flag",
            status=result_status,
            flag=clean_flag,
            response=response,
        )
        return (
            f"[action]\nsubmit_flag\n\n"
            f"[flag]\n{clean_flag}\n\n"
            "[response]\n"
            f"{json.dumps(response, ensure_ascii=False, indent=2)}"
        )
    except Exception as exc:
        log_action_trace(
            action_type="submit_flag",
            status="error",
            flag=clean_flag,
            error=str(exc),
        )
        return (
            f"[action]\nsubmit_flag\n\n"
            f"[flag]\n{clean_flag}\n\n"
            f"[error]\n{exc}"
        )


def maybe_submit_from_content(content: str) -> str:
    match = FLAG_PATTERN.search(content)
    if not match:
        return content

    submit_result = submit_flag_action(match.group(0))
    return f"{content}\n\n{submit_result}"


def noop_action(reason: str) -> str:
    log_action_trace(action_type="noop", status="ok", reason=reason)
    return f"[action]\nnoop\n\n[reason]\n{reason}"


def tool_action(name: str, kwargs: dict) -> str:
    try:
        response = invoke_tool(name, kwargs)
        log_action_trace(
            action_type="tool",
            status="success",
            tool_name=name,
            kwargs=kwargs,
            response=response,
        )
        return (
            f"[action]\ntool\n\n"
            f"[tool_name]\n{name}\n\n"
            "[response]\n"
            f"{json.dumps(response, ensure_ascii=False, indent=2)}"
        )
    except Exception as exc:
        log_action_trace(
            action_type="tool",
            status="error",
            tool_name=name,
            kwargs=kwargs,
            error=str(exc),
        )
        return (
            f"[action]\ntool\n\n"
            f"[tool_name]\n{name}\n\n"
            f"[error]\n{exc}"
        )


def dispatch_action(action_json: str) -> str:
    try:
        data = extract_json_payload(action_json)
    except Exception as exc:
        return (
            "[action_error]\ninvalid_action_json\n\n"
            f"[raw]\n{action_json}\n\n"
            f"[error]\n{exc}"
        )

    if "action" not in data:
        return (
            "[action_error]\nmissing_action_field\n\n"
            f"[raw]\n{action_json}"
        )

    action = data["action"]
    args = data.get("args", {})

    print(f"\n[ACTION] {action}")
    print(f"[ARGS] {args}")

    if action == "shell":
        command = str(args.get("command", "")).strip()
        if not command:
            return "[action_error]\nmissing_shell_command"
        return run_shell(command)

    if action == "tool":
        name = str(args.get("name", "")).strip()
        if not name:
            return "[action_error]\nmissing_tool_name"
        kwargs = args.get("kwargs", {})
        if not isinstance(kwargs, dict):
            return "[action_error]\ninvalid_tool_kwargs"
        return tool_action(name, kwargs)

    if action == "submit_flag":
        flag = str(args.get("flag", "")).strip()
        if not flag:
            return "[action_error]\nmissing_flag"
        return submit_flag_action(flag)

    if action == "final_answer":
        content = str(args.get("content", ""))
        try:
            save_final_answer(content)
        except RuntimeError:
            pass
        return maybe_submit_from_content(content)

    if action == "noop":
        return noop_action(str(args.get("reason", "")).strip() or "no operation requested")

    return f"[action_error]\nunknown_action\n\n[action]\n{action}"
