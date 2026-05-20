#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
D_EXPERIMENTS_DIR = ROOT_DIR / "experiments"
C_EXPERIMENTS_DIR = ROOT_DIR.parent / "C_Server" / "experiments"
OUTPUT_ROOT = ROOT_DIR / "reports" / "run_validation"

REQUIRED_RECENT_RUNS = 3

DEFAULT_CONFIG = {
    "ENABLE_TOOLS": True,
    "ENABLE_SKILLS": True,
    "ENABLE_PLANNING": True,
    "ENABLE_MEMORY": True,
    "ENABLE_SUMMARIZATION": True,
    "ENABLE_JUDGE": True,
    "ENABLE_LOOP": True,
    "MONITOR_ONLY_MODE": False,
}

DEFAULT_ALLOWED_SKILLS = [
    "reconnaissance",
    "experiment_start",
    "http_enumeration",
    "admin_traffic_interception",
    "mitm_with_bettercap",
    "flag_submission",
    "trigger_prioritization",
]

LABEL_CONFIGS: dict[str, dict[str, Any]] = {
    "baseline_autonomous": {
        "MONITOR_ONLY_MODE": False,
    },
    "baseline_reactive": {
        "MONITOR_ONLY_MODE": True,
    },
    "wo_loop_autonomous": {
        "MONITOR_ONLY_MODE": False,
        "ENABLE_LOOP": False,
    },
    "wo_loop_reactive": {
        "MONITOR_ONLY_MODE": True,
        "ENABLE_LOOP": False,
    },
    "planning_off_autonomous": {
        "MONITOR_ONLY_MODE": False,
        "ENABLE_PLANNING": False,
    },
    "planning_off_reactive": {
        "MONITOR_ONLY_MODE": True,
        "ENABLE_PLANNING": False,
    },
    "planning_off_wo_loop_autonomous": {
        "MONITOR_ONLY_MODE": False,
        "ENABLE_PLANNING": False,
        "ENABLE_LOOP": False,
    },
    "planning_off_wo_loop_reactive": {
        "MONITOR_ONLY_MODE": True,
        "ENABLE_PLANNING": False,
        "ENABLE_LOOP": False,
    },
    "wo_summarization_autonomous": {
        "MONITOR_ONLY_MODE": False,
        "ENABLE_SUMMARIZATION": False,
    },
    "wo_summarization_reactive": {
        "MONITOR_ONLY_MODE": True,
        "ENABLE_SUMMARIZATION": False,
    },
    "wo_judge_autonomous": {
        "MONITOR_ONLY_MODE": False,
        "ENABLE_JUDGE": False,
    },
    "wo_judge_reactive": {
        "MONITOR_ONLY_MODE": True,
        "ENABLE_JUDGE": False,
    },
    "wo_judge_wo_loop_autonomous": {
        "MONITOR_ONLY_MODE": False,
        "ENABLE_JUDGE": False,
        "ENABLE_LOOP": False,
    },
    "wo_judge_wo_loop_reactive": {
        "MONITOR_ONLY_MODE": True,
        "ENABLE_JUDGE": False,
        "ENABLE_LOOP": False,
    },
    "wo_memory_autonomous": {
        "MONITOR_ONLY_MODE": False,
        "ENABLE_MEMORY": False,
    },
    "wo_memory_reactive": {
        "MONITOR_ONLY_MODE": True,
        "ENABLE_MEMORY": False,
    },
    "wo_skills_autonomous": {
        "MONITOR_ONLY_MODE": False,
        "ENABLE_SKILLS": False,
    },
    "wo_skills_reactive": {
        "MONITOR_ONLY_MODE": True,
        "ENABLE_SKILLS": False,
    },
    "wo_signal_selector_autonomous": {
        "MONITOR_ONLY_MODE": False,
        "ALLOWED_SKILLS": [
            "reconnaissance",
            "experiment_start",
            "http_enumeration",
            "admin_traffic_interception",
            "mitm_with_bettercap",
            "flag_submission",
        ],
    },
    "wo_signal_selector_reactive": {
        "MONITOR_ONLY_MODE": True,
        "ALLOWED_SKILLS": [
            "reconnaissance",
            "experiment_start",
            "http_enumeration",
            "admin_traffic_interception",
            "mitm_with_bettercap",
            "flag_submission",
        ],
    },
}

