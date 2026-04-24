# BadHarness: A CTF-Based Simulation for Evaluating LLM Agent Capabilities


This repository contains a monitor-driven reactive agent framework for a controlled cyber range experiment.
The system includes three components:

- `C_Server`: target and scoring server
- `B_Admin`: admin-side traffic generator/controller
- `D_agent`: monitoring and flag-submission agent

## Requirements

- Python 3.10+
- Linux
- `tshark`
- `bettercap`

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r C_Server/requirements.txt
python3 -m pip install -r B_Admin/requirements.txt
python3 -m pip install requests matplotlib
```

## Configuration

Edit the agent settings before running:

```bash
vim D_agent/configs/config.py
```

Set at least:

- `LLM_BASE_URL`
- `C_SERVER_BASE_URL`
- `B_ADMIN_BASE_URL`
- `MONITOR_INTERFACE`

## Run

Start each component in a separate terminal.

### 1. Start C_Server

```bash
cd C_Server
export BC_SHARED_TOKEN='change-this-shared-token'
export B_ADMIN_NOTIFY_URL='http://192.168.0.6:8686/api/capture-notify'
export B_ADMIN_EXPERIMENT_START_URL='http://192.168.0.6:8686/api/experiment/start'
export ADMIN_USERNAME='admin'
export ADMIN_PASSWORD='ChangeThisAdminPassword123!'
python3 app.py
```

### 2. Start B_Admin

```bash
cd B_Admin
export BC_SHARED_TOKEN='change-this-shared-token'
export C_SERVER_BASE_URL='http://192.168.0.17:7587'
export ADMIN_USERNAME='admin'
export ADMIN_PASSWORD='ChangeThisAdminPassword123!'
export POLL_INTERVAL_SECONDS='30'
python3 app.py
```

### 3. Start D_agent

```bash
cd D_agent
python3 run.py
```

## Output

Each experiment creates a new folder under:

```bash
D_agent/experiments/<experiment_id>/
```

This folder contains runtime logs, memory files, metadata, CSV summaries, JSON reports, and PNG figures.
