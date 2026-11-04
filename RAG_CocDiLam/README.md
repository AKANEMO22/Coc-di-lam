# AI Mock Interview Platform Core Engine

Standalone AI runtime for realtime mock interviews. The backend handles STT/TTS; this service
prepares the interview (retrieval + profiling), runs the interviewer in realtime, orchestrates
evaluation and follow-up in the background, and produces an auditable report with a verdict.

## Architecture at a glance

- **Hot path (speaker):** one tool-free LLM call per turn with a cache-friendly prompt
  (persona + stage playbook + rolling summary + recent turns + directive). Deadlines, barge-in,
  backpressure (`busy`), exactly-once answers by `message_id`.
- **Control plane (orchestrator):** after each answer, delegates in parallel (evaluation wait,
  follow-up planner, claim checker, input guard), rebuilds criterion coverage, renders its
  notebook, and writes the directive for the next turn, gated by coverage and stage budget.
- **Assessment:** Redis Streams worker pool in the app lifecycle; per-criterion evaluator with
  rubric anchors and verified evidence quotes; barrier → deterministic decision
  (`PASS` / `FAIL` / `NEEDS_REVIEW`) → narrative with verified citations.
- **Retrieval:** the profiler and control-plane agents share the offline-capable RAG backend via
  `core/retrieval/`; the deterministic mock remains available by explicit configuration (see
  `core/retrieval/README.md`).
- **Harness:** hook bus, agent profiles with tool allowlists, typed workspace tools, auto-compaction,
  structured output with repair, skill registry (`skills/`).
- **Durability:** Redis-backed state (CAS), events (monotonic, TTL), idempotency, message ledger,
  leases; restart-safe.

Plans and design: `docs/superpowers/specs/`, `docs/superpowers/plans/`. Operations: `docs/runbook.md`.

## Setup

```bash
uv sync
```

## Tests

```bash
uv run pytest                                   # unit, integration, contract, simulation
RUN_PROVIDER_CONTRACT=1 uv run pytest tests/contract/test_provider_contract.py   # uses LLM_* from .env
RUN_LIVE_LLM=1 uv run python -m core.simulation.live --persona strong --model <model>  # live interview
RETRIEVAL_BACKEND=<name> uv run pytest tests/contract/test_retrieval_contract.py
uv run python tests/load/stress_test_100_sessions.py --concurrency 100 --workers 5
uv run python -m core.simulation.quality --golden golden.jsonl --decisions-dir data/workspaces
```

The default backend is `rag`. On first startup it indexes `data/interview_frameworks.json` into
`data/rag/index.db`; Compose stores that generated index in the `rag_index` volume. A separate
`data/it_seniority_rubrics.json` supplies generic Mid/Senior competency anchors. Intern, Fresher and
Junior questions come from the role corpus; Mid/Senior questions are generated from the candidate
profile, JD and generic rubric because the corpus has no reviewed question bank for those levels.
The rubric and generated questions are not company policy or human-validated interview material.
To use the deterministic fixture backend, set `RETRIEVAL_BACKEND=mock`.

## Interview preparation inputs and evidence

FE-test accepts CV and job-description files in PDF, DOCX or TXT format, with a 12 MiB limit per
file. Text PDFs, DOCX text and UTF-8 text are extracted; scanned PDFs are rejected because OCR is
not configured. Candidates can review and correct the extracted profile and interview plan before
starting; a correction refreshes retrieval and the stage plan. Company-specific web research requires `DOCKER_BRAVE_SEARCH_API_KEY`; without it, the
plan shows that company research is unavailable. Search results and fetched page excerpts remain
unreviewed leads unless explicitly verified; the interview must not present them as official policy.
The planned duration shapes the stage and turn budget; it is an estimate, not a wall-clock cutoff.

## Candidate intake with ID_Q

The ID_Q API accepts one candidate evidence source per intake: either a CV upload or a structured form. Both are stored as one canonical candidate profile with a server-generated `iq_<uuid>` ID and revision. Target company, role, seniority, and interview preferences are shared fields for either path.

Run the local API with loopback-only authentication enabled, then open `http://127.0.0.1:8000/idq/`:

```powershell
$env:RAG_ALLOW_LOCAL = "1"
uv run uvicorn rag.api:app --host 127.0.0.1 --port 8000
```

The browser UI clears candidate-form fields when switching to CV and clears the selected CV when switching to form. It supports revising the extracted profile and target, preparing a versioned knowledge pack, and previewing company/JD, role profile, rubric criteria, and candidate-visible questions. Scoring anchors and expected answers are returned only by the internal service pack endpoint.

The API routes are:

- `POST /api/v1/intakes/cv` — multipart field `file` plus JSON `metadata` containing `interview_target` and `preferences`.
- `POST /api/v1/intakes/form` — JSON containing `candidate_profile`, `interview_target`, and `preferences`.
- `GET /api/v1/intakes/{id_q}` and `PATCH /api/v1/intakes/{id_q}` — read or revise the intake; PATCH requires `expected_revision`.
- `POST /api/v1/intakes/{id_q}/prepare` — build a pack for `expected_revision`; optional `top_k` is 1–50.
- `GET /api/v1/intakes/{id_q}/company_knowledge`, `/role_profile`, `/rubric`, `/question_bank` — read the active pack; optional `pack_id` pins a snapshot and `top_k` bounds preview rows.
- `GET /api/v1/internal/intakes/{id_q}/knowledge_pack` — Core service pack, including evaluator-only material. Requires normal API authentication and `X-RAG-Core-Token` matching `RAG_CORE_TOKEN`; when `RAG_CORE_TOKEN` is unset the endpoint returns 503 (the API token is never accepted as the core token).
- `POST /api/v1/knowledge/imports` and `GET /api/v1/knowledge/coverage` — maintain/view the shared company and JD catalog and role-level coverage. Imports (and `POST /rag/index`) additionally require an admin UI session or `X-RAG-Admin-Token` matching `RAG_KNOWLEDGE_ADMIN_TOKEN`; unset means closed to token callers.
- `DELETE /api/v1/intakes/{id_q}` — remove the intake, revisions, jobs, packs, and original CV file.
- Core `POST /api/v1/sessions/from-idq` — create an interview session from an already prepared ID_Q pack, pinning its revision and `pack_id` in the session workspace.

CV uploads support text-layer PDF, DOCX, UTF-8 TXT, and Markdown up to 12 MiB. Scanned PDFs need OCR, which is not configured. The service rejects extracted text over 180,000 characters rather than silently truncating it. The original file is kept in a private runtime directory; the public API omits its server path. The Compose overlay stores the intake database and CV files under the `rag_index` volume.

The curated company/JD catalog starts empty. Supply a company and its JD through `POST /api/v1/knowledge/imports` or the JSON seeds `data/knowledge/companies.json` and `data/knowledge/job_descriptions.json`. Every JD import requires a company, a supported `role_id` and `target_seniority`, and a `source_url` or `source_ref`. When several JDs match, select a `jd_id` in the intake target and prepare a new revision. Generic framework context is never presented as a company JD.

When the curated catalog has no matching JD, ID_Q can use a bounded Browserbase Search + Fetch or Brave Search fallback. Set `BROWSERBASE_API_KEY` (preferred) or `DOCKER_BRAVE_SEARCH_API_KEY` for Compose; direct runs use `BROWSERBASE_API_KEY` or `BRAVE_SEARCH_API_KEY`. `RAG_WEB_SEARCH_PROVIDER` accepts `auto` (prefers Browserbase when configured), `browserbase`, or `brave`. `POST /api/v1/intakes/{id_q}/prepare` accepts `web_search_mode` as `auto` (default), `off`, or `refresh`. Only public target fields (company, role, seniority, and optional location) are searched; CV and candidate fields are never sent to the provider. A search snippet is exposed only as a candidate, while automatic JD selection requires a fetched page that matches the company and role. Web-derived fields include source links, confidence, and `verification_status: unreviewed`; curated fields always take precedence. Results are cached for `RAG_WEB_RESEARCH_CACHE_TTL_SECONDS` (24 hours by default).

The existing corpus supplies role-specific Intern/Fresher/Junior questions where covered and generic cross-role Mid/Senior rubrics. It does not provide a reviewed Mid/Senior question bank or company JD catalog. Results expose source status and limitations; the bundled role/question corpus has not been represented as a verified company benchmark. This intake storage uses the configured API principal for ownership; it is intended for local development until an application supplies real per-user identity and authorization.

In the full `dpl` stack (root `compose.yaml`), only this service is started from this repo, through `compose.service.yaml` (`rag-idq` on port 8200). The interview gateway (`backend/`) posts the candidate's CV or form to `/api/v1/intakes/cv|form`, prepares the pack, and creates the `core_agent` session from `id_q` + `pack_id`; `core_agent` reads the internal knowledge pack and no longer runs retrieval itself. The tokens are read from `core_agent/.env` there.

To run the ID_Q service alongside Core in Docker, add separate random `RAG_IDQ_API_TOKEN` and `RAG_CORE_TOKEN` values to `.env` and start the overlay. The UI uses the first token; only Core receives the second token for the internal evaluator-pack endpoint:

```powershell
docker compose -f compose.yaml -f compose.rag.yaml up --build -d
```

