import json
import logging
import os
import sqlite3
import threading
import time
from functools import wraps
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from urllib import error, request as urllib_request

from cryptography.fernet import Fernet, InvalidToken
from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
SECRET_DIR = BASE_DIR / "secrets"
SECRET_DIR.mkdir(exist_ok=True)

DB_DIR = BASE_DIR / "data"
DB_DIR.mkdir(exist_ok=True)

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
EXPERIMENTS_DIR = BASE_DIR / "experiments"
EXPERIMENTS_DIR.mkdir(exist_ok=True)

FLAG_FILE = SECRET_DIR / "flag.enc"
DB_FILE = DB_DIR / "app.db"
REQUEST_LOG_FILE = LOG_DIR / "security.log"
LAB_EVENT_LOG_FILE = LOG_DIR / "lab_events.jsonl"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", os.urandom(32).hex())
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False  # Set to True when HTTPS is enabled

DEFAULT_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeThisAdminPassword123!")
AUTO_USER_COUNT = int(os.environ.get("AUTO_USER_COUNT", "10"))
AUTO_USER_PREFIX = os.environ.get("AUTO_USER_PREFIX", "user")
AUTO_USER_PASSWORD = os.environ.get("AUTO_USER_PASSWORD", "UserPassword123!")
LOG_SENSITIVE_REQUESTS = os.environ.get("LOG_SENSITIVE_REQUESTS", "1") == "1"
LOG_ROTATE_WHEN = os.environ.get("LOG_ROTATE_WHEN", "midnight")
LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", "7"))
BC_SHARED_TOKEN = os.environ.get("BC_SHARED_TOKEN", "change-this-shared-token")
B_ADMIN_NOTIFY_URL = os.environ.get("B_ADMIN_NOTIFY_URL", "http://192.168.0.6:8686/api/capture-notify")
B_ADMIN_EXPERIMENT_START_URL = os.environ.get(
    "B_ADMIN_EXPERIMENT_START_URL",
    "http://192.168.0.6:8686/api/experiment/start",
)
CAPTURE_NOTIFY_DELAY_SECONDS = int(os.environ.get("CAPTURE_NOTIFY_DELAY_SECONDS", "5"))
B_ADMIN_NOTIFY_TIMEOUT = int(os.environ.get("B_ADMIN_NOTIFY_TIMEOUT", "5"))


