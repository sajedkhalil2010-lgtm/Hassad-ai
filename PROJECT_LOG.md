# Hassad AI — Project Log & Architecture Record

> **حصاد AI** — multi-agent crop-leaf diagnosis and localized agronomic advisory.
> Target environment: `C:\Users\user\Desktop\حصاد v1` (Windows, local).
> Newest entries at the bottom of the Progress Log.

---

## 1. Environment & local path

| Item | Value |
|---|---|
| Project root | `C:\Users\user\Desktop\حصاد v1` |
| Backend code | `حصاد v1\backend\` — Python package, owns `.env` |
| Frontend code | `حصاد v1\frontend\` — `index.html` + `css/` + `js/` |
| Python | 3.14.7 |
| Web framework | FastAPI 0.143.0 + Uvicorn 0.54.0 |
| Groq SDK | **`groq` 1.7.0** (OpenAI-compatible chat completions) |
| Settings | pydantic-settings 2.15.0 / pydantic 2.14.0 |
| Upload parsing | python-multipart 0.0.32, Pillow 12.3.0 |
| VCS | git, initialized at project root, `.env` excluded |

The folder was **empty** at initialization — no prior files were overwritten.

---

## 2. Single-key secure architecture

The whole system runs on **exactly one credential**: `GROQ_API_KEY`.

```
.env                       <- GROQ_API_KEY=<key>   (git-ignored, never committed)
  └─> agents/config.py     <- Settings (pydantic-settings: env > .env > default)
        └─> SecretStr      <- repr()/str() render as **********
              ├─> DiagnosticAgent  (vision pass -> qwen/qwen3.8-27b)
              └─> AdvisorAgent     (advisory  -> openai/gpt-oss-120b)
                    └─> SupervisorAgent  (orchestration only — holds no key)
```

> **Provider migration (2026-10-21).** The project moved from Gemini to Groq.
> `google-genai` is no longer a dependency; `GEMINI_API_KEY` may remain in
> `.env` but is ignored by `Settings` (`extra="ignore"`).

Security properties, all covered by the offline test suite:

* **One key, no pool.** There is deliberately no key-rotation or multi-key
  machinery — a single credential keeps the trust boundary obvious.
* **`SecretStr` containment.** The key lives in a `pydantic.SecretStr`, so
  `repr(settings)` and `str(settings.groq_api_key)` render `**********`.
  It is only ever unwrapped inside `Settings.api_key`, at the two call sites.
* **Placeholder rejection.** Shipped sample values (e.g.
  `gsk_your_actual_key_here`) are treated as *unconfigured*, not as a live
  credential (`Settings._reject_placeholder`, matched by exact value **and**
  by substring markers, so a `gsk_`-prefixed sample string cannot masquerade
  as real). A deployment that forgot to set the key fails loudly and
  immediately rather than sending a bad-key request.
* **Never emitted in any payload.** `/health` returns only the key *state*
  (`"not configured"` or a 4+4 masked fragment). No error message, stage entry,
  traceback or HTTP body ever contains the key — asserted explicitly in tests.
* **`.gitignore` hardening.** `.env` and `.env.*` are ignored while
  `.env.example` (the clean template) is explicitly re-included with `!.env.example`.

---

## 3. System structure

```
حصاد v1/                          <- project root (git, launchers, docs)
├── .gitignore                    # secrets + python + uploads + logs
├── PROJECT_LOG.md                # this file
├── start.bat                     # dev launcher  -> python run.py
├── start_stable.bat              # prod launcher -> uvicorn main:app (no reload)
├── backend/                      # ALL Python lives here; cwd for every run
│   ├── .env                      # GROQ_API_KEY (git-ignored, never committed)
│   ├── .env.example              # clean committed template
│   ├── main.py                   # FastAPI app: CORS, /health, /analyze, /chat, auth, UI
│   ├── run.py                    # preflight + supervisor + port probe
│   ├── manage.py                 # CLI: list-users / promote / demote / db-info
│   ├── hassad.db                 # accounts + sessions (git-ignored, created on start)
│   ├── requirements.txt
│   ├── agents/
│   │   ├── __init__.py           # public surface: 4 agents + AgentError
│   │   ├── base.py               # AgentError / AgentResult / coercion / JSON / image utils
│   │   ├── config.py             # Settings — the single-key source of truth
│   │   ├── diagnostic_agent.py   # vision pathology via Groq (qwen3.8-27b)
│   │   ├── advisor_agent.py      # Tébessa-localized advisory (Groq + local KB)
│   │   ├── chat_agent.py         # conversational agronomist (groq-20b)
│   │   └── supervisor_agent.py   # orchestration, error policy, JSON contract
│   ├── auth/                     # email/password accounts — stdlib only
│   │   ├── __init__.py           # public surface: AuthStore + build_auth_router
│   │   ├── passwords.py          # scrypt hashing, opaque session tokens
│   │   ├── store.py              # SQLite users + sessions
│   │   └── routes.py             # /api/register|login|logout, /api/me, admin
│   └── tests/                    # run as: python -m tests.<name> from backend/
│       ├── test_offline.py       # 77 checks — no network, no key
│       ├── test_sdk_shape.py     # 93 checks — real groq APIError mapping
│       ├── test_quota_retry.py   # 33 checks — 429 backoff policy
│       ├── test_chat_agent.py    # 109 checks — chat contract + trust boundary
│       ├── test_auth.py          # 93 checks — hashing, sessions, throttle, admin
│       ├── test_ui.py            # 97 checks — page, assets, design system, guards
│       ├── test_frontend_static.py # 69 checks — module imports, ids, syntax balance
│       └── test_e2e.py           # live, opt-in
└── frontend/                     # served by FastAPI, never imported
    ├── index.html                # markup only, no inline <style>/<script>
    ├── css/styles.css            # tokens, layout, dark theme, reduced motion
    └── js/                       # ES modules, each mounted at /assets/js/*
        ├── api.js                # transport, bearer token, error envelope
        ├── dom.js                # textContent-only element helpers
        ├── auth.js               # account dialog — sign in / out / admin list
        ├── diagnose.js           # drop zone, local preview, results grid
        ├── chat.js               # agronomist panel, history, suggestions
        └── app.js                # entry — wires the three panels, status pill
