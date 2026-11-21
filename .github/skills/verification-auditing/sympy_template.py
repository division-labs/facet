"""Symbolic algebraic verification template using SymPy (Meurer et al. 2017).

Evaluates exact symbolic expressions with SymPy and checks numerical agreement
against a candidate Python implementation over sampled points.

References:
    Meurer, A., et al. (2017). SymPy: symbolic computing in Python. PeerJ
    Computer Science, 3:e103. https://doi.org/10.7717/peerj-cs.103

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Sequence, Tuple
import numpy as np
from numpy.typing import NDArray
import sympy as sp


def verify_symbolic_equivalence(
    impl_func: Callable[..., float],
    sympy_expr: sp.Expr,
    symbols_list: Sequence[sp.Symbol],
    sample_ranges: Mapping[str, Tuple[float, float]],
    rtol: float = 1e-7,
    atol: float = 1e-9,
    num_samples: int = 50,
) -> Dict[str, object]:
    """Compare ``impl_func`` outputs against an evaluated SymPy expression.

    Args:
        impl_func: Callable Python function under test.
        sympy_expr: SymPy expression representing the exact ground-truth formula.
        symbols_list: Ordered SymPy symbols matching ``impl_func`` arguments.
        sample_ranges: Mapping from symbol name to ``(min_val, max_val)``.
        rtol: Relative tolerance from the Specification Manifest.
        atol: Absolute tolerance from the Specification Manifest.
        num_samples: Number of uniform random sample evaluations.

    Returns:
        Summary dictionary with ``passed`` (bool), ``total_samples`` (int),
        ``failure_count`` (int), and up to 5 ``failures`` counterexamples.
    """
    # Compile the symbolic formula into a fast NumPy-backed callable function.
    lambdified_expr = sp.lambdify(symbols_list, sympy_expr, modules=["numpy"])

    # Use a fixed PRNG seed so symbolic equivalence checks are 100% reproducible.
    rng = np.random.default_rng(42)
    sample_data: Dict[str, NDArray[np.float64]] = {}
    for sym in symbols_list:
        # Draw `num_samples` uniform test values within each symbol's allowed range.
        low, high = sample_ranges.get(str(sym), (0.1, 10.0))
        sample_data[str(sym)] = rng.uniform(low, high, size=num_samples).astype(np.float64)

    failures: List[Dict[str, object]] = []
    # Step through `range(num_samples)` (`i`) to update or verify each item.
    for i in range(num_samples):
        # Assemble the ordered argument list for sample `i` and compare outputs.
        inputs = [float(sample_data[str(sym)][i]) for sym in symbols_list]
        expected = float(lambdified_expr(*inputs))
        actual = float(impl_func(*inputs))

        # Flag any sample where numerical error exceeds `(rtol, atol)`.
        if not np.isclose(actual, expected, rtol=rtol, atol=atol):
            failures.append({
                "sample_index": i,
                "inputs": {str(sym): inputs[idx] for idx, sym in enumerate(symbols_list)},
                "expected_symbolic": expected,
                "actual_implementation": actual,
                "abs_diff": abs(actual - expected),
                "rel_diff": abs(actual - expected) / (abs(expected) + 1e-15),
            })

    return {
        "passed": len(failures) == 0,
        "total_samples": num_samples,
        "failure_count": len(failures),
        "failures": failures[:5],
    }
