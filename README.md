# Mitra Central

A modular, multi-agent AI chatbot platform for QAD ERP. Pure Python backend with real-time WebSocket streaming, OpenAI for quality tasks, Groq for fast classification, and a modern ChatGPT-style UI.

## Agents

| Agent | Role | Data Source |
|---|---|---|
| **Mitra** | Natural-language to SQL on live QAD data | Progress DB via ODBC |
| **Apex** | Floating RAG assistant for user guides | Qdrant vector search |
| **Visual Intelligence** | KPI cards and chart generation from aggregated SQL | Progress DB via ODBC |
| **QAD-Zone** | 3 modes: Code Q&A, Doc generation, Modernisation analysis | Local .p/.i/.xml files |

Each agent is a fully self-contained module under `app/agents/`. Adding a new agent = drop a new folder and register it in `app/agents/registry.py`.

## Architecture

```
Browser (Alpine.js + Chart.js)
    │
    │ WebSocket (JSON frame protocol)
    │
FastAPI + Jinja2
    │
    ├── Groq API ────── fast free classification (intent routing, table ID)
    ├── OpenAI API ──── quality tasks (SQL gen, RAG answers, doc gen, embeddings)
    ├── Qdrant Cloud ── vector search for Apex (user guide chunks)
    ├── pyodbc ──────── QAD Progress DB (read-only SELECT guard)
    └── In-memory ───── TTLCache sessions (no Redis needed)
```

### WebSocket Frame Protocol

All agents stream responses via WebSocket using JSON frames:

| Frame type | Data |
|---|---|
| `token` | Streaming text chunk |
| `status` | Status indicator (e.g. "Searching...") |
| `sql` | Generated SQL query |
| `table` | `{columns, rows, row_count}` |
| `chart` | Chart specification `{type, title, x, y, kpis}` |
| `sources` | RAG source documents |
| `followup` | Suggested follow-up questions |
| `doc` | Document download `{url, title}` |
| `error` | Error message |
| `done` | Stream finished |

### Business Rules Engine (Mitra)

Per-domain rule files in `app/agents/mitra/rules/` define keyword patterns, SQL templates, business logic, and follow-up questions. Rules can have SQL only, logic only, both, or neither. The engine checks rules before falling back to LLM-generated SQL.

### QAD-Zone Modes

- **Query**: Loads all module source code and uses OpenAI to answer questions
- **Documentation**: Generates corporate Word documents (.docx) from custom code analysis
- **Modernisation**: Web search + AI analysis for QAD version migration planning

## Stack

- **Backend**: FastAPI (Python 3.11+)
- **Frontend**: Jinja2 + Tailwind CSS (CDN) + Alpine.js + Chart.js + Marked.js
- **LLM**: OpenAI (`gpt-4o`) for quality, Groq (`llama-3.3-70b-versatile`) for speed
- **Embeddings**: OpenAI `text-embedding-3-large` (3072 dims)
- **Vector DB**: Qdrant Cloud (collection `apex-normal`)
- **DB**: pyodbc -> Progress ODBC driver (read-only)
- **Sessions**: In-memory TTLCache via cachetools
- **Doc Gen**: python-docx for Word documents
- **Search**: duckduckgo-search for modernisation mode

## Quick Start

