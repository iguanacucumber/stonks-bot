"""EUR-native ledger adapter.

The real implementation lives in `bot/eur_ledger.py` (merged from
`v3/eur-ledger`, PR #22, with year-dependent French PFU). Alpha Lab reports use
it directly so there is one accounting implementation, not a research copy.
"""

from __future__ import annotations

from bot.eur_ledger import EurLedgerConfig, EurLedgerResult, replay

__all__ = ["EurLedgerConfig", "EurLedgerResult", "replay"]
