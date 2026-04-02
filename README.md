# Download Sentinel
Internal download testing & monitoring platform. Tracks whether installer files get blocked, warned, or downloaded successfully across browsers and CLI methods.

## What it does
- Accepts a list of download URLs via API or dashboard UI
- Downloads each file using Edge, Chrome, Firefox, curl, and PowerShell
- Classifies outcomes: success, SmartScreen warning, browser block, Defender block, etc.
- Captures screenshots at each step
- Reports results to Slack and a web dashboard
- Supports scheduled/recurring jobs (interval or cron)
- Parallel test execution via Azure VM pool with clean snapshot restore per run

## Stack
- **Backend**: FastAPI + SQLAlchemy + SQLite (dev) / PostgreSQL (prod)
- **Worker**: Playwright (browser automation) + Win32 APIs (screenshot capture)
- **Frontend**: React + Vite + Tailwind CSS
- **Scheduler**: APScheduler (async, fire & forget)
- **Reporting**: Slack webhooks

---

## Quick Start (Local / Dev Mode)

No VM needed — runs tasks locally and simulates outcomes if Playwright isn't installed.

```bash
# Backend
cd backend
pip install -r requirements.txt
pip install python-dotenv
python -m uvicorn main:app --reload --port 8000 --host 0.0.0.0

# Frontend
cd frontend
npm install
npm run dev
```

Frontend: http://localhost:5173
Backend: http://localhost:8000

---

## VM Setup (Production Mode)

In production, tasks run on a Windows VM (AVD or Azure VM). The VM runs a worker agent that receives tasks, executes downloads, and posts results back to the backend.

### Architecture

```
Main Machine
    ├── FastAPI backend     :8000
    ├── React frontend      :5173
    └── ngrok               → exposes :8000 publicly

Windows VM (Azure/AVD)
    ├── worker_agent.py     :5000
    └── ngrok               → exposes :5000 publicly
```

---

### Step 1 — Main Machine: expose backend via ngrok

Download ngrok from https://ngrok.com/download, then:

```bash
ngrok http 8000
```

Copy the URL e.g. `https://xxxx.ngrok-free.app`

Create `backend/.env`:
```env
CALLBACK_BASE_URL=https://xxxx.ngrok-free.app
VM_AGENT_HANDLES_RESTORE=true
DATABASE_URL=sqlite:///./download_monitor.db
SLACK_WEBHOOK_URL=
DASHBOARD_URL=http://localhost:5173
```

Restart backend:
```bash
python -m uvicorn main:app --reload --port 8000 --host 0.0.0.0
```

---

### Step 2 — VM: install Python 3.11

> ⚠️ Must be Python 3.11. Playwright does not support Python 3.14.

```bash
winget install Python.Python.3.11
```

---

### Step 3 — VM: set up worker folder

Create `C:\Worker\` (no spaces in path — important for curl/PowerShell downloads):

```bash
mkdir C:\Worker
```

Copy `worker_agent.py` and `models.py` into `C:\Worker\`

---

### Step 4 — VM: install dependencies

```bash
py -3.11 -m pip install fastapi uvicorn playwright requests pyautogui pillow
```

Install browsers:
```bash
py -3.11 -m playwright install msedge
py -3.11 -m playwright install chromium
py -3.11 -m playwright install firefox
winget install Google.Chrome
```

Install Visual C++ runtime (needed for greenlet/Playwright):
```bash
winget install Microsoft.VCRedist.2015+.x64
```

---

### Step 5 — VM: start ngrok

Download ngrok from https://ngrok.com/download → place `ngrok.exe` in `C:\Worker\`

Authenticate (one time):
```bash
ngrok config add-authtoken <your-token>
```

Start tunnel:
```bash
ngrok http 5000
```

Copy the forwarding URL e.g. `https://yyyy.ngrok-free.app`

---

### Step 6 — VM: start the agent

Open a new CMD window:

```bash
set AGENT_BASE_URL=https://yyyy.ngrok-free.app
py -3.11 C:\Worker\worker_agent.py
```

You should see:
```
INFO: Uvicorn running on http://0.0.0.0:5000
```

Test it:
```bash
curl http://localhost:5000/health
```

---

### Step 7 — Dashboard: add VM to pool

Go to **VM Pool** tab → **+ Add VM**:

