/* =====================================================================
   views.js — landing, app shell, dashboard, studio, history, auth modal
   Every function receives `ctx` (navigation + auth callbacks from app.js)
   ===================================================================== */

import api from "./api.js";
import { icon } from "./icons.js";
import { t, tAt, setLang, getLang, fmtDate, fmtBytes } from "./i18n.js";
import {
  $, $$, el, esc, toast, openModal, closeModal, confirmDialog,
  gauge, mdLite, statusBadge, statusHeadClass, initials, percent, busy, errToast,
} from "./ui.js";
import { getProfile, cropLabel, CROPS } from "./profile.js";

/* localized {ar,en} picker */
function pick(loc) {
  if (!loc) return "";
  if (typeof loc === "string") return loc;
  const lang = getLang();
  return (loc[lang] && loc[lang].trim()) || loc.ar || loc.en || "";
}

/* =====================================================================
   LANDING
   ===================================================================== */
export function renderLanding() {
  const L = function (k) { return esc(t(k)); };

  const stepBtn = function (n, key) {
    return (
      '<button class="step" data-step="' + n + '">' +
        '<span class="s-num">' + n + "</span>" +
        '<span class="s-t">' + L(key + ".t") + "</span>" +
        '<span class="s-d">' + L(key + ".d") + "</span>" +
      "</button>"
    );
  };

  const feature = function (key, ic) {
    return (
      '<div class="feature-tile reveal" style="--i:' + key + '">' +
        icon(ic, "lg") +
        "<h4>" + L("feat" + key + ".t") + "</h4>" +
        "<p>" + L("feat" + key + ".d") + "</p>" +
      "</div>"
    );
  };

  return (
  '<div class="landing">' +
    /* ---- top nav ---- */
    '<header class="nav-landing">' +
      '<a class="brand" href="#/">' +
        '<span class="brand-mark">' + icon("leaf") + "</span>" +
        "<span>حَصاد <span class=\"ai\">AI</span><small>HASSAD AI</small></span>" +
      "</a>" +
      '<nav class="nav-links">' +
        '<a href="#how">' + L("how.eyebrow") + "</a>" +
        '<a href="#modules">' + L("mod.eyebrow") + "</a>" +
        '<a href="#features">' + L("feat.eyebrow") + "</a>" +
      "</nav>" +
      '<div class="nav-cta">' +
        '<button class="btn btn-ghost btn-sm" data-act="lang"></button>' +
        '<button class="btn btn-ghost btn-sm btn-icon" data-act="theme" aria-label="theme"></button>' +
        '<button class="btn btn-outline btn-sm" data-act="login">' + L("nav.login") + "</button>" +
        '<button class="btn btn-primary btn-sm" data-act="start">' + L("nav.start") + "</button>" +
      "</div>" +
    "</header>" +

    /* ---- hero ---- */
    '<section class="hero">' +
      '<div class="hero-copy reveal in">' +
        '<span class="hero-badge"><span class="dot"></span>' + L("hero.badge") + "</span>" +
        "<h1>" + L("hero.title1") + '<br/><span class="grad">' + L("hero.titleGrad") + "</span></h1>" +
        '<p class="hero-sub">' + L("hero.sub") + "</p>" +
        '<div class="hero-cta">' +
          '<button class="btn btn-primary btn-lg" data-act="start">' +
            icon("sparkles") + L("hero.cta1") +
          "</button>" +
          '<a class="btn btn-outline btn-lg" href="#how">' + icon("play") + L("hero.cta2") + "</a>" +
        "</div>" +
        '<div class="hero-meta">' +
          '<span class="hm">' + icon("shield", "sm") + L("hero.meta1") + "</span>" +
          '<span class="hm">' + icon("zap", "sm") + L("hero.meta2") + "</span>" +
          '<span class="hm">' + icon("languages", "sm") + L("hero.meta3") + "</span>" +
        "</div>" +
      "</div>" +

      '<div class="hero-visual reveal in">' +
        '<div class="glow"></div>' +
        '<div class="float-chip a">' + icon("cpu") + "<span>AI Vision</span></div>" +
        '<div class="float-chip b">' + icon("shield") + "<span>JWT · Private</span></div>" +
        '<div class="float-chip c">' + icon("zap") + "&lt;30s</div>" +
        '<div class="mock-card">' +
          '<div class="mock-img">' +
            '<div class="leaf-blob"></div><div class="spot"></div>' +
            '<div class="scan-line"></div>' +
          "</div>" +
          '<div class="mock-row">' +
            '<div class="mock-gauge">' +
              '<svg width="64" height="64" viewBox="0 0 64 64">' +
                '<circle cx="32" cy="32" r="27" stroke="rgba(255,255,255,.1)" stroke-width="7" fill="none"/>' +
                '<circle cx="32" cy="32" r="27" stroke="url(#mg)" stroke-width="7" fill="none" stroke-linecap="round" stroke-dasharray="169.6" stroke-dashoffset="13.6" transform="rotate(-90 32 32)"/>' +
                '<defs><linearGradient id="mg" x1="0" y1="0" x2="1" y2="1">' +
                  '<stop offset="0" stop-color="#10b981"/><stop offset="1" stop-color="#a3e635"/>' +
                "</linearGradient></defs>" +
              "</svg>" +
              '<span class="g-txt num">92%</span>' +
            "</div>" +
            '<div class="mock-info">' +
              '<div class="t">' + L("hero.mockT") + "</div>" +
              '<div class="s">' + L("hero.mockS") + "</div>" +
              '<div class="mock-chips">' +
                '<span class="badge b-green">' + icon("check", "sm") + L("hero.chip1") + "</span>" +
                '<span class="badge b-lime num">92%</span>' +
                '<span class="badge b-blue">' + icon("shield", "sm") + L("hero.chip3") + "</span>" +
              "</div>" +
            "</div>" +
          "</div>" +
        "</div>" +
      "</div>" +
    "</section>" +

    /* ---- stats strip ---- */
    '<div class="stats-strip">' +
      [1, 2, 3, 4].map(function (i) {
        return (
          '<div class="stat reveal" style="--i:' + i + '">' +
            '<div class="n num">' + esc(t("stats." + i + "n")) + "</div>" +
            '<div class="l">' + esc(t("stats." + i + "l")) + "</div>" +
          "</div>"
        );
      }).join("") +
    "</div>" +

    /* ---- how it works ---- */
    '<section class="section" id="how">' +
      '<div class="section-head reveal">' +
        '<div class="eyebrow">' + icon("sparkles", "sm") + L("how.eyebrow") + "</div>" +
        '<h2 class="section-title">' + L("how.title") + '<span class="grad">' + L("how.titleGrad") + "</span></h2>" +
        '<p class="section-sub">' + L("how.sub") + "</p>" +
      "</div>" +
      '<div class="steps-wrap reveal">' +
        '<div class="steps-line"><i></i></div>' +
        '<div class="steps">' +
          stepBtn(1, "step1") + stepBtn(2, "step2") + stepBtn(3, "step3") +
        "</div>" +
        '<div class="step-stage"><div class="stage-inner" id="stage"></div></div>' +
      "</div>" +
    "</section>" +

    /* ---- modules ---- */
    '<section class="section" id="modules" style="padding-top:0">' +
      '<div class="section-head reveal">' +
        '<div class="eyebrow">' + icon("target", "sm") + L("mod.eyebrow") + "</div>" +
        '<h2 class="section-title">' + L("mod.title") + '<span class="grad">' + L("mod.titleGrad") + "</span></h2>" +
        '<p class="section-sub">' + L("mod.sub") + "</p>" +
      "</div>" +
      '<div class="module-grid">' +
        moduleCard("doctor", "stethoscope", "mod.doctor") +
        moduleCard("chat", "message", "mod.chat") +
        moduleCard("yield", "chart", "mod.yield") +
      "</div>" +
    "</section>" +

    /* ---- features ---- */
    '<section class="section" id="features" style="padding-top:0">' +
      '<div class="section-head reveal">' +
        '<div class="eyebrow">' + icon("award", "sm") + L("feat.eyebrow") + "</div>" +
        '<h2 class="section-title">' + L("feat.title") + '<span class="grad">' + L("feat.titleGrad") + "</span></h2>" +
        '<p class="section-sub">' + L("feat.sub") + "</p>" +
      "</div>" +
      '<div class="feature-grid">' +
        feature("1", "languages") + feature("2", "zap") + feature("3", "lock") +
        feature("4", "wifioff") + feature("5", "gauge") + feature("6", "book") +
      "</div>" +
    "</section>" +

    /* ---- CTA ---- */
    '<section class="section" style="padding-top:0">' +
      '<div class="cta-band reveal">' +
        "<h2>" + L("cta.title") + "</h2>" +
        "<p>" + L("cta.sub") + "</p>" +
        '<button class="btn btn-primary btn-lg" data-act="start">' +
          icon("sparkles") + L("cta.btn") +
        "</button>" +
      "</div>" +
    "</section>" +

    /* ---- footer ---- */
    '<footer class="footer">' +
      '<div class="row">' +
        '<span class="brand-mark" style="width:30px;height:30px;border-radius:9px">' + icon("leaf", "sm") + "</span>" +
        "<span>© " + new Date().getFullYear() + " حَصاد AI · " + L("foot.rights") + "</span>" +
      "</div>" +
      '<div class="f-links">' +
        '<a href="#how">' + L("how.eyebrow") + "</a>" +
        '<a href="#modules">' + L("mod.eyebrow") + "</a>" +
        '<a href="/docs" target="_blank" rel="noopener">API</a>' +
      "</div>" +
      '<span>' + L("foot.made") + " 🌱</span>" +
    "</footer>" +
  "</div>"
  );
}

