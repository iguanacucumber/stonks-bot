"""Tests for UCITS fund metadata, FX conversion and no-look-ahead.

Run: uv run python tests/test_ucits.py
No network: all bars are synthetic temporary files.
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.research.ucits import (  # noqa: E402
    FUNDS,
    add_ucits_to_panel,
    common_start,
    fx_to_usd,
    fund_stats,
    get_fund,
    to_usd_bars,
    to_usd_series,
)


def write_bars(
    cache: Path,
    symbol: str,
    dates: pd.DatetimeIndex,
    values: np.ndarray,
    volume: float = 1_000_000,
) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        {
            "date": dates,
            "open": values,
            "high": values * 1.01,
            "low": values * 0.99,
            "close": values,
            "adjclose": values,
            "volume": volume,
        }
    )
    df.to_csv(cache / f"{symbol}.csv", index=False)


def test_metadata_is_complete_and_domicile_correct():
    assert set(FUNDS) == {"SMH", "SMH.L", "SEMI.AS", "SEMI.L"}
    for sym in ("SMH.L", "SEMI.AS", "SEMI.L"):
        m = get_fund(sym)
        assert m["domicile"] == "IE"
        assert m["ucits"] is True
        assert m["accumulating"] is True
        assert abs(m["ter"] - 0.0035) < 1e-9
        assert m["isin"].startswith("IE")
    assert get_fund("SMH")["domicile"] == "US"
    assert get_fund("SMH")["ucits"] is False
    assert get_fund("SMH.L")["currency"] == "USD"
    assert get_fund("SEMI.L")["currency"] == "GBP"


def test_fx_to_usd_uses_same_date_rate():
    dates = pd.bdate_range("2024-01-01", periods=5)
    with TemporaryDirectory() as td:
        cache = Path(td)
        write_bars(cache, "GBPUSD=X", dates, np.array([1.20, 1.21, 1.22, 1.23, 1.24]))
        write_bars(cache, "EURUSD=X", dates, np.array([1.10, 1.11, 1.12, 1.13, 1.14]))
        usd = fx_to_usd("USD", dates, cache)
        gbp = fx_to_usd("GBP", dates, cache)
        eur = fx_to_usd("EUR", dates, cache)
        assert np.allclose(usd.to_numpy(), 1.0)
        assert np.allclose(gbp.to_numpy(), [1.20, 1.21, 1.22, 1.23, 1.24])
        assert np.allclose(eur.to_numpy(), [1.10, 1.11, 1.12, 1.13, 1.14])


def test_to_usd_bars_converts_same_date_without_lookahead():
    dates = pd.bdate_range("2024-01-01", periods=4)
    with TemporaryDirectory() as td:
        cache = Path(td)
        write_bars(cache, "GBPUSD=X", dates, np.array([1.20, 1.21, 1.22, 1.23]))
        write_bars(cache, "SEMI.L", dates, np.array([10.0, 10.1, 10.2, 10.3]))
        out = to_usd_bars("SEMI.L", cache)
        assert np.allclose(out["adjclose"].to_numpy(), [12.0, 12.221, 12.444, 12.669])
        # Appending a future, different FX path must not change earlier rows.
        write_bars(
            cache,
            "GBPUSD=X",
            dates,
            np.array([1.20, 1.21, 1.22, 1.99]),
        )
        out2 = to_usd_bars("SEMI.L", cache)
        assert np.allclose(
            out.iloc[:3]["adjclose"].to_numpy(),
            out2.iloc[:3]["adjclose"].to_numpy(),
        )


def test_add_ucits_panel_projects_to_us_sessions():
    dates = pd.bdate_range("2024-01-01", periods=10)
    # UCITS line has an extra non-US date that must not shift engine lookbacks.
    extra = dates.insert(2, pd.Timestamp("2024-01-03 00:00:00"))
    extra = extra.drop_duplicates()
    with TemporaryDirectory() as td:
        cache = Path(td)
        write_bars(cache, "SMH.L", extra, np.linspace(10, 11, len(extra)))
        write_bars(cache, "SEMI.AS", extra, np.linspace(20, 21, len(extra)))
        write_bars(cache, "SEMI.L", extra, np.linspace(30, 31, len(extra)))
        write_bars(cache, "GBPUSD=X", extra, np.full(len(extra), 1.25))
        us = pd.DataFrame(
            {
                "date": list(dates) * 2,
                "ticker": ["SPY"] * len(dates) + ["AAPL"] * len(dates),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "adjclose": 100.0,
                "volume": 1_000_000,
            }
        )
        out = add_ucits_to_panel(us, cache_dir=cache)
        assert set(out["date"]) == set(dates)
        assert set(out.loc[out.ticker == "SMH.L", "date"]) == set(dates)
        assert set(out["ticker"]) >= {"SPY", "AAPL", "SMH.L", "SEMI.AS", "SEMI.L"}


def test_common_start_and_stats():
    dates = pd.bdate_range("2024-01-01", periods=100)
    with TemporaryDirectory() as td:
        cache = Path(td)
        write_bars(cache, "SMH", dates, np.linspace(100, 140, len(dates)))
        write_bars(cache, "SMH.L", dates[10:], np.linspace(10, 13, len(dates[10:])))
        assert common_start(("SMH", "SMH.L"), cache) == dates[10]
        a = to_usd_series("SMH", cache)
        b = to_usd_series("SMH.L", cache)
        st = fund_stats(b, a)
        assert st.start == dates[10].date().isoformat()
        assert st.total_return > 0
        assert st.maxdd <= 0


def main() -> None:
    checks = [
        test_metadata_is_complete_and_domicile_correct,
        test_fx_to_usd_uses_same_date_rate,
        test_to_usd_bars_converts_same_date_without_lookahead,
        test_add_ucits_panel_projects_to_us_sessions,
        test_common_start_and_stats,
    ]
    for fn in checks:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"ok: {len(checks)} UCITS checks")


if __name__ == "__main__":
    main()
