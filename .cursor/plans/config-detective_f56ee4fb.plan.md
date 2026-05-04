---
title: CONFIG DETECTIVE - Works-On-My-Machine Forensics Agent
todos:
  - id: phase0_handoff
    content: "Repo handoff: clone Game Glitch Investigator into applied-ai-system-final, retarget remote, push, scaffold directory structure, set up Supabase project + pgvector, collect free API keys (Groq, HF, GitHub, Supabase)"
    status: pending
  - id: phase1_snapshot
    content: "Snapshot module: capture lockfiles, Dockerfile layers, env vars (PII-scrubbed), OS packages, runtime versions, locale, TZ to deterministic JSON; CLI `config-detective snapshot`"
    status: pending
  - id: phase2_graph
    content: "Environment Graph builder + Differ: multi-layer NetworkX graph with typed nodes/edges, cross-layer join wiring, delta extraction, suspect-priority scoring"
    status: pending
  - id: phase3_memory
    content: "Memory RAG: Supabase pgvector schema, episodic + semantic memory, HF bge embeddings, retrieve_top_k by failure signature, reflection agent for pattern compression"
    status: pending
  - id: phase4_retrieval
    content: "Multi-source retrieval: GitHub Issues, StackExchange, OSV.dev, libraries.io with fan-out + dedup + local SQLite cache"
    status: pending
  - id: phase5_orchestrator
    content: "LangGraph multi-agent orchestrator with observable intermediate states: Triager, Prioritizer, Hypothesizer (k=3), Verifier, Critic, Reporter; structured trace store"
    status: pending
  - id: phase6_sandbox
    content: "Sandbox verifier: Docker SDK ephemeral containers with resource caps; Windows subprocess fallback for graceful degradation"
    status: pending
  - id: phase7_guardrails
    content: "Guardrails: PII/secret scrubber, hallucination guard (claimed cause must exist in delta), refusal patterns, hard iter/time caps"
    status: pending
  - id: phase8a_patcher
    content: "Patcher: unified-diff builder, applier, rollback (config-detective undo), interactive confirm prompt; CLI --mode propose|apply, Streamlit Apply button"
    status: pending
  - id: phase8b_mcp
    content: "MCP server emission: tools (compare_envs, bisect_dockerfile_layer, explain_config_delta, find_similar_past_case, propose_fix, apply_fix, undo_fix) over stdio transport for Cursor integration"
    status: pending
  - id: phase9_ui
    content: "Streamlit UI: 5 tabs (Investigate, Live Trace Viewer, Memory dashboard, Eval Harness runner, MCP Export); reuse Game Glitch Investigator UI patterns"
    status: pending
  - id: phase10_eval
    content: "15-case seeded benchmark: locale (2), openssl (2), glibc/musl (2), timezone (1), python-version (2), missing-OS-pkg (2), lockfile-drift (2), env-var (2); compute top-1/top-3 accuracy, avg-iters, hallucination rate"
    status: pending
  - id: phase11_docs_loom
    content: "Pytest suite (25-35 tests), README with base-project identification + 3 sample interactions, model_card.md with ethics + AI-collaboration retrospective, architecture PNG in assets/, Loom walkthrough showing 3 inputs + live trace + MCP-from-Cursor + eval harness"
    status: pending
---

# CONFIG DETECTIVE - Works-On-My-Machine Forensics Agent

## Overview

Build CONFIG DETECTIVE - an agentic, multi-layer Graph RAG forensics system that diagnoses "works on my machine" bugs by performing differential bisection over environment graphs (lockfiles, Docker layers, OS packages, env vars), backed by Supabase pgvector memory, and emitting an MCP server so Cursor can drive follow-up queries. Extends the Game Glitch Investigator's investigator DNA into a portfolio-grade applied AI system.

## Pitch

Drop in two environment snapshots (one where the code works, one where it fails) plus the failure trace. A multi-agent system performs differential bisection across a multi-layered environment graph (Python deps, OS packages, env vars, Dockerfile layers, locale/TZ, runtime versions), retrieves similar past cases from Supabase pgvector memory, sandbox-verifies hypotheses in disposable Docker containers, and outputs a confidence-scored root cause with a one-line fix that has been **empirically verified to resolve the failure**. The agent can either propose the patch for human review (default) or apply it directly to the repo (opt-in `--apply` mode). The entire investigation is exposed as an MCP server so Cursor itself becomes a debugging UI.

