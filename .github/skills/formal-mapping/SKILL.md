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
1. **Public Interface (`public_interface`):** Define `target_module` (ending in `.py`) and typed `entrypoints` honoring caller-mandated names, parameter counts, and variadic `*args` / `**kwargs` (`var_positional` / `var_keyword`).
2. **Types & Design-by-Contract Preconditions:**
   - Map every `manifest.json` variable (`role: input | parameter`) across `entrypoints[*].parameters` or `type_mappings`, preserving dimensional `unit` tags.
   - Populate `precondition_assertions` strictly with structural $O(1)$/$O(N)$ shape, finiteness, discrete framing, caller-stated bounds, and genuine domain singularities (allow `[]` when total over typed inputs). Record theoretical convergence/spectral sufficiency hypotheses in `assumptions` rather than narrowing caller domains or adding $O(N^3)$ entry assertions.
3. **Hughes 4-Predicate Obligations (`verification_obligations`):**
   - Cover every `SPEC-k` across `anchor` (16–17 digit `float64` RFC 8259 primitives), `reference_model`, `relational_law`, and `postcondition` using `np.allclose(..., rtol=rtol, atol=atol)` or `math.isclose` and coupled multi-parameter expressions.
4. **Persistence:** Author `mapping.json` once via `create`; apply revisions via scoped `edit` or `code_patch.py replace-json`.
