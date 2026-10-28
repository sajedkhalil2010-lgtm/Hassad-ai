/* ============================================================================
 * recordfx.js — the cinematic layer that sits OVER the demo recorder
 *
 * What this adds
 * --------------
 * A camera (Ken Burns push-in and pull-out), chapter transitions, a spotlight
 * that dims everything except the thing being discussed, and a smooth gliding
 * pointer that arrives at each control and ripples when it "clicks". It exists
 * because a static screen recording of a working product is still a dull three
 * minutes, and engagement is a legitimate production goal.
 *
 * What it explicitly does NOT do
 * ------------------------------
 * It never changes a single thing about what the product does. The recorder in
 * record.js still sends the operator's real photograph to the real model and
 * still films whatever comes back — including a slow answer and including a
 * failure. Everything here is a PRESENTATION layer painted on top of that
 * genuine behaviour, exactly like zooming a clip in an editor or adding a
 * pointer highlight in a screencast:
 *
 *   · the cursor is drawn, not recorded — the operator's real pointer is
 *     parked and plays no part;
 *   · the zoom is a CSS transform on the page, not a different page;
 *   · `tap(node, { real: false })` shows a click ripple WITHOUT dispatching a
 *     click — used for the dropzone, where a real click would open a file
 *     picker and stall the automation. Only `real: true` activates anything.
 *
 * The distinction that keeps this honest is that nothing is ever *invented*:
 * no frame of the interface is redrawn, no verdict is authored, no state is
 * faked. If the diagnosis is unflattering, the zoom makes it more visible, not
 * less. The recorder panel says outright that this layer is presentational.
 *
 * Why the camera is a transform and not a scroll
 * ---------------------------------------------
 * `getDisplayMedia` captures the tab, so anything painted in the tab is in the
 * recording. Scaling the screen layers with CSS therefore produces a genuine
 * camera move in the exported file. It is done with `transform` alone because
 * that is compositor work: it animates on the GPU without re-running layout or
 * paint, which is what keeps a 60fps capture at 60fps.
 *
 * The scroll and the zoom are kept deliberately independent. Scrolling uses
 * `offsetTop` (a layout quantity, unaffected by transforms) and the zoom uses
 * `transform-origin` pinned to the viewport centre — so neither can read the
 * other's output and they cannot fight. A zoom about the viewport centre also
 * means an element scrolled to centre STAYS centred while it is magnified,
 * which is the whole trick behind a stable push-in.
 *
 * Nothing here is verifiable without a browser: it is checked by static suites
 * and by reading it, not by looking at a rendered frame.
 * ========================================================================== */

/** The two full screens. Both are normal-flow layers, so their transform
 *  origin is the viewport centre expressed in the layer's own coordinates. */
const SCREENS = ["#landing", "#app"];

/** Fixed-position layers. They already fill the viewport, so their origin is
 *  simply their own centre — no coordinate conversion needed. */
const FIXED = ["#su-modal", "#plan-modal", "#chat-modal", "#auth-modal", ".toast"];

/** Push-in amounts. Three magnifications plus the neutral `reset()` are
 *  enough to build a shot list; a fourth would only make the cuts harder to
 *  read. The pull-back between chapters is the reset, not a fourth zoom. */
const ZOOM_WIDE = 1.18;   // whole cards, pricing tiers
const ZOOM_MID = 1.45;    // the default emphasis
const ZOOM_TIGHT = 1.62;  // a single control, a link, a headline

const ZOOM_MS = 900;                 // camera move duration
const CURVE = "cubic-bezier(.33,.02,.2,1)";
const SCROLL_SETTLE_MS = 900;        // Safari < 18 emits no scrollend
const HOLD_DEFAULT_MS = 1600;        // dwell on a shot before moving on

/* --------------------------------------------------------------- helpers -- */

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const q = (selector) => document.querySelector(selector);

/** An id, a selector or a node all name a target the same way. Beats read
 *  better as `focus("#dropzone")` than as three lines of null checking. */
