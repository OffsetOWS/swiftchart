import { useEffect, useState } from "react";
import { ArrowUpRight, Check, ScanLine } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import swiftChartLogo from "../assets/swiftchart-logo.png";
import { getPublicOrigin } from "../lib/siteUrl.js";

const DESKTOP_BREAKPOINT = 768;
const BYPASS_STORAGE_KEY = "swiftchart.desktopMobileGateBypassed.v1";
const PRODUCTION_APP_URL = import.meta.env.VITE_PRODUCTION_APP_URL || `${getPublicOrigin()}/app/home`;

function isDesktopViewport() {
  return window.innerWidth > DESKTOP_BREAKPOINT;
}

function hasRememberedBypass() {
  try {
    return window.localStorage?.getItem(BYPASS_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function PhonePreview() {
  return (
    <div className="desktop-mobile-phone" aria-hidden="true">
      <div className="desktop-mobile-phone-speaker" />
      <div className="desktop-mobile-phone-screen">
        <header>
          <span>SWIFTCHART</span>
          <i />
        </header>
        <section>
          <small>MARKET SUMMARY</small>
          <strong>97</strong>
          <p>BTCUSDT is the strongest setup right now.</p>
        </section>
        <div className="desktop-mobile-signal">
          <b>BTCUSDT</b>
          <span>Long · 4H · 3.1R</span>
          <em>97</em>
        </div>
        <div className="desktop-mobile-signal">
          <b>ETHUSDT</b>
          <span>Long · 6H · 2.6R</span>
          <em>85</em>
        </div>
        <nav>
          <i />
          <ScanLine size={14} />
          <i />
          <i />
        </nav>
      </div>
    </div>
  );
}

function DesktopMobileLanding({ onContinue }) {
  return (
    <main className="desktop-mobile-gate">
      <div className="desktop-mobile-gate-lines" aria-hidden="true" />
      <section className="desktop-mobile-gate-content">
        <div className="desktop-mobile-gate-copy">
          <img src={swiftChartLogo} alt="SwiftChart" />
          <span className="desktop-mobile-eyebrow"><Check size={13} /> Mobile-first trading intelligence</span>
          <h1>SwiftChart is optimized for mobile.</h1>
          <p>For the best trading experience, please open SwiftChart on your phone. Desktop support is coming soon.</p>

          <div className="desktop-mobile-actions">
            <a href={PRODUCTION_APP_URL} className="desktop-mobile-primary">
              Open on Mobile <ArrowUpRight size={17} />
            </a>
            <button type="button" onClick={onContinue}>Continue Anyway</button>
          </div>
        </div>

        <div className="desktop-mobile-visual">
          <PhonePreview />
          <div className="desktop-mobile-qr-card">
            <QRCodeSVG
              value={PRODUCTION_APP_URL}
              size={132}
              level="M"
              bgColor="#f5f8f8"
              fgColor="#10191b"
              marginSize={2}
              title="Open SwiftChart on mobile"
            />
            <div>
              <strong>Scan to open</strong>
              <span>Point your phone camera at the code.</span>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

export default function DesktopMobileGate({ enabled = true, children }) {
  const [desktopViewport, setDesktopViewport] = useState(() => isDesktopViewport());
  const [bypassed, setBypassed] = useState(() => hasRememberedBypass());

  useEffect(() => {
    const mediaQuery = window.matchMedia(`(min-width: ${DESKTOP_BREAKPOINT + 1}px)`);
    const updateViewport = (event) => setDesktopViewport(event.matches);

    setDesktopViewport(mediaQuery.matches);
    mediaQuery.addEventListener?.("change", updateViewport);
    return () => mediaQuery.removeEventListener?.("change", updateViewport);
  }, []);

  function continueToApp() {
    try {
      window.localStorage?.setItem(BYPASS_STORAGE_KEY, "true");
    } catch {
      // The current visit can still continue when storage is unavailable.
    }
    setBypassed(true);
  }

  if (!enabled || !desktopViewport || bypassed) return children;
  return <DesktopMobileLanding onContinue={continueToApp} />;
}
