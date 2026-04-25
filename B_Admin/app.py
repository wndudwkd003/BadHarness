import json
import os
import random
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib import parse, request as urllib_request

from cryptography.fernet import Fernet
from flask import Flask, jsonify, render_template_string, request
import requests


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
EXPERIMENTS_DIR = BASE_DIR / "experiments"
EXPERIMENTS_DIR.mkdir(exist_ok=True)

DB_FILE = DATA_DIR / "admin.db"

app = Flask(__name__)

C_SERVER_BASE_URL = os.environ.get("C_SERVER_BASE_URL", "http://192.168.0.17:7587")
BC_SHARED_TOKEN = os.environ.get("BC_SHARED_TOKEN", "change-this-shared-token")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeThisAdminPassword123!")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))
AUTO_BOOTSTRAP = os.environ.get("AUTO_BOOTSTRAP", "1") == "1"
HOST = os.environ.get("B_ADMIN_HOST", "0.0.0.0")
PORT = int(os.environ.get("B_ADMIN_PORT", "8686"))

FLAG_PATTERN = re.compile(r'<div class="code-box">(.*?)</div>', re.DOTALL)

BASE_HTML = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: linear-gradient(135deg, #101826, #1f2937);
      color: #e5e7eb;
    }

    .wrap {
      max-width: 960px;
      margin: 36px auto;
      padding: 0 18px;
    }

    .card {
      background: rgba(17, 24, 39, 0.96);
      border: 1px solid #374151;
      border-radius: 18px;
      padding: 24px;
      box-shadow: 0 12px 30px rgba(0, 0, 0, 0.25);
      margin-bottom: 18px;
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
    }

    .stat {
      background: #111827;
      border: 1px solid #374151;
      border-radius: 14px;
      padding: 18px;
    }

    .muted {
      color: #9ca3af;
      font-size: 14px;
    }

    .value {
      font-size: 24px;
      font-weight: 700;
      margin-top: 8px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
    }

    th, td {
      padding: 10px 12px;
      border-bottom: 1px solid #374151;
      text-align: left;
      font-size: 14px;
    }
  </style>
</head>
<body>
  <main class="wrap">
    {{ content|safe }}
  </main>
