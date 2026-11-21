---
name: spec-pipeline
description: Executes the end-to-end grounded technical engineering pipeline from literature/spec to verified code. Covers the complete S0V + S0->S5 lifecycle and human checkpoint gates.
license: Apache-2.0
user-invocable: true
argument-hint: 'Problem statement, literature source, PDF/diagram, or algorithm to implement'
---

# Facet: Specification-Driven Engineering Pipeline

Authoritative lifecycle reference for the two-tier `S0V` + `S0 -> S5` grounded translation pipeline. Gated by `@Facet`, coordinated by `@Orchestrator`, and enforced deterministically by [pipeline-guards.json](../../hooks/pipeline-guards.json) (schema: [pipeline_config.schema.json](./pipeline_config.schema.json)).

## Pipeline Stages & Two-Tier Validation Trajectory

| Stage | Worker Agent | Input | Deliverable (`create` once $\rightarrow$ scoped `edit`) | Local & Global Gate |
| :--- | :--- | :--- | :--- | :--- |
| **Boot** | `@Facet` | `.pipeline_state.json` / `upstream_seals` | Read-only (`view`) | Pre-flight SHA-256 check $\rightarrow$ `@Orchestrator` |
| **S0V** *(Conditional)* | `@Scribe` | PDFs, Images, Visual HTML | `SOURCE_OCR.md` | Plate-view + TEI P5 XML gate $\rightarrow$ `@Researcher` |
| **S0** *(Path B)* | `@Researcher` | Goal / `SOURCE_OCR.md` | `RESEARCH.md` | Citation + LaTeX gate $\rightarrow$ **Checkpoint 0 (`C0`)** |
| **S1** | `@Researcher` $\leftrightarrow$ `@Auditor` | `RESEARCH.md` / `SOURCE_OCR.md` / Spec | `manifest.json` / `LOCAL_CRITIC.json` | Schema + caller parity + optional `STAGE_1_CRITIC` $\rightarrow$ **Checkpoint 1 (`C1`)** |
| **S2** | `@Formalist` $\leftrightarrow$ `@Auditor` | `manifest.json` | `mapping.json` / `LOCAL_CRITIC.json` | Schema + predicate-AST + optional `STAGE_2_CRITIC` $\rightarrow$ **Checkpoint 2 (`C2`)** |
| **S3** | `@Engineer` | `mapping.json` | `requirements.txt` + `.venv` + `<target_module>.py` | AST + entrypoint + anti-leakage gate $\rightarrow$ `S4` |
| **S4** *(Global Audit)* | `@Auditor` | Code + `manifest.json` + `mapping.json` | `verify_*.py` + `VERIFICATION.md` / `AUDIT_FEEDBACK.json` | `pytest` proof $\rightarrow$ **Checkpoint 3 (`C3`)** *(or artifact-preserving rewind $\le K_{\max}$)* |
| **S5** | `pipeline_guards.py` + `@Facet` | `/approve` (or batch) | `specs/<concept_slug>/PROVENANCE.json` | $L_1$ dead-code prune + Merkle seal $\rightarrow$ `PRE_RUN_HALT` |

## Contractual Slash Commands
- `/approve` (or `/approve <target>`): Seals the active Human Checkpoint (`C0`–`C3`) and advances the pipeline.
- `/status`: Emits an instant zero-tool status pulse of active drafts, stage, and cryptographic seals.
- `/reset`: Archives in-progress drafts into `specs/<slug>_interrupted_<ts>/` and cleans the workspace root.
- `/add-source [--workspace] <domain_or_url>`: Authorizes research domains in [approved_sources.json](../../hooks/approved_sources.json) or user scope.