```

### 3a. `agents/base.py` — shared contracts
Dependency-free (imports **no** SDK) so the supervisor can classify failures
without pulling in Google's client library.

* `AgentError(kind, message, retryable, detail)` — a stable failure taxonomy:
  `auth` | `quota` | `busy` | `upstream` | `input` | `api`. It never carries the key.
* `AgentResult` — the uniform `{ok, data, error}` envelope every agent returns.
  `success()` coerces non-dict payloads to `{}` so a stray `None` cannot
  propagate as a fake result.
* Defensive coercers for untrusted model output: `clamp01`, `as_str`,
  `as_str_list`, `as_dict`, `as_severity`.
* `parse_model_json` — extracts a JSON object from fenced / prose-wrapped model
  output, while **rejecting** a top-level JSON array as a schema violation.
* `normalise_image_bytes` — magic-byte MIME sniffing (JPEG/PNG/WebP/BMP/TIFF),
  alpha flattening, RGB conversion and downscale to ≤1600 px (JPEG q88), which
  keeps payloads small and latency low.

### 3b. `agents/diagnostic_agent.py`
One schema-enforced vision pass per image.

* Builds the single Gemini client lazily from `Settings` (fails with
  `kind="auth"` when no key is configured).
* Sends `Part.from_bytes(...)` + a strict inline JSON contract; requests
  `response_mime_type="application/json"`.
* Extracts, normalises and returns: crop, disease (EN+AR), pathogen, pathology
  narrative, symptoms, severity enum + `severity_pct`, `confidence`,
  `confidence_basis`, differential diagnoses and image quality.
* Flags `low_confidence` when `confidence < 0.55`.
* **Never raises** — returns an `AgentResult` for every failure path.

### 3c. `agents/advisor_agent.py`
Turns a diagnosis into a localized plan for **Tébessa, Algeria** — semi-arid
high plateau, 300–400 mm erratic rainfall, calcareous Terra Rossa soils
(~pH 8), strong summer winds; dominant crops durum wheat, barley, olive, almond,
fig, pepper, potato.

Two sources, in order of preference:

1. **Gemini** — one structured generation, localized prompt.
2. **Local Tébessa KB** (`LOCAL_KB`) — built-in, offline, keyed by condition
   (early blight, late blight, powdery mildew, rust, nitrogen deficiency,
   healthy). Used whenever the API is unavailable, the key is missing, or the
   response is unusable — so a quota error **never** dead-ends a farmer.

Every plan carries: immediate actions, cultural/organic/chemical treatments
with real doses, a drip-irrigation schedule (morning-only, field-capacity
targets, deficit strategies), prevention guidelines, an Arabic regional-context
block, and a disclaimer pointing to the **DPA of Tébessa / Chambre
d'Agriculture**. The remote output is merged with the KB so safety-critical
fields (disclaimer, region, context) are always present.

### 3d. `agents/supervisor_agent.py`
Orchestration only — **never calls a model itself**.

* Dispatches diagnostic → advisory, timing and recording each stage.
* Applies a threshold policy: `confidence < 0.55` ⇒ status `provisional`,
  otherwise `confirmed`.
* **Strict error policy:** a diagnostic failure fails the whole report with a
  classified error; an *advisory* failure is **non-fatal** — the honest
  diagnosis is still delivered with `advisory.unavailable = true`.
* Always returns one JSON-serialisable report (`run_id`, `pipeline`, `status`,
  `diagnosis`, `advisory`, `confidence`, `stages[]`, `timing`).

### 3e. `main.py` — FastAPI backend
* CORS middleware enabled (origins configurable via `cors_origins`).
* `GET /health` — service/version, pipeline id, agent list, model, region, and
  the key **state** only (`configured`, `key_state`).
* `POST /api/v1/analyze` — multipart image upload, size guard (8 MB → 413),
  empty/garbage guards (→ 400), routed through `SupervisorAgent`. Failure
  kinds map to HTTP codes: `input`→400, `auth`→500, `quota`→429, `busy`→503,
  `upstream`/`api`→502.

---

## 4. Validation

| Suite | Result | Coverage |
|---|---|---|
| `tests/test_offline.py` | **66/66** | config hygiene, base coercers, JSON extraction, image normalisation, no-key auth failure, local KB matching, supervisor orchestration + both failure policies, `/health`, 400/413 guards, no key leakage, CORS |
| `tests/test_sdk_shape.py` | **63/63** | exact `google-genai` 2.29 call shapes, malformed-output resilience, **real `APIError`** mapping (401/403/429/400/5xx), timeout → `busy`, advisor remote+merge+fallback, `advisor_fallback_enabled=False` |
| Live server smoke | **pass** | uvicorn booted on 127.0.0.1:8077; `/health` 200, `/api/v1/analyze` returns structured JSON, preflight 200 with `acao: *`, 404 confirmed |

**Zero network calls and zero quota consumed** during validation — the Gemini
integration is proven by shape and by constructing genuine
`google.genai.errors.APIError` instances.

### Bugs caught and fixed during validation
1. `_stage()` crashed on `None` data — `AgentResult.success()` now coerces
   non-dict payloads to `{}`, and `_stage()` guards defensively.
2. `parse_model_json('[{"a":1}]')` silently unwrapped a top-level JSON array
   into `{"a":1}`. The parser now rejects non-object JSON as a schema
   violation instead of papering over it.

---

## 5. Running it

```bash
cd "C:\Users\user\Desktop\حصاد v1\backend"     # every command runs from here

