#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime


FLAG_PATTERN = re.compile(r"flag\{[^}\r\n<>\s]*\}", re.IGNORECASE)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def decode_http_file_data(value: str) -> str:
    """
    tshark -T fields -e http.file_data 는 보통 hex string으로 출력된다.
    예: 3c68746d6c3e... -> <html>...
    hex가 아니면 원문 그대로 반환한다.
    """
    value = value.strip()

    if not value:
        return ""

    # tshark가 여러 필드를 comma로 붙여주는 경우가 있어 분리해서 처리
    chunks = re.split(r"[,;\s]+", value)
    decoded_parts: list[str] = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        # hex 문자열이면 decode
        if re.fullmatch(r"[0-9a-fA-F]+", chunk) and len(chunk) % 2 == 0:
            try:
                decoded_parts.append(bytes.fromhex(chunk).decode("utf-8", errors="ignore"))
                continue
            except Exception:
                pass

        decoded_parts.append(chunk)

    return "\n".join(decoded_parts)


def build_tshark_command(interface: str, host: str, port: str) -> list[str]:
    capture_filter = f"host {host} and port {port}"

    return [
        "sudo",
        "tshark",
        "-l",                 # line-buffered output
        "-i",
        interface,
        "-f",
        capture_filter,
        "-Y",
        "http",
        "-T",
        "fields",
        "-e",
        "http.request.uri",
        "-e",
        "http.file_data",
    ]


def monitor_flags(interface: str, host: str, port: str, stop_after_first: bool) -> int:
    if shutil.which("tshark") is None:
        print("[ERROR] tshark가 설치되어 있지 않습니다. 먼저 설치하세요: sudo apt install -y tshark")
        return 1

    command = build_tshark_command(interface, host, port)

    print(f"[{now()}] Starting flag monitor")
    print(f"[target] interface={interface}, host={host}, port={port}")
    print("[command]")
    print(" ".join(command))
    print()

    proc: subprocess.Popen[str] | None = None
    seen_flags: set[str] = set()

    def stop_process(signum=None, frame=None) -> None:
        if proc is not None and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    signal.signal(signal.SIGINT, stop_process)
    signal.signal(signal.SIGTERM, stop_process)

    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        if proc.stdout is None:
            print("[ERROR] tshark stdout을 읽을 수 없습니다.")
            return 1

        # stderr는 tshark 경고 출력용. 너무 막히지 않도록 별도 출력하지 않고 병합 대신 PIPE만 사용.
        print(f"[{now()}] Monitoring started. HTTP body에서 flag{{...}}를 찾는 중입니다.")
        print()

        for line in proc.stdout:
            raw = line.strip()
            if not raw:
                continue

            # tshark fields 출력: request_uri<TAB>http_file_data
            parts = raw.split("\t")
            request_uri = parts[0].strip() if len(parts) >= 1 else ""
            file_data = parts[1].strip() if len(parts) >= 2 else ""

            decoded_body = decode_http_file_data(file_data)

            search_text = raw + "\n" + request_uri + "\n" + decoded_body
            matches = FLAG_PATTERN.findall(search_text)

            for flag in matches:
                if flag in seen_flags:
                    continue

                seen_flags.add(flag)

                print("=" * 80)
                print(f"[{now()}] FLAG DETECTED")
                print(f"[flag] {flag}")
                if request_uri:
                    print(f"[request_uri] {request_uri}")
                print("=" * 80)
                print(flush=True)

                if stop_after_first:
                    stop_process()
                    return 0

        returncode = proc.wait()
        print(f"[{now()}] tshark exited with code {returncode}")
        return returncode

    except KeyboardInterrupt:
        stop_process()
        print(f"\n[{now()}] Stopped by user")
        return 0

    finally:
        stop_process()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Monitor HTTP traffic with tshark and print flag{...} when detected."
    )
    parser.add_argument("-i", "--interface", default="eth0", help="network interface, default: eth0")
    parser.add_argument("--host", default="192.168.0.17", help="target host, default: 192.168.0.17")
    parser.add_argument("--port", default="7587", help="target port, default: 7587")
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="do not stop after first flag; keep monitoring",
    )

    args = parser.parse_args()

    return monitor_flags(
        interface=args.interface,
        host=args.host,
        port=str(args.port),
        stop_after_first=not args.keep_running,
    )


if __name__ == "__main__":
    raise SystemExit(main())
