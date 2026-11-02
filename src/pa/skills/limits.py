"""Execution allowance shared by every engine invocation for one task."""

from dataclasses import dataclass

from pa.skills.receipt import Usage


@dataclass
class ExecutionBudget:
    max_tokens: int
    timeout_s: float
    tokens: int = 0
    duration_s: float = 0

    @property
    def remaining_s(self):
        return max(0, self.timeout_s - self.duration_s)

    @property
    def exhausted(self):
        if self.tokens >= self.max_tokens:
            return "task_token_limit"
        if self.remaining_s <= 0:
            return "task_timeout"
        return None

    def consume(self, usage: Usage):
        self.tokens += usage.input_tokens + usage.output_tokens
        self.duration_s += usage.duration_s
