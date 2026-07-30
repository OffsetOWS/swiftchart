import { useMemo, useRef, useState } from "react";
import {
  AppWindow,
  Bell,
  BookOpen,
  Bug,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Download,
  ExternalLink,
  FileText,
  HelpCircle,
  Info,
  KeyRound,
  Laptop,
  LockKeyhole,
  MessageCircle,
  Moon,
  Palette,
  RefreshCw,
  Search,
  Send,
  Shield,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Upload,
  User,
  Volume2,
} from "lucide-react";
import { getPublicOrigin } from "../lib/siteUrl.js";
import packageJson from "../../package.json";
import { useAuth } from "../lib/AuthContext.jsx";

const TELEGRAM_URL = import.meta.env.VITE_TELEGRAM_BOT_URL || "";
const PUBLIC_SITE_URL = getPublicOrigin();
const SUPPORT_ENDPOINT = import.meta.env.VITE_SUPPORT_ENDPOINT || "";
const LEGAL_URLS = {
  privacy: import.meta.env.VITE_PRIVACY_POLICY_URL || "",
  terms: import.meta.env.VITE_TERMS_URL || "",
  risk: import.meta.env.VITE_RISK_DISCLAIMER_URL || "",
};

const SETTINGS_SECTIONS = [
  ["settings-profile", "Profile", "Account details and security", User],
  ["settings-notifications", "Notifications", "Trade, market, and delivery alerts", Bell],
  ["settings-trading", "Trading Preferences", "Default market and scan filters", SlidersHorizontal],
  ["settings-appearance", "Appearance", "Theme, spacing, and motion", Palette],
  ["settings-security", "Privacy & Security", "Password, sessions, and data", Shield],
  ["settings-info", "App Information", "Version, updates, and about", Info],
];

const SUPPORT_SECTIONS = [
  ["support-help", "Help Center", "Search SwiftChart answers", CircleHelp],
  ["support-contact", "Contact Support", "Account or technical help", MessageCircle],
  ["support-bug", "Report a Bug", "Send a structured technical report", Bug],
  ["support-feature", "Request a Feature", "Tell us what SwiftChart should solve", Sparkles],
  ["support-telegram", "Telegram Community", "Open the configured SwiftChart Telegram", Send],
  ["support-docs", "Documentation", "Learn scores, setups, and scanning", BookOpen],
  ["support-legal", "Legal and Risk Information", "Trading disclaimer and policies", FileText],
];

const FAQS = [
  ["Getting Started", "How do I find a setup?", "Open Scan, scan the market or a coin, then tap a ranked result to review its execution levels and reasoning."],
  ["Scan Scores", "What does the setup score mean?", "The score ranks structure, momentum, liquidity, confirmation, and risk/reward into a 65-100 quality signal."],
  ["High Conviction", "What is High Conviction?", "Scores of 90 or above are labeled High Conviction. It is a quality classification, not a profit guarantee."],
  ["Trade Bias", "What does Long or Short mean?", "Long expects price strength; Short expects price weakness. Always review the stop and invalidation before acting."],
  ["Entry, Stop Loss, TP1 and TP2", "How do execution levels work?", "Entry is the decision zone, Stop Loss defines invalidation, and TP1/TP2 are staged profit objectives."],
  ["Crypto and Forex", "Can I switch markets?", "Use the Crypto/Forex switch on Home. The selected market carries into Scan, History, and Account."],
  ["Notifications", "Why did I not receive browser push?", "In-app alerts remain available. Browser push also requires permission from your browser and operating system."],
  ["Account and Login", "How do I reset my password?", "Open Settings, then Privacy & Security or Profile, and request a secure reset email."],
  ["Telegram Alerts", "How do I connect Telegram?", "Open Telegram from Account and follow the configured SwiftChart bot flow. Staff will never request passwords or private keys."],
];