function moduleCard(id, ic, key) {
  return (
    '<button class="module-card reveal" data-module="' + id + '" style="--i:0">' +
      '<span class="m-ico">' + icon(ic, "lg") + "</span>" +
      "<h3>" + esc(t(key + ".t")) + "</h3>" +
      "<p>" + esc(t(key + ".d")) + "</p>" +
      '<div class="m-foot">' +
        '<span class="m-link">' + esc(t(key + ".link")) + icon("arrowRight", "sm") + "</span>" +
        '<span class="badge b-slate">' + icon("play", "sm") + esc(t("common.seeDemo")) + "</span>" +
      "</div>" +
    "</button>"
  );
}

/* ---------- landing: step stage content ---------------------------- */
const STEP_VISUALS = {
  1: function () {
    return (
      '<div class="sv-drop stage-fade">' +
        '<div class="ring">' + icon("upload", "xl") + "</div>" +
        '<b>' + esc(t("step1.svHint")) + "</b>" +
        '<span class="badge b-slate">' + esc(t("studio.rule1")) + "</span>" +
      "</div>"
    );
  },
  2: function () {
    return (
      '<div class="stage-fade" style="display:grid;gap:16px;justify-items:center">' +
        '<div class="sv-bars">' +
          "<i></i><i></i><i></i><i></i><i></i><i></i>" +
        "</div>" +
        '<b class="muted">' + esc(t("step2.svHint")) + "</b>" +
        '<div class="row">' +
          '<span class="badge b-blue">' + icon("cpu", "sm") + "CNN</span>" +
          '<span class="badge b-lime">' + icon("scan", "sm") + "ViT</span>" +
        "</div>" +
      "</div>"
    );
  },
  3: function () {
    const g = gauge(0.92);
    const html =
      '<div class="sv-result stage-fade">' +
        '<div class="rr">' + g.node.outerHTML.replace('class="gauge"', 'class="gauge" style="width:62px;height:62px"') +
          '<div><b>' + esc(t("hero.mockT")) + '</b><div class="tiny muted">' + esc(t("res.confidence")) + " 92%</div></div>" +
        "</div>" +
        '<div class="sv-check">' + icon("check") + "</div>" +
      "</div>";
    return html;
  },
};

function renderStage(n) {
  const stage = document.getElementById("stage");
  if (!stage) return;
  const listKey = "step" + n;
  const visual = STEP_VISUALS[n] ? STEP_VISUALS[n]() : "";
  const items = [1, 2, 3]
    .map(function (i) {
      const txt = t(listKey + ".li" + i);
      if (!txt || txt.indexOf("step") === 0) return "";
      return '<div class="li">' + icon("checkCircle", "sm") + "<span>" + esc(txt) + "</span></div>";
    })
    .join("");

  stage.innerHTML =
    '<div class="stage-copy stage-fade">' +
      '<span class="badge b-lime">' + esc(t("common.new")) + " · 0" + n + "</span>" +
      "<h3>" + esc(t(listKey + ".t")) + "</h3>" +
      "<p>" + esc(t(listKey + ".d")) + "</p>" +
      '<div class="stage-list">' + items + "</div>" +
    "</div>" +
    '<div class="stage-visual stage-fade">' + visual + "</div>";
}

/* ---------- landing: bind ------------------------------------------ */
export function bindLanding(root, ctx) {
  /* nav bar controls */
  const langBtn = root.querySelector('[data-act="lang"]');
  const themeBtn = root.querySelector('[data-act="theme"]');
  const paint = function () {
    if (langBtn) langBtn.textContent = getLang() === "ar" ? "EN" : "ع";
    if (themeBtn) themeBtn.innerHTML = icon(document.documentElement.dataset.theme === "light" ? "moon" : "sun");
  };
  paint();
  if (langBtn) {
    langBtn.addEventListener("click", function () {
      ctx.toggleLang();
      paint();
    });
  }
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      ctx.toggleTheme();
      paint();
    });
  }

  /* CTA buttons */
  $$('[data-act="start"]', root).forEach(function (b) {
    b.addEventListener("click", function () { ctx.start(); });
  });
  $$('[data-act="login"]', root).forEach(function (b) {
    b.addEventListener("click", function () { ctx.openAuth("login"); });
  });

  /* interactive steps */
  let active = 1;
  let paused = false;
  let timer = null;
  const steps = $$(".step", root);
  const line = root.querySelector(".steps-line i");

  function setStep(n) {
    active = n;
    steps.forEach(function (s) {
      const i = Number(s.dataset.step);
      s.classList.toggle("active", i === n);
      s.classList.toggle("done", i < n);
    });
    if (line) line.style.width = (n / 3) * 100 + "%";
    renderStage(n);
  }
  steps.forEach(function (s) {
    s.addEventListener("click", function () {
      paused = true;
      setStep(Number(s.dataset.step));
      setTimeout(function () { paused = false; }, 8000);
    });
  });
  setStep(1);
  timer = setInterval(function () {
    if (paused) return;
    setStep(active % 3 + 1);
  }, 4200);
  /* stop the interval when leaving the landing page */
  const stageWrap = root.querySelector(".steps-wrap");
  if (stageWrap) {
    stageWrap.addEventListener("mouseenter", function () { paused = true; });
    stageWrap.addEventListener("mouseleave", function () { paused = false; });
  }
  ctx.onLeave(function () {
    if (timer) clearInterval(timer);
  });

  /* module cards — cursor glow + demo modal */
  $$(".module-card", root).forEach(function (card) {
    card.addEventListener("mousemove", function (e) {
      const r = card.getBoundingClientRect();
      card.style.setProperty("--mx", ((e.clientX - r.left) / r.width) * 100 + "%");
      card.style.setProperty("--my", ((e.clientY - r.top) / r.height) * 100 + "%");
    });
    card.addEventListener("click", function () {
      openModuleDemo(card.dataset.module, ctx);
    });
  });

  /* reveal on scroll */
  const io = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add("in");
          io.unobserve(en.target);
        }
      });
    },
    { threshold: 0.12 }
  );
  $$(".reveal", root).forEach(function (n) { io.observe(n); });
}

/* ---------- landing: live demo modals ------------------------------ */
export function openModuleDemo(which, ctx) {
  if (which === "doctor") return demoDoctor(ctx);
  if (which === "chat") return demoChat();
  if (which === "yield") return demoYield();
}

function demoHead(title) {
  return (
    '<div class="modal-head"><h3>' + esc(title) + "</h3>" +
    '<button class="icon-btn" data-close>' + icon("x") + "</button></div>"
  );
}

function demoDoctor() {
  const node = el(
    '<div>' + demoHead(t("mod.doctor.t")) +
      '<div class="modal-body demo-stage">' +
        '<div class="tiny muted center">' + esc(t("mod.doctor.prev")) + "</div>" +
        '<div id="demoDoc"></div>' +
        '<button class="btn btn-primary btn-block" data-close>' + esc(t("demo.open")) + "</button>" +
      "</div></div>"
  );
  const m = openModal({ node: node, size: "lg" });
  const host = node.querySelector("#demoDoc");
  host.innerHTML =
    '<div class="demo-scan"><div class="leaf-blob"></div><div class="spot"></div><div class="scan-line"></div>' +
    '<span class="badge b-lime" style="position:absolute;bottom:12px">' + icon("scan", "sm") + esc(t("demo.scan")) + "</span></div>";

  setTimeout(function () {
    if (!host.isConnected) return;
    const g = gauge(0.92);
    host.innerHTML = "";
    const card = el(
      '<div class="result-card card view-enter">' +
        '<div class="result-head ok"></div>' +
        '<div class="quality-chips">' +
          '<span class="qchip">' + icon("check", "sm") + esc(t("q.ok")) + "</span>" +
          '<span class="qchip num">640×480</span>' +
          '<span class="qchip">' + icon("sprout", "sm") + esc(getLang() === "ar" ? "طماطم" : "Tomato") + "</span>" +
        "</div>" +
        '<div class="plan"><div class="plan-section"><h4>' + icon("clipboard", "sm") + esc(t("plan.summary")) + "</h4>" +
          '<div class="plan-summary">' +
            esc(
              getLang() === "ar"
                ? "بليط متأخر على الطماطم — اخفض الرطوبة، أزل الأوراق المصابة وابدأ معالجة نحاسية وفق الجدول."
                : "Late blight on tomato — reduce humidity, remove affected leaves and start a copper program on schedule."
            ) +
          "</div></div></div>" +
      "</div>"
    );
    const head = card.querySelector(".result-head");
    head.appendChild(g.node);
    const title = el(
      '<div class="result-title"><div class="rt-status">' + statusBadge("confirmed") +
      '<span class="badge b-lime num">92%</span></div>' +
      "<h3>" + esc(getLang() === "ar" ? "البليط المتأخر" : "Late blight") + "</h3>" +
      '<div class="rt-crop">' + esc(getLang() === "ar" ? "الطماطم · Solanum lycopersicum" : "Tomato · Solanum lycopersicum") + "</div></div>"
    );
    head.appendChild(title);
    host.appendChild(card);
    g.setValue(0.92);
  }, 2300);

  node.querySelectorAll("[data-close]").forEach(function (b) {
    b.addEventListener("click", function () { m.close(); });
  });
}

