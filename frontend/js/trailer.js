/* ============================================================================
 * trailer.js — "Play Demo Trailer": a motion preview, and nothing else
 *
 * What this is
 * ------------
 * A button that plays the product's three-minute visual sequence live in the
 * browser so the choreography can be watched, judged and retimed before anyone
 * spends three minutes of real time filming it. Chapters, easing, dwell times
 * and cursor travel are all here to be tuned; record.js imports none of it, so
 * the trailer can change freely without touching what gets recorded.
 *
 * It deliberately reuses recordfx.js for every primitive — the pointer, the
 * spotlight, the wipes, the scrolling. The trailer and the recorder therefore
 * move identically, which is the point: what is previewed is what is filmed.
 *
 * What this deliberately is NOT
 * -----------------------------
 * It is not a recording and it does not behave like one. No `getDisplayMedia`,
 * no `MediaRecorder`, no file, no download — the moment this module named any
 * of them it would stop being a preview and start being a second recorder.
 * It imports no HTTP helper and submits nothing.
 *
 * The line that matters most is the verdict. The diagnosis chapter frames the
 * studio and presses the Analyze control as a DRAWN gesture only — it never
 * dispatches a real click, because that would fire a genuine request at a
 * moment the operator did not ask for one, and because the one thing this
 * project has never done is invent a diagnosis. When a real result is already
 * on screen it is framed; when it is not, the chapter frames the upload step
 * and moves on. There is no third case, and nothing is ever rendered to fill
 * the gap.
 *
 * Language: the sequence opens in English and LTR, because that is the language
 * a review audience reads. The operator's own choice is remembered and put back
 * when the trailer stops, so previewing does not quietly change the app's
 * language out from under them.
 *
 * Nothing here is verifiable without a browser: it is checked by static suites
 * and by reading it, not by looking at a rendered frame.
 * ========================================================================== */

import { $ } from "./dom.js";
import { t } from "./i18n.js";
import { initPresentation } from "./recordfx.js";

/** The whole sequence, as absolute times. Five chapters, matching the brief:
 *  problem, upload and diagnosis, the bilingual toggle, the plans, and the
 *  call to action with the repository link. */
const TOTAL_MS = 180_000;
const TICK_MS = 250;

