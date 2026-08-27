import { useEffect, useMemo, useState } from "react";

const STORAGE_PREFIX = "swiftchart.notifications.v2";
const PUSH_EVENT = "swiftchart:notification";
const REFRESH_EVENT = "swiftchart:notifications-refresh-requested";

export function notificationStorageKey(userId) {
  return `${STORAGE_PREFIX}:${userId || "guest"}`;
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

function readStoredNotifications(userId) {
  try {
    const stored = JSON.parse(window.localStorage?.getItem(notificationStorageKey(userId)) || "null");
    if (Array.isArray(stored)) return stored.map(normalizeNotification);
  } catch {
    // A corrupt local cache should not prevent the app from opening.
  }
  return [];
}

function newestFirst(rows) {
  return [...rows].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
}

export function pushSwiftChartNotification(notification) {
  window.dispatchEvent(new CustomEvent(PUSH_EVENT, { detail: normalizeNotification(notification) }));
}

export function useNotifications(userId) {
  const storageKey = notificationStorageKey(userId);
  const [store, setStore] = useState(() => ({
    key: storageKey,
    notifications: newestFirst(readStoredNotifications(userId)),
  }));
  const notifications = store.key === storageKey ? store.notifications : [];

  const updateNotifications = (updater) => {
    setStore((current) => {
      const rows = current.key === storageKey
        ? current.notifications
        : newestFirst(readStoredNotifications(userId));
      return { key: storageKey, notifications: updater(rows) };
    });
  };

  useEffect(() => {
    if (store.key !== storageKey) return;
    try {
      window.localStorage?.setItem(storageKey, JSON.stringify(store.notifications));
    } catch {
      // Persistence can be unavailable in private browsing; in-memory state still works.
    }
  }, [storageKey, store]);

  useEffect(() => {
    setStore((current) => current.key === storageKey ? current : {
      key: storageKey,
      notifications: newestFirst(readStoredNotifications(userId)),
    });
  }, [storageKey, userId]);

  useEffect(() => {
    function receiveNotification(event) {
      const incoming = normalizeNotification(event.detail);
      updateNotifications((current) => newestFirst([incoming, ...current.filter((item) => item.id !== incoming.id)]));
    }
    window.addEventListener(PUSH_EVENT, receiveNotification);
    return () => window.removeEventListener(PUSH_EVENT, receiveNotification);
  }, [storageKey]);

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
      updateNotifications((current) => current.map((item) => item.id === id ? { ...item, read: true } : item));
    },
    markAllRead() {
      updateNotifications((current) => current.map((item) => ({ ...item, read: true })));
    },
    remove(id) {
      updateNotifications((current) => current.filter((item) => item.id !== id));
    },
    clearAll() {
      updateNotifications(() => []);
    },
    refresh() {
      window.dispatchEvent(new CustomEvent(REFRESH_EVENT));
      updateNotifications(() => newestFirst(readStoredNotifications(userId)));
    },
  };
}
