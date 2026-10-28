/* =========================================================================
   Three-minute presentation recorder.

   Press one button and this module does the rest: it captures the tab, walks
   the real interface on a fixed 180-second clock, narrates each chapter, and
   hands back a video file without asking again.

   WHAT IS REAL, AND WHAT IS NOT
   -----------------------------
   The picture is the browser's own surface — getDisplayMedia, cropped to
   #app, muxed by MediaRecorder. No frame is redrawn offscreen, so the file
   shows what the interface actually did.

   The pointer is a drawing. It glides to a real control and dispatches a real
   click. Three presses are deliberately cosmetic, and each is marked where it
   happens: the upload dropzone (a real click opens a file dialog and stalls
   the take), Analyze (a real click waits on a live model), and the assistant
   (a real question costs a network round-trip mid-take). The upload itself is
   genuine — a real JPEG is placed on the real input — and both the report and
   the assistant exchange are samples, badged SAMPLE, so the film never
   claims a model produced them.

   AUDIO
   -----
   Narration is spoken with the Web Speech API. That audio is produced by the
   operating system, NOT by this page, so it cannot be routed into a
   MediaStream node — the browser gives no handle on it. What this module can
   do is ask for system audio alongside the picture; when the take is granted
   that track, the voice that plays through the speakers is inside the file.
   When it is not, the file still exports and the panel says, in plain words,
   that the voiceover did not make it in. It never claims otherwise.

   The same is true of the timeline: absolute cue times, so a slow frame or a
   long scroll cannot push the ending later and clip the closing card.
   ========================================================================= */

import { $, show } from "./dom.js";
import { t } from "./i18n.js";
import {
  mount, unmount, tap, reveal, spotOff, caption, titleCard, outro, fadeTo, countdown, hideCursor,
} from "./recordfx.js";

const TOTAL = 180_000;           // three minutes, fixed
const BITRATE = 12_000_000;      // screen content, 1080p60 — roughly 250 MB
const FPS = 60;

/** Tried in order; the first the browser confirms wins. MP4 first, because
 *  that is what the button promises — but a container is never renamed to
 *  match a label, so a WebM result keeps its .webm extension. */
const MIMES = [
  'video/mp4; codecs="avc1.640033"',
  'video/mp4; codecs="avc1.42E01E"',
  "video/mp4",
  'video/x-matroska; codecs="avc1.42E01E"',
  'video/webm; codecs="vp9"',
  "video/webm",
];

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const q = (selector) => document.querySelector(selector);

/* ------------------------------------------------------------- narration -- */

/** Four chapters, sized to the four slots. Each is spoken from its own cue,
 *  and the previous utterance is cancelled at the boundary, so a slow voice
 *  can never drift into the next chapter. */
const SCRIPT = {
  c1:
    "Every season begins with a single leaf. In the high plateau of Tebessa, " +
    "three hundred millimetres of erratic rain falls on alkaline soil, crops are " +
    "planted on faith, and the nearest plant laboratory is a hundred kilometres " +
    "away. A spot appears. A margin yellows. By the time the pattern is " +
    "unmistakable, the harvest has already paid for the delay. Hassad AI turns " +
    "one photograph into a named condition, a measured severity, and a treatment " +
    "plan written for this soil and this season, in Arabic and in English.",
  c2:
    "Here is the whole workflow. Drop a leaf into the studio. The image is " +
    "compressed in the browser before it is sent, once. The model identifies the " +
    "plant before it names a disease, because a wrong species produces a wrong " +
    "treatment. Then it reports what it can actually see: concentric lesions, a " +
    "chlorotic halo, the lower leaves failing first. And it commits to a verdict, " +
    "with the confidence attached. Potato. Early blight. Alternaria solani. " +
    "Moderate severity, ninety four percent confidence. Symptoms on one side, a " +
    "treatment ladder on the other, with a seven day interval and a rotation away " +
    "from solanaceous crops. The irrigation schedule is written for drip, not " +
    "overhead, because overhead water spreads the spores. Every plan ends with a " +
    "disclaimer, because one leaf is one reading.",
  c3:
    "Now switch the interface to Arabic. The layout turns with it: labels, " +
    "tables, and the diagnosis itself, right to left, without a reload. A farmer " +
    "should not have to read English to protect a crop. And when the report is " +
    "not enough, the agronomist takes over. Ask a question in plain language and " +
    "the answer is grounded in the plant that was actually analysed, its species, " +
    "its pathogen, its severity, not in a generic leaf. That is the difference " +
    "between a search engine and someone who has walked your field.",
  c4:
    "Hassad AI is open source. The engine, the bilingual knowledge base, and this " +
    "presentation live in one repository, built to be read, run, and corrected. " +
    "Crop diagnosis for every farmer. The code is on GitHub.",
};

