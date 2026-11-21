# Facet Reference Examples

This directory contains visual Quickstart inputs and complete reference specifications for Facet.

## Included Examples

- [examples/magical_mystery/](magical_mystery/): Bundled visual equation photograph ([IMG_1598.jpg](magical_mystery/IMG_1598.jpg)) exercising the full `Stage 0V (@Scribe Vision OCR) -> Stage 5 (Archival)` lifecycle end to end. Try it in VS Code Chat with:
  ```text
  @Facet What can you see through your crystalline lens if you peer into the examples folder about the magical mystery image?
  ```
- [examples/mandelbrot_set/](mandelbrot_set/): A complete formal specification and mapping document for Mandelbrot Set escape dynamics, illustrating:
  - `RESEARCH.md`: Literature brief anchored in Mandelbrot (1982) and Douady and Hubbard (1984).
  - `manifest.json`: Formal Specification Manifest declaring `SPEC-1` (quadratic map recurrence) and `SPEC-2` (escape radius criterion).
  - `mapping.json`: Discrete mapping document declaring `public_interface`, `type_mappings`, `precondition_assertions`, `anchor_test_cases`, and 4-predicate `verification_obligations` (`OBL-1`, `OBL-2`, `OBL-3`).
  - `requirements.txt`: Pinned dependencies required for execution and testing.
