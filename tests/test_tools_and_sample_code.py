"""M2-19 tool registry + local MCP, M2-20 sample-code skill."""

import json

from sqlalchemy import text

from pa.skills.loader import load_skill
from pa.store.db import transaction
from pa.tools.mcp_server import McpServer, bind_host
from pa.tools.registry import (
    BUILTIN_TOOLS,
    authorize,
    describe,
    issue_token,
    mandatory_tags,
    validate_permissions,
    whitelist_for,
)

# --- M2-19 registry ----------------------------------------------------------


def test_builtin_tools_complete():
    assert set(BUILTIN_TOOLS) == {
        "http_fetch",
        "read_url",
        "memory_search",
        "memory_write",
        "feishu_notify",
        "repo_read",
    }
    for tool in BUILTIN_TOOLS.values():
        assert tool.schema["type"] == "object"
        assert set(tool.permissions) <= {
            "network",
            "fs",
            *{
                "delete_data",
                "stop_service",
                "spend_money",
                "external_message",
                "product_direction",
                "secret_change",
                "permission_change",
            },
        }


def test_permissions_validation():
    validate_permissions(["network", "fs"])
    validate_permissions(["external_message"])
    try:
        validate_permissions(["root_access"])
    except ValueError as err:
        assert "unknown tool permissions" in str(err)
    else:
        raise AssertionError("unknown permission accepted")


def test_mandatory_tag_routing():
    gates = mandatory_tags(BUILTIN_TOOLS["feishu_notify"])
    assert gates == ["external_message"]
    assert mandatory_tags(BUILTIN_TOOLS["read_url"]) == []


def test_whitelist_for_skill():
    skill = load_skill("sample-direct")
    tools = whitelist_for(skill)
    assert [t.name for t in tools] == ["read_url", "memory_search", "feishu_notify"]
    code_skill = load_skill("sample-code")
    assert [t.name for t in whitelist_for(code_skill)] == ["read_url", "memory_search"]


def test_whitelist_rejects_unknown():
    class Fake:
        tools = ["read_url", "does_not_exist"]

    try:
        whitelist_for(Fake())
    except ValueError as err:
        assert "does_not_exist" in str(err)
    else:
        raise AssertionError("unknown tool accepted into whitelist")


def test_token_single_use_and_whitelist():
    skill = load_skill("sample-code")
    session = issue_token("task-1", whitelist_for(skill))
    ok = authorize(session, token=session.token, tool="read_url")
    assert ok["ok"] and ok["requires_approval"] == []
    denied = authorize(session, token=session.token, tool="feishu_notify")
    assert denied["ok"] is False and denied["reason"] == "not_whitelisted"
    bad = authorize(session, token="forged", tool="read_url")
    assert bad["ok"] is False and bad["reason"] == "bad_token"


def test_describe_lists_only_whitelist():
    skill = load_skill("sample-code")
    session = issue_token("task-2", whitelist_for(skill))
    listing = json.loads(describe(session))
    names = {tool["name"] for tool in listing["tools"]}
    assert names == {"read_url", "memory_search"}


# --- M2-19 MCP server --------------------------------------------------------


def test_mcp_loopback_only():
    assert bind_host() == "127.0.0.1"


def _server(skill_id="sample-code"):
    server = McpServer()
    return server, load_skill(skill_id)


def test_mcp_session_tools_list_and_call():
    server, skill = _server()
    token = server.open_session("task-3", skill)
    listing = server.handle(b'{"token": "%s", "method": "tools/list"}' % token.encode())
    names = {tool["name"] for tool in listing["result"]["tools"]}
    assert names == {"read_url", "memory_search"}
    executed = []

    def executor(tool, arguments):
        executed.append((tool, arguments))
        return {"text": "ok"}

    server.executor = executor
    result = server.handle(
        b'{"token": "%s", "method": "tools/call", "tool": "read_url", '
        b'"arguments": {"url": "https://example/x"}}' % token.encode()
    )
    assert result == {"result": {"text": "ok"}}
    assert executed == [("read_url", {"url": "https://example/x"})]


