import { AlertTriangle, Clock3, Globe2, RefreshCcw, ShieldAlert, Waves } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { getForexOverview, getForexSignals } from "../lib/api.js";
import "../styles/forex.css";

function fmt(value, digits = 5) {
  if (value === undefined || value === null || value === "") return "-";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: Math.min(2, digits) });
}

function dt(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function normalizeSignal(signal) {
  return {
    ...signal,
    pair: signal.symbol || signal.pair,
    score: signal.setup_score ?? signal.score,
    grade: signal.grade || (signal.setup_score >= 90 ? "A+" : signal.setup_score >= 80 ? "A" : "B"),
    session: signal.market_session || signal.session,
    pre_session_bias: signal.timeframe_alignment || signal.htf_bias,
    entry: signal.entry_price ?? signal.entry,
    stopLoss: signal.stop_loss ?? signal.stopLoss,
    tp1: signal.take_profit_1 ?? signal.tp1,
    tp2: signal.take_profit_2 ?? signal.tp2,
    rr: signal.risk_reward_2 ?? signal.rr,
    spreadStatus: signal.spread_status || signal.spreadStatus,
    newsRisk: signal.news_risk || signal.newsRisk,
    lastUpdated: signal.last_price_updated_at || signal.created_at || signal.lastUpdated,
    reason: signal.setup_reason || signal.reason,
  };
}

function ForexSignalCard({ signal }) {
  const direction = String(signal.direction || "WAIT").toLowerCase();
  return (
    <article className={`forex-signal-card ${direction}`}>
      <div className="forex-signal-head">
        <div>
          <span className="eyebrow">FOREX SETUP</span>
          <h3>{signal.pair}</h3>
        </div>
        <b className={`forex-direction ${direction}`}>{signal.direction}</b>
      </div>
      <div className="forex-score-row">
        <span>Score <b>{Math.round(signal.score || 0)}</b></span>
        <span>Grade <b>{signal.grade}</b></span>
        <span>Session <b>{signal.session}</b></span>
      </div>
      <div className="forex-bias-strip">
        <Waves size={15} />
        <span>{signal.pre_session_bias || "Bias pending"}</span>
      </div>
      <div className="forex-level-grid">
        <div><span>Entry</span><b>{fmt(signal.entry)}</b></div>
        <div><span>Stop loss</span><b>{fmt(signal.stopLoss)}</b></div>
        <div><span>TP1</span><b>{fmt(signal.tp1)}</b></div>
        <div><span>TP2</span><b>{fmt(signal.tp2)}</b></div>
        <div><span>Risk/reward</span><b>{signal.rr ? `${signal.rr}R` : "-"}</b></div>
        <div><span>Spread</span><b>{signal.spreadStatus}</b></div>
        <div><span>News risk</span><b>{signal.newsRisk}</b></div>
        <div><span>Last updated</span><b>{dt(signal.lastUpdated)}</b></div>
      </div>
      <p>{signal.reason}</p>
    </article>
  );
}

export default function Forex() {
  const [overview, setOverview] = useState(null);
  const [signals, setSignals] = useState([]);
  const [topSetups, setTopSetups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [overviewData, signalData] = await Promise.all([getForexOverview(), getForexSignals()]);
      setOverview(overviewData);
      setSignals((signalData.signals || []).map(normalizeSignal));
      setTopSetups((overviewData.top_setups || []).map(normalizeSignal));
    } catch (err) {
      setError(err.message || "Could not load Forex Mode.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const session = overview?.active_session || overview?.activeSession;
  const supportedPairs = overview?.supported_pairs || overview?.supportedPairs || [];
  const message = error || overview?.message;
  const cleanSignals = useMemo(() => (signals.length ? signals : topSetups), [signals, topSetups]);

  return (
    <div className="forex-page">
      <section className="panel forex-hero">
        <div>
          <span className="eyebrow">FOREX MODE</span>
          <h2>Session-aware FX scanner</h2>
          <p>Dedicated forex logic for major pairs, session timing, spread safety, news risk, and risk-defined setups.</p>
        </div>
        <span className="forex-scan-button"><RefreshCcw size={17} /> Scheduled scanner</span>
      </section>

      {message ? (
        <section className={`forex-empty-state ${error ? "error" : ""}`}>
          <AlertTriangle size={18} />
          <span>{message}</span>
        </section>
      ) : null}

      <section className="forex-overview-grid">
        <article className="panel forex-overview-card">
          <Globe2 size={18} />
          <span>Market overview</span>
          <b>{overview?.configured === false ? "Provider not configured" : "Major pairs only"}</b>
          <small>EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, XAUUSD</small>
        </article>
        <article className="panel forex-overview-card">
          <Clock3 size={18} />
          <span>Active session</span>
          <b>{session?.active_session || "-"}</b>
          <small>{session?.label || "Loading session state..."}</small>
        </article>
        <article className="panel forex-overview-card">
          <Clock3 size={18} />
          <span>Next session open</span>
          <b>{session?.next_session || "-"}</b>
          <small>{session?.next_session_open ? dt(session.next_session_open) : "-"}</small>
        </article>
        <article className="panel forex-overview-card">
          <ShieldAlert size={18} />
          <span>Pre-session scan</span>
          <b>{overview?.preSessionScanStatus || "-"}</b>
          <small>{overview?.newsRiskWarning || "News risk placeholder: LOW."}</small>
        </article>
      </section>

      <section className="panel forex-supported">
        <div className="panel-head">
          <div>
            <span className="eyebrow">SUPPORTED FOREX PAIRS</span>
            <h2>Majors and gold only</h2>
          </div>
          <span className="badge">FOREX</span>
        </div>
        <div className="forex-pair-list">
          {supportedPairs.map((pair) => (
            <span key={pair.pair}>{pair.pair}<small>{pair.sessions.join(" / ")}</small></span>
          ))}
        </div>
      </section>

      <section className="forex-signals-section">
        <div className="panel-head forex-section-head">
          <div>
            <span className="eyebrow">TOP FOREX SETUPS</span>
            <h2>Risk-defined signal cards</h2>
          </div>
          {loading ? <span className="badge refresh-badge">Loading...</span> : <span className="badge">{topSetups.length} active</span>}
        </div>
        <div className="forex-signal-list">
          {!loading && cleanSignals.length === 0 ? <div className="empty">No clean forex setups right now.</div> : null}
          {cleanSignals.map((signal) => <ForexSignalCard key={`${signal.pair}-${signal.lastUpdated}`} signal={signal} />)}
        </div>
      </section>
    </div>
  );
}