const DOCUMENTATION = [
  ["How SwiftChart works", "SwiftChart filters market data, ranks valid structures, and presents the strongest setups with execution context."],
  ["Scan scores", "Scores combine trend structure, momentum, liquidity, confirmations, freshness, and risk/reward. Higher is stronger, never guaranteed."],
  ["High Conviction", "A score of 90-100 indicates unusually strong alignment across SwiftChart's quality checks."],
  ["Market bias and regime", "Bias describes directional pressure. Regime describes whether conditions are trending, ranging, or selective."],
  ["Long and Short", "Long setups look for upside continuation or reversal. Short setups look for downside continuation or rejection."],
  ["Risk-to-reward", "Potential reward divided by planned risk. It describes the setup profile, not its probability of winning."],
  ["Crypto scanning", "Ranks supported crypto markets using current market data and quality filters."],
  ["Forex scanning", "Adds session timing, spread, pair behavior, and news-risk context to the ranking process."],
];

function PageHeader({ title, onBack }) {
  return (
    <header className="settings-page-header">
      <button type="button" onClick={onBack} aria-label={`Back from ${title}`}><ChevronLeft size={18} /></button>
      <h1>{title}</h1>
    </header>
  );
}

function SettingsGroup({ title, children, className = "" }) {
  return (
    <section className={`settings-group ${className}`.trim()}>
      {title ? <span className="settings-group-title">{title}</span> : null}
      <div>{children}</div>
    </section>
  );
}

function SettingsRow({ icon: Icon, title, description, value, onClick, trailing, disabled = false }) {
  const content = (
    <>
      {Icon ? <span className="settings-row-icon"><Icon size={17} /></span> : null}
      <span className="settings-row-copy"><strong>{title}</strong>{description ? <small>{description}</small> : null}</span>
      {value ? <em>{value}</em> : null}
      {trailing || (onClick ? <ChevronRight size={16} /> : null)}
    </>
  );
  return onClick ? (
    <button type="button" className="settings-row" onClick={onClick} disabled={disabled}>{content}</button>
  ) : <div className={`settings-row${disabled ? " disabled" : ""}`}>{content}</div>;
}

function Switch({ checked, onChange, label, disabled = false }) {
  return (
    <button
      type="button"
      className={`settings-switch${checked ? " on" : ""}`}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <i />
    </button>
  );
}

function Segment({ value, options, onChange, label }) {
  return (
    <div className="settings-segment" role="group" aria-label={label}>
      {options.map(([key, text]) => <button key={key} type="button" className={value === key ? "active" : ""} onClick={() => onChange(key)}>{text}</button>)}
    </div>
  );
}

function Notice({ tone = "info", children }) {
  return children ? <p className={`settings-notice ${tone}`} role="status">{children}</p> : null;
}

function ConfirmDialog({ title, message, confirmLabel, danger = false, phrase, onCancel, onConfirm }) {
  const [value, setValue] = useState("");
  const blocked = phrase && value !== phrase;
  return (
    <div className="settings-confirm-backdrop" role="dialog" aria-modal="true" aria-label={title}>
      <section className="settings-confirm">
        <h2>{title}</h2>
        <p>{message}</p>
        {phrase ? <label>Type <strong>{phrase}</strong><input value={value} onChange={(event) => setValue(event.target.value)} /></label> : null}
        <div>
          <button type="button" onClick={onCancel}>Cancel</button>
          <button type="button" className={danger ? "danger" : ""} disabled={blocked} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </section>
    </div>
  );
}

function SettingsOverview({ onNavigate, onBack }) {
  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="Settings" onBack={onBack} />
      <SettingsGroup>
        {SETTINGS_SECTIONS.map(([view, title, description, Icon]) => (
          <SettingsRow key={view} icon={Icon} title={title} description={description} onClick={() => onNavigate(view)} />
        ))}
      </SettingsGroup>
    </div>
  );
}