LABEL_ALIASES: dict[str, list[str]] = {
    "baseline_autonomous": ["baseline_autonomous", "loop_on_autonomy"],
    "baseline_reactive": ["baseline_reactive", "loop_on_monitor"],
    "wo_loop_autonomous": ["wo_loop_autonomous", "baseline_autonomy_no_loop"],
    "wo_loop_reactive": ["wo_loop_reactive", "baseline_monitor_no_loop"],
    "planning_off_autonomous": ["planning_off_autonomous", "planning_off_loop_autonomy"],
    "planning_off_reactive": ["planning_off_reactive", "planning_off_loop_monitor"],
    "planning_off_wo_loop_autonomous": ["planning_off_wo_loop_autonomous", "planning_off_no_loop_autonomy"],
    "planning_off_wo_loop_reactive": ["planning_off_wo_loop_reactive", "planning_off_no_loop_monitor"],
    "wo_summarization_autonomous": ["wo_summarization_autonomous", "wo_summarization_autonomy"],
    "wo_summarization_reactive": ["wo_summarization_reactive", "wo_summarization_monitor"],
    "wo_judge_autonomous": ["wo_judge_autonomous"],
    "wo_judge_reactive": ["wo_judge_reactive"],
    "wo_judge_wo_loop_autonomous": ["wo_judge_wo_loop_autonomous"],
    "wo_judge_wo_loop_reactive": ["wo_judge_wo_loop_reactive"],
    "wo_memory_autonomous": ["wo_memory_autonomous", "wo_memory_autonomy"],
    "wo_memory_reactive": ["wo_memory_reactive", "wo_memory_monitor"],
    "wo_skills_autonomous": ["wo_skills_autonomous", "wo_skills_autonomy"],
    "wo_skills_reactive": ["wo_skills_reactive", "wo_skills_monitor"],
    "wo_signal_selector_autonomous": ["wo_signal_selector_autonomous", "wo_signal_selector_autonomy"],
    "wo_signal_selector_reactive": ["wo_signal_selector_reactive", "wo_signal_selector_monitor"],
}

LABELS = list(LABEL_CONFIGS.keys())


@dataclass
class RunRecord:
    label: str
    experiment_id: str
    experiment_dir: Path
    metadata_path: Path | None
    report_summary_path: Path | None
    config_snapshot_path: Path | None
    c_metrics_path: Path | None
    metadata: dict[str, Any]
    report_summary: dict[str, Any]
    c_metrics: dict[str, Any]


def _safe_read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _extract_label(name: str) -> str | None:
    alias_pairs: list[tuple[str, str]] = []
    for canonical, aliases in LABEL_ALIASES.items():
        for alias in aliases:
            alias_pairs.append((canonical, alias))
    for canonical, alias in sorted(alias_pairs, key=lambda item: len(item[1]), reverse=True):
        if name.endswith(f"_{alias}"):
            return canonical
    return None


def _experiment_id_from_dir(name: str) -> str:
    return name.split("_", 2)[0] + "_" + name.split("_", 2)[1]


def _load_grouped_records() -> dict[str, list[RunRecord]]:
    grouped: dict[str, list[RunRecord]] = {label: [] for label in LABELS}
    for path in D_EXPERIMENTS_DIR.iterdir():
        if not path.is_dir():
            continue
        label = _extract_label(path.name)
        if not label:
            continue
        experiment_id = _experiment_id_from_dir(path.name)
        metadata_path = path / "metadata.json"
        report_summary_path = path / "analysis" / "report_summary.json"
        config_snapshot_path = path / "config_snapshot.py"
        c_metrics_path = C_EXPERIMENTS_DIR / experiment_id / "metrics.json"

        grouped[label].append(
            RunRecord(
                label=label,
                experiment_id=experiment_id,
                experiment_dir=path,
                metadata_path=metadata_path if metadata_path.exists() else None,
                report_summary_path=report_summary_path if report_summary_path.exists() else None,
                config_snapshot_path=config_snapshot_path if config_snapshot_path.exists() else None,
                c_metrics_path=c_metrics_path if c_metrics_path.exists() else None,
                metadata=_safe_read_json(metadata_path if metadata_path.exists() else None),
                report_summary=_safe_read_json(report_summary_path if report_summary_path.exists() else None),
                c_metrics=_safe_read_json(c_metrics_path if c_metrics_path.exists() else None),
            )
        )

    for label in grouped:
        grouped[label].sort(
            key=lambda record: (
                int(record.metadata.get("ended_epoch", 0) or 0),
                record.experiment_dir.stat().st_mtime,
                record.experiment_dir.name,
            )
        )
    return grouped


