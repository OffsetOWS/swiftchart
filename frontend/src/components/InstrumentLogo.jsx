import TokenAAVE from "@web3icons/react/icons/tokens/TokenAAVE";
import TokenAVAX from "@web3icons/react/icons/tokens/TokenAVAX";
import TokenBTC from "@web3icons/react/icons/tokens/TokenBTC";
import TokenDOGE from "@web3icons/react/icons/tokens/TokenDOGE";
import TokenETH from "@web3icons/react/icons/tokens/TokenETH";
import TokenHYPE from "@web3icons/react/icons/tokens/TokenHYPE";
import TokenINJ from "@web3icons/react/icons/tokens/TokenINJ";
import TokenLINK from "@web3icons/react/icons/tokens/TokenLINK";
import TokenSOL from "@web3icons/react/icons/tokens/TokenSOL";
import TokenSUI from "@web3icons/react/icons/tokens/TokenSUI";
import {
  inferMarketType,
  instrumentInitials,
  normalizeCryptoSymbol,
  normalizeForexPair,
} from "../lib/instruments.js";

const CRYPTO_LOGOS = new Map([
  ["AAVE", TokenAAVE],
  ["AVAX", TokenAVAX],
  ["BTC", TokenBTC],
  ["DOGE", TokenDOGE],
  ["ETH", TokenETH],
  ["HYPE", TokenHYPE],
  ["INJ", TokenINJ],
  ["LINK", TokenLINK],
  ["SOL", TokenSOL],
  ["SUI", TokenSUI],
]);

const CURRENCY_FLAGS = {
  AUD: "🇦🇺",
  CAD: "🇨🇦",
  CHF: "🇨🇭",
  EUR: "🇪🇺",
  GBP: "🇬🇧",
  JPY: "🇯🇵",
  NZD: "🇳🇿",
  USD: "🇺🇸",
  XAG: "Ag",
  XAU: "Au",
};

function ForexIdentity({ symbol }) {
  const { base, quote } = normalizeForexPair(symbol);
  const baseFlag = CURRENCY_FLAGS[base];
  const quoteFlag = CURRENCY_FLAGS[quote];

  if (!baseFlag || !quoteFlag) {
    return <span className="instrument-logo-fallback forex">{instrumentInitials(symbol, "forex")}</span>;
  }

  return (
    <span className="instrument-logo-flags" aria-hidden="true">
      <i data-currency={base}>{baseFlag}</i>
      <i data-currency={quote}>{quoteFlag}</i>
    </span>
  );
}

export default function InstrumentLogo({ symbol, marketType, size = 42, className = "" }) {
  const resolvedMarket = inferMarketType(symbol, marketType);
  const token = normalizeCryptoSymbol(symbol);
  const Logo = resolvedMarket === "crypto" ? CRYPTO_LOGOS.get(token) : null;
  const classes = [
    "instrument-logo",
    `instrument-logo-${resolvedMarket}`,
    Logo ? "has-logo" : "has-fallback",
    className,
  ].filter(Boolean).join(" ");

  return (
    <span
      className={classes}
      style={{ "--instrument-logo-size": `${Number(size) || 42}px` }}
      data-token={resolvedMarket === "crypto" ? token : undefined}
      data-market={resolvedMarket}
      aria-label={`${symbol} ${resolvedMarket === "forex" ? "currency pair" : "asset"} logo`}
    >
      {resolvedMarket === "forex" ? <ForexIdentity symbol={symbol} /> : null}
      {resolvedMarket === "crypto" && Logo ? <Logo variant="background" size="100%" aria-hidden="true" /> : null}
      {resolvedMarket === "crypto" && !Logo ? <span className="instrument-logo-fallback">{instrumentInitials(symbol, "crypto")}</span> : null}
    </span>
  );
}
