"""Skill-ordered, deterministic eligibility checks before atomic slot reservation."""

import asyncio

from sqlalchemy import text

from pa.common import PAError, now
from pa.engines.registry import get_engine


def ordered_candidates(config, skill, *, role=None, preferred=None, previous=None):
    if not skill.enabled:
        return []
    choices = []
    selectors = [(role, None)] if role else [(ref.role, ref.ref) for ref in skill.engines]
    for role_name, direct in selectors:
        for name in config.engines.roles.get(role_name, []) if role_name else [direct]:
            entry = config.engines.engines.get(name)
            if not entry or not entry.enabled or name in choices:
                continue
            tier = "C" if entry.adapter == "gui" else "A"
            if tier not in skill.allowed_tiers or (tier == "C" and skill.risk_ceiling != "low"):
                continue
            choices.append(name)
    if preferred:
        return [preferred] if preferred in choices else []
    if previous in choices:
        choices = [name for name in choices if name != previous] + [previous]
    return choices


def slot_available(connection, config, engine_id, *, task_id=None):
    count = connection.execute(
        text("""
        SELECT count(*) FROM tasks t WHERE t.status IN ('leased','running')
          AND (:task IS NULL OR t.id<>:task)
          AND (t.lease_engine_id=:engine OR (t.lease_engine_id IS NULL AND EXISTS (
            SELECT 1 FROM runs r WHERE r.task_id=t.id AND r.engine_id=:engine
              AND r.ended_at IS NULL)))
        """),
        {"engine": engine_id, "task": task_id},
    ).scalar_one()
    return count < config.engines.engines[engine_id].max_concurrency


async def eligible(
    engine,
    config,
    skill,
    *,
    injected=None,
    role=None,
    preferred=None,
    previous=None,
    excluded=(),
    task_id=None,
    budget_allows=None,
):
    """Yield installed, healthy candidates; callers must recheck slots in their write transaction.

    Monetary budget enforcement belongs to M5-07; its estimate gate is injectable here.
    Task token/time/turn limits are independently enforced by the worker.
    """
    for name in ordered_candidates(
        config, skill, role=role, preferred=preferred, previous=previous
    ):
        if name in excluded:
            continue
        with engine.connect() as connection:
            record = (
                connection.execute(
                    text(
                        "SELECT status,breaker_state,breaker_until FROM engine_health "
                        "WHERE engine_id=:id"
                    ),
                    {"id": name},
                )
                .mappings()
                .one_or_none()
            )
            if record and (
                record["status"] in {"unhealthy", "unavailable"}
                or (
                    record["breaker_state"] == "open"
                    and (not record["breaker_until"] or record["breaker_until"] > now())
                )
            ):
                continue
            if not slot_available(connection, config, name, task_id=task_id):
                continue
        if budget_allows is not None and not budget_allows(name):
            continue
        try:
            candidate = (injected or {}).get(name) or get_engine(config, name)
            async with asyncio.timeout(5):
                health = await candidate.health()
        except (PAError, OSError, TimeoutError):
            continue
        if (
            not health.get("installed")
            or health.get("enabled") is False
            or health.get("healthy") is False
            or health.get("authentication")
            in {"required", "unauthenticated", "failed", "auth_required"}
        ):
            continue
        yield candidate, name
