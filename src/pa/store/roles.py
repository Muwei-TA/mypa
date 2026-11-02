"""Role-scoped facades (M3-01, PRD §20.1 / v1.1·D21).

OwnerFacade keeps full rights. Agent tokens may only ever hold AgentFacade:
the class simply does not define writes to secrets, merge policy, budgets,
whitelists or precedents — the restriction is structural (no attribute to
call), not a runtime check. Executor/Hub/Settings facades expose the
subsets their processes need.
"""

from __future__ import annotations

from pa.store.facades import OwnerFacade


class AgentFacade:
    """What PA_TOKEN=<agent-token> can reach: read-heavy, task-submit only.

    Allowed: emit events, list/show tasks and events, queue view, retry
    tasks, pin/unpin, priorities, reorder queue, skill/rule reads and tests.
    Structurally absent: cancel, decisions, secrets, budgets, merge policy,
    rules/skills writes, effects retry/drop, precedents writes, audit.
    """

    def __init__(self, owner: OwnerFacade):
        self._owner = owner

    # read-only delegated properties (no write surface added)
    @property
    def config_dir(self):
        return self._owner.config_dir

    @property
    def engine(self):
        return self._owner.engine

    # reads delegate to the owner facade's read methods
    def list(self, table, limit=50):
        return self._owner.list(table, limit=limit)

    def show(self, table, id_):
        return self._owner.show(table, id_)

    def list_tasks(self, **kwargs):
        return self._owner.list_tasks(**kwargs)

    def emit(self, event):
        return self._owner.emit(event)

    def move_task(self, task_id, **kwargs):
        return self._owner.move_task(task_id, **kwargs)

    def set_pinned(self, task_id, pinned):
        return self._owner.set_pinned(task_id, pinned)

    def set_priority(self, task_id, priority):
        return self._owner.set_priority(task_id, priority)

    def status(self):
        return self._owner.status()


class ExecutorFacade:
    """pa-worker process: claim/run/lease management only."""

    def __init__(self, owner: OwnerFacade):
        self._owner = owner

    def list_tasks(self, **kwargs):
        return self._owner.list_tasks(**kwargs)

    def show(self, table, id_):
        return self._owner.show(table, id_)


class HubFacade:
    """pa-core pipeline: ingest, subscriptions, decisions routing."""

    def __init__(self, owner: OwnerFacade):
        self._owner = owner

    def emit(self, event):
        return self._owner.emit(event)

    def list(self, table, limit=50):
        return self._owner.list(table, limit=limit)

    def show(self, table, id_):
        return self._owner.show(table, id_)


class SettingsFacade:
    """Owner-only settings surface: rules, skills ordering, rule modes."""

    def __init__(self, owner: OwnerFacade):
        self._owner = owner

    def set_rule_mode(self, rule_id, mode):
        return self._owner.set_rule_mode(rule_id, mode)

    def reorder_skills(self, skill_ids):
        return self._owner.reorder_skills(skill_ids)

    def reorder_rules(self, rule_ids):
        return self._owner.reorder_rules(rule_ids)


def facade_for(token_role: str, owner: OwnerFacade):
    """Map a token role to its facade; only 'owner' gets OwnerFacade."""
    if token_role == "owner":
        return owner
    if token_role == "agent":
        return AgentFacade(owner)
    if token_role == "executor":
        return ExecutorFacade(owner)
    if token_role == "hub":
        return HubFacade(owner)
    if token_role == "settings":
        return SettingsFacade(owner)
    raise ValueError(f"unknown token role: {token_role}")


# Attributes an agent token must never see, enforced structurally (M3-01).
AGENT_FORBIDDEN_ATTRS = frozenset(
    {
        "cancel",
        "pause",
        "resume",
        "effect_retry",
        "effect_drop",
        "set_rule_mode",
        "reorder_skills",
        "reorder_rules",
    }
)
