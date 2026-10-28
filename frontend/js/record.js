/* ============================================================================
 * record.js — automated three-minute demo recorder
 *
 * What this is
 * ------------
 * A self-driving capture rig. It borrows the browser's own tab surface
 * (`getDisplayMedia`), records it with `MediaRecorder`, and then drives the
 * REAL interface on a fixed 180-second clock: scroll the landing page, run a
 * genuine diagnosis on a genuine photograph, flip the language to demonstrate
 * RTL/LTR, open the subscription dialog, and land on the call to action.
 *
 * What this deliberately is NOT
 * -----------------------------
 * Nothing here synthesises a frame. There is no offscreen canvas redrawing
 * the product, no pre-baked verdict, no scripted "fake" analysis. Every pixel
 * in the export is the real application responding to real input — which is
 * the only kind of demo that survives being checked. If the model is slow the
 * video shows it being slow. If the model fails the video shows the failure.
 *
 * Two constraints no amount of code removes, so they are surfaced in the UI
 * rather than hidden:
 *   1. `MediaRecorder` captures in real time. A three-minute video takes three
 *      minutes to make. There is no fast-forward and there never will be.
 *   2. Capturing a tab is gated behind a user gesture: the browser shows its
 *      own picker and the operator has to choose this tab. That prompt is a
 *      security boundary, not a setting.
 *
 * Container: MP4 when the browser can actually mux it (Safari, and recent
 * Chromium), WebM otherwise. Probed with `isTypeSupported` and then REPORTED,
 * so nobody ends up holding a .webm that has been renamed .mp4.
 * ========================================================================== */

import { $, $all, bytes } from "./dom.js";
import { t } from "./i18n.js";

/** The sequence itself. Beats are pinned to this clock so the exported file
 *  always runs 03:00, whatever the network was doing on the day it was shot. */
const TOTAL_MS = 180_000;

/** A short hold on the opening frame before the clock starts. It gives the
 *  operator time to take their hand off the mouse, and it doubles as a clean
 *  title beat at the head of the video. */
const PREROLL_MS = 3_000;

const TICK_MS = 250;          // HUD refresh, deliberately coarse
const CHUNK_MS = 1_000;       // MediaRecorder timeslice
const BITRATE = 6_000_000;    // ~45 MB for three minutes, comfortable at 1080p
const RESULT_WAIT_MS = 45_000; // a 429 backoff can push a real scan this far
const SCROLL_OPTS = { behavior: "smooth", block: "center" };

/** Probe order matters: MP4 first so a browser that can mux it gets MP4.
 *  The extension is derived from the container rather than carried alongside
 *  it — there is one source of truth for what the file actually is. */
const MIMES = [
  "video/mp4;codecs=avc1.42E01E",
  "video/mp4",
  "video/webm;codecs=vp9",
  "video/webm;codecs=vp8",
  "video/webm",
];

/** Shown when nobody is signed in. A demo account row needs *something* in
 *  the address slot, and this one is self-evidently a placeholder rather
 *  than a plausible-looking invented customer. */
const DEMO_ACCOUNT = "demo@hassad.ai";

/* ------------------------------------------------------------- helpers -- */

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** `$` from dom.js is getElementById. Highlight targets are often a class or
 *  a descendant selector, and handing one of those to `$` returns null — no
 *  throw, no log, just a beat that silently highlights nothing. Selector
 *  lookups go through here instead. */
const q = (selector) => document.querySelector(selector);

/** camelCase -> kebab-case, so a missing-element message can name the id
 *  that is actually in the markup rather than the key it is stored under. */
const kebab = (value) => value.replace(/[A-Z]/g, (ch) => `-${ch.toLowerCase()}`);

function clock(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = String(Math.floor(total / 60)).padStart(2, "0");
  const s = String(total % 60).padStart(2, "0");
  return `${m}:${s}`;
}

/** MP4 is offered only when the browser says it can produce it. */
function pickMime() {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return null;
  return MIMES.find((mime) => MediaRecorder.isTypeSupported(mime)) || null;
}

/* --------------------------------------------------------------- module -- */

