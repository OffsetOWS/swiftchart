import { RefreshCcw } from "lucide-react";
import { Fragment } from "react";
import { useEffect, useState } from "react";
import { checkTradeHistory, getTradeHistory, getTradeStats } from "../lib/api.js";

const EMPTY_FILTERS = { symbol: "", timeframe: "", exchange: "all", direction: "", status: "", result: "", date_from: "", date_to: "", sort: "desc" };

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
  if (["TP1_HIT", "TP2_HIT", "WIN", "PARTIAL_WIN"].includes(value)) return "good";
  if (["SL_HIT", "LOSS"].includes(value)) return "bad";
  if (["AMBIGUOUS", "NO_ENTRY", "EXPIRED"].includes(value)) return "warn";
  return "neutral";
}

function bestLabel(items, key) {
  if (!items?.length) return "-";
  const top = items[0];
  return `${top[key]} (${top.win_rate}%)`;
}

export default function TradeHistory() {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [records, setRecords] = useState([]);
  const [stats, setStats] = useState(null);
  const [pagination, setPagination] = useState({ page: 1, limit: 20, total: 0, pages: 0 });
  const [expanded, setExpanded] = useState(null);
  const [loading, setLoading] = useState(false);

  async function load(page = pagination.page) {
    setLoading(true);
    try {
      const [historyData, statsData] = await Promise.all([getTradeHistory({ ...filters, page, limit: pagination.limit }), getTradeStats()]);
      const returnedRecords = Array.isArray(historyData) ? historyData : historyData.records;
      setRecords(returnedRecords || []);
      if (!Array.isArray(historyData)) {
        setPagination({ page: historyData.page, limit: historyData.limit, total: historyData.total, pages: historyData.pages });
      }
      setStats(statsData);
    } finally {
      setLoading(false);
    }
  }

  async function runCheck() {
    setLoading(true);
    try {
      await checkTradeHistory();
      await load();
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="history-page">
      <section className="panel hero-panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">TRADE MEMORY</span>
            <h2>Trade History</h2>
            <p>Review saved SwiftChart ideas and check whether entries, take profits, or stops were confirmed by later candles.</p>
          </div>
          <button className="primary" onClick={runCheck}><RefreshCcw size={16} /> Check outcomes</button>
        </div>
        <div className="stats history-stats">
          <div className="stat"><span>Total analyzed setups</span><b>{stats?.total_ideas ?? 0}</b></div>
          <div className="stat"><span>Win rate</span><b>{stats?.win_rate ?? 0}%</b></div>
          <div className="stat"><span>TP hit rate</span><b>{stats?.tp_hit_rate ?? 0}%</b></div>
          <div className="stat"><span>SL hit rate</span><b>{stats?.sl_hit_rate ?? 0}%</b></div>
          <div className="stat"><span>Average R</span><b>{stats?.average_r_multiple ?? 0}</b></div>
          <div className="stat"><span>Open setups</span><b>{stats?.open_count ?? 0}</b></div>
        </div>
        <div className="stats history-stats compact">
          <div className="stat"><span>Best timeframe</span><b>{bestLabel(stats?.best_timeframe_performance, "timeframe")}</b></div>
          <div className="stat"><span>Best coin</span><b>{bestLabel(stats?.best_symbol_performance, "symbol")}</b></div>
          <div className="stat"><span>Best grade</span><b>{bestLabel(stats?.best_setup_grade_performance, "setup_grade")}</b></div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">FILTERS</span>
            <h2>Past Ideas</h2>
          </div>
          <button className="secondary" onClick={() => setFilters(EMPTY_FILTERS)}>Clear</button>
        </div>
        <div className="history-filters">
          <input placeholder="Symbol" value={filters.symbol} onChange={(e) => setFilters({ ...filters, symbol: e.target.value.toUpperCase() })} />
          <select value={filters.exchange} onChange={(e) => setFilters({ ...filters, exchange: e.target.value })}>
            <option value="all">All exchanges</option>
            <option value="binance">Binance</option>
            <option value="hyperliquid">Hyperliquid</option>
          </select>
          <select value={filters.timeframe} onChange={(e) => setFilters({ ...filters, timeframe: e.target.value })}>
            <option value="">Any timeframe</option>
            {["30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"].map((tf) => <option key={tf}>{tf}</option>)}
          </select>
          <select value={filters.direction} onChange={(e) => setFilters({ ...filters, direction: e.target.value })}>
            <option value="">Any direction</option>
            <option value="LONG">LONG</option>
            <option value="SHORT">SHORT</option>
          </select>
          <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
            <option value="">Any status</option>
            {["PENDING", "ENTRY_TRIGGERED", "TP1_HIT", "TP2_HIT", "SL_HIT", "EXPIRED", "INVALIDATED", "AMBIGUOUS"].map((item) => <option key={item}>{item}</option>)}
          </select>
          <select value={filters.result} onChange={(e) => setFilters({ ...filters, result: e.target.value })}>
            <option value="">Any result</option>
            {["WIN", "PARTIAL_WIN", "LOSS", "NO_ENTRY", "AMBIGUOUS", "OPEN"].map((item) => <option key={item}>{item}</option>)}
          </select>
          <input type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} />
          <input type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} />
          <select value={filters.sort} onChange={(e) => setFilters({ ...filters, sort: e.target.value })}>
            <option value="desc">Newest first</option>
            <option value="asc">Oldest first</option>
          </select>
          <button className="primary" onClick={() => load(1)}>{loading ? "Loading..." : "Apply"}</button>
        </div>

        <div className="history-table-wrap">
          <table className="history-table">
            <thead>
              <tr>
                <th>Date</th><th>Exchange</th><th>Symbol</th><th>TF</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP1</th><th>TP2</th><th>Score</th><th>Status</th><th>Result</th><th>R</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <Fragment key={record.id}>
                  <tr onClick={() => setExpanded(expanded === record.id ? null : record.id)}>
                    <td>{dt(record.created_at)}</td>
                    <td>{record.exchange}</td>
                    <td>{record.symbol}</td>
                    <td>{record.timeframe}</td>
                    <td>{record.direction}</td>
                    <td>{fmt(record.entry_zone_low)} - {fmt(record.entry_zone_high)}</td>
                    <td>{fmt(record.stop_loss)}</td>
                    <td>{fmt(record.take_profit_1)}</td>
                    <td>{fmt(record.take_profit_2)}</td>
                    <td>{fmt(record.setup_score)}</td>
                    <td><span className={`outcome-badge ${badgeClass(record.status)}`}>{record.status}</span></td>
                    <td><span className={`outcome-badge ${badgeClass(record.result)}`}>{record.result}</span></td>
                    <td>{fmt(record.pnl_r_multiple)}</td>
                  </tr>
                  {expanded === record.id ? (
                    <tr className="detail-row">
                      <td colSpan="13">
                        <div className="history-detail">
                          <p><b>Regime:</b> {record.market_regime || "-"} | <b>HTF:</b> {record.higher_timeframe_bias || "-"}</p>
                          <p><b>Reason:</b> {record.reason}</p>
                          <p><b>Invalidation:</b> {record.invalidation}</p>
                          <p><b>Entry:</b> {dt(record.entry_triggered_at)} | <b>Closed:</b> {dt(record.closed_at)} | <b>Checked:</b> {dt(record.outcome_checked_at)}</p>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
          {records.length === 0 ? <div className="empty">No saved trade ideas match these filters.</div> : null}
        </div>
        <div className="history-pagination">
          <span>{pagination.total} records · page {pagination.page || 1} of {pagination.pages || 1}</span>
          <div>
            <button className="secondary" disabled={loading || pagination.page <= 1} onClick={() => load(pagination.page - 1)}>Previous</button>
            <button className="secondary" disabled={loading || pagination.page >= pagination.pages} onClick={() => load(pagination.page + 1)}>Next</button>
          </div>
        </div>
      </section>
    </div>
  );
}
