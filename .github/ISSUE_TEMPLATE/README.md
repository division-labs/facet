# GitHub Issue Forms and Chooser Configuration

This directory defines structured GitHub Issue Forms (`.yml`) and template-chooser links (`config.yml`) for [`division-labs/facet`](https://github.com/division-labs/facet). Together with [`PULL_REQUEST_TEMPLATE.md`](../PULL_REQUEST_TEMPLATE.md), these forms connect day-to-day engineering work directly to [`ROADMAP.md`](../../ROADMAP.md), the [`Facet Roadmap & Pipeline Tracker`](https://github.com/orgs/division-labs/projects/1) Project v2 board, and the four-step empirical loop in [`docs/empirical-engineering.md`](../../docs/empirical-engineering.md).

---

## Directory Contents

| File | Purpose | Default Prefix & Labels |
| :--- | :--- | :--- |
| [`config.yml`](./config.yml) | Configures the New Issue template chooser (`blank_issues_enabled: true`) and surfaces quick-reference links to [`docs/empirical-engineering.md`](../../docs/empirical-engineering.md) and [`ROADMAP.md`](../../ROADMAP.md). | Chooser metadata |
| [`01_trace_or_pipeline_defect.yml`](./01_trace_or_pipeline_defect.yml) | **Pipeline or Trace Defect** — reports stage failures, OCR/contract defects, latency regressions, or Cleanroom/UX leaks traced back to their earliest originating pipeline stage via [`scripts/analyze_trace.py`](../../scripts/analyze_trace.py). | `[Hardening] ` · `kind/trace-defect` |
| [`02_roadmap_deliverable.yml`](./02_roadmap_deliverable.yml) | **Mosaic / Roadmap Deliverable** — tracks implementation tasks across Milestones 1-4 of [`ROADMAP.md`](../../ROADMAP.md), including coupled schema/module contracts and deterministic gates in [`scripts/validate_system.py`](../../scripts/validate_system.py). | `[M1.x] ` · `kind/roadmap`, `area/facet-lens` |
| [`03_ab_experiment.yml`](./03_ab_experiment.yml) | **Empirical A/B Experiment** — proposes and logs zero-repo-mutation multi-modality scratch batteries across fresh rotating challenges (`CC BY 4.0` arXiv PDFs, disjoint `SciCode` subproblems, and multi-entrypoint synthetic specifications) with explicit hook activation counters and `--factorial-json` interaction terms. | `[Experiment] ` · `kind/experiment`, `status/needs-ab-probe` |

---

## Authoring & Ordering Conventions

1. **Numeric Filename Prefixes (`01_` .. `03_`):** GitHub sorts Issue Forms alphanumerically by filename rather than by the display `name:` field. Two-digit numeric prefixes preserve deterministic ordering in the New Issue chooser as additional forms are added.
2. **Issue Forms (`.yml`) vs. Directory Documentation (`README.md`):** Active templates use GitHub's structured YAML Issue Form schema (`.yml`) with `name:`, `description:`, and `body:` fields. This `README.md` intentionally omits YAML frontmatter (`name:` / `about:`) so GitHub renders it below the directory listing without adding `README.md` to the New Issue template chooser.
3. **Trace-Grounded Defect Reporting:** Before opening a `01_trace_or_pipeline_defect.yml` issue, generate a verbatim 1:1 audit bundle on the current codebase so the report cites the earliest upstream stage where the defect originates rather than a downstream symptom:
   ```bash
   ./.venv/bin/python scripts/analyze_trace.py --session <session_id_or_events_jsonl> \
     --audit-bundle logs/context_audits/issue_triage
   ```
4. **Repository Validation:** [`scripts/validate_system.py`](../../scripts/validate_system.py) (`--pathway static`) verifies organization URL alignment, relative Markdown links, and zero-emoji ASCII formatting across this directory.
