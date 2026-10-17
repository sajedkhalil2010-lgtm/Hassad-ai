# حَصاد AI — Hassad AI v2/v3/v4 · Project Log & Architecture Record

> Living document: every architectural decision, agent file, and step of the
> agentic refactor is recorded here for presentation and audit.
> Newest entries at the bottom of the Progress Log.

---

## 1. What changed (architectural reset)

**v1 (retired):** raw, *linear* Gemini API calls — one procedural service
(`disease_detector.py`) fired single shots, and the router contained the
pipeline logic inline.

**v2 (current):** an autonomous **Multi-Agent System** under `backend/app/agents/`.
Supervision, key management, and failure recovery are first-class objects;
routers only handle HTTP + persistence.

**v3 — pure agentic autonomy (zero raw APIs):** *no module outside the
infrastructure layer touches a model SDK, and no agent fires a "prompt →
call → response" wrapper anymore.* Three upgrades on top of v2:

1. **Single cognition gateway** — `agents/cognition.py` (`CognitionEngine`)
   is the *only* file that imports `google.genai`. Agents ask the engine
   for *reasoning*, never for HTTP.
2. **Autonomous reasoning protocols** — DiagnosticAgent runs
   *hypothesize → self-verify → (bounded) refine*; AdvisorAgent grounds
   and validates its plan before shipping it; neither executes
   pre-written linear glue — each decides its next step from the state
   of its own reasoning.
3. **Supervisor coordination loop** — the SupervisorAgent no longer
   walks a straight line: it holds a blackboard (`RunTrace`), dispatches
   stages from a policy table, evaluates each agent's result, and decides
   the next dispatch (continue / ask-crop / retake / review / degrade).
   The whole coordination trace ships in `model_meta.trace` as
   auditable proof of autonomy.

```
Client (untouched RTL Arabic SPA)
   ▼
FastAPI router — HTTP + persistence only (no AI code at all)
   ▼
SupervisorAgent — coordination loop over a policy table + blackboard
   │   dispatch → evaluate → next dispatch (trace recorded)
   ├─► DiagnosticAgent  — hypothesize → self-verify → bounded refine
   ├─► AdvisorAgent     — grounded plan synthesis + chat reasoning
   ▼
CognitionEngine (agents/cognition.py)   ← ONLY module importing google-genai
   ▼
KeyPoolManager (agents/key_manager.py)  ← key state: rotation on 429,
                                           quota memo, zero SDK retries
```

## 1b. v4 — 100% local autonomy (current)

**v4 replaces the remote cognition engine entirely.** The diagnosis
pipeline now runs on **OpenCV + NumPy inside this process**: no
external APIs, no API keys, no rate limits, no 429s. `cognition.py`
and `key_manager.py` were **deleted**; `google-genai` was removed from
`requirements.txt` (added `opencv-python`). An old `.env` carrying key
templates is harmlessly ignored (`extra="ignore"`).

```
Client (untouched RTL Arabic SPA)
   ▼
FastAPI router — HTTP + persistence only (no AI code at all)
   ▼
SupervisorAgent — coordination loop over a policy table + blackboard
   │   dispatch → evaluate → next dispatch (trace recorded)
   ├─► DiagnosticAgent — segment → measure → match condition profiles
   │       ▼
   │   local_vision.py — OpenCV+NumPy leaf/lesion segmentation
   │       • leaf mask (HSV vegetation + hole-fill + largest component;
   │         canopy mode for green backgrounds/dense canopies)
   │       • lesion classes: necrotic / water_soaked / chlorotic /
   │         powdery / rust_ochre (morphology-cleaned, min-size filtered)
   │       • SECOND independent segmentation in CIE-Lab (Otsu on the
   │         distance from the healthy-green reference) → the reported
   │         segmentation_dice_score = Dice(maskA, maskB): honest
   │         inter-method agreement, not a fabricated accuracy
   │       • metrics: affected_area_percentage, lesion_count,
   │         largest_lesion_share, halo_index (early-blight signature),
   │         border_necrosis_share (K-deficiency proxy), severity
   │         (Early <5% ≤ Moderate ≤15% < Critical, settings-driven)
   ├─► AdvisorAgent — Tébessa KB: bilingual (real AR+EN) treatments,
   │       irrigation strategy (drip/deficit by crop group), seasonal
   │       calendar, disclaimer referencing DPA Tébessa / Chambre
   │       d'Agriculture; chat = pure local-KB retrieval (mode=local_kb)
   ▼
NO SDK, NO NETWORK — sub-second diagnosis, unlimited requests
```

