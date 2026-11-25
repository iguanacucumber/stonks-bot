# Ten rounds (historical chain)

> **Historical.** This chain tuned the pre-SMH config on the pre-rebuild 807-ticker panel.
> The shipped bot has since moved to SMH + 2.5x + a daily 200-day cut + month-end/next-open.
> Numbers below are regenerated on the current panel by `scripts/rounds10.py`; the baseline
> row moved after the panel rebuild, but the chain remains monotone.

Every row changes **one knob** from the row above, same engine, same costs (10 bps/side),
window 2015-01-02 → 2026-11-24, $10,000 start.

| # | change | full $10k | cagr | sharpe | maxDD | oos $10k | y26 $10k | cost30 $10k |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 00 | baseline | $34,531 | 11.0% | 0.67 | -23.3% | $16,238 | $10,063 | $25,510 |
| 01 | top_n=5 | $35,815 | 11.4% | 0.57 | -31.5% | $24,419 | $10,364 | $25,184 |
| 02 | core=SMH | $123,265 | 23.6% | 0.86 | -40.2% | $31,067 | $11,291 | $110,951 |
| 03 | core_lookback=126 | $136,242 | 24.6% | 0.90 | -35.5% | $38,974 | $11,742 | $118,098 |
| 04 | stop=1.0 | $136,498 | 24.6% | 0.88 | -40.6% | $38,157 | $11,587 | $119,331 |
| 05 | regime_gross=1.0 | $160,087 | 26.3% | 0.90 | -42.1% | $41,050 | $11,587 | $138,325 |
| 06 | core=SOXX | $169,271 | 26.9% | 0.89 | -39.9% | $37,633 | $12,308 | $144,020 |
| 07 | gross=1.0 | $170,507 | 27.0% | 0.89 | -40.4% | $37,710 | $12,274 | $144,781 |
| 08 | sector_cap=0.4 | $184,375 | 27.8% | 0.90 | -39.5% | $35,599 | $12,593 | $157,299 |
| 09 | skip=10 | $195,306 | 28.5% | 0.92 | -39.5% | $44,090 | $12,593 | $166,760 |
| 10 | top_n=6 | $214,182 | 29.5% | 0.96 | -39.5% | $44,884 | $12,573 | $183,281 |

Reproduce: `uv run python scripts/rounds10.py` (asserts every row beats the one above).

## Verdict

The chain is monotone and the final row reaches **$214,182**. Rounds 1-3 improved the held-back 2023-2026 window; later rounds mostly add exposure and are in-sample. The whole chain is a semiconductor bet: it is beta, not stock alpha. The shipped default has moved on (SMH, 2.5x, daily trend cut, month-end rebalance, next-open fills); see `results/leverage.md` and the README.
