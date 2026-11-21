# Domain Preset: Machine Learning Architecture & Custom Kernels

## Pipeline Focus
Translating deep learning theory papers, novel attention mechanisms, normalization layers, and custom autograd operators into typed, vectorized PyTorch/NumPy implementations.

## Key Configurations
* **Manifest (`manifest.json`):**
  - `domain_category`: `"Machine Learning Theory & Neural Architecture"`
  - `verification_contract.equivalence_mode`: `"approximate_tolerance"`
  - `verification_contract.rtol`: `1e-5`, `atol`: `1e-6`, `precision`: `"float32"`
  - Dimensions: `[batch_size, seq_len, d_model]`
* **Mapping (`mapping.json`):**
  - Type mappings: `torch.Tensor`, `NDArray[np.float32]`, `bfloat16`
  - Predicate types:
    - `anchor`: Dimension and shape preservation checks
    - `reference_model`: Autograd gradient vs. finite-difference numerical gradient checks
    - `relational_law`: Permutation equivariance / invariance across batch/spatial dimensions
* **Toolchain & Libraries:**
  - Python 3.9+ harness floor (`3.11+` recommended for PyTorch 2), `torch>=2.2.0`, `numpy>=1.26.0`, `hypothesis[numpy]>=6.98.0`
