from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from typing import Iterable

import pandas as pd

from app.config import get_settings
from app.models.schemas import SignalReview, TradeIdea
from app.services.alert_dedupe import setup_fingerprint
from app.services.market_data import get_candles_cached
from app.utils.database import get_connection
from app.utils.opportunities import canonical_candle_time, canonical_opportunity_key, setup_family_from_regime


TIMEFRAME_MINUTES = {
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
}

logger = logging.getLogger(__name__)

CRYPTO_QUOTE_SUFFIXES = ("USDT", "USDC", "SUSD", "USD", "PERP")


def canonical_crypto_symbol(symbol: str) -> str:
    normalized = "".join(character for character in str(symbol or "").upper() if character.isalnum())
    for suffix in CRYPTO_QUOTE_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return f"{normalized}USDT" if normalized else ""


def _active_trade_signal_id(row: dict) -> str:
    opportunity_key = str(row.get("opportunity_key") or "").strip()
    if opportunity_key:
        digest = hashlib.sha256(opportunity_key.encode("utf-8")).hexdigest()[:20]
        return f"swiftchart-{digest}"
    return f"trade-idea-{row['id']}"


def _release_stale_active_trades(connection, *, now_iso: str | None = None) -> int:
    """Repair rows whose outcome/expiry already makes them non-locking."""

    current = now_iso or _now_iso()
    cursor = connection.execute(
        """
        UPDATE trade_ideas
        SET lifecycle_status = CASE
            WHEN sl_hit_at IS NOT NULL
              OR tp2_hit_at IS NOT NULL
              OR status IN (
                  'TP2_HIT', 'SL_HIT', 'AMBIGUOUS', 'BREAK_EVEN',
                  'CLOSED_AFTER_TP1', 'PARTIAL_LOSS'
              )
              THEN 'closed'
            WHEN tp1_hit_at IS NOT NULL OR status = 'TP1_HIT'
              THEN 'tp1_hit'
            WHEN expired_at IS NOT NULL
              OR status IN ('EXPIRED', 'PARTIAL_WIN')
              OR (expires_at IS NOT NULL AND datetime(expires_at) <= datetime(?))
              THEN 'expired'
            WHEN closed_at IS NOT NULL
              THEN 'closed'
            ELSE lifecycle_status
        END
        WHERE lifecycle_status = 'active'
          AND (
              tp1_hit_at IS NOT NULL
              OR tp2_hit_at IS NOT NULL
              OR sl_hit_at IS NOT NULL
              OR expired_at IS NOT NULL
              OR closed_at IS NOT NULL
              OR status IN (
                  'TP1_HIT', 'TP2_HIT', 'SL_HIT', 'EXPIRED', 'AMBIGUOUS',
                  'BREAK_EVEN', 'CLOSED_AFTER_TP1', 'PARTIAL_WIN', 'PARTIAL_LOSS'
              )
              OR (expires_at IS NOT NULL AND datetime(expires_at) <= datetime(?))
          )
        """,
        (current, current),
    )
    return int(cursor.rowcount or 0)


def _find_active_crypto_trade(connection, symbol: str, *, exclude_trade_id: int | None = None) -> dict | None:
    canonical_symbol = canonical_crypto_symbol(symbol)
    if not canonical_symbol:
        return None
    now_iso = _now_iso()
    _release_stale_active_trades(connection, now_iso=now_iso)
    rows = connection.execute(
        """
        SELECT * FROM trade_ideas
        WHERE strategy_decision = 'TRADE'
          AND entry_status = 'READY'
          AND lifecycle_status = 'active'
          AND executable_at IS NOT NULL
          AND tp1_hit_at IS NULL
          AND sl_hit_at IS NULL
          AND closed_at IS NULL
          AND (expires_at IS NULL OR datetime(expires_at) > datetime(?))
          AND COALESCE(status, 'PENDING') NOT IN (
              'TP1_HIT', 'TP2_HIT', 'SL_HIT', 'EXPIRED', 'AMBIGUOUS',
              'BREAK_EVEN', 'CLOSED_AFTER_TP1', 'PARTIAL_WIN', 'PARTIAL_LOSS'
          )
        ORDER BY executable_at ASC, id ASC
        """,
        (now_iso,),
    ).fetchall()
    for row in rows:
        active = dict(row)
        if exclude_trade_id is not None and int(active["id"]) == int(exclude_trade_id):
            continue
        if canonical_crypto_symbol(active["symbol"]) == canonical_symbol:
            return active
    return None


def active_crypto_trade_for_symbol(symbol: str) -> dict | None:
    with get_connection() as connection:
        return _find_active_crypto_trade(connection, symbol)


def active_trade_skip_diagnostic(symbol: str, existing: dict, *, opportunity_key: str | None = None) -> dict:
    canonical_symbol = canonical_crypto_symbol(symbol)
    signal_id = _active_trade_signal_id(existing)
    return {
        "decision": "SKIPPED_ACTIVE_TRADE",
        "symbol": canonical_symbol,
        "existing_trade_id": int(existing["id"]),
        "existing_signal_id": signal_id,
        "skipped_opportunity_key": opportunity_key,
        "reason": (
            f"Skipped {canonical_symbol}: active TRADE {signal_id} has reached neither TP1 nor SL."
        ),
    }


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sqlite_cutoff(minutes: int = 30) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _timeframe_minutes(timeframe: str) -> int:
    return TIMEFRAME_MINUTES.get(str(timeframe).lower(), 240)


def _market_structure_changed_after_sl(row: dict, idea: TradeIdea) -> bool:
    previous_score = row.get("regime_score")
    current_score = idea.regime_score
    if previous_score is not None and current_score is not None and abs(float(current_score) - float(previous_score)) >= 30:
        return True
    if row.get("regime_label") and idea.regime_label and str(row["regime_label"]) != str(idea.regime_label):
        return True
    if idea.trend_alignment == "with-trend" and row.get("trend_alignment") != "with-trend":
        return True
    if (idea.setup_score or idea.confidence_score) >= 88 and len(idea.reversal_confirmations) >= 3:
        return True
    return False


