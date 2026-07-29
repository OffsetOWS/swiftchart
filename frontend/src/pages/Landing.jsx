import swiftChartMark from "../assets/swiftchart-s-mark.png";

export default function Landing() {
  return (
    <main className="landing-home" aria-label="SwiftChart">
      <div className="landing-flow landing-flow-a" aria-hidden="true" />
      <div className="landing-flow landing-flow-b" aria-hidden="true" />
      <div className="landing-flow landing-flow-c" aria-hidden="true" />

      <section className="landing-home-hero">
        <div className="landing-mark-shell">
          <img src={swiftChartMark} alt="SwiftChart" />
        </div>
        <a className="landing-launch-button" href="/login?returnTo=%2Fapp%2Fhome">
          Launch App
        </a>
      </section>
    </main>
  );
}
