/* Hassad AI — diagnostic dashboard behaviour.
 *
 * Talks to the backend on the same origin by default; append
 * ?api=http://127.0.0.1:8000 to the page URL if it is ever opened from
 * somewhere else. No framework, no build step.
 */
(function () {
  "use strict";

  var API_BASE = new URLSearchParams(location.search).get("api") || "";
  var ENDPOINT = API_BASE + "/api/v1/analyze";
  var TIMEOUT_MS = 240000;   // live runs measure ~8-10s; generous headroom

  var fileInput = document.getElementById("file");
  var btn       = document.getElementById("analyze");
  var preview   = document.getElementById("preview");
  var statusEl  = document.getElementById("status");
  var results   = document.getElementById("results");
  var summaryEl = document.getElementById("summary");

  var ticker = null;

  function setStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = cls || "";
  }

  function show(id, value) {
    document.getElementById(id).textContent =
      JSON.stringify(value === undefined ? null : value, null, 2);
  }

  function kv(label, value) {
    var wrap = document.createElement("div");
    var k = document.createElement("div"); k.className = "k"; k.textContent = label;
    var v = document.createElement("div"); v.className = "v";
    v.textContent = (value === undefined || value === null || value === "") ? "—" : String(value);
    wrap.appendChild(k); wrap.appendChild(v);
    return wrap;
  }

  // --- file selection ------------------------------------------------------
  fileInput.addEventListener("change", function () {
    var f = fileInput.files && fileInput.files[0];
    btn.disabled = !f;
    results.classList.add("hidden");
    if (!f) { preview.classList.add("hidden"); setStatus("Choose an image to begin."); return; }

    setStatus("Selected: " + f.name + " (" + (f.size / 1024).toFixed(1) + " KB)");

    var url = URL.createObjectURL(f);
    preview.src = url;
    preview.classList.remove("hidden");
    preview.onload = function () { URL.revokeObjectURL(url); };
  });

  // --- analyze -------------------------------------------------------------
  btn.addEventListener("click", function () {
    var f = fileInput.files && fileInput.files[0];
    if (!f) return;

    btn.disabled = true;
    fileInput.disabled = true;
    results.classList.add("hidden");

    var started = Date.now();
    setStatus("Analyzing… 0s (usually ~8-10s)");

    ticker = setInterval(function () {
      var s = Math.round((Date.now() - started) / 1000);
      setStatus("Analyzing… " + s + "s (usually ~8-10s)");
    }, 1000);

    var form = new FormData();
    form.append("file", f, f.name);

    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, TIMEOUT_MS);

    fetch(ENDPOINT, { method: "POST", body: form, signal: controller.signal })
      .then(function (res) {
        return res.json().catch(function () {
          throw new Error("Backend returned a non-JSON response (HTTP " + res.status + ").");
        }).then(function (body) { return { status: res.status, body: body }; });
      })
      .then(function (r) { render(r.status, r.body); })
      .catch(function (err) {
        var msg = (err && err.name === "AbortError")
          ? "Request timed out after " + Math.round(TIMEOUT_MS / 1000) + "s."
          : (err && err.message) ? err.message : String(err);
        setStatus("Failed: " + msg + "  — is the backend running?  Try:  start_stable.bat", "err");
      })
      .then(function () {
        clearTimeout(timer);
        if (ticker) { clearInterval(ticker); ticker = null; }
        btn.disabled = false;
        fileInput.disabled = false;
      });
  });

  // --- render --------------------------------------------------------------
  function render(status, body) {
    body = body || {};
    var ok = (status === 200 && body.ok !== false);

    if (!ok) {
      var e = body.error || {};
      setStatus(
        "HTTP " + status + " — " + (e.kind ? "[" + e.kind + "] " : "") +
        (e.message || body.status || "Request failed."),
        "err"
      );
      // Still show whatever the backend returned so it can be inspected.
      results.classList.remove("hidden");
      summaryEl.innerHTML = "";
      show("diagnosis", {});
      show("advisory", {});
      show("supervisor", body);
      return;
    }

    var dg = body.diagnosis || {};
    var ad = body.advisory || {};

    summaryEl.innerHTML = "";
    summaryEl.appendChild(kv("Status", body.status));
    summaryEl.appendChild(kv("Disease", dg.disease));
    summaryEl.appendChild(kv("Disease (AR)", dg.disease_ar));
    summaryEl.appendChild(kv("Crop", dg.crop));
    summaryEl.appendChild(kv("Severity", dg.severity + (dg.severity_pct != null ? " (" + Math.round(dg.severity_pct * 100) + "%)" : "")));
    summaryEl.appendChild(kv("Confidence", dg.confidence != null ? Number(dg.confidence).toFixed(2) : null));
    summaryEl.appendChild(kv("Advisory", ad.title));
    summaryEl.appendChild(kv("Advisory source", ad.meta && ad.meta.source));
    summaryEl.appendChild(kv("Total time", body.timing && body.timing.total_ms + " ms"));

    show("diagnosis", dg);
    show("advisory", ad);
    show("supervisor", body);

    results.classList.remove("hidden");
    setStatus("Done — HTTP " + status + ".", "ok");
  }
})();

