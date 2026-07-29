export const PAYMENTS_ENABLED =
  String(import.meta.env.VITE_PAYMENTS_ENABLED || "").trim().toLowerCase() === "true";

export const PAYMENTS_COMING_SOON_MESSAGE = "SwiftChart Pro payments are coming soon.";
