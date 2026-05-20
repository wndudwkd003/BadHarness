#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


MODE_ORDER = ["autonomous", "reactive"]
AXIS_ORDER = ["execution_paradigm", "collaborative_agent", "execution_support"]
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
AXIS_TITLES = {
    "execution_paradigm": "Execution Paradigm",
    "collaborative_agent": "Collaborative Agent",
    "execution_support": "Execution Support",
}
TABLE_AXES = ["execution_paradigm", "collaborative_agent", "execution_support"]


def _log(message: str) -> None:
    print(f"[axis-tex] {message}", flush=True)


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
    filtered: list[dict[str, Any]] = []
    for row in rows:
        axis = str(row.get("axis", ""))
        variant = str(row.get("variant", ""))
        if axis == "collaborative_agent" and variant in {"wo_judge_wo_loop", "planning_off_wo_loop"}:
            continue
        filtered.append(row)
    return filtered


def _sort_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
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
    return grouped


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
    escaped = []
    for char in text:
        escaped.append(replacements.get(char, char))
    return "".join(escaped)


def _display_name(axis: str, row: dict[str, Any]) -> str:
    name = str(row.get("display_name", "") or "").strip()
    mode = str(row.get("mode", "") or "").strip()
    if name == "Baseline":
        return "Reactive" if axis == "execution_paradigm" and mode == "reactive" else "Ours"
    return name


def _format_value(mean_value: float, std_value: float, digits: int) -> str:
    return f"{mean_value:.{digits}f} $\\pm$ {std_value:.{digits}f}".replace("$\\\\pm$", "$\\pm$")


def _find_autonomous_ours(axis_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    for axis in AXIS_ORDER:
        for row in axis_rows.get(axis, []):
            if str(row.get("variant", "")) == "baseline" and str(row.get("mode", "")) == "autonomous":
                return row
    raise SystemExit(
        json.dumps(
            {
                "error": "missing_autonomous_baseline",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _format_change(value: float, baseline: float) -> str:
    if baseline == 0:
        return "n/a"
    delta = ((value - baseline) / baseline) * 100.0
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.2f}\\%"


def _build_change_cell(row: dict[str, Any], ours_row: dict[str, Any]) -> str:
    sr_change = _format_change(
        float(row.get("mean_success_rate_pct", 0.0) or 0.0),
        float(ours_row.get("mean_success_rate_pct", 0.0) or 0.0),
    )
    time_change = _format_change(
        float(row.get("mean_elapsed_seconds", 0.0) or 0.0),
        float(ours_row.get("mean_elapsed_seconds", 0.0) or 0.0),
    )
    return sr_change, time_change


def _build_axis_block(axis: str, rows: list[dict[str, Any]], ours_row: dict[str, Any]) -> list[str]:
    lines = [rf"\multicolumn{{5}}{{l}}{{\textbf{{{_latex_escape(AXIS_TITLES.get(axis, axis))}}}}} \\"]
    if axis == "execution_paradigm":
        block_rows = [
            row
            for row in rows
            if str(row.get("variant", "")) == "baseline" and str(row.get("mode", "")) == "reactive"
        ]
    else:
        block_rows = [
            row
            for row in rows
            if str(row.get("mode", "")) == "autonomous" and str(row.get("variant", "")) != "baseline"
        ]

    for row in block_rows:
        variant = str(row.get("variant", "") or "")
        if axis != "execution_paradigm" and variant not in VARIANT_ORDER.get(axis, []):
            continue
        method = _latex_escape(_display_name(axis, row))
        success_rate = _format_value(
            float(row.get("mean_success_rate_pct", 0.0) or 0.0),
            float(row.get("std_success_rate_pct", 0.0) or 0.0),
            digits=2,
        )
        elapsed_time = _format_value(
            float(row.get("mean_elapsed_seconds", 0.0) or 0.0),
            float(row.get("std_elapsed_seconds", 0.0) or 0.0),
            digits=2,
        )
        sr_change, time_change = _build_change_cell(row, ours_row)
        lines.append(f"{method} & {success_rate} & {elapsed_time} & {sr_change} & {time_change} \\\\")
    return lines


def _build_table_text(report_dir: Path, axis_rows: dict[str, list[dict[str, Any]]]) -> str:
    ours_row = _find_autonomous_ours(axis_rows)
    ours_success_rate = _format_value(
        float(ours_row.get("mean_success_rate_pct", 0.0) or 0.0),
        float(ours_row.get("std_success_rate_pct", 0.0) or 0.0),
        digits=2,
    )
    ours_elapsed_time = _format_value(
        float(ours_row.get("mean_elapsed_seconds", 0.0) or 0.0),
        float(ours_row.get("std_elapsed_seconds", 0.0) or 0.0),
        digits=2,
    )
    lines = [
        "% Requires: \\usepackage{booktabs}",
        r"\begin{table*}[t]",
        r"\centering",
        r"\caption{Table comparing success rate and elapsed time across the three settings.}",
        r"\label{tab:axis-analysis-summary}",
        r"\vspace{1.5mm}",
        r"\small",
        r"\setlength{\tabcolsep}{5pt}",
        r"\begin{tabular}{lcccc}",
        r"\toprule",
        r"Method & Success Rate (\%) & Elapsed Time (s) & SR Change & Time Change \\",
        r"\midrule",
        rf"\textbf{{Ours}} & \textbf{{{ours_success_rate}}} & \textbf{{{ours_elapsed_time}}} & \textbf{{--}} & \textbf{{--}} \\",
        r"\midrule",
    ]

    for axis_index, axis in enumerate(TABLE_AXES):
        block_lines = _build_axis_block(axis, axis_rows.get(axis, []), ours_row)
        if len(block_lines) <= 1:
            continue
        lines.extend(block_lines)
        if axis_index != len(TABLE_AXES) - 1:
            lines.append(r"\midrule")

    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table*}",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a LaTeX table from an existing axis_analysis directory.")
    parser.add_argument("report_dir", help="Path to an existing axis_analysis/<timestamp> directory")
    args = parser.parse_args()

    report_dir = Path(args.report_dir).resolve()
    if not report_dir.exists():
        raise SystemExit(
            json.dumps(
                {
                    "error": "missing_report_dir",
                    "path": str(report_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    rows = _filter_rows(_load_axis_rows(report_dir))
    axis_rows = _sort_rows(rows)

    tex_dir = report_dir / "tex"
    tex_dir.mkdir(parents=True, exist_ok=True)
    output_path = tex_dir / "axis_summary_table.tex"

    table_text = _build_table_text(report_dir, axis_rows)
    _log(f"write tex: {output_path}")
    output_path.write_text(table_text, encoding="utf-8")

    print(
        json.dumps(
            {
                "source_report_dir": str(report_dir),
                "output_path": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