/* --- account panel ------------------------------------------------------
 *
 * Auth runs ALONGSIDE the analysis endpoints: the page still analyzes an
 * image without a token, so adding an account can never lock the farmer out
 * of the tool. The token lives in localStorage so a refresh does not sign
 * them back out.
 *
 * Every piece of user-supplied text here is written with textContent —
 * never innerHTML — so a name containing markup cannot inject into the page.
 */
(function () {
  "use strict";

  var API_BASE = new URLSearchParams(location.search).get("api") || "";
  var TOKEN_KEY = "hassad.token";

  var signedOut = document.getElementById("auth-signed-out");
  var signedIn  = document.getElementById("auth-signed-in");
  var tabLogin  = document.getElementById("tab-login");
  var tabReg    = document.getElementById("tab-register");
  var emailEl   = document.getElementById("auth-email");
  var passEl    = document.getElementById("auth-password");
  var nameEl    = document.getElementById("auth-name");
  var nameField = document.getElementById("name-field");
  var submitEl  = document.getElementById("auth-submit");
  var statusEl  = document.getElementById("auth-status");
  var whoEl     = document.getElementById("auth-who");
  var logoutEl  = document.getElementById("auth-logout");
  var adminWrap = document.getElementById("auth-admin");
  var adminBtn  = document.getElementById("auth-admin-view");
  var adminOut  = document.getElementById("admin-output");

  var mode = "login";

  function getToken() {
    try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
  }
  function saveToken(value) {
    try {
      if (value) localStorage.setItem(TOKEN_KEY, value);
      else localStorage.removeItem(TOKEN_KEY);
    } catch (e) { /* storage disabled: auth still works for this page load */ }
  }

  function setStatus(text, kind) {
    statusEl.textContent = text || "";
    statusEl.className = kind || "";
  }

  function call(path, method, payload) {
    var headers = {};
    if (payload !== undefined) headers["Content-Type"] = "application/json";
    var token = getToken();
    if (token) headers["Authorization"] = "Bearer " + token;

    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 20000);

    return fetch(API_BASE + path, {
      method: method,
      headers: headers,
      body: payload === undefined ? undefined : JSON.stringify(payload),
      signal: controller.signal
    }).then(function (res) {
      clearTimeout(timer);
      return res.json()
        .catch(function () { return {}; })
        .then(function (body) { return { status: res.status, body: body }; });
    }, function (err) {
      clearTimeout(timer);
      throw err;
    });
  }

  function renderSignedIn(user) {
    whoEl.textContent = user.email + (user.name ? " (" + user.name + ")" : "");
    adminWrap.classList.toggle("hidden", !user.is_admin);
    adminOut.classList.add("hidden");
    signedIn.classList.remove("hidden");
    signedOut.classList.add("hidden");
  }

  function renderSignedOut() {
    signedIn.classList.add("hidden");
    signedOut.classList.remove("hidden");
    adminOut.classList.add("hidden");
  }

  function setMode(next) {
    mode = next;
    var registering = mode === "register";
    tabLogin.classList.toggle("active", !registering);
    tabLogin.setAttribute("aria-selected", String(!registering));
    tabReg.classList.toggle("active", registering);
    tabReg.setAttribute("aria-selected", String(registering));
    nameField.classList.toggle("hidden", !registering);
    submitEl.textContent = registering ? "Create account" : "Sign in";
    // Asking the browser to remember a new password is different from
    // filling an existing one, and getting this wrong is why some password
    // managers silently skip the form.
    passEl.setAttribute("autocomplete", registering ? "new-password" : "current-password");
    setStatus("");
  }

  function submit() {
    var email = emailEl.value.trim();
    var password = passEl.value;
    if (!email || !password) {
      setStatus("Enter your email and password.", "err");
      return;
    }

    submitEl.disabled = true;
    setStatus(mode === "register" ? "Creating your account…" : "Signing in…");

    var payload = { email: email, password: password };
    var name = nameEl.value.trim();
    if (mode === "register" && name) payload.name = name;

    call(mode === "register" ? "/api/register" : "/api/login", "POST", payload)
      .then(function (r) {
        var ok = (r.status === 201 || r.status === 200) && r.body && r.body.ok;
        if (ok) {
          saveToken(r.body.token);
          passEl.value = "";
          renderSignedIn(r.body.user);
          setStatus("Signed in as " + r.body.user.email + ".", "ok");
          return;
        }
        var error = (r.body && r.body.error) || {};
        setStatus(error.message || ("Request failed (HTTP " + r.status + ")."), "err");
      })
      .catch(function (err) {
        setStatus("Could not reach the backend — " + (err && err.message ? err.message : err), "err");
      })
      .then(function () { submitEl.disabled = false; });
  }

  function restore() {
    if (!getToken()) { renderSignedOut(); return; }
    call("/api/me", "GET")
      .then(function (r) {
        if (r.status === 200 && r.body && r.body.ok) {
          renderSignedIn(r.body.user);
        } else {
          // Token rejected (expired or signed out elsewhere): clear it so
          // the form is not hidden behind a session that will never work.
          saveToken(null);
          renderSignedOut();
        }
      })
      .catch(function () {
        // Backend unreachable: keep the token and show the form. Dropping a
        // valid session over a network blip would be the worse mistake.
        renderSignedOut();
      });
  }

  function showUsers() {
    call("/api/admin/users", "GET").then(function (r) {
      adminOut.classList.remove("hidden");
      if (r.status !== 200 || !r.body || !r.body.ok) {
        var error = (r.body && r.body.error) || {};
        adminOut.textContent = error.message || ("HTTP " + r.status);
        return;
      }
      var lines = r.body.users.map(function (u) {
        return [
          u.email,
          u.name || null,
          u.is_admin ? "admin" : null,
          "created " + String(u.created_at).slice(0, 10),
          "last login " + (u.last_login_at ? String(u.last_login_at).slice(0, 16).replace("T", " ") : "never")
        ].filter(Boolean).join("  ·  ");
      });
      adminOut.textContent = lines.join("\n") || "No accounts yet.";
    }).catch(function (err) {
      adminOut.classList.remove("hidden");
      adminOut.textContent = "Could not reach the backend — " + (err && err.message ? err.message : err);
    });
  }

  tabLogin.addEventListener("click", function () { setMode("login"); });
  tabReg.addEventListener("click", function () { setMode("register"); });
  submitEl.addEventListener("click", submit);

  logoutEl.addEventListener("click", function () {
    call("/api/logout", "POST").catch(function () {}).then(function () {
      saveToken(null);
      passEl.value = "";
      renderSignedOut();
      setStatus("Signed out.", "ok");
    });
  });

  adminBtn.addEventListener("click", showUsers);

  [emailEl, passEl, nameEl].forEach(function (el) {
    el.addEventListener("keydown", function (event) {
      if (event.key === "Enter") submit();
    });
  });

  setMode("login");
  restore();
})();