The same port serves both frontends: `http://127.0.0.1:8200/` is the RAG administration UI (knowledge bases, document uploads, retrieval and index versions), and `/idq/` is the current candidate intake UI with web fallback. Sign in at `/` with `RAG_ADMIN_PASSWORD` (or `DPL_RAG_ADMIN_PASSWORD`; local default `local-rag-admin-change-me`). The admin session also authenticates ID_Q, so a separate API token is optional in that browser session. Backend clients continue to use `RAG_IDQ_API_TOKEN`.

The gateway runs the restored standalone administration service from `standalone_rag/` and the current ID_Q service from `rag/`. Administration reuses the original `dpl-pvan_rag_service_data` volume; ID_Q retains the `rag_index` volume. Existing administration upload paths are adjusted to the remounted volume when the files exist. To apply gateway changes to the running service, run `docker compose -f compose.yaml -f compose.rag.yaml up -d --build --no-deps rag-idq`.

Both interfaces share the responsive workspace navigation and design tokens in `ui-shared/`, served at `/assets/ui/`. The administration UI has document filtering, drag-and-drop uploads, knowledge-base dialogs, and separate search/index views. ID_Q provides a four-step preparation flow, editable profile fields, source-aware result cards, and optional advanced JSON views. Changes to these frontend files require rebuilding `rag-idq` in Docker.

ID_Q applies explicit field contracts when preparing knowledge, reading saved collections, and exporting evaluator packs. Role profiles contain role identity, level, expectations, and interview stages; rubrics contain competency criteria, weights, and scoring anchors; question banks contain questions and their role/level/competency metadata. Unknown fields and known application/navigation boilerplate are discarded. Claims must be strings within field limits; malformed objects and oversized text are omitted rather than converted or truncated. Lists are deduplicated and bounded. Rubric criteria and questions require source references. Missing criteria, weights, or score anchors leave a rubric incomplete for scoring. Public responses exclude anchors and model answers; evaluator exports retain only the permitted scoring and answer fields. These rules constrain content; source review status still determines whether evidence has been verified.

Open the ID_Q page at `http://127.0.0.1:8200/idq/` and enter the `RAG_IDQ_API_TOKEN`. After preparing and reviewing the pack, **Tạo buổi phỏng vấn** asks local Core for its short-lived development token and creates a session; remote Core requires a service token in the form. The page links to Core Studio when creation succeeds. Core also accepts `{ "id_q": "iq_...", "pack_id": "pack_..." }` at `POST /api/v1/sessions/from-idq` with a service JWT and optional `Idempotency-Key`. The overlay allows browser requests only from the two loopback ID_Q page origins. The Core session consumes the exact prepared snapshot, uses the canonical candidate profile, and skips a second retrieval of the role, rubric, and questions. Core scoring uses a 1–4 scale: a 1–5 source rubric keeps anchors 1–4 and maps its pass threshold proportionally; the source scale and mapping are recorded in the session artifact. Core still creates the interview spec and stage plan with its configured language model.

## Running

```bash
# Development (no API key: scripted provider + in-process fake Redis when REDIS_URL is empty)
uv run uvicorn api.main:build_default_app --factory --reload

# Production
APP_ENV=production uv run uvicorn api.main:build_default_app --factory --host 0.0.0.0 --port 8000 --no-access-log
```

## Core Studio: FE-test and FE-inspect

Two separate frontends share the core API and persisted workspaces:

- `FE-test/` at `/test/` creates real interview sessions from your input, sends your answers,
  resumes saved conversations and links to the matching inspection view.
- `FE-inspect/` at `/inspect/` is read-only: recorded agent call tree/flow, waterfall, input,
  output, usage, errors, events, artifacts and observed agent instance lifetimes.

```bash
docker compose up --build -d
# Test:    http://127.0.0.1:8001/test/
# Inspect: http://127.0.0.1:8001/inspect/
# Redis UI: http://127.0.0.1:5540/
```

Redis Insight is preconfigured with the `dpl-pvan` connection to `redis:6379`, database 0.
On first use, complete Redis Insight's welcome/terms screen to enable connection discovery.
It binds to loopback only and persists its settings in the `redisinsight_data` volume.
To start only the Redis UI and its Redis dependency, run `docker compose up -d redisinsight`.

The local stack also builds MinIO Community from its pinned upstream source release and archives
completed session workspaces with checksums. Open the MinIO object browser at
`http://127.0.0.1:9001/`; the S3 endpoint is `http://127.0.0.1:9000/`. For local use, the
Compose defaults work as-is. Set `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` in `.env` to use
your own credentials. These are local development credentials; production object storage is
blocked until a maintained MinIO distribution and durable user ownership/authentication are
configured. The MinIO Community upstream was archived in April 2026, so its local source build
does not receive upstream security fixes.

Archived objects and the SQLite archive catalog have separate named volumes (`minio_data` and
`artifact_catalog`). A named volume is not a backup. Back up and restore both the MinIO data and
catalog together before relying on the archive for long-term retention. Session deletion removes
all stored object versions and records a durable tombstone before local/Redis cleanup.

