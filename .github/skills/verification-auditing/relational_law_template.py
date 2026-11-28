"""Metamorphic and algebraic relational invariant template.

Evaluates multi-call invariants (such as ``decode(encode(x)) == x``, symmetry,
conservation, or scaling) where no single-call closed-form oracle exists,
drawing on metamorphic testing (Chen et al., 1998) and algebraic property
specification (Hughes, 2020).

References:
    Chen, T. Y., Cheung, S. C., & Yiu, S. M. (1998). Metamorphic testing: A new
    approach for generating next test cases. HKUST-CS98-01 (arXiv:2002.12543).
    Hughes, J. (2020). How to specify it! A guide to writing properties of pure
    functions. Trends in Functional Programming (TFP 2019), LNCS 12053, 58-83.
    https://doi.org/10.1007/978-3-030-47147-7_4

Function Map:
  - Section 1: Multi-Call Relational Invariant Evaluator (`verify_relational_law`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple


# ============================================================================
# Section 1: Multi-Call Relational Invariant Evaluator
# ============================================================================

def verify_relational_law(
    law_predicate: Callable[..., Optional[bool]],
    input_generator: Callable[[], Tuple[Sequence[object], Mapping[str, object]]],
    num_trials: int = 100,
) -> Dict[str, object]:
    """Evaluate a relational invariant predicate across randomized trials.

    Args:
        law_predicate: Callable ``(*args, **kwargs) -> Optional[bool]`` that
            verifies a multi-call relation using ``math.isclose`` /
            ``np.allclose`` for continuous values (or raises ``AssertionError``).
        input_generator: Zero-arg callable yielding ``(args, kwargs)`` per trial.
        num_trials: Number of randomized trial evaluations to run.

    Returns:
        Summary dictionary with ``passed`` (bool), ``total_trials`` (int),
        ``failure_count`` (int), and up to 5 ``failures`` counterexamples.
    """
    # Accumulate any randomized inputs that violate the relational law.
    failures: List[Dict[str, object]] = []

    for i in range(num_trials):
        # Generate a fresh input tuple/dict and evaluate the multi-call law.
        args, kwargs = input_generator()
        try:
            verdict = law_predicate(*args, **kwargs)
            # Support predicates that return a boolean (`False` = law violated)
            # as well as predicates that use `assert` statements (`None` = passed).
            if verdict is False:
                failures.append({
                    "trial": i,
                    "inputs": repr(args),
                    "kwargs": repr(kwargs),
                    "error": "Relational law predicate evaluated to False.",
                })
        except AssertionError as ae:
            # Capture explicit assertion messages from inside `law_predicate`.
            failures.append({
                "trial": i,
                "inputs": repr(args),
                "kwargs": repr(kwargs),
                "error": f"Assertion failed: {ae}",
            })
        except Exception as exc:
            # Capture unexpected runtime errors (e.g. ZeroDivisionError or shape errors).
            failures.append({
                "trial": i,
                "inputs": repr(args),
                "kwargs": repr(kwargs),
                "exception": f"Exception raised during relational evaluation: {exc}",
            })

    # Return overall pass status alongside up to 5 concrete counterexamples.
    return {
        "passed": len(failures) == 0,
        "total_trials": num_trials,
        "failure_count": len(failures),
        "failures": failures[:5],
    }
