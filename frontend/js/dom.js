/* =========================================================================
   DOM helpers.

   el() is the only way this UI creates an element that holds dynamic text.
   It sets textContent, so a model reply, an account name or a filename can
   never be parsed as markup — the XSS surface stays closed by construction
   rather than by remembering to escape each time.
   ========================================================================= */

/**
 * @param {string} tag
 * @param {string} [className]
 * @param {string|number} [text]
 */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/** Same as el(), but tagged and directed for Arabic copy. */
export function ar(className, text) {
  const node = el("div", className, text);
  node.lang = "ar";
  node.dir = "rtl";
  return node;
}

/** Empty a container in place. Returns it for chaining. */
export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function show(node, visible) {
  node.classList.toggle("hidden", !visible);
  return node;
}

export const $ = (id) => document.getElementById(id);

/** Safe number formatting: never renders NaN to a farmer's screen. */
export function pct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n * 100)));
}

/** Human file size. */
export function bytes(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