function ProfileSettings({ onBack }) {
  const auth = useAuth();
  const originalName = auth.profile?.username || auth.user?.user_metadata?.username || "";
  const [username, setUsername] = useState(originalName);
  const [email, setEmail] = useState(auth.user?.email || "");
  const [avatarPreview, setAvatarPreview] = useState(auth.profile?.avatar_url || auth.user?.user_metadata?.avatar_url || "");
  const [avatarPending, setAvatarPending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const fileRef = useRef(null);

  function chooseAvatar(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 3 * 1024 * 1024) {
      setError("Use a PNG, JPG, or WebP image smaller than 3 MB.");
      return;
    }
    setAvatarPreview(URL.createObjectURL(file));
    setAvatarPending(true);
    setError("");
  }

  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await auth.updateProfileDetails({ username });
      if (email.trim().toLowerCase() !== String(auth.user?.email || "").toLowerCase()) {
        await auth.updateEmail(email.trim());
        setNotice("Profile saved. Check your new email address to verify the change.");
      } else {
        setNotice("Profile saved.");
      }
      if (avatarPending) {
        setNotice((current) => `${current} Avatar upload needs a configured Supabase Storage bucket and was not uploaded.`);
      }
    } catch (saveError) {
      setError(saveError.message || "Could not save profile.");
    } finally {
      setSaving(false);
    }
  }

  async function sendPasswordReset() {
    setError("");
    setNotice("");
    try {
      await auth.sendPasswordReset(auth.user?.email, "/app/account?view=settings-profile");
      setNotice("Password reset email sent.");
    } catch (resetError) {
      setError(resetError.message || "Could not send reset email.");
    }
  }

  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="Profile" onBack={onBack} />
      <form className="settings-form" onSubmit={save}>
        <section className="profile-photo-editor">
          <div className="graphite-avatar">{avatarPreview ? <img src={avatarPreview} alt="" /> : <User size={23} />}</div>
          <div><strong>Profile photo</strong><small>PNG, JPG, or WebP · max 3 MB</small></div>
          <input ref={fileRef} hidden type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseAvatar} />
          <button type="button" onClick={() => fileRef.current?.click()}><Upload size={15} /> Replace</button>
          {avatarPreview ? <button type="button" className="quiet" onClick={() => { setAvatarPreview(""); setAvatarPending(false); }}>Remove</button> : null}
        </section>
        <label>Username<input value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} maxLength={28} pattern="[A-Za-z0-9_]+" required /></label>
        <label>Email address<input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required /></label>
        <SettingsGroup title="Security">
          <SettingsRow icon={KeyRound} title="Change password" description="Send a secure reset email" onClick={sendPasswordReset} />
        </SettingsGroup>
        <Notice>{notice}</Notice><Notice tone="error">{error}</Notice>
        <button className="settings-primary" type="submit" disabled={saving}>{saving ? "Saving..." : "Save Changes"}</button>
      </form>
      <SettingsGroup title="Danger Zone" className="danger-zone">
        <SettingsRow icon={Trash2} title="Delete account" description="Permanently remove your SwiftChart account" onClick={() => setConfirmDelete(true)} />
      </SettingsGroup>
      {confirmDelete ? (
        <ConfirmDialog
          title="Delete account"
          message="Account deletion is permanent. The secure server deletion endpoint is not connected yet, so confirming will not remove data."
          phrase="DELETE"
          confirmLabel="Request deletion"
          danger
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => { setConfirmDelete(false); setError("Account deletion is unavailable until the secure backend deletion endpoint is configured."); }}
        />
      ) : null}
    </div>
  );
}

