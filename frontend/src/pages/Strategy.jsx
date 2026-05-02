export default function Strategy() {
  return (
    <div className="explain">
      <section className="panel">
        <h1>Buy support. Sell resistance. Avoid the middle.</h1>
        <p style={{ marginTop: 14 }}>
          The engine looks for clean ranges, waits for price to reach an edge, and prefers setups where liquidity has been swept or momentum confirms a breakout.
        </p>
        <div className="risk-strip">Every output is a potential setup, not a profit promise.</div>
      </section>
      <section className="panel">
        <h2>Strategy Rules</h2>
        <div className="steps" style={{ marginTop: 14 }}>
          {[
            ["1", "Detect recent swing highs and lows, then cluster them into horizontal support and resistance zones."],
            ["2", "Classify whether price is trending, range-bound, breaking out, breaking down, or sitting in no-man's-land."],
            ["3", "Look for longs near strong support and shorts near strong resistance."],
            ["4", "Treat stop hunts as useful only when price sweeps a level and reclaims or rejects it."],
            ["5", "Require volume or momentum for breakout and breakdown ideas."],
            ["6", "Skip unclear setups and mid-range entries by default."],
          ].map(([number, text]) => (
            <div className="step" key={number}><b>{number}</b><p>{text}</p></div>
          ))}
        </div>
      </section>
    </div>
  );
}
