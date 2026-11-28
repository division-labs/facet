# Facet `scripts/` — Validation, 1:1 Trace Forensics, SDK Runner & User-Scope Installer

The `scripts/` directory provides Facet's release-verification spine, 1:1 runtime/wire trace forensics, headless `copilot-runtime` driver, and user-level customization installer. None of these tools consume VRAM on their own or write inside sealed `specs/<concept_slug>/` packages. For the end-to-end contributor workflow that pairs these scripts with multi-modality scratch A/B batteries, see [Empirical Engineering and Contributor Guide](../docs/empirical-engineering.md).

---

## Directory Overview

| Script | Responsibility |
| :--- | :--- |
| [`validate_system.py`](./validate_system.py) | Five-pathway self-validation suite covering static packaging/schema contracts, visual OCR & domain governance, deterministic fault injections, multi-axis FSM trajectories, and live 1:1 `copilot-runtime` modality verification. |
| [`analyze_trace.py`](./analyze_trace.py) | Zero-token session trace analyzer, verbatim 1:1 stage-context bundler, 6-bucket agent-cognition diagnostician, 5-modality `pass@k` / `pass^k` evaluator, and head-to-head A/B non-regression comparator. |
| [`copilot_sdk_runner.mjs`](./copilot_sdk_runner.mjs) | Shared Node/Electron ES-module driver that runs VS Code's unpacked `@github/copilot-sdk` (`copilot-runtime`) over `stdio` through a byte-preserving loopback HTTP wire-tap proxy (`wire_capture.jsonl`) with OS idle-sleep protection (`caffeinate -i`) and a 30-second streaming heartbeat. |
| [`install_user_level.py`](./install_user_level.py) | Synchronizes custom agents, skills, ambient instructions, Modelfiles, and lifecycle hooks (`--scope user`) into `~/.copilot/` (or `$COPILOT_HOME`) while preserving user-configured `model:` identifiers in `~/.copilot/agents/*.agent.md`. |

---

## 1. `validate_system.py` — Five-Pathway System Validation Suite

[`validate_system.py`](./validate_system.py) verifies that every static contract, deterministic hook guard, Cleanroom boundary, cryptographic seal, and live local model integration holds end-to-end. Every run automatically isolates `COPILOT_HOME` inside a temporary directory so host `~/.copilot` state and the Shadow Seal Ledger are never mutated during testing.

### Architecture & The Five Validation Pathways

1. **Pathway 1 — Static Contracts, Packaging & Runtime Entrypoint Inventory (`--pathway static`):**
   - Validates all four JSON Schemas ([`schema.json`](../.github/skills/technical-perception/schema.json), [`mapping_schema.json`](../.github/skills/formal-mapping/mapping_schema.json), [`audit_feedback_schema.json`](../.github/skills/verification-auditing/audit_feedback_schema.json), and [`pipeline_config.schema.json`](../.github/skills/spec-pipeline/pipeline_config.schema.json)), [`approved_sources.json`](../.github/hooks/approved_sources.json), and the shipped reference bundle ([`examples/mandelbrot_set/`](../examples/mandelbrot_set/)).
   - Verifies the acyclic 9-layer import DAG across [`.github/hooks/scripts/facet_guards/`](../.github/hooks/scripts/facet_guards/), centralized path-anchor invariants (`constants_and_context.py`), `Function Map:` module headers paired 1:1 with `Section N:` anchors and AST symbols across all repository `.py`/`.mjs` files, and outer-vs-inner hook timeout bounds ([`pipeline-guards.json`](../.github/hooks/pipeline-guards.json)).
   - Audits all 7 `.agent.md` definitions, 5 `SKILL.md` bundles, 7 clean-stop Ollama Modelfiles (`build_modelfiles.sh`), `Facet Lens` ES modules (`node --check`), documentation relative links, and repository-wide zero-emoji ASCII hygiene.
2. **Pathway 2 — Source Domain Governance, Visual OCR Staging & TEI P5 Provenance (`--pathway ocr`):**
   - Exercises the 3-layer research domain allowlist, `/add-source` workspace/user commands, SSRF cross-domain redirect blocking, MediaWiki API canonicalization, duplicate `web_fetch` loop protection, and `permissionRequest` auto-approval boundaries.
   - Tests [`prepare_visual_sources.py`](../.github/skills/technical-perception/scripts/prepare_visual_sources.py) across multi-page PDFs (`200 DPI` lossless PNG page plates + per-plate `<vector_glyph_anchor>` + orthogonal `CGPDFScanner` `SOURCE_TEXT.md` extraction), images (PNG, JPEG, BMP, GIF, WebP, TIFF, SVG), HTML documents, corrupted-asset quarantine, and single-plate-at-a-time `@Scribe` TEI P5 XML validation (`<uncertain>`, `<illegible/>`).