def same_direction_sl_cooldown_review(idea: TradeIdea, *, candles: int = 6, now: datetime | None = None) -> SignalReview | None:
    signal_time = idea.signal_candle_time or now or datetime.now(UTC)
    signal_time = signal_time if signal_time.tzinfo else signal_time.replace(tzinfo=UTC)
    cutoff = signal_time - timedelta(minutes=_timeframe_minutes(idea.timeframe) * candles)
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT * FROM trade_ideas
            WHERE symbol = ?
              AND timeframe = ?
              AND exchange = ?
              AND direction = ?
              AND (status = 'SL_HIT' OR sl_hit_at IS NOT NULL)
              AND COALESCE(sl_hit_at, closed_at, outcome_checked_at, created_at) >= ?
            ORDER BY COALESCE(sl_hit_at, closed_at, outcome_checked_at, created_at) DESC, id DESC
            LIMIT 1
            """,
            (
                idea.symbol.upper(),
                idea.timeframe,
                idea.exchange,
                idea.direction.upper(),
                cutoff.isoformat(),
            ),
        ).fetchone()
    if row is None:
        return None
    previous = dict(row)
    if _market_structure_changed_after_sl(previous, idea):
        logger.info(
            "SL cooldown bypassed pair=%s direction=%s bias=%s confidence=%.1f reason=market_structure_changed previous_regime=%s current_regime=%s",
            idea.symbol,
            idea.direction,
            idea.regime_bias,
            idea.confidence_score,
            previous.get("regime_label"),
            idea.regime_label,
        )
        return None
    reason = (
        f"{idea.direction} signal rejected because {idea.symbol.upper()} hit SL on the same direction "
        f"within the last {candles} {idea.timeframe} candles and market structure has not changed."
    )
    logger.info(
        "Signal rejected pair=%s direction=%s bias=%s btc_regime=%s confidence=%.1f rejection_reason=%s",
        idea.symbol,
        idea.direction,
        idea.regime_bias or "-",
        "n/a",
        idea.confidence_score,
        reason,
    )
    return SignalReview(
        symbol=idea.symbol,
        timeframe=idea.timeframe,
        exchange=idea.exchange,
        direction=idea.direction,
        accepted=False,
        reason=reason,
        base_score=idea.setup_score,
        adjusted_score=max(0.0, (idea.setup_score or idea.confidence_score) - 60),
        confidence_adjustment=-60,
        regime_score=idea.regime_score or 0,
        regime_label=idea.regime_label or "Unknown",
        trend_alignment=idea.trend_alignment or "range-trade",
        reversal_confirmations=idea.reversal_confirmations,
    )


def _signal_expires_at(idea: TradeIdea) -> str:
    candle_time = idea.signal_candle_time
    if candle_time is None:
        return (datetime.now(UTC) + timedelta(minutes=get_settings().signal_max_age_minutes)).replace(microsecond=0).isoformat()
    normalized = candle_time if candle_time.tzinfo else candle_time.replace(tzinfo=UTC)
    return (normalized + timedelta(minutes=get_settings().signal_max_age_minutes)).replace(microsecond=0).isoformat()


def _recent_duplicate_id(connection, idea: TradeIdea) -> int | None:
    row = connection.execute(
        """
        SELECT id FROM trade_ideas
        WHERE symbol = ?
          AND timeframe = ?
          AND exchange = ?
          AND direction = ?
          AND ABS(entry_zone_low - ?) < 0.00000001
          AND ABS(entry_zone_high - ?) < 0.00000001
          AND ABS(stop_loss - ?) < 0.00000001
          AND ABS(take_profit_1 - ?) < 0.00000001
          AND ABS(take_profit_2 - ?) < 0.00000001
          AND created_at >= ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (
            idea.symbol.upper(),
            idea.timeframe,
            idea.exchange,
            idea.direction.upper(),
            idea.entry_zone[0],
            idea.entry_zone[1],
            idea.stop_loss,
            idea.take_profit_1,
            idea.take_profit_2,
            _sqlite_cutoff(30),
        ),
    ).fetchone()
    return int(row["id"]) if row else None


def _prepare_opportunity_identity(idea: TradeIdea) -> tuple[str | None, str | None]:
    family = idea.setup_family or setup_family_from_regime(str(idea.market_regime or idea.regime_type or ""))
    key = idea.opportunity_key or canonical_opportunity_key(
        exchange=idea.exchange,
        symbol=idea.symbol,
        timeframe=idea.timeframe,
        direction=idea.direction,
        setup_family=family,
        strategy_version=idea.strategy_version,
        signal_candle_time=idea.signal_candle_time,
    )
    idea.setup_family = family
    idea.opportunity_key = key
    return family, key


def _is_protected_history(row: dict) -> bool:
    return bool(
        row.get("entry_triggered_at")
        or row.get("closed_at")
        or row.get("pnl_r_multiple") is not None
        or row.get("result") not in {None, "OPEN"}
        or row.get("status") not in {None, "PENDING", "OPEN"}
    )


def _confirmation_keywords(direction: str) -> tuple[str, ...]:
    if direction.upper() == "LONG":
        return ("rejection at support", "liquidity sweep", "closed above resistance", "bullish market structure break")
    return ("rejection at resistance", "liquidity sweep", "closed below support", "bearish market structure break")


def _valid_retest_promotion(existing: dict, idea: TradeIdea) -> bool:
    original_candle = canonical_candle_time(existing.get("signal_candle_time"))
    confirmation_candle = canonical_candle_time(idea.signal_candle_time)
    if idea.entry_status != "READY" or not original_candle or not confirmation_candle or confirmation_candle <= original_candle:
        return False
    if idea.signal_candle_high is None or idea.signal_candle_low is None or idea.signal_candle_close is None:
        return False
    entry_low = float(existing["entry_zone_low"])
    entry_high = float(existing["entry_zone_high"])
    touched_original_entry = float(idea.signal_candle_high) >= entry_low and float(idea.signal_candle_low) <= entry_high
    entry_mid = (entry_low + entry_high) / 2
    directional_close = float(idea.signal_candle_close) >= entry_mid if idea.direction.upper() == "LONG" else float(idea.signal_candle_close) <= entry_mid
    confirmation_text = " ".join(idea.reversal_confirmations).lower()
    price_action_confirmation = any(keyword in confirmation_text for keyword in _confirmation_keywords(idea.direction))
    return touched_original_entry and directional_close and price_action_confirmation


def _find_pending_retest(connection, idea: TradeIdea, family: str) -> dict | None:
    row = connection.execute(
        """
        SELECT * FROM trade_ideas
        WHERE exchange = ? AND symbol = ? AND timeframe = ? AND direction = ?
          AND setup_family = ? AND entry_status = 'WAIT_FOR_RETEST'
          AND COALESCE(strategy_version, '') = COALESCE(?, '')
          AND (strategy_version IS NULL OR strategy_decision = 'WAIT_FOR_RETEST')
          AND result = 'OPEN' AND entry_triggered_at IS NULL AND closed_at IS NULL
          AND status IN ('PENDING', 'OPEN')
          AND (expires_at IS NULL OR expires_at >= ?)
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (idea.exchange, idea.symbol.upper(), idea.timeframe, idea.direction.upper(), family, idea.strategy_version, _now_iso()),
    ).fetchone()
    return dict(row) if row else None


def _find_promoted_confirmation(connection, idea: TradeIdea, family: str) -> dict | None:
    confirmation_candle = canonical_candle_time(idea.signal_candle_time)
    if confirmation_candle is None:
        return None
    row = connection.execute(
        """
        SELECT * FROM trade_ideas
        WHERE exchange = ? AND symbol = ? AND timeframe = ? AND direction = ?
          AND setup_family = ? AND entry_status = 'READY' AND retest_confirmed_at = ?
          AND COALESCE(strategy_version, '') = COALESCE(?, '')
          AND result = 'OPEN' AND entry_triggered_at IS NULL AND closed_at IS NULL
          AND status IN ('PENDING', 'OPEN')
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (idea.exchange, idea.symbol.upper(), idea.timeframe, idea.direction.upper(), family, confirmation_candle, idea.strategy_version),
    ).fetchone()
    return dict(row) if row else None


