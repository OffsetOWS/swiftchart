const DEFAULT_PUBLIC_ORIGIN = "https://swiftchart.xyz";
const LEGACY_PRODUCTION_HOST = "swiftchart.vercel.app";

export function getPublicOrigin() {
  if (import.meta.env.DEV) return window.location.origin;
  return String(import.meta.env.VITE_PUBLIC_SITE_URL || DEFAULT_PUBLIC_ORIGIN).replace(/\/+$/, "");
}

export function getCanonicalRedirectUrl() {
  if (import.meta.env.DEV || window.location.hostname !== LEGACY_PRODUCTION_HOST) return "";

  const canonicalUrl = new URL(window.location.href);
  canonicalUrl.protocol = "https:";
  canonicalUrl.host = new URL(getPublicOrigin()).host;
  return canonicalUrl.toString();
}
