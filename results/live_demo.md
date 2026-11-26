# Live autopilot demo transcript

Generated from the committed cache with a throwaway state dir `/tmp/opencode/live-demo-_skwlx9a`.

No network, no real broker, no secrets. The state dir is outside git.

## Plan (TECH monthly, committed cache, no write)

Command: `uv run python scripts/live.py plan --book monthly --market TECH --state-dir /tmp/opencode/live-demo-_skwlx9a --as-of 2026-11-25 --now 2026-11-26T02:00:00+00:00`

```text
LIVE PLAN (dry-run)  book=monthly (monthly momentum)  market=TECH (NASDAQ tech)  native=USD  reporting=EUR  broker=paper
recommendation: US monthly is the best-evidence live book; this TECH monthly run is for validation/research
session=2026-11-25  equity=10,000.00 USD  cash=10,000.00  gross=0.00%  equity_eur=€9,100.00 @ 0.910000 EUR/USD (2026-11-24)
signal=2026-11-25  fill=2026-11-27  plan=fc4f2484149b3273be3d  status=OK
data: bench last=2026-11-25  fresh=True  panel=2026-11-25
risk: drawdown=+0.00%  breaker_off=True  reason=trend  trend_breach=True  scale=0.50

target vs current
ticker        target   current    drift%      target$              order
AMGN          20.83%     0.00%    +20.83      2083.33         BUY 4.4398
COST          20.83%     0.00%    +20.83      2083.33         BUY 1.9169
CRWD          20.83%     0.00%    +20.83      2083.33         BUY 9.9538
GILD          20.83%     0.00%    +20.83      2083.33        BUY 11.7876
PANW          20.83%     0.00%    +20.83      2083.33         BUY 5.8498
PEP           20.83%     0.00%    +20.83      2083.33        BUY 14.4746

next orders for 2026-11-27 (6 intents, estimated cost 12.50 = 12.5 bps)
ticker     side          qty        ref     notional     comm     slip    status
AMGN       BUY        4.4398     469.24      2083.33     1.04     1.04   PENDING
COST       BUY        1.9169    1086.81      2083.33     1.04     1.04   PENDING
CRWD       BUY        9.9538     209.30      2083.33     1.04     1.04   PENDING
GILD       BUY       11.7876     176.74      2083.33     1.04     1.04   PENDING
PANW       BUY        5.8498     356.14      2083.33     1.04     1.04   PENDING
PEP        BUY       14.4746     143.93      2083.33     1.04     1.04   PENDING

rails: gross 1.250 <= 3.00  turnover 1.250 <= 3.00  orders 6 <= 50
execution model: next open + 5.0 bps slippage, 5.0 bps commission (min 1.00), max 2.0% of open volume
coverage: all required symbols fresh

NO ORDERS SENT — paper/dry-run only
forward gate: WAIT 1/5 verified dates (monthly/TECH, paper_audit.csv)
replay mode: --as-of 2026-11-25 (no state written)
```

Exit code: `0`

## Plan (US monthly, best-evidence label)

Command: `uv run python scripts/live.py plan --book monthly --market US --state-dir /tmp/opencode/live-demo-_skwlx9a --as-of 2026-11-25 --now 2026-11-26T02:00:00+00:00`

```text
LIVE PLAN (dry-run)  book=monthly (monthly momentum)  market=US (S&P 500)  native=USD  reporting=EUR  broker=paper
recommendation: US monthly is the best-evidence live book; TECH monthly remains the forward validation track
session=2026-11-25  equity=10,000.00 USD  cash=10,000.00  gross=0.00%  equity_eur=€9,100.00 @ 0.910000 EUR/USD (2026-11-24)
signal=2026-11-25  fill=2026-11-27  plan=0bd999b47ad179caebac  status=OK
data: bench last=2026-11-25  fresh=True  panel=2026-11-25
risk: drawdown=+0.00%  breaker_off=True  reason=trend  trend_breach=True  scale=0.50

target vs current
ticker        target   current    drift%      target$              order
CRL           20.83%     0.00%    +20.83      2083.33         BUY 6.2736
MPC           20.83%     0.00%    +20.83      2083.33         BUY 6.0165
MRNA          20.83%     0.00%    +20.83      2083.33         BUY 9.4337
OKTA          20.83%     0.00%    +20.83      2083.33        BUY 10.3253
ROP           20.83%     0.00%    +20.83      2083.33         BUY 4.3125
VLO           20.83%     0.00%    +20.83      2083.33         BUY 6.4499

next orders for 2026-11-27 (6 intents, estimated cost 12.50 = 12.5 bps)
ticker     side          qty        ref     notional     comm     slip    status
CRL        BUY        6.2736     332.08      2083.33     1.04     1.04   PENDING
MPC        BUY        6.0165     346.27      2083.33     1.04     1.04   PENDING
MRNA       BUY        9.4337     220.84      2083.33     1.04     1.04   PENDING
OKTA       BUY       10.3253     201.77      2083.33     1.04     1.04   PENDING
ROP        BUY        4.3125     483.09      2083.33     1.04     1.04   PENDING
VLO        BUY        6.4499     323.00      2083.33     1.04     1.04   PENDING

rails: gross 1.250 <= 3.00  turnover 1.250 <= 3.00  orders 6 <= 50
execution model: next open + 5.0 bps slippage, 5.0 bps commission (min 1.00), max 2.0% of open volume
coverage: all required symbols fresh

NO ORDERS SENT — paper/dry-run only
forward gate: WAIT 0/5 verified dates (no monthly/US rows in paper_audit.csv)
replay mode: --as-of 2026-11-25 (no state written)
```