```bash
# Clone and setup
git clone https://github.com/tripathisharad/mitra-central.git
cd mitra-central
cp .env.example .env
# Edit .env with your API keys (OpenAI, Groq, Qdrant, ODBC)

# Install dependencies
pip install -r requirements.txt

# Run
python run.py
# or: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open http://localhost:8000 and log in with `admin` / `mfgpro`.

## Apex: Local Docs Indexing (qad_docs)

If you prefer to host the Apex documentation locally (instead of using n8n), this
project includes a helper to embed PDFs stored under `data/apex-pdf` into a new
Qdrant collection named `qad_docs`.

Steps:

1. Place your PDFs in `data/apex-pdf/`. Use filenames like `sales.pdf`, `purchase.pdf`, `manufacturing.pdf` — the filename (without extension) will be stored as the `module` metadata.
2. Ensure your `.env` has `OPENAI_API_KEY`, `QDRANT_URL`, and `QDRANT_API_KEY` set.
3. Install dependencies: `pip install -r requirements.txt` (this adds `PyPDF2` for PDF parsing).
4. Run the embedding script to create/replace the `qad_docs` collection and upload chunks:

```bash
python -m scripts.embed_qad_docs
```

5. (Optional) Create a payload index on the `module` field so the Apex agent can filter by module efficiently:

```bash
python -m scripts.create_qdrant_index
```

Notes:
- Text is split into ~1000-character chunks with 200-character overlap to preserve context.
- Each chunk payload contains `text`, `module`, and `filename` fields. The Apex agent filters using the `module` payload to restrict results (e.g., `sales`, `purchase`, `manufacturing`).
- The embedding model used is `text-embedding-3-large` (configured in `app/core/llm.py`).

Testing in the app:
- Start the app (`python run.py`) and open the Apex widget. Select the appropriate module (Sales/Purchasing/Manufacturing) and ask a question. The agent will query the `qad_docs` collection and return answers based on the uploaded PDFs.

## Project Structure

```
app/
├── main.py                    # FastAPI entry point
├── core/                      # config, llm client, session, websocket helpers
│   ├── config.py              # Pydantic Settings from .env
│   ├── llm.py                 # Unified OpenAI + Groq client
│   ├── session.py             # In-memory TTLCache sessions
│   ├── ws.py                  # WebSocket frame protocol
│   └── security.py            # Auth helpers
├── db/
│   └── odbc.py                # Async pyodbc wrapper (SELECT-only guard)
├── vector/
│   └── qdrant.py              # Qdrant search client
├── auth/
│   └── routes.py              # Login, roles, settings, logout
├── agents/
│   ├── registry.py            # Central agent list
│   ├── base.py                # AgentMeta dataclass
│   ├── mitra/                 # Text-to-SQL agent
│   │   ├── routes.py
│   │   ├── service.py         # WebSocket handler pipeline
│   │   ├── table_catalog.py   # 17 QAD table descriptions
│   │   ├── table_schemas.py   # Full CREATE TABLE DDL
│   │   └── rules/             # Business rules by domain
│   │       ├── inventory.py
│   │       ├── sales.py
│   │       ├── purchase.py
│   │       └── manufacturing.py
│   ├── apex/                  # Floating RAG widget
│   │   ├── routes.py
│   │   └── service.py
│   ├── visual_intelligence/   # Charts & KPIs
│   │   ├── routes.py
│   │   └── service.py
│   └── qad_zone/              # Code knowledge base
│       ├── routes.py
│       ├── service.py         # 3-mode handler
│       ├── programs.py        # Progress 4GL file loader
│       ├── doc_generator.py   # Word document generator
│       └── modernisation.py   # Migration analysis
├── templates/                 # Jinja2 HTML templates
│   ├── base.html
│   ├── settings.html
│   ├── auth/
│   ├── partials/              # sidebar, chat_shell, apex_widget
│   └── agents/                # mitra, visual, qadzone
└── static/
    ├── css/app.css
    └── js/app.js              # WebSocket streaming + Chart.js
data/
└── qad_programs/              # Place .p/.i/.xml files here by module
    ├── e-invoice/
    ├── doa/
    └── shared/
```

## Adding a New Agent

1. Create `app/agents/my_agent/` with `routes.py` and `service.py`
2. Register it in `app/agents/registry.py` with an `AgentMeta` entry
3. Add a template under `app/templates/agents/`
4. Include the router in `app/main.py`

The sidebar picks it up automatically from the registry.

## Configuration

All config is via environment variables (see `.env.example`):

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key for GPT-4o | (required) |
| `GROQ_API_KEY` | Groq API key for fast classification | (required) |
| `QDRANT_URL` | Qdrant Cloud cluster URL | (required for Apex) |
| `QDRANT_API_KEY` | Qdrant API key | (required for Apex) |
| `ODBC_DSN` | ODBC data source name for QAD Progress DB | `QAD_PROGRESS` |
| `QAD_DOMAIN` | QAD domain filter | `INDIA` |
| `DEFAULT_ROW_LIMIT` | Max rows returned by SQL queries | `50` |
