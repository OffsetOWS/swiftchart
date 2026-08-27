import sqlite3
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query

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


def evaluate_crypto_paper_trade(trade: dict, candles) -> dict:
    """Evaluate a user-saved crypto trade from the moment it was taken."""
    taken_at = _datetime(trade.get("taken_at") or trade.get("created_at"))
    direction = _normalized_direction(trade.get("direction"))
    is_long = direction == "BUY"
    stop = float(trade["stop_loss"])
    tp1 = float(trade["take_profit_1"])
    tp2 = float(trade["take_profit_2"])
    ordered = candles.copy().sort_values("timestamp") if candles is not None and not candles.empty else None
    latest_price = None
    latest_price_at = None
    if ordered is not None and not ordered.empty:
        latest = ordered.iloc[-1]
        latest_price = float(latest["close"])
        latest_price_at = _datetime(latest["timestamp"]).isoformat()

    tp1_hit_at = None
    tp2_hit_at = None
    stopped_at = None
    closed_at = None
    lifecycle_status = "OPEN"
    lifecycle_label = "Open"
    lifecycle_result = "open"
    paper_status = "open"
    paper_result = "open"
    result_r = None

    if ordered is not None:
        for _, candle in ordered.iterrows():
            candle_at = _datetime(candle["timestamp"])
            if candle_at <= taken_at:
                continue
            high = float(candle["high"])
            low = float(candle["low"])
            tp1_hit = high >= tp1 if is_long else low <= tp1
            tp2_hit = high >= tp2 if is_long else low <= tp2
            stop_hit = low <= stop if is_long else high >= stop

            if tp1_hit_at is None:
                if stop_hit and (tp1_hit or tp2_hit):
                    lifecycle_status = "AMBIGUOUS"
                    lifecycle_label = "TP/SL order unclear"
                    lifecycle_result = "closed"
                    paper_status = "closed"
                    paper_result = "closed"
                    closed_at = candle_at.isoformat()
                    break
                if tp2_hit:
                    tp1_hit_at = candle_at.isoformat()
                    tp2_hit_at = candle_at.isoformat()
                    closed_at = candle_at.isoformat()
                    lifecycle_status = "TP2_HIT"
                    lifecycle_label = "TP2 Hit"
                    lifecycle_result = "win"
                    paper_status = "closed"
                    paper_result = "win"
                    result_r = _crypto_result_r(trade)
                    break
                if tp1_hit:
                    tp1_hit_at = candle_at.isoformat()
                    lifecycle_status = "TP1_HIT_TP2_RUNNING"
                    lifecycle_label = "TP1 Hit · TP2 running"
                    lifecycle_result = "open"
                    paper_status = "tp_hit"
                    continue
                if stop_hit:
                    stopped_at = candle_at.isoformat()
                    closed_at = candle_at.isoformat()
                    lifecycle_status = "STOPPED"
                    lifecycle_label = "Stop Loss"
                    lifecycle_result = "loss"
                    paper_status = "sl_hit"
                    paper_result = "loss"
                    result_r = -1.0
                    break
            else:
                if stop_hit and tp2_hit:
                    lifecycle_status = "AMBIGUOUS"
                    lifecycle_label = "TP2/SL order unclear"
                    lifecycle_result = "closed"
                    paper_status = "closed"
                    paper_result = "closed"
                    closed_at = candle_at.isoformat()
                    break
                if tp2_hit:
                    tp2_hit_at = candle_at.isoformat()
                    closed_at = candle_at.isoformat()
                    lifecycle_status = "TP2_HIT"
                    lifecycle_label = "TP2 Hit"
                    lifecycle_result = "win"
                    paper_status = "closed"
                    paper_result = "win"
                    result_r = _crypto_result_r(trade)
                    break
                if stop_hit:
                    stopped_at = candle_at.isoformat()
                    closed_at = candle_at.isoformat()
                    lifecycle_status = "CLOSED_AFTER_TP1"
                    lifecycle_label = "TP1 then Stop"
                    result_r = _crypto_result_r(trade, stopped_after_tp1=True)
                    lifecycle_result = "win" if result_r > 0 else "loss" if result_r < 0 else "closed"
                    paper_status = "closed"
                    paper_result = lifecycle_result
                    break

    return {
        "status": paper_status,
        "result": paper_result,
        "pnl": result_r,
        "lifecycle_status": lifecycle_status,
        "lifecycle_label": lifecycle_label,
        "lifecycle_result": lifecycle_result,
        "result_r": result_r,
        "latest_price": latest_price,
        "latest_price_at": latest_price_at,
        "activated_at": taken_at.isoformat(),
        "tp1_hit_at": tp1_hit_at,
        "tp2_hit_at": tp2_hit_at,
        "stopped_at": stopped_at,
        "closed_at": closed_at,
        "entry_low": float(trade["entry_price"]),
        "entry_high": float(trade["entry_price"]),
    }


async def _refresh_crypto_paper_trades(rows: list[dict]) -> list[dict]:
    candle_cache = {}
    refreshed = []
    persistence_updates = []
    for row in rows:
        if str(row.get("exchange") or "").lower() == "forex":
            refreshed.append(row)
            continue
        key = (
            str(row.get("exchange") or "hyperliquid").lower(),
            str(row.get("symbol") or "").upper(),
            str(row.get("timeframe") or "4h").lower(),
        )
        try:
            if key not in candle_cache:
                candle_cache[key] = await get_candles_cached(*key, 1000)
            outcome = evaluate_crypto_paper_trade(row, candle_cache[key])
        except Exception:
            logger.exception("Crypto paper-trade refresh failed id=%s symbol=%s", row.get("id"), row.get("symbol"))
            refreshed.append(row)
            continue
        enriched = {**row, **outcome}
        refreshed.append(enriched)
        if (row.get("status"), row.get("result"), row.get("pnl")) != (
            outcome["status"], outcome["result"], outcome["pnl"],
        ):
            persistence_updates.append((outcome["status"], outcome["result"], outcome["pnl"], row["id"]))
    if persistence_updates:
        with get_connection() as connection:
            connection.executemany(
                "UPDATE paper_trades SET status = ?, result = ?, pnl = ? WHERE id = ?",
                persistence_updates,
            )
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
async def list_paper_trades(user_id: str | None = Query(default=None)):
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
    enriched = await _refresh_crypto_paper_trades(enriched)
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
