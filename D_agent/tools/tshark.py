from __future__ import annotations

import subprocess
from pathlib import Path


def analyze_pcap(file_path: str, display_filter: str = "http") -> dict:
    """Analyze an existing pcap file with tshark."""
    path = Path(file_path).expanduser()
    if not path.exists():
        raise RuntimeError(f"pcap file not found: {path}")

    command = ["tshark", "-r", str(path), "-Y", display_filter]
    result = subprocess.run(command, capture_output=True, text=True)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def grep_in_traffic(file_path: str, keyword: str, display_filter: str = "http") -> dict:
    """Search decoded tshark output for a keyword."""
    analysis = analyze_pcap(file_path=file_path, display_filter=display_filter)
    keyword_lower = keyword.lower()
    matched_lines = [
        line
        for line in analysis.get("stdout", "").splitlines()
        if keyword_lower in line.lower()
    ]
    return {
        "keyword": keyword,
        "matches": matched_lines,
        "match_count": len(matched_lines),
        "analysis": analysis,
    }


def tshark_tool(file_path: str, display_filter: str = "http", keyword: str | None = None) -> dict:
    """Unified tshark entrypoint for analysis and keyword search."""
    if keyword:
        return grep_in_traffic(file_path=file_path, keyword=keyword, display_filter=display_filter)
    return analyze_pcap(file_path=file_path, display_filter=display_filter)
