/* ============================================================================
 * recordfx.js — the presentation layer that sits OVER the demo recorder
 *
 * What this adds
 * --------------
 * A smooth gliding pointer that arrives at each control and ripples when it
 * "clicks", a vignette that dims everything except the thing being discussed,
 * chapter transitions, and the scrolling that carries the framing from one
 * subject to the next. It exists because a static screen recording of a working
 * product is still a dull three minutes, and engagement is a legitimate
 * production goal.
 *
 * What it explicitly does NOT do
 * ------------------------------
 * It never changes a single thing about what the product does. The recorder in
 * record.js still sends the operator's real photograph to the real model and
 * still films whatever comes back — including a slow answer and including a
 * failure. Everything here is a PRESENTATION layer painted on top of that
 * genuine behaviour, exactly like adding a pointer highlight in a screencast:
 *
 *   · the cursor is drawn, not recorded — the operator's real pointer is
 *     parked and plays no part;
 *   · `tap(node, { real: false })` shows a click ripple WITHOUT dispatching a
 *     click — used for the dropzone, where a real click would open a file
 *     picker and stall the automation. Only `real: true` activates anything.
 *
 * The distinction that keeps this honest is that nothing is ever *invented*:
 * no frame of the interface is redrawn, no verdict is authored, no state is
 * faked. If the diagnosis is unflattering, the spotlight makes it more
 * visible, not less.
 *
 * Why there is deliberately NO camera
 * ----------------------------------
 * An earlier version pushed in and out on each subject — a Ken Burns move done
 * as a CSS transform on the screen layers. It has been removed. The framing is
 * now fixed for the whole take: no scale, no transform-origin, no transform
 * written to any layer of the product at any point. The interface is shown at
 * its own size, and the only things that move are the pointer, the scroll and
 * the light.
 *
 * That is not a lesser result, it is a different and in several ways a better
 * one. A zoom crops: at 1.45x a viewer sees one card and loses the two beside
 * it, which is exactly the context a three-minute product tour is trying to
 * build. It also means the recording is a faithful record of the product's own
 * layout rather than of a presentation of it.
 *
 * Everything animated here is a transform or an opacity — compositor work that
 * does not re-run layout or paint, which is what keeps a 60fps capture at 60fps.
 * The only transform in this file is `translate3d` on the pointer, and the only
 * element it is ever applied to is this module's own overlay.
 *
 * Nothing here is verifiable without a browser: it is checked by static suites
 * and by reading it, not by looking at a rendered frame.
 * ========================================================================== */

/** Fixed-position layers. Those are pinned to the viewport by definition, so
 *  scrolling the window under them would move the page for nothing. */
const FIXED = ["#su-modal", "#plan-modal", "#chat-modal", "#auth-modal", ".toast"];

const MOVE_MS = 900;         // how long a scroll is allowed to settle
const HOLD_DEFAULT_MS = 1600; // dwell on a shot before moving on

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
 *  chain. offsetTop is a layout quantity, so nothing this module animates can
 *  ever move the number the scroll is aimed at. */
function docTop(node) {
  let y = 0;
  let cursor = node;
  while (cursor) {
    y += cursor.offsetTop;
    cursor = cursor.offsetParent;
  }
  return y;
}

/** True when the node lives inside a fixed layer — a dialog, mostly. */
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

