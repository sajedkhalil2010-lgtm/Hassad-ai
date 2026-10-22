/* =========================================================================
   Diagnostic workspace — drag-and-drop, local preview, and the results grid.

   The structured grid is the product here; the raw diagnostic / advisory /
   supervisor payloads stay one disclosure away for anyone who needs to see
   exactly what the backend returned.

   Exposes initDiagnose({onResult}) so the assistant panel can answer in the
   context of the plant that was actually analysed, rather than generically.
   ========================================================================= */

import { call } from "./api.js";
import { $, ar, bytes, clear, el, pct, show } from "./dom.js";
import { addRecord } from "./history.js";
import { currentLang, t } from "./i18n.js";

/** Mirrors the backend's default max_image_bytes (8 MB). The server is
 *  authoritative — this guard just avoids a pointless upload. */
const MAX_BYTES = 8 * 1024 * 1024;

const SEVERITIES = ["none", "low", "moderate", "high", "critical"];

export function initDiagnose({ onResult } = {}) {
  const els = {
    dropzone: $("dropzone"),
    file: $("file"),
    idle: $("drop-idle"),
    previewWrap: $("preview-wrap"),
    preview: $("preview"),
    previewName: $("preview-name"),
    previewSize: $("preview-size"),
    clearImage: $("clear-image"),
    analyze: $("analyze"),
    analyzeSpin: document.querySelector("#analyze .btn__spin"),
    analyzeText: document.querySelector("#analyze .btn__text"),
    status: $("status"),
    results: $("results"),
    summary: $("summary"),
    ringConfidence: $("ring-confidence"),
    ringSeverity: $("ring-severity"),
    confNum: $("conf-num"),
    sevNum: $("sev-num"),
    badges: $("badges"),
    verdictCrop: $("verdict-crop"),
    verdictAr: $("verdict-ar"),
    symptoms: $("symptoms"),
    advisoryBody: $("advisory-body"),
    diagnosis: $("diagnosis"),
    advisory: $("advisory"),
    supervisor: $("supervisor"),
  };

  /** @type {File|null} */
  let picked = null;
  /** @type {string|null} Object URL for the current preview, revoked on swap. */
  let previewUrl = null;
  let busy = false;
  /** @type {object|null} Last accepted supervisor report. */
  let lastReport = null;

  /* --------------------------------------------------------- status ----- */

  function setStatus(text, tone) {
    els.status.textContent = text;
    if (tone) els.status.setAttribute("data-tone", tone);
    else els.status.removeAttribute("data-tone");
  }

  function setBusy(next) {
    busy = next;
    els.analyze.disabled = next || !picked;
    show(els.analyzeSpin, next);
    els.analyzeText.textContent = next ? "Analyzing…" : "Analyze";
    // Drives the wave and the sweeping highlight over the preview.
    els.dropzone.classList.toggle("is-uploading", next);
  }

  /* ---------------------------------------------------------- preview --- */

  function accept(file) {
    if (!file) return;

    if (!file.type || !file.type.startsWith("image/")) {
      setStatus(t("st_notimg"), "err");
      return;
    }
    if (file.size > MAX_BYTES) {
      setStatus(
        t("st_big").replace("{size}", bytes(file.size)).replace("{max}", bytes(MAX_BYTES)),
        "err",
      );
      return;
    }

    picked = file;

    // Swap the URL only after we know the file is acceptable, so a rejected
    // drop cannot leave a stale preview on screen.
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);

    els.preview.src = previewUrl;
    els.previewName.textContent = file.name || "image";
    els.previewSize.textContent = bytes(file.size);

    show(els.idle, false);
    show(els.previewWrap, true);
    els.dropzone.classList.add("has-image");

    // A verdict already on screen describes the PREVIOUS leaf. Leaving it
    // fully lit while a new photo sits in the preview invites the farmer to
    // read the old diagnosis as the new one, so it is dimmed and the status
    // line says plainly that it is stale.
    if (!els.results.classList.contains("hidden")) {
      els.summary.classList.add("is-stale");
      setStatus(t("st_stale"), "warn");
    } else {
      setStatus(t("st_ready"), null);
    }
    setBusy(false);
    els.analyze.focus({ preventScroll: true });
  }

  function discard() {
    picked = null;
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }
    els.preview.removeAttribute("src");
    els.file.value = "";

    show(els.previewWrap, false);
    show(els.idle, true);
    els.dropzone.classList.remove("has-image");

    setStatus(t("st_pick"), null);
    setBusy(false);
  }

  /* --------------------------------------------------------- rendering -- */

  function badge(text, kind, severity) {
    const node = el("span", kind);
    node.append(el("span", "badge__dot"), document.createTextNode(text));
    if (severity) node.setAttribute("data-sev", severity);
    return node;
  }

  /** The verdict block: two gauges, the badge row, and the two prose lines. */
  function renderVerdict(report) {
    const d = report.diagnosis || {};
    const overall = (report.confidence && report.confidence.overall) ?? d.confidence ?? 0;
    const confidence = pct(overall);
    const provisional = Boolean(
      d.low_confidence || (report.confidence && report.confidence.provisional),
    );
    const severity = SEVERITIES.includes(d.severity) ? d.severity : "moderate";
    const severityPct = typeof d.severity_pct === "number" ? pct(d.severity_pct) : 0;

    // A CSS transition only fires when the value CHANGES, so a second
    // analysis landing on the same figure would leave a dead, static ring.
    // Clearing both gauges first guarantees the sweep runs every time.
    els.ringConfidence.style.setProperty("--p", "0");
    els.ringSeverity.style.setProperty("--p", "0");
    requestAnimationFrame(() => {
      els.ringConfidence.style.setProperty("--p", String(confidence));
      els.ringSeverity.style.setProperty("--p", String(severityPct));
    });

    // Tone is carried separately from the value, so an amber read is
    // visible without altering the figure it describes.
    if (provisional) els.ringConfidence.setAttribute("data-tone", "low");
    else els.ringConfidence.removeAttribute("data-tone");
    els.confNum.textContent = String(confidence);

    els.ringSeverity.setAttribute("data-sev", severity);
    els.sevNum.textContent = String(severityPct);

    clear(els.badges);
    if (d.crop) els.badges.append(badge(d.crop, "badge badge--crop"));
    // A healthy leaf still needs its own read: green, not the neutral disease
    // styling, so "Healthy Plant" lands as good news rather than a diagnosis.
    if (d.disease) {
      els.badges.append(
        badge(d.disease, d.status === "healthy" ? "badge badge--healthy" : "badge badge--dis"),
      );
    }
    els.badges.append(badge(severity, "badge badge--sev", severity));
    if (report.status) els.badges.append(badge(report.status, "badge badge--status"));
    // Worth showing only when it explains something: a poor photo behind a
    // low-confidence read is exactly what the farmer needs told.
    if (d.image_quality && !["usable", "good"].includes(d.image_quality)) {
      els.badges.append(badge(`Image: ${d.image_quality}`, "badge"));
    }

    const cropLine = d.crop
      ? (d.pathogen && d.pathogen !== "-" ? `${d.crop} · ${d.pathogen}` : d.crop)
      : "";
    els.verdictCrop.textContent = cropLine;
    show(els.verdictCrop, Boolean(cropLine));

    els.verdictAr.textContent = d.disease_ar || "";
    show(els.verdictAr, Boolean(d.disease_ar));

    // This verdict now describes the leaf currently in the preview.
    els.summary.classList.remove("is-stale");
  }

  function renderSymptoms(diagnosis) {
    clear(els.symptoms);
    const list = Array.isArray(diagnosis.symptoms) ? diagnosis.symptoms : [];
    if (!list.length) {
      els.symptoms.append(el("li", null, "No symptoms were reported."));
      return;
    }
    list.forEach((text) => els.symptoms.append(el("li", null, text)));
  }

  function note(label, text) {
    const node = el("div", "note");
    node.append(el("b", null, label), document.createTextNode(text));
    return node;
  }

  function renderGuidance(report) {
    const a = report.advisory || {};
    const d = report.diagnosis || {};
    const meta = a.meta || {};

    clear(els.advisoryBody);

    const head = el("div", "guide__head");
    head.append(el("div", "guide__title", a.title || "Advisory"));
    if (a.title_ar) head.append(ar("guide__title-ar", a.title_ar));

    const tags = el("div", "guide__meta");
    if (a.region) tags.append(el("span", "tag", a.region));
    if (meta.source === "local_kb") {
      tags.append(el("span", "tag tag--kb", "Local knowledge base"));
    } else if (meta.source === "groq") {
      tags.append(el("span", "tag", "AI advisory"));
    }
    if (a.kind) tags.append(el("span", "tag", a.kind));
    head.append(tags);
    els.advisoryBody.append(head);

    // A degraded advisory is still useful, but the farmer should know it
    // came from the offline table rather than the model.
    if (meta.source === "local_kb") {
      els.advisoryBody.append(
        note(
          "Answered offline — ",
          meta.fallback_reason
            ? `the live advisory was unavailable (${meta.fallback_reason}), so this came from the built-in Tébessa knowledge base.`
            : "the live advisory was unavailable, so this came from the built-in Tébessa knowledge base.",
        ),
      );
    }

    if (report.status === "provisional" && d.confidence_basis) {
      els.advisoryBody.append(
        note("Provisional — ", `${d.confidence_basis} Confirm before treating.`),
      );
    }

    const actions = Array.isArray(a.actions) ? a.actions : [];
    if (actions.length) {
      const steps = el("ol", "steps");
      actions.forEach((text) => steps.append(el("li", null, text)));
      els.advisoryBody.append(steps);
    }

    const treatments = Array.isArray(a.treatments) ? a.treatments : [];
    if (treatments.length) {
      const list = el("ul", "ticks");
      treatments.forEach((t) => {
        if (!t || !t.detail) return;
        const li = el("li");
        if (t.type) li.append(el("b", null, `${t.type} — `));
        li.append(document.createTextNode(t.detail));
        list.append(li);
      });
      if (list.childElementCount) els.advisoryBody.append(list);
    }

    const irrig = a.irrigation_schedule || {};
    const irrigParts = [irrig.schedule, irrig.target, irrig.note].filter(Boolean);
    if (irrigParts.length) {
      els.advisoryBody.append(note("Irrigation — ", irrigParts.join(" · ")));
    }

    const prevention = Array.isArray(a.prevention) ? a.prevention : [];
    if (prevention.length) {
      const list = el("ul", "ticks");
      prevention.forEach((text) => list.append(el("li", null, text)));
      els.advisoryBody.append(list);
    }

    if (a.disclaimer) {
      els.advisoryBody.append(note("Before you act — ", a.disclaimer));
    }
  }

  function renderRaw(report) {
    // Pretty-printed for humans; the grid above is the authoritative view.
    const json = (value) => JSON.stringify(value ?? {}, null, 2);
    els.diagnosis.textContent = json(report.diagnosis);
    els.advisory.textContent = json(report.advisory);
    els.supervisor.textContent = JSON.stringify(report, null, 2);
  }

  function render(report) {
    renderVerdict(report);
    renderSymptoms(report.diagnosis || {});
    renderGuidance(report);
    renderRaw(report);
    show(els.results, true);
  }

  /* ----------------------------------------------------------- analyze -- */

  async function analyze() {
    if (!picked || busy) return;

    setBusy(true);
    setStatus(t("st_analyzing"), "busy");

    const form = new FormData();
    form.append("file", picked, picked.name || "leaf");
    // The API answers in the interface language. Without this it defaults to
    // English, and an Arabic farmer would get an English treatment plan.
    form.append("language", currentLang());

    try {
      const report = await call("/api/v1/analyze", { method: "POST", formData: form });
      lastReport = report;
      render(report);
      // A compact record lands in localStorage so the dashboard's metrics and
      // recent list show real figures. Never the image, never the payload.
      addRecord(report);
      const ms = report.timing && report.timing.total_ms;
      setStatus(ms ? `${t("st_done")} (${(ms / 1000).toFixed(1)} s)` : t("st_done"), "ok");
      if (onResult) onResult(report);
    } catch (err) {
      // The envelope already carries a farmer-readable sentence, so it is
      // shown verbatim rather than translated into a status code.
      setStatus(err && err.message ? err.message : t("st_error"), "err");
    } finally {
      setBusy(false);
    }
  }

  /* ------------------------------------------------------------ wiring -- */

  // The dropzone is the visible control; the real input is transparent and
  // sits on top of it. Removing it from the tab order stops focus landing on
  // an invisible element — keyboard users go through the dropzone instead.
  els.file.tabIndex = -1;

  els.dropzone.addEventListener("click", (event) => {
    // The file input covers the zone, so clicks already reach it; this only
    // matters if something inside swallows one.
    if (event.target !== els.file) els.file.click();
  });

  els.dropzone.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    els.file.click();
  });

  els.file.addEventListener("change", () => {
    const [file] = els.file.files || [];
    if (file) accept(file);
  });

  ["dragenter", "dragover"].forEach((type) => {
    els.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      els.dropzone.classList.add("is-over");
    });
  });

  ["dragleave", "dragend"].forEach((type) => {
    els.dropzone.addEventListener(type, () => els.dropzone.classList.remove("is-over"));
  });

  els.dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    els.dropzone.classList.remove("is-over");
    const [file] = (event.dataTransfer && event.dataTransfer.files) || [];
    if (file) accept(file);
  });

  // Dropping a file anywhere else in the window would navigate away from the
  // app and lose the session; block that outright.
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());

  els.clearImage.addEventListener("click", (event) => {
    event.stopPropagation();
    discard();
  });

  els.analyze.addEventListener("click", analyze);

  // A language flip re-reads the verdict in the new script rather than
  // leaving it stranded in whichever language produced it.
  document.addEventListener("hassad:lang", () => {
    if (lastReport) renderVerdict(lastReport);
    if (!picked) setStatus(t("st_pick"), null);
  });

  return {
    /** Put the farmer back at the upload control — used by "New diagnosis". */
    focusDropzone() {
      els.dropzone.focus({ preventScroll: false });
    },

    /** Compact, whitelisted view of the last diagnosis, for chat context. */
    getContext() {
      const d = lastReport && lastReport.diagnosis;
      if (!d) return null;
      return {
        crop: d.crop,
        disease: d.disease,
        disease_ar: d.disease_ar,
        severity: d.severity,
        status: d.status,
        healthy: d.healthy,
      };
    },
  };
}