function NotificationSettings({ preferences, updateSection, onBack }) {
  const settings = preferences.notifications;
  const [permissionState, setPermissionState] = useState(() => (
    "Notification" in window ? window.Notification.permission : "unsupported"
  ));

  async function togglePush(next) {
    if (!next) {
      updateSection("notifications", { pushEnabled: false });
      return;
    }
    if (!("Notification" in window)) {
      setPermissionState("unsupported");
      return;
    }
    const permission = await window.Notification.requestPermission();
    setPermissionState(permission);
    updateSection("notifications", { pushEnabled: permission === "granted" });
  }

  const groups = [
    ["Trade Alerts", [["highConviction", "High Conviction Setups"], ["tp1", "TP1 Hit Alerts"], ["tp2", "TP2 Hit Alerts"], ["stopLoss", "Stop Loss Alerts"], ["nearEntry", "Price Near Entry Alerts"], ["tradeOpened", "Trade Opened"], ["tradeClosed", "Trade Closed"]]],
    ["Market Alerts", [["marketBias", "Market Bias Changes"], ["sessions", "Market Session Alerts"], ["dailySummary", "Daily Market Summary"]]],
    ["App Alerts", [["systemNotices", "System and Account Notices"]]],
    ["Delivery Preferences", [["sound", "Sound"], ["vibration", "Vibration"]]],
  ];

  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="Notifications" onBack={onBack} />
      <SettingsGroup>
        <SettingsRow icon={Bell} title="Push Notifications" description="In-app notifications remain available when push is off." trailing={<Switch label="Push Notifications" checked={settings.pushEnabled} onChange={togglePush} />} />
      </SettingsGroup>
      {permissionState === "denied" ? <Notice tone="error">Browser notifications are blocked. Enable them in your browser or device settings.</Notice> : null}
      {permissionState === "unsupported" ? <Notice>Browser push is not supported here. In-app notifications still work.</Notice> : null}
      {groups.map(([title, rows]) => (
        <SettingsGroup key={title} title={title} className={!settings.pushEnabled ? "dependent-disabled" : ""}>
          {rows.map(([key, label]) => (
            <SettingsRow key={key} title={label} disabled={!settings.pushEnabled} trailing={<Switch label={label} checked={settings[key]} disabled={!settings.pushEnabled} onChange={(value) => updateSection("notifications", { [key]: value })} />} />
          ))}
        </SettingsGroup>
      ))}
    </div>
  );
}

function TradingSettings({ preferences, updateSection, resetSection, onBack }) {
  const settings = preferences.trading;
  const [confirmReset, setConfirmReset] = useState(false);
  const minimum = settings.minimumScore;
  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="Trading Preferences" onBack={onBack} />
      <SettingsGroup title="Default Market">
        <Segment value={settings.defaultMarket} options={[["crypto", "Crypto"], ["forex", "Forex"]]} onChange={(value) => updateSection("trading", { defaultMarket: value })} label="Default market" />
      </SettingsGroup>
      <SettingsGroup title="Default Timeframe">
        <Segment value={settings.defaultTimeframe} options={[["1h", "1H"], ["4h", "4H"], ["1d", "1D"]]} onChange={(value) => updateSection("trading", { defaultTimeframe: value })} label="Default timeframe" />
      </SettingsGroup>
      <SettingsGroup title="Scan Defaults">
        <label className="settings-range">Minimum scan score <strong>{minimum}</strong><input type="range" min="0" max="100" value={minimum} onChange={(event) => updateSection("trading", { minimumScore: Number(event.target.value) })} /></label>
        <label className="settings-select">Preferred exchange<select value={settings.preferredExchange} onChange={(event) => updateSection("trading", { preferredExchange: event.target.value })}><option value="all">All Exchanges</option><option value="hyperliquid">Hyperliquid</option><option value="variational">Variational</option></select><ChevronDown size={15} /></label>
        <label className="settings-select">Default sorting<select value={settings.defaultSorting} onChange={(event) => updateSection("trading", { defaultSorting: event.target.value })}><option value="highest_score">Highest Score</option><option value="newest">Newest</option><option value="highest_rr">Highest R:R</option></select><ChevronDown size={15} /></label>
      </SettingsGroup>
      <SettingsGroup title="Result Display">
        {[["hideLowConviction", "Hide Low Conviction Setups"], ["showOnlyOpen", "Show Only Open Setups"], ["prioritizeHighConviction", "Prioritize High Conviction"], ["rememberFilters", "Remember Last Used Filters"]].map(([key, label]) => (
          <SettingsRow key={key} title={label} trailing={<Switch label={label} checked={settings[key]} onChange={(value) => updateSection("trading", { [key]: value })} />} />
        ))}
      </SettingsGroup>
      <button type="button" className="settings-secondary" onClick={() => setConfirmReset(true)}>Reset to Defaults</button>
      {confirmReset ? <ConfirmDialog title="Reset trading preferences?" message="Your active scan will not change. Defaults apply the next time you open a market or scan." confirmLabel="Reset" onCancel={() => setConfirmReset(false)} onConfirm={() => { resetSection("trading"); setConfirmReset(false); }} /> : null}
    </div>
  );
}

