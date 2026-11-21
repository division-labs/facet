"""Property-based postcondition testing template using Hypothesis (Hughes 2019).

Generates randomized inputs across a bounded domain and shrinks any failing
input down to a minimal counterexample.

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

from typing import Callable
from hypothesis import given, settings, strategies as st
import numpy as np


def make_monotonicity_check(
    impl_function: Callable[[float], float],
    delta: float = 1e-4,
) -> Callable[[], None]:
    """Build a Hypothesis monotonicity check on ``(0.1, 1000.0)``.

    Args:
        impl_function: Scalar function expected to be strictly increasing.
        delta: Positive step perturbation applied to each sampled point.

    Returns:
        Zero-argument Hypothesis test callable verifying
        ``impl_function(x + delta) > impl_function(x)``.
    """

    # `@given` instructs Hypothesis to sample finite floats across [0.1, 1000.0],
    # automatically probing boundary values and shrinking any failure it finds.
    @given(st.floats(min_value=0.1, max_value=1000.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=100)
    def _check(x: float) -> None:
        """Assert finite outputs and strict increase at ``x``."""
        # Evaluate the function at `x + delta` and at `x` to check strict growth.
        lhs = impl_function(x + delta)
        rhs = impl_function(x)
        # First confirm neither call produced NaN or +/-Infinity.
        assert np.isfinite(lhs) and np.isfinite(rhs)
        # Then verify the monotonic postcondition `f(x + delta) > f(x)`.
        assert lhs > rhs, f"Monotonicity postcondition violated at x={x}"

    # Return the wrapped zero-argument test function so pytest can execute it directly.
    return _check
