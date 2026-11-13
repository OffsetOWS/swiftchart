"""Explicit maintenance replay; importing the module does not change data."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


async def main():
    from app.services.trade_history import replay_trade_outcomes, stats
    parser = argparse.ArgumentParser(description="Recheck saved crypto tracking, preserving correction history.")
    parser.add_argument("--days", type=int, default=365, choices=range(1, 366), metavar="1..365")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = await replay_trade_outcomes(args.days)
    summary = stats()
    result["coverage"] = {key: summary[key] for key in (
        "total_ideas", "resolved_entered_count", "unresolved_count", "ambiguous_count",
        "legacy_unreplayed_count", "funding_unknown_count", "expectancy_sample_count")}
    body = json.dumps(result, indent=2, allow_nan=False)
    print(body)
    if args.report:
        # Do not overwrite a previous replay receipt.
        with args.report.open("x") as stream:
            stream.write(body + "\n")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
