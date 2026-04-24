from __future__ import annotations

import requests

from configs.config import (
    AGENT_ID,
    AUTO_RESET_EXPERIMENT_ON_409,
    AUTO_SHUTDOWN_RELATED_SERVERS_ON_STOP,
    BC_SHARED_TOKEN,
    B_ADMIN_BASE_URL,
    C_SERVER_BASE_URL,
    EXPERIMENT_DURATION_SECONDS,
    REQUESTED_BY,
    START_TIMEOUT,
    SUBMIT_TIMEOUT,
    TECHNIQUE,
)


def _admin_headers() -> dict[str, str]:
    return {"X-BC-Token": BC_SHARED_TOKEN}


def _force_finish_active_experiment() -> dict:
    response = requests.post(
        f"{C_SERVER_BASE_URL}/api/admin/force-finish-experiment",
        headers=_admin_headers(),
        timeout=START_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def shutdown_related_servers(reason: str, experiment_id: str) -> dict[str, dict | str]:
    if not AUTO_SHUTDOWN_RELATED_SERVERS_ON_STOP:
        return {"status": "disabled"}

    payload = {
        "reason": reason,
        "experiment_id": experiment_id,
        "requested_by": AGENT_ID,
    }
    results: dict[str, dict | str] = {}

    for name, base_url in (
        ("B_Admin", B_ADMIN_BASE_URL),
        ("C_Server", C_SERVER_BASE_URL),
    ):
        try:
            response = requests.post(
                f"{base_url}/api/admin/shutdown-process",
                json=payload,
                headers=_admin_headers(),
                timeout=START_TIMEOUT,
            )
            response.raise_for_status()
            results[name] = response.json()
        except Exception as exc:  # noqa: BLE001
            results[name] = f"shutdown request failed: {exc}"

    return results


def start_experiment() -> dict:
    payload = {
        "agent_id": AGENT_ID,
        "technique": TECHNIQUE,
        "duration_seconds": EXPERIMENT_DURATION_SECONDS,
        "requested_by": REQUESTED_BY,
    }

    response = requests.post(
        f"{C_SERVER_BASE_URL}/api/lab/start",
        json=payload,
        timeout=START_TIMEOUT,
    )

    if response.status_code == 409 and AUTO_RESET_EXPERIMENT_ON_409:
        _force_finish_active_experiment()
        response = requests.post(
            f"{C_SERVER_BASE_URL}/api/lab/start",
            json=payload,
            timeout=START_TIMEOUT,
        )

    response.raise_for_status()
    data = response.json()
    if data.get("status") not in {"ok", "partial"}:
        raise RuntimeError(f"failed to start experiment: {data}")
    return data


def submit_flag(flag: str) -> dict:
    response = requests.post(
        f"{C_SERVER_BASE_URL}/api/agent/submit-flag",
        json={
            "flag": flag,
            "agent_id": AGENT_ID,
            "technique": TECHNIQUE,
        },
        timeout=SUBMIT_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()
