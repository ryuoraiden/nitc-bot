"""Pure giveaway logic: duration parsing and weighted winner drawing.

No Discord or DB here so it can be unit-tested offline.
"""
from __future__ import annotations

import random
import re
from datetime import timedelta

_DURATION_TOKEN = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}

MIN_SECONDS = 10
MAX_SECONDS = 60 * 86400  # 60 days


def parse_duration(text: str) -> timedelta | None:
    """'1d', '2h30m', '1d6h', '45s' -> timedelta. None if unparseable or out
    of the [10s, 60d] range."""
    if not text:
        return None
    cleaned = re.sub(r"\s+", "", text.lower())
    tokens = _DURATION_TOKEN.findall(cleaned)
    if not tokens:
        return None
    # reject stray characters: the tokens must account for the whole string
    if "".join(f"{n}{u}" for n, u in tokens) != cleaned:
        return None
    total = sum(int(n) * _UNIT_SECONDS[u] for n, u in tokens)
    if not MIN_SECONDS <= total <= MAX_SECONDS:
        return None
    return timedelta(seconds=total)


def draw_winners(
    weights: dict[int, int], count: int, exclude: set[int] | None = None
) -> list[int]:
    """Pick up to `count` unique winners from {entrant_id: entry_weight},
    weighted by entries, skipping anyone in `exclude`. Fewer than `count` are
    returned if the pool runs out.
    """
    exclude = set(exclude or ())
    pool = {uid: w for uid, w in weights.items() if uid not in exclude and w > 0}
    winners: list[int] = []
    while pool and len(winners) < count:
        ids = list(pool)
        pick = random.choices(ids, weights=[pool[i] for i in ids], k=1)[0]
        winners.append(pick)
        del pool[pick]
    return winners
