"""Persistent cron delivery, with event and next cursor committed together.

APScheduler date jobs wake up at the exact next cron slot stored in SQLite.
Reconciliation recreates a wakeup if a crash or delivery error removed it.
Downtime is caught up one slot per reconciliation, without changing dedup keys.
Only this ingress runs here; model execution and effects remain separate.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_RUNNING
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from pa.common import now
from pa.config.loader import load
from pa.hub.pipeline import ingest
from pa.ingress.cron import CronAdapter
from pa.store.db import transaction
from pa.store.facades import OwnerFacade

PREFIX = "cron:"


class IngressScheduler(BackgroundScheduler):
    def _process_jobs(self):
        # APScheduler 3.x wakes its timer once during shutdown. Check state
        # under the same lock used by job removal, including that last wakeup.
        with self._jobstores_lock:
            if self.state != STATE_RUNNING:
                return None
            return super()._process_jobs()


def signature(schedule, timezone):
    value = {"schedule": schedule.model_dump(mode="json"), "timezone": timezone}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _cursor(connection, schedule_id):
    raw = connection.execute(
        text("SELECT value FROM system_state WHERE key=:key"),
        {"key": PREFIX + schedule_id},
    ).scalar_one_or_none()
    return json.loads(raw) if raw else None


def _save_cursor(connection, schedule_id, value):
    connection.execute(
        text("""
        INSERT INTO system_state(key,value,updated_at) VALUES(:key,:value,:at)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
        """),
        {"key": PREFIX + schedule_id, "value": json.dumps(value), "at": now()},
    )


def deliver_tick(config_dir, schedule_id, revision, scheduled_at):
    """Global callable for APScheduler serialization; no credentials in job arguments."""
    facade = OwnerFacade(Path(config_dir))
    try:
        config = facade.config
        schedule = next((s for s in config.schedules.schedules if s.id == schedule_id), None)
        if (
            not schedule
            or not schedule.enabled
            or signature(schedule, config.main.timezone) != revision
        ):
            return {"skipped": True}
        at = datetime.fromisoformat(scheduled_at)
        if at.tzinfo is None or at > datetime.now(UTC):
            raise ValueError("scheduled_at must be a due timezone-aware time")
        with transaction(facade.engine) as connection:
            cursor = _cursor(connection, schedule_id)
            if cursor != {"revision": revision, "next": scheduled_at}:
                return {"skipped": True}
            event = CronAdapter().parse(
                {
                    "id": schedule.id,
                    "event_type": schedule.event_type,
                    "scheduled_at": scheduled_at,
                    "payload": schedule.payload,
                }
            )
            result = ingest(connection, event, config)
            trigger = CronTrigger.from_crontab(schedule.cron, timezone=config.main.timezone)
            following = trigger.get_next_fire_time(at, at + timedelta(microseconds=1))
            _save_cursor(
                connection,
                schedule_id,
                {
                    "revision": revision,
                    "next": following.astimezone(UTC).isoformat() if following else None,
                },
            )
            return result
    finally:
        facade.close()


class ScheduleService:
    """Run under the core singleton lock. Config changes reconcile once per core cycle."""

    def __init__(self, facade, config_dir):
        self.facade = facade
        self.config_dir = Path(config_dir).resolve()
        self.scheduler = IngressScheduler(
            jobstores={
                "default": SQLAlchemyJobStore(engine=facade.engine, tablename="scheduler_jobs")
            },
            executors={"default": ThreadPoolExecutor(1)},
            timezone="UTC",
        )

    def start(self):
        self.scheduler.start(paused=True)
        try:
            self.sync()
            self.scheduler.resume()
        except BaseException:
            self.scheduler.shutdown()
            raise

    def sync(self):
        config = load(self.config_dir)
        desired = {}
        with transaction(self.facade.engine) as connection:
            enabled_ids = {s.id for s in config.schedules.schedules if s.enabled}
            for row in connection.execute(
                text("SELECT key FROM system_state WHERE key LIKE 'cron:%'")
            ).all():
                if row[0].removeprefix(PREFIX) not in enabled_ids:
                    connection.execute(
                        text("DELETE FROM system_state WHERE key=:key"), {"key": row[0]}
                    )
            for schedule in config.schedules.schedules:
                if not schedule.enabled:
                    continue
                revision = signature(schedule, config.main.timezone)
                cursor = _cursor(connection, schedule.id)
                if not cursor or cursor["revision"] != revision:
                    trigger = CronTrigger.from_crontab(schedule.cron, timezone=config.main.timezone)
                    next_at = trigger.get_next_fire_time(None, datetime.now(UTC))
                    cursor = {
                        "revision": revision,
                        "next": next_at.astimezone(UTC).isoformat() if next_at else None,
                    }
                    _save_cursor(connection, schedule.id, cursor)
                if cursor["next"]:
                    desired[PREFIX + schedule.id] = {
                        "config_dir": str(self.config_dir),
                        "schedule_id": schedule.id,
                        "revision": revision,
                        "scheduled_at": cursor["next"],
                    }
        for job in self.scheduler.get_jobs():
            if job.id.startswith(PREFIX) and job.id not in desired:
                try:
                    self.scheduler.remove_job(job.id)
                except JobLookupError:
                    pass  # A due date job can finish between listing and removal.
        for job_id, kwargs in desired.items():
            job = self.scheduler.get_job(job_id)
            if job and job.kwargs == kwargs:
                continue
            self.scheduler.add_job(
                deliver_tick,
                "date",
                id=job_id,
                run_date=datetime.fromisoformat(kwargs["scheduled_at"]),
                kwargs=kwargs,
                replace_existing=True,
                misfire_grace_time=None,
                max_instances=1,
            )

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.pause()
            self.scheduler.get_jobs()  # Wait for an in-progress scan before changing to STOPPED.
            self.scheduler.shutdown(wait=True)
