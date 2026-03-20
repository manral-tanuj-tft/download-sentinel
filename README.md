# Download Sentinel

Internal download testing & monitoring platform. Tracks whether installer files get blocked, warned, or downloaded successfully across browsers and CLI methods.

## What it does

- Accepts a list of download URLs via API
- Downloads each file using Edge, Chrome, Firefox, curl, and PowerShell
- Classifies outcomes: success, SmartScreen warning, browser block, Defender block, etc.
- Captures screenshots at each step
- Reports results to Slack and a web dashboard

## Stack

- **Backend**: FastAPI + SQLAlchemy + SQLite (dev) / PostgreSQL (prod)
- **Worker**: Playwright (browser automation) + Win32 APIs (screenshot capture)
- **Frontend**: React + Vite + Tailwind CSS
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

- `POST /api/runs` — Start a new test run with URLs and browser list
- `GET /api/runs` — List all runs (filterable by brand, status)
- `GET /api/runs/{id}` — Get run details with all task results
- `GET /api/brands` — List brands
- `POST /api/brands` — Create a brand
- `WS /ws` — WebSocket for live progress updates

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