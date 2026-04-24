from __future__ import annotations


TARGET_HOST = "192.168.0.17"
TARGET_PORT = "7587"

LLM_BASE_URL = "" # Fill in your LLM endpoint here
LLM_TIMEOUT = 120

AGENT_ID = "D-1"
TECHNIQUE = "monitor-driven-reactive"
REQUESTED_BY = "D_agent"
MONITOR_SUDO_PASSWORD = "kali"
BC_SHARED_TOKEN = "change-this-shared-token"
B_ADMIN_BASE_URL = "http://192.168.0.6:8686"




EXPERIMENT_DURATION_SECONDS = 300
C_SERVER_BASE_URL = "http://192.168.0.17:7587"
START_TIMEOUT = 10
SUBMIT_TIMEOUT = 10
AUTO_RESET_EXPERIMENT_ON_409 = True
AUTO_SHUTDOWN_RELATED_SERVERS_ON_STOP = True
AUTO_GENERATE_EXPERIMENT_REPORT = True
LOOP_SLEEP_SECONDS = 2.0
LOOP_MAX_RETRIES_PER_DETAIL = 3
DETAIL_PLAN_MAX_SECONDS = 45
IDLE_POLL_SECONDS = 2.0
MAX_ACTIONS_PER_TRIGGER = 1
MONITOR_SIGNAL_SELECTION_LIMIT = 12
MAX_CORRECT_FLAGS_PER_EXPERIMENT = 10
REPORT_TOP_N = 8

ENABLE_TOOLS = True
ENABLE_SKILLS = True
ENABLE_PLANNING = False
ENABLE_MEMORY = True
ENABLE_SUMMARIZATION = True
ENABLE_LOOP = True
MONITOR_ONLY_MODE = False

# Ablation toggles
ENABLE_SUBMITTED_FLAG_DEDUP = True

ALLOWED_TOOLS = [
    "start_experiment",
    "nmap",
    "curl",
    "tshark",
    "submit_flag",
]

ALLOWED_SKILLS = [
    "reconnaissance",
    "experiment_start",
    "http_enumeration",
    "admin_traffic_interception",
    "mitm_with_bettercap",
    "flag_submission",
    "trigger_prioritization",
]

MONITOR_USE_TCPDUMP = True
# Monitoring / MITM
ENABLE_MONITORING = True

MONITOR_INTERFACE = "eth0"

MONITOR_TARGETS = [
    "192.168.0.6",   # B
    "192.168.0.17",  # C
]

MONITOR_PORT = "7587"

MONITOR_POLL_INTERVAL_SECONDS = 3.0

MONITOR_FLAG_REGEX = r"flag\{[^}]+\}"
MONITOR_TRIGGER_EVENT_TYPES = [
    "flag_observed",
    "session_cookie_observed",
    "session_cookie_used",
    "http_redirect",
]

MONITOR_USE_BETTERCAP = True

MONITOR_BETTERCAP_INTERNAL = True

MONITOR_VERBOSE = True
