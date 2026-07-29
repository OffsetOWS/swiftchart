import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { ensureUserProfile } from "./profile.js";
import { isSupabaseConfigured, supabase } from "./supabase.js";
import { getPublicOrigin } from "./siteUrl.js";

const AuthContext = createContext(null);
const SUPABASE_CONFIG_ERROR = "Supabase is not configured yet. Add VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.";
const DEV_AUTH_BYPASS = import.meta.env.DEV && import.meta.env.VITE_DEV_AUTH_BYPASS === "true";
const DEV_SESSION = DEV_AUTH_BYPASS ? {
  access_token: "swiftchart-local-preview",
  user: {
    id: "00000000-0000-4000-8000-000000000001",
    email: "creator@swiftchart.local",
    user_metadata: { username: "doflam" },
  },
} : null;
const DEV_PROFILE = DEV_AUTH_BYPASS ? {
  id: DEV_SESSION.user.id,
  username: "doflam",
  subscription_status: "free",
  profile_storage_ready: false,
} : null;

function safeReturnTo(value, fallback = "/app/home") {
  return value?.startsWith("/") && !value.startsWith("//") ? value : fallback;
}

function friendlyAuthError(error) {
  const message = String(error?.message || error || "").toLowerCase();
  if (/invalid login credentials|invalid credentials/.test(message)) return "Incorrect email or password.";
  if (/email not confirmed/.test(message)) return "Confirm your email before signing in.";
  if (/user already registered|already been registered/.test(message)) return "An account already exists for this email.";
  if (/password.*(least|characters|weak)/.test(message)) return "Use a stronger password with at least 8 characters.";
  if (/rate limit|too many requests/.test(message)) return "Too many attempts. Wait a moment and try again.";
  if (/failed to fetch|network|load failed|fetch/.test(message)) return "Could not reach the authentication service. Check your connection and try again.";
  return "Authentication could not be completed. Please try again.";
}

function getAuthRedirectUrl(returnToOverride) {
  const queryReturnTo = new URLSearchParams(window.location.search).get("returnTo");
  const currentPath = `${window.location.pathname}${window.location.search}`;
  const fallback = ["/auth", "/login", "/signup"].includes(window.location.pathname) ? "/app/home" : currentPath;
  const returnTo = safeReturnTo(returnToOverride || queryReturnTo, fallback);
  return `${getPublicOrigin()}/auth?returnTo=${encodeURIComponent(returnTo)}`;
}

