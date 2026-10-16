# HF fade walk-forward (1m bars)

Generated: 2026-10-16T11:33:38Z
Pairs: SUIUSD, ALGOUSD, ADAUSD, KPEPEUSD, NEARUSD, ZROUSD, TAOUSD, ARBUSD
Fade window=5m min_z=2.0; horizon≈30m; step=15m

Caveat: bar first-touch grader (both-touch→loss). Not tick 2-touch parity.

| rule | n | HR | Wilson LB | 1st half HR | 2nd half HR |
|------|--:|---:|----------:|------------:|------------:|
| trend_2h | 12210 | 0.495 | 0.486 | 0.490 | 0.500 |
| always_long | 12225 | 0.506 | 0.497 | 0.516 | 0.496 |
| fade_spike | 783 | 0.515 | 0.480 | 0.491 | 0.538 |

## Gate for live changes

- Prefer fade_spike when both halves ≥ always_long and HR ≥ 0.51 with n≥1000.
- Do not raise SN89_HF_MAX_PER_DAY on a rule whose halves disagree by >3pp.
- Tick grader-parity backtest still required before trusting production HR.

