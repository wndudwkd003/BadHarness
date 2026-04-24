from __future__ import annotations

import csv
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from configs.config import (
    AGENT_ID,
    ALLOWED_SKILLS,
    ALLOWED_TOOLS,
    REPORT_TOP_N,
    TECHNIQUE,
)
from core.workspace import (
    get_analysis_dir,
    get_comparison_reports_dir,
    get_experiment_dir,
    get_memory_dir,
    get_metadata_path,
    get_runtime_dir,
)


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
HISTORY_ENTRY_RE = re.compile(
    r"^\[(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (?P<title>[^\n]+)\n(?P<content>.*?)(?=^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] |\Z)",
    re.MULTILINE | re.DOTALL,
)


def _safe_read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _safe_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _parse_dt(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, TIME_FORMAT)
    except Exception:
        return None


def _format_float(value: float) -> str:
    return f"{value:.3f}"


def _section_map(text: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    matches = list(re.finditer(r"^\[(?P<name>[^\n]+)\]\n", text, re.MULTILINE))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        parts[match.group("name").strip()] = text[start:end].strip()
    return parts


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_figure(figsize: tuple[float, float] = (11.5, 6.5)) -> tuple[Any, Any]:
    fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor("#f8fafc")
    ax.set_facecolor("#ffffff")
    return fig, ax


def _save_figure(fig: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _save_bar_chart_png(
    path: Path,
    *,
    title: str,
    rows: list[dict[str, Any]],
    label_key: str,
    value_key: str,
    horizontal: bool = False,
) -> None:
    fig, ax = _make_figure((12, 7))
    if not rows:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", fontsize=18, color="#64748b")
        ax.axis("off")
        _save_figure(fig, path)
        return

    labels = [str(row.get(label_key, "")) for row in rows]
    values = [float(row.get(value_key, 0) or 0) for row in rows]
    colors = [_color(idx) for idx in range(len(rows))]

    if horizontal:
        ax.barh(labels, values, color=colors)
        ax.invert_yaxis()
    else:
        ax.bar(range(len(labels)), values, color=colors)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=25, ha="right")

    ax.set_title(title, fontsize=18, fontweight="bold")
    ax.grid(axis="y" if not horizontal else "x", alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    _save_figure(fig, path)


def _save_grouped_bar_chart_png(
    path: Path,
    *,
    title: str,
    labels: list[str],
    series: list[tuple[str, list[float]]],
) -> None:
    fig, ax = _make_figure((12, 7))
    if not labels or not series:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", fontsize=18, color="#64748b")
        ax.axis("off")
        _save_figure(fig, path)
        return

    x = list(range(len(labels)))
    width = 0.8 / max(1, len(series))
    for idx, (name, values) in enumerate(series):
        shifted = [item + (idx - (len(series) - 1) / 2) * width for item in x]
        ax.bar(shifted, values, width=width, label=name, color=_color(idx))

    ax.set_title(title, fontsize=18, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=25, ha="right")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    _save_figure(fig, path)


def _save_line_chart_png(
    path: Path,
    *,
    title: str,
    points: list[tuple[float, float]],
    x_label: str = "relative seconds",
    y_label: str = "count",
) -> None:
    fig, ax = _make_figure((12, 7))
    if not points:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", fontsize=18, color="#64748b")
        ax.axis("off")
        _save_figure(fig, path)
        return

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    ax.plot(xs, ys, marker="o", color="#2563eb", linewidth=2.5)
    ax.set_title(title, fontsize=18, fontweight="bold")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.grid(alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    _save_figure(fig, path)


def _save_scatter_timeline_png(
    path: Path,
    *,
    title: str,
    events: list[dict[str, Any]],
) -> None:
    fig, ax = _make_figure((13, 7))
    if not events:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", fontsize=18, color="#64748b")
        ax.axis("off")
        _save_figure(fig, path)
        return

    categories = ["monitor", "selection", "action", "correct"]
    y_map = {name: idx for idx, name in enumerate(categories)}
    color_map = {
        "monitor": "#2563eb",
        "selection": "#7c3aed",
        "action": "#ea580c",
        "correct": "#16a34a",
    }
    for category in categories:
        subset = [item for item in events if str(item.get("category", "")) == category]
        xs = [float(item.get("relative_seconds", 0) or 0) for item in subset]
        ys = [y_map[category]] * len(subset)
        ax.scatter(xs, ys, s=50, color=color_map[category], label=category)

    ax.set_title(title, fontsize=18, fontweight="bold")
    ax.set_xlabel("relative seconds")
    ax.set_yticks(list(y_map.values()))
    ax.set_yticklabels(categories)
    ax.grid(alpha=0.25)
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    _save_figure(fig, path)


def _save_summary_dashboard_png(path: Path, metrics: dict[str, Any]) -> None:
    fig, axes = plt.subplots(2, 4, figsize=(14, 7))
    fig.patch.set_facecolor("#f8fafc")
    cards = [
        ("Correct Flags", metrics.get("correct_flag_submissions", 0)),
        ("Processed Triggers", metrics.get("processed_triggers", 0)),
        ("Elapsed (s)", metrics.get("elapsed_seconds", 0)),
        ("Trigger Efficiency", metrics.get("trigger_efficiency", 0)),
        ("Unique Observed Flags", metrics.get("unique_flags_observed", 0)),
        ("Unique Submitted Flags", metrics.get("unique_flags_submitted", 0)),
        ("LLM Calls", metrics.get("llm_calls", 0)),
        ("Total Tokens", metrics.get("total_tokens", 0)),
    ]
    for idx, ax in enumerate(axes.flat):
        ax.set_facecolor("#ffffff")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_edgecolor("#cbd5e1")
        title, value = cards[idx]
        ax.text(0.05, 0.78, str(title), fontsize=11, color="#64748b", transform=ax.transAxes)
        ax.text(0.05, 0.35, str(value), fontsize=24, fontweight="bold", color="#0f172a", transform=ax.transAxes)
    fig.suptitle(f"Experiment Dashboard: {metrics.get('experiment_id', '-')}", fontsize=20, fontweight="bold")
    _save_figure(fig, path)


def _svg_text(x: float, y: float, content: str, size: int = 14, anchor: str = "start", weight: str = "400", fill: str = "#0f172a") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
        f'font-family="Arial, sans-serif" font-weight="{weight}" fill="{fill}">{escape(content)}</text>'
    )


def _svg_wrap(width: int, height: int, inner: list[str]) -> str:
    body = "\n  ".join(inner)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
        f'  <rect width="{width}" height="{height}" fill="#f8fafc"/>\n'
        f'  {body}\n'
        f"</svg>\n"
    )


def _color(index: int) -> str:
    palette = [
        "#2563eb",
        "#16a34a",
        "#ea580c",
        "#7c3aed",
        "#dc2626",
        "#0891b2",
        "#ca8a04",
        "#4f46e5",
        "#059669",
        "#db2777",
    ]
    return palette[index % len(palette)]


def _write_svg(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _new_canvas(width: int, height: int, bg: tuple[int, int, int] = (248, 250, 252)) -> dict[str, Any]:
    pixels = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 3
            pixels[offset : offset + 3] = bytes(bg)
    return {
        "width": width,
        "height": height,
        "pixels": pixels,
    }


def _set_pixel(canvas: dict[str, Any], x: int, y: int, color: tuple[int, int, int]) -> None:
    width = int(canvas["width"])
    height = int(canvas["height"])
    if x < 0 or y < 0 or x >= width or y >= height:
        return
    offset = (y * width + x) * 3
    canvas["pixels"][offset : offset + 3] = bytes(color)


def _fill_rect(canvas: dict[str, Any], x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
    width = int(canvas["width"])
    height = int(canvas["height"])
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(width, x + max(0, w))
    y1 = min(height, y + max(0, h))
    for py in range(y0, y1):
        row_offset = (py * width) * 3
        for px in range(x0, x1):
            offset = row_offset + px * 3
            canvas["pixels"][offset : offset + 3] = bytes(color)


def _draw_line(canvas: dict[str, Any], x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int]) -> None:
    dx = abs(x2 - x1)
    sx = 1 if x1 < x2 else -1
    dy = -abs(y2 - y1)
    sy = 1 if y1 < y2 else -1
    err = dx + dy
    while True:
        _set_pixel(canvas, x1, y1, color)
        if x1 == x2 and y1 == y2:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x1 += sx
        if e2 <= dx:
            err += dx
            y1 += sy


def _draw_circle(canvas: dict[str, Any], cx: int, cy: int, radius: int, color: tuple[int, int, int]) -> None:
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= radius * radius:
                _set_pixel(canvas, x, y, color)


def _save_bmp(path: Path, canvas: dict[str, Any]) -> None:
    width = int(canvas["width"])
    height = int(canvas["height"])
    pixels: bytearray = canvas["pixels"]
    row_size = width * 3
    padded_row_size = (row_size + 3) & ~3
    pixel_array_size = padded_row_size * height
    file_size = 54 + pixel_array_size

    header = bytearray()
    header.extend(b"BM")
    header.extend(file_size.to_bytes(4, "little"))
    header.extend((0).to_bytes(4, "little"))
    header.extend((54).to_bytes(4, "little"))
    header.extend((40).to_bytes(4, "little"))
    header.extend(width.to_bytes(4, "little", signed=True))
    header.extend(height.to_bytes(4, "little", signed=True))
    header.extend((1).to_bytes(2, "little"))
    header.extend((24).to_bytes(2, "little"))
    header.extend((0).to_bytes(4, "little"))
    header.extend(pixel_array_size.to_bytes(4, "little"))
    header.extend((2835).to_bytes(4, "little"))
    header.extend((2835).to_bytes(4, "little"))
    header.extend((0).to_bytes(4, "little"))
    header.extend((0).to_bytes(4, "little"))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(header)
        padding = b"\x00" * (padded_row_size - row_size)
        for y in range(height - 1, -1, -1):
            row = bytearray()
            for x in range(width):
                offset = (y * width + x) * 3
                r, g, b = pixels[offset : offset + 3]
                row.extend(bytes((b, g, r)))
            row.extend(padding)
            handle.write(row)


def _chart_canvas(width: int = 960, height: int = 560) -> dict[str, Any]:
    canvas = _new_canvas(width, height)
    _fill_rect(canvas, 0, 0, width, 18, (37, 99, 235))
    return canvas


def _bar_chart_bmp(
    *,
    rows: list[dict[str, Any]],
    value_key: str,
    width: int = 960,
    height: int = 560,
    horizontal: bool = False,
) -> dict[str, Any]:
    canvas = _chart_canvas(width, height)
    if not rows:
        return canvas

    margin_left = 160 if horizontal else 70
    margin_right = 40
    margin_top = 40
    margin_bottom = 60 if not horizontal else 40
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    axis = (148, 163, 184)
    grid = (226, 232, 240)
    max_value = max(float(row.get(value_key, 0) or 0) for row in rows)
    max_value = max(max_value, 1.0)

    if horizontal:
        _draw_line(canvas, margin_left, margin_top, margin_left, margin_top + plot_h, axis)
        bar_h = max(16, min(36, int(plot_h / max(1, len(rows)) - 8)))
        gap = max(8, int((plot_h - (bar_h * len(rows))) / max(1, len(rows))))
        for idx, row in enumerate(rows):
            value = float(row.get(value_key, 0) or 0)
            y = margin_top + idx * (bar_h + gap)
            _draw_line(canvas, margin_left, y + bar_h // 2, margin_left + plot_w, y + bar_h // 2, grid)
            bar_w = int((value / max_value) * plot_w)
            _fill_rect(canvas, margin_left, y, bar_w, bar_h, tuple(int(_color(idx)[i : i + 2], 16) for i in (1, 3, 5)))
            _fill_rect(canvas, 20, y, 14, bar_h, tuple(int(_color(idx)[i : i + 2], 16) for i in (1, 3, 5)))
    else:
        _draw_line(canvas, margin_left, margin_top + plot_h, margin_left + plot_w, margin_top + plot_h, axis)
        bar_w = max(14, min(50, int(plot_w / max(1, len(rows)) - 8)))
        gap = max(8, int((plot_w - (bar_w * len(rows))) / max(1, len(rows))))
        for idx, row in enumerate(rows):
            value = float(row.get(value_key, 0) or 0)
            x = margin_left + idx * (bar_w + gap)
            bar_h = int((value / max_value) * plot_h)
            y = margin_top + plot_h - bar_h
            _fill_rect(canvas, x, y, bar_w, bar_h, tuple(int(_color(idx)[i : i + 2], 16) for i in (1, 3, 5)))
            _fill_rect(canvas, x, margin_top + plot_h + 8, bar_w, 10, tuple(int(_color(idx)[i : i + 2], 16) for i in (1, 3, 5)))
    return canvas


def _grouped_bar_chart_bmp(labels: list[str], series: list[tuple[str, list[float]]], width: int = 960, height: int = 560) -> dict[str, Any]:
    canvas = _chart_canvas(width, height)
    if not labels or not series:
        return canvas

    margin_left = 80
    margin_right = 40
    margin_top = 50
    margin_bottom = 60
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    max_value = max(max(values) if values else 0 for _, values in series)
    max_value = max(float(max_value), 1.0)
    group_w = plot_w / max(1, len(labels))
    bar_w = max(10, min(28, int(group_w / max(1, len(series)) - 4)))
    _draw_line(canvas, margin_left, margin_top + plot_h, margin_left + plot_w, margin_top + plot_h, (148, 163, 184))

    for idx in range(len(labels)):
        gx = margin_left + int(idx * group_w)
        for s_idx, (_, values) in enumerate(series):
            value = float(values[idx]) if idx < len(values) else 0.0
            x = gx + 8 + s_idx * (bar_w + 4)
            bar_h = int((value / max_value) * plot_h)
            y = margin_top + plot_h - bar_h
            color = tuple(int(_color(s_idx)[i : i + 2], 16) for i in (1, 3, 5))
            _fill_rect(canvas, x, y, bar_w, bar_h, color)
    return canvas


def _line_chart_bmp(points: list[tuple[float, float]], width: int = 960, height: int = 560) -> dict[str, Any]:
    canvas = _chart_canvas(width, height)
    if not points:
        return canvas

    margin_left = 80
    margin_right = 40
    margin_top = 50
    margin_bottom = 60
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    max_x = max(point[0] for point in points) or 1.0
    max_y = max(point[1] for point in points) or 1.0
    max_x = max(max_x, 1.0)
    max_y = max(max_y, 1.0)
    _draw_line(canvas, margin_left, margin_top + plot_h, margin_left + plot_w, margin_top + plot_h, (148, 163, 184))
    _draw_line(canvas, margin_left, margin_top, margin_left, margin_top + plot_h, (148, 163, 184))

    prev = None
    for x_value, y_value in points:
        x = margin_left + int((x_value / max_x) * plot_w)
        y = margin_top + plot_h - int((y_value / max_y) * plot_h)
        if prev is not None:
            _draw_line(canvas, prev[0], prev[1], x, y, (37, 99, 235))
        _draw_circle(canvas, x, y, 4, (37, 99, 235))
        prev = (x, y)
    return canvas


def _timeline_bmp(events: list[dict[str, Any]], width: int = 1080, height: int = 520) -> dict[str, Any]:
    canvas = _chart_canvas(width, height)
    if not events:
        return canvas

    categories = ["monitor", "selection", "action", "correct"]
    y_map = {name: 100 + idx * 90 for idx, name in enumerate(categories)}
    margin_left = 120
    margin_right = 40
    plot_w = width - margin_left - margin_right
    max_x = max(float(event.get("relative_seconds", 0) or 0) for event in events)
    max_x = max(max_x, 1.0)

    for y in y_map.values():
        _draw_line(canvas, margin_left, y, margin_left + plot_w, y, (203, 213, 225))

    for index, event in enumerate(events[:160]):
        x = margin_left + int((float(event.get("relative_seconds", 0) or 0) / max_x) * plot_w)
        category = str(event.get("category", "monitor"))
        y = y_map.get(category, y_map["monitor"])
        color_map = {
            "monitor": (37, 99, 235),
            "selection": (124, 58, 237),
            "action": (234, 88, 12),
            "correct": (22, 163, 74),
        }
        _draw_circle(canvas, x, y, 5, color_map.get(category, tuple(int(_color(index)[i : i + 2], 16) for i in (1, 3, 5))))
    return canvas


def _dashboard_bmp(metrics: dict[str, Any], width: int = 1080, height: int = 420) -> dict[str, Any]:
    canvas = _chart_canvas(width, height)
    metrics_order = [
        float(metrics.get("correct_flag_submissions", 0) or 0),
        float(metrics.get("processed_triggers", 0) or 0),
        float(metrics.get("elapsed_seconds", 0) or 0),
        float(metrics.get("unique_flags_observed", 0) or 0),
        float(metrics.get("unique_flags_submitted", 0) or 0),
        float(metrics.get("llm_calls", 0) or 0),
        float(metrics.get("total_tokens", 0) or 0),
        float(float(metrics.get("trigger_efficiency", 0) or 0) * 100),
    ]
    max_value = max(metrics_order) if metrics_order else 1.0
    max_value = max(max_value, 1.0)
    x_positions = [32, 288, 544, 800]
    y_positions = [90, 220]
    idx = 0
    for y in y_positions:
        for x in x_positions:
            if idx >= len(metrics_order):
                break
            value = metrics_order[idx]
            _fill_rect(canvas, x, y, 220, 100, (255, 255, 255))
            _fill_rect(canvas, x, y, 220, 8, tuple(int(_color(idx)[i : i + 2], 16) for i in (1, 3, 5)))
            bar_w = int((value / max_value) * 180)
            _fill_rect(canvas, x + 20, y + 46, bar_w, 24, tuple(int(_color(idx)[i : i + 2], 16) for i in (1, 3, 5)))
            idx += 1
    return canvas

def _bar_chart_svg(
    *,
    title: str,
    rows: list[dict[str, Any]],
    label_key: str,
    value_key: str,
    width: int = 960,
    height: int = 560,
    horizontal: bool = False,
    unit: str = "",
) -> str:
    margin_left = 230 if horizontal else 70
    margin_right = 40
    margin_top = 70
    margin_bottom = 120 if not horizontal else 40
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    inner: list[str] = [
        _svg_text(32, 36, title, size=24, weight="700"),
        _svg_text(32, 58, f"rows={len(rows)}", size=12, fill="#475569"),
    ]

    if not rows:
        inner.append(_svg_text(width / 2, height / 2, "no data", size=20, anchor="middle", fill="#64748b"))
        return _svg_wrap(width, height, inner)

    values = [float(row.get(value_key, 0) or 0) for row in rows]
    max_value = max(values) if values else 0.0
    max_value = max(max_value, 1.0)

    if horizontal:
        bar_h = max(22.0, min(40.0, plot_h / max(1, len(rows)) - 10.0))
        gap = max(8.0, (plot_h - (bar_h * len(rows))) / max(1, len(rows)))
        for idx, row in enumerate(rows):
            value = float(row.get(value_key, 0) or 0)
            label = str(row.get(label_key, ""))
            y = margin_top + idx * (bar_h + gap)
            bar_w = (value / max_value) * plot_w
            inner.append(f'<line x1="{margin_left}" y1="{y + bar_h/2:.1f}" x2="{margin_left + plot_w}" y2="{y + bar_h/2:.1f}" stroke="#e2e8f0"/>')
            inner.append(f'<rect x="{margin_left}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="8" fill="{_color(idx)}"/>')
            inner.append(_svg_text(margin_left - 12, y + bar_h / 2 + 5, label[:40], size=13, anchor="end"))
            inner.append(_svg_text(margin_left + bar_w + 8, y + bar_h / 2 + 5, f"{value:g}{unit}", size=12, fill="#334155"))
    else:
        bar_w = max(18.0, min(70.0, plot_w / max(1, len(rows)) - 18.0))
        gap = max(8.0, (plot_w - (bar_w * len(rows))) / max(1, len(rows)))
        inner.append(f'<line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{margin_left + plot_w}" y2="{margin_top + plot_h}" stroke="#94a3b8"/>')
        for idx, row in enumerate(rows):
            value = float(row.get(value_key, 0) or 0)
            label = str(row.get(label_key, ""))
            x = margin_left + idx * (bar_w + gap)
            bar_h = (value / max_value) * plot_h
            y = margin_top + plot_h - bar_h
            inner.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="8" fill="{_color(idx)}"/>')
            inner.append(_svg_text(x + bar_w / 2, y - 8, f"{value:g}{unit}", size=12, anchor="middle", fill="#334155"))
            inner.append(_svg_text(x + bar_w / 2, margin_top + plot_h + 20, label[:16], size=12, anchor="middle", fill="#334155"))

    return _svg_wrap(width, height, inner)


def _grouped_bar_chart_svg(
    *,
    title: str,
    labels: list[str],
    series: list[tuple[str, list[float]]],
    width: int = 960,
    height: int = 560,
) -> str:
    inner = [
        _svg_text(32, 36, title, size=24, weight="700"),
    ]
    if not labels or not series:
        inner.append(_svg_text(width / 2, height / 2, "no data", size=20, anchor="middle", fill="#64748b"))
        return _svg_wrap(width, height, inner)

    margin_left = 80
    margin_right = 50
    margin_top = 80
    margin_bottom = 130
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    max_value = max(max(values) if values else 0 for _, values in series)
    max_value = max(float(max_value), 1.0)

    group_w = plot_w / max(1, len(labels))
    bar_w = max(16.0, min(36.0, group_w / max(1, len(series)) - 6.0))

    inner.append(f'<line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{margin_left + plot_w}" y2="{margin_top + plot_h}" stroke="#94a3b8"/>')
    for idx, label in enumerate(labels):
        gx = margin_left + idx * group_w
        for s_idx, (_, values) in enumerate(series):
            value = float(values[idx]) if idx < len(values) else 0.0
            x = gx + 10 + s_idx * (bar_w + 4)
            bar_h = (value / max_value) * plot_h
            y = margin_top + plot_h - bar_h
            inner.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="6" fill="{_color(s_idx)}"/>')
        inner.append(_svg_text(gx + group_w / 2, margin_top + plot_h + 20, label[:18], size=12, anchor="middle", fill="#334155"))

    for s_idx, (name, _) in enumerate(series):
        lx = margin_left + s_idx * 180
        inner.append(f'<rect x="{lx}" y="{height - 40}" width="18" height="18" rx="4" fill="{_color(s_idx)}"/>')
        inner.append(_svg_text(lx + 26, height - 26, name, size=13, fill="#334155"))

    return _svg_wrap(width, height, inner)


def _line_chart_svg(
    *,
    title: str,
    points: list[tuple[float, float]],
    width: int = 960,
    height: int = 560,
    x_unit: str = "s",
) -> str:
    inner = [
        _svg_text(32, 36, title, size=24, weight="700"),
    ]
    if not points:
        inner.append(_svg_text(width / 2, height / 2, "no data", size=20, anchor="middle", fill="#64748b"))
        return _svg_wrap(width, height, inner)

    margin_left = 80
    margin_right = 40
    margin_top = 80
    margin_bottom = 80
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    max_x = max(point[0] for point in points) or 1.0
    max_y = max(point[1] for point in points) or 1.0
    max_x = max(max_x, 1.0)
    max_y = max(max_y, 1.0)

    inner.append(f'<line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{margin_left + plot_w}" y2="{margin_top + plot_h}" stroke="#94a3b8"/>')
    inner.append(f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_h}" stroke="#94a3b8"/>')

    poly_points: list[str] = []
    for x_value, y_value in points:
        x = margin_left + (x_value / max_x) * plot_w
        y = margin_top + plot_h - (y_value / max_y) * plot_h
        poly_points.append(f"{x:.1f},{y:.1f}")
        inner.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="#2563eb"/>')
    inner.append(f'<polyline fill="none" stroke="#2563eb" stroke-width="3" points="{" ".join(poly_points)}"/>')
    inner.append(_svg_text(margin_left + plot_w, margin_top + plot_h + 28, f"time ({x_unit})", size=13, anchor="end", fill="#475569"))
    inner.append(_svg_text(26, margin_top + 10, "count", size=13, fill="#475569"))

    return _svg_wrap(width, height, inner)


def _timeline_svg(title: str, events: list[dict[str, Any]], width: int = 1080, height: int = 520) -> str:
    inner = [_svg_text(32, 36, title, size=24, weight="700")]
    if not events:
        inner.append(_svg_text(width / 2, height / 2, "no data", size=20, anchor="middle", fill="#64748b"))
        return _svg_wrap(width, height, inner)

    categories = ["monitor", "selection", "action", "correct"]
    y_map = {name: 100 + idx * 90 for idx, name in enumerate(categories)}
    margin_left = 120
    margin_right = 40
    plot_w = width - margin_left - margin_right
    max_x = max(float(event.get("relative_seconds", 0) or 0) for event in events)
    max_x = max(max_x, 1.0)

    for name, y in y_map.items():
        inner.append(f'<line x1="{margin_left}" y1="{y}" x2="{margin_left + plot_w}" y2="{y}" stroke="#cbd5e1"/>')
        inner.append(_svg_text(margin_left - 16, y + 5, name, size=13, anchor="end", fill="#334155"))

    for index, event in enumerate(events[:120]):
        x = margin_left + (float(event.get("relative_seconds", 0) or 0) / max_x) * plot_w
        category = str(event.get("category", "monitor"))
        y = y_map.get(category, y_map["monitor"])
        color = {
            "monitor": "#2563eb",
            "selection": "#7c3aed",
            "action": "#ea580c",
            "correct": "#16a34a",
        }.get(category, _color(index))
        label = str(event.get("label", ""))[:36]
        inner.append(f'<circle cx="{x:.1f}" cy="{y}" r="6" fill="{color}"/>')
        inner.append(_svg_text(x, y - 10, label, size=10, anchor="middle", fill="#475569"))

    inner.append(_svg_text(margin_left + plot_w, height - 24, "relative seconds", size=13, anchor="end", fill="#475569"))
    return _svg_wrap(width, height, inner)


def _dashboard_svg(metrics: dict[str, Any], width: int = 1080, height: int = 420) -> str:
    cards = [
        ("Correct Flags", str(metrics.get("correct_flag_submissions", 0))),
        ("Processed Triggers", str(metrics.get("processed_triggers", 0))),
        ("Elapsed", str(metrics.get("elapsed_seconds", 0)) + "s"),
        ("Trigger Efficiency", _format_float(float(metrics.get("trigger_efficiency", 0) or 0))),
        ("Unique Observed Flags", str(metrics.get("unique_flags_observed", 0))),
        ("Unique Submitted Flags", str(metrics.get("unique_flags_submitted", 0))),
        ("LLM Calls", str(metrics.get("llm_calls", 0))),
        ("Total Tokens", str(metrics.get("total_tokens", 0))),
    ]
    inner = [
        _svg_text(32, 36, f"Experiment Dashboard: {metrics.get('experiment_id', '-')}", size=24, weight="700"),
        _svg_text(32, 60, f"technique={metrics.get('technique', TECHNIQUE)} | stop_reason={metrics.get('stop_reason', '-')}", size=13, fill="#475569"),
    ]
    x_positions = [32, 288, 544, 800]
    y_positions = [90, 220]
    card_w = 220
    card_h = 100
    idx = 0
    for y in y_positions:
        for x in x_positions:
            if idx >= len(cards):
                break
            title, value = cards[idx]
            inner.append(f'<rect x="{x}" y="{y}" width="{card_w}" height="{card_h}" rx="18" fill="white" stroke="#cbd5e1"/>')
            inner.append(_svg_text(x + 18, y + 30, title, size=13, fill="#64748b"))
            inner.append(_svg_text(x + 18, y + 68, value, size=30, weight="700", fill="#0f172a"))
            idx += 1
    return _svg_wrap(width, height, inner)


def _html_table(rows: list[dict[str, Any]], fieldnames: list[str], max_rows: int = 12) -> str:
    if not rows:
        return "<p class='muted'>no rows</p>"
    head = "".join(f"<th>{escape(name)}</th>" for name in fieldnames)
    body_rows = []
    for row in rows[:max_rows]:
        cols = "".join(f"<td>{escape(str(row.get(name, '')))}</td>" for name in fieldnames)
        body_rows.append(f"<tr>{cols}</tr>")
    body = "".join(body_rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _write_report_html(path: Path, summary: dict[str, Any], csv_previews: dict[str, tuple[list[dict[str, Any]], list[str]]]) -> None:
    images = [
        "summary_dashboard.svg",
        "cumulative_correct_flags.svg",
        "observed_vs_selected_signals.svg",
        "action_usage.svg",
        "endpoint_usage.svg",
        "skill_usage.svg",
        "llm_token_usage.svg",
        "event_timeline.svg",
        "communication_pairs.svg",
    ]
    image_blocks = "".join(
        f"<section class='card'><h2>{escape(image)}</h2><img src='{escape(image)}' alt='{escape(image)}'></section>"
        for image in images
        if (path.parent / image).exists()
    )
    table_blocks = "".join(
        f"<section class='card'><h2>{escape(name)}</h2>{_html_table(rows, fields)}</section>"
        for name, (rows, fields) in csv_previews.items()
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Experiment Report {escape(str(summary.get('experiment_id', '-')))}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; background: #f8fafc; color: #0f172a; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
    .hero {{ background: white; border: 1px solid #cbd5e1; border-radius: 18px; padding: 24px; margin-bottom: 24px; }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 20px; }}
    .card {{ background: white; border: 1px solid #cbd5e1; border-radius: 18px; padding: 20px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #e2e8f0; padding: 8px 10px; text-align: left; }}
    th {{ background: #f1f5f9; }}
    img {{ width: 100%; border: 1px solid #e2e8f0; border-radius: 12px; background: #fff; }}
    .muted {{ color: #64748b; }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>Experiment Report: {escape(str(summary.get("experiment_id", "-")))}</h1>
      <p class="muted">technique={escape(str(summary.get("technique", TECHNIQUE)))} | stop_reason={escape(str(summary.get("stop_reason", "-")))} | elapsed={escape(str(summary.get("elapsed_seconds", 0)))}s</p>
    </section>
    <div class="grid">
      {image_blocks}
      {table_blocks}
    </div>
  </main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def _parse_history_entries(history_path: Path) -> list[dict[str, Any]]:
    if not history_path.exists():
        return []
    text = history_path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for match in HISTORY_ENTRY_RE.finditer(text):
        rows.append(
            {
                "time": match.group("time"),
                "title": match.group("title").strip(),
                "content": match.group("content").strip(),
            }
        )
    return rows


def _relative_seconds(ts: datetime | None, base: datetime | None) -> float:
    if ts is None or base is None:
        return 0.0
    return max(0.0, (ts - base).total_seconds())


def _collect_experiment_data(experiment_id: str) -> dict[str, Any]:
    experiment_dir = get_experiment_dir(experiment_id)
    memory_dir = get_memory_dir(experiment_id)
    runtime_dir = get_runtime_dir(experiment_id)

    metadata = _safe_read_json(get_metadata_path(experiment_id), {})
    monitor_state = _safe_read_json(runtime_dir / "monitor_state.json", {})
    monitor_signal = _safe_read_json(runtime_dir / "monitor_signal.json", {})
    submitted_flags = _safe_read_json(runtime_dir / "submitted_flags.json", [])
    monitor_events = _safe_read_jsonl(runtime_dir / "monitor.jsonl")
    llm_trace = _safe_read_jsonl(runtime_dir / "llm_trace.jsonl")
    action_trace = _safe_read_jsonl(runtime_dir / "action_trace.jsonl")
    skill_trace = _safe_read_jsonl(runtime_dir / "skill_trace.jsonl")
    history_entries = _parse_history_entries(memory_dir / "history.log")

    history_times = [_parse_dt(item["time"]) for item in history_entries]
    monitor_times = [_parse_dt(item.get("time", "")) for item in monitor_events]
    valid_times = [item for item in history_times + monitor_times if item is not None]
    base_time = min(valid_times) if valid_times else None

    observed_rows: list[dict[str, Any]] = []
    for raw in monitor_state.get("seen_event_keys", []):
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            observed_rows.append(payload)

    observed_type_counts = Counter(str(item.get("type", "unknown")) for item in observed_rows)
    endpoint_counts = Counter(str(item.get("uri", "") or "-") for item in observed_rows)
    pair_counts = Counter(f"{item.get('src', '-') } -> { item.get('dst', '-')}" for item in observed_rows)

    selected_rows: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    timeline_rows: list[dict[str, Any]] = []
    correct_timeline: list[tuple[float, float]] = []
    correct_seen = 0.0
    execution_action_counts = Counter()
    tool_counts = Counter()

    for entry in history_entries:
        entry_dt = _parse_dt(entry["time"])
        rel = _relative_seconds(entry_dt, base_time)
        title = entry["title"]
        content = entry["content"]

        if title == "MONITOR SIGNAL SELECT":
            try:
                payload = json.loads(content)
            except Exception:
                payload = {}
            selected_rows.append(
                {
                    "time": entry["time"],
                    "relative_seconds": int(rel),
                    "event_type": payload.get("event_type", ""),
                    "summary": payload.get("summary", ""),
                    "reason": payload.get("reason", ""),
                    "event_key": payload.get("event_key", ""),
                }
            )
            timeline_rows.append(
                {
                    "time": entry["time"],
                    "relative_seconds": int(rel),
                    "category": "selection",
                    "label": str(payload.get("event_type", "select")),
                    "detail": payload.get("summary", ""),
                }
            )
            continue

        if title == "MONITOR SIGNAL SELECT FALLBACK":
            try:
                payload = json.loads(content)
            except Exception:
                payload = {}
            fallback_rows.append(
                {
                    "time": entry["time"],
                    "relative_seconds": int(rel),
                    "event_type": payload.get("fallback_event_type", ""),
                    "summary": payload.get("fallback_summary", ""),
                    "reason": payload.get("reason", ""),
                    "event_key": payload.get("fallback_event_key", ""),
                }
            )
            timeline_rows.append(
                {
                    "time": entry["time"],
                    "relative_seconds": int(rel),
                    "category": "selection",
                    "label": f"fallback:{payload.get('fallback_event_type', '-')}",
                    "detail": payload.get("fallback_summary", ""),
                }
            )
            continue

        if title == "EXECUTION RESULT":
            sections = _section_map(content)
            action_name = sections.get("action", "").strip()
            tool_name = sections.get("tool_name", "").strip()
            if action_name:
                execution_action_counts[action_name] += 1
            if tool_name:
                tool_counts[tool_name] += 1
            timeline_rows.append(
                {
                    "time": entry["time"],
                    "relative_seconds": int(rel),
                    "category": "action",
                    "label": tool_name or action_name or "execution",
                    "detail": sections.get("response", "")[:200],
                }
            )
            continue

        if title == "TRIGGER ACTION RESULT":
            sections = _section_map(content)
            action_name = sections.get("action", "").strip()
            flag = sections.get("flag", "").strip()
            status = sections.get("status", "").strip()
            response_blob = sections.get("response", "")
            result_label = ""
            if response_blob:
                try:
                    response_payload = json.loads(response_blob)
                    result_label = str(response_payload.get("result", response_payload.get("status", ""))).strip()
                except Exception:
                    result_label = ""
            execution_action_counts[action_name or "trigger_action"] += 1
            if action_name == "submit_flag" and (result_label == "correct" or status == "correct"):
                correct_seen += 1
                correct_timeline.append((rel, correct_seen))
                timeline_rows.append(
                    {
                        "time": entry["time"],
                        "relative_seconds": int(rel),
                        "category": "correct",
                        "label": flag or "correct_flag",
                        "detail": result_label or status,
                    }
                )
            else:
                timeline_rows.append(
                    {
                        "time": entry["time"],
                        "relative_seconds": int(rel),
                        "category": "action",
                        "label": action_name or "trigger_action",
                        "detail": result_label or status,
                    }
                )

    if action_trace:
        execution_action_counts = Counter()
        tool_counts = Counter()
        correct_timeline = []
        correct_seen = 0.0
        for item in action_trace:
            execution_action_counts[str(item.get("action_type", "unknown"))] += 1
            if item.get("action_type") == "tool":
                tool_counts[str(item.get("tool_name", "unknown"))] += 1
            dt = _parse_dt(str(item.get("time", "")))
            rel = _relative_seconds(dt, base_time)
            timeline_rows.append(
                {
                    "time": item.get("time", ""),
                    "relative_seconds": int(rel),
                    "category": "correct" if item.get("action_type") == "submit_flag" and item.get("status") == "correct" else "action",
                    "label": str(item.get("tool_name") or item.get("action_type", "action")),
                    "detail": str(item.get("status", "")),
                }
            )
            if item.get("action_type") == "submit_flag" and item.get("status") == "correct":
                correct_seen += 1
                correct_timeline.append((rel, correct_seen))

    selected_type_counts = Counter(row["event_type"] or "unknown" for row in selected_rows)
    if fallback_rows:
        for row in fallback_rows:
            selected_type_counts[f"fallback:{row['event_type'] or 'unknown'}"] += 1

    llm_role_counts = Counter(str(item.get("role", "unknown")) for item in llm_trace)
    llm_prompt_tokens = sum(int(item.get("usage", {}).get("prompt_tokens", 0) or 0) for item in llm_trace)
    llm_completion_tokens = sum(int(item.get("usage", {}).get("completion_tokens", 0) or 0) for item in llm_trace)
    llm_total_tokens = sum(int(item.get("usage", {}).get("total_tokens", 0) or 0) for item in llm_trace)
    llm_system_chars = sum(int(item.get("system_chars", 0) or 0) for item in llm_trace)
    llm_user_chars = sum(int(item.get("user_chars", 0) or 0) for item in llm_trace)
    llm_output_chars = sum(int(item.get("output_chars", 0) or 0) for item in llm_trace)
    skill_counts = Counter(str(item.get("skill", "unknown")) for item in skill_trace)

    if not skill_counts:
        for skill_name in ALLOWED_SKILLS:
            skill_counts[skill_name] += 0

    observed_flags = list(monitor_state.get("seen_flags", []))
    submitted_flag_list = [str(item).strip() for item in submitted_flags if str(item).strip()]
    pending_signal_count = len(monitor_signal.get("pending", [])) if isinstance(monitor_signal.get("pending", []), list) else 0

    flag_lifecycle: dict[str, dict[str, Any]] = {}
    for event in monitor_events:
        event_dt = _parse_dt(str(event.get("time", "")))
        rel = _relative_seconds(event_dt, base_time)
        event_type = str(event.get("type", ""))
        uri = str(event.get("uri", ""))
        if event_type in {"flag_observed", "session_cookie_observed", "session_cookie_used", "bettercap_start", "tshark_live_start"}:
            timeline_rows.append(
                {
                    "time": event.get("time", ""),
                    "relative_seconds": int(rel),
                    "category": "monitor",
                    "label": event_type,
                    "detail": event.get("summary", uri),
                }
            )
        for flag in event.get("flags", []) or []:
            record = flag_lifecycle.setdefault(
                flag,
                {
                    "flag": flag,
                    "first_observed_time": event.get("time", ""),
                    "first_observed_relative_seconds": int(rel),
                    "first_observed_uri": uri,
                    "first_submitted_time": "",
                    "first_submitted_relative_seconds": "",
                    "submit_status": "",
                },
            )
            if not record["first_observed_time"]:
                record["first_observed_time"] = event.get("time", "")
                record["first_observed_relative_seconds"] = int(rel)
                record["first_observed_uri"] = uri

    if action_trace:
        for item in action_trace:
            if item.get("action_type") != "submit_flag":
                continue
            flag = str(item.get("flag", "")).strip()
            if not flag:
                continue
            dt = _parse_dt(str(item.get("time", "")))
            rel = _relative_seconds(dt, base_time)
            record = flag_lifecycle.setdefault(
                flag,
                {
                    "flag": flag,
                    "first_observed_time": "",
                    "first_observed_relative_seconds": "",
                    "first_observed_uri": "",
                    "first_submitted_time": "",
                    "first_submitted_relative_seconds": "",
                    "submit_status": "",
                },
            )
            if not record["first_submitted_time"]:
                record["first_submitted_time"] = item.get("time", "")
                record["first_submitted_relative_seconds"] = int(rel)
                record["submit_status"] = item.get("status", "")

    processed_triggers = int(metadata.get("processed_triggers", 0) or 0)
    correct_flag_submissions = int(metadata.get("correct_flag_submissions", 0) or 0)
    trigger_efficiency = (correct_flag_submissions / processed_triggers) if processed_triggers else 0.0
    unique_flags_observed = len(observed_flags)
    unique_flags_submitted = len(submitted_flag_list)
    llm_calls = len(llm_trace)

    summary = {
        "experiment_id": experiment_id,
        "agent_id": metadata.get("agent_id", AGENT_ID),
        "technique": metadata.get("technique", TECHNIQUE),
        "duration_seconds": int(metadata.get("duration_seconds", 0) or 0),
        "elapsed_seconds": int(metadata.get("elapsed_seconds", 0) or 0),
        "processed_triggers": processed_triggers,
        "correct_flag_submissions": correct_flag_submissions,
        "incorrect_flag_submissions": int(metadata.get("incorrect_flag_submissions", 0) or 0),
        "duplicate_flag_submissions": int(metadata.get("duplicate_flag_submissions", 0) or 0),
        "invalid_flag_submissions": int(metadata.get("invalid_flag_submissions", 0) or 0),
        "submit_flag_attempts": int(metadata.get("submit_flag_attempts", 0) or 0),
        "submit_flag_errors": int(metadata.get("submit_flag_errors", 0) or 0),
        "unique_flags_observed": unique_flags_observed,
        "unique_flags_submitted": unique_flags_submitted,
        "pending_signal_count": pending_signal_count,
        "trigger_efficiency": _format_float(trigger_efficiency),
        "observed_event_count": len(observed_rows),
        "monitor_log_event_count": len(monitor_events),
        "selected_signal_count": len(selected_rows),
        "fallback_selection_count": len(fallback_rows),
        "tool_attempt_count": 0,
        "tool_call_count": 0,
        "shell_call_count": execution_action_counts.get("shell", 0),
        "submit_action_count": execution_action_counts.get("submit_flag", 0),
        "llm_calls": llm_calls,
        "prompt_tokens": llm_prompt_tokens,
        "completion_tokens": llm_completion_tokens,
        "total_tokens": llm_total_tokens,
        "system_chars": llm_system_chars,
        "user_chars": llm_user_chars,
        "output_chars": llm_output_chars,
        "stop_reason": metadata.get("stop_reason", ""),
        "max_correct_flags_per_experiment": metadata.get("max_correct_flags_per_experiment", ""),
    }

    observed_type_rows = [
        {"event_type": event_type, "count": count}
        for event_type, count in observed_type_counts.most_common()
    ]
    selected_type_rows = [
        {"event_type": event_type, "count": count}
        for event_type, count in selected_type_counts.most_common()
    ]
    action_status_map: dict[str, dict[str, int]] = defaultdict(lambda: {"total_count": 0, "success_count": 0, "error_count": 0, "other_count": 0})
    tool_status_map: dict[str, dict[str, int]] = defaultdict(lambda: {"total_count": 0, "success_count": 0, "error_count": 0, "other_count": 0})
    for item in action_trace:
        action_type = str(item.get("action_type", "unknown"))
        status = str(item.get("status", ""))
        action_status_map[action_type]["total_count"] += 1
        if status in {"success", "correct", "ok"}:
            action_status_map[action_type]["success_count"] += 1
        elif status in {"error", "request_error"}:
            action_status_map[action_type]["error_count"] += 1
        else:
            action_status_map[action_type]["other_count"] += 1

        if action_type == "tool":
            tool_name = str(item.get("tool_name", "unknown"))
            tool_status_map[tool_name]["total_count"] += 1
            if status in {"success", "ok"}:
                tool_status_map[tool_name]["success_count"] += 1
            elif status in {"error", "request_error"}:
                tool_status_map[tool_name]["error_count"] += 1
            else:
                tool_status_map[tool_name]["other_count"] += 1

    if action_trace:
        action_rows = [
            {"action_type": action_type, **counts}
            for action_type, counts in sorted(
                action_status_map.items(),
                key=lambda item: (-item[1]["total_count"], item[0]),
            )
        ]
        tool_rows = [
            {"tool_name": tool_name, **counts}
            for tool_name, counts in sorted(
                tool_status_map.items(),
                key=lambda item: (-item[1]["total_count"], item[0]),
            )
        ]
    else:
        action_rows = [
            {
                "action_type": action_type,
                "total_count": count,
                "success_count": count,
                "error_count": 0,
                "other_count": 0,
            }
            for action_type, count in execution_action_counts.most_common()
        ]
        tool_rows = [
            {
                "tool_name": tool_name,
                "total_count": count,
                "success_count": count,
                "error_count": 0,
                "other_count": 0,
            }
            for tool_name, count in tool_counts.most_common()
        ]
    endpoint_rows = [
        {"uri": uri, "count": count}
        for uri, count in endpoint_counts.most_common(REPORT_TOP_N)
    ]
    pair_rows = [
        {"pair": pair, "count": count}
        for pair, count in pair_counts.most_common(REPORT_TOP_N)
    ]
    llm_role_rows = [
        {
            "role": role,
            "count": count,
        }
        for role, count in llm_role_counts.most_common()
    ]
    llm_usage_rows = [
        {
            "metric": "prompt_tokens",
            "value": llm_prompt_tokens,
        },
        {
            "metric": "completion_tokens",
            "value": llm_completion_tokens,
        },
        {
            "metric": "total_tokens",
            "value": llm_total_tokens,
        },
        {
            "metric": "system_chars",
            "value": llm_system_chars,
        },
        {
            "metric": "user_chars",
            "value": llm_user_chars,
        },
        {
            "metric": "output_chars",
            "value": llm_output_chars,
        },
    ]
    skill_rows = [
        {
            "skill": skill,
            "activation_count": count,
        }
        for skill, count in skill_counts.most_common()
    ]
    configured_skill_rows = [{"skill": skill} for skill in ALLOWED_SKILLS]
    configured_tool_rows = [{"tool": tool} for tool in ALLOWED_TOOLS]
    flag_rows = list(flag_lifecycle.values())
    flag_rows.sort(key=lambda item: str(item.get("first_observed_relative_seconds", "")))
    timeline_rows.sort(key=lambda item: (int(item.get("relative_seconds", 0) or 0), item.get("category", "")))

    summary["tool_attempt_count"] = sum(row["total_count"] for row in tool_rows)
    summary["tool_call_count"] = sum(row["success_count"] for row in tool_rows)

    return {
        "summary": summary,
        "summary_row": [summary],
        "observed_type_rows": observed_type_rows,
        "selected_type_rows": selected_type_rows,
        "action_rows": action_rows,
        "tool_rows": tool_rows,
        "endpoint_rows": endpoint_rows,
        "pair_rows": pair_rows,
        "flag_rows": flag_rows,
        "timeline_rows": timeline_rows,
        "correct_timeline": correct_timeline,
        "llm_role_rows": llm_role_rows,
        "llm_usage_rows": llm_usage_rows,
        "skill_rows": skill_rows,
        "configured_skill_rows": configured_skill_rows,
        "configured_tool_rows": configured_tool_rows,
    }


def generate_experiment_report(experiment_id: str) -> dict[str, Any]:
    analysis_dir = get_analysis_dir(experiment_id)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    data = _collect_experiment_data(experiment_id)
    summary = data["summary"]

    csv_specs = {
        "summary_metrics.csv": (data["summary_row"], list(data["summary_row"][0].keys()) if data["summary_row"] else []),
        "observed_event_types.csv": (data["observed_type_rows"], ["event_type", "count"]),
        "selected_signal_types.csv": (data["selected_type_rows"], ["event_type", "count"]),
        "action_usage.csv": (data["action_rows"], ["action_type", "total_count", "success_count", "error_count", "other_count"]),
        "tool_usage.csv": (data["tool_rows"], ["tool_name", "total_count", "success_count", "error_count", "other_count"]),
        "endpoint_usage.csv": (data["endpoint_rows"], ["uri", "count"]),
        "communication_pairs.csv": (data["pair_rows"], ["pair", "count"]),
        "flag_lifecycle.csv": (
            data["flag_rows"],
            [
                "flag",
                "first_observed_time",
                "first_observed_relative_seconds",
                "first_observed_uri",
                "first_submitted_time",
                "first_submitted_relative_seconds",
                "submit_status",
            ],
        ),
        "timeline_events.csv": (data["timeline_rows"], ["time", "relative_seconds", "category", "label", "detail"]),
        "llm_role_usage.csv": (data["llm_role_rows"], ["role", "count"]),
        "llm_usage.csv": (data["llm_usage_rows"], ["metric", "value"]),
        "skill_usage.csv": (data["skill_rows"], ["skill", "activation_count"]),
        "configured_skills.csv": (data["configured_skill_rows"], ["skill"]),
        "configured_tools.csv": (data["configured_tool_rows"], ["tool"]),
    }

    for filename, (rows, fieldnames) in csv_specs.items():
        _write_csv(analysis_dir / filename, rows, fieldnames)

    _write_json(analysis_dir / "report_summary.json", summary)

    _save_summary_dashboard_png(analysis_dir / "summary_dashboard.png", summary)
    _save_line_chart_png(
        analysis_dir / "cumulative_correct_flags.png",
        title="Cumulative Correct Flags Over Time",
        points=data["correct_timeline"],
    )
    observed_map = {row["event_type"]: float(row["count"]) for row in data["observed_type_rows"]}
    selected_map = {row["event_type"]: float(row["count"]) for row in data["selected_type_rows"]}
    signal_labels = list(dict.fromkeys([*observed_map.keys(), *selected_map.keys()]))
    _save_grouped_bar_chart_png(
        analysis_dir / "observed_vs_selected_signals.png",
        title="Observed vs Selected Signal Types",
        labels=signal_labels,
        series=[
            ("observed", [observed_map.get(label, 0.0) for label in signal_labels]),
            ("selected", [selected_map.get(label, 0.0) for label in signal_labels]),
        ],
    )
    _save_bar_chart_png(
        analysis_dir / "action_usage.png",
        title="Action Usage",
        rows=data["action_rows"],
        label_key="action_type",
        value_key="success_count",
    )
    _save_bar_chart_png(
        analysis_dir / "endpoint_usage.png",
        title="Endpoint Usage",
        rows=data["endpoint_rows"],
        label_key="uri",
        value_key="count",
        horizontal=True,
    )
    _save_bar_chart_png(
        analysis_dir / "skill_usage.png",
        title="Skill Activation Counts",
        rows=data["skill_rows"],
        label_key="skill",
        value_key="activation_count",
        horizontal=True,
    )
    _save_bar_chart_png(
        analysis_dir / "llm_token_usage.png",
        title="LLM Usage and Character Volume",
        rows=data["llm_usage_rows"],
        label_key="metric",
        value_key="value",
    )
    _save_scatter_timeline_png(
        analysis_dir / "event_timeline.png",
        title="Experiment Event Timeline",
        events=data["timeline_rows"],
    )
    _save_bar_chart_png(
        analysis_dir / "communication_pairs.png",
        title="Communication Pair Counts",
        rows=data["pair_rows"],
        label_key="pair",
        value_key="count",
        horizontal=True,
    )

    return {
        "experiment_id": experiment_id,
        "analysis_dir": str(analysis_dir),
        "summary_metrics_csv": str(analysis_dir / "summary_metrics.csv"),
        "image_count": 8,
        "csv_count": len(csv_specs),
        "json_count": 1,
    }


def generate_comparison_report(
    experiment_ids: list[str],
    label_map: dict[str, str] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    labels = label_map or {}
    if not experiment_ids:
        raise ValueError("at least one experiment id is required")

    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_dir = get_comparison_reports_dir() / stamp
    else:
        target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    comparison_rows: list[dict[str, Any]] = []
    for experiment_id in experiment_ids:
        report_path = get_analysis_dir(experiment_id) / "report_summary.json"
        if not report_path.exists():
            generate_experiment_report(experiment_id)
        summary = _safe_read_json(report_path, {})
        if not summary:
            continue
        row = dict(summary)
        row["label"] = labels.get(experiment_id, experiment_id)
        comparison_rows.append(row)

    comparison_rows.sort(key=lambda item: str(item.get("label", item.get("experiment_id", ""))))
    if not comparison_rows:
        raise ValueError("no experiment summaries were available")

    fields = [
        "label",
        "experiment_id",
        "technique",
        "elapsed_seconds",
        "processed_triggers",
        "correct_flag_submissions",
        "incorrect_flag_submissions",
        "duplicate_flag_submissions",
        "submit_flag_attempts",
        "unique_flags_observed",
        "unique_flags_submitted",
        "trigger_efficiency",
        "selected_signal_count",
        "tool_attempt_count",
        "tool_call_count",
        "llm_calls",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "stop_reason",
    ]
    _write_csv(target_dir / "comparison_metrics.csv", comparison_rows, fields)
    _write_csv(
        target_dir / "label_mapping.csv",
        [{"experiment_id": exp_id, "label": labels.get(exp_id, exp_id)} for exp_id in experiment_ids],
        ["experiment_id", "label"],
    )
    _write_json(target_dir / "comparison_metrics.json", comparison_rows)

    chart_specs = [
        ("compare_correct_flags.png", "Correct Flag Submissions", "correct_flag_submissions"),
        ("compare_elapsed_seconds.png", "Elapsed Seconds", "elapsed_seconds"),
        ("compare_processed_triggers.png", "Processed Triggers", "processed_triggers"),
        ("compare_trigger_efficiency.png", "Trigger Efficiency", "trigger_efficiency"),
        ("compare_total_tokens.png", "Total Tokens", "total_tokens"),
        ("compare_tool_calls.png", "Tool Calls", "tool_call_count"),
    ]

    for filename, title, field in chart_specs:
        rows = [
            {"label": row.get("label", row.get("experiment_id", "")), "value": float(row.get(field, 0) or 0)}
            for row in comparison_rows
        ]
        _save_bar_chart_png(
            target_dir / filename,
            title=title,
            rows=rows,
            label_key="label",
            value_key="value",
        )

    return {
        "output_dir": str(target_dir),
        "comparison_count": len(comparison_rows),
        "comparison_csv": str(target_dir / "comparison_metrics.csv"),
        "image_count": len(chart_specs),
    }
