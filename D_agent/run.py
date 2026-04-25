from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime
from getpass import getpass
from pathlib import Path

from configs.config import (
    AUTO_GENERATE_EXPERIMENT_REPORT,
    DETAIL_PLAN_MAX_SECONDS,
    ENABLE_LOOP,
    ENABLE_MONITORING,
    IDLE_POLL_SECONDS,
    LOOP_MAX_RETRIES_PER_DETAIL,
    LOOP_SLEEP_SECONDS,
    MAX_ACTIONS_PER_TRIGGER,
    MAX_CORRECT_FLAGS_PER_EXPERIMENT,
    MONITOR_ONLY_MODE,
    MONITOR_SIGNAL_SELECTION_LIMIT,
    MONITOR_SUDO_PASSWORD,
)
from core.agent import build_base_prompt, build_detailed_plans, run_cycle
from core.actions import extract_json_payload, load_submitted_flags, submit_flag_action
from core.harness import shutdown_related_servers, start_experiment
from core.llm import MODEL_4B, call_llm
from core.memory import append_history, load_memory, reset_memory
from core.monitor import (
    claim_monitor_signal,
    peek_monitor_signals,
    start_monitor_process,
    stop_monitor_process,
)
from core.plans import load_global_plans
from core.reporting import generate_experiment_report
from core.telemetry import log_skill_usage
from core.workspace import get_runtime_file, prepare_experiment_workspace, update_experiment_metadata


def log(title: str, content: str = "") -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{now}] {title}")
    if content:
        print(content)


def build_experiment_result(
    *,
    experiment_id: str,
    started_at_epoch: int,
    processed_triggers: int,
    correct_flag_submissions: int,
    stop_reason: str,
    stop_detail: str = "",
) -> dict:
    ended_epoch = int(time.time())
    elapsed_seconds = max(0, ended_epoch - int(started_at_epoch))
    result = {
        "experiment_id": experiment_id,
        "processed_triggers": processed_triggers,
        "correct_flag_submissions": correct_flag_submissions,
        "max_correct_flags_per_experiment": MAX_CORRECT_FLAGS_PER_EXPERIMENT,
        "stop_reason": stop_reason,
        "ended_epoch": ended_epoch,
        "ended_at": datetime.fromtimestamp(ended_epoch).strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_seconds": elapsed_seconds,
        "elapsed_human": f"{elapsed_seconds}s",
    }
    if stop_detail:
        result["stop_detail"] = stop_detail
    return result


def collect_submit_flag_metrics(experiment_id: str) -> dict:
    path = get_runtime_file("action_trace.jsonl", experiment_id)
    if not path.exists():
        return {
            "submit_flag_attempts": 0,
            "correct_flag_submissions": 0,
            "incorrect_flag_submissions": 0,
            "duplicate_flag_submissions": 0,
            "invalid_flag_submissions": 0,
            "submit_flag_errors": 0,
        }

    counts = {
        "submit_flag_attempts": 0,
        "correct_flag_submissions": 0,
        "incorrect_flag_submissions": 0,
        "duplicate_flag_submissions": 0,
        "invalid_flag_submissions": 0,
        "submit_flag_errors": 0,
    }

    with Path(path).open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue

            if str(payload.get("action_type", "")).strip() != "submit_flag":
                continue

            counts["submit_flag_attempts"] += 1
            status = str(payload.get("status", "")).strip()
            if status == "correct":
                counts["correct_flag_submissions"] += 1
            elif status == "skipped_duplicate":
                counts["duplicate_flag_submissions"] += 1
            elif status == "invalid_flag_format":
                counts["invalid_flag_submissions"] += 1
            elif status in {"incorrect", "wrong"}:
                counts["incorrect_flag_submissions"] += 1
            elif status in {"error", "request_error"}:
                counts["submit_flag_errors"] += 1
            elif status:
                counts["incorrect_flag_submissions"] += 1

    return counts


