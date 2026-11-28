# Facet Customization and Reparameterization Guide

Facet is built around the **Dependency Inversion Principle** and **Bridge Pattern**, enabling you to reparameterize the pipeline for diverse engineering domains, hardware configurations, governance levels, and programming languages without altering core hook logic or persona definitions.

---

## 1. Domain Adaptation Presets

Facet decouples verification from hardcoded numerical tools. Pre-configured domain adaptation presets are available in `templates/domains/`:

### A. Scientific Computing (Default)
- **Folder:** `templates/domains/scientific-computing/`
- **Focus:** Numerical analysis, differential equations, optimization, and linear algebra.
- **Verification Basis:** Pinned analytical vectors, finite-difference pseudo-oracles, metamorphic step-halving convergence tests, and Hypothesis property fuzzing.
- **Dependencies:** `numpy`, `scipy`, `hypothesis`, `sympy`.

### B. Systems and RFC Network Protocols
- **Folder:** `templates/domains/systems-rfc-protocol/`
- **Focus:** IETF RFC specifications (e.g. RFC-8446 TLS 1.3, RFC-9000 QUIC), packet wire formats, and network state transition engines.
- **Verification Basis:**
  - `anchor`: Pinned test vectors from RFC appendices.
  - `relational_law`: Round-trip serialization identity (`decode(encode(msg)) == msg`) and state transitions.
  - `postcondition`: Rejection of malformed/truncated packets without panics.
- **Deliverables:** Protocol parser, serializer, and state machine in Python.

### C. Machine Learning Architectures and Custom Kernels
- **Folder:** `templates/domains/ml-architecture-kernels/`
- **Focus:** Deep learning architecture papers, attention variants, custom loss functions, and tensor operators.
- **Verification Basis:**
  - `anchor`: Pinned reference tensor values and shape conservation contracts.
  - `reference_model`: Numerical gradient checks (finite difference vs. autograd).
  - `relational_law`: Permutation and batch-invariance properties.
- **Deliverables:** Vectorized PyTorch modules with strict tensor shape contracts.

### D. Verified Backend Domain Services
- **Folder:** `templates/domains/verified-api-service/`
- **Focus:** Domain-Driven Design (DDD) specifications, OpenAPI schemas, and business logic invariants.
- **Verification Basis:**
  - `anchor`: Standard HTTP CRUD boundary conditions.
  - `relational_law`: Idempotence on repeated operations (`f(f(state)) == f(state)`).
  - `postcondition`: Invariant business assertions (e.g. non-negative balances).
- **Deliverables:** FastAPI + Pydantic v2 services with stateful property testing.

---

## 2. Model Profiles and Hardware Sizing

Role definitions are decoupled from specific model tags. To adapt Facet to your hardware, select a profile from `templates/model-profiles/`:

### Profile A: Apple Silicon 64GB Unified Memory (`gemma4-64gb-apple-silicon.json`)
- **Target:** Mac Mini / Studio with >= 48GB wired Metal memory.
- **Configuration:**
  - `@Researcher`, `@Formalist`, `@Engineer` & `@Auditor`: `ollama-models/gemma4:26b-a4b-it-q8_0` (Unified MoE, ~27.6 GB wired with `q8_0` KV / 3.8B active — powers Stage 0/1 literature grounding, Stage 2 `mapping.json` formalization, Stage 3 environment/coding, and Stage 4 cleanroom verification at ~52–55 tok/s with zero `S1..S4` swap).
  - `@Facet`, `@Orchestrator` & `@Scribe`: `ollama-models/gemma4:e4b` (4.5B effective PLE + dedicated `~150M` Vision Encoder, 9.4 GB — `T=0.1` routing and Stage 0V PDF/diagram OCR into `SOURCE_OCR.md`, `0.0 s` swap across `@Facet <-> @Orchestrator`).
  - `SystemOneDecider` (boundary-only hook): `pplx-decider-flash` (4B Vision+Text, `keep_alive=0`, `<1 s` semantic spec screen + Stage-4 triage + `Method L3` edit line-scope localization with `0 B` persistent VRAM).
