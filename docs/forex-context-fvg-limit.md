# Forex cross-market context and FVG limit opportunities

SwiftChart evaluates DXY for USD pairs and WTI for CAD pairs only after a Forex setup has passed technical validation. Context changes confidence by at most -10 to +10 and cannot create a trade, bypass risk filters, or rewrite a stored historical snapshot.

The experimental `liquidity_sweep_fvg_limit_v1` strategy recognizes a confirmed sweep/reclaim, ATR-qualified displacement, and a strict three-candle fair value gap. It creates only `BUY_LIMIT` or `SELL_LIMIT` opportunities in `WAIT_FOR_RETEST`; a separate idempotent lifecycle promotes an opportunity to `ACTIVE_TRADE` only after price touches its entry.

Shadow mode is itself a scan mode: when it is true, the detector scans, persists, and lifecycle-tracks even if the public/live feature flag is false. Shadow opportunities never enqueue public Telegram messages and broker execution remains impossible:

```env
FOREX_DXY_CONTEXT_ENABLED=true
FOREX_OIL_CONTEXT_ENABLED=true
FOREX_LIQUIDITY_FVG_LIMIT_ENABLED=false
FOREX_LIQUIDITY_FVG_LIMIT_SHADOW_MODE=true
FOREX_LIQUIDITY_FVG_AUTO_EXECUTION_ENABLED=false
```

External symbols are `USDOLLAR_USD` and `WTICO_USD` on OANDA, with `DXY` and `WTI/USD` used by Twelve Data. Missing, incomplete, or stale context has zero influence and is labeled unavailable.

When provider DXY is unavailable, USD context falls back to an equal-weight synthetic basket of EURUSD, GBPUSD, AUDUSD, NZDUSD, USDJPY, USDCHF, and USDCAD completed candles. Five-candle movement is divided by ATR, quote-USD pairs are inverted, every component is capped at ±2.5 ATR and scaled to [-1, 1], and the available fresh components are averaged. At least four components are required.

The sweep may occur one to three completed candles before displacement. A close back through the swept level must confirm the reclaim before the displacement candle, and the displacement must still create a strict three-candle FVG.

`TP1_HIT_TP2_RUNNING` is the partial-active state. `TP1_HIT` is terminal and means the configured position was fully closed at TP1.

Pending opportunities are shown separately under **Limit Opportunities**. Expired and cancelled opportunities never enter active-trade PnL or win/loss statistics.
