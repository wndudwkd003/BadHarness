from __future__ import annotations

from core.harness import start_experiment


def start_experiment_tool() -> dict:
    """Start a new experiment on the configured C server and return its metadata."""
    return start_experiment()
