"""fleet_priors mode gating — off ignores small-sample bans."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _reload(mode: str):
    os.environ["SN89_FLEET_PRIORS_MODE"] = mode
    import fleet_priors as fp

    return importlib.reload(fp)


def test_off_mode_no_bans_or_boosts():
    fp = _reload("off")
    assert fp.hf_pair_dir_blocked("ZROUSD", "LONG") is False
    assert fp.hf_pair_dir_mult("ADAUSD", "SHORT") == 1.0
    assert fp.lf_pair_blocked("BTCUSD") is False
    assert fp.lf_hour_mult(1 * 3600) == 1.0
    assert fp.hf_hour_blocked(5 * 3600, score=0.1, min_score=1.0) is False


def test_legacy_mode_keeps_zro_ban():
    fp = _reload("legacy")
    assert fp.hf_pair_dir_blocked("ZROUSD", "LONG") is True
    assert fp.hf_pair_dir_mult("ADAUSD", "SHORT") > 1.0


def test_wilson_helpers():
    fp = _reload("off")
    # With n=30 and 20 wins, LB should be >0.45
    assert fp.wilson_lb(20, 30) > 0.45
    assert fp.wilson_lb(0, 2) < 0.2
