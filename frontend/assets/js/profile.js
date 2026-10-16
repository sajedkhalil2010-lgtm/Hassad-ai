/* =====================================================================
   profile.js — local onboarding profile (LocalStorage session)
   The first-run gateway collects the farmer's name, farm location
   (default: Tebessa, Algeria) and crop focus. The profile lives on the
   device; app.js silently upgrades it to a full backend account when
   the API is reachable, and keeps working in local guest mode when not.
   ===================================================================== */

const KEY = "hassad.profile";

/* Canonical crop vocabulary — same stable ids as the backend vision
   pipeline, so a chosen crop flows straight into the diagnose call. */
export const CROPS = [
  { id: "tomato", ar: "طماطم", en: "Tomato" },
  { id: "potato", ar: "بطاطس", en: "Potato" },
  { id: "wheat", ar: "قمح", en: "Wheat" },
  { id: "rice", ar: "أرز", en: "Rice" },
  { id: "maize", ar: "ذرة", en: "Maize" },
  { id: "cotton", ar: "قطن", en: "Cotton" },
  { id: "grape", ar: "عنب", en: "Grape" },
  { id: "soybean", ar: "فول صويا", en: "Soybean" },
  { id: "barley", ar: "شعير", en: "Barley" },
  { id: "date-palm", ar: "نخيل", en: "Date Palm" },
];

export function getProfile() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const p = JSON.parse(raw);
    return p && typeof p === "object" ? p : null;
  } catch (e) {
    return null;
  }
}

export function hasProfile() {
  return !!getProfile();
}

export function saveProfile(p) {
  const clean = {
    name: String((p && p.name) || "").slice(0, 80),
    location: String((p && p.location) || "").slice(0, 120),
    crop: String((p && p.crop) || "").slice(0, 40),
    ts: Date.now(),
  };
  try {
    localStorage.setItem(KEY, JSON.stringify(clean));
  } catch (e) {
    /* private mode — profile stays in memory for this tab */
  }
  return clean;
}

export function clearProfile() {
  try {
    localStorage.removeItem(KEY);
  } catch (e) {
    /* ignore */
  }
}

/** Localized label for a stored crop id ("tomato" → "طماطم" / "Tomato"). */
export function cropLabel(id, lang) {
  const row = CROPS.find(function (c) {
    return c.id === id;
  });
  if (!row) return id || "";
  return lang === "en" ? row.en : row.ar;
}