function resolve(target) {
  if (!target) return null;
  if (typeof target !== "string") return target;
  return document.getElementById(target) || q(target);
}

/** The element's position in the DOCUMENT, computed from the offsetParent
 *  chain. `getBoundingClientRect` would be wrong here: it reports the
 *  visually transformed box, which is exactly the number the camera is trying
 *  not to depend on. offsetTop is layout and no transform can move it. */
function docTop(node) {
  let y = 0;
  let cursor = node;
  while (cursor) {
    y += cursor.offsetTop;
    cursor = cursor.offsetParent;
  }
  return y;
}

/** True when the node lives inside a fixed layer. Those are pinned to the
 *  viewport by definition, so scrolling the window under them would move the
 *  page for nothing. */
function inFixedLayer(node) {
  let cursor = node;
  while (cursor && cursor !== document.body) {
    if (getComputedStyle(cursor).position === "fixed") return true;
    cursor = cursor.parentElement;
  }
  return false;
}

/** SVG elements need their own namespace, and createElementNS is the only
 *  way to get one. It is also why the pointer is assembled node by node
 *  instead of being assigned as a string: this project forbids innerHTML
 *  wholesale, so that no edit can ever route model- or user-supplied text
 *  through the one sink that would render it as markup. */
const SVG_NS = "http://www.w3.org/2000/svg";

