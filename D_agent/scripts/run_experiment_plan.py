#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import pprint
import random
import re
import statistics
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "configs" / "config.py"
RUN_PATH = ROOT_DIR / "run.py"
EXPERIMENTS_DIR = ROOT_DIR / "experiments"
REPORTS_DIR = ROOT_DIR / "reports" / "plan_runs"


@dataclass
class AssignmentBlock:
    name: str
    start: int
    end: int


def _load_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("plan JSON must contain an object")
    experiments = payload.get("experiments")
    if not isinstance(experiments, list) or not experiments:
        raise ValueError("plan JSON must contain a non-empty 'experiments' list")
    return payload


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    return text or "run"


def _render_literal(value: Any) -> str:
    return pprint.pformat(value, sort_dicts=False, width=88)


def _extract_simple_assignments(source: str) -> dict[str, Any]:
    tree = ast.parse(source, filename=str(CONFIG_PATH))
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        try:
            values[node.targets[0].id] = ast.literal_eval(node.value)
        except Exception:
            continue
    return values


def _find_assignments(source: str) -> dict[str, AssignmentBlock]:
    tree = ast.parse(source, filename=str(CONFIG_PATH))
    blocks: dict[str, AssignmentBlock] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if getattr(node, "lineno", None) is None or getattr(node, "end_lineno", None) is None:
            continue
        name = node.targets[0].id
        blocks[name] = AssignmentBlock(name=name, start=node.lineno - 1, end=node.end_lineno - 1)
    return blocks


def _apply_overrides_to_config(source: str, overrides: dict[str, Any]) -> str:
    lines = source.splitlines()
    blocks = _find_assignments(source)

    unknown = [key for key in overrides if key not in blocks]
    if unknown:
        raise ValueError(f"unknown config keys in plan: {', '.join(sorted(unknown))}")

    replacements: list[tuple[int, int, list[str]]] = []
    for key, value in overrides.items():
        block = blocks[key]
        replacement = f"{key} = {_render_literal(value)}"
        replacements.append((block.start, block.end, replacement.splitlines()))

    replacements.sort(key=lambda item: item[0], reverse=True)
    for start, end, new_lines in replacements:
        lines[start : end + 1] = new_lines

    return "\n".join(lines) + "\n"


