/* =====================================================================
   ui.js — shared UI utilities: toasts, modals, gauge, markdown-lite
   ===================================================================== */

import { icon } from "./icons.js";
import { t, tAt } from "./i18n.js";

/* ---------- DOM helpers -------------------------------------------- */
export function $(sel, root) {
  return (root || document).querySelector(sel);
}
export function $$(sel, root) {
  return Array.prototype.slice.call((root || document).querySelectorAll(sel));
}

export function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function el(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html.trim();
  return tpl.content.firstElementChild;
}

/* ---------- toasts -------------------------------------------------- */
let toastSeq = 0;

/**
 * Show a floating toast.
 * @param {string} msg
 * @param {"ok"|"err"|"info"} type
 * @param {string} [title]
 */
export function toast(msg, type, title) {
  const root = document.getElementById("toast-root");
  if (!root) return;
  const kind = type || "ok";
  const ic = kind === "ok" ? "checkCircle" : kind === "err" ? "xCircle" : "info";
  const id = "t" + ++toastSeq;
  const node = el(
    '<div class="toast ' + kind + '" id="' + id + '" role="status">' +
      '<span class="t-ico">' + icon(ic) + "</span>" +
      "<span>" +
        (title ? "<b>" + esc(title) + "</b>" : "") +
        "<span>" + esc(msg) + "</span>" +
      "</span>" +
      '<button class="t-x" aria-label="' + esc(t("common.close")) + '">' + icon("x", "sm") + "</button>" +
      "</div>"
  );
  root.appendChild(node);

  const remove = function () {
    if (!node.parentNode) return;
    node.classList.add("out");
    setTimeout(function () {
      if (node.parentNode) node.parentNode.removeChild(node);
    }, 340);
  };
  const timer = setTimeout(remove, kind === "err" ? 6000 : 4200);
  node.querySelector(".t-x").addEventListener("click", function () {
    clearTimeout(timer);
    remove();
  });

  // keep the stack small
  while (root.children.length > 4) root.removeChild(root.firstChild);
}

/* ---------- modal --------------------------------------------------- */
let modalPrevFocus = null;
let activeModal = null;

/**
 * Open a modal. content may be an HTML string or a Node.
 * @param {{html?:string, node?:Node, size?:"sm"|"lg", onClose?:Function}} opts
 * @returns {{root:Element, close:Function}}
 */
export function openModal(opts) {
  closeModal(); // single modal at a time — runs the previous modal's close()
  modalPrevFocus = document.activeElement;
  const root = document.getElementById("modal-root");
  const size = opts.size ? " " + opts.size : "";
  const wrap = el(
    '<div class="overlay" role="dialog" aria-modal="true">' +
      '<div class="modal' + size + '">' +
        (opts.node ? "" : opts.html || "") +
      "</div>" +
    "</div>"
  );
  const modal = wrap.querySelector(".modal");
  if (opts.node) modal.appendChild(opts.node);
  root.appendChild(wrap);

  const close = function () {
    /* always detach the Escape listener — even if the node was already
       removed — so repeated opens can never accumulate handlers */
    document.removeEventListener("keydown", onKey);
    if (activeModal && activeModal.close === close) activeModal = null;
    const wasConnected = !!wrap.parentNode;
    if (wasConnected) wrap.parentNode.removeChild(wrap);
    if (!wasConnected) return; /* close() ran before — don't double-fire */
    if (opts.onClose) opts.onClose();
    if (modalPrevFocus && modalPrevFocus.focus) {
      try {
        modalPrevFocus.focus();
      } catch (e) { /* ignore */ }
    }
  };
  const onKey = function (e) {
    if (e.key === "Escape") close();
  };
  document.addEventListener("keydown", onKey);
  wrap.addEventListener("mousedown", function (e) {
    if (e.target === wrap) close();
  });

  activeModal = { close: close };
  return { root: modal, wrap: wrap, close: close };
}

export function closeModal() {
  /* go through the active modal's own close() so Escape listeners,
     onClose hooks and focus restore all run properly */
  if (activeModal) {
    const c = activeModal.close;
    activeModal = null;
    c();
    return;
  }
  const root = document.getElementById("modal-root");
  if (root) root.innerHTML = "";
}

/** Native-feeling confirm dialog. Resolves true/false. */
export function confirmDialog(title, subtitle, danger) {
  return new Promise(function (resolve) {
    const node = el(
      '<div class="center">' +
        '<div class="state-card" style="padding:26px 18px 8px">' +
          '<div class="st-ic ' + (danger ? "bad" : "warn") + '" style="width:62px;height:62px">' +
            icon(danger ? "trash" : "alert") +
          "</div>" +
          "<h3>" + esc(title) + "</h3>" +
          "<p>" + esc(subtitle || "") + "</p>" +
        "</div>" +
        '<div class="modal-foot" style="padding-top:14px">' +
          '<button class="btn btn-ghost" data-act="no">' + esc(t("common.cancel")) + "</button>" +
          '<button class="btn ' + (danger ? "btn-danger" : "btn-primary") + '" data-act="yes">' +
            esc(t("common.confirm")) +
          "</button>" +
        "</div>" +
      "</div>"
    );
    const m = openModal({ node: node, size: "sm" });
    node.querySelector('[data-act="no"]').addEventListener("click", function () {
      m.close();
      resolve(false);
    });
    node.querySelector('[data-act="yes"]').addEventListener("click", function () {
      m.close();
      resolve(true);
    });
  });
}