function demoChat() {
  const node = el(
    '<div>' + demoHead(t("mod.chat.t")) +
      '<div class="modal-body">' +
        '<div class="tiny muted center" style="margin-bottom:14px">' + esc(t("mod.chat.prev")) + "</div>" +
        '<div class="demo-chat" id="demoChat"></div>' +
        '<button class="btn btn-primary btn-block" style="margin-top:16px" data-close>' + esc(t("demo.open")) + "</button>" +
      "</div></div>"
  );
  const m = openModal({ node: node, size: "lg" });
  const host = node.querySelector("#demoChat");

  function bubble(cls, text) {
    const n = el('<div class="d-msg ' + cls + '"><div class="b">' + esc(text) + "</div></div>");
    host.appendChild(n);
    return n;
  }
  function typing() {
    const n = el(
      '<div class="d-msg"><div class="b typing"><i></i><i></i><i></i></div></div>'
    );
    host.appendChild(n);
    return n;
  }

  const seq = [
    function () { bubble("u", t("demo.chat.user")); },
    function () {
      const tp = typing();
      setTimeout(function () {
        if (!tp.isConnected) return;
        tp.remove();
        const b = bubble("", "");
        const full = t("demo.chat.ai");
        let i = 0;
        const iv = setInterval(function () {
          if (!b.isConnected) { clearInterval(iv); return; }
          i += 2;
          b.querySelector(".b").textContent = full.slice(0, i);
          if (i >= full.length) clearInterval(iv);
        }, 24);
      }, 900);
    },
    function () {
      const c = el(
        '<div class="d-msg"><div class="b" style="border-color:rgba(16,185,129,.35)">' +
          '<div class="cites"><span class="cite-chip">' + icon("book", "sm") +
          "<span>" + esc(getLang() === "ar" ? "دليل مكافحة أمراض الخضروات" : "Vegetable disease guide") +
          "</span></span></div></div></div>"
      );
      host.appendChild(c);
    },
  ];
  const timers = [];
  timers.push(setTimeout(function () { seq[0](); }, 250));
  timers.push(setTimeout(function () { seq[1](); }, 900));
  timers.push(setTimeout(function () { seq[2](); }, 3400));

  node.querySelectorAll("[data-close]").forEach(function (b) {
    b.addEventListener("click", function () {
      timers.forEach(clearTimeout);
      m.close();
    });
  });
}

function demoYield() {
  const bars = [
    { l: "W1", v: 46 }, { l: "W2", v: 58 }, { l: "W3", v: 52 },
    { l: "W4", v: 71 }, { l: "W5", v: 83 }, { l: "W6", v: 92, pred: true },
    { l: "W7", v: 78, pred: true },
  ];
  const node = el(
    '<div>' + demoHead(t("mod.yield.t")) +
      '<div class="modal-body">' +
        '<div class="tiny muted center">' + esc(t("demo.yield.sub")) + "</div>" +
        '<div class="demo-chart">' +
          bars.map(function (b, i) {
            return (
              '<div class="col' + (b.pred ? " pred" : "") + '">' +
                '<i style="--h:' + b.v + '%;animation-delay:' + (i * 0.09) + 's"></i>' +
                "<span>" + b.l + "</span>" +
              "</div>"
            );
          }).join("") +
        "</div>" +
        '<div class="row" style="justify-content:center;margin-top:16px">' +
          '<span class="badge b-slate">' + esc(t("demo.yield.now")) + ': <b class="num">4.2 t/ha</b></span>' +
          '<span class="badge b-lime">' + esc(t("demo.yield.pred")) + ': <b class="num">5.6 t/ha</b></span>' +
          '<span class="badge b-blue">' + esc(t("common.comingSoon")) + "</span>" +
        "</div>" +
        '<button class="btn btn-primary btn-block" style="margin-top:18px" data-close>' + esc(t("demo.open")) + "</button>" +
      "</div></div>"
  );
  const m = openModal({ node: node, size: "lg" });
  node.querySelectorAll("[data-close]").forEach(function (b) {
    b.addEventListener("click", function () { m.close(); });
  });
}

/* =====================================================================
   ONBOARDING GATEWAY — the first screen (name · farm · crop focus)
   ===================================================================== */
export function renderWelcome() {
  const L = function (k) { return esc(t(k)); };
  const lang = getLang();
  const chips = CROPS.map(function (c, i) {
    return (
      '<button type="button" class="crop-chip' + (i === 0 ? " sel" : "") + '" data-crop="' + c.id + '">' +
        icon("sprout", "sm") + "<span>" + esc(lang === "en" ? c.en : c.ar) + "</span>" +
      "</button>"
    );
  }).join("");

  return (
  '<div class="onb">' +
    '<div class="onb-orbs" aria-hidden="true"><i class="orb o1"></i><i class="orb o2"></i><i class="orb o3"></i></div>' +
    '<div class="onb-grid" aria-hidden="true"></div>' +
    '<div class="onb-card">' +
      '<div class="onb-brand">' +
        '<span class="brand-mark">' + icon("leaf") + "</span>" +
        "<span>حَصاد <span class=\"ai\">AI</span><small>HASSAD AI</small></span>" +
      "</div>" +
      '<span class="onb-badge"><span class="dot"></span>' + L("hero.badge") + "</span>" +
      '<h1 class="onb-title">' + L("onb.title") + "</h1>" +
      '<p class="onb-sub">' + L("onb.sub") + "</p>" +
      '<form id="onbForm" class="onb-form" novalidate>' +
        '<label class="fld">' +
          "<span>" + L("onb.name") + "</span>" +
          '<input id="onbName" name="name" type="text" maxlength="80" autocomplete="name" placeholder="' +
            L("onb.namePh") + '" required />' +
          '<small class="fld-err" data-err="name"></small>' +
        "</label>" +
        '<label class="fld">' +
          "<span>" + icon("target", "sm") + " " + L("onb.location") + "</span>" +
          '<input id="onbLoc" name="location" type="text" maxlength="120" placeholder="' +
            L("onb.locationPh") + '" value="' + (lang === "en" ? "Tébessa, Algeria" : "تبسة، الجزائر") + '" />' +
        "</label>" +
        '<div class="onb-crop">' +
          '<span class="onb-lbl">' + icon("sprout", "sm") + " " + L("onb.crop") + "</span>" +
          '<div class="crop-chips" id="onbCrops">' + chips + "</div>" +
        "</div>" +
        '<button class="btn btn-primary btn-block btn-lg onb-go" type="submit">' +
          icon("sparkles", "sm") + L("onb.submit") +
        "</button>" +
      "</form>" +
      '<div class="onb-foot"><span>' + L("onb.have") + "</span>" +
        '<button class="linklike" data-act="login">' + L("onb.login") + "</button></div>" +
      '<div class="onb-progress" aria-hidden="true"><i class="on"></i><i></i><i></i></div>' +
    "</div>" +
  "</div>"
  );
}

export function bindWelcome(root, ctx) {
  const form = root.querySelector("#onbForm");
  const nameEl = root.querySelector("#onbName");
  const locEl = root.querySelector("#onbLoc");
  const errEl = root.querySelector('[data-err="name"]');
  let crop = CROPS[0].id;

  root.querySelectorAll(".crop-chip").forEach(function (b) {
    b.addEventListener("click", function () {
      root.querySelectorAll(".crop-chip").forEach(function (x) {
        x.classList.remove("sel");
      });
      b.classList.add("sel");
      crop = b.dataset.crop;
    });
  });

  const loginBtn = root.querySelector('[data-act="login"]');
  if (loginBtn) loginBtn.addEventListener("click", function () { ctx.openAuth("login"); });

  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      const name = ((nameEl && nameEl.value) || "").trim();
      if (name.length < 2) {
        if (errEl) errEl.textContent = t("onb.requiredName");
        if (nameEl) nameEl.focus();
        return;
      }
      if (errEl) errEl.textContent = "";
      const profile = {
        name: name,
        location: (((locEl && locEl.value) || "")).trim(),
        crop: crop,
      };
      const onb = root.querySelector(".onb");
      if (onb) onb.classList.add("exiting");
      setTimeout(function () {
        ctx.enterFromOnboarding(profile);
      }, 340); /* let the transition animation land first */
    });
  }
  if (nameEl) setTimeout(function () { nameEl.focus(); }, 260);
}

/* =====================================================================
   APP SHELL (sidebar + topbar)
   ===================================================================== */
export function shellHTML(user, route) {
  const navItem = function (r, ic, labelKey, count) {
    return (
      '<button class="nav-item' + (route === r ? " active" : "") + '" data-route="' + r + '">' +
        icon(ic) + "<span>" + esc(t(labelKey)) + "</span>" +
        (count ? '<span class="n-count num">' + count + "</span>" : "") +
      "</button>"
    );
  };

  return (
  '<div class="shell" id="shell">' +
    '<div class="nav-overlay" data-navclose></div>' +
    '<aside class="sidebar">' +
      '<div class="side-head">' +
        '<a class="brand" href="#/">' +
          '<span class="brand-mark">' + icon("leaf") + "</span>" +
          "<span>حَصاد <span class=\"ai\">AI</span><small>HASSAD AI</small></span>" +
        "</a>" +
      "</div>" +
      '<nav class="side-nav">' +
        '<div class="nav-lbl">' + esc(t("nav.sectionMain")) + "</div>" +
        navItem("app", "dashboard", "nav.dashboard") +
        navItem("studio", "scan", "nav.studio") +
        navItem("history", "history", "nav.history") +
        '<div class="nav-lbl">' + esc(t("nav.sectionTools")) + "</div>" +
        '<button class="nav-item" data-act="chat">' + icon("message") +
          "<span>" + esc(t("nav.chat")) + "</span>" +
          '<span class="n-count" style="color:var(--brand-2)">AI</span>' +
        "</button>" +
      "</nav>" +
      '<div class="side-foot">' +
        '<div class="side-user">' +
          '<span class="avatar">' + esc(initials(user && user.full_name)) + "</span>" +
          '<span class="su-info"><span class="su-name">' + esc((user && user.full_name) || "—") + "</span>" +
            '<span class="su-role">' + esc(
              user && user.role === "agronomist"
                ? getLang() === "ar" ? "أخصائي زراعي" : "Agronomist"
                : getLang() === "ar" ? "مزارع" : "Farmer"
            ) + "</span></span>" +
          '<button class="icon-btn" data-act="logout" title="' + esc(t("nav.logout")) + '">' + icon("logout", "sm") + "</button>" +
        "</div>" +
      "</div>" +
    "</aside>" +

    '<div class="main">' +
      '<header class="topbar">' +
        '<button class="icon-btn menu-btn" data-act="menu" aria-label="' + esc(t("nav.menu")) + '">' + icon("menu") + "</button>" +
        '<div><div class="t-title" id="topTitle"></div><div class="t-sub" id="topSub"></div></div>' +
        '<div class="top-actions">' +
          '<span class="api-pill" id="apiPill"><span class="d"></span><span>' + esc(t("dash.api.ok")) + "</span></span>" +
          '<button class="btn btn-ghost btn-sm" data-act="lang"></button>' +
          '<button class="icon-btn" data-act="theme" aria-label="theme"></button>' +
        "</div>" +
      "</header>" +
      '<main class="view" id="view"></main>' +
    "</div>" +
  "</div>"
  );
}

