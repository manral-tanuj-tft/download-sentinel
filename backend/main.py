"""
Download Monitor — FastAPI Backend (Dev mode: SQLite + sync tasks)
Run: uvicorn main:app --reload --port 8000
"""
import os, json, threading
from uuid import UUID, uuid4
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, select, func, event
from sqlalchemy.orm import Session, sessionmaker

from fastapi.staticfiles import StaticFiles
from pathlib import Path

from models import (
    Base, Brand, TestRun, DownloadTask, TaskScreenshot,
    DownloadOutcome, BrowserType
)

# Create screenshots dir
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# ── Config ── SQLite for dev, swap to postgres in prod
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./download_monitor.db")
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:5173")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
# Enable WAL mode for SQLite (better concurrency)
if "sqlite" in DATABASE_URL:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="Download Monitor API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# Serve screenshots as static files
app.mount("/screenshots", StaticFiles(directory=str(SCREENSHOT_DIR)), name="screenshots")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── WebSocket Connection Manager ──
class ConnectionManager:
    def __init__(self):
        self.active: dict[str, list[WebSocket]] = {}

    async def connect(self, ws: WebSocket, run_id: str):
        await ws.accept()
        self.active.setdefault(run_id, []).append(ws)

    def disconnect(self, ws: WebSocket, run_id: str):
        if run_id in self.active:
            self.active[run_id] = [w for w in self.active[run_id] if w != ws]

    async def broadcast(self, run_id: str, data: dict):
        for ws in self.active.get(run_id, []):
            try:
                await ws.send_json(data)
            except Exception:
                pass

    async def broadcast_all(self, data: dict):
        for conns in self.active.values():
            for ws in conns:
                try:
                    await ws.send_json(data)
                except Exception:
                    pass

mgr = ConnectionManager()


# ── Pydantic Schemas ──
class BrandCreate(BaseModel):
    name: str
    slug: str

class TestRunCreate(BaseModel):
    urls: list[str]
    brand_id: Optional[str] = None
    name: Optional[str] = None
    browsers: list[BrowserType] = [
        BrowserType.EDGE, BrowserType.CHROME, BrowserType.FIREFOX,
        BrowserType.CURL, BrowserType.POWERSHELL
    ]

class TaskUpdate(BaseModel):
    outcome: DownloadOutcome
    browser_message: Optional[str] = None
    defender_message: Optional[str] = None
    screenshot_url: Optional[str] = None
    http_status: Optional[int] = None
    error_details: Optional[str] = None

class ScreenshotAdd(BaseModel):
    step: str
    s3_url: str
    ocr_text: Optional[str] = None


# ── Brand endpoints ──
@app.post("/api/brands")
def create_brand(data: BrandCreate, db: Session = Depends(get_db)):
    brand = Brand(name=data.name, slug=data.slug)
    db.add(brand)
    db.commit()
    db.refresh(brand)
    return {"id": str(brand.id), "name": brand.name, "slug": brand.slug}

@app.get("/api/brands")
def list_brands(db: Session = Depends(get_db)):
    brands = db.execute(select(Brand).order_by(Brand.name)).scalars().all()
    return [{"id": str(b.id), "name": b.name, "slug": b.slug} for b in brands]


# ── Test Run endpoints ──
@app.post("/api/runs")
async def create_test_run(data: TestRunCreate, db: Session = Depends(get_db)):
    run = TestRun(
        name=data.name,
        brand_id=data.brand_id,
        status="queued",
        total_tasks=len(data.urls) * len(data.browsers),
    )
    db.add(run)
    db.flush()

    tasks = []
    for url in data.urls:
        for browser in data.browsers:
            task = DownloadTask(
                test_run_id=run.id,
                url=url,
                browser=browser,
                outcome=DownloadOutcome.PENDING,
            )
            db.add(task)
            tasks.append(task)

    db.commit()
    run_id_str = str(run.id)

    await mgr.broadcast_all({
        "type": "run_created",
        "run_id": run_id_str,
        "total_tasks": run.total_tasks,
    })

    # Run tasks in background thread (no Celery needed for dev)
    threading.Thread(
        target=_execute_run_sync,
        args=(run_id_str,),
        daemon=True,
    ).start()

    return {
        "run_id": run_id_str,
        "total_tasks": run.total_tasks,
        "status": run.status,
    }


