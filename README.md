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

> **Status:** Phase 1 — project foundation and architecture only. The full
> 9-agent pipeline and final video rendering are NOT implemented yet (see
> [Current implementation status](#current-implementation-status)).

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

## Current implementation status (Phase 1)

Implemented and tested:

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
- 89 unit tests (all passing).

Phase 1 server behaviour:

- **Script** — heuristic paragraph splitter (returns scene specs).
- **QA** — fully implemented structural checks (missing scenes/gaps, timeline
  overlaps, audio/video duration mismatch, invalid coordinates, missing
  assets) producing a structured `QAReport`.
- **Geo / Asset / Sound / Render** — explicit stubs that **refuse** to
  produce unverifiable output, with `TODO(Phase 2)` documentation.
- **Audio / Text / Transition** — contract + light logic; full features Phase 2.

---

## What is NOT implemented yet (Phase 2+)

- LLM-driven script understanding and entity detection (NER).
- Real audio analysis via FFmpeg/ffprobe (duration, silence, transcript).
- Real geocoding via configurable providers (Nominatim/Mapbox/MapTiler/Google)
  and GeoJSON/vector map rendering (MapLibre/Leaflet).
- Icon/asset discovery and generation from real sources.
- Typographic text measurement and overflow checks; full lower-third styling.
- Sound library integration (SFX, ambience, licensed music).
- **Final video rendering** via an FFmpeg filtergraph (16:9 MP4).
- The full 9-agent orchestration loop (phase-ordered pipeline with rollback).
- **Streamlit UI** (deliberately deferred).
- A real MCP network transport (stdio/SSE).

Each unimplemented piece is an explicit interface/stub with `TODO` markers —
nothing is faked as production-ready.

---

## License

MIT
