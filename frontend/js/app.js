/* =========================================================================
   Entry point — the shell that holds the two screens together.

   All requests are same-origin: the backend serves this page at '/', so
   there is no base-URL override to configure and no CORS to reason about.

   WIRING IS NOT DONE AT TOP LEVEL. Every handler is attached from boot(),
   which is invoked either immediately or on DOMContentLoaded (whichever
   comes later). A module script normally runs with the document already
   parsed, so this costs nothing — but if the script is ever loaded into
   <head>, made non-deferred, or split and re-imported, the old top-level
   version would have run against an incomplete DOM. It would NOT have
   thrown: `$(id)?.addEventListener(...)` matches nothing and silently
   binds nothing, which reads exactly like "the buttons don't work". The
   guard below removes that failure mode instead of leaving it as a
   landmine for whoever edits the script tag next.

   The app is ONE document with two stages:
      · #landing  — the public face, on stage until a session or a guest
                    decision exists;
      · #app      — the workspace. Never unmounted, only hidden, so the
                    diagnostic module's wiring survives the transition and
                    the page does not re-download anything to switch screens.
   ========================================================================= */

import { ping } from "./api.js";
import { $, $all } from "./dom.js";
import { initAuth } from "./auth.js";
import { initChat } from "./chat.js";
import { initDiagnose } from "./diagnose.js";
import { initHistory } from "./history.js";
import { initI18n } from "./i18n.js";
import { initPricing } from "./pricing.js";
import { initSubscribe } from "./subscribe.js";

const body = document.body;

/* Everything boot() looks up once and hands to a handler below. Declared at
   module scope because openChat/closeChat, the Escape handler and
   refreshConnection all read them, and they outlive any single call. */
let chatModal = null;
let diagnose = null;
let auth = null;
let subscribe = null;
let conn = null;
let connLabel = null;
let poller = null;

/* ------------------------------------------------------------- screens -- */

/** The single way into the workspace. Reached after a successful sign-in,
 *  after creating an account, or by continuing as a guest — diagnosis never
 *  requires an account, so the login dialog is an invitation rather than a
 *  toll gate. Sign-out deliberately does NOT reverse it: the workspace runs
 *  without an account, so sending a user back to the landing after they
 *  sign out would punish them for leaving a tool that works anonymously. */
function enterDashboard(panel = "dashboard") {
  const first = body.dataset.view !== "dashboard";
  body.dataset.view = "dashboard";
  $("landing")?.classList.add("hidden");
  $("app")?.classList.remove("hidden");
  showPanel(panel);
  if (first) window.scrollTo(0, 0);
}

/* ------------------------------------------------------------- panels -- */

function showPanel(name) {
  body.dataset.panel = name;

  for (const node of $all("[data-view-panel]")) {
    node.classList.toggle("hidden", node.dataset.viewPanel !== name);
  }
  // Only the sidebar carries an active marker: applying it to a quick-action
  // card would leave two things on screen claiming to be "the current page".
  for (const node of $all(".snav__item[data-panel-target]")) {
    node.classList.toggle("is-active", node.dataset.panelTarget === name);
  }
}

/* --------------------------------------------------------------- chat -- */

function openChat() {
  chatModal?.classList.remove("hidden");
  $("chat-input")?.focus();
}

function closeChat() {
  chatModal?.classList.add("hidden");
}

/* --------------------------------------------------------------- bind -- */

/** Attach one handler, and say so loudly if the id matched nothing.
 *
 *  `$(id)?.addEventListener(...)` is the idiomatic guard, and every id below
 *  is asserted to exist by the static suite — so in a healthy build this
 *  helper never logs. It exists because the optional-chaining form fails
 *  SILENTLY: a typo'd id, a renamed element or a node that has not rendered
 *  reads as a dead button with no error anywhere. One console line turns
 *  that into a real signal instead of a support ticket.
 *
 *  Returns the element so a handler can keep its own reference (the chat
 *  expand button reads and writes aria-pressed on itself). */
function bind(id, type, handler) {
  const el = $(id);
  if (!el) {
    console.error(`[hassad] #${id} is missing — the "${type}" handler was not bound.`);
    return null;
  }
  el.addEventListener(type, handler);
  return el;
}

/** The same, for a selector that must match at least one node. A loop over
 *  an empty array binds nothing at all and looks identical to a broken
 *  button, so zero matches is reported the same way. */
function bindAll(selector, type, handler) {
  const nodes = $all(selector);
  if (!nodes.length) {
    console.error(`[hassad] "${selector}" matched nothing — the "${type}" handler was not bound.`);
    return 0;
  }
  for (const node of nodes) node.addEventListener(type, handler);
  return nodes.length;
}

/* ------------------------------------------------------ connection pill -- */

