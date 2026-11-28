# Research Brief: [Concept / Problem Title]

## 1. Problem Statement
[Concise formal description of the technical or mathematical problem, including inputs, desired outputs, and core computational challenges.]

## 2. Candidate Comparison Table

| Algorithm / Method | Canonical Literature Citation | Verified Link / Reference | Governing Mathematical Formulation | Complexity & Constraints | Trade-offs & Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **[Algorithm 1]** | [Title, Authors, Year] | [arXiv URL / DOI / RFC / Textbook] | $\displaystyle [LaTeX formula]$ | Time: $O(...)$, Space: $O(...)$<br>Constraints: [e.g. $N$ even, $x > 0$] | [Numerical stability, edge cases, implementation effort] |
| **[Algorithm 2]** | [Title, Authors, Year] | [arXiv URL / DOI / RFC / Textbook] | $\displaystyle [LaTeX formula]$ | Time: $O(...)$, Space: $O(...)$<br>Constraints: [...] | [Trade-offs] |
| **[Algorithm 3]** | [Title, Authors, Year] | [arXiv URL / DOI / RFC / Textbook] | $\displaystyle [LaTeX formula]$ | Time: $O(...)$, Space: $O(...)$<br>Constraints: [...] | [Trade-offs] |

## 3. Detailed Candidate Breakdown

### 1. [Algorithm 1 Name]
* **Canonical Literature Reference:** [Complete bibliographic citation: Authors, Title, Publication/Journal/Book, Year, DOI/arXiv link or Book Chapter/Section (e.g. *Burden & Faires, Numerical Analysis 9th ed., Section 4.4*; *Press et al., Numerical Recipes in C, Section 4.2*; *RFC-8446 Section 4.1.2*)].
* **Governing Mathematical Formulation:**
  $$\displaystyle [Explicit LaTeX formulation]$$
* **Core Computational Mechanism:** [Step-by-step description of how the mathematical expression translates to algorithm steps].
* **Preconditions & Domain Invariants:** [Dimensional shapes, valid parameter ranges, conservation properties, and stability bounds].

### 2. [Algorithm 2 Name]
* **Canonical Literature Reference:** [Complete bibliographic citation].
* **Governing Mathematical Formulation:**
  $$\displaystyle [Explicit LaTeX formulation]$$
* **Core Computational Mechanism:** [Mechanism description].
* **Preconditions & Domain Invariants:** [Preconditions and bounds].

### 3. [Algorithm 3 Name]
* **Canonical Literature Reference:** [Complete bibliographic citation].
* **Governing Mathematical Formulation:**
  $$\displaystyle [Explicit LaTeX formulation]$$
* **Core Computational Mechanism:** [Mechanism description].
* **Preconditions & Domain Invariants:** [Preconditions and bounds].

## 4. Recommended Approach & Justification
* **Primary Recommendation:** [Selected algorithm name].
* **Technical Justification:** [Why this approach best satisfies precision, stability, and complexity requirements].
* **Pre-Manifest Handoff Readiness:** [Confirms all closed-form sub-equations, variable domains, anchor benchmarks, and epistemic uncertainties below are complete so Stage 1 compiles `manifest.json` strictly from `RESEARCH.md`].

## 5. Complete Specification Extraction Dossier (Self-Contained Basis for `manifest.json`)
* **Complete Closed-Form Equations (`SPEC-1..SPEC-k`):**
  [Write out EVERY governing equation AND every auxiliary closed-form sub-formula—coefficients, direction/grid vectors, index sets, recurrences, boundary conditions, and normalizations—explicitly in LaTeX `$$ ... $$` so ZERO symbols on any right-hand side remain undefined prose.]
* **Variable, Domain & Shape Ledger:**
  [List every input, parameter, internal state, and output symbol with its exact mathematical domain (e.g., $\mathbb{C}^d$ vs. $\mathbb{R}^d$, $\mathbb{N}_0^d$), array/tensor shape, role (`input` / `parameter` / `internal` / `output`), and unit.]
* **Structural Preconditions vs. Assumptions & Epistemic Uncertainties:**
  [Separate $O(1)/O(N)$ caller-checkable structural constraints from theoretical regularity/convergence assumptions, and carry forward any `<uncertain>` or `<illegible/>` provenance items from `SOURCE_OCR.md`.]
* **Published Anchor Reference Values & Verification Conditioning:**
  [Record exact numerical reference values/tables reported in the source (when present) and justify recommended `rtol`, `atol`, and `equivalence_mode` from floating-point conditioning.]

