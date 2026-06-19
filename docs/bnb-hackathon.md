# SwiftChart × CoinMarketCap Market Intelligence

## Problem

Trade signals explain direction, entry, invalidation, and risk/reward, but traders also need to understand the market quality of the asset behind a setup. A technically valid setup on a highly ranked, liquid asset has a different context from the same pattern on a thinly traded market.

SwiftChart already uses Hyperliquid as its primary market-data source and has established scanner, strategy, ranking, and risk systems. The integration therefore needs to add transparency without changing how a trade signal is discovered or scored.

## Solution

CoinMarketCap is integrated as a read-only market-intelligence layer after SwiftChart generates a signal.

For each supported asset, the interface displays:

- Market capitalization
- 24-hour volume
- 24-hour price change
- CoinMarketCap rank
- An informational Market Quality Score
- A plain-language context label such as **High Quality Asset** or **Lower Liquidity Risk**

The dashboard also presents CoinMarketCap trending assets, top gainers, top losers, and the highest market-quality assets.

Market Intelligence is also SwiftChart's asset-discovery entry point. Every discovery row is actionable: selecting an asset opens `/analysis/[symbol]`, where SwiftChart attempts to turn the discovered market into support/resistance, regime, and trade-opportunity intelligence.

## Architecture

```text
Hyperliquid market data
        ↓
Existing SwiftChart scanner and analysis engine
        ↓
Existing trade signal and ranking
        ↓
CoinMarketCap market intelligence (optional)
        ↓
Context validation and quality label
        ↓
Final display
```

The CoinMarketCap layer is deliberately downstream from signal generation. It does not import, call, or modify the scanner, strategy, ranking, risk, authentication, subscription, payment, or Supabase modules.

If CoinMarketCap is not configured, times out, rejects a request, or is temporarily unavailable, the API returns a graceful `available: false` response. SwiftChart continues to scan and display signals using Hyperliquid exactly as before.

## CoinMarketCap Integration

The server-side client reads `CMC_API_KEY` and requests:

- Latest quotes for signal assets
- Latest market-cap listings
- Trending assets
- 24-hour gainers and losers

Requests are cached in memory for five minutes by default to reduce latency and API usage. The cache duration can be configured with `CMC_CACHE_TTL_SECONDS`.

The API key is never exposed to the browser. The frontend calls SwiftChart's read-only `/api/market-intelligence` endpoint and receives normalized market fields.

The requested TypeScript client is located at `lib/cmc.ts`. The current FastAPI deployment uses the matching isolated adapter in `backend/app/services/cmc.py`.

## Market Intelligence Layer

The Market Quality Score is informational and separate from every existing SwiftChart score.

It uses three CoinMarketCap inputs:

- Market capitalization: 45%
- 24-hour volume: 35%
- CMC rank: 20%

Market cap and volume use bounded logarithmic normalization so that very large assets do not overwhelm the scale. Rank contributes more quality for assets closer to rank 1. The final result is bounded from 0 to 100.

This score is never passed into trade generation, ranking, risk sizing, paper trading, alerts, or execution.

CoinMarketCap helps users discover assets through trending markets, top gainers, top losers, and high-quality assets. SwiftChart then uses its existing analysis APIs to provide:

- Current market regime
- Nearest and major support
- Nearest and major resistance
- Active trade opportunities when the existing signal engine produces one

If SwiftChart does not currently track a discovered asset, the analysis route preserves its CoinMarketCap overview and clearly states that SwiftChart analysis is coming soon. This keeps discovery useful without pretending that aggregated CMC data is exchange-specific strategy data.

## AI Validation Context

The context layer translates market intelligence into an immediately understandable validation label:

- **High Quality Asset**: strong capitalization, volume, and rank
- **Established Market**: moderate-to-strong market depth and visibility
- **Lower Liquidity Risk**: weaker capitalization, volume, or rank

This validation answers “what kind of asset is behind this signal?” It does not answer “should SwiftChart generate this trade?” and cannot promote, reject, or reorder a signal.

## Discovery to Analysis Workflow

```text
CoinMarketCap Market Intelligence
        ↓
Discover trending or high-quality asset
        ↓
Open /analysis/[symbol]
        ↓
SwiftChart exchange-specific analysis
        ↓
Market regime + support/resistance
        ↓
Trade opportunity or no-opportunity state
```

CoinMarketCap discovers and contextualizes the asset. SwiftChart turns supported assets into actionable trading intelligence using its existing market-analysis and signal systems.

## Future BNB Chain Expansion

The isolated market-intelligence contract can later support BNB Chain-specific context without changing the core SwiftChart engine. Potential extensions include:

- BNB Chain token discovery and metadata
- DEX liquidity and pair-quality context
- On-chain volume and holder-distribution indicators
- BNB ecosystem trend dashboards
- Cross-checking centralized and decentralized market depth

These additions would remain downstream, optional, and informational, preserving Hyperliquid as the primary source for the existing signal workflow.