def _validate_record(record: RunRecord) -> dict[str, Any]:
    issues: list[str] = []
    md = record.metadata
    rs = record.report_summary
    cm = record.c_metrics

    if record.metadata_path is None or not md:
        issues.append("missing_metadata")
    if record.report_summary_path is None or not rs:
        issues.append("missing_report_summary")
    if record.config_snapshot_path is None or not record.config_snapshot_path.exists():
        issues.append("missing_config_snapshot")
    if record.c_metrics_path is None or not cm:
        issues.append("missing_c_metrics")

    md_experiment_id = str(md.get("experiment_id", "") or "")
    rs_experiment_id = str(rs.get("experiment_id", "") or "")
    cm_experiment_id = str(cm.get("experiment_id", "") or "")
    if md and md_experiment_id != record.experiment_id:
        issues.append("metadata_experiment_id_mismatch")
    if rs and rs_experiment_id != record.experiment_id:
        issues.append("report_experiment_id_mismatch")
    if cm and cm_experiment_id != record.experiment_id:
        issues.append("c_metrics_experiment_id_mismatch")

    elapsed_md = int(md.get("elapsed_seconds", 0) or 0) if md else 0
    elapsed_rs = int(rs.get("elapsed_seconds", 0) or 0) if rs else 0
    if md and rs and elapsed_md != elapsed_rs:
        issues.append("elapsed_mismatch")
    if md and not str(md.get("stop_reason", "") or "").strip():
        issues.append("missing_stop_reason")
    if md and elapsed_md <= 0:
        issues.append("non_positive_elapsed")

    correct_md = int(md.get("correct_flag_submissions", 0) or 0) if md else 0
    correct_rs = int(rs.get("correct_flag_submissions", 0) or 0) if rs else 0
    incorrect_md = int(md.get("incorrect_flag_submissions", 0) or 0) if md else 0
    incorrect_rs = int(rs.get("incorrect_flag_submissions", 0) or 0) if rs else 0
    if md and rs and correct_md != correct_rs:
        issues.append("correct_count_mismatch")
    if md and rs and incorrect_md != incorrect_rs:
        issues.append("incorrect_count_mismatch")

    captures = cm.get("captures", {}) if cm else {}
    correct_cm = int(captures.get("correct", 0) or 0) if captures else 0
    incorrect_cm = int(captures.get("incorrect", 0) or 0) if captures else 0
    if cm and correct_md != correct_cm:
        issues.append("c_metrics_correct_mismatch")
    if cm and incorrect_md != incorrect_cm:
        issues.append("c_metrics_incorrect_mismatch")
    if cm and str(cm.get("status", "") or "").strip() != "finished":
        issues.append("c_metrics_not_finished")

    valid = not issues
    return {
        "label": record.label,
        "experiment_id": record.experiment_id,
        "experiment_dir": str(record.experiment_dir.resolve()),
        "metadata_path": str(record.metadata_path.resolve()) if record.metadata_path else "",
        "report_summary_path": str(record.report_summary_path.resolve()) if record.report_summary_path else "",
        "config_snapshot_path": str(record.config_snapshot_path.resolve()) if record.config_snapshot_path else "",
        "c_metrics_path": str(record.c_metrics_path.resolve()) if record.c_metrics_path else "",
        "ended_epoch": int(md.get("ended_epoch", 0) or 0) if md else 0,
        "elapsed_seconds": elapsed_md if md else elapsed_rs,
        "correct_flag_submissions": correct_md if md else correct_rs,
        "incorrect_submissions": incorrect_md if md else incorrect_rs,
        "stop_reason": str(md.get("stop_reason", "") or ""),
        "valid": valid,
        "issues": issues,
    }


