import asyncio
import sqlite3
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.models.schemas import PaperTrade, PaperTradeCreate
from app.forex.storage import ensure_forex_schema
from app.services.market_data import get_candles_cached
from app.utils.database import get_connection

router = APIRouter()
logger = logging.getLogger(__name__)


def _notes(row: sqlite3.Row) -> dict:
    try:
        return json.loads(row["notes"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _normalized_symbol(value: str | None) -> str:
    return str(value or "").upper().replace("/", "").replace("_", "").replace("-", "")


def _normalized_timeframe(value: str | None) -> str:
    aliases = {"15MIN": "15M", "M15": "15M", "H1": "1H", "H4": "4H", "D": "1D", "DAILY": "1D"}
    normalized = str(value or "").upper().replace(" ", "")
    return aliases.get(normalized, normalized)


def _normalized_direction(value: str | None) -> str:
    return "SELL" if str(value or "").upper() in {"SELL", "SHORT"} else "BUY"


def _closest_forex_signal(connection: sqlite3.Connection, trade: sqlite3.Row) -> sqlite3.Row | None:
    notes = _notes(trade)
    source_signal_id = notes.get("source_signal_id")
    if source_signal_id:
        match = connection.execute(
            "SELECT * FROM forex_signals WHERE public_id = ? OR CAST(id AS TEXT) = ? LIMIT 1",
            (str(source_signal_id), str(source_signal_id)),
        ).fetchone()
        if match:
            return match

    candidates = connection.execute(
        "SELECT * FROM forex_signals ORDER BY created_at DESC LIMIT 500"
    ).fetchall()
    symbol = _normalized_symbol(trade["symbol"])
    timeframe = _normalized_timeframe(trade["timeframe"])
    direction = _normalized_direction(trade["direction"])
    matching = [
        row for row in candidates
        if _normalized_symbol(row["symbol"] or row["pair"]) == symbol
        and _normalized_timeframe(row["timeframe"] or row["execution_timeframe"]) == timeframe
        and _normalized_direction(row["direction"]) == direction
    ]
    if not matching:
        return None

    entry = float(trade["entry_price"] or 0)
    stop = float(trade["stop_loss"] or 0)
    return min(
        matching,
        key=lambda row: abs(float(row["entry_price"] or row["entry"] or 0) - entry)
        + abs(float(row["stop_loss"] or 0) - stop),
    )


def _enrich_paper_trade(connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
    result = dict(row)
    if str(row["exchange"] or "").lower() != "forex":
        return result

    signal = _closest_forex_signal(connection, row)
    if not signal:
        return result

    status = str(signal["status"] or "PENDING_ENTRY").upper()
    labels = {
        "PENDING_ENTRY": "Waiting for entry",
        "OPEN": "Open",
        "TP1_HIT": "TP1 Hit",
        "TP2_HIT": "TP2 Hit",
        "STOPPED": "Stop Loss",
        "EXPIRED": "Expired",
        "CANCELLED": "Cancelled",
    }
    lifecycle_result = "open"
    result_r = None
    if status == "TP2_HIT":
        lifecycle_result = "win"
        result_r = float(signal["risk_reward_2"] or signal["rr"] or 0)
    elif status == "STOPPED":
        lifecycle_result = "loss"
        result_r = -1.0
    elif status in {"EXPIRED", "CANCELLED"}:
        lifecycle_result = "closed"

    result.update({
        "source_signal_id": signal["public_id"],
        "lifecycle_status": status,
        "lifecycle_label": labels.get(status, status.replace("_", " ").title()),
        "lifecycle_result": lifecycle_result,
        "result_r": result_r,
        "latest_price": signal["latest_price"] or signal["last_market_price"],
        "latest_price_at": signal["latest_price_at"] or signal["last_price_updated_at"],
        "activated_at": signal["activated_at"],
        "tp1_hit_at": signal["tp1_hit_at"],
        "tp2_hit_at": signal["tp2_hit_at"],
        "stopped_at": signal["stopped_at"],
        "closed_at": signal["closed_at"],
        "expires_at": signal["expires_at"],
        "entry_low": signal["entry_low"],
        "entry_high": signal["entry_high"],
        "risk_reward_1": signal["risk_reward_1"],
        "market_session": signal["market_session"] or signal["session"],
        "setup_reason": signal["setup_reason"] or signal["reason"],
    })
    return result


def _datetime(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _crypto_result_r(trade: dict, *, stopped_after_tp1: bool = False) -> float:
    if not stopped_after_tp1:
        return float(trade.get("risk_reward") or 0)
    entry = float(trade["entry_price"])
    stop = float(trade["stop_loss"])
    risk = abs(entry - stop)
    if risk <= 0:
        return -0.5
    tp1_r = abs(float(trade["take_profit_1"]) - entry) / risk
    return round((tp1_r * 0.5) - 0.5, 4)


def evaluate_crypto_paper_trade(trade: dict, candles, *, as_of=None) -> dict:
    """User save time establishes availability, not a fill or a cash result."""
    from app.services.trade_history import evaluate_trade
    outcome = evaluate_trade(_paper_tracking_row(trade), candles, as_of=as_of)
    outcome["tracking_evidence"]["availability_basis"] = "user_saved_paper_plan"
    return _paper_outcome(trade, outcome, candles)



HISTORY_REFRESH_TIMEOUT_SECONDS = 5.0


async def _refresh_crypto_paper_trades(rows: list[dict]) -> list[dict]:
    """Market-data failures must never hide records already saved in History."""
    try:
        return await asyncio.wait_for(
            _refresh_crypto_paper_trade_outcomes(rows),
            timeout=HISTORY_REFRESH_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning("History outcome refresh timed out; returning %s saved trades", len(rows))
    except Exception:
        logger.exception("History outcome refresh failed; returning saved trades")
    return [dict(row, pnl=None, result_r=None, net_r_multiple=None, cost_status="UNKNOWN",
                 lifecycle_label="Costs unknown") if str(row.get("exchange", "")).lower() != "forex" else row for row in rows]



async def _refresh_crypto_paper_trade_outcomes(rows: list[dict]) -> list[dict]:
    from app.services.trade_history import _refine_with_intrabar, _cost_outcome
    from app.services.tracking_time_exit import apply_time_exit
    from app.services.tracking_ledger import record_correction
    candle_cache, funding_cache = {}, {}
    refreshed = []
    for row in rows:
        if str(row.get("exchange") or "").lower() == "forex":
            refreshed.append(row)
            continue
        key = (str(row.get("exchange") or "hyperliquid").lower(), str(row.get("symbol") or "").upper(), str(row.get("timeframe") or "4h").lower())
        try:
            if key not in candle_cache:
                candle_cache[key] = await get_candles_cached(*key, 1000)
            candles = candle_cache[key]
            model = _paper_tracking_row(row)
            tracked = evaluate_crypto_paper_trade(row, candles)["_tracking_outcome"]
            tracked = await _refine_with_intrabar(model, tracked, model["first_delivered_at"], candle_cache)
            tracked = apply_time_exit(model, tracked, candles.to_dict("records"))
            tracked = await _cost_outcome(model, tracked, funding_cache)
            outcome = _paper_outcome(row, tracked, candles)
            outcome.pop("_tracking_outcome")
            enriched = {**row, **outcome}
            with get_connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute("SELECT * FROM paper_trades WHERE id=?", (row["id"],)).fetchone()
                if current:
                    record_correction(connection, "PAPER", row["id"], dict(current), enriched,
                                      tracked["tracking_evidence"], tracked["outcome_checked_at"])
                    connection.execute("UPDATE paper_trades SET status=?, result=?, pnl=? WHERE id=?",
                        (outcome["status"], outcome["result"], outcome["pnl"], row["id"]))
            refreshed.append(enriched)
        except Exception:
            logger.exception("Crypto paper-trade refresh failed id=%s symbol=%s", row.get("id"), row.get("symbol"))
            refreshed.append({**row, "pnl": None, "result_r": None, "net_r_multiple": None,
                "result": "closed", "lifecycle_result": "closed", "lifecycle_status": "UNRESOLVED",
                "lifecycle_label": "Tracking unavailable", "cost_status": "UNKNOWN"})
    return refreshed



def _payload_user_id(payload: PaperTradeCreate) -> str:
    if payload.user_id:
        return str(payload.user_id)
    if payload.notes:
        try:
            user_id = json.loads(payload.notes).get("user_id")
            if user_id:
                return str(user_id)
        except (TypeError, json.JSONDecodeError):
            pass
    return "anonymous"


@router.post("/paper-trade", response_model=PaperTrade)
async def create_paper_trade(payload: PaperTradeCreate):
    if not payload.signal_id or not payload.symbol or not payload.entry_price or not payload.stop_loss:
        raise HTTPException(status_code=422, detail="Missing required trade data.")
    user_id = _payload_user_id(payload)
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT * FROM paper_trades WHERE user_id = ? AND signal_id = ?",
            (user_id, payload.signal_id),
        ).fetchone()
        if existing:
            return PaperTrade(**{**dict(existing), "already_taken": True})
        try:
            cursor = connection.execute(
                """
                INSERT INTO paper_trades (
                    user_id, signal_id, symbol, timeframe, exchange, direction,
                    entry_price, stop_loss, take_profit_1, take_profit_2, size,
                    risk_reward, setup_score, confidence, market_bias, notes,
                    status, result, taken_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'taken', 'open', CURRENT_TIMESTAMP)
                """,
                (
                    user_id,
                    payload.signal_id,
                    payload.symbol.upper(),
                    payload.timeframe.lower(),
                    payload.exchange.lower(),
                    payload.direction,
                    payload.entry_price,
                    payload.stop_loss,
                    payload.take_profit_1,
                    payload.take_profit_2,
                    payload.size,
                    payload.risk_reward,
                    payload.setup_score,
                    payload.confidence,
                    payload.market_bias,
                    payload.notes,
                ),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                "SELECT * FROM paper_trades WHERE user_id = ? AND signal_id = ?",
                (user_id, payload.signal_id),
            ).fetchone()
            if row:
                return PaperTrade(**{**dict(row), "already_taken": True})
            raise
        row = connection.execute("SELECT * FROM paper_trades WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return PaperTrade(**dict(row))


@router.get("/paper-trades", response_model=list[PaperTrade])
async def list_paper_trades(background_tasks: BackgroundTasks, user_id: str | None = Query(default=None)):
    ensure_forex_schema()
    with get_connection() as connection:
        if user_id:
            rows = connection.execute(
                "SELECT * FROM paper_trades WHERE user_id = ? ORDER BY taken_at DESC, created_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM paper_trades ORDER BY taken_at DESC, created_at DESC").fetchall()
        enriched = [_enrich_paper_trade(connection, row) for row in rows]
    background_tasks.add_task(_refresh_crypto_paper_trades, enriched)
    return [PaperTrade(**row) for row in enriched]


@router.get("/paper-trades/{trade_id}", response_model=PaperTrade)
async def get_paper_trade(trade_id: int, user_id: str | None = Query(default=None)):
    ensure_forex_schema()
    with get_connection() as connection:
        if user_id:
            row = connection.execute(
                "SELECT * FROM paper_trades WHERE id = ? AND user_id = ?",
                (trade_id, user_id),
            ).fetchone()
        else:
            row = connection.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Paper trade not found.")
        enriched = _enrich_paper_trade(connection, row)
    enriched = (await _refresh_crypto_paper_trades([enriched]))[0]
    return PaperTrade(**enriched)


@router.patch("/paper-trades/{trade_id}", response_model=PaperTrade)
async def update_paper_trade_status(trade_id: int, payload: dict, user_id: str | None = Query(default=None)):
    allowed_status = {"taken", "open", "tp_hit", "sl_hit", "closed"}
    allowed_result = {"open", "win", "loss", "closed"}
    status = str(payload.get("status") or "").lower()
    result = str(payload.get("result") or "").lower()
    pnl = payload.get("pnl")
    if status not in allowed_status or result not in allowed_result:
        raise HTTPException(status_code=422, detail="Invalid paper trade status update.")
    with get_connection() as connection:
        if user_id:
            connection.execute(
                """
                UPDATE paper_trades
                SET status = ?, result = ?, pnl = ?
                WHERE id = ? AND user_id = ?
                """,
                (status, result, pnl, trade_id, user_id),
            )
            row = connection.execute(
                "SELECT * FROM paper_trades WHERE id = ? AND user_id = ?",
                (trade_id, user_id),
            ).fetchone()
        else:
            connection.execute(
                """
                UPDATE paper_trades
                SET status = ?, result = ?, pnl = ?
                WHERE id = ?
                """,
                (status, result, pnl, trade_id),
            )
            row = connection.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Paper trade not found.")
    return PaperTrade(**dict(row))


def _paper_tracking_row(trade):
    taken = _datetime(trade.get("taken_at") or trade["created_at"])
    return dict(trade, created_at=taken, entry_zone_low=trade["entry_price"],
        entry_zone_high=trade["entry_price"], direction="LONG" if _normalized_direction(trade["direction"]) == "BUY" else "SHORT",
        timeframe=str(trade["timeframe"]).lower(), first_delivered_at=taken,
        entry_triggered_at=None, lifecycle_events="[]")



def _paper_outcome(trade, outcome, candles):
    costs = outcome.get("tracking_evidence", {}).get("costs", {})
    net = costs.get("net_r")
    status = outcome["status"]
    terminal = outcome.get("closed_at") is not None
    uncertain = status in {"AMBIGUOUS", "UNRESOLVED", "EXPIRED_POSITION", "EXPIRED_RUNNER"}
    result = "win" if net is not None and net > 0 else "loss" if net is not None and net < 0 else "closed" if terminal or uncertain or status == "EXPIRED" else "open"
    latest = candles.sort_values("timestamp").iloc[-1] if candles is not None and not candles.empty else None
    return dict(status="closed" if terminal or uncertain or status == "EXPIRED" else "tp_hit" if status == "TP1_HIT" else "open",
        result=result, pnl=net, result_r=net, gross_r_multiple=outcome.get("pnl_r_multiple"),
        fee_r_multiple=costs.get("fee_r"), funding_r_multiple=costs.get("funding_r"), net_r_multiple=net,
        cost_basis=costs.get("funding_basis"), cost_status="MODELED" if net is not None else "UNKNOWN",
        lifecycle_status="STOPPED" if status == "SL_HIT" else "TP1_HIT_TP2_RUNNING" if status == "TP1_HIT" else status,
        lifecycle_label="Time exit (modeled)" if status == "TIME_EXIT" else "Unresolved" if uncertain else status.replace("_", " ").title(),
        lifecycle_result=result, activated_at=outcome.get("entry_triggered_at"),
        tp1_hit_at=outcome.get("tp1_hit_at"), tp2_hit_at=outcome.get("tp2_hit_at"),
        stopped_at=outcome.get("sl_hit_at"), closed_at=outcome.get("closed_at"),
        latest_price=float(latest["close"]) if latest is not None else None,
        latest_price_at=_datetime(latest["timestamp"]).isoformat() if latest is not None else None,
        entry_low=trade["entry_price"], entry_high=trade["entry_price"], _tracking_outcome=outcome)
