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
  }

  /* ---------------------------------------------------------- preview --- */

  function accept(file) {
    if (!file) return;

    if (!file.type || !file.type.startsWith("image/")) {
      setStatus("That is not an image file. Use JPEG, PNG, WEBP, BMP or TIFF.", "err");
      return;
    }
    if (file.size > MAX_BYTES) {
      setStatus(`That image is ${bytes(file.size)}. The maximum is ${bytes(MAX_BYTES)}.`, "err");
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

    setStatus("Ready to analyze.", null);
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

    setStatus("Choose an image to begin.", null);
    setBusy(false);
  }

  /* --------------------------------------------------------- rendering -- */

  function tile(label, valueNode, severity) {
    const node = el("div", "tile");
    if (severity) node.setAttribute("data-sev", severity);
    node.append(el("div", "tile__k", label), valueNode);
    return node;
  }

  function renderSummary(report) {
    const d = report.diagnosis || {};
    const overall = (report.confidence && report.confidence.overall) ?? d.confidence ?? 0;
    const severity = SEVERITIES.includes(d.severity) ? d.severity : "moderate";
    const confidence = pct(overall);

    clear(els.summary);

    // Crop
    const crop = el("div", "tile__v", d.crop || "Unknown");
    if (d.pathogen && d.pathogen !== "-") crop.append(el("small", null, d.pathogen));
    els.summary.append(tile("Crop", crop));

    // Disease — Latin line then the Arabic name, right-aligned in Tajawal.
    const disease = el("div", "tile__v", d.disease || "Unknown");
    if (d.disease_ar) disease.append(ar("tile__ar", d.disease_ar));
    els.summary.append(tile("Diagnosis", disease, severity));

    // Severity
    const sevValue = el("div", "tile__v");
    const chip = el("span", "chip");
    chip.append(el("span", "chip__dot"), document.createTextNode(d.severity || "unknown"));
    sevValue.append(chip);
    if (typeof d.severity_pct === "number") {
      sevValue.append(el("small", "tile__num", `${pct(d.severity_pct)}% of leaf affected`));
    }
    els.summary.append(tile("Severity", sevValue, severity));

    // Confidence — the supervisor's overall figure, with a meter.
    const confValue = el("div", "tile__v tile__num", `${confidence}%`);
    const meter = el("div", "meter");
    const fill = el("div", "meter__fill");
    fill.style.width = `${confidence}%`;
    if (d.low_confidence || (report.confidence && report.confidence.provisional)) {
      fill.setAttribute("data-band", "low");
    }
    meter.append(fill);
    confValue.append(meter);
    els.summary.append(tile("Confidence", confValue, severity));

    // Image quality
    els.summary.append(tile("Image", el("div", "tile__v", d.image_quality || "usable")));
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
    renderSummary(report);
    renderSymptoms(report.diagnosis || {});
    renderGuidance(report);
    renderRaw(report);
    show(els.results, true);
  }

  /* ----------------------------------------------------------- analyze -- */

  async function analyze() {
    if (!picked || busy) return;

    setBusy(true);
    setStatus("Reading the leaf… this usually takes several seconds.", "busy");

    const form = new FormData();
    form.append("file", picked, picked.name || "leaf");

    try {
      const report = await call("/api/v1/analyze", { method: "POST", formData: form });
      lastReport = report;
      render(report);
      const ms = report.timing && report.timing.total_ms;
      setStatus(ms ? `Done in ${(ms / 1000).toFixed(1)} s.` : "Done.", "ok");
      if (onResult) onResult(report);
    } catch (err) {
      // The envelope already carries a farmer-readable sentence, so it is
      // shown verbatim rather than translated into a status code.
      setStatus(err && err.message ? err.message : "Analysis failed.", "err");
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

  return {
    /** Compact, whitelisted view of the last diagnosis, for chat context. */
    getContext() {
      const d = lastReport && lastReport.diagnosis;
      if (!d) return null;
      return {
        crop: d.crop,
        disease: d.disease,
        disease_ar: d.disease_ar,
        severity: d.severity,
        healthy: d.healthy,
      };
    },
  };
}
