/* =====================================================================
   chat.js — floating agronomy assistant (side panel, SSE streaming)
   ===================================================================== */

import api from "./api.js";
import { icon } from "./icons.js";
import { t, getLang, fmtTime } from "./i18n.js";
import { $, $$, el, esc, toast, mdLite, errToast, initials } from "./ui.js";
import { getProfile } from "./profile.js";

const SKEY = "hassad.chat.session";

let ctx = null;
let panel = null;
let backdrop = null;
let fab = null;
let msgsHost = null;
let input = null;
let sendBtn = null;
let suggHost = null;
let sessionId = null;
let busyStream = false;
let sending = false;
let offline = false;
let recovering = false;
let abortCtl = null;
let opened = false;

function storedSession() {
  try {
    return localStorage.getItem(SKEY);
  } catch (e) {
    return null;
  }
}
function saveSession(id) {
  try {
    if (id) localStorage.setItem(SKEY, id);
    else localStorage.removeItem(SKEY);
  } catch (e) { /* ignore */ }
}

export function initChat(context) {
  ctx = context;
  if (panel) return;

  backdrop = el('<div class="chat-backdrop"></div>');

  fab = el(
    '<button class="chat-fab hide" aria-label="' + esc(t("chat.title")) + '">' + icon("message") + "</button>"
  );

  panel = el(
    '<aside class="chat-panel" aria-label="' + esc(t("chat.title")) + '">' +
      '<div class="chat-head">' +
        '<span class="ch-ava">' + icon("sparkles") + "</span>" +
        '<div class="grow"><div class="ch-t">' + esc(t("chat.title")) +
          '<span class="badge b-lime" data-mode>' + esc(t("chat.mode.ai")) + "</span></div>" +
          '<div class="ch-s"><span class="d"></span>' + esc(t("chat.sub")) + "</div>" +
        "</div>" +
        '<button class="icon-btn" data-new title="' + esc(t("chat.new")) + '">' + icon("plus", "sm") + "</button>" +
        '<button class="icon-btn" data-close title="' + esc(t("common.close")) + '">' + icon("x", "sm") + "</button>" +
      "</div>" +
      '<div class="chat-msgs" id="chatMsgs"></div>' +
      '<div class="chat-sugg" id="chatSugg"></div>' +
      '<div class="chat-input">' +
        '<div class="ci-box">' +
          '<textarea rows="1" id="chatInput" placeholder="' + esc(t("chat.placeholder")) + '"></textarea>' +
          '<button class="send-btn" id="chatSend" disabled aria-label="' + esc(t("chat.send")) + '">' + icon("send", "sm") + "</button>" +
        "</div>" +
        '<div class="ci-hint">' + esc(t("chat.hint")) + "</div>" +
      "</div>" +
    "</aside>"
  );

  document.body.appendChild(backdrop);
  document.body.appendChild(panel);
  document.body.appendChild(fab);

  msgsHost = panel.querySelector("#chatMsgs");
  input = panel.querySelector("#chatInput");
  sendBtn = panel.querySelector("#chatSend");
  suggHost = panel.querySelector("#chatSugg");

  fab.addEventListener("click", function () { toggleChat(); });
  backdrop.addEventListener("click", closeChat);
  panel.querySelector("[data-close]").addEventListener("click", closeChat);
  panel.querySelector("[data-new]").addEventListener("click", newConversation);

  input.addEventListener("input", autosize);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.classList.contains("stop")) send();
      else stopStream();
    }
  });
  sendBtn.addEventListener("click", function () {
    if (sendBtn.classList.contains("stop")) stopStream();
    else send();
  });

  paintSuggestions();
}

function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(130, input.scrollHeight) + "px";
  sendBtn.disabled = !busyStream && !input.value.trim();
}

function paintSuggestions() {
  const keys = ["chat.sugg1", "chat.sugg2", "chat.sugg3", "chat.sugg4"];
  suggHost.innerHTML = "";
  keys.forEach(function (k) {
    const chip = el('<button class="sugg-chip">' + esc(t(k)) + "</button>");
    chip.addEventListener("click", function () {
      input.value = t(k);
      autosize();
      send();
    });
    suggHost.appendChild(chip);
  });
}

