"""Feishu message levels (PRD §12.2)."""

from typing import Literal

Level = Literal["P0", "P1", "P2", "P3"]

P0: Level = "P0"
P1: Level = "P1"
P2: Level = "P2"
P3: Level = "P3"
LEVELS: tuple[Level, ...] = (P0, P1, P2, P3)

# Only P2 small cards are merged inside the 10-minute window.
MERGED_LEVELS = frozenset({P2})
