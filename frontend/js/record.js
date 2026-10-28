/* =========================================================================
   Demo recorder — films the real workspace and hands back a video file.

   WHAT IS REAL HERE
   -----------------
   The recording is the browser's own tab surface: getDisplayMedia, cropped
   to #app, muxed by MediaRecorder. Nothing is redrawn on an offscreen canvas
   and no frame is synthesised, so what the file shows is what the interface
   actually did.

   The pointer is a drawing, not the OS cursor. It is a `pointer-events: none`
   layer that glides to a real control, presses it, and dispatches a real
   click. Two exceptions are deliberate and are marked where they happen:
   the upload dropzone and the Analyze button are PRESSED but not clicked,
   because one would open a file dialog and stall the take, and the other
   would wait on a live model. The upload is real — a real File is placed on
   the real input — and the report it leads to is the sample card, which
   carries a SAMPLE badge so the video never claims a model ran.

   The whole tour is on one clock with absolute cue times, so a slow frame
   cannot push the ending later and clip the outro.
   ========================================================================= */

import { $, el, show } from "./dom.js";
import { t } from "./i18n.js";

const TOTAL = 48000;              // the planned length, in milliseconds
const BITRATE = 24_000_000;       // high-bitrate screen content
const FPS = 60;

/** Tried in order; the first the browser confirms wins. MP4 first, because
 *  that is what the button promises — but a container is never renamed to
 *  match a label, so a WebM result keeps its .webm extension. */
const MIMES = [
  'video/mp4; codecs="avc1.640033"',
  'video/mp4; codecs="avc1.42E01E"',
  "video/mp4",
  'video/x-matroska; codecs="avc1.42E01E"',
  'video/webm; codecs="vp9"',
  "video/webm",
];

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const q = (selector) => document.querySelector(selector);

/* ------------------------------------------------------------- the leaf -- */

/** A plausible photograph of one infected leaf, drawn rather than shipped.
 *  The repository carries no sample imagery, and a demo that needs a file on
 *  disk is a demo that breaks the moment it is run from a clean checkout. */
