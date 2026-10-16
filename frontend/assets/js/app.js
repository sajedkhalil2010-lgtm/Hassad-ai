/* =====================================================================
   app.js — boot, hash router, auth state, page/shell lifecycle
   ===================================================================== */

import api, { refreshTokens } from "./api.js";
import { loadLang, getLang, setLang, t } from "./i18n.js";
import { icon } from "./icons.js";
import { toast } from "./ui.js";
import {
  renderLanding, bindLanding, renderWelcome, bindWelcome,
  shellHTML, bindShell, setTopbar,
  dashboardHTML, bindDashboard, renderStudio, renderHistory, openAuthModal,
} from "./views.js";
import { getProfile, hasProfile, saveProfile } from "./profile.js";
import chat from "./chat.js";

const app = document.getElementById("app");

/* ---------- state --------------------------------------------------- */
const state = {
  user: null,
  page: null, // "landing" | "welcome" | "shell"
  authPrompted: false,
};
const cleanups = { landing: [], shell: [], welcome: [] };

function runCleanups(which) {
  const list = cleanups[which] || [];
  list.forEach(function (fn) {
    try {
      fn();
    } catch (e) { /* ignore */ }
  });
  cleanups[which] = [];
}

/* ---------- theme --------------------------------------------------- */
function applyTheme() {
  let th = null;
  try {
    th = localStorage.getItem("hassad.theme");
  } catch (e) { /* ignore */ }
  document.documentElement.dataset.theme = th === "light" ? "light" : "dark";
  paintThemeButtons();
}
function paintThemeButtons() {
  const light = document.documentElement.dataset.theme === "light";
  document.querySelectorAll('[data-act="theme"]').forEach(function (b) {
    b.innerHTML = icon(light ? "moon" : "sun");
  });
}

/* ---------- routing ------------------------------------------------- */
const PROTECTED = { app: 1, studio: 1, history: 1 };

function currentRoute() {
  const h = (location.hash || "").replace(/^#\/?/, "");
  if (h === "welcome") return "welcome";
  if (PROTECTED[h]) return h;
  return "landing";
}

function go(route) {
  const h = route === "landing" ? "#/" : "#/" + route;
  if (location.hash === h) render();
  else location.hash = h;
}

/* ---------- context handed to views/chat ---------------------------- */
const ctx = {
  get user() {
    return state.user;
  },
  go: go,
  start: function () {
    /* the local profile (or a real session) is the gateway to the app */
    if (state.user || hasProfile()) go("app");
    else go("welcome");
  },
  openAuth: openAuth,
  openChat: function (q) {
    chat.openChat(q);
  },
  logout: logout,
  /* Onboarding submit: persist the local profile, then — best effort —
     silently promote it to a full backend session. Any failure keeps the
     app running in local (guest) mode; the UI never blocks or toasts. */
  enterFromOnboarding: function (profile) {
    saveProfile(profile);
    const email = "guest." + Math.random().toString(36).slice(2, 10) + "@hassad.app";
    const pass = "Garden" + Math.random().toString(36).slice(2, 12) + "9a";
    api
      .register({ email: email, password: pass, full_name: profile.name, role: "farmer", locale: getLang() })
      .then(function () {
        return api.login(email, pass);
      })
      .then(function (tok) {
        api.setTokens(tok);
        return api.me();
      })
      .then(function (user) {
        state.user = user;
        state.authPrompted = false;
        forceRender();
      })
      .catch(function () {
        /* offline / backend down — local guest mode continues silently */
      });
    go("app");
  },
  toggleLang: function () {
    setLang(getLang() === "ar" ? "en" : "ar");
    forceRender();
  },
  toggleTheme: function () {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("hassad.theme", next);
    } catch (e) { /* ignore */ }
    paintThemeButtons();
  },
  onLeave: function (fn) {
    const bucket = cleanups[state.page] || cleanups.landing;
    bucket.push(fn);
  },
  onAuthed: function (user) {
    state.user = user;
    state.authPrompted = false;
    go("app");
  },
};

function openAuth(mode) {
  openAuthModal(ctx, mode);
}

function logout() {
  api.clearTokens();
  state.user = null;
  chat.reset();
  chat.setChatVisible(false);
  go("landing");
  toast(t("nav.logout"), "info");
}

/* ---------- page rendering ------------------------------------------ */
function guestUser() {
  const profile = getProfile();
  return { full_name: (profile && profile.name) || "—", role: "farmer", email: "" };
}