def obtain_sudo_password() -> str | None:
    cached = os.environ.get("D_AGENT_SUDO_PASSWORD", "")
    if cached:
        return cached

    configured = MONITOR_SUDO_PASSWORD.strip()
    if configured:
        return configured

    print("monitor sudo password input is hidden.")
    for attempt in range(1, 4):
        password = getpass("sudo password for D monitor: ")
        if not password:
            print("empty password received; please try again.")
            continue

        result = subprocess.run(
            ["sudo", "-S", "-k", "-p", "", "true"],
            input=password + "\n",
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return password

        error = result.stderr.strip() or "sudo password validation failed"
        if attempt < 3:
            print(f"sudo authentication failed ({attempt}/3): {error}")
            continue
        raise RuntimeError(error)

    raise RuntimeError("monitoring requires a valid sudo password")


def build_reactive_global_plan() -> str:
    return "모니터가 의미 있는 신호를 줄 때만 제한적으로 대응하고, 신호가 없으면 에이전트 활동을 중단한 채 대기한다."


def build_signal_selector_prompt() -> str:
    base = build_base_prompt()
    role = """
[signal selector]
너의 임무는 monitor pending signal 후보들 중 지금 가장 먼저 처리할 1개를 선택하는 것이다.
고정 우선순위를 기계적으로 적용하지 말고, 현재 memory, 직전 결과, 이미 제출한 플래그, 후보들의 summary/flags/uri/status/location/created_at를 함께 보고 판단하라.
반드시 후보 목록에 실제로 존재하는 event_key 하나만 선택하라.
출력은 반드시 JSON만 사용한다.
형식:
{
  "event_key": "...",
  "reason": "..."
}
""".strip()
    return f"{base}\n\n{role}"


def summarize_signal_candidates(pending: list[dict]) -> list[dict]:
    return [
        {
            "created_at": item.get("created_at", ""),
            "event_key": item.get("event_key", ""),
            "event_type": item.get("event_type", ""),
            "summary": item.get("summary", ""),
            "flags": item.get("flags", []),
            "src": item.get("src", ""),
            "dst": item.get("dst", ""),
            "method": item.get("method", ""),
            "uri": item.get("uri", ""),
            "status_code": item.get("status_code", ""),
            "location": item.get("location", ""),
            "frame": item.get("frame", ""),
        }
        for item in pending
    ]


def select_monitor_signal(experiment_id: str, pending: list[dict], last_result: str) -> dict | None:
    if not pending:
        return None

    if len(pending) == 1:
        return pending[0]

    candidates = summarize_signal_candidates(pending)
    memory = load_memory()
    submitted_flags = sorted(load_submitted_flags())
    system_prompt = build_signal_selector_prompt()
    user_prompt = f"""
[experiment_id]
{experiment_id}

[current memory]
{memory}

[last result]
{last_result}

[submitted flags]
{json.dumps(submitted_flags, ensure_ascii=False)}

[pending monitor signals]
{json.dumps(candidates, ensure_ascii=False, indent=2)}

지금 가장 먼저 처리할 pending signal 1개를 선택하라.
반드시 후보 목록 안의 event_key만 고르라.
반드시 JSON만 출력하라.
"""

    try:
        log_skill_usage(
            skill="trigger_prioritization",
            phase="signal_selector",
            detail=f"pending_candidates={len(pending)}",
        )
        output = call_llm(
            model=MODEL_4B,
            system_prompt=system_prompt,
            user_prompt=user_prompt.strip(),
            temperature=0.1,
            max_tokens=300,
            trace_role="signal_selector",
            trace_skills=["trigger_prioritization"],
        ).strip()
        payload = extract_json_payload(output)
        selected_event_key = str(payload.get("event_key", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        selected = next(
            (item for item in pending if str(item.get("event_key", "")) == selected_event_key),
            None,
        )
        if selected is None:
            raise ValueError(f"selected event_key is not in pending list: {selected_event_key}")

        append_history(
            "MONITOR SIGNAL SELECT",
            json.dumps(
                {
                    "event_key": selected_event_key,
                    "reason": reason,
                    "event_type": selected.get("event_type", ""),
                    "summary": selected.get("summary", ""),
                },
                ensure_ascii=False,
            ),
        )
        return selected
    except Exception as exc:
        fallback = pending[0]
        append_history(
            "MONITOR SIGNAL SELECT FALLBACK",
            json.dumps(
                {
                    "reason": str(exc),
                    "fallback_event_key": fallback.get("event_key", ""),
                    "fallback_event_type": fallback.get("event_type", ""),
                    "fallback_summary": fallback.get("summary", ""),
                },
                ensure_ascii=False,
            ),
        )
        return fallback


def build_reactive_detail_plan(signal_payload: dict) -> str:
    summary = str(signal_payload.get("summary", "")).strip()
    event_type = str(signal_payload.get("event_type", "")).strip()
    flags = [flag for flag in signal_payload.get("flags", []) if str(flag).strip()]
    uri = str(signal_payload.get("uri", "")).strip()
    status_code = str(signal_payload.get("status_code", "")).strip()
    location = str(signal_payload.get("location", "")).strip()

    if flags:
        joined_flags = ", ".join(flags)
        return (
            f"모니터가 감지한 flag 후보({joined_flags})를 검증하고 근거가 충분하면 즉시 submit_flag를 1회만 수행한다. "
            f"추가 정찰 루프는 금지한다. 관측 요약: {summary or event_type}"
        )

    if event_type == "session_cookie_observed":
        return (
            f"모니터가 새 세션 쿠키를 감지했다. 해당 쿠키 흐름을 확인하는 데 필요한 최소 1단계 행동만 수행한다. "
            f"무의미한 재정찰은 금지한다. 관측 요약: {summary or event_type}"
        )

    if event_type == "session_cookie_used":
        return (
            f"모니터가 세션 쿠키 사용 흔적을 감지했다. 쿠키 재사용 또는 관련 엔드포인트 확인에 필요한 최소 1단계만 수행한다. "
            f"반복 순환 탐색은 금지한다. 관측 요약: {summary or event_type}"
        )

    if event_type == "http_redirect":
        return (
            f"모니터가 HTTP 리다이렉트를 감지했다. location={location or '-'}, status={status_code or '-'}, uri={uri or '-'} "
            f"정보를 바탕으로 의미 있는 후속 확인 1단계만 수행한다. 다른 탐색으로 확장하지 않는다."
        )

    return (
        f"모니터 신호({event_type or 'unknown'})를 확인했고, 관측 요약은 다음과 같다: {summary or '-'} "
        f"이 신호와 직접 관련된 최소 1단계 행동만 수행하고, 근거 없는 반복 탐색은 하지 않는다."
    )


def submit_flags_from_signal(signal_payload: dict) -> tuple[str, bool]:
    flags = [str(flag).strip() for flag in signal_payload.get("flags", []) if str(flag).strip()]
    outputs: list[str] = []
    success = False

    for flag in flags:
        log_skill_usage(skill="flag_submission", phase="signal_direct_submit", detail=flag)
        result = submit_flag_action(flag)
        outputs.append(result)
        if re.search(r'"result"\s*:\s*"correct"', result):
            success = True
            break

    return "\n\n".join(outputs), success


def result_contains_correct_flag(result: str) -> bool:
    return bool(re.search(r'"result"\s*:\s*"correct"', result))


def claim_next_monitor_signal(experiment_id: str, last_result: str) -> dict | None:
    pending = peek_monitor_signals(experiment_id, limit=MONITOR_SIGNAL_SELECTION_LIMIT)
    if not pending:
        return None

    selected = select_monitor_signal(experiment_id, pending, last_result)
    if not selected:
        return None

    return claim_monitor_signal(
        experiment_id,
        event_key=str(selected.get("event_key", "")).strip() or None,
    )


def process_monitor_signal(
    signal_payload: dict,
    *,
    label_prefix: str,
) -> tuple[str, bool]:
    trigger_context = json.dumps(signal_payload, ensure_ascii=False)
    append_history(f"{label_prefix} TRIGGER", trigger_context)
    log(
        f"{label_prefix} TRIGGER",
        (
            f"event_type={signal_payload.get('event_type', '-')}\n"
            f"summary={signal_payload.get('summary', '-')}"
        ),
    )

    if signal_payload.get("flags"):
        result, success = submit_flags_from_signal(signal_payload)
        append_history(f"{label_prefix} RESULT", result)
        log(f"{label_prefix} RESULT", result)
        return result, success

    reactive_global_plan = build_reactive_global_plan()
    reactive_detail_plan = build_reactive_detail_plan(signal_payload)
    result, _, reason = run_cycle(trigger_context, reactive_global_plan, reactive_detail_plan)
    append_history(f"{label_prefix} RESULT", result)
    log(
        f"{label_prefix} RESULT",
        f"{result}\n\n[reason]\n{reason}",
    )
    return result, result_contains_correct_flag(result)


def wait_for_monitor_signal(experiment_id: str, deadline: int, last_result: str) -> dict | None:
    last_wait_log = 0.0
    while time.time() < deadline:
        claimed = claim_next_monitor_signal(experiment_id, last_result)
        if claimed:
            return claimed

        now = time.time()
        if now - last_wait_log >= 30:
            remaining = max(0, int(deadline - now))
            log(
                "MONITOR WAIT",
                (
                    f"experiment_id={experiment_id}\n"
                    f"remaining_seconds={remaining}\n"
                    "status=waiting for monitor trigger"
                ),
            )
            last_wait_log = now

        time.sleep(IDLE_POLL_SECONDS)
    return None


def run_monitor_driven_loop(experiment_id: str, deadline: int, started_at_epoch: int) -> dict:
    trigger_index = 1
    last_result = "initial reactive state"
    correct_flag_submissions = 0

    while time.time() < deadline:
        if correct_flag_submissions >= MAX_CORRECT_FLAGS_PER_EXPERIMENT:
            return build_experiment_result(
                experiment_id=experiment_id,
                started_at_epoch=started_at_epoch,
                processed_triggers=trigger_index - 1,
                correct_flag_submissions=correct_flag_submissions,
                stop_reason="correct_flag_limit_reached",
                stop_detail=f"correct_flag_submissions reached {MAX_CORRECT_FLAGS_PER_EXPERIMENT}",
            )

        signal_payload = wait_for_monitor_signal(experiment_id, deadline, last_result)
        if signal_payload is None:
            return build_experiment_result(
                experiment_id=experiment_id,
                started_at_epoch=started_at_epoch,
                processed_triggers=trigger_index - 1,
                correct_flag_submissions=correct_flag_submissions,
                stop_reason="deadline_reached_or_no_signal",
            )

        remaining = max(0, int(deadline - time.time()))
        global_plan = build_reactive_global_plan()
        detailed_plan = build_reactive_detail_plan(signal_payload)
        trigger_context = json.dumps(signal_payload, ensure_ascii=False)

        append_history("MONITOR TRIGGER", trigger_context)
        log(
            f"TRIGGER #{trigger_index} RECEIVED",
            (
                f"EXPERIMENT: {experiment_id}\n"
                f"REMAINING_SECONDS: {remaining}\n"
                f"EVENT_TYPE: {signal_payload.get('event_type', '-')}\n"
                f"SUMMARY: {signal_payload.get('summary', '-')}"
            ),
        )

        if signal_payload.get("flags"):
            result, success = submit_flags_from_signal(signal_payload)
            append_history("TRIGGER ACTION RESULT", result)
            log("TRIGGER ACTION RESULT", result)
            last_result = result
            if success:
                correct_flag_submissions += 1
                log("TRIGGER COMPLETE", "모니터가 감지한 플래그를 즉시 제출했고 정답으로 확인되었습니다.")
            else:
                log("TRIGGER PAUSED", "플래그 후보를 제출했지만 정답 확인에는 실패했습니다.")
            trigger_index += 1
            continue

        for action_index in range(1, MAX_ACTIONS_PER_TRIGGER + 1):
            result, detail_completed, reason = run_cycle(trigger_context, global_plan, detailed_plan)
            log("TRIGGER ACTION RESULT", result)
            last_result = result

            if detail_completed:
                log("TRIGGER COMPLETE", reason)
                break

            log(
                "TRIGGER PAUSED",
                (
                    f"reason={reason}\n"
                    f"trigger_index={trigger_index}\n"
                    f"action_index={action_index}/{MAX_ACTIONS_PER_TRIGGER}"
                ),
            )

        trigger_index += 1

    return build_experiment_result(
        experiment_id=experiment_id,
        started_at_epoch=started_at_epoch,
        processed_triggers=trigger_index - 1,
        correct_flag_submissions=correct_flag_submissions,
        stop_reason="deadline_reached",
    )


def run_baseline_loop(experiment_id: str, deadline: int, started_at_epoch: int) -> dict:
    last_result = "initial state"
    loop = 1
    global_plans = load_global_plans()
    processed_triggers = 0
    correct_flag_submissions = 0

    while time.time() < deadline:
        for global_index, global_plan in enumerate(global_plans, start=1):
            if time.time() >= deadline:
                break

            detailed_plans = build_detailed_plans(global_plan, last_result)

            for detailed_index, detailed_plan in enumerate(detailed_plans, start=1):
                if time.time() >= deadline:
                    break

                detail_started_at = time.time()
                detail_retry_count = 0

                while time.time() < deadline:
                    monitor_signal = claim_next_monitor_signal(experiment_id, last_result)
                    if monitor_signal is not None:
                        processed_triggers += 1
                        result, success = process_monitor_signal(
                            monitor_signal,
                            label_prefix="BASELINE MONITOR",
                        )
                        last_result = result
                        if success:
                            correct_flag_submissions += 1
                            if correct_flag_submissions >= MAX_CORRECT_FLAGS_PER_EXPERIMENT:
                                return build_experiment_result(
                                    experiment_id=experiment_id,
                                    started_at_epoch=started_at_epoch,
                                    processed_triggers=processed_triggers,
                                    correct_flag_submissions=correct_flag_submissions,
                                    stop_reason="correct_flag_limit_reached",
                                    stop_detail=f"correct_flag_submissions reached {MAX_CORRECT_FLAGS_PER_EXPERIMENT}",
                                )
                        continue

                    if not ENABLE_LOOP and detail_retry_count >= 1:
                        log("DETAIL FORCED ADVANCE", f"{detailed_plan} 단일 실행 모드로 다음 단계로 이동합니다.")
                        break

                    if detail_retry_count >= LOOP_MAX_RETRIES_PER_DETAIL:
                        log(
                            "DETAIL FORCED ADVANCE",
                            f"{detailed_plan} 재시도 한도({LOOP_MAX_RETRIES_PER_DETAIL})에 도달해 다음 단계로 이동합니다.",
                        )
                        break

                    if (time.time() - detail_started_at) >= DETAIL_PLAN_MAX_SECONDS:
                        log(
                            "DETAIL FORCED ADVANCE",
                            f"{detailed_plan} 시간 한도({DETAIL_PLAN_MAX_SECONDS}초)에 도달해 다음 단계로 이동합니다.",
                        )
                        break

                    remaining = max(0, int(deadline - time.time()))
                    print("\n" + "=" * 80)
                    log(
                        f"LOOP #{loop} START",
                        (
                            f"EXPERIMENT: {experiment_id}\n"
                            f"REMAINING_SECONDS: {remaining}\n"
                            f"GLOBAL {global_index}/{len(global_plans)}: {global_plan}\n"
                            f"DETAIL {detailed_index}/{len(detailed_plans)}: {detailed_plan}"
                        ),
                    )

                    result, detail_completed, reason = run_cycle(last_result, global_plan, detailed_plan)
                    log("LOOP RESULT", result)
                    detail_retry_count += 1
                    if result_contains_correct_flag(result):
                        correct_flag_submissions += 1
                        if correct_flag_submissions >= MAX_CORRECT_FLAGS_PER_EXPERIMENT:
                            return build_experiment_result(
                                experiment_id=experiment_id,
                                started_at_epoch=started_at_epoch,
                                processed_triggers=processed_triggers,
                                correct_flag_submissions=correct_flag_submissions,
                                stop_reason="correct_flag_limit_reached",
                                stop_detail=f"correct_flag_submissions reached {MAX_CORRECT_FLAGS_PER_EXPERIMENT}",
                            )

                    if detail_completed:
                        log("DETAIL ADVANCE", f"{detailed_plan} 완료: {reason}")
                        last_result = result
                        loop += 1
                        break

                    log("DETAIL RETRY", f"{detailed_plan} 재시도: {reason}")
                    last_result = result
                    loop += 1
                    time.sleep(LOOP_SLEEP_SECONDS)

        if not ENABLE_LOOP:
            break

    return build_experiment_result(
        experiment_id=experiment_id,
        started_at_epoch=started_at_epoch,
        processed_triggers=processed_triggers,
        correct_flag_submissions=correct_flag_submissions,
        stop_reason="baseline_loop_finished_or_deadline_reached",
    )


def main() -> None:
    sudo_password = None
    if ENABLE_MONITORING:
        sudo_password = obtain_sudo_password()

    experiment = start_experiment()
    experiment_dir = prepare_experiment_workspace(experiment)
    reset_memory()

    experiment_id = str(experiment["experiment_id"])
    run_seed = os.environ.get("BADHARNESS_RUN_SEED", "").strip()
    if run_seed:
        update_experiment_metadata({"run_seed": run_seed}, experiment_id)
    monitor_proc = None

    try:
        if ENABLE_MONITORING:
            monitor_proc = start_monitor_process(experiment_id, sudo_password=sudo_password)
            log("MONITOR STARTED", f"monitor process started for experiment_id={experiment_id}")

        started_epoch = int(experiment["started_epoch"])
        duration_seconds = int(experiment["duration_seconds"])
        deadline = started_epoch + duration_seconds

        log(
            "EXPERIMENT STARTED",
            (
                f"experiment_id={experiment_id}\n"
                f"experiment_dir={experiment_dir}\n"
                f"agent_id={experiment['agent_id']}\n"
                f"technique={experiment['technique']}\n"
                f"duration_seconds={duration_seconds}\n"
                f"deadline_epoch={deadline}\n"
                f"monitor_only_mode={MONITOR_ONLY_MODE}"
            ),
        )

        if MONITOR_ONLY_MODE:
            result_summary = run_monitor_driven_loop(experiment_id, deadline, started_epoch)
        else:
            result_summary = run_baseline_loop(experiment_id, deadline, started_epoch)

        result_summary.update(collect_submit_flag_metrics(experiment_id))
        update_experiment_metadata(result_summary, experiment_id)
        append_history("EXPERIMENT RESULT", json.dumps(result_summary, ensure_ascii=False))
        log(
            "AGENT STOP",
            (
                f"experiment_id={experiment_id}\n"
                f"stop_reason={result_summary.get('stop_reason', '-')}\n"
                f"processed_triggers={result_summary.get('processed_triggers', 0)}\n"
                f"correct_flag_submissions={result_summary.get('correct_flag_submissions', 0)}\n"
                f"incorrect_flag_submissions={result_summary.get('incorrect_flag_submissions', 0)}\n"
                f"duplicate_flag_submissions={result_summary.get('duplicate_flag_submissions', 0)}\n"
                f"elapsed_seconds={result_summary.get('elapsed_seconds', 0)}\n"
                f"elapsed_human={result_summary.get('elapsed_human', '-')}"
            ),
        )

        if AUTO_GENERATE_EXPERIMENT_REPORT:
            try:
                report_summary = generate_experiment_report(experiment_id)
                append_history("EXPERIMENT REPORT", json.dumps(report_summary, ensure_ascii=False))
                log("EXPERIMENT REPORT", json.dumps(report_summary, ensure_ascii=False, indent=2))
            except Exception as exc:
                append_history("EXPERIMENT REPORT ERROR", str(exc))
                log("EXPERIMENT REPORT ERROR", str(exc))

        shutdown_result = shutdown_related_servers(
            reason=str(result_summary.get("stop_reason", "agent_stop")),
            experiment_id=experiment_id,
        )
        append_history("RELATED SERVER SHUTDOWN", json.dumps(shutdown_result, ensure_ascii=False))
        log("RELATED SERVER SHUTDOWN", json.dumps(shutdown_result, ensure_ascii=False, indent=2))

    finally:
        if ENABLE_MONITORING:
            stop_monitor_process(monitor_proc)
            log("MONITOR STOPPED", f"monitor process stopped for experiment_id={experiment_id}")


if __name__ == "__main__":
    main()