3. **Pathway 3 — Stage-Boundary Fault Injections, Multi-Stage Editing & Cleanroom Oracles (`--pathway faults`):**
   - Injects malformed Gemma `<|"|>` LaTeX JSON escapes, `.rollback_*` recovery scenarios, missing `SPEC-k` coverage, continuous float `==` violations, misplaced `from __future__ import annotations`, unbound lexical names (`AST-2`), asymmetric rational-pole clipping (`AST-3`), and forged SHA-256 checkpoint/archive seals.
   - Verifies the lossless multi-stage `edit` auto-snapper (`CRLF`/whitespace, uniform indentation shift, raw-docstring promotion, `ruff format` fragment pre-normalization, and AST symbol spans), `<on_disk_symbol_excerpt>` failure recovery, `StageWorkingSet` file-granular scope confinement (including read-only `bash` parity), `StageEvidenceReceipts`, all five lossless `@Engineer` hydration compilers, `extract_ast_fault_cone_slice`, 1st-truncation `COMPACTION_YIELD` (`<prior_attempt_diagnosis>`), exact `type_mappings[*].symbol` foreign-key coverage + `<0.4 s` synthesized-stub `mypy` composition checking, `@Engineer` `3/3` smoke-probe caps, and `@Engineer` `MANIFEST_DEFECT` + `MAPPING_DEFECT` upstream rewinds.
4. **Pathway 4 — Multi-Axis Pipeline Trajectories & Hook CLI (`--pathway trajectories`):**
   - Executes an orthogonal spanning matrix of complete `S0V + S0 -> S5` lifecycles plus `--fuzz-trajectories <N>` seeded random trajectories (`--seed <int>`) across governance modes (`strict-4-gate`, `streamlined-2-gate`, `autonomous-batch`), entry modalities, editing tools (`edit` vs. `code_patch.py`), and target-aware Reflexion rewinds (`IMPLEMENTATION_DEFECT`, `MAPPING_DEFECT`, `MANIFEST_DEFECT`).
   - Exercises all 22 [`pipeline_guards.py`](../.github/hooks/scripts/pipeline_guards.py) CLI actions, dual-scope deduplication, striped state locking, full-stack wire simulation, streaming non-duplication, two-plane UX checks, and the [`pytest_facet_stochastic.py`](../.github/hooks/scripts/pytest_facet_stochastic.py) Hypothesis plugin subprocess.
5. **Pathway 5 — Live Local Ollama & 1:1 `copilot-runtime` E2E Integration (`--pathway live`):**
   - Runs live local `gemma4:e4b`, `gemma4:26b-a4b-it-q8_0`, and `embeddinggemma-2` probes alongside full 1:1 `copilot-runtime` + wire-tap end-to-end pipeline runs across any selected `--live-modality` (`image`, `pdf`, `spec_bundle`, `web_ideation`, `closed_book_text`, `both`, or `all`), certifying `PROVENANCE.json` composite seals, 500-draw Cleanroom `pytest`, two-plane UX cleanliness, and `100%` 1:1 event/wire ledger parity.

### CLI Reference (`validate_system.py`)

```bash
# Run all offline pathways (Pathways 1..4; fast CI / pre-commit gate, no Ollama server required)
./.venv/bin/python scripts/validate_system.py --offline

# Run a single pathway by substring ('static', 'ocr', 'faults', 'trajectories', or 'live')
./.venv/bin/python scripts/validate_system.py --pathway static
./.venv/bin/python scripts/validate_system.py --pathway faults

# Fuzz 16 seeded random multi-axis pipeline trajectories in Pathway 4
./.venv/bin/python scripts/validate_system.py --pathway trajectories --fuzz-trajectories 16 --seed 42

# Run a live 1:1 copilot-runtime E2E test on a specific modality and save its audit bundle
FACET_SINGLE_MODALITY_DIR=1 FACET_SAVE_AUDIT_BUNDLE_DIR=logs/context_audits/live_image \
  ./.venv/bin/python scripts/validate_system.py --pathway live --live-modality image

# Run all 5 canonical modalities end-to-end and collate 00_MULTIMODAL_SCORECARD.{md,json}
FACET_SAVE_AUDIT_BUNDLE_DIR=logs/context_audits/suite_5modalities \
  ./.venv/bin/python scripts/validate_system.py --pathway live --live-modality all --scicode-step 19.1
```

