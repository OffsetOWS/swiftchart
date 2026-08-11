import {
  CheckCircle2,
  CircleAlert,
  Clock3,
  LoaderCircle,
  Radio,
  WifiOff,
} from "lucide-react";

const ICONS = {
  auth: CircleAlert,
  cached: Clock3,
  empty: Radio,
  error: WifiOff,
  loading: LoaderCircle,
  neutral: Radio,
  "no-trade": CircleAlert,
  success: CheckCircle2,
  wait: Clock3,
};

export default function GraphiteStateCard({
  tone = "neutral",
  eyebrow = "Market status",
  title,
  message,
  meta,
  progress,
  actionLabel,
  onAction,
  secondaryAction,
  busy = false,
  live = "polite",
  children,
}) {
  const Icon = ICONS[tone] || ICONS.neutral;
  const normalizedProgress = Number.isFinite(Number(progress))
    ? Math.min(100, Math.max(0, Number(progress)))
    : null;

  return (
    <section
      className={`graphite-state-card is-${tone}${busy ? " is-busy" : ""}`}
      aria-busy={busy || undefined}
      aria-live={live}
      role={tone === "error" || tone === "auth" ? "alert" : "status"}
    >
      <div className="graphite-state-icon" aria-hidden="true">
        <Icon size={18} />
      </div>
      <div className="graphite-state-copy">
        <span>{eyebrow}</span>
        <strong>{title}</strong>
        {message ? <p>{message}</p> : null}
      </div>
      {normalizedProgress !== null ? (
        <strong className="graphite-state-progress-value">{Math.round(normalizedProgress)}%</strong>
      ) : null}
      {meta ? <div className="graphite-state-meta">{meta}</div> : null}
      {normalizedProgress !== null ? (
        <div className="graphite-state-progress" aria-hidden="true">
          <i style={{ transform: `scaleX(${normalizedProgress / 100})` }} />
        </div>
      ) : null}
      {children ? <div className="graphite-state-content">{children}</div> : null}
      {actionLabel || secondaryAction ? (
        <div className="graphite-state-actions">
          {actionLabel ? <button type="button" onClick={onAction}>{actionLabel}</button> : null}
          {secondaryAction ? (
            <button type="button" className="secondary" onClick={secondaryAction.onAction}>
              {secondaryAction.label}
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