export function AuthProvider({ children }) {
  const [session, setSession] = useState(DEV_SESSION);
  const [profile, setProfile] = useState(DEV_PROFILE);
  const [loading, setLoading] = useState(!DEV_AUTH_BYPASS);
  const [profileLoading, setProfileLoading] = useState(false);
  const [error, setError] = useState("");

  async function loadProfile(nextSession) {
    if (!nextSession?.user) {
      setProfile(null);
      return;
    }

    setProfileLoading(true);
    setError("");
    try {
      const nextProfile = await ensureUserProfile(nextSession);
      setProfile(nextProfile);
    } catch {
      setError("SwiftChart could not load your profile. Please refresh and try again.");
    } finally {
      setProfileLoading(false);
    }
  }

  useEffect(() => {
    if (DEV_AUTH_BYPASS) return undefined;

    if (!isSupabaseConfigured || !supabase) {
      setLoading(false);
      return undefined;
    }

    let mounted = true;

    supabase.auth.getSession().then(({ data, error: sessionError }) => {
      if (!mounted) return;
      if (sessionError) setError(friendlyAuthError(sessionError));
      setSession(data.session);
      setLoading(false);
      if (data.session) loadProfile(data.session);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((event, nextSession) => {
      setSession(nextSession);
      if (!nextSession) {
        setProfile(null);
        setLoading(false);
        return;
      }
      if (event === "SIGNED_IN" || event === "TOKEN_REFRESHED" || event === "INITIAL_SESSION" || event === "PASSWORD_RECOVERY") {
        setTimeout(() => loadProfile(nextSession), 0);
      }
    });

    return () => {
      mounted = false;
      listener.subscription.unsubscribe();
    };
  }, []);

  function requireSupabase() {
    if (isSupabaseConfigured && supabase) return;
    setError(SUPABASE_CONFIG_ERROR);
    throw new Error(SUPABASE_CONFIG_ERROR);
  }

  async function signInWithPassword(email, password) {
    requireSupabase();
    setError("");
    const { data, error: signInError } = await supabase.auth.signInWithPassword({ email, password });
    if (signInError) {
      const safeError = friendlyAuthError(signInError);
      setError(safeError);
      throw new Error(safeError);
    }
    return data;
  }

  async function signUpWithPassword({ email, password, username }) {
    requireSupabase();
    setError("");
    const { data, error: signUpError } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: { username: String(username || "").trim() },
        emailRedirectTo: getAuthRedirectUrl(),
      },
    });
    if (signUpError) {
      const safeError = friendlyAuthError(signUpError);
      setError(safeError);
      throw new Error(safeError);
    }
    return data;
  }

  async function sendPasswordReset(email, returnTo) {
    requireSupabase();
    setError("");
    const redirectTo = `${getPublicOrigin()}/reset-password?returnTo=${encodeURIComponent(safeReturnTo(returnTo))}`;
    const { error: resetError } = await supabase.auth.resetPasswordForEmail(email, { redirectTo });
    if (resetError) {
      const safeError = friendlyAuthError(resetError);
      setError(safeError);
      throw new Error(safeError);
    }
  }

  async function resendSignupConfirmation(email, returnTo) {
    requireSupabase();
    setError("");
    const { error: resendError } = await supabase.auth.resend({
      type: "signup",
      email,
      options: {
        emailRedirectTo: getAuthRedirectUrl(returnTo),
      },
    });
    if (resendError) {
      const safeError = friendlyAuthError(resendError);
      setError(safeError);
      throw new Error(safeError);
    }
  }

  async function updatePassword(password) {
    requireSupabase();
    setError("");
    const { data, error: updateError } = await supabase.auth.updateUser({ password });
    if (updateError) {
      const safeError = friendlyAuthError(updateError);
      setError(safeError);
      throw new Error(safeError);
    }
    return data;
  }

  async function updateEmail(email) {
    requireSupabase();
    setError("");
    const { data, error: updateError } = await supabase.auth.updateUser({ email });
    if (updateError) {
      const safeError = friendlyAuthError(updateError);
      setError(safeError);
      throw new Error(safeError);
    }
    return data;
  }

  async function updateProfileDetails({ username, avatarUrl } = {}) {
    const cleanUsername = String(username || "").trim().toLowerCase();
    if (!/^[a-z0-9_]{3,28}$/.test(cleanUsername)) {
      throw new Error("Username must be 3-28 characters using letters, numbers, or underscores.");
    }

    if (DEV_AUTH_BYPASS) {
      setProfile((current) => ({ ...current, username: cleanUsername, avatar_url: avatarUrl ?? current?.avatar_url }));
      return;
    }

    requireSupabase();
    setError("");
    const metadata = { username: cleanUsername };
    if (avatarUrl !== undefined) metadata.avatar_url = avatarUrl;
    const { error: authUpdateError } = await supabase.auth.updateUser({ data: metadata });
    if (authUpdateError) throw new Error(friendlyAuthError(authUpdateError));

    if (profile?.profile_storage_ready !== false) {
      const update = { username: cleanUsername };
      if (avatarUrl !== undefined) update.avatar_url = avatarUrl;
      const { error: profileUpdateError } = await supabase.from("profiles").update(update).eq("id", session.user.id);
      if (profileUpdateError) throw new Error("SwiftChart could not save your profile. Please try again.");
    } else {
      window.localStorage?.setItem(`swiftchart_username_${session.user.id}`, cleanUsername);
    }

    setProfile((current) => ({ ...current, username: cleanUsername, avatar_url: avatarUrl ?? current?.avatar_url }));
  }

  async function signInWithGoogle(returnTo) {
    requireSupabase();
    setError("");
    window.sessionStorage?.removeItem("swiftchart.appSplashSeen.v1");
    const { error: signInError } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: getAuthRedirectUrl(returnTo),
        queryParams: {
          access_type: "offline",
          prompt: "select_account",
        },
      },
    });
    if (signInError) {
      const safeError = friendlyAuthError(signInError);
      setError(safeError);
      throw new Error(safeError);
    }
  }

  async function signOut() {
    if (DEV_AUTH_BYPASS) return;
    if (!supabase) return;
    setError("");
    const { error: signOutError } = await supabase.auth.signOut();
    if (signOutError) {
      const safeError = friendlyAuthError(signOutError);
      setError(safeError);
      throw new Error(safeError);
    }
    setSession(null);
    setProfile(null);
  }

  const value = useMemo(
    () => ({
      session,
      user: session?.user || null,
      profile,
      loading,
      profileLoading,
      error,
      configError: isSupabaseConfigured ? "" : SUPABASE_CONFIG_ERROR,
      isAuthenticated: Boolean(session),
      isDevAuthBypass: DEV_AUTH_BYPASS,
      isSupabaseConfigured,
      refreshProfile: () => loadProfile(session),
      clearError: () => setError(""),
      signInWithPassword,
      signUpWithPassword,
      resendSignupConfirmation,
      sendPasswordReset,
      updatePassword,
      updateEmail,
      updateProfileDetails,
      signInWithGoogle,
      signOut,
    }),
    [session, profile, loading, profileLoading, error]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider.");
  return context;
}
