import { useRef, useState } from "react";
import { Bell, CheckCheck, RefreshCw, Trash2, X } from "lucide-react";

function timeAgo(timestamp) {
  const elapsed = Math.max(0, Date.now() - new Date(timestamp).getTime());
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "Yesterday" : `${days}d ago`;
}

function badgeTone(notification) {
  if (notification.type === "stop_loss" || notification.priority === "urgent") return "urgent";
  if (notification.type === "take_profit" || /bullish|tp/i.test(notification.statusBadge)) return "profit";
  if (notification.type === "high_conviction") return "high";
  return "neutral";
}

function NotificationCard({ notification, renderAsset, onOpen, onDelete }) {
  const [dragX, setDragX] = useState(0);
  const [removing, setRemoving] = useState(false);
  const pointerStart = useRef(null);
  const moved = useRef(false);

  function pointerDown(event) {
    pointerStart.current = event.clientX;
    moved.current = false;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function pointerMove(event) {
    if (pointerStart.current === null) return;
    const next = Math.max(-92, Math.min(0, event.clientX - pointerStart.current));
    if (Math.abs(next) > 5) moved.current = true;
    setDragX(next);
  }

  function pointerUp() {
    pointerStart.current = null;
    if (dragX <= -62) {
      setRemoving(true);
      window.setTimeout(() => onDelete(notification.id), 180);
      return;
    }
    setDragX(0);
  }

  function openNotification() {
    if (moved.current) {
      moved.current = false;
      return;
    }
    onOpen(notification);
  }

  return (
    <div className={`notification-swipe-shell${removing ? " removing" : ""}`}>
      <div className="notification-delete-cue" aria-hidden="true"><Trash2 size={17} /> Delete</div>
      <button
        type="button"
        className={`notification-card${notification.read ? "" : " unread"}`}
        style={{ transform: `translateX(${dragX}px)` }}
        onPointerDown={pointerDown}
        onPointerMove={pointerMove}
        onPointerUp={pointerUp}
        onPointerCancel={() => { pointerStart.current = null; setDragX(0); }}
        onClick={openNotification}
        aria-label={`${notification.read ? "" : "Unread "}${notification.title}: ${notification.message}`}
      >
        <div className="notification-asset">
          {notification.symbol ? renderAsset(notification.symbol) : <Bell size={18} />}
        </div>
        <div className="notification-copy">
          <div>
            <strong>{notification.title}</strong>
            {!notification.read ? <i aria-label="Unread" /> : null}
          </div>
          <p>{notification.message}</p>
          <small>{notification.score ? `Score ${notification.score} · ` : ""}{timeAgo(notification.timestamp)}</small>
        </div>
        {notification.statusBadge ? <span className={`notification-status ${badgeTone(notification)}`}>{notification.statusBadge}</span> : null}
      </button>
    </div>
  );
}

export default function NotificationCenter({
  notifications,
  onOpen,
  onDelete,
  onMarkAllRead,
  onRefresh,
  onClose,
  renderAsset,
}) {
  const visible = notifications;
  const unreadCount = notifications.filter((item) => !item.read).length;

  return (
    <div className="notification-center">
      <header className="notification-center-header">
        <div className="notification-center-title">
          <h1>Notifications</h1>
          <span>{unreadCount} unread</span>
        </div>
        <div className="notification-center-actions">
          <button type="button" onClick={onMarkAllRead} disabled={!unreadCount}>
            <CheckCheck size={14} />
            Mark all read
          </button>
          <button type="button" className="notification-close" onClick={onClose} aria-label="Close notifications">
            <X size={17} />
          </button>
        </div>
      </header>

      {visible.length ? (
        <section className="notification-list" aria-live="polite">
          {visible.map((notification) => (
            <NotificationCard
              key={notification.id}
              notification={notification}
              renderAsset={renderAsset}
              onOpen={onOpen}
              onDelete={onDelete}
            />
          ))}
        </section>
      ) : (
        <section className="notification-empty">
          <Bell size={27} />
          <h2>No notifications yet</h2>
          <p>We&apos;ll notify you when high-quality trading opportunities or important market updates are available.</p>
          <button type="button" onClick={onRefresh}><RefreshCw size={15} /> Refresh</button>
        </section>
      )}
    </div>
  );
}
