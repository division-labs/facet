---
name: implementation-engineering
description: Generates typed, production-grade technical and numerical Python implementations from Mapping Documents with strict type hints, precondition assertions, and literature/specification citations. Use when implementing quantitative or technical algorithms in Python.
license: Apache-2.0
user-invocable: false
---

# Technical Implementation Engineering Skill

## Resources
- Static typing & AST lint rules: [lint_rules.json](./lint_rules.json)
- Structural AST/line/JSON patcher: [code_patch.py](./scripts/code_patch.py) (`inspect`, `replace-symbol`, `replace-lines`, `replace-json`)

## Procedure (`mapping.json` $\rightarrow$ `<target_module>.py`)
1. **Environment (`requirements.txt` + `./.venv`):** Pin version-bounded PEP 508 packages (`numpy`, `scipy`, `pytest`, `hypothesis`) and install into `./.venv/bin/pip` when not yet satisfied in `<workspace_map>`.
2. **Typed Implementation:**
   - Write strictly to `public_interface.target_module` with a raw `r"""..."""` Line-1 citation docstring before `from __future__ import annotations`, explicit `typing` / `NDArray[np.float64]` annotations (zero unconstrained `Any`), `# Implements SPEC-k` inline comments, and **only** the structural/singularity guards declared in `mapping.json` `precondition_assertions`.
3. **Scoped Repair & Inline Smoke Check:**
   - Author `<target_module>.py` once via `create`; on local or Stage 4 repairs, patch only affected symbols/lines via `edit` or [code_patch.py](./scripts/code_patch.py) without hardcoding test vectors.
   - Verify `anchor_test_cases` inline via `./.venv/bin/python -c "..."` (never create `test_*.py`, `verify_*.py`, or `smoke_*.py`).
