import { useEffect, useMemo, useState } from "react";
import { getMarketIntelligence } from "./api.js";
import { baseAssetSymbol, EMPTY_MARKET_INTELLIGENCE } from "./marketIntelligence.js";

const responseCache = new Map();

export default function useMarketIntelligence(symbols = []) {
  const symbolKey = useMemo(
    () => [...new Set(symbols.map(baseAssetSymbol).filter(Boolean))].sort().join(","),
    [symbols],
  );
  const [data, setData] = useState(() => responseCache.get(symbolKey) || EMPTY_MARKET_INTELLIGENCE);
  const [loading, setLoading] = useState(!responseCache.has(symbolKey));

  useEffect(() => {
    let cancelled = false;
    const cached = responseCache.get(symbolKey);
    if (cached) {
      setData(cached);
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    setLoading(true);
    getMarketIntelligence(symbolKey ? symbolKey.split(",") : [])
      .then((response) => {
        if (cancelled) return;
        const normalized = { ...EMPTY_MARKET_INTELLIGENCE, ...response };
        responseCache.set(symbolKey, normalized);
        setData(normalized);
      })
      .catch(() => {
        if (!cancelled) setData(EMPTY_MARKET_INTELLIGENCE);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [symbolKey]);

  return { data, loading };
}
