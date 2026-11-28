---
name: technical-perception
description: Discovers canonical literature for open-ended technical ideas, and extracts quantitative concepts, equations, constraints, and citations from papers, RFCs, and documents into Specification Manifests. Use when exploring technical ideas or reading literature.
license: Apache-2.0
user-invocable: false
---

# Technical Perception & Literature Grounding Skill

## Resources
- Visual/PDF rasterizer (`Stage 0V`): [prepare_visual_sources.py](./scripts/prepare_visual_sources.py)
- Candidate comparison template (`Stage 0`): [research_template.md](./research_template.md)
- Specification Manifest JSON Schema (`Stage 1`): [schema.json](./schema.json)
- Approved domain registry: [approved_sources.json](../../hooks/approved_sources.json)

## Procedures
1. **Mode 0 — Orthogonal PDF Text & 1-Plate-at-a-Time Multimodal OCR (`prepare_visual_sources.py` -> `SOURCE_TEXT.md` + `@Scribe` -> `plates/<stem>.md` -> `SOURCE_OCR.md`):**
   - Stage lossless 200 DPI PNG plates in `.pipeline_scratch/ocr_staging/` via [prepare_visual_sources.py](./scripts/prepare_visual_sources.py) (`--dir research`, `--pdf`, `--image`, `--html`, or `--url`; auto-scales PDF pages `1..N` up to the `32`-page warning cap, overridable via `--max-pages` or `max_visual_pages`). PDFs with embedded text automatically emit normalized `1..N` `SOURCE_TEXT.md` via Apple `CoreGraphics` (`CGPDFScanner`) + `PDFKit` for `@Researcher` while surfacing per-plate math-symbol `<vector_glyph_anchor>` hints to `@Scribe`; `@Scribe` never reads `SOURCE_TEXT.md`, instead inspecting staged plates **strictly 1 plate at a time** via `view` and writing isolated `plates/<plate_stem>.md` files via `create` for multi-plate inputs (deterministically assembled into `SOURCE_OCR.md` once all `M/M` plates validate, or direct `SOURCE_OCR.md` for single-plate inputs) using Facet's 3-tier transcriptional uncertainty markup (`Tier 1`: tag-free LaTeX; `Tier 2`: `<uncertain ...>guess</uncertain>`; `Tier 3`: `<illegible .../>`; cf. TEI P5 §3.5.3 & Ch. 12 `<unclear>`/`<gap>`).
2. **Mode 1 — Candidate Ideation & Self-Contained Dossier (`@Researcher` -> `RESEARCH.md`):**
   - Read local `SOURCE_TEXT.md` / `SOURCE_OCR.md` / `research/*` first when present (plus [research_template.md](./research_template.md) only), supplementing via `web_fetch` on approved domains when `allow_web_research=true`. Populate Section 5 of `RESEARCH.md` with **every closed-form governing and auxiliary sub-equation** (zero undefined RHS symbols), exact variable domains/shapes ($\mathbb{C}^d$ vs. $\mathbb{R}^d$), published anchor reference values/tables, and any `<uncertain>`/`<illegible/>` provenance from `SOURCE_OCR.md` so `RESEARCH.md` is 100% self-contained for Stage 1.
3. **Mode 2 — Stage-Bounded Manifest Extraction (`@Researcher` -> `manifest.json`):**
   - When `RESEARCH.md` exists on disk, compile `manifest.json` **exclusively** from approved `RESEARCH.md` + [schema.json](./schema.json) (without re-reading `SOURCE_TEXT.md`, `SOURCE_OCR.md`, `research/*`, or calling `web_fetch`): assign atomic `SPEC-k` IDs covering both governing identities and auxiliary coefficient/direction definitions, preserve source constants and caller `target_interface` signatures verbatim, propagate `<uncertain>`/`<illegible/>` provenance into `"ambiguities"` + `"epistemic_status": "assumption"`, and set `verification_contract` (`rtol`, `atol`, `equivalence_mode`).
   - Author new files via `create`; apply revisions via scoped `edit`. Dedicate `### Stage Briefing` strictly to the formal contract locked in `manifest.json` (variable domains/roles/units, structural constraints vs. epistemic assumptions/ambiguities, and `rtol`/`atol` conditioning rationale) without repeating Stage 0's source survey or standalone `$$ ... $$` equations.
