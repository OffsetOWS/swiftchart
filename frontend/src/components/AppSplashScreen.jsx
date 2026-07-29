import { useCallback, useEffect, useRef, useState } from "react";
import swiftChartLogo from "../assets/swiftchart-s-mark.png";
import "../styles/appSplash.css";

const MIN_VISIBLE_MS = 1500;
const MAX_VISIBLE_MS = 4000;
const EXIT_DURATION_MS = 360;

export function SplashLoader() {
  return (
    <div className="app-splash-loader" role="status" aria-label="SwiftChart is loading">
      <div className="app-splash-bars" aria-hidden="true">
        <span />
        <span />
      </div>
      <div className="app-splash-loading-text" aria-hidden="true">
        {"LOADING".split("").map((letter, index) => (
          <span key={`${letter}-${index}`} style={{ "--splash-letter-index": index }}>{letter}</span>
        ))}
        <i>.</i><i>.</i><i>.</i>
      </div>
    </div>
  );
}

export default function AppSplashScreen({ ready, onComplete }) {
  const [phase, setPhase] = useState("visible");
  const startedAt = useRef(Date.now());
  const finished = useRef(false);
  const onCompleteRef = useRef(onComplete);

  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  const finish = useCallback(() => {
    if (finished.current) return;
    finished.current = true;
    setPhase("exiting");
    window.setTimeout(() => onCompleteRef.current?.(), EXIT_DURATION_MS);
  }, []);

  useEffect(() => {
    const elapsed = Date.now() - startedAt.current;
    const maximumTimer = window.setTimeout(finish, Math.max(0, MAX_VISIBLE_MS - elapsed));
    return () => window.clearTimeout(maximumTimer);
  }, [finish]);

  useEffect(() => {
    if (!ready) return undefined;
    const elapsed = Date.now() - startedAt.current;
    const minimumTimer = window.setTimeout(finish, Math.max(0, MIN_VISIBLE_MS - elapsed));
    return () => window.clearTimeout(minimumTimer);
  }, [finish, ready]);

  return (
    <section className={`app-splash-screen ${phase}`} aria-label="SwiftChart application loading">
      <div className="app-splash-flow app-splash-flow-a" aria-hidden="true" />
      <div className="app-splash-flow app-splash-flow-b" aria-hidden="true" />
      <div className="app-splash-flow app-splash-flow-c" aria-hidden="true" />

      <div className="app-splash-logo-shell">
        <img src={swiftChartLogo} alt="SwiftChart S mark" />
      </div>

      <SplashLoader />
    </section>
  );
}
