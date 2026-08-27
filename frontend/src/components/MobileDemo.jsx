import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bell,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  HelpCircle,
  History,
  House,
  Info,
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
} from "lucide-react";
import { useAuth } from "../lib/AuthContext.jsx";
import {
  getForexCurrencyStrength,
  getForexOverview,
  getForexLimitOpportunities,
  getForexSignal,
  getForexSignals,
  runForexScan,
} from "../lib/api.js";
import { MARKET_TYPES, marketFromSearch, normalizeMarket } from "../lib/markets.js";
import { formatSessionCountdown, formatUtcClock, getForexSessionState } from "../lib/forexSessions.js";
import { createPaperTradeFromSignal, getPaperTradeDetail, listPaperTrades, signalIdForIdea } from "../lib/paperTrades.js";
import { pushSwiftChartNotification, useNotifications } from "../lib/notifications.js";
import { useAppPreferences } from "../lib/appPreferences.js";
import AppSplashScreen from "./AppSplashScreen.jsx";
import { CurrencyStrengthOpportunities, CurrencyStrengthRanking } from "./CurrencyStrength.jsx";
import GraphiteStateCard from "./GraphiteStateCard.jsx";
import InstrumentLogo from "./InstrumentLogo.jsx";
import NotificationCenter from "./NotificationCenter.jsx";
import SettingsSupport from "./SettingsSupport.jsx";
import { normalizeCryptoSymbol } from "../lib/instruments.js";
import { executionLadderState } from "../lib/executionLadder.js";
import {
  activeForexSignals,
  canonicalForexTimeframe,
  filterForexSignalsByTimeframe,
} from "../lib/forexSignals.js";

const APP_SPLASH_SESSION_KEY = "swiftchart.appSplashSeen.v1";
const FOREX_SIGNAL_CACHE_KEY = "swiftchart.forexSignals.v1";
const TELEGRAM_BOT_URL = import.meta.env.VITE_TELEGRAM_BOT_URL || "https://t.me/SwiftChartBot";

function hasSeenAppSplash() {
  try {
    return window.sessionStorage?.getItem(APP_SPLASH_SESSION_KEY) === "true";
  } catch {
    return false;
  }
}

function scoreTier(score) {
  if (score >= 95) return "Elite";
  if (score >= 90) return "High Conviction";
  if (score >= 80) return "Strong";
  return "Watchlist";
}

function formatTradePrice(value) {
  const numeric = Number(String(value ?? "").replace(/,/g, ""));
  if (!Number.isFinite(numeric)) return String(value ?? "-");

  const magnitude = Math.abs(numeric);
  const maximumFractionDigits = magnitude >= 1 ? 2 : 5;
  const minimumFractionDigits = magnitude >= 1 && !Number.isInteger(numeric) ? 2 : 0;

  return numeric.toLocaleString(undefined, {
    minimumFractionDigits,
    maximumFractionDigits,
  });
}

function displayRange(values) {
  if (!Array.isArray(values) || values.length === 0) return "-";
  const formatted = values.slice(0, 2).map(formatTradePrice);
  return formatted.length === 1 ? formatted[0] : formatted.join(" – ");
}

function normalizeIdea(idea, index) {
  const score = Math.round(Number(idea?.setup_score ?? idea?.confidence_score ?? 0));
  const entryZone = Array.isArray(idea?.entry_zone) ? idea.entry_zone : [];
  return {
    rawIdea: idea,
    market: MARKET_TYPES.crypto,
    rank: index + 1,
    symbol: idea?.symbol || "Unknown",
    direction: idea?.direction || "Wait",
    timeframe: idea?.timeframe || "-",
    score,
    tier: scoreTier(score),
    entry: displayRange(entryZone),
    sl: idea?.stop_loss !== undefined ? formatTradePrice(idea.stop_loss) : "-",
    tp: idea?.take_profit_1 !== undefined ? formatTradePrice(idea.take_profit_1) : "-",
    tp2: idea?.take_profit_2 !== undefined ? formatTradePrice(idea.take_profit_2) : "-",
    rr: idea?.risk_reward_ratio !== undefined ? `${Number(idea.risk_reward_ratio).toFixed(1)}R` : "-",
    confidence: idea?.confidence_score !== undefined ? `${Math.round(Number(idea.confidence_score))}%` : `${score}%`,
    regime: idea?.regime_label || idea?.market_regime || "Awaiting market classification",
    thesis: idea?.reason || "This setup passed SwiftChart's live confirmation checks.",
    exchange: idea?.exchange,
    status: idea?.status || "Open",
  };
}

function normalizePendingSetup(setup, index) {
  const score = Math.round(Number(setup?.score_preview ?? 0));
  const short = /short|sell/i.test(setup?.direction);
  const targetZone = short ? setup?.nearest_support : setup?.nearest_resistance;
  return {
    rawIdea: setup,
    isPending: true,
    market: MARKET_TYPES.crypto,
    rank: index + 1,
    symbol: setup?.symbol || "Unknown",
    direction: setup?.direction || "Wait",
    timeframe: setup?.timeframe || "-",
    score,
    tier: "Watchlist",
    entry: displayRange(setup?.entry_zone),
    sl: setup?.invalidation_level !== undefined ? formatTradePrice(setup.invalidation_level) : "-",
    tp: displayRange(targetZone),
    tp2: "-",
    rr: setup?.estimated_rr !== undefined ? `${Number(setup.estimated_rr).toFixed(1)}R` : "-",
    confidence: `${score}% preview`,
    regime: setup?.regime || "Pending confirmation",
    thesis: setup?.reason || "Live market candidate awaiting confirmation.",
    exchange: setup?.exchange,
    status: "Wait",
  };
}

function normalizeActiveCryptoTrade(trade, index) {
  return {
    ...normalizeIdea({
      symbol: trade.symbol,
      direction: /short|sell/i.test(trade.direction) ? "Short" : "Long",
      timeframe: trade.timeframe,
      exchange: trade.exchange,
      entry_zone: [trade.entry_zone_low, trade.entry_zone_high],
      stop_loss: trade.stop_loss,
      take_profit_1: trade.take_profit_1,
      take_profit_2: trade.take_profit_2,
      risk_reward_ratio: trade.risk_reward,
      setup_score: trade.setup_score,
      confidence_score: trade.confidence,
      market_regime: trade.market_regime,
      regime_label: trade.regime_label,
      reason: trade.reason,
      invalid_condition: trade.invalidation,
      signal_candle_time: trade.signal_candle_time,
      created_at: trade.created_at,
    }, index),
    id: `active-crypto-${trade.id}`,
    isActiveTrade: true,
    lifecycleStatus: trade.status || "OPEN",
  };
}

function numbersFromDisplay(value) {
  return (String(value || "").replace(/,/g, "").match(/\d*\.?\d+/g) || [])
    .map(Number)
    .filter(Number.isFinite);
}

