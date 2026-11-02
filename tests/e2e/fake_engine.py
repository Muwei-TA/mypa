"""Scripted fake engine for isolated E2E runs.

Mimics the `codex exec --json` event stream contract the CodexCLI adapter
parses: the final receipt rides inside an agent_message item on one line.
Reads its prompt via stdin and echoes a recognised keyword in the summary.
An input containing "slow" delays the answer to give the test harness a
window for killing the runtime mid-flight.
"""

import json
import sys
import time


def main() -> None:
    prompt = sys.stdin.read()
    if "slow" in prompt:
        time.sleep(3)
    summary = "E2E 收到输入并完成摘要"
    for word in ("hello", "世界", "告警"):
        if word in prompt:
            summary = f"E2E 概括：{word}"
            break
    receipt = {
        "status": "succeeded",
        "summary": summary,
        "code_changed": False,
        "confidence": 0.9,
        "risk": "low",
        "evidence": ["fake engine executed"],
        "usage": {"input_tokens": 10, "output_tokens": 5, "duration_s": 0.01},
        "artifacts": [],
        "draft": False,
        "risk_reasons": [],
        "tags": [],
        "suggested_action": "close",
        "human_action": None,
        "handback": [],
    }
    event = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": json.dumps(receipt, ensure_ascii=False)},
    }
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
