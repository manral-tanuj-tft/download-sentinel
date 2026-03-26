"""
Download Sentinel — Database Models
"""
import enum
import json
from datetime import datetime
from uuid import uuid4
from sqlalchemy import (
    Column, String, Text, DateTime, Integer, Boolean, ForeignKey
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


def gen_id():
    return str(uuid4())


# ── Enums ──────────────────────────────────────────────────────────────

class DownloadOutcome(str, enum.Enum):
    SUCCESS_EXECUTED        = "success_executed"
    SUCCESS_SMARTSCREEN     = "success_smartscreen"
    BROWSER_BLOCKED         = "browser_blocked"
    BROWSER_WARNED_DANGEROUS = "browser_warned_dangerous"
    BROWSER_WARNED_UNCOMMON = "browser_warned_uncommon"
    DEFENDER_BLOCKED        = "defender_blocked"
    DOWNLOAD_FAILED         = "download_failed"
    TIMEOUT                 = "timeout"
    PENDING                 = "pending"
    RUNNING                 = "running"


class BrowserType(str, enum.Enum):
    EDGE       = "edge"
    CHROME     = "chrome"
    FIREFOX    = "firefox"
    CURL       = "curl"
    POWERSHELL = "powershell"


class TestRunStatus(str, enum.Enum):
    QUEUED          = "queued"
    WAITING_FOR_VM  = "waiting_for_vm"
    PROVISIONING    = "provisioning"
    RUNNING         = "running"
    COMPLETED       = "completed"
    FAILED          = "failed"
    CANCELLED       = "cancelled"


class VMStatus(str, enum.Enum):
    IDLE         = "idle"
    BUSY         = "busy"
    OFFLINE      = "offline"
    PROVISIONING = "provisioning"


class ScheduleType(str, enum.Enum):
    INTERVAL = "interval"
    CRON     = "cron"


# ── Tables ─────────────────────────────────────────────────────────────

class Brand(Base):
    __tablename__ = "brands"

    id         = Column(String(36), primary_key=True, default=gen_id)
    name       = Column(String(255), nullable=False, unique=True)
    slug       = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    test_runs      = relationship("TestRun", back_populates="brand")
    scheduled_jobs = relationship("ScheduledJob", back_populates="brand")


class VMPool(Base):
    __tablename__ = "vm_pool"

    id                   = Column(String(36), primary_key=True, default=gen_id)
    name                 = Column(String(255), nullable=False)
    azure_resource_group = Column(String(255), nullable=True)
    azure_vm_name        = Column(String(255), nullable=True)
    snapshot_name        = Column(String(255), nullable=True)
    agent_url            = Column(String(500), nullable=True)   # e.g. http://20.1.2.3:5000
    agent_token          = Column(String(255), nullable=True)
    status               = Column(String(20), default=VMStatus.IDLE.value)
    current_run_id       = Column(String(36), nullable=True)
    last_heartbeat       = Column(DateTime, nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow)

    test_runs = relationship("TestRun", back_populates="vm")


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    id             = Column(String(36), primary_key=True, default=gen_id)
    name           = Column(String(255), nullable=False)
    brand_id       = Column(String(36), ForeignKey("brands.id"), nullable=True)
    urls           = Column(Text, nullable=False)     # JSON array of strings
    browsers       = Column(Text, nullable=False)     # JSON array of strings
    schedule_type  = Column(String(20), default=ScheduleType.INTERVAL.value)
    interval_hours = Column(Integer, nullable=True)
    cron_expr      = Column(String(100), nullable=True)
    enabled        = Column(Boolean, default=True)
    last_run_at    = Column(DateTime, nullable=True)
    next_run_at    = Column(DateTime, nullable=True)
    last_run_id    = Column(String(36), nullable=True)
    run_count      = Column(Integer, default=0)
    created_at     = Column(DateTime, default=datetime.utcnow)

    brand     = relationship("Brand", back_populates="scheduled_jobs")
    test_runs = relationship("TestRun", back_populates="scheduled_job")

    @property
    def urls_list(self):
        return json.loads(self.urls or "[]")

    @property
    def browsers_list(self):
        return json.loads(self.browsers or "[]")


class TestRun(Base):
    __tablename__ = "test_runs"

    id                = Column(String(36), primary_key=True, default=gen_id)
    brand_id          = Column(String(36), ForeignKey("brands.id"), nullable=True)
    scheduled_job_id  = Column(String(36), ForeignKey("scheduled_jobs.id"), nullable=True)
    vm_id             = Column(String(36), ForeignKey("vm_pool.id"), nullable=True)
    name              = Column(String(255), nullable=True)
    status            = Column(String(50), default=TestRunStatus.QUEUED.value)
    state_history     = Column(Text, nullable=True)   # JSON list of {state, timestamp, message}
    created_at        = Column(DateTime, default=datetime.utcnow)
    started_at        = Column(DateTime, nullable=True)
    completed_at      = Column(DateTime, nullable=True)
    triggered_by      = Column(String(255), nullable=True)
    total_tasks       = Column(Integer, default=0)
    completed_tasks   = Column(Integer, default=0)
    slack_notified    = Column(Boolean, default=False)

    brand         = relationship("Brand", back_populates="test_runs")
    scheduled_job = relationship("ScheduledJob", back_populates="test_runs")
    vm            = relationship("VMPool", back_populates="test_runs")
    tasks         = relationship("DownloadTask", back_populates="test_run",
                                 cascade="all, delete-orphan")


class DownloadTask(Base):
    __tablename__ = "download_tasks"

    id           = Column(String(36), primary_key=True, default=gen_id)
    test_run_id  = Column(String(36), ForeignKey("test_runs.id"), nullable=False)
    url          = Column(Text, nullable=False)
    file_name    = Column(String(500), nullable=True)
    browser      = Column(String(20), nullable=False)
    outcome      = Column(String(50), default=DownloadOutcome.PENDING.value)
    started_at   = Column(DateTime, nullable=True)
    finished_at  = Column(DateTime, nullable=True)

    screenshot_url   = Column(Text, nullable=True)
    browser_message  = Column(Text, nullable=True)
    defender_message = Column(Text, nullable=True)
    http_status      = Column(Integer, nullable=True)
    error_details    = Column(Text, nullable=True)
    vm_id            = Column(String(255), nullable=True)
    worker_id        = Column(String(255), nullable=True)

    test_run    = relationship("TestRun", back_populates="tasks")
    screenshots = relationship("TaskScreenshot", back_populates="task",
                               cascade="all, delete-orphan")


class TaskScreenshot(Base):
    __tablename__ = "task_screenshots"

    id          = Column(String(36), primary_key=True, default=gen_id)
    task_id     = Column(String(36), ForeignKey("download_tasks.id"), nullable=False)
    step        = Column(String(100), nullable=False)
    s3_url      = Column(Text, nullable=False)
    captured_at = Column(DateTime, default=datetime.utcnow)
    ocr_text    = Column(Text, nullable=True)

    task = relationship("DownloadTask", back_populates="screenshots")