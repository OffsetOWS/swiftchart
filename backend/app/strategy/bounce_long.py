"""Independent multi-candle counter-trend bounce-long setup."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd

from app.config import get_settings
from app.models.schemas import MarketRegimeSnapshot, RiskSettings, TradeIdea, Zone
from app.strategy.decision_engine import evaluate_strategy_decision
from app.strategy.strict_trend_short import candle_is_completed
from app.strategy.support_resistance import average_true_range

logger = logging.getLogger(__name__)
ELIGIBLE_REGIMES = {"TRENDING_DOWN", "BREAKDOWN", "TRANSITION_TO_BULLISH"}
_KILL_SWITCH_LOGGED = False


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    change = close.astype(float).diff()
    gain = change.clip(lower=0).rolling(period).mean()
    loss = (-change.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def _exhaustion(df: pd.DataFrame, i: int) -> bool:
    # Preserve the bounce setup's exhaustion gate: bullish divergence,
    # capitulation volume/wick, or available derivatives exhaustion.
    if i >= 20 and "volume" in df:
        prior = pd.to_numeric(df["volume"].iloc[i - 20:i], errors="coerce").mean()
        row = df.iloc[i]
        span = float(row.high) - float(row.low)
        wick = min(float(row.open), float(row.close)) - float(row.low)
        if pd.notna(prior) and prior > 0 and float(row.volume) >= 2 * prior and span > 0 and wick >= span * .5:
            return True
    for col in ("open_interest", "oi"):
        if col in df and i > 0:
            recent = pd.to_numeric(df[col].iloc[max(0, i - 20):i], errors="coerce").dropna()
            value = pd.to_numeric(pd.Series([df[col].iloc[i]]), errors="coerce").iloc[0]
            if len(recent) and pd.notna(value) and value < float(recent.max()) * .98:
                return True
    for col in ("funding_rate", "funding"):
        if col in df:
            value = pd.to_numeric(pd.Series([df[col].iloc[i]]), errors="coerce").iloc[0]
            if pd.notna(value) and value < 0:
                return True
    if i >= 30:
        lows = pd.to_numeric(df.low, errors="coerce")
        rsi = _rsi(pd.to_numeric(df.close, errors="coerce"))
        prior = [j for j in range(max(2, i - 48), i - 2)
                 if lows.iloc[j] <= lows.iloc[j - 1] and lows.iloc[j] <= lows.iloc[j + 1]]
        if prior:
            j = min(prior, key=lambda k: lows.iloc[k])
            if lows.iloc[i] < lows.iloc[j] and pd.notna(rsi.iloc[i]) and pd.notna(rsi.iloc[j]) and rsi.iloc[i] > rsi.iloc[j]:
                return True
    return False


def detect_bounce_long(
    *, symbol: str, timeframe: str, exchange: str, df: pd.DataFrame,
    regime: str, market_regime: MarketRegimeSnapshot, settings: RiskSettings,
    resistances: list[Zone], four_hour_ema50: float | None = None,
    short_active: bool = False, as_of: datetime | None = None,
    btc_market_context: dict | None = None,
) -> TradeIdea | None:
    """Detect completed sweep/reclaim/pivot/break and stage a six-bar limit retest."""
    if not {"timestamp", "open", "high", "low", "close"}.issubset(df.columns):
        return None
    now = as_of or datetime.now(UTC)
    complete_mask = df.timestamp.map(lambda stamp: candle_is_completed(stamp, timeframe, as_of=now))
    candles = df.loc[complete_mask].copy(deep=True).reset_index(drop=True)
    candles = candles.assign(**{
        col: pd.to_numeric(candles[col], errors="coerce")
        for col in ("open", "high", "low", "close", "volume") if col in candles
    })
    candles = candles.dropna(subset=["timestamp", "open", "high", "low", "close"]).reset_index(drop=True)
    app_settings = get_settings()
    eligible_regimes = ELIGIBLE_REGIMES | ({"CHOP"} if app_settings.bounce_long_allow_chop else set())
    if not app_settings.bounce_long_enabled or regime not in eligible_regimes or short_active:
        return None
    if app_settings.bounce_long_market_filter:
        from app.strategy.bounce_market_filter import blocks_bounce_long
        if blocks_bounce_long(app_settings.bounce_long_market_filter_mode, btc_market_context):
            return None
    global _KILL_SWITCH_LOGGED
    try:
        from app.services.trade_history import bounce_long_kill_switch_tripped, has_active_short_signal
        if bounce_long_kill_switch_tripped():
            if not _KILL_SWITCH_LOGGED:
                logger.critical("BOUNCE_LONG disabled: 15 completed trades have negative expectancy")
                _KILL_SWITCH_LOGGED = True
            return None
        if has_active_short_signal(symbol, timeframe, exchange):
            return None
    except Exception:
        logger.exception("bounce_long safety-state lookup failed; suppressing candidate")
        return None
    if len(candles) < 60:
        return None
    atr = average_true_range(candles)
    if atr <= 0:
        return None
    rsi_now = _rsi(candles.close).iloc[-1]
    if pd.isna(rsi_now) or (not app_settings.bounce_long_skip_rsi_filter and float(rsi_now) > 70):
        return None
    price = float(candles.close.iloc[-1])
    # Recompute any overhead zones from completed bars so a forming candle
    # cannot influence the detector's levels.
    from app.strategy.support_resistance import find_support_resistance
    _, completed_resistances = find_support_resistance(candles)
    resistances = list(resistances) + list(completed_resistances)

    # Search backward for the latest valid structure. Swing is [i-48, i-10]
    # inclusive, and reclaim must close back above it within eight candles.
    for sweep_i in range(len(candles) - 1, 48, -1):
        swing = float(candles.low.iloc[sweep_i - 48:sweep_i - 9].min())
        sweep_low = float(candles.low.iloc[sweep_i])
        if sweep_low >= swing or not _exhaustion(candles, sweep_i):
            continue
        reclaim_i = next((j for j in range(sweep_i, min(sweep_i + 9, len(candles)))
                          if float(candles.close.iloc[j]) > swing), None)
        if reclaim_i is None:
            continue
        pivots = [j for j in range(reclaim_i + 1, len(candles) - 2)
                  if float(candles.low.iloc[j]) < float(candles.low.iloc[j - 1])
                  and float(candles.low.iloc[j]) < float(candles.low.iloc[j - 2])
                  and float(candles.low.iloc[j]) < float(candles.low.iloc[j + 1])
                  and float(candles.low.iloc[j]) < float(candles.low.iloc[j + 2])
                  and float(candles.low.iloc[j]) > sweep_low
                  and float(candles.low.iloc[j]) >= swing - .25 * atr]
        for pivot_i in reversed(pivots):
            bounce_high = float(candles.high.iloc[reclaim_i:pivot_i + 1].max())
            confirmation_i = next((j for j in range(pivot_i + 1, len(candles))
                                   if float(candles.close.iloc[j]) > bounce_high), None)
            if confirmation_i is None or len(candles) - 1 - confirmation_i > 6:
                continue
            if not any(_exhaustion(candles, sweep_i) for _ in (0,)):
                continue
            higher_low = float(candles.low.iloc[pivot_i])
            entry = (higher_low + bounce_high) / 2
            stop = higher_low - .3 * atr
            risk = entry - stop
            if risk <= 0 or risk > 2.5 * atr:
                continue
            overhead = [float(z.lower) for z in resistances if float(z.lower) > entry]
            if four_hour_ema50 is not None and four_hour_ema50 > entry:
                overhead.append(float(four_hour_ema50))
            if not overhead:
                continue
            viable_targets = sorted(t for t in overhead if t > entry and (t - entry) / risk >= 1.5)
            if not viable_targets:
                continue
            target = viable_targets[0]
            rr = (target - entry) / risk
            if (price - swing) / max(target - swing, 1e-9) > .60:
                continue
            retest_i = next((j for j in range(confirmation_i + 1, len(candles))
                             if float(candles.low.iloc[j]) <= entry <= float(candles.high.iloc[j])
                             and float(candles.close.iloc[j]) >= entry), None)
            entry_status = "READY" if retest_i is not None else "WAIT_FOR_RETEST"
            signal_i = retest_i if retest_i is not None else confirmation_i
            risk_amount = settings.account_size * settings.risk_per_trade_pct / 100
            signal_time = candles.timestamp.iloc[signal_i]
            idea = TradeIdea(
                symbol=symbol, timeframe=timeframe, exchange=exchange, source=exchange,
                direction="Long", setup_family="bounce_long", strategy_version="v1",
                market_regime=regime, higher_timeframe_bias="HTF_BEARISH",
                setup_grade="Bounce Long", setup_score=85.0,
                entry_zone=(round(entry, 6), round(entry, 6)), stop_loss=round(stop, 6),
                take_profit_1=round(target, 6), take_profit_2=round(target, 6),
                risk_reward_ratio=round(rr, 2), reason=(
                    f"Price swept prior support at {swing:g} and printed a low of {sweep_low:g}, "
                    f"then reclaimed {swing:g} with a close at {float(candles.close.iloc[reclaim_i]):g}. "
                    f"After the reclaim, price held a higher low at {higher_low:g} and closed above the "
                    f"{bounce_high:g} bounce high, suggesting buyers have taken short-term control. "
                    f"Stop anchor swing level: {higher_low:g}. "
                    f"Target basis: {'4H EMA50' if four_hour_ema50 is not None and abs(target - four_hour_ema50) < 1e-9 else 'resistance'}."
                ), confidence_score=85.0,
                invalid_condition=f"Invalid if a {timeframe.upper()} candle closes below {stop:.6g}.",
                rank_score=85.0, position_size_units=round(risk_amount / risk, 6),
                risk_amount=round(risk_amount, 2), regime_score=market_regime.score,
                regime_label=market_regime.label, regime_type=market_regime.regime_type,
                regime_confidence_score=market_regime.confidence_score,
                regime_structure=market_regime.structure, regime_trade_decision=market_regime.trade_decision,
                is_regime_transition=market_regime.is_transition, regime_bias=market_regime.bias,
                regime_updated_at=market_regime.updated_at, trend_alignment="counter-trend",
                regime_explanation=market_regime.explanation, entry_status=entry_status,
                reversal_confirmations=["higher low pivot", "breakout above bounce high", "closed above resistance at bounce high"],
                signal_candle_time=signal_time,
                signal_candle_high=float(candles.high.iloc[signal_i]),
                signal_candle_low=float(candles.low.iloc[signal_i]),
                signal_candle_close=float(candles.close.iloc[signal_i]),
            )
            evaluate_strategy_decision(idea)
            return idea
    return None
