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
 * A presentation layer is painted on top by recordfx.js — chapter wipes, a
 * spotlight and a drawn pointer. That layer is PRESENTATION only: it changes
 * how a shot is lit and where the eye goes, never what the product does. The
 * verdict on camera is still whatever the model actually returned, slow or
 * failing included, and no frame of the interface is redrawn. The panel says
 * so in as many words, so nobody mistakes the export for an untouched capture.
 *
 * There is deliberately no camera move. No zoom, no pan, no transform is ever
 * written to a product layer: the interface is filmed at its own size and the
 * only thing that reframes a shot is the page's own scrolling. The framing is
 * therefore identical from the first frame to the last.
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
import { initPresentation } from "./recordfx.js";

/** The sequence itself. Beats are pinned to this clock so the exported file
 *  always runs 03:00, whatever the network was doing on the day it was shot. */
const TOTAL_MS = 180_000;

/** A short hold on the opening frame before the clock starts. It gives the
 *  operator time to take their hand off the mouse, and it doubles as a clean
 *  title beat at the head of the video. */
const PREROLL_MS = 3_000;

const TICK_MS = 250;           // HUD refresh, deliberately coarse
const CHUNK_MS = 1_000;        // MediaRecorder timeslice
/** 60fps needs roughly twice the budget of 30. ~90 MB for three minutes,
 *  which a laptop writes to disk without a stutter — dropping frames to keep
 *  the file small would cost more than the file saves. */
