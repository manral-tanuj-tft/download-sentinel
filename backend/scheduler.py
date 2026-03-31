"""
Download Sentinel — Scheduler + VM Manager
"""
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from models import (
    ScheduledJob, TestRun, DownloadTask,
    DownloadOutcome, VMPool, VMStatus, TestRunStatus
)

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler(timezone="UTC")


# ── VM Manager ────────────────────────────────────────────────────────

class VMManager:
    def __init__(self, SessionLocal):
        self.SessionLocal = SessionLocal

    async def acquire_vm(self, run_id: str) -> Optional[VMPool]:
        db = self.SessionLocal()
        try:
            vm = db.query(VMPool).filter(
                VMPool.status == VMStatus.IDLE.value
            ).with_for_update().first()
            if not vm:
                return None
            vm.status = VMStatus.BUSY.value
            vm.current_run_id = run_id
            db.commit()
            db.refresh(vm)
            return vm
        except Exception as e:
            db.rollback()
            logger.error(f"VM acquire failed: {e}")
            return None
        finally:
            db.close()

    async def release_vm(self, vm_id: str):
        db = self.SessionLocal()
        try:
            vm = db.get(VMPool, vm_id)
            if vm:
                vm.status = VMStatus.IDLE.value
                vm.current_run_id = None
                db.commit()
        finally:
            db.close()

    async def restore_snapshot(self, vm: VMPool) -> bool:
        import os
        use_agent_restore = os.getenv("VM_AGENT_HANDLES_RESTORE", "true").lower() == "true"
        sub_id = os.getenv("AZURE_SUBSCRIPTION_ID")

        if use_agent_restore and vm.agent_url:
            return await self._agent_restore(vm)
        if sub_id and vm.azure_resource_group and vm.azure_vm_name:
            return await self._azure_restore(vm, sub_id)

        logger.warning(f"[DEV] Skipping snapshot restore for VM {vm.name}")
        return True

    async def _agent_restore(self, vm: VMPool) -> bool:
        import httpx
        headers = {"Authorization": f"Bearer {vm.agent_token}"} if vm.agent_token else {}
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{vm.agent_url}/restore",
                    json={"snapshot": vm.snapshot_name},
                    headers=headers,
                )
                if resp.status_code == 200:
                    logger.info(f"VM {vm.name} snapshot restored via agent")
                    return True
                logger.error(f"VM agent restore failed: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.error(f"VM agent restore error: {e}")
            return False

    async def _azure_restore(self, vm: VMPool, subscription_id: str) -> bool:
        try:
            from azure.identity.aio import DefaultAzureCredential
            from azure.mgmt.compute.aio import ComputeManagementClient

            async with DefaultAzureCredential() as cred:
                async with ComputeManagementClient(cred, subscription_id) as client:
                    snapshot = await client.snapshots.get(
                        vm.azure_resource_group, vm.snapshot_name
                    )
                    disk_name = f"{vm.azure_vm_name}-osdisk-clean"
                    poller = await client.disks.begin_create_or_update(
                        vm.azure_resource_group, disk_name,
                        {
                            "location": snapshot.location,
                            "creation_data": {
                                "create_option": "Copy",
                                "source_resource_id": snapshot.id,
                            },
                        },
                    )
                    new_disk = await poller.result()

                    await (await client.virtual_machines.begin_deallocate(
                        vm.azure_resource_group, vm.azure_vm_name
                    )).result()

                    vm_obj = await client.virtual_machines.get(
                        vm.azure_resource_group, vm.azure_vm_name
                    )
                    vm_obj.storage_profile.os_disk.managed_disk.id = new_disk.id
                    vm_obj.storage_profile.os_disk.name = disk_name

                    await (await client.virtual_machines.begin_create_or_update(
                        vm.azure_resource_group, vm.azure_vm_name, vm_obj
                    )).result()

                    await (await client.virtual_machines.begin_start(
                        vm.azure_resource_group, vm.azure_vm_name
                    )).result()

            logger.info(f"Azure snapshot restore complete for {vm.azure_vm_name}")
            return True

        except ImportError:
            logger.warning("azure-mgmt-compute not installed, skipping real restore")
            return True
        except Exception as e:
            logger.error(f"Azure restore failed: {e}")
            return False

    async def trigger_test_on_vm(self, vm: VMPool, run_id: str, tasks: list) -> bool:
        import httpx
        import os

        if not vm.agent_url:
            logger.error(f"VM {vm.name} has no agent_url")
            return False

        callback_url = os.getenv("CALLBACK_BASE_URL", "http://localhost:8000")
        headers = {"Authorization": f"Bearer {vm.agent_token}"} if vm.agent_token else {}

        payload = {
            "run_id": run_id,
            "callback_url": callback_url,
            "tasks": [
                {"task_id": str(t.id), "url": t.url, "browser": t.browser}
                for t in tasks
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{vm.agent_url}/run",
                    json=payload,
                    headers=headers,
                )
                ok = resp.status_code == 200
                if ok:
                    logger.info(f"Run {run_id} dispatched to VM {vm.name}")
                else:
                    logger.error(f"VM agent rejected run: {resp.status_code}")
                return ok
        except Exception as e:
            logger.error(f"Dispatch to VM {vm.name} failed: {e}")
            return False

    async def heartbeat(self, vm_id: str):
        db = self.SessionLocal()
        try:
            vm = db.get(VMPool, vm_id)
            if vm:
                vm.last_heartbeat = datetime.utcnow()
                db.commit()
        finally:
            db.close()


# ── Run Dispatcher ────────────────────────────────────────────────────

class RunDispatcher:
    def __init__(self, SessionLocal, vm_manager: VMManager, ws_broadcast_fn=None):
        self.SessionLocal = SessionLocal
        self.vm_manager = vm_manager
        self.broadcast = ws_broadcast_fn

    async def dispatch(self, run_id: str):
        db = self.SessionLocal()
        try:
            run = db.get(TestRun, run_id)
            if not run:
                return

            await self._transition(db, run, TestRunStatus.WAITING_FOR_VM.value)

            # Try to acquire a VM (retry up to 10 min)
            vm = None
            for attempt in range(20):
                vm = await self.vm_manager.acquire_vm(run_id)
                if vm:
                    break
                logger.info(f"Run {run_id}: no VM (attempt {attempt+1}/20), waiting 30s...")
                await asyncio.sleep(30)

            if not vm:
                await self._transition(db, run, TestRunStatus.FAILED.value,
                                       "No VM became available within 10 minutes")
                return

            # Restore clean snapshot — this is the "clean state machine" per test
            await self._transition(db, run, TestRunStatus.PROVISIONING.value,
                                   f"Restoring snapshot on VM {vm.name}")
            run.vm_id = vm.id
            db.commit()

            snapshot_ok = await self.vm_manager.restore_snapshot(vm)
            if not snapshot_ok:
                await self._transition(db, run, TestRunStatus.FAILED.value,
                                       "Snapshot restore failed")
                await self.vm_manager.release_vm(vm.id)
                return

            # Mark running and dispatch to VM
            await self._transition(db, run, TestRunStatus.RUNNING.value)
            run.started_at = datetime.utcnow()
            db.commit()

            tasks = db.query(DownloadTask).filter(
                DownloadTask.test_run_id == run_id
            ).all()

            ok = await self.vm_manager.trigger_test_on_vm(vm, run_id, tasks)
            if not ok:
                await self._transition(db, run, TestRunStatus.FAILED.value,
                                       "VM agent rejected dispatch")
                await self.vm_manager.release_vm(vm.id)

            # VM will PATCH /api/tasks/{id} as it completes each task.
            # main.py releases the VM when all tasks are done.

        except Exception as e:
            logger.error(f"Dispatch error for run {run_id}: {e}")
            db.rollback()
        finally:
            db.close()

    async def _transition(self, db, run: TestRun, new_status: str, message: str = ""):
        history = []
        try:
            history = json.loads(run.state_history or "[]")
        except Exception:
            pass
        history.append({
            "state": new_status,
            "timestamp": datetime.utcnow().isoformat(),
            "message": message,
        })
        run.status = new_status
        run.state_history = json.dumps(history)
        db.commit()
        logger.info(f"Run {run.id} → {new_status} {message}")

        if self.broadcast:
            await self.broadcast(str(run.id), {
                "type": "run_status",
                "run_id": str(run.id),
                "status": new_status,
                "message": message,
            })


# ── Job Scheduler ─────────────────────────────────────────────────────

class JobScheduler:
    def __init__(self, SessionLocal, dispatcher: RunDispatcher, local_runner=None):
        self.SessionLocal = SessionLocal
        self.dispatcher = dispatcher
        self.local_runner = local_runner  # fn(run_id) — fallback when no VMs

    def load_all(self):
        db = self.SessionLocal()
        try:
            jobs = db.query(ScheduledJob).filter(ScheduledJob.enabled == True).all()
            for job in jobs:
                self._register(job)
            logger.info(f"Loaded {len(jobs)} scheduled jobs")
        finally:
            db.close()

    def _register(self, job: ScheduledJob):
        apid = f"job_{job.id}"

        if _scheduler.get_job(apid):
            _scheduler.remove_job(apid)

        if not job.enabled:
            return

        if job.schedule_type == "interval" and job.interval_hours:
            trigger = IntervalTrigger(hours=job.interval_hours)
        elif job.schedule_type == "cron" and job.cron_expr:
            parts = job.cron_expr.split()
            if len(parts) != 5:
                logger.error(f"Invalid cron '{job.cron_expr}' for job {job.id}")
                return
            trigger = CronTrigger(
                minute=parts[0], hour=parts[1],
                day=parts[2], month=parts[3], day_of_week=parts[4],
            )
        else:
            return

        _scheduler.add_job(
            self._fire_job,
            trigger=trigger,
            id=apid,
            args=[str(job.id)],
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Persist next_run_at
        db = self.SessionLocal()
        try:
            j = db.get(ScheduledJob, job.id)
            if j:
                ap_job = _scheduler.get_job(apid)
                next_run = getattr(ap_job, "next_fire_time", None) or getattr(ap_job, "next_run_time", None)
                j.next_run_at = next_run
                db.commit()
        finally:
            db.close()

        logger.info(f"Registered job '{job.name}' ({job.schedule_type})")

    async def _fire_job(self, job_id: str):
        db = self.SessionLocal()
        try:
            job = db.get(ScheduledJob, job_id)
            if not job or not job.enabled:
                return

            logger.info(f"Firing job '{job.name}' (run #{job.run_count + 1})")

            run = TestRun(
                name=f"{job.name} — Auto #{job.run_count + 1}",
                brand_id=job.brand_id,
                status=TestRunStatus.QUEUED.value,
                triggered_by=f"scheduled:{job.id}",
                scheduled_job_id=job.id,
                total_tasks=len(job.urls_list) * len(job.browsers_list),
                state_history=json.dumps([{
                    "state": "queued",
                    "timestamp": datetime.utcnow().isoformat(),
                    "message": f"Triggered by scheduled job '{job.name}'",
                }]),
            )
            db.add(run)
            db.flush()

            for url in job.urls_list:
                for browser in job.browsers_list:
                    db.add(DownloadTask(
                        test_run_id=run.id,
                        url=url,
                        browser=browser,
                        outcome=DownloadOutcome.PENDING.value,
                    ))

            job.last_run_at = datetime.utcnow()
            job.last_run_id = str(run.id)
            job.run_count += 1

            ap_job = _scheduler.get_job(f"job_{job.id}")
            if ap_job:
                job.next_run_at = getattr(ap_job, "next_fire_time", None) or getattr(ap_job, "next_run_time", None)

            db.commit()
            run_id = str(run.id)

        except Exception as e:
            db.rollback()
            logger.error(f"Job fire failed for {job_id}: {e}")
            return
        finally:
            db.close()

        # Check if VMs are configured — if not, run locally
        db2 = self.SessionLocal()
        try:
            vm_count = db2.query(VMPool).count()
        finally:
            db2.close()

        if vm_count > 0:
            asyncio.create_task(self.dispatcher.dispatch(run_id))
        elif self.local_runner:
            import threading
            threading.Thread(
                target=self.local_runner,
                args=(run_id,),
                daemon=True,
            ).start()
        else:
            logger.warning(f"No VMs and no local_runner configured for job {job_id}")

    def create(self, job: ScheduledJob):
        self._register(job)

    def update(self, job: ScheduledJob):
        self._register(job)

    def delete(self, job_id: str):
        apid = f"job_{job_id}"
        if _scheduler.get_job(apid):
            _scheduler.remove_job(apid)

    def toggle(self, job_id: str, enabled: bool):
        db = self.SessionLocal()
        try:
            job = db.get(ScheduledJob, job_id)
            if not job:
                return
            job.enabled = enabled
            db.commit()
            if enabled:
                self._register(job)
            else:
                self.delete(job_id)
        finally:
            db.close()

    def run_now(self, job_id: str, loop=None):
        """Schedule _fire_job on the running event loop from any thread."""
        if loop is None:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
        asyncio.run_coroutine_threadsafe(self._fire_job(job_id), loop)


# ── Module-level singletons ───────────────────────────────────────────

vm_manager:    Optional[VMManager]    = None
dispatcher:    Optional[RunDispatcher] = None
job_scheduler: Optional[JobScheduler] = None


def init(SessionLocal, ws_broadcast_fn=None, local_runner=None):
    global vm_manager, dispatcher, job_scheduler

    vm_manager    = VMManager(SessionLocal)
    dispatcher    = RunDispatcher(SessionLocal, vm_manager, ws_broadcast_fn)
    job_scheduler = JobScheduler(SessionLocal, dispatcher, local_runner=local_runner)

    job_scheduler.load_all()
    _scheduler.start()
    logger.info("Download Sentinel scheduler started")

    return job_scheduler


def shutdown():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")