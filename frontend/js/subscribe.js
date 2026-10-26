/* =========================================================================
   Sign-up handoff — choose a plan, then a checkout that collects nothing.

   Registration used to drop a brand-new account straight into the
   workspace. It stops here first, because the moment someone creates a
   session is the moment they need to know what it costs to keep.

   THE CARD FIELDS ARE A MOCK-UP AND MUST STAY ONE. This module imports
   nothing from api.js and issues no network traffic of any kind, so what is
   typed into them cannot leave the page: it exists in four DOM nodes, is
   wiped whenever the dialog closes, and is wiped again on completion. The
   fields carry autocomplete="off" deliberately — a browser offered these
   digits would offer to keep them, which is worse than losing them. Remove
   that and the form starts harvesting real card numbers for a backend with
   nowhere to put them. The banner above the fields says the same thing to
   the person typing, because a form that looks this complete is otherwise
   indistinguishable from one that charges money.

   Three routes out, every one of them arriving at the workspace:
     · Complete Subscription  -> confirmation toast -> workspace
     · Skip for now           -> workspace on the Free tier
     · Back                   -> the plan step

   The dialog owns its own Escape handling and traps its own focus, exactly
   as the account and pricing dialogs do — each guards its own visibility so
   that whichever is open is the one that closes, and no two handlers fight
   over a single key press.
   ========================================================================= */

import { $ } from "./dom.js";
import { t } from "./i18n.js";

/** tier -> the i18n key that names it, so a status line can name the plan
 *  that was actually chosen rather than a generic success sentence. */
const TIER_KEY = {
  free: "su_free_t",
  pro: "su_pro_t",
  enterprise: "su_ent_t",
};

/** Shown on the payment step as the amount about to be "taken". It is a
 *  figure, not a charge — nothing is processed anywhere. */
const PRICE = { free: "$0", pro: "$9", enterprise: "$49" };

/** Free has nothing to pay for, so routing it through the card step would
 *  ask for details with no reason attached to them. */
const PAID = new Set(["pro", "enterprise"]);

let els = null;
let onDone = null;
let lastFocused = null;
let plan = "free";
let step = "plan";
/* The status line is built from a key rather than a sentence so the language
   flip can rebuild it in the other language instead of stranding whichever
   one was showing. */
let statusKey = null;
let statusTone = "";
let toastTimer = null;

/* ----------------------------------------------------------- rendering -- */

function setStatus(key, tone) {
  statusKey = key || null;
  statusTone = tone || "";
  els.status.textContent = key ? t(key) : "";
  if (statusTone) els.status.setAttribute("data-tone", statusTone);
  else els.status.removeAttribute("data-tone");
}

function showStep(next) {
  step = next;
  const paying = step === "pay";

  els.planStep.classList.toggle("hidden", paying);
  els.payStep.classList.toggle("hidden", !paying);
  els.stepPlan.classList.toggle("is-active", !paying);
  els.stepPlan.classList.toggle("is-done", paying);
  els.stepPay.classList.toggle("is-active", paying);

  // The dialog heading tracks the step, so it is never left announcing the
  // stage the reader has already left behind.
  els.title.textContent = paying ? t("su_pay_t") : t("su_title");
  setStatus("");
}

function selectedPlan() {
  const picked = els.modal.querySelector('input[name="su-plan"]:checked');
  return picked ? picked.value : "free";
}

/** The line under the heading: which tier, at what figure. Rebuilt on every
 *  open because the radio keeps its previous state across dialogs. */
function renderSummary() {
  els.summary.textContent = `${t(TIER_KEY[plan])} · ${PRICE[plan]}`;
}

/* ------------------------------------------------------------ formatting -- */

/* Digits only, grouped as they are typed. Reading the field rather than
   keeping a parsed copy is the point: there is nowhere for the value to
   accumulate. `(.{4})` is balanced, so the syntax pass still balances. */

function formatNumber(input) {
  const digits = input.value.replace(/\D/g, "").slice(0, 16);
  input.value = digits.replace(/(.{4})/g, "$1 ").trim();
}

function formatExpiry(input) {
  const digits = input.value.replace(/\D/g, "").slice(0, 4);
  input.value = digits.length > 2 ? `${digits.slice(0, 2)}/${digits.slice(2)}` : digits;
}

function formatCvv(input) {
  input.value = input.value.replace(/\D/g, "").slice(0, 4);
}

/* ---------------------------------------------------------- validation -- */

function validate() {
  const number = els.number.value.replace(/\D/g, "");
  if (number.length < 15) {
    setStatus("su_bad_number", "err");
    els.number.focus();
    return false;
  }

  const parts = els.expiry.value.split("/");
  const month = Number(parts[0]);
  const year = Number(parts[1]);
  if (!month || month < 1 || month > 12 || !parts[1] || parts[1].length !== 2) {
    setStatus("su_bad_expiry", "err");
    els.expiry.focus();
    return false;
  }
  // The card has to outlive the plan, so an expiry already in the past is
  // the one value worth refusing outright.
  const ends = new Date(2000 + year, month, 1);
  if (Number.isNaN(ends.getTime()) || ends.getTime() <= Date.now()) {
    setStatus("su_bad_expiry", "err");
    els.expiry.focus();
    return false;
  }

  const cvv = els.cvv.value.replace(/\D/g, "");
  if (cvv.length < 3) {
    setStatus("su_bad_cvv", "err");
    els.cvv.focus();
    return false;
  }

  if (!els.holder.value.trim()) {
    setStatus("su_bad_holder", "err");
    els.holder.focus();
    return false;
  }

  return true;
}

