/* ============================================================================
   Hassad AI — cinematic trailer engine
   ----------------------------------------------------------------------------
   Standalone. No application module is imported, no state is shared with the
   product, and nothing here records, captures or downloads.

   The whole piece is one clock. A single requestAnimationFrame loop reads
   performance.now(), and every cue is an absolute millisecond on a 40-second
   timeline. Cues never call setTimeout for their own scheduling and never
   chain from one another, so a slow frame cannot make the later beats drift
   out of step: a beat that is due fires, and the clock keeps the rest honest.

   The pointer is the only element that moves continuously. Its position is a
   translate3d write on a compositor layer — no layout, no paint — and the
   easing is a cubic in/out so it accelerates out of rest and settles into its
   target rather than stopping dead.
   ========================================================================== */

const TOTAL = 40000;          // 40 seconds, exactly
const REDUCED = window.matchMedia &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ------------------------------------------------------------------ refs -- */

const $ = (id) => document.getElementById(id);

const stage = $("stage");
const curtain = $("curtain");
const wipe = $("wipe");
const pointer = $("pointer");
const ripple = $("ripple");
const chapterEl = $("chapter");
const timeEl = $("time");
const bar = $("bar");

const iEnOpt = $("i-en-opt");
const iArOpt = $("i-ar-opt");
const iBi = $("i-bi");

/* --------------------------------------------------------------- helpers -- */

const setText = (id, value) => {
  const node = $(id);
  if (node) node.textContent = value;
};

const on = (id) => {
  const node = $(id);
  if (node) node.classList.add("on");
};

const off = (id) => {
  const node = $(id);
  if (node) node.classList.remove("on");
};

const addClass = (id, name) => {
  const node = $(id);
  if (node) node.classList.add(name);
};

const chapter = (label) => {
  if (chapterEl) chapterEl.textContent = label;
};

const mmss = (ms) => {
  const secs = Math.max(0, Math.floor(ms / 1000));
  const m = String(Math.floor(secs / 60)).padStart(2, "0");
  const s = String(secs % 60).padStart(2, "0");
  return `${m}:${s}`;
};

/* -------------------------------------------------------------- pointer -- */

const pos = { x: 0, y: 0 };

/** Place the pointer's hotspot. One transform write; the compositor does the
 *  rest, so a 4K stage costs exactly what a 1080p one costs. */
function place(x, y) {
  pos.x = x;
  pos.y = y;
  pointer.style.transform = `translate3d(${x}px, ${y}px, 0)`;
}

/** Where a hand would actually land on the control: a little left of centre
 *  and a little below it, not dead middle. */
function aim(node) {
  const sr = stage.getBoundingClientRect();
  const r = node.getBoundingClientRect();
  return {
    x: r.left - sr.left + r.width * 0.42,
    y: r.top - sr.top + r.height * 0.58,
  };
}

let glideRaf = 0;

function glideTo(target, ms = 820) {
  const node = typeof target === "string" ? $(target) : target;
  if (!node) return;

  cancelAnimationFrame(glideRaf);

  const from = { x: pos.x, y: pos.y };
  const to = aim(node);
  const startedAt = performance.now();

  const step = (now) => {
    const p = Math.min(1, (now - startedAt) / ms);
    // Ease-in-out cubic: no jerk at either end.
    const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
    place(from.x + (to.x - from.x) * e, from.y + (to.y - from.y) * e);
    if (p < 1) glideRaf = requestAnimationFrame(step);
  };
  glideRaf = requestAnimationFrame(step);
}

function showPointer() {
  const r = stage.getBoundingClientRect();
  place(r.width * 0.74, r.height * 0.84);
  pointer.classList.add("is-on");
}

/** The ripple is positioned with left/top, not transform, because its own
 *  keyframes animate transform (the scale) and would overwrite a translate. */
function rippleAt(x, y) {
  ripple.style.left = `${x}px`;
  ripple.style.top = `${y}px`;
  ripple.classList.remove("is-on");
  void ripple.offsetWidth;   // restart the animation
  ripple.classList.add("is-on");
}

/** A press: put the pointer exactly on the control, flash the ring, and let
 *  the caller decide what the press means. Nothing is ever clicked for real —
 *  there is nothing behind these controls but the composition itself. */
function press(target, opts = {}) {
  const node = typeof target === "string" ? $(target) : target;
  if (!node) return;
  const spot = aim(node);
  place(spot.x, spot.y);
  rippleAt(spot.x, spot.y);
  const cls = opts.cls || "is-press";
  node.classList.add(cls);
  setTimeout(() => node.classList.remove(cls), opts.hold || 480);
}

/* -------------------------------------------------------- scene changes -- */

const scenes = () => document.querySelectorAll(".scene");

