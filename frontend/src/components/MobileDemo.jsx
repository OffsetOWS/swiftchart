import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bell,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  HelpCircle,
  History,
  House,
  LineChart,
  LogOut,
  MessageCircle,
  ScanLine,
  Search,
  Settings,
  Target,
  TrendingDown,
  TrendingUp,
  User,
  UserRound,
  X,
} from "lucide-react";
import { useAuth } from "../lib/AuthContext.jsx";
import { getForexOverview, getForexSignals, scanForex } from "../lib/api.js";
import { MARKET_TYPES, marketFromSearch, normalizeMarket } from "../lib/markets.js";
import { formatSessionCountdown, formatUtcClock, getForexSessionState } from "../lib/forexSessions.js";
import { createPaperTradeFromSignal, listPaperTrades, signalIdForIdea } from "../lib/paperTrades.js";
import { useNotifications } from "../lib/notifications.js";
import { useAppPreferences } from "../lib/appPreferences.js";
import AppSplashScreen from "./AppSplashScreen.jsx";
import InstrumentLogo from "./InstrumentLogo.jsx";
import NotificationCenter from "./NotificationCenter.jsx";
import SettingsSupport from "./SettingsSupport.jsx";
import { normalizeCryptoSymbol } from "../lib/instruments.js";

const APP_SPLASH_SESSION_KEY = "swiftchart.appSplashSeen.v1";
const TELEGRAM_BOT_URL = import.meta.env.VITE_TELEGRAM_BOT_URL || "https://t.me/SwiftChartBot";

function hasSeenAppSplash() {
  try {
    return window.sessionStorage?.getItem(APP_SPLASH_SESSION_KEY) === "true";
  } catch {
    return false;
  }
}

const mockSignals = [
  { rank: 1, symbol: "BTCUSDT", direction: "Long", timeframe: "4h", score: 97, tier: "Elite", entry: "104,200 - 104,850", tp: "108,900", sl: "102,760", rr: "3.1R", confidence: "Very high", regime: "Bullish continuation", thesis: "Liquidity swept below range. Momentum reclaimed. Clean continuation if BTC holds above entry." },
  { rank: 2, symbol: "HYPEUSDT", direction: "Short", timeframe: "4h", score: 92, tier: "High Conviction", entry: "41.90 - 42.30", tp: "38.70", sl: "43.10", rr: "3.4R", confidence: "High", regime: "Bearish retest", thesis: "Weak retest into supply. Sellers still defending the prior breakdown." },
  { rank: 3, symbol: "ETHUSDT", direction: "Long", timeframe: "6h", score: 85, tier: "Strong", entry: "3,520 - 3,555", tp: "3,710", sl: "3,470", rr: "2.6R", confidence: "High", regime: "Trend continuation", thesis: "ETH is holding trend structure. Needs BTC strength to confirm." },
  { rank: 4, symbol: "SOLUSDT", direction: "Long", timeframe: "4h", score: 72, tier: "Watchlist", entry: "142.20 - 144.10", tp: "149.80", sl: "139.70", rr: "2.8R", confidence: "Medium", regime: "Selective bullish", thesis: "Clean structure, lower conviction until volume expands." },
  { rank: 5, symbol: "AVAXUSDT", direction: "Short", timeframe: "1h", score: 69, tier: "Watchlist", entry: "31.80 - 32.10", tp: "30.20", sl: "32.70", rr: "1.9R", confidence: "Medium", regime: "Momentum fade", thesis: "Momentum is fading, but risk/reward is still developing." },
  { rank: 6, symbol: "LINKUSDT", direction: "Long", timeframe: "2h", score: 71, tier: "Watchlist", entry: "14.12 - 14.28", tp: "15.10", sl: "13.78", rr: "2.1R", confidence: "Medium", regime: "Range reclaim", thesis: "Reclaimed prior range. Needs a strong close above entry." },
  { rank: 7, symbol: "INJUSDT", direction: "Long", timeframe: "4h", score: 74, tier: "Watchlist", entry: "24.40 - 24.85", tp: "26.90", sl: "23.70", rr: "2.4R", confidence: "Medium", regime: "Bullish rotation", thesis: "Relative strength is improving while majors hold structure." },
  { rank: 8, symbol: "DOGEUSDT", direction: "Short", timeframe: "1h", score: 67, tier: "Watchlist", entry: "0.143 - 0.145", tp: "0.136", sl: "0.148", rr: "1.7R", confidence: "Low-medium", regime: "Weak bounce", thesis: "Weak bounce into resistance. Lower score due to choppy volume." },
  { rank: 9, symbol: "SUIUSDT", direction: "Long", timeframe: "6h", score: 88, tier: "Strong", entry: "3.42 - 3.49", tp: "3.82", sl: "3.28", rr: "2.5R", confidence: "High", regime: "Trend expansion", thesis: "Expansion structure is clean with momentum aligned above the trigger zone." },
  { rank: 10, symbol: "AAVEUSDT", direction: "Long", timeframe: "4h", score: 73, tier: "Watchlist", entry: "286 - 292", tp: "312", sl: "276", rr: "2.0R", confidence: "Medium", regime: "Demand hold", thesis: "Demand is holding. Watch for a clean trigger." },
];

const mockHistory = [
  { symbol: "INJUSDT", score: 74, result: "Win", state: "Closed", r: "+1.8R", date: "Today" },
  { symbol: "AVAXUSDT", score: 69, result: "Open", state: "Open", r: "Tracking", date: "Yesterday" },
  { symbol: "LINKUSDT", score: 66, result: "Loss", state: "Closed", r: "-1.0R", date: "Jun 13" },
  { symbol: "SOLUSDT", score: 72, result: "Win", state: "Closed", r: "+1.2R", date: "Jun 12" },
];

const mockForexSignals = [
  { market: "forex", symbol: "EURUSD", pair: "EUR/USD", direction: "Buy", timeframe: "15m", score: 84, grade: "A", entry: "1.0843 - 1.0847", sl: "1.0812", tp: "1.0881", tp2: "1.0915", rr: "2.1R", session: "London-New York overlap", spread: "SAFE", newsRisk: "LOW", status: "Open", confidence: "High", regime: "Bullish continuation", thesis: "London overlap breakout with momentum confirmation.", reasons: ["HTF trend aligned", "Spread safe", "Session active"], createdAt: "Now" },
  { market: "forex", symbol: "GBPUSD", pair: "GBP/USD", direction: "Sell", timeframe: "1h", score: 79, grade: "B", entry: "1.2702 - 1.2706", sl: "1.2734", tp: "1.2651", tp2: "1.2620", rr: "2.0R", session: "London", spread: "SAFE", newsRisk: "LOW", status: "Open", confidence: "Medium", regime: "Bearish retest", thesis: "Supply retest with weak continuation candles.", reasons: ["1H structure bearish", "Location near resistance"], createdAt: "12m ago" },
  { market: "forex", symbol: "USDJPY", pair: "USD/JPY", direction: "Buy", timeframe: "30m", score: 72, grade: "B", entry: "158.24 - 158.30", sl: "157.96", tp: "158.74", tp2: "158.96", rr: "2.3R", session: "Tokyo", spread: "UNKNOWN", newsRisk: "LOW", status: "Wait", confidence: "Medium", regime: "Range reclaim", thesis: "Waiting for Tokyo continuation above range.", reasons: ["Pre-session bias", "JPY pair pip sizing"], createdAt: "24m ago" },
];

