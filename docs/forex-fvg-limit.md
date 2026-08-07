# Forex FVG limit opportunities

The experimental `liquidity_sweep_fvg_limit_v1` strategy recognizes a confirmed sweep/reclaim, ATR-qualified displacement, and a strict three-candle fair value gap. It creates only `BUY_LIMIT` or `SELL_LIMIT` opportunities in `WAIT_FOR_RETEST`; a separate idempotent lifecycle promotes an opportunity to `ACTIVE_TRADE` only after price touches its entry.

Shadow mode is itself a scan mode: when it is true, the detector scans, persists, and lifecycle-tracks even if the public/live feature flag is false. Shadow opportunities never enqueue public Telegram messages and broker execution remains impossible:

```env
FOREX_LIQUIDITY_FVG_LIMIT_ENABLED=false
FOREX_LIQUIDITY_FVG_LIMIT_SHADOW_MODE=true
FOREX_LIQUIDITY_FVG_AUTO_EXECUTION_ENABLED=false
```

The sweep may occur one to three completed candles before displacement. A close back through the swept level must confirm the reclaim before the displacement candle, and the displacement must still create a strict three-candle FVG.

`TP1_HIT_TP2_RUNNING` is the partial-active state. `TP1_HIT` is terminal and means the configured position was fully closed at TP1.

Pending opportunities are shown separately under **Limit Opportunities**. Expired and cancelled opportunities never enter active-trade PnL or win/loss statistics.