/* ---------- open / close ------------------------------------------- */
export function openChat(prefill) {
  if (!panel) return;
  opened = true;
  panel.classList.add("open");
  backdrop.classList.add("open");
  fab.classList.add("hide");
  ensureSession();
  if (prefill) {
    input.value = prefill;
    autosize();
    setTimeout(function () { input.focus(); }, 340);
  } else {
    setTimeout(function () { input.focus(); }, 340);
  }
}
export function closeChat() {
  opened = false;
  if (!panel) return;
  panel.classList.remove("open");
  if (backdrop) backdrop.classList.remove("open");
  if (ctx && ctx.user && fab) fab.classList.remove("hide");
}
export function toggleChat() {
  if (opened) closeChat();
  else openChat();
}
export function setChatVisible(v) {
  if (!fab) return;
  /* close first — closeChat may re-show the FAB for logged-in users,
     so the visibility toggle must be the LAST word */
  if (!v) closeChat();
  fab.classList.toggle("hide", !v);
}

/* ---------- session ------------------------------------------------- */
const LOCAL_PREFIX = "local-";
const SESSION_TIMEOUT_MS = 8000;

/** Reject if a session call hangs longer than the budget (never freeze the UI). */
function withTimeout(promise, ms) {
  return new Promise(function (resolve, reject) {
    const tmr = setTimeout(function () {
      const err = new Error("timeout");
      err.status = 0;
      reject(err);
    }, ms);
    promise.then(
      function (v) { clearTimeout(tmr); resolve(v); },
      function (e) { clearTimeout(tmr); reject(e); }
    );
  });
}

function isLocalSession() {
  return !!sessionId && sessionId.indexOf(LOCAL_PREFIX) === 0;
}