- **Host Invariant:** Set `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_FLASH_ATTENTION=1`, and `OLLAMA_KV_CACHE_TYPE=q8_0` (via `launchctl setenv` + `Ollama.app` restart on macOS) so only one model occupies wired Metal memory at a time and GGUF `26b-a4b` 128K KV cache is reduced by 45% (`2860 -> 1572.5 MiB`); see `modelfiles/README.md`.

### Profile B: Local Workstation 16GB–24GB (`local-ollama-16gb.json`)
- **Target:** Workstation with 16GB–24GB VRAM.
- **Configuration:**
  - `@Formalist`, `@Engineer`, `@Auditor`: `ollama-models/gemma4:12b`.
  - `@Researcher`: `ollama-models/gemma4:e4b`.
  - `@Facet`, `@Orchestrator`, `@Scribe`: `ollama-models/gemma4:e2b` (one shared tag to avoid E2B/E4B runner swaps; low-latency coordination and `~150M`-encoder Stage 0V Vision/PDF OCR).

---

## 3. Governance Modes (`.pipeline_config.json`)

You can tune human review frequency by placing `.pipeline_config.json` in your workspace root. Pre-built profiles are available in `templates/governance-modes/`:

| Governance Mode | Description | Active Human Checkpoints |
| :--- | :--- | :--- |
| **`strict-4-gate`** (Default) | Comprehensive human-in-the-loop oversight. | C0 (Candidate), C1 (Manifest), C2 (Mapping), C3 (Verification) |
| **`streamlined-2-gate`** | Accelerated execution for experienced engineers. | C1 (Manifest) and C3 (Verification) |
| **`autonomous-batch`** | High-throughput batch translation. | Auto-advances through gates unless `manifest.json` contains `"epistemic_status": "assumption"` (e.g. from `<uncertain>`/`<illegible/>` OCR tags, pausing at C1) or self-repair fails ($K \ge 3$). |

### Applying a Governance Mode
```bash
cp templates/governance-modes/streamlined-2-gate.pipeline_config.json .pipeline_config.json
```

---

## 4. Python Verification and Static Validation Configuration

To customize verification test discovery globs or toggle `postToolUse` `py_compile` checks, configure the `toolchain` block in `.pipeline_config.json`:

```json
{
  "governance_mode": "strict-4-gate",
  "max_repair_attempts": 3,
  "toolchain": {
    "verification_globs": ["tests/test_*.py", "AUDIT_FEEDBACK.json", "VERIFICATION.md"],
    "static_validator": "py_compile"
  },
  "scratch_retention": "consume-once",
  "allow_web_research": true,
  "upstream_seals": ["specs/upstream_concept/PROVENANCE.json"],
  "experiments": {
    "sys1_trigger_policy": "ast_risk_gated",
    "context_hydration": "s2_s3_selective_hydration",
    "engineer_hydration_mode": "unified_build_sheet",
    "engineer_repair_mode": "pointer_full_view",
    "engineer_escalation_mode": "handoff_and_contract_escalation",
    "stage_checklist_mode": "hook_subagent_start",
    "auditor_cleanroom_policy": "two_phase_post_pytest"
  }
}
```

- `allow_web_research` (`true` by default): when set to `false`, `preToolUse` deterministically blocks both `web_fetch` and `web_search` so `@Researcher` extracts governing equations, constraints, and `target_interface` strictly from the user prompt, `SOURCE_TEXT.md`, `SOURCE_OCR.md`, and local `research/` files (closed-book mode).
- `upstream_seals` (optional, workspace-relative): lists prior-run `PROVENANCE.json` files that `@Facet` must view and that `hypervisor-guard` verifies (status, composite seal, artifact hashes, and Shadow Seal Ledger parity) before `@Orchestrator` may start. Downstream runs can also declare `upstream_modules` in `mapping.json` to import verified `specs/<slug>/` Python modules directly into Cleanroom `PYTHONPATH` with automatic SHA-256 seal verification.

