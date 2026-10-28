/* ============================================================================
   Hassad AI — trailer export engine
   ----------------------------------------------------------------------------
   Records the forty-second piece once, at the largest size the machine can
   actually give it, and hands back a single file.

   WHAT IS HONEST HERE
   -------------------
   A browser can only record what it can render. Screen capture is limited by
   the display, so a 1920×1080 screen produces a 1920×1080 file no matter what
   is requested — asking for 3840×2160 does not conjure pixels that are not
   there. The page therefore reads back what it actually got and says so,
   twice: in the capability line before recording, and in the file name
   afterwards. `Hassad_AI_Trailer_4K.mp4` is produced when, and only when, the
   file really is 4K and really is MP4.

   MP4 is likewise not a given. Chrome and Edge can mux MP4 in MediaRecorder;
   Firefox records WebM only. The candidate list below tries the exact string
   that was asked for first, then progressively more realistic ones, and the
   first one the browser confirms is the one used. Nothing is renamed: a WebM
   file is never handed out with an .mp4 extension, because a file that lies
   about its container will not open in the player the audience is using.

   Capture is opt-in. Nothing here touches the screen until a person clicks,
   the browser still asks them to confirm the shared surface, and every track
   is stopped and every object URL revoked on every exit path — success,
   cancel, error or Escape.
   ========================================================================== */

const TOTAL_MS = 40000;
const BITRATE = 50000000;   // 50 Mbps — 4K60 H.264 needs the headroom

/* Tried in order; the first the browser confirms wins. The first entry is the
   exact string requested. Later entries exist because `avc1.42E01E` is H.264
   Baseline level 3.0, which is a 720×480 profile — a 4K encoder will refuse
   it — and because several browsers will only accept a bare container, and
   because Firefox has no MP4 muxer at all. */
const CANDIDATES = [
  'video/mp4; codecs="avc1.42E01E, mp4a.40.2"',
  'video/mp4; codecs="avc1.640033, mp4a.40.2"',
  'video/mp4; codecs="avc1.4D401F, mp4a.40.2"',
  'video/mp4; codecs="avc1.42E01E"',
  "video/mp4",
  'video/x-matroska; codecs="avc1.42E01E"',
  'video/webm; codecs="h264"',
  'video/webm; codecs="vp9"',
  'video/webm; codecs="vp8"',
  "video/webm",
];

const $ = (id) => document.getElementById(id);

const specEl = $("export-spec");
const resultEl = $("export-result");

const state = {
  busy: false,
  recorder: null,
  stream: null,
  chunks: [],
  endResolve: null,
};

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* ------------------------------------------------------------- readouts -- */

function setSpec(text, tone) {
  if (!specEl) return;
  specEl.textContent = text;
  specEl.dataset.tone = tone || "info";
}

function setResult(text, tone) {
  if (!resultEl) return;
  resultEl.textContent = text;
  resultEl.dataset.tone = tone || "info";
}

/** What the stage would be in device pixels — which is the size of the frame,
 *  if the capture can be cropped to it. A 1920-wide viewport on a 2× display
 *  is 3840 device pixels, so a HiDPI laptop can genuinely produce 4K. */
function stagePixels() {
  const node = $("stage");
  const ratio = window.devicePixelRatio || 1;
  const rect = node ? node.getBoundingClientRect() : { width: 0, height: 0 };
  return {
    w: Math.round(rect.width * ratio),
    h: Math.round(rect.height * ratio),
  };
}

function pickMime() {
  if (typeof MediaRecorder === "undefined") return "";
  for (let i = 0; i < CANDIDATES.length; i += 1) {
    if (MediaRecorder.isTypeSupported(CANDIDATES[i])) return CANDIDATES[i];
  }
  return "";
}

