# Domain Preset: Scientific & Quantitative Computing

## Pipeline Focus
Translating continuous mathematics, numerical methods, differential equations, and computational physics papers into verified, typed scientific Python code.

## Key Configurations
* **Manifest (`manifest.json`):**
  - `domain_category`: `"Applied Mathematics & Computational Physics"`
  - `verification_contract.equivalence_mode`: `"approximate_tolerance"`
  - `verification_contract.rtol`: `1e-7`, `atol`: `1e-9`, `precision`: `"float64"`
* **Mapping (`mapping.json`):**
  - Type mappings: `float64`, `complex128`, `NDArray[np.float64]`
  - Predicate types: `anchor` (base cases), `reference_model` (naive integration/step loop), `relational_law` (conservation of energy/mass, step-halving convergence)
* **Toolchain & Libraries ([requirements.txt](./requirements.txt)):**
  - Runtime: Python 3.9+ in `./.venv` (`3.11+` recommended)
  - Dependencies: `numpy>=1.26.0`, `scipy>=1.12.0`, `hypothesis>=6.98.0`, `sympy>=1.12`, `pytest>=8.0.0`, `ruff>=0.9.0`, `mypy>=1.10.0`
