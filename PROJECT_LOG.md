# Hassad AI — Project Log & Architecture Record

> **حصاد AI** — multi-agent crop-leaf diagnosis and localized agronomic advisory.
> Target environment: `C:\Users\user\Desktop\حصاد v1` (Windows, local).
> Newest entries at the bottom of the Progress Log.

---

## 1. Environment & local path

| Item | Value |
|---|---|
| Project root | `C:\Users\user\Desktop\حصاد v1` |
| Python | 3.14.7 |
| Web framework | FastAPI 0.143.0 + Uvicorn 0.54.0 |
| Gemini SDK | **`google-genai` 2.29.0** (modern client API) |
| Settings | pydantic-settings 2.15.0 / pydantic 2.14.0 |
| Upload parsing | python-multipart 0.0.32, Pillow 12.3.0 |
| VCS | git, initialized at project root, `.env` excluded |

The folder was **empty** at initialization — no prior files were overwritten.

---

## 2. Single-key secure architecture

The whole system runs on **exactly one credential**: `GEMINI_API_KEY`.

```
.env                       <- GEMINI_API_KEY=<key>   (git-ignored, never committed)
  └─> agents/config.py     <- Settings (pydantic-settings: env > .env > default)
        └─> SecretStr      <- repr()/str() render as **********
              ├─> DiagnosticAgent  (one vision pass)
              └─> AdvisorAgent     (advisory generation)
                    └─> SupervisorAgent  (orchestration only — holds no key)
```

Security properties, all covered by the offline test suite:

* **One key, no pool.** There is deliberately no key-rotation or multi-key
  machinery — a single credential keeps the trust boundary obvious.
* **`SecretStr` containment.** The key lives in a `pydantic.SecretStr`, so
  `repr(settings)` and `str(settings.gemini_api_key)` render `**********`.
  It is only ever unwrapped inside `Settings.api_key`, at the two call sites.
* **Placeholder rejection.** The shipped `your_single_gemini_api_key_here`
  value is treated as *unconfigured*, not as a live credential
  (`Settings._reject_placeholder`). A deployment that forgot to set the key
  fails loudly and immediately rather than sending an anonymous request.
* **Never emitted in any payload.** `/health` returns only the key *state*
  (`"not configured"` or a 4+4 masked fragment). No error message, stage entry,
  traceback or HTTP body ever contains the key — asserted explicitly in tests.
* **`.gitignore` hardening.** `.env` and `.env.*` are ignored while
  `.env.example` (the clean template) is explicitly re-included with `!.env.example`.

---

## 3. System structure

```
حصاد v1/
├── .env                        # GEMINI_API_KEY (git-ignored)
├── .env.example                # clean committed template
├── .gitignore                  # secrets + python + uploads + logs
├── main.py                     # FastAPI app: CORS, /health, /api/v1/analyze
├── requirements.txt
├── PROJECT_LOG.md              # this file
├── agents/
│   ├── __init__.py             # public surface: the 3 agents + AgentError
│   ├── base.py                 # AgentError / AgentResult / coercion / JSON / image utils
│   ├── config.py               # Settings — the single-key source of truth
│   ├── diagnostic_agent.py     # vision pathology via Gemini
│   ├── advisor_agent.py        # Tébessa-localized advisory (Gemini + local KB)
│   └── supervisor_agent.py     # orchestration, error policy, JSON contract
└── tests/
    ├── test_offline.py         # 66 checks — no network, no key
    └── test_sdk_shape.py       # 63 checks — real google-genai APIError mapping
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
cd "C:\Users\user\Desktop\حصاد v1"

# 1. put your real key in .env
#    GEMINI_API_KEY=AIza...

# 2. (optional) create an isolated environment
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 3. validate offline — no key, no network, no quota
python -m tests.test_offline
python -m tests.test_sdk_shape

# 4. run
uvicorn main:app --reload --host 127.0.0.1 --port 8000
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

## 7. Launching

```bash
cd "C:\Users\user\Desktop\حصاد v1"

# one click (double-click start.bat), or:
python run.py                 # port 8000, opens browser
python run.py --no-open       # port 8000, no browser
python run.py --port 8010     # pick a specific port
python run.py --open-docs     # open /docs instead of /health

# manual equivalent
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

`run.py` prints the exact URLs on startup:

```
  Test UI     : http://127.0.0.1:8000/          (open this one)
  Health check: http://127.0.0.1:8000/health
  API docs    : http://127.0.0.1:8000/docs
  Analyze POST: http://127.0.0.1:8000/api/v1/analyze
```

Opening `http://127.0.0.1:8000` loads the lightweight single-file test UI
(`static/index.html`): pick a leaf image, press **Analyze**, and read the
diagnosis, the Tébessa advisory and the full supervisor JSON.

If port 8000 is occupied it reports the occupying PID and auto-selects the
next free port, so the printed URL is always the one that actually works.