**Resume bullet target:** "Built CONFIG DETECTIVE, an agentic system performing differential bisection over a multi-layer environment Graph RAG with empirical sandbox verification, episodic memory in Supabase pgvector, and observable LangGraph state transitions; achieved 87% top-1 root-cause accuracy on a 15-case seeded benchmark and shipped as a Model Context Protocol server."

## Operating modes

The agent has two output modes, controlled by a `--mode` flag (or UI toggle):

- **Propose mode (default, human-in-the-loop):** agent diagnoses, sandbox-verifies the fix, presents a unified diff + plain-English explanation + confidence score, and waits for human confirmation. User clicks "Apply" or runs `config-detective apply --case <case_id>` to commit the patch.
- **Auto-apply mode (`--mode apply` / `--apply`):** agent diagnoses, sandbox-verifies, and writes the patch directly to the repo. Requires a confirmation prompt at startup unless `--yes` is also passed (CI use case). Records every applied patch in Supabase memory with rollback metadata so a `--undo` is always possible.

This dual-mode design is itself a *responsible AI* talking point for the README/model_card: capable of full automation, but defaults to human review for safety.

## What "fix verified" actually means

When the agent reports a root cause, the claim is backed by an experiment:

1. Build a sandbox container identical to the *failing* environment.
2. Apply only the candidate fix (one-delta change).
3. Re-run the original failing command inside the sandbox.
4. Capture exit code and stderr.
5. The fix is "verified" only if exit code goes from non-zero (failure) to zero (success) **and** the failure-trace fingerprint disappears from stderr.

This is what separates CONFIG DETECTIVE from a generic LLM debugger: the report is grounded in a reproducible experiment, not in LLM intuition.

## Architecture

```mermaid
flowchart TB
    subgraph inputs [Inputs]
        snapA[snapshot A: works]
        snapB[snapshot B: fails]
        trace[failure trace]
    end

    subgraph snapshot [Snapshot Layer]
        sn[snapshotter CLI]
        lock[lockfile parser]
        dock[Dockerfile parser]
        envv[env var capture]
        ospk[OS pkg list]
    end

    subgraph graph [Environment Graph RAG]
        builder[graph builder NetworkX]
        differ[differ - delta extraction]
        prio[suspect prioritizer]
    end

    subgraph memory [Memory RAG - Supabase pgvector]
        epis[episodic memory: past cases]
        sem[semantic memory: pattern fingerprints]
        refl[reflection agent]
    end

    subgraph external [Multi-Source RAG]
        gh[GitHub Issues]
        so[StackExchange]
        osv[OSV.dev CVEs]
        debian[Debian/PyPI metadata]
    end

    subgraph orch [LangGraph Multi-Agent Orchestrator]
        triager[Triager]
        hypo[Hypothesizer]
        verif[Sandbox Verifier]
        critic[Critic + confidence]
        reporter[Reporter]
    end

    subgraph guards [Guardrails]
        pii[PII scrubber]
        halluc[hallucination guard]
        limits[resource caps]
    end

    subgraph outputs [Outputs]
        report[Root-cause report]
        mcpsrv[Per-project MCP server]
        traceview[live trace viewer]
    end

    inputs --> snapshot
    snapshot --> graph
    graph --> orch
    memory --> orch
    external --> orch
    orch --> guards
    guards --> outputs
    refl --> memory
    verif <--> sandbox[Docker disposable containers]
```

## What gets reused vs built fresh

- **Reused from Game Glitch Investigator:** Streamlit UI scaffolding, pytest patterns, `logic_utils`-style modular separation, Developer Debug tab pattern (now becomes Live Trace Viewer)
- **Built fresh:** everything else - the agent stack, Graph RAG, Memory RAG, MCP server, eval harness

## Stack