function describe() {
  const mime = pickMime();
  if (!mime) {
    return { text: "This browser cannot record video.", tone: "bad" };
  }

  const px = stagePixels();
  const pixels = `${px.w} × ${px.h}`;
  const megabytes = Math.round((BITRATE * (TOTAL_MS / 1000)) / 8 / 1e6);
  const size = `≈${megabytes} MB`;

  const mp4 = mime.indexOf("mp4") !== -1;
  const fourK = px.h >= 2160;

  if (mp4 && fourK) {
    return { text: `MP4 · H.264 · ${pixels} · ${size}`, tone: "ok" };
  }
  if (mp4) {
    return {
      text: `MP4 · H.264 · ${pixels} · ${size} — this display cannot produce 4K`,
      tone: "warn",
    };
  }
  if (fourK) {
    return {
      text: `WebM · ${pixels} · ${size} — this browser cannot write MP4`,
      tone: "warn",
    };
  }
  return {
    text: `WebM · ${pixels} · ${size} — this browser cannot write MP4 or capture 4K`,
    tone: "warn",
  };
}

function refreshSpec() {
  if (state.busy) return;
  const info = describe();
  let text = info.text;
  const reduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) text += " · motion on during export";
  setSpec(text, info.tone);
}

/** The name states what the file is. The extension follows the container and
 *  the label follows the height that was actually captured. */
function fileName(size, mime) {
  const height = size && size.h ? size.h : 0;

  let label = "1080p";
  if (height >= 2160) label = "4K";
  else if (height >= 1440) label = "1440p";
  else if (height >= 1080) label = "1080p";
  else if (height > 0) label = `${height}p`;

  let ext = "webm";
  if (mime.indexOf("mp4") !== -1) ext = "mp4";
  else if (mime.indexOf("matroska") !== -1) ext = "mkv";

  return `Hassad_AI_Trailer_${label}.${ext}`;
}

/* --------------------------------------------------------------- output -- */

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Long enough for a slow disk to start writing; the blob is released after.
  setTimeout(() => URL.revokeObjectURL(url), 300000);
}

function teardown() {
  if (state.stream) {
    state.stream.getTracks().forEach((track) => track.stop());
    state.stream = null;
  }
  state.recorder = null;
  state.busy = false;
  document.body.classList.remove("is-exporting");
}

async function finalize(recorder, size) {
  const mime = recorder.mimeType || pickMime();
  const blob = new Blob(state.chunks, { type: mime });
  state.chunks = [];

  if (!blob.size) {
    teardown();
    setResult("Nothing was captured — the recording came back empty.", "bad");
    return;
  }

  const name = fileName(size, mime);
  const megabytes = (blob.size / (1024 * 1024)).toFixed(1);

  download(blob, name);
  teardown();

  const pixels = size.w && size.h ? ` · ${size.w} × ${size.h}` : "";
  setResult(`Saved ${name} · ${megabytes} MB${pixels}`, "ok");
}

/* --------------------------------------------------------------- escape -- */

function abort() {
  if (!state.busy) return;

  if (state.endResolve) state.endResolve();

  if (state.recorder && state.recorder.state !== "inactive") {
    // Drop the handler first: a cancelled take must not be finalised.
    state.recorder.onstop = null;
    state.recorder.onerror = null;
    try {
      state.recorder.stop();
    } catch (err) {
      /* already inactive */
    }
  }

  state.chunks = [];
  teardown();

  if (window.HassadTrailer) window.HassadTrailer.reset();
  setSpec("Recording cancelled — nothing was saved.", "bad");
}

/* ---------------------------------------------------------------- export -- */

function waitForEnd() {
  return new Promise((resolve) => {
    let settled = false;
    const settle = () => {
      if (settled) return;
      settled = true;
      document.removeEventListener("hassad:trailer-end", settle);
      state.endResolve = null;
      resolve();
    };

    state.endResolve = settle;
    document.addEventListener("hassad:trailer-end", settle);

    // A hard stop of last resort, so a missing event can never leave the
    // encoder running and the person waiting on a file that never arrives.
    setTimeout(settle, TOTAL_MS + 8000);
  });
}