function render() {
  const route = currentRoute();

  if (route === "welcome") {
    state.authPrompted = false;
    if (state.page !== "welcome") {
      runCleanups("shell");
      runCleanups("landing");
      state.page = "welcome";
      chat.setChatVisible(false);
      app.innerHTML = renderWelcome();
      bindWelcome(app, ctx);
    }
    return;
  }

  if (route === "landing") {
    state.authPrompted = false;
    if (state.page !== "landing") {
      runCleanups("shell");
      state.page = "landing";
      chat.setChatVisible(false);
      app.innerHTML = renderLanding();
      bindLanding(app, ctx);
    }
    return;
  }

  /* protected routes — a local onboarding profile OR a real session
     grants entry (guest mode keeps every view usable offline-first) */
  const profile = getProfile();
  if (!state.user && !profile) {
    if (state.page !== "landing") {
      runCleanups("shell");
      state.page = "landing";
      app.innerHTML = renderLanding();
      bindLanding(app, ctx);
    }
    chat.setChatVisible(false);
    if (!state.authPrompted) {
      state.authPrompted = true;
      openAuth("login");
    }
    return;
  }

  if (state.page !== "shell") {
    runCleanups("landing");
    runCleanups("welcome");
    state.page = "shell";
    app.innerHTML = shellHTML(state.user || guestUser(), route);
    bindShell(app, ctx);
    window.scrollTo(0, 0);
  } else {
    /* keep nav active state in sync */
    document.querySelectorAll(".nav-item[data-route]").forEach(function (b) {
      b.classList.toggle("active", b.dataset.route === route);
    });
  }
  chat.setChatVisible(true);
  renderRouteView(route);
}

function renderRouteView(route) {
  const view = document.getElementById("view");
  if (!view) return;
  route = route || currentRoute();
  if (!PROTECTED[route]) return;

  view.classList.remove("view-enter");
  void view.offsetWidth; /* restart entrance animation */
  view.classList.add("view-enter");
  view.innerHTML = "";

  if (route === "app") {
    setTopbar(view, "nav.dashboard", null);
    view.innerHTML = dashboardHTML();
    bindDashboard(view, ctx);
  } else if (route === "studio") {
    setTopbar(view, "nav.studio", "studio.sub");
    renderStudio(view, ctx);
  } else if (route === "history") {
    setTopbar(view, "nav.history", "hist.sub");
    renderHistory(view, ctx);
  }
}

/** Full rebuild after language change (all labels are re-read). */
function forceRender() {
  runCleanups(state.page || "landing");
  cleanups.landing = [];
  cleanups.shell = [];
  state.page = null;
  state.authPrompted = false;
  chat.destroy();
  app.innerHTML = "";
  render();
  chat.initChat(ctx);
}

/* ---------- session restore ----------------------------------------- */
async function restoreUser() {
  if (!api.hasTokens()) return null;
  try {
    if (api.accessExpired()) await refreshTokens();
    return await api.me();
  } catch (e) {
    return null;
  }
}

/* ---------- globals used by views ----------------------------------- */
window.__hassadOpenChat = function (q) {
  if (!state.user && !hasProfile()) {
    openAuth("login");
    return;
  }
  chat.openChat(q || "");
};
window.__hassadGo = go;
window.__hassadRenderRoute = function () {
  renderRouteView(currentRoute());
};

/* ---------- events -------------------------------------------------- */
window.addEventListener("hashchange", render);

window.addEventListener("auth:expired", function () {
  state.user = null;
  chat.setChatVisible(false);
  toast(t("err.session"), "err");
  go("landing");
  forceRender();
});

/* forward unexpected API errors to a toast (safety net) */
window.addEventListener("unhandledrejection", function (e) {
  const r = e.reason;
  if (r && r.name === "ApiError") {
    toast(r.message || t("common.error"), "err");
    e.preventDefault();
  }
});

/* ---------- boot ----------------------------------------------------- */
async function boot() {
  applyTheme();
  loadLang(); /* sets lang + dir from storage (defaults to ar/rtl) */

  state.user = await restoreUser();
  chat.initChat(ctx);

  app.hidden = false;
  /* First-run gateway: nothing stored yet → the onboarding screen IS
     the first screen; returning farmers land straight on the app. */
  if (!state.user && !hasProfile() && !(location.hash || "").replace(/^#\/?/, "")) {
    location.hash = "#/welcome";
  }
  render();

  /* dismiss splash */
  const bootEl = document.getElementById("boot");
  if (bootEl) {
    bootEl.classList.add("out");
    setTimeout(function () {
      if (bootEl.parentNode) bootEl.parentNode.removeChild(bootEl);
    }, 450);
  }
}

boot();