def _discover_experiment_dir_from_log(log_text: str) -> tuple[str | None, str | None]:
    experiment_id = None
    experiment_dir = None
    id_match = re.search(r"experiment_id=([0-9]{8}_[0-9]{6})", log_text)
    if id_match:
        experiment_id = id_match.group(1)
    dir_match = re.search(r"experiment_dir=([^\n]+)", log_text)
    if dir_match:
        experiment_dir = dir_match.group(1).strip()
    return experiment_id, experiment_dir


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _safe_read_json_any(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _sanitize_filename(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip()).strip("_") or "group"


def _latest_experiment_dir(before: set[str]) -> Path | None:
    current = {path.name: path for path in EXPERIMENTS_DIR.iterdir() if path.is_dir()}
    new_names = [name for name in current if name not in before]
    if not new_names:
        return None
    new_paths = [current[name] for name in new_names]
    new_paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return new_paths[0]


def _rename_experiment_dir(path: Path, label: str) -> Path:
    target = path.with_name(f"{path.name}_{_slugify(label)}")
    if target.exists():
        stamp = datetime.now().strftime("%H%M%S")
        target = path.with_name(f"{path.name}_{_slugify(label)}_{stamp}")
    path.rename(target)
    return target


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _reset_server_residue(config_values: dict[str, Any], delay_seconds: float) -> dict[str, Any]:
    base_url = str(config_values.get("C_SERVER_BASE_URL", "") or "").strip()
    token = str(config_values.get("BC_SHARED_TOKEN", "") or "").strip()
    timeout = float(config_values.get("START_TIMEOUT", 10) or 10)
    headers = {"X-BC-Token": token} if token else {}
    result: dict[str, Any] = {
        "status_before": None,
        "force_finish": None,
        "status_after": None,
        "delay_seconds": delay_seconds,
    }

    if not base_url:
        result["warning"] = "missing C_SERVER_BASE_URL; skipped server residue reset"
        return result

    try:
        response = requests.get(
            f"{base_url}/api/admin/status",
            headers=headers,
            timeout=timeout,
        )
        result["status_before"] = {
            "http_status": response.status_code,
            "payload": response.json() if response.content else {},
        }
    except Exception as exc:  # noqa: BLE001
        result["status_before"] = {"error": str(exc)}

    try:
        response = requests.post(
            f"{base_url}/api/admin/force-finish-experiment",
            headers=headers,
            timeout=timeout,
        )
        result["force_finish"] = {
            "http_status": response.status_code,
            "payload": response.json() if response.content else {},
        }
    except Exception as exc:  # noqa: BLE001
        result["force_finish"] = {"error": str(exc)}

    if delay_seconds > 0:
        time.sleep(delay_seconds)

    try:
        response = requests.get(
            f"{base_url}/api/admin/status",
            headers=headers,
            timeout=timeout,
        )
        result["status_after"] = {
            "http_status": response.status_code,
            "payload": response.json() if response.content else {},
        }
    except Exception as exc:  # noqa: BLE001
        result["status_after"] = {"error": str(exc)}

    return result


def _run_single_experiment(
    *,
    index: int,
    total: int,
    name: str,
    label: str,
    overrides: dict[str, Any],
    config_source: str,
    rename_with_label: bool,
    logs_dir: Path,
    seed: int | None,
    repeat_index: int,
    repeat_total: int,
    reset_before_run: bool,
    reset_delay_seconds: float,
) -> dict[str, Any]:
    updated_config = _apply_overrides_to_config(config_source, overrides)
    CONFIG_PATH.write_text(updated_config, encoding="utf-8")
    effective_config = _extract_simple_assignments(updated_config)

    before_dirs = {path.name for path in EXPERIMENTS_DIR.iterdir() if path.is_dir()}
    log_path = logs_dir / f"{index:02d}_{_slugify(label or name)}.log"
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    reset_result: dict[str, Any] | None = None

    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"[plan] name={name}\n")
        handle.write(f"[plan] label={label}\n")
        handle.write(f"[plan] started_at={started_at}\n")
        handle.write(f"[plan] repeat_index={repeat_index}\n")
        handle.write(f"[plan] repeat_total={repeat_total}\n")
        handle.write(f"[plan] run_seed={seed if seed is not None else ''}\n")
        handle.write(f"[plan] overrides={json.dumps(overrides, ensure_ascii=False, sort_keys=True)}\n\n")
        if reset_before_run:
            reset_result = _reset_server_residue(effective_config, reset_delay_seconds)
            handle.write("[plan] reset_before_run=true\n")
            handle.write(f"[plan] reset_result={json.dumps(reset_result, ensure_ascii=False, sort_keys=True)}\n\n")
        handle.flush()
        env = dict(os.environ)
        if seed is not None:
            env["BADHARNESS_RUN_SEED"] = str(seed)
            env["PYTHONHASHSEED"] = str(seed)
        env["BADHARNESS_REPEAT_INDEX"] = str(repeat_index)
        env["BADHARNESS_REPEAT_TOTAL"] = str(repeat_total)
        env["BADHARNESS_RUN_LABEL"] = label
        process = subprocess.run(
            [sys.executable, str(RUN_PATH)],
            cwd=str(ROOT_DIR),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )

    log_text = log_path.read_text(encoding="utf-8")
    experiment_id, experiment_dir_text = _discover_experiment_dir_from_log(log_text)
    experiment_path = Path(experiment_dir_text) if experiment_dir_text else None
    if experiment_path is None or not experiment_path.exists():
        experiment_path = _latest_experiment_dir(before_dirs)

    renamed_path = experiment_path
    if experiment_path is not None and experiment_path.exists() and rename_with_label:
        current_name = experiment_path.name
        desired_suffix = f"_{_slugify(label)}"
        if label and not current_name.endswith(desired_suffix):
            renamed_path = _rename_experiment_dir(experiment_path, label)

    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    metadata = _safe_read_json((renamed_path or experiment_path) / "metadata.json") if (renamed_path or experiment_path) else {}
    report_summary = _safe_read_json((renamed_path or experiment_path) / "analysis" / "report_summary.json") if (renamed_path or experiment_path) else {}
    submit_flag_attempts = int(report_summary.get("submit_flag_attempts", metadata.get("submit_flag_attempts", 0)) or 0)
    incorrect_flag_submissions = int(report_summary.get("incorrect_flag_submissions", metadata.get("incorrect_flag_submissions", 0)) or 0)
    duplicate_flag_submissions = int(report_summary.get("duplicate_flag_submissions", metadata.get("duplicate_flag_submissions", 0)) or 0)
    invalid_flag_submissions = int(report_summary.get("invalid_flag_submissions", metadata.get("invalid_flag_submissions", 0)) or 0)
    submit_flag_errors = int(report_summary.get("submit_flag_errors", metadata.get("submit_flag_errors", 0)) or 0)
    return {
        "order": index,
        "total": total,
        "name": name,
        "label": label,
        "repeat_index": repeat_index,
        "repeat_total": repeat_total,
        "run_seed": seed,
        "returncode": process.returncode,
        "started_at": started_at,
        "finished_at": finished_at,
        "log_path": str(log_path),
        "experiment_id": experiment_id or "",
        "experiment_path": str(renamed_path.resolve()) if renamed_path else "",
        "raw_experiment_path": str(experiment_path.resolve()) if experiment_path else "",
        "rename_with_label": rename_with_label,
        "reset_before_run": reset_before_run,
        "reset_delay_seconds": reset_delay_seconds,
        "reset_result": reset_result or {},
        "config_overrides": overrides,
        "elapsed_seconds": int(report_summary.get("elapsed_seconds", metadata.get("elapsed_seconds", 0)) or 0),
        "correct_flag_submissions": int(report_summary.get("correct_flag_submissions", metadata.get("correct_flag_submissions", 0)) or 0),
        "incorrect_flag_submissions": incorrect_flag_submissions,
        "duplicate_flag_submissions": duplicate_flag_submissions,
        "invalid_flag_submissions": invalid_flag_submissions,
        "submit_flag_attempts": submit_flag_attempts,
        "submit_flag_errors": submit_flag_errors,
        "attack_failure_count": incorrect_flag_submissions + duplicate_flag_submissions + invalid_flag_submissions + submit_flag_errors,
        "processed_triggers": int(report_summary.get("processed_triggers", metadata.get("processed_triggers", 0)) or 0),
        "trigger_efficiency": float(report_summary.get("trigger_efficiency", metadata.get("trigger_efficiency", 0)) or 0),
        "total_tokens": int(report_summary.get("total_tokens", metadata.get("total_tokens", 0)) or 0),
    }


