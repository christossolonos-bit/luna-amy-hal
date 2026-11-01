"""New Champ semantic memory for Luna.

Per-person long-term facts (SQLite + MiniLM), extract after every exchange.
Used alongside HalMini (fact brain) and NVIDIA (talk when HalMini misses).
"""

from server.newchamp.bridge import (
    context_block,
    enabled,
    learn_exchange,
    recall,
    remember_facts,
)

__all__ = [
    "enabled",
    "recall",
    "remember_facts",
    "learn_exchange",
    "context_block",
]