export function bindShell(root, ctx) {
  const shell = root.querySelector("#shell");
  const langBtn = root.querySelector('.topbar [data-act="lang"]');
  const themeBtn = root.querySelector('.topbar [data-act="theme"]');

  const paint = function () {
    if (langBtn) langBtn.textContent = getLang() === "ar" ? "EN" : "ع";
    if (themeBtn) themeBtn.innerHTML = icon(document.documentElement.dataset.theme === "light" ? "moon" : "sun");
  };
  paint();
  ctx._paintTopbar = paint;

  if (langBtn) langBtn.addEventListener("click", function () { ctx.toggleLang(); });
  if (themeBtn) themeBtn.addEventListener("click", function () { ctx.toggleTheme(); });

  $$("[data-route]", root).forEach(function (b) {
    b.addEventListener("click", function () {
      ctx.go(b.dataset.route);
      shell.classList.remove("nav-open");
    });
  });
  const menuBtn = root.querySelector('[data-act="menu"]');
  if (menuBtn) menuBtn.addEventListener("click", function () { shell.classList.toggle("nav-open"); });
  const navClose = root.querySelector("[data-navclose]");
  if (navClose) navClose.addEventListener("click", function () { shell.classList.remove("nav-open"); });

  const chatBtn = root.querySelector('[data-act="chat"]');
  if (chatBtn) chatBtn.addEventListener("click", function () {
    shell.classList.remove("nav-open");
    ctx.openChat();
  });
  const logoutBtn = root.querySelector('[data-act="logout"]');
  if (logoutBtn) logoutBtn.addEventListener("click", function () { ctx.logout(); });

  /* API health pill */
  const pill = root.querySelector("#apiPill");
  const poll = async function () {
    const h = await api.health();
    if (!pill) return;
    const ok = h && h.status === "ok";
    pill.classList.toggle("on", !!ok);
    pill.classList.toggle("off", !ok);
    pill.querySelector("span:last-child").textContent = ok ? t("dash.api.ok") : t("dash.api.bad");
  };
  poll();
  const iv = setInterval(poll, 45000);
  ctx.onLeave(function () { clearInterval(iv); });
}

export function setTopbar(root, titleKey, subKey) {
  const title = root.querySelector("#topTitle");
  const sub = root.querySelector("#topSub");
  if (title) title.textContent = t(titleKey);
  if (sub) sub.textContent = subKey ? t(subKey) : "";
}

/* =====================================================================
   DASHBOARD
   ===================================================================== */
export function dashboardHTML() {
  const profile = getProfile();
  const farmChips = [];
  if (profile && profile.location) {
    farmChips.push('<span class="qchip">' + icon("target", "sm") + esc(profile.location) + "</span>");
  }
  if (profile && profile.crop) {
    farmChips.push('<span class="qchip">' + icon("sprout", "sm") + esc(cropLabel(profile.crop, getLang())) + "</span>");
  }
  return (
    '<div class="welcome">' +
      "<div>" +
        "<h2>" + esc(t("dash.hi")) + ' <span id="dashName"></span> 👋</h2>' +
        "<p><b>" + esc(t("dash.welcome")) + "</b><br/>" + esc(t("dash.welcomeS")) + "</p>" +
        (farmChips.length
          ? '<div class="farm-line">' + icon("leaf", "sm") + farmChips.join("") + "</div>"
          : "") +
      "</div>" +
      '<div class="w-actions">' +
        '<button class="btn btn-primary" data-go="studio">' + icon("scan", "sm") + esc(t("dash.newScan")) + "</button>" +
        '<button class="btn btn-outline" data-act="ask">' + icon("message", "sm") + esc(t("dash.askBot")) + "</button>" +
      "</div>" +
    "</div>" +

    '<div class="stat-grid">' +
      statCard("total", "clipboard", "", "—") +
      statCard("confirmed", "checkCircle", "", "—") +
      statCard("review", "clock", "amber", "—") +
      statCard("avg", "gauge", "lime", "—") +
    "</div>" +

    '<div class="dash-grid">' +
      '<div class="panel">' +
        '<div class="panel-head"><h3>' + icon("history", "sm") + esc(t("dash.recent")) + "</h3>" +
          '<button class="btn btn-ghost btn-sm" data-go="history">' + esc(t("nav.history")) + icon("arrowRight", "sm") + "</button>" +
        "</div>" +
        '<div class="panel-body"><div class="recent-list" id="recentList">' +
          '<div class="skeleton sk-line w80"></div><div class="skeleton sk-line w60"></div><div class="skeleton sk-line w40"></div>' +
        "</div></div>" +
      "</div>" +
      '<div class="panel">' +
        '<div class="panel-head"><h3>' + icon("zap", "sm") + esc(t("dash.quick")) + "</h3></div>" +
        '<div class="panel-body"><div class="quick-grid">' +
          quickCard("studio", "scan", "dash.q1") +
          quickCard("ask", "message", "dash.q2") +
          quickCard("history", "history", "dash.q3") +
          quickCard("sync", "refresh", "dash.q4") +
        "</div></div>" +
      "</div>" +
    "</div>"
  );
}

function statCard(key, ic, cls, val) {
  return (
    '<div class="stat-card"><span class="st-ico ' + cls + '">' + icon(ic, "lg") + "</span>" +
    '<div><div class="st-n num" data-stat="' + key + '">' + val + "</div>" +
    '<div class="st-l">' + esc(t("dash.stat." + key)) + "</div></div></div>"
  );
}

function quickCard(act, ic, key) {
  return (
    '<button class="quick-card" data-quick="' + act + '">' + icon(ic, "lg") +
    "<b>" + esc(t(key + ".t")) + "</b><span>" + esc(t(key + ".d")) + "</span></button>"
  );
}

export async function bindDashboard(root, ctx) {
  const nameEl = root.querySelector("#dashName");
  if (nameEl) {
    const first =
      ctx.user && ctx.user.full_name ? String(ctx.user.full_name).split(/\s+/)[0] : "";
    const profile = getProfile();
    nameEl.textContent = first || (profile && profile.name) || "";
  }

  root.querySelectorAll("[data-go]").forEach(function (b) {
    b.addEventListener("click", function () { ctx.go(b.dataset.go); });
  });
  root.querySelectorAll('[data-quick="studio"], [data-quick="history"]').forEach(function (b) {
    b.addEventListener("click", function () { ctx.go(b.dataset.quick); });
  });
  const ask = root.querySelector('[data-quick="ask"], [data-act="ask"]');
  root.querySelectorAll('[data-quick="ask"], [data-act="ask"]').forEach(function (b) {
    b.addEventListener("click", function () { ctx.openChat(); });
  });
  const sync = root.querySelector('[data-quick="sync"]');
  if (sync) {
    sync.addEventListener("click", async function () {
      await busy(sync, t("common.loading"), async function () {});
      toast(t("dash.api.ok"), "ok");
    });
  }

  /* stats + recent list */
  const list = root.querySelector("#recentList");
  try {
    const data = await api.diagnoses(50, 0);
    const items = (data && data.items) || [];
    const total = (data && data.total) || items.length;
    const confirmed = items.filter(function (x) { return x.status === "confirmed"; }).length;
    const review = items.filter(function (x) { return x.status === "in_review"; }).length;
    const confs = items
      .map(function (x) { return x.disease_confidence != null ? x.disease_confidence : x.crop_confidence; })
      .filter(function (x) { return x != null; });
    const avg = confs.length ? confs.reduce(function (a, b) { return a + b; }, 0) / confs.length : null;

    const set = function (k, v) {
      const n = root.querySelector('[data-stat="' + k + '"]');
      if (n) n.textContent = v;
    };
    set("total", String(total));
    set("confirmed", String(confirmed));
    set("review", String(review));
    set("avg", percent(avg));

    if (list) {
      if (!items.length) {
        list.innerHTML =
          '<div class="empty-state">' + icon("leaf") +
          "<b>" + esc(t("dash.recentEmpty")) + "</b>" +
          '<button class="btn btn-primary btn-sm" data-go="studio">' + esc(t("dash.newScan")) + "</button></div>";
        list.querySelector("[data-go]").addEventListener("click", function () { ctx.go("studio"); });
      } else {
        list.innerHTML = items
          .slice(0, 5)
          .map(function (d) {
            const title = d.disease_label || d.crop_label || (getLang() === "ar" ? "تشخيص" : "Diagnosis");
            const sub =
              (d.crop_label ? d.crop_label + " · " : "") +
              fmtDate(d.created_at, true);
            return (
              '<div class="recent-item">' +
                '<span class="r-ico">' + icon("leaf", "sm") + "</span>" +
                '<span class="grow"><span class="r-t">' + esc(title) + "</span>" +
                '<span class="r-s">' + esc(sub) + "</span></span>" +
                statusBadge(d.status) +
              "</div>"
            );
          })
          .join("");
      }
    }
  } catch (e) {
    if (list) {
      list.innerHTML =
        '<div class="empty-state">' + icon("alert") +
        "<b>" + esc(t("dash.api.bad")) + "</b><p>" + esc(e.message || "") + "</p></div>";
    }
  }
}

/* =====================================================================
   DIAGNOSTIC STUDIO
   ===================================================================== */