def _generate_seeds(repeats: int) -> list[int]:
    rng = random.SystemRandom()
    return [rng.randint(1, 2_147_483_647) for _ in range(repeats)]


def _expand_repeats(item: dict[str, Any], defaults: dict[str, Any]) -> list[dict[str, Any]]:
    repeats = int(item.get("repeats", defaults.get("repeats", 1)) or 1)
    if repeats < 1:
        raise ValueError(f"invalid repeats for experiment '{item.get('name', '-')}'")

    seeds = item.get("seeds")
    if seeds is None:
        seeds = _generate_seeds(repeats)
    elif not isinstance(seeds, list):
        raise ValueError(f"experiment '{item.get('name', '-')}' has non-list 'seeds'")
    else:
        seeds = [int(seed) for seed in seeds]
        if len(seeds) < repeats:
            seeds = seeds + _generate_seeds(repeats - len(seeds))
        elif len(seeds) > repeats:
            seeds = seeds[:repeats]

    expanded: list[dict[str, Any]] = []
    for repeat_index in range(1, repeats + 1):
        expanded.append(
            {
                "name": str(item.get("name") or item.get("label") or f"experiment_{repeat_index}"),
                "label": str(item.get("label") or item.get("name") or f"experiment_{repeat_index}"),
                "config": dict(item.get("config", {})),
                "repeat_index": repeat_index,
                "repeat_total": repeats,
                "run_seed": seeds[repeat_index - 1],
            }
        )
    return expanded


