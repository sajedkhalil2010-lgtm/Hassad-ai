/* =========================================================================
   Assistant panel — one conversational turn at a time with the agronomist.

   History is kept client-side and replayed on each turn, trimmed to the
   window the backend accepts (40 turns), so a long conversation cannot
   outgrow the request.

   When a leaf has just been analysed, a compact whitelisted slice of that
   diagnosis travels with the next question — which is what lets the
   agronomist answer "how much should I spray on THIS plant?" instead of
   giving generic advice.
   ========================================================================= */

import { call } from "./api.js";
import { $, clear, el, show } from "./dom.js";
import { currentLang, t } from "./i18n.js";

/** Backend caps history at 40 turns; stay comfortably under it. */
const MAX_TURNS = 24;

export function initChat({ getContext } = {}) {
  const els = {
    form: $("chat-form"),
    input: $("chat-input"),
    send: $("chat-send"),
    log: $("messages"),
    intro: $("chat-empty"),
    suggestions: $("suggestions"),
    reset: $("chat-reset"),
  };

  /** @type {Array<{role: "user"|"assistant", content: string}>} */
  const history = [];
  let busy = false;
  let typingNode = null;

  /* ------------------------------------------------------------ render -- */

  function scrollToEnd() {
    // rAF so the browser has laid the new bubble out before we measure it.
    requestAnimationFrame(() => {
      els.log.scrollTop = els.log.scrollHeight;
    });
  }

  /** The standing intro note. Removed the moment a real turn lands, so it
   *  does not linger above the conversation as a permanent first message. */
  function makeIntro() {
    const node = el("div", "bubble bubble--bot");
    node.id = "chat-empty";
    node.append(el("p", null, t("chat_intro")));
    return node;
  }

  function dropIntro() {
    if (els.intro && els.intro.isConnected) {
      els.intro.remove();
      els.intro = null;
    }
  }

  /* The three bubble variants, held as data rather than interpolated into a
     template literal. An unknown kind then falls back to the bot style
     instead of rendering an unstyled div, and the class names stay
     something the stylesheet can be checked against statically. */
  const BUBBLE_CLASS = {
    user: "bubble bubble--user",
    bot: "bubble bubble--bot",
    err: "bubble bubble--err",
  };

  function bubble(kind, text) {
    const node = el("div", BUBBLE_CLASS[kind] || BUBBLE_CLASS.bot);
    // pre-wrap keeps the farmer's line breaks; textContent keeps it inert.
    node.textContent = text;
    els.log.append(node);
    scrollToEnd();
    return node;
  }

  function startTyping() {
    stopTyping();
    typingNode = el("div", "bubble bubble--bot");
    const dots = el("span", "typing");
    dots.append(el("i"), el("i"), el("i"));
    typingNode.append(dots);
    els.log.append(typingNode);
    scrollToEnd();
  }

  function stopTyping() {
    if (typingNode) {
      typingNode.remove();
      typingNode = null;
    }
  }

  function renderSuggestions(list) {
    clear(els.suggestions);
    const items = (Array.isArray(list) ? list : []).filter(
      (s) => typeof s === "string" && s.trim(),
    );
    if (!items.length) {
      show(els.suggestions, false);
      return;
    }
    items.forEach((text) => {
      const chip = el("button", "chip-q", text.trim());
      chip.type = "button";
      chip.addEventListener("click", () => {
        els.input.value = text.trim();
        autosize();
        syncSend();
        els.input.focus();
      });
      els.suggestions.append(chip);
    });
    show(els.suggestions, true);
  }

  /* ------------------------------------------------------------ states -- */

  function setBusy(next) {
    busy = next;
    els.input.disabled = next;
    syncSend();
  }

  function syncSend() {
    els.send.disabled = busy || !els.input.value.trim();
  }

  function autosize() {
    els.input.style.height = "auto";
    els.input.style.height = `${Math.min(els.input.scrollHeight, 132)}px`;
  }

  function reset() {
    history.length = 0;
    stopTyping();
    clear(els.log);
    els.intro = makeIntro();
    els.log.append(els.intro);
    renderSuggestions([]);
    els.input.value = "";
    autosize();
    syncSend();
  }

  /* -------------------------------------------------------------- send -- */

  async function send(text) {
    const message = text.trim();
    if (!message || busy) return;

    els.input.value = "";
    autosize();
    setBusy(true);
    show(els.suggestions, false);
    dropIntro();

    // The optimistic bubble makes the turn feel immediate; the reply lands
    // underneath it once the model answers. The dots say "working" in the
    // meantime, which matters most on a slow connection in a field.
    bubble("user", message);
    startTyping();
    const context = getContext ? getContext() : null;

    try {
      const body = await call("/api/v1/chat", {
        method: "POST",
        body: {
          message,
          history: history.slice(-MAX_TURNS),
          diagnosis: context,
          // Mirrors the interface language. The server would otherwise detect
          // it from the message, which fails the moment a farmer types a
          // Latin-cultivar name into an Arabic session.
          language: currentLang(),
        },
      });

      // Only commit the turn to history once the backend accepted it — a
      // rejected message must not be replayed as though it had been answered.
      history.push({ role: "user", content: message });
      history.push({ role: "assistant", content: body.reply || "" });

      if (history.length > MAX_TURNS * 2) history.splice(0, history.length - MAX_TURNS * 2);

      stopTyping();
      bubble("bot", body.reply || "(no reply)");
      renderSuggestions(body.suggestions);
    } catch (err) {
      stopTyping();
      // Shown in place, beside the message it belongs to, so the failure
      // scrolls away with the turn instead of floating over unrelated UI.
      bubble("err", err && err.message ? err.message : "The agronomist could not answer.");
      // The user's own question still belongs in history: they can retry it.
      history.push({ role: "user", content: message });
    } finally {
      setBusy(false);
    }
  }

  /* ------------------------------------------------------------ wiring -- */

  els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    send(els.input.value);
  });

  els.input.addEventListener("input", () => {
    autosize();
    syncSend();
  });

  // Enter sends, Shift+Enter breaks the line — the convention people expect
  // from every chat box they have ever used.
  els.input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    send(els.input.value);
  });

  els.reset.addEventListener("click", reset);

  autosize();
  syncSend();

  return { reset };
}
