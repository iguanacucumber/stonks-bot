"""Market registry: local stock universes, benchmarks, FX and a local-currency core.

Only the S&P 500 has point-in-time membership. Every other market here is a static
list of large caps that were listed for the whole window, so it is survivorship-biased
by construction. Say so in anything that quotes these numbers.

The core (SOXX) trades in USD. For a local-currency backtest it is converted with the
FX pair: EURUSD/GBPUSD quote USD per unit (divide), USDCHF quotes CHF per USD (multiply).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import data

ROOT = Path(__file__).resolve().parent.parent
START = "2014-01-01"  # membership snapshot before the 2015 backtest start

# currency -> (yahoo pair, pair quotes USD-per-local)
FX = {
    "EUR": ("EURUSD=X", True),
    "GBP": ("GBPUSD=X", True),
    "CHF": ("USDCHF=X", False),
}

UNIVERSES: dict[str, list[str]] = {
    "DE": """ADS.DE ALV.DE BAS.DE BAYN.DE BMW.DE MBG.DE SIE.DE SAP.DE DTE.DE DBK.DE
        CBK.DE IFX.DE MRK.DE RWE.DE EOAN.DE VOW3.DE PAH3.DE HEN3.DE FME.DE FRE.DE
        SHL.DE HLAG.DE DHER.DE SY1.DE ZAL.DE CON.DE HEI.DE MTX.DE QIA.DE BEI.DE
        HNR1.DE MUV2.DE GBF.DE TKA.DE LHA.DE BNR.DE WCH.DE SRT3.DE FPE3.DE PUM.DE
        BOSS.DE SZG.DE AFX.DE G24.DE LXS.DE NEM.DE RAA.DE KRN.DE EVT.DE LIN.DE""".split(),
    "FR": """AI.PA AIR.PA MT.AS SAN.PA BNP.PA SU.PA OR.PA MC.PA DG.PA EL.PA RMS.PA
        TTE.PA CS.PA GLE.PA ACA.PA CAP.PA SGO.PA RI.PA KER.PA LR.PA SAF.PA HO.PA
        ML.PA ORA.PA VIE.PA ENGI.PA STMPA.PA STLAP.PA RNO.PA PUB.PA BN.PA CA.PA
        ALO.PA UBI.PA WLN.PA ERF.PA TEP.PA SW.PA AC.PA BVI.PA EDEN.PA AMUN.PA
        GTT.PA NEX.PA FR.PA ATO.PA EN.PA FGR.PA CO.PA SCR.PA ELIS.PA GET.PA
        RXL.PA IPS.PA""".split(),
    "ES": """SAN.MC BBVA.MC ITX.MC IBE.MC TEF.MC REP.MC AMS.MC FER.MC AENA.MC ENG.MC
        ACS.MC CABK.MC BKT.MC MAP.MC GRF.MC ELE.MC ANA.MC CLNX.MC RED.MC IAG.MC
        MRL.MC COL.MC SGRE.MC VIS.MC ACX.MC ALM.MC CIE.MC EBRO.MC FAE.MC GEST.MC
        IDR.MC LDA.MC LOG.MC MCM.MC MEL.MC NAT.MC OHLA.MC PHM.MC PRS.MC ROVI.MC
        SAB.MC TRE.MC TUB.MC UNI.MC FDR.MC NEA.MC DIA.MC""".split(),
    "IT": """ENI.MI ENEL.MI ISP.MI UCG.MI G.MI RACE.MI STLAM.MI LDO.MI MB.MI PRY.MI
        SPM.MI TRN.MI SRG.MI ENAV.MI PST.MI BMPS.MI BPE.MI BAMI.MI CPR.MI FBK.MI
        REC.MI AMP.MI AZM.MI BZU.MI DIA.MI HER.MI IP.MI IVG.MI NEXI.MI UNI.MI
        TIT.MI BRE.MI CAL.MI GVS.MI IRE.MI LSR.MI MARR.MI MED.MI PIRC.MI STM.MI
        JUVE.MI CRE.MI""".split(),
    "NL": """ASML.AS AD.AS PHIA.AS HEIA.AS INGA.AS ABNA.AS WKL.AS ADYEN.AS KPN.AS
        AKZA.AS RAND.AS UNA.AS ASM.AS BESI.AS HEIJM.AS AALB.AS INPOST.AS JDEP.AS
        TKWY.AS TOM2.AS VPK.AS SBMO.AS BAMNB.AS CRBN.AS NEDAP.AS VASTN.AS AGN.AS
        NN.AS ASRNL.AS BOKA.AS LIGHT.AS OCI.AS UMG.AS HEIO.AS""".split(),
    "GB": """SHEL.L AZN.L HSBA.L ULVR.L BP.L GSK.L RIO.L BATS.L REL.L LSEG.L DGE.L
        BARC.L LLOY.L NWG.L STAN.L VOD.L BT-A.L NG.L SSE.L TSCO.L SBRY.L MKS.L
        JD.L NXT.L ABF.L IMB.L PRU.L AV.L LGEN.L ADM.L RR.L BA.L WEIR.L SMIN.L
        ANTO.L GLEN.L AAL.L FRES.L CRH.L EXPN.L INF.L RKT.L HLMA.L SPX.L SGE.L
        AUTO.L IHG.L WTB.L CCH.L OCDO.L MNDI.L SMDS.L KGF.L TW.L BLND.L LAND.L
        SGRO.L MRO.L RMV.L III.L BRBY.L ENT.L FLTR.L PSON.L SVT.L UU.L JMAT.L
        DCC.L BNZL.L ITRK.L DPLM.L GAW.L PHNX.L ABDN.L""".split(),
    "TECH": """AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA COST NFLX AMD PEP ADBE
        CSCO TMUS INTU QCOM TXN AMAT MU LRCX KLAC ADI PANW SNPS CDNS CRWD MRVL
        FTNT ANET ASML ARM MELI PYPL SBUX BKNG ISRG VRTX REGN AMGN GILD MDLZ
        CTSH WDAY TEAM DDOG ZS OKTA ANT""".split(),
    "CH": """NESN.SW ROG.SW NOVN.SW UBSG.SW ZURN.SW ABBN.SW CFR.SW HOLN.SW SIKA.SW
        GIVN.SW LONN.SW ALC.SW SREN.SW SCMN.SW GEBN.SW LOGN.SW ADEN.SW TEMN.SW
        STMN.SW BUCN.SW BALN.SW HELN.SW EMSN.SW KNIN.SW SOON.SW BCVN.SW VACN.SW
        SFSN.SW CLN.SW DKSH.SW FHZN.SW LISN.SW PGHN.SW SRAIL.SW UHR.SW""".split(),
}

# name -> (benchmark Yahoo symbol, currency, label)
MARKETS: dict[str, dict] = {
    "US": {"bench": "SPY", "fx": "USD", "label": "S&P 500"},
    "DE": {"bench": "EWG", "fx": "EUR", "label": "DAX (EWG)"},
    "FR": {"bench": "CAC.PA", "fx": "EUR", "label": "CAC 40"},
    "ES": {"bench": "EWP", "fx": "EUR", "label": "IBEX 35 (EWP)"},
    "IT": {"bench": "EWI", "fx": "EUR", "label": "FTSE MIB (EWI)"},
    "NL": {"bench": "EWN", "fx": "EUR", "label": "AEX (EWN)"},
    "GB": {"bench": "EWU", "fx": "GBP", "label": "FTSE 100 (EWU)"},
    "CH": {"bench": "EWL", "fx": "CHF", "label": "SMI (EWL)"},
    # ANT = Anthropic, PBC: IPO 2026-11-19, so it enters the universe now but has no
    # 126-day signal until roughly 2027-05. It cannot affect the backtest before then.
    "TECH": {"bench": "QQQ", "fx": "USD", "label": "NASDAQ tech"},
}

# Tier 2: one USD-listed ETF per market, no stock picking, no FX.
COUNTRY_ETFS: dict[str, str] = {
    "US": "VOO",
    "FR": "EWQ",
    "DE": "EWG",
    "ES": "EWP",
    "IT": "EWI",
    "NL": "EWN",
    "BE": "EWK",
    "AT": "EWO",
    "IE": "EIRL",
    "FI": "EFNL",
    "GR": "GREK",
    "GB": "EWU",
    "CH": "EWL",
    "SE": "EWD",
    "NO": "ENOR",
    "DK": "EDEN",
    "PL": "EPOL",
    "JP": "EWJ",
    "TW": "EWT",
    "KR": "EWY",
    "IN": "INDA",
    "CN": "MCHI",
    "AU": "EWA",
    "CA": "EWC",
    "BR": "EWZ",
    "MX": "EWW",
    "ZA": "EZA",
    "TR": "TUR",
    "EU": "FEZ",
    "TECH": "QQQ",
}
ETF_NAMES = {
    "VOO": "United States",
    "EWQ": "France",
    "EWG": "Germany",
    "EWP": "Spain",
    "EWI": "Italy",
    "EWN": "Netherlands",
    "EWK": "Belgium",
    "EWO": "Austria",
    "EIRL": "Ireland",
    "EFNL": "Finland",
    "GREK": "Greece",
    "EWU": "United Kingdom",
    "EWL": "Switzerland",
    "EWD": "Sweden",
    "ENOR": "Norway",
    "EDEN": "Denmark",
    "EPOL": "Poland",
    "EWJ": "Japan",
    "EWT": "Taiwan",
    "EWY": "South Korea",
    "INDA": "India",
    "MCHI": "China",
    "EWA": "Australia",
    "EWC": "Canada",
    "EWZ": "Brazil",
    "EWW": "Mexico",
    "EZA": "South Africa",
    "TUR": "Turkey",
    "FEZ": "Eurozone",
    "QQQ": "US tech (Nasdaq 100)",
}


CORE_SYMBOLS = ("SOXX", "SMH")


def local_core(symbol: str, currency: str, calendar=None) -> pd.Series:
    """A USD core ETF expressed in `currency`, projected onto `calendar`.

    US and European holidays differ, so the local series is forward-filled onto the
    local trading calendar; otherwise every US holiday leaves a hole in the panel.
    """
    soxx = data.fetch_one(symbol).set_index("date")["adjclose"].sort_index()
    if currency == "USD":
        out = soxx
    else:
        pair, usd_per_local = FX[currency]
        fx = data.fetch_one(pair).set_index("date")["adjclose"].sort_index()
        fx = fx.reindex(fx.index.union(soxx.index)).ffill().reindex(soxx.index)
        out = (soxx / fx if usd_per_local else soxx * fx).dropna()
    if calendar is not None:
        cal = pd.DatetimeIndex(calendar).unique().sort_values()
        out = out.reindex(out.index.union(cal)).ffill().reindex(cal)
    return out


def _rows(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    out = df[["date", "adjclose", "close", "volume"]].copy()
    if "open" in df.columns:
        out["open"] = df["open"] * df["adjclose"] / df["close"]
    else:
        out["open"] = df["adjclose"]
    # Keep the same adjustment factor on the range, so a limit at the adjusted
    # open can be checked against adjusted high/low.
    for col in ("high", "low"):
        if col in df.columns:
            out[col] = df[col] * df["adjclose"] / df["close"]
        else:
            out[col] = df["adjclose"]
    out["ticker"] = ticker
    return out


def _range_from_bars(
    panel: pd.DataFrame, alias: dict[str, str] | None = None
) -> pd.DataFrame:
    """Add adjusted high/low from cache/bars to a panel that dropped them.

    Read-only: a symbol with no cached bar file is left as-is (no network fetch).
    `alias` maps a panel ticker to its bar symbol (e.g. TECH stores QQQ as SPY).
    """
    alias = alias or {}
    have_cols = {"high", "low"} <= set(panel.columns)
    if have_cols and not panel[["high", "low"]].isna().any().any():
        return panel
    parts = []
    for ticker in panel["ticker"].unique():
        symbol = alias.get(ticker, ticker)
        path = data.BARS / f"{symbol.replace('/', '_')}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, parse_dates=["date"])
        except Exception:
            continue
        if df.empty or not {"high", "low"} <= set(df.columns):
            continue
        f = df["adjclose"] / df["close"]
        parts.append(
            pd.DataFrame(
                {
                    "date": df["date"],
                    "ticker": ticker,
                    "high": df["high"] * f,
                    "low": df["low"] * f,
                }
            )
        )
    if not parts:
        return panel
    rng = pd.concat(parts, ignore_index=True)
    out = panel.drop(columns=["high", "low"], errors="ignore").merge(
        rng, on=["date", "ticker"], how="left"
    )
    if have_cols:
        old = panel[["date", "ticker", "high", "low"]]
        out = out.merge(old, on=["date", "ticker"], how="left", suffixes=("", "_keep"))
        for col in ("high", "low"):
            out[col] = out[col].where(out[col].notna(), out[f"{col}_keep"])
            out = out.drop(columns=[f"{col}_keep"])
    return out


def _core_from_bars(panel: pd.DataFrame) -> pd.DataFrame:
    """Replace the synthetic core `open == close` rows with real adjusted bars.

    `local_core` has only adjusted closes, so it stores the close as the open.
    For USD panels (US/TECH) the raw cache has the real open/high/low; splice
    them in. Rows that already have a real open, and non-US panels, are untouched.
    """
    core = panel[panel["ticker"].isin(CORE_SYMBOLS)]
    if core.empty:
        return panel
    synthetic = (core["open"] - core["close"]).abs() <= 1e-12
    if not synthetic.any():
        return panel
    parts = []
    for sym in CORE_SYMBOLS:
        path = data.BARS / f"{sym}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, parse_dates=["date"])
        except Exception:
            continue
        if df.empty or not {"open", "high", "low"} <= set(df.columns):
            continue
        f = df["adjclose"] / df["close"]
        parts.append(
            pd.DataFrame(
                {
                    "date": df["date"],
                    "ticker": sym,
                    "open_bar": df["open"] * f,
                    "high_bar": df["high"] * f,
                    "low_bar": df["low"] * f,
                }
            )
        )
    if not parts:
        return panel
    rng = pd.concat(parts, ignore_index=True)
    out = panel.merge(rng, on=["date", "ticker"], how="left")
    mask = out["ticker"].isin(CORE_SYMBOLS)
    fix = mask & ((out["open"] - out["close"]).abs() <= 1e-12) & out["open_bar"].notna()
    out.loc[fix, "open"] = out.loc[fix, "open_bar"]
    for col in ("high", "low"):
        bcol = f"{col}_bar"
        if col not in out.columns:
            out[col] = float("nan")
        fill = mask & out[bcol].notna()
        out.loc[fill, col] = out.loc[fill, bcol]
    return out.drop(columns=["open_bar", "high_bar", "low_bar"])


_CACHE: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
_CORE_CACHE: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}


def _real_core(name: str, panel: pd.DataFrame) -> pd.DataFrame:
    """Cached real-open core panel for US/TECH; other markets keep the old path."""
    if name not in ("US", "TECH"):
        return panel
    hit = _CORE_CACHE.get(name)
    if hit is not None and hit[0] is panel:
        return hit[1]
    out = _core_from_bars(panel)
    _CORE_CACHE[name] = (panel, out)
    return out


def _finish(
    name: str, panel: pd.DataFrame, with_range: bool, core_close_fill: bool
) -> pd.DataFrame:
    """Apply the default real core open and the optional high/low join."""
    if not core_close_fill:
        panel = _real_core(name, panel)
    if with_range:
        panel = _range_from_bars(
            panel, {"SPY": MARKETS[name]["bench"]} if name != "US" else None
        )
    return panel


def build(
    name: str, with_range: bool = False, core_close_fill: bool = False
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Long panel + static membership for one market. Benchmark is stored as 'SPY'.

    US/TECH use the real adjusted core open/high/low by default; `core_close_fill`
    restores the old synthetic `open == close` core path for comparison. Non-US
    markets still carry the close-fill core quirk.

    `with_range` adds adjusted high/low from cache/bars when the stored panel
    dropped them. It is needed by the limit-at-open execution mode.
    """
    if name in _CACHE:
        panel, mem = _CACHE[name]
        return _finish(name, panel, with_range, core_close_fill), mem
    spec = MARKETS[name]
    if name == "US":
        from bot import universe

        mem = universe.load_membership()
        panel = data.build_panel(mem)
        have = set(panel.ticker.unique())
        missing = [s for s in CORE_SYMBOLS if s not in have]
        if missing:
            parts = [panel]
            for sym in missing:
                parts.append(_rows(data.fetch_one(sym), sym))
            panel = pd.concat(parts, ignore_index=True)
            panel = panel.drop_duplicates(["date", "ticker"]).sort_values(
                ["date", "ticker"]
            )
        _CACHE[name] = (panel, mem)
        return _finish(name, panel, with_range, core_close_fill), mem

    tickers = UNIVERSES[name]
    symbols = tickers + [spec["bench"]]
    frames = data.fetch_all(symbols)
    parts = []
    for t, df in frames.items():
        if t == spec["bench"]:
            parts.append(_rows(df, "SPY"))
        elif t in tickers:
            parts.append(_rows(df, t))
    cal = frames[spec["bench"]]["date"]
    for sym in CORE_SYMBOLS:
        core = local_core(sym, spec["fx"], cal)
        parts.append(
            pd.DataFrame(
                {
                    "date": core.index,
                    "adjclose": core.to_numpy(),
                    "close": core.to_numpy(),
                    "open": core.to_numpy(),
                    "volume": 1e9,  # cores are never screened for liquidity
                    "ticker": sym,
                }
            )
        )
    panel = pd.concat(parts, ignore_index=True)
    panel = panel.drop_duplicates(["date", "ticker"]).sort_values(["date", "ticker"])
    mem = pd.DataFrame({"date": pd.Timestamp(START), "ticker": tickers})
    _CACHE[name] = (panel, mem)
    return _finish(name, panel, with_range, core_close_fill), mem


def build_etf(etf: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Panel for one country ETF as the benchmark plus SOXX as the core (both USD)."""
    if etf in _CACHE:
        return _CACHE[etf]
    parts = []
    for sym, col in [(etf, "SPY")] + [(c, c) for c in CORE_SYMBOLS]:
        df = data.fetch_one(sym)
        if df.empty:
            raise RuntimeError(f"no data for {sym}")
        parts.append(_rows(df, col))
    panel = pd.concat(parts, ignore_index=True)
    panel = panel.drop_duplicates(["date", "ticker"]).sort_values(["date", "ticker"])
    mem = pd.DataFrame({"date": pd.Timestamp(START), "ticker": [etf]})
    _CACHE[etf] = (panel, mem)
    return panel, mem
