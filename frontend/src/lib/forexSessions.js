const SESSION_DEFINITIONS = [
  { name: "Tokyo", timeZone: "Asia/Tokyo", openHour: 9, closeHour: 18 },
  { name: "UK", timeZone: "Europe/London", openHour: 8, closeHour: 17 },
  { name: "New York", timeZone: "America/New_York", openHour: 8, closeHour: 17 },
];

const formatters = new Map();

function formatterFor(timeZone) {
  if (!formatters.has(timeZone)) {
    formatters.set(timeZone, new Intl.DateTimeFormat("en-US", {
      timeZone,
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }));
  }
  return formatters.get(timeZone);
}

function zonedParts(date, timeZone) {
  return Object.fromEntries(
    formatterFor(timeZone)
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value])
  );
}

function forexMarketOpenAt(date) {
  const newYork = zonedParts(date, "America/New_York");
  const hour = Number(newYork.hour);
  if (["Mon", "Tue", "Wed", "Thu"].includes(newYork.weekday)) return true;
  if (newYork.weekday === "Fri") return hour < 17;
  if (newYork.weekday === "Sun") return hour >= 17;
  return false;
}

function sessionOpenAt(date, session) {
  if (!forexMarketOpenAt(date)) return false;
  const local = zonedParts(date, session.timeZone);
  const minutes = Number(local.hour) * 60 + Number(local.minute);
  return minutes >= session.openHour * 60 && minutes < session.closeHour * 60;
}

function findNextSessionOpen(now) {
  const stepMinutes = 15;
  const maxMinutes = 7 * 24 * 60;

  for (let offset = stepMinutes; offset <= maxMinutes; offset += stepMinutes) {
    const candidate = new Date(now.getTime() + offset * 60_000);
    const previous = new Date(candidate.getTime() - stepMinutes * 60_000);
    const openingSession = SESSION_DEFINITIONS.find(
      (session) => sessionOpenAt(candidate, session) && !sessionOpenAt(previous, session)
    );
    if (!openingSession) continue;

    const lowerBound = Math.max(1, offset - stepMinutes);
    for (let minute = lowerBound; minute <= offset; minute += 1) {
      const exact = new Date(now.getTime() + minute * 60_000);
      const minuteBefore = new Date(exact.getTime() - 60_000);
      if (sessionOpenAt(exact, openingSession) && !sessionOpenAt(minuteBefore, openingSession)) {
        return { name: openingSession.name, opensAt: exact, minutesUntil: minute };
      }
    }
  }

  return { name: "-", opensAt: null, minutesUntil: null };
}

export function getForexSessionState(now = new Date()) {
  const marketOpen = forexMarketOpenAt(now);
  const activeSessions = SESSION_DEFINITIONS
    .filter((session) => sessionOpenAt(now, session))
    .map((session) => session.name);
  const next = findNextSessionOpen(now);

  let displayName = "Forex Closed";
  if (activeSessions.length) displayName = `${activeSessions.join(" + ")} Open`;
  else if (marketOpen) displayName = "Between Sessions";

  return {
    activeSessions,
    displayName,
    marketOpen,
    nextSession: next.name,
    nextSessionOpen: next.opensAt,
    minutesUntilNext: next.minutesUntil,
  };
}

export function formatSessionCountdown(minutes) {
  if (!Number.isFinite(minutes)) return "-";
  if (minutes < 60) return `${Math.max(1, minutes)}m`;
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const remainingMinutes = minutes % 60;
  if (days) return `${days}d ${hours}h`;
  return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

export function formatUtcClock(date = new Date()) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}
