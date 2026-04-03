"""
Download Sentinel — FastAPI Backend
Run: uvicorn main:app --reload --port 8000
"""

import os
import json
import asyncio
import threading
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, select, func, event
from sqlalchemy.orm import Session, sessionmaker
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from models import (
    Base, Brand, TestRun, DownloadTask, TaskScreenshot,
    DownloadOutcome, BrowserType, VMPool, VMStatus,
    ScheduledJob, ScheduleType, TestRunStatus
)

# ── Dirs ──────────────────────────────────────────────────────────────
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"

# ── Config ────────────────────────────────────────────────────────────
DATABASE_URL  = os.getenv("DATABASE_URL",       "sqlite:///./download_monitor.db")
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL",  "")
DASHBOARD_URL = os.getenv("DASHBOARD_URL",      "http://localhost:5173")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)

if "sqlite" in DATABASE_URL:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── WebSocket manager ─────────────────────────────────────────────────
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


# ── Lifespan ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    # Reset any stuck BUSY VMs on startup
    _db = SessionLocal()
    try:
        stuck = _db.query(VMPool).filter(VMPool.status == VMStatus.BUSY.value).all()
        for _vm in stuck:
            _vm.status = VMStatus.IDLE.value
            _vm.current_run_id = None
        if stuck:
            print(f"[startup] Reset {len(stuck)} stuck BUSY VMs to IDLE")
        _db.commit()
    finally:
        _db.close()

    import scheduler as sched
    sched.init(SessionLocal, ws_broadcast_fn=mgr.broadcast, local_runner=_execute_run_local)

    yield

    sched.shutdown()


