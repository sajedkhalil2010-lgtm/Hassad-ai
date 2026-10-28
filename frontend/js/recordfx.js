/* =========================================================================
   Presentation layer for the demo recorder.

   Nothing here touches product data. It draws what a camera operator would
   otherwise have to do by hand: the pointer, a reading spotlight, the lower
   third, the chapter card, the closing card and the fade between them. The
   engine in record.js decides WHAT happens and WHEN; this file only knows
   how it looks.

   Everything is `pointer-events: none` except the interaction lock, which is
   the one element that must swallow clicks while a take is running. Synthetic
   `.click()` calls still work underneath it, which is exactly the split we
   want: the film drives the product, the viewer does not.
   ========================================================================= */

import { el } from "./dom.js";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const SVG_NS = "http://www.w3.org/2000/svg";

const fx = {
  root: null,
  ptr: null,
  ring: null,
  spot: null,
  cap: null,
  capKick: null,
  capLine: null,
  title: null,
  titleKick: null,
  titleText: null,
  outro: null,
  fade: null,
  x: 0,
  y: 0,
};

/* ------------------------------------------------------------- lifecycle -- */

export function mount() {
  if (fx.root) return;

  const root = el("div", "recfx");
  // Decoration, not content: the words a viewer needs are already on screen.
  root.setAttribute("aria-hidden", "true");

  const lock = el("div", "recfx__lock");
  const spot = el("div", "recfx__spot");

  const cap = el("div", "recfx__cap");
  const capKick = el("span", "recfx__kick");
  const capLine = el("span", "recfx__line");
  cap.append(capKick, capLine);

  const title = el("div", "recfx__title");
  const titleKick = el("span", "recfx__tk");
  const titleText = el("span", "recfx__tt");
  title.append(titleKick, titleText);

  const outro = el("div", "recfx__outro");
  outro.append(
    el("p", "recfx__brand", "Hassad AI"),
    el("p", "recfx__ol", "Crop diagnosis for every farmer."),
    el("p", "recfx__repo", "github.com/sajedkhalil2010-lgtm/Hassad-ai"),
  );

  const fade = el("div", "recfx__fade");

  const ring = el("div", "recfx__ring");
  const ptr = el("div", "recfx__ptr");
  const art = document.createElementNS(SVG_NS, "svg");
  art.setAttribute("viewBox", "0 0 24 24");
  art.setAttribute("aria-hidden", "true");
  art.classList.add("recfx__art");
  const shape = document.createElementNS(SVG_NS, "path");
  shape.setAttribute("d", "M5.6 3.4 18.8 12.2l-5.6 1.1 3.1 5.7-2.7 1.4-3.1-5.7-4.6 3.5Z");
  art.append(shape);
  ptr.append(art);

  root.append(lock, spot, cap, title, outro, fade, ring, ptr);
  document.body.append(root);

  fx.root = root;
  fx.ptr = ptr;
  fx.ring = ring;
  fx.spot = spot;
  fx.cap = cap;
  fx.capKick = capKick;
  fx.capLine = capLine;
  fx.title = title;
  fx.titleKick = titleKick;
  fx.titleText = titleText;
  fx.outro = outro;
  fx.fade = fade;

  // The real OS pointer must not wander into the take, and a stray drag must
  // not select half the interface while it is being filmed.
  document.documentElement.classList.add("is-filming");

  write(Math.round(window.innerWidth * 0.56), Math.round(window.innerHeight * 0.84));
}

export function unmount() {
  fx.root?.remove();
  fx.root = null;
  fx.ptr = null;
  fx.ring = null;
  fx.spot = null;
  fx.cap = null;
  fx.title = null;
  fx.outro = null;
  fx.fade = null;
  document.documentElement.classList.remove("is-filming");
}

/* ---------------------------------------------------------------- cursor -- */

function write(x, y) {
  if (!fx.ptr || !fx.ring) return;
  fx.x = x;
  fx.y = y;
  const at = `translate3d(${x}px, ${y}px, 0)`;
  fx.ptr.style.transform = at;
  fx.ring.style.transform = at;
}

function aim(node) {
  const box = node.getBoundingClientRect();
  return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
}

/** One transform write per frame along a shallow arc, eased in and out.
 *  A pointer that travels in a straight line at constant speed reads as
 *  robotic; the arc and the easing are what make it look handled. */
function glideTo(point, ms) {
  if (!fx.root) return Promise.resolve();

  const from = { x: fx.x, y: fx.y };
  const dx = point.x - from.x;
  const dy = point.y - from.y;
  const distance = Math.hypot(dx, dy);
  const dur = ms || Math.max(480, Math.min(1050, distance * 0.52));
  const ctrl = {
    x: from.x + dx * 0.5 - dy * 0.13,
    y: from.y + dy * 0.5 + dx * 0.13,
  };

  return new Promise((resolve) => {
    const started = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - started) / dur);
      const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      const u = 1 - e;
      write(
        u * u * from.x + 2 * u * e * ctrl.x + e * e * point.x,
        u * u * from.y + 2 * u * e * ctrl.y + e * e * point.y,
      );
      if (p < 1) requestAnimationFrame(step);
      else resolve();
    };
    requestAnimationFrame(step);
  });
}