/** Device-generated unique id per record (the API dedups on (user, client_id)). */
function newClientId() {
  return "web-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}

function setMode(kind) {
  const badge = panel && panel.querySelector("[data-mode]");
  if (badge) badge.textContent = kind === "local" ? t("chat.mode.local") : t("chat.mode.ai");
}

/** Seamless offline fallback: switch to an in-memory local session,
 *  no toast, no dead end — the chat stays fully usable. */
function newLocalSession() {
  offline = true;
  sessionId = LOCAL_PREFIX + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  saveSession(null); /* local ids are never persisted */
  setMode("local");
  renderHistory([]);
}

/** Quietly try to bring the chat back online (no toasts, no UI jumps). */
function recoverOnline() {
  if (recovering || !isLocalSession()) return;
  recovering = true;
  withTimeout(
    api.createSession({
      title: "New conversation",
      locale: getLang() === "ar" ? "ar" : "en",
      client_id: newClientId(),
    }),
    SESSION_TIMEOUT_MS
  ).then(
    function (s) {
      offline = false;
      sessionId = s.id;
      saveSession(sessionId);
      setMode("ai");
      /* on-screen local messages stay; only future sends use the AI */
    },
    function () { /* still offline — stay quiet */ }
  ).then(function () { recovering = false; });
}

async function ensureSession() {
  /* already have a usable session (local sessions try to recover quietly) */
  if (sessionId && !isLocalSession()) return;
  if (isLocalSession()) { recoverOnline(); return; }

  const stored = storedSession();
  if (stored) {
    sessionId = stored;
    try {
      const msgs = await withTimeout(api.messages(sessionId), SESSION_TIMEOUT_MS);
      offline = false;
      setMode("ai");
      renderHistory(msgs);
      return;
    } catch (e) {
      sessionId = null;
      saveSession(null);
    }
  }
  try {
    const s = await withTimeout(
      api.createSession({
        title: "New conversation",
        locale: getLang() === "ar" ? "ar" : "en",
        client_id: newClientId(),
      }),
      SESSION_TIMEOUT_MS
    );
    offline = false;
    sessionId = s.id;
    setMode("ai");
    saveSession(sessionId);
    renderHistory([]);
  } catch (e) {
    /* no error toast — fall back to a local offline session instantly */
    newLocalSession();
  }
}

function newConversation() {
  /* drop any in-flight answer so it can't leak into the new thread */
  if (busyStream && abortCtl) abortCtl.abort();
  saveSession(null);
  sessionId = null;
  msgsHost.innerHTML = "";
  paintSuggestions();
  greeting();
  ensureSession();
}

function greeting() {
  const profile = getProfile();
  const hello =
    esc(t("chat.greeting")) +
    (profile && profile.name ? " <b class=\"greet-name\">" + esc(profile.name) + "</b>" : "");
  const g = el(
    '<div class="msg"><span class="m-ava">' + icon("sparkles") + "</span>" +
      '<div class="bubble">' + hello + "</div></div>"
  );
  msgsHost.appendChild(g);
  scrollBottom();
}

function renderHistory(msgs) {
  msgsHost.innerHTML = "";
  if (!msgs || !msgs.length) {
    greeting();
    suggHost.style.display = "";
    return;
  }
  suggHost.style.display = "none";
  msgs.forEach(function (m) { appendMsg(m.role, m.content, m.citations, m.created_at); });
  scrollBottom();
}

function scrollBottom() {
  msgsHost.scrollTop = msgsHost.scrollHeight;
}

/* ---------- message rendering -------------------------------------- */
function appendMsg(role, text, citations, time) {
  const node = el(
    '<div class="msg ' + (role === "user" ? "user" : "") + '">' +
      '<span class="m-ava">' + icon(role === "user" ? "user" : "sparkles", "sm") + "</span>" +
      "<div class=\"grow\"><div class=\"bubble\"></div>" +
      '<div class="m-time">' + esc(time ? fmtTime(time) : "") + "</div></div>" +
    "</div>"
  );
  const bubble = node.querySelector(".bubble");
  if (role === "user") bubble.textContent = text;
  else bubble.innerHTML = mdLite(text);
  if (citations && citations.length) bubble.appendChild(citesNode(citations));
  msgsHost.appendChild(node);
  return node;
}

function citesNode(citations) {
  const wrap = el('<div class="cites"></div>');
  citations.forEach(function (c) {
    const chip = el(
      '<span class="cite-chip" title="' + esc(c.snippet || c.title || "") + '">' +
        icon("book", "sm") + "<span>[" + esc(c.index) + "] " + esc(c.title) + "</span></span>"
    );
    if (c.source_url) {
      chip.style.cursor = "pointer";
      chip.addEventListener("click", function () {
        window.open(c.source_url, "_blank", "noopener");
      });
    }
    wrap.appendChild(chip);
  });
  return wrap;
}

function scrollEase() {
  /* keep pinned to bottom while streaming */
  const near = msgsHost.scrollHeight - msgsHost.scrollTop - msgsHost.clientHeight < 140;
  if (near) msgsHost.scrollTop = msgsHost.scrollHeight;
}

/* ---------- sending ------------------------------------------------- */
async function send() {
  const text = input.value.trim();
  if (!text || busyStream || sending) return;
  if (!ctx || !ctx.user) {
    toast(t("chat.needLogin"), "err");
    if (ctx) ctx.openAuth("login");
    return;
  }
  /* guard the async window so a double-Enter can't send twice —
     busyStream is only set after the session round-trip */
  sending = true;
  if (!sessionId) await ensureSession();
  sending = false;
  if (!sessionId) return; /* defensive — local fallback always yields an id */

  /* offline session: answer locally (mock stream) and try to recover in background */
  if (isLocalSession()) {
    recoverOnline();
    sendLocal(text);
    return;
  }

  suggHost.style.display = "none";
  appendMsg("user", text, null, new Date().toISOString());
  input.value = "";
  autosize();
  scrollBottom();

  /* assistant shell with typing dots */
  const shell = el(
    '<div class="msg"><span class="m-ava">' + icon("sparkles", "sm") + "</span>" +
      '<div class="grow"><div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div>' +
      '<div class="m-time"></div></div></div>'
  );
  msgsHost.appendChild(shell);
  scrollBottom();

  busyStream = true;
  abortCtl = new AbortController();
  sendBtn.classList.add("stop");
  sendBtn.disabled = false;
  sendBtn.innerHTML = icon("xCircle", "sm");

  let acc = "";
  let gotCites = null;
  const bubble = shell.querySelector(".bubble");
  const modeBadge = panel.querySelector("[data-mode]");

  /* live wait indicator — free-tier Gemini can take 1-2 minutes before the
     first token, so show elapsed time + a patient hint meanwhile */
  const timeHost = shell.querySelector(".m-time");
  const waitHint = el('<div class="wait-hint">' + esc(t("chat.waitHint")) + "</div>");
  waitHint.style.display = "none";
  bubble.parentNode.insertBefore(waitHint, bubble.nextSibling);
  let waitTick = 0;
  let waitStopped = false;
  const waitTimer = setInterval(function () {
    waitTick += 1;
    if (timeHost) timeHost.textContent = waitTick + t("chat.waitSec");
    if (waitTick >= 8) waitHint.style.display = "block";
  }, 1000);
  function stopWait() {
    if (waitStopped) return;
    waitStopped = true;
    clearInterval(waitTimer);
    if (waitHint.parentNode) waitHint.parentNode.removeChild(waitHint);
    if (timeHost) timeHost.textContent = fmtTime(new Date().toISOString());
  }

  function finalize() {
    stopWait();
    busyStream = false;
    abortCtl = null;
    sendBtn.classList.remove("stop");
    sendBtn.innerHTML = icon("send", "sm");
    autosize();
    if (!acc.trim()) {
      bubble.innerHTML = '<span class="muted">—</span>';
    }
    if (gotCites && gotCites.length) bubble.appendChild(citesNode(gotCites));
    scrollBottom();
  }

  try {
    await api.sendStream(
      sessionId,
      { content: text, lang: "auto" },
      {
        signal: abortCtl.signal,
        onChunk: function (delta) {
          stopWait();
          acc += delta;
          bubble.innerHTML = mdLite(acc);
          scrollEase();
        },
        onCitations: function (cards) {
          gotCites = cards;
        },
        onDone: function (obj) {
          if (modeBadge && obj && obj.mode) {
            modeBadge.textContent = obj.mode === "gemini" ? t("chat.mode.ai") : t("chat.mode.rag");
          }
        },
        onError: function (e) {
          /* session vanished server-side (e.g. DB reset): drop it so the
             next send transparently creates a fresh one */
          if (e && e.status === 404) {
            sessionId = null;
            saveSession(null);
          }
          bubble.classList.add("err-x");
          shell.classList.add("err");
          bubble.innerHTML = icon("alert", "sm") + " " + esc((e && e.message) || t("chat.err"));
          const retry = el('<button class="btn btn-outline btn-sm" style="margin-top:8px">' + esc(t("common.retry")) + "</button>");
          retry.addEventListener("click", function () {
            shell.remove();
            input.value = text;
            autosize();
            send();
          });
          bubble.appendChild(document.createElement("br"));
          bubble.appendChild(retry);
        },
      }
    );
  } catch (e) {
    if (!shell.isConnected) return;
    if (e && e.status !== 0 && e.message && !bubble.textContent) {
      shell.classList.add("err");
      bubble.innerHTML = esc(e.message);
    }
  } finally {
    if (shell.isConnected) finalize();
    else {
      stopWait();
      busyStream = false;
      abortCtl = null;
      sendBtn.classList.remove("stop");
      sendBtn.innerHTML = icon("send", "sm");
      autosize();
    }
  }
}

function stopStream() {
  if (abortCtl) abortCtl.abort();
}

/* ---------- offline mode: local streamed answers -------------------- */
/** Keyword-matched agronomy answers used while the backend is unreachable. */
function localAnswer(text) {
  const q = String(text || "");
  const ql = q.toLowerCase();
  const tokens = q.split(/[^\u0600-\u06FFa-zA-Z]+/);
  const hasAr = function (words) {
    for (let i = 0; i < words.length; i++) {
      const w = words[i];
      for (let j = 0; j < tokens.length; j++) {
        const tk = tokens[j];
        if (tk === w || (w.length >= 4 && tk.indexOf(w) >= 0)) return true;
      }
    }
    return false;
  };
  const hasEn = function (words) {
    for (let i = 0; i < words.length; i++) if (ql.indexOf(words[i]) >= 0) return true;
    return false;
  };

  if (/[\u0600-\u06FF]/.test(q)) {
    if (hasAr(["بليط", "لفحة"])) {
      return (
        "**البليط المتأخر (Phytophthora infestans):**\n" +
        "- أزل الأوراق المصابة وأحرقها بعيدًا عن الحقل.\n" +
        "- توقّف عن ريّ الأوراق وقلّل الرطوبة حول النبات.\n" +
        "- طبّق رشًا نحاسيًا (محلول بوردو) كل 7–10 أيام مع مراعاة فترة الأمان قبل الحصاد.\n" +
        "- راقب الحولة أسبوعيًا وأبلغ الجهة الزراعية المختصة عند أي انتشار.\n" +
        "إرشادات عامة من الوضع المحلي — مع عودة الاتصال سأحلّل صورتك بدقة."
      );
    }
    if (hasAr(["نيتروجين", "ازوت", "ازوتيه"])) {
      return (
        "**نقص النيتروجين:**\n" +
        "- يظهر أولًا على الأوراق القديمة على شكل اصفرار بين العروق.\n" +
        "- عالِج بسماد آزوتي (يوريا 46%) على دفعتين خلال الموسم.\n" +
        "- تأكّد من الريّ الكافي — الجذور الجافة لا تمتص المغذّيات.\n" +
        "إرشادات عامة من الوضع المحلي."
      );
    }
    if (hasAr(["ري", "ريّ", "أري", "سقي"])) {
      return (
        "**جدول الريّ الذكي:**\n" +
        "- الريّ العميق المتباعد أفضل من السطحي المتكرر.\n" +
        "- اسقِ في الصباح الباكر لتقليل الفقد بالتبخّر.\n" +
        "- قِس رطوبة التربة حتى عمق 5 سم قبل كل ريّ.\n" +
        "- قلّل الريّ في الأيام الغائمة.\n" +
        "إرشادات عامة من الوضع المحلي."
      );
    }
    if (hasAr(["ذبابة", "حشر", "آفه", "آفات"])) {
      return (
        "**الذبابة البيضاء والآفات الماصّة:**\n" +
        "- علّق لاصقات صفراء داخل الصفوف.\n" +
        "- استعمل الحشرات النافعة (الدعارة العدسية) لخفض الإصابة.\n" +
        "- ركّز على الوجه السفلي للأوراق وطبّق صابون بيوتي أو زيت نيم.\n" +
        "- تجنّب الريّ العلوي على الأوراق.\n" +
        "إرشادات عامة من الوضع المحلي."
      );
    }
    return (
      "أنا الآن أعمل في **الوضع المحلي** (تعذّر الاتصال بالخادم)، وأقدّم إرشادات عامة فقط:\n" +
      "- حدّد المحصول والعَرَض بوضوح (اصفرار، بقع، ذبول…).\n" +
      "- تجنّب الريّ العلوي وأزل الأوراق المصابة فورًا.\n" +
      "- راجع جدول التسميد والريّ حسب مرحلة النمو.\n" +
      "سأنتقل تلقائيًا إلى المساعد الذكي فور عودة الاتصال."
    );
  }

  if (hasEn(["blight", "phytophthora"])) {
    return (
      "**Late blight (Phytophthora infestans):**\n" +
      "- Remove and burn affected leaves away from the field.\n" +
      "- Stop foliar irrigation and reduce humidity around the plants.\n" +
      "- Apply a copper-based spray (Bordeaux mixture) every 7–10 days, respecting the pre-harvest interval.\n" +
      "- Scout the plot weekly and report spread to your extension service.\n" +
      "General guidance from offline mode — once the connection returns I'll analyse your photo precisely."
    );
  }
  if (hasEn(["nitrogen", "deficiency"])) {
    return (
      "**Nitrogen deficiency:**\n" +
      "- Shows first on older leaves (yellowing between the veins).\n" +
      "- Apply a nitrogen fertiliser (e.g. urea 46%) in two splits during the season.\n" +
      "- Keep irrigation adequate — dry roots cannot absorb nutrients.\n" +
      "General guidance from offline mode."
    );
  }
  if (hasEn(["irrigat", "watering"])) {
    return (
      "**Smart irrigation:**\n" +
      "- Deep, infrequent watering beats frequent shallow irrigation.\n" +
      "- Water early in the morning to reduce evaporation losses.\n" +
      "- Check soil moisture at 5 cm depth before each irrigation.\n" +
      "- Reduce watering on cloudy or rainy days.\n" +
      "General guidance from offline mode."
    );
  }
  if (hasEn(["whitefly", "aphid", "pest", "insect"])) {
    return (
      "**Whitefly & sucking pests:**\n" +
      "- Hang yellow sticky traps between the rows.\n" +
      "- Release beneficial insects (e.g. Encarsia formosa) to suppress populations.\n" +
      "- Target the leaf undersides; use bio-based sprays (potassium soap, neem oil).\n" +
      "- Avoid overhead irrigation.\n" +
      "General guidance from offline mode."
    );
  }
  return (
    "I'm currently in **offline mode** (the server is unreachable), so I can only offer general agronomy guidance:\n" +
    "- Describe the crop and the symptom precisely (yellowing, spots, wilting…).\n" +
    "- Avoid foliar irrigation and remove affected leaves promptly.\n" +
    "- Review your fertiliser and irrigation schedule for the current growth stage.\n" +
    "I'll switch back to the full AI assistant as soon as the connection returns."
  );
}

/** Answer locally with a simulated streaming reply (same UX as the AI path). */
function sendLocal(text) {
  suggHost.style.display = "none";
  appendMsg("user", text, null, new Date().toISOString());
  input.value = "";
  autosize();
  scrollBottom();

  const shell = el(
    '<div class="msg"><span class="m-ava">' + icon("sparkles", "sm") + "</span>" +
      '<div class="grow"><div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div>' +
      '<div class="m-time"></div></div></div>'
  );
  msgsHost.appendChild(shell);
  scrollBottom();

  busyStream = true;
  abortCtl = new AbortController();
  sendBtn.classList.add("stop");
  sendBtn.disabled = false;
  sendBtn.innerHTML = icon("xCircle", "sm");

  const bubble = shell.querySelector(".bubble");
  const timeHost = shell.querySelector(".m-time");
  const full = localAnswer(text);
  const words = full.split(" ");
  let idx = 0;
  let tick = null;

  const aborted = function () { return !!(abortCtl && abortCtl.signal.aborted); };

  function finalize() {
    if (tick !== null) { clearTimeout(tick); clearInterval(tick); tick = null; }
    busyStream = false;
    abortCtl = null;
    sendBtn.classList.remove("stop");
    sendBtn.innerHTML = icon("send", "sm");
    autosize();
    if (!bubble.querySelector("p")) bubble.innerHTML = '<span class="muted">—</span>';
    timeHost.textContent = fmtTime(new Date().toISOString());
    scrollBottom();
  }

  /* brief "typing" phase, then reveal the answer word by word */
  tick = setTimeout(function () {
    if (aborted()) { finalize(); return; }
    bubble.innerHTML = "";
    tick = setInterval(function () {
      if (aborted()) { finalize(); return; }
      idx = Math.min(words.length, idx + 3);
      bubble.innerHTML = mdLite(words.slice(0, idx).join(" "));
      scrollEase();
      if (idx >= words.length) finalize();
    }, 45);
  }, 480);
}

/** Clear the current conversation (called on logout). */
export function reset() {
  saveSession(null);
  sessionId = null;
  offline = false;
  if (msgsHost) msgsHost.innerHTML = "";
  if (suggHost) suggHost.style.display = "";
}

/** Tear the whole widget down (called on full re-render / language change). */
export function destroy() {
  if (busyStream && abortCtl) abortCtl.abort();
  if (panel && panel.parentNode) panel.parentNode.removeChild(panel);
  if (backdrop && backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
  if (fab && fab.parentNode) fab.parentNode.removeChild(fab);
  panel = null;
  backdrop = null;
  fab = null;
  msgsHost = null;
  input = null;
  sendBtn = null;
  suggHost = null;
  sessionId = null;
  busyStream = false;
  sending = false;
  offline = false;
  recovering = false;
  abortCtl = null;
  opened = false;
}

export default { initChat, openChat, closeChat, toggleChat, setChatVisible, reset, destroy };
