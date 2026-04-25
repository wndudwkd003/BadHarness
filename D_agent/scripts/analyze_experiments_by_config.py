#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.reporting import generate_experiment_report  # noqa: E402


TIME_FORMAT = "%Y%m%d_%H%M%S"
DEFAULT_EXPERIMENTS_DIR = ROOT_DIR / "experiments"
DEFAULT_REPORTS_DIR = ROOT_DIR / "reports" / "config_analysis"

RELEVANT_CONFIG_KEYS = [
    "ENABLE_TOOLS",
    "ENABLE_SKILLS",
    "ENABLE_PLANNING",
    "ENABLE_MEMORY",
    "ENABLE_SUMMARIZATION",
    "ENABLE_LOOP",
    "MONITOR_ONLY_MODE",
    "ENABLE_SUBMITTED_FLAG_DEDUP",
    "ENABLE_MONITORING",
    "ALLOWED_TOOLS",
    "ALLOWED_SKILLS",
    "MAX_CORRECT_FLAGS_PER_EXPERIMENT",
    "EXPERIMENT_DURATION_SECONDS",
    "TECHNIQUE",
]

SUMMARY_FIELDS = [
    "elapsed_seconds",
    "processed_triggers",
    "correct_flag_submissions",
    "incorrect_flag_submissions",
    "duplicate_flag_submissions",
    "submit_flag_attempts",
    "trigger_efficiency",
    "llm_calls",
    "total_tokens",
    "tool_call_count",
    "tool_attempt_count",
    "selected_signal_count",
    "unique_flags_observed",
    "unique_flags_submitted",
]


