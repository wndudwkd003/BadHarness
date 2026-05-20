#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import shutil
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch


ROOT_DIR = Path(__file__).resolve().parents[1]
D_EXPERIMENTS_DIR = ROOT_DIR / "experiments"
C_SERVER_EXPERIMENTS_DIR = ROOT_DIR.parent / "C_Server" / "experiments"
OUTPUT_ROOT = ROOT_DIR / "reports" / "axis_analysis"

MAX_CORRECT_FLAGS = 10
REQUIRED_RECENT_RUNS = 3


@dataclass(frozen=True)
class VariantDef:
    label: str
    axis: str
    mode: str
    variant: str
    display_name: str


VARIANTS: list[VariantDef] = [
    VariantDef("baseline_autonomous", "execution_paradigm", "autonomous", "baseline", "Baseline"),
    VariantDef("baseline_reactive", "execution_paradigm", "reactive", "baseline", "Baseline"),
    VariantDef("baseline_autonomous", "collaborative_agent", "autonomous", "baseline", "Baseline"),
    VariantDef("baseline_reactive", "collaborative_agent", "reactive", "baseline", "Baseline"),
    VariantDef("planning_off_autonomous", "collaborative_agent", "autonomous", "planning_off", "w/o Planning"),
    VariantDef("planning_off_reactive", "collaborative_agent", "reactive", "planning_off", "w/o Planning"),
    VariantDef("planning_off_wo_loop_autonomous", "collaborative_agent", "autonomous", "planning_off_wo_loop", "w/o Planning, w/o Loop"),
    VariantDef("planning_off_wo_loop_reactive", "collaborative_agent", "reactive", "planning_off_wo_loop", "w/o Planning, w/o Loop"),
    VariantDef("wo_summarization_autonomous", "collaborative_agent", "autonomous", "wo_summarization", "w/o Summarization"),
    VariantDef("wo_summarization_reactive", "collaborative_agent", "reactive", "wo_summarization", "w/o Summarization"),
    VariantDef("wo_judge_autonomous", "collaborative_agent", "autonomous", "wo_judge", "w/o Judge"),
    VariantDef("wo_judge_reactive", "collaborative_agent", "reactive", "wo_judge", "w/o Judge"),
    VariantDef("wo_judge_wo_loop_autonomous", "collaborative_agent", "autonomous", "wo_judge_wo_loop", "w/o Judge, w/o Loop"),
    VariantDef("wo_judge_wo_loop_reactive", "collaborative_agent", "reactive", "wo_judge_wo_loop", "w/o Judge, w/o Loop"),
    VariantDef("wo_signal_selector_autonomous", "collaborative_agent", "autonomous", "wo_signal_selector", "w/o Signal Selector"),
    VariantDef("wo_signal_selector_reactive", "collaborative_agent", "reactive", "wo_signal_selector", "w/o Signal Selector"),
    VariantDef("baseline_autonomous", "execution_support", "autonomous", "baseline", "Baseline"),
    VariantDef("baseline_reactive", "execution_support", "reactive", "baseline", "Baseline"),
    VariantDef("wo_loop_autonomous", "execution_support", "autonomous", "wo_loop", "w/o Loop"),
    VariantDef("wo_loop_reactive", "execution_support", "reactive", "wo_loop", "w/o Loop"),
    VariantDef("wo_memory_autonomous", "execution_support", "autonomous", "wo_memory", "w/o Memory"),
    VariantDef("wo_memory_reactive", "execution_support", "reactive", "wo_memory", "w/o Memory"),
    VariantDef("wo_skills_autonomous", "execution_support", "autonomous", "wo_skills", "w/o Skills"),
    VariantDef("wo_skills_reactive", "execution_support", "reactive", "wo_skills", "w/o Skills"),
]

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
    "wo_signal_selector_autonomous": ["wo_signal_selector_autonomous", "wo_signal_selector_autonomy"],
    "wo_signal_selector_reactive": ["wo_signal_selector_reactive", "wo_signal_selector_monitor"],
    "wo_memory_autonomous": ["wo_memory_autonomous", "wo_memory_autonomy"],
    "wo_memory_reactive": ["wo_memory_reactive", "wo_memory_monitor"],
    "wo_skills_autonomous": ["wo_skills_autonomous", "wo_skills_autonomy"],
    "wo_skills_reactive": ["wo_skills_reactive", "wo_skills_monitor"],
}

