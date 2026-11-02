import asyncio
import json
import secrets
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from pa.common import PAError, new_id, now
from pa.config.loader import load
from pa.config.schema import SEVEN_TAGS
from pa.engines.base import EngineRequest, EngineResult
from pa.engines.selector import eligible, slot_available
from pa.hub.dispatcher import claim, recover_expired
from pa.hub.pause import state
from pa.notify.dispatch import request_notification
from pa.notify.levels import P1, P2
from pa.obs.redact import redact
from pa.plugins import discover
from pa.skills.limits import ExecutionBudget
from pa.skills.loader import load_skill
from pa.skills.prompt_assembly import assemble
from pa.skills.receipt import Usage
from pa.store.db import transaction
from pa.store.models import Reason
from pa.store.transitions import transition


class Worker:
    def __init__(self, facade, config_dir, *, engines=None, sleeper=None, tools=None):
        self.facade = facade
        self.config_dir = config_dir
        self.injected = engines or {}
        self.tools = tools or {}
        # Injected in tests so transient backoff does not slow the suite.
        self.sleeper = sleeper or asyncio.sleep

    async def once(self, engine_id=None):
        config = load(self.config_dir)
        recover_expired(self.facade.engine)
        for queued in self._queue_candidates():
            skill = load_skill(queued["skill_id"])
            role = self._engine_role(config, queued)
            async for chosen, chosen_id in eligible(
                self.facade.engine,
                config,
                skill,
                injected=self.injected,
                role=role,
                preferred=engine_id,
                previous=queued["engine_hint"],
            ):
                task = claim(
                    self.facade.engine, config, chosen_id, task_id=queued["id"], skill=skill
                )
                if task is not None:
                    return await self._drive(task, chosen, chosen_id, config)
        return None

    def _queue_candidates(self):
        with self.facade.engine.connect() as connection:
            if state(connection)["paused"]:
                return []
            return (
                connection.execute(
                    text(
                        "SELECT * FROM tasks WHERE status='queued' "
                        "AND skill_id='sample-direct' ORDER BY pinned DESC,priority ASC,"
                        "manual_rank ASC NULLS LAST,skill_rank ASC,enqueued_at ASC LIMIT 1"
                    )
                )
                .mappings()
                .all()
            )

    def _engine_role(self, config, task):
        mandate = json.loads(task["mandate_json"])
        return next(
            (
                r.engine_role
                for r in config.rules.rules
                if r.id in mandate["matched_rules"] and r.engine_role
            ),
            None,
        )

    async def _another_engine(self, config, used_engines, task):
        skill = load_skill(task["skill_id"])
        async for candidate, name in eligible(
            self.facade.engine,
            config,
            skill,
            injected=self.injected,
            role=self._engine_role(config, task),
            excluded=used_engines,
            task_id=task["id"],
        ):
            with transaction(self.facade.engine) as connection:
                if not slot_available(connection, config, name, task_id=task["id"]):
                    continue
                reserved = connection.execute(
                    text(
                        "UPDATE tasks SET lease_engine_id=:engine WHERE id=:id "
                        "AND status='running' AND lease_owner=:owner"
                    ),
                    {"id": task["id"], "engine": name, "owner": task["lease_owner"]},
                )
                if reserved.rowcount:
                    task["lease_engine_id"] = name
                    return candidate, name
        return None, None

    async def _drive(self, task, engine, engine_id, config):
        skill = load_skill(task["skill_id"])
        with self.facade.engine.connect() as connection:
            usage = connection.execute(
                text("""
                SELECT COALESCE(sum(COALESCE(input_tokens,0) + COALESCE(output_tokens,0)),0),
                  COALESCE(sum(duration_s),0) FROM cost_ledger WHERE task_id=:id
                """),
                {"id": task["id"]},
            ).one()
            turns, unknown = connection.execute(
                text(
                    "SELECT COALESCE(sum(turns_charged),0),COALESCE(max(turns_unknown),0) "
                    "FROM runs WHERE task_id=:id"
                ),
                {"id": task["id"]},
            ).one()
        if unknown:
            return self._escalate_limit(task, "task_turn_usage_unknown", config)
        budget = ExecutionBudget(
            max_tokens=skill.budget.per_task_tokens,
            timeout_s=min(skill.timeout_min, config.main.limits.default_task_timeout_min) * 60,
            tokens=usage[0],
            duration_s=usage[1],
            max_turns=skill.budget.per_task_turns,
            turns=turns,
        )
        used_engines = {engine_id}
        transient_retries = 0
        max_transient = config.main.limits.transient_retries
        while True:
            if stopped := self._stopped(task):
                return stopped
            if reason := budget.exhausted:
                return self._escalate_limit(task, reason, config)
            run_id, result = await self._attempt(task, engine, engine_id, config, budget)
            if stopped := self._stopped(task):
                return stopped
            if result.error_kind == "cancelled":
                return {"task_id": task["id"], "status": "cancelled"}
            if result.error_kind in {
                "task_token_limit",
                "task_timeout",
                "task_turn_limit",
                "source_read_failed",
            }:
                return self._escalate_limit(task, result.error_kind, config)
            if result.receipt is not None and not result.error:
                return await self._finalize(task, run_id, result, engine_id, config)
            kind = result.error_kind
            if kind == "transient" and transient_retries < max_transient:
                transient_retries += 1
                await self._backoff(transient_retries)
                continue
            if kind in ("auth", "quota", "invalid_output"):
                next_engine, next_id = await self._another_engine(config, used_engines, task)
                if next_engine is not None:
                    engine, engine_id = next_engine, next_id
                    used_engines.add(engine_id)
                    continue
            return self._requeue_or_escalate(task, result, engine_id, config)

    async def _bounded_call(self, engine, engine_id, request, budget, run_id, task, config):
        allowance = min(config.engines.engines[engine_id].max_turns, budget.remaining_turns)
        if allowance <= 0:
            return EngineResult(
                engine_id=engine_id, error="turn allowance exhausted", error_kind="task_turn_limit"
            )
        with transaction(self.facade.engine) as connection:
            was_estimated = connection.execute(
                text("SELECT turns_estimated FROM runs WHERE id=:id"), {"id": run_id}
            ).scalar_one()
            reservation = connection.execute(
                text("""
                UPDATE runs SET turns_charged=turns_charged+:turns,turns_estimated=1
                WHERE id=:run AND EXISTS (
                    SELECT 1 FROM tasks WHERE id=:task AND status='running' AND lease_owner=:owner)
                """),
                {
                    "run": run_id,
                    "turns": allowance,
                    "task": task["id"],
                    "owner": task["lease_owner"],
                },
            )
            if not reservation.rowcount:
                return EngineResult(engine_id=engine_id, error="cancelled", error_kind="cancelled")
        # Reserve durably before the call. Missing usage, cancellation or a crash
        # retains the whole allowance instead of resetting it on redispatch.
        budget.turns += allowance
        request = request.model_copy(update={"max_turns": allowance})
        started = time.monotonic()
        try:
            async with asyncio.timeout(budget.remaining_s):
                result = (await engine.run(request)).model_copy(deep=True)
        except TimeoutError:
            result = EngineResult(
                engine_id=engine_id,
                error="task execution time exhausted",
                error_kind="task_timeout",
            )
        # Use measured time even when an adapter cannot report usage.
        result.usage.duration_s = max(result.usage.duration_s, time.monotonic() - started)
        budget.consume(result.usage)
        if result.turns is not None:
            difference = result.turns - allowance
            budget.turns += difference
            with transaction(self.facade.engine) as connection:
                connection.execute(
                    text(
                        "UPDATE runs SET turns_charged=turns_charged+:difference,"
                        "turns_estimated=:estimated WHERE id=:run"
                    ),
                    {"run": run_id, "difference": difference, "estimated": was_estimated},
                )
            if result.turns > allowance:
                result.error = "engine exceeded its requested turn limit"
                result.error_kind = "task_turn_limit"
        if reason := budget.exhausted:
            result.error = reason
            result.error_kind = reason
        return result

    async def _prepare_request(self, payload, task, config, output_dir, recovered, budget):
        started = time.monotonic()
        try:
            async with asyncio.timeout(budget.remaining_s):
                content = payload.get("text") or json.dumps(payload, ensure_ascii=False)
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                if redact(content) != content:
                    raise PAError("input appears to contain a secret")
                url = payload.get("url")
                if url is None and content.strip().startswith(("https://", "http://")):
                    url = content.strip() if len(content.split()) == 1 else None
                if url is not None:
                    if not isinstance(url, str):
                        raise PAError("source URL must be a string", "source_read_failed")
                    skill = load_skill(task["skill_id"])
                    if "read_url" not in skill.tools:
                        raise PAError("Skill does not allow read_url", "source_read_failed")
                    reader = self.tools.get("read_url")
                    if reader is None:
                        entry = discover("pa.tools").get("read_url")
                        if entry is None:
                            raise PAError("read_url is not installed", "source_read_failed")
                        reader = entry.load()()
                    source = await reader.read(url, permissions=skill.permissions)
                    content = (
                        f"原始输入：\n{content}\n\n已读取来源：{source.url}\n原文：\n{source.text}"
                    )
                return EngineRequest(
                    task_id=task["id"],
                    output_dir=output_dir,
                    prompt=assemble(
                        content,
                        policy_version=config.policy_version,
                        recovered=recovered,
                        skill_id=task["skill_id"],
                    ),
                )
        finally:
            budget.consume(Usage(duration_s=time.monotonic() - started))

    async def _attempt(self, task, engine, engine_id, config, budget):
        run_id = new_id()
        with transaction(self.facade.engine) as connection:
            payload = json.loads(
                connection.execute(
                    text("SELECT payload_json FROM events WHERE id=:id"),
                    {"id": task["event_id"]},
                ).scalar_one()
            )
            recovered = bool(
                connection.execute(
                    text("SELECT count(*) FROM runs WHERE task_id=:id AND recovered=1"),
                    {"id": task["id"]},
                ).scalar_one()
            )
            attempt = connection.execute(
                text("SELECT COALESCE(MAX(attempt), 0) + 1 FROM runs WHERE task_id=:id"),
                {"id": task["id"]},
            ).scalar_one()
            connection.execute(
                text("""
                INSERT INTO runs(id,task_id,attempt,engine_id,tier,started_at,recovered)
                VALUES(:id,:task,:attempt,:engine,'A',:at,:recovered)
            """),
                {
                    "id": run_id,
                    "task": task["id"],
                    "attempt": attempt,
                    "engine": engine_id,
                    "at": now(),
                    "recovered": int(recovered),
                },
            )
        output_dir = config.main.data_path(self.config_dir) / "runs" / task["id"] / run_id
        started = time.monotonic()
        completed_usage = Usage()
        before_preparation = budget.duration_s
        preparing = asyncio.create_task(
            self._prepare_request(payload, task, config, output_dir, recovered, budget)
        )
        running_ref = [preparing]
        heartbeat = asyncio.create_task(self._heartbeat(task, running_ref))
        request = None
        try:
            try:
                request = await preparing
            except TimeoutError:
                result = EngineResult(
                    engine_id=engine_id,
                    error="task preparation timed out",
                    error_kind="task_timeout",
                )
            except PAError as exc:
                result = EngineResult(
                    engine_id=engine_id,
                    error="source could not be read"
                    if exc.code == "source_read_failed"
                    else "secret detected in input",
                    error_kind="source_read_failed"
                    if exc.code == "source_read_failed"
                    else "fatal",
                )
            finally:
                preparation_s = budget.duration_s - before_preparation
            if request is not None:
                if self._stopped(task):
                    result = EngineResult(
                        engine_id=engine_id, error="cancelled", error_kind="cancelled"
                    )
                elif reason := budget.exhausted:
                    result = EngineResult(engine_id=engine_id, error=reason, error_kind=reason)
                else:
                    running_ref[0] = asyncio.create_task(
                        self._bounded_call(engine, engine_id, request, budget, run_id, task, config)
                    )
                    result = await running_ref[0]
                completed_usage = result.usage.model_copy(deep=True)
                if result.error_kind == "invalid_output" and not self._stopped(task):
                    # PRD §8.3: one same-engine correction before switching engines.
                    repair = request.model_copy(
                        update={
                            "output_dir": output_dir / "repair",
                            "prompt": request.prompt.replace(
                                "[requirements]",
                                "[requirements]\n上次回执无效，这次严格遵守 Schema，"
                                "普通文本任务的 tags 必须为空数组。",
                                1,
                            ),
                        }
                    )
                    previous = result.usage.model_copy(deep=True)
                    running_ref[0] = asyncio.create_task(
                        self._bounded_call(engine, engine_id, repair, budget, run_id, task, config)
                    )
                    result = await running_ref[0]
                    result.usage = Usage(
                        input_tokens=previous.input_tokens + result.usage.input_tokens,
                        output_tokens=previous.output_tokens + result.usage.output_tokens,
                        duration_s=previous.duration_s + result.usage.duration_s,
                    )
                    if result.receipt:
                        result.receipt.usage = result.usage
            # Preparation (including URL fetch) consumes the same task time allowance.
            # Engine time is already included in result.usage; do not count it twice.
            result.usage.duration_s += preparation_s
            if result.receipt:
                result.receipt.usage = result.usage
        except asyncio.CancelledError:
            completed_usage.duration_s = max(
                completed_usage.duration_s,
                time.monotonic() - started,
                budget.duration_s - before_preparation,
            )
            cancelled = EngineResult(
                engine_id=engine_id,
                error="cancelled",
                error_kind="cancelled",
                usage=completed_usage,
            )
            self._record_attempt(run_id, task, cancelled, engine_id)
            if asyncio.current_task().cancelling():
                raise
            return run_id, cancelled
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        self._record_attempt(run_id, task, result, engine_id)
        return run_id, result

    def _record_attempt(self, run_id, task, result, engine_id):
        with transaction(self.facade.engine) as connection:
            receipt = result.receipt
            connection.execute(
                text("""
                UPDATE runs SET ended_at=:at,outcome=:outcome,error_class=:error,
                  receipt_json=:receipt,session_log_path=:log WHERE id=:id
            """),
                {
                    "id": run_id,
                    "at": now(),
                    "outcome": (
                        "cancelled"
                        if result.error_kind == "cancelled"
                        else receipt.status
                        if receipt and not result.error
                        else "failed"
                    ),
                    "error": result.error_kind,
                    "receipt": json.dumps(
                        redact(receipt.model_dump(mode="json", exclude_none=True))
                    )
                    if receipt
                    else None,
                    "log": result.log_path,
                },
            )
            connection.execute(
                text("""
                INSERT INTO cost_ledger(id,task_id,run_id,engine_id,skill_id,mode,input_tokens,
                  output_tokens,duration_s,cost_cny,est,at)
                VALUES(:id,:task,:run,:engine,'sample-direct','token',:input,:output,:duration,
                  NULL,1,:at)
            """),
                {
                    "id": new_id(),
                    "task": task["id"],
                    "run": run_id,
                    "engine": engine_id,
                    "input": result.usage.input_tokens,
                    "output": result.usage.output_tokens,
                    "duration": result.usage.duration_s,
                    "at": now(),
                },
            )

    async def _finalize(self, task, run_id, result, engine_id, config):
        with transaction(self.facade.engine) as connection:
            current = (
                connection.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": task["id"]})
                .mappings()
                .one()
            )
            receipt = result.receipt
            if current["status"] != "running" or current["lease_owner"] != task["lease_owner"]:
                return {"task_id": task["id"], "status": current["status"]}
            need = (
                receipt.status in {"failed", "blocked", "needs_human"}
                or receipt.draft
                or receipt.code_changed
                or receipt.risk != "low"
                or receipt.confidence < config.main.human_gate.confidence_below
                or bool(set(receipt.tags) & SEVEN_TAGS)
            )
            dropped = receipt.status == "dropped"
            status = "awaiting_decision" if need and not dropped else "succeeded"
            next_task = transition(
                connection,
                task["id"],
                status,
                current["version"],
                [Reason(code="needs_owner" if need else "receipt_accepted")],
            )
            if status == "succeeded":
                next_task = transition(
                    connection,
                    task["id"],
                    "judged",
                    next_task["version"],
                    [Reason(code="judged")],
                )
                next_task = transition(
                    connection,
                    task["id"],
                    "closed",
                    next_task["version"],
                    [Reason(code="dropped" if dropped else "completed")],
                )
            else:
                question = (
                    receipt.human_action.question
                    if receipt.human_action
                    else "此任务需要你接手处理。"
                )
                connection.execute(
                    text("""
                    INSERT INTO decisions(
                      id,task_id,kind,question,nonce,status,expires_at,reason_json)
                    VALUES(:id,:task,'task',:question,:nonce,'pending',:expires,:reason)
                """),
                    {
                        "id": new_id(),
                        "task": task["id"],
                        "question": redact(question),
                        "nonce": secrets.token_urlsafe(24),
                        "expires": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                        "reason": json.dumps([{"code": result.error_kind or "needs_owner"}]),
                    },
                )
            mandate = json.loads(task["mandate_json"])
            rule = next((r for r in config.rules.rules if r.id in mandate["matched_rules"]), None)
            notify = status == "awaiting_decision" or (rule and rule.notify == "result")
            if (
                notify
                and not dropped
                and config.sources.sources.feishu.enabled
                and config.notify.target.id
            ):
                summary = receipt.summary
                if status == "awaiting_decision":
                    summary = "需要你处理：" + summary
                level = P1 if status == "awaiting_decision" else P2
                request_notification(
                    connection,
                    config=config,
                    task_id=task["id"],
                    level=level,
                    text_=summary,
                )
            connection.execute(
                text(
                    "UPDATE tasks SET lease_owner=NULL,lease_until=NULL,lease_engine_id=NULL "
                    "WHERE id=:id"
                ),
                {"id": task["id"]},
            )
            return {
                "task_id": task["id"],
                "engine": engine_id,
                "status": next_task["status"],
            }

    def _requeue_or_escalate(self, task, result, engine_id, config):
        with transaction(self.facade.engine) as connection:
            current = (
                connection.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": task["id"]})
                .mappings()
                .one()
            )
            if current["status"] != "running" or current["lease_owner"] != task["lease_owner"]:
                return {"task_id": task["id"], "status": current["status"]}
            if current["retry_count"] < config.main.limits.max_retry:
                next_task = transition(
                    connection,
                    task["id"],
                    "failed",
                    current["version"],
                    [Reason(code="retry_scheduled")],
                )
                transition(
                    connection,
                    task["id"],
                    "queued",
                    next_task["version"],
                    [Reason(code="retry")],
                )
                connection.execute(
                    text("""
                    UPDATE tasks SET retry_count=retry_count + 1, engine_hint=:engine,
                      lease_owner=NULL, lease_until=NULL,lease_engine_id=NULL WHERE id=:id
                """),
                    {"id": task["id"], "engine": engine_id},
                )
                return {
                    "task_id": task["id"],
                    "status": "queued",
                    "retry_count": current["retry_count"] + 1,
                    "engine_hint": engine_id,
                }
            next_task = transition(
                connection,
                task["id"],
                "awaiting_decision",
                current["version"],
                [Reason(code="retry_exhausted")],
            )
            connection.execute(
                text("""
                INSERT INTO decisions(id,task_id,kind,question,nonce,status,expires_at,
                  reason_json)
                VALUES(:id,:task,'task',:question,:nonce,'pending',:expires,:reason)
            """),
                {
                    "id": new_id(),
                    "task": task["id"],
                    "question": "自动重试已达上限，需要你接手处理。",
                    "nonce": secrets.token_urlsafe(24),
                    "expires": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                    "reason": json.dumps(
                        [
                            {"code": "retry_exhausted"},
                            {"code": result.error_kind or "unknown"},
                        ]
                    ),
                },
            )
            connection.execute(
                text(
                    "UPDATE tasks SET lease_owner=NULL,lease_until=NULL,lease_engine_id=NULL "
                    "WHERE id=:id"
                ),
                {"id": task["id"]},
            )
            if config.sources.sources.feishu.enabled and config.notify.target.id:
                request_notification(
                    connection,
                    config=config,
                    task_id=task["id"],
                    level=P1,
                    text_="自动重试已达上限，需要你接手处理。",
                )
            return {
                "task_id": task["id"],
                "status": next_task["status"],
                "reason": "retry_exhausted",
            }

    def _escalate_limit(self, task, reason, config):
        question = {
            "task_token_limit": "任务累计 token 用量已达上限，需要你接手处理。",
            "task_timeout": "任务累计执行时间已达上限，需要你接手处理。",
            "task_turn_limit": "任务累计轮数额度已达上限，需要你接手处理。",
            "task_turn_usage_unknown": (
                "此任务的历史轮数无法确认。请核实既有运行记录后决定如何继续。"
            ),
            "source_read_failed": (
                "无法读取此任务的 URL 原文。请提供可访问的许可域名链接或粘贴原文。"
            ),
        }[reason]
        with transaction(self.facade.engine) as connection:
            current = (
                connection.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": task["id"]})
                .mappings()
                .one()
            )
            if current["status"] != "running" or current["lease_owner"] != task["lease_owner"]:
                return {"task_id": task["id"], "status": current["status"]}
            transition(
                connection,
                task["id"],
                "awaiting_decision",
                current["version"],
                [Reason(code=reason)],
            )
            connection.execute(
                text("""
                INSERT INTO decisions(id,task_id,kind,question,nonce,status,expires_at,reason_json)
                VALUES(:id,:task,'task',:question,:nonce,'pending',:expires,:reason)
                """),
                {
                    "id": new_id(),
                    "task": task["id"],
                    "question": question,
                    "nonce": secrets.token_urlsafe(24),
                    "expires": (
                        datetime.now(UTC) + timedelta(hours=config.main.limits.decision_timeout_h)
                    ).isoformat(),
                    "reason": json.dumps([{"code": reason}]),
                },
            )
            connection.execute(
                text(
                    "UPDATE tasks SET lease_owner=NULL,lease_until=NULL,lease_engine_id=NULL "
                    "WHERE id=:id"
                ),
                {"id": task["id"]},
            )
            if config.sources.sources.feishu.enabled and config.notify.target.id:
                request_notification(
                    connection, config=config, task_id=task["id"], level=P1, text_=question
                )
        return {"task_id": task["id"], "status": "awaiting_decision", "reason": reason}

    def _stopped(self, task):
        with self.facade.engine.connect() as connection:
            current = (
                connection.execute(
                    text("SELECT status,lease_owner FROM tasks WHERE id=:id"), {"id": task["id"]}
                )
                .mappings()
                .one()
            )
        if current["status"] != "running" or current["lease_owner"] != task["lease_owner"]:
            return {"task_id": task["id"], "status": current["status"]}
        return None

    async def _backoff(self, attempt):
        # Exponential backoff with jitter (PRD §16.2): base 0.5s -> 0.5, 1, 2.
        base = 0.5 * 2 ** (attempt - 1)
        jitter = secrets.SystemRandom().uniform(0, base * 0.25)
        await self.sleeper(base + jitter)

    async def _heartbeat(self, task, running_ref):
        while True:
            await asyncio.sleep(10)
            with transaction(self.facade.engine) as connection:
                result = connection.execute(
                    text("""
                    UPDATE tasks SET lease_until=:until
                    WHERE id=:id AND lease_owner=:owner AND status='running'
                """),
                    {
                        "id": task["id"],
                        "owner": task["lease_owner"],
                        "until": (datetime.now(UTC) + timedelta(seconds=90)).isoformat(),
                    },
                )
                if not result.rowcount:
                    running_ref[0].cancel()
                    return