def _execute_run_sync(run_id: str):
    """Background thread that runs all download tasks sequentially."""
    db = SessionLocal()
    try:
        run = db.get(TestRun, run_id)
        if not run:
            return
        run.status = "running"
        db.commit()

        tasks = db.execute(
            select(DownloadTask).where(DownloadTask.test_run_id == run_id)
        ).scalars().all()

        for task in tasks:
            task.outcome = "running"
            task.started_at = datetime.utcnow()
            db.commit()

            try:
                # Try real browser download if playwright is available
                from worker_agent import BrowserDownloader, CLIDownloader
                browser = task.browser  # already a string
                if browser in ("edge", "chrome", "firefox"):
                    dl = BrowserDownloader(browser=browser, task_id=str(task.id))
                    result = dl.execute(task.url)
                else:
                    dl = CLIDownloader(method=browser, task_id=str(task.id))
                    result = dl.execute(task.url)

                outcome = result["outcome"]
                task.outcome = outcome.value if hasattr(outcome, 'value') else outcome
                task.browser_message = result.get("browser_message")
                task.defender_message = result.get("defender_message")
                task.screenshot_url = result.get("screenshot_url")
                task.http_status = result.get("http_status")
                task.error_details = result.get("error_details")
                task.file_name = result.get("file_name")

            except ImportError:
                # Playwright not installed — simulate for dev
                import time, random
                time.sleep(1)
                outcomes = [
                    DownloadOutcome.SUCCESS_EXECUTED,
                    DownloadOutcome.SUCCESS_SMARTSCREEN,
                    DownloadOutcome.BROWSER_BLOCKED,
                    DownloadOutcome.BROWSER_WARNED_DANGEROUS,
                    DownloadOutcome.DEFENDER_BLOCKED,
                ]
                task.outcome = random.choice(outcomes)
                task.browser_message = f"[simulated] {task.outcome.value}"

            except Exception as e:
                task.outcome = DownloadOutcome.DOWNLOAD_FAILED
                task.error_details = str(e)

            task.finished_at = datetime.utcnow()
            run.completed_tasks += 1
            db.commit()

        run.status = "completed"
        run.completed_at = datetime.utcnow()
        db.commit()

        # Notify slack
        if SLACK_WEBHOOK:
            _send_slack_notification(run_id, db)

    except Exception as e:
        print(f"Run {run_id} failed: {e}")
        try:
            run = db.get(TestRun, run_id)
            if run:
                run.status = "failed"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _send_slack_notification(run_id: str, db: Session):
    """Send completion report to Slack."""
    import requests as req

    run = db.get(TestRun, run_id)
    if not run:
        return

    rows = db.execute(
        select(DownloadTask.outcome, func.count())
        .where(DownloadTask.test_run_id == run_id)
        .group_by(DownloadTask.outcome)
    ).all()

    emoji = {
        "success_executed": ":white_check_mark:",
        "success_smartscreen": ":warning:",
        "browser_blocked": ":no_entry:",
        "browser_warned_dangerous": ":rotating_light:",
        "browser_warned_uncommon": ":eyes:",
        "defender_blocked": ":shield:",
        "download_failed": ":x:",
        "timeout": ":hourglass:",
    }

    lines = [f'{emoji.get(str(o), "?")} *{str(o).replace("_"," ").title()}*: {c}' for o, c in rows]

    # Count pass/fail
    pass_count = sum(c for o, c in rows if str(o) in ("success_executed",))
    warn_count = sum(c for o, c in rows if str(o) in ("success_smartscreen", "browser_warned_dangerous", "browser_warned_uncommon"))
    fail_count = sum(c for o, c in rows if str(o) in ("browser_blocked", "defender_blocked", "download_failed", "timeout"))

    # Color the header based on results
    if fail_count > 0:
        header_emoji = ":red_circle:"
    elif warn_count > 0:
        header_emoji = ":large_yellow_circle:"
    else:
        header_emoji = ":large_green_circle:"

    run_name = run.name or f"Run #{run_id[:8]}"

    payload = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"{header_emoji} Download Sentinel — {run_name}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Total tasks:* {run.total_tasks}"},
                {"type": "mrkdwn", "text": f"*Pass:* {pass_count} | *Warn:* {warn_count} | *Fail:* {fail_count}"},
            ]},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "View Dashboard"}, "url": f"{DASHBOARD_URL}"}
            ]},
        ]
    }

    try:
        req.post(SLACK_WEBHOOK, json=payload, timeout=10)
        run.slack_notified = True
        db.commit()
    except Exception:
        pass


