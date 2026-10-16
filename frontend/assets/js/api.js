/* =====================================================================
   api.js — HTTP client for Hassad AI
   • Bearer JWT auto-persisted in localStorage (+ silent refresh on 401)
   • JSON requests, multipart upload with progress, SSE streaming chat
   Base URL bound to the local backend: http://127.0.0.1:8000/api/v1
   ===================================================================== */

export const API_ORIGIN = "http://127.0.0.1:8000";
export const API_BASE = API_ORIGIN + "/api/v1";

const K = {
  access: "hassad.access",
  refresh: "hassad.refresh",
  expires: "hassad.expires",
  refreshExpires: "hassad.refreshExpires",
};

/* ---------- token persistence -------------------------------------- */
function lsGet(k) {
  try {
    return localStorage.getItem(k);
  } catch (e) {
    return null;
  }
}
function lsSet(k, v) {
  try {
    localStorage.setItem(k, v);
  } catch (e) { /* private mode */ }
}
function lsDel(k) {
  try {
    localStorage.removeItem(k);
  } catch (e) { /* ignore */ }
}

export function setTokens(tok) {
  if (!tok || !tok.access_token) return;
  lsSet(K.access, tok.access_token);
  lsSet(K.refresh, tok.refresh_token || "");
  if (tok.expires_in) lsSet(K.expires, String(Date.now() + tok.expires_in * 1000));
  if (tok.refresh_expires_in) {
    lsSet(K.refreshExpires, String(Date.now() + tok.refresh_expires_in * 1000));
  }
}

export function clearTokens() {
  lsDel(K.access);
  lsDel(K.refresh);
  lsDel(K.expires);
  lsDel(K.refreshExpires);
}

export function accessToken() {
  return lsGet(K.access);
}

export function hasTokens() {
  return !!lsGet(K.access) || !!lsGet(K.refresh);
}

export function accessExpired() {
  const exp = lsGet(K.expires);
  if (!exp) return false;
  return Date.now() > Number(exp) - 5000; // 5s safety margin
}

function refreshExpired() {
  const exp = lsGet(K.refreshExpires);
  if (!exp) return true;
  return Date.now() > Number(exp);
}

/* ---------- error type --------------------------------------------- */
export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code || "";
  }
}

/** Pull the most useful message out of any backend error body. */
function extractError(status, body) {
  let code = "";
  let msg = "";
  if (body && typeof body === "object") {
    const d = body.detail !== undefined ? body.detail : body;
    if (typeof d === "string") {
      msg = d;
    } else if (d && typeof d === "object") {
      if (Array.isArray(d)) {
        // FastAPI validation errors: [{loc, msg, ...}]
        const first = d.find(function (x) { return x && (x.msg || x.message); }) || {};
        msg = first.msg || first.message || "";
        if (!msg && d.length) msg = "Validation error";
      } else {
        msg = d.message || d.msg || "";
        code = d.code || "";
      }
    }
    if (!msg && typeof body.message === "string") msg = body.message;
  }
  if (!msg) {
    if (status === 401) msg = "Unauthorized";
    else if (status === 403) msg = "Forbidden";
    else if (status === 404) msg = "Not found";
    else if (status === 413) msg = "Payload too large";
    else if (status >= 500) msg = "Server error";
    else msg = "Request failed (" + status + ")";
  }
  return new ApiError(status, code, msg);
}

/* ---------- auth refresh ------------------------------------------- */
let refreshPromise = null;

