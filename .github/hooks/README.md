# Facet Deterministic Hooks & Reference Monitor (`Layer 5`)

This directory implements Facet's zero-VRAM Python standard-library reference monitor. Every Copilot lifecycle event and tool boundary passes through [`scripts/pipeline_guards.py`](./scripts/pipeline_guards.py) and the acyclic 9-layer [`scripts/facet_guards/`](./scripts/facet_guards/) package to enforce stage routing, Cleanroom isolation, schema/AST soundness, format-symmetric scoped editing, and tamper-evident cryptographic provenance. For the complete event-by-event specification, see [`docs/reference/hooks-and-guards.md`](../../docs/reference/hooks-and-guards.md).

---

## Directory Layout

| Path | Responsibility |
| :--- | :--- |
| [`pipeline-guards.json`](./pipeline-guards.json) | Workspace Copilot hook manifest binding `sessionStart`, `preToolUse`, `postToolUse`, `postToolUseFailure`, `preCompact`, `userPromptSubmitted`, `userPromptTransformed`, `subagentStart`, `subagentStop`, `sessionEnd`, `errorOccurred`, and `permissionRequest` (paired with inline `agentStop` / prompt / tool hooks on `@Facet` and `@Orchestrator`). |
| [`approved_sources.json`](./approved_sources.json) | Base allowlist of authoritative scientific, mathematical, and standards domains (merged with user `~/.copilot/approved_sources.local.json` and workspace `.pipeline_config.json` `approved_research_domains`). |
| [`scripts/pipeline_guards.py`](./scripts/pipeline_guards.py) | Backward-compatible public symbol facade and 20-action CLI dispatcher (`--scope workspace\|user`, lifecycle handlers, `garden-specs`, and `reset`). |
| [`scripts/pytest_facet_stochastic.py`](./scripts/pytest_facet_stochastic.py) | Stage 4 Cleanroom `pytest` plugin enforcing unseeded Hypothesis stochastic verification (`min_examples >= 500`, `derandomize=False`). |
| [`scripts/facet_guards/`](./scripts/facet_guards/) | Modular, strictly acyclic 9-layer reference-monitor implementation (layers `0` through `8` below). |

---

## Acyclic 9-Layer `facet_guards/` Package

Imports flow strictly downward (`Layer k` imports only `Layers 0 .. k-1`, verified mechanically by [`scripts/validate_system.py`](../../scripts/validate_system.py)):

| Layer | Module | Core Responsibilities |
| :---: | :--- | :--- |
| `0` | [`constants_and_context.py`](./scripts/facet_guards/constants_and_context.py) | FSM/checkpoint tables, single-source config `Literal` / enum tuples (`EXPERIMENT_ENUM_OPTIONS`), precompiled regexes, stat-coherent `HookContext` memoization, and path/slug helpers. |
| `1` | [`state_locks_and_seals.py`](./scripts/facet_guards/state_locks_and_seals.py) | `--scope` precedence shadowing, POSIX `O_EXCL` event deduplication, 64-stripe `fcntl.flock` serialization, atomic writes, Shadow Seal Ledger, and RFC 6962 §2.1 binary Merkle tree + Merkle DAG sealing. |
| `2` | [`text_ast_and_patch.py`](./scripts/facet_guards/text_ast_and_patch.py) | Gemma `<\|"\|>` -> RFC 8259 JSON repair, raw `r"""` LaTeX docstring & `__future__` normalizers, 4-stage Ruff-normalized `resolve_edit_pair()` auto-snapping, and `AST-1..AST-6` analyzers. |
| `3` | [`visual_ocr_and_sources.py`](./scripts/facet_guards/visual_ocr_and_sources.py) | 3-layer domain registry, MediaWiki API canonicalizer, Stage 0V per-plate assembly, TEI P5 `<uncertain>`/`<illegible/>` parser, and `StageWorkingSet` scope confinement. |
| `4` | [`spec_and_mapping_gates.py`](./scripts/facet_guards/spec_and_mapping_gates.py) | `manifest.json` & `mapping.json` validators, foreign-key `type_mappings[*].symbol` coverage, `<0.4 s` synthesized-stub `mypy` composition check, and AST-risk-gated System-1 screening. |
| `5` | [`cleanroom_and_repair.py`](./scripts/facet_guards/cleanroom_and_repair.py) | Workspace shell rewriting, `permissionRequest` Cleanroom auto-approval, `AUDIT_FEEDBACK.json` normalization, target-aware `S1`/`S2`/`S3`/`S4` rewinds, and deliverable completion checks. |
| `6` | [`ux_cards_and_archival.py`](./scripts/facet_guards/ux_cards_and_archival.py) | Pure tag-free user-plane checkpoint cards, 3-column stage previews, Stage 5 dead-code pruning, Markdown dossier synthesis, `Facet Lens` SSE push, and `specs/<slug>/PROVENANCE.json` archival. |
| `7` | [`context_and_hydration.py`](./scripts/facet_guards/context_and_hydration.py) | Canonical specialist prompts, Cleanroom-redacted `engineering_build_sheet` hydration, `<hydrated_public_surface>` AST stubs, role-scoped `<workspace_map>`, deterministic `<stage_checklist>`, and `subagentStart`. |
| `8` | [`lifecycle_dispatch.py`](./scripts/facet_guards/lifecycle_dispatch.py) | Top-level Copilot lifecycle hook entrypoints (`preToolUse`, `postToolUse`, `postToolUseFailure`, slash-command/prompt transforms, and `agentStop` / `subagentStop` gates). |

---

## Dual-Scope Execution & Administrative CLI

- **Zero Double Execution (`--scope workspace` vs. `--scope user`):** When installed globally in `~/.copilot/hooks/` via [`scripts/install_user_level.py`](../../scripts/install_user_level.py), `--scope user` hooks inspect `<cwd>/.github/hooks/pipeline-guards.json` and yield `{}` in `<1 ms` whenever an active workspace hook is present.
- **Common CLI Commands:**
  ```bash
  # Audit all archived specs/*/PROVENANCE.json seals, RFC 6962 Merkle roots, and DAG links
  echo '{}' | ./.venv/bin/python .github/hooks/scripts/pipeline_guards.py garden-specs

  # Snapshot active root drafts to specs/<slug>_interrupted_<ts>/ and reset the workspace root
  echo '{}' | ./.venv/bin/python .github/hooks/scripts/pipeline_guards.py reset
  ```
