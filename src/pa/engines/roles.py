"""Engine role assignment (M2-08, PRD §8.5 / v1.1·K3).

`review` must skip the engine AND model that last wrote code for this task;
when only that same model remains, mark `same_model_fallback` so the caller
can surface it in the review record.
"""

import json

from sqlalchemy import text


def last_author(engine, task_id: str) -> dict | None:
    """The engine_id and model that produced the last code-writing run."""
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""
                SELECT r.engine_id, r.receipt_json FROM runs r
                WHERE r.task_id=:task AND r.receipt_json IS NOT NULL
                  AND json_extract(r.receipt_json, '$.code_changed') = 1
                ORDER BY r.rowid DESC LIMIT 1
            """),
                {"task": task_id},
            )
            .mappings()
            .first()
        )
    if not row:
        return None
    try:
        receipt = json.loads(row["receipt_json"])
    except (TypeError, json.JSONDecodeError):
        receipt = {}
    return {"engine_id": row["engine_id"], "model": receipt.get("model")}


def review_candidates(config, skill, engine, task_id: str) -> dict:
    """Ordered review-role engines with the last author filtered out."""
    from pa.engines.selector import ordered_candidates

    author = last_author(engine, task_id)
    candidates = ordered_candidates(config, skill, role="review")
    if not author:
        return {"engines": candidates, "same_model_fallback": False, "author": None}
    author_engine = author["engine_id"]
    others = [name for name in candidates if name != author_engine]
    if others:
        return {
            "engines": others,
            "same_model_fallback": False,
            "author": author_engine,
        }
    # Only the same engine/model is available: allow it but flag the fallback.
    return {
        "engines": candidates,
        "same_model_fallback": True,
        "author": author_engine,
    }