export function initRecorder(opts = {}) {
  const nav = {
    // app.js owns the two screens, so the timeline asks it to move rather
    // than reaching into the DOM and re-implementing the router.
    goLanding: typeof opts.goLanding === "function" ? opts.goLanding : () => {},
    goPanel: typeof opts.goPanel === "function" ? opts.goPanel : () => {},
    getUser: typeof opts.getUser === "function" ? opts.getUser : () => null,
    getSubscribe: typeof opts.getSubscribe === "function" ? opts.getSubscribe : () => null,
  };

  const els = {
    panel: $("rec-panel"),
    start: $("rec-start"),
    stop: $("rec-stop"),
    status: $("rec-status"),
    elapsed: $("rec-elapsed"),
    seg: $("rec-seg"),
    sample: $("rec-sample"),
    sampleName: $("rec-sample-name"),
    download: $("rec-download"),
    out: $("rec-out"),
    outSize: $("rec-out-size"),
    outFormat: $("rec-out-format"),
  };

  const absent = Object.keys(els).filter((key) => !els[key]);
  if (absent.length) {
    console.error(
      `[hassad] recorder panel is missing: ${absent.map((key) => `rec-${kebab(key)}`).join(", ")} — the demo recorder cannot start.`,
    );
    return { start: () => {}, stop: () => {} };
  }

  const state = {
    running: false,
    settled: false,
    recorder: null,
    stream: null,
    chunks: [],
    mime: null,
    url: null,
    sample: null,
    reason: "",
    startedAt: 0,
    capTimer: null,
    ticker: null,
    hl: null,
    hlTimer: null,
    poller: null,
  };

  /* ---------------------------------------------------------- element ops */

  function setStatus(text) {
    els.status.textContent = text;
  }

  function clearHl() {
    if (state.hl) state.hl.classList.remove("rec-hl");
    state.hl = null;
    if (state.hlTimer) clearTimeout(state.hlTimer);
    state.hlTimer = null;
  }

  /** One framed element at a time, so the eye always knows where to look. */
  function hl(node, ms = 4000) {
    if (!node) return;
    clearHl();
    node.classList.add("rec-hl");
    state.hl = node;
    state.hlTimer = setTimeout(clearHl, ms);
  }

  function hlAt(selector, index, ms) {
    hl($all(selector)[index] || null, ms);
  }

  function to(selector) {
    const node = document.querySelector(selector);
    if (node) node.scrollIntoView(SCROLL_OPTS);
  }

  function press(selector) {
    const node = document.querySelector(selector);
    if (node) node.click();
  }

  /* ------------------------------------------------------------ timeline */

  /** Waits until the sequence clock reaches `ms`. If a beat overran its slot
   *  this returns immediately, which is what keeps a slow diagnosis from
   *  knocking every later beat out of sync — the tail compresses instead of
   *  drifting, and the file still ends on 03:00. */
  function waitUntil(ms) {
    const target = PREROLL_MS + ms;
    return wait(Math.max(0, target - (Date.now() - state.startedAt)));
  }

  function setSeg(key) {
    els.seg.textContent = t(key);
  }

  function tick() {
    els.elapsed.textContent = `${clock(Date.now() - state.startedAt)} / ${clock(TOTAL_MS)}`;
  }

  /** The real upload path: a real File on the real <input>, dispatched as a
   *  real change event. diagnose.js sees exactly what a farmer's click gives
   *  it, so the compression, the preview and the request are all genuine. */
  function loadSample() {
    const input = $("file");
    if (!input || !state.sample) return false;
    const dt = new DataTransfer();
    dt.items.add(state.sample);
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  /** Resolves true once #results has been unhidden, false on timeout or if
   *  the run is cancelled. Polling rather than a MutationObserver because the
   *  element is checked four times a second for at most 45 seconds — there is
   *  nothing to gain from being event-driven here. */
  function waitForResults(ms) {
    const started = Date.now();
    return new Promise((resolve) => {
      state.poller = setInterval(() => {
        const node = $("results");
        const done = node && !node.classList.contains("hidden");
        const out = !state.running || done || Date.now() - started > ms;
        if (!out) return;
        clearInterval(state.poller);
        state.poller = null;
        resolve(Boolean(done));
      }, 250);
    });
  }

  function flipLanguage() {
    press("#app [data-lang-toggle]");
  }

  function openSubscription() {
    const handle = nav.getSubscribe();
    if (!handle || typeof handle.open !== "function") return;
    const user = nav.getUser();
    handle.open(user || { email: DEMO_ACCOUNT });
  }

  function closeSubscription() {
    const handle = nav.getSubscribe();
    if (handle && typeof handle.close === "function") handle.close();
  }

  /** The whole 180 seconds, as absolute times. Four segments, matching the
   *  brief: opening problem statement, live diagnosis, plans, call to action. */
  function beats() {
    return [
      /* ---- 0:00–0:45 · the problem, and the header that answers it ---- */
      { at: 0, seg: "rec_seg1", run: async () => { nav.goLanding(); to("#main"); } },
      { at: 4_500, seg: "rec_seg1", run: async () => hl(q(".hero__title"), 5000) },
      { at: 10_000, seg: "rec_seg1", run: async () => hl(q(".hero__sub"), 5000) },
      { at: 16_000, seg: "rec_seg1", run: async () => { to("#how"); await wait(2500); hl(q(".steps"), 4000); } },
      { at: 24_000, seg: "rec_seg1", run: async () => to("#modules") },
      { at: 29_000, seg: "rec_seg1", run: async () => to("#why") },
      { at: 34_000, seg: "rec_seg1", run: async () => { to("#main"); await wait(2000); hl(q("#landing .brand"), 5000); } },

      /* ---- 0:45–1:45 · a real photograph, a real diagnosis ---- */
      { at: 45_000, seg: "rec_seg2", run: async () => { nav.goPanel("studio"); await wait(1500); } },
      { at: 47_500, seg: "rec_seg2", run: async () => { to("#dropzone"); await wait(2000); } },
      { at: 50_000, seg: "rec_seg2", run: async () => { loadSample(); await wait(3500); hl($("preview-wrap"), 3000); } },
      { at: 56_000, seg: "rec_seg2", run: async () => { to("#analyze"); await wait(1500); hl($("analyze"), 2500); } },
      {
        at: 58_000,
        seg: "rec_seg2",
        run: async () => {
          // The real request. Whatever comes back is what gets filmed.
          press("#analyze");
          const ok = await waitForResults(RESULT_WAIT_MS);
          if (!ok) return; // the interface is already showing the real outcome
          await wait(4000);
          to("#results");
          await wait(2500);
          hl($("summary"), 6000);
          await wait(7000);
          hl($("badges"), 4000);
        },
      },
      { at: 88_000, seg: "rec_seg2", run: async () => { to("#results"); await wait(1500); hl($("summary"), 4000); } },
      { at: 93_000, seg: "rec_seg2", run: async () => hl($("verdict-ar"), 4000) },
      // Flip away, read the verdict in the other script, flip back. This is the
      // RTL/LTR demonstration, and it uses the app's own toggle.
      { at: 96_000, seg: "rec_seg2", run: async () => { flipLanguage(); await wait(2500); } },
      { at: 98_500, seg: "rec_seg2", run: async () => { to("#results"); await wait(2000); hl($("summary"), 5000); } },
      { at: 104_000, seg: "rec_seg2", run: async () => flipLanguage() },

      /* ---- 1:45–2:30 · the plans, and the subscription dialog ---- */
      { at: 106_000, seg: "rec_seg3", run: async () => { nav.goPanel("pricing"); await wait(1500); to(".plans"); } },
      { at: 111_000, seg: "rec_seg3", run: async () => hlAt(".plans .plan", 0, 5000) },
      { at: 117_000, seg: "rec_seg3", run: async () => hlAt(".plans .plan", 1, 5000) },
      { at: 123_000, seg: "rec_seg3", run: async () => hlAt(".plans .plan", 2, 5000) },
      { at: 129_000, seg: "rec_seg3", run: async () => hl(q(".pricing-note"), 4000) },
      { at: 134_000, seg: "rec_seg3", run: async () => { openSubscription(); await wait(3000); } },
      { at: 138_000, seg: "rec_seg3", run: async () => hl(q("#su-modal .modal__panel"), 6000) },
      // The checkout fields are filmed on purpose — they carry their own
      // "nothing is charged" banner, so the video shows the demo as a demo.
      { at: 145_000, seg: "rec_seg3", run: async () => hl($("pay-form"), 6000) },

      /* ---- 2:30–3:00 · the call to action ---- */
      { at: 152_000, seg: "rec_seg4", run: async () => { closeSubscription(); await wait(2500); } },
      { at: 155_000, seg: "rec_seg4", run: async () => { nav.goPanel("dashboard"); await wait(1500); to("#app-cta"); } },
      { at: 160_000, seg: "rec_seg4", run: async () => hl($("app-cta"), 6000) },
      { at: 167_000, seg: "rec_seg4", run: async () => hl(q("#app-cta a"), 7000) },
      { at: 175_000, seg: "rec_seg4", run: async () => hl(q("#app .brand"), 4000) },
    ];
  }

  async function runTimeline() {
    for (const beat of beats()) {
      await waitUntil(beat.at);
      if (!state.running) return;
      setSeg(beat.seg);
      try {
        await beat.run();
      } catch (err) {
        // One bad beat must not cost the operator three minutes: log it and
        // let the next one carry the video.
        console.error("[hassad] recorder beat failed:", err);
      }
    }
  }

  /* ------------------------------------------------------------ teardown */

  /** Finishes a run and renders the file. Guarded because two different
   *  events can report the same end — a normal stop() and the "ended" signal
   *  from the browser's own Stop sharing bar — and letting both through would
   *  build the blob, and fire the download, twice. */
  function onStopped() {
    if (state.settled) return;
    state.settled = true;

    if (state.poller) { clearInterval(state.poller); state.poller = null; }
    if (state.ticker) { clearInterval(state.ticker); state.ticker = null; }

    els.start.classList.remove("hidden");
    els.stop.classList.add("hidden");

    if (!state.chunks.length) {
      setStatus(t("rec_failed"));
      nav.goPanel("recorder");
      return;
    }

    const blob = new Blob(state.chunks, { type: state.mime });
    if (state.url) URL.revokeObjectURL(state.url);
    state.url = URL.createObjectURL(blob);

    const isMp4 = state.mime.indexOf("mp4") > -1;
    els.download.href = state.url;
    els.download.download = `hassad-ai-demo-3min.${isMp4 ? "mp4" : "webm"}`;
    els.outSize.textContent = bytes(blob.size);
    els.outFormat.textContent = isMp4 ? "MP4" : "WebM";
    els.out.classList.remove("hidden");

    const outcome = state.reason === "early" ? "rec_early"
      : state.reason === "error" ? "rec_failed"
      : "rec_done";
    setStatus(t(outcome));
    setSeg("rec_seg4");
    nav.goPanel("recorder");

    // Best effort: hand the file over without a second click. The button
    // stays on screen either way, because a popup blocker is entitled to say
    // no and the operator still needs a way to collect the file.
    try { els.download.click(); } catch { /* the visible button still works */ }
    els.download.focus({ preventScroll: true });
  }

  function finish(reason) {
    if (!state.running) return;
    state.running = false;
    state.reason = reason;

    if (state.capTimer) { clearTimeout(state.capTimer); state.capTimer = null; }
    if (state.poller) { clearInterval(state.poller); state.poller = null; }
    clearHl();

    setStatus(t("rec_finalising"));
    for (const track of state.stream ? state.stream.getTracks() : []) track.stop();

    if (state.recorder && state.recorder.state !== "inactive") {
      // onstop picks up from here.
      state.recorder.stop();
    } else {
      onStopped();
    }
  }

  /* ---------------------------------------------------------------- start */

  function prerequisites() {
    if (!state.sample) { setStatus(t("rec_need_image")); return false; }
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      setStatus(t("rec_no_cap"));
      return false;
    }
    return true;
  }

  async function start() {
    if (state.running || !prerequisites()) return;

    const picked = pickMime();
    if (!picked) { setStatus(t("rec_no_cap")); return; }
    state.mime = picked;
    state.chunks = [];
    state.settled = false;

    setStatus(t("rec_granting"));

    let stream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: { ideal: 30, max: 60 } },
        audio: false,
      });
    } catch {
      setStatus(t("rec_denied"));
      return;
    }

    state.stream = stream;
    state.recorder = new MediaRecorder(stream, {
      mimeType: state.mime,
      videoBitsPerSecond: BITRATE,
    });

    state.recorder.ondataavailable = (event) => {
      if (event.data && event.data.size) state.chunks.push(event.data);
    };
    state.recorder.onerror = () => finish("error");
    state.recorder.onstop = onStopped;

    // The browser's own "Stop sharing" bar has to count as a stop. Without
    // this the timeline would keep driving a surface nobody is recording.
    for (const track of stream.getTracks()) {
      track.addEventListener("ended", () => finish("early"));
    }

    state.recorder.start(CHUNK_MS);
    state.running = true;
    state.startedAt = Date.now();

    els.out.classList.add("hidden");
    els.start.classList.add("hidden");
    els.stop.classList.remove("hidden");
    tick();
    state.ticker = setInterval(tick, TICK_MS);
    state.capTimer = setTimeout(() => finish("auto"), TOTAL_MS);

    // The permission prompt may have parked the pointer mid-canvas. Get a
    // clean, still opening frame before the clock starts.
    nav.goLanding();
    window.scrollTo(0, 0); // an instant jump: a smooth one would be on camera
    setStatus(t("rec_preroll"));
    setSeg("rec_seg1");
    await wait(PREROLL_MS);
    if (!state.running) return;

    setStatus(t("rec_running"));
    runTimeline();
  }

  /* ----------------------------------------------------------------- init */

  els.sample.addEventListener("change", () => {
    const [file] = els.sample.files || [];
    state.sample = file || null;
    els.sampleName.textContent = file ? file.name : "";
    els.start.disabled = !file;
    setStatus(file ? t("rec_ready") : t("rec_need_image"));
  });

  els.start.addEventListener("click", start);
  els.stop.addEventListener("click", () => finish("early"));

  // The panel scrolls away for most of the run, so early stopping has to work
  // from anywhere. Escape only acts while a recording is live, so it does not
  // collide with the dialogs' own Escape handling.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !state.running) return;
    event.preventDefault();
    finish("early");
  });

  const supported = typeof MediaRecorder !== "undefined"
    && !!navigator.mediaDevices
    && !!navigator.mediaDevices.getDisplayMedia
    && !!pickMime();

  els.start.disabled = true; // stays disabled until a sample photograph exists
  setStatus(supported ? t("rec_need_image") : t("rec_no_cap"));

  return {
    start: () => { if (!state.running) start(); },
    stop: () => finish("early"),
  };
}