- **Language:** Python 3.11+, packaged with `pyproject.toml`
- **Reasoning LLM:** Groq free tier (Llama 3.3 70B) primary, Google Gemini 2.0 Flash fallback
- **Code/explanation LLM:** HuggingFace Inference API (Qwen2.5-Coder-32B free)
- **Embeddings:** HuggingFace `bge-large-en-v1.5` via free Inference
- **Database:** Supabase Postgres + pgvector (free tier, 500MB)
- **Graph:** NetworkX in-memory, persisted as JSON in Supabase
- **Sandbox:** Docker SDK for Python, ephemeral containers (Windows: requires Docker Desktop + WSL2)
- **Agent framework:** LangGraph (observable state transitions, rubric stretch +2)
- **MCP:** Anthropic `mcp` Python SDK
- **UI:** Streamlit (preserves Game Glitch Investigator DNA)
- **Testing:** pytest, deterministic seeded fixtures
- **External APIs (free):** GitHub REST, StackExchange, OSV.dev, libraries.io

## Repo structure

```
applied-ai-system-final/
├── README.md                          # main project doc
├── model_card.md                      # ethics + reflection
├── pyproject.toml
├── requirements.txt
├── .env.example                       # GROQ_API_KEY, HF_TOKEN, SUPABASE_URL, SUPABASE_KEY, GITHUB_TOKEN
├── assets/
│   ├── architecture.png               # exported from architecture.mmd
│   ├── architecture.mmd
│   └── demo_screenshots/
├── config_detective/
│   ├── __init__.py
│   ├── cli.py                         # `config-detective snapshot|investigate|eval|mcp-serve`
│   ├── streamlit_app.py               # 5-tab UI
│   ├── snapshot/
│   │   ├── snapshot.py                # orchestrates capture
│   │   ├── env_vars.py
│   │   ├── lockfiles.py               # uv.lock, requirements.txt, package-lock.json
│   │   ├── dockerfile.py              # uses dockerfile-parse
│   │   └── os_packages.py             # apt/dpkg/brew probes
│   ├── graph/
│   │   ├── schema.py                  # node/edge typed dataclasses
│   │   ├── builder.py                 # multi-layer NetworkX graph
│   │   ├── differ.py                  # delta extraction across layers
│   │   └── traversal.py               # path queries for cross-layer joins
│   ├── memory/
│   │   ├── supabase_client.py
│   │   ├── memory_rag.py              # episodic + semantic, mem0-style
│   │   └── reflection.py              # weekly compression
│   ├── retrieval/
│   │   ├── github_search.py
│   │   ├── stackoverflow.py
│   │   ├── osv.py
│   │   └── multi_source.py            # fan-out + dedup
│   ├── agents/
│   │   ├── orchestrator.py            # LangGraph state machine
│   │   ├── triager.py
│   │   ├── prioritizer.py             # ranks deltas by suspect-score
│   │   ├── hypothesizer.py
│   │   ├── verifier.py                # sandbox runner
│   │   ├── critic.py                  # confidence + missing-info detection
│   │   └── reporter.py
│   ├── sandbox/
│   │   ├── docker_runner.py           # ephemeral containers
│   │   └── subprocess_fallback.py     # Windows fallback if Docker unavailable
│   ├── llm/
│   │   ├── client.py                  # Groq + Gemini fallback
│   │   ├── code_llm.py                # HF Inference for code-LLM
│   │   └── prompts.py
│   ├── guardrails/
│   │   ├── pii.py                     # secret/token redactor
│   │   ├── hallucination.py           # claimed cause must exist in delta
│   │   └── limits.py                  # iter caps, time caps
│   ├── patcher/
│   │   ├── unified_diff.py            # builds unified diff from candidate delta
│   │   ├── applier.py                 # writes patch to repo files (Dockerfile, lockfile, .env)
│   │   ├── rollback.py                # snapshot pre-patch state for one-shot --undo
│   │   └── confirm.py                 # interactive confirmation prompt
│   ├── mcp_server/
│   │   ├── server.py                  # MCP server entry point
│   │   └── tools.py                   # compare_envs, bisect_layer, explain_delta, find_similar, propose_fix, apply_fix, undo_fix
│   └── eval/
│       ├── benchmark.py               # runner over seeds/
│       ├── metrics.py                 # top-1, top-3, avg-iters, hallucination rate
│       └── seeds/                     # 15 ground-truth cases
│           ├── locale_lang_c/
│           ├── locale_lc_all/
│           ├── openssl_v3_break/
│           ├── openssl_legacy_provider/
│           ├── glibc_alpine_vs_debian/
│           ├── musl_vs_glibc/
│           ├── timezone_utc_vs_kolkata/
│           ├── python_312_typing/
│           ├── python_311_async/
│           ├── missing_libpq_dev/
│           ├── missing_libgomp/
│           ├── lockfile_resolver_drift/
│           ├── lockfile_pin_missing/
│           ├── env_var_pythonpath/
│           └── env_var_openssl_conf/
└── tests/
    ├── test_differ.py
    ├── test_graph.py
    ├── test_memory.py
    ├── test_guardrails.py
    ├── test_orchestrator_smoke.py
    └── conftest.py                    # mocked LLM + Supabase fixtures
```

