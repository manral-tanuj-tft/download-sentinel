"""
Download Monitor — Database Models (SQLite compatible)
"""
import enum
from datetime import datetime
from uuid import uuid4
from sqlalchemy import (
    Column, String, Text, DateTime, Integer, Boolean, ForeignKey
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


def gen_id():
    return str(uuid4())


class DownloadOutcome(str, enum.Enum):
    SUCCESS_EXECUTED = "success_executed"
    SUCCESS_SMARTSCREEN = "success_smartscreen"
    BROWSER_BLOCKED = "browser_blocked"
    BROWSER_WARNED_DANGEROUS = "browser_warned_dangerous"
    BROWSER_WARNED_UNCOMMON = "browser_warned_uncommon"
    DEFENDER_BLOCKED = "defender_blocked"
    DOWNLOAD_FAILED = "download_failed"
    TIMEOUT = "timeout"
    PENDING = "pending"
    RUNNING = "running"


class BrowserType(str, enum.Enum):
    EDGE = "edge"
    CHROME = "chrome"
    FIREFOX = "firefox"
    CURL = "curl"
    POWERSHELL = "powershell"


class Brand(Base):
    __tablename__ = "brands"

    id = Column(String(36), primary_key=True, default=gen_id)
    name = Column(String(255), nullable=False, unique=True)
    slug = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    test_runs = relationship("TestRun", back_populates="brand")


class TestRun(Base):
    __tablename__ = "test_runs"

    id = Column(String(36), primary_key=True, default=gen_id)
    brand_id = Column(String(36), ForeignKey("brands.id"), nullable=True)
    name = Column(String(255), nullable=True)
    status = Column(String(50), default="queued")
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    triggered_by = Column(String(255), nullable=True)
    total_tasks = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
    slack_notified = Column(Boolean, default=False)

    brand = relationship("Brand", back_populates="test_runs")
    tasks = relationship("DownloadTask", back_populates="test_run", cascade="all, delete-orphan")


class DownloadTask(Base):
    __tablename__ = "download_tasks"

    id = Column(String(36), primary_key=True, default=gen_id)
    test_run_id = Column(String(36), ForeignKey("test_runs.id"), nullable=False)
    url = Column(Text, nullable=False)
    file_name = Column(String(500), nullable=True)
    browser = Column(String(20), nullable=False)
    outcome = Column(String(50), default=DownloadOutcome.PENDING.value)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    screenshot_url = Column(Text, nullable=True)
    browser_message = Column(Text, nullable=True)
    defender_message = Column(Text, nullable=True)
    http_status = Column(Integer, nullable=True)
    error_details = Column(Text, nullable=True)

    vm_id = Column(String(255), nullable=True)
    worker_id = Column(String(255), nullable=True)

    test_run = relationship("TestRun", back_populates="tasks")
    screenshots = relationship("TaskScreenshot", back_populates="task", cascade="all, delete-orphan")


class TaskScreenshot(Base):
    __tablename__ = "task_screenshots"

    id = Column(String(36), primary_key=True, default=gen_id)
    task_id = Column(String(36), ForeignKey("download_tasks.id"), nullable=False)
    step = Column(String(100), nullable=False)
    s3_url = Column(Text, nullable=False)
    captured_at = Column(DateTime, default=datetime.utcnow)
    ocr_text = Column(Text, nullable=True)

    task = relationship("DownloadTask", back_populates="screenshots")