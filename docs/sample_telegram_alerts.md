# Sample Telegram alerts

These examples mirror SwiftChart's current `format_trade_alert` output. They are deterministic demo messages linked to records in `sample_scan_logs.json`; they are not live alerts or audited performance claims.

## Alert 1 — `scan_20260603_001`

```text
SwiftChart Trade Alert: BTCUSDT — 4H

Signal: Potential Long
Strength: Strong Setup
Setup Score: 88/100
Grade: A+ Setup

Entry: 104,820 — 105,240
Stop Loss: 102,960
TP1: 109,300
TP2: 112,700
R:R: 3.71

Reason:
Market structure favors trend-continuation pullbacks. Higher timeframe bias is aligned (HTF_BULLISH).
```

Lifecycle result: `TP2_HIT` / `WIN`

## Alert 2 — `scan_20260604_006`

```text
SwiftChart Trade Alert: ETHUSDT — 2H

Signal: Potential Short
Strength: Medium Setup
Setup Score: 82/100
Grade: A+ Setup

Entry: 3,862 — 3,884
Stop Loss: 3,942
TP1: 3,714
TP2: 3,626
R:R: 3.58

Reason:
Price is holding below support with continuation confirmation. Higher timeframe bias is aligned (HTF_BEARISH).
```

Lifecycle result: `TP1_HIT` / `PARTIAL_WIN`

## Alert 3 — `scan_20260606_014`

```text
SwiftChart Trade Alert: SOLUSDT — 1H

Signal: Potential Long
Strength: Fast Setup
Setup Score: 79/100
Grade: Valid Setup

Entry: 157.8 — 158.45
Stop Loss: 155.2
TP1: 163.7
TP2: 167.4
R:R: 3.17

Reason:
Price is trading at a clean range extreme instead of the middle. Long idea has a confirmed liquidity sweep/reclaim with quality score 83.
```

Lifecycle result: `SL_HIT` / `LOSS`

## Alert 4 — `scan_20260609_025`

```text
SwiftChart Trade Alert: HYPEUSDT — 4H

Signal: Potential Long
Strength: Strong Setup
Setup Score: 91/100
Grade: A+ Setup

Entry: 41.28 — 41.62
Stop Loss: 39.84
TP1: 44.7
TP2: 47.2
R:R: 3.57

Reason:
Price is holding above resistance with continuation confirmation. Higher timeframe bias is aligned (HTF_BULLISH).
```

Lifecycle result: `TP2_HIT` / `WIN`

## Alert 5 — `scan_20260612_037`

```text
SwiftChart Trade Alert: LINKUSDT — 6H

Signal: Potential Short
Strength: Strong Setup
Setup Score: 85/100
Grade: A+ Setup

Entry: 17.42 — 17.55
Stop Loss: 18.08
TP1: 16.31
TP2: 15.62
R:R: 3.13

Reason:
Market structure favors trend-continuation pullbacks. Higher timeframe bias is aligned (HTF_BEARISH).
```

Lifecycle result: `ENTRY_TRIGGERED` / `OPEN`

## Alert 6 — `scan_20260616_048`

```text
SwiftChart Trade Alert: AVAXUSDT — 4H

Signal: Potential Long
Strength: Strong Setup
Setup Score: 81/100
Grade: A+ Setup

Entry: 22.84 — 23.02
Stop Loss: 22.18
TP1: 24.42
TP2: 25.36
R:R: 3.24

Reason:
Market structure is transitioning bullish and long confirmation is being tested. Higher timeframe bias is aligned (HTF_BULLISH).
```

Lifecycle result: `PENDING` / `OPEN`

> Not financial advice. Manage your risk.