const forexHistory = [
  { symbol: "EURUSD", score: 84, result: "Open", state: "Open", r: "Tracking", date: "Today", market: "forex" },
  { symbol: "GBPUSD", score: 79, result: "TP1", state: "TP1 hit", r: "+1.0R", date: "Yesterday", market: "forex" },
  { symbol: "USDJPY", score: 72, result: "Expired", state: "Expired", r: "0R", date: "Jul 12", market: "forex" },
];

function scoreTier(score) {
  if (score >= 95) return "Elite";
  if (score >= 90) return "High Conviction";
  if (score >= 80) return "Strong";
  return "Watchlist";
}

function normalizeIdea(idea, index) {
  const fallback = mockSignals[index] || mockSignals[3];
  const score = Math.round(Number(idea?.setup_score || idea?.confidence_score || fallback.score));
  const entryZone = Array.isArray(idea?.entry_zone) ? idea.entry_zone : [];
  return {
    ...fallback,
    rawIdea: idea,
    rank: index + 1,
    symbol: idea?.symbol || fallback.symbol,
    direction: idea?.direction || fallback.direction,
    timeframe: idea?.timeframe || fallback.timeframe,
    score,
    tier: scoreTier(score),
    entry: entryZone.length >= 2 ? `${Number(entryZone[0]).toLocaleString()} - ${Number(entryZone[1]).toLocaleString()}` : fallback.entry,
    sl: idea?.stop_loss !== undefined ? Number(idea.stop_loss).toLocaleString() : fallback.sl,
    tp: idea?.take_profit_1 !== undefined ? Number(idea.take_profit_1).toLocaleString() : fallback.tp,
    tp2: idea?.take_profit_2 !== undefined ? Number(idea.take_profit_2).toLocaleString() : fallback.tp2,
    rr: idea?.risk_reward_ratio !== undefined ? `${Number(idea.risk_reward_ratio).toFixed(1)}R` : fallback.rr,
    confidence: idea?.confidence_score !== undefined ? `${Math.round(Number(idea.confidence_score))}%` : fallback.confidence,
    regime: idea?.regime_label || idea?.market_regime || fallback.regime,
    thesis: idea?.reason || fallback.thesis,
  };
}

function numbersFromDisplay(value) {
  return (String(value || "").replace(/,/g, "").match(/\d*\.?\d+/g) || [])
    .map(Number)
    .filter(Number.isFinite);
}

function paperTradeIdeaForSignal(signal) {
  if (signal.rawIdea) return signal.rawIdea;
  const entryValues = numbersFromDisplay(signal.entry);
  const stopLoss = numbersFromDisplay(signal.sl)[0];
  const takeProfit = numbersFromDisplay(signal.tp)[0];
  const takeProfit2 = numbersFromDisplay(signal.tp2)[0] ?? takeProfit;
  const riskReward = numbersFromDisplay(signal.rr)[0];

  return {
    symbol: signal.symbol,
    timeframe: signal.timeframe,
    exchange: signal.exchange || "hyperliquid",
    source: "mobile-demo",
    direction: /short|sell/i.test(signal.direction) ? "Short" : "Long",
    entry_zone: [entryValues[0], entryValues[1] ?? entryValues[0]],
    stop_loss: stopLoss,
    take_profit_1: takeProfit,
    take_profit_2: takeProfit2,
    risk_reward_ratio: riskReward,
    setup_score: signal.score,
    confidence_score: signal.score,
    market_regime: signal.regime,
    reason: signal.thesis,
  };
}

