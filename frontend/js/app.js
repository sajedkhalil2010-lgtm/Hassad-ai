/* =========================================================================
   Entry point.

   Loaded as a module from the end of <body>, so every element it touches
   already exists by the time this runs.

   All requests are same-origin: the backend serves this page at '/', so
   there is no base-URL override to configure and no CORS to reason about.
   ========================================================================= */

import { ping } from "./api.js";
import { initAuth } from "./auth.js";
import { initChat } from "./chat.js";
import { initDiagnose } from "./diagnose.js";
import { $ } from "./dom.js";

/* --------------------------------------------------------------- panels -- */

// Diagnosis first: the assistant asks it for context whenever a question is
// sent, so it has to exist before the chat is constructed.
const diagnose = initDiagnose();
const chat = initChat({ getContext: () => diagnose.getContext() });
initAuth();

/* ------------------------------------------------------- connection pill -- */

const conn = $("conn");
const connLabel = $("conn-label");

async function refreshConnection() {
  const up = await ping();
  conn.setAttribute("data-state", up ? "up" : "down");
  connLabel.textContent = up ? "Connected" : "Offline";
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
