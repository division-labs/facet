---
name: formal-mapping
description: Translates abstract quantitative formulas, mathematical concepts, and Specification Manifests into unambiguous computational structures, data types, preconditions, public interfaces, and verification obligations. Use when translating theoretical models into algorithm designs.
license: Apache-2.0
user-invocable: false
---

# Formal Mapping & Computational Discretization Skill

## Resources
- Mapping Document JSON Schema: [mapping_schema.json](./mapping_schema.json)

## Procedure (`manifest.json` $\rightarrow$ `mapping.json`)
1. **Public Interface (`public_interface`):** Consume `<hydrated_artifact name="manifest.json">` directly from `<subagent_context>` when present (call `view` only if unhydrated or revising), and define `target_module` (ending in `.py`) plus typed `entrypoints` honoring caller-mandated names, parameter counts, and variadic `*args` / `**kwargs` (`var_positional` / `var_keyword`).
2. **Types & Design-by-Contract Preconditions:**
   - Include every `manifest.json` variable (`role: input | parameter`) verbatim in `type_mappings[*].symbol` (mapping each mathematical symbol such as `\Delta t`, `q_i^n`, or `g_{\lambda}` to its computational type while keeping Python parameter names in `entrypoints[*].parameters` idiomatic, e.g. `dt`, `windows`), preserve dimensional `unit` tags, and never collapse standalone parameters into an opaque unparameterized `dict`, `Dict[str, Any]`, or bare `list`/`tuple` (use explicit typed parameters or parameterized generics such as `NDArray[np.float64]`, `list[NDArray[np.complex128]]`, `tuple[int, int]`). Ensure cross-entrypoint `formal_predicate` calls compose with matching parameter/return types (`mypy`-checked).
   - Populate `precondition_assertions` strictly with **executable Python boolean expressions** (`ast.parse(..., mode="eval")`, e.g. `"isinstance(n, int) and n >= 1"` — NEVER English prose or LaTeX/math symbols) covering structural $O(1)$/$O(N)$ shape, finiteness, discrete framing, caller-stated bounds, and genuine domain singularities (allow `[]` when total over typed inputs). Record theoretical convergence/spectral sufficiency hypotheses in `assumptions` rather than narrowing caller domains or adding $O(N^3)$ entry assertions.
3. **4-Predicate Obligations (`verification_obligations`):**
   - Cover every `SPEC-k` across `anchor` (16–17 digit `float64` RFC 8259 primitives), `reference_model`, `relational_law`, and `postcondition` using `np.allclose(..., rtol=rtol, atol=atol)` or `math.isclose` and coupled multi-parameter expressions.
   - Prefer gauge-invariant, backward-stable numerical residuals (e.g. eigenpair residual $\|M v - \lambda v\|_2 \le \text{tol} \cdot \max(1, \|M\|_F)\|v\|_2$, trace/Schur spectrum invariants, or subspace projectors rather than raw $\det(M - \lambda I) = 0$ or unaligned raw basis-vector comparisons across independent solver calls) and align `NDArray[np.float64]` vs. `NDArray[np.complex128]` return types with symmetric vs. general real matrix inputs.
4. **Persistence:** Author `mapping.json` once via `create`; apply revisions via scoped `edit` or `code_patch.py replace-json`.