function drawLeaf(ctx, w, h) {
  ctx.fillStyle = "#e9e6df";
  ctx.fillRect(0, 0, w, h);

  const wash = ctx.createRadialGradient(w * 0.5, h * 0.42, 60, w * 0.5, h * 0.5, w * 0.62);
  wash.addColorStop(0, "rgba(255,255,255,.85)");
  wash.addColorStop(1, "rgba(196,190,178,.55)");
  ctx.fillStyle = wash;
  ctx.fillRect(0, 0, w, h);

  ctx.save();
  ctx.translate(w * 0.5, h * 0.5);
  ctx.rotate(-0.34);

  // A soft contact shadow keeps the leaf from looking pasted on.
  ctx.save();
  ctx.translate(18, 26);
  ctx.filter = "blur(22px)";
  ctx.fillStyle = "rgba(40,52,32,.42)";
  ctx.beginPath();
  ctx.ellipse(0, 0, 240, 372, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  const blade = () => {
    ctx.beginPath();
    ctx.moveTo(0, -362);
    ctx.bezierCurveTo(232, -268, 268, 78, 0, 366);
    ctx.bezierCurveTo(-268, 78, -232, -268, 0, -362);
    ctx.closePath();
  };

  const green = ctx.createLinearGradient(-210, -330, 210, 340);
  green.addColorStop(0, "#4f8f3d");
  green.addColorStop(0.45, "#2f6b2a");
  green.addColorStop(1, "#173d15");
  blade();
  ctx.fillStyle = green;
  ctx.fill();

  // Midrib and side veins.
  ctx.strokeStyle = "rgba(206,232,178,.34)";
  ctx.lineWidth = 3.2;
  ctx.beginPath();
  ctx.moveTo(0, -344);
  ctx.lineTo(0, 352);
  ctx.stroke();

  ctx.lineWidth = 1.7;
  for (let i = 0; i < 9; i += 1) {
    const y = -230 + i * 62;
    const reach = 150 - Math.abs(i - 4) * 16;
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.quadraticCurveTo(side * reach * 0.6, y - 26, side * reach, y - 74);
      ctx.stroke();
    }
  }

  // Lesions: concentric rings, the signature the diagnosis is about.
  const spots = [
    { x: -118, y: -60, r: 44 },
    { x: 92, y: 108, r: 54 },
    { x: -58, y: 196, r: 34 },
    { x: 128, y: -148, r: 28 },
  ];

  for (const spot of spots) {
    const halo = ctx.createRadialGradient(spot.x, spot.y, spot.r * 0.2, spot.x, spot.y, spot.r * 1.9);
    halo.addColorStop(0, "rgba(150,150,52,.55)");
    halo.addColorStop(1, "rgba(150,150,52,0)");
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(spot.x, spot.y, spot.r * 1.9, 0, Math.PI * 2);
    ctx.fill();

    const core = ctx.createRadialGradient(spot.x, spot.y, 2, spot.x, spot.y, spot.r);
    core.addColorStop(0, "#4a3316");
    core.addColorStop(0.65, "#6b4a1e");
    core.addColorStop(1, "rgba(107,74,30,.25)");
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(spot.x, spot.y, spot.r, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = "rgba(58,40,16,.55)";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(spot.x, spot.y, spot.r * 0.62, 0, Math.PI * 2);
    ctx.stroke();
  }

  // A little sheen across the upper third.
  const sheen = ctx.createLinearGradient(-160, -320, 140, -40);
  sheen.addColorStop(0, "rgba(255,255,255,.16)");
  sheen.addColorStop(1, "rgba(255,255,255,0)");
  blade();
  ctx.fillStyle = sheen;
  ctx.fill();

  ctx.restore();
  ctx.restore();
}

function leafFile() {
  return new Promise((resolve) => {
    const canvas = document.createElement("canvas");
    canvas.width = 1440;
    canvas.height = 1080;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      resolve(null);
      return;
    }
    drawLeaf(ctx, canvas.width, canvas.height);
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          resolve(null);
          return;
        }
        resolve(new File([blob], "potato-leaf-sample.jpg", { type: "image/jpeg" }));
      },
      "image/jpeg",
      0.92,
    );
  });
}

/* ----------------------------------------------------------- sample card -- */

/** The report the tour displays. It reaches the screen through
 *  renderSample(), which badges the card SAMPLE — nothing here claims a model
 *  read this leaf. */
const SAMPLE = {
  status: "provisional",
  confidence: { overall: 0.94 },
  diagnosis: {
    crop: "Potato",
    crop_ar: "بطاطا",
    disease: "Early blight",
    disease_ar: "اللفحة المبكرة",
    pathogen: "Alternaria solani",
    status: "diseased",
    healthy: false,
    uncertain: false,
    low_confidence: false,
    severity: "moderate",
    severity_pct: 0.38,     // a float in [0,1], like the API's own field
    confidence: 0.94,
    image_quality: "good",
    confidence_basis: "Concentric lesions and a chlorotic halo on the lower leaves.",
    symptoms: [
      "Concentric brown lesions on the older leaves",
      "A narrow yellow halo around each lesion",
      "Lower foliage affected first, the crown still green",
      "Lesion margins darker than their centres",
    ],
  },
  advisory: {
    title: "Early blight — stop the spread before it reaches the tubers",
    title_ar: "اللفحة المبكرة — أوقف الانتشار قبل أن يصل إلى الدرنات",
    region: "Tébessa",
    kind: "treatment",
    actions: [
      "Remove and burn the affected lower leaves today.",
      "Apply a protectant fungicide to the remaining canopy.",
      "Keep the foliage dry: water at the base, early in the morning.",
    ],
    treatments: [
      { type: "Fungicide", detail: "Chlorothalonil or mancozeb on a 7-day interval, two sprays." },
      { type: "Resistance", detail: "Rotate away from potato and tomato for two seasons." },
    ],
    irrigation_schedule: {
      schedule: "Every 3 days",
      target: "early morning, at the base",
      note: "overhead watering spreads the spores",
    },
    prevention: [
      "Certified seed tubers only.",
      "Space rows so the canopy dries within an hour of sunrise.",
      "Clear volunteer potatoes between seasons.",
    ],
    disclaimer:
      "Confirm the diagnosis before treating a whole field; a single leaf is one reading.",
  },
};

