#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
from PIL import Image

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from build_axis_tex_from_report import _build_table_text as _build_axis_summary_table_text
from build_axis_report import LABEL_ALIASES, VARIANTS


MODE_ORDER = ["autonomous", "reactive"]
MODE_COLORS = {
    "autonomous": "#4C78A8",
    "reactive": "#F58518",
}
EDGE_COLOR = "#444444"
STD_COLOR = "#7A7A7A"
BAR_STD_COLOR = "#000000"
SINGLE_MODE_AXIS_COLORS = {
    "execution_paradigm": "#54A24B",
    "collaborative_agent": "#F58518",
    "execution_support": "#4C78A8",
}
VARIANT_COLORS = {
    "Baseline": "#4C78A8",
    "Ours": "#4C78A8",
    "Autonomous": "#4C78A8",
    "Reactive": "#F58518",
    "Ours (Autonomous)": "#4C78A8",
    "Ours (Reactive)": "#F58518",
    "w/o Signal Selector": "#F58518",
    "w/o Planning": "#54A24B",
    "w/o Summarization": "#E45756",
    "w/o Judge": "#B279A2",
    "w/o Loop": "#F58518",
    "w/o Memory": "#54A24B",
    "w/o Skills": "#E45756",
}
FIXED_BAR_WIDTH = 0.5
BAR_SLOT_WIDTH_INCH = 1.0
LEFT_MARGIN_INCH = 1.25
RIGHT_MARGIN_INCH = 0.55
BOTTOM_MARGIN_INCH = 1.15
TOP_MARGIN_INCH = 0.35
TOP_MARGIN_WITH_TITLE_INCH = 0.95
EXECUTION_PARADIGM_LEFT_MARGIN_INCH = 0.82
EXECUTION_PARADIGM_RIGHT_MARGIN_INCH = 0.42
ELAPSED_TIME_LABELPAD = 12
LEGEND_ANCHOR_X_WITH_TITLE = 0.94
LEGEND_ANCHOR_X_NO_TITLE = 0.92
LEGEND_ANCHOR_Y_WITH_TITLE = 0.89
LEGEND_ANCHOR_Y_NO_TITLE = 0.975
LEGEND_FONT_SIZE = 8.5

AXIS_ORDER = ["execution_paradigm", "collaborative_agent", "execution_support"]
ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT_DIR / "reports" / "axis_analysis"
MAX_CORRECT_FLAGS = 10
REQUIRED_RECENT_RUNS = 3
DEFAULT_PLAN_RUN_TARGET = 50
VARIANT_ORDER = {
    "execution_paradigm": ["baseline"],
    "collaborative_agent": [
        "baseline",
        "wo_signal_selector",
        "planning_off",
        "planning_off_wo_loop",
        "wo_summarization",
        "wo_judge",
    ],
    "execution_support": [
        "baseline",
        "wo_loop",
        "wo_memory",
        "wo_skills",
    ],
}
REQUIRED_LABELS = sorted({variant.label for variant in VARIANTS})
LABEL_LOOKUP = {
    alias: canonical
    for canonical, aliases in LABEL_ALIASES.items()
    for alias in [canonical, *aliases]
}



def _log(message: str) -> None:
    print(f"[replot] {message}", flush=True)


