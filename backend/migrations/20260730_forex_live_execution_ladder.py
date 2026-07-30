from app.forex.storage import ensure_forex_schema


if __name__ == "__main__":
    ensure_forex_schema()
    print("Forex live execution ladder migration applied.")