async function runExport() {
  if (state.busy) return;

  const mime = pickMime();

  if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
    setSpec("This browser cannot capture a screen. Use Chrome or Edge, served over HTTPS or localhost.", "bad");
    return;
  }
  if (!mime) {
    setSpec("This browser supports neither MP4 nor WebM recording.", "bad");
    return;
  }

  state.busy = true;
  state.chunks = [];
  setResult("", "info");
  setSpec("Confirm sharing this tab in the browser dialog…", "busy");

  let stream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({
      video: {
        width: { ideal: 3840 },
        height: { ideal: 2160 },
        frameRate: { ideal: 60 },
      },
      // No microphone, no system audio: the piece is silent by design, and a
      // video track alone is one less permission to grant.
      audio: false,
      preferCurrentTab: true,
      selfBrowserSurface: "include",
      surfaceSwitching: "exclude",
    });
  } catch (err) {
    state.busy = false;
    const cancelled = err && err.name === "NotAllowedError";
    const message = err && err.message ? err.message : "unknown error";
    setSpec(cancelled
      ? "Capture cancelled — nothing was recorded."
      : `Capture failed: ${message}`, "bad");
    return;
  }

  state.stream = stream;

  // Crop the capture to the stage itself, so the file is the 16:9 frame and
  // not the whole tab with black letterbox bars. Region Capture is Chrome's;
  // where it is absent the full tab is recorded and the readout reflects the
  // size that was actually captured.
  let cropped = false;
  try {
    if (window.CropTarget && typeof window.CropTarget.fromElement === "function") {
      const track = stream.getVideoTracks()[0];
      if (track && typeof track.cropTo === "function") {
        const region = await window.CropTarget.fromElement($("stage"));
        await track.cropTo(region);
        cropped = true;
      }
    }
  } catch (err) {
    cropped = false;
  }

  const settings = stream.getVideoTracks()[0].getSettings();
  const size = cropped
    ? stagePixels()
    : { w: settings.width || 0, h: settings.height || 0 };

  let recorder;
  try {
    recorder = new MediaRecorder(stream, {
      mimeType: mime,
      videoBitsPerSecond: BITRATE,
    });
  } catch (err) {
    // A few builds reject a full codec list but accept the bare container.
    try {
      recorder = new MediaRecorder(stream, {
        mimeType: mime.split(";")[0],
        videoBitsPerSecond: BITRATE,
      });
    } catch (err2) {
      stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
      state.busy = false;
      const message = err2 && err2.message ? err2.message : "unsupported format";
      setSpec(`This browser refused the recording format: ${message}`, "bad");
      return;
    }
  }

  state.recorder = recorder;

  recorder.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) state.chunks.push(event.data);
  };

  recorder.onstop = () => {
    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
    }
    finalize(recorder, size);
  };

  recorder.onerror = (event) => {
    const error = event && event.error ? event.error : null;
    const name = error && error.name ? ` (${error.name})` : "";
    setResult(`Recording stopped unexpectedly${name}.`, "bad");
    abort();
  };

  recorder.start(1000);

  // Let the muxer settle before the clock starts, so the opening fade is not
  // clipped while the encoder warms up.
  await wait(320);

  document.body.classList.add("is-exporting");

  if (!window.HassadTrailer) {
    abort();
    setSpec("The trailer engine did not load, so there is nothing to record.", "bad");
    return;
  }

  window.HassadTrailer.reset();
  window.HassadTrailer.start();

  await waitForEnd();

  // Hold the final frame briefly so it is not clipped by the stop.
  await wait(700);

  if (recorder.state !== "inactive") {
    try {
      recorder.stop();
    } catch (err) {
      /* already inactive */
    }
  }
}

/* ----------------------------------------------------------------- wire -- */

const exportBtn = $("export");
if (exportBtn) exportBtn.addEventListener("click", () => runExport());

const againBtn = $("export-again");
if (againBtn) againBtn.addEventListener("click", () => runExport());

// Escape during a take cancels it and saves nothing. The trailer's own Escape
// handler still resets the picture; this only stops the encoder.
document.addEventListener("keydown", (event) => {
  if (event.code === "Escape" && state.busy) abort();
});

// A plain playback clears whatever the last export reported, so a stale
// "Saved …" line never sits under a fresh run.
document.addEventListener("hassad:trailer-start", () => {
  if (!state.busy) setResult("", "info");
});

window.addEventListener("resize", refreshSpec);

refreshSpec();