function AppearanceSettings({ preferences, updateSection, onBack }) {
  const settings = preferences.appearance;
  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="Appearance" onBack={onBack} />
      <SettingsGroup title="Theme">
        <Segment value={settings.theme} options={[["dark", "Dark"], ["light", "Light"]]} onChange={(value) => updateSection("appearance", { theme: value })} label="Theme" />
      </SettingsGroup>
      <SettingsGroup title="Display">
        {[["compactLayout", "Compact Layout", "Reduces spacing while keeping touch targets comfortable"], ["largeText", "Large Text", "Increases essential labels and values"], ["reduceAnimations", "Reduce Animations", "Minimizes transitions and panel motion"]].map(([key, label, description]) => (
          <SettingsRow key={key} title={label} description={description} trailing={<Switch label={label} checked={settings[key]} onChange={(value) => updateSection("appearance", { [key]: value })} />} />
        ))}
      </SettingsGroup>
    </div>
  );
}

function SecuritySettings({ onBack, onDeleteAccount }) {
  const auth = useAuth();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [confirmLogoutAll, setConfirmLogoutAll] = useState(false);
  const device = useMemo(() => {
    const ua = navigator.userAgent;
    const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : /Firefox\//.test(ua) ? "Firefox" : "Browser";
    const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Mac/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : "Device";
    return `${browser} on ${os}`;
  }, []);

  async function resetPassword() {
    setError("");
    try {
      await auth.sendPasswordReset(auth.user?.email, "/app/account?view=settings-security");
      setNotice("Password reset email sent.");
    } catch (resetError) {
      setError(resetError.message || "Could not send reset email.");
    }
  }

  async function logoutAll() {
    try {
      await auth.signOut();
      window.location.assign("/login");
    } catch {
      setError("Could not log out all devices.");
      setConfirmLogoutAll(false);
    }
  }

  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="Privacy & Security" onBack={onBack} />
      <SettingsGroup title="Security">
        <SettingsRow icon={KeyRound} title="Change Password" description="Send a secure reset email" onClick={resetPassword} />
        <SettingsRow icon={LockKeyhole} title="Two-Factor Authentication" value="Coming Soon" />
        <SettingsRow icon={Laptop} title="Biometric Login" value={window.PublicKeyCredential ? "Coming Soon" : "Unavailable"} />
      </SettingsGroup>
      <SettingsGroup title="Active Devices">
        <SettingsRow icon={Laptop} title={device} description="Current device · active now · location not collected" />
        <SettingsRow icon={Shield} title="Log Out From All Devices" description="The current device will also be logged out" onClick={() => setConfirmLogoutAll(true)} />
      </SettingsGroup>
      <SettingsGroup title="Data Controls">
        <SettingsRow icon={Download} title="Download My Data" value="Coming Soon" />
        <SettingsRow icon={Trash2} title="Delete Account" onClick={onDeleteAccount} />
      </SettingsGroup>
      <SettingsGroup title="Legal">
        <LegalLink title="Privacy Policy" url={LEGAL_URLS.privacy} />
        <LegalLink title="Terms of Service" url={LEGAL_URLS.terms} />
      </SettingsGroup>
      <Notice>{notice}</Notice><Notice tone="error">{error}</Notice>
      {confirmLogoutAll ? <ConfirmDialog title="Log out all devices?" message="Supabase will end the current session too. You will need to sign in again." confirmLabel="Log Out All" danger onCancel={() => setConfirmLogoutAll(false)} onConfirm={logoutAll} /> : null}
    </div>
  );
}

