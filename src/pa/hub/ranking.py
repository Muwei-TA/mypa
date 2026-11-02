"""Ranking value maintenance (PRD §6.4).

Fixed-step spaced integers with whole-list renumbering when an insertion has no
integer gap. Pure functions only; persistence lives in the store layer.
"""

from pa.common import PAError

STEP = 1000


def reindex(ids: list[str]) -> dict[str, int]:
    """Assign ``STEP, 2*STEP, ...`` to the ids in the given order."""
    return {id_: (index + 1) * STEP for index, id_ in enumerate(ids)}


def fill_missing(ids: list[str], ranks: dict[str, int | None]) -> dict[str, int]:
    """Give every id without a rank a value appended after the current maximum."""
    result = {id_: ranks.get(id_) for id_ in ids}
    unranked = [id_ for id_ in ids if result[id_] is None]
    if unranked:
        base = max((value for value in result.values() if value is not None), default=0)
        for offset, id_ in enumerate(unranked, start=1):
            result[id_] = base + offset * STEP
    return result


def plan_move(
    ids: list[str],
    ranks: dict[str, int | None],
    target: str,
    *,
    before: str | None = None,
    to_top: bool = False,
    to_bottom: bool = False,
) -> tuple[list[str], dict[str, int]]:
    """Plan a move and return the new order plus the ranks that changed.

    Exactly one placement is used: ``to_top``, ``before``, otherwise ``to_bottom``
    (including an explicit ``to_bottom``).
    """
    if target not in ids:
        raise PAError("target is not in the ranked list", "not_found", 4)
    if before is not None and (before not in ids or before == target):
        raise PAError("the --before anchor must be another item in the list")
    filled = fill_missing(ids, ranks)
    current_order = sorted(ids, key=lambda id_: filled[id_])
    remaining = [id_ for id_ in current_order if id_ != target]

    if to_top:
        new_order = [target, *remaining]
        previous_id, next_id = None, remaining[0] if remaining else None
    elif before is not None:
        position = remaining.index(before)
        new_order = [*remaining[:position], target, *remaining[position:]]
        previous_id = remaining[position - 1] if position > 0 else None
        next_id = remaining[position]
    else:
        new_order = [*remaining, target]
        previous_id, next_id = (remaining[-1] if remaining else None), None

    lower = filled[previous_id] if previous_id is not None else 0
    upper = filled[next_id] if next_id is not None else lower + 2 * STEP
    changed: dict[str, int] = {}
    if upper - lower <= 1:
        # No integer gap: renumber the whole list, then recompute the neighbours.
        renumbered = reindex(new_order)
        filled.update(renumbered)
        changed.update(renumbered)
        lower = filled[previous_id] if previous_id is not None else 0
        upper = filled[next_id] if next_id is not None else lower + 2 * STEP
    new_rank = (lower + upper) // 2
    changed[target] = new_rank
    return new_order, changed