async function refreshConnection() {
  const up = await ping();
  conn?.setAttribute("data-state", up ? "up" : "down");
  if (connLabel) connLabel.textContent = up ? "Server online" : "Offline";
  if (conn) {
    conn.title = up
      ? "Backend reachable"
      : "Backend unreachable. Start it from the project folder with start_stable.bat";
  }
}

// Poll slowly, and stop entirely while the tab is hidden — a background tab
// should not be keeping a request loop alive on a phone in a field.
function startPolling() {
  if (poller) return;
  poller = setInterval(refreshConnection, 30_000);
}

function stopPolling() {
  clearInterval(poller);
  poller = null;
}

/* -------------------------------------------------------------- wiring -- */

/** Every listener in the app is attached here — and only here. */
function boot() {
  chatModal = $("chat-modal");

  // Diagnosis first: the assistant asks it for context whenever a question is
  // sent, so it has to exist before the chat is constructed.
  diagnose = initDiagnose();
  initChat({ getContext: () => diagnose.getContext() });
  initHistory();
  initI18n();
  // After initI18n so the stored language is already applied when the
  // notice first renders its composed tier label.
  initPricing();

  // Before initAuth: the registration hook below closes over this handle.
  subscribe = initSubscribe({ onFinish: () => enterDashboard() });

  auth = initAuth({
    onChange(user, meta) {
      const name = $("welcome-name");
      if (name) name.textContent = user ? (user.name || user.email) : "👤";
      // Any real session moves the farmer off the landing page and into work
      // — except a brand-new one, which has a plan to choose first. The
      // subscription step calls onFinish once that decision is made, so the
      // workspace opens exactly once and from exactly one place.
      if (user && !(meta && meta.signup)) enterDashboard();
    },
    // Diagnosis works without an account, so the dialog has to let people
    // past it — otherwise a tool that runs anonymously would look paywalled.
    onGuest: () => enterDashboard(),
    // Registration hands off here instead of opening the workspace.
    onRegistered: (user) => subscribe.open(user),
  });

  /* Account entry points on the landing page. */
  for (const id of ["landing-login", "landing-signup"]) {
    bind(id, "click", () => auth.open());
  }

  /* The primary CTA says "start diagnosing now", and diagnosis needs no
     account — so it goes straight to the studio and puts the cursor in the
     dropzone instead of parking a sign-in dialog between a farmer and the
     tool. Anyone who does want a session still has the two links above; this
     is the same route "New diagnosis" takes in the workspace. */
  bind("hero-start", "click", () => {
    enterDashboard("studio");
    diagnose.focusDropzone();
  });

  /* Theme toggle — landing only. The workspace has no light variant, and this
     control never reaches into it: the rules it flips are scoped to .landing. */
  bind("theme-toggle", "click", () => {
    const next = body.dataset.theme === "light" ? "dark" : "light";
    body.dataset.theme = next;
    $("theme-toggle")?.setAttribute("aria-pressed", String(next === "light"));
  });

  /* Panel routing: sidebar items, the quick-action cards and "see all". */
  bindAll("[data-panel-target]", "click", (event) =>
    enterDashboard(event.currentTarget.dataset.panelTarget),
  );

  /* Anything that means "open the assistant" — sidebar item, quick card, the
     top-bar button. One handler so the entry points cannot drift apart. */
  bindAll('[data-action="assistant"]', "click", openChat);
  bind("ask-assistant", "click", openChat);

  /* The top-bar primary action drops the farmer straight into a fresh upload. */
  bind("new-diagnosis", "click", () => {
    enterDashboard("studio");
    diagnose.focusDropzone();
  });

  bindAll("[data-chat-close]", "click", closeChat);

  /* Expand widens the panel rather than mounting a second layout. */
  bind("chat-expand", "click", function onExpand() {
    const open = chatModal.dataset.expanded === "true";
    chatModal.dataset.expanded = String(!open);
    this.setAttribute("aria-pressed", String(!open));
  });

  /* Escape closes whichever dialog is on top, and only the one that is open. */
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (chatModal && !chatModal.classList.contains("hidden")) closeChat();
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopPolling();
    } else {
      refreshConnection();
      startPolling();
    }
  });

  window.addEventListener("online", refreshConnection);
  window.addEventListener("offline", refreshConnection);

  conn = $("conn");
  connLabel = $("conn-label");
  refreshConnection();
  startPolling();

  /* Last statement on purpose. The inline safety net in index.html reads
     this to decide whether the module graph actually arrived — so it may
     only be stamped once every listener above is attached. Partial boot +
     fallback binding is harmless (opening the studio twice or scrolling
     twice is idempotent); a boot that never finished must not be mistaken
     for a finished one. */
  document.documentElement.dataset.appReady = "1";
}

/* ---------------------------------------------------------- start here -- */

if (document.readyState === "loading") {
  // The parser is still running: nothing below may look elements up yet.
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  // Deferred module scripts land here (readyState is "interactive" by then),
  // which is the normal case.
  boot();
}