function LegalLink({ title, url }) {
  return url
    ? <SettingsRow icon={ExternalLink} title={title} onClick={() => window.open(url, "_blank", "noopener,noreferrer")} />
    : <SettingsRow icon={FileText} title={title} value="Not published" disabled />;
}

function AppInformation({ onBack }) {
  const [notice, setNotice] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);

  async function checkUpdates() {
    if ("serviceWorker" in navigator) {
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations.map((registration) => registration.update()));
    }
    setNotice("SwiftChart is up to date.");
  }

  async function clearCache() {
    if ("caches" in window) {
      const keys = await window.caches.keys();
      await Promise.all(keys.map((key) => window.caches.delete(key)));
    }
    window.sessionStorage?.removeItem("swiftchart.appSplashSeen.v1");
    window.location.reload();
  }

  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="App Information" onBack={onBack} />
      <SettingsGroup>
        <SettingsRow icon={AppWindow} title="Current Version" value={packageJson.version} />
        <SettingsRow icon={Sparkles} title="What's New" value="No changelog published" disabled />
        <SettingsRow icon={RefreshCw} title="Check for Updates" onClick={checkUpdates} />
        <SettingsRow icon={Trash2} title="Clear Cache" description="Keeps your account, plan, backend trades, and saved preferences" onClick={() => setConfirmClear(true)} />
      </SettingsGroup>
      <SettingsGroup title="About SwiftChart">
        <div className="settings-copy-block">
          <p>SwiftChart is a market-analysis and decision-support product for ranked crypto and Forex setups.</p>
          <a href={PUBLIC_SITE_URL} target="_blank" rel="noreferrer">Official website <ExternalLink size={13} /></a>
          <small>SwiftChart does not guarantee profit or provide financial advice. Trading involves risk, and users remain responsible for their decisions.</small>
        </div>
      </SettingsGroup>
      <Notice>{notice}</Notice>
      {confirmClear ? <ConfirmDialog title="Clear cached app data?" message="Cached assets and temporary session data will be removed. Your account, backend history, and preferences remain." confirmLabel="Clear Cache" onCancel={() => setConfirmClear(false)} onConfirm={clearCache} /> : null}
    </div>
  );
}

function SupportOverview({ onNavigate, onBack }) {
  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="Support" onBack={onBack} />
      <SettingsGroup>
        {SUPPORT_SECTIONS.map(([view, title, description, Icon]) => <SettingsRow key={view} icon={Icon} title={title} description={description} onClick={() => onNavigate(view)} />)}
      </SettingsGroup>
    </div>
  );
}

function HelpCenter({ onBack }) {
  const [query, setQuery] = useState("");
  const [openQuestion, setOpenQuestion] = useState("");
  const visible = FAQS.filter((item) => item.join(" ").toLowerCase().includes(query.toLowerCase()));
  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="Help Center" onBack={onBack} />
      <label className="settings-search"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search help articles" /></label>
      <div className="faq-list">
        {visible.map(([category, question, answer]) => (
          <article key={question} className={openQuestion === question ? "open" : ""}>
            <button type="button" onClick={() => setOpenQuestion((current) => current === question ? "" : question)}>
              <span><small>{category}</small><strong>{question}</strong></span><ChevronDown size={16} />
            </button>
            {openQuestion === question ? <p>{answer}</p> : null}
          </article>
        ))}
        {!visible.length ? <Notice>No matching help article.</Notice> : null}
      </div>
    </div>
  );
}

