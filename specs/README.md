# Archived Specifications Directory (`specs/`)

This directory stores self-contained, version-controlled specification packages produced by Facet upon Stage 5 verification sign-off (`archive_completed_run()`).

## Specification Package Structure

Each concept directory under `specs/<concept_slug>/` forms an immutable cryptographic bundle containing:

| Artifact | Responsibility | Description |
| :--- | :--- | :--- |
| **`SOURCE_OCR.md`** | `@Scribe` *(Optional)* | Verbatim Stage 0V mathematical and algorithmic transcription of PDFs, diagrams, or visual web pages with TEI P5 `<uncertain>` / `<illegible/>` epistemic provenance. |
| **`RESEARCH.md`** | `@Researcher` | Grounded research brief detailing candidate algorithms, governing mathematical formulations, and canonical literature citations (arXiv, DOI, RFC, or textbook). |
| **`manifest.json`** | `@Researcher` | Formal `Specification Manifest` declaring canonical definitions (`SPEC-k`), domain variables, constraints, `ambiguities`, and the `verification_contract`. |
| **`mapping.json`** | `@Formalist` | Computational `Mapping Document` declaring `public_interface`, type mappings, precondition assertions, and `verification_obligations` (`OBL-j`). |
| **`<target_module>.py`** | `@Engineer` | Verified production Python module(s) implementing `public_interface` from `mapping.json`, moved into `specs/<concept_slug>/` upon Stage 5 sign-off. |
| **`requirements.txt`** | `@Engineer` | Pinned PEP 508 dependencies moved into `specs/<concept_slug>/` alongside the verified implementation and oracle suite. |
| **`verify_*.py`** | `@Auditor` | Cleanroom verification oracle suite executed across all `OBL-j` obligations. |
| **`VERIFICATION.md`** | `@Auditor` | Dual-track verification report documenting clean-room test execution, tolerances tested, and counter-example analyses. |
| **`README.md`** | `pipeline_guards.py` | Auto-generated Specification & Verification Dossier (`0` LLM tokens) featuring a Mermaid `SPEC -> API -> OBL` traceability graph, display KaTeX equations, Python interface signatures, one-command `pytest` reproduction, and SHA-256 ledger. |
| **`hook_state_snapshot.json`** | `pipeline_guards.py` | Whitelisted 7-key snapshot of root/subagent hook tracking state captured and hashed prior to sealing `PROVENANCE.json`. |
| **`PROVENANCE.json`** | `pipeline_guards.py` | Cryptographic audit trail recording SHA-256 hashes for all deliverables, the complete handshake history across worker agents, and any self-repair iterations. |
| **`AUDIT_HISTORY.json`** | `@Auditor` | *(Optional)* Preserved ledger of any Stage 3 code repairs or Stage 1/2 upstream specification rewinds (`MANIFEST_DEFECT` / `MAPPING_DEFECT`) executed prior to final pass. |
| **`scratch_traces/`** | `pipeline_guards.py` | *(Optional)* Preserved per-agent reasoning traces when `scratch_retention` is set to `"archive-trace"`. |

---

## Compositional Reuse & Cycle-2 Chaining

Sealed packages inside `specs/<concept_slug>/` can be reused directly in subsequent `@Facet` cycles:
1. **Prompt & Semantic Chaining:** Referencing `specs/<concept_slug>` (or enabling `enable_post_archive_embed: true` for semantic `knowledge.db` recall) automatically injects the package's dossier, `manifest.json`, verified module path, and `PROVENANCE.json` into `@Orchestrator`'s Cycle-2 context.
2. **Typed Cross-Archive Imports (`upstream_modules`):** Declaring `{"spec_slug": "<concept_slug>", "module": "<target_module>.py"}` in `mapping.json`'s `upstream_modules` array deterministically verifies `specs/<concept_slug>/PROVENANCE.json` and injects `specs/<concept_slug>` into Cleanroom `PYTHONPATH` during Stage 3 and Stage 4 execution.

---

## Workspace State Invariant

During active pipeline execution, working specification files (`SOURCE_OCR.md`, `RESEARCH.md`, `manifest.json`, `mapping.json`), verification scripts (`verify_*.py`), `requirements.txt`, and production source code (`*.py`) reside in the workspace root. Upon user approval at Checkpoint 3 (`/approve`), `archive_completed_run()` moves all run deliverables—verified production `*.py` module(s), `requirements.txt`, specification files, and `verify_*.py` scripts—into `specs/<concept_slug>/`, snapshots `hook_state_snapshot.json`, and seals `PROVENANCE.json`, leaving the workspace root completely clean for the next cycle.
