# Forex cross-market context and FVG limit opportunities

SwiftChart evaluates DXY for USD pairs and WTI for CAD pairs only after a Forex setup has passed technical validation. Context changes confidence by at most -10 to +10 and cannot create a trade, bypass risk filters, or rewrite a stored historical snapshot.

The experimental `liquidity_sweep_fvg_limit_v1` strategy recognizes a confirmed sweep/reclaim, ATR-qualified displacement, and a strict three-candle fair value gap. It creates only `BUY_LIMIT` or `SELL_LIMIT` opportunities in `WAIT_FOR_RETEST`; a separate idempotent lifecycle promotes an opportunity to `ACTIVE_TRADE` only after price touches its entry.

Defaults keep broker execution impossible and the detector disabled until shadow observation is intentionally enabled:

```env
FOREX_DXY_CONTEXT_ENABLED=true
FOREX_OIL_CONTEXT_ENABLED=true
FOREX_LIQUIDITY_FVG_LIMIT_ENABLED=false
FOREX_LIQUIDITY_FVG_LIMIT_SHADOW_MODE=true
FOREX_LIQUIDITY_FVG_AUTO_EXECUTION_ENABLED=false
```

External symbols are `USDOLLAR_USD` and `WTICO_USD` on OANDA, with `DXY` and `WTI/USD` used by Twelve Data. Missing, incomplete, or stale context has zero influence and is labeled unavailable.

Pending opportunities are shown separately under **Limit Opportunities**. Expired and cancelled opportunities never enter active-trade PnL or win/loss statistics.
