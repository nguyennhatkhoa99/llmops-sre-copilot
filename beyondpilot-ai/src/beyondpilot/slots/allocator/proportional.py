"""Share supply in proportion to demand, never above a region's demand or cap.

Water-filling: regions that hit their limit drop out and the rest of the supply
is shared again among the others, so no supply is left unused while some region
still has unmet demand.
"""

from __future__ import annotations

from collections.abc import Mapping


class Impl:
    async def allocate(
        self, demand: Mapping[str, float], supply: float, caps: Mapping[str, float]
    ) -> dict[str, float]:
        limit = {k: max(0.0, min(float(d), float(caps.get(k, d)))) for k, d in demand.items()}
        alloc = {k: 0.0 for k in demand}
        remaining = max(0.0, float(supply))
        active = {k for k, v in limit.items() if v > 0}
        while remaining > 1e-9 and active:
            weight = sum(float(demand[k]) for k in active) or float(len(active))
            given = 0.0
            for k in sorted(active):
                share = remaining * ((float(demand[k]) or 1.0) / weight)
                take = min(share, limit[k] - alloc[k])
                alloc[k] += take
                given += take
            remaining -= given
            active = {k for k in active if limit[k] - alloc[k] > 1e-9}
            if given <= 1e-12:
                break
        return {k: round(v, 6) for k, v in alloc.items()}