function SupportForm({ kind, onBack }) {
  const auth = useAuth();
  const [values, setValues] = useState({
    name: auth.profile?.username || "",
    email: auth.user?.email || "",
    subject: "",
    category: "Technical Issue",
    message: "",
    title: "",
    happened: "",
    steps: "",
    problem: "",
    behavior: "",
    details: "",
  });
  const [attachment, setAttachment] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const title = kind === "contact" ? "Contact Support" : kind === "bug" ? "Report a Bug" : "Request a Feature";
  const diagnostic = `${navigator.userAgent.split(" ").slice(-2).join(" ")} · ${window.innerWidth}×${window.innerHeight} · ${window.location.pathname} · v${packageJson.version}`;

  function set(key, value) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function chooseFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 5 * 1024 * 1024) {
      setError("Attachments must be PNG, JPG, or WebP and smaller than 5 MB.");
      return;
    }
    setAttachment(file);
    setError("");
  }

  async function submit(event) {
    event.preventDefault();
    const required = kind === "contact"
      ? [values.name, values.email, values.subject, values.message]
      : kind === "bug"
        ? [values.title, values.happened, values.steps]
        : [values.title, values.problem, values.behavior];
    if (required.some((value) => !String(value).trim())) {
      setError("Complete all required fields.");
      return;
    }
    setSubmitting(true);
    setError("");
    if (!SUPPORT_ENDPOINT) {
      setError("Support delivery is not connected yet. Your draft remains on this page.");
      setSubmitting(false);
      return;
    }
    setError("Support endpoint is configured but file transmission is intentionally disabled until its request contract is implemented.");
    setSubmitting(false);
  }

  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title={title} onBack={onBack} />
      <form className="settings-form support-form" onSubmit={submit}>
        {kind === "contact" ? (
          <>
            <label>Name<input value={values.name} onChange={(event) => set("name", event.target.value)} required /></label>
            <label>Email<input type="email" value={values.email} onChange={(event) => set("email", event.target.value)} required /></label>
            <label>Subject<input value={values.subject} onChange={(event) => set("subject", event.target.value)} required /></label>
            <label>Category<select value={values.category} onChange={(event) => set("category", event.target.value)}>{["Account", "Technical Issue", "Signal or Scan Issue", "Telegram", "Feature Question", "Other"].map((option) => <option key={option}>{option}</option>)}</select></label>
            <label>Message<textarea value={values.message} onChange={(event) => set("message", event.target.value)} required /></label>
          </>
        ) : null}
        {kind === "bug" ? (
          <>
            <label>Short title<input value={values.title} onChange={(event) => set("title", event.target.value)} required /></label>
            <label>What happened?<textarea value={values.happened} onChange={(event) => set("happened", event.target.value)} required /></label>
            <label>Steps to reproduce<textarea value={values.steps} onChange={(event) => set("steps", event.target.value)} required /></label>
            <label>Safe diagnostics<input value={diagnostic} readOnly /></label>
          </>
        ) : null}
        {kind === "feature" ? (
          <>
            <label>Feature title<input value={values.title} onChange={(event) => set("title", event.target.value)} required /></label>
            <label>Problem it solves<textarea value={values.problem} onChange={(event) => set("problem", event.target.value)} required /></label>
            <label>Suggested behavior<textarea value={values.behavior} onChange={(event) => set("behavior", event.target.value)} required /></label>
            <label>Additional details<textarea value={values.details} onChange={(event) => set("details", event.target.value)} /></label>
          </>
        ) : null}
        {kind !== "feature" ? <label className="settings-file"><Upload size={16} /> Optional screenshot<input type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseFile} /><small>{attachment ? `${attachment.name} · ${(attachment.size / 1024).toFixed(0)} KB` : "PNG, JPG, or WebP · max 5 MB"}</small></label> : null}
        <Notice tone="error">{error}</Notice>
        <button className="settings-primary" disabled={submitting} type="submit">{submitting ? "Submitting..." : kind === "contact" ? "Send Message" : kind === "bug" ? "Submit Bug Report" : "Submit Request"}</button>
      </form>
    </div>
  );
}

