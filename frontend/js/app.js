/* =========================================================================
   Entry point — the shell that holds the two screens together.

   Loaded as a module from the end of <body>, so every element it touches
   already exists by the time this runs. All requests are same-origin: the
   backend serves this page at '/', so there is no base-URL override to
   configure and no CORS to reason about.

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

const body = document.body;

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
  $("landing").classList.add("hidden");
  $("app").classList.remove("hidden");
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

const chatModal = $("chat-modal");

function openChat() {
  chatModal.classList.remove("hidden");
  $("chat-input")?.focus();
}

function closeChat() {
  chatModal.classList.add("hidden");
}

/* -------------------------------------------------------------- wiring -- */

// Diagnosis first: the assistant asks it for context whenever a question is
// sent, so it has to exist before the chat is constructed.
const diagnose = initDiagnose();
initChat({ getContext: () => diagnose.getContext() });
initHistory();
initI18n();

const auth = initAuth({
  onChange(user) {
    const name = $("welcome-name");
    if (name) name.textContent = user ? (user.name || user.email) : "👤";
    // Any real session moves the farmer off the landing page and into work.
    if (user) enterDashboard();
  },
  // Diagnosis works without an account, so the dialog has to let people
  // past it — otherwise a tool that runs anonymously would look paywalled.
  onGuest: () => enterDashboard(),
});

/* Account entry points on the landing page. */
for (const id of ["landing-login", "landing-signup"]) {
  $(id)?.addEventListener("click", () => auth.open());
}

/* The primary CTA says "start diagnosing now", and diagnosis needs no
   account — so it goes straight to the studio and puts the cursor in the
   dropzone instead of parking a sign-in dialog between a farmer and the
   tool. Anyone who does want a session still has the two links above; this
   is the same route "New diagnosis" takes in the workspace. */
$("hero-start")?.addEventListener("click", () => {
  enterDashboard("studio");
  diagnose.focusDropzone();
});

/* Theme toggle — landing only. The workspace has no light variant, and this
   control never reaches into it: the rules it flips are scoped to .landing. */
const themeToggle = $("theme-toggle");
themeToggle?.addEventListener("click", () => {
  const next = body.dataset.theme === "light" ? "dark" : "light";
  body.dataset.theme = next;
  themeToggle.setAttribute("aria-pressed", String(next === "light"));
});

/* Panel routing: sidebar items, the quick-action cards and "see all". */
for (const node of $all("[data-panel-target]")) {
  node.addEventListener("click", () => enterDashboard(node.dataset.panelTarget));
}

/* Anything that means "open the assistant" — sidebar item, quick card, the
   top-bar button. One handler so the entry points cannot drift apart. */
for (const node of $all('[data-action="assistant"]')) {
  node.addEventListener("click", openChat);
}
$("ask-assistant")?.addEventListener("click", openChat);

/* The top-bar primary action drops the farmer straight into a fresh upload. */
$("new-diagnosis")?.addEventListener("click", () => {
  enterDashboard("studio");
  diagnose.focusDropzone();
});

for (const node of $all("[data-chat-close]")) {
  node.addEventListener("click", closeChat);
}

/* Expand widens the panel rather than mounting a second layout. */
const expand = $("chat-expand");
expand?.addEventListener("click", () => {
  const open = chatModal.dataset.expanded === "true";
  chatModal.dataset.expanded = String(!open);
  expand.setAttribute("aria-pressed", String(!open));
});

/* Escape closes whichever dialog is on top, and only the one that is open. */
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!chatModal.classList.contains("hidden")) closeChat();
});

/* ----------------------------------------------------- connection pill -- */

const conn = $("conn");
const connLabel = $("conn-label");

async function refreshConnection() {
  const up = await ping();
  conn.setAttribute("data-state", up ? "up" : "down");
  connLabel.textContent = up ? "Server online" : "Offline";
  conn.title = up
    ? "Backend reachable"
    : "Backend unreachable. Start it from the project folder with start_stable.bat";
}

// Poll slowly, and stop entirely while the tab is hidden — a background tab
// should not be keeping a request loop alive on a phone in a field.
let poller = null;

function startPolling() {
  if (poller) return;
  poller = setInterval(refreshConnection, 30_000);
}

function stopPolling() {
  clearInterval(poller);
  poller = null;
}

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

refreshConnection();
startPolling();
