"""Checks for the PEA UCITS panel builder and eligibility assumptions."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pea_ucits as pea  # noqa: E402


def frame(prices, start="2020-01-02"):
    dates = pd.bdate_range(start, periods=len(prices))
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": p,
            "high": p * 1.01,
            "low": p * 0.99,
            "close": p,
            "adjclose": p,
            "volume": 1_000_000,
        }
    )


def main():
    # Eligibility registry: the three pure semi UCITS are not PEA-eligible.
    assert pea.ELIGIBILITY["PUST.PA"]["pea"] is True
    assert pea.ELIGIBILITY["PUST.PA"]["isin"] == "FR0011871110"
    assert pea.ELIGIBILITY["CW8.PA"]["pea"] is True
    assert pea.ELIGIBILITY["LQQ.PA"]["pea"] is True
    assert pea.ELIGIBILITY["LQQ.PA"]["isin"] == "FR0010342592"
    for ticker in ("SMH.PA", "SEMI.PA", "CHIP.PA"):
        assert pea.ELIGIBILITY[ticker]["pea"] is False, ticker
    assert all(m["isin"] and m["source"] for m in pea.ELIGIBILITY.values())

    # One-ETF panel: the core is canonicalised, and SPY is the same series.
    df = frame([100, 101, 102, 103, 104])
    panel, mem = pea.pea_panel("TEST.PA", frame=df, end="2020-02-01")
    assert set(panel.ticker) == {"TEST-PA", "SPY"}
    assert list(mem.ticker) == ["TEST-PA"]
    core = panel[panel.ticker == "TEST-PA"].set_index("date")["adjclose"]
    bench = panel[panel.ticker == "SPY"].set_index("date")["adjclose"]
    assert np.allclose(core.to_numpy(), bench.to_numpy())
    assert len(core) == len(df)

    try:
        pea.pea_panel("TEST.PA", frame=df.iloc[:0])
        raise AssertionError("empty frame should be rejected")
    except RuntimeError:
        pass

    # Equal-weight, month-end reset: A doubles, then B doubles after the reset.
    dates = pd.bdate_range("2020-01-30", periods=3)  # Jan 30, Jan 31, Feb 3
    px = pd.DataFrame(
        {"A": [100.0, 200.0, 200.0], "B": [100.0, 100.0, 200.0]}, index=dates
    )
    idx = pea.basket_index(px)
    assert abs(idx.iloc[0] - 100.0) < 1e-9
    assert abs(idx.iloc[1] - 150.0) < 1e-9, idx.tolist()
    assert abs(idx.iloc[2] - 225.0) < 1e-9, idx.tolist()

    # Changing the last price cannot rewrite earlier index values.
    px2 = px.copy()
    px2.iloc[-1, 0] = 999.0
    idx2 = pea.basket_index(px2)
    assert np.allclose(idx.iloc[:2].to_numpy(), idx2.iloc[:2].to_numpy())

    frames = {
        "AAA.AS": frame([100, 101, 102, 103]),
        "BBB.AS": frame([100, 99, 101, 102]),
    }
    bpanel, bmem, bidx = pea.basket_panel(
        ("AAA.AS", "BBB.AS"), frames=frames, end="2020-02-01"
    )
    assert set(bpanel.ticker) == {"PEA_SEMI", "SPY"}
    assert list(bmem.ticker) == ["PEA_SEMI"]
    assert len(bidx) == 4

    # Buy-and-hold PEA: 18.6% social on the gain, after one ticket each side.
    bh = pea.buy_hold(
        "TEST.PA",
        "Trade Republic",
        "2026-01-02",
        end="2026-01-05",
        capital=1000,
        frame=frame([100, 200], start="2026-01-02"),
        spread_bps=0.0,
    )
    assert abs(bh["pre_tax"] - 1997.0) < 1e-9, bh
    assert abs(bh["fees"] - 2.0) < 1e-9, bh
    assert abs(bh["exit_tax"] - 0.186 * 997.0) < 1e-9, bh
    assert abs(bh["after_tax"] - (1997.0 - 0.186 * 997.0)) < 1e-9, bh

    print(
        "ok: eligibility registry, panel builder, basket index, "
        f"buy-and-hold after tax {bh['after_tax']:.2f}"
    )


if __name__ == "__main__":
    main()