Key properties kept from v3: the coordination loop, the `RunTrace`
blackboard (`model_meta.trace`), the threshold policy, the two-stage
crop flow, the wire contract, live-only honesty (measured pixels — no
mock verdicts), English-first formatting. New in v4: proper bilingual
KB rows (real Arabic, not mirrored English), a quantitative block in
the report, `POST /api/v1/analyze` (stateless quantitative report with
the Tébessa advisory + trace), crop vocab extended with the Tébessa
staples `olive`/`almond`, and chat answers in ~0.1 s (was 20–30 s via
the free-tier API).

> Sections 2–6 below describe the retired v2/v3 remote stack and are
> kept as history; v4 content lives in section 1b and the progress log.


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
| 3 | 2026-10-16 | `agents/base.py` — shared contracts: `AgentFailure` (quota/busy), `AgentContext`, `failure_kind` classifier, defensive coercers, JSON response parser, canonical `CROP_VOCAB`, `raw_mime_for`. |
| 4 | 2026-10-16 | `agents/key_manager.py` — `KeyPoolManager`: pool from pydantic-settings (`GEMINI_API_KEY_1..4` + base), 429-only rotation, quota memo with Google's own reset window (parsed `retryDelay`), zero SDK retries (`attempts=0`), instant whole-pool-spent failure (zero network), `generate_json` + `stream_text` primitives. |
| 5 | 2026-10-16 | `agents/diagnostic_agent.py` — `DiagnosticAgent`: schema-enforced vision payloads (crop candidates + disease/deficiency verdict, AR+EN names), bounded optimizer (≤256 KB passthrough; else ≤1024 px + quality steps → ~380 KB), English-first prompts with localized (Algeria) chemical guidance, one shot per stage. |
| 6 | 2026-10-16 | `agents/knowledge.py` — local RAG layer moved verbatim from the retired `rag_service` (Arabic-aware normalization/defixing/synonyms, scorer, citations, prompt assembly, DB load/context, seeder). No API calls. |
| 7 | 2026-10-16 | `agents/advisor_agent.py` — `AdvisorAgent`: `build_plan` (localized TreatmentPlan, KB-enriched sections, bilingual rows, disclaimer) + `stream_advice`/`answer_once` (chat via `KeyPoolManager.stream_text`, hard deadline, retrieval fallback so the chatbot never dead-ends). |
| 8 | 2026-10-16 | `agents/supervisor_agent.py` — `SupervisorAgent`: coordinates diagnostic → advisor, applies threshold policy (auto/candidates/confirm/review/retake), returns the **unified JSON report** (`pipeline: supervisor_v2`, contract keys preserved: `source:"model"`, `vision{}`, `thresholds{}`, `quality{}`). |
| 9 | 2026-10-16 | **Deletions**: `app/services/disease_detector.py` and `app/services/rag_service.py` (the raw linear API code) removed. `services/__init__.py`, `main.py` seed import and `config.py`/`ai_vision.py` docstrings repointed to the agents package. |
| 10 | 2026-10-16 | **Router rewires**: `crop_doctor.py` is now a thin HTTP/persistence layer over `SupervisorAgent` (English-first 429 `vision_quota` / 503 `vision_busy` details); `chatbot.py` streams through `_advisor = AdvisorAgent()`; `/health` exposes `pipeline: supervisor_v2`, `agents: [diagnostic, advisor, supervisor]`, `key_pool_size` (count only). |
| 11 | 2026-10-16 | **Validation** — offline agent suite **15/15** (fake clients, zero quota: pool rotation A→B, memo, zero-retry clients, diagnostic coercion, supervisor confirmed+plan report, selected-crop deep shot, chat streaming + fallback, whole-pent-pool instant quota failure with `new_calls=0`); all modules compile; server boots with v2 health block; **E2E 36/36** (auth, chat SSE live stream through the advisor, diagnose honest 429, quality gate, sync, agronomist); `git status frontend/` empty — frontend untouched. |
| 12 | 2026-10-16 | v2 committed and pushed to `main` (`.env` still ignored/untracked — verified `git ls-files`: only the clean `.env.example`). |
| 13 | 2026-10-17 | **v3 — pure agentic autonomy (zero raw APIs)** decision recorded above: one cognition gateway, autonomous reasoning protocols, supervisor coordination loop over a blackboard. |
| 14 | 2026-10-17 | `agents/key_manager.py` rewritten as **pure key-state policy** (pool, `available_positions()`, `mark_exhausted()`, quota memo — no SDK imports). New **`agents/cognition.py`** — the single module importing `google.genai`: `structured_json` (bounded, schema-enforced, 429 rotation) + `stream_text` (hard deadline) + `config()`/`image_part()` builders so not even configs are assembled outside the gateway; zero SDK retries enforced at client construction. |
| 15 | 2026-10-17 | `agents/base.py` gains **`RunTrace`** (shared blackboard: agent/step/action/outcome/ms) + `AgentContext.budget_s` (agent-wide reasoning budget). `config.py` gains `AGENT_SELF_CHECK` (default on) + `AGENT_DIAGNOSTIC_BUDGET_SECONDS` (90 s covering all diagnostic passes). |
| 16 | 2026-10-17 | **DiagnosticAgent → autonomous protocol**: hypothesis (one structured vision pass) → **self-verify** (text-only critique of its own verdict, inside a confidence band) → **bounded refine** (max ONE re-analysis carrying the critique's issues; critique/refine failures keep the original live verdict — robust recovery, trace-recorded). **AdvisorAgent** streams/plans through the gateway only (no SDK imports left anywhere else). |
| 17 | 2026-10-17 | **SupervisorAgent → coordination loop**: builds the stage plan from inputs (`auto` vs `user-crop`), dispatches stage by stage, evaluates each result against the threshold policy, decides continuation/early-exit, and ships the full trail as `model_meta.trace`. Pipeline id `supervisor_v3`; `/health` adds `autonomy: "agentic"`. Wire-contract parity preserved: `vision{crop,disease,model,latency_ms}`, `alternatives` only on stage-2, `disease: null` on awaiting reports, `source: "model"`. |
| 18 | 2026-10-17 | **Validation** — offline v3 suite **21/21** (zero-SDK invariant scan proving `google.genai` lives only in `cognition.py`; pool rotation A→429→B; memo; self-verify trace; bounded refine wins; critique-failure recovery; awaiting early-report with null disease; chat streaming + retrieval fallback; spent-pool instant quota failure with `new_calls=0`); **15/15 compile**; **E2E 42/42 LIVE** — quota reset overnight so both uploads got real, distinct Gemini verdicts (`Septoria Leaf Spot` 0.85 via the two-stage flow, `Early Blight` 0.85 on the alt upload), live SSE chat streams, 45 s budget honored (33.2 s), quality gate/sync/agronomist/auth intact; `git status frontend/` empty (frontend untouched); server log clean. |
| 19 | 2026-10-17 | v3 committed and pushed to `main` (`.env` exclusion re-verified before commit: only the clean `.env.example` staged/ignored). |
| 20 | 2026-10-17 | **v4 — 100% local autonomy** decision recorded (section 1b): the user required a fully local system (zero external API dependencies, zero rate limits) tailored to Tébessa. Gemini/cognition/key-pool removed; OpenCV + NumPy becomes the vision engine; the Tébessa KB becomes the advisory source. |
| 21 | 2026-10-17 | **`agents/local_vision.py`** — quantitative segmentation engine: HSV vegetation leaf mask (+ internal hole-fill so symptomatic non-green tissue stays inside the reference area, canopy mode), five lesion colour classes with morphological cleanup + min-size filtering, an independent CIE-Lab second segmentation → `segmentation_dice_score` = inter-method Dice consensus, metrics (affected %, lesion count, largest-lesion share, halo index, border-necrosis share, severity from `SEVERITY_*` settings), CPU work off the loop (`asyncio.to_thread`). Deterministic: same image → identical metrics (tested). |
| 22 | 2026-10-17 | **DiagnosticAgent rewritten (local)** — measures via `local_vision`, scores 8 Tébessa-relevant condition profiles (early/late blight, septoria, rust, powdery mildew, N/K deficiency, healthy) over the measured feature vector, crop-mismatch penalty (not veto), confidence = fit × Dice consensus × ambiguity margin; emits bilingual measured symptoms, a `quantitative` block, `engine: local_cv`; auto path ships regional-prior crop candidates (wheat/barley/olive @ 0.35 — the local engine honestly never guesses species above the ask-threshold). |
| 23 | 2026-10-17 | **AdvisorAgent rewritten (Tébessa KB)** — `build_plan` keyed by `condition_id`: treatments (cultural/organic/chemical with doses), severity boosters, irrigation row by crop group (cereal rain-fed / vegetable drip-morning / orchard RDI), real bilingual AR+EN rows, DPA Tébessa disclaimer, `source: local_kb:tebessa_v1`; `advisory_extras` (climate/soil/irrigation/seasonal calendar) for `/analyze`; chat = pure local-KB retrieval (`mode=local_kb`, no model call, no keys). Supervisor rewritten for the local engine (`pipeline: supervisor_v4`, `source: local_cv`, quantitative in report, honest "please confirm the crop" wording). `cognition.py` + `key_manager.py` **deleted**; `config.py` purged of all Gemini/HF settings (+ `CV_MAX_SIDE`, `SEVERITY_EARLY_MAX_PCT/MODERATE_MAX_PCT`); crop vocab gains `olive`/`almond`; `/health` reports `engine: local_cv, opencv, external_apis: 0`. |
| 24 | 2026-10-17 | **New `POST /api/v1/analyze`** — stateless quantitative report (metrics + diagnosis + Tébessa advisory + agent trace), authenticated, nothing persisted; mounted alongside the untouched `/crop-doctor/*` routes. `requirements.txt`: `-google-genai`, `+opencv-python`. |
| 25 | 2026-10-17 | **Validation** — offline v4 suite **47/47** (zero-SDK invariant scan: no `google.genai`/`KeyPoolManager`/`GEMINI_API_KEY` anywhere in `app/`; config purge checks; synthetic-image ground truth: lesions → 10.4% moderate, clean → 0.00% none, powdery/chlorotic fractions detected, determinism; diagnostic matching + honest confidences; Tébessa plan bilingual + disclaimer; supervisor contract incl. stage-1 honest awaiting); compile clean; **E2E 43/43 LIVE** — full flow now local: SSE chat `mode=local_kb` in 0.1 s (was 28 s), diagnose two-stage flow 1.3 s (Septoria Leaf Spot 0.69 on Wheat), `/analyze` quantitative report (10.6% / dice 0.694 / moderate) with Tébessa region block + 7-step trace, quality gate/sync/agronomist/auth intact; `git status frontend/` empty (frontend untouched); server log clean. |
| 26 | 2026-10-17 | v4 committed and pushed to `main` (`.env` exclusion re-verified; v1/v2/v3 remain recoverable at `9931f0e`/`12e2e8d`/`a632323`). |