@app.get("/api/runs")
def list_runs(
    brand_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    q = select(TestRun).order_by(TestRun.created_at.desc())
    if brand_id:
        q = q.where(TestRun.brand_id == brand_id)
    if status:
        q = q.where(TestRun.status == status)
    runs = db.execute(q.limit(limit).offset(offset)).scalars().all()

    results = []
    for r in runs:
        outcome_counts = dict(
            db.execute(
                select(DownloadTask.outcome, func.count())
                .where(DownloadTask.test_run_id == r.id)
                .group_by(DownloadTask.outcome)
            ).all()
        )
        results.append({
            "id": str(r.id),
            "name": r.name,
            "brand_id": str(r.brand_id) if r.brand_id else None,
            "status": r.status,
            "total_tasks": r.total_tasks,
            "completed_tasks": r.completed_tasks,
            "outcome_summary": {str(k): v for k, v in outcome_counts.items()},
            "created_at": r.created_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        })
    return results


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(TestRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    tasks = db.execute(
        select(DownloadTask).where(DownloadTask.test_run_id == run_id)
        .order_by(DownloadTask.url, DownloadTask.browser)
    ).scalars().all()

    return {
        "id": str(run.id),
        "name": run.name,
        "status": run.status,
        "total_tasks": run.total_tasks,
        "completed_tasks": run.completed_tasks,
        "created_at": run.created_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "tasks": [
            {
                "id": str(t.id),
                "url": t.url,
                "file_name": t.file_name,
                "browser": t.browser,
                "outcome": t.outcome,
                "screenshot_url": t.screenshot_url,
                "browser_message": t.browser_message,
                "defender_message": t.defender_message,
                "http_status": t.http_status,
                "error_details": t.error_details,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "finished_at": t.finished_at.isoformat() if t.finished_at else None,
                "screenshots": [
                    {
                        "step": s.step,
                        "s3_url": s.s3_url,
                        "ocr_text": s.ocr_text,
                        "captured_at": s.captured_at.isoformat(),
                    }
                    for s in t.screenshots
                ],
            }
            for t in tasks
        ],
    }


# ── Task update (called by worker agent) ──
@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: str, data: TaskUpdate, db: Session = Depends(get_db)):
    task = db.get(DownloadTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    task.outcome = data.outcome
    task.browser_message = data.browser_message or task.browser_message
    task.defender_message = data.defender_message or task.defender_message
    task.screenshot_url = data.screenshot_url or task.screenshot_url
    task.http_status = data.http_status or task.http_status
    task.error_details = data.error_details or task.error_details

    if data.outcome not in (DownloadOutcome.PENDING, DownloadOutcome.RUNNING):
        task.finished_at = datetime.utcnow()
        run = db.get(TestRun, task.test_run_id)
        run.completed_tasks += 1
        if run.completed_tasks >= run.total_tasks:
            run.status = "completed"
            run.completed_at = datetime.utcnow()

    db.commit()

    await mgr.broadcast(str(task.test_run_id), {
        "type": "task_update",
        "task_id": str(task.id),
        "url": task.url,
        "browser": task.browser.value,
        "outcome": data.outcome.value,
        "browser_message": data.browser_message,
        "screenshot_url": data.screenshot_url,
    })

    return {"status": "updated"}


# ── Add screenshot to task ──
@app.post("/api/tasks/{task_id}/screenshots")
def add_screenshot(task_id: str, data: ScreenshotAdd, db: Session = Depends(get_db)):
    task = db.get(DownloadTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    ss = TaskScreenshot(
        task_id=task.id, step=data.step,
        s3_url=data.s3_url, ocr_text=data.ocr_text,
    )
    db.add(ss)
    db.commit()
    return {"status": "added", "screenshot_id": str(ss.id)}


# ── Settings endpoint ──
@app.get("/api/settings")
def get_settings():
    return {
        "slack_webhook_configured": bool(SLACK_WEBHOOK),
        "dashboard_url": DASHBOARD_URL,
    }

@app.post("/api/settings/slack")
def set_slack_webhook(data: dict):
    global SLACK_WEBHOOK
    SLACK_WEBHOOK = data.get("webhook_url", "")
    return {"status": "updated", "configured": bool(SLACK_WEBHOOK)}

@app.post("/api/settings/slack/test")
def test_slack():
    if not SLACK_WEBHOOK:
        raise HTTPException(400, "Slack webhook not configured")
    import requests as req
    payload = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Download Sentinel — Test Notification"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": ":white_check_mark: Slack integration is working!"}},
        ]
    }
    try:
        resp = req.post(SLACK_WEBHOOK, json=payload, timeout=10)
        return {"status": resp.status_code, "ok": resp.status_code == 200}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── List screenshots for a task (from disk) ──
@app.get("/api/tasks/{task_id}/screenshots")
def list_task_screenshots(task_id: str):
    ss_dir = SCREENSHOT_DIR / task_id
    if not ss_dir.exists():
        return []
    files = sorted(ss_dir.glob("*.png"))
    return [
        {
            "step": f.stem,
            "s3_url": f"/screenshots/{task_id}/{f.name}",
            "ocr_text": None,
            "captured_at": None,
        }
        for f in files
    ]


# ── WebSocket endpoints ──
@app.websocket("/ws/{run_id}")
async def ws_endpoint(ws: WebSocket, run_id: str):
    await mgr.connect(ws, run_id)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        mgr.disconnect(ws, run_id)


@app.websocket("/ws")
async def ws_global(ws: WebSocket):
    await mgr.connect(ws, "__global__")
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        mgr.disconnect(ws, "__global__")