/* --------------------------------------------------------------- cursor -- */

const cursor = { root: null, ptr: null, ring: null, spot: null, x: 0, y: 0 };

function writeCursor(x, y) {
  if (!cursor.ptr || !cursor.ring) return;
  cursor.x = x;
  cursor.y = y;
  const at = `translate3d(${x}px, ${y}px, 0)`;
  cursor.ptr.style.transform = at;
  cursor.ring.style.transform = at;
}

function mountCursor() {
  if (cursor.root) return;

  const root = el("div", "recursor");
  root.setAttribute("aria-hidden", "true");

  const spot = el("div", "recursor__spot");
  const ring = el("div", "recursor__ring");
  const ptr = el("div", "recursor__ptr");

  const art = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  art.setAttribute("viewBox", "0 0 24 24");
  art.setAttribute("aria-hidden", "true");
  art.classList.add("recursor__art");

  const shape = document.createElementNS("http://www.w3.org/2000/svg", "path");
  shape.setAttribute("d", "M5.6 3.4 18.8 12.2l-5.6 1.1 3.1 5.7-2.7 1.4-3.1-5.7-4.6 3.5Z");
  art.append(shape);
  ptr.append(art);

  root.append(spot, ring, ptr);
  document.body.append(root);

  cursor.root = root;
  cursor.spot = spot;
  cursor.ring = ring;
  cursor.ptr = ptr;

  writeCursor(Math.round(window.innerWidth * 0.52), Math.round(window.innerHeight * 0.84));
}

function unmountCursor() {
  cursor.root?.remove();
  cursor.root = null;
  cursor.ptr = null;
  cursor.ring = null;
  cursor.spot = null;
}

function ping() {
  if (!cursor.root) return;
  const node = el("span", "recursor__ping");
  node.style.left = `${cursor.x}px`;
  node.style.top = `${cursor.y}px`;
  cursor.root.append(node);
  setTimeout(() => node.remove(), 900);
}

function aim(node) {
  const box = node.getBoundingClientRect();
  return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
}

/** One transform write per frame, along a shallow arc, eased in and out.
 *  Screen recordings of a cursor that moves in a straight line at constant
 *  speed read as robotic; the arc is what makes it look handled. */
function glideTo(point, ms) {
  if (!cursor.root) return Promise.resolve();

  const from = { x: cursor.x, y: cursor.y };
  const dx = point.x - from.x;
  const dy = point.y - from.y;
  const distance = Math.hypot(dx, dy);
  const dur = ms || Math.max(480, Math.min(1000, distance * 0.55));
  const ctrl = {
    x: from.x + dx * 0.5 - dy * 0.14,
    y: from.y + dy * 0.5 + dx * 0.14,
  };

  return new Promise((resolve) => {
    const started = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - started) / dur);
      const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      const u = 1 - e;
      writeCursor(
        u * u * from.x + 2 * u * e * ctrl.x + e * e * point.x,
        u * u * from.y + 2 * u * e * ctrl.y + e * e * point.y,
      );
      if (p < 1) requestAnimationFrame(step);
      else resolve();
    };
    requestAnimationFrame(step);
  });
}

async function bringIntoView(node) {
  const box = node.getBoundingClientRect();
  if (box.top < 80 || box.bottom > window.innerHeight - 60) {
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    await wait(700);
  }
}

/** Glide, press, release. `real: false` keeps the press cosmetic — used where
 *  a genuine click would open a file dialog or call a live model. */
async function tap(node, options) {
  const opts = options || {};
  if (!node) return;

  await bringIntoView(node);
  await glideTo(aim(node), opts.ms);
  ping();

  cursor.ring.classList.add("is-down");
  if (node.classList) node.classList.add("is-pressed");
  await wait(130);
  cursor.ring.classList.remove("is-down");
  if (node.classList) node.classList.remove("is-pressed");

  if (opts.real !== false) node.click();
  await wait(opts.settle == null ? 260 : opts.settle);
}