/* --------------------------------------------------------------- helpers -- */

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const clock = (ms) => {
  const total = Math.max(0, Math.round(ms / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
};

/* --------------------------------------------------------------- module -- */

export function initTrailer(opts = {}) {
  const nav = {
    goLanding: typeof opts.goLanding === "function" ? opts.goLanding : () => {},
    goPanel: typeof opts.goPanel === "function" ? opts.goPanel : () => {},
    setLang: typeof opts.setLang === "function" ? opts.setLang : null,
    currentLang: typeof opts.currentLang === "function" ? opts.currentLang : null,
  };

  const els = {
    hud: $("trailer-hud"),
    chapter: $("trailer-chapter"),
    elapsed: $("trailer-elapsed"),
    stop: $("trailer-stop"),
    play: $("trailer-play"),
  };

  /** The pointer, the light and the transitions. Shared with record.js, so
   *  the preview and the recording cannot drift apart. */
  const fx = initPresentation();

  const state = {
    running: false,
    startedAt: 0,
    ticker: null,
    clock: null,
    chapter: "",
    previousLang: null,
    onEscape: null,
  };

  /* ------------------------------------------------------------- plumbing */

  /** True only when the element is genuinely on screen. The studio's preview
   *  and results blocks are conditional — they exist once a photograph has
   *  been analysed — and framing a hidden element would scroll to a zero-height
   *  box and hold on nothing. */
  function visible(id) {
    const node = $(id);
    if (!node || node.classList.contains("hidden")) return false;
    return node.offsetWidth > 0 && node.offsetHeight > 0;
  }

  function clearHl() {
    fx.clearSpotlight();
  }

  /** One subject: centre it with the page's own scrolling, glide the pointer
   *  onto it, hold. No scale is applied anywhere — the camera frame never
   *  moves but for this scroll. */
  function hl(target, ms = 4000, onLand) {
    clearHl();
    return fx.focus(target, { hold: ms, onLand });
  }

  function press(target, opts = {}) {
    return fx.tap(target, { real: Boolean(opts.real), hold: opts.hold ?? 420 });
  }

  /** The n-th card of a set — the pricing tiers all share a class, so an
   *  index is the only stable name for one of them. */
  function hlAt(selector, index, ms) {
    const node = document.querySelectorAll(selector)[index];
    return hl(node || null, ms);
  }

  /* ------------------------------------------------------------- the HUD */

  function setChapter(key) {
    if (state.chapter === key) return;
    state.chapter = key;
    if (els.chapter) els.chapter.textContent = t(key);
  }

  function tick() {
    if (!els.elapsed) return;
    els.elapsed.textContent = `${clock(Date.now() - state.startedAt)} / ${clock(TOTAL_MS)}`;
  }

  /* ------------------------------------------------------- the choreography */

  /** The sequence itself, as absolute times. Each beat reframes one subject
   *  with scrolling and the page's own layout — there is no zoom, no pan and
   *  no transform anywhere in this list. */
  function beats() {
    return [
      /* ---- 0:00–0:40 · the problem, and the header that answers it ---- */
      {
        at: 0,
        run: async () => {
          await fx.wipe("dissolve", () => nav.goLanding());
          await hl("#main .hero__title", 4600);
        },
      },
      { at: 6_500, run: () => hl(".hero__sub", 4000) },
      { at: 12_000, run: () => hl(".steps", 4200) },
      { at: 17_500, run: () => hl("#modules .lgrid", 3600) },
      { at: 22_500, run: () => hl("#why .lgrid", 3600) },
      { at: 27_500, run: () => hl("#landing .brand", 5000) },
      { at: 33_500, run: () => hl("#main .hero__copy", 3500) },
      { at: 37_500, run: () => fx.wander(2500) },

      /* ---- 0:40–1:30 · the upload, and the diagnosis ----
         No beat here submits anything. The Analyze control is pressed as a
         drawn gesture so the affordance is visible, and the result is framed
         only when a real one is already on screen. */
      {
        at: 40_000,
        run: async () => {
          setChapter("tr_ch2");
          await fx.wipe("sweep", () => nav.goPanel("studio"));
          await hl("#dropzone", 5000);
        },
      },
      { at: 46_500, run: () => press("#dropzone", { real: false, hold: 1000 }) },
      {
        at: 48_500,
        run: () => (visible("preview-wrap") ? hl("#preview-wrap", 5000) : fx.wander(5000)),
      },
      { at: 54_500, run: () => hl("#analyze", 4500) },
      // Drawn only. A real click would fire a genuine request at a moment the
      // operator did not ask for one — and would need a photograph to have
      // been chosen first.
      { at: 60_000, run: () => press("#analyze", { real: false, hold: 1200 }) },
      {
        at: 62_500,
        run: () => (visible("results") ? hl("#summary", 7000) : fx.wander(7000)),
      },
      {
        at: 70_500,
        run: () => (visible("results") ? hl("#badges", 4000) : fx.wander(4000)),
      },
      {
        at: 75_500,
        run: () => (visible("results") ? hl("#verdict-ar", 4000) : fx.wander(4000)),
      },
      { at: 80_500, run: () => fx.wander(9000) },

      /* ---- 1:30–2:00 · the same verdict, in both scripts ----
         This chapter DOES press for real: the toggle is the product's own
         control and flipping it is genuine UI, not a manufactured frame. */
      {
        at: 90_000,
        run: async () => {
          setChapter("tr_ch3");
          await fx.wipe("dissolve");
          await press("#app [data-lang-toggle]", { real: false, hold: 1200 });
        },
      },
      {
        at: 92_500,
        run: async () => {
          await Promise.all([press("#app [data-lang-toggle]", { real: true, hold: 300 }),
            fx.wipe("dissolve")]);
        },
      },
      { at: 94_500, run: () => hl("#analyze", 4500) },
      {
        at: 100_000,
        run: async () => {
          await Promise.all([press("#app [data-lang-toggle]", { real: true, hold: 300 }),
            fx.wipe("dissolve")]);
        },
      },
      { at: 102_000, run: () => hl("#analyze", 4000) },
      { at: 106_500, run: () => hl("#dropzone", 4000) },
      { at: 111_000, run: () => fx.wander(9000) },

      /* ---- 2:00–2:35 · the plans ---- */
      {
        at: 120_000,
        run: async () => {
          setChapter("tr_ch4");
          await fx.wipe("sweep", () => nav.goPanel("pricing"));
          await hl(".plans", 4500);
        },
      },
      { at: 126_000, run: () => hlAt(".plans .plan", 0, 4200) },
      { at: 131_000, run: () => hlAt(".plans .plan", 1, 4200) },
      { at: 136_000, run: () => hlAt(".plans .plan", 2, 4200) },
      { at: 141_000, run: () => hl(".pricing-note", 3500) },
      { at: 145_000, run: () => fx.wander(15000) },

      /* ---- 2:35–3:00 · the call to action ---- */
      {
        at: 155_000,
        run: async () => {
          setChapter("tr_ch5");
          await fx.wipe("sweep", () => nav.goPanel("dashboard"));
          await hl("#app-cta", 5500);
        },
      },
      { at: 161_500, run: () => hl("#app-cta a", 5500) },
      { at: 168_000, run: () => hl("#app .brand", 4500) },
      { at: 173_500, run: () => hl("#app-cta", 6000) },
    ];
  }

  async function runSequence() {
    for (const beat of beats()) {
      if (!state.running) return;
      const target = state.startedAt + beat.at;
      const delay = target - (Date.now() - state.startedAt);
      if (delay > 0) await wait(delay);
      if (!state.running) return;
      try {
        await beat.run();
      } catch (err) {
        // A beat that throws must not take the sequence down with it. The
        // next beat still has its own slot on the clock.
        console.error("[hassad] trailer beat failed:", err);
      }
    }
    stop();
  }

  /* ------------------------------------------------------------- lifecycle */

  function onEscape(event) {
    if (event.key !== "Escape") return;
    event.preventDefault();
    stop();
  }

  function play() {
    if (state.running) return;

    // English and LTR for the review audience, with the operator's own choice
    // kept safe to put back. setLang writes to localStorage, so a preview that
    // crashed mid-run would leave the app in English — restored here on every
    // exit path below, so the only way to lose the choice is a dead tab.
    state.previousLang = nav.currentLang ? nav.currentLang() : null;
    if (nav.setLang) nav.setLang("en");

    document.body.classList.add("is-trailer");
    fx.begin();
    state.running = true;
    state.startedAt = Date.now();
    state.chapter = "";
    setChapter("tr_ch1");

    if (els.hud) els.hud.classList.remove("hidden");
    if (els.play) els.play.classList.add("is-playing");

    tick();
    state.ticker = setInterval(tick, TICK_MS);
    state.clock = setTimeout(stop, TOTAL_MS);

    state.onEscape = onEscape;
    document.addEventListener("keydown", state.onEscape);

    runSequence();
  }

  function stop() {
    if (!state.running) return;
    state.running = false;

    if (state.ticker) { clearInterval(state.ticker); state.ticker = null; }
    if (state.clock) { clearTimeout(state.clock); state.clock = null; }
    if (state.onEscape) {
      document.removeEventListener("keydown", state.onEscape);
      state.onEscape = null;
    }

    clearHl();
    fx.end();
    document.body.classList.remove("is-trailer");

    if (els.hud) els.hud.classList.add("hidden");
    if (els.play) els.play.classList.remove("is-playing");

    if (nav.setLang && state.previousLang) nav.setLang(state.previousLang);
    state.previousLang = null;
  }

  // Bound here rather than through app.js: this control starts a preview, it
  // does not open a panel, so the sidebar's own panel routing would fight it.
  if (els.play) els.play.addEventListener("click", () => (state.running ? stop() : play()));
  if (els.stop) els.stop.addEventListener("click", stop);

  return { play, stop, isPlaying: () => state.running };
}
