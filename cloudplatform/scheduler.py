# -*- coding: utf-8 -*-
"""
Backend scheduler — APScheduler jobs that drive the agent command pipeline.

Two jobs:
  schedule_syncs      (every 60s)  — creates sync_all_companies commands for
                                      devices whose sync interval has elapsed.
  reset_stale_cmds    (every 5min) — re-queues commands stuck in 'fetched'
                                      state for > STALE_TIMEOUT_MINUTES.
"""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from cloudplatform.db.database import SessionLocal
from cloudplatform.db.models import DeviceSyncSchedule, SyncCommand

logger = logging.getLogger(__name__)

STALE_TIMEOUT_MINUTES = 5
_scheduler: BackgroundScheduler | None = None


# ── Jobs ──────────────────────────────────────────────────────────────────────

def schedule_syncs() -> None:
    """
    For every active DeviceSyncSchedule, queue a sync_all_companies command
    when the device's interval has elapsed since last_scheduled_at.
    Skips if a pending/fetched sync_all_companies already exists for the device.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        schedules = db.query(DeviceSyncSchedule).filter(
            DeviceSyncSchedule.is_active == True
        ).all()

        queued = 0
        for sched in schedules:
            if sched.last_scheduled_at is not None:
                elapsed = (now - sched.last_scheduled_at).total_seconds()
                if elapsed < sched.interval_seconds:
                    continue

            # Don't pile up commands if the device hasn't acked the last one yet
            already_pending = db.query(SyncCommand).filter(
                SyncCommand.device_id == sched.device_id,
                SyncCommand.tenant_id == sched.tenant_id,
                SyncCommand.command_type == "sync_all_companies",
                SyncCommand.status.in_(["pending", "fetched"]),
            ).first()
            if already_pending:
                continue

            cmd = SyncCommand(
                tenant_id=sched.tenant_id,
                device_id=sched.device_id,
                command_type="sync_all_companies",
                params="{}",
                status="pending",
                created_by="scheduler",
                created_at=now,
                expires_at=now + timedelta(hours=2),
            )
            db.add(cmd)
            sched.last_scheduled_at = now
            queued += 1

        if queued:
            db.commit()
            logger.info(f"[Scheduler] Queued sync_all_companies for {queued} device(s)")

    except Exception as e:
        logger.error(f"[Scheduler] schedule_syncs error: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


def reset_stale_commands() -> None:
    """
    Commands stuck in 'fetched' for longer than STALE_TIMEOUT_MINUTES are
    reset to 'pending' so the device can pick them up again after a crash/restart.
    """
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_TIMEOUT_MINUTES)
        stale = db.query(SyncCommand).filter(
            SyncCommand.status == "fetched",
            SyncCommand.fetched_at < cutoff,
        ).all()

        if stale:
            for cmd in stale:
                cmd.status = "pending"
                cmd.fetched_at = None
            db.commit()
            logger.info(f"[Scheduler] Reset {len(stale)} stale command(s) → pending")

    except Exception as e:
        logger.error(f"[Scheduler] reset_stale_commands error: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(schedule_syncs, "interval", seconds=60, id="schedule_syncs",
                       max_instances=1, coalesce=True)
    _scheduler.add_job(reset_stale_commands, "interval", minutes=5, id="reset_stale",
                       max_instances=1, coalesce=True)
    _scheduler.start()
    logger.info("[Scheduler] APScheduler started (schedule_syncs=60s, reset_stale=5m)")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[Scheduler] APScheduler stopped")
