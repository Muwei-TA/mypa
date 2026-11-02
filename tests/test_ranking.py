import random

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from pa.cli.main import app
from pa.common import PAError
from pa.hub.ranking import STEP, fill_missing, plan_move, reindex
from pa.ingress.manual import ManualAdapter


def make_event(**overrides):
    return ManualAdapter().parse(
        {
            "type": "manual.test",
            "payload": {"skill": "sample-direct", "text": "x"},
            **overrides,
        }
    )


def test_reindex_step():
    assert STEP == 1000
    assert reindex(["a", "b", "c"]) == {"a": 1000, "b": 2000, "c": 3000}


def test_fill_missing_appends():
    assert fill_missing(["a", "b"], {"a": 1500, "b": None}) == {"a": 1500, "b": 2500}


def test_move_to_top_and_bottom():
    ids = ["a", "b", "c"]
    ranks = reindex(ids)

    order, changed = plan_move(ids, ranks, "c", to_top=True)
    assert order == ["c", "a", "b"]
    final = {**ranks, **changed}
    assert changed["c"] < ranks["a"]
    assert sorted(order, key=lambda x: final[x]) == order

    order, changed = plan_move(ids, ranks, "a", to_bottom=True)
    assert order == ["b", "c", "a"]
    final = {**ranks, **changed}
    assert sorted(order, key=lambda x: final[x]) == order


def test_move_before_uses_midpoint():
    ids = ["a", "b", "c"]
    ranks = reindex(ids)
    order, changed = plan_move(ids, ranks, "a", before="c")
    assert order == ["b", "a", "c"]
    assert ranks["b"] < changed["a"] < ranks["c"]
    final = {**ranks, **changed}
    assert sorted(order, key=lambda x: final[x]) == order


def test_move_without_gap_renumbers_whole_list():
    # U-RANK-02: adjacent ranks 1000 and 1001 leave no integer midpoint.
    ids = ["a", "b", "t"]
    ranks = {"a": 1000, "b": 1001, "t": 3000}
    order, changed = plan_move(ids, ranks, "t", before="b")
    assert order == ["a", "t", "b"]
    assert changed == {"a": 1000, "t": 2000, "b": 3000}
    final = {**ranks, **changed}
    values = [final[x] for x in order]
    assert len(set(values)) == len(values)
    assert values == sorted(values)


def test_random_moves_keep_total_order():
    # U-RANK-03 style property test: ranks stay unique and order-consistent.
    rng = random.Random(42)
    order = [f"i{n}" for n in range(8)]
    ranks = reindex(order)
    for _ in range(400):
        target = rng.choice(order)
        mode = rng.choice(["top", "bottom", "before"])
        if mode == "top":
            order, changed = plan_move(order, ranks, target, to_top=True)
        elif mode == "bottom":
            order, changed = plan_move(order, ranks, target, to_bottom=True)
        else:
            anchor = rng.choice([x for x in order if x != target])
            order, changed = plan_move(order, ranks, target, before=anchor)
        ranks.update(changed)
        values = [ranks[x] for x in order]
        assert len(set(values)) == len(values)
        assert values == sorted(values)


def test_facade_move_task_order(facade):
    task_ids = [facade.emit(make_event(dedup_key=f"k{n}"))["tasks"][0] for n in range(4)]
    facade.move_task(task_ids[3], to_top=True)
    with facade.engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id FROM tasks WHERE manual_rank IS NOT NULL ORDER BY manual_rank")
        ).all()
    ordered_ids = [row[0] for row in rows]
    assert ordered_ids[0] == task_ids[3]
    assert ordered_ids[1:] == task_ids[:3]


def test_facade_pin_and_priority(facade):
    task_id = facade.emit(make_event(dedup_key="kp"))["tasks"][0]
    assert facade.set_pinned(task_id, True)["pinned"] is True
    assert facade.set_priority(task_id, "P1")["priority"] == "P1"
    with pytest.raises(PAError):
        facade.set_priority(task_id, "P9")


def test_facade_reorder_skills(facade):
    facade.emit(make_event(dedup_key="ks"))
    assert facade.reorder_skills(["sample-direct"])["skill_rank"] == {"sample-direct": 1000}
    with pytest.raises(PAError):
        facade.reorder_skills(["nonexistent"])


def test_cli_task_move(config_dir, facade):
    task_id = facade.emit(make_event(dedup_key="kc"))["tasks"][0]
    result = CliRunner().invoke(
        app,
        ["--config-dir", str(config_dir), "task", "move", task_id, "--to-top", "--json"],
    )
    assert result.exit_code == 0
