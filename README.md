# AI Video Agent

A production-oriented **AI Video Automation Agent** that turns a documentary
script plus a voice-over audio file into a professional 16:9
documentary-style video — with map animations, icons, text/captions,
transitions, sound design, music/audio mixing, timing synchronization, a
final rendered video, and automated quality checking.

The system is **MCP-based**: one orchestrator/client drives multiple
specialized MCP servers. It deliberately **does not** depend on Adobe After
Effects or GEOlayers 3 and favours open-source / local technologies
(FFmpeg, GeoJSON, SVG/HTML/Canvas, MapLibre/Leaflet).

> **Status:** Phase 4 — real video pipeline. The system accepts a documentary
> script + voice-over audio and produces a professional 16:9 documentary-style
> video (1920x1080 H.264 MP4) via FFmpeg. All 9 MCP servers, supervisor
> orchestration, DB persistence, QA validation, CLI, REST API, and Streamlit
> UI are functional. See [Current implementation status](#current-implementation-status).

---

## Architecture

```
                    AI Video Agent
                         |
              MCP Client / Orchestrator
                         |
   +-------+-------+-----+-----+-------+-------+-------+-------+
   |       |       |     |     |       |       |       |       |
 Script  Audio   Geo  Asset Text Trans- Sound   QA    Render
                                ition
```

- **MCP Client / Orchestrator** (`app/mcp/client`) — routes tool calls to the
  right specialized server and collects structured `AgentResult` objects.
- **Specialized MCP servers** (`app/mcp/servers/*`) — each implements a common
  `BaseMcpServer` interface (`handle(tool, arguments) -> Result`). They are
  modular and independently testable.
- **Supervisor Agent** (`app/agents/supervisor.py`) — coordinates agents,
  maintains `WorkflowState`, validates outputs, and decides retries. It never
  silently accepts invalid output and never fabricates missing data.
- **Guardrails** (`app/guardrails`) — central validation layer (required
  fields, file paths, media formats, durations, scene timing, timeline
  overlaps, geographic coordinates, missing assets, API configuration, agent
  output schemas).
- **Data contracts** (`app/schemas`) — strict Pydantic v2 models for every
  entity flowing through the pipeline.
- **Database** (`app/database`, `app/models`) — SQLAlchemy 2.0 ORM over SQLite
  (migratable to PostgreSQL).

### Planned specialized agents / servers

| # | Agent | MCP server | Responsibility |
|---|-------|------------|----------------|
| 1 | Script Understanding | `script` | Parse script → scenes; detect locations/dates/people/events/objects |
| 2 | Audio | `audio` | Analyze voice-over: duration, silence, timestamps, sync |
| 3 | Geo / Map | `geo` | Resolve locations via configurable providers; GeoJSON/vector data; animated maps. **Never invents coordinates.** |
| 4 | Icon / Asset | `assets` | Find/generate/select icons & visual assets; metadata |
| 5 | Text | `text` | Titles, labels, lower thirds, captions; safe positioning |
| 6 | Transition | `transitions` | Select scene transitions; avoid excessive transitions |
| 7 | Sound Design | `sound` | SFX, ambience, background music; sync to visuals |
| 8 | Video QA | `qa` | Inspect data + output; structured QA report |
| 9 | Supervisor | (orchestrator) | Coordinate, validate, retry |

---

## Folder structure

```
ai-video-agent/
├── app/
│   ├── main.py              # CLI entrypoint (serve / init-db / check)
│   ├── api.py               # FastAPI application factory
│   ├── config/              # env-driven Settings
│   ├── core/                # enums, exceptions, logging, Result type
│   ├── database/            # SQLAlchemy engine/session + Base
│   ├── models/              # ORM models (11 tables)
│   ├── schemas/             # Pydantic data contracts
│   ├── guardrails/          # validation rules + facade
│   ├── agents/              # BaseAgent + SupervisorAgent
│   ├── mcp/
│   │   ├── client/          # MCP orchestrator
│   │   └── servers/         # 9 specialized MCP servers
│   ├── services/            # FFmpeg interface (stub), ProjectService
│   └── utils/               # ids, async helpers
├── tests/                   # unit tests
├── data/  assets/  output/  logs/   # runtime directories
├── docs/  scripts/
├── .env.example
├── .gitignore
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## Setup

```bash
# 1. Clone
git clone https://github.com/Bakhti32102/ai-video-agent.git
cd ai-video-agent

# 2. Create a virtual environment (Python 3.11+)
python -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
#   edit .env and fill in real values (never commit .env)

# 5. Initialize the database
python -m app.main init-db

# 6. (Optional) verify config + DB + MCP registry
python -m app.main check
```

---

## Environment configuration

All configuration comes from environment variables (loaded from `.env` via
`pydantic-settings`). See `.env.example` for the full list. Highlights:

| Variable | Purpose |
|----------|---------|
| `APP_ENV` | `development` / `staging` / `production` |
| `DATABASE_URL` | SQLite (`sqlite:///./data/ai_video_agent.db`) or PostgreSQL URL |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `LLM_PROVIDER` / `LLM_MODEL` | LLM provider + model |
| `OPENAI_API_KEY` / `OPENROUTER_API_KEY` / `GOOGLE_API_KEY` | LLM keys |
| `MAP_PROVIDER` / `MAP_API_KEY` | geocoding/map provider |
| `FFMPEG_PATH` | path to the ffmpeg binary |

**No secrets are hard-coded.** API keys are read only from the environment and
are never logged in full (the `/health` endpoint reports only boolean
presence).

---

## Database initialization

```bash
python -m app.main init-db
```

Creates all tables on the SQLite database defined by `DATABASE_URL`. The
schema is designed to migrate to PostgreSQL by changing only the URL (no code
changes). Tables:

`projects`, `scenes`, `scene_assets`, `assets`, `locations`, `audio_files`,
`timeline_events`, `render_jobs`, `qa_reports`, `agent_runs`,
`workflow_state`.

Every table uses a string primary key, `created_at` / `updated_at`
timestamps, status fields, and appropriate foreign-key relationships.

---

## Testing

```bash
python -m pytest
```

Tests run against an isolated in-memory/temp SQLite database and a temporary
data directory (see `tests/conftest.py`), so they never touch real files.

Test modules:

- `tests/unit/test_config.py` — configuration validation
- `tests/unit/test_schemas.py` — Pydantic data contracts
- `tests/unit/test_guardrails.py` — guardrail rules + facade
- `tests/unit/test_database.py` — DB init + persistence
- `tests/unit/test_mcp.py` — MCP servers + client routing
- `tests/unit/test_supervisor.py` — supervisor retries + workflow state
- `tests/unit/test_api.py` — FastAPI health/MCP endpoints

---

## MCP architecture

The design follows the Model Context Protocol pattern: a single
**MCP client/orchestrator** holds a registry of specialized **MCP servers**.
Each server exposes named *tools* and returns structured results.

In Phase 1 the transport is in-process (servers are plain Python objects) so
the whole pipeline is unit-testable without network IO. The contract
(`BaseMcpServer.handle(tool, arguments) -> Result`) is transport-agnostic, so
a real MCP transport (stdio/SSE) can be layered on in Phase 2 without
changing callers.

Key guarantees enforced through the architecture:

- **Agents return structured `AgentResult` objects**, never arbitrary text.
- **The Geo server never fabricates coordinates** — in Phase 1 it refuses to
  geocode until a real provider is configured, and always requires a
  traceable `source` on any location it animates.
- **Guardrails wrap every agent output**; failures become structured errors,
  not silent repairs.

---

## Current implementation status (Phase 4)

### Phase 1 — foundation (complete)

- Project scaffolding, config (`pydantic-settings`), logging, exceptions,
  `Result` type.
- 11-table SQLAlchemy schema + DB init + `ProjectService` persistence.
- 14 Pydantic data contracts with strict validation.
- Guardrails module (12 rules + `Guardrails` facade) covering required fields,
  file paths, media formats, durations, scene timing, timeline overlaps,
  coordinates, locations, assets, missing assets, API config, agent output
  schemas, QA report consistency.
- MCP architecture: `BaseMcpServer` interface + 9 specialized servers +
  `McpClient` orchestrator.
- `BaseAgent` + `SupervisorAgent` with bounded retries and `WorkflowState`.
- FastAPI app with `/health` and `/mcp/tools`.

### Phase 2 — production-ready foundation (complete)

- **Error hierarchy & enums**: structured `AppError` subclasses
  (`FileSafetyError`, `GuardrailError`, etc.) with `code` and `details`;
  `ProvenanceType` and `WorkflowState` enums.
- **Workflow state machine** (`app/core/workflow.py`): deterministic
  transition validation; per-project `WorkflowStateMachine`.
- **Provenance tracking**: `Provenance` and `GeoProvenance` schemas.
- **Enhanced `AgentResult`**: auto-generated `run_id`, `confidence`, `provenance`.
- **File safety utilities** (`app/utils/paths.py`): path-traversal detection,
  directory-restriction enforcement.
- **Centralized guardrail pipeline** (`app/guardrails/pipeline.py`).
- **Database production-readiness**: FK enforcement, cascades, indexes,
  unique constraints, provenance columns.
- **Logging improvements**: `SecretRedactingFormatter`, `log_event`.

### Phase 3 — real MCP architecture (complete)

- **`BaseMcpServer` upgrade**: `ToolDefinition` with Pydantic input/output
  schemas, `health_check`, `execute_tool` with schema-validated I/O. No
  unvalidated dictionaries pass through the tool boundary.
- **MCP server registry** (`app/mcp/registry.py`): `register_server`,
  `unregister_server`, `get_server`, `list_servers`, `health_check_all`,
  `discover_tools`. Pre-loads all 9 canonical servers.
- **9 specialized MCP servers** with real tools:

| Server | Tools |
|--------|-------|
| Script | `analyze_script`, `split_into_scenes`, `extract_entities`, `extract_locations` |
| Audio | `inspect_audio`, `create_audio_timeline`, `detect_silence` |
| Geo | `geocode_location`, `batch_geocode`, `validate_coordinates`, `reverse_geocode` |
| Assets | `register_asset`, `get_asset`, `list_assets`, `validate_asset`, `find_asset` |
| Text | `create_text_overlay` |
| Transitions | `create_transition` |
| Sound | `create_sound_event`, `create_sound_design_plan`, `validate_sound_event` |
| Render | `create_render_job`, `validate_render_job`, `render_video`, `get_render_status` |
| QA | `validate_project`, `validate_timeline`, `validate_audio`, `validate_assets`, `validate_locations`, `validate_render`, `create_qa_report` |

- **Geo provider abstraction** (`app/services/geo.py`): `GeoProvider`
  interface + `GoogleGeoProvider`, `OpenStreetMapGeoProvider`,
  `NoneGeoProvider`. Provider selected via `GEO_PROVIDER` config. Every
  resolved location carries full provenance; ambiguous locations return
  `status=unresolved` (never fabricated).
- **FFmpeg renderer** (`app/services/ffmpeg.py`): `FFmpegRenderer` with safe
  subprocess execution (no `shell=True`), validated input/output paths,
  output restricted to project directory, shell-metachar rejection.
  `StubFFmpegService` for environments without ffmpeg.
- **MCP client upgrade**: server/tool discovery, input validation, timeout
  handling (`asyncio.wait_for`), guardrail validation on every result (never
  bypassed), structured logging.
- **Supervisor full orchestration** (`run_project`): drives the project
  through all 9 servers in order (script → audio → sync → geo → assets →
  text → transitions → sound → render → QA → decide COMPLETED/FAILED).
- **End-to-end mocked workflow**: tested with the Gadsden Purchase script;
  produces structured data for scenes, narration timing, locations, map
  requirements, text, transitions, sound design, render job, and QA report.

### Phase 4 — real video pipeline (complete)

- **Real video rendering**: `compose_video` in the Render MCP server uses
  real FFmpeg to produce 1920x1080 @ 30fps H.264 MP4 files. Text overlays are
  rendered to PNG via Pillow, then composited into the video.
- **Text overlay rendering**: `render_text` uses Pillow to render documentary
  captions/titles to transparent PNG overlays with configurable font, size,
  color, and position.
- **Map animation engine**: `MapAnimationEngine` generates SVG/PNG map frames
  with zoom/pan animations from GeoJSON + geocoded coordinates.
- **QA validation of real video**: the QA server probes rendered MP4s with
  ffprobe to verify codec (h264), resolution (1920x1080), duration, and file
  integrity. Flags CRITICAL when render is expected but missing.
- **DB persistence**: `ProjectService` now persists render jobs, QA reports,
  and workflow state to the database. The supervisor's `_finalize` method
  auto-creates project records and saves all outputs.
- **Guardrails for video output**: new `video_output` guardrail validates
  resolution, fps, codec, and duration meet documentary standards.
- **CLI `produce` command**: `python -m app.main produce --script "..." --duration 30`
  runs the full pipeline from the command line.
- **REST API `/produce` endpoint**: POST to `/produce` with script text and
  duration to trigger video production.
- **Streamlit UI** (`app/ui.py`): interactive web UI for entering scripts,
  configuring duration, producing videos, and viewing results including the
  video player and QA findings.
- **Real video e2e test**: `test_end_to_end_real_video_pipeline` produces a
  real MP4, verifies QA passes, and confirms DB persistence.

**380 tests** (all passing), covering the full architecture, all 9 servers,
real FFmpeg rendering, DB persistence, guardrails, CLI, API, and e2e pipeline.

---

## MCP architecture

```
SupervisorAgent
       ↓
   McpClient  ──→  McpServerRegistry
       ↓                    ↓
  tool call          9 specialized servers
                         ↓
              BaseMcpServer.execute_tool
                   (schema-validated I/O)
                         ↓
              GuardrailPipeline.validate_before_accept
```

Every tool call flows through:
1. Input validation against the tool's Pydantic input schema
2. Handler dispatch
3. Output validation against the tool's Pydantic output schema
4. Guardrail pipeline validation (never bypassed)
5. Structured `AgentResult` returned to the caller

### Provider adapters

Geo and rendering use provider abstractions so the architecture stays
provider-agnostic:

- **Geo**: `GEO_PROVIDER=none|osm|google`. OSM (Nominatim) is free; Google
  requires `GOOGLE_MAPS_API_KEY`.
- **Rendering**: `FFmpegRenderer` (real ffmpeg) or `StubFFmpegService` (when
  ffmpeg is not installed).

### Database interaction

The MCP servers operate on in-memory Pydantic contracts. Persistence is
handled by the existing SQLAlchemy models and `ProjectService`. The workflow
state machine tracks lifecycle; the supervisor can persist results through
`ProjectService` (Phase 4+ will wire full persistence into the orchestration).

### Security

- All file paths validated against path-traversal (`app/utils/paths.py`)
- FFmpeg subprocess uses argv lists, never `shell=True`
- Output paths restricted to approved project directories
- Shell metacharacters rejected in ffmpeg arguments
- Secrets redacted in logs (`SecretRedactingFormatter`)
- No API keys hard-coded; all via environment variables
- Every externally-obtained datum carries provenance

### Testing

```
python -m pytest           # 362 passed, 2 skipped
python -m compileall app tests
```

Tests cover: base architecture, registry, client (discovery/validation/
timeout/guardrails), all 9 servers, tool schemas, geo providers, FFmpeg
renderer safety, QA system, and the end-to-end mocked workflow. No paid APIs
are called during tests (geo uses a mock provider; render uses the stub).

---

## What is NOT implemented yet (Phase 5+)

- LLM-driven script understanding and NER (current: heuristic detection).
- Real map-tile rendering via MapLibre/Leaflet (current: SVG/PNG spec).
- Whisper/forced-alignment for audio transcription.
- Real icon/asset generation from external sources.
- A real MCP network transport (stdio/SSE).

**After Effects and GEOlayers 3 are NOT required** by the core architecture.
The pipeline uses FFmpeg for rendering. An optional After Effects/GEOlayers
adapter may be added in the future.

---

## Quick Start

### Prerequisites

- Python 3.12+
- FFmpeg (with libx264, libfreetype)
- Pillow (for text rendering)

### Install

```bash
pip install -r requirements.txt
```

### Initialize the database

```bash
python -m app.main init-db
```

### Produce a video (CLI)

```bash
python -m app.main produce \
  --script "The Gadsden Purchase of 1853 was a landmark treaty between the United States and Mexico." \
  --duration 30
```

### Produce a video (REST API)

```bash
# Start the server
python -m app.main serve

# POST to /produce
curl -X POST http://localhost:8000/produce \
  -H "Content-Type: application/json" \
  -d '{"script_text": "The Gadsden Purchase of 1853...", "total_duration_sec": 30}'
```

### Produce a video (Streamlit UI)

```bash
streamlit run app/ui.py
```

### Run tests

```bash
python -m pytest -q
```

---

## License

MIT