Exit code: `0`

## Paper run 1: plan from 2026-11-24, fill next open

Command: `uv run python scripts/live.py run --book monthly --market TECH --state-dir /tmp/opencode/live-demo-_skwlx9a --paper --as-of 2026-11-24 --now 2026-11-25T02:00:00+00:00`

```text
LIVE PAPER RUN  book=monthly (monthly momentum)  market=TECH (NASDAQ tech)  native=USD  reporting=EUR  broker=paper
recommendation: US monthly is the best-evidence live book; this TECH monthly run is for validation/research
session=2026-11-24  equity=10,000.00 USD  cash=10,000.00  gross=0.00%  equity_eur=€9,100.00 @ 0.910000 EUR/USD (2026-11-24)
signal=2026-11-24  fill=2026-11-25  plan=dbc2640c6b47bd10e59a  status=OK
data: bench last=2026-11-24  fresh=True  panel=2026-11-24
risk: drawdown=+0.00%  breaker_off=True  reason=trend  trend_breach=True  scale=0.50

target vs current
ticker        target   current    drift%      target$              order
AMGN          20.83%     0.00%    +20.83      2083.33         BUY 4.3958
COST          20.83%     0.00%    +20.83      2083.33         BUY 1.9083
CRWD          20.83%     0.00%    +20.83      2083.33         BUY 9.8802
GILD          20.83%     0.00%    +20.83      2083.33        BUY 11.6851
META          20.83%     0.00%    +20.83      2083.33         BUY 3.5322
PANW          20.83%     0.00%    +20.83      2083.33         BUY 5.9020

next orders for 2026-11-25 (6 intents, estimated cost 12.50 = 12.5 bps)
ticker     side          qty        ref     notional     comm     slip    status
AMGN       BUY        4.3958     473.94      2083.33     1.04     1.04   PENDING
COST       BUY        1.9083    1091.71      2083.33     1.04     1.04   PENDING
CRWD       BUY        9.8802     210.86      2083.33     1.04     1.04   PENDING
GILD       BUY       11.6851     178.29      2083.33     1.04     1.04   PENDING
META       BUY        3.5322     589.82      2083.33     1.04     1.04   PENDING
PANW       BUY        5.9020     352.99      2083.33     1.04     1.04   PENDING

rails: gross 1.250 <= 3.00  turnover 1.250 <= 3.00  orders 6 <= 50
execution model: next open + 5.0 bps slippage, 5.0 bps commission (min 1.00), max 2.0% of open volume
coverage: all required symbols fresh

NO ORDERS SENT — paper/dry-run only
forward gate: WAIT 1/5 verified dates (monthly/TECH, paper_audit.csv)
replay mode: --as-of 2026-11-24
```

Exit code: `0`

## Paper run 2: fill 2026-11-24 plan at 2026-11-25 open, plan next

Command: `uv run python scripts/live.py run --book monthly --market TECH --state-dir /tmp/opencode/live-demo-_skwlx9a --paper --as-of 2026-11-25 --now 2026-11-26T02:00:00+00:00`