REQUIRED_LABELS = sorted({variant.label for variant in VARIANTS})
AXIS_ORDER = ["execution_paradigm", "collaborative_agent", "execution_support"]
METRIC_SPECS = [
    ("success_rate_pct", "Success Rate (%)", "success_rate"),
    ("elapsed_seconds", "Elapsed Time (s)", "elapsed_time"),
]
MODE_COLORS = {
    "autonomous": "#4C78A8",
    "reactive": "#F58518",
}
EDGE_COLOR = "#444444"
STD_COLOR = "#7A7A7A"
BAR_STD_COLOR = "#000000"


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


def _slugify(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


def _experiment_id_from_name(name: str) -> str | None:
    if len(name) < 15:
        return None
    candidate = name[:15]
    if len(candidate) == 15 and candidate[8] == "_":
        return candidate
    return None


def _extract_label(dir_name: str) -> str | None:
    alias_pairs: list[tuple[str, str]] = []
    for canonical, aliases in LABEL_ALIASES.items():
        for alias in aliases:
            alias_pairs.append((canonical, alias))
    for canonical, alias in sorted(alias_pairs, key=lambda item: len(item[1]), reverse=True):
        if dir_name.endswith(f"_{alias}"):
            return canonical
    return None


def _score_time_key(metadata: dict[str, Any], path: Path) -> tuple[int, float]:
    epoch = metadata.get("ended_epoch") or metadata.get("started_epoch") or 0
    try:
        epoch_int = int(epoch or 0)
    except Exception:
        epoch_int = 0
    return epoch_int, path.stat().st_mtime


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    return statistics.pstdev(values)


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


def _load_records() -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {label: [] for label in REQUIRED_LABELS}
    for path in D_EXPERIMENTS_DIR.iterdir():
        if not path.is_dir():
            continue
        label = _extract_label(path.name)
        if not label:
            continue
        metadata = _safe_read_json(path / "metadata.json")
        report = _safe_read_json(path / "analysis" / "report_summary.json")
        config_snapshot = path / "config_snapshot.py"
        experiment_id = str(metadata.get("experiment_id") or _experiment_id_from_name(path.name) or "")
        c_dir = C_SERVER_EXPERIMENTS_DIR / experiment_id if experiment_id else None
        c_metrics = (c_dir / "metrics.json") if c_dir and c_dir.exists() else None

        correct = int(report.get("correct_flag_submissions", metadata.get("correct_flag_submissions", 0)) or 0)
        incorrect = int(report.get("incorrect_flag_submissions", metadata.get("incorrect_flag_submissions", 0)) or 0)
        elapsed = int(report.get("elapsed_seconds", metadata.get("elapsed_seconds", 0)) or 0)
        duplicate = int(report.get("duplicate_flag_submissions", metadata.get("duplicate_flag_submissions", 0)) or 0)
        processed_triggers = int(report.get("processed_triggers", metadata.get("processed_triggers", 0)) or 0)

        grouped[label].append(
            {
                "label": label,
                "experiment_id": experiment_id,
                "experiment_dir": str(path.resolve()),
                "metadata_path": str((path / "metadata.json").resolve()) if (path / "metadata.json").exists() else "",
                "report_summary_path": str((path / "analysis" / "report_summary.json").resolve()) if (path / "analysis" / "report_summary.json").exists() else "",
                "config_snapshot_path": str(config_snapshot.resolve()) if config_snapshot.exists() else "",
                "c_server_experiment_dir": str(c_dir.resolve()) if c_dir and c_dir.exists() else "",
                "c_server_metrics_path": str(c_metrics.resolve()) if c_metrics and c_metrics.exists() else "",
                "stop_reason": str(metadata.get("stop_reason", "") or ""),
                "ended_epoch": int(metadata.get("ended_epoch", 0) or 0),
                "elapsed_seconds": elapsed,
                "correct_flag_submissions": correct,
                "incorrect_submissions": incorrect,
                "duplicate_flag_submissions": duplicate,
                "processed_triggers": processed_triggers,
                "success_rate_pct": round((correct / MAX_CORRECT_FLAGS) * 100.0, 3),
            }
        )

    return grouped


def _select_latest_three(grouped: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    selected: dict[str, list[dict[str, Any]]] = {}
    missing: list[dict[str, Any]] = []

    for label in REQUIRED_LABELS:
        items = grouped.get(label, [])
        items = sorted(
            items,
            key=lambda row: (
                int(row.get("ended_epoch", 0) or 0),
                Path(str(row.get("experiment_dir", ""))).stat().st_mtime if row.get("experiment_dir") else 0,
                str(row.get("experiment_dir", "")),
            ),
        )
        if len(items) < REQUIRED_RECENT_RUNS:
            missing.append(
                {
                    "label": label,
                    "found_count": len(items),
                    "required_count": REQUIRED_RECENT_RUNS,
                    "found_experiments": [item.get("experiment_dir", "") for item in items],
                }
            )
            continue
        selected[label] = items[-REQUIRED_RECENT_RUNS:]

    if missing:
        raise SystemExit(
            json.dumps(
                {
                    "error": "insufficient_experiment_runs",
                    "required_recent_runs": REQUIRED_RECENT_RUNS,
                    "missing_labels": missing,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    return selected


def _copy_evidence(output_dir: Path, selected: dict[str, list[dict[str, Any]]]) -> None:
    evidence_root = output_dir / "evidence"
    for label, rows in selected.items():
        for index, row in enumerate(rows, start=1):
            experiment_id = str(row.get("experiment_id", "") or f"unknown_{index}")
            target_dir = evidence_root / label / f"{index:02d}_{experiment_id}"
            target_dir.mkdir(parents=True, exist_ok=True)

            sources = {
                "metadata.json": row.get("metadata_path", ""),
                "report_summary.json": row.get("report_summary_path", ""),
                "config_snapshot.py": row.get("config_snapshot_path", ""),
                "c_metrics.json": row.get("c_server_metrics_path", ""),
            }
            for target_name, source_text in sources.items():
                source = Path(str(source_text))
                if source_text and source.exists():
                    shutil.copy2(source, target_dir / target_name)

            _write_json(target_dir / "record.json", row)


def _build_overall_rows(selected: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label in sorted(selected):
        records = selected[label]
        success_values = [float(record["success_rate_pct"]) for record in records]
        elapsed_values = [float(record["elapsed_seconds"]) for record in records]
        rows.append(
            {
                "label": label,
                "sample_count": len(records),
                "selected_experiment_ids": "|".join(str(record.get("experiment_id", "")) for record in records),
                "selected_experiment_dirs": "|".join(str(record.get("experiment_dir", "")) for record in records),
                "mean_success_rate_pct": round(_mean(success_values), 3),
                "std_success_rate_pct": round(_std(success_values), 3),
                "mean_elapsed_seconds": round(_mean(elapsed_values), 3),
                "std_elapsed_seconds": round(_std(elapsed_values), 3),
            }
        )
    return rows


def _build_axis_rows(selected: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows: list[dict[str, Any]] = []
    by_axis: dict[str, list[dict[str, Any]]] = {axis: [] for axis in AXIS_ORDER}

    for variant in VARIANTS:
        records = selected[variant.label]
        success_values = [float(record["success_rate_pct"]) for record in records]
        elapsed_values = [float(record["elapsed_seconds"]) for record in records]
        row = {
            "axis": variant.axis,
            "mode": variant.mode,
            "variant": variant.variant,
            "label": variant.label,
            "display_name": variant.display_name,
            "sample_count": len(records),
            "selected_experiment_ids": "|".join(str(record.get("experiment_id", "")) for record in records),
            "mean_success_rate_pct": round(_mean(success_values), 3),
            "std_success_rate_pct": round(_std(success_values), 3),
            "mean_elapsed_seconds": round(_mean(elapsed_values), 3),
            "std_elapsed_seconds": round(_std(elapsed_values), 3),
        }
        rows.append(row)
        by_axis[variant.axis].append(row)

    return rows, by_axis


def _save_axis_tables(output_dir: Path, axis_rows: dict[str, list[dict[str, Any]]]) -> None:
    for axis, rows in axis_rows.items():
        fieldnames = [
            "axis",
            "mode",
            "variant",
            "label",
            "display_name",
            "sample_count",
            "selected_experiment_ids",
            "mean_success_rate_pct",
            "std_success_rate_pct",
            "mean_elapsed_seconds",
            "std_elapsed_seconds",
        ]
        _write_csv(output_dir / f"{axis}.csv", rows, fieldnames)
        _write_json(output_dir / f"{axis}.json", rows)


def _plot_metric(
    output_path: Path,
    title: str,
    rows: list[dict[str, Any]],
    metric_key: str,
    std_key: str,
    ylabel: str,
    include_title: bool = True,
) -> None:
    labels = [str(row["display_name"]) for row in rows]
    values = [float(row[metric_key]) for row in rows]
    errors = [float(row[std_key]) for row in rows]
    colors = [MODE_COLORS.get(str(row.get("mode", "")), "#4C78A8") for row in rows]
    ymax = max(values) if values else 1.0

    width = max(10, 0.9 * len(labels))
    fig, ax = plt.subplots(figsize=(width, 4.8))
    bars = ax.bar(
        range(len(labels)),
        values,
        yerr=errors,
        capsize=4,
        color=colors,
        edgecolor=EDGE_COLOR,
        linewidth=0.6,
        error_kw={"ecolor": BAR_STD_COLOR, "elinewidth": 1.0, "capthick": 1.0},
    )
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel(ylabel)
    if include_title:
        ax.set_title(title)
    ax.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.35)
    ax.set_axisbelow(True)

    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            max(ymax * 0.08, 0.6),
            f"{value:.1f}" if not math.isclose(value, round(value)) else f"{int(round(value))}",
            ha="center",
            va="center",
            fontsize=9,
        )

    ax.set_ylim(0, ymax * 1.18 if ymax > 0 else 1.0)

    fig.tight_layout()
    fig.savefig(output_path, dpi=220)
    plt.close(fig)


def _plot_dual_metric(
    output_path: Path,
    rows: list[dict[str, Any]],
    primary_mean_key: str,
    primary_std_key: str,
    primary_ylabel: str,
    slug_title: str,
    include_title: bool = True,
) -> None:
    labels = [str(row["display_name"]) for row in rows]
    primary_values = [float(row[primary_mean_key]) for row in rows]
    primary_errors = [float(row[primary_std_key]) for row in rows]
    elapsed_values = [float(row["mean_elapsed_seconds"]) for row in rows]
    elapsed_errors = [float(row["std_elapsed_seconds"]) for row in rows]
    mode_colors = [MODE_COLORS.get(str(row.get("mode", "")), "#4C78A8") for row in rows]
    xs = list(range(len(labels)))

    width = max(10, 0.95 * len(labels))
    fig, ax1 = plt.subplots(figsize=(width, 6.0))
    bars = ax1.bar(
        xs,
        primary_values,
        yerr=primary_errors,
        capsize=4,
        color=mode_colors,
        edgecolor=EDGE_COLOR,
        linewidth=0.6,
        error_kw={"ecolor": BAR_STD_COLOR, "elinewidth": 1.0, "capthick": 1.0},
    )
    ax1.set_xticks(xs)
    ax1.set_xticklabels(labels, rotation=20, ha="right")
    ax1.set_ylabel(primary_ylabel)
    ax1.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.35)
    ax1.set_axisbelow(True)

    ax2 = ax1.twinx()
    ax2.plot(xs, elapsed_values, color="black", linewidth=1.4, marker="o", markersize=0)
    lower_band = [max(0.0, value - error) for value, error in zip(elapsed_values, elapsed_errors)]
    upper_band = [value + error for value, error in zip(elapsed_values, elapsed_errors)]
    ax2.fill_between(xs, lower_band, upper_band, color=STD_COLOR, alpha=0.18, zorder=2)
    ax2.scatter(xs, elapsed_values, c=mode_colors, edgecolors="black", linewidths=0.6, s=42, zorder=4)
    ax2.set_ylabel("Elapsed Time (s)")

    primary_max = max(primary_values) if primary_values else 1.0
    elapsed_max = max(elapsed_values) if elapsed_values else 1.0
    primary_offset = primary_max * 0.035 if primary_max > 0 else 0.5
    elapsed_offset = elapsed_max * 0.035 if elapsed_max > 0 else 1.0

    for bar, value in zip(bars, primary_values):
        label = f"{value:.1f}" if not math.isclose(value, round(value)) else f"{int(round(value))}"
        ax1.text(
            bar.get_x() + bar.get_width() / 2.0,
            max(primary_max * 0.10, 0.8),
            label,
            ha="center",
            va="center",
            fontsize=9,
        )

    for x, value in zip(xs, elapsed_values):
        ax2.text(
            x,
            value + elapsed_offset,
            f"{int(round(value))}",
            ha="center",
            va="bottom",
            fontsize=9,
            color="black",
        )

    ax1.set_ylim(0, primary_max * 1.16 if primary_max > 0 else 1.0)
    ax2.set_ylim(0, elapsed_max * 1.16 if elapsed_max > 0 else 1.0)

    present_modes: list[str] = []
    for row in rows:
        mode = str(row.get("mode", ""))
        if mode and mode not in present_modes:
            present_modes.append(mode)

    legend_handles = [
        Patch(facecolor=MODE_COLORS[mode], edgecolor=EDGE_COLOR, label=mode.title())
        for mode in present_modes
        if mode in MODE_COLORS
    ]
    legend_handles.extend(
        [
            Line2D([0], [0], color="black", marker="o", markersize=6, linewidth=1.4, label="Elapsed Time"),
            Line2D([0], [0], color=BAR_STD_COLOR, linewidth=1.0, label="Bar Std. Dev."),
            Patch(facecolor=STD_COLOR, edgecolor="none", alpha=0.18, label="Time Std. Dev."),
        ]
    )
    fig.legend(
        handles=legend_handles,
        loc="upper right",
        bbox_to_anchor=(0.985, 0.89 if include_title else 0.975),
        ncol=len(legend_handles),
        frameon=False,
        borderaxespad=0.0,
        columnspacing=1.0,
        handletextpad=0.5,
        fontsize=9,
    )
    if include_title:
        fig.suptitle(slug_title, y=0.955, fontsize=14)
        fig.tight_layout(rect=(0, 0, 1, 0.955))
        fig.subplots_adjust(top=0.84)
    else:
        fig.tight_layout(rect=(0, 0, 1, 1))
        fig.subplots_adjust(top=0.90)
    fig.savefig(output_path, dpi=220)
    plt.close(fig)


def _plot_dual_metric_by_mode(
    figures_dir: Path,
    axis: str,
    rows: list[dict[str, Any]],
    primary_mean_key: str,
    primary_std_key: str,
    primary_ylabel: str,
    slug: str,
    title_prefix: str,
    include_title: bool = True,
) -> None:
    for mode in ("autonomous", "reactive"):
        mode_rows = [row for row in rows if str(row.get("mode", "")) == mode]
        if not mode_rows:
            continue
        _plot_dual_metric(
            figures_dir / f"{axis}_{slug}_{mode}.png",
            rows=mode_rows,
            primary_mean_key=primary_mean_key,
            primary_std_key=primary_std_key,
            primary_ylabel=primary_ylabel,
            slug_title=f"{title_prefix} - {mode.title()}",
            include_title=include_title,
        )


def _plot_axis_figures(output_dir: Path, axis_rows: dict[str, list[dict[str, Any]]]) -> None:
    figures_dir = output_dir / "figures"
    figures_with_title_dir = figures_dir / "with_title"
    figures_no_title_dir = figures_dir / "no_title"
    figures_with_title_dir.mkdir(parents=True, exist_ok=True)
    figures_no_title_dir.mkdir(parents=True, exist_ok=True)
    for axis, rows in axis_rows.items():
        for metric_key, ylabel, slug in METRIC_SPECS:
            std_key = {
                "success_rate_pct": "std_success_rate_pct",
                "elapsed_seconds": "std_elapsed_seconds",
            }[metric_key]
            _plot_metric(
                figures_with_title_dir / f"{axis}_{slug}.png",
                title=f"{axis.replace('_', ' ').title()} - {ylabel}",
                rows=rows,
                metric_key=f"mean_{metric_key}",
                std_key=std_key,
                ylabel=ylabel,
            )
            _plot_metric(
                figures_no_title_dir / f"{axis}_{slug}.png",
                title=f"{axis.replace('_', ' ').title()} - {ylabel}",
                rows=rows,
                metric_key=f"mean_{metric_key}",
                std_key=std_key,
                ylabel=ylabel,
                include_title=False,
            )
        _plot_dual_metric(
            figures_with_title_dir / f"{axis}_success_rate_vs_elapsed_time.png",
            rows=rows,
            primary_mean_key="mean_success_rate_pct",
            primary_std_key="std_success_rate_pct",
            primary_ylabel="Success Rate (%)",
            slug_title=f"{axis.replace('_', ' ').title()} - Success Rate vs. Elapsed Time",
        )
        _plot_dual_metric(
            figures_no_title_dir / f"{axis}_success_rate_vs_elapsed_time.png",
            rows=rows,
            primary_mean_key="mean_success_rate_pct",
            primary_std_key="std_success_rate_pct",
            primary_ylabel="Success Rate (%)",
            slug_title=f"{axis.replace('_', ' ').title()} - Success Rate vs. Elapsed Time",
            include_title=False,
        )
        _plot_dual_metric_by_mode(
            figures_dir=figures_with_title_dir,
            axis=axis,
            rows=rows,
            primary_mean_key="mean_success_rate_pct",
            primary_std_key="std_success_rate_pct",
            primary_ylabel="Success Rate (%)",
            slug="success_rate_vs_elapsed_time",
            title_prefix=f"{axis.replace('_', ' ').title()} - Success Rate vs. Elapsed Time",
        )
        _plot_dual_metric_by_mode(
            figures_dir=figures_no_title_dir,
            axis=axis,
            rows=rows,
            primary_mean_key="mean_success_rate_pct",
            primary_std_key="std_success_rate_pct",
            primary_ylabel="Success Rate (%)",
            slug="success_rate_vs_elapsed_time",
            title_prefix=f"{axis.replace('_', ' ').title()} - Success Rate vs. Elapsed Time",
            include_title=False,
        )


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUT_ROOT / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    grouped = _load_records()
    selected = _select_latest_three(grouped)

    overall_rows = _build_overall_rows(selected)
    axis_rows_flat, axis_rows = _build_axis_rows(selected)

    _write_json(output_dir / "latest_selection.json", selected)
    _write_json(output_dir / "label_to_axis_mapping.json", [variant.__dict__ for variant in VARIANTS])
    _write_csv(
        output_dir / "overall_summary.csv",
        overall_rows,
        [
            "label",
            "sample_count",
            "selected_experiment_ids",
            "selected_experiment_dirs",
            "mean_success_rate_pct",
            "std_success_rate_pct",
            "mean_elapsed_seconds",
            "std_elapsed_seconds",
        ],
    )
    _write_json(output_dir / "overall_summary.json", overall_rows)
    _write_csv(
        output_dir / "axis_summary.csv",
        axis_rows_flat,
        [
            "axis",
            "mode",
            "variant",
            "label",
            "display_name",
            "sample_count",
            "selected_experiment_ids",
            "mean_success_rate_pct",
            "std_success_rate_pct",
            "mean_elapsed_seconds",
            "std_elapsed_seconds",
        ],
    )
    _write_json(output_dir / "axis_summary.json", axis_rows_flat)
    _save_axis_tables(output_dir, axis_rows)
    _plot_axis_figures(output_dir, axis_rows)
    _copy_evidence(output_dir, selected)

    print(json.dumps({"output_dir": str(output_dir), "labels": REQUIRED_LABELS}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
