from __future__ import annotations

from datetime import datetime, timezone

from execution_bot.config import get_execution_settings
from execution_bot.exchanges import get_execution_exchange
from execution_bot.market_filter import evaluate_market
from execution_bot.models import BotStatus, SignalDecision, SignalIn
from execution_bot.risk import build_execution_plan
from execution_bot.storage import (
    account_balance,
    claim_signal,
    consecutive_losses,
    daily_pnl,
    get_status,
    is_duplicate,
    log_event,
    open_exposure_for_symbol,
    open_trade_count,
    record_signal,
    record_trade,
    close_trade,
    list_open_trades,
    runtime_base_risk_percent,
    set_account_balance,
    update_trade_execution_details,
    weekly_pnl,
)


def _age_seconds(signal: SignalIn) -> float:
    created_at = signal.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created_at).total_seconds()


def preflight_signal(signal: SignalIn) -> tuple[bool, str]:
    settings = get_execution_settings()
    status = get_status()
    balance = account_balance()

    if status == BotStatus.killed:
        return False, "Execution bot kill switch is active."
    if status == BotStatus.paused:
        return False, "Execution bot is paused."
    if signal.confidence < settings.min_confidence_to_trade:
        return False, f"Confidence {signal.confidence} is below minimum {settings.min_confidence_to_trade}."
    if _age_seconds(signal) > settings.signal_ttl_seconds:
        return False, "Signal is expired."
    if is_duplicate(signal, settings.duplicate_window_seconds):
        return False, "Duplicate signal rejected."
    if open_trade_count() >= settings.max_open_trades:
        return False, "Maximum open trades reached."
    daily_loss_limit = balance * settings.max_daily_loss_percent / 100
    weekly_loss_limit = balance * settings.max_weekly_loss_percent / 100
    if balance > 0 and daily_loss_limit > 0 and daily_pnl() < -daily_loss_limit:
        return False, "Daily loss limit reached."
    if balance > 0 and weekly_loss_limit > 0 and weekly_pnl() < -weekly_loss_limit:
        return False, "Weekly loss limit reached."
    if consecutive_losses() >= settings.max_consecutive_losses:
        return False, f"{settings.max_consecutive_losses} consecutive losses reached; manual resume required."
    return True, "Preflight passed."


def _planning_balance() -> float:
    settings = get_execution_settings()
    balance = account_balance()
    if balance > 0:
        return balance
    return settings.starting_balance


async def process_signal(signal: SignalIn) -> SignalDecision:
    settings = get_execution_settings()
    log_event("signal_received", signal.model_dump(mode="json"))
    passed, reason = preflight_signal(signal)
    if not passed:
        decision = SignalDecision(accepted=False, reason=reason, signal=signal)
        log_event("rejected_signal", {"pair": signal.pair, "reason": reason})
        record_signal(decision)
        await _notify(decision)
        return decision

    if not claim_signal(signal):
        reason = "Duplicate signal rejected."
        decision = SignalDecision(accepted=False, reason=reason, signal=signal)
        log_event("rejected_signal", {"pair": signal.pair, "reason": reason})
        record_signal(decision)
        await _notify(decision)
        return decision

    symbol = f"{signal.pair}{settings.execution_quote_asset}" if not signal.pair.endswith(settings.execution_quote_asset) else signal.pair
    exchange = get_execution_exchange(settings.execution_exchange)
    snapshot = await exchange.get_market_snapshot(symbol, signal.timeframe)
    market = evaluate_market(snapshot, settings)
    if not market.allowed:
        decision = SignalDecision(
            accepted=False,
            reason="; ".join(market.reasons),
            signal=signal,
            metadata=market.model_dump(),
        )
        log_event("rejected_signal", {"pair": signal.pair, "reason": decision.reason, "market": market.model_dump()})
        record_signal(decision)
        await _notify(decision)
        return decision

    try:
        plan = build_execution_plan(
            signal=signal,
            candles=snapshot.candles,
            account_balance=_planning_balance(),
            consecutive_losses=consecutive_losses(),
            open_exposure=open_exposure_for_symbol(symbol),
            atr_value=market.atr_value,
            atr_percent=market.atr_percent,
            market_condition=market.condition,
            settings=settings,
            base_risk_percent=runtime_base_risk_percent(),
        )
    except ValueError as exc:
        decision = SignalDecision(accepted=False, reason=str(exc), signal=signal, metadata=market.model_dump())
        log_event("rejected_signal", {"pair": signal.pair, "reason": str(exc)})
        record_signal(decision)
        await _notify(decision)
        return decision
    if plan.notional_value < settings.min_order_notional:
        reason = f"Order value ${plan.notional_value:.2f} is below the ${settings.min_order_notional:.2f} exchange minimum."
        decision = SignalDecision(accepted=False, reason=reason, signal=signal, plan=plan, metadata=market.model_dump())
        log_event("rejected_signal", {"pair": signal.pair, "reason": reason, "notional_value": plan.notional_value})
        record_signal(decision)
        await _notify(decision)
        return decision
    log_event("trade_approved", {"pair": signal.pair, "symbol": symbol, "risk_percent": plan.risk_percent, "leverage": plan.leverage})
    try:
        log_event("order_sent", {"pair": signal.pair, "symbol": symbol, "side": plan.side.value, "size": plan.position_size})
        order = await exchange.place_order(plan)
    except Exception as exc:
        decision = SignalDecision(accepted=False, reason=f"Order placement failed: {exc}", signal=signal, plan=plan, metadata=market.model_dump())
        log_event("execution_error", {"pair": signal.pair, "error": str(exc)})
        record_signal(decision)
        await _notify(decision)
        return decision
    verification = order.get("verification") if isinstance(order.get("verification"), dict) else {}
    decision = SignalDecision(
        accepted=True,
        reason="Signal accepted and live execution verified." if verification.get("all_protection_active", True) else "Signal accepted, but protective order verification needs attention.",
        signal=signal,
        plan=plan,
        metadata={"order": order},
    )
    signal_key = record_signal(decision)
    trade_id = record_trade(signal_key, plan, order_id=order.get("id"))
    balance = await exchange.sync_account_balance()
    if balance is not None:
        set_account_balance(balance)
    update_trade_execution_details(trade_id, order, balance=balance)
    if order.get("status") in {"filled", "accepted", "submitted", "paper"}:
        log_event("order_filled", {"order_id": order.get("id"), "fill_price": order.get("fill_price")}, trade_id=trade_id)
    if verification.get("stop_loss_active"):
        log_event("stop_placed", {"order_id": order.get("stop_order_id"), "stop_loss": plan.stop_loss}, trade_id=trade_id)
    if verification.get("tp_orders_active"):
        log_event("tp_placed", {"order_ids": order.get("tp_order_ids"), "take_profits": plan.take_profits}, trade_id=trade_id)
    if verification and not verification.get("all_protection_active"):
        log_event("execution_error", {"trade_id": trade_id, "reason": "Protective order verification failed.", "verification": verification}, trade_id=trade_id)
        await _notify_execution_event(
            "SwiftChart Execution Error",
            f"{plan.symbol} opened, but SL/TP verification is incomplete.\nTrade ID: {trade_id}\nCheck Hyperliquid immediately.",
            {"trade_id": trade_id, "verification": verification},
        )
    await _notify(decision)
    return decision


