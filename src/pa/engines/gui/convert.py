"""Structured conversion for GUI output (M4-06, PRD §7.3/§8.4).

GUI engines return raw text; the conversion turns it into a receipt using a
fixed template plus one cheap-model call. The result confidence is clamped
to the C-tier cap (0.6) regardless of what the model reports.
"""

from pa.engines.gui.limits import clamp_confidence

TEMPLATE = """你是结构化转换器。把下面的 GUI 引擎输出转成 JSON 回执。
只输出 JSON，字段：answer（正文）、confidence（0-1）、notes（可选）。
GUI 输出：
{raw}
"""


def build_prompt(raw_text: str) -> str:
    return TEMPLATE.format(raw=raw_text)


def parse_model_output(payload: dict | None) -> dict:
    """Validate and clamp the model's JSON answer.

    Any payload missing required keys or carrying an invalid confidence is
    rejected with a validation error so the caller can fall back to manual
    review instead of trusting a malformed conversion.
    """
    if not isinstance(payload, dict) or "answer" not in payload:
        return {"ok": False, "error": "missing answer"}
    confidence = payload.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return {"ok": False, "error": "invalid confidence"}
    clamped = clamp_confidence(float(confidence))
    return {
        "ok": True,
        "answer": str(payload["answer"]),
        "confidence": clamped,
        "clamped": clamped < float(confidence),
        "notes": payload.get("notes", ""),
    }


def convert(raw_text: str, model_call) -> dict:
    """Full conversion: one cheap model call, then clamp confidence."""
    response = model_call(build_prompt(raw_text))
    result = parse_model_output(response)
    if not result["ok"]:
        return {**result, "fallback": "manual_review"}
    return result