def _update_opportunity(
    connection,
    existing: dict,
    idea: TradeIdea,
    *,
    entry_status: str,
    retest_confirmed_at: str | None = None,
) -> int:
    actionable = entry_status == "READY" and (idea.strategy_version is None or idea.strategy_decision == "TRADE")
    executable_at = _now_iso() if actionable else None
    if idea.strategy_decision == "SHADOW":
        lifecycle_status = "shadow"
    elif idea.strategy_decision == "NO_TRADE":
        lifecycle_status = "no_trade"
    elif entry_status == "WAIT_FOR_RETEST":
        lifecycle_status = "pending_retest"
    else:
        lifecycle_status = "active"
    connection.execute(
        """
        UPDATE trade_ideas
        SET market_regime = ?, higher_timeframe_bias = ?, setup_score = ?, setup_grade = ?,
            entry_zone_low = ?, entry_zone_high = ?, stop_loss = ?, take_profit_1 = ?,
            take_profit_2 = ?, risk_reward = ?, confidence = ?, reason = ?, invalidation = ?,
            regime_score = ?, regime_label = ?, trend_alignment = ?, regime_confidence_adjustment = ?,
            reversal_confirmations = ?, regime_explanation = ?, signal_fingerprint = ?,
            entry_status = ?, setup_family = ?, lifecycle_status = ?, expires_at = ?,
            strategy_version = ?, edge_status = ?, strategy_decision = ?, v2_decision_reason = ?,
            regime_confidence = ?, entry_quality_status = ?, entry_quality_score = ?,
            entry_quality_reason = ?, outcome_tracking_mode = ?, v2_evaluated_at = ?,
            retest_confirmed_at = COALESCE(?, retest_confirmed_at),
            executable_at = ?,
            production_rule_accepted = ?, strict_trend_short_eligible = ?,
            strict_trigger_type = ?, strict_confirmation_type = ?,
            strict_trigger_candle_time = ?, strict_trigger_candle_completed = ?
        WHERE id = ?
        """,
        (
            idea.market_regime,
            idea.higher_timeframe_bias,
            idea.setup_score,
            idea.setup_grade,
            idea.entry_zone[0],
            idea.entry_zone[1],
            idea.stop_loss,
            idea.take_profit_1,
            idea.take_profit_2,
            idea.risk_reward_ratio,
            idea.confidence_score,
            idea.reason,
            idea.invalid_condition,
            idea.regime_score,
            idea.regime_label,
            idea.trend_alignment,
            idea.regime_confidence_adjustment,
            json.dumps(idea.reversal_confirmations),
            idea.regime_explanation,
            setup_fingerprint(idea),
            entry_status,
            idea.setup_family,
            lifecycle_status,
            _signal_expires_at(idea),
            idea.strategy_version,
            idea.edge_status,
            idea.strategy_decision,
            idea.v2_decision_reason,
            idea.regime_confidence,
            idea.entry_quality_status,
            idea.entry_quality_score,
            idea.entry_quality_reason,
            idea.outcome_tracking_mode,
            canonical_candle_time(idea.v2_evaluated_at),
            retest_confirmed_at,
            executable_at,
            int(idea.production_rule_accepted) if idea.production_rule_accepted is not None else None,
            int(idea.strict_trend_short_eligible) if idea.strict_trend_short_eligible is not None else None,
            idea.strict_trigger_type,
            idea.strict_confirmation_type,
            canonical_candle_time(idea.strict_trigger_candle_time),
            int(idea.strict_trigger_candle_completed) if idea.strict_trigger_candle_completed is not None else None,
            existing["id"],
        ),
    )
    return int(existing["id"])


def _adopt_existing_identity(idea: TradeIdea, existing: dict) -> None:
    idea.opportunity_key = existing.get("opportunity_key") or idea.opportunity_key
    idea.setup_family = existing.get("setup_family") or idea.setup_family
    if existing.get("signal_candle_time"):
        idea.signal_candle_time = _parse_dt(existing["signal_candle_time"])


