import { ChevronRight } from "lucide-react";
import "../styles/currencyStrength.css";

const CURRENCY_FLAGS = {
  USD: "🇺🇸",
  EUR: "🇪🇺",
  GBP: "🇬🇧",
  JPY: "🇯🇵",
  CHF: "🇨🇭",
  CAD: "🇨🇦",
  AUD: "🇦🇺",
  NZD: "🇳🇿",
};

function tone(score) {
  if (score == null || !Number.isFinite(Number(score))) return "missing";
  if (Number(score) >= 55) return "strong";
  if (Number(score) <= 45) return "weak";
  return "neutral";
}

export function CurrencyStrengthRanking({ rows = [], compact = false }) {
  return (
    <div className={`currency-strength-ranking ${compact ? "compact" : ""}`}>
      {rows.map((row) => {
        const score = Number(row.normalized_score);
        const available = row.normalized_score != null && Number.isFinite(score);
        const filledSegments = available ? Math.max(1, Math.min(6, Math.ceil(score / (100 / 6)))) : 0;
        return (
          <article className={`currency-strength-row ${tone(row.normalized_score)}`} key={row.currency}>
            <span className="currency-strength-rank">{row.rank}</span>
            <span className="currency-strength-flag" aria-hidden="true">{CURRENCY_FLAGS[row.currency] || "🌐"}</span>
            <strong>{row.currency}</strong>
            <div className="currency-strength-track" aria-label={`${row.currency} strength ${available ? score.toFixed(1) : "unavailable"}`}>
              {Array.from({ length: 6 }, (_, index) => (
                <span className={index < filledSegments ? "filled" : ""} key={index} aria-hidden="true" />
              ))}
            </div>
            <b>{available ? score.toFixed(1) : "—"}</b>
            {!compact ? <small>{row.available_pairs}/{row.total_pairs} pairs</small> : null}
          </article>
        );
      })}
    </div>
  );
}

export function CurrencyStrengthOpportunities({ opportunities = [], limit = 6, onSelectPair, compact = false }) {
  const rows = opportunities.slice(0, limit);
  if (!rows.length) return <p className="currency-strength-empty">Not enough complete cross data for pair opportunities.</p>;
  return (
    <div className={`currency-strength-opportunities ${compact ? "compact" : ""}`}>
      {rows.map((row) => (
        <button key={row.pair} type="button" onClick={() => onSelectPair?.(row.pair)}>
          <span><strong>{row.pair}</strong><small>{row.strong_currency} vs {row.weak_currency}</small></span>
          <em className={row.direction === "BUY" ? "buy" : "sell"}>{row.direction}</em>
          <b>{Number.isFinite(Number(row.strength_gap)) ? `Δ ${Number(row.strength_gap).toFixed(1)}` : "—"}</b>
          {onSelectPair ? <ChevronRight size={16} aria-hidden="true" /> : null}
        </button>
      ))}
    </div>
  );
}
