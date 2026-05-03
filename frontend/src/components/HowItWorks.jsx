const steps = [
  ["01", "Send chart or pair", "Choose a symbol and timeframe, or use SwiftChart Bot from Telegram."],
  ["02", "SwiftChart analyzes structure", "The engine maps regime, zones, liquidity, momentum, and higher-timeframe bias."],
  ["03", "Get the trade plan", "Receive entry zone, stop loss, take profits, risk/reward, confidence, and invalidation."],
  ["04", "Track alerts on Telegram", "Keep your setup workflow mobile-first without living inside a terminal."],
];

export default function HowItWorks() {
  return (
    <section id="how-it-works" className="section">
      <div className="section-heading">
        <span className="eyebrow">Workflow</span>
        <h2>From chart to trade plan in seconds.</h2>
        <p>SwiftChart turns messy market structure into a readable, risk-first checklist.</p>
      </div>
      <div className="steps-grid">
        {steps.map(([number, title, text]) => (
          <article className="step-card" key={number}>
            <span>{number}</span>
            <h3>{title}</h3>
            <p>{text}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