function svgNode(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

/* --------------------------------------------------------------- module -- */

export function initCinema() {
  const live = { on: false, raf: 0, wander: 0, zoom: 1 };
  const at = { x: 0, y: 0 };          // where the drawn pointer currently is
  const els = {};
  let styled = [];                    // layers carrying an inline camera style

  /* --------------------------------------------------------- the overlay */

  /** Built in JS and torn down on stop, rather than shipped in index.html.
   *  The filming rig is not part of the product a farmer receives: keeping it
   *  out of the markup means a normal session has no trace of it, structurally
   *  rather than by hiding it with CSS. */
  function mount() {
    const veil = document.createElement("div");
    veil.className = "rec-veil";
    veil.setAttribute("aria-hidden", "true");

    const wipe = document.createElement("div");
    wipe.className = "rec-wipe";
    wipe.setAttribute("aria-hidden", "true");

    const pointer = document.createElement("div");
    pointer.className = "recursor";
    pointer.setAttribute("aria-hidden", "true");

    const ripple = document.createElement("span");
    ripple.className = "recursor__ripple";

    const art = svgNode("svg", {
      viewBox: "0 0 24 24", "aria-hidden": "true", class: "recursor__ptr",
    });
    art.append(svgNode("path", {
      d: "M5.5 3.2 18.6 12.1l-5.5 1.1 3 5.6-2.6 1.4-3-5.6-4.5 3.4Z",
    }));

    pointer.append(ripple, art);
    document.body.append(veil, wipe, pointer);
    els.veil = veil;
    els.wipe = wipe;
    els.pointer = pointer;
    els.ripple = pointer.querySelector(".recursor__ripple");
  }

  function unmount() {
    for (const node of [els.veil, els.wipe, els.pointer]) {
      if (node) node.remove();
    }
    els.veil = els.wipe = els.pointer = els.ripple = null;
  }

  /* ------------------------------------------------------------- the camera */

  function layersFor(node) {
    if (node && inFixedLayer(node)) {
      return FIXED.map(q).filter(Boolean);
    }
    return SCREENS.map(q).filter(Boolean);
  }

  /** The single place a transform is ever written. Origin is pinned to the
   *  viewport centre so a centred element stays centred while it grows; at
   *  z=1 any origin renders identically, which is why easing back out is
   *  always safe. */
  function applyZoom(z, layers) {
    for (const layer of layers) {
      const fixedLayer = FIXED.some((sel) => q(sel) === layer);
      layer.style.transformOrigin = fixedLayer
        ? "50% 50%"
        : `50% ${Math.round(window.scrollY + window.innerHeight / 2)}px`;
      layer.style.transition = `transform ${ZOOM_MS}ms ${CURVE}`;
      layer.style.transform = z === 1 ? "" : `scale(${z})`;
      if (!styled.includes(layer)) styled.push(layer);
    }
    live.zoom = z;
  }

  /** Back to a neutral framing with no animation, for use under cover of a
   *  wipe. An eased move here would still be half-finished when the
   *  transition faded away, and the chapter would open mid-gesture. Each
   *  one therefore starts wide and pushes in on its own first subject. */
  function reset() {
    for (const layer of [...SCREENS.map(q), ...FIXED.map(q)].filter(Boolean)) {
      layer.style.transition = "";
      layer.style.transform = "";
      layer.style.transformOrigin = "";
      if (!styled.includes(layer)) styled.push(layer);
    }
    live.zoom = 1;
  }

  /* ------------------------------------------------------------------ scroll */

  /** Centres the element in the un-transformed viewport, then waits for the
   *  browser's own smooth scroll to finish. `scrollend` is the right signal
   *  where it exists; elsewhere a bounded wait is the honest fallback rather
   *  than a guess that is right on one machine. */
  function scrollCenter(node) {
    if (inFixedLayer(node)) return Promise.resolve();
    const height = Math.max(
      document.body.scrollHeight, document.documentElement.scrollHeight,
    );
    const goal = Math.max(
      0,
      Math.min(
        docTop(node) + node.offsetHeight / 2 - window.innerHeight / 2,
        height - window.innerHeight,
      ),
    );
    if (Math.abs(window.scrollY - goal) < 2) return Promise.resolve();

    return new Promise((resolve) => {
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        window.removeEventListener("scrollend", done);
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(done, SCROLL_SETTLE_MS);
      window.addEventListener("scrollend", done, { once: true });
      window.scrollTo({ top: goal, behavior: "smooth" });
    });
  }

  /* ----------------------------------------------------------------- pointer */

  function moveCursor(x, y) {
    at.x = x;
    at.y = y;
    if (els.pointer) {
      els.pointer.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    }
  }

  /** Where the element's centre will be once the camera has finished moving.
   *  The rect is inverted with the scale it was actually MEASURED under —
   *  applyZoom has already stored the destination by the time this runs, but
   *  the transition has not moved a pixel yet, so using the destination here
   *  would divide a still-wide rect by a still-tight scale. Getting this
   *  wrong sends the pointer to the wrong place exactly when the push-in is
   *  largest, which is most of the video. It is also what lets the pointer
   *  and the camera travel CONCURRENTLY — one gesture, not two in a row. */
  function finalCenter(node, zoom, fromZoom) {
    const r = node.getBoundingClientRect();
    const c = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    const raw = {
      x: c.x + (r.left + r.width / 2 - c.x) / (fromZoom || 1),
      y: c.y + (r.top + r.height / 2 - c.y) / (fromZoom || 1),
    };
    return {
      x: c.x + zoom * (raw.x - c.x),
      y: c.y + zoom * (raw.y - c.y),
    };
  }

  /** Glides along a quadratic bezier rather than a straight line. Real hands
   *  arc, and the perpendicular lift here is what stops the motion reading as
   *  a machine interpolating between two coordinates. */
  function glideTo(point) {
    return new Promise((resolve) => {
      if (!live.on || !els.pointer) { resolve(); return; }
      const from = { x: at.x, y: at.y };
      const dx = point.x - from.x;
      const dy = point.y - from.y;
      const dur = Math.max(420, Math.min(1150, Math.hypot(dx, dy) * 0.62));
      const lift = { x: from.x + dx * 0.5 - dy * 0.16, y: from.y + dy * 0.5 + dx * 0.16 };
      const start = performance.now();

      const step = (now) => {
        if (!live.on) { resolve(); return; }
        const p = Math.min(1, (now - start) / dur);
        const e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
        const inv = 1 - e;
        moveCursor(
          inv * inv * from.x + 2 * inv * e * lift.x + e * e * point.x,
          inv * inv * from.y + 2 * inv * e * lift.y + e * e * point.y,
        );
        if (p < 1) live.raf = requestAnimationFrame(step);
        else resolve();
      };
      live.raf = requestAnimationFrame(step);
    });
  }

  /** The click: a ring out of the fingertip and a brief flash on the control.
   *  `real` is the safety catch. Only the beats that should genuinely activate
   *  something pass true — everything else is a drawn gesture. */
  function tap(target, opts = {}) {
    const node = resolve(target);
    if (!node) return Promise.resolve();
    const r = node.getBoundingClientRect();
    moveCursor(r.left + r.width * 0.42, r.top + r.height * 0.4);

    if (els.ripple) {
      els.ripple.classList.remove("is-on");
      void els.ripple.offsetWidth;    // restart the animation from its start
      els.ripple.classList.add("is-on");
    }
    node.classList.add("rec-tap");
    setTimeout(() => node.classList.remove("rec-tap"), 620);

    if (opts.real) node.click();
    return wait(opts.hold ?? 420);
  }

  /* ------------------------------------------------------------- spotlight */

  /** Dims the periphery so the eye has nowhere else to go.
   *
   *  Retargeting is deliberately two-phase: fade down, move the gradient
   *  while the veil is invisible, fade back up. A gradient's stops cannot be
   *  transitioned — moving them is paint work and would snap the whole
   *  vignette across the screen between two shots — so only opacity is ever
   *  animated and the reposition happens where nobody can see it. The move is
   *  deferred rather than awaited, so the shot's dwell is never held open
   *  waiting on a fade the viewer does not need to sit through. */
  function spotlight(node) {
    if (!els.veil || !node) return;
    const r = node.getBoundingClientRect();

    const place = () => {
      els.veil.style.setProperty("--spot-x", `${Math.round(r.left + r.width / 2)}px`);
      els.veil.style.setProperty("--spot-y", `${Math.round(r.top + r.height / 2)}px`);
      els.veil.style.setProperty("--spot-r", `${Math.round(Math.max(r.width, r.height) * 0.95)}px`);
      void els.veil.offsetWidth;
      els.veil.style.transition = "opacity 460ms ease";
      els.veil.style.opacity = "1";
    };

    // Only worth stepping down if something is actually lit. On the first
    // shot the veil is already dark, and a needless fade-out first would
    // show the periphery for a beat it has no business being seen in.
    if (parseFloat(els.veil.style.opacity || "0") > 0.05) {
      els.veil.style.transition = "opacity 240ms ease";
      els.veil.style.opacity = "0";
      setTimeout(place, 260);
    } else {
      place();
    }
  }

  function clearSpotlight() {
    if (!els.veil) return;
    els.veil.style.transition = "opacity 420ms ease";
    els.veil.style.opacity = "0";
  }

  /* --------------------------------------------------------------- drift */

  /** Ambient life for the stretches where nothing can be driven — chiefly the
   *  seconds a genuine diagnosis is in flight. The pointer wanders a slow
   *  figure-eight and the vignette breathes; neither touches the product. */
  function startWander() {
    if (!live.on) return;
    els.veil?.classList.add("is-drifting");
    const origin = { x: at.x, y: at.y };
    const start = performance.now();
    const step = (now) => {
      if (!live.on) return;
      const t = (now - start) / 1000;
      moveCursor(origin.x + Math.sin(t * 0.7) * 46, origin.y + Math.sin(t * 1.4) * 22);
      live.wander = requestAnimationFrame(step);
    };
    live.wander = requestAnimationFrame(step);
  }

  function stopWander() {
    cancelAnimationFrame(live.wander);
    live.wander = 0;
    els.veil?.classList.remove("is-drifting");
  }

  function wander(ms) {
    startWander();
    return wait(ms).then(stopWander);
  }

  /** The honest shape for a wait whose length nobody controls. Keeps the
   *  frame alive for exactly as long as the real thing takes and not a
   *  moment longer — a fixed wander would pad a fast answer and cut a slow
   *  one, and the whole point is that the recording keeps the real timing. */
  function wanderUntil(promise) {
    startWander();
    return Promise.resolve(promise).then(
      (value) => { stopWander(); return value; },
      (err) => { stopWander(); throw err; },
    );
  }

  /* ------------------------------------------------------------ transitions */

  /** A chapter change. The screens swap under cover of the wipe, so the hard
   *  cut between the landing page and the workspace never reaches the file.
   *  "sweep" slides a band across for a chapter that changes location;
   *  "dissolve" is for a change of state in the same place. */
  function wipe(kind, switchFn) {
    if (!els.wipe) return Promise.resolve();
    const el = els.wipe;
    el.className = kind === "sweep" ? "rec-wipe rec-wipe--sweep" : "rec-wipe";
    el.style.opacity = "0";
    void el.offsetWidth;
    el.style.transition = "opacity 300ms ease";
    el.style.opacity = "1";

    return wait(320)
      .then(() => {
        try { if (switchFn) switchFn(); } catch (err) {
          console.error("[hassad] chapter switch failed:", err);
        }
        return wait(150);
      })
      .then(() => {
        el.style.transition = "opacity 470ms ease";
        el.style.opacity = "0";
        return wait(490);
      });
  }

  /* --------------------------------------------------------------- public */

  /** One shot: bring the subject into frame, push in on it, glide the pointer
   *  onto it, and let the spotlight take over. Every beat in the timeline is
   *  built out of this and `tap`. */
  function focus(target, opts = {}) {
    const node = resolve(target);
    if (!node) return Promise.resolve();
    const zoom = opts.zoom ?? ZOOM_MID;
    const layers = layersFor(node);

    const ready = opts.overlay ? Promise.resolve() : scrollCenter(node);
    return ready.then(() => {
      if (!live.on) return null;
      // Captured before the move, because the rect below is still showing
      // this scale even though applyZoom has already committed to the next.
      const fromZoom = live.zoom;
      applyZoom(zoom, layers);
      const arrival = finalCenter(node, zoom, fromZoom);
      // Camera and pointer travel together: two sequential moves would read
      // as a slideshow, one concurrent move reads as a person.
      return Promise.all([glideTo(arrival), wait(ZOOM_MS)]);
    }).then(() => {
      if (!live.on) return null;
      if (opts.spot !== false) spotlight(node);
      // Fires once the frame has settled, before the dwell — the moment to
      // put a crisp edge on the subject or update the HUD, rather than
      // guessing at a delay that only matches one machine's animation.
      if (typeof opts.onLand === "function") {
        try { opts.onLand(node); } catch (err) { console.error("[hassad] shot hook failed:", err); }
      }
      return wait(opts.hold ?? HOLD_DEFAULT_MS);
    });
  }

  function begin() {
    if (live.on) return;
    mount();
    live.on = true;
    live.zoom = 1;
    styled = [];
    document.body.classList.add("is-recording");
    moveCursor(window.innerWidth * 0.7, window.innerHeight * 0.32);
  }

  function end() {
    live.on = false;
    cancelAnimationFrame(live.raf);
    stopWander();
    for (const layer of styled) {
      layer.style.transform = "";
      layer.style.transformOrigin = "";
      layer.style.transition = "";
    }
    styled = [];
    live.zoom = 1;
    document.body.classList.remove("is-recording");
    clearSpotlight();
    unmount();
  }

  return {
    begin,
    end,
    focus,
    tap,
    wipe,
    reset,
    wander,
    wanderUntil,
    clearSpotlight,
    ZOOM_WIDE,
    ZOOM_MID,
    ZOOM_TIGHT,
  };
}