| Flag / Env Var | Default | Description |
| :--- | :--- | :--- |
| `--pathway <filter>` | `all` | Substring selector (`static`, `ocr`, `faults`, `trajectories`, `live`, or `all`). |
| `--offline` | `False` | Skip Pathway 5 live Ollama / `copilot-runtime` probes. |
| `--fuzz-trajectories <N>` | `4` | Number of seeded non-baked random trajectories in Pathway 4. |
| `--seed <int>` | `20261115` | Deterministic PRNG seed for Pathway 4 trajectory fuzzing. |
| `--live-modality <mod>` | `image` | Modality for Pathway 5 (`image`, `pdf`, `spec_bundle`, `web_ideation`, `closed_book_text`, `both`, `all`). |
| `--scicode-step <id>` | `19.1` | 1-step `SciCode` subproblem backing `closed_book_text` (for example `19.1` or `11.1`). |
| `--allow-web-research` | `auto` | Override `allow_web_research` in Pathway 5 (`auto` uses the modality preset default). |
| `FACET_SAVE_AUDIT_BUNDLE_DIR` | *(unset)* | Optional directory where Pathway 5 persists raw `events.jsonl`, `wire_capture.jsonl`, and the 1:1 audit bundle. |
| `FACET_SINGLE_MODALITY_DIR=1` | *(unset)* | Write a single-modality Pathway 5 bundle directly into `FACET_SAVE_AUDIT_BUNDLE_DIR` instead of `<dir>/<modality>/`. |

---

## 2. `analyze_trace.py` — 1:1 Session Auditor, 6-Bucket Diagnostician & Multi-Modality Evaluator

[`analyze_trace.py`](./analyze_trace.py) parses native Copilot SDK session logs (`$COPILOT_HOME/session-state/<id>/events.jsonl`), optional loopback wire captures (`wire_capture.jsonl`), and `SciCode` benchmark directories (`benchmarks/runs/<run_id>/`) to produce exact token/latency accounting, 1:1 completeness verification, 6-bucket cognition forensics, multi-trial `pass@k` / `pass^k` metrics, and Control-vs.-Candidate A/B non-regression reports.

### Core Capabilities

1. **Strict 1:1 Event & Wire Completeness Accounting (`audit_and_collate_session`):**
   - Verifies that `100%` of lines in `events.jsonl` belong to `KNOWN_EVENT_TYPES` with `0` unaccounted lines, `100%` paired `hook.start`/`hook.end` and `tool.execution_start`/`tool.execution_complete` spans, `1:1` inference-call parity against `wire_capture.jsonl` socket records, and byte-exact `.agent.md`, `SKILL.md`, and `copilot-instructions.md` disk parity.
   - Emits a browsable audit bundle (`00_MASTER_AUDIT.md`, `ledger.json`, and per-stage `stages/01_...md` packets separating the User-Facing Chat Plane from full verbatim internal prompts, `<|thought|>` reasoning, hook injections, and base64-scrubbed tool payloads).
