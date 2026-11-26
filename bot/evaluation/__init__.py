"""Reproducible evaluation tools for the v2 research program.

Import submodules directly for new work::

    from bot.evaluation import metrics, bootstrap, cscv, walkforward, trials

Nothing here writes to the cache or calls Yahoo.  The CLI entry point is
``scripts/reproduce.py`` (headline, audit, survivorship).
"""

from .currency import (  # noqa: F401
    ACCOUNTING_MODES,
    MODE_LABELS,
    REAL_LIFE_MODE,
    dimension_table,
    eur_ledger_available,
    is_real_life,
    ledger_summary,
    native_after_tax_multiple,
    require_mode,
)
from .metrics import (  # noqa: F401
    TRADING_DAYS,
    after_tax_multiple,
    after_tax_summary,
    beta_alpha,
    cagr,
    cagr_from_multiple,
    calmar_ratio,
    compute_metrics,
    hit_rate,
    max_drawdown,
    multiple,
    sharpe_ratio,
    sortino_ratio,
    to_returns,
    volatility,
)
from .bootstrap import BootstrapResult, bootstrap as bootstrap_returns  # noqa: F401
from .bootstrap import bootstrap_table  # noqa: F401
from .cscv import CSCVResult, cscv_pbo, deflated_sharpe, expected_max_sharpe  # noqa: F401
from .windows import WindowTable, five_year_table, rolling_windows  # noqa: F401
from .walkforward import (
    Fold,
    assert_no_leakage,
    expanding_splits,
    oos_path,
    rolling_splits,
)  # noqa: F401
from .trials import TrialRegistry  # noqa: F401

__all__ = [
    "ACCOUNTING_MODES",
    "MODE_LABELS",
    "REAL_LIFE_MODE",
    "TRADING_DAYS",
    "BootstrapResult",
    "CSCVResult",
    "Fold",
    "TrialRegistry",
    "WindowTable",
    "after_tax_multiple",
    "after_tax_summary",
    "assert_no_leakage",
    "beta_alpha",
    "bootstrap_returns",
    "bootstrap_table",
    "cagr",
    "cagr_from_multiple",
    "calmar_ratio",
    "compute_metrics",
    "cscv_pbo",
    "deflated_sharpe",
    "dimension_table",
    "eur_ledger_available",
    "expanding_splits",
    "expected_max_sharpe",
    "five_year_table",
    "hit_rate",
    "is_real_life",
    "ledger_summary",
    "max_drawdown",
    "multiple",
    "native_after_tax_multiple",
    "oos_path",
    "require_mode",
    "rolling_splits",
    "rolling_windows",
    "sharpe_ratio",
    "sortino_ratio",
    "to_returns",
    "volatility",
]
