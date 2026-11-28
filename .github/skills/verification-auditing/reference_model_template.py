"""Differential testing template against a direct reference model.

Compares an optimized or vectorized production implementation against a simple,
transparent reference implementation over bounded inputs using pseudo-oracle
differential testing (Weyuker, 1982; McKeeman, 1998; Zhang et al., 2023).

References:
    Weyuker, E. J. (1982). On testing non-testable programs. The Computer
    Journal, 25(4), 465-470. https://doi.org/10.1093/comjnl/25.4.465
    McKeeman, W. M. (1998). Differential testing for software. Digital Technical
    Journal, 10(1), 100-107.
    Zhang, K., Wang, D., Xia, J., Wang, W. Y., & Li, L. (2023). ALGO:
    Synthesizing algorithmic programs with LLM-generated oracle verifiers.
    NeurIPS 2023. https://arxiv.org/abs/2305.14591

Function Map:
  - Section 1: Pseudo-Oracle Reference Model Comparator (`verify_against_reference_model`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Sequence, Tuple
import numpy as np


# ============================================================================
# Section 1: Pseudo-Oracle Reference Model Comparator
# ============================================================================

def verify_against_reference_model(
    impl_func: Callable[..., object],
    ref_func: Callable[..., object],
    input_generator: Callable[[], Tuple[Sequence[object], Mapping[str, object]]],
    num_trials: int = 100,
    rtol: float = 1e-7,
    atol: float = 1e-9,
) -> Dict[str, object]:
    """Run differential testing comparing ``impl_func`` against ``ref_func``.

    Args:
        impl_func: Optimized production implementation under test.
        ref_func: Naive, transparent reference implementation (pseudo-oracle).
        input_generator: Zero-arg callable yielding ``(args, kwargs)`` per trial.
        num_trials: Number of randomized input trials to evaluate.
        rtol: Relative tolerance for continuous comparisons.
        atol: Absolute tolerance for continuous comparisons.

    Returns:
        Summary dictionary with ``passed`` (bool), ``total_trials`` (int),
        ``failure_count`` (int), and up to 5 ``failures`` counterexamples.
    """
    # Track mismatches between the fast production routine and the reference model.
    failures: List[Dict[str, object]] = []

    for i in range(num_trials):
        # Sample one fresh `(args, kwargs)` input pair for this trial.
        args, kwargs = input_generator()

        # Run the simple reference model first; if the randomly sampled input falls
        # outside the valid mathematical domain, skip the trial cleanly.
        try:
            expected = ref_func(*args, **kwargs)
        except Exception:
            continue

        # Run the production implementation on the exact same inputs and compare.
        try:
            actual = impl_func(*args, **kwargs)

            # Scalar numeric outputs: check agreement within `(rtol, atol)`.
            if isinstance(expected, (float, int, complex, np.number)) and not isinstance(expected, bool):
                if not np.isclose(actual, expected, rtol=rtol, atol=atol):  # type: ignore[arg-type]
                    failures.append({
                        "trial": i,
                        "args": repr(args),
                        "kwargs": repr(kwargs),
                        "expected_reference": expected,
                        "actual_implementation": actual,
                        "diff": abs(complex(actual) - complex(expected)),  # type: ignore[arg-type]
                    })
            # Array outputs: verify all elements match within `(rtol, atol)` and log max error.
            elif isinstance(expected, np.ndarray):
                if not np.allclose(actual, expected, rtol=rtol, atol=atol):  # type: ignore[arg-type]
                    failures.append({
                        "trial": i,
                        "args": repr(args),
                        "kwargs": repr(kwargs),
                        "max_diff": float(np.max(np.abs(actual - expected))),  # type: ignore[operator]
                    })
            # Discrete outputs (tuples, strings, booleans): compare directly.
            else:
                if actual != expected:
                    failures.append({
                        "trial": i,
                        "args": repr(args),
                        "kwargs": repr(kwargs),
                        "expected": expected,
                        "actual": actual,
                    })
        except Exception as impl_err:
            # If the reference model succeeded on this input, an implementation crash is a bug.
            failures.append({
                "trial": i,
                "args": repr(args),
                "kwargs": repr(kwargs),
                "exception": str(impl_err),
            })

    # Cap stored counterexamples to 5 so failure reports remain concise and readable.
    return {
        "passed": len(failures) == 0,
        "total_trials": num_trials,
        "failure_count": len(failures),
        "failures": failures[:5],
    }
