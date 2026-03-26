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

## Quick Start
```bash
# Backend
cd backend
pip install -r requirements.txt
python -m playwright install chromium firefox msedge
python -m uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

## API
### Runs
- `POST /api/runs` — Start a new test run with URLs and browser list
- `GET /api/runs` — List all runs (filterable by brand, status, date_from, date_to)
- `GET /api/runs/{id}` — Get run details with all task results

### Brands
- `GET /api/brands` — List brands
- `POST /api/brands` — Create a brand

### Scheduled Jobs
- `POST /api/jobs` — Create a recurring job (interval or cron)
- `GET /api/jobs` — List all scheduled jobs
- `GET /api/jobs/{id}` — Job detail + recent run history
- `PATCH /api/jobs/{id}` — Update job schedule or config
- `DELETE /api/jobs/{id}` — Delete a job
- `POST /api/jobs/{id}/run` — Trigger a job manually right now
- `POST /api/jobs/{id}/toggle` — Enable or disable a job

### VM Pool
- `POST /api/vms` — Register a Windows VM into the pool
- `GET /api/vms` — List all VMs and their status
- `DELETE /api/vms/{id}` — Remove a VM from the pool
- `POST /api/vms/{id}/heartbeat` — VM agent calls this to report it's alive

### WebSocket
- `WS /ws` — Global live updates (run created, task updates, status changes)
- `WS /ws/{run_id}` — Live updates scoped to a specific run

## Outcome Categories
| Outcome | Description |
|---------|-------------|
| success_executed | Downloaded and executed without issues |
| success_smartscreen | Downloaded but SmartScreen popup appeared |
| browser_blocked | Browser hard-blocked the download |
| browser_warned_dangerous | Browser warned file might be dangerous |
| browser_warned_uncommon | Browser warned file is not commonly downloaded |
| defender_blocked | Windows Defender removed/blocked the file |
| download_failed | Network or HTTP error |
| timeout | Download took too long |

## Slack Integration

### Setting up a Webhook

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps)
2. Click **"Create New App"** → **"From scratch"**
3. Name it `Download Sentinel`, pick your workspace
4. In the left sidebar, click **"Incoming Webhooks"**
5. Toggle **"Activate Incoming Webhooks"** to ON
6. Click **"Add New Webhook to Workspace"**
7. Pick the channel where you want reports (e.g. `#download-monitoring`)
8. Click **Allow**
9. Copy the webhook URL — it looks like:
   ```
   https://hooks.slack.com/services/T.../B.../xxx...
   ```

### Connecting to the Dashboard

1. Open the dashboard at `http://localhost:5173`
2. Click **Settings** (top right)
3. Paste the webhook URL into the **Slack webhook** field
4. Click **Save**
5. Click **Send test** to verify it's working — you should see a message in your Slack channel

### What gets reported
After every test run completes, Sentinel sends a summary to Slack with:
- Pass / Warn / Fail counts
- Breakdown per outcome category
- Direct link back to the dashboard run detail

## Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./download_monitor.db` | DB connection string |
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook URL |
| `DASHBOARD_URL` | `http://localhost:5173` | Used in Slack report links |
| `CALLBACK_BASE_URL` | `http://localhost:8000` | VM agent POSTs results here |
| `AZURE_SUBSCRIPTION_ID` | — | For Azure VM snapshot restore |
| `VM_AGENT_HANDLES_RESTORE` | `true` | If true, VM agent does its own restore |
