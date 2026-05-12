import { Check, Chrome } from "lucide-react";
import swiftChartLogo from "../assets/swiftchart-logo.png";
import { useAuth } from "../lib/AuthContext.jsx";

const features = ["Live bias tracking", "Signal history", "Telegram alerts soon", "Watchlist soon"];

export default function Auth() {
  const auth = useAuth();

  return (
    <main className="auth-shell">
      <header className="auth-brand" aria-label="SwiftChart">
        <img src={swiftChartLogo} alt="SwiftChart" />
      </header>

      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-copy">
          <span className="eyebrow">SwiftChart account</span>
          <h1 id="auth-title">Join SwiftChart</h1>
          <p>Save your dashboard, track signals, and access future alerts.</p>
        </div>

        <button className="google-auth-button" type="button" onClick={auth.signInWithGoogle} disabled={auth.loading || !auth.isSupabaseConfigured}>
          <Chrome size={18} aria-hidden="true" />
          <span>{auth.loading ? "Checking session" : "Continue with Google"}</span>
        </button>

        {auth.error ? <p className="auth-error" role="alert">{auth.error}</p> : null}

        <ul className="auth-feature-list" aria-label="SwiftChart account features">
          {features.map((feature) => (
            <li key={feature}>
              <span>
                <Check size={14} aria-hidden="true" />
              </span>
              {feature}
            </li>
          ))}
        </ul>

        <p className="auth-footer-text">No spam. Your account is only used to save your SwiftChart experience.</p>
      </section>
    </main>
  );
}
