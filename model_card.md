# Model Card: CONFIG DETECTIVE

> A model card is a short document that summarizes a system's intended use, design choices, limitations, and ethical considerations. This card covers CONFIG DETECTIVE as required by the assignment's reflection prompts.

## System summary

CONFIG DETECTIVE is an agentic AI system for diagnosing config-divergence bugs ("works on my machine"). It compares two environment snapshots, performs differential bisection over a multi-layer environment graph, retrieves similar past cases from a vector memory store, hypothesizes top-3 candidate root causes, and empirically verifies each candidate by re-running the original failure inside a sandboxed Docker container with the candidate fix applied. It returns the verified fix with a confidence score and an applicable patch, and can either propose the fix for human review or apply it directly.

## Intended use

- Developers diagnosing why code that works locally fails in CI, Docker, or production
- Platform/SRE engineers triaging cross-environment regressions
- Educators teaching about reproducible environments and configuration management

**This is not a production-grade tool.** It is a portfolio project demonstrating applied AI engineering patterns (Graph RAG, Memory RAG, agentic workflows, MCP, sandbox verification, evaluation harnesses). It should not be relied upon as the sole authority for fixes in critical production systems.

## Out-of-scope use

- Diagnosing application-level bugs (logic errors in source code) — that is what the legacy Game Glitch Investigator is for and what generic AI debuggers cover
- Auto-applying fixes to production systems without human review
- Decompiling or reverse-engineering third-party binaries (deliberately out of scope; see the `legacy/` reflection on the alternative OBSCURA design that was considered and not selected)

## Architecture and key dependencies

See [`README.md`](./README.md#architecture) for the full architecture. Critical AI dependencies:

- **Reasoning LLM:** Groq (Llama 3.3 70B) primary, Google Gemini 2.0 Flash fallback
- **Code/explanation LLM:** HuggingFace Inference (Qwen2.5-Coder)
- **Embeddings:** HuggingFace `bge-large-en-v1.5`
- **Vector DB:** Supabase pgvector
- **Agent framework:** LangGraph

## Reflection prompts

The assignment requires this section answer five specific reflection prompts.

### 1. What are the limitations or biases in your system?

> [in progress] - filled in once Phase 10 (eval harness) has produced quantitative results. Initial expected limitations:

- **English-only failure traces.** The LLM and embeddings handle other languages, but the trace classifier is tuned on English error messages. Failures expressed in localized error messages may underperform.
- **Linux/macOS-centric environment graph.** The OS-package layer is built around `dpkg`, `apt`, and `brew`. Windows-native environments will have a sparser graph; the system falls back to env-vars-only diff in that case.
- **Pattern bias from memory recall.** Once the memory store has seen many `LANG=C` locale bugs, it will be predisposed to suggest locale fixes for new ambiguous cases. The hallucination guard at the Critic node catches outright fabrications but cannot fully compensate for confirmation bias.
- **Eval bias.** The 15-case benchmark is hand-built from common config-divergence patterns I have personally seen or read about. Real-world cases include long-tail bugs (corporate proxies, SELinux contexts, exotic kernel modules) that the benchmark does not cover.

### 2. Could your AI be misused, and how would you prevent that?

> [in progress] - finalized in Phase 7. Initial misuse vectors and mitigations:

- **Auto-apply in CI without review.** A user could run `config-detective investigate --apply --yes` in CI and let the agent push patches automatically. The agent could pick a wrong fix that masks a deeper bug. **Mitigation:** the `--yes` flag is intentionally separate from `--apply` so CI use requires explicit double opt-in. Every applied patch is logged with rollback metadata so `config-detective undo` always works.
- **Secret exfiltration via env-var snapshots.** Snapshots include `os.environ`, which often contains tokens. **Mitigation:** the PII/secret scrubber regex-matches common token patterns (AWS keys, GitHub PATs, Slack tokens, JWTs) plus an entropy heuristic on unknown values, redacting them before any LLM call or storage.
- **Hallucinated root causes accepted as truth.** **Mitigation:** the empirical sandbox verifier requires the proposed fix to actually resolve the failure (exit code goes from non-zero to zero) before the report is generated. The Critic also enforces that any claimed root-cause node must exist in the actual delta set.

### 3. What surprised you while testing your AI's reliability?

> [in progress] - filled in after Phase 10 (eval harness).

### 4. Describe your collaboration with AI during this project. Identify one instance when AI gave a helpful suggestion and one instance where its suggestion was flawed or incorrect.

> [in progress] - finalized at project end. Will include:
>
> - One concrete example where the AI assistant gave a useful suggestion that meaningfully improved the design (e.g., proposing the propose-vs-apply dual mode, suggesting LangGraph for observable state, or recommending a specific guardrail pattern)
> - One concrete example where the AI assistant suggested something that was wrong or misleading, how it was caught, and what was done instead

### 5. Testing summary

> [in progress] - filled in once Phase 10 (eval harness) has run. Will include the final 15-case benchmark results in the format:
>
> "X out of 15 tests passed; the AI struggled when the delta set was very large (>30 items). Confidence scores averaged Y; accuracy improved by Z% after adding the memory recall step."

## Data and privacy

- **No data leaves the user's machine without explicit configuration.** Supabase memory storage is opt-in (controlled by `SUPABASE_URL` being set). When unset, memory is held in a local SQLite cache that never reaches the network.
- **Snapshots include env vars,** which often contain secrets. The PII scrubber redacts them before storage or LLM exposure.
- **Sandbox containers** run with no network unless the user explicitly opts in (the `--allow-network` flag) — by default the verifier runs offline.

## Responsible AI choices made

- **Default to human-in-the-loop.** Auto-apply is opt-in, not the default.
- **Empirical verification, not LLM faith.** Every reported root cause is backed by a reproducible sandbox experiment. The LLM does not get the final say.
- **One-shot rollback.** Every applied patch creates a rollback entry; `undo` is always available.
- **PII scrubbing before LLM exposure.** No raw secrets reach an external API.
- **Transparent reasoning.** The Live Trace Viewer in the Streamlit UI exposes every intermediate agent step so users can audit the reasoning chain.

## Known failure modes

> [in progress] - filled in as failures are observed during eval. Expected categories:
>
> - Very large delta sets (>30 items) where the prioritizer struggles to rank
> - Multi-causal failures (two interacting deltas) where single-delta sandbox verification fails to confirm any one candidate
> - Failures requiring code changes in addition to config changes