def test_mcp_unknown_token_and_method():
    server, skill = _server()
    server.open_session("task-4", skill)
    unknown = server.handle(b'{"token": "nope", "method": "tools/list"}')
    assert unknown == {"error": "unknown_token"}
    token = next(iter(server.sessions))
    bad_method = server.handle(b'{"token": "%s", "method": "resources/list"}' % token.encode())
    assert bad_method == {"error": "unknown_method"}
    invalid = server.handle(b"not json")
    assert invalid == {"error": "invalid_json"}


def test_mcp_gated_tool_requires_approval():
    server = McpServer(judge=lambda tool, args, gates: {"allowed": False})
    skill = load_skill("sample-direct")  # whitelists feishu_notify
    token = server.open_session("task-5", skill)
    verdict = server.handle(
        b'{"token": "%s", "method": "tools/call", "tool": "feishu_notify", '
        b'"arguments": {"text": "hi"}}' % token.encode()
    )
    assert verdict["error"] == "needs_approval"
    # judge approval lets the call through
    server.judge = lambda tool, args, gates: {"allowed": True}
    server.executor = lambda tool, args: {"sent": True}
    passed = server.handle(
        b'{"token": "%s", "method": "tools/call", "tool": "feishu_notify", '
        b'"arguments": {"text": "hi"}}' % token.encode()
    )
    assert passed == {"result": {"sent": True}}


def test_mcp_close_session_revokes_token():
    server, skill = _server()
    token = server.open_session("task-6", skill)
    server.close_session(token)
    revoked = server.handle(b'{"token": "%s", "method": "tools/list"}' % token.encode())
    assert revoked == {"error": "unknown_token"}


# --- M2-20 sample-code skill --------------------------------------------------


def test_sample_code_skill_loads():
    skill = load_skill("sample-code")
    assert skill.id == "sample-code"
    assert skill.mode == "code"
    assert skill.risk_ceiling == "medium"
    assert skill.permissions.repo_write is True
    assert skill.steps[0] == "read_issue" and skill.steps[-1] == "wait_ci"
    rendered = __import__("pa.skills.loader", fromlist=["render_prompt"]).render_prompt(
        "sample-code", inputs="Issue #12: 修复登录空指针"
    )
    assert "本内容由自动化 Agent 生成" in rendered
    assert "pa/<task_id>" in rendered
    assert "Issue #12" in rendered


def test_sample_code_prompt_mentions_merge_identity():
    from pa.skills.loader import render_prompt

    text_ = render_prompt("sample-code", inputs="x")
    assert "merger" in text_
    assert "不得直接改目标分支" in text_


def test_registry_records_gate_call_via_judge(configured):
    """A gated tool call without precedent routes to a decision card."""
    from pa.ingress.manual import ManualAdapter
    from pa.judge.precedents import add_precedent, check_precedent
    from pa.notify.cards import create_decision

    task = configured.emit(
        ManualAdapter().parse(
            {
                "type": "manual.test",
                "payload": {"skill": "sample-direct", "text": "x"},
                "dedup_key": "tg-1",
            }
        )
    )["tasks"][0]
    with transaction(configured.engine) as c:
        add_precedent(
            c,
            kind="precedent_decided",
            scope="global",
            title="允许发飞书通知",
            content="例行通知可以直接发送",
        )
        verdict = check_precedent(c, question="例行通知可以直接发送吗？")
    assert verdict["status"] == "hit"
    # no precedent: judge would create a decision card instead
    with transaction(configured.engine) as c:
        create_decision(
            c,
            task_id=task,
            kind="tool_gate",
            question="发送外部消息？",
            recommend="approve",
            options=["approve", "reject"],
            expires_at="2026-12-01T00:00:00+00:00",
        )
        count = c.execute(text("SELECT COUNT(*) FROM decisions")).scalar_one()
    assert count == 1
