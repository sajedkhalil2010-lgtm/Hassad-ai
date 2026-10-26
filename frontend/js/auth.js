/* =========================================================================
   Account dialog — sign in / create account / sign out / admin listing.

   Exposes initAuth({onChange, onGuest}) so the shell can react to a session
   appearing or disappearing without this module reaching back into it.

   The dialog has two faces: signed-out (tabs + form + guest route) and
   signed-in (identity). The sidebar profile card is the trigger, which keeps
   "where do I sign out?" in exactly one place.

   The guest route matters: diagnosis never requires an account, so the
   dialog has to offer a way past itself. Without it the login wall becomes
   a toll gate on a tool that works fine anonymously.
   ========================================================================= */

import { call, getToken, setToken } from "./api.js";
import { $ } from "./dom.js";
import { t } from "./i18n.js";

export function initAuth({ onChange, onGuest, onRegistered } = {}) {
  const els = {
    modal: $("auth-modal"),
    title: $("auth-modal-title"),
    signedOut: $("auth-signed-out"),
    signedIn: $("auth-signed-in"),
    tabLogin: $("tab-login"),
    tabRegister: $("tab-register"),
    form: $("auth-form"),
    email: $("auth-email"),
    password: $("auth-password"),
    nameField: $("name-field"),
    name: $("auth-name"),
    submit: $("auth-submit"),
    submitText: document.querySelector("#auth-submit .btn__text"),
    status: $("auth-status"),
    guest: $("auth-guest"),
    // Sidebar profile card
    who: $("auth-who"),
    meta: $("auth-meta"),
    avatar: $("auth-avatar"),
    trigger: $("account-trigger"),
    triggerLabel: $("account-label"),
    // Dialog identity face
    identityAvatar: $("auth-identity-avatar"),
    identityEmail: $("auth-identity-email"),
    identityMeta: $("auth-identity-meta"),
    adminView: $("auth-admin-view"),
    adminOut: $("admin-output"),
    logout: $("auth-logout"),
  };

  let user = null;
  let mode = "login";
  let lastFocused = null;
  /* True only for the render that follows a successful registration and only
     when something is waiting to receive it. onChange reads it so the shell
     can hold the workspace back for one step instead of opening it the
     instant the account exists. */
  let pendingSignup = false;

  /* ----------------------------------------------------------- helpers -- */

  function setStatus(text, tone) {
    els.status.textContent = text || "";
    if (tone) els.status.setAttribute("data-tone", tone);
    else els.status.removeAttribute("data-tone");
  }

  function setMode(next) {
    mode = next;
    const registering = mode === "register";

    els.tabLogin.classList.toggle("is-active", !registering);
    els.tabLogin.setAttribute("aria-selected", String(!registering));
    els.tabRegister.classList.toggle("is-active", registering);
    els.tabRegister.setAttribute("aria-selected", String(registering));

    els.nameField.classList.toggle("hidden", !registering);
    els.submitText.textContent = registering ? t("create_account") : t("sign_in");
    els.title.textContent = registering ? t("create_account") : t("sign_in");

    // Asking a password manager to save a new password is a different
    // request from filling an existing one; getting this wrong is why some
    // managers quietly skip the form.
    els.password.setAttribute("autocomplete", registering ? "new-password" : "current-password");
    els.password.value = "";
    setStatus("");
  }

  function render() {
    const signedIn = Boolean(user);

    els.signedIn.classList.toggle("hidden", !signedIn);
    els.signedOut.classList.toggle("hidden", signedIn);
    els.trigger.setAttribute("data-signed-in", String(signedIn));
    // Signing out is meaningless without a session, so the control is
    // removed rather than left to fail silently when pressed.
    els.logout.classList.toggle("hidden", !signedIn);

    if (signedIn) {
      const name = user.name || user.email;
      const role = user.is_admin ? t("role_admin") : t("role_farmer");
      const initial = (user.email || "?").trim().charAt(0);

      els.who.textContent = name;
      els.meta.textContent = role;
      els.avatar.textContent = initial;
      els.identityAvatar.textContent = initial;
      els.identityEmail.textContent = user.email;
      els.identityMeta.textContent = role;

      els.adminView.classList.toggle("hidden", !user.is_admin);
      els.adminOut.classList.add("hidden");
      els.title.textContent = t("account_t");
    } else {
      els.who.textContent = t("role_guest");
      els.meta.textContent = t("local_account");
      els.avatar.textContent = "؟";
    }

    const label = signedIn ? `Account: ${user.email}` : t("sign_in");
    els.trigger.setAttribute("aria-label", label);
    els.triggerLabel.textContent = label;

    if (onChange) onChange(user, { signup: pendingSignup });
  }

  /* -------------------------------------------------------------- open -- */

  function open() {
    lastFocused = document.activeElement;
    els.modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    // Focus the first empty field, not blindly the email box, so returning
    // users are not made to retype an address that is already known.
    (user ? els.adminView : els.email).focus();
    if (!user) setMode(mode);
  }

  function close() {
    els.modal.classList.add("hidden");
    document.body.style.overflow = "";
    setStatus("");
    if (lastFocused && typeof lastFocused.focus === "function") lastFocused.focus();
    lastFocused = null;
  }

  /** Past the dialog and into the workspace without an account. */
  function enterAsGuest() {
    close();
    if (onGuest) onGuest();
  }

  /* ------------------------------------------------------------- calls -- */

  async function submit(event) {
    event.preventDefault();

    const email = els.email.value.trim();
    const password = els.password.value;

    if (!email || !password) {
      setStatus("Enter your email and password.", "err");
      return;
    }

    els.submit.disabled = true;
    setStatus(mode === "register" ? "Creating your account…" : "Signing in…");

    const payload = { email, password };
    const name = els.name.value.trim();
    if (mode === "register" && name) payload.name = name;

    try {
      const body = await call(mode === "register" ? "/api/register" : "/api/login", {
        method: "POST",
        body: payload,
      });
      setToken(body.token);
      user = body.user;
      els.password.value = "";

      // A brand-new account stops at plan selection instead of opening the
      // workspace: `pendingSignup` marks this one transition so onChange does
      // not route to the dashboard ahead of the step meant to precede it.
      const signup = mode === "register" && Boolean(onRegistered);
      pendingSignup = signup;
      render();
      pendingSignup = false;

      close();
      if (signup) onRegistered(body.user);
    } catch (err) {
      // A duplicate registration comes back 409, which is a real conflict
      // rather than bad input — offering the sign-in route is the useful move.
      if (err && err.status === 409) {
        setStatus("An account already exists for that email. Try signing in.", "err");
        setMode("login");
        els.password.focus();
      } else {
        setStatus(err && err.message ? err.message : "Something went wrong.", "err");
      }
    } finally {
      els.submit.disabled = false;
    }
  }

  async function signOut() {
    // Always clear the local session, even if the revoke call fails: the
    // user asked to sign out, and refusing because the network blipped
    // would be the wrong answer.
    try {
      await call("/api/logout", { method: "POST" });
    } catch {
      /* ignore — token is discarded below regardless */
    }
    setToken(null);
    user = null;
    els.password.value = "";
    render();
    close();
  }

  /** Re-attach to an existing session, or fall back to the signed-out face. */
  async function restoreSession() {
    if (!getToken()) {
      user = null;
      render();
      return;
    }
    try {
      const body = await call("/api/me");
      user = body.user;
    } catch {
      // Token rejected (expired or revoked elsewhere): drop it, otherwise the
      // dialog would show a session that can never work again.
      setToken(null);
      user = null;
    }
    render();
  }

  async function showUsers() {
    els.adminOut.classList.remove("hidden");
    els.adminOut.textContent = "Loading…";
    try {
      const body = await call("/api/admin/users");
      // Built entirely with textContent: an account name is user input, and
      // must never be parsed as markup.
      els.adminOut.textContent =
        body.users
          .map((u) =>
            [
              u.email,
              u.name || null,
              u.is_admin ? "admin" : null,
              `created ${String(u.created_at).slice(0, 10)}`,
              `last login ${u.last_login_at ? String(u.last_login_at).slice(0, 16).replace("T", " ") : "never"}`,
            ]
              .filter(Boolean)
              .join("  ·  "),
          )
          .join("\n") || "No accounts yet.";
    } catch (err) {
      els.adminOut.textContent = err && err.message ? err.message : "Could not load the list.";
    }
  }

  /* ------------------------------------------------------------ wiring -- */

  els.trigger.addEventListener("click", open);
  els.tabLogin.addEventListener("click", () => setMode("login"));
  els.tabRegister.addEventListener("click", () => setMode("register"));
  els.form.addEventListener("submit", submit);
  els.adminView.addEventListener("click", showUsers);
  els.guest.addEventListener("click", enterAsGuest);
  $("auth-logout").addEventListener("click", signOut);

  els.modal.addEventListener("click", (event) => {
    if (event.target.closest("[data-close]")) close();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || els.modal.classList.contains("hidden")) return;
    close();
  });

  // Role labels are translated, so repaint them when the language flips.
  document.addEventListener("hassad:lang", () => {
    setMode(mode);
    render();
  });

  // Keep tabbing inside the dialog while it is modal — without this, focus
  // walks off to the page behind the backdrop and the user is stranded.
  els.modal.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const focusables = [...els.modal.querySelectorAll(
      'button:not([disabled]):not(.hidden), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
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

  render();
  restoreSession();

  return {
    getUser: () => user,
    open,
    close,
    enterAsGuest,
    refresh: restoreSession,
  };
}
