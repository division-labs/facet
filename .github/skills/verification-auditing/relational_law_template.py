"""Metamorphic and algebraic relational invariant template (Hughes 2019).

Tests multi-call relational laws (such as round-trip ``decode(encode(x)) == x``,
time-reversibility, symmetry, scaling, or step-halving convergence) where no
closed-form single-point formula exists (Hughes 2019; Chen et al. 1998).

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple


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
