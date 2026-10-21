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
