from __future__ import annotations

import json
import shutil
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
EXPERIMENTS_DIR = BASE_DIR / "experiments"

_active_experiment_id: str | None = None


def get_monitor_jsonl_path(experiment_id: str | None = None) -> Path:
    return get_runtime_file("monitor.jsonl", experiment_id)


def get_monitor_state_path(experiment_id: str | None = None) -> Path:
    return get_runtime_file("monitor_state.json", experiment_id)


def get_monitor_signal_path(experiment_id: str | None = None) -> Path:
    return get_runtime_file("monitor_signal.json", experiment_id)


def get_monitor_pcap_path(experiment_id: str | None = None) -> Path:
    return get_runtime_file("mitm_capture.pcap", experiment_id)


def get_submitted_flags_path(experiment_id: str | None = None) -> Path:
    return get_runtime_file("submitted_flags.json", experiment_id)


def get_metadata_path(experiment_id: str | None = None) -> Path:
    return get_experiment_dir(experiment_id) / "metadata.json"


def get_config_snapshot_path(experiment_id: str | None = None) -> Path:
    return get_experiment_dir(experiment_id) / "config_snapshot.py"


def get_analysis_dir(experiment_id: str | None = None) -> Path:
    return get_experiment_dir(experiment_id) / "analysis"


def get_reports_root() -> Path:
    return BASE_DIR / "reports"


def get_comparison_reports_dir() -> Path:
    return get_reports_root() / "comparisons"



def set_active_experiment(experiment_id: str) -> None:
    global _active_experiment_id
    _active_experiment_id = experiment_id


def get_active_experiment_id() -> str:
    if _active_experiment_id:
        return _active_experiment_id

    import os

    experiment_id = os.environ.get("D_AGENT_EXPERIMENT_ID", "").strip()
    if experiment_id:
        return experiment_id

    raise RuntimeError("active experiment is not initialized")


def get_experiment_dir(experiment_id: str | None = None) -> Path:
    target = experiment_id or get_active_experiment_id()
    return EXPERIMENTS_DIR / target


def get_memory_dir(experiment_id: str | None = None) -> Path:
    return get_experiment_dir(experiment_id) / "memory"


def get_runtime_dir(experiment_id: str | None = None) -> Path:
    return get_experiment_dir(experiment_id) / "runtime"


def get_runtime_file(name: str, experiment_id: str | None = None) -> Path:
    return get_runtime_dir(experiment_id) / name


def prepare_experiment_workspace(experiment: dict) -> Path:
    experiment_id = str(experiment["experiment_id"])
    set_active_experiment(experiment_id)

    experiment_dir = get_experiment_dir(experiment_id)
    memory_dir = get_memory_dir(experiment_id)
    runtime_dir = get_runtime_dir(experiment_id)
    memory_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = get_metadata_path(experiment_id)
    metadata_path.write_text(json.dumps(experiment, ensure_ascii=False, indent=2), encoding="utf-8")

    config_source = BASE_DIR / "configs" / "config.py"
    config_snapshot = get_config_snapshot_path(experiment_id)
    if config_source.exists():
        shutil.copyfile(config_source, config_snapshot)
    return experiment_dir


def update_experiment_metadata(updates: dict, experiment_id: str | None = None) -> None:
    metadata_path = get_metadata_path(experiment_id)
    payload: dict = {}
    if metadata_path.exists():
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}

    payload.update(updates)
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