async def sync_live_account_state() -> None:
    settings = get_execution_settings()
    exchange = get_execution_exchange(settings.execution_exchange)
    summary = await exchange.account_summary()
    balance = summary.get("balance")
    if balance is not None and float(balance) > 0:
        set_account_balance(float(balance))
        log_event("pnl_updated", {"balance": float(balance)})

    positions = {str(position.get("coin")): position for position in summary.get("positions", [])}
    for trade in list_open_trades(50):
        symbol = str(trade["symbol"])
        coin = symbol.upper().replace(settings.execution_quote_asset, "")
        position = positions.get(coin)
        if position and abs(float(position.get("size") or 0)) > 0:
            log_event(
                "pnl_updated",
                {"trade_id": trade["id"], "symbol": symbol, "unrealized_pnl": float(position.get("unrealized_pnl") or 0)},
                trade_id=int(trade["id"]),
            )
            continue

        created = datetime.fromisoformat(str(trade["created_at"]).replace("Z", "+00:00"))
        fills = await exchange.recent_fills(symbol, int(created.timestamp() * 1000))
        closed_pnl = sum(float(fill.get("closedPnl") or 0) for fill in fills)
        if not fills:
            continue
        close_trade(int(trade["id"]), closed_pnl, balance=float(balance) if balance is not None else None)
        event_title = "SwiftChart TP Hit" if closed_pnl >= 0 else "SwiftChart Stop Loss Hit"
        log_event("trade_closed", {"trade_id": trade["id"], "symbol": symbol, "pnl": closed_pnl}, trade_id=int(trade["id"]))
        await _notify_execution_event(
            event_title,
            f"{symbol} closed.\nPnL: ${closed_pnl:,.2f}\nBalance: ${float(balance or account_balance()):,.2f}",
            {"trade_id": trade["id"], "pnl": closed_pnl},
        )
        await _notify_execution_event(
            "SwiftChart Trade Closed",
            f"{symbol} is now closed.\nPnL: ${closed_pnl:,.2f}",
            {"trade_id": trade["id"], "pnl": closed_pnl},
        )


async def _notify(decision: SignalDecision) -> None:
    try:
        from execution_bot.telegram_bot import notify_signal_decision

        await notify_signal_decision(decision)
    except Exception:
        pass


async def _notify_execution_event(title: str, body: str, details: dict | None = None) -> None:
    try:
        from execution_bot.telegram_bot import notify_execution_event

        await notify_execution_event(title, body, details)
    except Exception:
        pass