The deterministic hooks adapt capability confinement (`verification_globs`) and `postToolUse` static compilation (`py_compile` or `none`) while preserving deep AST docstring/typing analysis (`validate_python_ast`) and [code_patch.py](../.github/skills/implementation-engineering/scripts/code_patch.py) across Python modules.

> **Copilot Harness Frontmatter Notes:** Facet targets the VS Code **Copilot harness** (`target: github-copilot`). Local-harness-only attributes such as `handoffs` and `argument-hint` are unsupported in the Copilot harness (so `@Facet` emits a fenced copy-paste `@Facet ...` Next-Cycle Ready Prompt instead of `handoffs` buttons), and fields such as `strict-tools-list`, `include-custom-instructions`, and `model-policy` are not forwarded during subagent conversion—tool boundaries and role instructions are therefore enforced deterministically via `tools:` frontmatter and `pipeline_guards.py` hooks.

---

## 5. Extending Approved Research Websites

`@Researcher` is decoupled from hardcoded URLs. Approved domains are resolved by merging three layers:
1. **Default Seed Registry:** [approved_sources.json](../.github/hooks/approved_sources.json) (synced to `~/.copilot/hooks/approved_sources.json`), including optional `fetch_hint` endpoint templates.
2. **User-Scoped Persistent Additions:** `~/.copilot/approved_sources.local.json`, updated via `/add-source <domain_or_url>` in VS Code Chat.
3. **Workspace-Scoped Additions:** `approved_research_domains` in `.pipeline_config.json` (or `/add-source --workspace <domain_or_url>`):

```json
{
  "governance_mode": "strict-4-gate",
  "approved_research_domains": ["docs.jax.dev", "eprint.iacr.org"]
}
```

Approved domains are auto-authorized by `permissionRequest` (`{"behavior": "allow"}`) and dynamically injected into `@Researcher`'s `<subagent_context>` at startup, while unapproved domains trigger VS Code's native interactive `Fetch URL` confirmation dialog.

---

## 6. Optional Post-Archival EmbeddingGemma 2 Semantic Index & Stage 0 Recall

To automatically chunk and index archived Stage 5 bundles into a local SQLite vector store (`$COPILOT_HOME/knowledge.db`) using Ollama's `POST /api/embed` endpoint (`embeddinggemma-2:740m-mxfp8` with Google DeepMind's asymmetric `"title: ... | text: ..."` document prompts), set `"enable_post_archive_embed": true` in `.pipeline_config.json` or invoke [post_archive_embed.py](../templates/extensions/post_archive_embed.py) directly:

```bash
./.venv/bin/python templates/extensions/post_archive_embed.py specs/mandelbrot_iteration
./.venv/bin/python templates/extensions/post_archive_embed.py --query "escape radius quadratic recurrence"
```

When `$COPILOT_HOME/knowledge.db` is populated, `discover_referenced_archived_specs()` automatically queries `recall_semantic_archived_specs()` on fresh `@Facet` prompts, surfacing the top-$k$ mathematically related sealed `specs/<slug>/` dossiers into `@Orchestrator`'s Cycle-2 handoff context.

---

## 7. Evaluating & Contributing Harness Improvements

When modifying hooks, prompts, schemas, or runtime policies, follow the empirical workflow in [Empirical Engineering and Contributor Guide](empirical-engineering.md): diagnose current-codebase traces with [analyze_trace.py](../scripts/analyze_trace.py), benchmark candidates across disjoint multi-modality scratch workspaces (including combined interaction runs), lock invariants into [validate_system.py](../scripts/validate_system.py) (`--offline`), and certify all five held-out live modalities before committing.