2. **Six-Bucket Agent-Cognition & Efficiency Forensics (`--diagnose`):**
   - **Bucket 1 — Reasoning & Throughput (`1_reasoning_bloat`):** Total `<|thought|>` chars vs. content chars, input/output tokens, `mean_output_tokens_per_sec` (computed over `eval_duration`), `pre_tool_prose_script_turns` (subagent turns emitting fenced Python/JSON code in prose prior to any `create`/`edit` tool call), and slow turns `> 30 s` (separating active GPU compute from OS idle-sleep gaps via `sleep_confounded_turns`).
   - **Bucket 2 — Unhydrated Tool & Scope Tax (`2_unhydrated_tool_tax`):** Per-role `view` and `todo` counts, `out_of_scope_probing_calls` (sibling `examples/*`, showcase `README.md`, unlinked `specs/*`, unscoped `grep`/`glob`, or out-of-scope `bash` `ls`/`find`/`cat` calls), `redundant_hydrated_reads`, `duplicate_unchanged_calls`, `.venv` import probes, and `web_fetch` grounding telemetry (`open_book_zero_web_warning`).
   - **Bucket 3 — Schema, Hook & Permission Friction (`3_schema_hook_and_permission_friction`):** `task` schema failures, `preToolUse` denials, `agentStop`/`subagentStop` blocks, null permission events, and `pytest` collection errors.
   - **Bucket 4 — Prompt Footprint & Echo (`4_prompt_footprint_and_echo`):** `system.message` character budgets, `peak_input_tokens_by_role`, `hook_injection_chars_by_tag` (`<subagent_context>`, `<workspace_map>`, `<hydrated_artifact>`, etc.), and cross-layer invariant echo counts.
   - **Bucket 5 — VRAM Residency & Model Swaps (`5_vram_residency_and_swaps`):** Total model transitions (`e4b <-> 26b-a4b <-> pplx-decider-flash`) and Stage 4 swap isolation.
   - **Bucket 6 — Cleanroom & Two-Plane UX Integrity (`6_cleanroom_and_ux_integrity`):** Certifies zero pre-`pytest` Cleanroom `bash` leaks, zero closed-book web leaks, zero control-plane XML/stage-enum leaks into user replies, and zero out-of-scope reads (`integrity_pass`).
3. **Declarative Five-Modality Suite & `pass@k` / `pass^k` Grading (`MODALITY_EVAL_SPECS`):**
   - `image`: Open-book visual equation scan ([`examples/magical_mystery/IMG_1598.jpg`](../examples/magical_mystery/IMG_1598.jpg), interactive checkpoints + mandatory approved-domain `web_fetch`).
   - `pdf`: Closed-book 5-page dual-channel paper ([`examples/constant_q_transform/2609.29119v1.pdf`](../examples/constant_q_transform/2609.29119v1.pdf), `CGPDFScanner` `SOURCE_TEXT.md` + 5 lossless `200 DPI` PNG plates + per-plate `<vector_glyph_anchor>` hints in `@Scribe` `SOURCE_OCR.md`).
   - `spec_bundle`: Pre-staged specification bundle ([`examples/mandelbrot_set/`](../examples/mandelbrot_set/), `autonomous-batch` closed-book).
   - `web_ideation`: Open-book pure prompt ideation (`Chebyshev / Runge` barycentric polynomial interpolation, mandatory approved-domain `web_fetch`).
   - `closed_book_text`: Selectable 1-step `SciCode` benchmark subproblem (`--scicode-step`, default `19.1`) graded against both Cleanroom 500-draw `pytest` and official `benchmarks/.cache/test_data.h5` numerical targets when cached.
   - Passing `--trials <k>` runs $k$ independent trials per modality and computes both **`pass@k`** ($1 - (1 - \hat{p})^k$, capability ceiling) and **`pass^k`** ($\hat{p}^k$, multi-run reliability).
4. **A/B Comparison & Factorial Interaction Analysis (`--compare`, `--matrix-dir`, `--factorial-json`):**
   - `--compare <base> <cand>` computes exact wall-clock, active-GPU, token, `view`, swap, and accuracy deltas and enforces an exit-code non-regression gate (`non_regression_pass`).
   - `--matrix-dir` and `--factorial-json` compute main effects and pairwise interaction terms ($I_{AB} = y_{AB} - y_A - y_B + y_0$) across multi-factor experiments.

### Audit Bundle Layout (`--audit-bundle <dir>`)

```text
<audit_bundle_dir>/
|-- 00_MASTER_AUDIT.md          # Completeness checks, 6-bucket scorecard, stage index, & user transcript
|-- ledger.json                 # Machine-readable 1:1 completeness + 6-bucket diagnostics + stage records
|-- stages/                     # Per-stage verbatim internal context packets (prompts, thoughts, tools, hooks)
|   |-- 01_stage_1_of_4_research_discovery.md
|   |-- 02_stage_2_of_4_formal_specification.md
|   |-- 03_stage_3_of_4_interface_proof_plan.md
|   `-- 04_stage_4_of_4_cleanroom_verification.md
`-- raw/                        # Populated in --run-live / copilot_sdk_runner.mjs runs
    |-- events.jsonl            # Native copilot-runtime session event stream
    |-- wire_capture.jsonl      # Byte-exact loopback Ollama /v1/responses HTTP frames & timings
    `-- run_meta.json           # Session metadata, per-turn replies, and archived spec paths
