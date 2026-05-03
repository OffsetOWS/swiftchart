const items = [
  ["Does SwiftChart place trades automatically?", "No. SwiftChart is analysis-only and paper-trading first. It does not execute live trades."],
  ["Is this financial advice?", "No. SwiftChart is for education and analysis. You control your risk and trading decisions."],
  ["What markets does it support?", "The current app focuses on crypto pairs through Binance and Hyperliquid connectors, with forex positioning in the product direction."],
  ["Can I use it on mobile?", "Yes. The website is responsive, and the Telegram bot is designed for fast mobile analysis."],
  ["Does it work with Telegram?", "Yes. SwiftChart Bot can return analysis, top setups, and strategy explanations directly in Telegram."],
];

export default function FAQ() {
  return (
    <section id="faq" className="section faq-section">
      <div className="section-heading">
        <span className="eyebrow">FAQ</span>
        <h2>Common questions.</h2>
      </div>
      <div className="faq-list">
        {items.map(([question, answer]) => (
          <details key={question} className="faq-item">
            <summary>{question}</summary>
            <p>{answer}</p>
          </details>
        ))}
      </div>
    </section>
  );
}