const studioState = {
  file: null,
  previewUrl: null,
  result: null,
  chosen: null,
  busy: false,
  abort: null,
  timers: [],
};

function clearStudioTimers() {
  studioState.timers.forEach(clearTimeout);
  studioState.timers = [];
}

export function studioHTML() {
  const s = studioState;
  return (
    '<div class="row between wrap" style="margin-bottom:18px">' +
      "<div><h2 style=\"margin:0 0 4px;font-size:24px\">" + esc(t("studio.title")) + "</h2>" +
      '<p class="muted" style="margin:0;font-size:14px">' + esc(t("studio.sub")) + "</p></div>" +
      (s.result ? '<button class="btn btn-outline btn-sm" data-act="reset">' + icon("refresh", "sm") + esc(t("res.newScan")) + "</button>" : "") +
    "</div>" +
    '<div class="studio-grid" id="studioGrid">' +
      '<div id="upCol"></div>' +
      '<div id="resCol"></div>' +
    "</div>"
  );
}

function uploaderHTML() {
  const s = studioState;
  if (s.file && s.previewUrl) {
    return (
      '<div class="dropzone has-img">' +
        '<div class="preview">' +
          '<img src="' + esc(s.previewUrl) + '" alt="preview"/>' +
          '<div class="p-grad"></div>' +
          '<div class="p-bar">' +
            '<div class="p-meta"><div class="p-name">' + esc(s.file.name) + "</div>" +
              '<div class="p-size num">' + esc(fmtBytes(s.file.size)) + "</div></div>" +
            '<button class="icon-btn" data-act="remove" title="' + esc(t("studio.remove")) + '" style="background:rgba(0,0,0,.45);color:#fff;border-color:rgba(255,255,255,.25)">' +
              icon("trash", "sm") +
            "</button>" +
          "</div>" +
        "</div>" +
      "</div>" +
      '<div class="studio-actions">' +
        '<button class="btn btn-primary" data-act="analyze">' + icon("sparkles", "sm") +
          esc(s.result || studioState.result ? t("studio.analyze2") : t("studio.analyze")) + "</button>" +
        '<button class="btn btn-outline" data-act="other">' + icon("camera", "sm") + esc(t("studio.other")) + "</button>" +
      "</div>" +
      '<input type="file" id="fileInput" accept="image/png,image/jpeg,image/webp" hidden/>' +
      '<div id="progHost"></div>'
    );
  }
  return (
    '<div class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="' + esc(t("studio.dropT")) + '">' +
      '<div class="dz-inner">' +
        '<div class="dz-icon">' + icon("upload") + "</div>" +
        '<div class="dz-title">' + esc(t("studio.dropT")) + "</div>" +
        '<div class="dz-sub">' + esc(t("studio.dropS")) + "</div>" +
        '<div class="dz-rules">' +
          '<span class="badge b-slate num">' + esc(t("studio.rule1")) + "</span>" +
          '<span class="badge b-slate num">' + esc(t("studio.rule2")) + "</span>" +
          '<span class="badge b-slate">' + icon("check", "sm") + esc(t("studio.rule3")) + "</span>" +
        "</div>" +
      "</div>" +
    "</div>" +
    '<input type="file" id="fileInput" accept="image/png,image/jpeg,image/webp" hidden/>' +
    '<div id="progHost"></div>'
  );
}

function progressHTML() {
  const steps = ["up", "quality", "crop", "disease", "plan"];
  return (
    '<div class="card progress-card view-enter" style="margin-top:15px" id="progCard">' +
      '<div class="prog-head"><b>' + icon("loader", "sm spin") + " " + esc(t("prog.title")) + "</b>" +
        '<span class="prog-pct num" id="progPct">0%</span></div>' +
      '<div class="prog-track"><div class="prog-fill" id="progFill"></div></div>' +
      '<div class="prog-steps">' +
        steps
          .map(function (k, i) {
            return (
              '<div class="prog-step" data-pstep="' + i + '">' +
                '<span class="p-dot">' + icon("loader") + "</span>" +
                "<span>" + esc(t("prog." + k)) + "</span>" +
              "</div>"
            );
          })
          .join("") +
      "</div>" +
      '<div class="prog-hint" id="progHint" hidden>' +
        icon("clock", "sm") + " " + esc(t("prog.slowHint")) +
      "</div>" +
    "</div>"
  );
}

function qualityChips(iq) {
  if (!iq) return "";
  return (
    '<div class="quality-chips">' +
      '<span class="qchip num">' + icon("image", "sm") + iq.width + "×" + iq.height + "</span>" +
      '<span class="qchip num">' + icon("database", "sm") + esc(fmtBytes(iq.bytes_size)) + "</span>" +
      '<span class="qchip">' + icon(iq.is_blurry ? "alert" : "check", "sm") +
        esc(t("q.blur")) + ": " + (iq.is_blurry ? esc(t("q.blurry")) : esc(t("q.ok"))) +
        " · <span class=\"num\">" + (iq.blur_variance != null ? iq.blur_variance.toFixed(0) : "—") + "</span></span>" +
    "</div>"
  );
}

function planHTML(plan) {
  if (!plan) return "";
  const section = function (ic, key, inner) {
    if (!inner) return "";
    return (
      '<div class="plan-section"><h4>' + icon(ic, "sm") + esc(t(key)) + "</h4>" + inner + "</div>"
    );
  };
  const list = function (arr) {
    if (!arr || !arr.length) return "";
    return (
      '<div class="plan-list">' +
      arr
        .map(function (item, i) {
          return (
            '<div class="plan-item"><span class="pi-n num">' + (i + 1) + "</span><span>" + esc(pick(item)) + "</span></div>"
          );
        })
        .join("") +
      "</div>"
    );
  };

  let controls = "";
  if (plan.control_options && plan.control_options.length) {
    const typeBadge = function (tp) {
      const cls = tp === "chemical" ? "b-red" : tp === "organic" ? "b-green" : tp === "biological" ? "b-blue" : "b-slate";
      return '<span class="badge ' + cls + '">' + esc(tp) + "</span>";
    };
    controls =
      '<div class="plan-list">' +
      plan.control_options
        .map(function (o) {
          return (
            '<div class="control-opt">' +
              '<div class="co-head">' + typeBadge(o.type) + '<span class="co-act">' + esc(pick(o.action)) + "</span></div>" +
              (o.notes ? '<div class="co-notes">' + esc(pick(o.notes)) + "</div>" : "") +
            "</div>"
          );
        })
        .join("") +
      "</div>";
  }

  const disclaimer = plan.disclaimer
    ? '<div class="disclaimer">' + icon("alert", "sm") + "<span>" + esc(pick(plan.disclaimer)) + "</span></div>"
    : "";

  /* Observed symptoms extracted by the Gemini vision model (Arabic-first) */
  let symptoms = "";
  if (plan.symptoms && plan.symptoms.length) {
    symptoms =
      '<div class="sym-chips">' +
      plan.symptoms
        .map(function (s) {
          return '<span class="sym-chip">' + icon("alert", "sm") + esc(pick(s)) + "</span>";
        })
        .join("") +
      "</div>";
  }

  return (
    '<div class="plan">' +
      section("clipboard", "plan.summary", plan.summary ? '<div class="plan-summary">' + esc(pick(plan.summary)) + "</div>" : "") +
      section("stethoscope", "plan.symptoms", symptoms) +
      section("zap", "plan.immediate", list(plan.immediate_actions)) +
      section("flask", "plan.control", controls) +
      section("eye", "plan.monitoring", list(plan.monitoring)) +
      section("shield", "plan.prevention", list(plan.prevention)) +
      disclaimer +
    "</div>"
  );
}

function resultHTML(r) {
  const conf = r.disease_confidence != null ? r.disease_confidence : r.crop_confidence;
  const g = gauge(conf || 0);
  const headCls = statusHeadClass(r.status);
  const title = r.disease_label || r.crop_label || (getLang() === "ar" ? "التشخيص" : "Diagnosis");
  const cropLine = (r.crop_label || "") + (r.crop_source ? " · " + r.crop_source : "");

  const candidates =
    r.requires_crop_selection && r.candidates && r.candidates.length
      ? '<div class="plan"><div class="plan-section"><h4>' + icon("sprout", "sm") + esc(t("cand.title")) + "</h4>" +
        '<p class="muted tiny" style="margin-bottom:12px">' + esc(t("cand.sub")) + "</p>" +
        '<div class="cand-grid" id="candGrid">' +
          r.candidates
            .map(function (c) {
              return (
                '<button class="cand" data-cand="' + esc(c.id) + '">' +
                  '<span class="c-radio"></span>' +
                  '<span class="c-info"><span class="c-name">' + esc(c.label) + "</span>" +
                    '<span class="c-conf num">' + esc(c.id) + " · " + percent(c.confidence) + "</span></span>" +
                  '<span class="c-bar"><i style="width:' + Math.round((c.confidence || 0) * 100) + '%"></i></span>' +
                "</button>"
              );
            })
            .join("") +
        "</div>" +
        '<button class="btn btn-primary btn-block" style="margin-top:14px" data-act="confirmCrop" disabled>' +
          esc(t("cand.confirm")) + "</button>" +
        "</div></div>"
      : "";

  const stateCard =
    r.status === "retake_required"
      ? '<div class="state-card"><div class="st-ic bad">' + icon("camera") + "</div>" +
        "<h3>" + esc(t("retake.title")) + "</h3><p>" + esc(t("retake.sub")) + "</p>" +
        '<div class="st-tips">' +
          [1, 2, 3]
            .map(function (i) {
              return '<div class="li">' + icon("check", "sm") + "<span>" + esc(t("retake.tip" + i)) + "</span></div>";
            })
            .join("") +
        "</div>" +
        '<button class="btn btn-primary" data-act="retake" style="margin-top:8px">' + icon("camera", "sm") + esc(t("retake.btn")) + "</button>" +
        "</div>"
      : r.status === "in_review"
      ? '<div class="state-card"><div class="st-ic warn">' + icon("clock") + "</div>" +
        "<h3>" + esc(t("review.title")) + "</h3><p>" + esc(t("review.sub")) + "</p></div>"
      : "";

  return (
    '<div class="result-card card view-enter" id="resultCard">' +
      '<div class="result-head ' + headCls + '"></div>' +
      qualityChips(r.image_quality) +
      candidates +
      (r.treatment_plan ? planHTML(r.treatment_plan) : "") +
      stateCard +
      '<div class="result-foot">' +
        '<button class="btn btn-outline" data-act="askAbout">' + icon("message", "sm") + esc(t("res.askBot")) + "</button>" +
        '<button class="btn btn-ghost" data-act="goHistory">' + icon("history", "sm") + esc(t("res.viewHistory")) + "</button>" +
      "</div>" +
    "</div>"
  ).replace(
    'class="result-head ' + headCls + '"></div>',
    'class="result-head ' + headCls + '"></div>'
  );
}

