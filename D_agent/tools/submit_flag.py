from __future__ import annotations

from core.harness import submit_flag


def submit_flag_tool(flag: str) -> dict:
    """Submit a candidate flag to the configured C server."""
    clean_flag = flag.strip()
    if not clean_flag:
        raise RuntimeError("flag must not be empty")
    return submit_flag(clean_flag)
