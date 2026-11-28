# Facet Agent Skills (`Layer 3`)

This directory contains Facet's five portable domain skills following the [`agentskills.io`](https://agentskills.io/) convention (`<skill-name>/SKILL.md` paired with declarative JSON Schemas, Markdown/Python templates, and deterministic helper scripts). Each skill scopes domain standards, schemas, and tooling to the pipeline stages that need them; see [`docs/reference/schemas-and-contracts.md`](../../docs/reference/schemas-and-contracts.md) for full schema definitions.

---

## Skill Inventory

| Skill Directory | Pipeline Stages & Agents | Bundled Schemas, Templates & Executable Helpers |
| :--- | :--- | :--- |
| [`spec-pipeline/`](./spec-pipeline/SKILL.md) | `S0V + S0 -> S5` (`@Facet`, `@Orchestrator`, user `/spec-pipeline`) | End-to-end lifecycle contract, slash-command reference (`/approve`, `/status`, `/reset`, `/add-source`), and [`.pipeline_config.json` schema (`pipeline_config.schema.json`)](./spec-pipeline/pipeline_config.schema.json). |
| [`technical-perception/`](./technical-perception/SKILL.md) | `Stage 0V` (`@Scribe`), `Stage 0` & `Stage 1` (`@Researcher`) | [`scripts/prepare_visual_sources.py`](./technical-perception/scripts/prepare_visual_sources.py) (`200 DPI` lossless PNG plate rasterization, Apple `CoreGraphics` `CGPDFScanner` `SOURCE_TEXT.md` + per-plate `<vector_glyph_anchor>` extraction, and asset quarantine), [`research_template.md`](./technical-perception/research_template.md), and [`manifest.json` schema (`schema.json`)](./technical-perception/schema.json). |
| [`formal-mapping/`](./formal-mapping/SKILL.md) | `Stage 2` (`@Formalist`) | Discrete computational type lowering, Design-by-Contract precondition non-strengthening rules, 4-predicate obligation design, and [`mapping.json` schema (`mapping_schema.json`)](./formal-mapping/mapping_schema.json). |
| [`implementation-engineering/`](./implementation-engineering/SKILL.md) | `Stage 3` (`@Engineer`) | Strict typing (`NDArray`, zero unconstrained `Any`), citation/tolerance invariants ([`lint_rules.json`](./implementation-engineering/lint_rules.json)), and AST/JSON surgical patch CLI ([`scripts/code_patch.py`](./implementation-engineering/scripts/code_patch.py): `inspect`, `replace-symbol`, `replace-lines`, `replace-json`). |
| [`verification-auditing/`](./verification-auditing/SKILL.md) | `Stage 1/2 Critic` & `Stage 4` (`@Auditor`, user `/verification-auditing`) | Cleanroom 4-predicate verification templates ([`anchor_oracle_template.py`](./verification-auditing/anchor_oracle_template.py), [`reference_model_template.py`](./verification-auditing/reference_model_template.py), [`relational_law_template.py`](./verification-auditing/relational_law_template.py), [`hypothesis_template.py`](./verification-auditing/hypothesis_template.py), [`sympy_template.py`](./verification-auditing/sympy_template.py)) and [`AUDIT_FEEDBACK.json` schema (`audit_feedback_schema.json`)](./verification-auditing/audit_feedback_schema.json). |

---

## Runtime & User-Scope Conventions

1. **In-Scope Reference Access (`StageWorkingSet`):** Both `.github/skills/` and `$COPILOT_HOME/skills/` are explicitly whitelisted in `compute_stage_working_set()` ([`visual_ocr_and_sources.py`](../hooks/scripts/facet_guards/visual_ocr_and_sources.py)), allowing specialists to inspect JSON schemas and verification templates while remaining isolated from out-of-scope repository folders.
2. **Automatic User-Scope Script Fallback:** [`scripts/install_user_level.py`](../../scripts/install_user_level.py) synchronizes these five skill folders into `~/.copilot/skills/` (marking `*/scripts/*.py` executable with `0o755`), and `rewrite_bash_command_for_workspace()` automatically rewrites `.github/skills/<skill>/scripts/<tool>.py` commands to `$COPILOT_HOME/skills/...` when `@Facet` runs in external workspaces without a local `.github/` folder.
3. **Validation:** [`scripts/validate_system.py`](../../scripts/validate_system.py) verifies all four JSON Schemas, AST soundness/hygiene across every `verification-auditing/*_template.py` file, `Function Map:` / `Section N:` headers in `prepare_visual_sources.py` and `code_patch.py`, and 1:1 enum parity between `pipeline_config.schema.json` and `constants_and_context.py`.
