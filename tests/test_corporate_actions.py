"""Corporate-action tests: splits, dividends, state, ledger and reconcile.

Run: uv run python tests/test_corporate_actions.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.live import reconcile  # noqa: E402
from bot.live.config import LiveConfig  # noqa: E402
from bot.live.corporate import CorporateAction, apply_to_state  # noqa: E402
from bot.live.models import FILLED, Fill, OrderIntent, Plan, Portfolio, Position  # noqa: E402
from bot.live.state import LiveState, StateStore  # noqa: E402


def _state(tmp: str) -> tuple[LiveConfig, LiveState, StateStore]:
    cfg = LiveConfig(
        book="monthly",
        market="US",
        state_dir=tmp,
        leverage=1.0,
        financing_bps=0.0,
    )
    state = LiveState.new(cfg)
    order = OrderIntent("o1", "SMH", "BUY", 10.0, 100.0, "test", "2025-12-31")
    order.status = FILLED
    order.filled_qty = 10.0
    plan = Plan(
        plan_id="p1",
        book="monthly",
        market="US",
        signal_session="2025-12-30",
        fill_session="2025-12-31",
        created_at="2025-12-30T22:00:00+00:00",
        target_weights={"SMH": 1.0},
        current_weights={},
        orders=[order],
    )
    plan.fills = [
        Fill(
            fill_id="f1",
            order_id="o1",
            ticker="SMH",
            side="BUY",
            qty=10.0,
            price=100.0,
            commission=0.0,
            session="2025-12-31",
            created_at="2025-12-31T14:30:00+00:00",
        )
    ]
    state.plans = {"p1": plan}
    state.portfolio = Portfolio(
        cash=cfg.capital - 1000.0,
        positions={"SMH": Position(10.0, 100.0, 100.0, "2025-12-31")},
    )
    store = StateStore(cfg)
    store.save(state)
    return cfg, state, store


def test_split_adjusts_position_and_cost() -> None:
    with TemporaryDirectory() as tmp:
        cfg, state, store = _state(tmp)
        action = CorporateAction(
            event_id="split-1",
            kind="split",
            ticker="SMH",
            session="2026-01-02",
            ratio=2.0,
        )
        event = apply_to_state(state, action)
        store.save(state)
        store.upsert_corporate_action(event)
        assert state.portfolio.positions["SMH"].qty == 20.0
        assert state.portfolio.positions["SMH"].avg_cost == 50.0
        assert state.portfolio.positions["SMH"].last_price == 50.0
        assert state.portfolio.cash == cfg.capital - 1000.0
        assert event["qty_before"] == 10.0 and event["qty_after"] == 20.0
        assert len(store.corporate_action_rows()) == 1
        loaded = StateStore(cfg).load()
        assert loaded is not None
        assert loaded.portfolio.positions["SMH"].qty == 20.0
        assert loaded.corporate_actions[0]["event_id"] == "split-1"
        rep = reconcile.reconcile(cfg, loaded, StateStore(cfg))
        assert rep.ok, rep.issues


def test_dividend_adds_net_cash_and_eur_fields() -> None:
    with TemporaryDirectory() as tmp:
        cfg, state, store = _state(tmp)
        action = CorporateAction(
            event_id="div-1",
            kind="dividend",
            ticker="SMH",
            session="2026-01-02",
            amount_per_share=1.0,
            withholding_rate=0.15,
            fx_rate=0.90,
        )
        event = apply_to_state(state, action)
        store.save(state)
        store.upsert_corporate_action(event)
        assert event["gross_cash"] == 10.0
        assert event["withholding"] == 1.5
        assert event["net_cash"] == 8.5
        assert state.portfolio.cash == cfg.capital - 1000.0 + 8.5
        assert state.dividend_income == 10.0
        assert state.dividend_withholding == 1.5
        assert abs(state.dividend_income_eur - 8.5 * 0.90) < 1e-9
        assert state.last_cash_eur == state.portfolio.cash * 0.90
        rep = reconcile.reconcile(cfg, state, store)
        assert rep.ok, rep.issues


def test_missing_position_and_missing_ledger_fail() -> None:
    with TemporaryDirectory() as tmp:
        cfg, state, store = _state(tmp)
        try:
            apply_to_state(
                state,
                CorporateAction(
                    event_id="bad",
                    kind="split",
                    ticker="QQQ",
                    session="2026-01-02",
                    ratio=2.0,
                ),
            )
            raise AssertionError("split on a missing position must fail")
        except ValueError:
            pass
        # A state event without its corporate-action CSV row must fail reconcile.
        state.corporate_actions.append(
            {
                "event_id": "manual",
                "kind": "dividend",
                "ticker": "SMH",
                "session": "2026-01-02",
                "net_cash": 0.0,
            }
        )
        store.save(state)
        rep = reconcile.reconcile(cfg, state, store)
        assert not rep.ok
        assert any("corporate action" in issue for issue in rep.issues)


def test_cli_corporate_applies_and_is_idempotent() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import live as live_cli

    with TemporaryDirectory() as tmp:
        cfg, state, store = _state(tmp)
        rc = live_cli.main(
            [
                "corporate",
                "--book",
                "monthly",
                "--market",
                "US",
                "--state-dir",
                tmp,
                "--kind",
                "dividend",
                "--ticker",
                "SMH",
                "--session",
                "2026-01-02",
                "--amount",
                "1.0",
                "--withholding",
                "0.15",
                "--fx-rate",
                "0.90",
                "--event-id",
                "cli-div",
                "--allow-config-change",
            ]
        )
        assert rc == 0
        loaded = StateStore(cfg).load()
        assert loaded is not None
        assert loaded.portfolio.cash == cfg.capital - 1000.0 + 8.5
        rc2 = live_cli.main(
            [
                "corporate",
                "--book",
                "monthly",
                "--market",
                "US",
                "--state-dir",
                tmp,
                "--kind",
                "dividend",
                "--ticker",
                "SMH",
                "--session",
                "2026-01-02",
                "--amount",
                "1.0",
                "--event-id",
                "cli-div",
                "--allow-config-change",
            ]
        )
        assert rc2 == 1


def main() -> None:
    test_split_adjusts_position_and_cost()
    test_dividend_adds_net_cash_and_eur_fields()
    test_missing_position_and_missing_ledger_fail()
    test_cli_corporate_applies_and_is_idempotent()
    print("ok: corporate splits, dividends, state ledger and reconcile")


if __name__ == "__main__":
    main()
