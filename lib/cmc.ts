type CmcQuote = {
  market_cap?: number;
  volume_24h?: number;
  percent_change_24h?: number;
};

export type CmcAsset = {
  id: number;
  name: string;
  symbol: string;
  cmc_rank: number | null;
  quote?: { USD?: CmcQuote };
};

type CacheEntry<T> = { expiresAt: number; value: T };

const CMC_API_BASE = "https://pro-api.coinmarketcap.com";
const DEFAULT_TTL_MS = 5 * 60 * 1000;

export class CoinMarketCapClient {
  private readonly apiKey: string;
  private readonly cacheTtlMs: number;
  private readonly cache = new Map<string, CacheEntry<unknown>>();

  constructor(apiKey = process.env.CMC_API_KEY || "", cacheTtlMs = DEFAULT_TTL_MS) {
    this.apiKey = apiKey;
    this.cacheTtlMs = cacheTtlMs;
  }

  private async get<T>(path: string, params: Record<string, string | number>): Promise<T | null> {
    if (!this.apiKey) return null;
    const query = new URLSearchParams(
      Object.entries(params).map(([key, value]) => [key, String(value)]),
    );
    const cacheKey = `${path}?${query}`;
    const cached = this.cache.get(cacheKey) as CacheEntry<T> | undefined;
    if (cached && cached.expiresAt > Date.now()) return cached.value;

    try {
      const response = await fetch(`${CMC_API_BASE}${path}?${query}`, {
        headers: {
          Accept: "application/json",
          "X-CMC_PRO_API_KEY": this.apiKey,
        },
        signal: AbortSignal.timeout(8_000),
      });
      if (!response.ok) return null;
      const payload = await response.json() as { data: T };
      this.cache.set(cacheKey, { value: payload.data, expiresAt: Date.now() + this.cacheTtlMs });
      return payload.data;
    } catch {
      return null;
    }
  }

  quotes(symbols: string[]) {
    return this.get<Record<string, CmcAsset[]>>("/v2/cryptocurrency/quotes/latest", {
      symbol: symbols.join(","),
      convert: "USD",
      skip_invalid: "true",
    });
  }

  listings(limit = 100) {
    return this.get<CmcAsset[]>("/v1/cryptocurrency/listings/latest", {
      start: 1,
      limit,
      convert: "USD",
      sort: "market_cap",
      sort_dir: "desc",
    });
  }

  trending(limit = 10) {
    return this.get<CmcAsset[]>("/v1/cryptocurrency/trending/latest", {
      start: 1,
      limit,
      convert: "USD",
    });
  }

  gainersAndLosers(limit = 20) {
    return this.get<CmcAsset[]>("/v1/cryptocurrency/trending/gainers-losers", {
      start: 1,
      limit,
      convert: "USD",
      time_period: "24h",
    });
  }
}