const CHAPTERS = [
  { at: 700, text: SCRIPT.c1 },
  { at: 45_000, text: SCRIPT.c2 },
  { at: 120_000, text: SCRIPT.c3 },
  { at: 160_000, text: SCRIPT.c4 },
];

/* ---------------------------------------------------------- sample cards -- */

/** Reaches the screen through diagnose.renderSample(), which badges the card
 *  SAMPLE. Nothing here claims a model read this leaf. */
const SAMPLE = {
  status: "confirmed",
  confidence: { overall: 0.94, provisional: false },
  diagnosis: {
    crop: "Potato",
    crop_ar: "بطاطا",
    disease: "Early blight",
    disease_ar: "اللفحة المبكرة",
    pathogen: "Alternaria solani",
    status: "diseased",
    healthy: false,
    uncertain: false,
    low_confidence: false,
    severity: "moderate",
    severity_pct: 0.38,     // a float in [0,1], like the API's own field
    confidence: 0.94,
    image_quality: "good",
    confidence_basis: "Concentric lesions and a chlorotic halo on the lower leaves.",
    symptoms: [
      "Concentric brown lesions on the older leaves",
      "A narrow yellow halo around each lesion",
      "Lower foliage affected first, the crown still green",
      "Lesion margins darker than their centres",
    ],
  },
  advisory: {
    title: "Early blight — stop the spread before it reaches the tubers",
    title_ar: "اللفحة المبكرة — أوقف الانتشار قبل أن يصل إلى الدرنات",
    region: "Tébessa",
    kind: "treatment",
    actions: [
      "Remove and burn the affected lower leaves today.",
      "Apply a protectant fungicide to the remaining canopy.",
      "Keep the foliage dry: water at the base, early in the morning.",
    ],
    treatments: [
      { type: "Fungicide", detail: "Chlorothalonil or mancozeb on a 7-day interval, two sprays." },
      { type: "Resistance", detail: "Rotate away from potato and tomato for two seasons." },
    ],
    irrigation_schedule: {
      schedule: "Every 3 days",
      target: "early morning, at the base",
      note: "overhead watering spreads the spores",
    },
    prevention: [
      "Certified seed tubers only.",
      "Space rows so the canopy dries within an hour of sunrise.",
      "Clear volunteer potatoes between seasons.",
    ],
    disclaimer:
      "Confirm the diagnosis before treating a whole field; a single leaf is one reading.",
  },
};

/** The assistant exchange, replayed through the real bubbles. Arabic, because
 *  the interface has just been switched and the answer should match it. */
const EXCHANGE = [
  {
    role: "user",
    text: "البقع تنتشر إلى الأوراق العليا. هل أرشّ الآن أم أنتظر حتى تهدأ الريح؟",
  },
  {
    role: "bot",
    text:
      "ارشّ الآن، لكن ليس في هذه الريح. الانجراف فوق ١٥ كم/س يهدر المنتج وقد " +
      "يحملُه إلى شعير جارك. تُعطيك توقعات تبسة نافذة غدًا بين الخامسة والثامنة " +
      "صباحًا: ٦ كم/س، ولا ندى بعد السابعة. استخدم كلوروثالونيل بالمعدل الموصى " +
      "به، وحافظ على الفاصل سبعة أيام؛ اللفحة المبكرة لا تتوقف بجولة واحدة. " +
      "ولا تروِ بالرش فوق الأوراق ٤٨ ساعة بعد الرش.",
  },
];

