# Facet: Workspace Map & Engineering Invariants

## 1. Repository Map (Progressive Disclosure)
* **Control Plane & Hooks:** `.github/hooks/scripts/pipeline_guards.py` and `.github/hooks/scripts/facet_guards/` (9-layer acyclic FSM gates, SHA-256 seals, `<workspace_map>` injection, local & global validation).
* **Agents & Skills:** `.github/agents/*.agent.md` (personas) and `.github/skills/*/` (schemas, templates, `prepare_visual_sources.py`, `code_patch.py`).
* **Inputs & Outputs:** `research/` (read-only user inputs); workspace root (active cycle drafts); `specs/<concept_slug>/` (sealed `PROVENANCE.json` archives — root stays clean after Stage 5).

## 2. Tool & Scoped-Edit Protocol
* **Path-First & Space Hygiene:** Pass `"path"` (absolute path) **first** in `view`, `create`, and `edit`. In `bash`, invoke workspace-relative `./.venv/bin/python` with non-interactive flags (`git --no-pager`, `less -F`).
* **Create Once, Scoped Edit Thereafter:** Author new deliverables once via `create(path, file_text)`. Preserve existing artifacts on disk across local/global validation retries and apply minimal targeted replacements via `edit(path, old_str, new_str)` or `code_patch.py` (`replace-symbol`, `replace-lines`, `replace-json`) rather than regenerating whole files. Never repeat an identical failing `edit` without re-reading via `view`.

## 3. Quantitative Typing, Preconditions & Traceability
* **Strict Typing & Tolerances:** Include `from __future__ import annotations`, explicit `typing` / `numpy.typing` (`NDArray[np.float64]`), and zero unconstrained `Any`. Compare floats exclusively with `numpy.allclose(..., rtol=..., atol=...)` or `math.isclose(..., rel_tol=..., abs_tol=...)` (never `==` or `math.isclose(..., rtol=...)`).
* **Design-by-Contract Preconditions:** Assert structural $O(1)$/$O(N)$ shape, finiteness, discrete framing, caller-declared bounds, and genuine domain singularities at function entry; record theoretical convergence or spectral sufficiency hypotheses in `mapping.json` `assumptions` without narrowing caller interfaces or adding $O(N^3)$ entry checks.
* **Literature Citations & Local Execution:** Cite defining equations/sections in module and function docstrings plus `# Implements SPEC-k` comments, anchor state in `.pipeline_state.json`, and delegate strictly to local Gemma subagents.

