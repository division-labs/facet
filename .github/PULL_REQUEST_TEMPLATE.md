## Summary

<!-- Concise summary of the architectural or roadmap change and why it was made. -->

## Related Issues / Milestone

<!-- e.g. Closes #12, Part of Milestone 1 -->

## Empirical & Verification Evidence

- [ ] `./.venv/bin/python scripts/validate_system.py --offline` passes `100%` (Pathways 1-4, including `Function Map:`, `Phases:`, `Args:`/`Raises:`, and config enum parity)
- [ ] `~/.copilot/` user-scope parity verified via `./.venv/bin/python scripts/install_user_level.py` (preserving user `model:` tags)
- [ ] For hook/prompt/harness behavior changes: validated via an isolated rotating-challenge scratch A/B battery before promotion (see [`docs/empirical-engineering.md`](docs/empirical-engineering.md))
- [ ] Held-out 5-modality non-regression (`00_MULTIMODAL_SCORECARD.md` / `analyze_trace.py --compare`) verified when pipeline runtime behavior changed
