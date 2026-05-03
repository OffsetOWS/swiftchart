export default function Strategy() {
  return (
    <div className="explain">
      <section className="panel hero-panel strategy-hero">
        <span className="eyebrow">SWIFTCHART PLAYBOOK</span>
        <h1>Buy support. Sell resistance. Avoid the middle.</h1>
        <p style={{ marginTop: 14 }}>
          The engine classifies regime first, scores support and resistance quality, checks higher-timeframe bias, and only shows setups that clear the 65/100 quality threshold.
        </p>
        <div className="risk-strip">Every output is a potential setup, not a profit promise.</div>
      </section>
      <section className="panel ideas-panel">
        <span className="eyebrow">RULE ENGINE</span>
        <h2>Strategy Rules</h2>
        <div className="steps" style={{ marginTop: 14 }}>
          {[
            ["1", "Detect recent swing highs and lows, then cluster them into horizontal support and resistance zones."],
            ["2", "Classify whether price is RANGE_BOUND, TRENDING_UP, TRENDING_DOWN, BREAKOUT, BREAKDOWN, CHOP, or NO_TRADE."],
            ["3", "Divide ranges into bottom 25%, middle 50%, and top 25%, then reject mid-range trades."],
            ["4", "Treat stop hunts as valid only after sweep, close back inside, next-candle confirmation, and failed continuation."],
            ["5", "Use higher-timeframe bias and volatility-adjusted stops before scoring the setup."],
            ["6", "Only show trade ideas with a setup score of 65/100 or higher."],
          ].map(([number, text]) => (
            <div className="step" key={number}><b>{number}</b><p>{text}</p></div>
          ))}
        </div>
      </section>
    </div>
  );
}
