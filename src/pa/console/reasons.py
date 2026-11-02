"""Reason-code translation (M3-06, v1.1·D12).

Every page shows translated copy, never the internal code. Codes not in the
table fall back to the code itself so a missing translation is visible in
review instead of silently blank.
"""

TRANSLATIONS: dict[str, str] = {
    # task lifecycle
    "claimed": "任务已被引擎领取",
    "started": "引擎开始执行",
    "completed": "执行完成",
    "judged": "判断层已处理",
    "dropped": "核实后无需处理，已静默结案",
    "receipt_accepted": "回执合格，已接受",
    "needs_owner": "需要你确认后继续",
    "owner_cancelled": "你手动取消了任务",
    "lease_expired": "引擎租约过期，任务回队",
    "lease_recovered": "服务重启，任务已恢复排队",
    "retry_scheduled": "已安排自动重试",
    "retry": "执行失败，自动重派",
    "retry_exhausted": "自动重试已达上限",
    "context_added": "线程有新回复，已并入任务上下文",
    "mandate_voided": "原授权已失效，任务不再继续",
    "decision_timeout": "决定超时，任务已暂停",
    "policy_changed": "规则配置已变化，任务按新版本处理",
    # limit escalations
    "task_token_limit": "任务累计 token 用量已达上限",
    "task_timeout": "任务累计执行时间已达上限",
    "task_turn_limit": "任务累计轮数额度已达上限",
    "task_turn_usage_unknown": "历史轮数无法确认，请核实后决定",
    "source_read_failed": "无法读取任务来源的 URL 原文",
    # CI / decisions
    "precedent_applied": "已按先例自动处理",
    "fix_round_exhausted": "退回修改已达 3 轮",
    # API errors
    "validation_error": "参数校验未通过",
    "webhook_rejected": "Webhook 校验失败，已拒绝",
    "feishu_error": "飞书接口调用失败",
    "storage_error": "本地存储读写失败",
    "not_found": "资源不存在",
    "forbidden": "当前身份无权执行此操作",
    "conflict": "操作与当前状态冲突",
}


def translate(code: str) -> str:
    return TRANSLATIONS.get(code, code)


def translate_reasons(reason_json: str | list | None) -> list[dict]:
    """Translate a task_events reason array (or single object) for display."""
    import json

    if not reason_json:
        return []
    raw = reason_json if isinstance(reason_json, list) else json.loads(reason_json)
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for item in raw:
        code = item.get("code", "")
        entry = {"code": code, "text": translate(code)}
        if item.get("values"):
            entry["values"] = item["values"]
        if item.get("detail"):
            entry["detail"] = item["detail"]
        out.append(entry)
    return out


def stage_label(stage: str) -> str:
    """Human labels for the seven task_stage buckets (D10)."""
    return {
        "owner/answer": "等你决定",
        "owner/review": "等你审查",
        "owner/blocked_failed": "受阻 / 失败",
        "owner/delivery": "投递失败",
        "working/queued": "排队中",
        "working": "执行中",
        "idle": "已结束，可续接",
    }.get(stage, stage)