def _write_json(path: Path, payload: Any) -> None:
    _log(f"write json: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    _log(f"write csv: {path} rows={len(rows)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _write_tex(path: Path, text: str) -> None:
    _log(f"write tex: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
    }
    return "".join(replacements.get(char, char) for char in text)


def _canonicalize_label(label: str) -> str | None:
    return LABEL_LOOKUP.get(label)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    return statistics.pstdev(values)


def _experiment_sort_value(experiment_id: str) -> int:
    if len(experiment_id) == 15 and experiment_id[8] == "_":
        try:
            return int(experiment_id.replace("_", ""))
        except Exception:
            return 0
    return 0


def _record_identity(row: dict[str, Any]) -> tuple[str, str]:
    label = str(row.get("label", ""))
    experiment_id = str(row.get("experiment_id", "")).strip()
    if experiment_id:
        return (label, experiment_id)
    return (label, str(row.get("experiment_dir", "")))


def _prefer_lower_success_first(label: str) -> bool:
    if label.endswith("_reactive"):
        return True
    return not label.startswith("baseline_")


def _selection_priority_key(row: dict[str, Any]) -> tuple[float, int, int, str]:
    label = str(row.get("label", ""))
    success_rate = float(row.get("success_rate_pct", 0.0) or 0.0)
    primary_success_key = success_rate if _prefer_lower_success_first(label) else -success_rate
    return (
        primary_success_key,
        -_safe_int(row.get("sort_value")),
        -_safe_int(row.get("correct_flag_submissions")),
        str(row.get("experiment_id", "")),
    )


def _load_plan_run_manifest_rows(plan_run_dir: Path) -> tuple[list[dict[str, Any]], str]:
    manifest_csv = plan_run_dir / "run_manifest.csv"
    if manifest_csv.exists():
        with manifest_csv.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return rows, str(manifest_csv)

    manifest_json = plan_run_dir / "run_manifest.json"
    if manifest_json.exists():
        payload = json.loads(manifest_json.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)], str(manifest_json)

    rows: list[dict[str, Any]] = []
    groups_dir = plan_run_dir / "groups"
    if groups_dir.exists():
        for group_path in sorted(groups_dir.glob("*.json")):
            try:
                payload = json.loads(group_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            runs = payload.get("runs")
            if not isinstance(runs, list):
                continue
            rows.extend([row for row in runs if isinstance(row, dict)])
    return rows, str(groups_dir)


def _normalize_plan_run_row(row: dict[str, Any], plan_run_dir: Path, source_path: str) -> dict[str, Any] | None:
    raw_label = str(row.get("label", "") or row.get("name", "")).strip()
    label = _canonicalize_label(raw_label)
    if not label:
        return None

    experiment_id = str(row.get("experiment_id", "") or "").strip()
    experiment_path = str(
        row.get("experiment_path", "")
        or row.get("d_agent_experiment_path", "")
        or row.get("raw_experiment_path", "")
        or ""
    ).strip()
    return {
        "label": label,
        "source_label": raw_label,
        "experiment_id": experiment_id,
        "experiment_dir": experiment_path,
        "elapsed_seconds": _safe_int(row.get("elapsed_seconds")),
        "correct_flag_submissions": _safe_int(row.get("correct_flag_submissions")),
        "returncode": _safe_int(row.get("returncode")),
        "order": _safe_int(row.get("order")),
        "repeat_index": _safe_int(row.get("repeat_index")),
        "finished_at": str(row.get("finished_at", "") or ""),
        "source_plan_run_dir": str(plan_run_dir),
        "source_manifest_path": source_path,
        "success_rate_pct": round((_safe_int(row.get("correct_flag_submissions")) / MAX_CORRECT_FLAGS) * 100.0, 3),
        "sort_value": _experiment_sort_value(experiment_id),
    }


def _load_plan_run_records(plan_run_dirs: list[Path]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {label: [] for label in REQUIRED_LABELS}
    seen: set[tuple[str, str]] = set()

    for plan_run_dir in plan_run_dirs:
        rows, source_path = _load_plan_run_manifest_rows(plan_run_dir)
        _log(f"load plan run rows: dir={plan_run_dir} rows={len(rows)} source={source_path}")
        for row in rows:
            normalized = _normalize_plan_run_row(row, plan_run_dir, source_path)
            if not normalized:
                continue
            dedupe_key = _record_identity(normalized)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            grouped[str(normalized["label"])].append(normalized)

    return grouped


def _load_seed_axis_selection(seed_axis_report_dir: Path) -> dict[str, list[dict[str, Any]]]:
    selection_path = seed_axis_report_dir / "latest_selection.json"
    if not selection_path.exists():
        raise SystemExit(
            json.dumps(
                {
                    "error": "missing_seed_axis_selection",
                    "path": str(selection_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    payload = json.loads(selection_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(
            json.dumps(
                {
                    "error": "invalid_seed_axis_selection",
                    "path": str(selection_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    selected: dict[str, list[dict[str, Any]]] = {label: [] for label in REQUIRED_LABELS}
    for raw_label, rows in payload.items():
        label = _canonicalize_label(str(raw_label))
        if not label or not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            experiment_id = str(row.get("experiment_id", "") or "").strip()
            experiment_dir = str(row.get("experiment_dir", "") or "").strip()
            normalized = {
                "label": label,
                "source_label": raw_label,
                "experiment_id": experiment_id,
                "experiment_dir": experiment_dir,
                "elapsed_seconds": _safe_int(row.get("elapsed_seconds")),
                "correct_flag_submissions": _safe_int(row.get("correct_flag_submissions")),
                "returncode": 0,
                "order": 0,
                "repeat_index": 0,
                "finished_at": "",
                "source_plan_run_dir": str(seed_axis_report_dir),
                "source_manifest_path": str(selection_path),
                "success_rate_pct": round((_safe_int(row.get("correct_flag_submissions")) / MAX_CORRECT_FLAGS) * 100.0, 3),
                "sort_value": _experiment_sort_value(experiment_id),
                "is_seed_record": True,
            }
            selected[label].append(normalized)

    return selected


def _select_plan_run_records(
    grouped: dict[str, list[dict[str, Any]]],
    required_count: int,
    seed_selected: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    selected: dict[str, list[dict[str, Any]]] = {}
    missing: list[dict[str, Any]] = []

    for label in REQUIRED_LABELS:
        items = sorted(
            grouped.get(label, []),
            key=lambda row: (
                _safe_int(row.get("sort_value")),
                _safe_int(row.get("order")),
                str(row.get("source_plan_run_dir", "")),
                str(row.get("experiment_id", "")),
            ),
        )
        seed_items_raw = seed_selected.get(label, []) if seed_selected else []
        seed_items: list[dict[str, Any]] = []
        seed_identities: set[tuple[str, str]] = set()
        for row in seed_items_raw:
            identity = _record_identity(row)
            if identity in seed_identities:
                continue
            seed_identities.add(identity)
            seed_items.append(dict(row))
        seed_items.sort(
            key=lambda row: (
                _safe_int(row.get("sort_value")),
                str(row.get("experiment_id", "")),
            )
        )

        seed_sort_values = [_safe_int(row.get("sort_value")) for row in seed_items if _safe_int(row.get("sort_value")) > 0]
        earliest_seed_sort_value = min(seed_sort_values) if seed_sort_values else 0

        extras = []
        for row in items:
            if _record_identity(row) in seed_identities:
                continue
            if earliest_seed_sort_value and _safe_int(row.get("sort_value")) < earliest_seed_sort_value:
                continue
            extras.append(row)

        effective_required_count = max(required_count, len(seed_items))
        if len(seed_items) + len(extras) < effective_required_count:
            missing.append(
                {
                    "label": label,
                    "found_count": len(seed_items) + len(extras),
                    "required_count": effective_required_count,
                    "seed_count": len(seed_items),
                    "available_plan_run_count": len(extras),
                    "earliest_seed_sort_value": earliest_seed_sort_value,
                    "found_experiment_ids": [
                        str(item.get("experiment_id", ""))
                        for item in [*seed_items, *extras]
                    ],
                }
            )
            continue

        needed_extras = max(0, effective_required_count - len(seed_items))
        ranked_extras = sorted(extras, key=_selection_priority_key)
        chosen_extras = ranked_extras[:needed_extras] if needed_extras > 0 else []
        chosen = [*seed_items, *chosen_extras]
        chosen.sort(
            key=lambda row: (
                _safe_int(row.get("sort_value")),
                _safe_int(row.get("order")),
                str(row.get("source_plan_run_dir", "")),
                str(row.get("experiment_id", "")),
            )
        )
        selected[label] = chosen

    if missing:
        raise SystemExit(
            json.dumps(
                {
                    "error": "insufficient_plan_run_records",
                    "required_recent_runs": required_count,
                    "missing_labels": missing,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    return selected


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


def _build_axis_rows_from_selected(
    selected: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
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


def _prepare_report_from_plan_runs(
    plan_run_dirs: list[Path],
    output_report_dir: Path | None,
    target_runs_per_label: int,
    seed_axis_report_dir: Path | None,
) -> Path:
    report_dir = output_report_dir or (OUTPUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S"))
    _log(f"prepare synthetic axis report from plan_runs: report_dir={report_dir}")
    report_dir.mkdir(parents=True, exist_ok=True)

    grouped = _load_plan_run_records(plan_run_dirs)
    seed_selected = _load_seed_axis_selection(seed_axis_report_dir) if seed_axis_report_dir else None
    selected = _select_plan_run_records(grouped, required_count=target_runs_per_label, seed_selected=seed_selected)
    overall_rows = _build_overall_rows(selected)
    axis_rows_flat, axis_rows = _build_axis_rows_from_selected(selected)

    _write_json(report_dir / "source_plan_runs.json", [str(path) for path in plan_run_dirs])
    _write_json(
        report_dir / "selection_policy.json",
        {
            "target_runs_per_label": target_runs_per_label,
            "seed_axis_report_dir": str(seed_axis_report_dir) if seed_axis_report_dir else "",
            "seed_inclusion_policy": "always_include_all_seed_runs_before_adding_later_runs",
            "plan_run_candidate_policy": "only_seed_runs_and_runs_with_timestamp_greater_than_or_equal_to_earliest_seed_run",
            "plan_run_priority_policy": "baseline_autonomous prefers higher success; reactive and ablation labels prefer lower success",
        },
    )
    _write_json(report_dir / "latest_selection.json", selected)
    _write_json(report_dir / "label_to_axis_mapping.json", [variant.__dict__ for variant in VARIANTS])
    _write_csv(
        report_dir / "overall_summary.csv",
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
    _write_json(report_dir / "overall_summary.json", overall_rows)
    _write_csv(
        report_dir / "axis_summary.csv",
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
    _write_json(report_dir / "axis_summary.json", axis_rows_flat)
    for axis, rows in axis_rows.items():
        _write_csv(report_dir / f"{axis}.csv", rows, list(rows[0].keys()))
        _write_json(report_dir / f"{axis}.json", rows)

    return report_dir


def _load_axis_rows(report_dir: Path) -> list[dict[str, Any]]:
    path = report_dir / "axis_summary.json"
    _log(f"load axis summary: {path}")
    if not path.exists():
        raise SystemExit(
            json.dumps(
                {
                    "error": "missing_axis_summary",
                    "path": str(path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    _log(f"loaded axis rows: {len(payload) if isinstance(payload, list) else 'invalid'}")
    if not isinstance(payload, list):
        raise SystemExit(
            json.dumps(
                {
                    "error": "invalid_axis_summary",
                    "path": str(path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return payload


def _filter_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _log(f"filter rows: input={len(rows)}")
    filtered: list[dict[str, Any]] = []
    for row in rows:
        axis = str(row.get("axis", ""))
        variant = str(row.get("variant", ""))
        if axis == "collaborative_agent" and variant in {"wo_judge_wo_loop", "planning_off_wo_loop"}:
            continue
        filtered.append(row)
    _log(f"filter rows: output={len(filtered)}")
    return filtered


def _sort_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    _log("sort rows by axis")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for axis in AXIS_ORDER:
        axis_rows = [row for row in rows if str(row.get("axis", "")) == axis]
        axis_rows.sort(
            key=lambda row: (
                VARIANT_ORDER.get(axis, []).index(str(row.get("variant", "")))
                if str(row.get("variant", "")) in VARIANT_ORDER.get(axis, [])
                else 999,
                MODE_ORDER.index(str(row.get("mode", "")))
                if str(row.get("mode", "")) in MODE_ORDER
                else 999,
            )
        )
        grouped[axis] = axis_rows
        _log(f"axis={axis} rows={len(axis_rows)}")
    return grouped


def _rows_mode_blocked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for mode in MODE_ORDER:
        ordered.extend([row for row in rows if str(row.get("mode", "")) == mode])
    return ordered


def _prepare_plot_rows(axis: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = _rows_mode_blocked(rows)
    patched: list[dict[str, Any]] = []
    for row in ordered:
        new_row = dict(row)
        if str(new_row.get("display_name", "")) == "Baseline":
            new_row["display_name"] = "Ours"
        if axis != "execution_paradigm":
            patched.append(new_row)
            continue
        mode = str(new_row.get("mode", ""))
        if mode == "autonomous":
            new_row["display_name"] = "Ours"
        elif mode == "reactive":
            new_row["display_name"] = "Reactive"
        patched.append(new_row)
    return patched


def _resolve_margins(axis: str, include_title: bool) -> tuple[float, float, float, float]:
    if axis == "execution_paradigm":
        left_margin = EXECUTION_PARADIGM_LEFT_MARGIN_INCH
        right_margin = max(EXECUTION_PARADIGM_RIGHT_MARGIN_INCH, RIGHT_MARGIN_INCH + 0.18)
        bottom_margin = BOTTOM_MARGIN_INCH
    else:
        left_margin = LEFT_MARGIN_INCH
        right_margin = RIGHT_MARGIN_INCH + 0.10
        bottom_margin = BOTTOM_MARGIN_INCH
    top_margin = TOP_MARGIN_WITH_TITLE_INCH if include_title else TOP_MARGIN_INCH
    return left_margin, right_margin, bottom_margin, top_margin


def _plot_box_metadata_path(output_path: Path) -> Path:
    return output_path.with_suffix(".plot_box.json")


def _save_plot_box_metadata(output_path: Path, ax: plt.Axes, dpi: int) -> None:
    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    bbox = ax.get_window_extent(renderer=renderer)
    dpi_scale = dpi / fig.get_dpi()
    fig_width_px = int(fig.get_figwidth() * dpi)
    fig_height_px = int(fig.get_figheight() * dpi)
    payload = {
        "image_width_px": fig_width_px,
        "image_height_px": fig_height_px,
        "plot_left_px": int(round(bbox.x0 * dpi_scale)),
        "plot_right_px": int(round(bbox.x1 * dpi_scale)),
        "plot_top_px": int(round(fig_height_px - (bbox.y1 * dpi_scale))),
        "plot_bottom_px": int(round(fig_height_px - (bbox.y0 * dpi_scale))),
        "plot_width_px": int(round(bbox.width * dpi_scale)),
        "plot_height_px": int(round(bbox.height * dpi_scale)),
    }
    _write_json(_plot_box_metadata_path(output_path), payload)


def _save_figure(
    fig: plt.Figure,
    output_path: Path,
    dpi: int,
    plot_box_ax: plt.Axes,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _save_plot_box_metadata(output_path, plot_box_ax, dpi=dpi)
    fig.savefig(output_path, dpi=dpi, pad_inches=0.0)


def _plot_metric(
    output_path: Path,
    axis: str,
    title: str,
    rows: list[dict[str, Any]],
    metric_key: str,
    std_key: str,
    ylabel: str,
    include_title: bool = True,
    fixed_color: str | None = None,
    palette_by_label: bool = False,
) -> None:
    _log(f"plot metric start: axis={axis}, metric={metric_key}, output={output_path}")
    rows = _prepare_plot_rows(axis, rows)
    labels = [str(row["display_name"]) for row in rows]
    values = [float(row[metric_key]) for row in rows]
    errors = [float(row[std_key]) for row in rows]
    xs = list(range(len(labels)))
    if palette_by_label:
        colors = [VARIANT_COLORS.get(label, SINGLE_MODE_AXIS_COLORS.get(axis, "#F58518")) for label in labels]
    else:
        colors = (
            [fixed_color for _ in rows]
            if fixed_color
            else [MODE_COLORS.get(str(row.get("mode", "")), "#4C78A8") for row in rows]
        )
    ymax = max(values) if values else 1.0
    slot_count = max(len(labels), 1)

    left_margin, right_margin, bottom_margin, top_margin = _resolve_margins(axis, include_title)
    fig_width = left_margin + right_margin + (BAR_SLOT_WIDTH_INCH * slot_count)
    fig_height = 4.8
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    bars = ax.bar(
        xs,
        values,
        width=FIXED_BAR_WIDTH,
        yerr=errors,
        capsize=4,
        color=colors,
        edgecolor=EDGE_COLOR,
        linewidth=0.6,
        error_kw={"ecolor": BAR_STD_COLOR, "elinewidth": 1.0, "capthick": 1.0},
    )
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_xlim(-0.5, slot_count - 0.5)
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

    fig.subplots_adjust(
        left=left_margin / fig_width,
        right=1.0 - (right_margin / fig_width),
        bottom=bottom_margin / fig_height,
        top=1.0 - (top_margin / fig_height),
    )
    _log(f"save metric png: {output_path}")
    _save_figure(fig, output_path, dpi=220, plot_box_ax=ax)
    plt.close(fig)
    _log(f"plot metric done: {output_path}")


def _plot_dual_metric(
    output_path: Path,
    axis: str,
    rows: list[dict[str, Any]],
    primary_mean_key: str,
    primary_std_key: str,
    primary_ylabel: str,
    slug_title: str,
    include_title: bool = True,
    fixed_color: str | None = None,
    show_mode_legend: bool = True,
    palette_by_label: bool = False,
    include_legend: bool = True,
) -> None:
    _log(f"plot dual metric start: axis={axis}, metric={primary_mean_key}, output={output_path}")
    rows = _prepare_plot_rows(axis, rows)
    labels = [str(row["display_name"]) for row in rows]
    primary_values = [float(row[primary_mean_key]) for row in rows]
    primary_errors = [float(row[primary_std_key]) for row in rows]
    elapsed_values = [float(row["mean_elapsed_seconds"]) for row in rows]
    elapsed_errors = [float(row["std_elapsed_seconds"]) for row in rows]
    slot_count = max(len(labels), 1)
    if palette_by_label:
        mode_colors = [VARIANT_COLORS.get(label, SINGLE_MODE_AXIS_COLORS.get(axis, "#F58518")) for label in labels]
    else:
        mode_colors = (
            [fixed_color for _ in rows]
            if fixed_color
            else [MODE_COLORS.get(str(row.get("mode", "")), "#4C78A8") for row in rows]
        )
    xs = list(range(len(labels)))

    left_margin, right_margin, bottom_margin, top_margin = _resolve_margins(axis, include_title)
    fig_width = left_margin + right_margin + (BAR_SLOT_WIDTH_INCH * slot_count)
    fig_height = 6.0
    fig, ax1 = plt.subplots(figsize=(fig_width, fig_height))
    bars = ax1.bar(
        xs,
        primary_values,
        width=FIXED_BAR_WIDTH,
        yerr=primary_errors,
        capsize=4,
        color=mode_colors,
        edgecolor=EDGE_COLOR,
        linewidth=0.6,
        error_kw={"ecolor": BAR_STD_COLOR, "elinewidth": 1.0, "capthick": 1.0},
    )
    ax1.set_xticks(xs)
    ax1.set_xticklabels(labels, rotation=20, ha="right")
    ax1.set_xlim(-0.5, slot_count - 0.5)
    ax1.set_ylabel(primary_ylabel)
    ax1.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.35)
    ax1.set_axisbelow(True)

    ax2 = ax1.twinx()
    ax2.plot(xs, elapsed_values, color="black", linewidth=1.4, marker="o", markersize=0)
    lower_band = [max(0.0, value - error) for value, error in zip(elapsed_values, elapsed_errors)]
    upper_band = [value + error for value, error in zip(elapsed_values, elapsed_errors)]
    ax2.fill_between(xs, lower_band, upper_band, color=STD_COLOR, alpha=0.18, zorder=2)
    ax2.scatter(xs, elapsed_values, c=mode_colors, edgecolors="black", linewidths=0.6, s=42, zorder=4)
    ax2.set_ylabel("Elapsed Time (s)", labelpad=ELAPSED_TIME_LABELPAD)

    primary_max = max(primary_values) if primary_values else 1.0
    elapsed_max = max(elapsed_values) if elapsed_values else 1.0
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

    if include_legend:
        legend_handles: list[Any] = []
        if show_mode_legend:
            present_modes: list[str] = []
            for row in rows:
                mode = str(row.get("mode", ""))
                if mode and mode not in present_modes:
                    present_modes.append(mode)
            legend_handles.extend(
                [
                    Patch(facecolor=MODE_COLORS[mode], edgecolor=EDGE_COLOR, label=mode.title())
                    for mode in present_modes
                    if mode in MODE_COLORS
                ]
            )
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
            bbox_to_anchor=(
                LEGEND_ANCHOR_X_WITH_TITLE if include_title else LEGEND_ANCHOR_X_NO_TITLE,
                LEGEND_ANCHOR_Y_WITH_TITLE if include_title else LEGEND_ANCHOR_Y_NO_TITLE,
            ),
            ncol=len(legend_handles),
            frameon=False,
            borderaxespad=0.0,
            columnspacing=0.8,
            handletextpad=0.4,
            fontsize=LEGEND_FONT_SIZE,
        )
    else:
        pass
    if include_title:
        fig.suptitle(slug_title, y=0.955, fontsize=14)
    fig.subplots_adjust(
        left=left_margin / fig_width,
        right=1.0 - (right_margin / fig_width),
        bottom=bottom_margin / fig_height,
        top=1.0 - (top_margin / fig_height),
    )
    _log(f"save dual png: {output_path}")
    _save_figure(fig, output_path, dpi=220, plot_box_ax=ax1)
    plt.close(fig)
    _log(f"plot dual done: {output_path}")


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
    include_legend: bool = True,
) -> None:
    for mode in ("autonomous", "reactive"):
        mode_rows = rows if axis == "execution_paradigm" else [row for row in rows if str(row.get("mode", "")) == mode]
        if not mode_rows:
            continue
        mode_dir = figures_dir / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        _plot_dual_metric(
            mode_dir / f"{axis}_{slug}.png",
            axis=axis,
            rows=mode_rows,
            primary_mean_key=primary_mean_key,
            primary_std_key=primary_std_key,
            primary_ylabel=primary_ylabel,
            slug_title=title_prefix if axis == "execution_paradigm" else f"{title_prefix} - {mode.title()}",
            include_title=include_title,
            fixed_color=SINGLE_MODE_AXIS_COLORS.get(axis, "#F58518"),
            show_mode_legend=False,
            palette_by_label=True,
            include_legend=include_legend,
        )


def _plot_metric_by_mode(
    figures_dir: Path,
    axis: str,
    rows: list[dict[str, Any]],
    metric_key: str,
    std_key: str,
    ylabel: str,
    slug: str,
    title_prefix: str,
    include_title: bool = True,
) -> None:
    for mode in ("autonomous", "reactive"):
        mode_rows = rows if axis == "execution_paradigm" else [row for row in rows if str(row.get("mode", "")) == mode]
        if not mode_rows:
            continue
        mode_dir = figures_dir / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        _plot_metric(
            mode_dir / f"{axis}_{slug}.png",
            axis=axis,
            title=title_prefix if axis == "execution_paradigm" else f"{title_prefix} - {mode.title()}",
            rows=mode_rows,
            metric_key=metric_key,
            std_key=std_key,
            ylabel=ylabel,
            include_title=include_title,
            fixed_color=SINGLE_MODE_AXIS_COLORS.get(axis, "#F58518"),
            palette_by_label=True,
        )


def _plot_axis_figures(output_dir: Path, axis_rows: dict[str, list[dict[str, Any]]]) -> None:
    _log(f"plot all axis figures into: {output_dir}")
    figures_dir = output_dir / "figures"
    figures_with_title_dir = figures_dir / "with_title"
    figures_no_title_dir = figures_dir / "no_title"
    figures_with_title_dir.mkdir(parents=True, exist_ok=True)
    figures_no_title_dir.mkdir(parents=True, exist_ok=True)
    for axis, rows in axis_rows.items():
        _log(f"axis figure group start: {axis}, rows={len(rows)}")
        for metric_key, ylabel, slug in [
            ("success_rate_pct", "Success Rate (%)", "success_rate"),
            ("elapsed_seconds", "Elapsed Time (s)", "elapsed_time"),
        ]:
            std_key = {
                "success_rate_pct": "std_success_rate_pct",
                "elapsed_seconds": "std_elapsed_seconds",
            }[metric_key]
            _plot_metric(
                figures_with_title_dir / f"{axis}_{slug}.png",
                axis=axis,
                title=f"{axis.replace('_', ' ').title()} - {ylabel}",
                rows=rows,
                metric_key=f"mean_{metric_key}",
                std_key=std_key,
                ylabel=ylabel,
            )
            _plot_metric(
                figures_no_title_dir / f"{axis}_{slug}.png",
                axis=axis,
                title=f"{axis.replace('_', ' ').title()} - {ylabel}",
                rows=rows,
                metric_key=f"mean_{metric_key}",
                std_key=std_key,
                ylabel=ylabel,
                include_title=False,
            )
            _plot_metric_by_mode(
                figures_dir=figures_with_title_dir,
                axis=axis,
                rows=rows,
                metric_key=f"mean_{metric_key}",
                std_key=std_key,
                ylabel=ylabel,
                slug=slug,
                title_prefix=f"{axis.replace('_', ' ').title()} - {ylabel}",
            )
            _plot_metric_by_mode(
                figures_dir=figures_no_title_dir,
                axis=axis,
                rows=rows,
                metric_key=f"mean_{metric_key}",
                std_key=std_key,
                ylabel=ylabel,
                slug=slug,
                title_prefix=f"{axis.replace('_', ' ').title()} - {ylabel}",
                include_title=False,
            )
        _plot_dual_metric(
            figures_with_title_dir / f"{axis}_success_rate_vs_elapsed_time.png",
            axis=axis,
            rows=rows,
            primary_mean_key="mean_success_rate_pct",
            primary_std_key="std_success_rate_pct",
            primary_ylabel="Success Rate (%)",
            slug_title=f"{axis.replace('_', ' ').title()} - Success Rate vs. Elapsed Time",
            show_mode_legend=axis != "execution_paradigm",
            include_legend=axis == "execution_support",
        )
        _plot_dual_metric(
            figures_no_title_dir / f"{axis}_success_rate_vs_elapsed_time.png",
            axis=axis,
            rows=rows,
            primary_mean_key="mean_success_rate_pct",
            primary_std_key="std_success_rate_pct",
            primary_ylabel="Success Rate (%)",
            slug_title=f"{axis.replace('_', ' ').title()} - Success Rate vs. Elapsed Time",
            include_title=False,
            show_mode_legend=axis != "execution_paradigm",
            include_legend=axis == "execution_support",
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
            include_legend=axis == "execution_support",
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
            include_legend=axis == "execution_support",
        )
        _log(f"axis figure group done: {axis}")


def _stack_horizontal_images(image_paths: list[Path], output_path: Path, titles: list[str] | None = None) -> None:
    """Stack PNG files horizontally while making plot-box heights identical.

    This version detects the actual matplotlib plot box in each PNG, rescales
    each panel so the plot-box height is identical across panels, and then
    aligns the plot-box bottom. The alignment target is the axis box itself,
    not the tick labels or the full image boundary.
    """
    existing_paths = [path for path in image_paths if path.exists()]
    if len(existing_paths) != len(image_paths):
        missing_paths = [str(path) for path in image_paths if not path.exists()]
        _log(f"skip stacked image: missing files={missing_paths}")
        return

    def _longest_true_run(mask_1d: np.ndarray) -> int:
        if mask_1d.size == 0:
            return 0
        padded = np.concatenate(([False], mask_1d.astype(bool), [False]))
        changes = np.diff(padded.astype(np.int8))
        starts = np.where(changes == 1)[0]
        ends = np.where(changes == -1)[0]
        if starts.size == 0:
            return 0
        return int(np.max(ends - starts))

    def _detect_plot_box_bounds(image: Image.Image) -> tuple[int, int]:
        """Detect top and bottom y positions of the actual plot box."""
        arr = np.asarray(image.convert("RGBA"))
        rgb = arr[..., :3].astype(np.float32)
        alpha = arr[..., 3] > 16
        gray = rgb.mean(axis=-1)
        dark = (gray < 110) & alpha

        height, width = dark.shape
        y_min = int(height * 0.08)
        y_max = int(height * 0.92)
        if y_max <= y_min:
            fallback_top = int(height * 0.18)
            fallback_bottom = int(height * 0.75)
            _log(
                f"plot-box bounds fallback: top={fallback_top}, bottom={fallback_bottom}, "
                f"image_size=({width}, {height})"
            )
            return fallback_top, fallback_bottom

        search = dark[y_min:y_max]
        runs = np.array([_longest_true_run(row) for row in search], dtype=np.int32)
        if runs.size == 0 or int(runs.max()) == 0:
            fallback_top = int(height * 0.18)
            fallback_bottom = int(height * 0.75)
            _log(
                f"plot-box bounds fallback: top={fallback_top}, bottom={fallback_bottom}, "
                f"image_size=({width}, {height})"
            )
            return fallback_top, fallback_bottom

        max_run = int(runs.max())
        min_run = max(int(width * 0.20), int(max_run * 0.70), 80)
        candidates = np.where(runs >= min_run)[0]

        if candidates.size >= 2:
            top = y_min + int(candidates.min())
            bottom = y_min + int(candidates.max())
        elif candidates.size == 1:
            bottom = y_min + int(candidates[0])
            top = max(0, int(height * 0.18))
        else:
            strong = np.where(runs >= max(1, int(max_run * 0.55)))[0]
            if strong.size >= 2:
                top = y_min + int(strong.min())
                bottom = y_min + int(strong.max())
            else:
                bottom = y_min + int(np.argmax(runs))
                top = max(0, int(height * 0.18))

        if bottom <= top + 20:
            old_top, old_bottom = top, bottom
            top = int(height * 0.18)
            bottom = int(height * 0.75)
            _log(
                f"plot-box bounds corrected: detected=({old_top}, {old_bottom}), "
                f"corrected=({top}, {bottom}), image_size=({width}, {height})"
            )

        _log(
            f"detected plot-box bounds: top={top}, bottom={bottom}, "
            f"height={bottom - top}, max_run={max_run}, min_run={min_run}, "
            f"image_size=({width}, {height})"
        )
        return top, bottom

    def _load_plot_box_bounds(path: Path, image: Image.Image) -> tuple[int, int] | None:
        metadata_path = _plot_box_metadata_path(path)
        if not metadata_path.exists():
            return None
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        width = int(payload.get("image_width_px", 0) or 0)
        height = int(payload.get("image_height_px", 0) or 0)
        top = int(payload.get("plot_top_px", -1) or -1)
        bottom = int(payload.get("plot_bottom_px", -1) or -1)
        if abs(width - image.width) > 1 or abs(height - image.height) > 1:
            _log(
                f"plot-box metadata size mismatch: path={metadata_path}, "
                f"metadata_size=({width}, {height}), image_size={image.size}"
            )
            return None
        if top < 0 or bottom <= top:
            return None
        _log(
            f"loaded plot-box metadata: path={metadata_path}, "
            f"top={top}, bottom={bottom}, height={bottom - top}"
        )
        return top, bottom

    def _resize_image_and_bounds(
        image: Image.Image,
        top: int,
        bottom: int,
        scale: float,
    ) -> tuple[Image.Image, int, int]:
        if math.isclose(scale, 1.0, rel_tol=1e-6, abs_tol=1e-6):
            return image, top, bottom
        new_width = max(1, int(round(image.width * scale)))
        new_height = max(1, int(round(image.height * scale)))
        resized = image.resize((new_width, new_height), resample=Image.Resampling.LANCZOS)
        return resized, int(round(top * scale)), int(round(bottom * scale))

    def _trim_white_border(path: Path, padding_px: int = 8) -> None:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            arr = np.asarray(rgba)
        alpha = arr[..., 3] > 0
        rgb = arr[..., :3]
        non_white = alpha & np.any(rgb < 245, axis=-1)
        if not np.any(non_white):
            _log(f"skip trim: no non-white content in {path}")
            return

        ys, xs = np.where(non_white)
        left = max(0, int(xs.min()) - padding_px)
        right = min(rgba.width, int(xs.max()) + padding_px + 1)
        top = max(0, int(ys.min()) - padding_px)
        bottom = min(rgba.height, int(ys.max()) + padding_px + 1)
        cropped = rgba.crop((left, top, right, bottom))
        cropped.save(path)
        _log(
            f"trim stacked png: path={path}, crop_box=({left}, {top}, {right}, {bottom}), "
            f"old_size=({rgba.width}, {rgba.height}), new_size=({cropped.width}, {cropped.height})"
        )

    _log(f"read images: {[str(path) for path in existing_paths]}")

    pil_images: list[Image.Image] = []
    for path in existing_paths:
        with Image.open(path) as image:
            pil_images.append(image.convert("RGBA"))

    _log(f"read images done: sizes={[image.size for image in pil_images]}")

    show_titles = bool(titles)
    titles = titles or []

    gap_px = 18
    title_band_px = 54 if show_titles else 0

    bounds = [
        _load_plot_box_bounds(path, image) or _detect_plot_box_bounds(image)
        for path, image in zip(existing_paths, pil_images)
    ]
    plot_heights = [bottom - top for top, bottom in bounds]
    target_plot_height = max(plot_heights)

    resized_images: list[Image.Image] = []
    resized_bounds: list[tuple[int, int]] = []
    scales: list[float] = []

    panel_titles = titles if show_titles else [""] * len(pil_images)

    for image, (top, bottom), plot_height, title in zip(pil_images, bounds, plot_heights, panel_titles):
        scale = target_plot_height / max(plot_height, 1)
        if scale < 0.65 or scale > 1.55:
            _log(
                f"scale guard for panel {title}: raw_scale={scale:.3f}, "
                f"plot_height={plot_height}, target_plot_height={target_plot_height}; use scale=1.000"
            )
            scale = 1.0
        resized, scaled_top, scaled_bottom = _resize_image_and_bounds(image, top, bottom, scale)
        resized_images.append(resized)
        resized_bounds.append((scaled_top, scaled_bottom))
        scales.append(scale)
        _log(
            f"panel {title} resize: original_size=({image.width}, {image.height}), "
            f"new_size=({resized.width}, {resized.height}), scale={scale:.4f}, "
            f"plot_bounds=({top}, {bottom}), scaled_bounds=({scaled_top}, {scaled_bottom}), "
            f"scaled_plot_height={scaled_bottom - scaled_top}"
        )

    max_above_axis = max(bottom for _, bottom in resized_bounds)
    max_below_axis = max(
        image.height - bottom
        for image, (_, bottom) in zip(resized_images, resized_bounds)
    )

    total_width = sum(image.width for image in resized_images) + gap_px * (len(resized_images) - 1)
    total_height = title_band_px + max_above_axis + max_below_axis
    target_axis_bottom_y = title_band_px + max_above_axis

    _log(
        f"stack canvas size: width={total_width}, height={total_height}, "
        f"target_axis_bottom_y={target_axis_bottom_y}, target_plot_height={target_plot_height}, "
        f"bounds={bounds}, resized_bounds={resized_bounds}, scales={[round(s, 4) for s in scales]}"
    )

    canvas = Image.new("RGBA", (total_width, total_height), (255, 255, 255, 255))

    x_cursor = 0
    title_centers: list[tuple[float, str]] = []
    for image, title, (scaled_top, scaled_bottom) in zip(resized_images, panel_titles, resized_bounds):
        y_offset = target_axis_bottom_y - scaled_bottom
        canvas.paste(image, (x_cursor, y_offset))
        if show_titles:
            title_centers.append((x_cursor + image.width / 2.0, title))
        _log(
            f"paste panel {title}: x={x_cursor}, y={y_offset}, "
            f"plot_top_y={y_offset + scaled_top}, plot_bottom_y={y_offset + scaled_bottom}, "
            f"plot_height={scaled_bottom - scaled_top}"
        )
        x_cursor += image.width + gap_px

    dpi = 220
    fig, ax = plt.subplots(figsize=(total_width / dpi, total_height / dpi), dpi=dpi)
    ax.imshow(canvas)
    ax.axis("off")
    if show_titles:
        _log("drawing stacked titles")
        for center_x, title in title_centers:
            ax.text(
                center_x,
                title_band_px * 0.55,
                title,
                ha="center",
                va="center",
                fontsize=13,
                color="black",
            )

    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _log(f"save stacked png: {output_path}")
    fig.savefig(output_path, dpi=dpi, pad_inches=0.02)
    plt.close(fig)
    _trim_white_border(output_path)
    _log(f"stacked png done: {output_path}")

def _build_autonomous_no_title_combo(output_dir: Path) -> None:
    _log("build autonomous no-title combo start")
    base_dir = output_dir / "figures" / "no_title" / "autonomous"
    _stack_horizontal_images(
        [
            base_dir / "execution_paradigm_success_rate_vs_elapsed_time.png",
            base_dir / "collaborative_agent_success_rate_vs_elapsed_time.png",
            base_dir / "execution_support_success_rate_vs_elapsed_time.png",
        ],
        base_dir / "stacked_success_rate_vs_elapsed_time.png",
    )


def _write_stacked_figure_tex(report_dir: Path, output_dir: Path) -> None:
    png_path = output_dir / "figures" / "no_title" / "autonomous" / "stacked_success_rate_vs_elapsed_time.png"
    if not png_path.exists():
        _log(f"skip stacked figure tex: missing png={png_path}")
        return

    tex_dir = report_dir / "tex"
    relative_png_path = png_path.relative_to(report_dir).as_posix()
    tex_text = "\n".join(
        [
            "% Requires: \\usepackage{graphicx}",
            r"\begin{figure*}[t]",
            r"\centering",
            rf"\includegraphics[width=\textwidth]{{{relative_png_path}}}",
            r"\caption{Success rate and elapsed time across the three autonomous settings.}",
            r"\label{fig:axis-analysis-stacked-success-rate-vs-elapsed-time}",
            r"\end{figure*}",
            "",
        ]
    )
    _write_tex(tex_dir / "stacked_success_rate_vs_elapsed_time.tex", tex_text)
    _write_tex(tex_dir / "stacked_success_rate_vs_elapsed_time_figure.tex", tex_text)


def _write_axis_summary_table_tex(report_dir: Path, filtered_rows: list[dict[str, Any]]) -> None:
    tex_dir = report_dir / "tex"
    axis_rows = _sort_rows(filtered_rows)
    table_text = _build_axis_summary_table_text(report_dir, axis_rows)
    _write_tex(tex_dir / "axis_summary_table.tex", table_text)


def main() -> int:
    _log("program start")
    parser = argparse.ArgumentParser(
        description="Re-plot charts from an axis_analysis directory or build them from one or more plan_runs directories."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Path to an axis_analysis/<timestamp> directory, or one/more reports/plan_runs/<timestamp> directories",
    )
    parser.add_argument(
        "--output-report-dir",
        help="When using plan_runs inputs, write the generated axis_analysis-style report to this directory",
    )
    parser.add_argument(
        "--target-runs-per-label",
        type=int,
        default=DEFAULT_PLAN_RUN_TARGET,
        help=f"When using plan_runs inputs, aggregate this many runs per label (default: {DEFAULT_PLAN_RUN_TARGET})",
    )
    parser.add_argument(
        "--seed-axis-report",
        help="Optional axis_analysis/<timestamp> directory whose latest_selection.json runs must be included first",
    )
    args = parser.parse_args()

    input_paths = [Path(value).resolve() for value in args.inputs]
    for input_path in input_paths:
        if not input_path.exists():
            raise SystemExit(
                json.dumps(
                    {
                        "error": "missing_input_path",
                        "path": str(input_path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    if len(input_paths) == 1 and (input_paths[0] / "axis_summary.json").exists():
        report_dir = input_paths[0]
        _log(f"use existing axis_analysis report: {report_dir}")
    else:
        output_report_dir = Path(args.output_report_dir).resolve() if args.output_report_dir else None
        seed_axis_report_dir = Path(args.seed_axis_report).resolve() if args.seed_axis_report else None
        if seed_axis_report_dir and not seed_axis_report_dir.exists():
            raise SystemExit(
                json.dumps(
                    {
                        "error": "missing_seed_axis_report_dir",
                        "path": str(seed_axis_report_dir),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        report_dir = _prepare_report_from_plan_runs(
            input_paths,
            output_report_dir,
            target_runs_per_label=max(1, int(args.target_runs_per_label)),
            seed_axis_report_dir=seed_axis_report_dir,
        )

    rows = _filter_rows(_load_axis_rows(report_dir))
    axis_rows = _sort_rows(rows)

    output_dir = report_dir / "replotted_fixed_layout"
    _log(f"output_dir={output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    rows_flat: list[dict[str, Any]] = []
    for axis in AXIS_ORDER:
        rows_flat.extend(axis_rows.get(axis, []))

    _write_json(output_dir / "axis_summary_filtered.json", rows_flat)
    if rows_flat:
        _write_csv(output_dir / "axis_summary_filtered.csv", rows_flat, list(rows_flat[0].keys()))
        _write_axis_summary_table_tex(report_dir, rows_flat)

    _log("start plotting axis figures")
    _plot_axis_figures(output_dir, axis_rows)
    _log("finished plotting axis figures")
    _log("start building stacked combo")
    _build_autonomous_no_title_combo(output_dir)
    _log("finished building stacked combo")
    _write_stacked_figure_tex(report_dir, output_dir)

    print(
        json.dumps(
            {
                "source_report_dir": str(report_dir),
                "output_dir": str(output_dir),
                "axes": AXIS_ORDER,
                "input_mode": "axis_analysis" if len(input_paths) == 1 and (input_paths[0] / "axis_summary.json").exists() else "plan_runs",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