function spotOn(node) {
  if (!node || !cursor.spot) return;
  const pad = 10;
  const box = node.getBoundingClientRect();
  cursor.spot.style.left = `${box.left - pad}px`;
  cursor.spot.style.top = `${box.top - pad}px`;
  cursor.spot.style.width = `${box.width + pad * 2}px`;
  cursor.spot.style.height = `${box.height + pad * 2}px`;
  cursor.spot.classList.add("is-on");
}

function spotOff() {
  cursor.spot?.classList.remove("is-on");
}

/* ---------------------------------------------------------------- extras -- */

function countdown(seconds) {
  return new Promise((resolve) => {
    const box = el("div", "reccount");
    const num = el("span", "reccount__num", String(seconds));
    const hint = el("span", "reccount__hint", "Recording starts in");
    box.append(num, hint);
    document.body.append(box);

    let left = seconds;
    const tick = setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(tick);
        box.remove();
        resolve();
        return;
      }
      num.textContent = String(left);
    }, 1000);
  });
}

function outro() {
  const box = el("div", "recoutro");
  box.append(
    el("p", "recoutro__brand", "Hassad AI"),
    el("p", "recoutro__line", "Crop diagnosis for every farmer."),
    el("p", "recoutro__repo", "github.com/sajedkhalil2010-lgtm/Hassad-ai"),
  );
  document.body.append(box);
}

/* ---------------------------------------------------------------- export -- */

function pickMime() {
  if (typeof MediaRecorder === "undefined") return "";
  for (const type of MIMES) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return "";
}

function extension(mime) {
  return container(mime).toLowerCase();
}

/** The container as a viewer would name it. The full MIME string, codecs and
 *  all, belongs in a debugger, not in a status line under the button. */
function container(mime) {
  if (mime.indexOf("mp4") !== -1) return "MP4";
  if (mime.indexOf("matroska") !== -1) return "MKV";
  return "WEBM";
}

