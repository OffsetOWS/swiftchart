import { RefreshCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { getCandles } from "../lib/api.js";
import { listPaperTrades, updatePaperTradeStatus } from "../lib/paperTrades.js";
import { useAuth } from "../lib/AuthContext.jsx";

function fmt(value) {
  if (value === undefined || value === null || value === "") return "-";
  if (typeof value === "number") return Number(value).toLocaleString(undefined, { maximumFractionDigits: 4 });
  return value;
}

function dt(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function badgeClass(value) {
  if (["tp_hit", "win"].includes(value)) return "good";
  if (["sl_hit", "loss"].includes(value)) return "bad";
  if (["closed"].includes(value)) return "warn";
  return "neutral";
}

function timeframeForTrade(_trade) {
  return _trade.timeframe || "4h";
}

function tradeStatusFromCandles(trade, candles) {
  const takenAt = new Date(trade.created_at).getTime();
  const relevant = candles.filter((candle) => new Date(candle.timestamp).getTime() >= takenAt);
  if (!relevant.length || trade.status !== "open") return null;

  const entry = Number(trade.entry_price);
  const stop = Number(trade.stop_loss);
  const target = Number(trade.take_profit);
  const risk = Math.abs(entry - stop) || 1;
  const isLong = trade.direction === "long";

  for (const candle of relevant) {
    const high = Number(candle.high);
    const low = Number(candle.low);
    const hitTarget = isLong ? high >= target : low <= target;
    const hitStop = isLong ? low <= stop : high >= stop;

    if (hitTarget && hitStop) {
      return { status: "closed", result: "closed", pnl: 0 };
    }
    if (hitTarget) {
      return { status: "tp_hit", result: "win", pnl: Number((Math.abs(target - entry) / risk).toFixed(2)) };
    }
    if (hitStop) {
      return { status: "sl_hit", result: "loss", pnl: -1 };
    }
  }
  return null;
}

export default function TradeHistory({ version = 0 }) {
  const auth = useAuth();
  const [records, setRecords] = useState([]);
  const [expanded, setExpanded] = useState(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const stats = useMemo(() => {
    const total = records.length;
    const wins = records.filter((record) => record.result === "win").length;
    const losses = records.filter((record) => record.result === "loss").length;
    const open = records.filter((record) => record.status === "open").length;
    const closed = wins + losses;
    const winRate = closed ? Math.round((wins / closed) * 100) : 0;
    const pnl = records.reduce((sum, record) => sum + (Number(record.pnl) || 0), 0);
    return { total, wins, losses, open, winRate, pnl: Number(pnl.toFixed(2)) };
  }, [records]);

  async function load() {
    if (!auth.user?.id) {
      setRecords([]);
      setMessage("Sign in to view your paper trade history.");
      return;
    }
    setLoading(true);
    setMessage("");
    try {
      const trades = await listPaperTrades(auth.user.id);
      setRecords(trades);
      setMessage(trades.length ? `Loaded ${trades.length} paper trades.` : "No paper trades yet. Click Take Trade on a signal to save one here.");
    } catch (error) {
      setMessage(`Could not load paper trades: ${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function refreshStatuses() {
    if (!auth.user?.id) return;
    setLoading(true);
    setMessage("");
    try {
      const openTrades = records.filter((trade) => trade.status === "open");
      const updates = [];
      for (const trade of openTrades) {
        const candles = await getCandles({ exchange: trade.exchange || "hyperliquid", symbol: trade.symbol, timeframe: timeframeForTrade(trade) });
        const statusUpdate = tradeStatusFromCandles(trade, candles);
        if (statusUpdate) {
          updates.push(updatePaperTradeStatus(trade.id, statusUpdate));
        }
      }
      if (updates.length) {
        await Promise.all(updates);
        await load();
        setMessage(`Updated ${updates.length} paper trades.`);
      } else {
        setMessage("No open paper trades hit TP or SL yet.");
      }
    } catch (error) {
      setMessage(`Could not update paper trades: ${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [auth.user?.id, version]);

  return (
    <div className="history-page">
      <section className="panel hero-panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">PAPER TRADE MEMORY</span>
            <h2>Trade History</h2>
            <p>Every signal you click with Take Trade is saved here as paper trading history. No real orders are placed.</p>
          </div>
          <button className="primary" onClick={refreshStatuses} disabled={loading || !records.length}>
            <RefreshCcw size={16} /> {loading ? "Checking..." : "Check TP / SL"}
          </button>
        </div>
        <div className="stats history-stats">
          <div className="stat"><span>Paper trades</span><b>{stats.total}</b></div>
          <div className="stat"><span>Open</span><b>{stats.open}</b></div>
          <div className="stat"><span>Wins</span><b>{stats.wins}</b></div>
          <div className="stat"><span>Losses</span><b>{stats.losses}</b></div>
          <div className="stat"><span>Win rate</span><b>{stats.winRate}%</b></div>
          <div className="stat"><span>Total R</span><b>{stats.pnl}R</b></div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">MANUAL PAPER TRADES</span>
            <h2>Trades You Took</h2>
          </div>
          <button className="secondary" onClick={load} disabled={loading}>Refresh</button>
        </div>

        <div className="history-table-wrap">
          {message ? <div className="history-state">{message}</div> : null}
          <table className="history-table">
            <thead>
              <tr>
                <th>Date taken</th><th>Pair</th><th>TF</th><th>Direction</th><th>Entry</th><th>Stop loss</th><th>Take profit</th><th>R:R</th><th>Confidence</th><th>Bias</th><th>Status</th><th>Result</th><th>PnL</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <tr key={record.id} onClick={() => setExpanded(expanded === record.id ? null : record.id)}>
                  <td>{dt(record.created_at)}</td>
                  <td>{record.symbol}</td>
                  <td>{record.timeframe || "-"}</td>
                  <td>{record.direction.toUpperCase()}</td>
                  <td>{fmt(record.entry_price)}</td>
                  <td>{fmt(record.stop_loss)}</td>
                  <td>{fmt(record.take_profit)}</td>
                  <td>{fmt(record.risk_reward)}R</td>
                  <td>{fmt(record.confidence)}%</td>
                  <td>{record.market_bias || "-"}</td>
                  <td><span className={`outcome-badge ${badgeClass(record.status)}`}>{record.status}</span></td>
                  <td><span className={`outcome-badge ${badgeClass(record.result)}`}>{record.result}</span></td>
                  <td>{record.pnl === null || record.pnl === undefined ? "-" : `${fmt(record.pnl)}R`}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {records.length === 0 ? <div className="empty">No paper trades yet.</div> : null}
        </div>
      </section>
    </div>
  );
}