function mountResultInto(host, r) {
  host.innerHTML = "";
  const wrap = el('<div class="view-enter"></div>');

  const conf = r.disease_confidence != null ? r.disease_confidence : r.crop_confidence;
  const headCls = statusHeadClass(r.status);
  const title = r.disease_label || r.crop_label || (getLang() === "ar" ? "التشخيص" : "Diagnosis");

  const card = el('<div class="result-card card" id="resultCard"></div>');
  const head = el('<div class="result-head ' + headCls + '"></div>');
  const g = gauge(conf || 0);
  head.appendChild(g.node);
  head.appendChild(
    el(
      '<div class="result-title">' +
        '<div class="rt-status">' + statusBadge(r.status) +
          (conf != null ? '<span class="badge b-lime num">' + percent(conf) + "</span>" : "") +
        "</div>" +
        "<h3>" + esc(title) + "</h3>" +
        '<div class="rt-crop">' + esc(r.crop_label || "—") +
          (r.crop_source ? " · " + esc(r.crop_source) : "") + "</div>" +
      "</div>"
    )
  );
  card.appendChild(head);

  /* offline mock fallback marker — honest badge, never an error */
  if (r.source === "mock") {
    card.appendChild(
      el(
        '<div class="mock-note">' + icon("info", "sm") +
          "<span>" + esc(t("res.mock")) + "</span></div>"
      )
    );
  }

  const qc = qualityChips(r.image_quality);
  if (qc) card.appendChild(el("<div>" + qc + "</div>"));

  /* candidates */
  if (r.requires_crop_selection && r.candidates && r.candidates.length) {
    const candWrap = el(
      '<div class="plan"><div class="plan-section"><h4>' + icon("sprout", "sm") + esc(t("cand.title")) + "</h4>" +
        '<p class="muted tiny" style="margin:-4px 0 12px">' + esc(t("cand.sub")) + "</p>" +
        '<div class="cand-grid" id="candGrid"></div>' +
        '<button class="btn btn-primary btn-block" style="margin-top:14px" data-act="confirmCrop" disabled>' +
          esc(t("cand.confirm")) + "</button></div></div>"
    );
    const grid = candWrap.querySelector("#candGrid");
    const confirmBtn = candWrap.querySelector('[data-act="confirmCrop"]');
    r.candidates.forEach(function (c) {
      const b = el(
        '<button class="cand" data-cand="' + esc(c.id) + '">' +
          '<span class="c-radio"></span>' +
          '<span class="c-info"><span class="c-name">' + esc(c.label) + "</span>" +
            '<span class="c-conf num">' + esc(c.id) + " · " + percent(c.confidence) + "</span></span>" +
          '<span class="c-bar"><i style="width:0%"></i></span>' +
        "</button>"
      );
      grid.appendChild(b);
      setTimeout(function () {
        const bar = b.querySelector(".c-bar i");
        if (bar) bar.style.width = Math.round((c.confidence || 0) * 100) + "%";
      }, 260);
      b.addEventListener("click", function () {
        grid.querySelectorAll(".cand").forEach(function (x) { x.classList.remove("sel"); });
        b.classList.add("sel");
        studioState.chosen = c.id;
        confirmBtn.disabled = false;
      });
    });
    card.appendChild(candWrap);
    confirmBtn.addEventListener("click", function () {
      if (!studioState.chosen) return;
      runDiagnose(studioState.chosen);
    });
  }

  if (r.treatment_plan) card.appendChild(el(planHTML(r.treatment_plan)));

  if (r.status === "retake_required") {
    const st = el(
      '<div class="state-card"><div class="st-ic bad">' + icon("camera") + "</div>" +
        "<h3>" + esc(t("retake.title")) + "</h3><p>" + esc(t("retake.sub")) + "</p>" +
        '<div class="st-tips">' +
          [1, 2, 3]
            .map(function (i) {
              return '<div class="li">' + icon("checkCircle", "sm") + "<span>" + esc(t("retake.tip" + i)) + "</span></div>";
            })
            .join("") +
        "</div>" +
        '<button class="btn btn-primary" data-act="retake" style="margin-top:8px">' +
          icon("camera", "sm") + esc(t("retake.btn")) + "</button></div>"
    );
    st.querySelector('[data-act="retake"]').addEventListener("click", function () { resetStudio(); });
    card.appendChild(st);
  } else if (r.status === "in_review") {
    card.appendChild(
      el(
        '<div class="state-card" style="padding-top:22px"><div class="st-ic warn">' + icon("clock") + "</div>" +
          "<h3>" + esc(t("review.title")) + "</h3><p>" + esc(t("review.sub")) + "</p></div>"
      )
    );
  }

  /* footer actions */
  const foot = el(
    '<div class="result-foot">' +
      '<button class="btn btn-outline" data-act="askAbout">' + icon("message", "sm") + esc(t("res.askBot")) + "</button>" +
      '<button class="btn btn-ghost" data-act="goHistory">' + icon("history", "sm") + esc(t("res.viewHistory")) + "</button>" +
    "</div>"
  );
  foot.querySelector('[data-act="askAbout"]').addEventListener("click", function () {
    const q =
      getLang() === "ar"
        ? "عندي تشخيص: " + (r.disease_label || "") + " على " + (r.crop_label || "") + " — ما نصيحتك؟"
        : "I got a diagnosis of " + (r.disease_label || "") + " on " + (r.crop_label || "") + " — what do you advise?";
    if (window.__hassadOpenChat) window.__hassadOpenChat(q);
  });
  foot.querySelector('[data-act="goHistory"]').addEventListener("click", function () {
    if (window.__hassadGo) window.__hassadGo("history");
  });
  card.appendChild(foot);

  wrap.appendChild(card);
  host.appendChild(wrap);
  if (conf != null) g.setValue(conf);
  studioState.result = r;
}