# 1. put your real key in backend/.env
#    GROQ_API_KEY=gsk_...

# 2. (optional) create an isolated environment
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 3. validate offline — no key, no network, no quota
python -m tests.test_offline
python -m tests.test_sdk_shape

# 4. run  (or double-click start_stable.bat at the project root)
uvicorn main:app --host 127.0.0.1 --port 8000
```

Then `POST` an image:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/analyze" -F "file=@leaf.jpg"
```

---

## 6. Progress log

| # | Date | Step |
|---|---|---|
| 1 | 2026-10-19 | Recon: target `C:\Users\user\Desktop\حصاد v1` confirmed **empty** (UTF-8 name verified byte-by-byte — PowerShell console encoding had mangled it). Toolchain inventoried: Python 3.14.7, `google-genai` 2.29.0, FastAPI 0.143, pydantic-settings 2.15, Pillow 12.3, python-multipart. |
| 2 | 2026-10-19 | Decisions confirmed with the user: **`google-genai`** (modern SDK) over the deprecated `google-generativeai`; **git init + first commit**; advisor = **Gemini + local Tébessa fallback**. |
| 3 | 2026-10-19 | Scaffolding: `.env` (placeholder), `.env.example`, `.gitignore` (secrets-first), `requirements.txt`, `agents/` package, `config.py` with `SecretStr` single-key settings + placeholder rejection + masked reporting. |
| 4 | 2026-10-19 | `agents/base.py` — `AgentError` taxonomy, `AgentResult` envelope, defensive coercers, fence-tolerant JSON parser, magic-byte image normaliser with ≤1600 px downscale. |
| 5 | 2026-10-19 | `agents/diagnostic_agent.py` — lazy single-key client, inline JSON contract, `response_mime_type="application/json"`, full output normalisation, never-raise contract. |
| 6 | 2026-10-19 | `agents/advisor_agent.py` — Tébessa regional KB (6 condition profiles, real AR/EN, drip schedules, DPA disclaimer) + Gemini generation with KB merge and offline fallback. |
| 7 | 2026-10-19 | `agents/supervisor_agent.py` — orchestration loop, stage timing, provisional/confirmed threshold policy, strict error policy (advisory failure is non-fatal). |
| 8 | 2026-10-19 | `main.py` — FastAPI + CORS, `GET /health`, `POST /api/v1/analyze`, size/type guards, failure-kind → HTTP status mapping. |
| 9 | 2026-10-19 | Validation suite written and run: **66/66 offline**, **63/63 SDK-shape**, live uvicorn smoke pass. Two real bugs caught and fixed (crash on `None` result data; JSON-array parser leniency). Zero network calls, zero quota. |
| 10 | 2026-10-19 | `PROJECT_LOG.md` written; git initialized and initial commit created with `.env` verified excluded. |
| 11 | 2026-10-19 | **Runtime verification with a live key.** The key in `.env` authenticates fine (the `AQ.` prefix is simply a newer Google key format — the earlier "suspicious format" suspicion was wrong). |
| 12 | 2026-10-19 | **Critical runtime bug found:** `gemini-2.5-flash` is retired for new API users and returns `404 NOT_FOUND`. Every diagnose call would have failed. Default switched to **`gemini-3.8-flash`** (confirmed working), and `request_timeout_s` raised 60 s → **120 s** after a real vision pass measured 49-54 s. |
| 13 | 2026-10-19 | **`main.py` refactored into `create_app(settings, supervisor)`** so tests inject isolated settings instead of reading the live `.env`. The offline suite had become non-hermetic (6 failures once a real key existed); it now builds its own `Settings(_env_file=None, ...)`. Suite grew 66 → **74** checks. |
| 14 | 2026-10-19 | **`run.py` + `start.bat` launchers.** Pins `sys.path` to this folder and *verifies* `main`/`agents` resolve inside it, so sibling checkouts (other Hassad folders on the Desktop) can never be imported. Reports key state (masked), probes for a free port from 8000 up, waits on `/health`, prints the exact URLs. |
| 15 | 2026-10-19 | **Port-probe bug fixed:** the first `pick_port` used `SO_REUSEADDR`, which on Windows means "allow binding over an existing listener", so a busy port was reported free. Now binds with `SO_EXCLUSIVEADDRUSE`, which fails outright when anything holds the port; added `port_owner()` to name the occupant via netstat/tasklist. |
| 16 | 2026-10-19 | **Live end-to-end pass:** `GET /health` 200 (`gemini-3.8-flash`, `configured: true`, key masked, no leak); `POST /api/v1/analyze` 200 with a fully populated diagnosis + Gemini advisory, both stages `ok`. Correctly reported `confidence 0.05` / `is_leaf_image: false` on a synthetic test image — honest low confidence rather than fabricated certainty. |
| 17 | 2026-10-19 | **Legacy conflict identified:** a stale `python -m uvicorn main:app` (PID from `dotenv run`, the *other* project's launcher) has been holding **port 8000**, serving the retired `supervisor_v4`/`local_cv` app. The new launcher detects it, names it, and falls back to 8001. This is exactly the legacy-code interference the project must avoid. Stopped on the user's instruction so this project owns port 8000. |
| 18 | 2026-10-19 | **Lightweight test UI** — `static/index.html`, a single self-contained file (no CDN, no framework, ~8.6 KB). File input with live preview, Analyze button, elapsed-time ticker, summary tiles, and three collapsible JSON panes (diagnostic / advisor / supervisor). Served at `/` via `FileResponse`, path resolved from `__file__` so it loads regardless of the caller's cwd. Root is excluded from the OpenAPI schema. |
| 19 | 2026-10-19 | **UI notes:** fetch timeout set to 240 s (a live run is 28–70 s); `?api=` query param lets the page target another origin; non-200 responses still render the raw body for inspection rather than hiding it; the DOM is built with `textContent` only, so model-generated Arabic text can never inject markup. |
| 20 | 2026-10-19 | **Test coverage:** `tests/test_ui.py` (**19 checks** — route, content-type, required controls, other endpoints unaffected, upload guards still rejecting) plus `tests/test_e2e.py` (live end-to-end through the exact request the UI issues: real Gemini call → `Early blight` / اللفحة المبكرة + Tébessa advisory in 28.7 s, both stages `ok`, no key leaked). Suite total **156**. |
| 21 | 2026-10-19 | **`start_stable.bat`** — production launcher that runs `uvicorn main:app --host 127.0.0.1 --port 8000` **directly, with no `--reload`**. `run.py` and `start.bat` use `reload`-less uvicorn too, but wrap it in a Python supervisor thread; this script removes that layer entirely so the process tree is a single uvicorn — nothing restarts mid-session, and connections stay up across repeated image uploads. Prints the exact URLs before binding, and pre-checks port 8000 with `netstat` so a clash reports a clear message instead of a raw `[Errno 10048]`. |
| 22 | 2026-10-19 | **HTTP 429 / quota resilience.** Reporting said a 429 "crashes or returns `{}`"; probing the real stack showed it did neither — it already returned a structured 429. The genuine gaps were **no retry** and an **English-only** message, both now fixed. `agents/base.py` gained `is_quota_error()` (duck-typed on `.code`/`status`, so base.py keeps its zero-SDK-import property) and `run_with_quota_retry()`, an injectable-sleeper loop that retries **only** quota errors with exponential backoff (2s, 4s — deliberately short, since an analysis already costs tens of seconds); a 400 or bad key is never retried, because that only burns more quota. Both agents wrap their `generate_content` call in it, with `quota_retry_attempts` / `quota_retry_base_delay_s` on `Settings`. `AgentError` gained `message_en`, and the quota `message` is now the Arabic string the user asked for — `static/index.html` renders `error.message` directly, so the farmer sees it with no frontend change. **Bug found and fixed:** `advisor_agent.py` discarded the classified error whenever the local-KB fallback was disabled, replacing it with a generic "No advisory source available." — losing both the Arabic text and the true `retryable` flag; it now surfaces the original error. Suite **192** (new `tests/test_quota_retry.py`, 31 checks). Live-verified against a **real** 429 from Gemini: `duration_ms` 502 → **7397** (3 calls + backoff) and the Arabic message in the HTTP body, no traceback, no key. |
| 23 | 2026-10-21 | **Gemini → Groq migration (full backend switch).** `google-genai` removed from `requirements.txt`, `groq` 1.7.0 added. `Settings` now exposes `groq_api_key` (SecretStr), `vision_model`, `advisor_model`; `gemini_model`/`gemini_api_key`/`gemini_temperature`/`gemini_max_output_tokens` are gone. **Model IDs checked against Groq's live deprecation page before coding** — the requested `llama-3.2-11b-vision-preview` has been **shut down since 2025-04-14** and would have failed every call, so vision runs **`qwen/qwen3.8-27b`** (the only production Groq vision model, and it supports JSON mode) per user decision; advisor initially kept the user's explicit choice `llama-3.3-70b-versatile` **(superseded by entry 24 — the id proved to be already retired, not merely scheduled)**, which carries a 2026-08-16 deprecation notice — so a `NotFoundError` (404) is now classified **non-retryable** and tells the operator exactly which config field to change, instead of looping. **Both agents rewritten** to `client.chat.completions.create(...)` with `response_format={"type":"json_object"}`; the vision call encodes the pre-normalised JPEG as `data:image/jpeg;base64,...` inside an `image_url` part. Clients are built with **`max_retries=0`** so Groq's own internal retries cannot stack under `run_with_quota_retry` (3×3 calls). `is_quota_error()` now reads **`status_code`** — Groq sets neither `.code` nor `.status`, so the old detector would have matched nothing and silently disabled the retry. `_classify_sdk_error` maps Groq's subclasses (`RateLimitError`/`AuthenticationError`/`NotFoundError`/`APITimeoutError`). Diagnosis output gains the frontend key **`recommendations`** (always non-empty). Arabic quota message made provider-neutral (it previously named Gemini). `run.py` module probe, `/health` (`provider`, both models) and `requirements.txt` updated. **Suite 222** (77 offline / 93 sdk-shape / 33 quota-retry / 19 ui), all green. `.env` now carries `GROQ_API_KEY`; its shipped placeholder is rejected via substring markers so a `gsk_`-prefixed sample value cannot masquerade as a real key. |
| 24 | 2026-10-21 | **Live credential verification + advisor model corrected.** With a real `GROQ_API_KEY` in `.env`, `client.models.list()` returned **11 models** and settled what the docs could not: `llama-3.3-70b-versatile` is **absent from the account and returns model-not-found** — already retired, not merely scheduled. Groq's own models page still listed it as Production, so a doc-only check is provably insufficient here. Vision was confirmed live (`qwen/qwen3.8-27b` AVAILABLE) and the pipeline already ran end-to-end on it. Four available text models were then tested against the advisory's real requirement (`response_format=json_object`) — all four produced valid JSON: `openai/gpt-oss-120b` (2958 ms), `openai/gpt-oss-20b` (816 ms), `qwen/qwen3.8-27b` (508 ms), `allam-2-7b` (415 ms). **Advisor set to `openai/gpt-oss-120b`** per user decision — highest reasoning quality, chosen because advisory output includes pesticide dosages where accuracy is safety-relevant, and +2 s is negligible beside the vision call. **New startup preflight** `run.py::check_models()` (step **[4/5]**) probes the live models endpoint for both configured ids and names the exact config field to fix — this is what would have caught the retired id at launch rather than via an e2e run. Deliberately **non-blocking**: offline/timeout/auth failures degrade to a warning, because the advisor retains its local-KB fallback and a hard dependency here would make the server unlaunchable without internet. **Launcher crash fixed** (pre-existing, surfaced by redirecting output): `run.py` printed `PROJECT_ROOT` — a path containing the Arabic folder name — to a stdout defaulting to **cp1252**, dying with `UnicodeEncodeError` on a `print` the moment output went to a file or pipe; stdout/stderr are now reconfigured to UTF-8 with `errors="replace"`. `main.py` needed no change (FastAPI serializes JSON as UTF-8). **Leak test fixed:** `test_e2e.py` still asserted on `"AQ.Ab8"`, the *Gemini* key prefix — post-migration it would have passed even if the live Groq key were returned verbatim; it now checks the actual configured secret plus a 12-char prefix too short to be satisfied by any masked form (`gsk_...L3A9`). **Live end-to-end, both stages on Groq:** `Early blight` / اللفحة المبكرة, confidence 0.92, `Advisory source: groq`, advisory grounded on Tébessa, HTTP 200, no key in body. Diagnostic 1994 ms + advisor 5743 ms = **7.7 s** total (Gemini's single stage ran ~30 s). Suite still **222** green. |
| 25 | 2026-10-21 | **Chat agent (`/api/v1/chat`) — interactive agronomist.** New `agents/chat_agent.py`: one conversational turn, JSON-mode, reusing the existing single-key Groq client pattern, the `AgentResult`/`AgentError` envelope and `run_with_quota_retry`. **The requested `llama-3.3-70b-versatile` was rejected on live evidence** — a direct call returned HTTP 404 ("does not exist or you do not have access to it") and the id is absent from `models.list()`: the same retired id the advisor hit. Chat therefore defaults to **`openai/gpt-oss-20b`** (~0.8–2.0 s measured) rather than the heavier `-120b` (~3.0 s), because a farmer is sitting there typing. `Settings` gained `chat_model`, `max_chat_chars` (2000) and `max_chat_history` (12); `check_models()` now probes all three ids at startup. **Trust boundary** — the client is not trusted: history is role-whitelisted (`user`/`assistant` only, so a spoofed `system` turn is dropped), content is truncated, and the oldest turns are discarded; the optional `diagnosis` context copies only whitelisted scalars and never the raw payload, so a caller cannot smuggle instructions into the prompt (tested against an `instructions` / `IGNORE ALL PREVIOUS INSTRUCTIONS` payload). **Deliberately no KB fallback** — unlike the advisor, whose questions are a closed set, inventing an answer to "how much copper do I spray on my olives?" is worse than admitting the outage, so failures return an honest retryable error. The system prompt grounds the model as an Algerian steppe agronomist (local crops, steppe constraints, Tuta absoluta / bayoud / rusts), requires mirroring the farmer's language, forbids inventing Algerian registrations, and always defers label and pre-harvest-interval confirmation to the DPA. **Bug caught by the new tests:** `ask()` called `_normalise()` unguarded, and because `_normalise` raises when the model omits `reply`, an upstream oddity escaped as a bare exception — a 500 traceback instead of a structured failure, breaking the "never raises" contract the other agents keep; now guarded, with an HTTP-level regression test. **One error envelope** — FastAPI's default 422 is `{"detail": [...]}`, a second shape the chat UI would have to special-case, so a `RequestValidationError` handler now emits `{ok:false, error:{kind:"input", message, retryable:false}}` with a readable field-named message (`history.0.role: Input should be 'user' or 'assistant'`). The route is a sync `def`, so FastAPI runs the blocking Groq call in its threadpool instead of stalling the event loop the way the existing `async def analyze` does. **Suite 331** (+109 in the new `tests/test_chat_agent.py`). Live-verified: English and Arabic turns, multi-turn follow-up (`history_turns: 2`), diagnosis-context grounding, and an analyze re-run for regression (`Advisory source: groq`). |
| 26 | 2026-10-21 | **Project restructured into `backend/` + `frontend/`.** All Python (`main.py`, `run.py`, `requirements.txt`, `agents/`, `tests/`) moved under `backend/`; the UI moved out of `static/` into `frontend/` and was split into `index.html` + `css/styles.css` + `js/app.js`. **`.env` moved *with* the code to `backend/.env`** so `config.py`'s existing `parent.parent / ".env"` resolution still lands on it unchanged - no path logic was forked - and the move was a byte copy verified by SHA-256 before and after (`EF0EBD81...9015`, 141 bytes), since the key cannot be reconstructed if lost. `FRONTEND_DIR` is derived from `__file__`, never the cwd, and the directory is mounted at `/assets`, so `main.py` stays correct no matter where uvicorn is started; `/` still returns index.html via an explicit route with a pinned charset. Both launchers now `pushd` into `backend\` (probing `backend\.venv` first, then a root `.venv`) - that is what keeps `main:app` and the `agents` package resolving. `test_ui.py` grew **19 -> 31 checks**, adding assertions that the split assets serve with the right content types and that `/assets/../main.py` (and its URL-encoded form) return 404 without leaking backend source. Suite **343** (77 offline / 93 sdk-shape / 33 quota-retry / 109 chat / 31 ui), all green; every tracked file was moved with `git mv` so history follows. Live-verified on the restarted server: `/` 200 with no inline `<style>`/`<script>`, both assets 200, `/health` reporting all three models with the key still masked (`gsk_...L3A9`), and chat turns returning 200 (`history_turns: 2`, `latency_ms` 1216-1942). **Follow-up surfaced by that live run:** a single transient Groq 400 was classified by `chat_agent.py:169` as `kind="input"`, `retryable=False`, so the farmer saw "The chat request was rejected by the model." - yet the identical payload succeeded on all later attempts. Upstream 400s are being reported as caller fault and marked non-retryable; not changed here, since it alters the tested error contract. |
| 27 | 2026-10-21 | **Local database + email/password authentication.** Google Sign-In was **deliberately dropped** at the user's decision — it cannot function without an OAuth Client ID from Google Cloud Console and this deployment has none, so a dead button would have been worse than no button. The word `google` now appears nowhere in the backend, frontend or tests (asserted in `test_ui`). New `backend/auth/` package with **zero new dependencies**: `hashlib.scrypt` (memory-hard, OWASP-listed, ships inside CPython, no build step — compiling bcrypt is a common and hard-to-diagnose stumble on Windows) for passwords, stdlib `sqlite3` for storage. `backend/hassad.db` holds two tables (`users`, `sessions`), git-ignored along with its `-wal`/`-shm` sidecars. **Hashes are self-describing** (`scrypt$N$r$p$salt$hash`), so raising `SCRYPT_N` later never signs anyone out; the default N=2**15/r=8 costs ~32 MiB and ~200 ms measured on this machine, and `maxmem` is set explicitly because OpenSSL's own 32 MiB ceiling would otherwise make identical parameters work on one machine and raise on another. **Only a SHA-256 digest of each session token is stored**, so a database file that walks off cannot be replayed; tokens are opaque bearer strings rather than JWTs, because a single resource server gains nothing from a self-contained token and loses the ability to revoke. Routes at the exact paths requested — `POST /api/register` (201), `POST /api/login`, `POST /api/logout`, plus `GET /api/me` and admin-only `GET /api/admin/users` — all emitting the existing `{ok,error:{kind,message,retryable}}` envelope, with `409` added for a duplicate address. **Two enumeration defences**: a wrong password and an unknown email return the same message *and* the same timing (the store burns a real scrypt against a dummy hash when no row matches, so a microsecond response cannot reveal that an address is unregistered). Emails are normalized and compared `COLLATE NOCASE`, so `Farmer@X.com` cannot become a second account beside `farmer@x.com`. `LoginThrottle` (5 failures / 300 s, keyed by email+IP, injectable clock) returns 429 with a retry-after message. **Admin** is seeded from `ADMIN_EMAILS` at registration *and* re-checked on login, so editing `.env` promotes an existing account with no database surgery; `python -m manage list-users|promote|demote|db-info` is the shell view and builds every row from `PUBLIC_USER_FIELDS`, so a future column cannot leak a hash by accident. `create_app` gained an injectable `auth_store`, and schema creation moved into a `lifespan` hook so merely importing `main` — as the suite does — cannot create a database file. **Auth runs alongside, not in front of, the pipeline**: `/api/v1/analyze` and `/api/v1/chat` still work with no token (asserted, so an account can never lock the farmer out of the tool). The frontend gained a tabbed sign-in / create-account panel that restores the session from `/api/me` on load; all user text is written with `textContent`, and the only `innerHTML` assignments left are empty-string clears (asserted). Suite **456** (77 offline / 93 sdk-shape / 33 quota-retry / 109 chat / **93 auth** / **51 ui**). Live-verified end to end against the real server, including the admin listing, with no password hash and no `scrypt$` in any response body. |
| 28 | 2026-10-21 | **Bespoke dashboard UI — the test UI replaced.** The page is now a designed agritech dashboard: a sticky, blurred topbar (leaf mark, حَصاد AI, a live `/health` pill, an understated account trigger), a two-column workspace with diagnosis on the left and the agronomist on the right (chat sticky on desktop, stacked below 1040 px, single column below 620 px), and a modal account dialog. **Design system** lives entirely in tokens in `:root`; the dark theme and the reduced-motion variant only re-point those tokens, so no component carries a dark-mode branch of its own. Structure comes from 1px borders and space — shadows are reserved for things that genuinely float (the dialog, a hovered button), per the brief's "subtle borders, no heavy shadows". The requested palette (`#0f766e` / `#047857` accents on `#f8fafc` / `#ffffff` / `#1e293b`) and both typefaces are asserted by the test, so a drift is caught rather than shipped. **Type:** Tajawal + Plus Jakarta Sans via Google Fonts with `preconnect` and `display=swap`, so text paints immediately in the system stack and the faces are a pure upgrade — deleting the two `<link>` lines leaves a fully usable page. This is the **one external request the app now makes**, flagged deliberately against the project's offline ethos: it was chosen because those two faces were requested by name and cannot be self-hosted with the tools available. **`test_ui`'s bare-word `google` assertion narrowed to sign-in markers** (`accounts.google.com`, `gapi`, `gsi/client`, `oneTap`, `id_token`); entry 27's claim that the word appears nowhere is **superseded**. A font CDN is not an identity integration, and asserting on the bare word would have either blocked the requested typefaces or — worse — gone silently meaningless the next time a font link was added. The property that actually matters (no sign-in code) is now tested directly. **JS split into six ES modules**, one concern each. `dom.js` is the only path to dynamic text: `el()` sets `textContent`, so a model reply, an account name or a filename can never be parsed as markup — the XSS surface is closed by construction rather than by remembering to escape each site, and no `innerHTML` assignment writes content (asserted). **The `?api=` origin override was removed.** The page is served by the same FastAPI app that owns the API, so every request is same-origin: no base URL to configure, no CORS to reason about. **Results grid is the product** — crop, diagnosis in Latin and Arabic, a severity chip, a confidence meter driven by the supervisor's own `overall` figure and `provisional` flag, image quality — with symptoms and a numbered guidance block (actions, treatments, irrigation, prevention, disclaimer) beneath, and the raw diagnostic / advisory / supervisor payloads one disclosure away. A degraded advisory is labelled as such with the real `fallback_reason` rather than passing silently for a live one. Chat keeps history client-side (trimmed to 24 turns against the backend's 40 cap), sends a whitelisted four-field slice of the last diagnosis so the agronomist can answer about *this* plant, and renders failures inline beside the message that caused them. **Three real bugs the new static suite caught before shipping:** the typing indicator was defined but never called, so the farmer would have seen nothing while a turn ran; the intro bubble was never removed, so it would have sat above every message permanently; and `auth.js` redefined `$` instead of importing it while `ApiError` was exported but never imported — dead surface on both sides. Fixed by wiring `startTyping()`, dropping the intro on first send, importing `$` from `dom.js` and un-exporting `ApiError`. **`test_ui` 51 → 97 checks** (per-module content types, palette / typeface / reduced-motion assertions, workspace and chat structure, routing guards) plus a new `tests/test_frontend_static.py` with **69 checks** that parse the modules directly: every `import` resolves to a file that exists, every imported symbol is genuinely exported, every `$()` and `querySelector()` target exists in the markup, every export is consumed by another module, and braces balance once comments and strings are stripped. That last suite is what catches the class of breakage a browser-only test reports as a blank page with no console error — and it earned its place by finding the three bugs above. Measured over live HTTP: 12.0 KB HTML + 31.5 KB CSS + 36.9 KB JS = **~78 KB uncompressed**, no framework, no build step. Suite **571** (77 offline / 93 sdk-shape / 33 quota-retry / 109 chat / 93 auth / 97 ui / 69 static), all green. |
| 29 | 2026-10-21 | **Vision 400s were being blamed on the farmer's photo** (commit `ed4ac39`). "The image payload was rejected by the vision model" was reported for *any* Groq HTTP 400 with `retryable=False`, so a transient upstream fault was presented to the farmer as "your photo is broken — do not retry". Fixed by evidence rather than conjecture. `normalise_image_bytes` was probed across 16 real-world variants (alpha, CMYK, palette, 16-bit, EXIF rotation, truncated, non-image): **13 normalised correctly and all 3 rejections were correct**; the live pipeline then returned 200 with a correct diagnosis for JPEG, WebP and a 3000 px camera photo. The payload was never the problem. The classifier was then probed against **live Groq** to learn its real 400 wording instead of guessing it — a corrupt image yields `invalid base64 url`, while `response_format` / `max_tokens` / `stop` / `temperature` faults say nothing about media. So a 400 is the farmer's fault **only when the upstream message names the image**; everything else stays retryable (`busy`/503 rather than `input`/400). The defect lived in **two of three agents** because each carried its own copy, and the third copy (`advisor_agent.py`) had **no 400 branch at all** — correct only by accident. All three now delegate to one `classify_http_error()` in `base.py`, taking `media=True` only for the vision call. **Closes the open issue logged in entry 26** against `chat_agent.py`. Separately, `normalise_image_bytes` now applies `ImageOps.exif_transpose`: cameras record orientation in EXIF rather than rotating pixels, so a portrait photo reached the model sideways — the most common phone-upload variant there is (verified: orientation=6 now yields 600×800 instead of 800×600). Two tests pinned the old contract `(400, "input", False)` and were updated, plus new checks asserting the media/non-media split using wording **observed from live Groq**. Suite **582** (from 571). |
| 30 | 2026-10-21 | **Dark emerald glassmorphism redesign** (commit `0883731`), replacing the light theme of entry 28. The brief asked for CDN Tailwind and Lucide; **the user was asked and chose a hand-rolled build**, because Tailwind's CDN is a browser-side JIT compile that costs render time on the low-end phones this targets and would make the dashboard stop working outright if the CDN were unreachable in the field — the visuals are identical either way, only the delivery differs. Field is `#040d0a`, never pure black (on OLED that makes emerald edges float in a void); panels are translucent glass with a **lit top inset edge**, because a drop shadow alone cannot sell "pane of glass" on a dark field, and three fixed radial pools sit behind everything — without them the glass has nothing to be translucent *for*. **Glow is rationed** to the live result, so it stays a signal. New in the results: SVG **gauges** for confidence and severity (driven by `--p` alone, so the sweep is pure CSS; tone on a separate attribute, so an amber read is visible without altering the figure), **badges**, breakdown **cards**, and a five-bar **wave** plus sweep over the preview while a request is in flight. **Two silent defects fixed on the way**: a CSS transition only fires when the value *changes*, so a second analysis landing on the same figure left a dead static ring — both gauges now clear first; and picking a second photo left the first verdict fully lit, inviting the farmer to read the old diagnosis as the new one — it is now dimmed, the glow withdrawn, and the status line says so. **New permanent check in `test_frontend_static`**: a class emitted by the markup or a module after its rule was deleted raises nothing and errors nowhere, the farmer just gets an unstyled block — it found **three on the first run** (`.account__label`, `.btn__text`, a `panel--work` modifier nothing styled). Bubble kinds became explicit data rather than a template literal, which makes them checkable *and* gives an unknown kind a safe fallback. `test_ui` asserts the new palette and that the field is dark by design rather than by media query; the no-pure-black check is scoped to `--bg`, since the print block deliberately sets black on white. Suite **586**. Live-verified end to end on the running server — **16/16**, including a real analysis (crop + disease + Arabic name + advisory) and a chat turn. |
| 31 | 2026-10-21 | **Dual-view landing + dashboard, Arabic-first.** One document now holds two stages instead of one: `#landing` is the public face (hero, three-step explainer, modules, why-section, and a static product mockup) and `#app` is the workspace — the sidebar / topbar / panel shell from the brief. The dashboard is **never unmounted, only hidden**, so the diagnostic module's wiring survives the transition and switching screens re-downloads nothing. **`<html lang="ar" dir="rtl">` is the default**, with Tajawal plus IBM Plex Sans Arabic; the new `i18n.js` flips direction and repaints every `[data-i18n]` node (textContent only), and RTL comes free across the stylesheet because every spacing and border rule uses logical properties. **Two contradictions in the brief were resolved rather than silently picked**: the theme toggle had been dropped in entry 30 but has now been asked for twice, so it is real — gated to `.landing` only, because a control that changes nothing reads as broken, while the workspace stays dark *by design* (the emerald ramp and the gauges are tuned to a dark field, and a second variant would double the maintained surface for a control that lives on the other screen); and "default when unauthenticated" cannot mean "gated", since the standing rule is that auth is alongside `/api/v1/analyze` and `/api/v1/chat`, never in front — so the dialog carries a **guest route**. **`history.js`** stores a compact whitelisted record per analysis in localStorage (never the image, never the full payload), which is what makes the metrics grid and the recent list show real counts instead of decorative zeros and keeps the History panel working with no network at all; the tradeoff is stated on the panel itself — per-browser, not per-account, and cleared by wiping site data. **The id contract caught a real defect the class check could not**: `app.js` had been written with selector-style `$("#landing")` against a `$()` that takes a bare id — **twelve calls that would each have returned `null` and thrown on the first click**, taking the shell down before the dashboard ever opened. `test_frontend_static` now asserts a *superset* of core modules rather than exact equality (the entry point legitimately grows new ones), and `test_ui` asserts the disclosure *structure* rather than the literal English summary, which is translated at runtime. 586 → **637 checks**. |

## 7. Launching

```bash
# one click, either launcher from the project root:
start_stable.bat               # prod: uvicorn directly, port 8000, no --reload
start.bat                      # dev:  python run.py, probes a free port

# explicitly, from backend\:
cd "C:\Users\user\Desktop\حصاد v1\backend"
python run.py                 # port 8000, opens browser
python run.py --no-open       # port 8000, no browser
python run.py --port 8010     # pick a specific port
python run.py --open-docs     # open /docs instead of /health

# manual equivalent (must run from backend\)
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Both launchers `pushd` into `backend\` themselves, so `main:app` and the
`agents` package resolve correctly regardless of where they were started from.

`run.py` prints the exact URLs on startup:

```
  Test UI     : http://127.0.0.1:8000/          (open this one)
  Health check: http://127.0.0.1:8000/health
  API docs    : http://127.0.0.1:8000/docs
  Analyze POST: http://127.0.0.1:8000/api/v1/analyze
```

Opening `http://127.0.0.1:8000` loads the dashboard (`frontend/index.html`,
with its stylesheet and six ES modules served at `/assets/*`): sign in from
the top-right trigger if you like — analysis works with or without an
account — then drop a leaf photo into the drop zone, press **Analyze**, and
read the results grid: crop, diagnosis in Latin and Arabic, severity,
confidence, the Tébessa advisory and the full supervisor JSON behind a
disclosure. The panel beside it is the agronomist: ask follow-up questions
about the plant that was just analysed. The pill in the header reports
whether the backend is reachable, so a stopped server is obvious at a glance
rather than surfacing as a failed upload.

If port 8000 is occupied it reports the occupying PID and auto-selects the
next free port, so the printed URL is always the one that actually works.
