"""Successful Telegram receipts bound to the exact saved signal, never queue times."""
from datetime import UTC, datetime

from app.services.alert_dedupe import setup_fingerprint
from app.utils.database import get_connection


def ensure_delivery_receipts(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS crypto_delivery_receipts (
        trade_idea_id INTEGER NOT NULL REFERENCES trade_ideas(id),
        chat_id TEXT NOT NULL, message_id INTEGER NOT NULL, sent_at TEXT NOT NULL,
        PRIMARY KEY(chat_id, message_id))""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_crypto_delivery_signal ON crypto_delivery_receipts(trade_idea_id, sent_at)")


def find_saved_signal(idea):
    with get_connection() as connection:
        row = connection.execute("""SELECT id FROM trade_ideas
            WHERE signal_fingerprint=? AND symbol=? AND timeframe=? AND exchange=? AND direction=?
              AND entry_zone_low=? AND entry_zone_high=? AND stop_loss=?
              AND take_profit_1=? AND take_profit_2=? ORDER BY id DESC LIMIT 1""",
            (setup_fingerprint(idea), idea.symbol.upper(), idea.timeframe, idea.exchange,
             idea.direction.upper(), *idea.entry_zone, idea.stop_loss, idea.take_profit_1, idea.take_profit_2)).fetchone()
    return row["id"] if row else None


def record_delivery(trade_id, chat_id, message):
    # A Telegram Message supplies both values. A successful coroutine without a
    # receipt (e.g. an incomplete adapter) cannot prove delivery.
    sent_at = getattr(message, "date", None)
    message_id = getattr(message, "message_id", None)
    if not isinstance(sent_at, datetime) or sent_at.tzinfo is None or message_id is None:
        return False
    sent_at = sent_at.astimezone(UTC)
    if sent_at > datetime.now(UTC):
        raise ValueError("Future delivery receipt")
    with get_connection() as connection:
        ensure_delivery_receipts(connection)
        existing = connection.execute("SELECT trade_idea_id, sent_at FROM crypto_delivery_receipts WHERE chat_id=? AND message_id=?",
                                      (str(chat_id), int(message_id))).fetchone()
        if existing and (existing["trade_idea_id"] != trade_id or existing["sent_at"] != sent_at.isoformat()):
            raise ValueError("Delivery receipt conflicts with saved identity")
        connection.execute("INSERT OR IGNORE INTO crypto_delivery_receipts VALUES (?, ?, ?, ?)",
                           (trade_id, str(chat_id), int(message_id), sent_at.isoformat()))
    return True