/* ---------- studio: diagnostics run -------------------------------- */
async function runDiagnose(chosenCrop) {
  const s = studioState;
  if (!s.file) {
    toast(t("toasts.needImage"), "err");
    return;
  }
  if (s.busy) return;
  s.busy = true;
  clearStudioTimers();

  const resCol = document.getElementById("resCol");
  const upCol = document.getElementById("upCol");
  const progHost = document.getElementById("progHost");
  if (progHost) {
    progHost.innerHTML = progressHTML();
    progHost.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  const setPct = function (p) {
    const fill = document.getElementById("progFill");
    const pct = document.getElementById("progPct");
    if (fill) fill.style.width = Math.round(p * 100) + "%";
    if (pct) pct.textContent = Math.round(p * 100) + "%";
  };
  const setStep = function (i, state) {
    const n = document.querySelector('[data-pstep="' + i + '"]');
    if (!n) return;
    n.classList.remove("active", "done", "fail");
    if (state) n.classList.add(state);
    const dot = n.querySelector(".p-dot");
    if (state === "done") dot.innerHTML = icon("check");
    else if (state === "fail") dot.innerHTML = icon("x");
    else dot.innerHTML = icon("loader");
  };

  s.abort = new AbortController();
  const fd = new FormData();
  fd.append("file", s.file, s.file.name);
  if (chosenCrop) fd.append("selected_crop", chosenCrop);
  if (s.result && s.result.diagnosis_id) fd.append("diagnosis_id", s.result.diagnosis_id);
  /* unique per run: the API treats (user, client_id) as a resume key, so a
     constant id would silently overwrite the previous diagnosis */
  fd.append(
    "client_id",
    "web-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10)
  );

  let uploadDone = false;
  const t0 = Date.now();
  /* staged visual progress while the AI pipeline runs */
  setStep(0, "active");
  s.timers.push(setTimeout(function () { if (uploadDone) setPct(0.6); }, 350));
  /* long-run affordance: the vision budget is 45s — surface a calm hint
     only when the run is actually slow, so short runs stay quiet */
  s.timers.push(setTimeout(function () {
    const hint = document.getElementById("progHint");
    if (hint) hint.hidden = false;
  }, 7000));

  try {
    const resp = await api.diagnose(
      fd,
      function (p) {
        uploadDone = p >= 0.999;
        setPct(Math.min(0.5, p * 0.5));
        if (p >= 0.999) {
          setStep(0, "done");
          setStep(1, "active");
        }
      },
      s.abort.signal
    );

    /* cascade remaining steps quickly (request already answered) */
    setPct(0.92);
    setStep(0, "done");
    setStep(1, "done");
    setStep(2, "active");
    s.timers.push(setTimeout(function () { setStep(2, "done"); setStep(3, "active"); setPct(0.96); }, 260));
    s.timers.push(setTimeout(function () { setStep(3, "done"); setStep(4, "active"); setPct(0.99); }, 540));
    s.timers.push(
      setTimeout(function () {
        setStep(4, "done");
        setPct(1);
        finishResult(resp);
      }, 820)
    );
  } catch (e) {
    const progCard = document.getElementById("progCard");
    if (progCard) {
      const active = progCard.querySelector(".prog-step.active");
      if (active) {
        active.classList.remove("active");
        active.classList.add("fail");
        active.querySelector(".p-dot").innerHTML = icon("x");
      }
    }
    s.busy = false;
    errToast(e);
    if (resCol && resCol.querySelector("#resultCard") === null && s.result) {
      mountResultInto(resCol, s.result);
    }
  }
}

function finishResult(resp) {
  const s = studioState;
  s.busy = false;
  if (!resp) return;
  const resCol = document.getElementById("resCol");
  const progHost = document.getElementById("progHost");
  if (progHost) progHost.innerHTML = "";
  if (!resCol) return;
  mountResultInto(resCol, resp);
  if (resp.status === "confirmed") {
    /* mock results announce themselves instead of a plain success toast */
    toast(
      resp.source === "mock" ? t("res.mockShort") : t("toasts.ok"),
      resp.source === "mock" ? "info" : "ok"
    );
  }
}

function resetStudio() {
  const s = studioState;
  clearStudioTimers();
  if (s.abort) s.abort.abort();
  if (s.previewUrl) URL.revokeObjectURL(s.previewUrl);
  s.file = null;
  s.previewUrl = null;
  s.result = null;
  s.chosen = null;
  s.busy = false;
  s.abort = null;
  if (window.__hassadRenderRoute) window.__hassadRenderRoute();
}

/* ---------- studio: view + bind ------------------------------------ */
export function renderStudio(host, ctx) {
  const s = studioState;
  const grid = el('<div></div>');
  grid.innerHTML = studioHTML();
  host.appendChild(grid);

  const upCol = grid.querySelector("#upCol");
  const resCol = grid.querySelector("#resCol");
  upCol.innerHTML = uploaderHTML();
  if (s.result) mountResultInto(resCol, s.result);

  const resetBtn = grid.querySelector('[data-act="reset"]');
  if (resetBtn) resetBtn.addEventListener("click", resetStudio);

  bindUploader(upCol);
}

function bindUploader(upCol) {
  const s = studioState;
  const dz = upCol.querySelector("#dropzone");
  const input = upCol.querySelector("#fileInput");

  const pickFile = function () {
    if (input) input.click();
  };
  if (dz) {
    dz.addEventListener("click", pickFile);
    dz.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pickFile();
      }
    });
    ["dragenter", "dragover"].forEach(function (ev) {
      dz.addEventListener(ev, function (e) {
        e.preventDefault();
        e.stopPropagation();
        dz.classList.add("drag");
      });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      dz.addEventListener(ev, function (e) {
        e.preventDefault();
        e.stopPropagation();
        dz.classList.remove("drag");
      });
    });
    dz.addEventListener("drop", function (e) {
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) acceptFile(f);
    });
  }
  if (input) {
    input.addEventListener("change", function () {
      if (input.files && input.files[0]) acceptFile(input.files[0]);
    });
  }

  const rm = upCol.querySelector('[data-act="remove"]');
  if (rm) rm.addEventListener("click", function () { resetStudio(); });
  const other = upCol.querySelector('[data-act="other"]');
  if (other) other.addEventListener("click", function () { resetStudio(); setTimeout(pickFile, 60); });
  const analyze = upCol.querySelector('[data-act="analyze"]');
  if (analyze) analyze.addEventListener("click", function () { runDiagnose(null); });
}

function acceptFile(f) {
  const s = studioState;
  const ok = ["image/png", "image/jpeg", "image/webp"];
  if (ok.indexOf(f.type) < 0) {
    toast(t("studio.rule1") + " only", "err");
    return;
  }
  if (f.size > 8 * 1024 * 1024) {
    toast(t("err.tooLarge"), "err");
    return;
  }
  if (s.previewUrl) URL.revokeObjectURL(s.previewUrl);
  s.file = f;
  s.previewUrl = URL.createObjectURL(f);
  s.result = null;
  s.chosen = null;
  /* re-render this column */
  const upCol = document.getElementById("upCol");
  if (upCol) {
    upCol.innerHTML = uploaderHTML();
    bindUploader(upCol);
    upCol.querySelector(".dropzone").classList.add("view-enter");
  }
  const resCol = document.getElementById("resCol");
  if (resCol) resCol.innerHTML = "";
  toast(t("toasts.uploaded"), "info");
}

/* =====================================================================
   HISTORY
   ===================================================================== */
let histFilter = "all";

export function renderHistory(host, ctx) {
  const wrap = el('<div class="view-enter"></div>');
  wrap.innerHTML =
    '<div class="row between wrap" style="margin-bottom:16px">' +
      "<div><h2 style=\"margin:0 0 4px;font-size:24px\">" + esc(t("hist.title")) + "</h2>" +
      '<p class="muted" style="margin:0;font-size:14px">' + esc(t("hist.sub")) + "</p></div>" +
      '<button class="btn btn-primary btn-sm" data-go="studio">' + icon("plus", "sm") + esc(t("dash.newScan")) + "</button>" +
    "</div>" +
    '<div class="filter-chips" id="histFilters"></div>' +
    '<div class="hist-list" id="histList"></div>';
  host.appendChild(wrap);

  wrap.querySelector("[data-go]").addEventListener("click", function () { ctx.go("studio"); });

  const filters = wrap.querySelector("#histFilters");
  const defs = [
    { k: "all", label: t("hist.filter.all") },
    { k: "confirmed", label: t("res.status.confirmed") },
    { k: "in_review", label: t("res.status.in_review") },
    { k: "retake_required", label: t("res.status.retake_required") },
    { k: "queued", label: t("res.status.queued") },
  ];
  defs.forEach(function (d) {
    const b = el('<button class="chip' + (histFilter === d.k ? " sel" : "") + '" data-f="' + d.k + '">' + esc(d.label) + "</button>");
    b.addEventListener("click", function () {
      histFilter = d.k;
      filters.querySelectorAll(".chip").forEach(function (x) { x.classList.remove("sel"); });
      b.classList.add("sel");
      load();
    });
    filters.appendChild(b);
  });

  const list = wrap.querySelector("#histList");

  async function load() {
    list.innerHTML =
      '<div class="panel"><div class="panel-body">' +
      '<div class="skeleton sk-line w80"></div><div class="skeleton sk-line w60"></div>' +
      '<div class="skeleton sk-line w40"></div></div></div>';
    try {
      const data = await api.diagnoses(100, 0);
      let items = (data && data.items) || [];
      if (histFilter !== "all") items = items.filter(function (x) { return x.status === histFilter; });

      if (!items.length) {
        list.innerHTML =
          '<div class="empty-state card" style="padding:46px 20px">' + icon("leaf") +
          "<b>" + esc(t("hist.empty")) + "</b><p>" + esc(t("hist.emptyS")) + "</p>" +
          '<button class="btn btn-primary btn-sm" data-go>' + esc(t("dash.newScan")) + "</button></div>";
        list.querySelector("[data-go]").addEventListener("click", function () { ctx.go("studio"); });
        return;
      }

      list.innerHTML = "";
      items.forEach(function (d) {
        const title = d.disease_label || d.crop_label || (getLang() === "ar" ? "تشخيص" : "Diagnosis");
        const conf = d.disease_confidence != null ? d.disease_confidence : d.crop_confidence;
        const item = el(
          '<div class="hist-item">' +
            '<div class="hist-head" tabindex="0" role="button">' +
              '<span class="h-ico">' + icon("leaf") + "</span>" +
              '<span class="grow"><span class="h-t">' + esc(title) + "</span>" +
                '<span class="h-s">' + esc((d.crop_label ? d.crop_label + " · " : "") + fmtDate(d.created_at, true)) + "</span></span>" +
              '<span class="h-right">' +
                (conf != null ? '<span class="hist-conf num" style="color:var(--brand-2)">' + percent(conf) + "</span>" : "") +
                statusBadge(d.status) +
                '<span class="h-chevron">' + icon("chevronDown", "sm") + "</span>" +
              "</span>" +
            "</div>" +
            '<div class="hist-body"></div>' +
          "</div>"
        );
        const body = item.querySelector(".hist-body");

        const details = el(
          '<div style="padding-top:14px"></div>'
        );
        const rows = [];
        if (d.crop_label) rows.push([t("res.crop"), d.crop_label]);
        if (d.disease_label) rows.push([t("res.disease"), d.disease_label]);
        if (d.source === "mock") rows.push([t("res.mockShort"), t("chat.mode.local")]);
        rows.push([t("hist.id"), d.id]);
        rows.push([t("hist.date"), fmtDate(d.created_at, true)]);
        if (d.notes) rows.push([t("hist.notes"), d.notes]);

        details.innerHTML =
          '<div class="plan-list" style="margin-bottom:13px">' +
            rows
              .map(function (r) {
                return (
                  '<div class="plan-item"><span class="pi-n">' + icon("info", "sm") + "</span>" +
                  "<span><b>" + esc(r[0]) + ":</b> " + esc(r[1]) + "</span></div>"
                );
              })
              .join("") +
          "</div>" +
          (d.treatment_plan && d.treatment_plan.summary
            ? '<div class="plan-summary" style="margin-bottom:13px">' + esc(pick(d.treatment_plan.summary)) + "</div>"
            : "") +
          '<div class="row wrap"><button class="btn btn-outline btn-sm" data-a="ask">' +
            icon("message", "sm") + esc(t("res.askBot")) + "</button>" +
            '<button class="btn btn-danger btn-sm" data-a="del">' + icon("trash", "sm") + esc(t("common.delete")) + "</button></div>";

        details.querySelector('[data-a="ask"]').addEventListener("click", function (e) {
          e.stopPropagation();
          const q =
            getLang() === "ar"
              ? "بخصوص تشخيص " + (d.disease_label || "") + " على " + (d.crop_label || "") + " — كيف أتابع العلاج؟"
              : "About the diagnosis of " + (d.disease_label || "") + " on " + (d.crop_label || "") + " — how should I follow up?";
          if (window.__hassadOpenChat) window.__hassadOpenChat(q);
        });
        details.querySelector('[data-a="del"]').addEventListener("click", async function (e) {
          e.stopPropagation();
          const ok = await confirmDialog(t("hist.deleteAsk"), t("hist.deleteS"), true);
          if (!ok) return;
          try {
            await api.deleteDiagnosis(d.id);
            toast(t("toasts.deleted"), "ok");
            item.remove();
          } catch (err) {
            errToast(err);
          }
        });

        body.appendChild(details);
        const head = item.querySelector(".hist-head");
        const toggle = function () { item.classList.toggle("open"); };
        head.addEventListener("click", toggle);
        head.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        });
        list.appendChild(item);
      });
    } catch (e) {
      list.innerHTML =
        '<div class="empty-state card" style="padding:46px 20px">' + icon("alert") +
        "<b>" + esc(t("dash.api.bad")) + "</b><p>" + esc(e.message || "") + "</p></div>";
    }
  }

  load();
}

