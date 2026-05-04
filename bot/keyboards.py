from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


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
        [["/top", "/history"], ["/stats", "/checktrades"], ["/subscribe", "/strategy"], ["/help", "/analyze SOLUSDT 4h"]],
        resize_keyboard=True,
        input_field_placeholder="/analyze SOLUSDT 4h",
    )