def save_trade_ideas(ideas: Iterable[TradeIdea], *, diagnostics: list[dict] | None = None) -> list[int]:
    ids: list[int] = []
    with get_connection() as connection:
        # Serialize the active-symbol recheck with activation. This closes the
        # gap between scanner preflight and persistence across service workers.
        connection.execute("BEGIN IMMEDIATE")
        for idea in ideas:
            try:
                family, opportunity_key = _prepare_opportunity_identity(idea)
                existing = None
                if opportunity_key:
                    row = connection.execute("SELECT * FROM trade_ideas WHERE opportunity_key = ?", (opportunity_key,)).fetchone()
                    if row is not None:
                        existing = dict(row)

                activates_trade = idea.entry_status == "READY" and idea.strategy_decision == "TRADE"
                if activates_trade:
                    active_trade = _find_active_crypto_trade(
                        connection,
                        idea.symbol,
                        exclude_trade_id=int(existing["id"]) if existing is not None else None,
                    )
                    if active_trade is not None:
                        diagnostic = active_trade_skip_diagnostic(
                            idea.symbol,
                            active_trade,
                            opportunity_key=opportunity_key,
                        )
                        if diagnostics is not None:
                            diagnostics.append(diagnostic)
                        logger.info(
                            "crypto_scan_decision=%s symbol=%s existing_trade_id=%s existing_signal_id=%s reason=%s",
                            diagnostic["decision"],
                            diagnostic["symbol"],
                            diagnostic["existing_trade_id"],
                            diagnostic["existing_signal_id"],
                            diagnostic["reason"],
                        )
                        continue

                if existing is not None:
                    if _is_protected_history(existing):
                        logger.info(
                            "opportunity_skipped reason=protected_history opportunity_key=%s id=%s status=%s result=%s",
                            opportunity_key,
                            existing["id"],
                            existing.get("status"),
                            existing.get("result"),
                        )
                        continue
                    if existing.get("entry_status") == "WAIT_FOR_RETEST" and idea.entry_status == "READY":
                        idea.entry_status = "WAIT_FOR_RETEST"
                        if idea.strategy_version is not None:
                            idea.strategy_decision = "WAIT_FOR_RETEST"
                            idea.outcome_tracking_mode = "NONE"
                        _adopt_existing_identity(idea, existing)
                        logger.info(
                            "opportunity_skipped reason=same_candle_is_not_retest opportunity_key=%s id=%s",
                            opportunity_key,
                            existing["id"],
                        )
                        continue
                    trade_id = _update_opportunity(connection, existing, idea, entry_status=idea.entry_status)
                    if trade_id not in ids:
                        ids.append(trade_id)
                    logger.info(
                        "opportunity_updated opportunity_key=%s id=%s entry_status=%s",
                        opportunity_key,
                        trade_id,
                        idea.entry_status,
                    )
                    continue

                can_promote = idea.strategy_version is None or idea.strategy_decision in {"TRADE", "SHADOW"}
                if family and idea.entry_status == "READY" and can_promote:
                    promoted = _find_promoted_confirmation(connection, idea, family)
                    if promoted is not None:
                        confirmation_candle = canonical_candle_time(idea.signal_candle_time)
                        trade_id = _update_opportunity(
                            connection,
                            promoted,
                            idea,
                            entry_status="READY",
                            retest_confirmed_at=confirmation_candle,
                        )
                        _adopt_existing_identity(idea, promoted)
                        idea.retest_confirmed_at = _parse_dt(promoted["retest_confirmed_at"])
                        idea.executable_at = _parse_dt(promoted["executable_at"])
                        if trade_id not in ids:
                            ids.append(trade_id)
                        logger.info(
                            "opportunity_updated reason=confirmation_candle_rescan opportunity_key=%s id=%s",
                            promoted.get("opportunity_key"),
                            trade_id,
                        )
                        continue
                    pending = _find_pending_retest(connection, idea, family)
                    if pending is not None:
                        if not _valid_retest_promotion(pending, idea):
                            idea.entry_status = "WAIT_FOR_RETEST"
                            _adopt_existing_identity(idea, pending)
                            logger.info(
                                "opportunity_skipped reason=pending_retest_not_confirmed opportunity_key=%s pending_id=%s confirmation_candle=%s",
                                pending.get("opportunity_key"),
                                pending["id"],
                                canonical_candle_time(idea.signal_candle_time),
                            )
                            continue
                        confirmed_at = canonical_candle_time(idea.signal_candle_time)
                        original_confirmation_time = idea.signal_candle_time
                        trade_id = _update_opportunity(
                            connection,
                            pending,
                            idea,
                            entry_status="READY",
                            retest_confirmed_at=confirmed_at,
                        )
                        if trade_id not in ids:
                            ids.append(trade_id)
                        _adopt_existing_identity(idea, pending)
                        idea.retest_confirmed_at = original_confirmation_time
                        idea.executable_at = datetime.now(UTC).replace(microsecond=0) if idea.strategy_decision in {None, "TRADE"} else None
                        logger.info(
                            "opportunity_updated reason=retest_promoted opportunity_key=%s id=%s retest_confirmed_at=%s",
                            pending.get("opportunity_key"),
                            trade_id,
                            confirmed_at,
                        )
                        continue

                duplicate_id = None if opportunity_key else _recent_duplicate_id(connection, idea)
                if duplicate_id is not None:
                    logger.info("opportunity_skipped reason=legacy_price_duplicate id=%s symbol=%s timeframe=%s exchange=%s", duplicate_id, idea.symbol, idea.timeframe, idea.exchange)
                    continue
                cursor = connection.execute(
                    """
                    INSERT INTO trade_ideas (
                        symbol, timeframe, exchange, direction, market_regime,
                        higher_timeframe_bias, setup_score, setup_grade,
                        entry_zone_low, entry_zone_high, stop_loss, take_profit_1,
                        take_profit_2, risk_reward, confidence, reason, invalidation,
                        regime_score, regime_label, trend_alignment, regime_confidence_adjustment,
                        reversal_confirmations, regime_explanation, signal_candle_time,
                        signal_fingerprint, lifecycle_status, expires_at, entry_status,
                        setup_family, opportunity_key, retest_confirmed_at, executable_at,
                        strategy_version, edge_status, strategy_decision, v2_decision_reason,
                        regime_confidence, entry_quality_status, entry_quality_score,
                        entry_quality_reason, outcome_tracking_mode, v2_evaluated_at,
                        production_rule_accepted, strict_trend_short_eligible,
                        strict_trigger_type, strict_confirmation_type,
                        strict_trigger_candle_time, strict_trigger_candle_completed
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        idea.symbol.upper(),
                        idea.timeframe,
                        idea.exchange,
                        idea.direction.upper(),
                        idea.market_regime,
                        idea.higher_timeframe_bias,
                        idea.setup_score,
                        idea.setup_grade,
                        idea.entry_zone[0],
                        idea.entry_zone[1],
                        idea.stop_loss,
                        idea.take_profit_1,
                        idea.take_profit_2,
                        idea.risk_reward_ratio,
                        idea.confidence_score,
                        idea.reason,
                        idea.invalid_condition,
                        idea.regime_score,
                        idea.regime_label,
                        idea.trend_alignment,
                        idea.regime_confidence_adjustment,
                        json.dumps(idea.reversal_confirmations),
                        idea.regime_explanation,
                        idea.signal_candle_time.isoformat() if idea.signal_candle_time else None,
                        setup_fingerprint(idea),
                        (
                            "shadow"
                            if idea.strategy_decision == "SHADOW"
                            else "no_trade"
                            if idea.strategy_decision == "NO_TRADE"
                            else "active"
                            if idea.entry_status == "READY"
                            else "pending_retest"
                        ),
                        _signal_expires_at(idea),
                        idea.entry_status,
                        family,
                        opportunity_key,
                        canonical_candle_time(idea.retest_confirmed_at),
                        canonical_candle_time(idea.executable_at)
                        or (_now_iso() if idea.entry_status == "READY" and idea.strategy_decision in {None, "TRADE"} else None),
                        idea.strategy_version,
                        idea.edge_status,
                        idea.strategy_decision,
                        idea.v2_decision_reason,
                        idea.regime_confidence,
                        idea.entry_quality_status,
                        idea.entry_quality_score,
                        idea.entry_quality_reason,
                        idea.outcome_tracking_mode,
                        canonical_candle_time(idea.v2_evaluated_at),
                        int(idea.production_rule_accepted) if idea.production_rule_accepted is not None else None,
                        int(idea.strict_trend_short_eligible) if idea.strict_trend_short_eligible is not None else None,
                        idea.strict_trigger_type,
                        idea.strict_confirmation_type,
                        canonical_candle_time(idea.strict_trigger_candle_time),
                        int(idea.strict_trigger_candle_completed) if idea.strict_trigger_candle_completed is not None else None,
                    ),
                )
                trade_id = int(cursor.lastrowid)
                if idea.entry_status == "READY" and idea.strategy_decision in {None, "TRADE"} and idea.executable_at is None:
                    idea.executable_at = datetime.now(UTC).replace(microsecond=0)
                connection.execute(
                    """
                    INSERT INTO signal_reviews (
                        symbol, timeframe, exchange, direction, accepted, reason,
                        base_score, adjusted_score, confidence_adjustment,
                        regime_score, regime_label, trend_alignment, reversal_confirmations
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        idea.symbol.upper(),
                        idea.timeframe,
                        idea.exchange,
                        idea.direction.upper(),
                        0 if idea.strategy_decision == "NO_TRADE" else 1,
                        idea.v2_decision_reason or "Signal accepted and saved as a trade idea.",
                        None,
                        idea.confidence_score,
                        idea.regime_confidence_adjustment,
                        idea.regime_score or 0,
                        idea.regime_label or "Ranging / Neutral",
                        idea.trend_alignment or "range-trade",
                        json.dumps(idea.reversal_confirmations),
                    ),
                )
                ids.append(trade_id)
                logger.info(
                    "opportunity_new opportunity_key=%s id=%s symbol=%s timeframe=%s exchange=%s direction=%s setup_family=%s entry_status=%s",
                    opportunity_key,
                    trade_id,
                    idea.symbol,
                    idea.timeframe,
                    idea.exchange,
                    idea.direction,
                    family,
                    idea.entry_status,
                )
            except Exception:
                logger.exception("Failed to save trade idea symbol=%s timeframe=%s exchange=%s", idea.symbol, idea.timeframe, idea.exchange)
    return ids