function TelegramSupport({ onBack }) {
  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="Telegram Community" onBack={onBack} />
      <section className="support-telegram-card">
        <MessageCircle size={25} />
        <h2>SwiftChart on Telegram</h2>
        <p>Open the configured SwiftChart community or bot for alerts and account connection.</p>
        {TELEGRAM_URL ? <button type="button" onClick={() => window.open(TELEGRAM_URL, "_blank", "noopener,noreferrer")}>Join SwiftChart Telegram <ExternalLink size={15} /></button> : <Notice tone="error">No Telegram URL is configured.</Notice>}
      </section>
      <Notice tone="warning">SwiftChart staff will never request passwords, seed phrases, private keys, or remote-access credentials.</Notice>
    </div>
  );
}

function Documentation({ onBack }) {
  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="Documentation" onBack={onBack} />
      <div className="documentation-list">{DOCUMENTATION.map(([title, copy]) => <article key={title}><h2>{title}</h2><p>{copy}</p></article>)}</div>
    </div>
  );
}

function LegalSupport({ onBack }) {
  return (
    <div className="graphite-screen settings-screen">
      <PageHeader title="Legal & Risk" onBack={onBack} />
      <SettingsGroup>
        <LegalLink title="Risk Disclaimer" url={LEGAL_URLS.risk} />
        <LegalLink title="Privacy Policy" url={LEGAL_URLS.privacy} />
        <LegalLink title="Terms of Service" url={LEGAL_URLS.terms} />
      </SettingsGroup>
      <section className="settings-copy-block legal-copy">
        <h2>Trading disclaimer</h2>
        <p>SwiftChart provides analysis and decision-support tools. It does not guarantee profit or risk-free trading, and it is not financial advice.</p>
        <p>Trading involves risk. Users are responsible for their own trading decisions, position sizing, and account security.</p>
      </section>
    </div>
  );
}

export default function SettingsSupport({
  view,
  preferencesController,
  onNavigate,
  onBack,
}) {
  const props = { preferences: preferencesController.preferences, updateSection: preferencesController.updateSection, resetSection: preferencesController.resetSection, onBack };
  if (view === "settings") return <SettingsOverview onNavigate={onNavigate} onBack={onBack} />;
  if (view === "settings-profile") return <ProfileSettings onBack={onBack} />;
  if (view === "settings-notifications") return <NotificationSettings {...props} />;
  if (view === "settings-trading") return <TradingSettings {...props} />;
  if (view === "settings-appearance") return <AppearanceSettings {...props} />;
  if (view === "settings-security") return <SecuritySettings onBack={onBack} onDeleteAccount={() => onNavigate("settings-profile")} />;
  if (view === "settings-info") return <AppInformation onBack={onBack} />;
  if (view === "support") return <SupportOverview onNavigate={onNavigate} onBack={onBack} />;
  if (view === "support-help") return <HelpCenter onBack={onBack} />;
  if (view === "support-contact") return <SupportForm kind="contact" onBack={onBack} />;
  if (view === "support-bug") return <SupportForm kind="bug" onBack={onBack} />;
  if (view === "support-feature") return <SupportForm kind="feature" onBack={onBack} />;
  if (view === "support-telegram") return <TelegramSupport onBack={onBack} />;
  if (view === "support-docs") return <Documentation onBack={onBack} />;
  if (view === "support-legal") return <LegalSupport onBack={onBack} />;
  return null;
}