## Build phases (target ~23-26 hours)

### Phase 0 - Repo handoff (30 min)
- Clone Game Glitch Investigator into new repo `applied-ai-system-final`
- `git remote set-url origin` to new public repo
- Push, scaffold the directory structure above
- Create Supabase project, enable pgvector
- Get free API keys: Groq, HuggingFace, Supabase, GitHub PAT

### Phase 1 - Snapshot module (3 hrs)
- `config_detective/snapshot/snapshot.py` produces a deterministic JSON snapshot covering:
  - All lockfiles in repo
  - Parsed Dockerfile layers (build args, FROM, RUN, ENV, COPY, etc.)
  - `os.environ` filtered through PII scrubber
  - `dpkg -l` / `apt list --installed` / `brew list` (whichever applies)
  - Python/Node/Ruby version files
  - Locale, timezone, kernel, glibc/musl detection
- CLI command: `config-detective snapshot --output snap.json`
- Tests: snapshot determinism (same env produces same hash modulo timestamp)

### Phase 2 - Environment Graph + Differ (3 hrs)
- Multi-layer NetworkX graph with typed nodes (`PythonPackage`, `OSPackage`, `EnvVar`, `DockerfileLayer`, `LocaleSetting`, `RuntimeVersion`) and typed edges (`requires`, `affects`, `installed_by`, `read_by`)
- Differ extracts: items only in A, only in B, items differing in version/value
- Cross-layer join examples wired in:
  - `cryptography` (PyPkg) -[requires]-> `libssl3` (OSPkg) -[configured_by]-> `OPENSSL_CONF` (EnvVar)
  - `psycopg2` -[requires]-> `libpq5`
  - `pandas` (datetime parsing) -[reads]-> `TZ` -[reads]-> `/etc/timezone`
- Suspect-priority scoring: heuristic features (delta-class weight) + LLM-aided rerank using failure trace embedding
- Tests: 5 hand-built fixtures with known deltas

### Phase 3 - Memory RAG (3 hrs)
- Supabase schema: `cases` (snapshot_a_hash, snapshot_b_hash, failure_signature_embedding, root_cause_node_id, fix_text, created_at), `pattern_fingerprints` (compressed semantic memory)
- `memory/memory_rag.py`: insert, retrieve_top_k by failure-signature embedding, reflection agent that runs nightly to compress repeated patterns into semantic memory ("locale bugs typically present as UnicodeDecodeError + LANG delta")
- Embedding via HF `bge-large-en-v1.5`
- Tests: insert + retrieve + reflection unit tests with mocked Supabase

### Phase 4 - Multi-source retrieval (1.5 hrs)
- GitHub Issues search keyed by error signature + delta items
- StackExchange search same
- OSV.dev for CVEs on packages with version delta
- Fan-out + dedup + relevance rerank
- Aggressive caching to local SQLite to avoid burning rate limits during dev

### Phase 5 - LangGraph orchestrator with observable steps (4 hrs)
- LangGraph state machine: `Triage -> Build_Graph -> Diff -> Prioritize -> Recall_Memory -> Multi_Source_Retrieve -> Hypothesize_k=3 -> Verify_in_Sandbox -> Critique -> [loop or finalize] -> Report -> Persist_Memory -> Emit_MCP`
- Each node logs structured events to a thread-safe trace store
- Streamlit Live Trace Viewer renders these in real time (rubric stretch +2: observable intermediate steps)
- Confidence scoring at the Critic node; if confidence < 0.7, agent escalates with "human review needed" + reasoning chain shown

