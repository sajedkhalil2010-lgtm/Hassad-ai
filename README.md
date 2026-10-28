# 🌾 حَصاد AI — Hassad AI

> **Next-Generation Agritech Pathology & Vision Intelligence Platform**
>
> Crop-leaf diagnosis and agronomic decision support, built for Algerian
> smallholder farmers — Arabic-first, offline-capable, and honest about what
> it does not know.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Framework-009688.svg)](https://fastapi.tiangolo.com/)
[![HTML5](https://img.shields.io/badge/UI-HTML5%20%2B%20Vanilla%20CSS-E34F26.svg)](frontend/)
[![Groq](https://img.shields.io/badge/AI-Groq-111827.svg)](https://console.groq.com/)
[![Tests](https://img.shields.io/badge/Tests-802%20passing-brightgreen.svg)](#%EF%B8%8F-testing)
[![RTL](https://img.shields.io/badge/Locale-Arabic%20%7C%20English-8b5cf6.svg)](#-adaptive-bilingual-ai-engine)

---

## 📋 Executive Summary

**Hassad AI (حَصاد AI)** is a bespoke agritech pathology platform that turns a
single photograph of a crop leaf into a structured, bilingual diagnostic report
and a localized treatment plan for the Tébessa high-plateau region of Algeria.

The platform is built around one principle that shapes every design decision:
**a wrong disease name is worse than no disease name.** A farmer who is told
their healthy crop is sick will spray at real cost, with no benefit and with
resistance pressure on the whole region. So the system is engineered to refuse
to guess — reporting `healthy` or `uncertain` rather than inventing a plausible
pathology, and enforcing that refusal in code rather than merely asking the
model to behave.

It is a **multi-agent pipeline**: a Vision Agent reads the leaf, an Agronomic
Advisor builds the treatment plan against a regional knowledge base, a Chat
Agent answers follow-up questions in context, and a Supervisor orchestrates the
stages, folds in retries, and emits one report.

**Offline-first by design.** Authentication is local SQLite with memory-hard
password hashing and no third-party dependency. When the AI provider is
unreachable or rate-limited, a built-in Tébessa knowledge base — fully bilingual
— delivers a real treatment plan instead of a dead end. No CDN framework, no
browser-side JIT: the dashboard is hand-rolled CSS that loads instantly on the
low-end phones this targets, and keeps working with no network at all.

---

## ✨ Core Features

### 🔬 Hyper-Accurate Vision Studio

A **strict two-step verification pipeline** that separates *Healthy* from
*Pathological* before any disease is ever named.

- **Step 1 — identify the species.** The agent names the plant
  (`crop` / `crop_ar`) and records `species_confidence`, so a leaf is never
  assumed to be a crop it is not.
- **Step 2 — the healthy gate.** Natural morphology is explicitly catalogued as
  *not* symptomatic: trichomes, silvery felty sheen, waxy bloom, variegation,
  and glare. An evenly fuzzy lavender or sage leaf is **not** powdery mildew.
- **Step 3 — name it, only if something is wrong.**

The verdict is a three-state `status`: `healthy`, `diseased`, or **`uncertain`**.

> **Enforced in code, not just in the prompt.** A `diseased` verdict is
> downgraded to `uncertain` when model confidence falls below `0.70`, when the
> species was named below `0.50`, when the payload is not a verifiable leaf, or
> when the label contradicts the status. Downgrades only ever run one way —
> the system never promotes a verdict the model did not stand behind — and the
> model's own guess is preserved as a differential rather than deleted.

An `uncertain` verdict never carries a curative or chemical treatment plan,
because applying a chemical is an assertion that a disease exists.

### 🌐 Adaptive Bilingual AI Engine

Language is resolved **deterministically and offline** (`agents/language.py`),
not left to the model's discretion — Arabic is detected by Unicode *script*
(a character counts only when it sits in an Arabic block **and** is a letter, so
the digits `٤٦` alone stay English), French by accented characters plus a
conservative stop-word set, English otherwise.

The resolved language is threaded through the entire pipeline, so the
diagnosis, the treatment plan, the regional context, the safety disclaimer and
every chat reply arrive in the **same** language the farmer wrote in. Both the
vision schema and the knowledge base carry paired English/Arabic fields, so a
farmer can flip script without re-running an analysis.

### 🎨 Bespoke Glassmorphic Dashboard

A hand-rolled **"Deep-Forest Obsidian"** dark theme — no CSS framework, no
CDN-hosted JavaScript. Built on design tokens, so the entire palette lives in
one place.

- `<html lang="ar" dir="rtl">` by default; RTL comes free across the
  stylesheet because spacing and borders use logical properties.
- SVG **confidence and severity gauges** driven by a single `--p` custom
  property, so the sweep is pure CSS.
- A structured results grid — verdict, symptoms, treatment steps, irrigation
  schedule — with the raw payloads one disclosure away.
- Text is written with `textContent` only, never `innerHTML`.
- `prefers-reduced-motion` honoured; responsive down to narrow viewports.

### 🔐 Offline-First Architecture

Secure local authentication and session management on **SQLite**, with
**zero third-party dependencies**:

- `hashlib.scrypt` for passwords — memory-hard, OWASP-listed, ships inside
  CPython. Hashes are self-describing (`scrypt$N$r$p$salt$hash`), so raising
  the cost factor later never signs anyone out.
- Only a **SHA-256 digest** of each session token is stored, so a database file
  that walks off cannot be replayed.
- **Enumeration defence**: a wrong password and an unknown email return the
  same message *and* the same timing — the store burns a real scrypt against a
  dummy hash when no row matches.

> **Auth sits alongside the analysis endpoints, never in front of them.**
> `/api/v1/analyze` and `/api/v1/chat` work without a token.

### 🛟 Local Agronomic Knowledge Base

A built-in, bilingual Tébessa knowledge base covering six condition profiles
(early blight, late blight, powdery mildew, rust, nitrogen deficiency, healthy)
with drip-irrigation schedules, cultural/organic/chemical treatment ladders,
prevention steps, and the agro-climatic context of the high plateau —
300–400 mm erratic rainfall, alkaline Terra Rossa soils at ~pH 8, dominant
crops of durum wheat, barley, olive, almond, fig, pepper and potato.

This is what guarantees a farmer still receives a real, localized,
correctly-translated treatment plan when the AI provider is rate-limited or
offline. Every plan carries a safety disclaimer pointing to the DPA of Tébessa.

### 🎬 Built-in Demo Recorder

The **Demo Recorder** panel films the real workspace — no offscreen canvas and
no redrawn frames — and hands back a video file. A simulated pointer glides to
the actual controls (the studio tab, the upload dropzone, Analyze, the language
toggle, the pricing panel) and presses them, so the take shows the product
doing the product's job.

- **Capture** — `getDisplayMedia` cropped to the workspace through Region
  Capture, muxed by `MediaRecorder` at 24 Mbps and 60 fps.
- **Container** — MP4/H.264 is tried first and the file is named
  `Hassad_AI_Demo.mp4`; where the browser cannot mux MP4 the export falls back
  to WebM and keeps the `.webm` extension rather than mislabelling the file.
- **One click** — start, stop, and the file downloads on its own; container,
  resolution and duration are reported under the button.

Two presses are cosmetic by design: the dropzone (a real click would open a
file dialog) and Analyze (a real click would wait on a live model). The upload
itself is real — a real JPEG is placed on the real input — and the report it
leads to is a sample, badged **SAMPLE** on the card, so a recording never claims
a live model reading.

---

## 🛠️ Technology Stack

| Layer | Technology |
| --- | --- |
| **Backend** | Python 3.10+, FastAPI, Pydantic v2, Uvicorn |
| **AI Provider** | Groq — vision `qwen/qwen3.8-27b`, advisor `openai/gpt-oss-120b`, chat `openai/gpt-oss-20b` |
| **Agents** | Vision · Advisor · Chat · Supervisor, composed as a retrying pipeline |
| **Imaging** | Pillow — EXIF-aware decode, downscale, JPEG normalisation |
| **Database** | SQLite (stdlib `sqlite3`), `hashlib.scrypt` password hashing |
| **Frontend** | HTML5, ES6 modules, hand-rolled CSS (glassmorphism, design tokens, logical properties for RTL) |
| **Typefaces** | Tajawal · IBM Plex Sans Arabic (progressive enhancement; the system stack is a complete fallback) |

> **No CSS framework and no build step.** The dashboard is vanilla CSS and
> vanilla ES6 modules — deliberate, because a CDN framework would cost render
> time on low-end devices and stop working entirely without a network.

---

## ⚙️ Quick Start for Evaluators

### 1. Requirements

- **Python 3.10+**
- A **Groq API key** — free at [console.groq.com/keys](https://console.groq.com/keys)
- No database server, no Node, no build tooling

### 2. Clone & install

```bash
git clone https://github.com/sajedkhalil2010-lgtm/Hassad-ai.git
cd Hassad-ai
pip install -r backend/requirements.txt
```

Dependencies: `fastapi`, `uvicorn`, `python-multipart`, `pydantic`,
`pydantic-settings`, `groq`, `Pillow`.

### 3. Configure the environment

```bash
copy backend\.env.example backend\.env      # Windows
cp backend/.env.example backend/.env        # macOS / Linux
```

Then edit `backend/.env` and set your key:

```ini
GROQ_API_KEY=gsk_your_real_key_here
```

> ⚠️ **`backend/.env` is git-ignored. Never commit it.** The server fails
> closed with a clear `auth` error if the key is missing or still the
> placeholder — it never sends an anonymous request. `.env.example` ships only
> the placeholder.

Optional overrides live in the same file: `VISION_MODEL`, `ADVISOR_MODEL`,
`CHAT_MODEL`, `ADMIN_EMAILS`, `SESSION_TTL_DAYS`, `SCRYPT_N`, `DATABASE_PATH`.

### 4. Run

**Option A — one-click launcher (recommended):**

```bash
start.bat            # dev: auto port, opens the browser
start_stable.bat     # prod: uvicorn directly on port 8000, no --reload
```

**Option B — run Uvicorn directly.** ⚠️ **Run it from `backend/`**, because the
application imports the `agents` package by top-level name:

```bash
cd backend
python -m uvicorn main:app --reload
```

> Running `uvicorn backend.main:app` from the project root fails with
> `ModuleNotFoundError: No module named 'agents'`. Both `.bat` launchers
> `pushd` into `backend/` for exactly this reason.

### 5. Open the UI

Visit **<http://127.0.0.1:8000>** — the Arabic-first dashboard loads directly,
no build step required. Drag a leaf photo onto the dropzone and press
**Analyse**.

---

## 🔌 API Reference

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/health` | System verification — models, provider, key state (masked). **Never leaks the key.** |
| `POST` | `/api/v1/analyze` | Upload a leaf image → full supervisor report. Accepts `language`. |
| `POST` | `/api/v1/chat` | One conversational turn, with optional diagnosis context. |
| `POST` | `/api/register` | Create an account → session token. `200` on success. |
| `POST` | `/api/login` | Verify credentials → session token. |
| `POST` | `/api/logout` | Revoke the calling token. |
| `GET` | `/api/me` | Who the calling token belongs to. |
| `GET` | `/api/admin/users` | Every registered account (admin only). |

All errors return a stable envelope:

```json
{ "ok": false, "error": { "kind": "quota", "message": "…", "message_en": "…", "retryable": true } }
```

### Example

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analyze \
  -F "file=@leaf.jpg" -F "language=ar"
```

---

## 📁 Project Structure

```
Hassad-ai/
├── backend/
│   ├── main.py                 FastAPI app, routes, CORS, static mount
│   ├── run.py                  Dev launcher + live model preflight
│   ├── requirements.txt
│   ├── .env.example            Template — copy to .env
│   ├── agents/
│   │   ├── diagnostic_agent.py Vision pass, two-step gate, confidence floor
│   │   ├── advisor_agent.py    Treatment plans + bilingual Tébessa KB
│   │   ├── chat_agent.py       Contextual agronomist chat
│   │   ├── supervisor_agent.py Orchestration, retries, report assembly
│   │   ├── language.py         Deterministic AR/EN/FR detection
│   │   ├── base.py             Error taxonomy, retry loops, image utils
│   │   └── config.py           Settings, model ids, .env resolution
│   ├── auth/                   SQLite store, scrypt hashing, session router
│   └── tests/                  802 checks — script-style, no pytest needed
├── frontend/
│   ├── index.html              Landing + dashboard (single document)
│   ├── css/styles.css          Design tokens, glassmorphism, RTL, motion
│   └── js/                     api · dom · i18n · diagnose · chat · history · auth · app
├── start.bat / start_stable.bat
└── PROJECT_LOG.md              Full engineering decision journal
```

---

## ✅ Testing

The suite is **802 checks, all green** — and runs with no extra dependency
beyond the requirements above (plain `python`, not pytest):

```bash
cd backend
python tests/test_offline.py         # 179 — config, helpers, agents, KB, API
python tests/test_sdk_shape.py       # 133 — Groq payload/response contracts
python tests/test_quota_retry.py     #  52 — 429 backoff & malformed-output retry
python tests/test_chat_agent.py      # 122 — chat routing & language
python tests/test_auth.py            #  93 — scrypt, sessions, enumeration defence
python tests/test_ui.py              # 116 — routes, assets, content types
python tests/test_frontend_static.py # 107 — markup ↔ stylesheet contract
```

Included is a permanent regression guard asserting that a `diseased` verdict
below the confidence floor is downgraded — the check that keeps the lavender
false-positive from returning.

---

## 🧯 Troubleshooting

| Symptom | Cause & fix |
| --- | --- |
| `ModuleNotFoundError: No module named 'agents'` | You ran Uvicorn from the project root. `cd backend` first, or use `start.bat`. |
| HTTP `500`, `kind: "auth"` | `GROQ_API_KEY` missing or still the placeholder in `backend/.env`. |
| HTTP `429` | Groq rate limit. Handled automatically with backoff; the message is returned in your language and is retryable. |
| HTTP `404` on the model | The configured model id is retired. Check the startup preflight output, which names the exact config field to change. |
| Port already in use | `start.bat --port 8010`, or stop the other process. |
| Arabic text renders as boxes | A console codepage issue, not the app. The server forces UTF-8; the browser needs no configuration. |

---

## 🗺️ Roadmap

- Live weather integration on top of the existing regional agronomic context
- Per-account history synced server-side (today it is per-browser, in `localStorage`)
- Optional session enforcement on the analysis endpoints

---

## 📄 License

All rights reserved. Part of the Hassad AI evaluation submission.

---

## 👥 Creators & Authors

**Hassad AI (حَصاد AI)** was designed, engineered and delivered by:

- **Khalil Sayada**
- **Mohamed Ayoub Hennane**

From the platform architecture — the multi-agent vision pipeline, the
deterministic bilingual routing, the confidence floor that prevents false
positives, and the offline-first SQLite auth — through to the Arabic-first
glassmorphic interface, the work in this repository is theirs.

<p align="center">
  <b>حَصاد AI</b> · Hassad AI<br/>
  <sub>Built for Algerian smallholder farmers · Tébessa</sub>
</p>