/* =====================================================================
   AUTH MODAL
   ===================================================================== */
export function openAuthModal(ctx, mode) {
  let current = mode === "reg" ? "reg" : "login";

  const node = el(
    '<div>' +
      '<div class="auth-logo">' +
        '<span class="mark">' + icon("leaf") + "</span>" +
        '<h3 data-title></h3><p data-sub></p>' +
      "</div>" +
      '<div class="auth-tabs"><i></i>' +
        '<button class="auth-tab" data-tab="login"></button>' +
        '<button class="auth-tab" data-tab="reg"></button>' +
      "</div>" +
      '<form novalidate data-form></form>' +
    "</div>"
  );

  const m = openModal({ node: node, size: "sm" });
  const tabsWrap = node.querySelector(".auth-tabs");
  const title = node.querySelector("[data-title]");
  const sub = node.querySelector("[data-sub]");
  const form = node.querySelector("[data-form]");

  function fieldsHTML(mode) {
    const nameField =
      mode === "reg"
        ? '<div class="field" data-f="full_name"><label class="label">' + icon("user", "sm") + esc(t("auth.name")) +
          '</label><input class="input" name="full_name" autocomplete="name" placeholder="' +
          esc(getLang() === "ar" ? "مثال: أحمد المزارع" : "e.g. Ahmed Farmer") + '"/>' +
          '<span class="field-err">' + esc(t("auth.err.name")) + "</span></div>"
        : "";
    const roleField =
      mode === "reg"
        ? '<div class="field"><label class="label">' + icon("award", "sm") + esc(t("auth.role")) + "</label>" +
          '<div class="radio-row">' +
            '<label class="radio-pill"><input type="radio" name="role" value="farmer" checked/>' +
              "<span>" + icon("sprout") + esc(t("auth.roleFarmer")) + "<small>" + esc(t("auth.roleFarmerS")) + "</small></span></label>" +
            '<label class="radio-pill"><input type="radio" name="role" value="agronomist"/>' +
              "<span>" + icon("stethoscope") + esc(t("auth.roleAgronomist")) + "<small>" + esc(t("auth.roleAgronomistS")) + "</small></span></label>" +
          "</div></div>"
        : "";

    return (
      nameField +
      '<div class="field" data-f="email"><label class="label">' + icon("mail", "sm") + esc(t("auth.email")) +
        '</label><input class="input" name="email" type="email" autocomplete="email" placeholder="you@example.com" dir="ltr"/>' +
        '<span class="field-err">' + esc(t("auth.err.email")) + "</span></div>" +
      '<div class="field" data-f="password"><label class="label">' + icon("lock", "sm") + esc(t("auth.password")) +
        '</label><div class="row" style="gap:8px"><input class="input" name="password" type="password" autocomplete="' +
        (mode === "reg" ? "new-password" : "current-password") + '" placeholder="••••••••" dir="ltr" style="flex:1"/>' +
        '<button type="button" class="icon-btn" data-eye aria-label="' + esc(t("auth.showPw")) + '">' + icon("eye", "sm") + "</button></div>" +
        (mode === "reg" ? '<div class="strength" data-strength><i></i><i></i><i></i><i></i></div><span class="hint" data-strength-lbl></span>' : "") +
        '<span class="field-err">' + esc(t("auth.err.pw")) + "</span></div>" +
      roleField +
      '<button class="btn btn-primary btn-block btn-lg" type="submit" data-submit style="margin-top:6px"></button>' +
      '<p class="center muted tiny" style="margin:16px 0 0" data-switch></p>'
    );
  }

  function paint() {
    const isLogin = current === "login";
    tabsWrap.classList.toggle("tab-reg", !isLogin);
    tabsWrap.querySelector('[data-tab="login"]').classList.toggle("active", isLogin);
    tabsWrap.querySelector('[data-tab="reg"]').classList.toggle("active", !isLogin);
    tabsWrap.querySelector('[data-tab="login"]').textContent = t("auth.tabLogin");
    tabsWrap.querySelector('[data-tab="reg"]').textContent = t("auth.tabReg");
    title.textContent = isLogin ? t("auth.loginT") : t("auth.regT");
    sub.textContent = isLogin ? t("auth.loginS") : t("auth.regS");
    form.innerHTML = fieldsHTML(current);
    form.querySelector("[data-submit]").textContent = isLogin ? t("auth.submitLogin") : t("auth.submitReg");
    form.querySelector("[data-switch]").innerHTML = isLogin
      ? esc(t("auth.noAccount")) + ' <b style="color:var(--brand-2);cursor:pointer" data-sw>' + esc(t("auth.tabReg")) + "</b>"
      : esc(t("auth.hasAccount")) + ' <b style="color:var(--brand-2);cursor:pointer" data-sw>' + esc(t("auth.tabLogin")) + "</b>";

    /* show/hide password */
    const eye = form.querySelector("[data-eye]");
    const pw = form.querySelector('[name="password"]');
    eye.addEventListener("click", function () {
      const show = pw.type === "password";
      pw.type = show ? "text" : "password";
      eye.innerHTML = icon(show ? "eyeOff" : "eye", "sm");
    });

    /* strength meter (register only) */
    const meter = form.querySelector("[data-strength]");
    if (meter) {
      const lbl = form.querySelector("[data-strength-lbl]");
      const update = function () {
        const v = pw.value;
        let score = 0;
        if (v.length >= 8) score++;
        if (/[A-Za-z]/.test(v) && /\d/.test(v)) score++;
        if (v.length >= 12) score++;
        if (/[^A-Za-z0-9]/.test(v)) score++;
        meter.className = "strength" + (v ? " s" + Math.min(4, Math.max(1, score)) : "");
        lbl.textContent = v ? tAt("auth.strength", Math.min(3, Math.max(0, score - 1))) + " · " + t("auth.pwHint") : t("auth.pwHint");
      };
      pw.addEventListener("input", update);
      update();
    }

    form.querySelector("[data-sw]").addEventListener("click", function () {
      current = isLogin ? "reg" : "login";
      paint();
    });
    /* NOTE: the submit listener is bound ONCE below — paint() re-runs on
       every tab switch and must not stack duplicate listeners */
  }

  function fieldError(name, msg) {
    const f = form.querySelector('[data-f="' + name + '"]');
    if (!f) return false;
    f.classList.add("err");
    if (msg) f.querySelector(".field-err").textContent = msg;
    return true;
  }

  async function onSubmit(e) {
    e.preventDefault();
    form.querySelectorAll(".field").forEach(function (f) { f.classList.remove("err"); });

    const data = new FormData(form);
    const email = String(data.get("email") || "").trim();
    const password = String(data.get("password") || "");
    const name = String(data.get("full_name") || "").trim();
    let bad = false;

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) bad = fieldError("email", t("auth.err.email")) || bad;
    if (current === "reg" && password.length < 8) bad = fieldError("password", t("auth.err.pw")) || bad;
    if (current === "reg" && (!/[A-Za-z]/.test(password) || !/\d/.test(password))) {
      bad = fieldError("password", t("auth.err.pw")) || bad;
    }
    if (current === "reg" && name.length < 2) bad = fieldError("full_name", t("auth.err.name")) || bad;
    if (bad) return;

    const submit = form.querySelector("[data-submit]");
    await busy(submit, t("common.loading"), async function () {
      try {
        if (current === "reg") {
          await api.register({
            email: email,
            password: password,
            full_name: name,
            role: String(data.get("role") || "farmer"),
            locale: getLang(),
          });
        }
        const tok = await api.login(email, password);
        api.setTokens(tok);
        const user = await api.me();
        m.close();
        toast(t(current === "reg" ? "auth.ok.reg" : "auth.ok.login"), "ok");
        ctx.onAuthed(user);
      } catch (err) {
        /* map validation errors to fields when possible */
        let mapped = false;
        if (err && err.status === 422) {
          const msg = err.message || "";
          if (/email/i.test(msg)) mapped = fieldError("email", msg);
          else if (/password/i.test(msg)) mapped = fieldError("password", msg);
          else if (/name/i.test(msg)) mapped = fieldError("full_name", msg);
        }
        if (!mapped) {
          const msg =
            err && err.status === 401 ? t("err.creds")
              : err && err.status === 0 ? t("err.network")
                : (err && err.message) || t("common.error");
          toast(msg, "err");
        }
      }
    });
  }

  tabsWrap.querySelectorAll("[data-tab]").forEach(function (b) {
    b.addEventListener("click", function () {
      if (current === b.dataset.tab) return;
      current = b.dataset.tab;
      paint();
    });
  });

  form.addEventListener("submit", onSubmit); /* single binding — survives paint() */
  paint();
  setTimeout(function () {
    const first = form.querySelector("input");
    if (first) first.focus();
  }, 120);
  return m;
}
