from __future__ import annotations

import subprocess

from configs.config import TARGET_HOST, TARGET_PORT


def run_nmap_scan(host: str | None = None, ports: str | None = None, service_version: bool = True) -> dict:
    """Run a tightly scoped nmap scan against the configured target host and target port."""
    selected_host = (host or TARGET_HOST).strip()
    selected_ports = (ports or TARGET_PORT).strip()

    if selected_host != TARGET_HOST:
        raise RuntimeError("run_nmap_scan is restricted to the configured target host")
    if selected_ports != TARGET_PORT:
        raise RuntimeError("run_nmap_scan is restricted to the configured target port")

    command = ["nmap"]
    if service_version:
        command.append("-sV")
    command.extend(["-p", selected_ports, selected_host])

    result = subprocess.run(command, capture_output=True, text=True)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
