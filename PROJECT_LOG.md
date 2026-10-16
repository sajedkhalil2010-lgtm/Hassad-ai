# حَصاد AI — Hassad AI v2 · Project Log & Architecture Record

> Living document: every architectural decision, agent file, and step of the
> v2 multi-agent refactor is recorded here for presentation and audit.
> Newest entries at the bottom of the Progress Log.

---

## 1. What changed in v2 (architectural reset)

**v1 (retired):** raw, *linear* Gemini API calls — one procedural service
(`disease_detector.py`) fired single shots, and the router contained the
pipeline logic inline.

**v2 (current):** an autonomous **Multi-Agent System** under `backend/app/agents/`.
Supervision, key management, and failure recovery are first-class objects;
routers only handle HTTP + persistence.

```
Client (untouched RTL Arabic SPA)
   │  multipart /crop-doctor/diagnose  (identical wire contract)
   ▼
FastAPI router (app/api/v1/crop_doctor.py)  — auth, validation, DB only
   ▼
SupervisorAgent (agents/supervisor_agent.py)  — orchestration & fallbacks
   ├─► DiagnosticAgent (agents/diagnostic_agent.py)
   │      Gemini vision · schema-enforced JSON · image optimiser
   ├─► AdvisorAgent (agents/advisor_agent.py)
   │      localized treatment plans · KB grounding · chat streaming
   └─► KeyPoolManager (agents/key_manager.py)
          multi-key rotation on HTTP 429 · quota memo · zero SDK retries
```

## 2. The three agents

| Agent | File | Responsibility |
|---|---|---|
| **DiagnosticAgent** | `backend/app/agents/diagnostic_agent.py` | Deep visual pathology on uploaded leaf/crop images via `gemini-3.8-flash`. Rigorous `response_schema` JSON (disease AR+EN name, calibrated confidence, symptoms, actions, treatments, alternatives). Detects disease **and** nutrient deficiencies. One bounded shot per stage, optimized ≤380 KB payload. |
| **AdvisorAgent** | `backend/app/agents/advisor_agent.py` | Turns the diagnostic verdict into a tailored, **localized** (Algeria/MENA agronomy) treatment plan: immediate actions, cultural + organic/chemical recommendations, monitoring, prevention. Also streams the chat assistant answers (RAG-grounded) for `/chatbot/*`. |
| **SupervisorAgent** | `backend/app/agents/supervisor_agent.py` | Coordinates the sequence (crop identification → deep diagnosis → advisory), applies thresholds/decision policy, handles fallbacks (crop-selection ask, retake, review queue), and returns one **unified, clean JSON report** to the router. |

## 3. Smart key management (`backend/app/agents/key_manager.py`)

* **Key pool** — reads `GEMINI_API_KEY_1..4` then base `GEMINI_API_KEY`
  through pydantic-settings only (env-var > `.env` > default). Values are
  never logged, never repr'd, never leave the process.
* **Automatic rotation** — an HTTP 429 / `RESOURCE_EXHAUSTED` on one key
  fails over to the next key in the pool (quota errors only; other errors
  are surfaced immediately, never retried blindly).
* **Quota memo** — a key that answered 429 is skipped until Google's own
  reset window (parsed from the payload, e.g. `retryDelay≈47840s ≈ 13h`);
  when every key is spent the pipeline fails **instantly with zero network
  calls**.
* **Zero hidden retries** — every google-genai client is built with
  `HttpRetryOptions(attempts=0)` (the SDK default retries 429/5XX up to
  4× with backoff — disabled).
* **Never exposed** — `/health` reports only the key **count**.

## 4. Core standards

* **English-first response formatting** — all JSON keys, statuses, enum
  values, and free-text `message` fields are English by default; the
  bilingual schema fields (`name_ar`/`name_en`) are preserved; the chat
  assistant honours the client's `lang` parameter (Arabic stays first-class
  there). The frontend UI itself is untouched and stays fully RTL Arabic.
* **Robust error recovery** — every agent raises `AgentFailure(kind=quota|busy)`;
  the router maps it to structured HTTP 429 (`vision_quota`) or 503
  (`vision_busy`) with an honest message. **No mock verdicts exist.**
* **Security** — `.env` is listed in `.gitignore` (root + backend) and is
  never committed/pushed (verified: `git ls-files` shows only the clean
  `backend/.env.example` template). Farmer photos are never committed
  (`uploads/`, `*.jpg…` ignored). Keys reach Gemini via pydantic-settings only.
* **Performance** — image payloads optimized ≤380 KB before transmission,
  encoding off the event loop (`asyncio.to_thread`), one bounded shot per
  stage (45 s), zero-retry clients, instant failures when quota is spent.

## 5. Frontend preservation (hard constraint)

The frontend (`frontend/`) is **completely untouched** in v2 — same design,
templates, and assets. The wire contract it depends on is preserved exactly:

* `POST /api/v1/crop-doctor/diagnose` (multipart: `file`, `selected_crop`,
  `client_id`, `diagnosis_id`, …) → `DiagnosisResponse` (status/stage/
  candidates/treatment_plan/source/image_quality/message…).
* `GET/DELETE /crop-doctor/diagnoses…`, `/chatbot/*` (SSE events
  `chunk`/`citations`/`done`/`error`), `/auth/*`, `/sync/*`,
  `/agronomist/*`, `/health` — all unchanged.

## 6. Retained by design (contain no API calls)

`app/services/ai_vision.py` (local bilingual KB + image quality gate),
`app/models/*`, `app/schemas/*`, auth/sync/agronomist routers, DB layer —
these are the persistence/workflow layer the untouched frontend depends
on. **Every module that made raw linear Gemini calls was deleted**
(`services/disease_detector.py`, `services/rag_service.py`) and replaced
by the agent framework.

---

## Progress log

| # | Date | Step |
|---|---|---|
| 1 | 2026-10-16 | v2 reset initiated. Full recon of the frozen frontend wire contract (`DiagnosisResponse`, SSE chat events, router imports). Old code is recoverable from git commit `9931f0e` on `main`. |
| 2 | 2026-10-16 | `PROJECT_LOG.md` created at the workspace root and committed — architecture, agent roles, key-pool design, standards. |
| 3 | 2026-10-16 | *(in progress)* `agents/` package: `base.py` → `key_manager.py` → `diagnostic_agent.py` → `knowledge.py` → `advisor_agent.py` → `supervisor_agent.py`. |
