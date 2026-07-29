import { useEffect, useMemo, useState } from "react";

const STORAGE_PREFIX = "swiftchart.preferences.v1";
const PREFERENCES_EVENT = "swiftchart:preferences";

export const DEFAULT_APP_PREFERENCES = Object.freeze({
  notifications: {
    pushEnabled: false,
    highConviction: true,
    tp1: true,
    tp2: true,
    stopLoss: true,
    nearEntry: true,
    tradeOpened: true,
    tradeClosed: true,
    marketBias: true,
    sessions: true,
    dailySummary: true,
    systemNotices: true,
    sound: false,
    vibration: false,
  },
  trading: {
    defaultMarket: "crypto",
    defaultTimeframe: "4h",
    minimumScore: 65,
    preferredExchange: "all",
    defaultSorting: "highest_score",
    hideLowConviction: false,
    showOnlyOpen: false,
    prioritizeHighConviction: true,
    rememberFilters: true,
  },
  appearance: {
    theme: "dark",
    compactLayout: false,
    largeText: false,
    reduceAnimations: false,
  },
});

function storageKey(userId) {
  return `${STORAGE_PREFIX}:${userId || "guest"}`;
}

function mergePreferences(value = {}) {
  return {
    notifications: { ...DEFAULT_APP_PREFERENCES.notifications, ...(value.notifications || {}) },
    trading: { ...DEFAULT_APP_PREFERENCES.trading, ...(value.trading || {}) },
    appearance: { ...DEFAULT_APP_PREFERENCES.appearance, ...(value.appearance || {}) },
  };
}

export function loadAppPreferences(userId) {
  try {
    const raw = window.localStorage?.getItem(storageKey(userId));
    return mergePreferences(raw ? JSON.parse(raw) : {});
  } catch {
    return mergePreferences();
  }
}

export function saveAppPreferences(userId, preferences) {
  const next = mergePreferences(preferences);
  window.localStorage?.setItem(storageKey(userId), JSON.stringify(next));
  window.dispatchEvent(new CustomEvent(PREFERENCES_EVENT, { detail: { userId, preferences: next } }));
  return next;
}

export function useAppPreferences(userId) {
  const [preferences, setPreferences] = useState(() => loadAppPreferences(userId));

  useEffect(() => {
    setPreferences(loadAppPreferences(userId));
  }, [userId]);

  useEffect(() => {
    const sync = (event) => {
      if ((event.detail?.userId || null) === (userId || null)) {
        setPreferences(mergePreferences(event.detail.preferences));
      }
    };
    window.addEventListener(PREFERENCES_EVENT, sync);
    return () => window.removeEventListener(PREFERENCES_EVENT, sync);
  }, [userId]);

  return useMemo(() => ({
    preferences,
    updateSection(section, patch) {
      setPreferences((current) => saveAppPreferences(userId, {
        ...current,
        [section]: { ...current[section], ...patch },
      }));
    },
    resetSection(section) {
      setPreferences((current) => saveAppPreferences(userId, {
        ...current,
        [section]: { ...DEFAULT_APP_PREFERENCES[section] },
      }));
    },
  }), [preferences, userId]);
}