BASE_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    :root {
      --bg: #0f172a;
      --panel: #111827;
      --panel-2: #1f2937;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --danger: #ef4444;
      --success: #10b981;
      --border: #374151;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: linear-gradient(135deg, #0b1220, #111827);
      color: var(--text);
    }

    .navbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 28px;
      background: rgba(17, 24, 39, 0.95);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
    }

    .brand {
      font-size: 20px;
      font-weight: 700;
      color: white;
      text-decoration: none;
    }

    .nav-links a {
      color: var(--text);
      text-decoration: none;
      margin-left: 16px;
      font-size: 14px;
    }

    .nav-links a:hover {
      color: white;
    }

    .container {
      max-width: 960px;
      margin: 40px auto;
      padding: 0 20px;
    }

    .card {
      background: rgba(17, 24, 39, 0.96);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 12px 30px rgba(0,0,0,0.25);
    }

    .hero {
      padding: 36px 28px;
      border-radius: 20px;
      background: linear-gradient(135deg, rgba(59,130,246,0.15), rgba(16,185,129,0.10));
      border: 1px solid var(--border);
      margin-bottom: 24px;
    }

    h1, h2, h3 {
      margin-top: 0;
      color: white;
    }

    p {
      color: var(--text);
      line-height: 1.6;
    }

    .muted {
      color: var(--muted);
      font-size: 14px;
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-top: 20px;
    }

    .stat {
      background: var(--panel-2);
      border: 1px solid var(--border);
      padding: 18px;
      border-radius: 14px;
    }

    .stat-title {
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 8px;
    }

    .stat-value {
      font-size: 22px;
      font-weight: 700;
      color: white;
    }

    form {
      display: grid;
      gap: 14px;
    }

    label {
      font-size: 14px;
      color: var(--text);
    }

    input {
      width: 100%;
      margin-top: 6px;
      padding: 12px 14px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #0b1220;
      color: white;
      outline: none;
    }

    input:focus {
      border-color: var(--primary);
    }

    .btn {
      display: inline-block;
      text-decoration: none;
      border: none;
      padding: 12px 16px;
      border-radius: 10px;
      cursor: pointer;
      font-weight: 700;
      font-size: 14px;
    }

    .btn-primary {
      background: var(--primary);
      color: white;
    }

    .btn-primary:hover {
      background: var(--primary-hover);
    }

    .btn-secondary {
      background: var(--panel-2);
      color: white;
      border: 1px solid var(--border);
    }

    .btn-danger {
      background: var(--danger);
      color: white;
    }

    .flash-wrap {
      margin-bottom: 18px;
    }

    .flash {
      padding: 12px 14px;
      border-radius: 10px;
      margin-bottom: 10px;
      font-size: 14px;
      border: 1px solid var(--border);
      background: #172033;
    }

    .flash.success { border-color: #14532d; background: rgba(16,185,129,0.12); }
    .flash.error { border-color: #7f1d1d; background: rgba(239,68,68,0.12); }
    .flash.info { border-color: #1e3a8a; background: rgba(59,130,246,0.12); }

    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 18px;
      overflow: hidden;
      border-radius: 12px;
      border: 1px solid var(--border);
    }

    th, td {
      padding: 12px 14px;
      text-align: left;
      border-bottom: 1px solid var(--border);
      font-size: 14px;
    }

    th {
      background: #0b1220;
      color: white;
    }

    tr:hover td {
      background: rgba(255,255,255,0.02);
    }

    .badge {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
    }

    .badge-admin {
      background: rgba(59,130,246,0.18);
      color: #93c5fd;
    }

    .badge-user {
      background: rgba(156,163,175,0.16);
      color: #d1d5db;
    }

    .actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 18px;
    }

    .code-box {
      padding: 16px;
      background: #0b1220;
      border: 1px solid var(--border);
      border-radius: 12px;
      font-family: monospace;
      color: #a7f3d0;
      word-break: break-all;
      margin-top: 14px;
    }
  </style>
</head>
<body>
  <nav class="navbar">
    <a class="brand" href="{{ url_for('index') }}">Research Secure Server</a>
    <div class="nav-links">
      {% if current_user %}
        <a href="{{ url_for('dashboard') }}">Dashboard</a>
        {% if current_user["is_admin"] %}
          <a href="{{ url_for('admin_users') }}">Admin</a>
          <a href="{{ url_for('get_flag') }}">Flag</a>
        {% endif %}
        <a href="{{ url_for('logout') }}">Logout</a>
      {% else %}
        <a href="{{ url_for('login') }}">Login</a>
        <a href="{{ url_for('register') }}">Sign Up</a>
      {% endif %}
    </div>
  </nav>

  <main class="container">
    <div class="flash-wrap">
      {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
          {% for category, message in messages %}
            <div class="flash {{ category }}">{{ message }}</div>
          {% endfor %}
        {% endif %}
      {% endwith %}
    </div>

    {{ content|safe }}
  </main>
</body>
</html>
"""


request_logger = logging.getLogger("research.request")
security_logger = logging.getLogger("research.security")
experiment_shutdown_lock = threading.Lock()
shutdown_in_progress = False
scheduled_shutdown_experiment_id = None


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_FILE)
        g.db.row_factory = sqlite3.Row
    return g.db


def setup_logging():
    if request_logger.handlers:
        return

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    file_handler = TimedRotatingFileHandler(
        REQUEST_LOG_FILE,
        when=LOG_ROTATE_WHEN,
        interval=1,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.WARNING)

    request_logger.setLevel(logging.INFO)
    request_logger.propagate = False
    request_logger.addHandler(console_handler)

    security_logger.setLevel(logging.WARNING)
    security_logger.propagate = False
    security_logger.addHandler(console_handler)
    security_logger.addHandler(file_handler)


def get_client_ip() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded_for:
        return forwarded_for
    return request.remote_addr or "-"


def get_request_body_preview() -> str:
    if request.method not in {"POST", "PUT", "PATCH"}:
        return ""

    content_type = request.content_type or ""
    if not any(kind in content_type for kind in ("json", "form", "text", "xml")):
        return ""

    raw = request.get_data(cache=True, as_text=True)[:200]
    return raw.replace("\n", "\\n")


def inspect_request() -> list[str]:
    findings = []
    request_target = f"{request.path}?{request.query_string.decode('utf-8', errors='ignore')}"
    lowered = request_target.lower()
    body_preview = get_request_body_preview().lower()
    combined = f"{lowered} {body_preview}"

    suspicious_tokens = (
        "../",
        "..\\",
        "/etc/passwd",
        "union select",
        "<script",
        " or 1=1",
        "cmd=",
        "wget ",
        "curl ",
    )
    if any(token in combined for token in suspicious_tokens):
        findings.append("suspicious-pattern")

    if request.path in {"/flag", "/admin/users", "/api/admin/register-flag"}:
        findings.append("sensitive-route")

    user_agent = (request.headers.get("User-Agent") or "").lower()
    noisy_agents = ("sqlmap", "nikto", "nmap", "masscan", "dirbuster", "gobuster")
    if any(agent in user_agent for agent in noisy_agents):
        findings.append("scanner-user-agent")

    return findings


@app.before_request
def log_request_start():
    g.request_started_at = time.perf_counter()
    g.request_findings = inspect_request()


@app.after_request
def log_request_result(response):
    duration_ms = (time.perf_counter() - g.get("request_started_at", time.perf_counter())) * 1000
    client_ip = get_client_ip()
    user = get_current_user()
    username = user["username"] if user else "-"
    body_preview = get_request_body_preview()
    request_line = (
        f'{client_ip} "{request.method} {request.full_path.rstrip("?")}" '
        f'status={response.status_code} duration_ms={duration_ms:.2f} '
        f'user={username} len={response.calculate_content_length() or 0}'
    )
    if body_preview:
        request_line += f' body="{body_preview}"'

    request_logger.info(request_line)

    findings = list(g.get("request_findings", []))
    if response.status_code >= 400:
        findings.append(f"http-{response.status_code}")

    if request.endpoint == "login" and request.method == "POST" and response.status_code in {200, 302}:
        if "_flashes" in session:
            for category, message in session["_flashes"]:
                if "Invalid username or password." in message:
                    findings.append("login-failed")
                    break

    if LOG_SENSITIVE_REQUESTS and findings:
        security_logger.warning("%s findings=%s", request_line, ",".join(sorted(set(findings))))

    return response


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS lab_state (
            name TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.commit()

    admin = db.execute("SELECT id FROM users WHERE username = ?", (DEFAULT_ADMIN_USERNAME,)).fetchone()
    if admin is None:
        db.execute(
            """
            INSERT INTO users (username, password_hash, is_admin)
            VALUES (?, ?, 1)
            """,
            (DEFAULT_ADMIN_USERNAME, generate_password_hash(DEFAULT_ADMIN_PASSWORD)),
        )
        db.commit()
    else:
        db.execute(
            """
            UPDATE users
            SET password_hash = ?, is_admin = 1
            WHERE username = ?
            """,
            (generate_password_hash(DEFAULT_ADMIN_PASSWORD), DEFAULT_ADMIN_USERNAME),
        )
        db.commit()


def seed_auto_users():
    if AUTO_USER_COUNT <= 0:
        return

    db = get_db()
    for index in range(1, AUTO_USER_COUNT + 1):
        username = f"{AUTO_USER_PREFIX}{index:03d}"
        existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            continue

        db.execute(
            """
            INSERT INTO users (username, password_hash, is_admin)
            VALUES (?, ?, 0)
            """,
            (username, generate_password_hash(AUTO_USER_PASSWORD)),
        )

    db.commit()


def get_state(name: str, default: str | None = None) -> str | None:
    row = get_db().execute("SELECT value FROM lab_state WHERE name = ?", (name,)).fetchone()
    return row["value"] if row else default


def set_state(name: str, value: str) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO lab_state (name, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(name) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (name, value),
    )
    db.commit()


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


def make_experiment_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


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


def write_jsonl(path: Path, payloads: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def get_active_experiment_id() -> str | None:
    if refresh_experiment_status() != "running":
        return None
    return get_state("active_experiment_id")


def get_experiment_dir(experiment_id: str | None = None) -> Path | None:
    target_id = experiment_id or get_active_experiment_id()
    if not target_id:
        return None
    return EXPERIMENTS_DIR / target_id


def get_active_experiment_dir() -> Path | None:
    return get_experiment_dir()


def get_experiment_capture_log_path(experiment_id: str) -> Path:
    return EXPERIMENTS_DIR / experiment_id / "capture_events.jsonl"


def load_capture_events(experiment_id: str | None = None) -> list[dict]:
    target_id = experiment_id or get_state("active_experiment_id")
    if not target_id:
        return []
    return read_jsonl(get_experiment_capture_log_path(target_id))


def append_capture_event(experiment_id: str, payload: dict) -> None:
    append_jsonl(get_experiment_capture_log_path(experiment_id), payload)


def build_experiment_metadata(experiment_id: str) -> dict:
    return {
        "experiment_id": experiment_id,
        "status": get_state("experiment_status", "unknown"),
        "started_at": get_state("experiment_started_at", "-"),
        "started_epoch": get_state_int("experiment_started_epoch", 0),
        "finished_at": get_state("experiment_finished_at", "-"),
        "duration_seconds": get_state_int("experiment_duration_seconds", 0),
        "agent_id": get_state("experiment_agent_id", "-"),
        "technique": get_state("experiment_technique", "-"),
        "requested_by": get_state("experiment_requested_by", "-"),
        "flag_version_at_start": get_state_int("experiment_start_flag_version", 0),
        "current_flag_version": get_state_int("flag_version", 0),
        "updated_at": now_string(),
    }


def write_experiment_metadata(experiment_id: str) -> None:
    experiment_dir = get_experiment_dir(experiment_id)
    if experiment_dir is None:
        return
    experiment_dir.mkdir(parents=True, exist_ok=True)
    write_json(experiment_dir / "metadata.json", build_experiment_metadata(experiment_id))


def write_experiment_metrics(experiment_id: str) -> None:
    experiment_dir = get_experiment_dir(experiment_id)
    if experiment_dir is None:
        return

    capture_events = load_capture_events(experiment_id)
    total = len(capture_events)
    correct = sum(1 for event in capture_events if event.get("is_correct"))

    payload = {
        "experiment_id": experiment_id,
        "status": get_state("experiment_status", "unknown"),
        "started_at": get_state("experiment_started_at"),
        "updated_at": now_string(),
        "flag_version": get_state_int("flag_version", 0),
        "captures": {
            "total": total,
            "correct": correct,
            "incorrect": total - correct,
        },
    }
    write_json(experiment_dir / "metrics.json", payload)


def log_lab_event(event_type: str, details: dict, experiment_id: str | None = None) -> None:
    payload = {
        "timestamp": now_string(),
        "event_type": event_type,
        "experiment_id": experiment_id or get_active_experiment_id(),
        "details": details,
    }
    append_jsonl(LAB_EVENT_LOG_FILE, payload)

    target_dir = get_experiment_dir(payload["experiment_id"])
    if target_dir is not None:
        target_dir.mkdir(parents=True, exist_ok=True)
        append_jsonl(target_dir / "events.jsonl", payload)


def finalize_experiment(reason: str) -> str:
    status = get_state("experiment_status", "idle")
    experiment_id = get_state("active_experiment_id")
    if status != "running":
        return status

    set_state("experiment_status", "finished")
    set_state("experiment_finished_at", now_string())
    if experiment_id:
        log_lab_event(
            "experiment_finished",
            {"reason": reason, "duration_seconds": get_state_int("experiment_duration_seconds", 0)},
            experiment_id=experiment_id,
        )
        write_experiment_metadata(experiment_id)
        write_experiment_metrics(experiment_id)
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
        log_lab_event(
            "process_exit",
            {"reason": reason, "component": "C_Server"},
            experiment_id=experiment_id,
        )
        write_experiment_metadata(experiment_id)
        write_experiment_metrics(experiment_id)

    logging.shutdown()
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
        with app.app_context():
            terminate_process_for_experiment("duration_elapsed")

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


def start_experiment(
    agent_id: str,
    technique: str,
    duration_seconds: int,
    requested_by: str,
) -> dict:
    experiment_id = make_experiment_id()
    started_at = now_string()
    started_epoch = int(time.time())
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
    set_state("experiment_start_flag_version", str(get_state_int("flag_version", 0)))

    write_experiment_metadata(experiment_id)
    write_experiment_metrics(experiment_id)
    log_lab_event(
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

    return {
        "experiment_id": experiment_id,
        "started_at": started_at,
        "started_epoch": started_epoch,
        "duration_seconds": duration_seconds,
        "agent_id": agent_id,
        "technique": technique,
        "requested_by": requested_by,
    }


def load_active_key() -> bytes:
    key = get_state("flag_key")
    if key:
        return key.encode("utf-8")

    env_key = os.environ.get("FLAG_KEY")
    if env_key:
        return env_key.encode("utf-8")

    raise RuntimeError("No active flag key is registered.")


def encrypt_and_store_flag(flag_value: str, flag_key: str) -> None:
    encrypted = Fernet(flag_key.encode("utf-8")).encrypt(flag_value.encode("utf-8"))
    FLAG_FILE.write_bytes(encrypted)


def register_flag_material(flag_value: str, flag_key: str, source: str, reason: str) -> int:
    next_version = get_state_int("flag_version", 0) + 1
    encrypt_and_store_flag(flag_value, flag_key)
    set_state("flag_key", flag_key)
    set_state("flag_version", str(next_version))
    set_state("last_rotation_source", source)
    set_state("last_rotation_reason", reason)
    set_state("last_rotation_at", now_string())
    active_experiment_id = get_active_experiment_id()
    log_lab_event(
        "flag_rotated",
        {
            "flag_version": next_version,
            "source": source,
            "reason": reason,
        },
        experiment_id=active_experiment_id,
    )
    if active_experiment_id:
        write_experiment_metadata(active_experiment_id)
        write_experiment_metrics(active_experiment_id)
    return next_version


def decrypt_flag() -> str:
    if not FLAG_FILE.exists():
        raise FileNotFoundError("Encrypted flag file is missing.")

    encrypted = FLAG_FILE.read_bytes()
    try:
        plaintext = Fernet(load_active_key()).decrypt(encrypted)
        return plaintext.decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Failed to decrypt the flag.") from exc


def ensure_initial_flag_state() -> None:
    if get_state("flag_key") and FLAG_FILE.exists():
        return

    default_flag = os.environ.get("LAB_FLAG", "flag{example_research_flag_change_me}")
    default_key = os.environ.get("FLAG_KEY")
    if not default_key:
        default_key = Fernet.generate_key().decode("utf-8")

    register_flag_material(default_flag, default_key, source="bootstrap", reason="initialize")


def initialize():
    init_db()
    seed_auto_users()
    ensure_initial_flag_state()
    active_experiment_id = get_state("active_experiment_id")
    if active_experiment_id and get_state("experiment_status") == "running":
        schedule_experiment_shutdown(
            active_experiment_id,
            get_experiment_started_epoch(),
            get_state_int("experiment_duration_seconds", 0),
        )


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    return get_db().execute(
        "SELECT id, username, is_admin, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()


@app.context_processor
def inject_user():
    return {"current_user": get_current_user()}


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            flash("Login is required.", "error")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapper


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            flash("Login is required.", "error")
            return redirect(url_for("login"))
        if not user["is_admin"]:
            abort(403)
        return view_func(*args, **kwargs)

    return wrapper


def require_shared_token() -> None:
    provided = request.headers.get("X-BC-Token", "")
    if not BC_SHARED_TOKEN or provided != BC_SHARED_TOKEN:
        abort(403)


def render_page(title: str, content: str):
    return render_template_string(BASE_HTML, title=title, content=content)


def get_capture_summary() -> dict[str, int]:
    capture_events = load_capture_events()
    total = len(capture_events)
    correct = sum(1 for event in capture_events if event.get("is_correct"))
    return {
        "total": total,
        "correct": correct,
        "incorrect": total - correct,
    }


def fetch_capture_event(event_id: int, experiment_id: str | None = None) -> dict | None:
    for event in load_capture_events(experiment_id):
        if int(event.get("id", 0)) == event_id:
            return event
    return None


def update_capture_notification(
    event_id: int,
    notified: bool,
    status: str,
    experiment_id: str | None = None,
) -> None:
    target_id = experiment_id or get_state("active_experiment_id")
    if not target_id:
        return

    events = load_capture_events(target_id)
    for event in events:
        if int(event.get("id", 0)) == event_id:
            event["notified_b"] = bool(notified)
            event["notify_status"] = status
            break
    write_jsonl(get_experiment_capture_log_path(target_id), events)


def post_json(url: str, payload: dict, timeout: int) -> dict:
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


def notify_b_of_capture(event_id: int, experiment_id: str) -> None:
    time.sleep(CAPTURE_NOTIFY_DELAY_SECONDS)
    with app.app_context():
        event = fetch_capture_event(event_id, experiment_id)
        if event is None or not event.get("is_correct"):
            return

        payload = {
            "event_id": event["id"],
            "agent_id": event["agent_id"],
            "technique": event["technique"],
            "captured_flag_version": event["flag_version"],
            "captured_at": event["created_at"],
            "delay_seconds": CAPTURE_NOTIFY_DELAY_SECONDS,
        }

        try:
            response = post_json(B_ADMIN_NOTIFY_URL, payload, timeout=B_ADMIN_NOTIFY_TIMEOUT)
            response_status = response.get("status", "unknown")
            update_capture_notification(event_id, True, response_status, experiment_id)
            log_lab_event(
                "capture_notified_b",
                {
                    "event_id": event_id,
                    "response_status": response_status,
                    "captured_flag_version": event["flag_version"],
                },
            )
            security_logger.warning(
                "capture event=%s notified_b=1 status=%s", event_id, response_status
            )
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            update_capture_notification(event_id, False, f"notify-failed:{exc}", experiment_id)
            log_lab_event(
                "capture_notify_failed",
                {
                    "event_id": event_id,
                    "error": str(exc),
                    "captured_flag_version": event["flag_version"],
                },
            )
            security_logger.warning("capture event=%s notified_b=0 error=%s", event_id, exc)


@app.route("/")
def index():
    user = get_current_user()
    summary = get_capture_summary()
    flag_version = get_state_int("flag_version", 0)
    last_rotation_reason = get_state("last_rotation_reason", "-")

    content = render_template_string(
        """
        <section class="hero">
          <h1>Research Secure Server</h1>
          <p>
            This experimental C server includes B-admin-driven flag rotation,
            agent submission tracking, and administrator web login flows.
          </p>
          <div class="actions">
            {% if user %}
              <a class="btn btn-primary" href="{{ url_for('dashboard') }}">Open Dashboard</a>
            {% else %}
              <a class="btn btn-primary" href="{{ url_for('register') }}">Sign Up</a>
              <a class="btn btn-secondary" href="{{ url_for('login') }}">Login</a>
            {% endif %}
          </div>
        </section>

        <section class="grid">
          <div class="stat">
            <div class="stat-title">Active Flag Version</div>
            <div class="stat-value">{{ flag_version }}</div>
          </div>
          <div class="stat">
            <div class="stat-title">Correct Submissions</div>
            <div class="stat-value">{{ summary["correct"] }}</div>
          </div>
          <div class="stat">
            <div class="stat-title">Last Rotation Reason</div>
            <div class="stat-value" style="font-size: 16px;">{{ last_rotation_reason }}</div>
          </div>
        </section>
        """,
        user=user,
        summary=summary,
        flag_version=flag_version,
        last_rotation_reason=last_rotation_reason,
    )
    return render_page("Home", content)


@app.route("/register", methods=["GET", "POST"])
def register():
    if get_current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        if not username or not password:
            flash("Please enter both username and password.", "error")
        elif len(username) < 3:
            flash("Username must be at least 3 characters long.", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters long.", "error")
        elif password != password_confirm:
            flash("Password confirmation does not match.", "error")
        else:
            db = get_db()
            existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if existing:
                flash("That username already exists.", "error")
            else:
                db.execute(
                    """
                    INSERT INTO users (username, password_hash, is_admin)
                    VALUES (?, ?, 0)
                    """,
                    (username, generate_password_hash(password)),
                )
                db.commit()
                flash("Registration completed. Please log in.", "success")
                return redirect(url_for("login"))

    content = """
    <div class="card" style="max-width: 520px; margin: 0 auto;">
      <h2>Sign Up</h2>
      <p class="muted">Create a standard user account.</p>
      <form method="post">
        <label>Username
          <input type="text" name="username" placeholder="At least 3 characters" required>
        </label>
        <label>Password
          <input type="password" name="password" placeholder="At least 8 characters" required>
        </label>
        <label>Confirm Password
          <input type="password" name="password_confirm" required>
        </label>
        <button class="btn btn-primary" type="submit">Create Account</button>
      </form>
    </div>
    """
    return render_page("Sign Up", content)


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = get_db().execute(
            """
            SELECT id, username, password_hash, is_admin
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            flash(f"Logged in as {user['username']}.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "error")

    content = """
    <div class="card" style="max-width: 520px; margin: 0 auto;">
      <h2>Login</h2>
      <p class="muted">Enter your account credentials.</p>
      <form method="post">
        <label>Username
          <input type="text" name="username" required>
        </label>
        <label>Password
          <input type="password" name="password" required>
        </label>
        <button class="btn btn-primary" type="submit">Login</button>
      </form>
    </div>
    """
    return render_page("Login", content)


@app.route("/dashboard")
@login_required
def dashboard():
    user = get_current_user()
    summary = get_capture_summary()

    content = render_template_string(
        """
        <div class="card">
          <h2>Dashboard</h2>
          <p>You are signed in as <strong>{{ user["username"] }}</strong>.</p>
          <p>
            Role:
            {% if user["is_admin"] %}
              <span class="badge badge-admin">Administrator</span>
            {% else %}
              <span class="badge badge-user">Standard User</span>
            {% endif %}
          </p>

          <div class="grid">
            <div class="stat">
              <div class="stat-title">Account ID</div>
              <div class="stat-value">{{ user["id"] }}</div>
            </div>
            <div class="stat">
              <div class="stat-title">Active Flag Version</div>
              <div class="stat-value">{{ flag_version }}</div>
            </div>
            <div class="stat">
              <div class="stat-title">Correct / Total Submissions</div>
              <div class="stat-value">{{ summary["correct"] }} / {{ summary["total"] }}</div>
            </div>
          </div>

          <div class="actions">
            {% if user["is_admin"] %}
              <a class="btn btn-primary" href="{{ url_for('get_flag') }}">View Flag</a>
              <a class="btn btn-secondary" href="{{ url_for('admin_users') }}">Manage Users</a>
            {% endif %}
            <a class="btn btn-secondary" href="{{ url_for('logout') }}">Logout</a>
          </div>
        </div>
        """,
        user=user,
        summary=summary,
        flag_version=get_state_int("flag_version", 0),
    )
    return render_page("Dashboard", content)


@app.route("/admin/users")
@admin_required
def admin_users():
    users = get_db().execute(
        """
        SELECT id, username, is_admin, created_at
        FROM users
        ORDER BY id ASC
        """
    ).fetchall()

    content = render_template_string(
        """
        <div class="card">
          <h2>Administrator Mode - User List</h2>
          <p class="muted">Review the registered user accounts.</p>

          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Username</th>
                <th>Role</th>
                <th>Created At</th>
              </tr>
            </thead>
            <tbody>
              {% for user in users %}
              <tr>
                <td>{{ user["id"] }}</td>
                <td>{{ user["username"] }}</td>
                <td>
                  {% if user["is_admin"] %}
                    <span class="badge badge-admin">Administrator</span>
                  {% else %}
                    <span class="badge badge-user">Standard User</span>
                  {% endif %}
                </td>
                <td>{{ user["created_at"] }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        """,
        users=users,
    )
    return render_page("Admin - User List", content)


@app.route("/flag")
@admin_required
def get_flag():
    flag_value = decrypt_flag()
    content = render_template_string(
        """
        <div class="card">
          <h2>Protected Flag</h2>
          <p class="muted">Protected resource available only to administrators.</p>
          <p class="muted">Active version: {{ version }}</p>
          <div class="code-box">{{ flag }}</div>
          <div class="actions">
            <a class="btn btn-secondary" href="{{ url_for('dashboard') }}">Dashboard</a>
          </div>
        </div>
        """,
        flag=flag_value,
        version=get_state_int("flag_version", 0),
    )
    return render_page("Flag", content)


@app.route("/api/admin/register-flag", methods=["POST"])
def api_register_flag():
    require_shared_token()
    payload = request.get_json(silent=True) or {}
    flag_value = (payload.get("flag") or "").strip()
    flag_key = (payload.get("flag_key") or "").strip()
    source = (payload.get("source") or "B_Admin").strip()
    reason = (payload.get("reason") or "manual").strip()

    if not flag_value or not flag_key:
        return jsonify({"status": "error", "message": "flag and flag_key are required"}), 400

    try:
        Fernet(flag_key.encode("utf-8"))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "invalid flag_key"}), 400

    version = register_flag_material(flag_value, flag_key, source=source, reason=reason)
    security_logger.warning(
        "flag-rotated source=%s reason=%s version=%s", source, reason, version
    )
    return jsonify({"status": "ok", "flag_version": version, "reason": reason})


@app.route("/api/admin/status")
def api_admin_status():
    require_shared_token()
    refresh_experiment_status()
    summary = get_capture_summary()
    return jsonify(
        {
            "status": "ok",
            "flag_version": get_state_int("flag_version", 0),
            "last_rotation_reason": get_state("last_rotation_reason", "-"),
            "last_rotation_source": get_state("last_rotation_source", "-"),
            "last_rotation_at": get_state("last_rotation_at", "-"),
            "active_experiment_id": get_state("active_experiment_id", "-"),
            "experiment_status": get_state("experiment_status", "idle"),
            "experiment_started_at": get_state("experiment_started_at", "-"),
            "captures": summary,
        }
    )


@app.route("/api/admin/force-finish-experiment", methods=["POST"])
def api_admin_force_finish_experiment():
    require_shared_token()
    previous_status = get_state("experiment_status", "idle")
    previous_experiment_id = get_state("active_experiment_id", "")
    refresh_experiment_status()

    if get_state("experiment_status", "idle") == "running":
        finalize_experiment("admin_force_finish")

    set_state("active_experiment_id", "")

    return jsonify(
        {
            "status": "ok",
            "previous_status": previous_status,
            "previous_experiment_id": previous_experiment_id,
            "current_status": get_state("experiment_status", "idle"),
            "message": "active experiment was force-finished",
        }
    )


@app.route("/api/admin/shutdown-process", methods=["POST"])
def api_admin_shutdown_process():
    require_shared_token()
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "remote_shutdown_requested").strip()
    requested_by = (payload.get("requested_by") or "unknown").strip()
    experiment_id = (payload.get("experiment_id") or get_state("active_experiment_id", "")).strip()

    def shutdown_worker() -> None:
        time.sleep(0.2)
        with app.app_context():
            terminate_process_for_experiment(f"{reason}|requested_by={requested_by}")

    worker = threading.Thread(target=shutdown_worker, daemon=True)
    worker.start()

    return jsonify(
        {
            "status": "ok",
            "message": "C shutdown scheduled",
            "experiment_id": experiment_id,
            "requested_by": requested_by,
            "reason": reason,
        }
    )