def _mean(rows: list[dict[str, Any]], field: str) -> float:
    values: list[float] = []
    for row in rows:
        try:
            values.append(float(row.get(field, 0) or 0))
        except Exception:
            values.append(0.0)
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stddev(rows: list[dict[str, Any]], field: str) -> float:
    values: list[float] = []
    for row in rows:
        try:
            values.append(float(row.get(field, 0) or 0))
        except Exception:
            values.append(0.0)
    if len(values) <= 1:
        return 0.0
    return statistics.pstdev(values)


def _build_group_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("label", row.get("name", "")))
        grouped.setdefault(key, []).append(row)

    summary_rows: list[dict[str, Any]] = []
    for label, items in sorted(grouped.items(), key=lambda item: item[0]):
        success_count = sum(1 for item in items if int(item.get("returncode", 1)) == 0)
        system_failure_runs = sum(1 for item in items if int(item.get("returncode", 1)) != 0)
        summary_rows.append(
            {
                "label": label,
                "name": str(items[0].get("name", label)),
                "repeat_count": len(items),
                "success_count": success_count,
                "system_failure_runs": system_failure_runs,
                "success_rate": round(success_count / len(items), 6) if items else 0.0,
                "avg_elapsed_seconds": round(_mean(items, "elapsed_seconds"), 3),
                "std_elapsed_seconds": round(_stddev(items, "elapsed_seconds"), 3),
                "avg_correct_flag_submissions": round(_mean(items, "correct_flag_submissions"), 3),
                "std_correct_flag_submissions": round(_stddev(items, "correct_flag_submissions"), 3),
                "avg_incorrect_flag_submissions": round(_mean(items, "incorrect_flag_submissions"), 3),
                "std_incorrect_flag_submissions": round(_stddev(items, "incorrect_flag_submissions"), 3),
                "avg_duplicate_flag_submissions": round(_mean(items, "duplicate_flag_submissions"), 3),
                "avg_invalid_flag_submissions": round(_mean(items, "invalid_flag_submissions"), 3),
                "avg_submit_flag_attempts": round(_mean(items, "submit_flag_attempts"), 3),
                "avg_submit_flag_errors": round(_mean(items, "submit_flag_errors"), 3),
                "avg_attack_failure_count": round(_mean(items, "attack_failure_count"), 3),
                "std_attack_failure_count": round(_stddev(items, "attack_failure_count"), 3),
                "avg_processed_triggers": round(_mean(items, "processed_triggers"), 3),
                "avg_trigger_efficiency": round(_mean(items, "trigger_efficiency"), 6),
                "avg_total_tokens": round(_mean(items, "total_tokens"), 3),
                "experiment_paths": "|".join(str(item.get("experiment_path", "")) for item in items),
            }
        )
    return summary_rows


def _print_run_summary(result: dict[str, Any]) -> None:
    print(
        (
            f"  -> returncode={result.get('returncode', 1)}"
            f", experiment_id={result.get('experiment_id', '-') or '-'}"
            f", elapsed={result.get('elapsed_seconds', 0)}s"
            f", correct={result.get('correct_flag_submissions', 0)}"
            f", incorrect={result.get('incorrect_flag_submissions', 0)}"
            f", failures={result.get('attack_failure_count', 0)}"
            f", triggers={result.get('processed_triggers', 0)}"
        )
    )

    experiment_path = str(result.get("experiment_path", "") or "").strip()
    if experiment_path:
        print(f"  -> experiment_path={experiment_path}")

    log_path = str(result.get("log_path", "") or "").strip()
    if log_path:
        print(f"  -> log={log_path}")