```

### CLI Reference (`analyze_trace.py`)

```bash
# 1. Inspect or diagnose an existing VS Code session (by UUID or path to events.jsonl)
./.venv/bin/python scripts/analyze_trace.py --session <session_id_or_events_jsonl>
./.venv/bin/python scripts/analyze_trace.py --session <session_id_or_events_jsonl> --diagnose
./.venv/bin/python scripts/analyze_trace.py --session <session_id_or_events_jsonl> \
  --audit-bundle logs/context_audits/my_session_audit

# 2. Run a live 1:1 SDK evaluation for one modality, all 5 modalities, or k repeated trials
./.venv/bin/python scripts/analyze_trace.py --run-live --modality pdf \
  --audit-bundle logs/context_audits/live_pdf
./.venv/bin/python scripts/analyze_trace.py --run-live --modality all --scicode-step 19.1 \
  --audit-bundle logs/context_audits/suite_5modalities
./.venv/bin/python scripts/analyze_trace.py --run-live --modality closed_book_text \
  --scicode-step 19.1 --trials 3 --audit-bundle logs/context_audits/scicode_19_1_k3

# 3. Collate a unified multi-modality Markdown + JSON scorecard across an audits directory
./.venv/bin/python scripts/analyze_trace.py --collate-multimodal-scorecard logs/context_audits/suite_5modalities

# 4. Inspect a SciCode benchmark run or compare Control vs. Candidate traces
./.venv/bin/python scripts/analyze_trace.py --bench-run benchmarks/runs/<run_id> --diagnose
./.venv/bin/python scripts/analyze_trace.py --compare logs/context_audits/control logs/context_audits/candidate

# 5. Compute orthogonal main effects and pairwise interactions from a factorial experiment
./.venv/bin/python scripts/analyze_trace.py --factorial-json path/to/factorial_metrics.json
```

---

## 3. Supporting Scripts (`copilot_sdk_runner.mjs` & `install_user_level.py`)

### [`copilot_sdk_runner.mjs`](./copilot_sdk_runner.mjs) — Headless 1:1 `copilot-runtime` + Wire-Tap Runner
Invoked automatically by [`validate_system.py`](./validate_system.py) (`--pathway live`), [`analyze_trace.py`](./analyze_trace.py) (`--run-live`), and [`benchmarks/run_benchmark.py`](../benchmarks/run_benchmark.py) (`--facet-engine sdk|auto`). It locates VS Code's local `@github/copilot-sdk` bundle, launches an ephemeral loopback HTTP proxy recording every `/v1/responses` request/SSE stream to `raw/wire_capture.jsonl` (printing a 30-second live streaming heartbeat during long reasoning turns), materializes an isolated workspace and `COPILOT_HOME`, steps interactive `/approve` checkpoints (up to `--max-approves` / `--max-turns`), and writes `raw/run_meta.json`.

```bash
# Probe whether VS Code's local @github/copilot-sdk + copilot-runtime binary are available
node scripts/copilot_sdk_runner.mjs --probe-sdk
```

### [`install_user_level.py`](./install_user_level.py) — User-Scope `~/.copilot/` Synchronization
Installs Facet's agents, skills, ambient instructions, Modelfiles, and `--scope user` lifecycle hooks into `~/.copilot/` (or `$COPILOT_HOME`) so `@Facet` is available in any local workspace without duplicating hook execution inside repositories that already contain `.github/hooks/pipeline-guards.json`:

```bash
# Preview target paths without writing to disk
./.venv/bin/python scripts/install_user_level.py --dry-run

# Sync agents (preserving existing model: tags), skills, instructions, Modelfiles, and hooks
./.venv/bin/python scripts/install_user_level.py
```

### [`sync_github_project.py`](./sync_github_project.py) — Idempotent GitHub Milestones, Issues & Project v2 Sync
Synchronizes [`ROADMAP.md`](../ROADMAP.md) deliverables and active empirical pipeline hardening items into GitHub Labels, Milestones, Issues, and a linked GitHub Project v2 board (`Facet Roadmap & Pipeline Tracker`) via the official `gh` CLI (`gh auth login --web -h github.com -p https -s repo,project`, or `GITHUB_TOKEN` in CI):

```bash
# Preview the declarative Labels, Milestones, Project v2, and Seeded Issues plan offline
./.venv/bin/python scripts/sync_github_project.py --dry-run

# Create or update Labels, Milestones, Project v2 custom fields, and Issues idempotently
./.venv/bin/python scripts/sync_github_project.py --apply
```
