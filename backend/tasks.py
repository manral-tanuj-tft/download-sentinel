"""
Download Monitor — Celery Tasks
Dispatches download tasks to worker agents on Azure VMs.
Run: celery -A tasks worker --loglevel=info --concurrency=4
"""
import os
from uuid import UUID
from celery import Celery
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from models import Base, TestRun, DownloadTask, DownloadOutcome

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/download_monitor")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("download_monitor", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


@celery_app.task(name="dispatch_test_run", bind=True, max_retries=3)
def dispatch_test_run(self, run_id: str):
    """
    Takes a test run ID, marks it as running, and fans out
    individual download tasks to the worker pool.
    """
    db = SessionLocal()
    try:
        run = db.get(TestRun, UUID(run_id))
        if not run:
            return {"error": "run not found"}

        run.status = "running"
        db.commit()

        tasks = db.execute(
            select(DownloadTask).where(DownloadTask.test_run_id == UUID(run_id))
        ).scalars().all()

        for task in tasks:
            execute_download.delay(str(task.id))

        return {"dispatched": len(tasks)}
    finally:
        db.close()


@celery_app.task(name="execute_download", bind=True, max_retries=2,
                 soft_time_limit=120, time_limit=180)
def execute_download(self, task_id: str):
    """
    Executes a single download attempt — browser or CLI — on the worker VM.
    Captures screenshots, classifies outcome, reports back via API.
    """
    db = SessionLocal()
    try:
        task = db.get(DownloadTask, UUID(task_id))
        if not task:
            return {"error": "task not found"}

        from datetime import datetime
        task.outcome = DownloadOutcome.RUNNING
        task.started_at = datetime.utcnow()
        task.worker_id = self.request.hostname
        db.commit()

        # Route to appropriate download method
        browser = task.browser.value
        if browser in ("edge", "chrome", "firefox"):
            result = _browser_download(task.url, browser, str(task.id))
        elif browser == "curl":
            result = _cli_download(task.url, "curl", str(task.id))
        elif browser == "powershell":
            result = _cli_download(task.url, "powershell", str(task.id))
        else:
            result = {"outcome": DownloadOutcome.DOWNLOAD_FAILED, "error": f"unknown browser: {browser}"}

        # Update task with results
        task.outcome = result["outcome"]
        task.browser_message = result.get("browser_message")
        task.defender_message = result.get("defender_message")
        task.screenshot_url = result.get("screenshot_url")
        task.http_status = result.get("http_status")
        task.error_details = result.get("error_details")
        task.file_name = result.get("file_name")
        task.finished_at = datetime.utcnow()

        # Update run progress
        run = db.get(TestRun, task.test_run_id)
        run.completed_tasks += 1
        if run.completed_tasks >= run.total_tasks:
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            # Trigger slack notification
            notify_slack.delay(str(run.id))

        db.commit()
        return {"task_id": task_id, "outcome": result["outcome"].value}

    except Exception as exc:
        db.rollback()
        task = db.get(DownloadTask, UUID(task_id))
        if task:
            task.outcome = DownloadOutcome.DOWNLOAD_FAILED
            task.error_details = str(exc)
            db.commit()
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()


def _browser_download(url: str, browser: str, task_id: str) -> dict:
    """
    Uses Playwright to download a file via a real browser and classify the outcome.
    Captures screenshots at each step.
    """
    from worker_agent import BrowserDownloader
    downloader = BrowserDownloader(browser=browser, task_id=task_id)
    return downloader.execute(url)


def _cli_download(url: str, method: str, task_id: str) -> dict:
    """
    Downloads via curl or PowerShell Invoke-WebRequest.
    Checks if Defender intervenes after download.
    """
    from worker_agent import CLIDownloader
    downloader = CLIDownloader(method=method, task_id=task_id)
    return downloader.execute(url)


@celery_app.task(name="notify_slack")
def notify_slack(run_id: str):
    """Send a summary report to Slack when a test run completes."""
    import requests
    from sqlalchemy import func

    SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")
    if not SLACK_WEBHOOK:
        return {"skipped": "no webhook configured"}

    db = SessionLocal()
    try:
        run = db.get(TestRun, UUID(run_id))
        if not run:
            return

        # Aggregate outcomes
        rows = db.execute(
            select(DownloadTask.outcome, func.count())
            .where(DownloadTask.test_run_id == UUID(run_id))
            .group_by(DownloadTask.outcome)
        ).all()
        summary = {str(outcome.value): count for outcome, count in rows}

        # Build Slack blocks
        status_emoji = {
            "success_executed": ":white_check_mark:",
            "success_smartscreen": ":warning:",
            "browser_blocked": ":no_entry:",
            "browser_warned_dangerous": ":rotating_light:",
            "browser_warned_uncommon": ":eyes:",
            "defender_blocked": ":shield:",
            "download_failed": ":x:",
            "timeout": ":hourglass:",
        }

        lines = []
        for outcome, count in summary.items():
            emoji = status_emoji.get(outcome, ":question:")
            lines.append(f"{emoji}  *{outcome.replace('_', ' ').title()}*: {count}")

        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"Download Test Run Complete — {run.name or run_id[:8]}"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "\n".join(lines)}
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Total*: {run.total_tasks} tasks | "
                                f"<{os.getenv('DASHBOARD_URL', 'http://localhost:3000')}/runs/{run_id}|View Dashboard>"
                    }
                },
            ]
        }

        resp = requests.post(SLACK_WEBHOOK, json=payload, timeout=10)
        run.slack_notified = True
        db.commit()
        return {"status": resp.status_code}
    finally:
        db.close()