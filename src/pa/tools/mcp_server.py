"""Local MCP server (M2-19, PRD §9.2).

Binds 127.0.0.1 only, authenticates each task with a one-shot token, exposes
only the Skill's whitelisted tools, and routes seven-tag calls through the
judge layer before execution. Protocol: minimal JSON-RPC over one POST
endpoint (``/mcp``) so a plain ASGI app can host it without extra deps.
"""

import json

from pa.tools.registry import authorize, describe, issue_token, whitelist_for


class TokenNotFound(Exception):
    pass


class McpServer:
    def __init__(self, *, judge=None, executor=None):
        # judge(connection, tool, arguments, gates) -> {"allowed": bool, ...}
        self.judge = judge
        # executor(tool, arguments) -> result dict
        self.executor = executor
        self.sessions: dict[str, object] = {}

    def open_session(self, task_id: str, skill) -> str:
        """Issue a one-shot token for one task's tool whitelist."""
        session = issue_token(task_id, whitelist_for(skill))
        self.sessions[session.token] = session
        return session.token

    def close_session(self, token: str) -> None:
        self.sessions.pop(token, None)

    def handle(self, body: bytes) -> dict:
        """Handle one MCP request envelope."""
        try:
            request = json.loads(body)
        except ValueError:
            return {"error": "invalid_json"}
        token = request.get("token", "")
        session = self.sessions.get(token)
        if session is None:
            return {"error": "unknown_token"}
        method = request.get("method")
        if method == "tools/list":
            return {"result": json.loads(describe(session))}
        if method == "tools/call":
            name = request.get("tool", "")
            arguments = request.get("arguments", {})
            decision = authorize(session, token=token, tool=name)
            if not decision["ok"]:
                return {"error": decision["reason"]}
            gates = decision["requires_approval"]
            if gates and self.judge is None:
                return {"error": "needs_approval", "decision": None}
            if gates:
                verdict = self.judge(name, arguments, gates)
                if not verdict.get("allowed"):
                    return {
                        "error": "needs_approval",
                        "decision": verdict.get("decision"),
                    }
            session.calls.append({"tool": name, "arguments": arguments})
            if self.executor is None:
                return {"result": None}
            return {"result": self.executor(name, arguments)}
        return {"error": "unknown_method"}


def bind_host() -> str:
    """The MCP endpoint must never leave the loopback interface."""
    return "127.0.0.1"