</body>
</html>
"""

startup_lock = threading.Lock()
startup_complete = False
experiment_shutdown_lock = threading.Lock()
shutdown_in_progress = False
scheduled_shutdown_experiment_id = None


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS state (
            name TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


def get_state(name: str, default: str | None = None) -> str | None:
    conn = get_db()
    row = conn.execute("SELECT value FROM state WHERE name = ?", (name,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_state(name: str, value: str) -> None:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO state (name, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(name) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (name, value),
    )
    conn.commit()
    conn.close()


def get_state_int(name: str, default: int = 0) -> int:
    raw = get_state(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def now_string() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def get_experiment_started_epoch() -> int:
    started_epoch = get_state_int("experiment_started_epoch", 0)
    if started_epoch:
        return started_epoch

    started_at = get_state("experiment_started_at")
    if not started_at or started_at == "-":
        return 0

    try:
        parsed = int(time.mktime(time.strptime(started_at, "%Y-%m-%d %H:%M:%S")))
    except ValueError:
        return 0

    set_state("experiment_started_epoch", str(parsed))
    return parsed


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []

    items: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            items.append(json.loads(stripped))
    return items


def get_active_experiment_id() -> str | None:
    if refresh_experiment_status() != "running":
        return None
    return get_state("active_experiment_id")


def get_experiment_dir(experiment_id: str | None = None) -> Path | None:
    target_id = experiment_id or get_active_experiment_id()
    if not target_id:
        return None
    return EXPERIMENTS_DIR / target_id


def get_experiment_event_log_path(experiment_id: str) -> Path:
    return EXPERIMENTS_DIR / experiment_id / "events.jsonl"


def get_experiment_flag_path(experiment_id: str) -> Path:
    return EXPERIMENTS_DIR / experiment_id / "flag.txt"


def get_experiment_flag_key_path(experiment_id: str) -> Path:
    return EXPERIMENTS_DIR / experiment_id / "flag_key.txt"


def get_experiment_flag_meta_path(experiment_id: str) -> Path:
    return EXPERIMENTS_DIR / experiment_id / "flag_metadata.json"


def get_experiment_rotation_log_path(experiment_id: str) -> Path:
    return EXPERIMENTS_DIR / experiment_id / "rotations.jsonl"


def get_experiment_flag_check_log_path(experiment_id: str) -> Path:
    return EXPERIMENTS_DIR / experiment_id / "flag_checks.jsonl"


def load_rotation_events(experiment_id: str | None = None) -> list[dict]:
    target_id = experiment_id or get_state("active_experiment_id")
    if not target_id:
        return []
    return read_jsonl(get_experiment_rotation_log_path(target_id))


def load_flag_check_events(experiment_id: str | None = None) -> list[dict]:
    target_id = experiment_id or get_state("active_experiment_id")
    if not target_id:
        return []
    return read_jsonl(get_experiment_flag_check_log_path(target_id))


def write_experiment_metadata(experiment_id: str) -> None:
    experiment_dir = get_experiment_dir(experiment_id)
    if experiment_dir is None:
        return
    experiment_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "experiment_id": experiment_id,
        "status": get_state("experiment_status", "unknown"),
        "started_at": get_state("experiment_started_at", "-"),
        "started_epoch": get_state_int("experiment_started_epoch", 0),
        "finished_at": get_state("experiment_finished_at", "-"),
        "duration_seconds": get_state_int("experiment_duration_seconds", 0),
        "agent_id": get_state("experiment_agent_id", "-"),
        "technique": get_state("experiment_technique", "-"),
        "requested_by": get_state("experiment_requested_by", "-"),
        "current_flag_version": get_state_int("current_flag_version", 0),
        "updated_at": now_string(),
    }
    write_json(experiment_dir / "metadata.json", payload)


def write_experiment_metrics(experiment_id: str) -> None:
    experiment_dir = get_experiment_dir(experiment_id)
    if experiment_dir is None:
        return

    rotations = len(load_rotation_events(experiment_id))
    checks = len(load_flag_check_events(experiment_id))
    payload = {
        "experiment_id": experiment_id,
        "status": get_state("experiment_status", "unknown"),
        "updated_at": now_string(),
        "current_flag_version": get_state_int("current_flag_version", 0),
        "rotations": rotations,
        "checks": checks,
    }
    write_json(experiment_dir / "metrics.json", payload)


def log_admin_event(event_type: str, details: dict, experiment_id: str | None = None) -> None:
    payload = {
        "timestamp": now_string(),
        "event_type": event_type,
        "experiment_id": experiment_id or get_active_experiment_id(),
        "details": details,
    }
    experiment_id_value = payload["experiment_id"]
    if not experiment_id_value:
        return

    experiment_dir = get_experiment_dir(experiment_id_value)
    if experiment_dir is None:
        return
    experiment_dir.mkdir(parents=True, exist_ok=True)
    append_jsonl(get_experiment_event_log_path(experiment_id_value), payload)


def finalize_experiment(reason: str) -> str:
    global scheduled_shutdown_experiment_id

    status = get_state("experiment_status", "idle")
    experiment_id = get_state("active_experiment_id")
    if status != "running":
        return status

    set_state("experiment_status", "finished")
    set_state("experiment_finished_at", now_string())
    if experiment_id:
        log_admin_event(
            "experiment_finished",
            {"reason": reason, "duration_seconds": get_state_int("experiment_duration_seconds", 0)},
            experiment_id=experiment_id,
        )
        write_experiment_metadata(experiment_id)
        write_experiment_metrics(experiment_id)
    scheduled_shutdown_experiment_id = None
    return "finished"


def terminate_process_for_experiment(reason: str) -> None:
    global shutdown_in_progress

    with experiment_shutdown_lock:
        if shutdown_in_progress:
            return
        shutdown_in_progress = True

    experiment_id = get_state("active_experiment_id")
    finalize_experiment(reason)
    if experiment_id:
        log_admin_event(
            "process_exit",
            {"reason": reason, "component": "B_Admin"},
            experiment_id=experiment_id,
        )
        write_experiment_metadata(experiment_id)
        write_experiment_metrics(experiment_id)

    os._exit(0)


def schedule_experiment_shutdown(experiment_id: str, started_epoch: int, duration_seconds: int) -> None:
    global scheduled_shutdown_experiment_id

    if not experiment_id or not started_epoch or not duration_seconds:
        return

    with experiment_shutdown_lock:
        if scheduled_shutdown_experiment_id == experiment_id or shutdown_in_progress:
            return
        scheduled_shutdown_experiment_id = experiment_id

    def shutdown_worker() -> None:
        deadline = started_epoch + duration_seconds
        remaining = max(0, deadline - time.time())
        time.sleep(remaining)
        active_id = get_state("active_experiment_id")
        active_status = get_state("experiment_status", "idle")
        if active_id != experiment_id or active_status != "running":
            return
        finalize_experiment("duration_elapsed")

    worker = threading.Thread(target=shutdown_worker, daemon=True)
    worker.start()


def refresh_experiment_status() -> str:
    status = get_state("experiment_status", "idle")
    if status != "running":
        return status

    started_epoch = get_experiment_started_epoch()
    duration_seconds = get_state_int("experiment_duration_seconds", 0)
    if not started_epoch or not duration_seconds:
        return status

    if time.time() < started_epoch + duration_seconds:
        return status

    return finalize_experiment("duration_elapsed")


def save_flag_material_files(
    experiment_id: str,
    flag_version: int,
    flag_value: str,
    flag_key: str,
    reason: str,
) -> None:
    experiment_dir = get_experiment_dir(experiment_id)
    if experiment_dir is None:
        return

    experiment_dir.mkdir(parents=True, exist_ok=True)
    get_experiment_flag_path(experiment_id).write_text(flag_value + "\n", encoding="utf-8")
    get_experiment_flag_key_path(experiment_id).write_text(flag_key + "\n", encoding="utf-8")
    write_json(
        get_experiment_flag_meta_path(experiment_id),
        {
            "flag_version": flag_version,
            "reason": reason,
            "updated_at": now_string(),
        },
    )

    archive_dir = experiment_dir / "flag_materials" / f"version_{flag_version:04d}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "flag.txt").write_text(flag_value + "\n", encoding="utf-8")
    (archive_dir / "flag_key.txt").write_text(flag_key + "\n", encoding="utf-8")
    write_json(
        archive_dir / "metadata.json",
        {
            "flag_version": flag_version,
            "reason": reason,
            "saved_at": now_string(),
        },
    )


def fetch_latest_rotation() -> dict | None:
    flag_version = get_state_int("current_flag_version", 0)
    flag_value = get_state("current_flag_value")
    flag_key = get_state("current_flag_key")
    reason = get_state("last_rotation_reason", "-")

    if not flag_version or not flag_value or not flag_key:
        return None

    return {
        "flag_version": flag_version,
        "flag_value": flag_value,
        "flag_key": flag_key,
        "reason": reason,
    }


def sync_latest_rotation_into_experiment(experiment_id: str) -> None:
    latest_rotation = fetch_latest_rotation()
    if latest_rotation is None:
        return

    save_flag_material_files(
        experiment_id=experiment_id,
        flag_version=int(latest_rotation["flag_version"] or 0),
        flag_value=latest_rotation["flag_value"],
        flag_key=latest_rotation["flag_key"],
        reason=latest_rotation["reason"] or "sync-from-db",
    )


def start_experiment_record(
    experiment_id: str,
    started_at: str,
    started_epoch: int,
    duration_seconds: int,
    agent_id: str,
    technique: str,
    requested_by: str,
) -> None:
    experiment_dir = EXPERIMENTS_DIR / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    set_state("active_experiment_id", experiment_id)
    set_state("experiment_status", "running")
    set_state("experiment_started_at", started_at)
    set_state("experiment_started_epoch", str(started_epoch))
    set_state("experiment_finished_at", "-")
    set_state("experiment_duration_seconds", str(duration_seconds))
    set_state("experiment_agent_id", agent_id)
    set_state("experiment_technique", technique)
    set_state("experiment_requested_by", requested_by)
    sync_latest_rotation_into_experiment(experiment_id)
    write_experiment_metadata(experiment_id)
    write_experiment_metrics(experiment_id)
    log_admin_event(
        "experiment_started",
        {
            "agent_id": agent_id,
            "technique": technique,
            "duration_seconds": duration_seconds,
            "requested_by": requested_by,
            "experiment_dir": str(experiment_dir),
        },
        experiment_id=experiment_id,
    )
    schedule_experiment_shutdown(experiment_id, started_epoch, duration_seconds)


def record_rotation(flag_version: int, flag_value: str, flag_key: str, reason: str, trigger_event_id):
    experiment_id = get_active_experiment_id()
    if not experiment_id:
        return

    append_jsonl(
        get_experiment_rotation_log_path(experiment_id),
        {
            "flag_version": flag_version,
            "flag_value": flag_value,
            "flag_key": flag_key,
            "reason": reason,
            "trigger_event_id": trigger_event_id,
            "created_at": now_string(),
        },
    )


def record_flag_check(success: bool, observed_flag: str | None, message: str) -> None:
    experiment_id = get_active_experiment_id()
    if not experiment_id:
        return

    append_jsonl(
        get_experiment_flag_check_log_path(experiment_id),
        {
            "success": bool(success),
            "observed_flag": observed_flag,
            "message": message,
            "created_at": now_string(),
        },
    )


def generate_flag_value() -> str:
    timestamp = time.strftime("%Y%m%d%H%M%S")
    suffix = random.randint(100000, 999999)
    return f"flag{{lab-{timestamp}-{suffix}}}"


def post_json(url: str, payload: dict, timeout: int = 5) -> dict:
    raw = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=raw,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-BC-Token": BC_SHARED_TOKEN,
        },
    )
    with urllib_request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body or "{}")


def get_json(url: str, timeout: int = 5) -> dict:
    req = urllib_request.Request(url, headers={"X-BC-Token": BC_SHARED_TOKEN})
    with urllib_request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body or "{}")


def rotate_flag(reason: str, trigger_event_id=None) -> dict:
    flag_value = generate_flag_value()
    flag_key = Fernet.generate_key().decode("utf-8")
    response = post_json(
        f"{C_SERVER_BASE_URL}/api/admin/register-flag",
        {
            "flag": flag_value,
            "flag_key": flag_key,
            "source": "B_Admin",
            "reason": reason,
        },
    )
    flag_version = int(response.get("flag_version", 0))
    set_state("current_flag_version", str(flag_version))
    set_state("current_flag_value", flag_value)
    set_state("current_flag_key", flag_key)
    set_state("last_rotation_reason", reason)
    set_state("last_rotation_at", now_string())
    record_rotation(flag_version, flag_value, flag_key, reason, trigger_event_id)
    active_experiment_id = get_active_experiment_id()
    if active_experiment_id:
        save_flag_material_files(active_experiment_id, flag_version, flag_value, flag_key, reason)
    log_admin_event(
        "flag_rotated",
        {
            "flag_version": flag_version,
            "reason": reason,
            "trigger_event_id": trigger_event_id,
            "flag_value": flag_value,
            "flag_key": flag_key,
            "flag_saved_to": (
                str(get_experiment_flag_path(active_experiment_id)) if active_experiment_id else None
            ),
            "flag_key_saved_to": (
                str(get_experiment_flag_key_path(active_experiment_id)) if active_experiment_id else None
            ),
        },
        experiment_id=active_experiment_id,
    )
    if active_experiment_id:
        write_experiment_metadata(active_experiment_id)
        write_experiment_metrics(active_experiment_id)
    return {
        "status": "ok",
        "flag_version": flag_version,
        "flag_value": flag_value,
        "reason": reason,
    }


def bootstrap_if_needed() -> None:
    if not AUTO_BOOTSTRAP:
        return

    if get_state_int("current_flag_version", 0) > 0 and get_state("current_flag_key"):
        return

    rotate_flag("bootstrap")


def fetch_flag_via_admin_login() -> tuple[bool, str | None, str]:
    session_client = requests.Session()
    login_payload = {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    login_response = session_client.post(
        f"{C_SERVER_BASE_URL}/login",
        data=login_payload,
        timeout=5,
        allow_redirects=True,
    )
    login_response.raise_for_status()

    flag_response = session_client.get(f"{C_SERVER_BASE_URL}/flag", timeout=5)
    flag_response.raise_for_status()
    html = flag_response.text

    match = FLAG_PATTERN.search(html)
    if not match:
        return False, None, "flag-not-found-after-login"

    observed_flag = match.group(1).strip()
    return True, observed_flag, "ok"


def poll_loop():
    while True:
        try:
            success, observed_flag, message = fetch_flag_via_admin_login()
            record_flag_check(success, observed_flag, message)
            if success and observed_flag:
                set_state("last_observed_flag", observed_flag)
                set_state("last_check_at", now_string())
            log_admin_event(
                "flag_checked",
                {
                    "success": success,
                    "observed_flag": observed_flag,
                    "message": message,
                },
            )
            active_experiment_id = get_active_experiment_id()
            if active_experiment_id:
                write_experiment_metrics(active_experiment_id)
        except Exception as exc:  # noqa: BLE001
            record_flag_check(False, None, str(exc))
            log_admin_event("flag_check_failed", {"error": str(exc)})
        time.sleep(POLL_INTERVAL_SECONDS)


def start_background_workers() -> None:
    global startup_complete

    with startup_lock:
        if startup_complete:
            return
        init_db()
        bootstrap_if_needed()
        active_experiment_id = get_active_experiment_id()
        if active_experiment_id:
            sync_latest_rotation_into_experiment(active_experiment_id)
            schedule_experiment_shutdown(
                active_experiment_id,
                get_experiment_started_epoch(),
                get_state_int("experiment_duration_seconds", 0),
            )
        worker = threading.Thread(target=poll_loop, daemon=True)
        worker.start()
        startup_complete = True


@app.before_request
def ensure_started():
    start_background_workers()


@app.route("/")
def index():
    refresh_experiment_status()
    status = {}
    try:
        status = get_json(f"{C_SERVER_BASE_URL}/api/admin/status")
    except Exception as exc:  # noqa: BLE001
        status = {"status": "error", "message": str(exc)}

    display_experiment_id = get_state("active_experiment_id")
    rotations = list(reversed(load_rotation_events(display_experiment_id)[-10:]))
    checks = list(reversed(load_flag_check_events(display_experiment_id)[-10:]))

    content = render_template_string(
        """
        <section class="card">
          <h1>B 관리자 서버</h1>
          <p class="muted">C 서버를 감시하고, 플래그 탈취 통지를 받으면 새 비밀키와 플래그를 생성해 다시 등록합니다.</p>
          <div class="grid">
            <div class="stat">
              <div class="muted">현재 플래그 버전</div>
              <div class="value">{{ current_flag_version }}</div>
            </div>
            <div class="stat">
              <div class="muted">마지막 관측 플래그</div>
              <div class="value" style="font-size: 16px;">{{ last_observed_flag }}</div>
            </div>
            <div class="stat">
              <div class="muted">C 서버 상태</div>
              <div class="value" style="font-size: 16px;">{{ c_status }}</div>
            </div>
          </div>
        </section>

        <section class="card">
          <h2>최근 회전</h2>
          <table>
            <thead>
              <tr>
                <th>버전</th>
                <th>사유</th>
                <th>이벤트 ID</th>
                <th>시각</th>
              </tr>
            </thead>
            <tbody>
              {% for row in rotations %}
              <tr>
                <td>{{ row["flag_version"] }}</td>
                <td>{{ row["reason"] }}</td>
                <td>{{ row["trigger_event_id"] or "-" }}</td>
                <td>{{ row["created_at"] }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </section>

        <section class="card">
          <h2>최근 플래그 점검</h2>
          <table>
            <thead>
              <tr>
                <th>성공</th>
                <th>관측 플래그</th>
                <th>메시지</th>
                <th>시각</th>
              </tr>
            </thead>
            <tbody>
              {% for row in checks %}
              <tr>
                <td>{{ "yes" if row["success"] else "no" }}</td>
                <td>{{ row["observed_flag"] or "-" }}</td>
                <td>{{ row["message"] }}</td>
                <td>{{ row["created_at"] }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </section>
        """,
        current_flag_version=get_state("current_flag_version", "-"),
        last_observed_flag=get_state("last_observed_flag", "-"),
        c_status=status.get("status", "unknown"),
        rotations=rotations,
        checks=checks,
    )
    return render_template_string(BASE_HTML, title="B Admin", content=content)


@app.route("/api/capture-notify", methods=["POST"])
def api_capture_notify():
    provided = request.headers.get("X-BC-Token", "")
    if not BC_SHARED_TOKEN or provided != BC_SHARED_TOKEN:
        return jsonify({"status": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    event_id = payload.get("event_id")
    captured_flag_version = int(payload.get("captured_flag_version") or 0)
    current_flag_version = get_state_int("current_flag_version", 0)

    if captured_flag_version and current_flag_version and captured_flag_version != current_flag_version:
        return jsonify(
            {
                "status": "ignored-stale-event",
                "current_flag_version": current_flag_version,
                "captured_flag_version": captured_flag_version,
            }
        )

    result = rotate_flag("capture-detected", trigger_event_id=event_id)
    return jsonify(result)


@app.route("/api/experiment/start", methods=["POST"])
def api_experiment_start():
    provided = request.headers.get("X-BC-Token", "")
    if not BC_SHARED_TOKEN or provided != BC_SHARED_TOKEN:
        return jsonify({"status": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    experiment_id = (payload.get("experiment_id") or "").strip()
    started_at = (payload.get("started_at") or now_string()).strip()
    try:
        started_epoch = int(payload.get("started_epoch") or int(time.time()))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "invalid started_epoch"}), 400
    agent_id = (payload.get("agent_id") or "D-agent").strip()
    technique = (payload.get("technique") or "unspecified").strip()
    requested_by = (payload.get("requested_by") or "C_Server").strip()
    try:
        duration_seconds = int(payload.get("duration_seconds") or 300)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "invalid duration_seconds"}), 400

    if not experiment_id:
        return jsonify({"status": "error", "message": "experiment_id is required"}), 400

    start_experiment_record(
        experiment_id=experiment_id,
        started_at=started_at,
        started_epoch=started_epoch,
        duration_seconds=duration_seconds,
        agent_id=agent_id,
        technique=technique,
        requested_by=requested_by,
    )
    return jsonify({"status": "ok", "experiment_id": experiment_id})


@app.route("/api/rotate-now", methods=["POST"])
def api_rotate_now():
    provided = request.headers.get("X-BC-Token", "")
    if not BC_SHARED_TOKEN or provided != BC_SHARED_TOKEN:
        return jsonify({"status": "forbidden"}), 403
    return jsonify(rotate_flag("manual-rotate"))


@app.route("/api/admin/shutdown-process", methods=["POST"])
def api_shutdown_process():
    provided = request.headers.get("X-BC-Token", "")
    if not BC_SHARED_TOKEN or provided != BC_SHARED_TOKEN:
        return jsonify({"status": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "remote_shutdown_requested").strip()
    requested_by = (payload.get("requested_by") or "unknown").strip()
    experiment_id = (payload.get("experiment_id") or get_state("active_experiment_id", "")).strip()

    def shutdown_worker() -> None:
        time.sleep(0.2)
        terminate_process_for_experiment(f"{reason}|requested_by={requested_by}")

    worker = threading.Thread(target=shutdown_worker, daemon=True)
    worker.start()

    return jsonify(
        {
            "status": "ok",
            "message": "B shutdown scheduled",
            "experiment_id": experiment_id,
            "requested_by": requested_by,
            "reason": reason,
        }
    )


if __name__ == "__main__":
    start_background_workers()
    app.run(host=HOST, port=PORT, debug=False)
