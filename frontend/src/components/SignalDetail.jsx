import { Check, ChevronLeft } from "lucide-react";
import { buildTradeLadder } from "../lib/tradeLadder.js";
import InstrumentLogo from "./InstrumentLogo.jsx";
import "../styles/signalDetail.css";

function pairLabel(symbol) {
  const text = String(symbol || "");
  const match = text.match(/^(.+?)(USDT|USDC|USD)$/i);
  return match ? `${match[1]} / ${match[2]}` : text;
}

export function SignalTopBar({ signal, onBack, recency }) {
  const isShort = /short|sell/i.test(String(signal.direction || ""));
  return (
    <header className="sd-top">
      <button type="button" className="sd-back" onClick={onBack} aria-label="Back to signals">
        <ChevronLeft size={20} aria-hidden="true" />
      </button>
      <InstrumentLogo symbol={signal.symbol} marketType={signal.market} />
      <div className="sd-title">
        <h1>{signal.pair || pairLabel(signal.symbol)}</h1>
        <p>
          <span className={`sd-side ${isShort ? "short" : "long"}`}>{isShort ? "Short" : "Long"}</span>
          <span>{String(signal.timeframe || "").toUpperCase()}</span>
          <span aria-hidden="true">·</span>
          <span>{recency}</span>
        </p>
      </div>
    </header>
  );
}

export function TradeLadder({ signal, progress }) {
  const ladder = buildTradeLadder(signal, progress);
  if (!ladder.hasLevels) return null;
  return (
    <section className={`sd-ladder is-${progress.tone || "neutral"}`} aria-label="Trade plan">
      <div className="sd-ladder-head">
        <div>
          <span className="sd-eyebrow">Live price</span>
          <strong className="sd-price">{ladder.currentDisplay}</strong>
        </div>
        <span className={`sd-status is-${progress.tone || "neutral"}`}>{progress.label}</span>
      </div>
      <ol className="sd-rows">
        {ladder.rows.map((row) => (
          <li key={row.key} className={`sd-row is-${row.tone}${row.done ? " is-done" : ""}`}>
            <span className="sd-node" aria-hidden="true">{row.done ? <Check size={11} strokeWidth={3} /> : null}</span>
            <div className="sd-row-label">
              <strong>{row.label}</strong>
              {row.key === "now"
                ? <span>{!ladder.currentR || ladder.currentR === "0R" ? "At entry" : `${ladder.currentR} from entry`}</span>
                : row.done ? <span>Reached</span>
                : row.inside ? <span>Price is inside the zone</span>
                : row.distance ? <span>{row.distance} away</span> : null}
            </div>
            <div className="sd-row-value">
              <b>{row.display}</b>
              {row.r && row.key !== "now" ? <span>{row.r}</span> : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function SignalFacts({ rr, validity, regime }) {
  return (
    <dl className="sd-facts">
      <div><dt>Risk / reward</dt><dd>{rr}</dd></div>
      <div><dt>Valid until</dt><dd>{validity ? validity.when : "48h from entry"}{validity ? <small>{validity.left}</small> : null}</dd></div>
      {regime ? <div className="sd-facts-wide"><dt>Market regime</dt><dd>{regime}</dd></div> : null}
    </dl>
  );
}

export function AnalystNote({ text, invalidation }) {
  return (
    <section className="sd-note" aria-labelledby="sd-note-title">
      <span id="sd-note-title" className="sd-eyebrow">Why this trade</span>
      <p>{text}</p>
      {invalidation ? (
        <p className="sd-invalidation"><strong>Invalidated if</strong> {invalidation}</p>
      ) : null}
    </section>
  );
}
