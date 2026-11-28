"""Pytest plugin enforcing Facet's unseeded high-sample Hypothesis floor.

Loaded automatically during Stage 4 ``pytest`` execution so property-based
obligations sample across genuine stochastic draws (default minimum 500
examples per ``@given`` test) with deterministic seed-pinning disabled.

Function Map:
  - Section 1: Unseeded Hypothesis Profile Floor & Proof Header (`DEFAULT_MIN_EXAMPLES`, `_resolve_min_examples`, `pytest_configure`, `pytest_report_header`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import os
from typing import Any

# ============================================================================
# Section 1: Unseeded Hypothesis Profile Floor & Proof Header
# ============================================================================

DEFAULT_MIN_EXAMPLES: int = 500
_PATCHED_FLAG: str = "_facet_stochastic_floor_patched"


def _resolve_min_examples() -> int:
    """Resolve the minimum Hypothesis example floor from environment or default."""
    raw = os.environ.get("FACET_HYPOTHESIS_MIN_EXAMPLES", "").strip()
    if raw.isdigit():
        return max(100, int(raw))
    return DEFAULT_MIN_EXAMPLES


def pytest_configure(config: Any) -> None:
    """Register and enforce the unseeded high-sample Hypothesis profile before collection."""
    del config
    try:
        from hypothesis import HealthCheck, settings as hyp_settings
    except Exception:
        return

    min_examples = _resolve_min_examples()
    if not getattr(hyp_settings, _PATCHED_FLAG, False):
        orig_init = hyp_settings.__init__

        def _enforced_settings_init(self: Any, parent: Any = None, **kwargs: Any) -> None:
            if kwargs.get("max_examples") is not None and int(kwargs["max_examples"]) < min_examples:
                kwargs["max_examples"] = min_examples
            if kwargs.get("derandomize"):
                kwargs["derandomize"] = False
            orig_init(self, parent=parent, **kwargs)

        setattr(hyp_settings, "__init__", _enforced_settings_init)
        setattr(hyp_settings, _PATCHED_FLAG, True)

    hyp_settings.register_profile(
        "facet_stochastic",
        max_examples=min_examples,
        derandomize=False,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    hyp_settings.load_profile("facet_stochastic")


def pytest_report_header(config: Any) -> str:
    """Emit proof-of-execution header line confirming unseeded stochastic sampling."""
    del config
    return f"facet-stochastic: min_examples={_resolve_min_examples()}, derandomize=False"