/* ------------------------------------------------------------- leaf art -- */

/** A plausible photograph of one infected leaf, drawn rather than shipped.
 *  The repository carries no sample imagery, and a demo that needs a file on
 *  disk is a demo that breaks the moment it is run from a clean checkout. */
function drawLeaf(ctx, w, h) {
  ctx.fillStyle = "#e9e6df";
  ctx.fillRect(0, 0, w, h);

  const wash = ctx.createRadialGradient(w * 0.5, h * 0.42, 60, w * 0.5, h * 0.5, w * 0.62);
  wash.addColorStop(0, "rgba(255,255,255,.85)");
  wash.addColorStop(1, "rgba(196,190,178,.55)");
  ctx.fillStyle = wash;
  ctx.fillRect(0, 0, w, h);

  ctx.save();
  ctx.translate(w * 0.5, h * 0.5);
  ctx.rotate(-0.34);

  ctx.save();
  ctx.translate(18, 26);
  ctx.filter = "blur(22px)";
  ctx.fillStyle = "rgba(40,52,32,.42)";
  ctx.beginPath();
  ctx.ellipse(0, 0, 240, 372, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  const blade = () => {
    ctx.beginPath();
    ctx.moveTo(0, -362);
    ctx.bezierCurveTo(232, -268, 268, 78, 0, 366);
    ctx.bezierCurveTo(-268, 78, -232, -268, 0, -362);
    ctx.closePath();
  };

  const green = ctx.createLinearGradient(-210, -330, 210, 340);
  green.addColorStop(0, "#4f8f3d");
  green.addColorStop(0.45, "#2f6b2a");
  green.addColorStop(1, "#173d15");
  blade();
  ctx.fillStyle = green;
  ctx.fill();

  ctx.strokeStyle = "rgba(206,232,178,.34)";
  ctx.lineWidth = 3.2;
  ctx.beginPath();
  ctx.moveTo(0, -344);
  ctx.lineTo(0, 352);
  ctx.stroke();

  ctx.lineWidth = 1.7;
  for (let i = 0; i < 9; i += 1) {
    const y = -230 + i * 62;
    const reach = 150 - Math.abs(i - 4) * 16;
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.quadraticCurveTo(side * reach * 0.6, y - 26, side * reach, y - 74);
      ctx.stroke();
    }
  }

  const spots = [
    { x: -118, y: -60, r: 44 },
    { x: 92, y: 108, r: 54 },
    { x: -58, y: 196, r: 34 },
    { x: 128, y: -148, r: 28 },
  ];

  for (const spot of spots) {
    const halo = ctx.createRadialGradient(spot.x, spot.y, spot.r * 0.2, spot.x, spot.y, spot.r * 1.9);
    halo.addColorStop(0, "rgba(150,150,52,.55)");
    halo.addColorStop(1, "rgba(150,150,52,0)");
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(spot.x, spot.y, spot.r * 1.9, 0, Math.PI * 2);
    ctx.fill();

    const core = ctx.createRadialGradient(spot.x, spot.y, 2, spot.x, spot.y, spot.r);
    core.addColorStop(0, "#4a3316");
    core.addColorStop(0.65, "#6b4a1e");
    core.addColorStop(1, "rgba(107,74,30,.25)");
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(spot.x, spot.y, spot.r, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = "rgba(58,40,16,.55)";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(spot.x, spot.y, spot.r * 0.62, 0, Math.PI * 2);
    ctx.stroke();
  }

  const sheen = ctx.createLinearGradient(-160, -320, 140, -40);
  sheen.addColorStop(0, "rgba(255,255,255,.16)");
  sheen.addColorStop(1, "rgba(255,255,255,0)");
  blade();
  ctx.fillStyle = sheen;
  ctx.fill();

  ctx.restore();
  ctx.restore();
}

function leafFile() {
  return new Promise((resolve) => {
    const canvas = document.createElement("canvas");
    canvas.width = 1440;
    canvas.height = 1080;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      resolve(null);
      return;
    }
    drawLeaf(ctx, canvas.width, canvas.height);
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          resolve(null);
          return;
        }
        resolve(new File([blob], "potato-leaf-sample.jpg", { type: "image/jpeg" }));
      },
      "image/jpeg",
      0.92,
    );
  });
}