/* ---------- confidence gauge (SVG radial) --------------------------- */
const GAUGE_R = 40;
const GAUGE_C = 2 * Math.PI * GAUGE_R;

/**
 * Build a radial gauge node. `value` in 0..1.
 * Returns {node, setValue}.
 */
export function gauge(value, labelText) {
  const v = Math.max(0, Math.min(1, value || 0));
  const node = el(
    '<div class="gauge" role="img" aria-label="' + esc(t("res.confidence")) + '">' +
      '<svg width="92" height="92" viewBox="0 0 92 92">' +
        "<defs>" +
          '<linearGradient id="gaugeGrad" x1="0" y1="0" x2="1" y2="1">' +
            '<stop offset="0" stop-color="#10b981"/>' +
            '<stop offset="1" stop-color="#a3e635"/>' +
          "</linearGradient>" +
        "</defs>" +
        '<circle class="g-track" cx="46" cy="46" r="' + GAUGE_R + '"/>' +
        '<circle class="g-val" cx="46" cy="46" r="' + GAUGE_R + '" stroke-dasharray="' +
          GAUGE_C.toFixed(1) + '" stroke-dashoffset="' + GAUGE_C.toFixed(1) + '"/>' +
      "</svg>" +
      '<div class="g-mid"><span class="g-num num">0%</span>' +
      '<span class="g-lbl">' + esc(labelText || t("res.confidence")) + "</span></div>" +
    "</div>"
  );

  const ring = node.querySelector(".g-val");
  const num = node.querySelector(".g-num");
  let raf = null;

  function setValue(target) {
    const to = Math.max(0, Math.min(1, target || 0));
    if (raf) cancelAnimationFrame(raf);
    // animate number + ring together
    const start = performance.now();
    const dur = 1100;
    const from = 0;
    const step = function (now) {
      const p = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      const cur = from + (to - from) * eased;
      ring.style.strokeDashoffset = String(GAUGE_C * (1 - cur));
      num.textContent = Math.round(cur * 100) + "%";
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
  }

  return { node: node, setValue: setValue };
}

/* ---------- markdown-lite (bold / lists / paragraphs) --------------- */
export function mdLite(src) {
  if (!src) return "";
  const lines = String(src).split(/\r?\n/);
  let html = "";
  let inUl = false;
  let inOl = false;

  const closeLists = function () {
    if (inUl) { html += "</ul>"; inUl = false; }
    if (inOl) { html += "</ol>"; inOl = false; }
  };

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const line = esc(raw.trim());
    if (!line) {
      closeLists();
      continue;
    }
    let inline = line
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_]+)__/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    const ulM = /^[-•*]\s+(.*)$/.exec(inline);
    const olM = /^(\d+)[.)]\s+(.*)$/.exec(inline);
    if (ulM) {
      if (!inUl) { closeLists(); html += "<ul>"; inUl = true; }
      html += "<li>" + ulM[1] + "</li>";
    } else if (olM) {
      if (!inOl) { closeLists(); html += "<ol>"; inOl = true; }
      html += "<li>" + olM[2] + "</li>";
    } else {
      closeLists();
      html += "<p>" + inline + "</p>";
    }
  }
  closeLists();
  return html;
}

/* ---------- status helpers ----------------------------------------- */
const STATUS_META = {
  confirmed: { cls: "b-green", ic: "checkCircle" },
  in_review: { cls: "b-amber", ic: "clock" },
  retake_required: { cls: "b-red", ic: "camera" },
  queued: { cls: "b-blue", ic: "clock" },
  awaiting_crop_selection: { cls: "b-lime", ic: "sprout" },
  rejected: { cls: "b-red", ic: "xCircle" },
};

export function statusBadge(status) {
  const meta = STATUS_META[status] || { cls: "b-slate", ic: "info" };
  const labelKey = "res.status." + status;
  const label = t(labelKey);
  return (
    '<span class="badge ' + meta.cls + '">' +
    icon(meta.ic, "sm") +
    esc(label === labelKey ? status : label) +
    "</span>"
  );
}

export function statusHeadClass(status) {
  if (status === "confirmed") return "ok";
  if (status === "in_review" || status === "awaiting_crop_selection" || status === "queued") return "warn";
  if (status === "retake_required" || status === "rejected") return "bad";
  return "ok";
}

/* ---------- misc ---------------------------------------------------- */
export function initials(name) {
  if (!name) return "؟";
  const parts = String(name).trim().split(/\s+/);
  const a = parts[0] ? parts[0][0] : "";
  const b = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (a + b).toUpperCase();
}

export function percent(x) {
  if (x === null || x === undefined) return "—";
  return Math.round(x * 100) + "%";
}

/** Disable a button + swap label for the duration of `fn`. */
export async function busy(btn, label, fn) {
  if (btn) {
    btn.disabled = true;
    btn.dataset.oldHtml = btn.innerHTML;
    btn.innerHTML = icon("loader", "sm spin") + "<span>" + esc(label || t("common.loading")) + "</span>";
  }
  try {
    return await fn();
  } finally {
    if (btn && btn.isConnected) {
      btn.disabled = false;
      if (btn.dataset.oldHtml) btn.innerHTML = btn.dataset.oldHtml;
    }
  }
}

/** Common error → toast mapper. */
export function errToast(e) {
  let msg = "";
  if (e && e.message) msg = e.message;
  if (e && e.status === 0) {
    /* a client-side timeout deserves its own calm message; else network */
    msg = e && e.code === "timeout" ? t("err.timeout") : t("err.network");
  }
  toast(msg || t("common.error"), "err");
}

export { t, tAt };