function showScene(id) {
  scenes().forEach((scene) => {
    scene.classList.toggle("is-active", scene.id === id);
  });
}

/** Carry a scene change on a moving plate. The plate covers the frame, the
 *  scene swaps underneath it, and the plate leaves — so a cut is never seen
 *  and the camera itself never moves. */
function shutterTo(nextId) {
  wipe.classList.remove("is-in", "is-out");
  void wipe.offsetWidth;
  wipe.classList.add("is-in");
  setTimeout(() => {
    showScene(nextId);
    wipe.classList.remove("is-in");
    wipe.classList.add("is-out");
    setTimeout(() => wipe.classList.remove("is-out"), 420);
  }, 380);
}

/* -------------------------------------------------------------- counters -- */

function countTo(id, target, suffix = "", ms = 1100) {
  const node = $(id);
  if (!node) return;
  const startedAt = performance.now();
  const step = (now) => {
    const p = Math.min(1, (now - startedAt) / ms);
    const e = 1 - Math.pow(1 - p, 3);
    node.textContent = `${Math.round(target * e)}${suffix}`;
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ---------------------------------------------------------------- timeline -- */

/* Every beat of the forty seconds, as an absolute time. Reading down this list
   is reading the piece: the hook, the answer, the diagnosis, the reach. */
const CUES = [
  /* ---- ACT I · the hook ---- */
  { at: 0, run: () => chapter("The hook") },
  { at: 480, run: () => on("b-kicker") },
  { at: 1150, run: () => on("b-crisis") },
  { at: 2100, run: () => on("b-src") },
  { at: 2800, run: () => on("b-turn") },
  { at: 4400, run: () => off("b-turn") },
  { at: 4650, run: () => off("b-crisis") },
  { at: 4800, run: () => off("b-src") },
  { at: 4950, run: () => off("b-kicker") },

  /* ---- ACT I · the answer ---- */
  { at: 5300, run: () => shutterTo("scene-identity") },
  { at: 5850, run: () => on("b-wordmark") },
  { at: 6800, run: () => on("b-tag") },
  { at: 7650, run: () => on("b-chips") },

  /* ---- ACT II · the diagnosis ---- */
  { at: 9150, run: () => shutterTo("scene-magic") },
  { at: 10000, run: () => { chapter("The diagnosis"); on("m-upload"); } },
  { at: 10300, run: () => showPointer() },
  { at: 10600, run: () => glideTo("m-upload", 900) },
  { at: 11650, run: () => addClass("m-upload", "is-hover") },
  {
    at: 12150,
    run: () => {
      press("m-upload", { cls: "is-hover", hold: 700 });
      addClass("m-upload", "is-loaded");
      on("m-leaf");
      setText("m-hint", "Leaf selected");
    },
  },
  { at: 13300, run: () => glideTo("m-btn", 780) },
  { at: 14150, run: () => addClass("m-btn", "is-press") },
  {
    at: 14500,
    run: () => {
      press("m-btn", { hold: 520 });
      addClass("m-magic", "is-busy");
      setText("m-btn-label", "Analyzing");
    },
  },
  { at: 15550, run: () => on("m-diag") },
  { at: 16200, run: () => countTo("m-pct", 94, "%", 1100) },
  { at: 19500, run: () => glideTo("m-diag", 760) },
  { at: 20300, run: () => on("m-facts") },
  { at: 22300, run: () => addClass("m-diag", "is-focus") },

  /* ---- ACT III · reach ---- */
  { at: 25000, run: () => shutterTo("scene-impact") },
  { at: 25400, run: () => { chapter("Global impact"); on("i-head"); } },
  {
    at: 26300,
    run: () => {
      off("i-head");
      on("i-bi");
    },
  },
  { at: 27000, run: () => glideTo("i-ar-opt", 760) },
  {
    at: 27650,
    run: () => {
      press("i-ar-opt", { hold: 420 });
      iArOpt.classList.add("is-on");
      iEnOpt.classList.remove("is-on");
      iBi.classList.add("is-ar");
    },
  },
  {
    at: 29300,
    run: () => {
      press("i-en-opt", { hold: 420 });
      iEnOpt.classList.add("is-on");
      iArOpt.classList.remove("is-on");
      iBi.classList.remove("is-ar");
    },
  },
  { at: 29900, run: () => off("i-bi") },
  {
    at: 30200,
    run: () => {
      on("i-oss");
      countTo("st-1", 610, "", 1200);
      countTo("st-2", 6, "", 900);
      countTo("st-3", 2, "", 700);
      countTo("st-4", 100, "", 1200);
    },
  },
  { at: 33300, run: () => off("i-oss") },
  { at: 33600, run: () => on("i-cta") },
  { at: 36800, run: () => off("i-cta") },
  { at: 37000, run: () => on("i-finale") },
];

// A cue list written out of order is a bug that would only show up as one beat
// firing early. Sorting here costs nothing and removes the failure mode.
const ORDERED = CUES.slice().sort((a, b) => a.at - b.at);

/* ------------------------------------------------------------------ clock -- */

let playing = false;
let raf = 0;
let startedAt = 0;
let nextCue = 0;

function paint(elapsed) {
  if (bar) bar.style.transform = `scaleX(${Math.min(1, elapsed / TOTAL)})`;
  if (timeEl) timeEl.textContent = `${mmss(elapsed)} / ${mmss(TOTAL)}`;
}

function frame(now) {
  if (!playing) return;

  const elapsed = now - startedAt;

  while (nextCue < ORDERED.length && ORDERED[nextCue].at <= elapsed) {
    const cue = ORDERED[nextCue++];
    try {
      cue.run();
    } catch (err) {
      // One failed beat must not stop the clock for the rest of the piece.
      console.error("[hassad] trailer cue failed:", err);
    }
  }

  paint(elapsed);

  if (elapsed >= TOTAL) {
    finish();
    return;
  }
  raf = requestAnimationFrame(frame);
}

/* ------------------------------------------------------------- lifecycle -- */

function clearState() {
  stage.querySelectorAll(".on").forEach((node) => node.classList.remove("on"));
  ["is-hover", "is-loaded", "is-press", "is-focus", "is-busy", "is-ar", "is-on"]
    .forEach((name) => {
      stage.querySelectorAll(`.${name}`).forEach((node) => node.classList.remove(name));
    });

  scenes().forEach((scene) => scene.classList.remove("is-active"));

  if (iEnOpt) iEnOpt.classList.add("is-on");

  setText("m-pct", "0%");
  setText("m-hint", "Drop a photo to begin");
  setText("m-btn-label", "Analyze");
  ["st-1", "st-2", "st-3", "st-4"].forEach((id) => setText(id, "0"));

  cancelAnimationFrame(glideRaf);
}

function reset() {
  playing = false;
  cancelAnimationFrame(raf);
  clearState();

  document.body.classList.remove("is-playing", "is-done", "is-static");
  curtain.classList.remove("is-closing");

  chapter("The hook");
  paint(0);
}

function finish() {
  playing = false;
  cancelAnimationFrame(raf);
  document.body.classList.remove("is-playing");
  document.body.classList.add("is-done");
  paint(TOTAL);
}

function start() {
  if (playing) return;

  clearState();
  document.body.classList.remove("is-done", "is-static");
  document.body.classList.add("is-playing");

  // A fade from black, not a cut onto a lit stage.
  curtain.classList.remove("is-closing");
  void curtain.offsetWidth;
  curtain.classList.add("is-closing");

  showScene("scene-crisis");
  paint(0);

  // A viewer who asked the system not to animate is shown the finished frame
  // and the replay control, not a slowed-down version of the same forty
  // seconds. CSS alone would still sail each beat past them one at a time.
  if (REDUCED) {
    showScene("scene-impact");
    on("i-finale");
    countTo("st-1", 610, "", 1);
    countTo("st-2", 6, "", 1);
    countTo("st-3", 2, "", 1);
    countTo("st-4", 100, "", 1);
    setText("m-pct", "94%");
    document.body.classList.remove("is-playing");
    document.body.classList.add("is-static");
    paint(TOTAL);
    return;
  }

  nextCue = 0;
  startedAt = performance.now();
  playing = true;
  raf = requestAnimationFrame(frame);
}

/* ------------------------------------------------------------------- wire -- */

$("play").addEventListener("click", () => start());
$("replay").addEventListener("click", () => {
  reset();
  start();
});

document.addEventListener("keydown", (event) => {
  if (event.code === "Space" || event.code === "Enter") {
    if (!playing) {
      event.preventDefault();
      start();
    }
  }
  if (event.code === "Escape") reset();
});

// A hidden tab stops requestAnimationFrame, and on return every cue that was
// due would fire in one frame. Stopping instead keeps the piece honest: it
// either plays in front of someone or it does not play.
document.addEventListener("visibilitychange", () => {
  if (document.hidden && playing) reset();
});

/* ---------------------------------------------------------------- viewport -- */

/** The honest readout: what the display is actually showing, in device
 *  pixels. "4K ready" means the composition is vector and unit-scaled, so it
 *  is drawn at whatever density the screen offers — never upscaled to it. */
function paintRes() {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(window.innerWidth * dpr);
  const h = Math.round(window.innerHeight * dpr);
  setText("res", `${w} × ${h}`);
}

window.addEventListener("resize", paintRes);

paintRes();
paint(0);
chapter("The hook");