export async function refreshTokens() {
  const rt = lsGet(K.refresh);
  if (!rt) throw new ApiError(401, "no_refresh", "No refresh token");
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async function () {
    try {
      const res = await fetch(API_BASE + "/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      const body = await res.json().catch(function () { return null; });
      if (!res.ok) throw extractError(res.status, body);
      setTokens(body);
      return body;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

function sessionExpired() {
  clearTokens();
  window.dispatchEvent(new CustomEvent("auth:expired"));
}

/* ---------- generic request ---------------------------------------- */
async function rawFetch(path, opts) {
  const method = opts.method || "GET";
  const headers = Object.assign({}, opts.headers || {});

  if (opts.auth !== false) {
    const tk = accessToken();
    if (tk) headers["Authorization"] = "Bearer " + tk;
  }
  let body;
  if (opts.body !== undefined && opts.body !== null) {
    if (typeof opts.body === "string") {
      body = opts.body;
      headers["Content-Type"] = headers["Content-Type"] || "application/json";
    } else {
      body = JSON.stringify(opts.body);
      headers["Content-Type"] = "application/json";
    }
  }

  let url = API_BASE + path;
  if (opts.query) {
    const qs = Object.keys(opts.query)
      .filter(function (k) { return opts.query[k] !== undefined && opts.query[k] !== null && opts.query[k] !== ""; })
      .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(opts.query[k]); });
    if (qs.length) url += (url.indexOf("?") >= 0 ? "&" : "?") + qs.join("&");
  }

  return fetch(url, {
    method: method,
    headers: headers,
    body: body,
    signal: opts.signal,
    cache: "no-store",
  });
}

/**
 * Perform a JSON API request with automatic token refresh on 401.
 * @returns {Promise<any>} parsed body (null on 204)
 */
export async function request(path, opts) {
  opts = opts || {};
  let res = await rawFetch(path, opts);

  if (res.status === 401 && opts.auth !== false && !opts._retried && !refreshExpired()) {
    try {
      await refreshTokens();
      opts = Object.assign({}, opts, { _retried: true });
      res = await rawFetch(path, opts);
    } catch (e) {
      sessionExpired();
      throw extractError(401, null);
    }
  }

  if (res.status === 204 || res.status === 205) return null;
  const body = await res.json().catch(function () { return null; });
  if (!res.ok) {
    if (res.status === 401 && opts.auth !== false) sessionExpired();
    throw extractError(res.status, body);
  }
  return body;
}

/* ---------- multipart upload (XHR → real progress events) ----------- */
function xhrUpload(path, formData, onProgress, signal) {
  return new Promise(function (resolve, reject) {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", API_BASE + path);
    const tk = accessToken();
    if (tk) xhr.setRequestHeader("Authorization", "Bearer " + tk);
    xhr.responseType = "text";
    /* Vision budget is 45s server-side — allow well over that so the UI
       keeps its loading state instead of dying on a client cut-off. */
    xhr.timeout = 75000;

    xhr.upload.onprogress = function (e) {
      if (onProgress && e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = function () {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch (e) { /* non-json */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(extractError(xhr.status, body));
    };
    xhr.onerror = function () { reject(new ApiError(0, "network", "Network error")); };
    xhr.onabort = function () { reject(new ApiError(0, "aborted", "Aborted")); };
    if (signal) {
      if (signal.aborted) { xhr.abort(); return; }
      signal.addEventListener("abort", function () { xhr.abort(); });
    }
    xhr.send(formData);
  });
}

/**
 * Upload multipart form data with progress callbacks; retries once after
 * a silent token refresh when the server answers 401.
 */
export async function upload(path, formData, onProgress, signal) {
  try {
    return await xhrUpload(path, formData, onProgress, signal);
  } catch (e) {
    if (e instanceof ApiError && e.status === 401 && !refreshExpired()) {
      await refreshTokens();
      return xhrUpload(path, formData, onProgress, signal);
    }
    throw e;
  }
}

/* ---------- SSE streaming (POST + fetch reader) --------------------- */
/**
 * POST `body` and consume a text/event-stream response.
 * @param {string} path
 * @param {object} body
 * @param {{onChunk?:Function, onCitations?:Function, onDone?:Function, onError?:Function, signal?:AbortSignal}} h
 */
export async function stream(path, body, h) {
  h = h || {};
  const headers = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  const tk = accessToken();
  if (tk) headers["Authorization"] = "Bearer " + tk;

  let res;
  try {
    res = await fetch(API_BASE + path, {
      method: "POST",
      headers: headers,
      body: JSON.stringify(body),
      signal: h.signal,
      cache: "no-store",
    });
  } catch (e) {
    if (e && e.name === "AbortError") return;
    const err = new ApiError(0, "network", "Network error");
    if (h.onError) h.onError(err);
    throw err;
  }

  if (!res.ok) {
    const bodyJson = await res.json().catch(function () { return null; });
    if (res.status === 401) sessionExpired();
    const err = extractError(res.status, bodyJson);
    if (h.onError) h.onError(err);
    throw err;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";

  function dispatchFrame(frame) {
    let ev = "";
    let data = "";
    frame.split("\n").forEach(function (line) {
      if (line.indexOf("event:") === 0) ev = line.slice(6).trim();
      else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
    });
    if (!data) return;
    let obj;
    try {
      obj = JSON.parse(data);
    } catch (e) {
      return;
    }
    const type = ev || obj.type || "";
    if (type === "chunk" && h.onChunk) h.onChunk(obj.delta || "");
    else if (type === "citations" && h.onCitations) h.onCitations(obj.citations || []);
    else if (type === "done" && h.onDone) h.onDone(obj);
    else if (type === "error" && h.onError) {
      const d = obj.detail;
      const msg = typeof d === "string" ? d : (d && (d.message || d.msg)) || "Stream error";
      const code = typeof d === "object" && d ? d.code : "";
      h.onError(new ApiError(200, code || "stream", msg));
    }
  }

  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buf += decoder.decode(chunk.value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        dispatchFrame(frame);
      }
    }
    if (buf.trim()) dispatchFrame(buf);
  } catch (e) {
    if (e && e.name === "AbortError") return;
    const err = new ApiError(0, "stream", "Stream interrupted");
    if (h.onError) h.onError(err);
    throw err;
  }
}

/* ---------- endpoint helpers --------------------------------------- */
export const api = {
  setTokens: setTokens,
  clearTokens: clearTokens,
  accessToken: accessToken,
  hasTokens: hasTokens,
  accessExpired: accessExpired,

  /* auth */
  register: function (payload) {
    return request("/auth/register", { method: "POST", body: payload, auth: false });
  },
  login: function (email, password) {
    return request("/auth/login", {
      method: "POST",
      body: { email: email, password: password },
      auth: false,
    });
  },
  me: function () {
    return request("/auth/me", {});
  },

  /* health (root, not under /api/v1) */
  health: async function () {
    try {
      const res = await fetch(API_ORIGIN + "/health", { cache: "no-store" });
      if (!res.ok) return null;
      return await res.json();
    } catch (e) {
      return null;
    }
  },

  /* crop doctor */
  diagnose: function (formData, onProgress, signal) {
    return upload("/crop-doctor/diagnose", formData, onProgress, signal);
  },
  diagnoses: function (limit, offset) {
    return request("/crop-doctor/diagnoses", { query: { limit: limit || 50, offset: offset || 0 } });
  },
  diagnosis: function (id) {
    return request("/crop-doctor/diagnoses/" + encodeURIComponent(id), {});
  },
  deleteDiagnosis: function (id) {
    return request("/crop-doctor/diagnoses/" + encodeURIComponent(id), { method: "DELETE" });
  },

  /* chatbot */
  createSession: function (payload) {
    return request("/chatbot/sessions", { method: "POST", body: payload || {} });
  },
  sessions: function () {
    return request("/chatbot/sessions", {});
  },
  messages: function (sessionId) {
    return request(
      "/chatbot/sessions/" + encodeURIComponent(sessionId) + "/messages",
      {}
    );
  },
  sendStream: function (sessionId, payload, handlers) {
    return stream(
      "/chatbot/sessions/" + encodeURIComponent(sessionId) + "/messages",
      payload,
      handlers
    );
  },
};

export default api;
