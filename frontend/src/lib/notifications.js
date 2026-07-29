import { useEffect, useMemo, useState } from "react";

const STORAGE_KEY = "swiftchart.notifications.v1";
const PUSH_EVENT = "swiftchart:notification";
const REFRESH_EVENT = "swiftchart:notifications-refresh-requested";

function minutesAgo(minutes) {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

function defaultNotifications() {
  return [
    {
      id: "high-conviction-btc",
      type: "high_conviction",
      category: "trades",
      title: "BTCUSDT",
      message: "High Conviction LONG",
      symbol: "BTCUSDT",
      score: 97,
      direction: "Long",
      timestamp: minutesAgo(2),
      read: false,
      priority: "high",
      statusBadge: "High Conviction",
      actionUrl: "/app/scan?market=crypto&detail=BTCUSDT",
    },
    {
      id: "near-entry-eth",
      type: "near_entry",
      category: "alerts",
      title: "ETHUSDT near entry",
      message: "ETHUSDT is 0.4% away from entry.",
      symbol: "ETHUSDT",
      score: 85,
      direction: "Long",
      timestamp: minutesAgo(11),
      read: false,
      priority: "normal",
      statusBadge: "Near Entry",
      actionUrl: "/app/scan?market=crypto&detail=ETHUSDT",
    },
    {
      id: "stop-loss-avax",
      type: "stop_loss",
      category: "alerts",
      title: "AVAXUSDT",
      message: "Stop Loss hit. The setup has been closed.",
      symbol: "AVAXUSDT",
      timestamp: minutesAgo(24),
      read: false,
      priority: "urgent",
      statusBadge: "Stop Loss",
      actionUrl: "/app/history?market=crypto",
    },
    {
      id: "tp1-sol",
      type: "take_profit",
      category: "trades",
      title: "SOLUSDT",
      message: "TP1 hit. The remaining position is still tracking.",
      symbol: "SOLUSDT",
      timestamp: minutesAgo(48),
      read: true,
      priority: "normal",
      statusBadge: "TP1 Hit",
      actionUrl: "/app/history?market=crypto",
    },
    {
      id: "bias-btc",
      type: "market_bias",
      category: "market",
      title: "Market bias changed",
      message: "BTC switched to Bullish Trend.",
      symbol: "BTCUSDT",
      timestamp: minutesAgo(76),
      read: false,
      priority: "normal",
      statusBadge: "Bullish",
      actionUrl: "/app/home?market=crypto",
    },
    {
      id: "london-session",
      type: "session_started",
      category: "market",
      title: "London session is open",
      message: "Forex liquidity and overlap filters are now active.",
      symbol: "GBPUSD",
      timestamp: minutesAgo(132),
      read: true,
      priority: "normal",
      statusBadge: "Session Open",
      actionUrl: "/app/home?market=forex",
    },
    {
      id: "daily-summary",
      type: "daily_summary",
      category: "market",
      title: "Daily market summary",
      message: "Momentum favors selective longs. Three clean setups remain active.",
      symbol: "",
      timestamp: minutesAgo(260),
      read: true,
      priority: "normal",
      actionUrl: "/app/home?market=crypto",
    },
    {
      id: "system-update",
      type: "system",
      category: "alerts",
      title: "SwiftChart updated",
      message: "Notification controls and trade alerts are now available.",
      symbol: "",
      timestamp: minutesAgo(1440),
      read: true,
      priority: "normal",
      statusBadge: "Update",
      actionUrl: "",
    },
  ];
}

function normalizeNotification(notification = {}) {
  return {
    id: String(notification.id || `${notification.type || "notice"}-${Date.now()}`),
    type: String(notification.type || "system"),
    category: ["trades", "alerts", "market"].includes(notification.category) ? notification.category : "alerts",
    title: String(notification.title || "SwiftChart"),
    message: String(notification.message || ""),
    symbol: String(notification.symbol || "").toUpperCase(),
    score: Number.isFinite(Number(notification.score)) ? Math.round(Number(notification.score)) : null,
    direction: String(notification.direction || ""),
    timestamp: notification.timestamp || new Date().toISOString(),
    read: Boolean(notification.read),
    priority: notification.priority === "urgent" ? "urgent" : notification.priority === "high" ? "high" : "normal",
    statusBadge: String(notification.statusBadge || ""),
    actionUrl: String(notification.actionUrl || ""),
  };
}

function readStoredNotifications() {
  try {
    const stored = JSON.parse(window.localStorage?.getItem(STORAGE_KEY) || "null");
    if (Array.isArray(stored)) return stored.map(normalizeNotification);
  } catch {
    // A corrupt local cache should not prevent the app from opening.
  }
  return defaultNotifications();
}

function newestFirst(rows) {
  return [...rows].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
}

export function pushSwiftChartNotification(notification) {
  window.dispatchEvent(new CustomEvent(PUSH_EVENT, { detail: normalizeNotification(notification) }));
}

export function useNotifications() {
  const [notifications, setNotifications] = useState(() => newestFirst(readStoredNotifications()));

  useEffect(() => {
    try {
      window.localStorage?.setItem(STORAGE_KEY, JSON.stringify(notifications));
    } catch {
      // Persistence can be unavailable in private browsing; in-memory state still works.
    }
  }, [notifications]);

  useEffect(() => {
    function receiveNotification(event) {
      const incoming = normalizeNotification(event.detail);
      setNotifications((current) => newestFirst([incoming, ...current.filter((item) => item.id !== incoming.id)]));
    }
    window.addEventListener(PUSH_EVENT, receiveNotification);
    return () => window.removeEventListener(PUSH_EVENT, receiveNotification);
  }, []);

  const unread = useMemo(() => notifications.filter((item) => !item.read), [notifications]);
  const priorityIndicator = unread.some((item) => item.priority === "urgent" || item.type === "stop_loss")
    ? "urgent"
    : unread.some((item) => item.priority === "high" || item.type === "high_conviction")
      ? "high"
      : "";

  return {
    notifications,
    unreadCount: unread.length,
    priorityIndicator,
    markRead(id) {
      setNotifications((current) => current.map((item) => item.id === id ? { ...item, read: true } : item));
    },
    markAllRead() {
      setNotifications((current) => current.map((item) => ({ ...item, read: true })));
    },
    remove(id) {
      setNotifications((current) => current.filter((item) => item.id !== id));
    },
    clearAll() {
      setNotifications([]);
    },
    refresh() {
      window.dispatchEvent(new CustomEvent(REFRESH_EVENT));
      setNotifications((current) => current.length ? newestFirst(current) : newestFirst(defaultNotifications()));
    },
  };
}
