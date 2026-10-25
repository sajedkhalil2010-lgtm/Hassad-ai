/* =========================================================================
   Pricing panel — a DEMO.

   Nothing here takes money. The three tiers exist to show the business
   model, and the two paid ones answer "subscribe" with a polite notice
   instead of a checkout, because no payment gateway is wired up — a button
   that pretended otherwise would be a lie the farmer only discovers at the
   form. The copy on the panel says the same thing up front.

   The Free tier is the one genuinely live action, and it needs no code
   here: its button carries data-panel-target="studio", which app.js
   already routes like any other panel link.

   Both dialogs on the page (auth, pricing) close independently — each
   guards its OWN visibility in the Escape handler, so whichever one is
   actually open is the one that closes, and no two handlers fight over a
   single key press.
   ========================================================================= */

import { $, $all } from "./dom.js";
import { t } from "./i18n.js";

/** tier -> the i18n key that names it, so the notice answers a specific
 *  question ("Enterprise & Co-op") rather than opening as a generic popup. */
const TIER_KEY = {
  pro: "plan_pro_t",
  enterprise: "plan_ent_t",
};

export function initPricing() {
  const modal = $("plan-modal");
  const label = $("plan-modal-plan");

  if (!modal || !label) {
    console.error("[hassad] pricing markup is missing — panel not wired.");
    return;
  }

  let currentTier = null;

  /** Redraw the tier line. Split out because the language toggle repaints
   *  every [data-i18n] node but cannot reach this one — it is built from
   *  two translated halves at open time, not a single key. */
  function renderLabel() {
    const key = TIER_KEY[currentTier];
    const text = key ? `${t("plan_modal_plan")}: ${t(key)}` : "";
    label.textContent = text;
    label.classList.toggle("hidden", !text);
  }

  function close() {
    currentTier = null;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
  }

  function open(tier) {
    currentTier = tier || null;
    renderLabel();
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    // Focus the dismiss control, not the backdrop: a div with no tabindex
    // silently swallows focus(), which would leave a keyboard user with a
    // dialog open and nothing to reach for.
    modal.querySelector("button[data-close]")?.focus();
  }

  for (const node of $all("[data-plan]")) {
    node.addEventListener("click", () => open(node.dataset.plan));
  }

  // One delegated listener covers the backdrop, the ✕ and "Got it" — all
  // three carry data-close, so each does not need a handler of its own.
  modal.addEventListener("click", (event) => {
    if (event.target.closest("[data-close]")) close();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || modal.classList.contains("hidden")) return;
    close();
  });

  // setLang() repaints [data-i18n] nodes but not this composed label, so an
  // open notice would otherwise keep the previous language on one line.
  document.addEventListener("hassad:lang", () => {
    if (!modal.classList.contains("hidden")) renderLabel();
  });

  return { open, close };
}
