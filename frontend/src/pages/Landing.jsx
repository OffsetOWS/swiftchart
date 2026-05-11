import { Menu, X } from "lucide-react";
import { useState } from "react";
import swiftChartLogo from "../assets/swiftchart-logo.png";

const navLinks = [
  ["Docs", "#docs"],
  ["X / Twitter", "#twitter"],
  ["Telegram", "https://t.me/SwiftChartBot"],
  ["Discord", "#discord"],
];

export default function Landing() {
  const [menuOpen, setMenuOpen] = useState(window.location.hash === "#menu");

  return (
    <main className="landing-home">
      <header className="landing-home-nav">
        <a className="landing-home-brand" href="/" aria-label="SwiftChart home">
          <img src={swiftChartLogo} alt="" />
          <span>SwiftChart</span>
        </a>

        <nav aria-label="SwiftChart links">
          {navLinks.map(([label, href]) => (
            <a key={label} href={href} target={href.startsWith("http") ? "_blank" : undefined} rel={href.startsWith("http") ? "noreferrer" : undefined}>
              {label}
            </a>
          ))}
        </nav>

        <button className="landing-menu-button" type="button" onClick={() => setMenuOpen(true)} aria-label="Open menu">
          <Menu size={38} strokeWidth={1.8} />
        </button>
      </header>

      <div className={menuOpen ? "landing-mobile-menu open" : "landing-mobile-menu"} aria-hidden={!menuOpen}>
        <div className="landing-mobile-menu-head">
          <a className="landing-home-brand" href="/" aria-label="SwiftChart home">
            <img src={swiftChartLogo} alt="" />
            <span>SwiftChart</span>
          </a>
          <button type="button" onClick={() => setMenuOpen(false)} aria-label="Close menu">
            <X size={42} strokeWidth={1.7} />
          </button>
        </div>

        <nav aria-label="SwiftChart mobile links">
          {navLinks.map(([label, href]) => (
            <a key={label} href={href} target={href.startsWith("http") ? "_blank" : undefined} rel={href.startsWith("http") ? "noreferrer" : undefined}>
              {label}
            </a>
          ))}
        </nav>

        <div className="landing-mobile-actions">
          <a className="landing-mobile-launch" href="/launch">
            Launch App
          </a>
          <a className="landing-mobile-signup" href="/auth">
            Sign Up
          </a>
        </div>

        <p>© 2026 SwiftChart</p>
      </div>

      <section className="landing-home-hero" aria-labelledby="landing-home-title">
        <h1 id="landing-home-title">
          Top traders have an edge,
          <br />
          now their edge is yours
        </h1>
        <p>SwiftChart turns market structure, bias, and signal history into a cleaner decision flow.</p>
        <a className="landing-launch-button" href="/launch">
          Launch App
        </a>
      </section>
    </main>
  );
}
