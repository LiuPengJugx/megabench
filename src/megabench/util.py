"""Small shared utilities."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any, Mapping


def stable_hash(value: str, length: int = 12) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def counter_to_sorted_dict(counter: Counter[Any] | Mapping[Any, int]) -> dict[str, int]:
    items = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    return {str(k): int(v) for k, v in items}


def js_divergence(left: Mapping[str, int], right: Mapping[str, int]) -> float:
    keys = set(left) | set(right)
    left_total = sum(left.values())
    right_total = sum(right.values())
    if left_total <= 0 or right_total <= 0:
        return 0.0

    p = {k: left.get(k, 0) / left_total for k in keys}
    q = {k: right.get(k, 0) / right_total for k in keys}
    m = {k: 0.5 * (p[k] + q[k]) for k in keys}
    return 0.5 * _kl_divergence(p, m) + 0.5 * _kl_divergence(q, m)


def _kl_divergence(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    total = 0.0
    for key, p_value in p.items():
        if p_value <= 0:
            continue
        q_value = q.get(key, 0.0)
        if q_value <= 0:
            continue
        total += p_value * math.log2(p_value / q_value)
    return total
