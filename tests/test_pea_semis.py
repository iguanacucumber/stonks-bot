"""Checks for the 2026 PEA semiconductor/tech registry and momentum timing.

Run: uv run python tests/test_pea_semis.py

No cache and no network: this file only checks metadata and the pure 12-1
signal. The data-dependent report is reproducible with the cached bars.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pea_semis as pea  # noqa: E402


def test_eligibility_registry() -> None:
    assert pea.ELIGIBILITY_AS_OF == "2026-11-26"
    assert len({m["isin"] for m in pea.ELIGIBILITY.values()}) == len(pea.ELIGIBILITY)
    for ticker, m in pea.ELIGIBILITY.items():
        assert m["name"] and m["isin"] and m["domicile"]
        assert m["replication"] and m["index"]
        assert m["source"].startswith("https://")
        assert isinstance(m["pea"], bool)
        assert ticker.endswith(".PA"), ticker
    for ticker in ("PUST.PA", "PSP5.PA", "PE500.PA", "CW8.PA", "LQQ.PA", "TNO.PA"):
        assert pea.ELIGIBILITY[ticker]["pea"] is True, ticker
    # The mission's core finding: no pure semiconductor UCITS is PEA-eligible.
    for ticker in ("SMH.PA", "SEMI.PA", "CHIP.PA"):
        assert pea.ELIGIBILITY[ticker]["pea"] is False, ticker
    assert pea.ELIGIBILITY["PUST.PA"]["isin"] == "FR0011871110"
    assert pea.ELIGIBILITY["PSP5.PA"]["isin"] == "FR0011871128"
    assert pea.ELIGIBILITY["PE500.PA"]["isin"] == "FR0013412285"


def test_momentum_signal_boundaries() -> None:
    dates = pd.bdate_range("2020-01-01", periods=320)
    # 12-1 return positive: t-21 above t-252.
    px = pd.Series(100.0, index=dates)
    px.iloc[300 - 252] = 100.0
    px.iloc[300 - 21] = 110.0
    assert pea.momentum_signal(px, dates[300]) is True
    px.iloc[300 - 21] = 90.0
    assert pea.momentum_signal(px, dates[300]) is False
    # A flat series is exactly zero -> cash.
    flat = pd.Series(100.0, index=dates)
    assert pea.momentum_signal(flat, dates[300]) is False
    # Not enough history -> hold (True), not a look-ahead default.
    assert pea.momentum_signal(px, dates[10]) is True


def test_momentum_no_lookahead() -> None:
    dates = pd.bdate_range("2020-01-01", periods=320)
    rng = np.random.default_rng(7)
    px = pd.Series(
        100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(dates)))), index=dates
    )
    for k in (260, 280, 300):
        # The signal at k must not change when future prices are appended.
        full = pea.momentum_signal(px, dates[k])
        truncated = pea.momentum_signal(px.iloc[: k + 1], dates[k])
        assert full == truncated, (k, full, truncated)


def main() -> None:
    test_eligibility_registry()
    test_momentum_signal_boundaries()
    test_momentum_no_lookahead()
    print("ok: PEA eligibility registry and 12-1 momentum causality")


if __name__ == "__main__":
    main()