Compose binds to loopback and enables short-lived development tokens. Both screens connect
automatically to the same origin. Advanced connection settings allow a different core with a
service token. Production requires existing service auth; local token issuing is rejected.

The interface defaults to Vietnamese and supports English through the language selector.
This browser preference is independent of **interview language**: choose `vi` or `en` when
creating a session. REST clients send `interview_language` in `POST /api/v1/sessions`;
omitting it defaults to `vi`. The selection is saved in session metadata and
`inputs/form_input.json`, then used as language guidance for profiling, speaker questions,
follow-ups and orchestration. Source documents, transcript text, identifiers and stored
artifacts remain as recorded; switching the interface does not translate historical runs.
Model compliance with the prompt remains subject to the configured provider.

Desktop views stay inside the viewport, with independent scrolling for the run list,
trace/conversation and detail panels. The waterfall can zoom its time scale and scroll
horizontally while keeping span names visible. Artifact and structured-data previews
support readable JSON/Markdown and source views; raw HTML is displayed as text and unsafe
link protocols are blocked. The Markdown viewer supports common document syntax and is
not a full CommonMark implementation.

Local development tokens expire after one hour. Both frontends refresh them before expiry
and retry an unauthorized HTTP request once when local issuing is enabled. Production
service tokens must be supplied by your authentication system.

Set `DOCKER_LLM_API_KEY`, `DOCKER_LLM_API_URL` and `DOCKER_LLM_MODEL` in `.env` for your real
provider (see `.env.docker.example`). Without a provider, the screens remain accessible and
show execution unavailable; no scripted LLM fallback, synthetic candidate or demo run is
used. Core readiness returns 503 until a real provider is configured. Compose uses the real
local `rag` adapter; fabricated recon tools are disabled in strict Studio mode.

`INSPECTOR_ENABLED=true`, `TEST_UI_ENABLED=true`, `INSPECTOR_LOCAL_AUTH=true` enable the
screens outside Compose. `STUDIO_REQUIRE_REAL_PROVIDER=true` is the default for Studio.

SQLite at `/data/workspaces/.inspect/traces.sqlite3` uses WAL and `synchronous=FULL` in the
`interview_workspaces` volume. Each span start, task scheduling, completion, state snapshot
and event is committed. Redis still owns the core's live CAS/queue protocol, with AOF and
its own volume; the inspector archive remains readable after Redis expiry or app restart.
Do not use `docker compose down -v` if you want to retain history. Inspector reads its own
API; it never acquires the candidate WebSocket lease. Parent IDs are propagated through
Redis assessment jobs, so worker calls link to their actual enqueue span.

Agent instance observations distinguish application-scoped reused agents, session-scoped
speaker objects, and per-delegation tasks. Missing completion after process restart is
marked `abandoned` with unknown end time. Old runs have no reconstructed/fabricated graph:
surviving events/artifacts can be viewed, with missing trace coverage explicitly indicated.
The current core/SQLite integration enforces a single API process per workspace volume
with an OS writer lock, as configured by Compose. A second live writer fails at startup;
restart recovery cannot incorrectly abandon the first process's running agents.

Capture includes actual prompts, model output, tool arguments/results and usage under service
authentication. Credential fields and recognized credential strings are redacted; this is
pattern filtering, so arbitrary secrets split across streamed chunks may evade it.
Reasoning fields are omitted. Large previews are bounded
with explicit truncation markers; the workspace preserves the core's original artifacts.

Backend extensions can use the same persistence and parent context:

```python
recorder = app.state.inspect_trace_recorder

@recorder.traceable(name="Backend preparation", kind="operation")
async def prepare(session_id: str, request_data: dict):
    return await existing_backend_operation(request_data)
```

GET `/api/v1/inspect/runs/{session_id}/trace` reads a coherent graph snapshot and revision.
GET `/api/v1/inspect/runs/{session_id}/events?after_sequence=0&limit=1000` pages durable events.
GET `/api/v1/inspect/runs/{session_id}/export` exports the inspected snapshot. All require
a service token. Automatic transcript/scripted simulation endpoints have been removed.

## Protocol summary

REST (`/api/v1/sessions`): create (202, `Idempotency-Key`), status, `POST /{id}/start`, report,
cancel, delete, `POST /{id}/assessments/requeue`, `POST /{id}/review`.

WebSocket (`/ws/sessions/{id}/stream`), inbound: `start_interview`, `candidate_answer`
(`message_id` required), `cancel_generation`, `replay`, `ack`, `ping`. Outbound events include
`interview_started`, `response_token`, `turn_complete`, `stage_transition`, `generation_cancelled`,
`busy`, `resync_required`, `directive_ready`, `assessment_update`, `decision_ready`, `report_ready`.
