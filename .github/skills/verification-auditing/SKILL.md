---
name: verification-auditing
description: Rigorous technical verification comparing implementation code against theory via bidirectional verification obligations (anchor vectors, pseudo-oracles, relational metamorphic laws, and property fuzzing). Use when proving or validating technical implementations against specifications.
license: Apache-2.0
user-invocable: true
argument-hint: 'Implementation file or active specification to verify'
---

# Technical Verification & Auditing Skill

## Resources
- Audit failure schema: [audit_feedback_schema.json](./audit_feedback_schema.json)
- Cleanroom templates: [anchor_oracle_template.py](./anchor_oracle_template.py), [reference_model_template.py](./reference_model_template.py), [relational_law_template.py](./relational_law_template.py), [hypothesis_template.py](./hypothesis_template.py), [sympy_template.py](./sympy_template.py)

## Protocol
1. **Local Spec-Critic (`STAGE_1_CRITIC` / `STAGE_2_CRITIC`):** Verify semantic fidelity of `manifest.json` or `mapping.json` against source formulas and caller signatures, writing `LOCAL_CRITIC.json` (`PASS` or `FAIL`) so upstream stages fix spec errors via scoped `edit` before Stage 3.
2. **Cleanroom 4-Predicate Suite (`STAGE_4_VERIFICATION`):**
   - Formulate `verify_<slug>.py` strictly from `manifest.json`, `mapping.json` (`public_interface` + `verification_obligations`), and `<hydrated_public_surface>` stubs without inspecting implementation internals during Cleanroom test authoring, covering `anchor`, `reference_model`, `relational_law`, and `postcondition` (`@given` Hypothesis fuzzing; `pytest.raises` checks strictly when `precondition_assertions` is non-empty).
   - Enforce Line-1 docstrings before `from __future__ import annotations`, `-> None` annotations, `from hypothesis.extra.numpy import arrays` (never `st.arrays`), `.filter(lambda k: ...)` (never `filtered=...`), `@st.composite def ...(draw: st.DrawFn) -> tuple[...]:` (never `Any`), explicit finite bounds on all Hypothesis `st.floats(min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False)` strategies with well-conditioned non-subnormal separations (e.g. `assume(b - a >= 1e-6)` on continuous intervals `[a, b]` rather than bare `assume(a < b)` which shrinks to `5e-324` subnormals; never unbounded `st.floats()` across `1e-308..1e+308`, and never `@seed(...)` or `derandomize=True` seed-pinning), gauge-invariant assertions (testing defining equations $F(x, y) = 0$, sorted spectra, and subspace projectors rather than raw phase/sign-ambiguous basis vectors across independent calls), norm-scaled `np.allclose(..., rtol=..., atol=...)` or `math.isclose(..., rel_tol=..., abs_tol=...)` tolerances (never `math.isclose(..., rtol=...)`), and multi-argument `lambda t, y: ...` callables.
3. **Execution Proof & Two-Phase Triage:**
   - Run `./.venv/bin/python -m pytest verify_<slug>.py` via `bash` before writing plain-ASCII `VERIFICATION.md`.
   - Implementation source reads are Cleanroom-locked before `pytest`; if `pytest` fails on well-conditioned inputs, `two_phase_post_pytest` unlocks `<target_module>.py` read access strictly for root-cause triage between `MANIFEST_DEFECT`, `MAPPING_DEFECT`, `IMPLEMENTATION_DEFECT`, and `TEST_HARNESS_DEFECT` in `AUDIT_FEEDBACK.json`.