```text
LIVE PAPER RUN  book=monthly (monthly momentum)  market=TECH (NASDAQ tech)  native=USD  reporting=EUR  broker=paper
recommendation: US monthly is the best-evidence live book; this TECH monthly run is for validation/research
session=2026-11-25  equity=10,237.63 USD  cash=-2,288.32  gross=122.35%  equity_eur=€9,316.24 @ 0.910000 EUR/USD (2026-11-24)
signal=2026-11-25  fill=2026-11-27  plan=0af532ad035e7f0e4202  status=OK
data: bench last=2026-11-25  fresh=True  panel=2026-11-25
risk: drawdown=+0.00%  breaker_off=True  reason=trend  trend_breach=True  scale=0.50
settled prior plan dbc2640c6b47bd10e59a (6 fills, 6.14 commissions)

fills at the open
ticker     side          qty      price     notional     comm        pnl    session
AMGN       BUY        4.3958     472.99      2079.14     1.04       0.00 2026-11-25
COST       BUY        1.9083    1092.55      2084.93     1.04       0.00 2026-11-25
CRWD       BUY        9.8802     202.11      1996.89     1.00       0.00 2026-11-25
GILD       BUY       11.6851     178.54      2086.25     1.04       0.00 2026-11-25
META       BUY        3.5322     575.63      2033.20     1.02       0.00 2026-11-25
PANW       BUY        5.9020     339.17      2001.77     1.00       0.00 2026-11-25

target vs current
ticker        target   current    drift%      target$              order
AMGN          20.83%    20.15%     +0.69      2132.84                  -
COST          20.83%    20.26%     +0.57      2132.84                  -
CRWD          20.83%    20.20%     +0.63      2132.84                  -
GILD          20.83%    20.17%     +0.66      2132.84                  -
META          20.83%    21.04%     -0.21      2132.84                  -
PANW          20.83%    20.53%     +0.30      2132.84                  -

next orders for 2026-11-27 (0 intents, estimated cost 0.00 = 0.0 bps)
ticker     side          qty        ref     notional     comm     slip    status
  (no orders)

rails: gross 1.250 <= 3.00  turnover 0.000 <= 3.00  orders 0 <= 50
execution model: next open + 5.0 bps slippage, 5.0 bps commission (min 1.00), max 2.0% of open volume
coverage: all required symbols fresh

NO ORDERS SENT — paper/dry-run only
forward gate: WAIT 1/5 verified dates (monthly/TECH, paper_audit.csv)
replay mode: --as-of 2026-11-25
```

Exit code: `0`

## Paper run 3: exact rerun of the same session (idempotent no-op)

Command: `uv run python scripts/live.py run --book monthly --market TECH --state-dir /tmp/opencode/live-demo-_skwlx9a --paper --as-of 2026-11-25 --now 2026-11-26T03:00:00+00:00`

```text
LIVE PAPER RUN  book=monthly (monthly momentum)  market=TECH (NASDAQ tech)  native=USD  reporting=EUR  broker=paper
recommendation: US monthly is the best-evidence live book; this TECH monthly run is for validation/research
session=2026-11-25  equity=10,237.63 USD  cash=-2,288.32  gross=125.00%  equity_eur=€9,316.24 @ 0.910000 EUR/USD (2026-11-24)
signal=2026-11-25  fill=2026-11-27  plan=0af532ad035e7f0e4202  status=OK
data: bench last=2026-11-25  fresh=True  panel=2026-11-25
risk: drawdown=+0.00%  breaker_off=True  reason=trend  trend_breach=True  scale=0.50
settled prior plan dbc2640c6b47bd10e59a (6 fills, 6.14 commissions)

fills at the open
ticker     side          qty      price     notional     comm        pnl    session
AMGN       BUY        4.3958     472.99      2079.14     1.04       0.00 2026-11-25
COST       BUY        1.9083    1092.55      2084.93     1.04       0.00 2026-11-25
CRWD       BUY        9.8802     202.11      1996.89     1.00       0.00 2026-11-25
GILD       BUY       11.6851     178.54      2086.25     1.04       0.00 2026-11-25
META       BUY        3.5322     575.63      2033.20     1.02       0.00 2026-11-25
PANW       BUY        5.9020     339.17      2001.77     1.00       0.00 2026-11-25

target vs current
ticker        target   current    drift%      target$              order
AMGN          20.83%    20.15%     +0.69      2132.84                  -
COST          20.83%    20.26%     +0.57      2132.84                  -
CRWD          20.83%    20.20%     +0.63      2132.84                  -
GILD          20.83%    20.17%     +0.66      2132.84                  -
META          20.83%    21.04%     -0.21      2132.84                  -
PANW          20.83%    20.53%     +0.30      2132.84                  -

next orders for 2026-11-27 (0 intents, estimated cost 0.00 = 0.0 bps)
ticker     side          qty        ref     notional     comm     slip    status
  (no orders)

rails: gross 1.250 <= 3.00  turnover 0.000 <= 3.00  orders 0 <= 50
execution model: next open + 5.0 bps slippage, 5.0 bps commission (min 1.00), max 2.0% of open volume
coverage: all required symbols fresh

NO ORDERS SENT — paper/dry-run only
forward gate: WAIT 1/5 verified dates (monthly/TECH, paper_audit.csv)
idempotent no-op: this session is already recorded
replay mode: --as-of 2026-11-25
```

Exit code: `0`

## Reconcile fills / orders / positions / cash / EUR / ledger

Command: `uv run python scripts/live.py reconcile --book monthly --market TECH --state-dir /tmp/opencode/live-demo-_skwlx9a --now 2026-11-26T03:00:00+00:00`