function ping() {
  if (!fx.root) return;
  const node = el("span", "recfx__ping");
  node.style.left = `${fx.x}px`;
  node.style.top = `${fx.y}px`;
  fx.root.append(node);
  setTimeout(() => node.remove(), 900);
}

async function bringIntoView(node) {
  const box = node.getBoundingClientRect();
  if (box.top >= 96 && box.bottom <= window.innerHeight - 96) return;

  // A short hop animates; a jump across half the page cuts. Film time is
  // fixed, and a smooth scroll that is still travelling when the pointer
  // arrives leaves it aiming at nothing.
  const far = Math.abs(box.top) > window.innerHeight * 1.6;
  node.scrollIntoView({ block: "center", behavior: far ? "auto" : "smooth" });
  await wait(far ? 140 : 650);
}

/** Glide, press, release. `real: false` keeps the press cosmetic — used where
 *  a genuine click would open a file dialog or call a live model. */
export async function tap(node, options) {
  const opts = options || {};
  if (!fx.root) return;
  if (!node) {
    console.warn("[hassad] a presentation cue found no target to press.");
    return;
  }

  await bringIntoView(node);
  await glideTo(aim(node), opts.ms);
  ping();

  fx.ring.classList.add("is-down");
  if (node.classList) node.classList.add("is-pressed");
  await wait(140);
  fx.ring.classList.remove("is-down");
  if (node.classList) node.classList.remove("is-pressed");

  if (opts.real !== false) node.click();
  await wait(opts.settle == null ? 280 : opts.settle);
}

/* ------------------------------------------------------ spotlight, cards -- */

function spotOn(node) {
  if (!node || !fx.spot) return;
  const pad = 12;
  const box = node.getBoundingClientRect();
  fx.spot.style.left = `${box.left - pad}px`;
  fx.spot.style.top = `${box.top - pad}px`;
  fx.spot.style.width = `${box.width + pad * 2}px`;
  fx.spot.style.height = `${box.height + pad * 2}px`;
  fx.spot.classList.add("is-on");
}

export function spotOff() {
  fx.spot?.classList.remove("is-on");
}

/** Scroll a target into a comfortable position, then light it up. Every step
 *  that points at something uses this, so nothing is ever highlighted while
 *  it sits under the top bar or below the fold. */
export async function reveal(node, options) {
  const opts = options || {};
  if (!fx.root) return false;
  if (!node) {
    console.warn("[hassad] a presentation cue found no target to show.");
    return false;
  }

  if (opts.scroll !== false) {
    const box = node.getBoundingClientRect();
    if (box.top < 96 || box.bottom > window.innerHeight - 96) {
      const far = Math.abs(box.top) > window.innerHeight * 1.6;
      node.scrollIntoView({ block: "center", behavior: far ? "auto" : "smooth" });
      await wait(far ? 140 : 650);
    }
  }
  spotOn(node);
  return true;
}

/** The lower third. Passing an empty line clears it. */
export function caption(kick, line) {
  if (!fx.cap) return;
  if (fx.capKick) fx.capKick.textContent = kick || "";
  if (fx.capLine) fx.capLine.textContent = line || "";
  fx.cap.classList.toggle("is-on", Boolean(line));
}

/** A full-width chapter card, held long enough to read and then faded. */
export async function titleCard(kick, text, hold) {
  if (!fx.title) return;
  if (fx.titleKick) fx.titleKick.textContent = kick || "";
  if (fx.titleText) fx.titleText.textContent = text || "";
  fx.title.classList.add("is-on");
  await wait(hold == null ? 2400 : hold);
  fx.title.classList.remove("is-on");
  await wait(460);
}

export function outro() {
  fx.outro?.classList.add("is-on");
}

export async function fadeTo(black, ms) {
  if (!fx.fade) return;
  fx.fade.classList.toggle("is-black", Boolean(black));
  await wait(ms == null ? 600 : ms);
}

export function hideCursor() {
  fx.root?.classList.add("is-hidden");
}

/* ------------------------------------------------------------- countdown -- */

/** Runs BEFORE the recorder rolls, so the countdown never reaches the file. */
export async function countdown(seconds, hintText) {
  if (!fx.root) return;

  const box = el("div", "recfx__count");
  const num = el("span", "recfx__num", String(seconds));
  const hint = el("span", "recfx__hint", hintText || "");
  box.append(num, hint);
  fx.root.append(box);

  let left = seconds;
  while (left > 0) {
    num.textContent = String(left);
    await wait(1000);
    left -= 1;
  }
  box.remove();
}