def save_signal_reviews(reviews: Iterable[SignalReview]) -> int:
    count = 0
    with get_connection() as connection:
        for review in reviews:
            try:
                connection.execute(
                    """
                    INSERT INTO signal_reviews (
                        symbol, timeframe, exchange, direction, accepted, reason,
                        base_score, adjusted_score, confidence_adjustment,
                        regime_score, regime_label, trend_alignment, reversal_confirmations
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review.symbol.upper(),
                        review.timeframe,
                        review.exchange,
                        review.direction.upper(),
                        1 if review.accepted else 0,
                        review.reason,
                        review.base_score,
                        review.adjusted_score,
                        review.confidence_adjustment,
                        review.regime_score,
                        review.regime_label,
                        review.trend_alignment,
                        json.dumps(review.reversal_confirmations),
                    ),
                )
                count += 1
            except Exception:
                logger.exception("Failed to save signal review symbol=%s timeframe=%s exchange=%s", review.symbol, review.timeframe, review.exchange)
    return count


def row_to_dict(row) -> dict:
    data = dict(row)
    for key in ("created_at", "expires_at", "outcome_checked_at", "entry_triggered_at", "tp1_hit_at", "tp2_hit_at", "sl_hit_at", "expired_at", "closed_at", "signal_candle_time", "retest_confirmed_at", "executable_at", "strict_trigger_candle_time"):
        if data.get(key) and isinstance(data[key], str):
            data[key] = _parse_dt(data[key])
    return data


def _history_where(filters: dict) -> tuple[str, list]:
    clauses = []
    values = []
    for field in ("symbol", "timeframe", "exchange", "status", "result"):
        if filters.get(field):
            clauses.append(f"{field} = ?")
            values.append(str(filters[field]).upper() if field in {"symbol", "status", "result"} else str(filters[field]).lower() if field == "exchange" else filters[field])
    if filters.get("direction"):
        clauses.append("direction = ?")
        values.append(str(filters["direction"]).upper())
    if filters.get("date_from"):
        clauses.append("created_at >= ?")
        values.append(filters["date_from"])
    if filters.get("date_to"):
        clauses.append("created_at <= ?")
        date_to = str(filters["date_to"])
        values.append(f"{date_to} 23:59:59" if len(date_to) == 10 else date_to)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, values


def query_trade_history(filters: dict, page: int = 1, limit: int = 20, sort: str = "desc") -> dict:
    page = max(1, int(page or 1))
    limit = min(250, max(1, int(limit or 20)))
    direction = "ASC" if str(sort).lower() == "asc" else "DESC"
    where, values = _history_where(filters)
    offset = (page - 1) * limit
    with get_connection() as connection:
        total = int(connection.execute(f"SELECT COUNT(*) AS count FROM trade_ideas {where}", values).fetchone()["count"])
        rows = connection.execute(
            f"SELECT * FROM trade_ideas {where} ORDER BY created_at {direction}, id {direction} LIMIT ? OFFSET ?",
            [*values, limit, offset],
        ).fetchall()
        rows = _public_tracking_rows(connection, rows)
    if total == 0:
        logger.info("No trade history records found for filters=%s", {key: value for key, value in filters.items() if value})
    return {
        "records": [row_to_dict(row) for row in rows],
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit if total else 0,
        "sort": "asc" if direction == "ASC" else "desc",
    }



def list_trade_history(filters: dict, page: int = 1, limit: int = 20, sort: str = "desc") -> list[dict]:
    return query_trade_history(filters, page=page, limit=limit, sort=sort)["records"]


def get_trade_history(trade_id: int) -> dict | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM trade_ideas WHERE id = ?", (trade_id,)).fetchone()
        row = _public_tracking_rows(connection, [row])[0] if row else None
    return row_to_dict(row) if row else None



def _hit_checks(row: dict, candle: pd.Series) -> tuple[bool, bool, bool]:
    high = float(candle["high"])
    low = float(candle["low"])
    if row["direction"] == "LONG":
        tp1_hit = high >= float(row["take_profit_1"])
        tp2_hit = high >= float(row["take_profit_2"])
        sl_hit = low <= float(row["stop_loss"])
    else:
        tp1_hit = low <= float(row["take_profit_1"])
        tp2_hit = low <= float(row["take_profit_2"])
        sl_hit = high >= float(row["stop_loss"])
    return tp1_hit, tp2_hit, sl_hit


def _entry_triggered(row: dict, candle: pd.Series) -> bool:
    high = float(candle["high"])
    low = float(candle["low"])
    return low <= _entry_price(row) <= high



def _expiry_time(row: dict) -> datetime:
    bars = int(getattr(get_settings(), "trade_history_expiry_bars", 12))
    minutes = TIMEFRAME_MINUTES.get(str(row["timeframe"]).lower(), 240)
    return _parse_dt(row["created_at"]) + timedelta(minutes=minutes * bars)


def _entry_price(row: dict) -> float:
    from app.services.tracking_rules import zone_bounds
    low, high = zone_bounds(row["entry_zone_low"], row["entry_zone_high"])
    return (low + high) / 2



def _tp1_r(row: dict) -> float:
    risk = abs(_entry_price(row) - float(row["stop_loss"]))
    if risk <= 0:
        return 0.0
    return abs(float(row["take_profit_1"]) - _entry_price(row)) / risk


def _partial_r(row: dict, runner_r: float) -> float:
    return (_tp1_r(row) * 0.5) + (runner_r * 0.5)


def _result_for_r(value: float) -> str:
    if value > 0:
        return "PARTIAL_WIN"
    if value < 0:
        return "PARTIAL_LOSS"
    return "BREAK_EVEN"


def _r_result(row: dict, status: str) -> tuple[str, float | None]:
    if status == "TP2_HIT":
        risk = abs(_entry_price(row) - float(row["stop_loss"]))
        if risk <= 0:
            return "UNRESOLVED", None
        return "WIN", _partial_r(row, abs(float(row["take_profit_2"]) - _entry_price(row)) / risk)
    if status == "BREAK_EVEN":
        return "BREAK_EVEN", round(_partial_r(row, 0.0), 4)
    if status == "CLOSED_AFTER_TP1":
        pnl = round(_partial_r(row, -1.0), 4)
        return _result_for_r(pnl), pnl
    if status == "PARTIAL_WIN":
        return "UNRESOLVED", None  # Expiry is not an execution of the runner.
    if status == "PARTIAL_LOSS":
        return "PARTIAL_LOSS", round(_partial_r(row, -1.0), 4)
    if status == "TP1_HIT":
        return "OPEN", None
    if status == "SL_HIT":
        return "LOSS", -1.0
    if status == "EXPIRED":
        return ("UNRESOLVED", None) if row.get("entry_triggered_at") else ("NO_ENTRY", None)
    if status == "AMBIGUOUS":
        return "AMBIGUOUS", None
    return "OPEN", None



def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _stop_hit(row: dict, candle: pd.Series, stop_price: float) -> bool:
    high = float(candle["high"])
    low = float(candle["low"])
    return low <= stop_price if row["direction"] == "LONG" else high >= stop_price


def evaluate_trade(row: dict, candles: pd.DataFrame, *, expiry_bars: int | None = None,
                   move_stop_to_entry_after_tp1: bool | None = None,
                   first_delivery=None, as_of=None) -> dict:
    from app.services.tracking_rules import replay_crypto
    from app.services.tracking_time_exit import apply_time_exit
    settings = get_settings()
    records = candles.to_dict("records")
    outcome = replay_crypto(
        row, records,
        first_delivery=first_delivery or row.get("first_delivered_at"),
        as_of=as_of or datetime.now(UTC),
        expiry_bars=expiry_bars or settings.trade_history_expiry_bars,
        move_to_be=settings.trade_history_move_stop_to_entry_after_tp1
        if move_stop_to_entry_after_tp1 is None else move_stop_to_entry_after_tp1,
    )
    return apply_time_exit(row, outcome, records)



def update_outcome(trade_id: int, outcome: dict) -> None:
    from app.services.tracking_ledger import guard_crypto_transition, record_correction
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        before_row = connection.execute("SELECT * FROM trade_ideas WHERE id=?", (trade_id,)).fetchone()
        if before_row is None:
            raise LookupError(trade_id)
        before = dict(before_row)
        if before.get("outcome_checked_at") and _parse_dt(before["outcome_checked_at"]) > _parse_dt(outcome["outcome_checked_at"]):
            return
        previous = connection.execute("SELECT * FROM trade_outcomes WHERE trade_idea_id=?", (trade_id,)).fetchone()
        if previous:
            before["previous_trade_outcome"] = dict(previous)
        outcome = guard_crypto_transition(before, outcome)
        if outcome.get("tracking_evidence", {}).get("category") != "resolved":
            outcome.get("tracking_evidence", {}).pop("costs", None)
        status = str(outcome["status"] or "").upper()
        if status in {"TP2_HIT", "SL_HIT", "AMBIGUOUS", "BREAK_EVEN", "CLOSED_AFTER_TP1", "PARTIAL_LOSS", "TIME_EXIT"}:
            lifecycle_status = "closed"
        elif status == "TP1_HIT":
            lifecycle_status = "tp1_hit"
        elif status in {"EXPIRED", "PARTIAL_WIN"}:
            lifecycle_status = "expired"
        elif status in {"UNRESOLVED", "EXPIRED_POSITION", "EXPIRED_RUNNER"}:
            lifecycle_status = before.get("lifecycle_status") or "active"
        else:
            lifecycle_status = "active"
        record_correction(connection, "CRYPTO", trade_id, before, outcome,
                          outcome.get("tracking_evidence", {}), outcome["outcome_checked_at"])
        connection.execute(
            """
            UPDATE trade_ideas
            SET status = ?, result = ?, entry_triggered_at = ?, tp1_hit_at = ?,
                tp2_hit_at = ?, sl_hit_at = ?, expired_at = ?, closed_at = ?,
                outcome_checked_at = ?, pnl_r_multiple = ?, candles_to_resolution = ?,
                lifecycle_events = ?, lifecycle_status = ?
            WHERE id = ?
            """,
            (
                outcome["status"],
                outcome["result"],
                outcome["entry_triggered_at"],
                outcome["tp1_hit_at"],
                outcome["tp2_hit_at"],
                outcome["sl_hit_at"],
                outcome["expired_at"],
                outcome["closed_at"],
                outcome["outcome_checked_at"],
                outcome["pnl_r_multiple"],
                outcome["candles_to_resolution"],
                outcome["lifecycle_events"],
                lifecycle_status,
                trade_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO trade_outcomes (
                trade_idea_id, status, result, entry_triggered_at, tp1_hit_at,
                tp2_hit_at, sl_hit_at, expired_at, closed_at, outcome_checked_at,
                pnl_r_multiple, candles_to_resolution, lifecycle_events, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_idea_id) DO UPDATE SET
                status = excluded.status,
                result = excluded.result,
                entry_triggered_at = excluded.entry_triggered_at,
                tp1_hit_at = excluded.tp1_hit_at,
                tp2_hit_at = excluded.tp2_hit_at,
                sl_hit_at = excluded.sl_hit_at,
                expired_at = excluded.expired_at,
                closed_at = excluded.closed_at,
                outcome_checked_at = excluded.outcome_checked_at,
                pnl_r_multiple = excluded.pnl_r_multiple,
                candles_to_resolution = excluded.candles_to_resolution,
                lifecycle_events = excluded.lifecycle_events,
                notes = excluded.notes
            """,
            (
                trade_id,
                outcome["status"],
                outcome["result"],
                outcome["entry_triggered_at"],
                outcome["tp1_hit_at"],
                outcome["tp2_hit_at"],
                outcome["sl_hit_at"],
                outcome["expired_at"],
                outcome["closed_at"],
                outcome["outcome_checked_at"],
                outcome["pnl_r_multiple"],
                outcome["candles_to_resolution"],
                outcome["lifecycle_events"],
                "Checked from OHLCV candles.",
            ),
        )