function paperTradeIdeaForSignal(signal) {
  const entryValues = numbersFromDisplay(signal.entry);
  const rawIdea = signal.rawIdea || {};
  if (signal.market !== MARKET_TYPES.forex && signal.rawIdea) return signal.rawIdea;

  const entryLow = signal.entryLow ?? rawIdea.entry_low ?? rawIdea.entry_price ?? entryValues[0];
  const entryHigh = signal.entryHigh ?? rawIdea.entry_high ?? rawIdea.entry_price ?? entryValues[1] ?? entryLow;
  const stopLoss = rawIdea.stop_loss ?? numbersFromDisplay(signal.sl)[0];
  const takeProfit = rawIdea.take_profit_1 ?? numbersFromDisplay(signal.tp)[0];
  const takeProfit2 = rawIdea.take_profit_2 ?? numbersFromDisplay(signal.tp2)[0] ?? takeProfit;
  const riskReward = rawIdea.risk_reward_2 ?? rawIdea.risk_reward ?? numbersFromDisplay(signal.rr)[0];

  return {
    ...rawIdea,
    symbol: signal.symbol,
    timeframe: signal.timeframe,
    exchange: signal.market === MARKET_TYPES.forex ? "forex" : signal.exchange || "hyperliquid",
    source: signal.market === MARKET_TYPES.forex ? "forex" : "mobile-demo",
    direction: /short|sell/i.test(signal.direction) ? "Short" : "Long",
    entry_zone: [entryLow, entryHigh],
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
  const pair = signal?.pair || signal?.symbol || "Unknown";
  const direction = String(signal?.direction || "WAIT").toUpperCase();
  const score = Math.round(Number(signal?.setup_score ?? signal?.score ?? 0));
  const rrValue = signal?.risk_reward_2 ?? signal?.rr;
  const rr = rrValue !== undefined ? `${Number(rrValue).toFixed(1)}R` : "-";
  const entryLow = signal?.entry_low ?? signal?.entry;
  const entryHigh = signal?.entry_high ?? signal?.entry;
  const entry = entryLow !== undefined
    ? `${fmt(entryLow)}${entryHigh !== undefined && Number(entryHigh) !== Number(entryLow) ? ` - ${fmt(entryHigh)}` : ""}`
    : "-";
  return {
    ...signal,
    market: MARKET_TYPES.forex,
    symbol: String(pair).replace("/", "").toUpperCase(),
    pair: slashPair(pair),
    direction: direction === "SHORT" || direction === "SELL" ? "Sell" : direction === "WAIT" ? "Wait" : "Buy",
    score,
    id: signal?.id || `${pair}-${index}`,
    grade: signal?.grade || (score >= 90 ? "A+" : score >= 80 ? "A" : "B"),
    timeframe: canonicalForexTimeframe(signal?.timeframe || signal?.execution_timeframe),
    setupTimeframe: signal?.setup_timeframe || "1h",
    biasTimeframe: signal?.bias_timeframe || "4h",
    timeframeAlignment: signal?.timeframe_alignment || "-",
    entry,
    entryLow: entryLow !== undefined ? Number(entryLow) : undefined,
    entryHigh: entryHigh !== undefined ? Number(entryHigh) : undefined,
    sl: signal?.stop_loss !== undefined ? fmt(signal.stop_loss) : signal?.stopLoss !== undefined ? fmt(signal.stopLoss) : "-",
    tp: signal?.take_profit_1 !== undefined ? fmt(signal.take_profit_1) : signal?.tp1 !== undefined ? fmt(signal.tp1) : "-",
    tp2: signal?.take_profit_2 !== undefined ? fmt(signal.take_profit_2) : signal?.tp2 !== undefined ? fmt(signal.tp2) : "-",
    rr,
    session: signal?.market_session || signal?.session || "-",
    spread: signal?.spreadStatus || "-",
    newsRisk: signal?.newsRisk || "-",
    status: signal?.status || "Wait",
    regime: signal?.market_regime || signal?.htf_bias || signal?.pre_session_bias || "Awaiting structure",
    thesis: signal?.setup_reason || signal?.reason || "Persisted Forex setup.",
    reasons: signal?.setup_reason ? [signal.setup_reason] : signal?.reason ? [signal.reason] : [],
    createdAt: dt(signal?.created_at || signal?.lastUpdated),
    updatedAt: dt(signal?.last_price_updated_at || signal?.lastUpdated),
    expiresAt: dt(signal?.expires_at),
    latestPrice: signal?.latest_price ?? signal?.last_market_price,
    latestPriceAt: signal?.latest_price_at ?? signal?.last_price_updated_at,
    activatedEntryPrice: signal?.activated_entry_price,
    tp1HitAt: signal?.tp1_hit_at,
    tp2HitAt: signal?.tp2_hit_at,
    stoppedAt: signal?.stopped_at,
    lastMarketPrice: signal?.latest_price ?? signal?.last_market_price,
    rawIdea: signal,
  };
}

function normalizeSavedTrade(trade) {
  const isForex = String(trade.exchange || "").toLowerCase() === MARKET_TYPES.forex;
  const resultR = trade.result_r ?? trade.pnl;
  const lifecycleLabel = trade.lifecycle_label || (trade.status === "taken" ? "Open" : trade.status || "Open");
  if (isForex) {
    return {
      ...normalizeForexSignal({
        ...trade,
        id: trade.source_signal_id || trade.signal_id || `paper-${trade.id}`,
        pair: trade.symbol,
        entry_low: trade.entry_low ?? trade.entry_price,
        entry_high: trade.entry_high ?? trade.entry_price,
        risk_reward_1: trade.risk_reward_1,
        risk_reward_2: trade.risk_reward,
        setup_score: trade.setup_score ?? trade.confidence,
        market_session: trade.market_session,
        setup_reason: trade.setup_reason || trade.reason,
        status: trade.lifecycle_status || "OPEN",
      }),
      isHistory: true,
      historyTradeId: trade.id,
      historyResult: lifecycleLabel,
      historyResultR: resultR,
      takenAt: dt(trade.taken_at),
    };
  }

  return {
    ...normalizeIdea({
      symbol: trade.symbol,
      direction: /short|sell/i.test(trade.direction) ? "Short" : "Long",
      timeframe: trade.timeframe,
      entry_zone: [trade.entry_price, trade.entry_price],
      stop_loss: trade.stop_loss,
      take_profit_1: trade.take_profit_1,
      take_profit_2: trade.take_profit_2,
      risk_reward_ratio: trade.risk_reward,
      setup_score: trade.setup_score ?? trade.confidence,
      confidence_score: trade.confidence ?? trade.setup_score,
      market_regime: trade.market_regime || trade.market_bias,
      reason: trade.reason || "Saved SwiftChart setup.",
    }, 0),
    isHistory: true,
    historyTradeId: trade.id,
    historyResult: lifecycleLabel,
    historyResultR: resultR,
    takenAt: dt(trade.taken_at),
  };
}

function tokenSymbol(symbol) {
  return normalizeCryptoSymbol(symbol);
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

function StatTile({ label, value, tone }) {
  return (
    <article className={`graphite-stat ${tone || ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function HomeScreen({ signals, activeSignals, onSelect, onViewAll, market, forexSignals, forexOverview, forexStrength, onViewStrength, onSelectPair }) {
  if (market === MARKET_TYPES.forex) {
    return <ForexHomeScreen signals={forexSignals} overview={forexOverview} strength={forexStrength} onSelect={onSelect} onViewAll={onViewAll} onViewStrength={onViewStrength} onSelectPair={onSelectPair} />;
  }
  const best = signals[0] || null;
  const bullish = best ? !/short|sell/i.test(best.direction) : null;
  const opportunitySignals = signals.filter((signal) => !signal.isActiveTrade);
  return (
    <div className="graphite-screen">
      <section className="graphite-hero-card">
        <div className="graphite-hero-copy">
          <span>Market Summary</span>
          <h1>{best ? best.score : "--"}</h1>
          <p>{best ? `${best.symbol} is the strongest ranked setup right now.` : "SwiftChart is tracking the live crypto market."}</p>
          <span className={`graphite-bias-pill ${bullish === null ? "" : bullish ? "bullish" : "bearish"}`}>
            {bullish === null ? <Target size={15} /> : bullish ? <TrendingUp size={15} /> : <TrendingDown size={15} />}
            {bullish === null ? "Waiting for confirmation" : bullish ? "Bullish momentum" : "Bearish momentum"}
          </span>
        </div>
        <SparkChart />
      </section>

      <section className="graphite-regime-card">
        <div>
          <span>Market Regime</span>
          <strong>{bullish === null ? "Neutral, selective" : bullish ? "Bullish, selective" : "Bearish, selective"}</strong>
          <p>{bullish === null ? "Waiting for a confirmed directional edge." : bullish ? "Momentum favors longs." : "Momentum favors shorts."} Weak mid-range setups are filtered out.</p>
        </div>
        <LineChart size={24} />
      </section>

      <section className="market-pair-section">
        <div className="graphite-section-head">
          <span>Latest opportunities</span>
          <button type="button" onClick={onViewAll}>Scan market</button>
        </div>
        <div className="market-pair-list">
          {opportunitySignals.slice(0, 4).map((signal) => <MarketSignalRow key={`${signal.symbol}-${signal.timeframe}-home`} signal={signal} onSelect={onSelect} />)}
          {!opportunitySignals.length ? (
            <GraphiteStateCard
              tone="empty"
              eyebrow="Market scan"
              title="No new confirmed setup right now"
              message="Active Telegram trades stay listed above while SwiftChart scans for the next opportunity."
              actionLabel="Scan again"
              onAction={onViewAll}
            />
          ) : null}
        </div>
      </section>

      {activeSignals.length ? (
        <section className="market-pair-section">
          <div className="graphite-section-head">
            <span>Active signals</span>
            <button type="button" onClick={onViewAll}>View all</button>
          </div>
          <div className="market-pair-list">
            {activeSignals.slice(0, 6).map((signal) => <MarketSignalRow key={signal.id} signal={signal} onSelect={onSelect} />)}
          </div>
        </section>
      ) : null}

    </div>
  );
}

const FOREX_STATUS_GROUPS = [
  ["Active", ["OPEN", "TP1_HIT_TP2_RUNNING"]],
  ["Pending Entry", ["PENDING_ENTRY"]],
];

function ForexSignalGroups({ signals, onSelect, compact = false }) {
  return FOREX_STATUS_GROUPS.map(([label, statuses]) => {
    const rows = signals.filter((signal) => statuses.includes(String(signal.status).toUpperCase()));
    if (!rows.length) return null;
    return (
      <section className="graphite-forex-status-group" key={label}>
        <div className="graphite-section-head">
          <span>{label}</span>
          <small>{rows.length}</small>
        </div>
        <div className={`market-pair-list ${compact ? "compact" : ""}`}>
          {rows.map((signal) => (
            <MarketSignalRow key={signal.id} signal={signal} onSelect={onSelect} />
          ))}
        </div>
      </section>
    );
  });
}

const FOREX_HOME_SECTIONS = [
  ["Latest opportunities", ["PENDING_ENTRY"]],
  ["Active signals", ["OPEN", "TP1_HIT_TP2_RUNNING"]],
];

function marketRowStatus(signal) {
  const status = String(signal.status || "").toUpperCase();
  const entryStatus = String(signal.rawIdea?.entry_status || "").toUpperCase();
  if (status === "PENDING_ENTRY") return "Entry pending";
  if (status === "TP1_HIT_TP2_RUNNING") return "TP1 hit · TP2 running";
  if (signal.market === MARKET_TYPES.forex) return "Active trade";
  if (signal.isPending || entryStatus === "WAIT_FOR_RETEST") return "Retest pending";
  if (entryStatus === "READY") return "Entry ready";
  if (signal.isActiveTrade) return "Active trade";
  return "Qualified setup";
}

function MarketSignalRow({ signal, onSelect }) {
  const isShort = /short|sell/i.test(signal.direction);
  return (
    <button type="button" className="market-pair-row" onClick={() => onSelect(signal)}>
      <i className={isShort ? "short" : "long"} aria-hidden="true" />
      <div className="market-pair-copy">
        <div>
          <strong>{signal.symbol}</strong>
          <Direction direction={signal.direction} />
        </div>
        <small>{signal.timeframe.toUpperCase()} · {marketRowStatus(signal)}</small>
      </div>
      <span className="market-pair-quality">
        <strong>{signal.score}</strong>
        <small>{signal.rr}</small>
      </span>
    </button>
  );
}

function ForexHomeSignalLists({ signals, onSelect, onViewAll }) {
  return FOREX_HOME_SECTIONS.map(([label, statuses]) => {
    const rows = signals.filter((signal) => statuses.includes(String(signal.status).toUpperCase()));
    if (!rows.length) return null;
    return (
      <section className="market-pair-section" key={label}>
        <div className="graphite-section-head">
          <span>{label}</span>
          <button type="button" onClick={onViewAll}>{label === "Latest opportunities" ? "Scan market" : "View all"}</button>
        </div>
        <div className="market-pair-list">
          {rows.map((signal) => <MarketSignalRow key={signal.id} signal={signal} onSelect={onSelect} />)}
        </div>
      </section>
    );
  });
}

function ForexPairSearch({ pairs, onSelectPair }) {
  const [pairQuery, setPairQuery] = useState("");
  const pairMatches = useMemo(() => {
    const query = pairQuery.trim().toLowerCase();
    if (!query) return [];
    return (pairs || []).filter((item) => {
      const symbol = String(item.pair || "").toLowerCase();
      const name = String(item.display_name || "").toLowerCase();
      const aliases = symbol === "xauusd" ? "gold bullion metal" : "";
      return `${symbol} ${name} ${aliases}`.includes(query);
    }).slice(0, 6);
  }, [pairQuery, pairs]);
  const openFirstPairMatch = () => {
    if (pairMatches[0]) onSelectPair(pairMatches[0].pair);
  };

  return (
    <section className="forex-pair-search" aria-label="Find a Forex pair">
      <Search size={18} aria-hidden="true" />
      <input
        value={pairQuery}
        onChange={(event) => setPairQuery(event.target.value)}
        placeholder="Search EURUSD, XAUUSD, GBPJPY..."
        aria-label="Search Forex pairs"
        autoComplete="off"
        onKeyDown={(event) => {
          if (event.key === "Enter" && pairMatches[0]) {
            event.preventDefault();
            openFirstPairMatch();
          }
        }}
      />
      <button
        type="button"
        className="forex-pair-search-submit"
        onClick={openFirstPairMatch}
        disabled={!pairMatches.length}
        aria-label="Open first matching Forex pair"
      >
        Search
      </button>
      {pairMatches.length ? (
        <div className="forex-pair-search-results">
          {pairMatches.map((item) => (
            <button key={item.pair} type="button" onClick={() => onSelectPair(item.pair)}>
              <InstrumentLogo symbol={item.pair} market="forex" />
              <span><strong>{item.pair}</strong><small>{item.display_name}</small></span>
              <ChevronRight size={17} aria-hidden="true" />
            </button>
          ))}
        </div>
      ) : pairQuery.trim() ? <p className="forex-pair-search-empty">No matching supported pair.</p> : null}
    </section>
  );
}

function CurrencyStrengthPreview({ snapshot, onViewAll, onSelectPair }) {
  return (
    <section className="currency-strength-card" aria-label="Currency Strength">
      <header>
        <div>
          <span className="currency-strength-title">Currency Strength <Info size={14} aria-label="Calculated from OANDA price changes across 28 major currency crosses" /></span>
          <small>OANDA · LIVE · 24H · informational</small>
        </div>
        <button type="button" onClick={onViewAll}>View all</button>
      </header>
      {snapshot ? <>
        <CurrencyStrengthRanking rows={snapshot.ranking} compact />
        <div className="currency-strength-opportunity-heading"><span>Top pair opportunities</span><small>Strength gap</small></div>
        <CurrencyStrengthOpportunities opportunities={snapshot.opportunities} limit={3} onSelectPair={onSelectPair} compact />
        <p className="currency-strength-note">{snapshot.available_pair_count}/{snapshot.total_pair_count} major crosses available</p>
      </> : <p className="currency-strength-empty">Calculating relative strength from OANDA price changes…</p>}
    </section>
  );
}

function ForexHomeScreen({ signals, overview, strength, onSelect, onViewAll, onViewStrength, onSelectPair }) {
  const [sessionClock, setSessionClock] = useState(() => new Date());
  const session = useMemo(() => getForexSessionState(sessionClock), [sessionClock]);
  const homeSignals = useMemo(
    () => activeForexSignals(signals),
    [signals],
  );

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

      <CurrencyStrengthPreview snapshot={strength} onViewAll={onViewStrength} onSelectPair={onSelectPair} />

      {overview?.message ? (
        <GraphiteStateCard
          tone="neutral"
          eyebrow="Forex status"
          title="Session update"
          message={overview.message}
        />
      ) : null}

      <div className="forex-home-lists">
        <ForexHomeSignalLists signals={homeSignals} onSelect={onSelect} onViewAll={onViewAll} />
      </div>
      {!homeSignals.length ? (
        <p className="graphite-no-results">
          No persisted Forex setup currently meets the scanner criteria. SwiftChart will update this list after the next scheduled scan.
        </p>
      ) : null}
    </div>
  );
}

function ScanScreen({
  signals,
  onSelect,
  market,
  forexSignals,
  onCryptoScan,
  onForexScan,
  cryptoLoading,
  scanningForex,
  forexScanResult,
  forexFeedState,
  forexScanError,
  onRetryForex,
  tradingPreferences,
  forexPairs,
  onSelectPair,
}) {
  const [scanState, setScanState] = useState("idle");
  const [coinQuery, setCoinQuery] = useState("");
  const [appliedCoinQuery, setAppliedCoinQuery] = useState("");
  const [scoreBand, setScoreBand] = useState("all");
  const [timeframe, setTimeframe] = useState("all");
  const [forexTimeframe, setForexTimeframe] = useState("1H");
  const [forexCooldown, setForexCooldown] = useState(0);
  const forexScanInFlight = useRef(false);
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

  async function startScan() {
    setAppliedCoinQuery("");
    setScanState("scanning");
    try {
      const refreshed = await onCryptoScan?.();
      setScanState(refreshed === false ? "error" : "done");
    } catch {
      setScanState("error");
    }
  }

  function scanCoin(event) {
    event?.preventDefault();
    setAppliedCoinQuery(coinQuery.trim());
    setScanState("done");
  }

  useEffect(() => {
    if (!forexCooldown) return undefined;
    const timer = window.setInterval(
      () => setForexCooldown((seconds) => Math.max(0, seconds - 1)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [forexCooldown > 0]);

  async function startForexScan() {
    if (scanningForex || forexCooldown || forexScanInFlight.current) return;
    forexScanInFlight.current = true;
    try {
      const completed = await onForexScan?.(forexTimeframe);
      if (completed) setForexCooldown(30);
    } finally {
      forexScanInFlight.current = false;
    }
  }

  if (market === MARKET_TYPES.forex) {
    const filterTimeframe = canonicalForexTimeframe(forexTimeframe);
    const visibleForexSignals = filterForexSignalsByTimeframe(activeForexSignals(forexSignals), filterTimeframe);
    const resultSignals = [
      ...(forexScanResult?.created || []),
      ...(forexScanResult?.reused || []),
    ].map(normalizeForexSignal);
    const noTrade = forexScanResult?.result_status === "NO_TRADE";
    const failed = forexScanResult?.result_status === "FAILED";
    return (
      <div className="graphite-screen">
        <section className="graphite-scan-card forex">
          <div>
            <span>Forex Scan</span>
            <h1>Check the market now</h1>
            <p>Run the same qualified strategy used by SwiftChart's scheduled scanner.</p>
          </div>
          <button
            type="button"
            onClick={startForexScan}
            disabled={scanningForex || forexCooldown > 0}
            aria-busy={scanningForex}
          >
            {scanningForex ? <span className="graphite-button-spinner" aria-hidden="true" /> : <ScanLine size={16} />}
            {scanningForex
              ? "Scanning…"
              : forexCooldown
                ? `Wait ${forexCooldown}s`
                : "Scan Forex"}
          </button>
        </section>

        <ForexPairSearch pairs={forexPairs} onSelectPair={onSelectPair} />

        {forexFeedState.status === "loading" && !forexSignals.length ? (
          <GraphiteStateCard
            tone="loading"
            eyebrow="Forex feed"
            title="Loading Forex signals"
            message="Fetching the latest persisted setups."
            busy
          />
        ) : null}
        {forexFeedState.status === "auth_unavailable" ? (
          <GraphiteStateCard
            tone="auth"
            eyebrow="Authentication required"
            title="Authentication unavailable"
            message="Public Forex signals remain available in read-only mode. Sign-in actions may be temporarily unavailable."
            actionLabel="Retry"
            onAction={onRetryForex}
          />
        ) : null}
        {forexFeedState.status === "network_unavailable" || forexFeedState.status === "api_unavailable" ? (
          <GraphiteStateCard
            tone="error"
            eyebrow={forexFeedState.status === "network_unavailable" ? "Network error" : "API unavailable"}
            title="Could not load Forex signals"
            message={forexFeedState.message}
            actionLabel="Retry"
            onAction={onRetryForex}
          />
        ) : null}
        {forexFeedState.cached ? (
          <GraphiteStateCard
            tone="cached"
            eyebrow="Cached data"
            title="Showing the last verified signals"
            message={`Last updated ${forexFeedState.lastUpdated || "recently"}. Live refresh will resume when the feed is available.`}
          />
        ) : null}
        {forexScanError ? (
          <GraphiteStateCard
            tone="error"
            eyebrow="Scan error"
            title="Forex scan could not complete"
            message={forexScanError}
            actionLabel="Retry"
            onAction={startForexScan}
          />
        ) : null}

        <div className="graphite-filters forex-timeframe-filter" aria-label="Forex timeframe filter">
          <label>
            <span className="sr-only">Timeframe</span>
            <select value={forexTimeframe} onChange={(event) => setForexTimeframe(event.target.value)} aria-label="Forex timeframe">
              <option value="15M">15M</option>
              <option value="1H">1H</option>
              <option value="4H">4H</option>
              <option value="1D">1D</option>
            </select>
            <ChevronDown size={14} aria-hidden="true" />
          </label>
        </div>

        {forexScanResult && !failed ? (
          <GraphiteStateCard
            tone={noTrade ? "no-trade" : "success"}
            eyebrow={noTrade ? "NO_TRADE" : "Trade found"}
            title={noTrade
              ? `No qualified Forex setup on ${forexScanResult.timeframe}`
              : `${resultSignals.length} qualified ${resultSignals.length === 1 ? "setup" : "setups"} found`}
            message={noTrade && forexScanResult.rejection_reasons?.length
              ? `Most setups were rejected because: ${forexScanResult.rejection_reasons.join("; ")}`
              : noTrade
                ? "The scan completed without forcing a trade."
                : "Session, spread, structure and reward-to-risk checks passed."}
            meta={(
              <div className="forex-scan-facts">
                <p><small>Timeframe</small><strong>{forexScanResult.timeframe}</strong></p>
                <p><small>Pairs scanned</small><strong>{forexScanResult.pairs_scanned}</strong></p>
                <p>
                  <small>Completed</small>
                  <strong>{new Date(forexScanResult.completed_at || forexScanResult.scanned_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</strong>
                </p>
              </div>
            )}
          >
            {resultSignals.length ? (
              <div className="market-pair-list compact">
                {resultSignals.map((signal) => <MarketSignalRow key={signal.id} signal={signal} onSelect={onSelect} />)}
              </div>
            ) : null}
          </GraphiteStateCard>
        ) : null}
        {failed ? (
          <GraphiteStateCard
            tone="error"
            eyebrow="Scan failed"
            title="Forex scan could not complete"
            message={forexScanResult.errors?.[0] || "Market data is temporarily unavailable."}
          />
        ) : null}

        <section className="graphite-section">
          <div className="graphite-section-head">
            <span>Stored Forex signals</span>
            <small>{scanningForex ? `Scanning ${filterTimeframe}…` : `${filterTimeframe} · ${visibleForexSignals.length}`}</small>
          </div>
          <ForexSignalGroups signals={visibleForexSignals} onSelect={onSelect} compact />
          {!visibleForexSignals.length && !scanningForex && forexFeedState.status === "success" ? (
            <p className="graphite-no-results">No signals are available for {filterTimeframe}. Select another timeframe or retry later.</p>
          ) : null}
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
        <button type="button" onClick={startScan} disabled={scanState === "scanning" || cryptoLoading}><ScanLine size={18} /> {scanState === "scanning" || cryptoLoading ? "Scanning..." : "Scan Market"}</button>
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

      {scanState === "scanning" || cryptoLoading ? (
        <GraphiteStateCard
          tone="loading"
          eyebrow="Scanning market"
          title="Checking structure and confirmation"
          message="Refreshing ranked opportunities without changing SwiftChart's qualification rules."
          busy
        />
      ) : null}
      {scanState === "error" ? (
        <GraphiteStateCard
          tone="error"
          eyebrow="Refresh error"
          title="Could not refresh ranked setups"
          message="The last verified market view remains available. Retry when the connection is stable."
          actionLabel="Retry"
          onAction={startScan}
        />
      ) : null}

      <section className="graphite-section">
        <div className="graphite-section-head">
          <span>Scan results</span>
          <strong className="graphite-results-status">{scanState === "scanning" || cryptoLoading ? "Analyzing..." : scanState === "error" ? "Refresh failed" : "Live market"}<i /></strong>
        </div>
        <div className="market-pair-list compact">
          {visibleSignals.map((signal) => <MarketSignalRow key={`${signal.symbol}-scan`} signal={signal} onSelect={onSelect} />)}
          {visibleSignals.length === 0 ? <p className="graphite-no-results">No matching setup is currently ranked.</p> : null}
        </div>
      </section>
    </div>
  );
}

function ExecutionMarker({ position }) {
  const markerRef = useRef(null);
  const previousPosition = useRef(position);

  useEffect(() => {
    const marker = markerRef.current;
    const previous = previousPosition.current;
    previousPosition.current = position;
    if (!marker || previous === position || typeof marker.animate !== "function") return undefined;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches || marker.closest(".reduce-animations")) return undefined;
    const railWidth = marker.parentElement?.clientWidth || 0;
    const offset = ((previous - position) / 100) * railWidth;
    if (!Number.isFinite(offset) || Math.abs(offset) < 1) return undefined;
    const animation = marker.animate(
      [
        { transform: `translateX(${offset}px) translateX(-50%)` },
        { transform: "translateX(-50%)" },
      ],
      { duration: 200, easing: "cubic-bezier(0.23, 1, 0.32, 1)" },
    );
    return () => animation.cancel();
  }, [position]);

  return (
    <i ref={markerRef} className="current-price" style={{ left: `${position}%` }}>
      <b />
    </i>
  );
}

function ExecutionLadder({ signal }) {
  const progress = executionLadderState(signal);
  const priceDigits = /JPY/i.test(signal.symbol) ? 3 : /XAU/i.test(signal.symbol) ? 2 : 5;
  const updatedAt = signal.latestPriceAt || signal.rawIdea?.latest_price_at || signal.rawIdea?.last_price_updated_at;
  const hasTp2 = signal.tp2 && signal.tp2 !== "-";
  return (
    <section className={`graphite-execution-ladder is-${progress.tone}${progress.disabled ? " is-disabled" : ""}`} aria-label="Trade plan">
      <div className="graphite-ladder-heading">
        <span className="graphite-ladder-title">Trade plan</span>
        <strong>{progress.label}</strong>
      </div>
      <div className="graphite-ladder-live">
        <span>Current: <strong>{progress.currentPrice === null ? "-" : fmt(progress.currentPrice, priceDigits)}</strong></span>
        <span>Updated: {updatedAt ? new Date(updatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "-"}</span>
      </div>
      <div className="graphite-ladder-rail" aria-hidden="true">
        <i className={`target tp2${progress.tp2Complete ? " complete" : ""}`} style={{ left: `${progress.tp2Position}%` }} />
        <i className={`target tp1${progress.tp1Complete ? " complete" : ""}`} style={{ left: `${progress.tp1Position}%` }} />
        <i className="entry-zone" style={{ left: `${progress.entryStart}%`, width: `${Math.max(2, progress.entryEnd - progress.entryStart)}%` }} />
        <i className={`stop${progress.stopComplete ? " complete" : ""}`} style={{ left: `${progress.stopPosition}%` }} />
        {progress.currentPrice !== null ? <ExecutionMarker position={progress.markerPosition} /> : null}
      </div>
      <div className={`graphite-ladder-values plan-values${hasTp2 ? " has-tp2" : ""}`}>
        <div className="stop">
          <span>Stop loss</span>
          <strong>{signal.sl}</strong>
        </div>
        <div className="entry">
          <span>Entry zone</span>
          <strong>{signal.entry}</strong>
        </div>
        <div className="current">
          <span>Current</span>
          <strong>{progress.currentPrice === null ? "-" : fmt(progress.currentPrice, priceDigits)}</strong>
        </div>
        <div className="target">
          <span>TP1</span>
          <strong>{signal.tp}</strong>
        </div>
        {hasTp2 ? <div className="target tp2-value"><span>TP2</span><strong>{signal.tp2}</strong></div> : null}
      </div>
    </section>
  );
}

function sentenceCase(value) {
  const text = String(value || "").trim().replace(/[.!?]+$/, "");
  if (!text) return "market structure supports the setup";
  return /^[A-Z]{2,}/.test(text) ? text : `${text.charAt(0).toLowerCase()}${text.slice(1)}`;
}

function reasoningSentences(signal, { isForex }) {
  const source = isForex && signal.reasons?.length
    ? signal.reasons
    : [signal.thesis];
  return source
    .flatMap((value) => String(value || "").split(/(?<=[.!?])\s+/))
    .map((value) => value
      .replace(/\s+/g, " ")
      .replace(/\s+with quality score\s+[\d.]+/i, "")
      .trim()
      .replace(/[.!?]+$/, "")
      .replace(/;\s*session=(.+)$/i, " during the $1"))
    .filter((value) => value.length > 10)
    .filter((value) => !/^[A-Z]{6}\s+qualified independently on\s+\w+$/i.test(value))
    .filter((value) => !/^(market regime|confidence adjustment|backend invalidation|entry is|targets? (?:are|is))/i.test(value));
}

function tradeReasoning(signal, { isForex }) {
  const direction = String(signal.direction || "trade").toLowerCase();
  const timeframe = String(signal.timeframe || "").toUpperCase();
  const symbol = signal.pair || signal.symbol;
  const insights = reasoningSentences(signal, { isForex });
  const primary = sentenceCase(insights[0]);
  const supporting = insights[1]
    ? `${insights[1].charAt(0).toUpperCase()}${insights[1].slice(1)}.`
    : isForex
      ? `The ${signal.session || "active"} session passed SwiftChart's execution checks.`
      : `The stop remains beyond structural invalidation.`;
  return `${symbol} is ranked as a ${direction} setup on ${timeframe} because ${primary}. ${supporting}`;
}

function detailRecency(signal) {
  const raw = signal.rawIdea || {};
  const value = signal.latestPriceAt
    || raw.latest_price_at
    || raw.last_price_updated_at
    || raw.updated_at
    || raw.generated_at
    || raw.created_at;
  const timestamp = value ? new Date(value).getTime() : Number.NaN;
  if (!Number.isFinite(timestamp)) return "live market";
  const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return "updated now";
  if (minutes < 60) return `updated ${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `updated ${hours}h ago` : "latest verified setup";
}

function TradeAnalysis({ signal, onClose, onTakeTrade, tradeSaveState }) {
  if (!signal) return null;
  const isForex = signal.market === MARKET_TYPES.forex;
  const activeTradeSaveState = tradeSaveState?.symbol === signal.symbol ? tradeSaveState : { status: "idle" };
  const terminalForexStatus = ["TP2_HIT", "STOPPED", "EXPIRED", "CANCELLED"].includes(String(signal.status).toUpperCase());
  return (
    <main className="graphite-detail-screen" aria-label={`${signal.symbol} trade analysis`}>
      <article className="graphite-detail">
        <header>
          <InstrumentLogo symbol={signal.symbol} marketType={signal.market} />
          <div>
            <span>{isForex ? "Forex Trade Analysis" : "Trade Analysis"}</span>
            <strong>{isForex ? signal.pair : signal.symbol}</strong>
            <p>{isForex ? "Session-qualified setup" : signal.score >= 80 ? "High-conviction setup" : "Qualified setup"} · {detailRecency(signal)}</p>
          </div>
          <ScoreRing score={signal.score} />
        </header>

        <div className="graphite-trade-meta">
          <StatTile label="Direction" value={signal.direction} tone={/short|sell/i.test(signal.direction) ? "loss" : "profit"} />
          <StatTile label="Timeframe" value={signal.timeframe.toUpperCase()} />
          <StatTile label="R/R" value={signal.rr} tone="reward" />
        </div>

        {signal.isHistory ? (
          <section className={`graphite-history-result ${Number(signal.historyResultR) < 0 ? "loss" : Number(signal.historyResultR) > 0 ? "profit" : ""}`}>
            <div>
              <span>Trade result</span>
              <strong>{signal.historyResult || "Tracking"}</strong>
            </div>
            <em>{signal.historyResultR === null || signal.historyResultR === undefined ? "Tracking" : `${Number(signal.historyResultR) >= 0 ? "+" : ""}${Number(signal.historyResultR).toFixed(1)}R`}</em>
          </section>
        ) : null}

        <ExecutionLadder signal={signal} />

        <section className="graphite-reasoning">
          <span>Reasoning</span>
          <p>{tradeReasoning(signal, { isForex })}</p>
        </section>

        <div className="graphite-detail-actions">
          <button type="button" className="ghost" onClick={onClose}>Close</button>
          {!signal.isHistory ? (
            <button
              type="button"
              disabled={activeTradeSaveState.status === "saving" || activeTradeSaveState.status === "saved" || terminalForexStatus}
              onClick={() => onTakeTrade(signal)}
            >
              {terminalForexStatus ? "Signal closed" : activeTradeSaveState.status === "saving" ? "Saving to History..." : activeTradeSaveState.status === "saved" ? "Saved to History" : "Take Trade"}
            </button>
          ) : null}
        </div>
        {activeTradeSaveState.status === "error" ? <p className="graphite-action-error" role="alert">{activeTradeSaveState.message}</p> : null}
      </article>
    </main>
  );
}

function HistoryScreen({ market, paperTrades = [], onSelect }) {
  const [marketFilter, setMarketFilter] = useState(market);
  const [statusFilter, setStatusFilter] = useState("all");
  useEffect(() => {
    setMarketFilter(market);
  }, [market]);
  const savedRows = paperTrades.map((trade) => {
    const lifecycleStatus = String(trade.lifecycle_status || "").toUpperCase();
    const resultR = trade.result_r ?? trade.pnl;
    const state = trade.lifecycle_label || (trade.status === "taken" ? "Open" : trade.status || "Open");
    const displayResult = resultR === null || resultR === undefined
      ? lifecycleStatus === "TP1_HIT" ? "TP1 Hit" : ["TP2_HIT", "STOPPED", "EXPIRED", "CANCELLED"].includes(lifecycleStatus) ? state : "Tracking"
      : `${Number(resultR) >= 0 ? "+" : ""}${Number(resultR).toFixed(1)}R`;
    return {
      symbol: trade.symbol,
      direction: trade.direction || "Trade",
      timeframe: trade.timeframe || "-",
      score: Math.round(Number(trade.confidence || trade.setup_score || 0)),
      result: trade.lifecycle_result || trade.result || "Open",
      filterStatus: lifecycleStatus === "STOPPED" ? "sl" : lifecycleStatus.toLowerCase(),
      state,
      r: displayResult,
      date: trade.taken_at ? new Date(trade.taken_at).toLocaleDateString([], { month: "short", day: "numeric" }) : "Today",
      market: String(trade.exchange || "").toLowerCase() === "forex" ? MARKET_TYPES.forex : MARKET_TYPES.crypto,
      id: trade.id,
      trade,
    };
  });
  const rows = savedRows
    .filter((item) => (marketFilter === "all" ? true : item.market === marketFilter))
    .filter((item) => statusFilter === "all" || item.filterStatus.includes(statusFilter) || item.result.toLowerCase().includes(statusFilter));
  const totalR = rows.reduce((sum, item) => {
    const value = Number.parseFloat(item.r);
    return Number.isFinite(value) ? sum + value : sum;
  }, 0);
  return (
    <div className="graphite-screen">
      <section className="graphite-history-hero">
        <span>{market === MARKET_TYPES.forex ? "Forex Total R" : "Total R"}</span>
        <strong>{`${totalR >= 0 ? "+" : ""}${totalR.toFixed(1)}R`}</strong>
        <p>{rows.length} tracked ideas · market filter active</p>
      </section>

      <div className="graphite-filter-row">
        {["all", MARKET_TYPES.crypto, MARKET_TYPES.forex].map((option) => <button key={option} type="button" className={marketFilter === option ? "active" : ""} onClick={() => setMarketFilter(option)}>{option}</button>)}
      </div>
      <div className="graphite-filter-row">
        {["all", "open", "tp1", "tp2", "sl", "expired"].map((option) => <button key={option} type="button" className={statusFilter === option ? "active" : ""} onClick={() => setStatusFilter(option)}>{option}</button>)}
      </div>

      <section className="graphite-timeline market-history-list">
        <span>Trade timeline</span>
        {rows.length === 0 ? (
          <GraphiteStateCard
            tone="empty"
            eyebrow="History filter"
            title="No matching trades"
            message={`No ${marketFilter} historical signals match the selected outcome.`}
          />
        ) : null}
        {rows.map((item) => (
          <button type="button" className="market-pair-row history" key={item.id || `${item.symbol}-${item.date}`} onClick={() => onSelect?.(item.trade)}>
            <i className={item.r.startsWith("-") || /short|sell/i.test(item.direction) ? "short" : "long"} aria-hidden="true" />
            <div className="market-pair-copy">
              <div>
                <strong>{item.symbol}</strong>
                <Direction direction={item.direction} />
              </div>
              <small>{item.timeframe.toUpperCase()} · {item.date} · {item.state}</small>
            </div>
            <span className={`market-pair-quality ${item.r.startsWith("-") ? "loss" : item.r === "Tracking" ? "" : "profit"}`}>
              <strong>{item.r}</strong>
              <small>{item.score} score</small>
            </span>
          </button>
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

export default function MobileDemo({
  topIdeas = [],
  pendingSetups = [],
  activeCryptoTrades = [],
  initialConfigLoading = false,
  initialTab = "",
  onRefreshCrypto,
  onNavigate,
}) {
  const auth = useAuth();
  const notificationCenter = useNotifications(auth.user?.id);
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
  const [forexStrength, setForexStrength] = useState(null);
  const [forexSignals, setForexSignals] = useState([]);
  const [forexLimitOpportunities, setForexLimitOpportunities] = useState([]);
  const [forexPairs, setForexPairs] = useState([]);
  const [forexFeedState, setForexFeedState] = useState({
    status: "idle",
    message: "",
    cached: false,
    lastUpdated: "",
  });
  const [forexScanError, setForexScanError] = useState("");
  const [forexReloadVersion, setForexReloadVersion] = useState(0);
  const [forexLoading, setForexLoading] = useState(false);
  const [forexScanLoading, setForexScanLoading] = useState(false);
  const [forexScanResult, setForexScanResult] = useState(null);
  const [paperTrades, setPaperTrades] = useState([]);
  const [tradeSaveState, setTradeSaveState] = useState({ signalId: "", status: "idle" });
  const signals = useMemo(() => {
    const active = activeCryptoTrades.map(normalizeActiveCryptoTrade);
    const confirmed = topIdeas.map(normalizeIdea);
    const pending = pendingSetups.map(normalizePendingSetup);
    const unique = new Map();
    [...active, ...confirmed, ...pending].forEach((signal) => {
      const key = `${signal.symbol}|${signal.timeframe}|${String(signal.direction).toLowerCase()}`;
      if (!unique.has(key)) unique.set(key, signal);
    });
    return [...unique.values()]
      .sort((a, b) => b.score - a.score)
      .slice(0, 50)
      .map((signal, index) => ({ ...signal, rank: index + 1 }));
  }, [activeCryptoTrades, pendingSetups, topIdeas]);
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
  const activeSignals = useMemo(
    () => preferredSignals.filter((signal) => signal.isActiveTrade),
    [preferredSignals],
  );
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
    const matchedSignal = [...signals, ...forexSignals].find(
      (signal) => String(signal.id || "").toLowerCase() === requestedDetail.toLowerCase()
        || signal.symbol.toLowerCase() === requestedDetail.toLowerCase(),
    );
    if (matchedSignal) setSelectedSignal(matchedSignal);
  }, [forexSignals, selectedSignal, signals]);

  useEffect(() => {
    if (!selectedSignal || selectedSignal.isHistory || selectedSignal.market !== MARKET_TYPES.forex || !selectedSignal.id) return undefined;
    let cancelled = false;
    const refreshPersistedSignal = () => {
      getForexSignal(selectedSignal.id)
        .then((signal) => {
          if (!cancelled) setSelectedSignal(normalizeForexSignal(signal));
        })
        .catch(() => {
          // Keep the last persisted snapshot visible during transient refresh failures.
        });
    };
    refreshPersistedSignal();
    const timer = window.setInterval(refreshPersistedSignal, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedSignal?.id, selectedSignal?.market]);

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
    if (tab !== "account" || accountView === "main") return undefined;

    const frame = window.requestAnimationFrame(() => {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    });

    return () => window.cancelAnimationFrame(frame);
  }, [accountView, tab]);

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
    const loadForex = async () => {
      setForexLoading(true);
      setForexFeedState((current) => ({ ...current, status: "loading", message: "", cached: false }));
      const [overviewResult, signalsResult, limitsResult, strengthResult] = await Promise.allSettled([
        getForexOverview(),
        getForexSignals(),
        getForexLimitOpportunities(),
        getForexCurrencyStrength("24H"),
      ]);
      if (cancelled) return;

      if (overviewResult.status === "fulfilled") {
        setForexOverview(overviewResult.value);
        setForexPairs(overviewResult.value?.supported_pairs || overviewResult.value?.supportedPairs || []);
      } else {
        console.error("SwiftChart Forex overview request failed", {
          kind: overviewResult.reason?.kind || "request",
          status: overviewResult.reason?.status,
          message: overviewResult.reason?.message,
        });
      }

      if (signalsResult.status === "fulfilled") {
        const data = signalsResult.value;
        const raw = data.signals || [];
        const normalized = raw.map(normalizeForexSignal);
        const updatedAt = new Date().toISOString();
        setForexSignals(normalized);
        setForexPairs((current) => current.length ? current : data.supported_pairs || data.supportedPairs || []);
        setForexFeedState({ status: "success", message: "", cached: false, lastUpdated: dt(updatedAt) });
        try {
          window.localStorage?.setItem(FOREX_SIGNAL_CACHE_KEY, JSON.stringify({ signals: raw, updatedAt }));
        } catch {
          // Public feed remains usable when storage is blocked.
        }
      } else {
        const error = signalsResult.reason;
        console.error("SwiftChart public Forex signal request failed", {
          kind: error?.kind || "request",
          status: error?.status,
          message: error?.message,
        });
        let cached = null;
        try {
          cached = JSON.parse(window.localStorage?.getItem(FOREX_SIGNAL_CACHE_KEY) || "null");
        } catch {
          cached = null;
        }
        if (Array.isArray(cached?.signals)) setForexSignals(cached.signals.map(normalizeForexSignal));
        const authUnavailable = error?.kind === "authentication"
          || /authentication service|session/i.test(String(error?.message || ""));
        const networkUnavailable = error?.kind === "network";
        setForexFeedState({
          status: authUnavailable ? "auth_unavailable" : networkUnavailable ? "network_unavailable" : "api_unavailable",
          message: networkUnavailable
            ? "The Forex signal service could not be reached. Check your connection and retry."
            : "The Forex signal service is temporarily unavailable. Please retry.",
          cached: Array.isArray(cached?.signals),
          lastUpdated: cached?.updatedAt ? dt(cached.updatedAt) : "",
        });
      }
      if (limitsResult.status === "fulfilled") {
        setForexLimitOpportunities(limitsResult.value?.opportunities || []);
      }
      if (strengthResult.status === "fulfilled") {
        setForexStrength(strengthResult.value);
      }
      if (!cancelled) setForexLoading(false);
    };
    loadForex();
    const refreshTimer = window.setInterval(loadForex, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(refreshTimer);
    };
  }, [activeMarket, forexReloadVersion]);

  useEffect(() => {
    const signalMatch = window.location.pathname.match(/^\/app\/signal\/([^/]+)$/i);
    if (!signalMatch) return;
    let cancelled = false;
    const signalId = decodeURIComponent(signalMatch[1]);
    setActiveMarketState(MARKET_TYPES.forex);
    window.localStorage?.setItem("swiftchart.activeMarket", MARKET_TYPES.forex);
    setTab("scan");
    getForexSignal(signalId)
      .then((signal) => {
        if (cancelled) return;
        const normalized = normalizeForexSignal(signal);
        setForexSignals((current) => current.some((item) => item.id === normalized.id)
          ? current
          : [normalized, ...current]);
        setSelectedSignal(normalized);
      })
      .catch((error) => {
        if (!cancelled) {
          console.error("SwiftChart Forex signal detail request failed", {
            kind: error?.kind || "request",
            status: error?.status,
            message: error?.message,
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function scanForexNow(timeframe) {
    if (forexScanLoading) return null;
    if (!auth.session?.access_token) {
      const returnTo = `${window.location.pathname}${window.location.search}`;
      window.location.assign(`/login?returnTo=${encodeURIComponent(returnTo)}`);
      return null;
    }
    setForexScanLoading(true);
    setForexScanError("");
    try {
      const result = await runForexScan(canonicalForexTimeframe(timeframe), auth.session.access_token);
      setForexScanResult(result);
      const persisted = [...(result.created || []), ...(result.reused || [])]
        .map(normalizeForexSignal);
      if (persisted.length) {
        setForexSignals((current) => {
          const merged = new Map(current.map((signal) => [signal.id, signal]));
          persisted.forEach((signal) => merged.set(signal.id, signal));
          return [...merged.values()].sort((a, b) => b.score - a.score);
        });
      }
      return result;
    } catch (error) {
      console.error("SwiftChart authenticated Forex scan failed", {
        kind: error?.kind || "request",
        status: error?.status,
        message: error?.message,
      });
      setForexScanError(error.message || "Forex scan could not complete.");
      return null;
    } finally {
      setForexScanLoading(false);
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
      if (preferences.notifications.tradeOpened) {
        const market = normalizeMarket(signal.market);
        pushSwiftChartNotification({
          id: `trade-opened-${saved.id || signalId}`,
          type: "trade_opened",
          category: "trades",
          title: signal.symbol,
          message: `${String(signal.direction || "Trade").toUpperCase()} saved to History.`,
          symbol: signal.symbol,
          direction: signal.direction,
          timestamp: saved.created_at || new Date().toISOString(),
          read: false,
          priority: "normal",
          statusBadge: "Trade Opened",
          actionUrl: `/app/history?market=${market}`,
        });
      }
      return saved;
    } catch (error) {
      setTradeSaveState({ signalId, symbol: signal.symbol, status: "error", message: error.message || "Could not save trade to History." });
      return undefined;
    }
  }

  async function takeTrade(signal) {
    return saveTradeToHistory(signal);
  }

  async function openHistoryTrade(trade) {
    setSelectedSignal(normalizeSavedTrade(trade));
    if (!trade?.id || !auth.user?.id) return;
    try {
      const freshTrade = await getPaperTradeDetail(trade.id, auth.user.id, auth.session?.access_token);
      setPaperTrades((current) => current.map((item) => item.id === freshTrade.id ? freshTrade : item));
      setSelectedSignal(normalizeSavedTrade(freshTrade));
    } catch (error) {
      console.error("SwiftChart saved trade detail request failed", {
        kind: error?.kind || "request",
        status: error?.status,
        message: error?.message,
      });
    }
  }

  function selectTab(nextTab) {
    if (!auth.isAuthenticated && (nextTab === "history" || nextTab === "account")) {
      const nextUrl = new URL(window.location.href);
      nextUrl.pathname = `/app/${nextTab}`;
      nextUrl.searchParams.set("market", activeMarket);
      window.location.assign(`/login?returnTo=${encodeURIComponent(`${nextUrl.pathname}${nextUrl.search}`)}`);
      return;
    }
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

  function closeTradeAnalysis() {
    setSelectedSignal(null);
    const nextUrl = new URL(window.location.href);
    if (!nextUrl.searchParams.has("detail")) return;
    nextUrl.searchParams.delete("detail");
    window.history.replaceState({}, "", `${nextUrl.pathname}${nextUrl.search}`);
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
      ? [...signals, ...forexSignals].find(
        (signal) => String(signal.id || "").toLowerCase() === detailSymbol.toLowerCase()
          || signal.symbol.toLowerCase() === detailSymbol.toLowerCase(),
      ) || null
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
          {selectedSignal ? (
            <TradeAnalysis
              signal={selectedSignal}
              onClose={closeTradeAnalysis}
              onTakeTrade={takeTrade}
              tradeSaveState={tradeSaveState}
            />
          ) : <>
          <main className="graphite-content">
            {tab === "home" ? <HomeScreen signals={preferredSignals} activeSignals={activeSignals} onSelect={setSelectedSignal} onViewAll={() => selectTab("scan")} market={activeMarket} forexSignals={forexSignals} forexOverview={forexOverview} forexStrength={forexStrength} onViewStrength={() => onNavigate?.("/app/forex/currency-strength")} onSelectPair={(symbol) => onNavigate?.(`/app/forex/${symbol}`)} /> : null}
            {tab === "scan" ? <ScanScreen signals={signals} onSelect={setSelectedSignal} market={activeMarket} forexSignals={forexSignals} onCryptoScan={onRefreshCrypto} onForexScan={scanForexNow} cryptoLoading={initialConfigLoading} scanningForex={forexScanLoading} forexScanResult={forexScanResult} forexFeedState={forexFeedState} forexScanError={forexScanError} onRetryForex={() => setForexReloadVersion((value) => value + 1)} tradingPreferences={preferences.trading} forexPairs={forexPairs} onSelectPair={(symbol) => onNavigate?.(`/app/forex/${symbol}`)} /> : null}
            {tab === "history" ? <HistoryScreen market={activeMarket} paperTrades={paperTrades} onSelect={openHistoryTrade} /> : null}
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
          </>}
      </>
    </section>
  );
}
