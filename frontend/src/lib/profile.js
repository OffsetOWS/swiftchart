import { supabase } from "./supabase.js";

const usernameWords = [
  "swift_trader",
  "btcwizard",
  "chartbear",
  "trendpilot",
  "candlehawk",
  "biasrider",
  "rangehunter",
  "signalforge",
];

function randomDigits() {
  const array = new Uint32Array(1);
  crypto.getRandomValues(array);
  return String(array[0] % 10000).padStart(4, "0");
}

function createUsername() {
  const array = new Uint32Array(1);
  crypto.getRandomValues(array);
  return `${usernameWords[array[0] % usernameWords.length]}${randomDigits()}`;
}

function preferredUsername(user) {
  const value = String(user.user_metadata?.username || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]/g, "")
    .slice(0, 28);
  return value.length >= 3 ? value : "";
}

function isProfilesTableMissing(error) {
  return error?.code === "PGRST205" || error?.message?.includes("public.profiles");
}

function getAvatar(user) {
  return user.user_metadata?.avatar_url || user.user_metadata?.picture || null;
}

function getFallbackUsername(user) {
  const storageKey = `swiftchart_username_${user.id}`;
  const savedUsername = window.localStorage.getItem(storageKey);
  if (savedUsername) return savedUsername;

  const username = preferredUsername(user) || createUsername();
  window.localStorage.setItem(storageKey, username);
  return username;
}

function createFallbackProfile(user, now, avatarUrl) {
  return {
    id: user.id,
    email: user.email,
    username: getFallbackUsername(user),
    avatar_url: avatarUrl,
    signup_date: user.created_at || now,
    last_login: now,
    subscription_status: "free",
    subscription_started_at: null,
    subscription_expires_at: null,
    profile_storage_ready: false,
  };
}

const PROFILE_FIELDS = [
  "id",
  "email",
  "username",
  "avatar_url",
  "signup_date",
  "last_login",
  "subscription_status",
  "subscription_started_at",
  "subscription_expires_at",
].join(",");

async function refreshExpiredSubscription() {
  const { error } = await supabase.rpc("refresh_my_subscription_status");
  if (error && !["PGRST202", "42883"].includes(error.code)) throw error;
}

export async function ensureUserProfile(session) {
  if (!supabase || !session?.user) return null;

  const user = session.user;
  const now = new Date().toISOString();
  const avatarUrl = getAvatar(user);
  await refreshExpiredSubscription();

  const { data: existingProfile, error: selectError } = await supabase
    .from("profiles")
    .select(PROFILE_FIELDS)
    .eq("id", user.id)
    .maybeSingle();

  if (selectError) {
    if (isProfilesTableMissing(selectError)) {
      return createFallbackProfile(user, now, avatarUrl);
    }
    throw selectError;
  }

  if (existingProfile) {
    const { data, error } = await supabase
      .from("profiles")
      .update({
        email: user.email,
        avatar_url: avatarUrl,
        last_login: now,
      })
      .eq("id", user.id)
      .select(PROFILE_FIELDS)
      .single();

    if (error) throw error;
    return data;
  }

  for (let attempt = 0; attempt < 12; attempt += 1) {
    const username = attempt === 0 ? preferredUsername(user) || createUsername() : createUsername();
    const { data, error } = await supabase
      .from("profiles")
      .insert({
        id: user.id,
        email: user.email,
        username,
        avatar_url: avatarUrl,
        signup_date: now,
        last_login: now,
      })
      .select(PROFILE_FIELDS)
      .single();

    if (!error) return data;
    if (isProfilesTableMissing(error)) {
      return createFallbackProfile(user, now, avatarUrl);
    }
    if (error.code !== "23505") throw error;
  }

  throw new Error("Could not create a unique SwiftChart username. Please try again.");
}
