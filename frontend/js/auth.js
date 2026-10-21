/* =========================================================================
   Account dialog — sign in / create account / sign out / admin listing.

   Exposes initAuth({onChange}) so the shell can react to a session appearing
   or disappearing without this module reaching back into it.

   The dialog has two faces: signed-out (tabs + form) and signed-in (identity
   + sign out). Clicking the header trigger opens whichever matches the
   current session, which keeps "where do I sign out?" to a single place.
   ========================================================================= */

import { call, getToken, setToken } from "./api.js";
import { $ } from "./dom.js";

export function initAuth({ onChange } = {}) {
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
    who: $("auth-who"),
    meta: $("auth-meta"),
    avatar: $("auth-avatar"),
    adminView: $("auth-admin-view"),
    adminOut: $("admin-output"),
    trigger: $("account-trigger"),
    triggerLabel: $("account-label"),
  };

  let user = null;
  let mode = "login";
  let lastFocused = null;

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
    els.submitText.textContent = registering ? "Create account" : "Sign in";
    els.title.textContent = registering ? "Create your account" : "Sign in";

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
    els.triggerLabel.textContent = signedIn ? user.email : "Sign in";
    els.trigger.setAttribute("aria-label", signedIn ? `Account: ${user.email}` : "Sign in");

    if (signedIn) {
      els.title.textContent = "Your account";
      els.who.textContent = user.email;
      els.avatar.textContent = (user.email || "?").trim().charAt(0);
      els.meta.textContent = user.is_admin ? "Administrator · local account" : "Local account";
      // Only admins ever see the listing button; it is not merely hidden by
      // CSS, so a non-admin cannot discover it in the markup either.
      els.adminView.classList.toggle("hidden", !user.is_admin);
      els.adminOut.classList.add("hidden");
    }

    if (onChange) onChange(user);
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
      render();
      close();
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
    setStatus("Signed out.", "ok");
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

  document.querySelector("#auth-logout").addEventListener("click", signOut);

  els.modal.addEventListener("click", (event) => {
    if (event.target.closest("[data-close]")) close();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || els.modal.classList.contains("hidden")) return;
    close();
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
    refresh: restoreSession,
  };
}