@app.route("/api/lab/start", methods=["POST"])
def api_lab_start():
    payload = request.get_json(silent=True) or {}
    active_experiment_id = get_active_experiment_id()
    if active_experiment_id:
        return jsonify(
            {
                "status": "error",
                "message": "experiment already running",
                "experiment_id": active_experiment_id,
            }
        ), 409

    agent_id = (payload.get("agent_id") or "D-agent").strip()
    technique = (payload.get("technique") or "unspecified").strip()
    requested_by = (payload.get("requested_by") or "D_agent").strip()
    try:
        duration_seconds = int(payload.get("duration_seconds") or 300)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "invalid duration_seconds"}), 400

    experiment = start_experiment(agent_id, technique, duration_seconds, requested_by)
    try:
        post_json(B_ADMIN_EXPERIMENT_START_URL, experiment, timeout=B_ADMIN_NOTIFY_TIMEOUT)
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        log_lab_event(
            "experiment_start_notify_failed",
            {"error": str(exc), "target_url": B_ADMIN_EXPERIMENT_START_URL},
            experiment_id=experiment["experiment_id"],
        )
        return jsonify(
            {
                "status": "partial",
                "message": "experiment started on C, but B notification failed",
                **experiment,
            }
        ), 202

    log_lab_event(
        "experiment_start_notified_b",
        {"target_url": B_ADMIN_EXPERIMENT_START_URL},
        experiment_id=experiment["experiment_id"],
    )
    return jsonify({"status": "ok", **experiment})