```text
RECONCILE OK  state=/tmp/opencode/live-demo-_skwlx9a/monthly_tech.json  ledger_rows=2
  plans: 2
  orders: 6
  fills: 6
  positions: 6
  cash: -2288.319348
  fees_paid: 6.142643
  realized_pnl: 0.0
  financing_paid: 0.0
  fees_paid_eur: 5.589805
  realized_pnl_eur: 0.0
  financing_paid_eur: 0.0
  last_fx_rate: 0.91000005
  pending: None
all fills, orders, positions, cash and ledger rows agree
```

Exit code: `0`

## Status (book, pending plan, health, forward gate)

Command: `uv run python scripts/live.py status --book monthly --market TECH --state-dir /tmp/opencode/live-demo-_skwlx9a --as-of 2026-11-25 --now 2026-11-26T03:00:00+00:00`

```text
LIVE STATUS  book=monthly (monthly momentum)  market=TECH (NASDAQ tech)  native=USD  reporting=EUR  broker=paper
recommendation: US monthly is the best-evidence live book; this TECH monthly run is for validation/research
session=2026-11-25  equity=10,237.63 USD  cash=-2,288.32  gross=122.35%  equity_eur=€9,316.24 @ 0.910000 EUR/USD (2026-11-24)
data: bench last=2026-11-25  fresh=True  panel=2026-11-25
risk: drawdown=-0.00%  breaker_off=True  reason=trend  trend_breach=True  scale=0.50

read-only status; no state written
forward gate: WAIT 1/5 verified dates (monthly/TECH, paper_audit.csv)
state: /tmp/opencode/live-demo-_skwlx9a/monthly_tech.json
updated=2026-11-26T15:05:53+00:00  created=2026-11-26T15:05:52+00:00  last_session=2026-11-25  last_mark=2026-11-25
cash=-2,288.32  equity_hint=10,237.63  fees=6.14  financing=0.00  realized=0.00
fx: USD->EUR 0.910000 (2026-11-24)  equity_eur=9,316.24  cash_eur=-2,082.37  gross_eur=1.11
EUR ledger: fees_eur=5.59  financing_eur=0.00  realized_eur=0.00
risk_off=True  reason=trend  target_gross=1.250

positions
ticker              qty        avg       last        value
AMGN             4.3958     472.99     469.24      2062.67
COST             1.9083    1092.55    1086.81      2073.98
CRWD             9.8802     202.11     209.30      2067.92
GILD            11.6851     178.54     176.74      2065.22
META             3.5322     575.63     609.89      2154.22
PANW             5.9020     339.17     356.14      2101.92

target book
  AMGN        20.833%
  COST        20.833%
  CRWD        20.833%
  GILD        20.833%
  META        20.833%
  PANW        20.833%

pending plan: none

health
  [OK  ] market_session: 2026-11-25
  [OK  ] benchmark_fresh: SPY last=2026-11-25 session=2026-11-25
  [OK  ] fx_eur: USD->EUR 0.910000 (2026-11-24)
  [OK  ] symbols_fresh: all fresh
  [OK  ] state: updated 2026-11-26T15:05:53+00:00
  [OK  ] pending_plan: none
  [OK  ] duplicate_orders: 6 unique ids / 6 orders
  [OK  ] duplicate_fills: 6 unique ids / 6 fills
  [OK  ] ledger: in sync
```

Exit code: `0`

## Holiday gate: 2026-11-26 is Thanksgiving (no orders, exit 2)

Command: `uv run python scripts/live.py plan --book monthly --market TECH --state-dir /tmp/opencode/live-demo-_skwlx9a --as-of 2026-11-26 --now 2026-11-27T03:00:00+00:00`

```text
LIVE PLAN (dry-run)  book=monthly (monthly momentum)  market=TECH (NASDAQ tech)  native=USD  reporting=EUR  broker=paper
recommendation: US monthly is the best-evidence live book; this TECH monthly run is for validation/research
session=2026-11-26  equity=10,000.00 USD  cash=10,000.00  gross=0.00%
data: bench last=None  fresh=None  panel=None
BLOCKED: market closed: 2026-11-26 is not a US trading session
no orders will be sent
forward gate: WAIT 1/5 verified dates (monthly/TECH, paper_audit.csv)
replay mode: --as-of 2026-11-26 (no state written)
```

Exit code: `2`

## Artifacts

- state bytes: 17905
- ledger data rows: 2
- ledger columns: see `monthly_tech.csv` in the throwaway dir

## Tests run for this demo

```bash
uv run python tests/test_live.py
uv run python tests/test_core.py
uv run python tests/test_data.py
uv run python tests/test_brokers.py
uv run python tests/test_pea.py
```
