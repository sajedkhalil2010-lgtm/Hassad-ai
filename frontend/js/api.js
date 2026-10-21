/* =========================================================================
   Transport layer.

   One place that knows about the base URL, the bearer token and the
   backend's error envelope, so every other module can assume the happy path
   and let failures bubble up as a single ApiError type.

   The envelope is {ok:false, error:{kind, message, retryable}} where kind is
   one of input | auth | quota | busy | upstream | api. Callers mostly care
   about `retryable`: quota and busy mean "try again shortly", input and auth
   mean "change what you sent".
   ========================================================================= */

const TOKEN_KEY = "hassad.token";

/** Default ceiling for a single call. A cold vision analysis can run ~10s,
 *  so this is generous rather than tight. */
const DEFAULT_TIMEOUT_MS = 90_000;

/** Carries the backend's error envelope across the call boundary.
 *
 *  Deliberately not exported: `call()` throws it, but every consumer only
 *  reads `.message` / `.kind` / `.status` / `.retryable`, so exporting the
 *  type would promise an `instanceof` contract nothing uses. If a caller
 *  ever needs to discriminate on the type, export it then. */
class ApiError extends Error {
  constructor(status, kind, message, retryable) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.kind = kind;
    this.retryable = retryable;
  }
}

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    // Storage disabled (private window, blocked cookies). Auth still works
    // for this page load; it just will not survive a refresh.
    return null;
  }
}

export function setToken(value) {
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* see getToken */
  }
}

/**
 * Call the backend.
 *
 * Resolves with the parsed body on 2xx, rejects with ApiError otherwise —
 * so callers read as the success path and a single `catch` handles the rest.
 *
 * @param {string} path      e.g. "/api/v1/chat"
 * @param {object} [options]
 * @param {string} [options.method]   HTTP method, GET by default
 * @param {any}    [options.body]     JSON-serialised when given
 * @param {FormData} [options.formData] sent as multipart when given
 * @param {number} [options.timeout]  abort after this many ms
 */
export async function call(path, options = {}) {
  const { method = "GET", body, formData, timeout = DEFAULT_TIMEOUT_MS } = options;

  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  // Only set this for JSON: a FormData body needs the browser to generate
  // its own multipart boundary, and declaring one here breaks the upload.
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: formData !== undefined ? formData : body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    if (err && err.name === "AbortError") {
      throw new ApiError(0, "busy", "The request timed out. Please try again.", true);
    }
    throw new ApiError(0, "api", "Could not reach the backend. Is the server running?", true);
  }
  clearTimeout(timer);

  const payload = await res.json().catch(() => ({}));

  if (!res.ok || payload.ok === false) {
    const e = payload.error || {};
    throw new ApiError(
      res.status,
      e.kind || "api",
      e.message || `Request failed (HTTP ${res.status}).`,
      Boolean(e.retryable),
    );
  }

  return payload;
}

/**
 * GET /health, used for the connection pill. Deliberately tolerant: a
 * failure here means "offline", not "broken", so it resolves with a
 * boolean rather than throwing.
 */
export async function ping() {
  try {
    const res = await fetch("/health", { cache: "no-store" });
    if (!res.ok) return false;
    const body = await res.json();
    return body && body.ok !== false;
  } catch {
    return false;
  }
}