def _load_resume_state(output_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    manifest_path = output_dir / "run_manifest.json"
    scheduled_runs_path = output_dir / "scheduled_runs.json"
    if not manifest_path.exists():
        return [], None

    manifest = _safe_read_json_any(manifest_path, {})
    if not isinstance(manifest, dict):
        return [], None

    previous_rows = manifest.get("experiments", [])
    if not isinstance(previous_rows, list):
        previous_rows = []

    successful_prefix: list[dict[str, Any]] = []
    for row in previous_rows:
        if not isinstance(row, dict):
            break
        if int(row.get("returncode", 1)) != 0:
            break
        successful_prefix.append(row)

    scheduled_runs = _safe_read_json_any(scheduled_runs_path, None)
    if scheduled_runs is not None and not isinstance(scheduled_runs, list):
        scheduled_runs = None
    return successful_prefix, scheduled_runs


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a sequence of D_agent experiments from a JSON plan.")
    parser.add_argument("plan", help="path to a JSON plan file")
    parser.add_argument("--output-dir", help="directory to store logs and manifest")
    parser.add_argument("--continue-on-error", action="store_true", help="continue remaining experiments even if one run fails")
    parser.add_argument("--no-rename", action="store_true", help="do not append experiment labels to created experiment folder names")
    args = parser.parse_args()

    plan_path = Path(args.plan).resolve()
    plan = _load_plan(plan_path)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir).resolve() if args.output_dir else REPORTS_DIR / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    original_config = CONFIG_PATH.read_text(encoding="utf-8")
    if not (output_dir / "config_before_run.py").exists():
        shutil.copyfile(CONFIG_PATH, output_dir / "config_before_run.py")
    if not (output_dir / plan_path.name).exists():
        shutil.copyfile(plan_path, output_dir / plan_path.name)

    defaults = plan.get("defaults", {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        raise ValueError("'defaults' must be a JSON object")

    rename_with_label = not args.no_rename
    if "rename_with_label" in defaults:
        rename_with_label = bool(defaults["rename_with_label"]) and not args.no_rename

    default_config = dict(defaults.get("config", {})) if isinstance(defaults.get("config", {}), dict) else {}
    default_config["AUTO_SHUTDOWN_RELATED_SERVERS_ON_STOP"] = False
    reset_before_each_run = bool(defaults.get("reset_before_each_run", True))
    reset_delay_seconds = float(defaults.get("reset_delay_seconds", 3.0) or 0.0)

    experiment_rows: list[dict[str, Any]] = []
    experiments = plan["experiments"]
    expanded_runs: list[dict[str, Any]] = []
    for item in experiments:
        if not isinstance(item, dict):
            raise ValueError("each experiment entry must be an object")
        expanded_runs.extend(_expand_repeats(item, defaults))

    resumed_rows, persisted_runs = _load_resume_state(output_dir)
    if persisted_runs is not None:
        expanded_runs = persisted_runs
    else:
        (output_dir / "scheduled_runs.json").write_text(
            json.dumps(expanded_runs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    try:
        experiment_rows = list(resumed_rows)
        start_offset = len(resumed_rows)
        if start_offset:
            print(f"[resume] reusing {start_offset} completed runs from {output_dir}")

        for index, item in enumerate(expanded_runs[start_offset:], start=start_offset + 1):
            name = str(item.get("name") or item.get("label") or f"experiment_{index}")
            label = str(item.get("label") or name)
            overrides = dict(default_config)
            per_config = item.get("config", {})
            overrides.update(per_config)
            overrides["AUTO_SHUTDOWN_RELATED_SERVERS_ON_STOP"] = False
            run_reset_before = bool(item.get("reset_before_run", reset_before_each_run))
            run_reset_delay = float(item.get("reset_delay_seconds", reset_delay_seconds) or 0.0)

            print(f"[{index}/{len(expanded_runs)}] running {label} repeat {item['repeat_index']}/{item['repeat_total']}")
            result = _run_single_experiment(
                index=index,
                total=len(expanded_runs),
                name=name,
                label=label,
                overrides=overrides,
                config_source=original_config,
                rename_with_label=rename_with_label,
                logs_dir=logs_dir,
                seed=int(item["run_seed"]),
                repeat_index=int(item["repeat_index"]),
                repeat_total=int(item["repeat_total"]),
                reset_before_run=run_reset_before,
                reset_delay_seconds=run_reset_delay,
            )
            experiment_rows.append(result)
            _print_run_summary(result)
            if result["returncode"] != 0 and not args.continue_on_error:
                break
    finally:
        CONFIG_PATH.write_text(original_config, encoding="utf-8")

    group_summary = _build_group_summary(experiment_rows)
    manifest = {
        "plan_path": str(plan_path),
        "output_dir": str(output_dir),
        "restored_config_path": str(CONFIG_PATH),
        "rename_with_label": rename_with_label,
        "reset_before_each_run": reset_before_each_run,
        "reset_delay_seconds": reset_delay_seconds,
        "continue_on_error": bool(args.continue_on_error),
        "experiment_count": len(experiment_rows),
        "planned_run_count": len(expanded_runs),
        "group_summary": group_summary,
        "experiments": experiment_rows,
    }
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "group_summary.json").write_text(json.dumps(group_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(
        output_dir / "run_manifest.csv",
        [
            {
                "order": row["order"],
                "name": row["name"],
                "label": row["label"],
                "repeat_index": row["repeat_index"],
                "repeat_total": row["repeat_total"],
                "run_seed": row["run_seed"],
                "returncode": row["returncode"],
                "experiment_id": row["experiment_id"],
                "experiment_path": row["experiment_path"],
                "elapsed_seconds": row["elapsed_seconds"],
                "correct_flag_submissions": row["correct_flag_submissions"],
                "incorrect_flag_submissions": row["incorrect_flag_submissions"],
                "duplicate_flag_submissions": row["duplicate_flag_submissions"],
                "invalid_flag_submissions": row["invalid_flag_submissions"],
                "submit_flag_attempts": row["submit_flag_attempts"],
                "submit_flag_errors": row["submit_flag_errors"],
                "attack_failure_count": row["attack_failure_count"],
                "processed_triggers": row["processed_triggers"],
                "log_path": row["log_path"],
            }
            for row in experiment_rows
        ],
        [
            "order",
            "name",
            "label",
            "repeat_index",
            "repeat_total",
            "run_seed",
            "returncode",
            "experiment_id",
            "experiment_path",
            "elapsed_seconds",
            "correct_flag_submissions",
            "incorrect_flag_submissions",
            "duplicate_flag_submissions",
            "invalid_flag_submissions",
            "submit_flag_attempts",
            "submit_flag_errors",
            "attack_failure_count",
            "processed_triggers",
            "log_path",
        ],
    )
    _write_csv(
        output_dir / "group_summary.csv",
        group_summary,
        [
            "label",
            "name",
            "repeat_count",
            "success_count",
            "system_failure_runs",
            "success_rate",
            "avg_elapsed_seconds",
            "std_elapsed_seconds",
            "avg_correct_flag_submissions",
            "std_correct_flag_submissions",
            "avg_incorrect_flag_submissions",
            "std_incorrect_flag_submissions",
            "avg_duplicate_flag_submissions",
            "avg_invalid_flag_submissions",
            "avg_submit_flag_attempts",
            "avg_submit_flag_errors",
            "avg_attack_failure_count",
            "std_attack_failure_count",
            "avg_processed_triggers",
            "avg_trigger_efficiency",
            "avg_total_tokens",
            "experiment_paths",
        ],
    )

    groups_dir = output_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    grouped_rows: dict[str, list[dict[str, Any]]] = {}
    for row in experiment_rows:
        grouped_rows.setdefault(str(row.get("label", row.get("name", ""))), []).append(row)
    for summary_row in group_summary:
        label = str(summary_row.get("label", "group"))
        payload = {
            "label": label,
            "summary": summary_row,
            "runs": grouped_rows.get(label, []),
        }
        (groups_dir / f"{_sanitize_filename(label)}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if all(row["returncode"] == 0 for row in experiment_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
