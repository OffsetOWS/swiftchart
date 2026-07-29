import { Chrome, Eye, EyeOff, LockKeyhole, Mail, UserRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import swiftChartLogo from "../assets/swiftchart-logo.png";
import { useAuth } from "../lib/AuthContext.jsx";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const APP_SPLASH_SESSION_KEY = "swiftchart.appSplashSeen.v1";

function safeReturnTo() {
  const value = new URLSearchParams(window.location.search).get("returnTo");
  return value?.startsWith("/") && !value.startsWith("//") ? value : "/app/home";
}

function pageMode() {
  if (window.location.pathname === "/signup") return "signup";
  if (window.location.pathname === "/forgot-password") return "forgot";
  if (window.location.pathname === "/reset-password") return "reset";
  return "login";
}

function routeWithReturnTo(path, returnTo) {
  return `${path}?returnTo=${encodeURIComponent(returnTo)}`;
}

export default function Auth() {
  const auth = useAuth();
  const mode = pageMode();
  const returnTo = useMemo(safeReturnTo, []);
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [confirmVisible, setConfirmVisible] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    auth.clearError();
  }, [mode]);

  const copy = {
    login: { title: "Welcome back", subtitle: "Sign in to continue to SwiftChart", action: "Sign In" },
    signup: { title: "Create your account", subtitle: "Start using SwiftChart across Crypto and Forex", action: "Create Account" },
    forgot: { title: "Reset your password", subtitle: "We will email you a secure reset link", action: "Send Reset Link" },
    reset: { title: "Choose a new password", subtitle: "Set a new password for your SwiftChart account", action: "Update Password" },
  }[mode];

  function validate() {
    if (mode === "signup") {
      const cleanUsername = username.trim();
      if (cleanUsername.length < 3 || cleanUsername.length > 28 || !/^[a-zA-Z0-9_]+$/.test(cleanUsername)) {
        return "Username must be 3-28 characters using letters, numbers, or underscores.";
      }
    }
    if (mode !== "reset" && !EMAIL_PATTERN.test(email.trim())) return "Enter a valid email address.";
    if (mode === "forgot") return "";
    if (!password) return "Enter your password.";
    if ((mode === "signup" || mode === "reset") && password.length < 8) return "Password must contain at least 8 characters.";
    if ((mode === "signup" || mode === "reset") && password !== confirmPassword) return "Passwords do not match.";
    return "";
  }

  function enterApp() {
    window.sessionStorage?.removeItem(APP_SPLASH_SESSION_KEY);
    window.location.assign(returnTo);
  }

  async function submit(event) {
    event.preventDefault();
    const validationError = validate();
    if (validationError) {
      setFormError(validationError);
      return;
    }

    setSubmitting(true);
    setFormError("");
    setSuccess("");
    auth.clearError();
    try {
      if (mode === "login") {
        await auth.signInWithPassword(email.trim(), password);
        enterApp();
      } else if (mode === "signup") {
        const data = await auth.signUpWithPassword({ email: email.trim(), password, username: username.trim() });
        if (data.session) enterApp();
        else setSuccess("Check your email to confirm your account, then sign in.");
      } else if (mode === "forgot") {
        await auth.sendPasswordReset(email.trim(), returnTo);
        setSuccess("Reset link sent. Check your inbox and spam folder.");
      } else {
        await auth.updatePassword(password);
        setSuccess("Password updated. Opening SwiftChart...");
        window.setTimeout(enterApp, 700);
      }
    } catch (error) {
      setFormError(error.message || "Authentication could not be completed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  async function continueWithGoogle() {
    setSubmitting(true);
    setFormError("");
    auth.clearError();
    try {
      await auth.signInWithGoogle(returnTo);
    } catch (error) {
      setFormError(error.message || "Google sign-in could not be started.");
      setSubmitting(false);
    }
  }

  async function resendVerification() {
    if (!email.trim() || submitting) return;
    setSubmitting(true);
    setFormError("");
    auth.clearError();
    try {
      await auth.resendSignupConfirmation(email.trim(), returnTo);
      setSuccess("Verification email sent again. Check your inbox and spam folder.");
    } catch (error) {
      setFormError(error.message || "Verification email could not be resent. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  const awaitingVerification = mode === "signup" && Boolean(success);

  return (
    <main className="auth-shell auth-graphite">
      <a className="auth-brand" href="/" aria-label="SwiftChart public website">
        <img src={swiftChartLogo} alt="SwiftChart" />
      </a>

      <section className="auth-card" aria-labelledby="auth-title">
        {awaitingVerification ? (
          <div className="auth-confirmation" role="status">
            <div className="auth-copy">
              <Mail size={28} aria-hidden="true" />
              <h1 id="auth-title">Check your email</h1>
              <p>We sent a verification link to <strong>{email.trim()}</strong>. Open it to finish creating your SwiftChart account.</p>
            </div>
            {formError || auth.error ? <p className="auth-error" role="alert">{formError || auth.error}</p> : null}
            {success ? <p className="auth-success">{success}</p> : null}
            <button className="auth-primary" type="button" onClick={resendVerification} disabled={submitting}>
              {submitting ? "Sending..." : "Resend verification email"}
            </button>
            <a className="auth-confirmation-back" href={routeWithReturnTo("/login", returnTo)}>Back to login</a>
          </div>
        ) : (
          <>
        <div className="auth-copy">
          <h1 id="auth-title">{copy.title}</h1>
          <p>{copy.subtitle}</p>
        </div>

        <form className="auth-form" onSubmit={submit} noValidate>
          {mode === "signup" ? (
            <label className="auth-field">
              <span>Username</span>
              <div>
                <UserRound size={18} aria-hidden="true" />
                <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="Your username" disabled={submitting} />
              </div>
            </label>
          ) : null}

          {mode !== "reset" ? (
            <label className="auth-field">
              <span>Email</span>
              <div>
                <Mail size={18} aria-hidden="true" />
                <input type="email" inputMode="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" placeholder="you@example.com" disabled={submitting} />
              </div>
            </label>
          ) : null}

          {mode !== "forgot" ? (
            <label className="auth-field">
              <span>{mode === "reset" ? "New password" : "Password"}</span>
              <div>
                <LockKeyhole size={18} aria-hidden="true" />
                <input type={passwordVisible ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} placeholder="Enter password" disabled={submitting} />
                <button type="button" className="auth-visibility" onClick={() => setPasswordVisible((value) => !value)} aria-label={passwordVisible ? "Hide password" : "Show password"}>
                  {passwordVisible ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </label>
          ) : null}

          {mode === "signup" || mode === "reset" ? (
            <label className="auth-field">
              <span>Confirm password</span>
              <div>
                <LockKeyhole size={18} aria-hidden="true" />
                <input type={confirmVisible ? "text" : "password"} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} autoComplete="new-password" placeholder="Repeat password" disabled={submitting} />
                <button type="button" className="auth-visibility" onClick={() => setConfirmVisible((value) => !value)} aria-label={confirmVisible ? "Hide confirmed password" : "Show confirmed password"}>
                  {confirmVisible ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </label>
          ) : null}

          {mode === "login" ? <a className="auth-forgot" href={routeWithReturnTo("/forgot-password", returnTo)}>Forgot password?</a> : null}

          <button className="auth-primary" type="submit" disabled={submitting || !auth.isSupabaseConfigured}>
            {submitting ? "Please wait..." : copy.action}
          </button>
        </form>

        {formError || auth.error || auth.configError ? <p className="auth-error" role="alert">{formError || auth.error || auth.configError}</p> : null}
        {success ? <p className="auth-success" role="status">{success}</p> : null}

        {mode === "login" || mode === "signup" ? (
          <>
            <div className="auth-divider"><span>or continue with</span></div>
            <button className="google-auth-button" type="button" onClick={continueWithGoogle} disabled={submitting || !auth.isSupabaseConfigured}>
              <Chrome size={18} aria-hidden="true" />
              <span>Continue with Google</span>
            </button>
          </>
        ) : null}

        <p className="auth-switch">
          {mode === "signup" ? <>Already have an account? <a href={routeWithReturnTo("/login", returnTo)}>Sign in</a></> : null}
          {mode === "login" ? <>Don&apos;t have an account? <a href={routeWithReturnTo("/signup", returnTo)}>Sign up</a></> : null}
          {mode === "forgot" || mode === "reset" ? <a href={routeWithReturnTo("/login", returnTo)}>Back to sign in</a> : null}
        </p>
          </>
        )}
      </section>
    </main>
  );
}