function fmt(value, digits = 5) {
  if (value === undefined || value === null || value === "") return "-";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return numeric.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function dt(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function slashPair(symbol) {
  const clean = String(symbol || "").replace("/", "").toUpperCase();
  if (clean.length === 6) return `${clean.slice(0, 3)}/${clean.slice(3)}`;
  if (clean === "XAUUSD") return "XAU/USD";
  return clean;
}

function normalizeForexSignal(signal, index = 0) {
  const fallback = mockForexSignals[index] || mockForexSignals[0];
  const pair = signal?.pair || signal?.symbol || fallback.symbol;
  const direction = String(signal?.direction || fallback.direction).toUpperCase();
  const score = Math.round(Number(signal?.score ?? fallback.score));
  const rr = signal?.rr ? `${signal.rr}R` : fallback.rr;
  return {
    ...fallback,
    ...signal,
    market: MARKET_TYPES.forex,
    symbol: String(pair).replace("/", "").toUpperCase(),
    pair: slashPair(pair),
    direction: direction === "SHORT" || direction === "SELL" ? "Sell" : direction === "WAIT" ? "Wait" : "Buy",
    score,
    grade: signal?.grade || fallback.grade,
    timeframe: signal?.timeframe || fallback.timeframe,
    entry: signal?.entry ? fmt(signal.entry) : fallback.entry,
    sl: signal?.stopLoss ? fmt(signal.stopLoss) : fallback.sl,
    tp: signal?.tp1 ? fmt(signal.tp1) : fallback.tp,
    tp2: signal?.tp2 ? fmt(signal.tp2) : fallback.tp2,
    rr,
    session: signal?.session || fallback.session,
    spread: signal?.spreadStatus || fallback.spread,
    newsRisk: signal?.newsRisk || fallback.newsRisk,
    status: signal?.status || fallback.status,
    regime: signal?.pre_session_bias || fallback.regime,
    thesis: signal?.reason || fallback.thesis,
    reasons: signal?.reason ? [signal.reason] : fallback.reasons,
    createdAt: signal?.lastUpdated ? dt(signal.lastUpdated) : fallback.createdAt,
    updatedAt: signal?.lastUpdated ? dt(signal.lastUpdated) : undefined,
  };
}

function tokenSymbol(symbol) {
  return normalizeCryptoSymbol(symbol);
}

function ScoreBadge({ score }) {
  return <span className={`graphite-score ${score >= 90 ? "elite" : score > 75 ? "pro" : ""}`}>{score}</span>;
}

function ScoreRing({ score }) {
  const normalizedScore = Math.min(100, Math.max(0, Number(score) || 0));
  const tone = normalizedScore >= 90 ? "elite" : normalizedScore > 75 ? "pro" : "watch";
  return (
    <span
      className={`graphite-score-ring ${tone}`}
      style={{ "--score-angle": `${normalizedScore * 3.6}deg` }}
      aria-label={`${Math.round(normalizedScore)} confidence score`}
    >
      <strong>{Math.round(normalizedScore)}</strong>
    </span>
  );
}

function Direction({ direction }) {
  const side = /short|sell/i.test(direction) ? "short" : "long";
  return <span className={`graphite-side ${side}`}>{direction}</span>;
}

function statusForSignal(signal) {
  if (signal.market === MARKET_TYPES.forex) return signal.score >= 80 ? signal.grade || "A" : signal.status || "Open";
  if (signal.score >= 90) return "High Conviction";
  if (signal.score > 75) return signal.tier || scoreTier(signal.score);
  return "Open";
}

function MarketToggle({ activeMarket, onChange }) {
  const forexActive = activeMarket === MARKET_TYPES.forex;

  return (
    <div className="graphite-market-toggle" aria-label="Market type">
      <button
        type="button"
        className={!forexActive ? "active" : ""}
        aria-pressed={!forexActive}
        onClick={() => onChange(MARKET_TYPES.crypto)}
      >
        Crypto
      </button>
      <button
        type="button"
        className={forexActive ? "active" : ""}
        aria-pressed={forexActive}
        onClick={() => onChange(MARKET_TYPES.forex)}
      >
        Forex
      </button>
    </div>
  );
}

function GraphiteHeader({
  title,
  activeMarket,
  onMarketChange,
  showMarketToggle = false,
  onOpenNotifications,
  unreadCount = 0,
  notificationPriority = "",
  notificationsOpen = false,
}) {
  const countLabel = unreadCount > 99 ? "99+" : String(unreadCount);
  return (
    <header className="graphite-header">
      <div className="graphite-brand">
        <div>
        <span>SwiftChart</span>
        {title ? <strong>{title}</strong> : null}
        </div>
      </div>
      <div className="graphite-header-actions">
        {showMarketToggle ? <MarketToggle activeMarket={activeMarket} onChange={onMarketChange} /> : null}
        <button
          type="button"
          className="graphite-alert-button"
          onClick={onOpenNotifications}
          aria-label={`Notifications${unreadCount ? `, ${countLabel} unread` : ""}`}
          aria-expanded={notificationsOpen}
          aria-controls="swiftchart-notification-panel"
        >
          <Bell size={18} />
          {unreadCount ? <span className="graphite-alert-count">{countLabel}</span> : null}
          {notificationPriority ? <i className={`graphite-alert-priority ${notificationPriority}`} aria-hidden="true" /> : null}
        </button>
      </div>
    </header>
  );
}

function SparkChart() {
  return (
    <div className="graphite-chart" aria-hidden="true">
      <span className="bar a" />
      <span className="bar b" />
      <span className="bar c" />
      <span className="bar d" />
      <span className="bar e" />
      <span className="line" />
    </div>
  );
}

function SignalRow({ signal, onSelect, compact = false }) {
  return (
    <button type="button" className={`graphite-row ${compact ? "compact" : ""}`} onClick={() => onSelect(signal)}>
      <InstrumentLogo symbol={signal.symbol} marketType={signal.market} />
      <div>
        <strong>{signal.symbol}</strong>
        <span><Direction direction={signal.direction} /> {signal.timeframe.toUpperCase()} · {signal.rr}</span>
      </div>
      <ScoreBadge score={signal.score} />
      <em>{statusForSignal(signal)}</em>
      <ChevronRight className="graphite-row-chevron" size={18} aria-hidden="true" />
    </button>
  );
}

function StatTile({ label, value, tone }) {
  return (
    <article className={`graphite-stat ${tone || ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function EmptyState({ title, message, action, onAction }) {
  return (
    <section className="graphite-empty-state">
      <AlertTriangleIcon />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
      {action ? <button type="button" onClick={onAction}>{action}</button> : null}
    </section>
  );
}

function AlertTriangleIcon() {
  return <Target size={18} />;
}

function HomeScreen({ signals, onSelect, onViewAll, market, forexSignals, forexOverview }) {
  if (market === MARKET_TYPES.forex) {
    return <ForexHomeScreen signals={forexSignals} overview={forexOverview} onSelect={onSelect} />;
  }
  const best = signals[0];
  const bullish = !/short|sell/i.test(best.direction);
  return (
    <div className="graphite-screen">
      <section className="graphite-hero-card">
        <div className="graphite-hero-copy">
          <span>Market Summary</span>
          <h1>{best.score}</h1>
          <p>{best.symbol} is the strongest ranked setup right now.</p>
          <span className={`graphite-bias-pill ${bullish ? "bullish" : "bearish"}`}>
            {bullish ? <TrendingUp size={15} /> : <TrendingDown size={15} />}
            {bullish ? "Bullish momentum" : "Bearish momentum"}
          </span>
        </div>
        <SparkChart />
      </section>

      <section className="graphite-regime-card">
        <div>
          <span>Market Regime</span>
          <strong>{bullish ? "Bullish, selective" : "Bearish, selective"}</strong>
          <p>{bullish ? "Momentum favors longs." : "Momentum favors shorts."} Weak mid-range setups are filtered out.</p>
        </div>
        <LineChart size={24} />
      </section>

      <section className="graphite-section">
        <div className="graphite-section-head">
          <span>Top opportunities</span>
          <button type="button" onClick={onViewAll}>View All <ChevronRight size={15} /></button>
        </div>
        <div className="graphite-stack graphite-signal-list">
          {signals.slice(0, 4).map((signal) => <SignalRow key={`${signal.symbol}-home`} signal={signal} onSelect={onSelect} />)}
        </div>
      </section>

    </div>
  );
}

function ForexHomeScreen({ signals, overview, onSelect }) {
  const [sessionClock, setSessionClock] = useState(() => new Date());
  const session = useMemo(() => getForexSessionState(sessionClock), [sessionClock]);
  const top = signals.length ? signals : mockForexSignals;

  useEffect(() => {
    const timer = window.setInterval(() => setSessionClock(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="graphite-screen forex-home">
      <section className="graphite-hero-card forex">
        <div className="graphite-hero-copy">
          <span>Forex Market</span>
          <p>Live Forex sessions ranked against current market time.</p>
        </div>
        <div className="graphite-session-stack">
          <span>{session.marketOpen ? "Live session" : "Market status"}</span>
          <strong>{session.displayName}</strong>
          <p>Next: {session.nextSession} in {formatSessionCountdown(session.minutesUntilNext)}</p>
          <p>{formatUtcClock(sessionClock)} UTC</p>
        </div>
      </section>

      {overview?.message ? <EmptyState title="Forex status" message={overview.message} /> : null}

      <section className="graphite-section">
        <div className="graphite-section-head">
          <span>Top Forex setups</span>
        </div>
        <div className="graphite-stack graphite-signal-list">
          {top.slice(0, 5).map((signal) => <SignalRow key={`${signal.symbol}-forex-home`} signal={signal} onSelect={onSelect} />)}
        </div>
      </section>

    </div>
  );
}

function ScanScreen({ signals, onSelect, market, forexSignals, onForexScan, scanningForex, forexError, tradingPreferences }) {
  const [scanState, setScanState] = useState("idle");
  const [forexPair, setForexPair] = useState("EURUSD");
  const forexTimeframe = "15m";
  const [coinQuery, setCoinQuery] = useState("");
  const [appliedCoinQuery, setAppliedCoinQuery] = useState("");
  const [scoreBand, setScoreBand] = useState("all");
  const [timeframe, setTimeframe] = useState("all");
  const [exchange, setExchange] = useState(() => tradingPreferences.preferredExchange);
  const visibleSignals = useMemo(() => {
    const query = tokenSymbol(appliedCoinQuery);
    const [minimumScore, maximumScore] = scoreBand === "all" ? [0, 100] : scoreBand.split("-").map(Number);
    return signals.filter((signal) => {
      if (query && !tokenSymbol(signal.symbol).includes(query)) return false;
      if (signal.score < minimumScore || signal.score > maximumScore) return false;
      if (timeframe !== "all" && signal.timeframe.toLowerCase() !== timeframe) return false;
      if (exchange !== "all" && signal.exchange && signal.exchange !== exchange) return false;
      return true;
    });
  }, [appliedCoinQuery, exchange, scoreBand, signals, timeframe]);

  function startScan() {
    setAppliedCoinQuery("");
    setScanState("scanning");
    window.setTimeout(() => setScanState("done"), 1600);
  }

  function scanCoin(event) {
    event?.preventDefault();
    setAppliedCoinQuery(coinQuery.trim());
    setScanState("done");
  }

  if (market === MARKET_TYPES.forex) {
    return (
      <div className="graphite-screen">
        <section className="graphite-scan-card forex">
          <div>
            <span>Forex Scan</span>
            <h1>Session filter</h1>
            <p>Validate pair, spread, session, score, and news risk before ranking setups.</p>
          </div>
          <button type="button" onClick={() => onForexScan({ pair: forexPair, timeframe: forexTimeframe })} disabled={scanningForex}><ScanLine size={18} /> {scanningForex ? "Scanning..." : "Scan Forex"}</button>
        </section>

        <div className="graphite-search">
          <Search size={17} />
          <input type="text" value={forexPair} onChange={(event) => setForexPair(event.target.value.toUpperCase().replace(/[^A-Z/]/g, ""))} placeholder="Search pair (EURUSD, GBPUSD...)" aria-label="Search forex pair" />
          <button type="button" onClick={() => onForexScan({ pair: forexPair, timeframe: forexTimeframe })}>Scan</button>
        </div>

        {forexError ? <EmptyState title="Scan failed" message={forexError} action="Retry" onAction={() => onForexScan({ pair: forexPair, timeframe: forexTimeframe })} /> : null}

        <section className="graphite-section">
          <div className="graphite-section-head">
            <span>Forex results</span>
          </div>
          <div className="graphite-stack graphite-signal-list compact">
            {(forexSignals.length ? forexSignals : mockForexSignals).map((signal) => <SignalRow key={`${signal.symbol}-forex-scan`} signal={signal} onSelect={onSelect} compact />)}
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="graphite-screen">
      <section className="graphite-scan-card">
        <div>
          <span>Scan</span>
          <h1>Find clean setups</h1>
          <p>Search one coin or rank the market in seconds.</p>
        </div>
        <button type="button" onClick={startScan}><ScanLine size={18} /> Scan Market</button>
      </section>

      <form className="graphite-search" onSubmit={scanCoin}>
        <Search size={17} />
        <input value={coinQuery} onChange={(event) => setCoinQuery(event.target.value)} type="text" placeholder="Search coin (BTC, ETH, SOL...)" aria-label="Search coin" />
        <button type="submit">Scan</button>
      </form>

      <div className="graphite-filters" aria-label="Scan filters">
        <label>
          <span className="sr-only">Score range</span>
          <select value={scoreBand} onChange={(event) => setScoreBand(event.target.value)} aria-label="Score range">
            <option value="all">All Scores</option>
            <option value="65-75">65-75</option>
            <option value="75-90">75-90</option>
            <option value="90-100">90-100</option>
          </select>
          <ChevronDown size={14} aria-hidden="true" />
        </label>
        <label>
          <span className="sr-only">Timeframe</span>
          <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)} aria-label="Timeframe">
            <option value="all">All Timeframes</option>
            {["15m", "1h", "2h", "4h", "6h", "1d"].map((value) => <option key={value} value={value}>{value.toUpperCase()}</option>)}
          </select>
          <ChevronDown size={14} aria-hidden="true" />
        </label>
        <label>
          <span className="sr-only">Exchange</span>
          <select value={exchange} onChange={(event) => setExchange(event.target.value)} aria-label="Exchange">
            <option value="all">All Exchanges</option>
            <option value="hyperliquid">Hyperliquid</option>
            <option value="variational">Variational</option>
          </select>
          <ChevronDown size={14} aria-hidden="true" />
        </label>
      </div>

      <section className="graphite-section">
        <div className="graphite-section-head">
          <span>Scan results</span>
          <strong className="graphite-results-status">{scanState === "scanning" ? "Analyzing..." : "Updated just now"}<i /></strong>
        </div>
        <div className="graphite-stack graphite-signal-list compact">
          {visibleSignals.map((signal) => <SignalRow key={`${signal.symbol}-scan`} signal={signal} onSelect={onSelect} compact />)}
          {visibleSignals.length === 0 ? <p className="graphite-no-results">No matching setup is currently ranked.</p> : null}
        </div>
      </section>
    </div>
  );
}

function ExecutionLadder({ signal }) {
  return (
    <section className="graphite-execution-ladder" aria-label="Execution ladder">
      <span className="graphite-ladder-title">Execution ladder</span>
      <div className="graphite-ladder-rail" aria-hidden="true">
        <i className="target" />
        <i className="entry" />
        <i className="stop" />
      </div>
      <div className="graphite-ladder-values">
        <div className="target">
          <span>TP1</span>
          <strong>{signal.tp}</strong>
        </div>
        <div className="entry">
          <span>Entry zone</span>
          <strong>{signal.entry}</strong>
        </div>
        <div className="stop">
          <span>Stop loss</span>
          <strong>{signal.sl}</strong>
        </div>
      </div>
    </section>
  );
}

function sentenceCase(value) {
  const text = String(value || "").trim().replace(/[.!?]+$/, "");
  return text ? `${text.charAt(0).toLowerCase()}${text.slice(1)}` : "market structure supports the setup";
}

function tradeReasoning(signal, { isForex }) {
  const direction = String(signal.direction || "trade").toLowerCase();
  const timeframe = String(signal.timeframe || "").toUpperCase();
  const raw = signal.rawIdea || {};

  if (isForex) {
    const confirmations = signal.reasons?.length ? signal.reasons.join(", ") : signal.thesis;
    return `${signal.pair || signal.symbol} was surfaced as a ${direction} setup on ${timeframe} because ${sentenceCase(confirmations)}. The ${signal.session || "active"} session and ${String(signal.spread || "unknown").toLowerCase()} spread were checked, with ${String(signal.newsRisk || "unknown").toLowerCase()} news risk. Entry is ${signal.entry}, targets are ${signal.tp}${signal.tp2 ? ` and ${signal.tp2}` : ""}, and ${signal.sl} invalidates the idea.`;
  }

  const setup = `${signal.symbol} was surfaced as a ${direction} setup on ${timeframe} because ${sentenceCase(signal.thesis)}`;
  const confirmations = Array.isArray(raw.reversal_confirmations) && raw.reversal_confirmations.length
    ? ` Confirmations include ${raw.reversal_confirmations.join(", ")}.`
    : "";
  const execution = ` The ${signal.entry} zone is the decision area; confirmation favors a move toward ${signal.tp}, while ${signal.sl} invalidates the setup.`;
  const quality = signal.score < 75
    ? ` Its ${signal.score} score and ${signal.rr} profile keep it on the watchlist until ${direction === "short" ? "downside" : "upside"} confirmation improves.`
    : ` Its ${signal.score} score and ${signal.rr} profile place it in the ${String(signal.tier || "ranked").toLowerCase()} tier.`;
  const invalidation = raw.invalid_condition
    ? ` Backend invalidation: ${String(raw.invalid_condition).trim()}`
    : "";

  return `${setup}.${confirmations}${execution}${quality}${invalidation}`;
}

function TradeAnalysis({ signal, onClose, onTakeTrade, tradeSaveState }) {
  if (!signal) return null;
  const isForex = signal.market === MARKET_TYPES.forex;
  const activeTradeSaveState = tradeSaveState?.symbol === signal.symbol ? tradeSaveState : { status: "idle" };
  return (
    <div className="graphite-detail-backdrop" role="dialog" aria-modal="true" aria-label={`${signal.symbol} trade analysis`}>
      <article className="graphite-detail">
        <header>
          <InstrumentLogo symbol={signal.symbol} marketType={signal.market} />
          <div>
            <span>{isForex ? "Forex Trade Analysis" : "Trade Analysis"}</span>
            <strong>{isForex ? signal.pair : signal.symbol}</strong>
          </div>
          <ScoreRing score={signal.score} />
          <button type="button" className="graphite-close" onClick={onClose} aria-label="Close trade analysis"><X size={18} /></button>
        </header>

        <div className="graphite-trade-meta">
          <StatTile label="Direction" value={signal.direction} tone={/short|sell/i.test(signal.direction) ? "loss" : "profit"} />
          <StatTile label="Timeframe" value={signal.timeframe.toUpperCase()} />
          <StatTile label="R/R" value={signal.rr} tone="reward" />
        </div>

        <ExecutionLadder signal={signal} />

        <div className="graphite-context-pills" aria-label="Trade context">
          {!isForex ? <span>Context · {signal.regime}</span> : null}
          <span>{signal.timeframe.toUpperCase()} structure</span>
          <span>Confidence · {signal.confidence || signal.score}</span>
          {isForex ? <span>Session · {signal.session}</span> : null}
          {isForex && signal.tp2 ? <span>TP2 · {signal.tp2}</span> : null}
        </div>

        <section className="graphite-reasoning">
          <span>Reasoning</span>
          <p>{tradeReasoning(signal, { isForex })}</p>
        </section>

        <div className="graphite-detail-actions">
          <button type="button" className="ghost">{isForex ? "Open in MT5" : "View Chart"}</button>
          <button
            type="button"
            disabled={activeTradeSaveState.status === "saving" || activeTradeSaveState.status === "saved"}
            onClick={isForex ? undefined : () => onTakeTrade(signal)}
          >
            {isForex ? "Auto Trade" : activeTradeSaveState.status === "saving" ? "Saving to History..." : activeTradeSaveState.status === "saved" ? "Saved to History" : "Take Trade"}
          </button>
          {isForex ? <button type="button" className="ghost">Copy Setup</button> : null}
          {isForex ? <button type="button" className="ghost">Share Telegram</button> : null}
        </div>
        {activeTradeSaveState.status === "error" ? <p className="graphite-action-error" role="alert">{activeTradeSaveState.message}</p> : null}
      </article>
    </div>
  );
}

function HistoryScreen({ market, paperTrades = [] }) {
  const [marketFilter, setMarketFilter] = useState(market);
  const [statusFilter, setStatusFilter] = useState("all");
  useEffect(() => {
    setMarketFilter(market);
  }, [market]);
  const savedRows = paperTrades.map((trade) => ({
    symbol: trade.symbol,
    score: Math.round(Number(trade.confidence || trade.setup_score || 0)),
    result: trade.result || "Open",
    state: trade.status === "taken" ? "Open" : trade.status || "Open",
    r: trade.pnl === null || trade.pnl === undefined ? "Tracking" : `${Number(trade.pnl) >= 0 ? "+" : ""}${Number(trade.pnl).toFixed(1)}R`,
    date: trade.taken_at ? new Date(trade.taken_at).toLocaleDateString([], { month: "short", day: "numeric" }) : "Today",
    market: MARKET_TYPES.crypto,
    id: trade.id,
  }));
  const savedSymbols = new Set(savedRows.map((item) => item.symbol));
  const rows = [
    ...savedRows,
    ...mockHistory.filter((item) => !savedSymbols.has(item.symbol)).map((item) => ({ ...item, market: MARKET_TYPES.crypto })),
    ...forexHistory,
  ].filter((item) => (marketFilter === "all" ? true : item.market === marketFilter))
    .filter((item) => statusFilter === "all" || item.result.toLowerCase().includes(statusFilter));
  return (
    <div className="graphite-screen">
      <section className="graphite-history-hero">
        <span>{market === MARKET_TYPES.forex ? "Forex Total R" : "Total R"}</span>
        <strong>{market === MARKET_TYPES.forex ? "+1.0R" : "+2.6R"}</strong>
        <p>{rows.length} tracked ideas · market filter active</p>
      </section>

      <div className="graphite-filter-row">
        {["all", MARKET_TYPES.crypto, MARKET_TYPES.forex].map((option) => <button key={option} type="button" className={marketFilter === option ? "active" : ""} onClick={() => setMarketFilter(option)}>{option}</button>)}
      </div>
      <div className="graphite-filter-row">
        {["all", "open", "tp1", "tp2", "sl", "expired"].map((option) => <button key={option} type="button" className={statusFilter === option ? "active" : ""} onClick={() => setStatusFilter(option)}>{option}</button>)}
      </div>

      <section className="graphite-timeline">
        <span>Trade timeline</span>
        {rows.length === 0 ? <p>No {marketFilter} historical signals yet.</p> : null}
        {rows.map((item) => (
          <article key={item.id || `${item.symbol}-${item.date}`}>
            <div className="history-asset-visual">
              <InstrumentLogo symbol={item.symbol} marketType={item.market} size={34} />
              <ScoreBadge score={item.score} />
            </div>
            <div>
              <strong>{item.symbol}</strong>
              <p>{item.market.toUpperCase()} · {item.date} · {item.state}</p>
            </div>
            <em className={item.r.startsWith("-") ? "loss" : item.r === "Tracking" ? "" : "profit"}>{item.r}</em>
          </article>
        ))}
      </section>
    </div>
  );
}

function AccountScreen({ onSettings, onSupport }) {
  const auth = useAuth();
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [confirmLogout, setConfirmLogout] = useState(false);
  const name = auth.profile?.username || auth.user?.email?.split("@")[0] || "doflam";

  async function logout() {
    setLoggingOut(true);
    setLogoutError("");
    try {
      await auth.signOut();
      window.location.assign("/login");
    } catch {
      setLogoutError("Could not sign out. Please try again.");
      setLoggingOut(false);
    }
  }
  return (
    <div className="graphite-screen">
      <section className="graphite-profile">
        <div className="graphite-avatar"><User size={24} /></div>
        <div>
          <span>Profile</span>
          <strong>{name}</strong>
          <p>{auth.user?.email || "SwiftChart account"}</p>
        </div>
      </section>

      <div className="graphite-settings">
        <button type="button" onClick={() => window.open(TELEGRAM_BOT_URL, "_blank", "noopener,noreferrer")}><MessageCircle size={18} /><span>Telegram</span><CheckCircle2 size={16} /></button>
        <button type="button" onClick={onSettings}><Settings size={18} /><span>Settings</span><ChevronRight size={16} /></button>
        <button type="button" onClick={onSupport}><HelpCircle size={18} /><span>Support</span><ChevronRight size={16} /></button>
        <button type="button" onClick={() => setConfirmLogout(true)} disabled={loggingOut}><LogOut size={18} /><span>{loggingOut ? "Signing out..." : "Log out"}</span><ChevronRight size={16} /></button>
      </div>

      {logoutError ? <p className="graphite-inline-error" role="alert">{logoutError}</p> : null}
      {confirmLogout ? (
        <div className="settings-confirm-backdrop" role="dialog" aria-modal="true" aria-label="Log out">
          <section className="settings-confirm">
            <h2>Log out?</h2>
            <p>You will need to sign in again to access your SwiftChart account.</p>
            <div>
              <button type="button" onClick={() => setConfirmLogout(false)}>Cancel</button>
              <button type="button" className="danger" onClick={() => { setConfirmLogout(false); logout(); }}>Log Out</button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

const navItems = [
  ["home", "Home", House],
  ["scan", "Scan", ScanLine],
  ["history", "History", History],
  ["account", "Account", UserRound],
];

const validTabs = new Set(navItems.map(([key]) => key));

export default function MobileDemo({ topIdeas = [], initialConfigLoading = false, initialTab = "" }) {
  const auth = useAuth();
  const notificationCenter = useNotifications();
  const preferencesController = useAppPreferences(auth.user?.id);
  const { preferences } = preferencesController;
  const [showAppSplash, setShowAppSplash] = useState(() => !hasSeenAppSplash());
  const [marketPreferenceReady, setMarketPreferenceReady] = useState(false);
  const [activeMarket, setActiveMarketState] = useState(() => {
    const queryMarket = new URLSearchParams(window.location.search).get("market");
    if (queryMarket) return normalizeMarket(queryMarket);
    return normalizeMarket(window.localStorage?.getItem("swiftchart.activeMarket"));
  });
  const [tab, setTab] = useState(() => {
    if (validTabs.has(initialTab)) return initialTab;
    const pathTab = window.location.pathname.match(/^\/app\/(home|scan|history|account|notifications)$/i)?.[1]?.toLowerCase();
    if (validTabs.has(pathTab)) return pathTab;
    const requestedTab = new URLSearchParams(window.location.search).get("tab");
    return validTabs.has(requestedTab) ? requestedTab : "home";
  });
  const [notificationsOpen, setNotificationsOpen] = useState(
    () => initialTab === "notifications" || window.location.pathname === "/app/notifications",
  );
  const [selectedSignal, setSelectedSignal] = useState(null);
  const [accountView, setAccountView] = useState(() => {
    const requestedView = new URLSearchParams(window.location.search).get("view");
    return /^(settings(?:-[a-z]+)?|support(?:-[a-z]+)?)$/.test(requestedView || "") ? requestedView : "main";
  });
  const [forexOverview, setForexOverview] = useState(null);
  const [forexSignals, setForexSignals] = useState(mockForexSignals);
  const [forexPairs, setForexPairs] = useState([]);
  const [forexError, setForexError] = useState("");
  const [forexLoading, setForexLoading] = useState(false);
  const [paperTrades, setPaperTrades] = useState([]);
  const [tradeSaveState, setTradeSaveState] = useState({ signalId: "", status: "idle" });
  const signals = useMemo(() => {
    const mapped = topIdeas.slice(0, 10).map(normalizeIdea);
    return (mapped.length ? mapped : mockSignals)
      .sort((a, b) => b.score - a.score)
      .map((signal, index) => ({ ...signal, rank: index + 1 }));
  }, [topIdeas]);
  const preferredSignals = useMemo(() => {
    const settings = preferences.trading;
    return [...signals].sort((a, b) => {
      if (settings.defaultSorting === "highest_rr") return Number.parseFloat(b.rr) - Number.parseFloat(a.rr);
      if (settings.defaultSorting === "newest") {
        return new Date(b.rawIdea?.generated_at || b.rawIdea?.created_at || 0) - new Date(a.rawIdea?.generated_at || a.rawIdea?.created_at || 0);
      }
      return b.score - a.score;
    });
  }, [preferences.trading, signals]);
  const appInitializationReady = !auth.loading && !auth.profileLoading && marketPreferenceReady && !initialConfigLoading;

  const completeAppSplash = useCallback(() => {
    try {
      window.sessionStorage?.setItem(APP_SPLASH_SESSION_KEY, "true");
    } catch {
      // Session storage can be unavailable in strict privacy modes.
    }
    setShowAppSplash(false);
  }, []);

  function setActiveMarket(nextMarket) {
    const normalized = normalizeMarket(nextMarket);
    setActiveMarketState(normalized);
    window.localStorage?.setItem("swiftchart.activeMarket", normalized);
    const nextUrl = new URL(window.location.href);
    nextUrl.searchParams.set("market", normalized);
    window.history.replaceState({}, "", `${nextUrl.pathname}${nextUrl.search}`);
  }

  useEffect(() => {
    const requestedDetail = new URLSearchParams(window.location.search).get("detail");
    if (!requestedDetail || selectedSignal) return;
    const matchedSignal = [...signals, ...forexSignals].find((signal) => signal.symbol.toLowerCase() === requestedDetail.toLowerCase());
    if (matchedSignal) setSelectedSignal(matchedSignal);
  }, [forexSignals, selectedSignal, signals]);

  useEffect(() => {
    setMarketPreferenceReady(true);
  }, []);

  useEffect(() => {
    const nextUrl = new URL(window.location.href);
    const requestedView = nextUrl.searchParams.get("view");
    if (!/^(upgrade|payment|pricing|billing|subscription|admin-payment)$/i.test(requestedView || "")) return;
    nextUrl.pathname = "/app/account";
    nextUrl.searchParams.delete("view");
    nextUrl.searchParams.delete("plan");
    window.history.replaceState({}, "", `${nextUrl.pathname}${nextUrl.search}`);
    setTab("account");
    setAccountView("main");
  }, []);

  useEffect(() => {
    const hasMarketInUrl = new URLSearchParams(window.location.search).has("market");
    const hasRememberedMarket = Boolean(window.localStorage?.getItem("swiftchart.activeMarket"));
    if (!hasMarketInUrl && !hasRememberedMarket) {
      setActiveMarketState(normalizeMarket(preferences.trading.defaultMarket));
    }
  }, [preferences.trading.defaultMarket]);

  useEffect(() => {
    if (initialTab === "notifications") {
      setNotificationsOpen(true);
      return;
    }
    if (validTabs.has(initialTab)) {
      setTab(initialTab);
      setNotificationsOpen(false);
    }
  }, [initialTab]);

  useEffect(() => {
    if (!auth.user?.id) {
      setPaperTrades([]);
      return;
    }
    listPaperTrades(auth.user.id, auth.session?.access_token)
      .then(setPaperTrades)
      .catch(() => setPaperTrades([]));
  }, [auth.session?.access_token, auth.user?.id]);

  useEffect(() => {
    const onPopState = () => {
      setActiveMarketState(marketFromSearch(window.location.search));
      const pathTab = window.location.pathname.match(/^\/app\/(home|scan|history|account|notifications)$/i)?.[1]?.toLowerCase();
      const queryTab = new URLSearchParams(window.location.search).get("tab");
      if (pathTab === "notifications") {
        setNotificationsOpen(true);
        return;
      }
      const nextTab = validTabs.has(pathTab) ? pathTab : queryTab;
      if (validTabs.has(nextTab)) setTab(nextTab);
      const nextView = new URLSearchParams(window.location.search).get("view");
      setAccountView(/^(settings(?:-[a-z]+)?|support(?:-[a-z]+)?)$/.test(nextView || "") ? nextView : "main");
      setNotificationsOpen(false);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (!notificationsOpen) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === "Escape") closeNotifications();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [notificationsOpen]);

  useEffect(() => {
    if (activeMarket !== MARKET_TYPES.forex) return;
    let cancelled = false;
    setForexLoading(true);
    setForexError("");
    Promise.allSettled([getForexOverview(), getForexSignals()])
      .then(([overviewResult, signalsResult]) => {
        if (cancelled) return;
        if (overviewResult.status === "fulfilled") {
          setForexOverview(overviewResult.value);
          setForexPairs(overviewResult.value?.supportedPairs || []);
        }
        if (signalsResult.status === "fulfilled") {
          const data = signalsResult.value;
          const raw = data.topSetups?.length ? data.topSetups : data.signals || [];
          setForexSignals(raw.map(normalizeForexSignal));
          setForexPairs((current) => current.length ? current : data.supportedPairs || []);
        }
        const failed = [overviewResult, signalsResult].find((result) => result.status === "rejected");
        if (failed) setForexError(failed.reason?.message || "Could not load Forex data.");
      })
      .finally(() => {
        if (!cancelled) setForexLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeMarket]);

  async function runForexScan(payload = {}) {
    const pair = String(payload.pair || "").replace("/", "").toUpperCase();
    const timeframeValue = String(payload.timeframe || "15m").toLowerCase();
    if (!pair || pair.length < 6) {
      setForexError("Unsupported pair. Choose a valid Forex pair.");
      return;
    }
    if (!["15m", "30m", "1h", "2h", "4h", "6h", "1d"].includes(timeframeValue)) {
      setForexError("Unsupported timeframe for Forex scan.");
      return;
    }
    setForexLoading(true);
    setForexError("");
    try {
      const data = await scanForex({ pair, timeframe: timeframeValue });
      const raw = data.topSetups?.length ? data.topSetups : data.signals || [];
      setForexSignals(raw.map(normalizeForexSignal));
      setForexOverview((current) => current ? { ...current, activeSession: data.activeSession, message: data.message, supportedPairs: data.supportedPairs } : current);
      setForexPairs(data.supportedPairs || forexPairs);
    } catch (error) {
      setForexError(error.message || "Scan failed.");
    } finally {
      setForexLoading(false);
    }
  }

  async function saveTradeToHistory(signal) {
    if (!auth.isAuthenticated || !auth.user) {
      const returnUrl = new URL(window.location.href);
      returnUrl.searchParams.set("detail", signal.symbol);
      const returnTo = `${returnUrl.pathname}${returnUrl.search}`;
      window.location.assign(`/login?returnTo=${encodeURIComponent(returnTo)}`);
      return;
    }

    const idea = paperTradeIdeaForSignal(signal);
    const signalId = signalIdForIdea(idea);
    setTradeSaveState({ signalId, symbol: signal.symbol, status: "saving" });
    try {
      const saved = await createPaperTradeFromSignal(idea, auth.user.id, auth.session?.access_token);
      setPaperTrades((current) => {
        const matchingTrade = (trade) => trade.id === saved.id || (saved.signal_id && trade.signal_id === saved.signal_id);
        return current.some(matchingTrade)
          ? current.map((trade) => matchingTrade(trade) ? saved : trade)
          : [saved, ...current];
      });
      setTradeSaveState({ signalId, symbol: signal.symbol, status: "saved" });
    } catch (error) {
      setTradeSaveState({ signalId, symbol: signal.symbol, status: "error", message: error.message || "Could not save trade to History." });
    }
  }

  function selectTab(nextTab) {
    setNotificationsOpen(false);
    setTab(nextTab);
    setSelectedSignal(null);
    if (nextTab !== "account") setAccountView("main");
    const nextUrl = new URL(window.location.href);
    nextUrl.searchParams.set("market", activeMarket);
    if (/^\/app(?:\/|$)/.test(nextUrl.pathname)) {
      nextUrl.pathname = `/app/${nextTab}`;
      nextUrl.searchParams.delete("tab");
    } else {
      nextUrl.searchParams.set("tab", nextTab);
    }
    window.history.replaceState({}, "", `${nextUrl.pathname}${nextUrl.search}`);
  }

  function navigateAccountView(nextView, { replace = false } = {}) {
    setTab("account");
    setAccountView(nextView);
    setSelectedSignal(null);
    const nextUrl = new URL(window.location.href);
    nextUrl.pathname = "/app/account";
    nextUrl.search = "";
    nextUrl.searchParams.set("market", activeMarket);
    if (nextView !== "main") nextUrl.searchParams.set("view", nextView);
    window.history[replace ? "replaceState" : "pushState"]({}, "", `${nextUrl.pathname}${nextUrl.search}`);
  }

  function backAccountView() {
    if (window.history.length > 1) {
      window.history.back();
      return;
    }
    navigateAccountView("main", { replace: true });
  }

  function closeNotifications() {
    setNotificationsOpen(false);
    if (window.location.pathname !== "/app/notifications") return;
    const nextUrl = new URL(window.location.href);
    nextUrl.pathname = `/app/${tab}`;
    nextUrl.searchParams.set("market", activeMarket);
    window.history.replaceState({}, "", `${nextUrl.pathname}${nextUrl.search}`);
  }

  function openNotifications() {
    if (notificationsOpen) {
      closeNotifications();
      return;
    }
    setNotificationsOpen(true);
  }

  function openNotification(notification) {
    notificationCenter.markRead(notification.id);
    if (!notification.actionUrl) {
      closeNotifications();
      return;
    }
    setNotificationsOpen(false);

    const nextUrl = new URL(notification.actionUrl, window.location.origin);
    const nextMarket = normalizeMarket(nextUrl.searchParams.get("market") || activeMarket);
    const nextTab = nextUrl.pathname.match(/^\/app\/(home|scan|history|account)$/i)?.[1]?.toLowerCase() || "home";
    const detailSymbol = nextUrl.searchParams.get("detail");

    setActiveMarketState(nextMarket);
    window.localStorage?.setItem("swiftchart.activeMarket", nextMarket);
    setTab(nextTab);
    setAccountView("main");
    setSelectedSignal(detailSymbol
      ? [...signals, ...forexSignals].find((signal) => signal.symbol.toLowerCase() === detailSymbol.toLowerCase()) || null
      : null);
    window.history.pushState({}, "", `${nextUrl.pathname}${nextUrl.search}`);
  }

  const activeNavIndex = navItems.findIndex(([key]) => key === tab);

  return (
    <section
      className={[
        "mobile-demo-shell graphite-app",
        preferences.appearance.theme === "light" ? "is-light" : "",
        preferences.appearance.compactLayout ? "is-compact-layout" : "",
        preferences.appearance.largeText ? "has-large-text" : "",
        preferences.appearance.reduceAnimations ? "reduce-animations" : "",
      ].filter(Boolean).join(" ")}
      aria-label="SwiftChart mobile demo"
    >
      {showAppSplash ? <AppSplashScreen ready={appInitializationReady} onComplete={completeAppSplash} /> : null}
      <>
          <GraphiteHeader
            title={tab === "home" || tab === "scan" ? "" : tab}
            activeMarket={activeMarket}
            onMarketChange={setActiveMarket}
            showMarketToggle={tab === "home" || tab === "scan"}
            onOpenNotifications={openNotifications}
            unreadCount={notificationCenter.unreadCount}
            notificationPriority={notificationCenter.priorityIndicator}
            notificationsOpen={notificationsOpen}
          />
          {notificationsOpen ? (
            <div
              className="notification-popover-layer"
              onPointerDown={(event) => {
                if (event.target === event.currentTarget) closeNotifications();
              }}
            >
              <aside
                id="swiftchart-notification-panel"
                className="notification-popover"
                role="dialog"
                aria-label="Notifications"
                aria-modal="false"
                onPointerDown={(event) => event.stopPropagation()}
              >
                <NotificationCenter
                  notifications={notificationCenter.notifications}
                  onOpen={openNotification}
                  onDelete={notificationCenter.remove}
                  onMarkAllRead={notificationCenter.markAllRead}
                  onRefresh={notificationCenter.refresh}
                  onClose={closeNotifications}
                  renderAsset={(symbol) => <InstrumentLogo symbol={symbol} />}
                />
              </aside>
            </div>
          ) : null}
          <main className="graphite-content">
            {tab === "home" ? <HomeScreen signals={preferredSignals} onSelect={setSelectedSignal} onViewAll={() => selectTab("scan")} market={activeMarket} forexSignals={forexSignals} forexOverview={forexOverview} /> : null}
            {tab === "scan" ? <ScanScreen signals={signals} onSelect={setSelectedSignal} market={activeMarket} forexSignals={forexSignals} onForexScan={runForexScan} scanningForex={forexLoading} forexError={forexError} tradingPreferences={preferences.trading} /> : null}
            {tab === "history" ? <HistoryScreen market={activeMarket} paperTrades={paperTrades} /> : null}
            {tab === "account" && accountView === "main" ? (
              <AccountScreen
                onSettings={() => navigateAccountView("settings")}
                onSupport={() => navigateAccountView("support")}
              />
            ) : null}
            {tab === "account" && /^(settings|support)/.test(accountView) ? (
              <SettingsSupport
                view={accountView}
                preferencesController={preferencesController}
                onNavigate={navigateAccountView}
                onBack={backAccountView}
              />
            ) : null}
          </main>
          <nav
            className="graphite-nav"
            aria-label="SwiftChart mobile demo tabs"
            style={{ "--active-index": Math.max(0, activeNavIndex) }}
          >
            {navItems.map(([key, label, Icon]) => (
              <button
                key={key}
                type="button"
                className={tab === key ? "active" : ""}
                aria-current={tab === key ? "page" : undefined}
                aria-label={label}
                onClick={() => selectTab(key)}
              >
                <Icon size={21} />
                <span>{label}</span>
              </button>
            ))}
          </nav>
          <TradeAnalysis
            signal={selectedSignal}
            onClose={() => setSelectedSignal(null)}
            onTakeTrade={saveTradeToHistory}
            tradeSaveState={tradeSaveState}
          />
      </>
    </section>
  );
}