ACTIVE_TRADE_STATUSES = ("PENDING", "OPEN", "ENTRY_TRIGGERED", "TP1_HIT")


def list_active_crypto_trades(exchange: str | None = None, limit: int = 50) -> list[dict]:
    """Return unresolved crypto trades that were successfully delivered on Telegram."""
    selected_exchange = str(exchange or "all").lower()
    clauses = [
        "t.result = 'OPEN'",
        "t.lifecycle_status = 'active'",
        f"t.status IN ({','.join('?' for _ in ACTIVE_TRADE_STATUSES)})",
        "(t.expires_at IS NULL OR datetime(t.expires_at) > datetime(?))",
        "t.exchange != 'forex'",
        "EXISTS (SELECT 1 FROM telegram_dispatch_recipients r WHERE r.dispatch_id = d.id AND r.status = 'SENT')",
    ]
    values: list = [*ACTIVE_TRADE_STATUSES, _now_iso()]
    if selected_exchange != "all":
        clauses.append("t.exchange = ?")
        values.append(selected_exchange)
    row_limit = min(100, max(1, int(limit or 50)))
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT t.*
            FROM trade_ideas t
            JOIN telegram_dispatches d ON d.trade_idea_id = t.id
            WHERE {' AND '.join(clauses)}
            ORDER BY t.setup_score DESC, datetime(COALESCE(t.executable_at, t.created_at)) DESC, t.id DESC
            LIMIT ?
            """,
            [*values, row_limit],
        ).fetchall()

    output: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        record = row_to_dict(row)
        identity = str(record.get("opportunity_key") or record.get("signal_fingerprint") or "|".join(
            [str(record.get("symbol") or ""), str(record.get("timeframe") or ""), str(record.get("direction") or "")]
        ))
        if identity in seen:
            continue
        seen.add(identity)
        record["source"] = "telegram-canonical"
        output.append(record)
    return output


async def _evaluate_rows(rows) -> dict:
    from app.services.tracking_ledger import first_crypto_delivery
    checked = 0
    changed = 0
    errors = 0
    candle_cache: dict[tuple[str, str, str], pd.DataFrame] = {}
    funding_cache = {}
    funding_unknown = 0
    # Fetch enough recent history for the oldest row in each market, including
    # the requested 60-day 1h replay. Exchange retention remains a hard limit.
    from app.services.tracking_rules import timeframe_duration
    import math
    limits = {}
    for raw in rows:
        key = (raw["exchange"], raw["symbol"], raw["timeframe"])
        age = max(0, (datetime.now(UTC) - _parse_dt(raw["created_at"])).total_seconds())
        needed = math.ceil(age / timeframe_duration(raw["timeframe"]).total_seconds()) + 8
        limits[key] = max(limits.get(key, 0), min(5000, max(1000, needed)))
    for raw in rows:
        row = row_to_dict(raw)
        try:
            cache_key = (row["exchange"], row["symbol"], row["timeframe"])
            if cache_key not in candle_cache:
                candle_cache[cache_key] = await get_candles_cached(row["exchange"], row["symbol"], row["timeframe"], limits[cache_key])
            candles = candle_cache[cache_key]
            with get_connection() as connection:
                delivered = first_crypto_delivery(connection, row["id"])
            outcome = evaluate_trade(row, candles, first_delivery=delivered)
            outcome = await _refine_with_intrabar(row, outcome, delivered, candle_cache)
            from app.services.tracking_time_exit import apply_time_exit
            outcome = apply_time_exit(row, outcome, candles.to_dict("records"))
            outcome = await _cost_outcome(row, outcome, funding_cache)
            funding_unknown += outcome.get("pnl_r_multiple") is not None and outcome["tracking_evidence"]["costs"]["funding_r"] is None
        except Exception:
            errors += 1
            logger.exception("Trade lifecycle evaluation failed id=%s symbol=%s timeframe=%s exchange=%s", row.get("id"), row.get("symbol"), row.get("timeframe"), row.get("exchange"))
            continue
        checked += 1
        if outcome["status"] != row["status"] or outcome["result"] != row["result"]:
            changed += 1
        update_outcome(row["id"], outcome)
    return {"checked": checked, "updated": changed, "errors": errors, "markets_fetched": len(candle_cache),
            "funding_unknown": funding_unknown}



async def check_trade_outcomes() -> dict:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM trade_ideas
            WHERE status IN ('PENDING', 'OPEN', 'ENTRY_TRIGGERED', 'TP1_HIT', 'UNRESOLVED', 'AMBIGUOUS', 'EXPIRED_POSITION', 'EXPIRED_RUNNER')
              AND COALESCE(entry_status, 'READY') = 'READY'
              AND (
                    (strategy_version IS NULL AND (opportunity_key IS NULL OR executable_at IS NOT NULL))
                    OR outcome_tracking_mode IN ('PRODUCTION', 'SHADOW')
                  )
            ORDER BY created_at ASC
            """
        ).fetchall()
    return await _evaluate_rows(rows)