### Phase 6 - Sandbox verifier (2 hrs)
- Docker SDK builds disposable containers with candidate fix applied (single env var swap, single pkg version pin, single Dockerfile layer change)
- Captures exit code + stderr; compares failure signature
- Resource caps: max 5 min total, max 10 sandbox runs per investigation, max 512MB RAM per container
- Windows fallback (`subprocess_fallback.py`): venv-based partial verification when Docker unavailable

### Phase 7 - Guardrails (1 hr)
- PII/secret scrubber: regex + entropy heuristic on env vars before LLM exposure
- Hallucination guard: validates that every claimed root-cause node exists in the actual delta set; rejects fabricated config items
- Refusal patterns for "execute arbitrary user shell command" requests
- Hard caps wired into orchestrator state

### Phase 8a - Patcher: propose + apply modes (1 hr)
- `patcher/unified_diff.py`: takes verified delta + target file (Dockerfile / requirements.txt / .env / docker-compose.yml) and produces a unified diff
- `patcher/applier.py`: applies the diff to the actual repo files; supports `--dry-run`
- `patcher/rollback.py`: snapshots pre-patch contents to Supabase + local backup; `config-detective undo` reverts the last applied patch
- `patcher/confirm.py`: interactive yes/no prompt with diff preview; bypassed by `--yes` for CI
- CLI: `config-detective investigate ... --mode propose` (default) or `--mode apply [--yes]`
- Streamlit: "Apply this fix" button in the trace viewer with live diff preview
- Tests: applier round-trip (apply -> undo -> file is identical), confirm-prompt rejection path

### Phase 8b - MCP server emission (2 hrs)
- `mcp_server/server.py` exposes tools (read + write):
  - `compare_envs(snap_a, snap_b) -> delta_summary`
  - `bisect_dockerfile_layer(layer_idx) -> verification_result`
  - `explain_config_delta(item) -> natural_language_explanation`
  - `find_similar_past_case(failure_signature) -> top_k_cases`
  - `propose_fix() -> {diff, confidence, evidence}`
  - `apply_fix(case_id, confirm=true) -> applied_patch_metadata`
  - `undo_fix(case_id) -> rollback_result`
- CLI: `config-detective mcp-serve` (stdio transport) - drop into Cursor's MCP config and the agent becomes a Cursor tool. Demo flow: paste failing trace into Cursor chat -> Cursor calls `find_similar_past_case` then `propose_fix` then (with explicit user OK) `apply_fix` -> done without leaving the IDE

### Phase 9 - Streamlit UI (2 hrs)
- Tab 1 - Investigate: upload two snapshots + paste failure trace, run, watch live trace
- Tab 2 - Trace Viewer: full LangGraph state stream with cited graph nodes per step
- Tab 3 - Memory: browse past cases, see retrieved-similar pane
- Tab 4 - Eval Harness: run the 15-case benchmark, see metrics dashboard (rubric stretch +2)
- Tab 5 - MCP Export: one-click "copy MCP config for Cursor"

### Phase 10 - 15-case eval benchmark (2 hrs)
- Each `eval/seeds/<case>/` ships with `good.Dockerfile`, `bad.Dockerfile`, `trace.txt`, `ground_truth.yaml` (root_cause_node_id, fix_text, category)
- Categories covered (15 total): locale (2), openssl (2), glibc/musl (2), timezone (1), python-version (2), missing-OS-pkg (2), lockfile-drift (2), env-var (2)
- `eval/benchmark.py` runs all 15, computes top-1/top-3 root-cause accuracy, avg iterations, hallucination rate, mean confidence
- Output: markdown report + JSON for CI

### Phase 11 - Testing + docs + Loom (2 hrs)
- Pytest suite (target 25-35 tests covering differ, graph, memory, guardrails, orchestrator smoke)
- `README.md` per assignment requirements: identifies Game Glitch Investigator as base, summary, architecture overview, setup, 3 sample interactions, design decisions, testing summary, reflection, Loom link
- `model_card.md` per assignment requirements: bias/limitation analysis, misuse vectors, surprises during testing, AI collaboration retrospective with one helpful + one flawed example
- Architecture diagram exported PNG in `assets/`
- Loom recording: 3 sample inputs (locale, openssl, timezone), shows live trace, shows MCP server being called from Cursor, shows eval harness running

