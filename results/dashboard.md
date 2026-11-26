# stonks-bot dashboard

> **SYNTHETIC DEMO — illustrative only, not live state or a forward result.**

Generated: 2026-11-26T16:34:36+00:00  
Mode: paper (synthetic example)  
Overall: **WARN**  
Session: 2026-11-25  
Next session: 2026-11-27

## Books, risk, data and gate

| book | market | status | equity | equity EUR | drawdown | gross | forward gate | reconcile | last run | next | data fresh |
|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| monthly | US | ok | 12,480.00 USD | 11,356.80 EUR | -12.40% | 166.00% | WAIT 3/5 verified | OK | 2026-11-25 | 2026-11-27 | yes |
| monthly | TECH | risk_off | 9,120.00 USD | 8,299.20 EUR | -21.60% | 132.00% | WAIT 1/5 verified | OK | 2026-11-25 | 2026-11-27 | yes |

## Book details

### monthly / US

- Status: **ok**
- State: `synthetic/monthly_us.json` · updated 2026-11-25T22:00:00+00:00 · ledger rows 42
- Risk: drawdown -12.40% · risk_off **False** · reason —
- FX: 0.91 (2026-11-24) · data fresh: yes · last bar 2026-11-25
- Forward gate: WAIT 3/5 verified · reconcile: OK

| ticker | target | current | value | qty | avg | last |
|---|---:|---:|---:|---:|---:|---:|
| MSFT | 18.00% | 10.38% | 1,296.00 USD | 3.0 | 420.00 USD | 432.00 USD |
| NVDA | 32.00% | 15.51% | 1,936.00 USD | 11.0 | 145.00 USD | 176.00 USD |
| SMH | 145.00% | 67.25% | 8,393.40 USD | 18.0 | 410.00 USD | 466.30 USD |

| ticker | side | qty | ref | status |
|---|---|---:|---:|---|
| SMH | BUY | 1.2 |  | PENDING |

### monthly / TECH

- Status: **risk_off**
- State: `synthetic/monthly_tech.json` · updated 2026-11-25T22:00:00+00:00 · ledger rows 39
- Risk: drawdown -21.60% · risk_off **True** · reason trend
- FX: 0.91 (2026-11-24) · data fresh: yes · last bar 2026-11-25
- Forward gate: WAIT 1/5 verified · reconcile: OK

| ticker | target | current | value | qty | avg | last |
|---|---:|---:|---:|---:|---:|---:|
| SMH | 66.00% | 61.36% | 5,595.60 USD | 12.0 | 405.00 USD | 466.30 USD |

## Findings and warnings

| level | code | message | source |
|---|---|---|---|
| WARN | synthetic | SYNTHETIC DEMO — not live state or a forward result | demo |
| INFO | gate | monthly/US: WAIT 3/5 verified | demo |
| INFO | gate | monthly/TECH: WAIT 1/5 verified | demo |

## Research evidence

### Baseline after-tax multiples

| market | book | native | terminal-FX | EUR ledger (real-life) |
|---|---|---:|---:|---:|
| TECH | monthly | 51.82x | 57.01x | **52.98x** |
| US | monthly | 64.41x | 70.86x | **66.17x** |
| TECH | tax | 46.26x | 50.89x | **48.8x** |

- Data quality: **PASS** · 0 hard failures · 122 warnings
- Survivorship: TECH static 49 names; US missing 128/789 (16.2%).
- Trial registry rows: 14
- The EUR ledger is the real-life column; terminal-FX is a legacy upper bound.

_Read-only static report. Sources: synthetic demo (no files read)_
