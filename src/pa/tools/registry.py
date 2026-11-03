"""Tool registry (M2-19, PRD §9.2 / v1.1·K8).

Each tool declares name, schema, permissions and timeout. The local MCP
server exposes only tools whitelisted by the Skill; calls carrying one of
the seven mandatory-ask tags route through the judge layer (precedent
lookup first, decision card otherwise) before execution.
"""

import json
from dataclasses import dataclass, field

from pa.config.schema import SEVEN_TAGS

PERMISSION_KINDS = ("network", "fs", *sorted(SEVEN_TAGS))


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    permissions: tuple[str, ...] = ()
    timeout_s: int = 30


def _simple(name: str, description: str, properties: dict, required: list[str] | None = None):
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
        "title": name,
    }


BUILTIN_TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            name="http_fetch",
            description="受限 HTTP GET，读取公开文本资源",
            schema=_simple("http_fetch", "受限 HTTP GET", {"url": {"type": "string"}}),
            permissions=("network",),
            timeout_s=20,
        ),
        Tool(
            name="read_url",
            description="读取公开网页/JSON 并抽取正文",
            schema=_simple("read_url", "读取 URL", {"url": {"type": "string"}}, ["url"]),
            permissions=("network",),
            timeout_s=30,
        ),
        Tool(
            name="memory_search",
            description="检索记忆库（facts/lessons/precedents）",
            schema=_simple(
                "memory_search",
                "检索记忆",
                {"query": {"type": "string"}, "kind": {"type": "string"}},
                ["query"],
            ),
            timeout_s=10,
        ),
        Tool(
            name="memory_write",
            description="写入一条 fact 记忆",
            schema=_simple(
                "memory_write",
                "写入记忆",
                {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                ["title", "content"],
            ),
            timeout_s=10,
        ),
        Tool(
            name="feishu_notify",
            description="向 owner 发送飞书通知（对外消息）",
            schema=_simple(
                "feishu_notify",
                "发送通知",
                {"text": {"type": "string"}},
                ["text"],
            ),
            permissions=("external_message",),
            timeout_s=15,
        ),
        Tool(
            name="repo_read",
            description="只读访问镜像仓库中的文件",
            schema=_simple(
                "repo_read",
                "读取仓库文件",
                {"repo": {"type": "string"}, "path": {"type": "string"}},
                ["repo", "path"],
            ),
            permissions=("fs",),
            timeout_s=15,
        ),
    )
}


def validate_permissions(permissions) -> None:
    unknown = set(permissions) - set(PERMISSION_KINDS)
    if unknown:
        raise ValueError(f"unknown tool permissions: {sorted(unknown)}")


def register(registry: dict[str, Tool], tool: Tool) -> None:
    validate_permissions(tool.permissions)
    if tool.name in registry:
        raise ValueError(f"tool {tool.name} already registered")
    registry[tool.name] = tool


def whitelist_for(skill) -> list[Tool]:
    """Resolve a Skill's ``tools`` whitelist into registered Tool objects."""
    missing = [name for name in skill.tools if name not in BUILTIN_TOOLS]
    if missing:
        raise ValueError(f"skill references unknown tools: {missing}")
    return [BUILTIN_TOOLS[name] for name in skill.tools]


def mandatory_tags(tool: Tool) -> list[str]:
    """Tags that force judge-layer approval before execution (K8)."""
    return [tag for tag in tool.permissions if tag in SEVEN_TAGS]


@dataclass
class ToolSession:
    """Per-task one-shot token gating the local MCP endpoint (127.0.0.1)."""

    task_id: str
    token: str
    tools: tuple[str, ...] = ()
    used: bool = False
    calls: list[dict] = field(default_factory=list)


def issue_token(task_id: str, tools: list[Tool]) -> ToolSession:
    import secrets

    return ToolSession(
        task_id=task_id,
        token=secrets.token_urlsafe(24),
        tools=tuple(t.name for t in tools),
    )


def authorize(session: ToolSession, *, token: str, tool: str) -> dict:
    """Validate a tool call: token match, single use per task lifecycle,
    whitelist membership, and mandatory-tag routing."""
    if session.used and session.token != token:
        return {"ok": False, "reason": "bad_token"}
    if session.token != token:
        return {"ok": False, "reason": "bad_token"}
    if tool not in session.tools:
        return {"ok": False, "reason": "not_whitelisted"}
    tool_obj = BUILTIN_TOOLS[tool]
    gates = mandatory_tags(tool_obj)
    if gates:
        return {"ok": True, "requires_approval": gates, "tool": tool_obj}
    return {"ok": True, "requires_approval": [], "tool": tool_obj}


def describe(session: ToolSession) -> str:
    """MCP tools/list response body for this session."""
    return json.dumps(
        {
            "tools": [
                {
                    "name": name,
                    "description": BUILTIN_TOOLS[name].description,
                    "inputSchema": BUILTIN_TOOLS[name].schema,
                }
                for name in session.tools
            ]
        },
        ensure_ascii=False,
    )
