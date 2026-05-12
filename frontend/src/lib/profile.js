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

  const username = createUsername();
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
    profile_storage_ready: false,
  };
}

export async function ensureUserProfile(session) {
  if (!supabase || !session?.user) return null;

  const user = session.user;
  const now = new Date().toISOString();
  const avatarUrl = getAvatar(user);

  const { data: existingProfile, error: selectError } = await supabase
    .from("profiles")
    .select("id,email,username,avatar_url,signup_date,last_login")
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
      .select("id,email,username,avatar_url,signup_date,last_login")
      .single();

    if (error) throw error;
    return data;
  }

  for (let attempt = 0; attempt < 12; attempt += 1) {
    const { data, error } = await supabase
      .from("profiles")
      .insert({
        id: user.id,
        email: user.email,
        username: createUsername(),
        avatar_url: avatarUrl,
        signup_date: now,
        last_login: now,
      })
      .select("id,email,username,avatar_url,signup_date,last_login")
      .single();

    if (!error) return data;
    if (isProfilesTableMissing(error)) {
      return createFallbackProfile(user, now, avatarUrl);
    }
    if (error.code !== "23505") throw error;
  }

  throw new Error("Could not create a unique SwiftChart username. Please try again.");
}