def _copy_evidence(output_dir: Path, latest: dict[str, list[dict[str, Any]]]) -> None:
    root = output_dir / "evidence"
    for label, rows in latest.items():
        for index, row in enumerate(rows, start=1):
            target_dir = root / label / f"{index:02d}_{row['experiment_id']}"
            target_dir.mkdir(parents=True, exist_ok=True)
            for src_key, name in [
                ("metadata_path", "metadata.json"),
                ("report_summary_path", "report_summary.json"),
                ("config_snapshot_path", "config_snapshot.py"),
                ("c_metrics_path", "c_metrics.json"),
            ]:
                src_text = str(row.get(src_key, "") or "")
                if src_text:
                    src = Path(src_text)
                    if src.exists():
                        shutil.copy2(src, target_dir / name)
            _write_json(target_dir / "validation_record.json", row)


def _build_rerun_plan(invalid_counts: dict[str, int]) -> dict[str, Any]:
    experiments: list[dict[str, Any]] = []
    for label in LABELS:
        count = int(invalid_counts.get(label, 0) or 0)
        if count <= 0:
            continue
        config = dict(LABEL_CONFIGS[label])
        experiments.append(
            {
                "label": label,
                "repeats": count,
                "config": config,
            }
        )

    return {
        "defaults": {
            "rename_with_label": True,
            "reset_before_each_run": True,
            "reset_delay_seconds": 3,
            "repeats": REQUIRED_RECENT_RUNS,
            "config": DEFAULT_CONFIG,
        },
        "experiments": experiments,
    }


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUT_ROOT / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    grouped = _load_grouped_records()
    latest: dict[str, list[dict[str, Any]]] = {}
    validation_rows: list[dict[str, Any]] = []
    label_summary: list[dict[str, Any]] = []
    invalid_counts: dict[str, int] = {}

    for label in LABELS:
        records = grouped.get(label, [])
        selected = records[-REQUIRED_RECENT_RUNS:]
        if len(selected) < REQUIRED_RECENT_RUNS:
            missing = REQUIRED_RECENT_RUNS - len(selected)
            invalid_counts[label] = missing
            label_summary.append(
                {
                    "label": label,
                    "selected_count": len(selected),
                    "valid_count": 0,
                    "invalid_count": missing,
                    "status": "missing_runs",
                    "selected_experiment_ids": "|".join(record.experiment_id for record in selected),
                }
            )
            latest[label] = []
            continue

        row_items = [_validate_record(record) for record in selected]
        valid_count = sum(1 for row in row_items if bool(row["valid"]))
        invalid_count = len(row_items) - valid_count
        invalid_counts[label] = invalid_count
        validation_rows.extend(row_items)
        latest[label] = row_items
        label_summary.append(
            {
                "label": label,
                "selected_count": len(row_items),
                "valid_count": valid_count,
                "invalid_count": invalid_count,
                "status": "ok" if invalid_count == 0 else "rerun_required",
                "selected_experiment_ids": "|".join(row["experiment_id"] for row in row_items),
            }
        )

    rerun_plan = _build_rerun_plan(invalid_counts)

    _write_json(output_dir / "latest_three_validation.json", latest)
    _write_json(output_dir / "rerun_plan.json", rerun_plan)
    _write_json(output_dir / "label_summary.json", label_summary)
    _write_json(
        output_dir / "validation_overview.json",
        {
            "required_recent_runs": REQUIRED_RECENT_RUNS,
            "labels": LABELS,
            "invalid_counts": invalid_counts,
            "rerun_required_labels": [label for label, count in invalid_counts.items() if count > 0],
            "output_dir": str(output_dir),
        },
    )

    _write_csv(
        output_dir / "validation_rows.csv",
        validation_rows,
        [
            "label",
            "experiment_id",
            "experiment_dir",
            "metadata_path",
            "report_summary_path",
            "config_snapshot_path",
            "c_metrics_path",
            "ended_epoch",
            "elapsed_seconds",
            "correct_flag_submissions",
            "incorrect_submissions",
            "stop_reason",
            "valid",
            "issues",
        ],
    )
    _write_csv(
        output_dir / "label_summary.csv",
        label_summary,
        [
            "label",
            "selected_count",
            "valid_count",
            "invalid_count",
            "status",
            "selected_experiment_ids",
        ],
    )
    _copy_evidence(output_dir, latest)

    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "rerun_required_labels": [label for label, count in invalid_counts.items() if count > 0],
                "rerun_plan_path": str((output_dir / "rerun_plan.json").resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