export function initPresentation() {
  const live = { on: false, raf: 0, wander: 0 };
  const at = { x: 0, y: 0 };          // where the drawn pointer currently is
  const els = {};
  let hovered = null;                 // the control the drawn pointer rests on

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

  /* ------------------------------------------------------------------ scroll */

  /** Centres the element in the viewport, then waits for the browser's own
   *  smooth scroll to finish. `scrollend` is the right signal where it exists;
   *  elsewhere a bounded wait is the honest fallback rather than a guess that
   *  is right on one machine. This is the ONLY thing that reframes a shot —
   *  there is no scale, no pan and no transform anywhere in this module. */
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
      const timer = setTimeout(done, MOVE_MS);
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

  /** Where to put the pointer on a subject. A little up and left of the
   *  centre, which is where a hand actually lands on a button rather than on
   *  its mathematical middle. */
  function aim(node) {
    const r = node.getBoundingClientRect();
    return { x: r.left + r.width * 0.42, y: r.top + r.height * 0.4 };
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
    const spot = aim(node);
    moveCursor(spot.x, spot.y);
    restOn(node);

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

  /** Rests the pointer on a control and announces it the way a real pointer
   *  would. The DOM mouse events are dispatched for real, so any hover
   *  handler in the product runs exactly as it would under a farmer's hand.
   *
   *  The CSS `:hover` pseudo-class is a separate matter: browsers apply it
   *  only to genuine pointer input and it cannot be faked from script, by any
   *  technique, in any engine. Rather than copy every hover rule in the
   *  stylesheet into this module — a duplicate that would silently rot the
   *  first time a colour changes — the rest state is one class this module
   *  owns, styled once, sitting a step quieter than the focus outline. */
  function restOn(node) {
    if (hovered && hovered !== node) hovered.classList.remove("rec-hover");
    hovered = node || null;
    if (!hovered) return;
    hovered.classList.add("rec-hover");
    const spot = aim(hovered);
    hovered.dispatchEvent(new MouseEvent("mouseover", {
      bubbles: true, clientX: spot.x, clientY: spot.y,
    }));
    hovered.dispatchEvent(new MouseEvent("mouseenter", {
      bubbles: false, clientX: spot.x, clientY: spot.y,
    }));
  }

  /** Dims the periphery so the eye has nowhere else to go. With the framing
   *  fixed this is now the ONLY emphasis mechanism, which is why it matters
   *  that it targets precisely: it is what tells a viewer which of three
   *  similar cards is being discussed, in place of the crop a zoom used to do.
   *
   *  Retargeting is deliberately two-phase: fade down, move the gradient
   *  while the veil is invisible, fade back up. A gradient's stops cannot be
   *  transitioned — moving them is paint work and would snap the whole
   *  vignette across the screen between two shots — so only opacity is ever
   *  animated and the reposition happens where nobody can see it. */
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
   *  figure-eight and the vignette breathes; neither touches the product.
   *  With no camera to move, this is what keeps a long wait from reading as a
   *  frozen frame. */
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

  /** One shot: bring the subject into frame with the page's own scrolling,
   *  glide the pointer onto it, and let the spotlight take over. Every beat in
   *  the timeline is built out of this and `tap`. Nothing here touches the
   *  product's geometry — the framing it hands back is the framing the page
   *  already has. */
  function focus(target, opts = {}) {
    const node = resolve(target);
    if (!node) return Promise.resolve();

    // A dialog is already pinned to the centre of the viewport; scrolling the
    // page underneath it would only swing the backdrop around behind it.
    const ready = inFixedLayer(node) ? Promise.resolve() : scrollCenter(node);
    return ready.then(() => {
      if (!live.on) return null;
      return glideTo(aim(node));
    }).then(() => {
      if (!live.on) return null;
      if (opts.spot !== false) spotlight(node);
      // The pointer has arrived, so the control is now hovered — the DOM
      // events fire for real and the rest state lights up.
      restOn(node);
      // Fires once the pointer has landed, before the dwell — the moment to
      // put a crisp edge on the subject rather than guessing at a delay that
      // only matches one machine's animation.
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
    document.body.classList.add("is-recording");
    moveCursor(window.innerWidth * 0.7, window.innerHeight * 0.32);
  }

  function end() {
    live.on = false;
    cancelAnimationFrame(live.raf);
    stopWander();
    restOn(null);
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
    wander,
    wanderUntil,
    clearSpotlight,
  };
}
