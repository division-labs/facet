# Facet Reference Examples

This directory contains visual Quickstart inputs and complete reference specifications for Facet.

## Canonical `research/` Auto-Scan vs. Explicit `examples/` Promotion

Facet restricts automatic corpus scanning strictly to `<workspace>/research/` (which is `.gitignore`d and preserved across `/reset`) so unrelated runs never accidentally ingest bundled example images or UI assets, while automatically promoting any workspace path or `examples/` subfolder you explicitly reference in your prompt (`dual_tier_explicit_promotion`).

## Included Examples

- [examples/magical_mystery/](magical_mystery/): Bundled visual equation photograph ([IMG_1598.jpg](magical_mystery/IMG_1598.jpg)) exercising the full `Stage 0V (@Scribe Vision OCR) -> Stage 5 (Archival)` lifecycle end to end. Invoke `@Facet` directly in VS Code Chat (or copy files into `research/` for multi-file runs):
  ```text
  @Facet What can you see through your crystalline lens if you peer into the examples folder about the magical mystery image?
  ```
- [examples/constant_q_transform/](constant_q_transform/): Bundled 5-page `CC BY 4.0` open-access PDF paper ([2609.29119v1.pdf](constant_q_transform/2609.29119v1.pdf) — *Franchino, Moliner & Valimaki, 2026*, 2 figures, 2 tables; [attribution & details](constant_q_transform/README.md)) exercising Facet's orthogonal PDF pipeline (`CGPDFScanner` `SOURCE_TEXT.md` + 1-plate-at-a-time `@Scribe` `SOURCE_OCR.md` -> closed-book `Stage 0` -> Markovian `Stage 1` -> `Stage 5 PROVENANCE.json`):
  ```text
  @Facet Delve into examples/constant_q_transform/2609.29119v1.pdf and derive, implement, and verify the core method proposed in the paper.
  ```
- [examples/mandelbrot_set/](mandelbrot_set/): A complete formal specification and mapping document for Mandelbrot Set escape dynamics, illustrating:
  - `RESEARCH.md`: Literature brief anchored in Mandelbrot (1982) and Douady and Hubbard (1984).
  - `manifest.json`: Formal Specification Manifest declaring `SPEC-1` (quadratic map recurrence) and `SPEC-2` (escape radius criterion).
  - `mapping.json`: Discrete mapping document declaring `public_interface`, `type_mappings`, `precondition_assertions`, `anchor_test_cases`, and 4-predicate `verification_obligations` (`OBL-1`, `OBL-2`, `OBL-3`).
  - `requirements.txt`: Pinned dependencies required for execution and testing.