async def replay_trade_outcomes(days: int = 30) -> dict:
    cutoff = (datetime.now(UTC) - timedelta(days=max(1, int(days)))).replace(microsecond=0).isoformat()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM trade_ideas
            WHERE datetime(created_at) >= datetime(?)
              AND COALESCE(entry_status, 'READY') = 'READY'
              AND (
                    (strategy_version IS NULL AND (opportunity_key IS NULL OR executable_at IS NOT NULL))
                    OR outcome_tracking_mode IN ('PRODUCTION', 'SHADOW')
                  )
            ORDER BY created_at ASC
            """,
            (cutoff,),
        ).fetchall()
    result = await _evaluate_rows(rows)
    result["days"] = max(1, int(days))
    result["since"] = cutoff
    return result


def stats() -> dict:
    from app.services.tracking_statistics import summarize, grouped as summarize_grouped
    with get_connection() as connection:
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute("SELECT * FROM trade_ideas").fetchall()]
        # Reviews can exceed a million rows in production; aggregate inside
        # SQLite instead of materializing their wide records in API/bot memory.
        review_rows = {row["status"]: dict(row) for row in connection.execute("""
            SELECT CASE WHEN COALESCE(accepted,0) != 0 THEN 'accepted' ELSE 'rejected' END AS status,
                   COUNT(*) AS count,
                   AVG(COALESCE(base_score,0)) AS average_base_score,
                   AVG(COALESCE(adjusted_score,0)) AS average_adjusted_score
            FROM signal_reviews GROUP BY 1
        """).fetchall()}
        # Read results and their latest evidence in one consistent snapshot.
        rows = _public_tracking_rows(connection, rows)
    detected_total = len(rows)
    pending_rows = [row for row in rows if row.get("entry_status") == "WAIT_FOR_RETEST" or row.get("strategy_decision") == "WAIT_FOR_RETEST"]
    shadow_rows = [row for row in rows if row.get("outcome_tracking_mode") == "SHADOW" or row.get("strategy_decision") == "SHADOW"]
    rows = [row for row in rows if production_statistics_row(row)]
    cost_bps = get_settings().tracking_cost_bps_per_side
    totals = summarize(rows, cost_bps)

    def grouped(field):
        return summarize_grouped(rows, field, cost_bps)

    def review_grouped() -> list[dict]:
        return [
            {"status": key, "count": review_rows.get(key, {}).get("count", 0),
             "average_base_score": round(review_rows[key]["average_base_score"], 2) if key in review_rows else 0,
             "average_adjusted_score": round(review_rows[key]["average_adjusted_score"], 2) if key in review_rows else 0}
            for key in ("accepted", "rejected")
        ]

    return {
        **totals,
        "total_ideas": detected_total,
        "detected_ideas": detected_total,
        "pending_retest_count": len(pending_rows),
        "shadow_count": len(shadow_rows),
        "executable_ideas": len(rows),
        "completed_opportunities": totals["resolved_entered_count"],
        "best_setup_grade_performance": grouped("setup_grade"),
        "best_timeframe_performance": grouped("timeframe"),
        "best_symbol_performance": grouped("symbol"),
        "direction_performance": grouped("direction"),
        "regime_performance": grouped("regime_label"),
        "accepted_vs_rejected": review_grouped(),
        "counter_trend_performance": grouped("trend_alignment"),
        "expectancy_by_regime": grouped("regime_label"),
        "expectancy_by_setup_type": grouped("setup_grade"),
    }



def _public_tracking_rows(connection, rows):
    """Attach the same append-only evidence and costs used by aggregate stats."""
    from app.services.tracking_costs import cost_r, net_r
    rows = [dict(row) for row in rows]
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    evidence = {}
    if rows and "tracking_corrections" in tables:
        ids = [str(row["id"]) for row in rows]
        # Stay below SQLite's variable limit even for the complete history.
        for offset in range(0, len(ids), 900):
            batch = ids[offset:offset+900]
            records = connection.execute(f"""SELECT signal_id, evidence_json FROM tracking_corrections
                WHERE id IN (SELECT MAX(id) FROM tracking_corrections WHERE asset='CRYPTO'
                    AND signal_id IN ({','.join('?' for _ in batch)}) GROUP BY signal_id)""", batch).fetchall()
            evidence.update({int(r["signal_id"]): json.loads(r["evidence_json"]) for r in records})
    for row in rows:
        row["tracking_evidence"] = evidence.get(row["id"], {})
        costs = row["tracking_evidence"].get("costs", {})
        row["gross_r_multiple"] = row.get("pnl_r_multiple") if row["tracking_evidence"].get("category") == "resolved" else None
        row["fee_r_multiple"] = cost_r(row, get_settings().tracking_cost_bps_per_side) if costs else None
        row["funding_r_multiple"] = costs.get("funding_r")
        row["net_r_multiple"] = net_r(row, get_settings().tracking_cost_bps_per_side) if costs else None
        row["cost_basis"] = costs.get("funding_basis")
        row["cost_status"] = "MODELED" if row["net_r_multiple"] is not None else "UNKNOWN"
    return rows



async def _refine_with_intrabar(row: dict, outcome: dict, delivered, candle_cache: dict) -> dict:
    """Settle a same-candle AMBIGUOUS outcome with 15m candles; keep it if they cannot."""
    if outcome.get("status") != "AMBIGUOUS":
        return outcome
    from app.services.tracking_intrabar import INTRABAR_TIMEFRAME, intrabar_candles_needed, refine_ambiguous
    settings = get_settings()
    as_of = datetime.now(UTC)
    needed = intrabar_candles_needed(row, as_of)
    if needed is None:
        return outcome
    limit = -(-needed // 200) * 200
    cache_key = (row["exchange"], row["symbol"], INTRABAR_TIMEFRAME)
    try:
        cached = candle_cache.get(cache_key)
        if cached is None or len(cached) < needed:
            candle_cache[cache_key] = await get_candles_cached(row["exchange"], row["symbol"], INTRABAR_TIMEFRAME, limit)
        return refine_ambiguous(
            row, outcome, candle_cache[cache_key].to_dict("records"),
            first_delivery=delivered or row.get("first_delivered_at"), as_of=as_of,
            expiry_bars=settings.trade_history_expiry_bars,
            move_to_be=settings.trade_history_move_stop_to_entry_after_tp1,
            coarse_candles=candle_cache[(row["exchange"], row["symbol"], row["timeframe"])].to_dict("records"),
        )
    except Exception:
        logger.exception("Intrabar refinement failed id=%s symbol=%s", row.get("id"), row.get("symbol"))
        return outcome



async def _cost_outcome(row, outcome, cache):
    if outcome.get("pnl_r_multiple") is None:
        return outcome
    from app.services.tracking_funding import BASIS, fetch_rates, funding_r
    from app.services.tracking_costs import cost_r
    funding = rates = None
    try:
        rates = await fetch_rates(row, outcome, cache)
        if rates is not None:
            funding = funding_r(row, outcome, rates)
    except Exception:
        logger.exception("Funding history unavailable for signal id=%s", row.get("id"))
    fee = cost_r(dict(row, **outcome), get_settings().tracking_cost_bps_per_side)
    outcome["tracking_evidence"]["costs"] = dict(fee_r=fee, funding_r=funding,
        net_r=None if fee is None or funding is None else outcome["pnl_r_multiple"]-fee-funding,
        funding_basis=BASIS, rates=rates, bps_per_side=get_settings().tracking_cost_bps_per_side)
    return outcome



def production_statistics_row(row: dict) -> bool:
    """Never mix research or pending-retest cohorts into production accounting."""
    if row.get("entry_status") == "WAIT_FOR_RETEST" or row.get("strategy_decision") in {"WAIT_FOR_RETEST", "SHADOW", "NO_TRADE"}:
        return False
    if row.get("outcome_tracking_mode") == "SHADOW":
        return False
    if row.get("strategy_version"):
        return row.get("strategy_decision") == "TRADE" and row.get("outcome_tracking_mode") == "PRODUCTION"
    return not row.get("opportunity_key") or row.get("executable_at") is not None
