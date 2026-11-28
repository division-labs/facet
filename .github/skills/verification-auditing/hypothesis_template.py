"""Property-based postcondition testing template using Hypothesis.

Generates randomized inputs across bounded domains and shrinks failing cases to
minimal counterexamples using Hypothesis (MacIver et al., 2019), building on
property-based testing (Claessen & Hughes, 2000; Hughes, 2019).

References:
    MacIver, D. R., Hatfield-Dodds, Z., & Contributors (2019). Hypothesis: A new
    approach to property-based testing. JOSS, 4(43), 1891. https://doi.org/10.21105/joss.01891
    Claessen, K., & Hughes, J. (2000). QuickCheck: A lightweight tool for random
    testing of Haskell programs. ICFP '00, 268-279. https://doi.org/10.1145/351240.351266

Function Map:
  - Section 1: Composite NumPy Strategy & Postcondition Harness (`bounded_even_signal_and_step`, `make_monotonicity_check`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

from typing import Callable
from hypothesis import given, settings, strategies as st
from hypothesis.extra.numpy import arrays
import numpy as np
from numpy.typing import NDArray


# ============================================================================
# Section 1: Composite NumPy Strategy & Postcondition Harness
# ============================================================================

@st.composite
def bounded_even_signal_and_step(draw: st.DrawFn) -> tuple[NDArray[np.float64], float]:
    """Sample an even-length 1D float64 array and positive scalar step size."""
    n = draw(st.integers(min_value=4, max_value=64).filter(lambda k: k % 2 == 0))
    arr = draw(
        arrays(
            dtype=np.float64,
            shape=(n,),
            elements=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
        )
    )
    dt = draw(st.floats(min_value=1e-3, max_value=0.1, allow_nan=False, allow_infinity=False))
    return (arr, dt)


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
    @settings(max_examples=500, deadline=None)
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
