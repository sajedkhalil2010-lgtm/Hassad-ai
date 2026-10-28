/* =========================================================================
   Diagnostic workspace — drag-and-drop, local preview, and the results grid.

   The structured grid is the product here: verdict, symptoms and the
   advisory plan. There is deliberately no raw-payload view — a farmer
   reading this screen gains nothing from a JSON dump of the same data.

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

/** Longest edge the pipeline keeps anyway (``settings.max_image_side``).
 *  A phone hands over 4-12 MB at 4000px; the model never sees past this, so
 *  everything beyond it is upload time spent on pixels that get discarded. */
const MAX_UPLOAD_SIDE = 1600;

/** Quality for the client-side re-encode. Leaf discolouration and lesion
 *  edges — the two things the verdict turns on — survive this comfortably,
 *  while foliage is mostly low-frequency detail that drops away cleanly. */
const JPEG_QUALITY = 0.82;

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
  };

  /** @type {File|null} */
  let picked = null;
  /** @type {File|null} What is actually uploaded — the photo, once encoded.
   *  Starts as the original so a failed or still-running encode can never
   *  leave the request without a file. */
  let upload = null;
  /** @type {Promise<File>|null} Encoding of the current preview, awaited at
   *  submit time so a fast click cannot race it. */
  let preparing = null;
  /** @type {string|null} Object URL for the current preview, revoked on swap. */
  let previewUrl = null;
  let busy = false;
  /** @type {object|null} Last accepted supervisor report. */
  let lastReport = null;
  /** Whether the report on screen came from the demo rather than the model.
   *  It is a rendering flag, not a payload field: the API contract has no
   *  "sample" key and this module must not invent one. */
  let lastSample = false;

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
    // Keys, not literals: the idle label used to be hardcoded English, so the
    // button flipped to "Analyze" the moment an image was picked on an
    // Arabic screen — right above a status line that was translated.
    els.analyzeText.textContent = next ? t("analyze_busy") : t("analyze");
    // Drives the wave and the sweeping highlight over the preview.
    els.dropzone.classList.toggle("is-uploading", next);
  }

  /* ---------------------------------------------------------- preview --- */

  /** Encode the photo down to what the pipeline can use.
   *
   *  Never a downgrade: if any step fails, if the browser cannot decode the
   *  file, or if the re-encode comes out no smaller than what arrived, the
   *  original is returned untouched and the request behaves exactly as it
   *  did before this function existed.
   *
   *  EXIF orientation is applied by the decode, deliberately. A canvas
   *  round-trip drops the metadata tag, so a sideways photo sent raw would
   *  reach the model sideways with nothing left for the backend to
   *  transpose — which is why the orientation is requested explicitly here.
   *  An engine that does not know the option raises, and we fall back to the
   *  original rather than risk shipping a rotated leaf. */
  async function compress(file) {
    if (!self.createImageBitmap) return file;

    let bitmap;
    try {
      bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch {
      return file;
    }

    try {
      const scale = Math.min(
        1,
        MAX_UPLOAD_SIDE / Math.max(bitmap.width, bitmap.height),
      );
      const width = Math.max(1, Math.round(bitmap.width * scale));
      const height = Math.max(1, Math.round(bitmap.height * scale));

      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      if (!ctx) return file;
      ctx.drawImage(bitmap, 0, 0, width, height);

      const blob = await new Promise((resolve) => {
        canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY);
      });
      if (!blob || blob.size >= file.size) return file;

      const stem = String(file.name || "leaf").replace(/\.[^.]*$/, "") || "leaf";
      return new File([blob], `${stem}.jpg`, {
        type: "image/jpeg",
        lastModified: file.lastModified || Date.now(),
      });
    } catch {
      return file;
    } finally {
      if (bitmap && typeof bitmap.close === "function") bitmap.close();
    }
  }

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
    // Safe default: if encoding never lands, this is what goes up — the file
    // exactly as it arrived, which is what would have been sent before any of
    // this existed. Encoding can therefore only ever make the upload smaller.
    upload = file;
    preparing = compress(file).then(
      (out) => {
        // Only adopt it if this is still the photo on screen; a swap while
        // encoding ran leaves the newer file's own state alone.
        if (picked !== file) return upload;
        upload = out;
        if (upload !== file) {
          // The saving, on the number that decides how long the upload takes.
          els.previewSize.textContent = `${bytes(file.size)} → ${bytes(upload.size)}`;
        }
        return upload;
      },
      () => file,
    );

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
    upload = null;
    preparing = null;
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
    // Two separate things share the word "provisional" here, which is how the
    // name below ended up declared twice — an early SyntaxError that took the
    // whole module (and every listener behind it) down. This one is a FLAG:
    // it drives the amber tone on the confidence gauge. The probable NAME,
    // declared further down, drives the badge.
    const lowConfidence = Boolean(
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
    if (lowConfidence) els.ringConfidence.setAttribute("data-tone", "low");
    else els.ringConfidence.removeAttribute("data-tone");
    els.confNum.textContent = String(confidence);

    els.ringSeverity.setAttribute("data-sev", severity);
    els.sevNum.textContent = String(severityPct);

    // An undetermined verdict still carries the model's best HYPOTHESIS, but
    // in its own field — `disease`/`disease_ar` stay masked by design, because
    // a confident name on a low-confidence read is what sends a farmer to
    // spray a healthy crop. Showing the lead is the honest middle ground, and
    // it is only ever welded to a marker: the payload underneath still says
    // status:"uncertain" and low_confidence:true, and no treatment is
    // attached to the name.
    const provisional = d.status === "uncertain"
      ? (d.provisional_disease || d.provisional_disease_ar || "")
      : "";
    const probable = Boolean(provisional);

    clear(els.badges);
    if (d.crop) els.badges.append(badge(d.crop, "badge badge--crop"));
    // Three verdicts, three reads: green is good news, amber means "we could
    // not confirm anything", and the neutral styling stays for a real disease
    // name. Getting this wrong is what makes an uncertain scan look like a
    // diagnosis to a farmer scanning the screen quickly.
    if (d.disease) {
      const tone =
        d.status === "healthy" ? "badge badge--healthy" :
        d.status === "uncertain" ? "badge badge--uncertain" :
        "badge badge--dis";
      els.badges.append(badge(probable ? provisional : d.disease, tone));
    }
    // Sits directly beside the name so the two read as a single statement —
    // "this is a guess" — rather than as a diagnosis with its caveat printed
    // somewhere the eye never reaches.
    if (probable) els.badges.append(badge(t("badge_probable"), "badge badge--probable"));
    els.badges.append(badge(severity, "badge badge--sev", severity));
    if (report.status) els.badges.append(badge(report.status, "badge badge--status"));
    // A card produced for the demo rather than by the model says so in the
    // badge row, where the verdict is read — not in a footnote.
    if (lastSample) els.badges.append(badge(t("badge_sample"), "badge badge--sample"));
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

    // The Arabic line mirrors the badge: the probable name when there is one,
    // otherwise the masked label. Also marked in CSS, so a name cannot read as
    // confirmed even if the badge row is skimmed.
    const titleAr = probable
      ? (d.provisional_disease_ar || d.disease_ar || "")
      : (d.disease_ar || "");
    els.verdictAr.textContent = titleAr;
    els.verdictAr.classList.toggle("is-provisional", probable);
    show(els.verdictAr, Boolean(titleAr));

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

  function render(report) {
    renderVerdict(report);
    renderSymptoms(report.diagnosis || {});
    renderGuidance(report);
    show(els.results, true);
  }

  /* ----------------------------------------------------------- analyze -- */

  async function analyze() {
    if (!picked || busy) return;

    setBusy(true);
    const label = t("st_analyzing");
    setStatus(label, "busy");

    // Seconds that keep moving, rather than a label frozen on screen. An
    // "Analyzing…" that does not change for eight seconds reads as a hung
    // page; a number that climbs says the request is alive and lets the
    // farmer see it. It starts before the upload, so encoding counts too.
    const started = Date.now();
    const tick = setInterval(() => {
      const secs = ((Date.now() - started) / 1000).toFixed(1);
      setStatus(`${label} · ${secs} s`, "busy");
    }, 250);

    try {
      const form = new FormData();
      // Awaited here rather than in accept(): someone clicking Analyze the
      // instant the preview appears must not win a race against the encoder
      // and quietly lose the compression.
      if (preparing) await preparing;
      const body = upload || picked;
      form.append("file", body, body.name || "leaf");
      // The API answers in the interface language. Without this it defaults to
      // English, and an Arabic farmer would get an English treatment plan.
      form.append("language", currentLang());

      const report = await call("/api/v1/analyze", { method: "POST", formData: form });
      lastReport = report;
      // A live answer clears any SAMPLE marker left by the demo.
      lastSample = false;
      render(report);
      // A compact record lands in localStorage so the dashboard's metrics and
      // recent list show real figures. Never the image, never the payload.
      addRecord(report);
      const ms = report.timing && report.timing.total_ms;
      setStatus(ms ? `${t("st_done")} (${(ms / 1000).toFixed(1)} s)` : t("st_done"), "ok");
      if (onResult) onResult(report);
    } catch (err) {
      // The envelope already carries a farmer-readable sentence, so it is
      // shown verbatim rather than translated into a status code. Nothing is
      // rendered as a verdict here: a request that failed produces no
      // diagnosis, and inventing one would be indistinguishable from a real
      // one on screen.
      setStatus(err && err.message ? err.message : t("st_error"), "err");
    } finally {
      // Cleared before anything else can repaint: a stray tick landing after
      // the outcome would overwrite it with "Analyzing…" over a result.
      clearInterval(tick);
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

    /** Render a report the demo recorded, through the same renderer a live
     *  answer uses, so the demo cannot drift away from the product. The card
     *  it produces carries a SAMPLE badge for as long as it is on screen. */
    renderSample(report) {
      lastSample = true;
      lastReport = report;
      render(report);
      setStatus(t("st_sample"), "ok");
    },

    /** Empty the studio for a fresh take: no photo, no verdict, no status. */
    resetStudio() {
      discard();
      lastReport = null;
      lastSample = false;
      show(els.results, false);
    },

    /** Compact, whitelisted view of the last diagnosis, for chat context. */
    getContext() {
      const d = lastReport && lastReport.diagnosis;
      if (!d) return null;
      return {
        crop: d.crop,
        crop_ar: d.crop_ar,
        disease: d.disease,
        disease_ar: d.disease_ar,
        severity: d.severity,
        status: d.status,
        healthy: d.healthy,
        // Chat must not treat an undetermined scan as a diagnosis: the
        // assistant is told the cause was never confirmed.
        uncertain: Boolean(d.uncertain),
        confidence: d.confidence,
      };
    },
  };
}