/* ---------------------------------------------------------------- export -- */

function pickMime() {
  if (typeof MediaRecorder === "undefined") return "";
  for (const type of MIMES) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return "";
}

/** The container as a viewer would name it. The full MIME string, codecs and
 *  all, belongs in a debugger, not in a status line under the button. */
function container(mime) {
  if (mime.indexOf("mp4") !== -1) return "MP4";
  if (mime.indexOf("matroska") !== -1) return "MKV";
  return "WEBM";
}

function extension(mime) {
  return container(mime).toLowerCase();
}

function clock(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

/* --------------------------------------------------------------- module -- */

export function initRecorder({
  diagnose,
  chat,
  enterDashboard,
  enterLanding,
  i18n,
} = {}) {
  const els = {
    start: $("rec-start"),
    stop: $("rec-stop"),
    status: $("rec-status"),
    elapsed: $("rec-elapsed"),
    bar: $("rec-bar"),
    out: $("rec-out"),
    link: $("rec-download"),
    file: $("rec-file"),
    facts: $("rec-facts"),
  };

  const state = {
    busy: false,
    live: false,
    recorder: null,
    stream: null,
    chunks: [],
    url: null,
    ticker: 0,
    startedAt: 0,
    langBefore: null,
    audio: false,
    peak: 0,
    audioCtx: null,
    meter: 0,
  };

  /* ---------------------------------------------------------- panel ---- */

  function setStatus(text, tone) {
    if (!els.status) return;
    els.status.textContent = text;
    if (tone) els.status.setAttribute("data-tone", tone);
    else els.status.removeAttribute("data-tone");
  }

  function progress(ms) {
    if (els.elapsed) els.elapsed.textContent = `${clock(ms)} / ${clock(TOTAL)}`;
    if (els.bar) els.bar.style.width = `${Math.min(100, (ms / TOTAL) * 100).toFixed(2)}%`;
  }

  function audioLabel() {
    if (!state.audio) return t("rec_audio_none");
    if (state.peak < 0.002) return t("rec_audio_silent");
    return t("rec_audio_ok");
  }

  /* ---------------------------------------------------------- teardown -- */

  function teardown() {
    state.live = false;
    clearInterval(state.ticker);
    clearInterval(state.meter);

    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
    }
    if (state.audioCtx) {
      state.audioCtx.close().catch(() => {});
      state.audioCtx = null;
    }
    if (typeof speechSynthesis !== "undefined") {
      speechSynthesis.cancel();
    }

    state.recorder = null;
    state.busy = false;
    unmount();
    spotOff();
    show(els.stop, false);
    if (els.start) els.start.disabled = false;

    // The film flips the language on purpose. That is the film's business,
    // not the viewer's setting, so put back whatever they had before.
    if (state.langBefore && i18n && typeof i18n.setLang === "function") {
      i18n.setLang(state.langBefore);
    }
    state.langBefore = null;
  }

  /* ----------------------------------------------------------- finish -- */

  function finish(recorder, size) {
    const mime = recorder.mimeType || pickMime();
    const blob = new Blob(state.chunks, { type: mime });
    state.chunks = [];
    const shot = state.startedAt ? performance.now() - state.startedAt : 0;
    const silent = state.audio && state.peak < 0.002;

    if (!blob.size) {
      teardown();
      enterDashboard("recorder");
      setStatus(t("rec_fail"), "err");
      return;
    }

    if (state.url) URL.revokeObjectURL(state.url);
    state.url = URL.createObjectURL(blob);

    const name = `Hassad_AI_3Min_Presentation.${extension(mime)}`;
    const megabytes = (blob.size / (1024 * 1024)).toFixed(1);

    if (els.link) {
      els.link.href = state.url;
      els.link.download = name;
      show(els.out, true);
    }
    if (els.file) els.file.textContent = `${name} · ${megabytes} MB`;
    if (els.facts) {
      const pixels = size.w && size.h ? ` · ${size.w} × ${size.h}` : "";
      els.facts.textContent = `${container(mime)}${pixels} · ${clock(shot)} · ${audioLabel()}`;
    }

    teardown();
    enterDashboard("recorder");

    if (silent) setStatus(t("rec_saved_silent"), "warn");
    else if (!state.audio) setStatus(t("rec_saved_mute"), "warn");
    else setStatus(t("rec_saved"), "ok");

    // The file lands without a second action, then stays available above.
    if (els.link) els.link.click();
  }

  function abort(reason) {
    if (!state.busy) return;
    state.live = false;
    clearInterval(state.ticker);
    clearInterval(state.meter);

    if (state.recorder && state.recorder.state !== "inactive") {
      state.recorder.onstop = null;
      state.recorder.onerror = null;
      try {
        state.recorder.stop();
      } catch {
        /* already inactive */
      }
    }

    state.chunks = [];
    teardown();
    enterDashboard("recorder");
    progress(0);
    setStatus(reason || t("rec_cancel"), "err");
  }

  /** Stop filming now and keep what has been shot. This is the Stop button:
   *  an early ending is still a film. Escape discards instead. */
  function stopEarly() {
    if (!state.busy) return;
    state.live = false;
    clearInterval(state.ticker);
    spotOff();

    const recorder = state.recorder;
    if (recorder && recorder.state !== "inactive") {
      try {
        recorder.stop();          // onstop -> finish() -> download
      } catch {
        /* already inactive */
      }
    }
  }

  /* --------------------------------------------------------- portrayal -- */

  function narrate(text) {
    if (!text || typeof speechSynthesis === "undefined") return;
    try {
      speechSynthesis.cancel();
      const line = new SpeechSynthesisUtterance(text);
      line.lang = "en-US";
      line.rate = 0.98;
      line.pitch = 1;
      const voice = englishVoice();
      if (voice) line.voice = voice;
      speechSynthesis.speak(line);
    } catch {
      /* narration is an enhancement; the captions already carry the words */
    }
  }

  function englishVoice() {
    const voices = speechSynthesis.getVoices();
    if (!voices || !voices.length) return null;
    const english = voices.filter((voice) => /^en(-|_|$)/i.test(voice.lang));
    if (!english.length) return null;
    // A local voice starts instantly and does not need a network round-trip.
    return (
      english.find((voice) => voice.localService && /google|natural|neural/i.test(voice.name)) ||
      english.find((voice) => voice.localService) ||
      english[0]
    );
  }

  async function feedLeaf() {
    const input = $("file");
    if (!input) return false;
    const file = await leafFile();
    if (!file) return false;

    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  /* ----------------------------------------------------------- timeline -- */

  const STEPS = [
    /* ---- 0:00 problem and vision ------------------------------------ */
    { at: 700, run: () => narrate(SCRIPT.c1) },
    { at: 900, run: () => titleCard("01 — THE PROBLEM", "One leaf. One reading.") },

    {
      at: 4200,
      run: () => caption("01 · TÉBESSA", "300 mm of rain. Alkaline soil. No lab for a hundred kilometres."),
    },
    { at: 5000, run: () => reveal(q("#main .hero__title")) },

    { at: 12000, run: () => caption("01 · THE DELAY", "A spot. A yellowing margin. By then, the loss is already paid.") },
    { at: 12200, run: () => reveal(q("#main .mock")) },

    { at: 18000, run: () => reveal(q("#how .steps li:nth-child(1)")) },
    { at: 21500, run: () => reveal(q("#how .steps li:nth-child(2)")) },
    { at: 24500, run: () => reveal(q("#how .steps li:nth-child(3)")) },

    { at: 28000, run: () => caption("01 · THE PRODUCT", "One photograph. One reading. One plan.") },
    { at: 28200, run: () => reveal(q("#modules .lgrid article:nth-child(1)")) },
    { at: 31500, run: () => reveal(q("#modules .lgrid article:nth-child(2)")) },
    { at: 34500, run: () => reveal(q("#modules .lgrid article:nth-child(3)")) },

    { at: 37500, run: () => caption("01 · BILINGUAL", "Diagnosis in the farmer's language — both of them.") },
    { at: 37700, run: () => reveal(q("#why .lgrid article:nth-child(1)")) },
    { at: 40500, run: () => reveal(q("#why .lgrid article:nth-child(2)")) },
    { at: 43000, run: () => reveal(q("#why .lgrid article:nth-child(3)")) },

    { at: 43400, run: () => tap($("hero-start")) },

    /* ---- 0:45 upload and the report --------------------------------- */
    { at: 44800, run: () => narrate(SCRIPT.c2) },
    { at: 45000, run: () => titleCard("02 — DIAGNOSIS", "Upload · Verdict · Plan") },

    { at: 48200, run: () => caption("02 · UPLOAD", "Drop a leaf into the studio.") },
    { at: 48500, run: () => reveal($("dropzone")) },

    /* Pressed, not clicked: a real click opens the file dialog and the take
       would sit there until somebody chose a file. The upload is real. */
    {
      at: 51300,
      run: async () => {
        await tap($("dropzone"), { real: false });
        await feedLeaf();
      },
    },

    { at: 56200, run: () => caption("02 · UPLOAD", "Compressed in the browser, sent once.") },
    { at: 56500, run: () => reveal($("preview-wrap")) },

    /* Pressed, not clicked: a real click calls the live model. The card that
       follows carries a SAMPLE badge. */
    {
      at: 60000,
      run: async () => {
        await tap($("analyze"), { real: false });
        await wait(520);
        diagnose?.renderSample(SAMPLE);
      },
    },

    { at: 64200, run: () => caption("02 · VERDICT", "Potato · Early blight · Alternaria solani · 94%") },
    { at: 64500, run: () => reveal($("summary")) },

    { at: 72000, run: () => reveal(q(".ring-wrap--sev")) },
    { at: 78500, run: () => reveal(q("#results .ring-wrap")) },

    { at: 84000, run: () => caption("02 · EVIDENCE", "Symptoms the model can point to.") },
    { at: 84300, run: () => reveal($("symptoms")) },

    { at: 92000, run: () => caption("02 · TREATMENT", "A plan written for this soil, this climate, this season.") },
    { at: 92400, run: () => reveal($("advisory-body")) },

    { at: 102000, run: () => reveal(q("#advisory-body ol.steps")) },
    { at: 110000, run: () => caption("02 · IRRIGATION", "Drip, not overhead — overhead water spreads the spores.") },
    { at: 110300, run: () => reveal(q("#advisory-body .note")) },
    { at: 117000, run: () => spotOff() },

    /* ---- 2:00 bilingual and the assistant --------------------------- */
    { at: 120000, run: () => narrate(SCRIPT.c3) },
    { at: 121500, run: () => titleCard("03 — BILINGUAL", "العربية · English") },

    { at: 124000, run: () => tap(q("#app [data-lang-toggle]")) },
    { at: 128000, run: () => caption("03 · RTL", "Labels, layout, verdict — the whole workspace turns.") },
    { at: 128300, run: () => reveal($("summary")) },

    { at: 134000, run: () => caption("03 · THE AGRONOMIST", "And when the report is not enough, ask.") },
    { at: 134500, run: () => tap(q('[data-action="assistant"]')) },
    { at: 137500, run: () => reveal($("messages")) },

    { at: 140000, run: () => caption("03 · GROUNDED", "Grounded in the plant that was actually analysed.") },
    { at: 141000, run: () => chat?.renderSampleExchange(EXCHANGE, 1500) },
    { at: 147500, run: () => reveal(q("#messages .bubble:last-child")) },
    { at: 154000, run: () => spotOff() },
    { at: 155500, run: () => tap(q("#chat-modal button[data-chat-close]")) },

    /* ---- 2:40 close ------------------------------------------------- */
    { at: 160000, run: () => narrate(SCRIPT.c4) },
    { at: 161500, run: () => titleCard("04 — OPEN SOURCE", "github.com/sajedkhalil2010-lgtm/Hassad-ai") },

    { at: 164000, run: () => tap(q('[data-panel-target="pricing"]')) },
    { at: 167000, run: () => reveal(q(".plans")) },

    { at: 169000, run: async () => { spotOff(); caption("", ""); hideCursor(); } },
    { at: 170000, run: () => outro() },
    { at: 176000, run: () => fadeTo(true, 900) },
  ];

  async function tour(began) {
    for (const step of STEPS) {
      const delay = step.at - (performance.now() - began);
      if (delay > 0) await wait(delay);
      if (!state.live) return false;
      try {
        await step.run();
      } catch (err) {
        // A missing target must not kill a take that is already recording.
        console.warn("[hassad] presentation step skipped:", err);
      }
    }
    return true;
  }

  /* ----------------------------------------------------------- capture -- */

  async function start() {
    if (state.busy) return;

    const mime = pickMime();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      setStatus(t("rec_fail"), "err");
      return;
    }
    if (!mime) {
      setStatus(t("rec_unsupported"), "err");
      return;
    }

    state.busy = true;
    state.chunks = [];
    state.peak = 0;
    state.audio = false;
    if (state.url) {
      URL.revokeObjectURL(state.url);
      state.url = null;
    }
    show(els.out, false);
    if (els.start) els.start.disabled = true;
    setStatus(t("rec_wait"), "warn");

    let stream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: {
          width: { ideal: 3840 },
          height: { ideal: 2160 },
          frameRate: { ideal: FPS },
        },
        // The narration is produced by the OS, so it can only reach the file
        // through a system-audio capture. Ask for it; never assume it.
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
        systemAudio: "include",
        selfBrowserSurface: "include",
        surfaceSwitching: "exclude",
      });
    } catch (err) {
      state.busy = false;
      if (els.start) els.start.disabled = false;
      const cancelled = err && err.name === "NotAllowedError";
      setStatus(cancelled ? t("rec_cancel") : t("rec_fail"), "err");
      return;
    }

    state.stream = stream;

    // Crop to the page, so the file is the app frame and not the desktop with
    // the browser drawn around it. The element is <body>, not #app: chapter
    // one is the landing page and the rest is the workspace, and only the body
    // spans both. Where Region Capture is unavailable the whole surface is
    // recorded; the facts line reports the size that was actually captured.
    let cropped = false;
    try {
      if (window.CropTarget && typeof window.CropTarget.fromElement === "function") {
        const track = stream.getVideoTracks()[0];
        if (track && typeof track.cropTo === "function") {
          await track.cropTo(await window.CropTarget.fromElement(document.body));
          cropped = true;
        }
      }
    } catch {
      cropped = false;
    }

    const settings = stream.getVideoTracks()[0].getSettings();
    const ratio = window.devicePixelRatio || 1;
    const size = cropped
      ? { w: Math.round(window.innerWidth * ratio), h: Math.round(window.innerHeight * ratio) }
      : { w: settings.width || 0, h: settings.height || 0 };

    await listenForAudio(stream);

    // Put the workspace in the state the film opens on. English first, so the
    // switch to Arabic in chapter three reads as a switch.
    const before =
      i18n && typeof i18n.currentLang === "function" ? i18n.currentLang() : null;
    state.langBefore = before;
    if (i18n && typeof i18n.setLang === "function" && before !== "en") {
      i18n.setLang("en");
    }
    diagnose?.resetStudio();
    chat?.reset();
    enterLanding();
    window.scrollTo(0, 0);
    mount();
    await fadeTo(true, 0);
    progress(0);

    await countdown(3, t("rec_count"));
    if (!state.busy) return;   // aborted during the countdown

    let recorder;
    try {
      recorder = new MediaRecorder(stream, {
        mimeType: mime,
        videoBitsPerSecond: BITRATE,
        audioBitsPerSecond: 128_000,
      });
    } catch {
      try {
        recorder = new MediaRecorder(stream, {
          mimeType: mime.split(";")[0],
          videoBitsPerSecond: BITRATE,
        });
      } catch {
        teardown();
        enterDashboard("recorder");
        setStatus(t("rec_unsupported"), "err");
        return;
      }
    }

    state.recorder = recorder;
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) state.chunks.push(event.data);
    };
    recorder.onstop = () => finish(recorder, size);
    recorder.onerror = () => abort(t("rec_fail"));

    show(els.stop, true);
    recorder.start(1000);
    state.startedAt = performance.now();
    state.live = true;
    setStatus(t("rec_live"), "ok");

    // The clock starts at the first frame, so the fade-in is part of the film
    // rather than a preamble to it — otherwise every export ran a second long.
    const began = state.startedAt;
    state.ticker = setInterval(() => progress(performance.now() - began), 200);

    await wait(200);
    await fadeTo(false, 900);

    await tour(began);

    if (!state.live) return;               // stopped by hand
    const left = TOTAL - (performance.now() - began);
    if (left > 0) await wait(left);
    if (!state.live) return;

    state.live = false;
    clearInterval(state.ticker);
    if (recorder.state !== "inactive") {
      try {
        recorder.stop();
      } catch {
        /* already inactive */
      }
    }
  }

  /** Watch the captured audio without sending it anywhere. A track can exist
   *  and still be silent — a tab share with no audio, a muted output — and
   *  the difference belongs in the facts line, not in a guess. */
  async function listenForAudio(stream) {
    const tracks = stream.getAudioTracks();
    state.audio = tracks.length > 0;
    if (!state.audio || typeof AudioContext === "undefined") return;

    try {
      const ctx = new AudioContext();
      // A context the autoplay policy keeps suspended must not stall the
      // take; the meter is the only thing that depends on it.
      await Promise.race([ctx.resume(), wait(800)]);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(new MediaStream([tracks[0]])).connect(analyser);
      const buffer = new Float32Array(analyser.fftSize);

      state.audioCtx = ctx;
      state.meter = setInterval(() => {
        analyser.getFloatTimeDomainData(buffer);
        let peak = 0;
        for (let i = 0; i < buffer.length; i += 1) {
          const value = Math.abs(buffer[i]);
          if (value > peak) peak = value;
        }
        if (peak > state.peak) state.peak = peak;
      }, 400);
    } catch {
      // A meter that cannot start must not stop the take. The file still
      // exports; only the "did audio actually arrive" line is lost.
      state.audioCtx = null;
    }
  }

  /* ------------------------------------------------------------ wiring -- */

  els.start?.addEventListener("click", start);
  els.stop?.addEventListener("click", stopEarly);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.busy) abort(t("rec_cancel"));
  });

  // The idle line is translated at render time, so a language switch while
  // nothing is being filmed must not leave it in the previous language.
  document.addEventListener("hassad:lang", () => {
    if (!state.busy) setStatus(t("rec_ready"), null);
  });

  progress(0);
  setStatus(t("rec_ready"), null);

  return { start, stop: stopEarly };
}
