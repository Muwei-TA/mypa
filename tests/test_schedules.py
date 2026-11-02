import json
import threading
from datetime import UTC, datetime, timedelta

import pytest
from apscheduler.events import EVENT_JOB_EXECUTED
from sqlalchemy import text

from pa.common import PAError
from pa.config.loader import load
from pa.config.setup import save_files
from pa.ingress.scheduler import ScheduleService, deliver_tick
from pa.store.db import transaction


def configure(facade, config_dir, *, mode="auto", cron="* * * * *"):
    save_files(
        config_dir,
        {
            "schedules.yaml": {
                "schedules": [
                    {
                        "id": "digest",
                        "cron": cron,
                        "event_type": "cron.digest",
                        "payload": {"skill": "sample-direct", "text": "概括文本"},
                    }
                ]
            },
            "rules.yaml": {
                "rules": [
                    {
                        "id": "r-cron",
                        "rank": 1000,
                        "match": {"source": "cron"},
                        "skill": "sample-direct",
                        "accept_mode": mode,
                    }
                ]
            },
        },
    )
    facade.config = load(config_dir)


@pytest.fixture
def scheduled(facade, config_dir):
    configure(facade, config_dir)
    service = ScheduleService(facade, config_dir)
    service.start()
    service.scheduler.pause()
    try:
        yield service
    finally:
        service.shutdown()


def due(service):
    """Simulate a persisted cron slot missed while the runtime was stopped."""
    at = (datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)).isoformat()
    with transaction(service.facade.engine) as c:
        cursor = json.loads(
            c.execute(text("SELECT value FROM system_state WHERE key='cron:digest'")).scalar_one()
        )
        cursor["next"] = at
        c.execute(
            text("UPDATE system_state SET value=:value WHERE key='cron:digest'"),
            {"value": json.dumps(cursor)},
        )
    service.sync()
    return service.scheduler.get_job("cron:digest").kwargs


@pytest.mark.parametrize(
    "value,path",
    [
        ({"schedules": [{"id": "x", "cron": "bad", "event_type": "cron.x"}]}, "cron"),
        (
            {
                "schedules": [
                    {"id": "x", "cron": "* * * * *", "event_type": "cron.x", "payload": []}
                ]
            },
            "payload",
        ),
        (
            {
                "schedules": [
                    {"id": "x", "cron": "* * * * *", "event_type": "cron.x", "unexpected": True}
                ]
            },
            "unexpected",
        ),
        (
            {"schedules": [{"id": "x", "cron": "* * * * *", "event_type": "cron.x"}] * 2},
            "schedules",
        ),
    ],
)
def test_invalid_schedules_report_field(config_dir, value, path):
    with pytest.raises(PAError, match=path):
        save_files(config_dir, {"schedules.yaml": value})


def test_invalid_timezone_is_rejected(config_dir):
    value = load(config_dir).main.model_dump(mode="json")
    value["timezone"] = "not/a/timezone"
    with pytest.raises(PAError, match="pa.yaml: timezone"):
        save_files(config_dir, {"pa.yaml": value})


def test_schedule_file_is_optional_for_existing_install(config_dir):
    (config_dir / "schedules.yaml").unlink()
    assert load(config_dir).schedules.schedules == []


def test_tick_is_idempotent_and_advances_cursor(scheduled):
    kwargs = due(scheduled)
    first = deliver_tick(**kwargs)
    second = deliver_tick(**kwargs)
    assert len(first["tasks"]) == 1 and second == {"skipped": True}
    events = scheduled.facade.list("events")["items"]
    assert len(events) == 1
    assert events[0]["dedup_key"] == "digest:" + kwargs["scheduled_at"]
    scheduled.sync()
    next_job = scheduled.scheduler.get_job("cron:digest")
    assert datetime.fromisoformat(next_job.kwargs["scheduled_at"]) == (
        datetime.fromisoformat(kwargs["scheduled_at"]) + timedelta(minutes=1)
    )


def test_ingest_failure_rolls_back_cursor_and_can_retry(scheduled, monkeypatch):
    kwargs = due(scheduled)

    def fail(*args):
        raise RuntimeError("simulated database ingest failure")

    with monkeypatch.context() as patch:
        patch.setattr("pa.ingress.scheduler.ingest", fail)
        with pytest.raises(RuntimeError):
            deliver_tick(**kwargs)
    assert scheduled.facade.list("events")["items"] == []
    assert len(deliver_tick(**kwargs)["tasks"]) == 1


def test_real_scheduler_restart_delivers_persisted_due_job(scheduled, config_dir):
    kwargs = due(scheduled)
    scheduled.shutdown()
    restarted = ScheduleService(scheduled.facade, config_dir)
    delivered = threading.Event()
    restarted.scheduler.add_listener(lambda event: delivered.set(), EVENT_JOB_EXECUTED)
    try:
        restarted.start()
        assert delivered.wait(3), "persisted date job was not delivered after restart"
        events = scheduled.facade.list("events")["items"]
        assert len(events) == 1 and events[0]["dedup_key"] == "digest:" + kwargs["scheduled_at"]
        assert len(scheduled.facade.list("tasks")["items"]) == 1
    finally:
        restarted.shutdown()


def test_paused_system_still_ingests_ticks(scheduled):
    scheduled.facade.pause()
    assert len(deliver_tick(**due(scheduled))["tasks"]) == 1
    assert scheduled.facade.status()["paused"]


def test_observe_cron_never_queues_task(facade, config_dir):
    configure(facade, config_dir, mode="observe")
    service = ScheduleService(facade, config_dir)
    try:
        service.start()
        service.scheduler.pause()
        assert deliver_tick(**due(service))["tasks"] == []
        assert len(facade.list("observations")["items"]) == 1
        assert facade.list("tasks")["items"] == []
    finally:
        service.shutdown()


def test_disabled_schedule_removes_job_and_rejects_stale_delivery(scheduled, config_dir):
    kwargs = due(scheduled)
    value = load(config_dir).schedules.model_dump(mode="json")
    value["schedules"][0]["enabled"] = False
    save_files(config_dir, {"schedules.yaml": value})
    scheduled.sync()
    assert scheduled.scheduler.get_jobs() == []
    assert deliver_tick(**kwargs) == {"skipped": True}
    assert scheduled.facade.list("events")["items"] == []


def test_changed_schedule_rejects_old_payload(scheduled, config_dir):
    kwargs = due(scheduled)
    value = load(config_dir).schedules.model_dump(mode="json")
    value["schedules"][0]["payload"]["text"] = "新配置"
    save_files(config_dir, {"schedules.yaml": value})
    assert deliver_tick(**kwargs) == {"skipped": True}
    scheduled.sync()
    assert scheduled.scheduler.get_job("cron:digest").kwargs["revision"] != kwargs["revision"]


def test_timezone_and_next_fire_survive_reconciliation(facade, config_dir):
    configure(facade, config_dir, cron="0 9 * * *")
    service = ScheduleService(facade, config_dir)
    try:
        service.start()
        before = service.scheduler.get_job("cron:digest").next_run_time
        service.sync()
        assert service.scheduler.get_job("cron:digest").next_run_time == before
        assert before.astimezone(UTC).hour == 1  # default Asia/Shanghai is UTC+8
    finally:
        service.shutdown()
