# CONFIG DETECTIVE

> An agentic forensics tool that diagnoses "works on my machine" bugs by performing differential bisection over a multi-layer environment graph, with episodic memory in Supabase pgvector and empirical sandbox verification of every proposed fix. Ships as a CLI, Streamlit app, and Model Context Protocol server.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: in development](https://img.shields.io/badge/status-in--development-orange.svg)]()

---

## Base project (Module 1-3)

This project extends [**Game Glitch Investigator**](./legacy/README.md), my Module 1 number guessing game / debugging exercise. The original project was a Streamlit number-guessing game intentionally shipped with nine bugs across game logic, state management, and UI — fixed via systematic debugging, refactored into `logic_utils.py`, and validated by a 36-test pytest suite. The original code is preserved unchanged in [`legacy/`](./legacy).

The "investigator" DNA — systematic root-cause analysis backed by automated verification — is now generalized to a much harder target: diagnosing config-divergence bugs in real software environments.

## Why this project exists

Anyone who has shipped software has lived this nightmare: code runs perfectly on a developer laptop, breaks in CI, breaks differently in Docker, breaks again in production. The cause is rarely the code itself — it's a config delta hiding in lockfiles, env vars, OS packages, locale, timezone, OpenSSL versions, glibc versions, or Python minor versions.

**Standard AI debugging tools assume the code is the only variable.** They miss config bugs entirely. CONFIG DETECTIVE doesn't.

Given two environment snapshots (one where the code works, one where it fails) plus the failure trace, CONFIG DETECTIVE:

1. Builds a multi-layer **environment graph** (Python deps + OS packages + env vars + Dockerfile layers + locale + timezone + runtime versions) for each side
2. Computes the **delta** across all layers
3. Ranks suspects using **memory of past investigations** (Supabase pgvector) plus **multi-source RAG** over GitHub Issues, StackOverflow, and OSV.dev
4. Generates the top-3 hypothesized fixes
5. **Empirically verifies** each candidate by rebuilding the failing environment in a disposable Docker container with the candidate fix applied and re-running the original failure
6. Returns the verified fix with a confidence score, sandbox proof, and an applicable patch

Two operating modes: **propose** (default — shows the diff, waits for human "Apply") and **apply** (writes the patch directly, with `undo` always available).

## Architecture

![Architecture diagram](assets/architecture.png)

> The mermaid source for this diagram is at [`assets/architecture.mmd`](./assets/architecture.mmd). To regenerate the PNG, paste the mermaid source into the [Mermaid Live Editor](https://mermaid.live/) and export as PNG.

### Component overview

- **Snapshot layer** ([`config_detective/snapshot/`](./config_detective/snapshot)) — captures lockfiles, Dockerfile layers, env vars (PII-scrubbed), OS package list, runtime versions, locale, timezone into a deterministic JSON
- **Environment Graph RAG** ([`config_detective/graph/`](./config_detective/graph)) — multi-layer NetworkX graph with typed nodes (`PythonPackage`, `OSPackage`, `EnvVar`, `DockerfileLayer`, etc.) and cross-layer edges. The differ extracts deltas between two snapshots
- **Memory RAG** ([`config_detective/memory/`](./config_detective/memory)) — Supabase pgvector backed episodic memory of past `(failure → root cause → fix)` cases plus a semantic memory of compressed patterns
- **Multi-source RAG** ([`config_detective/retrieval/`](./config_detective/retrieval)) — fans out to GitHub Issues, StackExchange, OSV.dev, libraries.io, with local SQLite cache
- **Multi-agent orchestrator** ([`config_detective/agents/`](./config_detective/agents)) — LangGraph state machine: Triager → Prioritizer → Hypothesizer (k=3) → Sandbox Verifier → Critic → Reporter, with all intermediate states observable
- **Sandbox verifier** ([`config_detective/sandbox/`](./config_detective/sandbox)) — Docker SDK ephemeral containers with resource caps; Windows subprocess fallback
- **Patcher** ([`config_detective/patcher/`](./config_detective/patcher)) — unified-diff builder, applier, rollback (`undo`), interactive confirm prompt
- **Guardrails** ([`config_detective/guardrails/`](./config_detective/guardrails)) — PII scrubber, hallucination guard (claimed cause must exist in delta), iteration/time caps
- **MCP server** ([`config_detective/mcp_server/`](./config_detective/mcp_server)) — exposes the agent as MCP tools for use directly from Cursor / Claude Desktop

## Setup

> Status: setup instructions are accurate as of project scaffolding. Some commands assume Phase 1+ work has landed; sections marked with [in progress] will be filled in as features land.

### Prerequisites

- Python 3.11 or higher
- Git
- Docker Desktop with WSL2 backend (Windows) — only required for Phase 6 sandbox verifier; can be deferred during early phases

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/princymaheshwari/applied-ai-system.git
cd applied-ai-system

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -e ".[dev]"
# Or, if pyproject.toml extras are giving trouble:
pip install -r requirements-dev.txt
```

### 3. Configure environment variables

Copy the template and fill in your keys:

```bash
# Windows
copy .env.example .env
# macOS/Linux
cp .env.example .env
```

Open `.env` in your editor and replace each `your_..._here` placeholder with the real key:

- `GROQ_API_KEY` — from [console.groq.com](https://console.groq.com) (free)
- `GOOGLE_API_KEY` — from [aistudio.google.com](https://aistudio.google.com) (free, Gemini fallback)
- `HF_TOKEN` — from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) (free, Read scope)
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` — from your Supabase project's API settings; ensure pgvector extension is enabled

### 4. Run the Streamlit UI

```bash
# [in progress] - lands in Phase 9
python -m streamlit run config_detective/streamlit_app.py
```

### 5. Run the CLI

```bash
# [in progress] - lands in Phase 1
config-detective --help
config-detective snapshot --output snap.json
config-detective investigate --snap-a a.json --snap-b b.json --trace error.log
config-detective eval                    # runs the 15-case benchmark
config-detective mcp-serve               # starts the MCP server (stdio transport)
```

### 6. Run the test suite

```bash
pytest                                   # config_detective tests at top-level
pytest legacy/tests                      # the original Game Glitch Investigator tests, preserved
```

## Sample interactions

> [in progress] - finalized Loom-quality samples will be added once Phase 5 (orchestrator) and Phase 6 (sandbox) land.

Three planned demo cases (full inputs/outputs will be filled in as the system comes online):

1. **Locale bug** — works on Ubuntu (LANG=en_US.UTF-8), fails in Alpine container (LANG=C) with `UnicodeDecodeError`. Expected agent output: identifies LANG delta, sandbox-verifies fix `ENV LANG=C.UTF-8`.
2. **OpenSSL major version drift** — works on Debian Bookworm (libssl3), fails on Bullseye (libssl1.1) with `cryptography.exceptions.UnsupportedAlgorithm`. Expected output: identifies libssl delta, recommends compatible cryptography version pin.
3. **Timezone-dependent test** — pytest passes locally (UTC), fails in CI (Asia/Kolkata). Expected output: identifies TZ delta, recommends `ENV TZ=UTC` in CI.

## Design decisions

> [in progress] - filled in as decisions land. Initial decisions:

- **LangGraph for the agent loop** — chosen over hand-rolled state machines for built-in observability of intermediate states (rubric stretch +2)
- **Supabase pgvector** chosen over self-hosted Chroma/Qdrant — free tier, hosted, integrates with auth and Postgres in one product
- **Groq + Gemini as dual LLM providers** — Groq is fast and free but rate-limited; Gemini provides graceful fallback
- **Empirical sandbox verification before reporting** — the load-bearing differentiator vs generic LLM debuggers; every reported root cause is backed by a reproducible experiment
- **Propose-by-default, apply-by-flag** — agent has full auto-apply capability but defaults to human-in-the-loop for safety, with one-shot `undo` available

## Testing summary

> [in progress] - results from each phase will be summarized here. Target metrics: top-1 root-cause accuracy, top-3 accuracy, hallucination rate, mean confidence on the 15-case seeded benchmark.

## Reflection

> [in progress] - long-form reflection lives in [`model_card.md`](./model_card.md). This section will summarize highlights once the system is feature-complete.

## Demo

> [in progress] - Loom video walkthrough link will be added here showing 3 sample inputs end-to-end.

## License

MIT - see [`LICENSE`](./LICENSE).