## Three sample interactions (for README + Loom)

**Sample 1 - Locale bug**

- Input: snapshot A on Ubuntu with `LANG=en_US.UTF-8`, snapshot B in Alpine container with `LANG=C`. Trace: `UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2`.
- Expected agent path: Triage classifies as encoding error -> Differ surfaces 8 deltas -> Prioritizer ranks `LANG` delta highest (locale-class delta + encoding error correlation from memory) -> Hypothesizer proposes "missing UTF-8 locale" -> Sandbox verifies by setting `LANG=en_US.UTF-8` and reproducing pass -> Critic confidence 0.92 -> Report: "Root cause: container LANG=C strips UTF-8 codec. Fix: ENV LANG=C.UTF-8 (or install locales pkg + en_US.UTF-8)."

**Sample 2 - OpenSSL major version drift**

- Input: A on Debian Bookworm (libssl3), B on Bullseye (libssl1.1). Trace: `cryptography.exceptions.UnsupportedAlgorithm`.
- Agent path: Differ flags libssl3 -> libssl1.1 + cryptography version unchanged -> memory recall surfaces 2 prior similar cases -> hypothesis: ABI mismatch -> sandbox verifies by pinning cryptography to a Bullseye-compatible version -> Report with OSV.dev cross-reference link.

**Sample 3 - Timezone-dependent test**

- Input: A local laptop (TZ=UTC), B in CI (TZ=Asia/Kolkata). Trace: pytest assertion on timestamp string mismatch.
- Agent path: Differ surfaces TZ delta -> Prioritizer ranks high (TZ delta + timestamp assertion correlation) -> sandbox verifies by setting `TZ=UTC` in CI -> Report: "Root cause: TZ=Asia/Kolkata in CI shifts datetime.now() by +5:30. Fix: ENV TZ=UTC, or use timezone-aware datetimes in the test."

## Reliability and evaluation summary

- 15-case seeded benchmark with hand-labeled root-cause ground truth
- Metrics: top-1 root-cause accuracy, top-3 accuracy, avg iterations to verified fix, hallucination rate, mean confidence
- Hallucination guard enforced at the Critic node - any claimed cause node not present in the delta set is auto-rejected
- All sandbox runs deterministic (pinned base images, frozen lockfiles)
- Pytest covers core modules (differ, graph, memory, guardrails, orchestrator smoke)
- Live trace viewer in Streamlit makes every intermediate decision auditable

## Risk callouts

- **Docker on Windows:** requires Docker Desktop + WSL2; subprocess fallback ships for graceful degradation
- **HuggingFace Inference rate limits:** aggressive embedding cache to local SQLite; Groq used for chat to avoid HF chat-model rate limits
- **LangGraph learning curve:** ~2 hr investment; if it slips, a hand-rolled state machine in `orchestrator.py` is a 1-day fallback
- **Supabase free tier:** 500MB easily fits the projected ~5K cases over project lifetime
- **OneDrive workspace:** known to interfere with `.cursor/plans/` and other tool-managed dotfiles. Recommend either (a) moving the workspace out of OneDrive, or (b) excluding the `.cursor/` and `.git/` folders from OneDrive sync via right-click -> "Always keep on this device" off / "Free up space" / OneDrive settings exclusion list. Most reliable fix: develop in `C:\Users\princ\Projects\` outside OneDrive entirely.

## Stretch features hit (rubric +8)

- **RAG Enhancement (+2):** Multi-source retrieval (GitHub + StackExchange + OSV + library metadata) joined with episodic memory, with measurable lift documented in the eval report (top-1 with vs without memory recall)
- **Agentic Workflow Enhancement (+2):** LangGraph multi-agent loop with full observable intermediate steps in the live trace viewer
- **Fine-Tuning or Specialization (+2):** Few-shot prompt patterns specialized per delta-category (locale, openssl, timezone, etc.) demonstrated to outperform a baseline single-prompt LLM on the same benchmark
- **Test Harness or Evaluation Script (+2):** `config-detective eval` runs the 15-case benchmark and prints a pass/fail summary table with metrics
