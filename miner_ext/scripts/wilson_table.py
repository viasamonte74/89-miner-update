"""Print Wilson QUALIFY table using the repo's own scoring.confident_edge."""
from __future__ import annotations

import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from sn89_signals import config, scoring


def main() -> int:
    print(
        f"gate: decisive>={config.QUALIFY_MIN_DECISIVE} AND "
        f"WilsonLB(z={config.QUALIFY_Z})>={config.QUALIFY_LB_FLOOR}"
    )
    print(
        f"reputation: {config.HIT_RATE_WINDOW_S/86400:.0f}d, "
        f"cap_now={config.hit_rate_window_trades_as_of(1e12)}; "
        f"emission_decay={config.EMISSION_DECAY_S/86400:.0f}d; "
        f"TIER_PRIOR_K={config.TIER_PRIOR_K}"
    )
    print()
    hdr = f"{'n':>3} {'wins':>6} {'raw%':>7} {'wilsonLB':>9} {'shrunk':>7} {'tier':>6} {'Q':>3}"
    print(hdr)
    for n in range(8, 22):
        for w in range(0, n + 1):
            if scoring.confident_edge(w, n) >= config.QUALIFY_LB_FLOOR:
                s = scoring.shrunk_hit_rate(w, n)
                tier = scoring.tier_multiplier(w, n, t0_unix=1e12)
                q = scoring.is_qualified(w, n)
                print(
                    f"{n:3d} {w:2d}/{n:<2d} {100*w/n:6.1f}% {scoring.confident_edge(w,n):9.4f} "
                    f"{s:7.4f} {tier:6.2f} {'Y' if q else 'N':>3}"
                )
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
