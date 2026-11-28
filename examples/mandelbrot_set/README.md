# Reference Example: Mandelbrot Set Iteration and Escape Dynamics

This reference bundle illustrates the specification artifacts produced across Stages 0 through 2 alongside the Stage 3 environment manifest in the Facet pipeline:

- `RESEARCH.md`: Stage 0 literature brief (`@Researcher`) anchored in canonical sources (Benoit Mandelbrot, 1982, *The Fractal Geometry of Nature*; Adrien Douady and John H. Hubbard, 1984, *Étude dynamique des polynômes complexes*).
- `manifest.json`: Stage 1 Specification Manifest (`@Researcher`) defining formal specifications and invariants (`SPEC-1` and `SPEC-2`).
- `mapping.json`: Stage 2 discretized Mapping Document (`@Formalist`) defining the Python public interface (`mandelbrot.py` -> `mandelbrot_escape`), type mappings, precondition assertions, anchor test cases, `suggested_packages`, and verification obligations across Hughes's 4-predicate basis (`anchor`, `relational_law`, `postcondition`).
- `requirements.txt`: Stage 3 PEP 508 dependency manifest (`@Engineer`) pinning runtime, formatting (`ruff`), and verification packages.

