/* =========================================================================
   Diagnosis history — the data behind the dashboard's numbers.

   The backend deliberately has no history endpoint: an analysis is a
   stateless read of one photo, and persisting it per account would mean a
   schema, a retention policy and a privacy surface that the product has not
   earned yet. Storing a compact record in localStorage gives the metrics
   grid and the recent list REAL figures instead of decorative zeros, and it
   keeps working with no network at all.

   The tradeoff is stated rather than hidden: this history belongs to the
   BROWSER, not to the account. It is not synced across devices, and clearing
   site data clears it. Only a whitelisted slice is kept — never the image,
   never the full advisory payload.
   ========================================================================= */

import { $, clear, el } from "./dom.js";
import { currentLang, t } from "./i18n.js";

const KEY = "hassad.history";
/** Cap the list so a heavy user cannot grow localStorage without bound. */
const MAX = 60;

/** @returns {Array<Object>} newest first; [] whenever storage is unusable. */
function readHistory() {
  try {
    const raw = localStorage.getItem(KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

function write(list) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)));
  } catch {
    /* Quota or private mode: the analysis itself already succeeded, so the
       history failing to save must never surface as an error to the farmer. */
  }
}

/** Keep only the fields the dashboard renders. Dropping the rest is what
 *  stops localStorage accumulating a second copy of every advisory. */
function pick(report) {
  const d = report.diagnosis || {};
  const conf = (report.confidence && report.confidence.overall) ?? d.confidence ?? 0;
  return {
    at: report.created_at || new Date().toISOString(),
    run_id: report.run_id || null,
    crop: d.crop || "",
    disease: d.disease || "",
    disease_ar: d.disease_ar || "",
    severity: d.severity || "moderate",
    confidence: Number(conf) || 0,
    low_confidence: Boolean(d.low_confidence),
  };
}

export function addRecord(report) {
  if (!report || !report.diagnosis) return;
  const list = readHistory();
  list.unshift(pick(report));
  write(list);
  document.dispatchEvent(new CustomEvent("hassad:history"));
}

function clearHistory() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  document.dispatchEvent(new CustomEvent("hassad:history"));
}

/** Total, "in review" (low confidence — worth a human eye), and the mean
 *  confidence. Guards against an empty list so the grid shows 0 rather than
 *  NaN. */
function stats() {
  const list = readHistory();
  const total = list.length;
  const review = list.filter((r) => r.low_confidence).length;
  const avg = total
    ? list.reduce((sum, r) => sum + (Number(r.confidence) || 0), 0) / total
    : 0;
  return { total, review, avg: Math.round(avg * 100) };
}

function when(iso) {
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) return "";
  const locale = currentLang() === "ar" ? "ar-DZ" : "en-GB";
  const deltaMs = Date.now() - time.getTime();
  const mins = Math.round(deltaMs / 60000);
  try {
    if (mins < 1) return currentLang() === "ar" ? "الآن" : "just now";
    if (mins < 60) {
      return new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(-mins, "minute");
    }
    if (mins < 60 * 24) {
      return new Intl.RelativeTimeFormat(locale, { numeric: "auto" })
        .format(-Math.round(mins / 60), "hour");
    }
    return new Intl.DateTimeFormat(locale, { day: "numeric", month: "short" }).format(time);
  } catch {
    return time.toISOString().slice(0, 10);
  }
}

/** One row. Built entirely from textContent — a disease name is model
 *  output and must never be parsed as markup. */
function row(record) {
  const li = el("li", "recent__row");

  const dot = el("span", "recent__dot");
  dot.setAttribute("data-sev", record.severity || "moderate");
  li.append(dot);

  const main = el("div", "recent__main");
  // Prefer the Arabic name in RTL, the Latin one in English — whichever the
  // farmer is reading the rest of the interface in.
  const label = currentLang() === "ar"
    ? (record.disease_ar || record.disease)
    : (record.disease || record.disease_ar);
  main.append(el("span", "recent__d", label || "—"));
  main.append(el("span", "recent__c", record.crop || "—"));
  li.append(main);

  if (record.low_confidence) li.append(el("span", "recent__flag", t("m_review")));

  li.append(el("span", "recent__conf", `${Math.round((Number(record.confidence) || 0) * 100)}%`));
  li.append(el("span", "recent__when", when(record.at)));
  return li;
}

function renderMetrics() {
  const { total, review, avg } = stats();
  for (const [id, value] of [["metric-total", total],
                             ["metric-review", review],
                             ["metric-conf", avg]]) {
    const node = $(id);
    if (node) node.textContent = String(value);
  }
}

function renderList(container, list, emptyNode) {
  if (!container) return;
  clear(container);
  for (const record of list) container.append(row(record));
  if (emptyNode) emptyNode.classList.toggle("hidden", list.length > 0);
  container.classList.toggle("hidden", list.length === 0);
}

export function initHistory() {
  const recentList = $("recent-list");
  const historyList = $("history-list");
  const historyEmpty = $("history-empty");
  const clearBtn = $("history-clear");

  function paint() {
    const list = readHistory();
    renderMetrics();
    renderList(recentList, list.slice(0, 4), null);
    renderList(historyList, list, historyEmpty);
  }

  clearBtn?.addEventListener("click", clearHistory);
  document.addEventListener("hassad:history", paint);
  // A language change rewrites the row labels (Arabic vs Latin disease name).
  document.addEventListener("hassad:lang", paint);

  paint();
  return { paint, stats, readHistory, addRecord, clearHistory };
}