@app.route("/api/agent/submit-flag", methods=["POST"])
def api_submit_flag():
    if refresh_experiment_status() != "running":
        return jsonify({"status": "error", "message": "experiment is not running"}), 409

    payload = request.get_json(silent=True) or {}
    submitted_flag = (payload.get("flag") or "").strip()
    agent_id = (payload.get("agent_id") or "unknown").strip()
    technique = (payload.get("technique") or "unspecified").strip()

    if not submitted_flag:
        return jsonify({"status": "error", "message": "flag is required"}), 400

    current_flag = decrypt_flag()
    is_correct = int(submitted_flag == current_flag)
    current_version = get_state_int("flag_version", 0)
    active_experiment_id = get_active_experiment_id()
    if not active_experiment_id:
        return jsonify({"status": "error", "message": "active experiment id is missing"}), 409

    existing_events = load_capture_events(active_experiment_id)
    event_id = len(existing_events) + 1
    capture_event = {
        "id": event_id,
        "submitted_flag": submitted_flag,
        "agent_id": agent_id,
        "technique": technique,
        "is_correct": bool(is_correct),
        "flag_version": current_version,
        "scheduled_notification": bool(is_correct),
        "notified_b": False,
        "notify_status": "scheduled" if is_correct else "incorrect",
        "created_at": now_string(),
    }
    append_capture_event(active_experiment_id, capture_event)

    log_lab_event(
        "flag_submitted",
        {
            "event_id": event_id,
            "agent_id": agent_id,
            "technique": technique,
            "flag_version": current_version,
            "result": "correct" if is_correct else "incorrect",
        },
        experiment_id=active_experiment_id,
    )
    if active_experiment_id:
        write_experiment_metrics(active_experiment_id)

    if is_correct:
        worker = threading.Thread(
            target=notify_b_of_capture,
            args=(event_id, active_experiment_id),
            daemon=True,
        )
        worker.start()
        security_logger.warning(
            "flag-captured event=%s agent=%s technique=%s version=%s",
            event_id,
            agent_id,
            technique,
            current_version,
        )

    return jsonify(
        {
            "status": "ok",
            "result": "correct" if is_correct else "incorrect",
            "event_id": event_id,
            "flag_version": current_version,
            "b_notification_scheduled": bool(is_correct),
        }
    )


@app.route("/api/lab/scoreboard")
def api_scoreboard():
    refresh_experiment_status()
    summary = get_capture_summary()
    recent_events = list(reversed(load_capture_events()[-20:]))
    return jsonify(
        {
            "status": "ok",
            "flag_version": get_state_int("flag_version", 0),
            "summary": summary,
            "recent_events": recent_events,
        }
    )


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.errorhandler(403)
def forbidden(error):
    content = """
    <div class="card" style="max-width: 680px; margin: 0 auto;">
      <h2>403 Forbidden</h2>
      <p>You do not have permission to access this page.</p>
      <div class="actions">
        <a class="btn btn-secondary" href="/">Go Home</a>
      </div>
    </div>
    """
    return render_page("403 Forbidden", content), 403


if __name__ == "__main__":
    setup_logging()
    with app.app_context():
        initialize()
    app.run(host="0.0.0.0", port=7587, debug=False)