app = FastAPI(title="Download Sentinel API", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.mount("/screenshots", StaticFiles(directory=str(SCREENSHOT_DIR)), name="screenshots")


# ── Pydantic schemas ──────────────────────────────────────────────────

class BrandCreate(BaseModel):
    name: str
    slug: str


class TestRunCreate(BaseModel):
    urls: list[str]
    brand_id: Optional[str] = None
    name: Optional[str] = None
    browsers: list[str] = ["edge", "chrome", "firefox", "curl", "powershell"]


class TaskUpdate(BaseModel):
    outcome: str
    browser_message:  Optional[str] = None
    defender_message: Optional[str] = None
    screenshot_url:   Optional[str] = None
    http_status:      Optional[int] = None
    error_details:    Optional[str] = None


class ScreenshotAdd(BaseModel):
    step:     str
    s3_url:   str
    ocr_text: Optional[str] = None


class VMCreate(BaseModel):
    name:                 str
    azure_resource_group: Optional[str] = None
    azure_vm_name:        Optional[str] = None
    snapshot_name:        Optional[str] = None
    agent_url:            Optional[str] = None
    agent_token:          Optional[str] = None


class ScheduledJobCreate(BaseModel):
    name:           str
    urls:           list[str]
    browsers:       list[str] = ["edge", "chrome", "firefox", "curl", "powershell"]
    brand_id:       Optional[str] = None
    schedule_type:  str = "interval"
    interval_hours: Optional[int] = None
    cron_expr:      Optional[str] = None
    enabled:        bool = True


class ScheduledJobUpdate(BaseModel):
    name:           Optional[str] = None
    urls:           Optional[list[str]] = None
    browsers:       Optional[list[str]] = None
    schedule_type:  Optional[str] = None
    interval_hours: Optional[int] = None
    cron_expr:      Optional[str] = None
    enabled:        Optional[bool] = None


# ── Helpers ───────────────────────────────────────────────────────────

def _run_to_dict(r: TestRun, db: Session) -> dict:
    outcome_counts = dict(
        db.execute(
            select(DownloadTask.outcome, func.count())
            .where(DownloadTask.test_run_id == r.id)
            .group_by(DownloadTask.outcome)
        ).all()
    )
    history = []
    try:
        history = json.loads(r.state_history or "[]")
    except Exception:
        pass
    return {
        "id":               str(r.id),
        "name":             r.name,
        "brand_id":         str(r.brand_id) if r.brand_id else None,
        "scheduled_job_id": str(r.scheduled_job_id) if r.scheduled_job_id else None,
        "vm_id":            str(r.vm_id) if r.vm_id else None,
        "status":           r.status,
        "state_history":    history,
        "total_tasks":      r.total_tasks,
        "completed_tasks":  r.completed_tasks,
        "outcome_summary":  {str(k): v for k, v in outcome_counts.items()},
        "triggered_by":     r.triggered_by,
        "created_at":       r.created_at.isoformat(),
        "started_at":       r.started_at.isoformat() if r.started_at else None,
        "completed_at":     r.completed_at.isoformat() if r.completed_at else None,
    }


def _job_to_dict(j: ScheduledJob) -> dict:
    return {
        "id":             str(j.id),
        "name":           j.name,
        "brand_id":       str(j.brand_id) if j.brand_id else None,
        "urls":           j.urls_list,
        "browsers":       j.browsers_list,
        "schedule_type":  j.schedule_type,
        "interval_hours": j.interval_hours,
        "cron_expr":      j.cron_expr,
        "enabled":        j.enabled,
        "run_count":      j.run_count,
        "last_run_at":    j.last_run_at.isoformat() if j.last_run_at else None,
        "next_run_at":    j.next_run_at.isoformat() if j.next_run_at else None,
        "last_run_id":    j.last_run_id,
        "created_at":     j.created_at.isoformat(),
    }


def _vm_to_dict(v: VMPool) -> dict:
    return {
        "id":                   str(v.id),
        "name":                 v.name,
        "azure_resource_group": v.azure_resource_group,
        "azure_vm_name":        v.azure_vm_name,
        "snapshot_name":        v.snapshot_name,
        "agent_url":            v.agent_url,
        "status":               v.status,
        "current_run_id":       v.current_run_id,
        "last_heartbeat":       v.last_heartbeat.isoformat() if v.last_heartbeat else None,
        "created_at":           v.created_at.isoformat(),
    }


def _append_history(db, run: TestRun, state: str, message: str):
    history = []
    try:
        history = json.loads(run.state_history or "[]")
    except Exception:
        pass
    history.append({
        "state":     state,
        "timestamp": datetime.utcnow().isoformat(),
        "message":   message,
    })
    run.state_history = json.dumps(history)


# ── Brand endpoints ───────────────────────────────────────────────────

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


# ── VM Pool endpoints ─────────────────────────────────────────────────

@app.post("/api/vms")
def create_vm(data: VMCreate, db: Session = Depends(get_db)):
    vm = VMPool(
        name=data.name,
        azure_resource_group=data.azure_resource_group,
        azure_vm_name=data.azure_vm_name,
        snapshot_name=data.snapshot_name,
        agent_url=data.agent_url,
        agent_token=data.agent_token,
    )
    db.add(vm)
    db.commit()
    db.refresh(vm)
    return _vm_to_dict(vm)


@app.get("/api/vms")
def list_vms(db: Session = Depends(get_db)):
    vms = db.execute(select(VMPool).order_by(VMPool.name)).scalars().all()
    return [_vm_to_dict(v) for v in vms]


@app.get("/api/vms/{vm_id}")
def get_vm(vm_id: str, db: Session = Depends(get_db)):
    vm = db.get(VMPool, vm_id)
    if not vm:
        raise HTTPException(404, "VM not found")
    return _vm_to_dict(vm)


@app.delete("/api/vms/{vm_id}")
def delete_vm(vm_id: str, db: Session = Depends(get_db)):
    vm = db.get(VMPool, vm_id)
    if not vm:
        raise HTTPException(404, "VM not found")
    db.delete(vm)
    db.commit()
    return {"status": "deleted"}


@app.post("/api/vms/{vm_id}/heartbeat")
def vm_heartbeat(vm_id: str, db: Session = Depends(get_db)):
    vm = db.get(VMPool, vm_id)
    if not vm:
        raise HTTPException(404, "VM not found")
    vm.last_heartbeat = datetime.utcnow()
    if vm.status == VMStatus.OFFLINE.value:
        vm.status = VMStatus.IDLE.value
    db.commit()
    return {"status": "ok"}


# ── Scheduled Job endpoints ───────────────────────────────────────────

@app.post("/api/jobs")
def create_job(data: ScheduledJobCreate, db: Session = Depends(get_db)):
    job = ScheduledJob(
        name=data.name,
        brand_id=data.brand_id or None,
        urls=json.dumps(data.urls),
        browsers=json.dumps(data.browsers),
        schedule_type=data.schedule_type,
        interval_hours=data.interval_hours,
        cron_expr=data.cron_expr,
        enabled=data.enabled,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    import scheduler as sched
    sched.job_scheduler.create(job)
    return _job_to_dict(job)


@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.execute(
        select(ScheduledJob).order_by(ScheduledJob.created_at.desc())
    ).scalars().all()
    return [_job_to_dict(j) for j in jobs]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    recent_runs = db.execute(
        select(TestRun)
        .where(TestRun.scheduled_job_id == job_id)
        .order_by(TestRun.created_at.desc())
        .limit(20)
    ).scalars().all()
    d = _job_to_dict(job)
    d["recent_runs"] = [_run_to_dict(r, db) for r in recent_runs]
    return d


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: str, data: ScheduledJobUpdate, db: Session = Depends(get_db)):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if data.name           is not None: job.name           = data.name
    if data.urls           is not None: job.urls           = json.dumps(data.urls)
    if data.browsers       is not None: job.browsers       = json.dumps(data.browsers)
    if data.schedule_type  is not None: job.schedule_type  = data.schedule_type
    if data.interval_hours is not None: job.interval_hours = data.interval_hours
    if data.cron_expr      is not None: job.cron_expr      = data.cron_expr
    if data.enabled        is not None: job.enabled        = data.enabled
    db.commit()
    db.refresh(job)
    import scheduler as sched
    sched.job_scheduler.update(job)
    return _job_to_dict(job)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    db.delete(job)
    db.commit()
    import scheduler as sched
    sched.job_scheduler.delete(job_id)
    return {"status": "deleted"}


@app.post("/api/jobs/{job_id}/run")
async def trigger_job_now(job_id: str, db: Session = Depends(get_db)):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    import scheduler as sched
    asyncio.create_task(sched.job_scheduler._fire_job(job_id))
    return {"status": "triggered"}


@app.post("/api/jobs/{job_id}/toggle")
def toggle_job(job_id: str, data: dict, db: Session = Depends(get_db)):
    job = db.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    enabled = data.get("enabled", not job.enabled)
    import scheduler as sched
    sched.job_scheduler.toggle(job_id, enabled)
    db.refresh(job)
    return _job_to_dict(job)


# ── Test Run endpoints ────────────────────────────────────────────────

@app.post("/api/runs")
async def create_test_run(data: TestRunCreate, db: Session = Depends(get_db)):
    run = TestRun(
        name=data.name,
        brand_id=data.brand_id or None,
        status=TestRunStatus.QUEUED.value,
        triggered_by="manual",
        total_tasks=len(data.urls) * len(data.browsers),
        state_history=json.dumps([{
            "state":     "queued",
            "timestamp": datetime.utcnow().isoformat(),
            "message":   "Manually triggered",
        }]),
    )
    db.add(run)
    db.flush()

    for url in data.urls:
        for browser in data.browsers:
            db.add(DownloadTask(
                test_run_id=run.id,
                url=url,
                browser=browser,
                outcome=DownloadOutcome.PENDING.value,
            ))

    db.commit()
    run_id = str(run.id)

    await mgr.broadcast_all({
        "type":        "run_created",
        "run_id":      run_id,
        "total_tasks": run.total_tasks,
    })

    vm_count = db.execute(select(func.count()).select_from(VMPool)).scalar()

    if vm_count and vm_count > 0:
        try:
            import scheduler as sched
            if sched.dispatcher:
                asyncio.create_task(sched.dispatcher.dispatch(run_id))
                return {"run_id": run_id, "total_tasks": run.total_tasks, "status": run.status}
        except Exception:
            pass

    threading.Thread(target=_execute_run_local, args=(run_id,), daemon=True).start()
    return {"run_id": run_id, "total_tasks": run.total_tasks, "status": run.status}


def _execute_run_local(run_id: str):
    db = SessionLocal()
    try:
        run = db.get(TestRun, run_id)
        if not run:
            return

        run.status = TestRunStatus.RUNNING.value
        run.started_at = datetime.utcnow()
        _append_history(db, run, "running", "Running locally (no VM configured)")

        tasks = db.execute(
            select(DownloadTask).where(DownloadTask.test_run_id == run_id)
        ).scalars().all()

        for task in tasks:
            task.outcome = DownloadOutcome.RUNNING.value
            task.started_at = datetime.utcnow()
            db.commit()

            try:
                from worker_agent import BrowserDownloader, CLIDownloader
                browser = task.browser
                if browser in ("edge", "chrome", "firefox"):
                    dl = BrowserDownloader(browser=browser, task_id=str(task.id))
                else:
                    dl = CLIDownloader(method=browser, task_id=str(task.id))
                result = dl.execute(task.url)

                task.outcome          = result.get("outcome", DownloadOutcome.DOWNLOAD_FAILED.value)
                task.browser_message  = result.get("browser_message")
                task.defender_message = result.get("defender_message")
                task.screenshot_url   = result.get("screenshot_url")
                task.http_status      = result.get("http_status")
                task.error_details    = result.get("error_details")
                task.file_name        = result.get("file_name")

            except ImportError:
                import time, random
                time.sleep(1)
                task.outcome = random.choice([
                    DownloadOutcome.SUCCESS_EXECUTED.value,
                    DownloadOutcome.BROWSER_BLOCKED.value,
                    DownloadOutcome.DEFENDER_BLOCKED.value,
                ])
                task.browser_message = f"[simulated] {task.outcome}"

            except Exception as e:
                task.outcome = DownloadOutcome.DOWNLOAD_FAILED.value
                task.error_details = str(e)

            task.finished_at = datetime.utcnow()
            run.completed_tasks += 1
            db.commit()

        run.status = TestRunStatus.COMPLETED.value
        run.completed_at = datetime.utcnow()
        _append_history(db, run, "completed", "All tasks finished")
        db.commit()

        try:
            import mongo_reporter
            run_dict = _run_to_dict(run, db)
            tasks_list = db.execute(
                select(DownloadTask).where(DownloadTask.test_run_id == run_id)
            ).scalars().all()
            mongo_reporter.sync_run(run_dict, [
                {"url": t.url, "browser": t.browser, "outcome": t.outcome,
                 "browser_message": t.browser_message, "error_details": t.error_details}
                for t in tasks_list
            ])
        except Exception:
            pass

        if SLACK_WEBHOOK:
            _send_slack_notification(run_id, db)

    except Exception as e:
        print(f"Run {run_id} failed: {e}")
        run = db.get(TestRun, run_id)
        if run:
            run.status = TestRunStatus.FAILED.value
            _append_history(db, run, "failed", str(e))
            db.commit()
    finally:
        db.close()


@app.get("/api/runs")
def list_runs(
    brand_id:  Optional[str] = Query(None),
    status:    Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    limit:     int           = Query(50, le=200),
    offset:    int           = Query(0),
    db: Session = Depends(get_db),
):
    q = select(TestRun).order_by(TestRun.created_at.desc())
    if brand_id:  q = q.where(TestRun.brand_id == brand_id)
    if status:    q = q.where(TestRun.status == status)
    if date_from:
        try: q = q.where(TestRun.created_at >= datetime.fromisoformat(date_from))
        except ValueError: pass
    if date_to:
        try: q = q.where(TestRun.created_at <= datetime.fromisoformat(date_to))
        except ValueError: pass
    runs = db.execute(q.limit(limit).offset(offset)).scalars().all()
    return [_run_to_dict(r, db) for r in runs]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(TestRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    tasks = db.execute(
        select(DownloadTask)
        .where(DownloadTask.test_run_id == run_id)
        .order_by(DownloadTask.url, DownloadTask.browser)
    ).scalars().all()
    d = _run_to_dict(run, db)
    d["tasks"] = [
        {
            "id":               str(t.id),
            "url":              t.url,
            "file_name":        t.file_name,
            "browser":          t.browser,
            "outcome":          t.outcome,
            "screenshot_url":   t.screenshot_url,
            "browser_message":  t.browser_message,
            "defender_message": t.defender_message,
            "http_status":      t.http_status,
            "error_details":    t.error_details,
            "started_at":       t.started_at.isoformat() if t.started_at else None,
            "finished_at":      t.finished_at.isoformat() if t.finished_at else None,
            "screenshots": [
                {
                    "step":        s.step,
                    "s3_url":      s.s3_url,
                    "ocr_text":    s.ocr_text,
                    "captured_at": s.captured_at.isoformat(),
                }
                for s in t.screenshots
            ],
        }
        for t in tasks
    ]
    return d


# ── Task update (called by VM agent via PATCH) ────────────────────────

@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: str, data: TaskUpdate, db: Session = Depends(get_db)):
    task = db.get(DownloadTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    task.outcome          = data.outcome
    task.browser_message  = data.browser_message  or task.browser_message
    task.defender_message = data.defender_message or task.defender_message
    task.screenshot_url   = data.screenshot_url   or task.screenshot_url
    task.http_status      = data.http_status      or task.http_status
    task.error_details    = data.error_details    or task.error_details

    terminal = {DownloadOutcome.PENDING.value, DownloadOutcome.RUNNING.value}
    if data.outcome not in terminal:
        task.finished_at = datetime.utcnow()
        run = db.get(TestRun, task.test_run_id)
        run.completed_tasks += 1

        if run.completed_tasks >= run.total_tasks:
            run.status = TestRunStatus.COMPLETED.value
            run.completed_at = datetime.utcnow()
            _append_history(db, run, "completed", "All tasks finished")

            if run.vm_id:
                try:
                    import scheduler as sched
                    if sched.vm_manager:
                        asyncio.create_task(sched.vm_manager.release_vm(str(run.vm_id)))
                except Exception:
                    pass

            if SLACK_WEBHOOK:
                threading.Thread(
                    target=_send_slack_notification,
                    args=(str(task.test_run_id), SessionLocal()),
                    daemon=True,
                ).start()

    db.commit()

    await mgr.broadcast(str(task.test_run_id), {
        "type":            "task_update",
        "task_id":         str(task.id),
        "url":             task.url,
        "browser":         task.browser,
        "outcome":         data.outcome,
        "browser_message": data.browser_message,
        "screenshot_url":  data.screenshot_url,
    })

    return {"status": "updated"}


# ── Callback endpoint (called by VM agent after each task) ────────────

@app.post("/api/runs/{run_id}/tasks/{task_id}/result")
async def task_result_callback(
    run_id: str,
    task_id: str,
    data: TaskUpdate,
    db: Session = Depends(get_db),
):
    """VM agent POSTs results here when a task completes."""
    print(f"[callback] run={run_id} task={task_id} outcome={data.outcome}")
    return await update_task(task_id, data, db)


# ── Screenshots ───────────────────────────────────────────────────────

@app.post("/api/tasks/{task_id}/screenshots/upload")
async def upload_screenshot_file(task_id: str, file: UploadFile = File(...)):
    ss_dir = SCREENSHOT_DIR / task_id
    ss_dir.mkdir(parents=True, exist_ok=True)
    save_path = ss_dir / "screenshot.png"
    with open(save_path, "wb") as f:
        f.write(await file.read())
    return {"url": f"/screenshots/{task_id}/screenshot.png"}


@app.post("/api/tasks/{task_id}/screenshots")
def add_screenshot(task_id: str, data: ScreenshotAdd, db: Session = Depends(get_db)):
    task = db.get(DownloadTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    ss = TaskScreenshot(task_id=task.id, step=data.step, s3_url=data.s3_url, ocr_text=data.ocr_text)
    db.add(ss)
    db.commit()
    return {"status": "added", "screenshot_id": str(ss.id)}


@app.get("/api/tasks/{task_id}/screenshots")
def list_task_screenshots(task_id: str):
    ss_dir = SCREENSHOT_DIR / task_id
    if not ss_dir.exists():
        return []
    files = sorted(ss_dir.glob("*.png"))
    return [
        {
            "step":        f.stem,
            "s3_url":      f"/screenshots/{task_id}/{f.name}",
            "ocr_text":    None,
            "captured_at": None,
        }
        for f in files
    ]


# ── CSV Export ────────────────────────────────────────────────────────

@app.get("/api/runs/{run_id}/export")
def export_run_csv(run_id: str, db: Session = Depends(get_db)):
    import csv, io
    from fastapi.responses import StreamingResponse

    run = db.get(TestRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    tasks = db.execute(
        select(DownloadTask).where(DownloadTask.test_run_id == run_id)
        .order_by(DownloadTask.url, DownloadTask.browser)
    ).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "url", "browser", "outcome", "file_name",
        "http_status", "browser_message", "defender_message",
        "error_details", "started_at", "finished_at"
    ])
    for t in tasks:
        writer.writerow([
            t.url, t.browser, t.outcome, t.file_name or "",
            t.http_status or "", t.browser_message or "",
            t.defender_message or "", t.error_details or "",
            t.started_at.isoformat() if t.started_at else "",
            t.finished_at.isoformat() if t.finished_at else "",
        ])

    buf.seek(0)
    run_name = (run.name or run_id[:8]).replace(" ", "_")
    filename = f"sentinel_{run_name}_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Retry failed tasks ────────────────────────────────────────────────

@app.post("/api/runs/{run_id}/retry")
async def retry_failed_tasks(run_id: str, db: Session = Depends(get_db)):
    run = db.get(TestRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    failed_outcomes = {DownloadOutcome.DOWNLOAD_FAILED.value, DownloadOutcome.TIMEOUT.value}
    failed_tasks = db.execute(
        select(DownloadTask).where(
            DownloadTask.test_run_id == run_id,
            DownloadTask.outcome.in_(failed_outcomes)
        )
    ).scalars().all()

    if not failed_tasks:
        return {"status": "nothing_to_retry", "count": 0}

    for task in failed_tasks:
        task.outcome = DownloadOutcome.PENDING.value
        task.started_at = None
        task.finished_at = None
        task.error_details = None
        task.browser_message = None

    run.status = TestRunStatus.RUNNING.value
    run.completed_tasks = max(0, run.completed_tasks - len(failed_tasks))
    _append_history(db, run, "running", f"Retrying {len(failed_tasks)} failed tasks")
    db.commit()

    threading.Thread(target=_execute_run_local, args=(run_id,), daemon=True).start()
    return {"status": "retrying", "count": len(failed_tasks)}


# ── Analytics ─────────────────────────────────────────────────────────

@app.get("/api/analytics")
def get_analytics(
    brand_id: Optional[str] = Query(None),
    days:     int           = Query(30),
    db: Session = Depends(get_db),
):
    from datetime import timedelta
    since = datetime.utcnow() - timedelta(days=days)

    try:
        import mongo_reporter
        mongo_data = mongo_reporter.get_analytics(brand_id=brand_id, days=days)
        if mongo_data:
            return {"source": "mongodb", "outcomes": mongo_data}
    except Exception:
        pass

    q = select(DownloadTask.outcome, func.count()).where(
        DownloadTask.test_run_id.in_(
            select(TestRun.id).where(TestRun.created_at >= since)
        )
    )
    if brand_id:
        q = q.where(DownloadTask.test_run_id.in_(
            select(TestRun.id).where(TestRun.brand_id == brand_id)
        ))
    q = q.group_by(DownloadTask.outcome)
    outcome_rows = db.execute(q).all()

    daily_q = select(
        func.date(TestRun.created_at).label("day"),
        func.count().label("count")
    ).where(TestRun.created_at >= since)
    if brand_id:
        daily_q = daily_q.where(TestRun.brand_id == brand_id)
    daily_q = daily_q.group_by(func.date(TestRun.created_at)).order_by("day")
    daily_rows = db.execute(daily_q).all()

    return {
        "source":     "sqlite",
        "outcomes":   {str(o): c for o, c in outcome_rows},
        "daily_runs": [{"day": str(d), "count": c} for d, c in daily_rows],
    }


# ── Settings ──────────────────────────────────────────────────────────

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


# ── Slack notification ────────────────────────────────────────────────

def _send_slack_notification(run_id: str, db: Session):
    import requests as req

    run = db.get(TestRun, run_id)
    if not run:
        db.close()
        return

    rows = db.execute(
        select(DownloadTask.outcome, func.count())
        .where(DownloadTask.test_run_id == run_id)
        .group_by(DownloadTask.outcome)
    ).all()

    emoji = {
        "success_executed":         ":white_check_mark:",
        "success_smartscreen":      ":warning:",
        "browser_blocked":          ":no_entry:",
        "browser_warned_dangerous": ":rotating_light:",
        "browser_warned_uncommon":  ":eyes:",
        "defender_blocked":         ":shield:",
        "download_failed":          ":x:",
        "timeout":                  ":hourglass:",
    }

    lines = [
        f'{emoji.get(str(o), "?")} *{str(o).replace("_"," ").title()}*: {c}'
        for o, c in rows
    ]

    pass_count = sum(c for o, c in rows if str(o) == "success_executed")
    warn_count = sum(c for o, c in rows if str(o) in ("success_smartscreen", "browser_warned_dangerous", "browser_warned_uncommon"))
    fail_count = sum(c for o, c in rows if str(o) in ("browser_blocked", "defender_blocked", "download_failed", "timeout"))

    header_emoji = ":red_circle:" if fail_count else (":large_yellow_circle:" if warn_count else ":large_green_circle:")
    run_name  = run.name or f"Run #{run_id[:8]}"
    triggered = " _(scheduled)_" if run.scheduled_job_id else ""

    payload = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"{header_emoji} Download Sentinel — {run_name}{triggered}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Total:* {run.total_tasks}"},
                {"type": "mrkdwn", "text": f"*Pass:* {pass_count} | *Warn:* {warn_count} | *Fail:* {fail_count}"},
            ]},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "View Dashboard"}, "url": DASHBOARD_URL},
            ]},
        ]
    }

    try:
        req.post(SLACK_WEBHOOK, json=payload, timeout=10)
        run.slack_notified = True
        db.commit()
    except Exception:
        pass
    finally:
        db.close()


# ── WebSocket endpoints ───────────────────────────────────────────────

@app.websocket("/ws/{run_id}")
async def ws_run(ws: WebSocket, run_id: str):
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


# ── Frontend SPA (must be last) ───────────────────────────────────────

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="frontend-assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        fp = FRONTEND_DIR / full_path
        if fp.exists() and fp.is_file():
            return FileResponse(str(fp))
        return FileResponse(str(FRONTEND_DIR / "index.html"))