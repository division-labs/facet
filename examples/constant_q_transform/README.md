# Invertible Constant-Q Transform (`arXiv:2609.29119v1` — Open-Access Multi-Page PDF Example)

This example bundles a 5-page open-access signal-processing and numerical-analysis paper containing multi-column prose, display equations (`CMEX` operators & fractions), 2 vector figures, and 2 benchmark tables to exercise Facet's orthogonal dual-channel PDF pipeline (`prepare_visual_sources.py` -> `CGPDFScanner` `SOURCE_TEXT.md` + 1-plate-at-a-time `@Scribe` `SOURCE_OCR.md` -> Stage 5 `PROVENANCE.json`).

## Source Attribution & Open-Access License (`CC BY 4.0`)

- **Title:** *Exact Factorisation and Fast Computation of Invertible Constant-Q Transforms*
- **Authors:** Facundo Franchino, Eloi Moliner, and Vesa Valimaki (2026)
- **Identifier / DOI:** [arXiv:2609.29119v1](https://arxiv.org/abs/2609.29119v1) · [https://doi.org/10.48550/arXiv.2609.29119](https://doi.org/10.48550/arXiv.2609.29119)
- **File & SHA-256:** [`2609.29119v1.pdf`](./2609.29119v1.pdf) (`c66660176344b61ff627e9c7da2d2f2ac5f8bb29751140abda5dd08b8ccc5f92`, `260,361` bytes, `5` pages)
- **License:** Distributed under the [Creative Commons Attribution 4.0 International License (`CC BY 4.0`)](https://creativecommons.org/licenses/by/4.0/) as embedded in the PDF `/Info` dictionary (`/License (http://creativecommons.org/licenses/by/4.0/)`). No modifications were made to the bundled PDF.

## Canonical Prompt

Invoke `@Facet` in VS Code Chat (explicitly referencing `examples/constant_q_transform/2609.29119v1.pdf`, or after copying the PDF into `<workspace>/research/`):

```text
@Facet Delve into examples/constant_q_transform/2609.29119v1.pdf and derive, implement, and verify the core method proposed in the paper.
```
