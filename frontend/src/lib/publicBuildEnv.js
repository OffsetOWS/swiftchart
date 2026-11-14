import { isIP } from "node:net";

export function validatePublicBuildEnv(env) {
  const url = env.VITE_SUPABASE_URL || "";
  const key = env.VITE_SUPABASE_ANON_KEY || "";
  if (env.VERCEL_ENV === "production" && (!url || !key)) {
    throw new Error("Production public authentication configuration is missing.");
  }
  for (const value of [url, env.VITE_PUBLIC_SITE_URL, env.VITE_API_BASE].filter(Boolean)) {
    const parsed = new URL(value);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash || isIP(parsed.hostname.replace(/^\[|\]$/g, ""))) {
      throw new Error("Public build URLs must not contain credentials, query data or IP addresses.");
    }
  }
  if (env.VERCEL_ENV === "production" && env.VITE_API_BASE) {
    throw new Error("Production must use the same-origin API proxy.");
  }
  if (key && !key.startsWith("sb_publishable_")) {
    let claims;
    try {
      const parts = key.split(".");
      if (parts.length !== 3) throw new Error();
      claims = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
    } catch {
      throw new Error("The frontend key must be a publishable or legacy anon key.");
    }
    if (claims.role !== "anon") throw new Error("An elevated key cannot be included in frontend assets.");
  }
  return { authenticationConfigured: Boolean(url && key), api: "same-origin" };
}
