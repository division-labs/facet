"""Anchor-vector and closed-form boundary verification template.

Evaluates an implementation against pinned literature test vectors, singular
boundary inputs, and closed-form base cases, complementing property-based
specification (Claessen & Hughes, 2000; Hughes, 2020).

References:
    Claessen, K., & Hughes, J. (2000). QuickCheck: A lightweight tool for random
    testing of Haskell programs. ICFP '00, 268-279. https://doi.org/10.1145/351240.351266
    Hughes, J. (2020). How to specify it! A guide to writing properties of pure
    functions. Trends in Functional Programming (TFP 2019), LNCS 12053, 58-83.
    https://doi.org/10.1007/978-3-030-47147-7_4

Function Map:
  - Section 1: Pinned Anchor Vector & Expected-Exception Evaluator (`verify_anchor_vectors`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Sequence, Type
import numpy as np


# ============================================================================
# Section 1: Pinned Anchor Vector & Expected-Exception Evaluator
# ============================================================================

def verify_anchor_vectors(
    impl_func: Callable[..., object],
    test_cases: Sequence[Mapping[str, object]],
    rtol: float = 1e-7,
    atol: float = 1e-9,
) -> Dict[str, object]:
    """Verify ``impl_func`` against expected outputs or raised exceptions.

    Args:
        impl_func: Callable implementation under test.
        test_cases: Sequence of case mappings with ``name``, ``inputs``
            (mapping or positional sequence), and ``expected`` (or ``raises``).
        rtol: Relative tolerance for continuous scalar/array values.
        atol: Absolute tolerance for continuous scalar/array values.

    Returns:
        Summary dictionary containing ``passed`` (bool), ``total_cases`` (int),
        and ``failures`` (list of diagnostic dicts).
    """
    # Collect every failing test case rather than stopping on the first mismatch
    # so @Engineer receives all broken anchor cases in one diagnostic report.
    failures: List[Dict[str, object]] = []

    for case in test_cases:
        # Extract the human-readable test label, input arguments, and expected outcome.
        name = str(case.get("name", "unnamed_case"))
        inputs = case.get("inputs", {})
        expected = case.get("expected")
        should_raise = case.get("raises")

        # Support both positional argument lists (`[x, y]`) and keyword dicts (`{"x": 1}`).
        args: Sequence[object] = inputs if isinstance(inputs, (list, tuple)) else ()
        kwargs: Mapping[str, object] = inputs if isinstance(inputs, Mapping) else {}

        # Branch 1: Negative precondition test — verify that invalid inputs raise
        # the expected exception class (such as AssertionError or ValueError).
        if isinstance(should_raise, type) and issubclass(should_raise, BaseException):
            exc_type: Type[BaseException] = should_raise
            try:
                impl_func(*args, **kwargs)
                # Reaching this line means the function silently accepted bad input.
                failures.append({
                    "case": name,
                    "error": f"Expected exception {exc_type.__name__}, but function returned normally.",
                })
            except Exception as exc:
                # An exception was raised, so verify its class matches what the spec requires.
                if not isinstance(exc, exc_type):
                    failures.append({
                        "case": name,
                        "error": f"Expected exception {exc_type.__name__}, but got {type(exc).__name__}: {exc}",
                    })
            continue

        # Branch 2: Positive value check — call the implementation and compare its
        # output against the pinned reference answer using appropriate tolerances.
        try:
            actual = impl_func(*args, **kwargs)
            # Continuous scalar numbers must be compared with relative + absolute
            # tolerance (`np.isclose`), never exact `==`, due to IEEE-754 rounding.
            # Booleans are excluded because `isinstance(True, int)` is True in Python.
            if isinstance(expected, (float, int, complex, np.number)) and not isinstance(expected, bool):
                if not np.isclose(actual, expected, rtol=rtol, atol=atol):  # type: ignore[arg-type]
                    failures.append({
                        "case": name,
                        "inputs": inputs,
                        "expected": expected,
                        "actual": actual,
                        "diff": abs(complex(actual) - complex(expected)),  # type: ignore[arg-type]
                    })
            # NumPy arrays are compared element-by-element for both shape and tolerance.
            elif isinstance(expected, np.ndarray):
                if not np.allclose(actual, expected, rtol=rtol, atol=atol):  # type: ignore[arg-type]
                    failures.append({
                        "case": name,
                        "inputs": inputs,
                        "expected_shape": expected.shape,
                        "actual_shape": getattr(actual, "shape", None),
                        "error": "Array output does not match expected array within tolerance.",
                    })
            # Discrete outputs (booleans, strings, tuples of ints, dicts) use exact equality.
            else:
                if actual != expected:
                    failures.append({
                        "case": name,
                        "inputs": inputs,
                        "expected": expected,
                        "actual": actual,
                    })
        except Exception as exc:
            # Record unexpected runtime crashes as structured failures rather than aborting.
            failures.append({
                "case": name,
                "inputs": inputs,
                "exception": str(exc),
            })

    # Return a structured verdict dictionary ready for `AUDIT_FEEDBACK.json` reporting.
    return {
        "passed": len(failures) == 0,
        "total_cases": len(test_cases),
        "failures": failures,
    }
