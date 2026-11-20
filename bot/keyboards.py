from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from app.services.agent_links import signal_agent_url


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Analyze Coin", callback_data="analyze_help"),
                InlineKeyboardButton("Top 5 Trade Ideas", callback_data="top"),
            ],
            [
                InlineKeyboardButton("Trade Alerts", callback_data="subscribe"),
                InlineKeyboardButton("Strategy", callback_data="strategy"),
            ],
            [
                InlineKeyboardButton("Help", callback_data="help"),
            ],
        ]
    )


def command_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["/top", "/analysis"], ["/open", "/mytrades"], ["/subscribe", "/strategy"], ["/help", "/analyze SOLUSDT 4h"]],
        resize_keyboard=True,
        input_field_placeholder="/analyze SOLUSDT 4h",
    )


def trade_alert_keyboard(signal_id: str, *, agent_signal_id=None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("🧪 Paper Trade", callback_data=f"paper:{signal_id}")]]
    if agent_signal_id is not None:
        rows.append([InlineKeyboardButton('Ask Agent Swift', url=signal_agent_url('crypto',agent_signal_id))])
    return InlineKeyboardMarkup(
        rows
    )
