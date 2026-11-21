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
1. **Mode 0 — Multimodal OCR (`@Scribe` -> `SOURCE_OCR.md`):**
   - Stage 180 DPI plates in `.pipeline_scratch/ocr_staging/` via [prepare_visual_sources.py](./scripts/prepare_visual_sources.py) (`--dir research`, `--pdf`, `--image`, `--html`, or `--url`), inspect plates via `view` first, and apply 3-Tier TEI P5 provenance (`Tier 1`: tag-free LaTeX; `Tier 2`: `<uncertain ...>guess</uncertain>`; `Tier 3`: `<illegible .../>`).
2. **Mode 1 — Candidate Ideation (`@Researcher` -> `RESEARCH.md`):**
   - Read local `SOURCE_OCR.md` / `research/*` first when present (plus [research_template.md](./research_template.md) only), supplementing via `web_fetch` on approved domains when `allow_web_research=true`. Include canonical author/year/venue citations and governing `$$ ... $$` LaTeX formulas for every candidate.
3. **Mode 2 — Manifest Extraction (`@Researcher` -> `manifest.json`):**
   - Conform strictly to [schema.json](./schema.json): assign `SPEC-k` IDs, preserve source constants and caller `target_interface` signatures verbatim, propagate `<uncertain>`/`<illegible/>` tags into `"ambiguities"` + `"epistemic_status": "assumption"`, and set `verification_contract` (`rtol`, `atol`, `equivalence_mode`).
   - Author new files via `create`; apply revisions via scoped `edit`.