const BITRATE = 12_000_000;
const RESULT_WAIT_MS = 45_000; // a 429 backoff can push a real scan this far

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
    outFps: $("rec-fps"),
    outRes: $("rec-res"),
  };

  /** The pointer, the light and the transitions. Everything it does is layered
   *  over the real interface and nothing it does touches the product's
   *  geometry — see recordfx.js. It holds no camera and never scales anything. */
  const fx = initPresentation();

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

  /** Reads back what the capture actually committed to. The browser is free
   *  to deliver fewer frames and fewer pixels than were asked for, and the
   *  operator deserves the real numbers rather than the requested ones.
   *
   *  This matters most for the resolution. `getDisplayMedia` cannot invent
   *  pixels: it captures the tab or the screen as it physically is, so asking
   *  for 3840×2160 on a 1920×1080 display returns 1920×1080. There is no way
   *  to force 4K from in here that is not also a lie — upscaling on a canvas
   *  would manufacture detail the product never drew, and it is the one thing
   *  this recorder has never done and will not start. The only honest way to
   *  state the size is to read it back off the track, which is what the output
   *  panel shows. Shoot on a 4K display to get a 4K file. */
  function reportCapture(stream) {
    const [track] = stream.getVideoTracks();
    const s = track && typeof track.getSettings === "function" ? track.getSettings() : {};
    const fps = Math.round(s.frameRate || 0);
    if (els.outFps) els.outFps.textContent = fps ? `${fps} fps` : "—";
    if (els.outRes) {
      els.outRes.textContent = (s.width && s.height) ? `${s.width}×${s.height}` : "—";
    }
  }

  /** Removes the edge from the last subject and drops the spotlight. The
   *  outline earns its place now that there is no camera: with the framing
   *  fixed, this and the spotlight are what tell a viewer which of three
   *  similar cards is being discussed. */
  function clearHl() {
    if (state.hl) state.hl.classList.remove("rec-hl");
    state.hl = null;
    if (state.hlTimer) clearTimeout(state.hlTimer);
    state.hlTimer = null;
    fx.clearSpotlight();
  }

  /** One subject at a time: centre it with the page's own scrolling, glide the
   *  pointer onto it, then hold. No scale is applied anywhere — the subject is
   *  shown at exactly the size the product renders it. */
  function hl(target, ms = 4000, opts = {}) {
    clearHl();
    return fx.focus(target, {
      hold: ms,
      onLand: (el) => {
        el.classList.add("rec-hl");
        state.hl = el;
        state.hlTimer = setTimeout(clearHl, ms);
      },
    });
  }

  /** The n-th card of a set — the pricing tiers all share a class, so an
   *  index is the only stable name for one of them. */
  function hlAt(selector, index, ms, opts = {}) {
    return hl($all(selector)[index] || null, ms, opts);
  }

  /** Glide to a control and actually press it. `real: false` is the drawn
   *  version, used where a genuine click would derail the automation — the
   *  dropzone above all, whose only response to a click is a file picker. */
  function press(selector, opts = {}) {
    return fx.tap(selector, { real: opts.real !== false, hold: opts.hold ?? 420 });
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

  /** Presses the app's own toggle, so the RTL/LTR change on camera is the
   *  real one — including the verdict re-render it triggers. */
  function flipLanguage() {
    return press("#app [data-lang-toggle]", { hold: 400 });
  }

  /** The genuine dialog, opened through the same call registration hands off
   *  to. Only the identity in the address slot is a disclosed placeholder. */
  function openSubscription() {
    const handle = nav.getSubscribe();
    if (!handle || typeof handle.open !== "function") return;
    const user = nav.getUser();
    handle.open(user || { email: DEMO_ACCOUNT });
  }

  /** The whole 180 seconds, as absolute times. Four segments, matching the
   *  brief: opening problem statement, live diagnosis, plans, call to action.
   *
   *  No beat applies a scale. A subject is centred with the page's own
   *  scrolling and then simply left there, shown at the size the product
   *  renders it — so the framing of shot 1 and shot 20 differ only by scroll
   *  position, never by magnification. */
  function beats() {
    return [
      /* ---- 0:00–0:45 · the problem, and the header that answers it ---- */
      {
        at: 0,
        seg: "rec_seg1",
        run: async () => {
          // The opening dissolve covers the swap from the recorder panel to
          // the landing page, so the file never contains a frame of the
          // filming rig.
          await fx.wipe("dissolve", () => nav.goLanding());
          await hl("#main .hero__title", 4200);
        },
      },
      { at: 6_500, seg: "rec_seg1", run: () => hl(".hero__sub", 3400) },
      { at: 11_500, seg: "rec_seg1", run: () => hl(".steps", 3400) },
      { at: 17_000, seg: "rec_seg1", run: () => hl("#modules .lgrid", 3000) },
      { at: 22_000, seg: "rec_seg1", run: () => hl("#why .lgrid", 3000) },
      { at: 27_500, seg: "rec_seg1", run: () => hl("#landing .brand", 4500) },
      // Back to the hero and breathe before the chapter turns.
      { at: 33_500, seg: "rec_seg1", run: () => hl("#main .hero__copy", 4500) },
      { at: 39_500, seg: "rec_seg1", run: () => fx.wander(5000) },

      /* ---- 0:45–1:45 · a real photograph, a real diagnosis ---- */
      {
        at: 45_000,
        seg: "rec_seg2",
        run: async () => {
          await fx.wipe("sweep", () => nav.goPanel("studio"));
          await hl("#dropzone", 4500);
        },
      },
      // Drawn only. A real click here would open a file picker over the
      // recording and stall the automation — this is the one place the
      // pointer's gesture and the product's behaviour are deliberately apart,
      // and the photograph is loaded through the real input immediately after.
      { at: 51_000, seg: "rec_seg2", run: () => press("#dropzone", { real: false, hold: 900 }) },
      {
        at: 52_500,
        seg: "rec_seg2",
        run: async () => {
          loadSample();
          await hl("#preview-wrap", 4500);
        },
      },
      { at: 58_500, seg: "rec_seg2", run: () => hl("#analyze", 3000) },
      {
        at: 62_500,
        seg: "rec_seg2",
        run: async () => {
          // The real request. Whatever comes back — fast, slow or a failure —
          // is what gets filmed. The pointer wanders so the frame is not
          // dead, and stops the instant the answer lands: no padding, no cut.
          await press("#analyze", { real: true, hold: 400 });
          const ok = await fx.wanderUntil(waitForResults(RESULT_WAIT_MS));
          if (!ok) return; // the interface is already showing the real outcome
          await hl("#summary", 6000);
        },
      },
      { at: 78_000, seg: "rec_seg2", run: () => hl("#badges", 3500) },
      { at: 83_000, seg: "rec_seg2", run: () => hl("#verdict-ar", 3500) },
      // Flip away and back. The wipe covers the re-render, which otherwise
      // reads as a glitch rather than as the same verdict in another script.
      {
        at: 88_500,
        seg: "rec_seg2",
        run: async () => {
          // The pointer glides onto the app's own toggle first, so the
          // gesture is on screen; the flip itself then happens at the
          // dissolve's peak. The re-render it triggers would otherwise read
          // as a glitch rather than as the same verdict in another script.
          await fx.tap("#app [data-lang-toggle]", { real: false, hold: 320 });
          await fx.wipe("dissolve", flipLanguage);
        },
      },
      { at: 92_000, seg: "rec_seg2", run: () => hl("#summary", 4000) },
      {
        at: 98_000,
        seg: "rec_seg2",
        run: async () => {
          // Same again on the way back, so the toggle is visibly the one
          // control responsible for both readings of the verdict.
          await fx.tap("#app [data-lang-toggle]", { real: false, hold: 320 });
          await fx.wipe("dissolve", flipLanguage);
        },
      },
      { at: 101_500, seg: "rec_seg2", run: () => fx.wander(3200) },

      /* ---- 1:45–2:30 · the plans, and the subscription dialog ---- */
      {
        at: 105_000,
        seg: "rec_seg3",
        run: async () => {
          await fx.wipe("sweep", () => nav.goPanel("pricing"));
          await hl(".plans", 4000);
        },
      },
      { at: 111_000, seg: "rec_seg3", run: () => hlAt(".plans .plan", 0, 4000) },
      { at: 117_500, seg: "rec_seg3", run: () => hlAt(".plans .plan", 1, 4000) },
      { at: 124_000, seg: "rec_seg3", run: () => hlAt(".plans .plan", 2, 4000) },
      { at: 130_500, seg: "rec_seg3", run: () => hl(".pricing-note", 3400) },
      {
        at: 135_000,
        seg: "rec_seg3",
        run: async () => {
          // The dialog is opened by the same call registration hands off to.
          // Note what does NOT happen: the pointer does not pre-position on a
          // plan button, because clicking one opens the preview notice rather
          // than this dialog — implying otherwise is exactly the fabrication
          // this recorder exists to avoid. The modal arrives under the wipe
          // and the pointer glides into it afterwards.
          await fx.wipe("dissolve", openSubscription);
          await hl("#su-modal .modal__panel", 4500);
        },
      },
      // The checkout fields are filmed on purpose — they carry their own
      // "nothing is charged" banner, so the video shows the demo as a demo.
      { at: 142_000, seg: "rec_seg3", run: () => hl("#pay-form", 6000) },

      /* ---- 2:30–3:00 · the call to action ---- */
      {
        at: 149_000,
        seg: "rec_seg4",
        run: async () => {
          // The pointer travels to the dialog's own close button while the
          // veil is still down, so the gesture is legible. The wipe then
          // rises, and the press lands at its peak — under cover. Closing
          // first would expose the jump straight back to the pricing panel
          // for a third of a second.
          await fx.tap("#su-modal .modal__head [data-close]", { real: false, hold: 380 });
          await fx.wipe("sweep", () => {
            press("#su-modal .modal__head [data-close]", { real: true, hold: 0 });
            nav.goPanel("dashboard");
          });
          await hl("#app-cta", 5000);
        },
      },
      { at: 158_000, seg: "rec_seg4", run: () => hl("#app-cta a", 5000) },
      { at: 165_000, seg: "rec_seg4", run: () => hl("#app .brand", 4000) },
      // Ends on the call to action and the repository link, in frame, still.
      { at: 171_000, seg: "rec_seg4", run: () => hl("#app-cta", 6000) },
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
    // The camera, the pointer and the overlays come down with the recording.
    // Leaving a scale() on #app would be a bug the operator sees on their
    // next ordinary session, long after the file was exported.
    fx.end();

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
        // 60 is requested, not assumed. The same goes for 4K, and harder:
        // `getDisplayMedia` captures the tab or the screen as it physically
        // is and cannot invent pixels, so 3840×2160 lands only on a display
        // that already has them. Both numbers are read back off the track and
        // shown, so nobody ends up believing they shot 4K on a 1080p screen.
        video: {
          frameRate: { ideal: 60, max: 60 },
          width: { ideal: 3840 },
          height: { ideal: 2160 },
        },
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
    reportCapture(stream);

    els.out.classList.add("hidden");
    els.start.classList.add("hidden");
    els.stop.classList.remove("hidden");
    tick();
    state.ticker = setInterval(tick, TICK_MS);
    state.capTimer = setTimeout(() => finish("auto"), TOTAL_MS);
    fx.begin();

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