| Field | Value |
|---|---|
| VM name | `win11-pro-2` |
| Agent URL | `https://yyyy.ngrok-free.app` (VM ngrok URL) |
| Agent token | leave blank |
| Azure resource group | `rg-avd-prod` |
| Azure VM name | `win11-pro-2` |
| Snapshot name | leave blank for testing |

---

## Running a Test

1. Go to **Test Runs** → **New Run**
2. Paste a download URL e.g. `https://www.7-zip.org/a/7z2600.exe`
3. Select browsers
4. Click **Start Run**

The VM agent will open each browser, download the file, capture screenshots, detect blocks, and report results back to the dashboard.

---

## API

### Runs
- `POST /api/runs` — Start a new test run
- `GET /api/runs` — List all runs (filter by brand, status, date)
- `GET /api/runs/{id}` — Run detail with all task results
- `POST /api/runs/{id}/retry` — Retry failed tasks
- `GET /api/runs/{id}/export` — Export results as CSV

### Brands
- `GET /api/brands` — List brands
- `POST /api/brands` — Create a brand

### Scheduled Jobs
- `POST /api/jobs` — Create a recurring job
- `GET /api/jobs` — List all jobs
- `GET /api/jobs/{id}` — Job detail + recent runs
- `PATCH /api/jobs/{id}` — Update job
- `DELETE /api/jobs/{id}` — Delete job
- `POST /api/jobs/{id}/run` — Trigger now
- `POST /api/jobs/{id}/toggle` — Enable/disable

### VM Pool
- `POST /api/vms` — Register a VM
- `GET /api/vms` — List VMs
- `DELETE /api/vms/{id}` — Remove VM
- `POST /api/vms/{id}/heartbeat` — Agent keepalive

### WebSocket
- `WS /ws` — Global live updates
- `WS /ws/{run_id}` — Run-scoped live updates

---

## Outcome Categories

| Outcome | Description |
|---|---|
| `success_executed` | Downloaded and executed without issues |
| `success_smartscreen` | SmartScreen popup appeared |
| `browser_blocked` | Browser hard-blocked the download |
| `browser_warned_dangerous` | Browser warned file is dangerous |
| `browser_warned_uncommon` | Browser warned file is uncommon |
| `defender_blocked` | Windows Defender removed the file |
| `download_failed` | Network or HTTP error |
| `timeout` | Took too long |

---

## Slack Integration

1. Go to https://api.slack.com/apps → **Create New App** → **From scratch**
2. Name it `Download Sentinel`, pick workspace
3. Go to **Incoming Webhooks** → toggle ON
4. **Add New Webhook** → pick channel → **Allow**
5. Copy the webhook URL
6. In dashboard → **Settings** → paste webhook URL → **Save** → **Send test**

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./download_monitor.db` | DB connection |
| `SLACK_WEBHOOK_URL` | — | Slack webhook |
| `DASHBOARD_URL` | `http://localhost:5173` | Used in Slack links |
| `CALLBACK_BASE_URL` | `http://localhost:8000` | VM posts results here — **set to ngrok URL in production** |
| `VM_AGENT_HANDLES_RESTORE` | `true` | Agent handles snapshot restore |
| `AZURE_SUBSCRIPTION_ID` | — | For real Azure snapshot restore |
| `AGENT_BASE_URL` | `http://localhost:5000` | Set on VM to its ngrok URL for screenshots |

---

## Troubleshooting

**VM stuck at "waiting for vm"**

Reset stuck VMs via Python shell on main machine:
```python
from database import SessionLocal
from models import VMPool, VMStatus
db = SessionLocal()
for vm in db.query(VMPool).all():
    vm.status = VMStatus.IDLE.value
    vm.current_run_id = None
db.commit()
```

**Playwright greenlet DLL error**

Use Python 3.11, not 3.14:
```bash
py -3.11 worker_agent.py
```

Also install Visual C++ runtime:
```bash
winget install Microsoft.VCRedist.2015+.x64
```

**Callbacks timing out from VM**

Set `CALLBACK_BASE_URL` to your main machine's ngrok URL (not `localhost`) and restart backend.

**Screenshots not loading**

Set `AGENT_BASE_URL` to the VM's ngrok URL before starting the agent:
```bash
set AGENT_BASE_URL=https://yyyy.ngrok-free.app
py -3.11 worker_agent.py
```

**curl/PowerShell "file not found"**

Avoid spaces in the worker folder path. Use `C:\Worker\` not `C:\Users\...\OneDrive\Desktop\Worker\`.