/* -------------------------------------------------------------- output -- */

function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("is-on");
  clearTimeout(toastTimer);
  // Long enough to read across two languages, short enough not to become
  // furniture on the dashboard behind it.
  toastTimer = setTimeout(() => els.toast.classList.remove("is-on"), 4500);
}

function wipe() {
  // Every exit passes through here, so a typed number cannot outlive the
  // dialog that held it — not on completion, not on Escape, not on a close
  // from the backdrop.
  els.number.value = "";
  els.expiry.value = "";
  els.cvv.value = "";
  els.holder.value = "";
}

function close() {
  els.modal.classList.add("hidden");
  document.body.style.overflow = "";
  wipe();
  setStatus("");
  if (lastFocused && typeof lastFocused.focus === "function") lastFocused.focus();
  lastFocused = null;
}

/** The one way out: clear the fields, drop the dialog, say what happened,
 *  then hand back to the shell, which moves the reader into the workspace. */
function finish(message) {
  close();
  toast(message);
  if (onDone) onDone();
}

/* ------------------------------------------------------------ behaviour -- */

function continueToPayment() {
  plan = selectedPlan();
  if (!PAID.has(plan)) {
    finish(t("su_free_ok"));
    return;
  }
  renderSummary();
  showStep("pay");
  els.number.focus();
}

function backToPlans() {
  showStep("plan");
  els.modal.querySelector('input[name="su-plan"]:checked')?.focus();
}

function skip() {
  plan = "free";
  finish(t("su_free_ok"));
}

function complete(event) {
  event.preventDefault();
  if (!validate()) return;
  finish(t("su_ok"));
}

/* ---------------------------------------------------------------- open -- */

function open(user) {
  lastFocused = document.activeElement;
  // Read the radio rather than assuming "free": the group keeps its state
  // across dialogs, and the summary must describe what is actually marked.
  plan = selectedPlan();
  showStep("plan");
  els.who.textContent = user ? user.email || user.name || "" : "";
  els.modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  // Focus the first plan rather than the close button: the reader has just
  // arrived with a decision to make, and the heading is not a control.
  els.modal.querySelector('input[name="su-plan"]:checked')?.focus();
}

/* --------------------------------------------------------------- wiring -- */

export function initSubscribe({ onFinish } = {}) {
  els = {
    modal: $("su-modal"),
    title: $("su-title"),
    who: $("su-who"),
    summary: $("su-summary"),
    planStep: $("su-plan-step"),
    payStep: $("su-pay-step"),
    stepPlan: $("su-step-plan"),
    stepPay: $("su-step-pay"),
    status: $("su-status"),
    form: $("pay-form"),
    number: $("pay-number"),
    expiry: $("pay-expiry"),
    cvv: $("pay-cvv"),
    holder: $("pay-holder"),
    toast: $("toast"),
    goPay: $("su-continue"),
    skip: $("su-skip"),
    back: $("su-back"),
  };

  // One missing id is a dead button with no error anywhere, so every node is
  // checked together and the ones that failed are named. Binding what exists
  // would leave a half-wired dialog that looks wired.
  const missing = Object.entries(els)
    .filter(([, node]) => !node)
    .map(([key]) => key);
  if (missing.length) {
    console.error(
      `[hassad] subscription markup is incomplete (${missing.join(", ")}) — flow not wired.`,
    );
    return { open: () => {} };
  }

  onDone = onFinish || null;

  els.goPay.addEventListener("click", continueToPayment);
  els.back.addEventListener("click", backToPlans);
  els.skip.addEventListener("click", skip);
  els.form.addEventListener("submit", complete);

  // Grouping as the reader types is the whole of the formatting: no value is
  // copied anywhere, it is rewritten in the node that already holds it.
  els.number.addEventListener("input", () => formatNumber(els.number));
  els.expiry.addEventListener("input", () => formatExpiry(els.expiry));
  els.cvv.addEventListener("input", () => formatCvv(els.cvv));

  els.modal.addEventListener("click", (event) => {
    if (event.target.closest("[data-close]")) close();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || els.modal.classList.contains("hidden")) return;
    close();
  });

  // The heading and the status line are built from keys, so they have to be
  // rebuilt when the language flips — [data-i18n] repaints itself but cannot
  // reach either of them.
  document.addEventListener("hassad:lang", () => {
    els.title.textContent = step === "pay" ? t("su_pay_t") : t("su_title");
    if (statusKey) setStatus(statusKey, statusTone);
    renderSummary();
  });

  // Keep focus inside the dialog while it is modal — without this, tabbing
  // walks off to the page behind the backdrop and the reader is stranded.
  els.modal.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const focusables = [...els.modal.querySelectorAll(
      'button:not([disabled]):not(.hidden), input:not([disabled]):not(.hidden), [tabindex]:not([tabindex="-1"])',
    )].filter((el) => el.offsetParent !== null);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  return { open, close };
}