function clock(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

/* --------------------------------------------------------------- module -- */

export function initRecorder({ diagnose, enterDashboard, i18n } = {}) {
  const els = {
    start: $("rec-start"),
    stop: $("rec-stop"),
    status: $("rec-status"),
    elapsed: $("rec-elapsed"),
    bar: $("rec-bar"),
    out: $("rec-out"),
    link: $("rec-download"),
    file: $("rec-file"),
    facts: $("rec-facts"),
  };

  const state = {
    busy: false,
    live: false,
    recorder: null,
    stream: null,
    chunks: [],
    url: null,
    ticker: 0,
    startedAt: 0,
    langBefore: null,
  };

  function setStatus(text, tone) {
    if (!els.status) return;
    els.status.textContent = text;
    if (tone) els.status.setAttribute("data-tone", tone);
    else els.status.removeAttribute("data-tone");
  }

  function progress(ms) {
    if (els.elapsed) els.elapsed.textContent = `${clock(ms)} / ${clock(TOTAL)}`;
    if (els.bar) els.bar.style.width = `${Math.min(100, (ms / TOTAL) * 100).toFixed(2)}%`;
  }

  function teardown() {
    state.live = false;
    clearInterval(state.ticker);
    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
    }
    state.recorder = null;
    state.busy = false;
    unmountCursor();
    spotOff();
    q(".recoutro")?.remove();
    q(".reccount")?.remove();
    show(els.stop, false);
    if (els.start) els.start.disabled = false;

    // The tour deliberately flips the language to show both directions. That
    // is the film's business, not the viewer's setting, so put back whatever
    // they had before the take started.
    if (state.langBefore && i18n && typeof i18n.setLang === "function") {
      i18n.setLang(state.langBefore);
    }
    state.langBefore = null;
  }

  function finish(recorder, size) {
    const mime = recorder.mimeType || pickMime();
    const blob = new Blob(state.chunks, { type: mime });
    state.chunks = [];

    if (!blob.size) {
      teardown();
      setStatus(t("rec_fail"), "err");
      return;
    }

    if (state.url) URL.revokeObjectURL(state.url);
    state.url = URL.createObjectURL(blob);

    const name = `Hassad_AI_Demo.${extension(mime)}`;
    const megabytes = (blob.size / (1024 * 1024)).toFixed(1);

    if (els.link) {
      els.link.href = state.url;
      els.link.download = name;
      show(els.out, true);
    }
    if (els.file) els.file.textContent = `${name} · ${megabytes} MB`;
    if (els.facts) {
      const pixels = size.w && size.h ? ` · ${size.w} × ${size.h}` : "";
      const shot = state.startedAt ? performance.now() - state.startedAt : 0;
      els.facts.textContent = `${container(mime)}${pixels} · ${clock(shot)}`;
    }

    teardown();
    setStatus(t("rec_saved"), "ok");

    // The file lands without a second action, then stays available above.
    if (els.link) els.link.click();
  }

  function abort(reason) {
    if (!state.busy) return;
    state.live = false;
    clearInterval(state.ticker);

    if (state.recorder && state.recorder.state !== "inactive") {
      state.recorder.onstop = null;
      state.recorder.onerror = null;
      try {
        state.recorder.stop();
      } catch {
        /* already inactive */
      }
    }

    state.chunks = [];
    teardown();
    progress(0);
    setStatus(reason || t("rec_cancel"), "err");
  }

  /** Stop filming now and keep what has been shot so far. This is the Stop
   *  button: an early ending is still a film. Escape discards instead. */
  function stopEarly() {
    if (!state.busy) return;
    state.live = false;
    clearInterval(state.ticker);
    spotOff();
    q(".recoutro")?.remove();

    const recorder = state.recorder;
    if (recorder && recorder.state !== "inactive") {
      try {
        recorder.stop();          // onstop -> finish() -> download
      } catch {
        /* already inactive */
      }
    }
  }

  /* ------------------------------------------------------------ tour ---- */

  async function feedLeaf() {
    const input = $("file");
    if (!input) return false;
    const file = await leafFile();
    if (!file) return false;

    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  const STEPS = [
    { at: 900, run: () => tap(q('[data-panel-target="studio"]')) },

    /* Pressed, not clicked: a real click opens the file dialog and the take
       would sit there until somebody chose a file. The upload itself is real
       — a real File is placed on the real input. */
    {
      at: 5200,
      run: async () => {
        await tap($("dropzone"), { real: false });
        await feedLeaf();
      },
    },

    /* Pressed, not clicked: a real click would call the live model. The card
       that follows carries a SAMPLE badge. */
    {
      at: 8800,
      run: async () => {
        await tap($("analyze"), { real: false });
        await wait(420);
        diagnose?.renderSample(SAMPLE);
      },
    },

    { at: 12600, run: async () => { spotOn($("summary")); } },
    { at: 16400, run: async () => { spotOn($("symptoms")?.closest(".card")); } },
    { at: 20000, run: async () => { spotOn($("advisory-body")?.closest(".card")); } },

    { at: 23200, run: () => tap(q("[data-lang-toggle]")) },
    { at: 27200, run: async () => { spotOn($("summary")); } },

    { at: 31000, run: () => tap(q('[data-panel-target="pricing"]')) },
    { at: 35000, run: async () => { spotOn(q(".plans")); } },

    { at: 39000, run: () => tap(q('[data-panel-target="studio"]')) },
    { at: 41800, run: async () => { spotOff(); outro(); } },
  ];

  async function tour() {
    const started = performance.now();
    for (const step of STEPS) {
      const delay = step.at - (performance.now() - started);
      if (delay > 0) await wait(delay);
      if (!state.live) return;
      try {
        await step.run();
      } catch (err) {
        // A missing target must not kill a take that is already recording.
        console.warn("[hassad] demo step skipped:", err);
      }
    }
  }

  /* ----------------------------------------------------------- start ---- */

  async function start() {
    if (state.busy) return;

    const mime = pickMime();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      setStatus(t("rec_fail"), "err");
      return;
    }
    if (!mime) {
      setStatus(t("rec_unsupported"), "err");
      return;
    }

    state.busy = true;
    state.chunks = [];
    if (state.url) {
      URL.revokeObjectURL(state.url);
      state.url = null;
    }
    show(els.out, false);
    if (els.start) els.start.disabled = true;
    setStatus(t("rec_wait"), "warn");

    let stream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: {
          width: { ideal: 3840 },
          height: { ideal: 2160 },
          frameRate: { ideal: FPS },
        },
        audio: false,
        preferCurrentTab: true,
        selfBrowserSurface: "include",
        surfaceSwitching: "exclude",
      });
    } catch (err) {
      state.busy = false;
      if (els.start) els.start.disabled = false;
      const cancelled = err && err.name === "NotAllowedError";
      setStatus(cancelled ? t("rec_cancel") : t("rec_fail"), "err");
      return;
    }

    state.stream = stream;

    // Crop to the workspace, so the file is the app frame and not the tab
    // with whatever the browser drew around it. Where Region Capture is not
    // available the full surface is recorded; the facts line reports the
    // size that was actually captured.
    let cropped = false;
    try {
      if (window.CropTarget && typeof window.CropTarget.fromElement === "function") {
        const track = stream.getVideoTracks()[0];
        if (track && typeof track.cropTo === "function") {
          await track.cropTo(await window.CropTarget.fromElement($("app")));
          cropped = true;
        }
      }
    } catch {
      cropped = false;
    }

    const settings = stream.getVideoTracks()[0].getSettings();
    const box = $("app")?.getBoundingClientRect() || { width: 0, height: 0 };
    const ratio = window.devicePixelRatio || 1;
    const size = cropped
      ? { w: Math.round(box.width * ratio), h: Math.round(box.height * ratio) }
      : { w: settings.width || 0, h: settings.height || 0 };

    // Put the workspace in the state the film opens on, and make sure the
    // picture begins in English so the flip to Arabic reads as a switch.
    state.langBefore =
      i18n && typeof i18n.currentLang === "function" ? i18n.currentLang() : null;
    if (i18n && typeof i18n.setLang === "function" && i18n.currentLang() !== "en") {
      i18n.setLang("en");
    }
    diagnose?.resetStudio();
    enterDashboard("dashboard");
    spotOff();
    progress(0);

    await countdown(3);

    let recorder;
    try {
      recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: BITRATE });
    } catch {
      try {
        recorder = new MediaRecorder(stream, {
          mimeType: mime.split(";")[0],
          videoBitsPerSecond: BITRATE,
        });
      } catch {
        stream.getTracks().forEach((track) => track.stop());
        state.stream = null;
        state.busy = false;
        if (els.start) els.start.disabled = false;
        setStatus(t("rec_unsupported"), "err");
        return;
      }
    }

    state.recorder = recorder;
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) state.chunks.push(event.data);
    };
    recorder.onstop = () => finish(recorder, size);
    recorder.onerror = () => abort(t("rec_fail"));

    show(els.stop, true);
    recorder.start(1000);
    state.startedAt = performance.now();

    // One frame of slack so the muxer has a picture before the tour begins.
    await wait(350);

    mountCursor();
    state.live = true;
    setStatus(t("rec_live"), "ok");

    const began = performance.now();
    state.ticker = setInterval(() => progress(performance.now() - began), 200);

    await tour();

    if (!state.live) return;               // stopped by hand
    await wait(TOTAL - (performance.now() - began) + 200);
    if (!state.live) return;

    state.live = false;
    clearInterval(state.ticker);
    if (recorder.state !== "inactive") {
      try {
        recorder.stop();
      } catch {
        /* already inactive */
      }
    }
  }

  /* ------------------------------------------------------------ wiring -- */

  els.start?.addEventListener("click", start);
  els.stop?.addEventListener("click", stopEarly);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.busy) abort(t("rec_cancel"));
  });

  progress(0);
  setStatus(t("rec_ready"), null);

  return { start, stop: stopEarly };
}