def _safe_read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _parse_config_snapshot(path: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        try:
            values[name] = ast.literal_eval(node.value)
        except Exception:
            continue
    return values


def _normalize_for_signature(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_for_signature(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_for_signature(val) for key, val in sorted(value.items(), key=lambda item: str(item[0]))}
    return value


def _relevant_config_payload(config: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _normalize_for_signature(config.get(key))
        for key in RELEVANT_CONFIG_KEYS
        if key in config
    }


def _signature_for_config(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _infer_variant_label(config: dict[str, Any]) -> str:
    parts: list[str] = []
    monitor_only = bool(config.get("MONITOR_ONLY_MODE", False))
    parts.append("monitor_only" if monitor_only else "baseline")

    if config.get("ENABLE_TOOLS") is False:
        parts.append("wo_tools")
    if config.get("ENABLE_SKILLS") is False:
        parts.append("wo_skills")
    if config.get("ENABLE_PLANNING") is False:
        parts.append("wo_planning")
    if config.get("ENABLE_MEMORY") is False:
        parts.append("wo_memory")
    if config.get("ENABLE_SUMMARIZATION") is False:
        parts.append("wo_summarization")
    if config.get("ENABLE_LOOP") is False:
        parts.append("wo_loop")
    if config.get("ENABLE_SUBMITTED_FLAG_DEDUP") is False:
        parts.append("wo_dedupe")
    if config.get("ENABLE_MONITORING") is False:
        parts.append("wo_monitoring")

    allowed_tools = config.get("ALLOWED_TOOLS")
    allowed_skills = config.get("ALLOWED_SKILLS")
    if isinstance(allowed_tools, list) and not allowed_tools:
        parts.append("empty_allowed_tools")
    if isinstance(allowed_skills, list) and not allowed_skills:
        parts.append("empty_allowed_skills")

    if parts == ["monitor_only"]:
        return "proposal"
    if parts == ["baseline"]:
        return "baseline_full"
    return "+".join(parts)


def _display_label(config_variant: str) -> str:
    text = str(config_variant or "unknown").strip()
    if not text:
        return "UNKNOWN"
    return text.replace("+", " + ").replace("_", " ").upper()


def _variant_code(index: int) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if index < len(letters):
        return letters[index]
    quotient, remainder = divmod(index, len(letters))
    return f"{letters[remainder]}{quotient}"


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _coerce_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _collect_experiment_row(experiment_dir: Path, *, regenerate: bool) -> dict[str, Any] | None:
    metadata_path = experiment_dir / "metadata.json"
    if not metadata_path.exists():
        return None

    config_path = experiment_dir / "config_snapshot.py"
    if not config_path.exists():
        return {
            "experiment_folder": experiment_dir.name,
            "metadata_experiment_id": "",
            "config_variant": "missing_config_snapshot",
            "config_signature": "",
            "config_available": False,
        }

    config_snapshot = _parse_config_snapshot(config_path)
    config_payload = _relevant_config_payload(config_snapshot)
    config_signature = _signature_for_config(config_payload)
    config_variant = _infer_variant_label(config_snapshot)

    analysis_dir = experiment_dir / "analysis"
    report_summary_path = analysis_dir / "report_summary.json"
    if regenerate or not report_summary_path.exists():
        generate_experiment_report(experiment_dir.name)

    summary = _safe_read_json(report_summary_path, {})
    metadata = _safe_read_json(metadata_path, {})

    row: dict[str, Any] = {
        "experiment_folder": experiment_dir.name,
        "metadata_experiment_id": str(metadata.get("experiment_id", "")),
        "config_variant": config_variant,
        "config_signature": config_signature,
        "config_available": True,
        "technique": str(summary.get("technique", metadata.get("technique", ""))),
        "stop_reason": str(summary.get("stop_reason", metadata.get("stop_reason", ""))),
        "monitor_only_mode": bool(config_snapshot.get("MONITOR_ONLY_MODE", False)),
        "enable_tools": bool(config_snapshot.get("ENABLE_TOOLS", False)),
        "enable_skills": bool(config_snapshot.get("ENABLE_SKILLS", False)),
        "enable_planning": bool(config_snapshot.get("ENABLE_PLANNING", False)),
        "enable_memory": bool(config_snapshot.get("ENABLE_MEMORY", False)),
        "enable_summarization": bool(config_snapshot.get("ENABLE_SUMMARIZATION", False)),
        "enable_loop": bool(config_snapshot.get("ENABLE_LOOP", False)),
        "enable_submitted_flag_dedup": bool(config_snapshot.get("ENABLE_SUBMITTED_FLAG_DEDUP", False)),
        "enable_monitoring": bool(config_snapshot.get("ENABLE_MONITORING", False)),
        "allowed_tools_count": len(config_snapshot.get("ALLOWED_TOOLS", []) or []),
        "allowed_skills_count": len(config_snapshot.get("ALLOWED_SKILLS", []) or []),
        "allowed_tools": "|".join(config_snapshot.get("ALLOWED_TOOLS", []) or []),
        "allowed_skills": "|".join(config_snapshot.get("ALLOWED_SKILLS", []) or []),
    }

    for field in SUMMARY_FIELDS:
        row[field] = summary.get(field, metadata.get(field, 0))

    row["elapsed_seconds"] = _coerce_int(row.get("elapsed_seconds", 0))
    row["processed_triggers"] = _coerce_int(row.get("processed_triggers", 0))
    row["correct_flag_submissions"] = _coerce_int(row.get("correct_flag_submissions", 0))
    row["incorrect_flag_submissions"] = _coerce_int(row.get("incorrect_flag_submissions", 0))
    row["duplicate_flag_submissions"] = _coerce_int(row.get("duplicate_flag_submissions", 0))
    row["submit_flag_attempts"] = _coerce_int(row.get("submit_flag_attempts", 0))
    row["llm_calls"] = _coerce_int(row.get("llm_calls", 0))
    row["total_tokens"] = _coerce_int(row.get("total_tokens", 0))
    row["tool_call_count"] = _coerce_int(row.get("tool_call_count", 0))
    row["tool_attempt_count"] = _coerce_int(row.get("tool_attempt_count", 0))
    row["selected_signal_count"] = _coerce_int(row.get("selected_signal_count", 0))
    row["unique_flags_observed"] = _coerce_int(row.get("unique_flags_observed", 0))
    row["unique_flags_submitted"] = _coerce_int(row.get("unique_flags_submitted", 0))
    row["trigger_efficiency"] = _coerce_float(row.get("trigger_efficiency", 0))
    row["_config_payload"] = config_payload
    return row


def _mean(rows: list[dict[str, Any]], field: str) -> float:
    values = [_coerce_float(row.get(field, 0)) for row in rows]
    if not values:
        return 0.0
    return sum(values) / len(values)


def _build_variant_rows(experiment_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    variant_payloads: dict[str, dict[str, Any]] = {}
    variant_labels: dict[str, str] = {}

    for row in experiment_rows:
        signature = str(row.get("config_signature", ""))
        if not signature:
            continue
        grouped[signature].append(row)
        variant_payloads[signature] = dict(row.get("_config_payload", {}))
        variant_labels[signature] = str(row.get("config_variant", "unknown"))

    variant_rows: list[dict[str, Any]] = []
    config_index: dict[str, Any] = {}
    for signature, rows in grouped.items():
        rows.sort(key=lambda item: str(item.get("experiment_folder", "")))
        label = variant_labels.get(signature, "unknown")
        record = {
            "config_signature": signature,
            "config_variant": label,
            "display_label": _display_label(label),
            "monitor_only_mode": bool(rows[0].get("monitor_only_mode", False)),
            "run_count": len(rows),
            "avg_elapsed_seconds": round(_mean(rows, "elapsed_seconds"), 3),
            "avg_processed_triggers": round(_mean(rows, "processed_triggers"), 3),
            "avg_correct_flag_submissions": round(_mean(rows, "correct_flag_submissions"), 3),
            "avg_incorrect_flag_submissions": round(_mean(rows, "incorrect_flag_submissions"), 3),
            "avg_duplicate_flag_submissions": round(_mean(rows, "duplicate_flag_submissions"), 3),
            "avg_submit_flag_attempts": round(_mean(rows, "submit_flag_attempts"), 3),
            "avg_trigger_efficiency": round(_mean(rows, "trigger_efficiency"), 6),
            "avg_llm_calls": round(_mean(rows, "llm_calls"), 3),
            "avg_total_tokens": round(_mean(rows, "total_tokens"), 3),
            "avg_tool_call_count": round(_mean(rows, "tool_call_count"), 3),
            "avg_selected_signal_count": round(_mean(rows, "selected_signal_count"), 3),
            "experiment_folders": "|".join(str(row.get("experiment_folder", "")) for row in rows),
        }
        variant_rows.append(record)
        config_index[signature] = {
            "config_signature": signature,
            "config_variant": label,
            "monitor_only_mode": bool(rows[0].get("monitor_only_mode", False)),
            "config_payload": variant_payloads.get(signature, {}),
            "experiments": [
                {
                    "experiment_folder": row.get("experiment_folder", ""),
                    "metadata_experiment_id": row.get("metadata_experiment_id", ""),
                    "elapsed_seconds": row.get("elapsed_seconds", 0),
                    "correct_flag_submissions": row.get("correct_flag_submissions", 0),
                    "processed_triggers": row.get("processed_triggers", 0),
                    "stop_reason": row.get("stop_reason", ""),
                }
                for row in rows
            ],
        }

    variant_rows.sort(key=lambda item: (str(item.get("config_variant", "")), str(item.get("config_signature", ""))))
    return variant_rows, config_index


def _make_bar_chart(path: Path, title: str, rows: list[dict[str, Any]], label_key: str, value_key: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 7))
    fig.patch.set_facecolor("#f8fafc")
    ax.set_facecolor("#ffffff")

    if not rows:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", fontsize=18, color="#64748b")
        ax.axis("off")
    else:
        labels = [str(row.get(label_key, "")) for row in rows]
        values = [_coerce_float(row.get(value_key, 0)) for row in rows]
        ax.bar(range(len(labels)), values, color="#2563eb")
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=0, ha="center")
        ax.set_title(title, fontsize=18, fontweight="bold")
        ax.grid(axis="y", alpha=0.25)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _make_dual_metric_chart(
    path: Path,
    *,
    title: str,
    rows: list[dict[str, Any]],
    label_key: str,
    bar_value_key: str,
    line_value_key: str,
    bar_label: str,
    line_label: str,
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax1 = plt.subplots(figsize=(13, 7))
    fig.patch.set_facecolor("#ffffff")
    ax1.set_facecolor("#ffffff")

    if not rows:
        ax1.text(0.5, 0.5, "no data", ha="center", va="center", fontsize=18, color="#64748b")
        ax1.axis("off")
    else:
        labels = [str(row.get(label_key, "")) for row in rows]
        bar_values = [_coerce_float(row.get(bar_value_key, 0)) for row in rows]
        line_values = [min(300.0, _coerce_float(row.get(line_value_key, 0))) for row in rows]
        bar_colors = [
            "#2563eb" if bool(row.get("monitor_only_mode", False)) else "#16a34a"
            for row in rows
        ]
        xs = list(range(len(labels)))

        bars = ax1.bar(xs, bar_values, color=bar_colors, alpha=0.9, label=bar_label, width=0.62)
        ax1.set_ylabel("")
        ax1.tick_params(axis="y", labelcolor="#111111", colors="#111111", labelsize=15)
        ax1.set_xticks(xs)
        ax1.set_xticklabels(labels, rotation=0, ha="center")
        ax1.tick_params(axis="x", colors="#111111", labelsize=15)
        ax1.grid(axis="y", alpha=0.18, color="#777777")
        ax1.spines["top"].set_visible(False)
        ax1.spines["left"].set_color("#111111")
        ax1.spines["bottom"].set_color("#111111")

        ax2 = ax1.twinx()
        ax2.plot(xs, line_values, color="#ea580c", marker="o", markersize=6, linewidth=2.0, label=line_label)
        ax2.set_ylabel("")
        ax2.tick_params(axis="y", labelcolor="#111111", colors="#111111", labelsize=15)
        ax2.spines["top"].set_visible(False)
        ax2.spines["right"].set_color("#111111")
        ax2.set_ylim(0, 310)
        ax2.set_yticks([0, 50, 100, 150, 200, 250, 300])

        for idx, bar in enumerate(bars):
            if bar_values[idx] > 0:
                ax1.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(bar_values) * 0.025,
                    f"{int(round(bar_values[idx]))}",
                    ha="center",
                    va="bottom",
                    fontsize=14,
                    color="#111111",
                )
        for idx, value in enumerate(line_values):
            extra_offset = max(line_values) * 0.10
            ax2.text(
                xs[idx],
                min(307, value + extra_offset),
                f"{value:.0f}",
                ha="center",
                va="bottom",
                fontsize=14,
                color="#111111",
            )

        handles1, labels1 = ax1.get_legend_handles_labels()
        handles2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(
            handles1 + handles2,
            labels1 + labels2,
            loc="upper center",
            bbox_to_anchor=(0.5, 1.14),
            ncol=2,
            frameon=False,
            borderaxespad=1.0,
            fontsize=15,
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _build_output_dir(requested: str | None) -> Path:
    if requested:
        path = Path(requested)
        path.mkdir(parents=True, exist_ok=True)
        return path
    stamp = datetime.now().strftime(TIME_FORMAT)
    path = DEFAULT_REPORTS_DIR / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan all D_agent experiments, classify them by config_snapshot.py, and write per-config CSV/JSON/PNG summaries."
    )
    parser.add_argument("--experiments-dir", default=str(DEFAULT_EXPERIMENTS_DIR), help="directory containing experiment folders")
    parser.add_argument("--output-dir", help="directory for aggregated analysis artifacts")
    parser.add_argument("--regenerate", action="store_true", help="regenerate per-experiment report_summary.json before aggregation")
    args = parser.parse_args()

    experiments_dir = Path(args.experiments_dir)
    output_dir = _build_output_dir(args.output_dir)

    experiment_rows: list[dict[str, Any]] = []
    skipped: list[str] = []

    for experiment_dir in sorted(path for path in experiments_dir.iterdir() if path.is_dir()):
        row = _collect_experiment_row(experiment_dir, regenerate=args.regenerate)
        if row is None:
            skipped.append(experiment_dir.name)
            continue
        row["experiment_path"] = str(experiment_dir.resolve())
        experiment_rows.append(row)

    variant_rows, config_index = _build_variant_rows(experiment_rows)

    code_by_signature: dict[str, str] = {}
    for index, row in enumerate(variant_rows):
        code = _variant_code(index)
        row["variant_code"] = code
        code_by_signature[str(row.get("config_signature", ""))] = code

    for row in experiment_rows:
        signature = str(row.get("config_signature", ""))
        row["variant_code"] = code_by_signature.get(signature, "")

    cleaned_experiment_rows: list[dict[str, Any]] = []
    for row in experiment_rows:
        cleaned_row = {key: value for key, value in row.items() if not key.startswith("_")}
        cleaned_experiment_rows.append(cleaned_row)

    experiment_fields = [
        "experiment_path",
        "experiment_folder",
        "metadata_experiment_id",
        "config_variant",
        "variant_code",
        "config_signature",
        "config_available",
        "technique",
        "stop_reason",
        "monitor_only_mode",
        "enable_tools",
        "enable_skills",
        "enable_planning",
        "enable_memory",
        "enable_summarization",
        "enable_loop",
        "enable_submitted_flag_dedup",
        "enable_monitoring",
        "allowed_tools_count",
        "allowed_skills_count",
        "allowed_tools",
        "allowed_skills",
        "elapsed_seconds",
        "processed_triggers",
        "correct_flag_submissions",
        "incorrect_flag_submissions",
        "duplicate_flag_submissions",
        "submit_flag_attempts",
        "trigger_efficiency",
        "llm_calls",
        "total_tokens",
        "tool_call_count",
        "tool_attempt_count",
        "selected_signal_count",
        "unique_flags_observed",
        "unique_flags_submitted",
    ]

    variant_fields = [
        "variant_code",
        "config_signature",
        "config_variant",
        "display_label",
        "monitor_only_mode",
        "run_count",
        "avg_elapsed_seconds",
        "avg_processed_triggers",
        "avg_correct_flag_submissions",
        "avg_incorrect_flag_submissions",
        "avg_duplicate_flag_submissions",
        "avg_submit_flag_attempts",
        "avg_trigger_efficiency",
        "avg_llm_calls",
        "avg_total_tokens",
        "avg_tool_call_count",
        "avg_selected_signal_count",
        "experiment_folders",
    ]

    _write_csv(output_dir / "experiment_config_metrics.csv", cleaned_experiment_rows, experiment_fields)
    _write_csv(output_dir / "config_signature_summary.csv", variant_rows, variant_fields)
    _write_csv(
        output_dir / "experiment_label_mapping.csv",
        [
            {
                "experiment_path": str((experiments_dir / str(row.get("experiment_folder", ""))).resolve()),
                "experiment_folder": row.get("experiment_folder", ""),
                "variant_code": row.get("variant_code", ""),
                "config_variant": row.get("config_variant", ""),
                "config_signature": row.get("config_signature", ""),
            }
            for row in cleaned_experiment_rows
        ],
        ["experiment_path", "experiment_folder", "variant_code", "config_variant", "config_signature"],
    )
    _write_json(
        output_dir / "experiment_label_mapping.json",
        {
            str((experiments_dir / str(row.get("experiment_folder", ""))).resolve()): {
                "experiment_folder": str(row.get("experiment_folder", "")),
                "variant_code": str(row.get("variant_code", "")),
                "config_variant": str(row.get("config_variant", "")),
                "config_signature": str(row.get("config_signature", "")),
            }
            for row in cleaned_experiment_rows
        },
    )
    _write_json(output_dir / "config_signature_details.json", config_index)
    _write_json(
        output_dir / "variant_label_mapping.json",
        {
            row["config_signature"]: {
                "variant_code": row["variant_code"],
                "config_variant": row["config_variant"],
                "display_label": row["display_label"],
            }
            for row in variant_rows
        },
    )
    _write_json(
        output_dir / "analysis_summary.json",
        {
            "experiments_dir": str(experiments_dir),
            "output_dir": str(output_dir),
            "experiment_count": len(cleaned_experiment_rows),
            "config_variant_count": len(variant_rows),
            "skipped_folders": skipped,
        },
    )

    label_rows = [
        {
            "label": row["variant_code"],
            "monitor_only_mode": row["monitor_only_mode"],
            "avg_correct_flag_submissions": row["avg_correct_flag_submissions"],
            "avg_elapsed_seconds": row["avg_elapsed_seconds"],
            "avg_trigger_efficiency": row["avg_trigger_efficiency"],
            "avg_total_tokens": row["avg_total_tokens"],
            "run_count": row["run_count"],
        }
        for row in variant_rows
    ]

    _make_bar_chart(
        output_dir / "variant_avg_correct_flags.png",
        "Average Correct Flag Submissions by Config Variant",
        label_rows,
        "label",
        "avg_correct_flag_submissions",
    )
    _make_bar_chart(
        output_dir / "variant_avg_elapsed_seconds.png",
        "Average Elapsed Seconds by Config Variant",
        label_rows,
        "label",
        "avg_elapsed_seconds",
    )
    _make_bar_chart(
        output_dir / "variant_avg_trigger_efficiency.png",
        "Average Trigger Efficiency by Config Variant",
        label_rows,
        "label",
        "avg_trigger_efficiency",
    )
    _make_bar_chart(
        output_dir / "variant_avg_total_tokens.png",
        "Average Total Tokens by Config Variant",
        label_rows,
        "label",
        "avg_total_tokens",
    )
    _make_bar_chart(
        output_dir / "variant_run_count.png",
        "Experiment Count by Config Variant",
        label_rows,
        "label",
        "run_count",
    )
    _make_dual_metric_chart(
        output_dir / "variant_correct_flags_vs_elapsed_seconds.png",
        title="Average Correct Flags and Elapsed Seconds by Config Variant",
        rows=label_rows,
        label_key="label",
        bar_value_key="avg_correct_flag_submissions",
        line_value_key="avg_elapsed_seconds",
        bar_label="Avg. correct flags",
        line_label="Avg. elapsed seconds",
    )

    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "experiment_count": len(cleaned_experiment_rows),
                "config_variant_count": len(variant_rows),
                "experiment_csv": str(output_dir / "experiment_config_metrics.csv"),
                "variant_csv": str(output_dir / "config_signature_summary.csv"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
