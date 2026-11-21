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
   - Formulate `verify_<slug>.py` from `manifest.json` and `mapping.json` (`public_interface` + `verification_obligations`) before inspecting implementation internals, covering `anchor`, `reference_model`, `relational_law`, and `postcondition` (`@given` Hypothesis fuzzing; `pytest.raises` checks strictly when `precondition_assertions` is non-empty).
   - Enforce Line-1 docstrings before `from __future__ import annotations`, `-> None` annotations, `math.isclose`/`np.allclose` tolerances, and multi-argument `lambda t, y: ...` callables.
3. **Execution Proof & Triage:**
   - Run `./.venv/bin/python -m pytest verify_<slug>.py` via `bash` before writing plain-ASCII `VERIFICATION.md`.
   - On failure, write `AUDIT_FEEDBACK.json` with `failure_classification` in `{"MANIFEST_DEFECT", "MAPPING_DEFECT", "IMPLEMENTATION_DEFECT"}` so the responsible specialist repairs its existing artifact via scoped `edit`.
