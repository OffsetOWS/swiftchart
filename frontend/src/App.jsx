import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BarChart3, BookOpen, LayoutDashboard } from "lucide-react";
import Dashboard from "./pages/Dashboard.jsx";
import Analysis from "./pages/Analysis.jsx";
import Strategy from "./pages/Strategy.jsx";
import { createPaperTrade, getAnalysis, getCandles, getTopIdeas } from "./lib/api.js";
import "./styles/global.css";

export default function App() {
  const [page, setPage] = useState("dashboard");
  const [exchange, setExchange] = useState("hyperliquid");
  const [timeframe, setTimeframe] = useState("4h");
  const [symbol, setSymbol] = useState("SOLUSDT");
  const [risk, setRisk] = useState({ accountSize: 10000, riskPerTrade: 1, minRR: 2, maxOpenTrades: 3 });
  const [topIdeas, setTopIdeas] = useState([]);
  const [candles, setCandles] = useState([]);
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingTopIdeas, setLoadingTopIdeas] = useState(false);
  const [notice, setNotice] = useState("");

  async function refreshTopIdeas() {
    setLoadingTopIdeas(true);
    setNotice("");
    try {
      const data = await getTopIdeas({ exchange, timeframe });
      setTopIdeas(data.ideas || []);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setLoadingTopIdeas(false);
    }
  }

  async function runAnalysis() {
    setLoading(true);
    setNotice("");
    try {
      const [candleData, analysisData] = await Promise.all([
        getCandles({ exchange, symbol, timeframe }),
        getAnalysis({ exchange, symbol, timeframe, risk }),
      ]);
      setCandles(candleData);
      setAnalysis(analysisData);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function paperTrade(idea) {
    const entry = (idea.entry_zone[0] + idea.entry_zone[1]) / 2;
    await createPaperTrade({
      symbol: idea.symbol,
      timeframe: idea.timeframe,
      exchange: idea.exchange,
      direction: idea.direction,
      entry_price: entry,
      stop_loss: idea.stop_loss,
      take_profit_1: idea.take_profit_1,
      take_profit_2: idea.take_profit_2,
      size: idea.position_size_units || 0,
      notes: idea.reason,
    });
    setNotice("Paper trade saved.");
  }

  useEffect(() => {
    refreshTopIdeas();
  }, [exchange, timeframe]);

  useEffect(() => {
    runAnalysis();
  }, []);

  return (
    <main className="app-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <div className="cursor-aura" />
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" />
          <div><strong>SwiftChart</strong><span>AI-powered crypto charting</span></div>
        </div>
        <nav className="nav">
          <button className={page === "dashboard" ? "active" : ""} onClick={() => setPage("dashboard")}><LayoutDashboard size={15} /> Dashboard</button>
          <button className={page === "analysis" ? "active" : ""} onClick={() => setPage("analysis")}><BarChart3 size={15} /> Coin Analysis</button>
          <button className={page === "strategy" ? "active" : ""} onClick={() => setPage("strategy")}><BookOpen size={15} /> Strategy</button>
        </nav>
      </header>

      {notice ? <div className="risk-strip" style={{ marginBottom: 14 }}>{notice}</div> : null}

      {page === "dashboard" && (
        <Dashboard
          exchange={exchange}
          setExchange={setExchange}
          timeframe={timeframe}
          setTimeframe={setTimeframe}
          topIdeas={topIdeas}
          loadingTopIdeas={loadingTopIdeas}
          refreshTopIdeas={refreshTopIdeas}
        />
      )}
      {page === "analysis" && (
        <Analysis
          state={{ symbol, exchange, timeframe, risk }}
          setters={{ setSymbol, setExchange, setTimeframe, setRisk }}
          candles={candles}
          analysis={analysis}
          loading={loading}
          onAnalyze={runAnalysis}
          onPaperTrade={paperTrade}
        />
      )}
      {page === "strategy" && <Strategy />}
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
