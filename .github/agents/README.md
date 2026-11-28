# Facet Custom Agents (`Layer 2`)

This directory contains the seven custom agent personas (`*.agent.md`, `target: github-copilot`) that implement Facet's two-plane Coordinator-Worker pipeline over local Google Gemma 4 models in Ollama. Architectural context and cognitive-allocation details are documented in [`docs/architecture.md`](../../docs/architecture.md).

---

## Agent Persona Matrix

| Agent File | Handle & Visibility | Default Ollama Model | Frontmatter `tools:` | Pipeline Responsibility |
| :--- | :--- | :--- | :--- | :--- |
| [`facet.agent.md`](./facet.agent.md) | `@Facet` (`user-invocable: true`) | `gemma4:e4b` | `['read', 'edit', 'execute', 'search', 'agent', 'web']` *(hook-confined to `task` + `view`)* | Sole user-visible read-only cryptographic hypervisor: verifies `.pipeline_state.json` and Shadow Seal Ledger parity, dispatches synchronous `@Orchestrator` epochs, streams tag-free checkpoint cards, and verifies Stage 5 `PROVENANCE.json` archives. |
| [`orchestrator.agent.md`](./orchestrator.agent.md) | `@Orchestrator` (`hidden`) | `gemma4:e4b` | `['read', 'edit', 'execute', 'search', 'agent', 'web']` *(hook-confined to `task` + read/search)* | Internal `S0V + S0 -> S5` coordinator: follows the deterministic `<stage_checklist>` injected at `subagentStart`, delegates strictly 1:1 to stage specialists via `task(mode="sync")`, reconciles steered `/approve` commands, and halts cleanly at Human Checkpoints (`C0`-`C3`). |
| [`scribe.agent.md`](./scribe.agent.md) | `@Scribe` (`hidden`) | `gemma4:e4b` | `['read', 'edit', 'execute', 'search']` | Stage 0V multimodal Vision/PDF OCR specialist: stages `200 DPI` plates via [`prepare_visual_sources.py`](../skills/technical-perception/scripts/prepare_visual_sources.py), inspects plates 1 at a time with `<vector_glyph_anchor>` hints, and writes TEI P5 `<uncertain>` / `<illegible/>` plate transcripts assembled into `SOURCE_OCR.md`. |
| [`researcher.agent.md`](./researcher.agent.md) | `@Researcher` (`hidden`) | `gemma4:26b-a4b-it-q8_0` | `['read', 'edit', 'search', 'web']` | Stage 0 literature synthesis (`RESEARCH.md`) across [`approved_sources.json`](../hooks/approved_sources.json) + `SOURCE_TEXT.md` / `SOURCE_OCR.md`, and Markovian Stage 1 formal specification extraction (`manifest.json`). |
| [`formalist.agent.md`](./formalist.agent.md) | `@Formalist` (`hidden`) | `gemma4:26b-a4b-it-q8_0` | `['read', 'edit', 'search']` | Stage 2 computational systems architect: lowers `manifest.json` `SPEC-k` equations into typed Python entrypoints, foreign-key `type_mappings`, Design-by-Contract preconditions, and 4-predicate verification obligations in `mapping.json`. |
| [`engineer.agent.md`](./engineer.agent.md) | `@Engineer` (`hidden`) | `gemma4:26b-a4b-it-q8_0` | `['read', 'edit', 'execute', 'search']` | Stage 3 implementation engineer: provisions `./.venv` / `requirements.txt` when needed and authors strictly typed, citation-grounded Python modules from Cleanroom-redacted build sheets (`anchor_test_cases` and `expected` values stripped), escalating unsolvable upstream contracts via `MANIFEST_DEFECT` or `MAPPING_DEFECT`. |
| [`auditor.agent.md`](./auditor.agent.md) | `@Auditor` (`hidden`) | `gemma4:26b-a4b-it-q8_0` | `['read', 'edit', 'execute', 'search']` | Stage 1/2 Local Spec-Critic (`LOCAL_CRITIC.json`) and Stage 4 Global Cleanroom Auditor: authors black-box 4-predicate `verify_*.py` suites (`anchor`, `reference_model`, `relational_law`, and unseeded 500-draw Hypothesis `postcondition`) before issuing `VERIFICATION.md` or `AUDIT_FEEDBACK.json`. |

---

## Key Harness Invariants

1. **Transitive Tool-Category Ceiling & Zero `todo` Schema Bloat:**
   Under Copilot SDK `1.0.108+`, child and grandchild subagents inherit only the intersection of their parent's frontmatter `tools:` categories. `@Facet` and `@Orchestrator` therefore declare the 6-category ceiling (`read`, `edit`, `execute`, `search`, `agent`, `web`) so leaf specialists inherit `create`, `edit`, `bash`, and `web_fetch`, while inline agent hooks (`hypervisor-guard` and `coordinator-guard` in [`pipeline_guards.py`](../hooks/scripts/pipeline_guards.py)) deterministically block direct worker tool calls at Depth 0 and Depth 1. All seven manifests intentionally omit `'todo'` in favor of zero-tool-call deterministic `<stage_checklist>` blocks injected at `subagentStart`.
2. **Single-Model VRAM Residency (`OLLAMA_MAX_LOADED_MODELS=1`):**
   Sharing `gemma4:e4b` across `@Facet`, `@Orchestrator`, and `@Scribe` yields `0.0 s` coordinator/hypervisor handoffs, while unifying `@Researcher`, `@Formalist`, `@Engineer`, and `@Auditor` onto `gemma4:26b-a4b-it-q8_0` eliminates model swaps across `S1 -> S2 -> S3 -> S4` (see [`modelfiles/README.md`](../../modelfiles/README.md)).
3. **User-Scope Synchronization & Model-Tag Preservation:**
   Running `./.venv/bin/python scripts/install_user_level.py` installs these seven `*.agent.md` files into `~/.copilot/agents/` (rewriting `--scope workspace` inline hook paths to `--scope user`) while preserving any custom `model:` identifiers already configured in `~/.copilot/agents/*.agent.md` unless `--force-models` is passed.
4. **Structural Contract Validation:**
   Pathway 1 of [`scripts/validate_system.py`](../../scripts/validate_system.py) verifies YAML frontmatter syntax, quoted `"Use when ..."` discovery descriptions, `# Role:` / `## Constraints` / `## Approach` / `## Output Format` section headers, visibility exclusivity (`facet.agent.md` only), and Clean-Stop token hygiene across every `*.agent.md` file.
