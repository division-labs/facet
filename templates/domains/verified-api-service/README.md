# Domain Preset: Verified API & Microservice Engineering

## Pipeline Focus
Translating formal domain models, business rule specifications, and OpenAPI contracts into typed service backends with stateful property-based testing.

## Key Configurations
* **Manifest (`manifest.json`):**
  - `domain_category`: `"Domain-Driven Design & Microservice Specification"`
  - `verification_contract.equivalence_mode`: `"exact_equality"`
* **Mapping (`mapping.json`):**
  - Type mappings: Pydantic v2 models, UUID, Decimal, UTC Datetime
  - Predicate types:
    - `anchor`: Standard HTTP CRUD boundary checks
    - `relational_law`: Idempotence on repeated PUT/DELETE requests (`f(f(s)) == f(s)`)
    - `postcondition`: Stateful business rule invariant preservation (e.g. account balances never negative)
* **Toolchain & Libraries:**
  - `fastapi`, `pydantic>=2.6.0`, `hypothesis[stateful]`, `httpx`
