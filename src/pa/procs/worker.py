import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from pa.common import PAError, new_id, now
from pa.config.loader import load
from pa.config.schema import SEVEN_TAGS
from pa.effects.store import enqueue
from pa.engines.base import EngineRequest
from pa.engines.registry import get_engine
from pa.hub.dispatcher import claim, recover_expired
from pa.notify.feishu import target_payload
from pa.obs.redact import redact
from pa.skills.prompt_assembly import assemble
from pa.store.db import transaction
from pa.store.models import Reason
from pa.store.transitions import transition


class Worker:
    def __init__(self, facade, config_dir, *, engines=None):
        self.facade, self.config_dir, self.injected = facade, config_dir, engines or {}

    async def once(self, engine_id=None):
        config = load(self.config_dir)
        recover_expired(self.facade.engine)
        choices = [engine_id] if engine_id else config.engines.roles.get("dev", [])
        chosen = None
        for name in choices:
            try:
                candidate = self.injected.get(name) or get_engine(config, name)
            except PAError:
                continue
            if (await candidate.health())["installed"]:
                chosen, engine_id = candidate, name
                break
        if chosen is None:
            raise PAError("no enabled, installed CLI engine is configured")
        task = claim(self.facade.engine, config, engine_id)
        if not task:
            return None
        run_id = new_id()
        with transaction(self.facade.engine) as c:
            payload = json.loads(
                c.execute(
                    text("SELECT payload_json FROM events WHERE id=:id"), {"id": task["event_id"]}
                ).scalar_one()
            )
            recovered = bool(
                c.execute(
                    text("SELECT count(*) FROM runs WHERE task_id=:id AND recovered=1"),
                    {"id": task["id"]},
                ).scalar_one()
            )
            c.execute(
                text("""
                INSERT INTO runs(id,task_id,attempt,engine_id,tier,started_at,recovered)
                VALUES(:id,:task,:attempt,:engine,'A',:at,:recovered)
            """),
                {
                    "id": run_id,
                    "task": task["id"],
                    "attempt": task["retry_count"] + 1,
                    "engine": engine_id,
                    "at": now(),
                    "recovered": int(recovered),
                },
            )
        output_dir = config.main.data_path(self.config_dir) / "runs" / task["id"] / run_id
        content = payload.get("text") or json.dumps(payload, ensure_ascii=False)
        try:
            request = EngineRequest(
                task_id=task["id"],
                output_dir=output_dir,
                prompt=assemble(content, policy_version=config.policy_version, recovered=recovered),
            )
        except PAError:
            from pa.engines.base import EngineResult

            result = EngineResult(
                engine_id=engine_id, error="secret detected in input", error_kind="fatal"
            )
        else:
            running = asyncio.create_task(chosen.run(request))
            heartbeat = asyncio.create_task(self.heartbeat(task, asyncio.current_task()))
            try:
                result = await running
                if result.error_kind == "invalid_output":
                    request = request.model_copy(
                        update={
                            "output_dir": output_dir / "repair",
                            "prompt": request.prompt.replace(
                                "[requirements]",
                                "[requirements]\n上次回执无效，"
                                "这次严格遵守 Schema，普通文本任务的 tags 必须为空数组。",
                                1,
                            ),
                        }
                    )
                    result = await chosen.run(request)
            except asyncio.CancelledError:
                with transaction(self.facade.engine) as c:
                    c.execute(
                        text("UPDATE runs SET ended_at=:at,outcome='cancelled' WHERE id=:id"),
                        {"id": run_id, "at": now()},
                    )
                return {"task_id": task["id"], "status": "cancelled"}
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
        with transaction(self.facade.engine) as c:
            current = (
                c.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": task["id"]})
                .mappings()
                .one()
            )
            receipt = result.receipt
            c.execute(
                text("""
                UPDATE runs SET ended_at=:at,outcome=:outcome,error_class=:error,
                  receipt_json=:receipt,session_log_path=:log WHERE id=:id
            """),
                {
                    "id": run_id,
                    "at": now(),
                    "outcome": receipt.status if receipt else "failed",
                    "error": result.error_kind,
                    "receipt": json.dumps(
                        redact(receipt.model_dump(mode="json", exclude_none=True))
                    )
                    if receipt
                    else None,
                    "log": result.log_path,
                },
            )
            c.execute(
                text("""
                INSERT INTO cost_ledger(id,task_id,run_id,engine_id,skill_id,mode,input_tokens,
                  output_tokens,duration_s,cost_cny,est,at)
                VALUES(:id,:task,:run,:engine,'sample-direct','token',:input,:output,:duration,NULL,1,:at)
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
            if current["status"] != "running" or current["lease_owner"] != task["lease_owner"]:
                return {"task_id": task["id"], "status": current["status"]}
            need = (
                receipt is None
                or receipt.status in {"failed", "blocked", "needs_human"}
                or (
                    receipt.draft
                    or receipt.code_changed
                    or receipt.risk != "low"
                    or receipt.confidence < config.main.human_gate.confidence_below
                    or bool(set(receipt.tags) & SEVEN_TAGS)
                    or receipt.usage.input_tokens + receipt.usage.output_tokens > 200_000
                )
            )
            dropped = receipt is not None and receipt.status == "dropped"
            status = "awaiting_decision" if need and not dropped else "succeeded"
            next_task = transition(
                c,
                task["id"],
                status,
                current["version"],
                [Reason(code="needs_owner" if need else "receipt_accepted")],
            )
            if status == "succeeded":
                next_task = transition(
                    c, task["id"], "judged", next_task["version"], [Reason(code="judged")]
                )
                next_task = transition(
                    c,
                    task["id"],
                    "closed",
                    next_task["version"],
                    [Reason(code="dropped" if dropped else "completed")],
                )
            else:
                question = (
                    receipt.human_action.question
                    if receipt and receipt.human_action
                    else "此任务需要你接手处理。"
                )
                c.execute(
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
                summary = (
                    receipt.summary
                    if receipt
                    else "执行未完成，请检查本机 Agent 的登录或连接状态。"
                )
                if status == "awaiting_decision":
                    summary = "需要你处理：" + summary
                enqueue(
                    c,
                    task_id=task["id"],
                    kind="feishu.send",
                    idem_key=f"{task['id']}:result:{run_id}:1",
                    payload=target_payload(config, {"text": summary}),
                    order_key="feishu:" + config.notify.target.id,
                )
            c.execute(
                text("UPDATE tasks SET lease_owner=NULL,lease_until=NULL WHERE id=:id"),
                {"id": task["id"]},
            )
            return {"task_id": task["id"], "engine": engine_id, "status": next_task["status"]}

    async def heartbeat(self, task, running):
        while True:
            await asyncio.sleep(10)
            with transaction(self.facade.engine) as c:
                result = c.execute(
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
                    running.cancel()
                    return
