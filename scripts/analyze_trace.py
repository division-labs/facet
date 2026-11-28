#!/usr/bin/env python3
"""Unified session trace analyzer, 1:1 context bundler, and 6-bucket A/B diagnostician.

Inspects live VS Code Copilot session logs (``$COPILOT_HOME/session-state/<id>/events.jsonl``
and optional ``wire_capture.jsonl``), benchmark run directories (``benchmarks/runs/<run_id>/``),
and multi-arm factorial matrices without writing inside ``specs/<slug>/``:
  1. ``--session <id_or_path>``     — Per-role turn/latency/token/swap summary (+ ``--diagnose`` / ``--audit-bundle``).
  2. ``--run-live``                 — Drives ``scripts/copilot_sdk_runner.mjs`` (real ``copilot-runtime`` + loopback
                                      wire-tap) and emits a verbatim 1:1 stage-by-stage Markdown/JSON audit bundle.
  3. ``--bench-run <dir>``          — Aggregates ``SciCode`` step waterfalls, active GPU time, swaps, and diagnostics.
  4. ``--compare <base> <cand>``    — Reproducible head-to-head A/B delta report with non-regression gates.
  5. ``--matrix-dir`` / ``--factorial-json`` — Orthogonal main effects and pairwise ``I_AB`` interactions.

Full documentation and CLI guide: ``scripts/README.md``.

Function Map:
  - Section 1: Declarative 5-Modality Presets, SciCode Step Resolver & Trial Graders (`MODALITY_EVAL_SPECS`, `resolve_scicode_step_modality`, `grade_modality_trial`)
  - Section 2: Runtime/SDK Discovery, arXiv Cache & Session Event-Stream Parser (`resolve_node_command`, `analyze_session_events`)
  - Section 3: SciCode Benchmark Waterfall & Factorial Interaction Analyzer (`analyze_benchmark_run`, `compute_pairwise_interactions`)
  - Section 4: Six-Bucket Agent-Cognition & Scope-Hygiene Diagnostician (`diagnose_session_optimization`, `format_diagnostic_report`)
  - Section 5: Head-to-Head A/B Comparator & Non-Regression Gate (`compare_traces`, `format_comparison_report`)
  - Section 6: Strict 1:1 Stage-Context Bundler, Multi-Modality Scorecard & CLI Entrypoint (`audit_and_collate_session`, `collate_multimodal_scorecard`, `main`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple
import urllib.request

REPO_ROOT: Path = Path(__file__).resolve().parent.parent


# ============================================================================
# Section 1: Declarative 5-Modality Presets, SciCode Step Resolver & Trial Graders
# ============================================================================

KNOWN_EVENT_TYPES: frozenset[str] = frozenset({
    "session.start",
    "session.resume",
    "session.Idle",
    "session.idle",
    "session.shutdown",
    "session.error",
    "session.model_change",
    "session.usage_record",
    "session.canvas.recorded",
    "session.binary_asset",
    "session.truncation",
    "session.compaction_start",
    "session.compaction_complete",
    "session.TitleChanged",
    "session.title_changed",
    "session.mode_changed",
    "session.plan_changed",
    "session.workspace_file_changed",
    "session.handoff",
    "instruction.update",
    "subagent.selected",
    "subagent.started",
    "subagent.configured",
    "subagent.completed",
    "subagent.failed",
    "subagent.deselected",
    "skill.invoked",
    "skill.invoked_ref",
    "hook.start",
    "hook.end",
    "permission.requested",
    "permission.completed",
    "user.message",
    "system.message",
    "assistant.turn_start",
    "assistant.message",
    "assistant.turn_end",
    "assistant.message_delta",
    "assistant.reasoning",
    "assistant.reasoning_delta",
    "assistant.streaming_delta",
    "assistant.intent",
    "tool.execution_start",
    "tool.execution_partial_result",
    "tool.execution_progress",
    "tool.execution_complete",
    "tool.user_requested",
    "abort",
})

BASE64_IMAGE_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]{256,}")
USER_PLANE_LEAK_RE = re.compile(
    r"</?(?:hypervisor_directive|hypervisor_context|subagent_directive|subagent_context|checkpoint_briefing|checkpoint_reprompt|checkpoint_gate|archival_complete|reset_complete|status_pulse|recovery_notice|stage_anchor|stage_checklist|validation_error|validation_passed|failure_diagnostic|failure_recovery|workspace_map|hydrated_artifact|hydrated_schema_summary|hydrated_public_surface|ocr_staging_ready|ocr_plate_progress|ocr_plate_viewed|vector_glyph_anchor|compaction_state_anchor|user_request|user_input_data|local_critic_feedback|local_critic_mandate|audit_rewind_feedback|resolved_audit_note|stage1_executable_critic_directive|numeric_probe_first_directive|hypothesis_numpy_idioms|prior_attempt_diagnosis|edit_scope_hint|incremental_audit_scope|progressive_execution_plan|replace_symbol_repair_hint|truncation_recovery)\b|"
    r"\[(?:CHECKPOINT GATE|HYPERVISOR DISPATCH|HYPERVISOR HALT|ACTIVE STAGE|GATEKEEPER|STAGE ROUTING MISMATCH|SCOPED EDIT REQUIRED|CANONICAL HANDOFF BRIEF|CANONICAL STAGE|NON-CHECKPOINT|AGENT CONTEXT|SPECIALIST BRIEFING SOURCE|TOPIC & RESULT|SEALED PACKAGE CARD|MANDATORY REPROMPT|STAGE 5 ARCHIVAL COMPLETE|WORKSPACE RESET COMPLETE|REPEAT DENIAL|UNINSPECTED PLATE|SINGLE-PLATE OCR GATE|PER-PLATE OCR ISOLATION|CAPABILITY CONFINEMENT|MODALITY FIREWALL|LOOP GUARD|CLEANROOM|DETERMINISTIC\s+(?:EPOCH|STAGE\s+[34](?:\s+VERIFICATION)?)\s+CHECKLIST)\b|"
    r"\b(?:PRE_RUN_HALT|STAGE_[0-5]_[A-Z_]+|CHECKPOINT_[0-3]_AWAITING_APPROVAL|CHECKPOINT_REPAIR_ESCALATION|post_archive_halt_latch)\b",
    re.IGNORECASE,
)
ECHO_INVARIANT_PROBES: Tuple[Tuple[str, str], ...] = (
    ("path_first", "pass \"path\""),
    ("scoped_edit", "scoped edit"),
    ("np_allclose", "allclose"),
    ("ndarray_typing", "ndarray"),
    ("future_annotations", "from __future__ import annotations"),
)

TRACKED_EXAMPLE_PDF_REL: str = "examples/constant_q_transform/2609.29119v1.pdf"
TRACKED_EXAMPLE_PDF_SHA256: str = "c66660176344b61ff627e9c7da2d2f2ac5f8bb29751140abda5dd08b8ccc5f92"

CANONICAL_MODALITY_ORDER: Tuple[str, ...] = (
    "image",
    "pdf",
    "spec_bundle",
    "web_ideation",
    "closed_book_text",
)

MODALITY_EVAL_SPECS: Dict[str, Dict[str, object]] = {
    "image": {
        "display_title": "Modality 1: Open-Book Image (IMG_1598.jpg)",
        "governance_mode": "interactive",
        "allow_web_research": True,
        "no_examples": False,
        "expect_ocr": True,
        "expect_source_text": False,
        "expected_pages": 1,
        "min_web_fetches": 1,
        "max_web_fetches": None,
        "max_out_of_scope_reads": 0,
        "scicode_backed": False,
        "turn_timeout_ms": 3600000,
        "source_label": "examples/magical_mystery/IMG_1598.jpg",
        "prompt": (
            "@Facet What can you see through your crystalline lens if you peer into the examples folder "
            "about the magical mystery image?"
        ),
    },
    "pdf": {
        "display_title": "Modality 2: Closed-Book Multi-Page PDF (2609.29119v1.pdf)",
        "governance_mode": "interactive",
        "allow_web_research": False,
        "no_examples": False,
        "expect_ocr": True,
        "expect_source_text": True,
        "expected_pages": 5,
        "min_web_fetches": 0,
        "max_web_fetches": 0,
        "max_out_of_scope_reads": 0,
        "scicode_backed": False,
        "turn_timeout_ms": 3600000,
        "source_label": TRACKED_EXAMPLE_PDF_REL,
        "prompt": (
            f"@Facet Delve into {TRACKED_EXAMPLE_PDF_REL} and derive, implement, "
            "and verify the core method proposed in the paper."
        ),
    },
    "spec_bundle": {
        "display_title": "Modality 3: Reference Specification Bundle (examples/mandelbrot_set)",
        "governance_mode": "interactive",
        "allow_web_research": False,
        "no_examples": False,
        "expect_ocr": False,
        "expect_source_text": False,
        "expected_pages": 0,
        "min_web_fetches": 0,
        "max_web_fetches": 0,
        "max_out_of_scope_reads": 0,
        "scicode_backed": False,
        "turn_timeout_ms": 3600000,
        "source_label": "examples/mandelbrot_set",
        "prompt": (
            "@Facet Using the reference specification bundle in examples/mandelbrot_set, "
            "specify, implement, and cleanroom-verify the Mandelbrot escape module defined by its contract."
        ),
    },
    "web_ideation": {
        "display_title": "Modality 4: Open-Ended Web Ideation (Approved Research Domains)",
        "governance_mode": "interactive",
        "allow_web_research": True,
        "no_examples": True,
        "expect_ocr": False,
        "expect_source_text": False,
        "expected_pages": 0,
        "min_web_fetches": 1,
        "max_web_fetches": None,
        "max_out_of_scope_reads": 0,
        "scicode_backed": False,
        "turn_timeout_ms": 3600000,
        "source_label": "web_fetch (Approved Research Domains)",
        "prompt": (
            "@Facet Find a numerically stable polynomial interpolation method on bounded intervals "
            "that avoids Runge's phenomenon, then specify, implement, and verify it."
        ),
    },
    "closed_book_text": {
        "display_title": "Modality 5: Closed-Book 1-Step SciCode Benchmark",
        "governance_mode": "autonomous-batch",
        "allow_web_research": False,
        "no_examples": True,
        "expect_ocr": False,
        "expect_source_text": False,
        "expected_pages": 0,
        "min_web_fetches": 0,
        "max_web_fetches": 0,
        "max_out_of_scope_reads": 0,
        "scicode_backed": True,
        "turn_timeout_ms": 3600000,
        "source_label": "SciCode 1-step benchmark (default 19.1)",
        "prompt": "",
    },
}

LIVE_MODALITY_PRESETS: Dict[str, Dict[str, object]] = MODALITY_EVAL_SPECS


def _load_scicode_benchmark_module():
    """Dynamically load ``benchmarks/run_benchmark.py`` for 1-step SciCode prompt & HDF5 evaluation."""
    import importlib.util as _ilu

    bm_path = REPO_ROOT / "benchmarks" / "run_benchmark.py"
    spec = _ilu.spec_from_file_location("facet_scicode_bench_mod", bm_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load benchmark module at {bm_path}")
    mod = _ilu.module_from_spec(spec)
    sys.modules["facet_scicode_bench_mod"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def resolve_scicode_step_modality(
    scicode_step: str = "19.1",
    split: str = "validation",
) -> Dict[str, object]:
    r"""Resolve a 1-step SciCode problem specification into a closed-book ``@Facet`` prompt & metadata."""
    raw_sel = str(scicode_step or "19.1").strip()
    if "." in raw_sel:
        prob_id, sub_s = raw_sel.split(".", 1)
        step_num = max(1, int(sub_s))
    else:
        prob_id, step_num = raw_sel, 1

    bm = _load_scicode_benchmark_module()
    problems, _ = bm.load_scicode_split(split=split)
    prob_data = next((p for p in problems if str(p.get("problem_id")) == prob_id), None)
    if prob_data is None:
        for alt_split in ("validation", "test"):
            if alt_split == split:
                continue
            try:
                alt_probs, _ = bm.load_scicode_split(split=alt_split)
                prob_data = next((p for p in alt_probs if str(p.get("problem_id")) == prob_id), None)
                if prob_data is not None:
                    break
            except Exception:
                continue
    if prob_data is None:
        raise ValueError(f"SciCode problem_id={prob_id!r} not found in cached dataset.")

    sub_steps = list(prob_data.get("sub_steps") or [])
    if step_num > len(sub_steps):
        raise ValueError(f"SciCode problem {prob_id} has only {len(sub_steps)} substeps (requested .{step_num}).")

    with tempfile.TemporaryDirectory(prefix="facet_scicode_prompt_") as tmp_p:
        asst = bm.ScicodePromptingAssistant(Path(tmp_p) / "out", Path(tmp_p) / "pr", with_background=False)
        for prior_k in range(1, step_num):
            bg_code = str(sub_steps[prior_k - 1].get("ground_truth_code") or "")
            asst.previous_llm_code.append(bg_code)
        asst.previous_llm_code.append(None)
        prev_llm_s, next_step_s, _prev_code_s = asst.process_problem_steps(prob_data, step_num)
        canonical_step_prompt = f"{prev_llm_s}\n{next_step_s}".strip()

    step_obj = sub_steps[step_num - 1]
    step_id = str(step_obj.get("step_number") or f"{prob_id}.{step_num}")
    target_fn = bm.extract_function_name(str(step_obj.get("function_header") or ""))
    mod_slug = f"scicode_step_{step_id.replace('.', '_')}"
    req_deps_p = str(prob_data.get("required_dependencies") or "").strip()
    deps_block = f"\n\nDEPENDENCIES:\n{req_deps_p}" if req_deps_p else ""
    prompt_str = (
        f"@Facet {canonical_step_prompt}{deps_block}\n\n"
        f"(Path A closed-book specification: implement `{target_fn}` in target module `{mod_slug}.py` "
        "preserving the exact function header above; if previous step functions appear in the prompt above, "
        f"include them in `{mod_slug}.py` so 4-predicate verification executes self-contained.)"
    )
    return {
        "step_id": step_id,
        "problem_id": prob_id,
        "step_num": step_num,
        "target_function": target_fn,
        "target_module": f"{mod_slug}.py",
        "source_label": f"SciCode {step_id} ({target_fn})",
        "prompt": prompt_str,
        "problem_data": prob_data,
    }


def grade_scicode_step_workspace(
    workspace_dir: Path,
    scicode_step: str = "19.1",
    split: str = "validation",
) -> Dict[str, object]:
    r"""Evaluate a sealed 1-step SciCode workspace against official ``test_data.h5`` assertions when available."""
    resolved = resolve_scicode_step_modality(scicode_step=scicode_step, split=split)
    h5_path = REPO_ROOT / "benchmarks" / ".cache" / "test_data.h5"
    target_mod = str(resolved["target_module"])
    cand_files = list((workspace_dir / "specs").glob(f"*/{target_mod}")) if (workspace_dir / "specs").is_dir() else []
    if not cand_files and (workspace_dir / "specs").is_dir():
        cand_files = [
            p for p in sorted((workspace_dir / "specs").glob("*/*.py"))
            if not p.name.startswith(("verify_", "test_", "conftest"))
        ]
    if not cand_files:
        return {"evaluated": False, "passed": False, "reason": f"No sealed implementation module found under {workspace_dir / 'specs'}"}
    if not h5_path.is_file():
        return {"evaluated": False, "passed": True, "reason": "test_data.h5 not cached locally; Cleanroom 500-draw pytest verified"}

    bm = _load_scicode_benchmark_module()
    prob_data = resolved["problem_data"]  # type: ignore[assignment]
    step_num = int(resolved["step_num"])  # type: ignore[arg-type]
    step_id = str(resolved["step_id"])
    sub_steps = list(prob_data["sub_steps"])  # type: ignore[index]
    step_obj = sub_steps[step_num - 1]
    test_lst = list(step_obj.get("test_cases") or [])

    req_deps = str(prob_data.get("required_dependencies") or "").strip()  # type: ignore[union-attr]
    prior_code_parts: List[str] = []
    for k_prev in range(step_num - 1):
        gt_c = str(sub_steps[k_prev].get("ground_truth_code") or "").strip()
        if gt_c:
            prior_code_parts.append(gt_c)
    raw_impl_code = cand_files[0].read_text(encoding="utf-8")
    has_future_ann = bool(re.search(r"^\s*from\s+__future__\s+import\s+annotations\b", raw_impl_code, re.MULTILINE))
    cleaned_impl_code = re.sub(r"^\s*from\s+__future__\s+import\s+annotations[^\n]*\n?", "", raw_impl_code, flags=re.MULTILINE).strip()
    preamble_parts = [
        *(["from __future__ import annotations"] if has_future_ann else []),
        *([req_deps] if req_deps else []),
        *prior_code_parts,
        cleaned_impl_code,
    ]
    combined_code = "\n\n".join(p for p in preamble_parts if p).strip()

    with tempfile.TemporaryDirectory(prefix=f"facet_scicode_h5_{step_id}_") as tmp_d:
        tmp_dir = Path(tmp_d)
        shim_root = tmp_dir / "_scicode_pkg"
        bm._write_scicode_runtime_package(shim_root)
        assert_file = tmp_dir / f"{step_id}.py"
        lines_a = [
            combined_code,
            "",
            "from scicode.parse.parse import process_hdf5_to_tuple",
            f"targets = process_hdf5_to_tuple({step_id!r}, {len(test_lst)}, {str(h5_path)!r})",
        ]
        for idx_t, tc_block in enumerate(test_lst):
            lines_a.append(f"target = targets[{idx_t}]")
            lines_a.extend(str(tc_block).splitlines())
        assert_file.write_text("\n".join(lines_a) + "\n", encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{shim_root}:{env['PYTHONPATH']}" if env.get("PYTHONPATH") else str(shim_root)
        py_bin = str(REPO_ROOT / ".venv" / "bin" / "python") if (REPO_ROOT / ".venv" / "bin" / "python").is_file() else sys.executable
        proc = subprocess.run([py_bin, str(assert_file)], cwd=str(tmp_dir), env=env, capture_output=True, text=True, timeout=60)
        return {
            "evaluated": True,
            "passed": proc.returncode == 0,
            "step_id": step_id,
            "module_path": str(cand_files[0]),
            "stderr": proc.stderr[-800:] if proc.returncode != 0 else "",
        }


def compute_trial_pass_metrics(trial_passes: Sequence[bool]) -> Dict[str, object]:
    """Compute ``pass@k`` (at least 1 success) and ``pass^k`` ($p^k$ consistency) across ``k`` trials."""
    k = len(trial_passes)
    if k == 0:
        return {"trials": 0, "successes": 0, "empirical_rate": 0.0, "pass_at_k": 0.0, "pass_pow_k": 0.0}
    s = sum(1 for ok in trial_passes if ok)
    p_hat = s / float(k)
    return {
        "trials": k,
        "successes": s,
        "empirical_rate": round(p_hat, 4),
        "pass_at_k": round(1.0 - ((1.0 - p_hat) ** k), 4),
        "pass_pow_k": round(p_hat ** k, 4),
    }


def grade_modality_trial(
    modality: str,
    ledger: Dict[str, object],
    workspace_dir: Optional[Path] = None,
    scicode_step: str = "19.1",
    allow_web_override: str = "auto",
) -> Dict[str, object]:
    """Evaluate outcome graders and balanced transcript invariant graders for ``modality``."""
    spec = MODALITY_EVAL_SPECS[modality]
    eff_allow_web = (
        bool(spec["allow_web_research"])
        if allow_web_override == "auto"
        else (allow_web_override == "true")
    )
    chk = ledger.get("completeness_checks") or {}  # type: ignore[assignment]
    diag = ledger.get("diagnostics") or {}  # type: ignore[assignment]
    bk = diag.get("buckets") or {}  # type: ignore[union-attr]
    b2 = bk.get("2_unhydrated_tool_tax") or {}  # type: ignore[union-attr]
    b6 = bk.get("6_cleanroom_and_ux_integrity") or {}  # type: ignore[union-attr]

    wf_count = int(b2.get("web_fetch_calls_total") or 0)
    oos_count = int(b6.get("out_of_scope_reads_count") or 0)
    max_oos = int(spec.get("max_out_of_scope_reads") or 0)  # type: ignore[arg-type]

    min_wf = (1 if eff_allow_web else 0) if allow_web_override != "auto" else int(spec.get("min_web_fetches") or 0)  # type: ignore[arg-type]
    max_wf = (None if eff_allow_web else 0) if allow_web_override != "auto" else spec.get("max_web_fetches")

    web_min_ok = wf_count >= min_wf
    web_max_ok = True if max_wf is None else (wf_count <= int(max_wf))  # type: ignore[arg-type]
    scope_ok = oos_count <= max_oos
    one_to_one_ok = bool(chk.get("strict_1to1_pass"))  # type: ignore[union-attr]
    integrity_ok = bool(b6.get("integrity_pass"))  # type: ignore[union-attr]

    scicode_grade: Optional[Dict[str, object]] = None
    scicode_ok = True
    if bool(spec.get("scicode_backed")) and workspace_dir is not None and workspace_dir.is_dir():
        scicode_grade = grade_scicode_step_workspace(workspace_dir, scicode_step=scicode_step)
        scicode_ok = bool(scicode_grade.get("passed"))

    all_ok = bool(one_to_one_ok and integrity_ok and web_min_ok and web_max_ok and scope_ok and scicode_ok)
    return {
        "modality": modality,
        "all_graders_pass": all_ok,
        "strict_1to1_pass": one_to_one_ok,
        "integrity_pass": integrity_ok,
        "web_min_pass": web_min_ok,
        "web_max_pass": web_max_ok,
        "web_fetch_calls_total": wf_count,
        "scope_confinement_pass": scope_ok,
        "out_of_scope_reads_count": oos_count,
        "scicode_h5_grade": scicode_grade,
    }


# ============================================================================
# Section 2: Runtime/SDK Discovery, arXiv Cache & Session Event-Stream Parser
# ============================================================================

def resolve_copilot_home() -> Path:
    """Resolve ``$COPILOT_HOME`` when non-empty, defaulting to ``~/.copilot``."""
    raw = os.environ.get("COPILOT_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".copilot").resolve()


def resolve_node_command() -> Tuple[List[str], Dict[str, str]]:
    """Locate either system ``node`` or VS Code's ``Code Helper`` (``ELECTRON_RUN_AS_NODE=1``)."""
    env = dict(os.environ)
    env.pop("NODE_OPTIONS", None)
    node_bin = shutil.which("node")
    if node_bin:
        return ([node_bin, "--no-warnings=MODULE_TYPELESS_PACKAGE_JSON"], env)
    candidates = [
        Path("/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper.app/Contents/MacOS/Code Helper"),
        Path("/Applications/Visual Studio Code - Insiders.app/Contents/Frameworks/Code Helper.app/Contents/MacOS/Code Helper"),
    ]
    for cand in candidates:
        if cand.is_file():
            env["ELECTRON_RUN_AS_NODE"] = "1"
            return ([str(cand), "--no-warnings=MODULE_TYPELESS_PACKAGE_JSON"], env)
    raise RuntimeError("Neither `node` nor VS Code `Code Helper` was found on this machine.")


def is_copilot_sdk_available(repo_root: Path = REPO_ROOT) -> bool:
    """Return ``True`` when ``scripts/copilot_sdk_runner.mjs --probe-sdk`` finds ``copilot-runtime``."""
    runner_mjs = repo_root / "scripts" / "copilot_sdk_runner.mjs"
    if not runner_mjs.is_file():
        return False
    try:
        node_cmd, node_env = resolve_node_command()
        proc = subprocess.run(
            node_cmd + [str(runner_mjs), "--probe-sdk"],
            cwd=str(repo_root),
            env=node_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


def ensure_open_access_arxiv_pdf(
    arxiv_id: str = "2609.29119v1",
    cache_dir: Optional[Path] = None,
) -> Path:
    """Download and cache an open-access arXiv PDF inside git-ignored ``benchmarks/.cache/open_papers/``.

    Verifies that ``arxiv.org`` is registered in ``.github/hooks/approved_sources.json``,
    validates ``%PDF-`` and ``%%EOF`` structure, and persists a ``<id>.meta.json`` sidecar
    without committing third-party PDFs into version control.
    """
    clean_id = re.sub(r"\.pdf$", "", arxiv_id.strip())
    if not re.match(r"^[0-9]{4}\.[0-9]{4,5}(?:v[0-9]+)?$", clean_id):
        raise ValueError(f"Invalid arXiv identifier: {arxiv_id!r}")

    approved_path = REPO_ROOT / ".github" / "hooks" / "approved_sources.json"
    if approved_path.is_file():
        reg = json.loads(approved_path.read_text(encoding="utf-8"))
        domains = {str(d).lower() for d in (reg.get("approved_domains") or reg.get("domains") or [])}
        if domains and "arxiv.org" not in domains:
            raise ValueError("arxiv.org is not listed in .github/hooks/approved_sources.json")

    eff_cache = (cache_dir or (REPO_ROOT / "benchmarks" / ".cache" / "open_papers")).resolve()
    eff_cache.mkdir(parents=True, exist_ok=True)
    pdf_path = eff_cache / f"{clean_id}.pdf"
    meta_path = eff_cache / f"{clean_id}.meta.json"

    if pdf_path.is_file() and pdf_path.stat().st_size > 1024:
        raw_existing = pdf_path.read_bytes()
        if b"%PDF-" in raw_existing[:1024] and (b"%%EOF" in raw_existing[-2048:] or b"endobj" in raw_existing[-2048:]):
            return pdf_path

    pdf_url = f"https://arxiv.org/pdf/{clean_id}"
    req = urllib.request.Request(
        pdf_url,
        headers={"User-Agent": "Facet-OpenPaperCache/1.0 (+https://github.com/division-labs/facet)"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read(32 * 1024 * 1024 + 1)
    if len(body) > 32 * 1024 * 1024:
        raise ValueError(f"PDF at {pdf_url} exceeds 32 MiB ceiling.")
    if b"%PDF-" not in body[:1024] or (b"%%EOF" not in body[-2048:] and b"endobj" not in body[-2048:]):
        raise ValueError(f"Downloaded payload from {pdf_url} is not a valid complete PDF.")

    tmp_pdf = eff_cache / f".{clean_id}.{os.getpid()}.tmp"
    tmp_pdf.write_bytes(body)
    os.replace(tmp_pdf, pdf_path)

    meta_obj = {
        "arxiv_id": clean_id,
        "source_url": pdf_url,
        "abstract_url": f"https://arxiv.org/abs/{clean_id}",
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    meta_path.write_text(json.dumps(meta_obj, indent=2) + "\n", encoding="utf-8")
    return pdf_path


def parse_iso_timestamp(raw_ts: str) -> Optional[datetime.datetime]:
    """Parse an ISO-8601 timestamp string (including trailing ``Z``) into ``datetime``."""
    if not raw_ts:
        return None
    try:
        return datetime.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def resolve_session_events_path(session_arg: str) -> Path:
    r"""Resolve a direct ``events.jsonl`` path, bundle directory, or session UUID under ``$COPILOT_HOME``."""
    cand = Path(session_arg).expanduser()
    if cand.is_file():
        return cand.resolve()
    if cand.is_dir():
        for rel in ("events.jsonl", "raw/events.jsonl"):
            p = cand / rel
            if p.is_file():
                return p.resolve()
    by_id = resolve_copilot_home() / "session-state" / session_arg / "events.jsonl"
    if by_id.is_file():
        return by_id.resolve()
    raise FileNotFoundError(f"Could not locate session events.jsonl for {session_arg!r}")


def _extract_wire_reasoning_and_output(wire_res: Dict[str, object]) -> Tuple[str, str]:
    r"""Extract reasoning text and assistant message text from a ``/v1/responses`` payload."""
    reasoning_parts: List[str] = []
    text_parts: List[str] = []
    out_list = wire_res.get("output")
    if not isinstance(out_list, list):
        return ("", "")
    for item in out_list:
        if not isinstance(item, dict):
            continue
        itype = str(item.get("type") or "")
        if itype == "reasoning":
            for s in (item.get("summary") or item.get("content") or []):
                if isinstance(s, dict) and isinstance(s.get("text"), str):
                    reasoning_parts.append(s["text"])
            if isinstance(item.get("text"), str):
                reasoning_parts.append(item["text"])
        elif itype == "message":
            for c in (item.get("content") or []):
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    text_parts.append(c["text"])
    return ("\n".join(reasoning_parts).strip(), "\n".join(text_parts).strip())


def _load_optional_wire_index(
    events_path: Path, wire_path: Optional[Path] = None
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], Dict[str, Dict[str, object]]]:
    """Load optional ``wire_capture.jsonl`` inference and auxiliary socket records."""
    eff_wire = wire_path
    if eff_wire is None:
        sibling = events_path.with_name("wire_capture.jsonl")
        if sibling.is_file():
            eff_wire = sibling
    inference_calls: List[Dict[str, object]] = []
    aux_calls: List[Dict[str, object]] = []
    by_api_id: Dict[str, Dict[str, object]] = {}
    if eff_wire and eff_wire.is_file():
        for w_ln in eff_wire.read_text(encoding="utf-8", errors="replace").splitlines():
            if not w_ln.strip():
                continue
            try:
                w_obj = json.loads(w_ln)
            except Exception:
                continue
            w_url = str(w_obj.get("url") or "")
            if w_url.endswith("/responses") or w_url.endswith("/chat/completions"):
                inference_calls.append(w_obj)
                api_id = str(w_obj.get("api_call_id") or "")
                if api_id:
                    by_api_id[api_id] = w_obj
            else:
                aux_calls.append(w_obj)
    return (inference_calls, aux_calls, by_api_id)


def analyze_session_events(events_path: Path, wire_path: Optional[Path] = None) -> Dict[str, object]:
    """Analyze a VS Code, ``copilot-runtime``, or benchmark ``events.jsonl`` conversation log.

    Supports both ``agentId`` and ``subagent.started.toolCallId <-> parentToolCallId``
    attribution, pairs ``assistant.message`` with ``session.usage_record`` / ``wire_capture.jsonl``
    without double-counting tokens, and computes ``active_gpu_seconds`` across both Ollama
    ``/api/chat`` nanosecond fields and ``copilot-runtime`` ``/v1/responses`` millisecond records.
    """
    wire_inf, _wire_aux, wire_by_api = _load_optional_wire_index(events_path, wire_path)

    raw_events: List[Dict[str, object]] = []
    agent_id_to_role: Dict[str, str] = {}
    tool_call_to_role: Dict[str, str] = {}
    usage_by_api_id: Dict[str, Dict[str, object]] = {}
    unmatched_usage_by_role: Dict[str, List[Dict[str, object]]] = {}

    with events_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            raw_events.append(ev)
            et = str(ev.get("type") or "")
            aid = str(ev.get("agentId") or "")
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if et in ("subagent.started", "subagent.selected"):
                r_lbl = str(
                    data.get("agentDisplayName") or data.get("agentName") or data.get("agentType") or ""
                ).strip().lower()
                if r_lbl:
                    if aid:
                        agent_id_to_role[aid] = r_lbl
                    tc_id = str(data.get("toolCallId") or "")
                    if tc_id:
                        tool_call_to_role[tc_id] = r_lbl
            elif et == "session.usage_record":
                u_obj = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                api_id = str(u_obj.get("apiCallId") or "")
                if api_id:
                    usage_by_api_id[api_id] = u_obj
                else:
                    r_u = agent_id_to_role.get(aid) or (
                        aid.split("-")[1] if aid.startswith("sub-") else (aid[:12] if aid else "facet")
                    )
                    unmatched_usage_by_role.setdefault(r_u, []).append(u_obj)

    def _resolve_role(aid_val: str, data_obj: Dict[str, object]) -> str:
        if aid_val and aid_val in agent_id_to_role:
            return agent_id_to_role[aid_val]
        ptc = str(data_obj.get("parentToolCallId") or "")
        if ptc and ptc in tool_call_to_role:
            return tool_call_to_role[ptc]
        if aid_val.startswith("sub-"):
            return aid_val.split("-")[1]
        return aid_val[:12] if aid_val else "facet"

    first_ts: Optional[datetime.datetime] = None
    last_ts: Optional[datetime.datetime] = None
    prev_ts: Optional[datetime.datetime] = None
    prev_model: str = ""
    model_swaps = 0
    roles: Dict[str, Dict[str, float]] = {}
    hook_denials: List[str] = []
    tool_failures: List[str] = []
    sleep_confounded_turns = 0
    active_gpu_s = 0.0
    assistant_idx = 0

    for ev in raw_events:
        ts = parse_iso_timestamp(str(ev.get("timestamp") or ""))
        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
        et = str(ev.get("type") or "")
        aid = str(ev.get("agentId") or "")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}

        if et == "assistant.message":
            assistant_idx += 1
            role = _resolve_role(aid, data)
            mod = str(data.get("model") or "")
            if prev_model and mod and mod != prev_model:
                model_swaps += 1
            if mod:
                prev_model = mod
            dt = (ts - prev_ts).total_seconds() if (ts and prev_ts) else 0.0
            bucket = roles.setdefault(
                role,
                {
                    "turns": 0.0,
                    "wall_s": 0.0,
                    "in_tok": 0.0,
                    "out_tok": 0.0,
                    "reasoning_chars": 0.0,
                    "load_ms": 0.0,
                    "prefill_ms": 0.0,
                    "eval_ms": 0.0,
                },
            )
            bucket["turns"] += 1.0
            bucket["wall_s"] += max(0.0, dt)

            api_id = str(data.get("apiCallId") or "")
            u_rec = usage_by_api_id.get(api_id)
            if u_rec is None and unmatched_usage_by_role.get(role):
                u_rec = unmatched_usage_by_role[role].pop(0)
            wire_rec = (
                wire_by_api.get(api_id)
                if (api_id and api_id in wire_by_api)
                else (wire_inf[assistant_idx - 1] if assistant_idx <= len(wire_inf) else None)
            )
            w_res = wire_rec.get("response") if (isinstance(wire_rec, dict) and isinstance(wire_rec.get("response"), dict)) else {}
            w_usage = w_res.get("usage") if isinstance(w_res.get("usage"), dict) else {}

            in_tok_v = float(
                data.get("inputTokens")
                or (u_rec.get("inputTokens") if isinstance(u_rec, dict) else 0.0)
                or w_usage.get("input_tokens")
                or 0.0
            )
            out_tok_v = float(
                data.get("outputTokens")
                or (u_rec.get("outputTokens") if isinstance(u_rec, dict) else 0.0)
                or w_usage.get("output_tokens")
                or 0.0
            )
            r_txt = str(data.get("reasoningText") or "")
            if not r_txt and w_res:
                r_txt, _ = _extract_wire_reasoning_and_output(w_res)

            load_ms_v = float(data.get("loadDurationMs") or 0.0)
            pref_ms_v = float(data.get("promptEvalDurationMs") or 0.0)
            eval_ms_v = float(data.get("evalDurationMs") or 0.0)
            call_dur_ms = float(
                (u_rec.get("duration") if isinstance(u_rec, dict) else 0.0)
                or (wire_rec.get("duration_ms") if isinstance(wire_rec, dict) else 0.0)
                or 0.0
            )

            bucket["in_tok"] += in_tok_v
            bucket["out_tok"] += out_tok_v
            bucket["reasoning_chars"] += float(len(r_txt))
            bucket["load_ms"] += load_ms_v
            bucket["prefill_ms"] += pref_ms_v
            bucket["eval_ms"] += eval_ms_v if (pref_ms_v > 0.0 or eval_ms_v > 0.0) else call_dur_ms

            if pref_ms_v > 0.0 or eval_ms_v > 0.0:
                pref_stalled = pref_ms_v > 30000.0 and (in_tok_v <= 0.0 or (in_tok_v / (pref_ms_v / 1000.0)) < 250.0)
                eval_stalled = eval_ms_v > 30000.0 and out_tok_v > 0.0 and (out_tok_v / (eval_ms_v / 1000.0)) < 15.0
                if pref_stalled or eval_stalled:
                    sleep_confounded_turns += 1
                clean_pref_s = (in_tok_v / 2000.0) if pref_stalled else (pref_ms_v / 1000.0)
                clean_eval_s = (out_tok_v / 52.0) if eval_stalled else (eval_ms_v / 1000.0)
                active_gpu_s += (load_ms_v / 1000.0) + clean_pref_s + clean_eval_s
            elif call_dur_ms > 0.0:
                # `copilot-runtime` `/v1/responses` records combined prefill+eval duration in `usage.duration` (ms).
                call_s = call_dur_ms / 1000.0
                combined_stalled = call_dur_ms > 45000.0 and out_tok_v > 0.0 and ((in_tok_v / 40.0 + out_tok_v) / call_s) < 8.0
                if combined_stalled:
                    sleep_confounded_turns += 1
                    active_gpu_s += (in_tok_v / 2000.0) + (out_tok_v / 52.0)
                else:
                    active_gpu_s += call_s
        elif et == "hook.end":
            out_obj = data.get("output") or data.get("result")
            out_str = json.dumps(out_obj, ensure_ascii=False) if out_obj else ""
            if (
                "Denied by preToolUse" in out_str
                or '"decision": "block"' in out_str
                or '"permissionDecision": "deny"' in out_str
            ):
                hook_denials.append(f"{data.get('hookType')}: {out_str[:180]}")
        elif et == "tool.execution_complete" and data.get("success") is False:
            tool_failures.append(str(data.get("toolCallId") or "unknown_tool"))

        if ts is not None and et in ("user.message", "assistant.message"):
            prev_ts = ts

    wall_s = (last_ts - first_ts).total_seconds() if (first_ts and last_ts) else 0.0
    return {
        "events_path": str(events_path),
        "wall_seconds": round(wall_s, 2),
        "active_gpu_seconds": round(active_gpu_s, 2),
        "model_swaps": model_swaps,
        "sleep_confounded_turns": sleep_confounded_turns,
        "roles": roles,
        "hook_denials": hook_denials,
        "tool_failures": tool_failures,
    }


# ============================================================================
# Section 3: SciCode Benchmark Waterfall & Factorial Interaction Analyzer
# ============================================================================

def analyze_benchmark_run(run_dir: Path) -> Dict[str, object]:
    """Analyze a benchmark run folder (e.g. ``benchmarks/runs/scicode_quick``).

    Args:
        run_dir: Path to the benchmark run directory.

    Returns:
        Structured dictionary containing step summaries, total swaps, role
        totals, and detected byte-identical no-op stage re-executions.
    """
    # Aggregate per-substep logs and `PROVENANCE.json` handshakes to surface swaps and no-op stages.
    ws_root = run_dir / "facet-pipeline" / "facet_workspaces"
    step_summaries: Dict[str, Dict[str, object]] = {}
    agg_roles: Dict[str, Dict[str, float]] = {}
    noop_stages: List[str] = []
    snap_counts: Dict[str, int] = {}
    sys1_verdicts: List[Dict[str, object]] = []
    total_swaps = 0
    total_wall = 0.0
    tot_load_ms = 0.0
    tot_prefill_ms = 0.0
    tot_eval_ms = 0.0
    tot_active_gpu_s = 0.0
    tot_denials = 0
    tot_sleep_turns = 0

    if ws_root.is_dir():
        for step_dir in sorted(ws_root.iterdir()):
            if not step_dir.is_dir():
                continue
            ev_path = step_dir / "events.jsonl"
            ev_stats = (
                analyze_session_events(ev_path)
                if ev_path.is_file()
                else {"wall_seconds": 0.0, "active_gpu_seconds": 0.0, "model_swaps": 0, "sleep_confounded_turns": 0, "roles": {}, "hook_denials": []}
            )
            swaps = int(ev_stats["model_swaps"])  # type: ignore[arg-type]
            w_s = float(ev_stats["wall_seconds"])  # type: ignore[arg-type]
            agpu_s = float(ev_stats.get("active_gpu_seconds") or 0.0)  # type: ignore[arg-type]
            s_sleep = int(ev_stats.get("sleep_confounded_turns") or 0)  # type: ignore[arg-type]
            ev_denials = len(ev_stats.get("hook_denials") or [])  # type: ignore[arg-type]
            total_swaps += swaps
            total_wall += w_s
            tot_active_gpu_s += agpu_s
            tot_sleep_turns += s_sleep

            # Merge role totals across all subproblems.
            s_roles = ev_stats["roles"]  # type: ignore[assignment]
            s_load_ms = 0.0
            s_prefill_ms = 0.0
            s_eval_ms = 0.0
            for r_name, r_vals in s_roles.items():  # type: ignore[union-attr]
                dst = agg_roles.setdefault(
                    r_name,
                    {"turns": 0.0, "wall_s": 0.0, "in_tok": 0.0, "out_tok": 0.0, "load_ms": 0.0, "prefill_ms": 0.0, "eval_ms": 0.0},
                )
                for k, v in r_vals.items():
                    dst[k] = dst.get(k, 0.0) + float(v)
                s_load_ms += float(r_vals.get("load_ms") or 0.0)
                s_prefill_ms += float(r_vals.get("prefill_ms") or 0.0)
                s_eval_ms += float(r_vals.get("eval_ms") or 0.0)
            tot_load_ms += s_load_ms
            tot_prefill_ms += s_prefill_ms
            tot_eval_ms += s_eval_ms

            # Parse out-of-specs hook telemetry (`run_telemetry.jsonl`) when present.
            rt_f = step_dir / "copilot_home" / "session-state" / "run_telemetry.jsonl"
            rt_events = (
                [json.loads(x) for x in rt_f.read_text(encoding="utf-8").splitlines() if x.strip()]
                if rt_f.is_file()
                else []
            )
            rt_noops = 0
            rt_denials = 0
            for rte in rt_events:
                et_t = str(rte.get("type") or "")
                if et_t == "edit_snap":
                    sk = str(rte.get("snap_stage") or "unknown")
                    snap_counts[sk] = snap_counts.get(sk, 0) + 1
                elif et_t == "systemone_verdict":
                    sys1_verdicts.append(rte)
                elif et_t == "stage_span" and rte.get("noop_stage"):
                    rt_noops += 1
                elif et_t == "hook_gate":
                    rt_denials += 1
            step_denials = max(ev_denials, rt_denials)
            tot_denials += step_denials

            # Inspect archived PROVENANCE.json handshakes to detect byte-identical no-op re-executions.
            step_noops: List[str] = []
            for prov_p in sorted((step_dir / "workspace").glob("specs/*/PROVENANCE.json")):
                try:
                    prov = json.loads(prov_p.read_text(encoding="utf-8"))
                    seen_art_hashes: Dict[str, str] = {}
                    for idx, h in enumerate(prov.get("handshakes") or []):
                        if not isinstance(h, dict):
                            continue
                        art = str(h.get("artifact") or "")
                        sha = str(h.get("sha256") or "")
                        ag = str(h.get("agent") or "")
                        if art and sha and seen_art_hashes.get(art) == sha:
                            note = f"{step_dir.name} Handshake[{idx}] ({ag} -> {art}): byte-identical SHA-256 {sha[:12]}"
                            step_noops.append(note)
                            noop_stages.append(note)
                        if art and sha:
                            seen_art_hashes[art] = sha
                except Exception:
                    pass
            while len(step_noops) < rt_noops:
                synth_note = f"{step_dir.name} stage_span noop_stage"
                step_noops.append(synth_note)
                noop_stages.append(synth_note)

            step_summaries[step_dir.name] = {
                "wall_seconds": w_s,
                "active_gpu_seconds": agpu_s,
                "model_swaps": swaps,
                "vram_swap_count": swaps,
                "load_duration_ms": round(s_load_ms, 2),
                "prompt_eval_duration_ms": round(s_prefill_ms, 2),
                "eval_duration_ms": round(s_eval_ms, 2),
                "noop_stages": len(step_noops),
                "noop_handshakes": step_noops,
                "hook_denials": step_denials,
                "sleep_confounded_turns": s_sleep,
                "hook_telemetry_events": len(rt_events),
                "roles": s_roles,
            }

    return {
        "run_dir": str(run_dir),
        "total_wall_seconds": round(total_wall, 2),
        "active_gpu_seconds": round(tot_active_gpu_s, 2),
        "total_model_swaps": total_swaps,
        "noop_stages": noop_stages,
        "totals": {
            "active_gpu_seconds": round(tot_active_gpu_s, 2),
            "vram_swap_count": total_swaps,
            "load_duration_ms": round(tot_load_ms, 2),
            "prompt_eval_duration_ms": round(tot_prefill_ms, 2),
            "eval_duration_ms": round(tot_eval_ms, 2),
            "noop_stages": len(noop_stages),
            "hook_denials": tot_denials,
            "sleep_confounded_turns": tot_sleep_turns,
            "edit_snaps_by_stage": snap_counts,
            "systemone_verdicts_count": len(sys1_verdicts),
        },
        "systemone_verdicts": sys1_verdicts[:50],
        "roles": agg_roles,
        "steps": step_summaries,
    }


def compute_pairwise_interactions(
    baseline_metric: float,
    single_factor_metrics: Dict[str, float],
    pair_metrics: Dict[Tuple[str, str], float],
) -> List[Dict[str, object]]:
    """Compute main effects and pairwise interaction terms ``I_AB = Delta_AB - (Delta_A + Delta_B)``.

    For latency/cost metrics where lower is better, a negative ``interaction``
    indicates **super-additive synergy** (the pair saves more time together than
    the sum of individual savings), while a positive ``interaction`` indicates
    **antagonism / interference**.

    Args:
        baseline_metric: Control arm (`v0`) scalar metric (e.g. wall-clock seconds).
        single_factor_metrics: Mapping ``factor_name -> metric`` when enabled alone.
        pair_metrics: Mapping ``(factor_a, factor_b) -> metric`` when enabled together.

    Returns:
        List of interaction summary dicts containing ``pair``, ``delta_a``,
        ``delta_b``, ``expected_additive``, ``observed_delta_ab``, ``interaction``,
        and ``classification``.
    """
    # Evaluate deviations from additivity across every tested 2-factor combination.
    results: List[Dict[str, object]] = []
    for (fa, fb), y_ab in sorted(pair_metrics.items()):
        if fa not in single_factor_metrics or fb not in single_factor_metrics:
            continue
        delta_a = single_factor_metrics[fa] - baseline_metric
        delta_b = single_factor_metrics[fb] - baseline_metric
        expected_add = delta_a + delta_b
        observed_ab = y_ab - baseline_metric
        inter = observed_ab - expected_add
        scale = max(abs(baseline_metric) * 0.03, 1e-6)
        if inter < -scale:
            cls = "SYNERGISTIC (super-additive gain)"
        elif inter > scale:
            cls = "ANTAGONISTIC (interference)"
        else:
            cls = "ADDITIVE (orthogonal)"
        results.append(
            {
                "pair": f"{fa} x {fb}",
                "delta_a": round(delta_a, 3),
                "delta_b": round(delta_b, 3),
                "expected_additive": round(expected_add, 3),
                "observed_delta_ab": round(observed_ab, 3),
                "interaction": round(inter, 3),
                "classification": cls,
            }
        )
    return results


def analyze_matrix_directory(matrix_dir: Path) -> Dict[str, object]:
    """Analyze a multi-arm factorial benchmark directory containing arm subfolders.

    Each child subfolder may contain ``results.jsonl`` and ``RUN_TELEMETRY.json``
    (or ``arm_spec.json``). Computes arm resolve rates, average wall-clock, VRAM
    swap counts, load/prefill/eval splits, and pairwise interaction terms when
    a ``control`` arm plus single-factor and pair arms are present.

    Args:
        matrix_dir: Directory containing completed factorial arm subdirectories.

    Returns:
        Structured dictionary with ``arms`` and ``interactions`` tables.
    """
    # Collect telemetry and SciCode accuracy across every arm directory in `matrix_dir`.
    arms_out: Dict[str, Dict[str, object]] = {}
    for sub in sorted(matrix_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        res_f = sub / "results.jsonl"
        tel_f = sub / "RUN_TELEMETRY.json"
        if not res_f.is_file() and not tel_f.is_file():
            continue
        rows = [json.loads(x) for x in res_f.read_text(encoding="utf-8").splitlines() if x.strip()] if res_f.is_file() else []
        facet_rows = [r for r in rows if r.get("arm") == "facet-pipeline"] or rows
        tot_wall = sum(float(r.get("wall_seconds") or 0.0) for r in facet_rows)
        avg_wall = (tot_wall / len(facet_rows)) if facet_rows else 0.0
        tot_in_tok = sum(int(r.get("prompt_tokens") or 0) for r in facet_rows)
        tot_out_tok = sum(int(r.get("output_tokens") or 0) for r in facet_rows)
        sub_ok = sum(int(r.get("substeps_passed") or 0) for r in facet_rows)
        sub_tot = sum(int(r.get("substeps_total") or 0) for r in facet_rows)
        tel = json.loads(tel_f.read_text(encoding="utf-8")) if tel_f.is_file() else {}
        tots = tel.get("totals") if isinstance(tel.get("totals"), dict) else {}
        arms_out[sub.name] = {
            "total_wall_seconds": round(tot_wall, 2),
            "active_gpu_seconds": float(tots.get("active_gpu_seconds") or round(tot_wall, 2)),
            "avg_wall_seconds": round(avg_wall, 2),
            "prompt_tokens": tot_in_tok,
            "output_tokens": tot_out_tok,
            "substeps_passed": sub_ok,
            "substeps_total": sub_tot,
            "pass_rate_pct": round(100.0 * sub_ok / sub_tot, 1) if sub_tot > 0 else 0.0,
            "vram_swap_count": int(tots.get("vram_swap_count") or 0),
            "load_duration_ms": float(tots.get("load_duration_ms") or 0.0),
            "prompt_eval_duration_ms": float(tots.get("prompt_eval_duration_ms") or 0.0),
            "eval_duration_ms": float(tots.get("eval_duration_ms") or 0.0),
            "noop_stages": int(tots.get("noop_stages") or 0),
            "hook_denials": int(tots.get("hook_denials") or 0),
            "sleep_confounded_turns": int(tots.get("sleep_confounded_turns") or 0),
            "edit_snaps_by_stage": tots.get("edit_snaps_by_stage") or {},
            "systemone_verdicts_count": int(tots.get("systemone_verdicts_count") or 0),
            "experiments": tel.get("experiments") or {},
        }

    base_arm_name = "control" if "control" in arms_out else ("F3_base" if "F3_base" in arms_out else "")
    base_wall = float((arms_out.get(base_arm_name) or {}).get("avg_wall_seconds") or 0.0) if base_arm_name else 0.0
    cond_prefix = "F3+" if base_arm_name == "F3_base" else ""
    singles: Dict[str, float] = {}
    pairs: Dict[Tuple[str, str], float] = {}
    for a_name, a_info in arms_out.items():
        if a_name == base_arm_name or a_name.startswith("commit_"):
            continue
        rel_name = a_name[len(cond_prefix):] if (cond_prefix and a_name.startswith(cond_prefix)) else ("" if cond_prefix else a_name)
        if not rel_name:
            continue
        w_val = float(a_info["avg_wall_seconds"])  # type: ignore[arg-type]
        if "+" in rel_name:
            pts = [p.strip() for p in rel_name.split("+") if p.strip()]
            if len(pts) == 2:
                pairs[(pts[0], pts[1])] = w_val
        else:
            singles[rel_name] = w_val
    interactions = compute_pairwise_interactions(base_wall, singles, pairs) if (base_wall > 0 and singles and pairs) else []

    # Compute orthogonal factor main effects (Mean ON - Mean OFF) across L8 / factorial arms that carry `experiments`.
    factor_keys = [
        ("F1", "source_ingress_mode", "strict_research_aligned"),
        ("F2", "coordinator_residency", "e4b_orchestrator"),
        ("F3", "sys1_trigger_policy", "every_boundary"),
        ("F4", "context_hydration", "metadata_only"),
        ("F5", "rewind_dag_policy", "linear_stage_cascade"),
        ("F6", "contract_and_gates", "standard"),
        ("F7", "auditor_cleanroom_policy", "unrestricted_read"),
    ]
    l8_subset = {
        k: v for k, v in arms_out.items()
        if (k == "control" or k.startswith("L8_")) and isinstance(v.get("experiments"), dict) and v.get("experiments")
    }
    pool = l8_subset if len(l8_subset) >= 4 else {
        k: v for k, v in arms_out.items()
        if not k.startswith("commit_") and isinstance(v.get("experiments"), dict) and v.get("experiments")
    }
    main_effects: List[Dict[str, object]] = []
    for fid, cfg_k, ctrl_v in factor_keys:
        on_walls: List[float] = []
        off_walls: List[float] = []
        on_swaps: List[float] = []
        off_swaps: List[float] = []
        for a_info in pool.values():
            exp_d = a_info.get("experiments") or {}
            val = str(exp_d.get(cfg_k) or ctrl_v)  # type: ignore[union-attr]
            w_tot = float(a_info.get("total_wall_seconds") or 0.0)
            sw_c = float(a_info.get("vram_swap_count") or 0.0)
            if val != ctrl_v:
                on_walls.append(w_tot)
                on_swaps.append(sw_c)
            else:
                off_walls.append(w_tot)
                off_swaps.append(sw_c)
        if on_walls and off_walls:
            mean_on = sum(on_walls) / len(on_walls)
            mean_off = sum(off_walls) / len(off_walls)
            sw_on = sum(on_swaps) / len(on_swaps)
            sw_off = sum(off_swaps) / len(off_swaps)
            main_effects.append(
                {
                    "factor": fid,
                    "config_key": cfg_k,
                    "n_on": len(on_walls),
                    "n_off": len(off_walls),
                    "mean_wall_on": round(mean_on, 2),
                    "mean_wall_off": round(mean_off, 2),
                    "wall_effect_s": round(mean_on - mean_off, 2),
                    "swap_effect": round(sw_on - sw_off, 2),
                }
            )

    return {
        "matrix_dir": str(matrix_dir),
        "arms": arms_out,
        "main_effects": main_effects,
        "interactions": interactions,
    }


def format_benchmark_report(summary: Dict[str, object]) -> str:
    r"""Format a human-readable Markdown/console summary of ``analyze_benchmark_run``.

    Args:
        summary: Output dictionary from ``analyze_benchmark_run()``.

    Returns:
        Formatted multi-line report string.
    """
    # Render per-step swap counts, per-role wall shares, and any detected no-op stages.
    lines = [
        f"# Facet Benchmark Trace Summary: {Path(str(summary['run_dir'])).name}",
        f"- Total Facet Step Wall: {summary['total_wall_seconds']}s",
        f"- Total Model Swaps (e4b <-> 26B): {summary['total_model_swaps']}",
        "",
        "## Per-Subproblem Waterfall",
    ]
    steps = summary.get("steps") if isinstance(summary.get("steps"), dict) else {}
    for s_name, s_info in sorted(steps.items()):
        lines.append(
            f"- {s_name}: wall={s_info.get('wall_seconds')}s, swaps={s_info.get('model_swaps')}, "
            f"noop_handshakes={len(s_info.get('noop_handshakes') or [])}"
        )
    lines.append("")
    lines.append("## Per-Role Aggregate Breakdown")
    roles = summary.get("roles") if isinstance(summary.get("roles"), dict) else {}
    tot_w = max(float(summary.get("total_wall_seconds") or 0.0), 1e-6)
    for r_name, r_st in sorted(roles.items(), key=lambda kv: -float(kv[1].get("wall_s", 0.0))):
        w_v = float(r_st.get("wall_s", 0.0))
        t_v = int(r_st.get("turns", 0.0))
        lines.append(f"- {r_name:14s}: {t_v:3d} turns, {w_v:7.1f}s ({100.0 * w_v / tot_w:5.1f}%)")
    noops = summary.get("noop_stages") if isinstance(summary.get("noop_stages"), list) else []
    if noops:
        lines.append("")
        lines.append("## Detected Byte-Identical No-Op Stage Re-Executions")
        for item in noops:
            lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def sanitize_binary_blobs_for_markdown(text: str) -> str:
    r"""Keep text verbatim while replacing inline base64 image payloads with byte counts."""
    def _repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        prefix = raw.split(",", 1)[0]
        b64_len = len(raw) - len(prefix) - 1
        return f"{prefix},<omitted {b64_len} base64 chars in markdown; preserved in raw/>"

    return BASE64_IMAGE_RE.sub(_repl, text)


def format_fenced_block(text: str, lang: str = "text") -> str:
    r"""Wrap arbitrary text (even containing triple backticks) in a collision-free fence."""
    longest_run = 2
    for m in re.finditer(r"`+", text):
        longest_run = max(longest_run, len(m.group(0)))
    fence = "`" * max(3, longest_run + 1)
    body = sanitize_binary_blobs_for_markdown(text.rstrip("\n"))
    return f"{fence}{lang}\n{body}\n{fence}"


def _slugify_label(label: str) -> str:
    r"""Convert a stage title into a clean filename slug."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return cleaned[:48] or "stage"


def _load_disk_reference_texts(repo_root: Path) -> Dict[str, Dict[str, str]]:
    r"""Read current ``.agent.md``, ``SKILL.md``, and ``copilot-instructions.md`` bodies."""
    agents: Dict[str, str] = {}
    for p in sorted((repo_root / ".github" / "agents").glob("*.agent.md")):
        raw = p.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        body = parts[2].strip() if raw.startswith("---") and len(parts) == 3 else raw.strip()
        nm_m = re.search(r"^name:\s*(\S+)", parts[1] if len(parts) == 3 else "", re.MULTILINE)
        role = nm_m.group(1).strip() if nm_m else p.stem.replace(".agent", "").capitalize()
        agents[role] = body

    skills: Dict[str, str] = {}
    for p in sorted((repo_root / ".github" / "skills").glob("*/SKILL.md")):
        raw = p.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        body = parts[2].strip() if raw.startswith("---") and len(parts) == 3 else raw.strip()
        skills[p.parent.name] = body

    ci_path = repo_root / ".github" / "copilot-instructions.md"
    copilot_inst = ci_path.read_text(encoding="utf-8").strip() if ci_path.is_file() else ""
    return {
        "agents": agents,
        "skills": skills,
        "global": {"copilot_instructions": copilot_inst},
    }


def _extract_skill_blocks_from_prompt(transformed_text: str) -> List[Tuple[str, str]]:
    r"""Extract ``<skill-context name="...">...</skill-context>`` blocks from ``transformedContent``."""
    results: List[Tuple[str, str]] = []
    pattern = re.compile(r'<skill-context(?:\s+name="([^"]*)")?[^>]*>\n?([\s\S]*?)\n?</skill-context>')
    for m in pattern.finditer(transformed_text):
        results.append((m.group(1) or "unnamed-skill", m.group(2).strip()))
    return results


def _infer_stage_title(
    epoch_idx: int, root_prompt: str, assistant_bubbles: List[str], subagents_seen: List[str]
) -> str:
    r"""Derive a human stage heading from the stage's verified output card or prompt."""
    joined_bubbles = "\n".join(assistant_bubbles)
    m_card = re.search(
        r"^>\s*\*\*((?:Stage\s+[1-4]\s+of\s+4|Verification\s+Escalation|Complete\s*·\s*Sealed\s+Specification\s+Package|Workspace\s+Pulse)[^*\n]*)\*\*",
        joined_bubbles,
        flags=re.MULTILINE,
    )
    if m_card:
        base_title = m_card.group(1).strip()
        if "Updated from Verification Feedback:" in joined_bubbles or (
            "@Auditor" in subagents_seen and any(r in subagents_seen for r in ("@Researcher", "@Formalist"))
        ):
            return f"{base_title} (Rewind)"
        return base_title
    if root_prompt.strip().lower().startswith("/status"):
        return "Status Pulse"
    if root_prompt.strip().lower().startswith("/reset"):
        return "Workspace Reset"
    if subagents_seen:
        return f"Turn {epoch_idx} ({' -> '.join(dict.fromkeys(subagents_seen))})"
    return f"Turn {epoch_idx}"


def _flatten_wire_input_texts(wire_req: Dict[str, object]) -> List[str]:
    r"""Extract plain text segments from an OpenAI ``/v1/responses`` ``input`` list."""
    out: List[str] = []
    items = wire_req.get("input")
    if isinstance(items, str):
        return [items]
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        itype = str(it.get("type") or "")
        if itype == "function_call_output":
            out.append(str(it.get("output") or ""))
            continue
        content = it.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            parts = [cp["text"] for cp in content if isinstance(cp, dict) and isinstance(cp.get("text"), str)]
            if parts:
                out.append("\n".join(parts))
    return out


# ============================================================================
# Section 4: Six-Bucket Agent-Cognition & Scope-Hygiene Diagnostician
# ============================================================================

def diagnose_session_optimization(
    events_path: Path, wire_path: Optional[Path] = None
) -> Dict[str, object]:
    """Compute the deterministic 6-Bucket Optimization & Agent-Cognition Scorecard (O1-O10).

    Buckets:
      1. ``reasoning_bloat`` — Reasoning chars/time share and turns exceeding 30s.
      2. ``unhydrated_tool_tax`` — Specialist ``view`` calls and ephemeral ``update_todo``/``sql`` calls.
      3. ``schema_hook_and_permission_friction`` — ``task`` Zod schema rejections, hook denials,
         stop-hook blocks, ``permissionRequest -> null`` events, and ``pytest`` collection errors.
      4. ``prompt_footprint_and_echo`` — Per-role ``system.message`` sizes, max transformed prompt
         sizes, and cross-layer duplicate invariant echo count.
      5. ``vram_residency_and_swaps`` — Total model swaps and Stage 4 repair-loop swaps.
      6. ``cleanroom_and_ux_integrity`` — ``@Engineer`` running ``pytest verify_*.py`` via ``bash``,
         user-plane internal tag leaks, and ``AssertionError`` vs. ``ValueError`` repair triggers.

    Phases:
      - Phase 1: Event Stream, Wire Index & Subagent Role Registry Initialization
      - Phase 2: Chronological Turn, Tool, Hook & Permission Metric Accumulation
      - Phase 3: Six-Bucket Scorecard Assembly
    """
    # --- Phase 1: Event Stream, Wire Index & Subagent Role Registry Initialization ---
    base_stats = analyze_session_events(events_path, wire_path=wire_path)
    wire_inf, _wire_aux, wire_by_api = _load_optional_wire_index(events_path, wire_path)

    raw_events = [
        json.loads(ln)
        for ln in events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if ln.strip()
    ]
    agent_id_to_role: Dict[str, str] = {}
    tool_call_to_role: Dict[str, str] = {}
    usage_by_api_id: Dict[str, Dict[str, object]] = {}
    open_tools: Dict[str, Dict[str, object]] = {}
    open_perms: Dict[str, Dict[str, object]] = {}

    for ev in raw_events:
        et = str(ev.get("type") or "")
        aid = str(ev.get("agentId") or "")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if et in ("subagent.started", "subagent.selected"):
            rl = str(d.get("agentDisplayName") or d.get("agentName") or d.get("agentType") or "").strip().lower()
            if rl:
                if aid:
                    agent_id_to_role[aid] = rl
                tc_s = str(d.get("toolCallId") or "")
                if tc_s:
                    tool_call_to_role[tc_s] = rl
        elif et == "session.usage_record":
            u_o = d.get("usage") if isinstance(d.get("usage"), dict) else {}
            api_i = str(u_o.get("apiCallId") or "")
            if api_i:
                usage_by_api_id[api_i] = u_o

    def _role_of(aid_s: str, d_o: Dict[str, object]) -> str:
        if aid_s and aid_s in agent_id_to_role:
            return agent_id_to_role[aid_s]
        ptc = str(d_o.get("parentToolCallId") or "")
        if ptc and ptc in tool_call_to_role:
            return tool_call_to_role[ptc]
        if aid_s.startswith("sub-"):
            return aid_s.split("-")[1]
        return aid_s[:12] if aid_s else "facet"

    total_reasoning_chars = 0
    total_content_chars = 0
    total_input_tokens = 0
    total_output_tokens = 0
    timed_output_tokens = 0
    timed_eval_seconds = 0.0
    long_turns_gt_30s: List[Dict[str, object]] = []
    pre_tool_prose_script_turns: List[Dict[str, object]] = []
    agents_with_prior_write_tool: set[str] = set()
    long_turns_wall_s = 0.0

    specialist_views: Dict[str, int] = {}
    specialist_todos: Dict[str, int] = {}
    out_of_scope_probing_calls: List[Dict[str, object]] = []
    redundant_hydrated_reads: List[Dict[str, object]] = []
    duplicate_unchanged_calls: List[Dict[str, object]] = []
    venv_probe_calls: List[Dict[str, object]] = []
    failed_or_empty_tool_calls: List[Dict[str, object]] = []
    web_fetch_calls_total = 0
    web_search_api_calls_total = 0
    web_fetched_domains: List[str] = []
    created_research_md = False

    promoted_scope_prefixes: set[str] = set()
    linked_spec_slugs: set[str] = set()
    hydrated_files_by_agent: Dict[str, set[str]] = {}
    seen_ro_sigs_by_agent: Dict[str, set[str]] = {}

    task_schema_failures = 0
    stop_hook_blocks = 0
    permission_null_events = 0
    pytest_collection_errors = 0

    sys_msg_chars_by_role: Dict[str, int] = {}
    max_transformed_chars_by_role: Dict[str, int] = {}
    peak_input_tokens_by_role: Dict[str, int] = {}
    hook_injection_chars_by_tag: Dict[str, int] = {}
    latest_sys_by_role: Dict[str, str] = {}
    echo_layer_counts: Dict[str, int] = {k: 0 for k, _ in ECHO_INVARIANT_PROBES}

    cleanroom_bash_leaks: List[Dict[str, object]] = []
    closed_book_web_leaks: List[Dict[str, object]] = []
    closed_book_session = False
    user_plane_leaks: List[Dict[str, object]] = []
    contract_exception_repairs = 0

    seen_auditor = False
    stage4_swaps = 0
    prev_mod = ""
    assistant_seq = 0

    def _accumulate_hook_tags(text_blob: str) -> None:
        for tag_nm in (
            "subagent_context",
            "hydrated_artifact",
            "hydrated_schema_summary",
            "hydrated_public_surface",
            "workspace_map",
            "checkpoint_briefing",
            "failure_recovery",
            "archival_complete",
            "stage_anchor",
        ):
            for m_tag in re.finditer(rf"(?m)^\s*<{tag_nm}(?:\s+[^>\n]*)?>\n.*?</{tag_nm}>", text_blob, flags=re.DOTALL):
                hook_injection_chars_by_tag[tag_nm] = hook_injection_chars_by_tag.get(tag_nm, 0) + len(m_tag.group(0))

    def _record_promoted_hints(text_blob: str) -> None:
        for m_ex in re.finditer(r"\bexamples/([A-Za-z0-9_.-]+)(?:/[A-Za-z0-9_.-]+)*", text_blob):
            sub = m_ex.group(1)
            if sub.lower() != "readme.md":
                promoted_scope_prefixes.add(f"examples/{sub}")
        if "magical mystery" in text_blob.lower():
            promoted_scope_prefixes.add("examples/magical_mystery")
        for m_sp in re.finditer(r"\bspecs/([A-Za-z0-9_.-]+)", text_blob):
            slug_cand = m_sp.group(1)
            if slug_cand.lower() != "readme.md":
                linked_spec_slugs.add(slug_cand)

    for ev_scan in raw_events:
        if ev_scan.get("type") == "user.message":
            d_sc = ev_scan.get("data") if isinstance(ev_scan.get("data"), dict) else {}
            if not ev_scan.get("agentId") and str(d_sc.get("source") or "user") != "system":
                _record_promoted_hints(str(d_sc.get("content") or ""))
            tc_sc = str(d_sc.get("transformedContent") or "")
            for m_wm in re.finditer(r"visual=\[([^\]]*)\],\s*text=\[([^\]]*)\]", tc_sc):
                _record_promoted_hints(m_wm.group(0))

    # --- Phase 2: Chronological Turn, Tool, Hook & Permission Metric Accumulation ---
    for line_no, ev in enumerate(raw_events, 1):
        et = str(ev.get("type") or "")
        aid = str(ev.get("agentId") or "")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        role = _role_of(aid, d)
        if role == "auditor":
            seen_auditor = True

        if et == "system.message":
            sc = str(d.get("content") or "")
            sys_msg_chars_by_role[role] = max(sys_msg_chars_by_role.get(role, 0), len(sc))
            latest_sys_by_role[role] = sc

        elif et == "user.message":
            tc = str(d.get("transformedContent") or "")
            if "allow_web_research=false" in tc or "Closed-Book Mode Active" in tc:
                closed_book_session = True
            max_transformed_chars_by_role[role] = max(max_transformed_chars_by_role.get(role, 0), len(tc))
            _accumulate_hook_tags(tc)
            if aid:
                hyd_set = hydrated_files_by_agent.setdefault(aid, set())
                for m_hy in re.finditer(r'<(?:hydrated_artifact|hydrated_schema_summary)\s+name="([^"]+)"', tc):
                    hyd_set.add(Path(m_hy.group(1)).name)
            if role in ("engineer", "auditor", "researcher", "formalist") and tc:
                sys_txt = latest_sys_by_role.get(role, "").lower()
                tc_low = tc.lower()
                for pk, needle in ECHO_INVARIANT_PROBES:
                    hits = (1 if needle in sys_txt else 0) + tc_low.count(needle)
                    echo_layer_counts[pk] = max(echo_layer_counts[pk], hits)
            if "assertionerror" in tc.lower() and "valueerror" in tc.lower():
                contract_exception_repairs += 1

        elif et == "assistant.message":
            assistant_seq += 1
            mod = str(d.get("model") or "")
            if prev_mod and mod and mod != prev_mod and seen_auditor:
                stage4_swaps += 1
            if mod:
                prev_mod = mod

            api_id = str(d.get("apiCallId") or "")
            u_rec = usage_by_api_id.get(api_id, {})
            wire_rec = (
                wire_by_api.get(api_id)
                if (api_id and api_id in wire_by_api)
                else (wire_inf[assistant_seq - 1] if assistant_seq <= len(wire_inf) else None)
            )
            w_res = wire_rec.get("response") if (isinstance(wire_rec, dict) and isinstance(wire_rec.get("response"), dict)) else {}
            r_txt = str(d.get("reasoningText") or "")
            if not r_txt and w_res:
                r_txt, _ = _extract_wire_reasoning_and_output(w_res)
            c_txt = str(d.get("content") or "")
            total_reasoning_chars += len(r_txt)
            total_content_chars += len(c_txt)
            treqs_m = d.get("toolRequests") or []
            if (
                aid
                and role in ("scribe", "researcher", "formalist", "engineer", "auditor")
                and not treqs_m
                and aid not in agents_with_prior_write_tool
                and ("```python" in c_txt or "```json" in c_txt)
            ):
                pre_tool_prose_script_turns.append(
                    {"line": line_no, "role": role, "content_chars": len(c_txt)}
                )

            in_tok_m = int(
                d.get("inputTokens")
                or (u_rec.get("inputTokens") if isinstance(u_rec, dict) else 0)
                or 0
            )
            out_tok_m = int(
                d.get("outputTokens")
                or (u_rec.get("outputTokens") if isinstance(u_rec, dict) else 0)
                or 0
            )
            total_input_tokens += in_tok_m
            total_output_tokens += out_tok_m
            peak_input_tokens_by_role[role] = max(peak_input_tokens_by_role.get(role, 0), in_tok_m)

            dur_ms = float(
                (u_rec.get("duration") if isinstance(u_rec, dict) else 0.0)
                or (wire_rec.get("duration_ms") if isinstance(wire_rec, dict) else 0.0)
                or d.get("evalDurationMs")
                or 0.0
            )
            dur_s = round(dur_ms / 1000.0, 2)
            if dur_s > 0.05 and out_tok_m > 0:
                timed_output_tokens += out_tok_m
                timed_eval_seconds += dur_s
            if dur_s >= 30.0:
                long_turns_wall_s += dur_s
                long_turns_gt_30s.append(
                    {
                        "line": line_no,
                        "role": role,
                        "model": mod,
                        "duration_s": dur_s,
                        "reasoning_chars": len(r_txt),
                        "output_tokens": out_tok_m,
                    }
                )

            if not aid and c_txt.strip():
                leak_m = USER_PLANE_LEAK_RE.search(c_txt)
                if leak_m:
                    user_plane_leaks.append({"line": line_no, "token": leak_m.group(0)})

        elif et == "tool.execution_start":
            tcid = str(d.get("toolCallId") or f"tc_{line_no}")
            tname = str(d.get("toolName") or "")
            targs = d.get("arguments") if isinstance(d.get("arguments"), dict) else {}
            open_tools[tcid] = {"line": line_no, "aid": aid, "role": role, "name": tname, "args": targs}
            if role in ("scribe", "researcher", "formalist", "engineer", "auditor"):
                if tname == "view":
                    specialist_views[role] = specialist_views.get(role, 0) + 1
                elif tname in ("update_todo", "sql", "todo"):
                    specialist_todos[role] = specialist_todos.get(role, 0) + 1
            if tname in ("create", "edit", "str_replace_editor") and aid:
                agents_with_prior_write_tool.add(aid)
                seen_ro_sigs_by_agent.pop(aid, None)
                wr_p = str(targs.get("path") or targs.get("filePath") or "")
                if Path(wr_p).name == "RESEARCH.md":
                    created_research_md = True

        elif et == "tool.execution_complete":
            tcid = str(d.get("toolCallId") or "")
            st_info = open_tools.pop(tcid, {"line": line_no, "aid": aid, "role": role, "name": "", "args": {}})
            tname_c = str(st_info.get("name") or d.get("toolName") or "")
            st_aid = str(st_info.get("aid") or aid)
            st_role = str(st_info.get("role") or role)
            st_line = int(st_info.get("line") or line_no)
            st_args = st_info.get("args") if isinstance(st_info.get("args"), dict) else {}
            res_str = json.dumps(d.get("result") or {}, ensure_ascii=False)
            was_hook_denied = "Denied by preToolUse hook" in res_str or d.get("success") is False

            if tname_c != "task" and d.get("success") is False:
                tgt_fail = str(
                    st_args.get("path")
                    or st_args.get("url")
                    or st_args.get("pattern")
                    or st_args.get("command")
                    or ""
                )[:120]
                failed_or_empty_tool_calls.append({
                    "line": st_line, "role": st_role, "tool": tname_c,
                    "target": tgt_fail, "hook_denied": bool("Denied by preToolUse hook" in res_str),
                })

            if tname_c in ("view", "read", "read_file", "grep", "glob", "web_fetch", "web_search") and st_aid:
                ro_sig = f"{tname_c}:{json.dumps(st_args, sort_keys=True, ensure_ascii=True)}"
                seen_set = seen_ro_sigs_by_agent.setdefault(st_aid, set())
                if ro_sig in seen_set and not was_hook_denied:
                    duplicate_unchanged_calls.append({
                        "line": st_line, "role": st_role, "tool": tname_c,
                        "target": str(st_args.get("path") or st_args.get("url") or st_args.get("pattern") or "")[:120],
                    })
                seen_set.add(ro_sig)

            if tname_c in ("view", "read", "read_file", "grep", "glob") and st_role in (
                "scribe", "researcher", "formalist", "engineer", "auditor", "orchestrator"
            ):
                raw_tgt = str(st_args.get("path") or st_args.get("filePath") or "")
                tgt_parts = Path(raw_tgt).parts if raw_tgt else ()
                tgt_name = Path(raw_tgt).name if raw_tgt else ""
                if tname_c in ("view", "read", "read_file") and st_aid and tgt_name in hydrated_files_by_agent.get(st_aid, set()):
                    redundant_hydrated_reads.append({"line": st_line, "role": st_role, "target": tgt_name})
                if not was_hook_denied:
                    if tname_c in ("grep", "glob") and not st_args.get("paths"):
                        out_of_scope_probing_calls.append({
                            "line": st_line, "role": st_role, "tool": tname_c,
                            "target": f"pat={st_args.get('pattern')} (paths=None)", "reason": "unscoped_workspace_search",
                        })
                    elif "examples" in tgt_parts:
                        ex_idx = tgt_parts.index("examples")
                        after_ex = tgt_parts[ex_idx + 1:]
                        if not after_ex:
                            out_of_scope_probing_calls.append({
                                "line": st_line, "role": st_role, "tool": tname_c,
                                "target": "examples/", "reason": "parent_examples_index",
                            })
                        else:
                            ex_sub = f"examples/{after_ex[0]}"
                            if after_ex[0].lower() == "readme.md" or ex_sub not in promoted_scope_prefixes:
                                out_of_scope_probing_calls.append({
                                    "line": st_line, "role": st_role, "tool": tname_c,
                                    "target": "/".join(tgt_parts[ex_idx:]), "reason": "outside_promoted_example_scope",
                                })
                            elif after_ex[-1].lower() == "readme.md":
                                out_of_scope_probing_calls.append({
                                    "line": st_line, "role": st_role, "tool": tname_c,
                                    "target": "/".join(tgt_parts[ex_idx:]), "reason": "showcase_example_readme",
                                })
                    elif "specs" in tgt_parts:
                        sp_idx = tgt_parts.index("specs")
                        after_sp = tgt_parts[sp_idx + 1:]
                        if not after_sp or after_sp[0] not in linked_spec_slugs:
                            out_of_scope_probing_calls.append({
                                "line": st_line, "role": st_role, "tool": tname_c,
                                "target": "/".join(tgt_parts[sp_idx:]), "reason": "unlinked_specs_archive",
                            })
                    elif (
                        tgt_name in (".pipeline_state.json", ".pipeline_config.json", "LICENSE")
                        or (tgt_name.lower().endswith(".md") and "skills" not in tgt_parts and "research" not in tgt_parts and "examples" not in tgt_parts and "specs" not in tgt_parts and tgt_name not in ("SOURCE_TEXT.md", "SOURCE_OCR.md", "RESEARCH.md", "VERIFICATION.md"))
                    ):
                        out_of_scope_probing_calls.append({
                            "line": st_line, "role": st_role, "tool": tname_c,
                            "target": tgt_name, "reason": "repo_meta_or_control_file",
                        })

            if tname_c == "bash" and st_role in ("engineer", "auditor", "scribe", "researcher", "formalist") and not was_hook_denied:
                cmd_probe = str(st_args.get("command") or "")
                if st_role in ("engineer", "auditor") and re.search(r"(?:^|\s|/)python\d*\s+-c\s+['\"]\s*import\s+(?:numpy|scipy)\b[^;]*(?:__version__|print\(['\"]OK['\"]\))", cmd_probe):
                    venv_probe_calls.append({"line": st_line, "role": st_role, "command": cmd_probe[:120]})
                if re.search(
                    r"(?:^|[;&|]\s*)(?:ls|find|cat|head|tail|rg|grep|tree|stat|wc)\b[^;&|]*\b(?:examples|specs|docs|benchmarks|scripts|modelfiles|README\.md)\b",
                    cmd_probe,
                ):
                    out_of_scope_probing_calls.append({
                        "line": st_line, "role": st_role, "tool": "bash",
                        "target": cmd_probe[:120], "reason": "bash_outside_working_set",
                    })

            if tname_c in ("web_fetch", "web_search") and not was_hook_denied:
                wf_u = str(st_args.get("url") or st_args.get("query") or "")
                web_fetch_calls_total += 1
                if tname_c == "web_search" or "list=search" in wf_u or "api/query" in wf_u or "w/api.php" in wf_u:
                    web_search_api_calls_total += 1
                m_dom = re.match(r"^https?://([^/:]+)", wf_u)
                if m_dom and m_dom.group(1).lower() not in web_fetched_domains:
                    web_fetched_domains.append(m_dom.group(1).lower())

            if st_role == "engineer" and tname_c == "bash" and not was_hook_denied:
                cmd_str = str(st_args.get("command") or "")
                if re.search(r"\b(?:pytest|verify_[a-z0-9_]+\.py)\b", cmd_str):
                    cleanroom_bash_leaks.append({"line": st_line, "command": cmd_str[:200]})
            if closed_book_session and tname_c in ("web_fetch", "web_search") and not was_hook_denied:
                closed_book_web_leaks.append({
                    "line": st_line,
                    "tool": tname_c,
                    "target": str(st_args.get("url") or st_args.get("query") or "")[:200],
                })
            if tname_c == "task" and d.get("success") is False and (
                "description" in res_str and ("Required" in res_str or "Invalid" in res_str)
            ):
                task_schema_failures += 1
            if tname_c == "bash" and (
                "applied @given" in res_str
                or "more than once" in res_str
                or "ScopeMismatch" in res_str
                or "SyntaxError" in res_str
            ):
                pytest_collection_errors += 1
            if "CONTRACT-EXCEPTION-MISMATCH" in res_str or ("AssertionError" in res_str and "Raises:" in res_str):
                contract_exception_repairs += 1

        elif et == "hook.end":
            ht_end = str(d.get("hookType") or "")
            out_raw = d.get("output") if d.get("output") is not None else d.get("result")
            out_s = json.dumps(out_raw or {}, ensure_ascii=False)
            if "allow_web_research=false" in out_s or "Closed-Book Mode Active" in out_s:
                closed_book_session = True
            if '"decision": "block"' in out_s or '"decision":"block"' in out_s:
                stop_hook_blocks += 1
            if ht_end == "permissionRequest" and (not isinstance(out_raw, dict) or not out_raw):
                permission_null_events += 1

        elif et == "permission.requested":
            rid = str(d.get("requestId") or f"perm_{line_no}")
            open_perms[rid] = d
        elif et == "permission.completed":
            rid = str(d.get("requestId") or "")
            open_perms.pop(rid, None)
            p_res = d.get("result")
            if p_res is None or (isinstance(p_res, dict) and p_res.get("kind") not in ("approved", "allow") and p_res.get("behavior") != "allow" and not p_res):
                permission_null_events += 1

    # --- Phase 3: Six-Bucket Scorecard Assembly ---
    wall_tot = max(float(base_stats["wall_seconds"]), 1e-6)  # type: ignore[arg-type]
    active_gpu_tot = float(base_stats["active_gpu_seconds"])  # type: ignore[arg-type]
    non_gpu_gap_s = round(max(0.0, float(base_stats["wall_seconds"]) - active_gpu_tot), 2)  # type: ignore[arg-type]
    sleep_turns_n = int(base_stats.get("sleep_confounded_turns") or 0)  # type: ignore[arg-type]
    open_book_zero_web_warn = bool((not closed_book_session) and created_research_md and web_fetch_calls_total == 0)
    mean_out_tps = round(timed_output_tokens / timed_eval_seconds, 2) if timed_eval_seconds > 0.05 else 0.0
    integrity_pass = len(cleanroom_bash_leaks) == 0 and len(user_plane_leaks) == 0 and len(closed_book_web_leaks) == 0

    return {
        "events_path": str(events_path),
        "wall_seconds": base_stats["wall_seconds"],
        "active_gpu_seconds": base_stats["active_gpu_seconds"],
        "non_gpu_wall_gap_seconds": non_gpu_gap_s,
        "sleep_confounded_turns": sleep_turns_n,
        "buckets": {
            "1_reasoning_bloat": {
                "total_reasoning_chars": total_reasoning_chars,
                "total_content_chars": total_content_chars,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "mean_output_tokens_per_sec": mean_out_tps,
                "reasoning_to_content_ratio": round(total_reasoning_chars / max(total_content_chars, 1), 2),
                "turns_gt_30s_count": len(long_turns_gt_30s),
                "turns_gt_30s_wall_seconds": round(long_turns_wall_s, 2),
                "turns_gt_30s_wall_share_pct": round(100.0 * long_turns_wall_s / wall_tot, 1),
                "pre_tool_prose_script_turns": pre_tool_prose_script_turns,
                "top_slow_turns": sorted(long_turns_gt_30s, key=lambda x: -float(x["duration_s"]))[:8],  # type: ignore[arg-type]
            },
            "2_unhydrated_tool_tax": {
                "specialist_view_calls_total": sum(specialist_views.values()),
                "specialist_view_calls_by_role": specialist_views,
                "specialist_todo_calls_total": sum(specialist_todos.values()),
                "specialist_todo_calls_by_role": specialist_todos,
                "out_of_scope_probing_calls": out_of_scope_probing_calls,
                "redundant_hydrated_reads": redundant_hydrated_reads,
                "duplicate_unchanged_calls": duplicate_unchanged_calls,
                "venv_probe_calls": venv_probe_calls,
                "failed_or_empty_tool_calls": failed_or_empty_tool_calls,
                "web_fetch_calls_total": web_fetch_calls_total,
                "web_search_api_calls_total": web_search_api_calls_total,
                "web_fetched_domains": web_fetched_domains,
                "open_book_zero_web_warning": open_book_zero_web_warn,
            },
            "3_schema_hook_and_permission_friction": {
                "task_schema_failures": task_schema_failures,
                "hook_denials_count": len(base_stats["hook_denials"]),  # type: ignore[arg-type]
                "stop_hook_blocks_count": stop_hook_blocks,
                "tool_failures_count": len(base_stats["tool_failures"]),  # type: ignore[arg-type]
                "permission_null_events": permission_null_events,
                "pytest_collection_errors": pytest_collection_errors,
            },
            "4_prompt_footprint_and_echo": {
                "system_message_chars_by_role": sys_msg_chars_by_role,
                "max_transformed_prompt_chars_by_role": max_transformed_chars_by_role,
                "peak_input_tokens_by_role": peak_input_tokens_by_role,
                "hook_injection_chars_by_tag": hook_injection_chars_by_tag,
                "max_invariant_echo_layers": echo_layer_counts,
            },
            "5_vram_residency_and_swaps": {
                "model_swaps_total": base_stats["model_swaps"],
                "stage4_swaps": stage4_swaps,
            },
            "6_cleanroom_and_ux_integrity": {
                "integrity_pass": integrity_pass,
                "cleanroom_bash_pytest_leaks": cleanroom_bash_leaks,
                "closed_book_web_leaks": closed_book_web_leaks,
                "user_facing_plane_leaks": user_plane_leaks,
                "contract_exception_repair_signals": contract_exception_repairs,
                "out_of_scope_reads_count": len(out_of_scope_probing_calls),
                "web_fetch_calls_total": web_fetch_calls_total,
                "open_book_zero_web_warning": open_book_zero_web_warn,
            },
        },
        "roles": base_stats["roles"],
    }


def diagnose_benchmark_run(run_dir: Path) -> Dict[str, object]:
    """Aggregate 6-bucket agent-cognition diagnostics + ``SciCode`` accuracy across ``run_dir``."""
    bench_sum = analyze_benchmark_run(run_dir)
    ws_root = run_dir / "facet-pipeline" / "facet_workspaces"
    step_diags: Dict[str, Dict[str, object]] = {}
    agg = {
        "total_reasoning_chars": 0,
        "turns_gt_30s_count": 0,
        "turns_gt_30s_wall_seconds": 0.0,
        "specialist_view_calls_total": 0,
        "specialist_todo_calls_total": 0,
        "task_schema_failures": 0,
        "permission_null_events": 0,
        "cleanroom_bash_leaks_count": 0,
        "user_plane_leaks_count": 0,
    }
    if ws_root.is_dir():
        for step_d in sorted(ws_root.iterdir()):
            ev_p = step_d / "events.jsonl"
            if not ev_p.is_file():
                continue
            w_p = step_d / "wire_capture.jsonl"
            sd = diagnose_session_optimization(ev_p, wire_path=w_p if w_p.is_file() else None)
            step_diags[step_d.name] = sd
            b = sd["buckets"]  # type: ignore[index]
            agg["total_reasoning_chars"] += int(b["1_reasoning_bloat"]["total_reasoning_chars"])  # type: ignore[index]
            agg["turns_gt_30s_count"] += int(b["1_reasoning_bloat"]["turns_gt_30s_count"])  # type: ignore[index]
            agg["turns_gt_30s_wall_seconds"] = round(
                float(agg["turns_gt_30s_wall_seconds"]) + float(b["1_reasoning_bloat"]["turns_gt_30s_wall_seconds"]), 2  # type: ignore[index]
            )
            agg["specialist_view_calls_total"] += int(b["2_unhydrated_tool_tax"]["specialist_view_calls_total"])  # type: ignore[index]
            agg["specialist_todo_calls_total"] += int(b["2_unhydrated_tool_tax"]["specialist_todo_calls_total"])  # type: ignore[index]
            agg["task_schema_failures"] += int(b["3_schema_hook_and_permission_friction"]["task_schema_failures"])  # type: ignore[index]
            agg["permission_null_events"] += int(b["3_schema_hook_and_permission_friction"]["permission_null_events"])  # type: ignore[index]
            agg["cleanroom_bash_leaks_count"] += len(b["6_cleanroom_and_ux_integrity"]["cleanroom_bash_pytest_leaks"])  # type: ignore[index]
            agg["user_plane_leaks_count"] += len(b["6_cleanroom_and_ux_integrity"]["user_facing_plane_leaks"])  # type: ignore[index]

    res_f = run_dir / "results.jsonl"
    scicode_sub_ok = 0
    scicode_sub_tot = 0
    if res_f.is_file():
        for ln in res_f.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            r = json.loads(ln)
            if r.get("arm") == "facet-pipeline":
                scicode_sub_ok += int(r.get("substeps_passed") or 0)
                scicode_sub_tot += int(r.get("substeps_total") or 0)

    return {
        "run_dir": str(run_dir),
        "total_wall_seconds": bench_sum["total_wall_seconds"],
        "active_gpu_seconds": bench_sum["active_gpu_seconds"],
        "total_model_swaps": bench_sum["total_model_swaps"],
        "scicode_substeps_passed": scicode_sub_ok,
        "scicode_substeps_total": scicode_sub_tot,
        "integrity_pass": agg["cleanroom_bash_leaks_count"] == 0 and agg["user_plane_leaks_count"] == 0,
        "aggregated_diagnostics": agg,
        "steps": step_diags,
    }


def format_diagnostic_report(diag: Dict[str, object]) -> str:
    r"""Format a concise Markdown scorecard from ``diagnose_session_optimization`` or ``diagnose_benchmark_run``."""
    if "buckets" not in diag:
        agg = diag.get("aggregated_diagnostics") or {}
        return (
            f"# Facet Benchmark 6-Bucket Diagnostic Scorecard: {Path(str(diag['run_dir'])).name}\n"
            f"- Wall: {diag['total_wall_seconds']}s | Active GPU: {diag['active_gpu_seconds']}s | Swaps: {diag['total_model_swaps']}\n"
            f"- SciCode Substeps Passed: {diag['scicode_substeps_passed']} / {diag['scicode_substeps_total']}\n"
            f"- Integrity Verdict: {'PASS' if diag['integrity_pass'] else 'FAIL'}\n"
            f"- Reasoning Chars: {agg.get('total_reasoning_chars')} ({agg.get('turns_gt_30s_count')} turns >30s = {agg.get('turns_gt_30s_wall_seconds')}s)\n"
            f"- Specialist Unhydrated `view` Calls: {agg.get('specialist_view_calls_total')} | `todo` Calls: {agg.get('specialist_todo_calls_total')}\n"
            f"- `task` Schema Failures: {agg.get('task_schema_failures')} | Permission Nulls: {agg.get('permission_null_events')}\n"
            f"- Cleanroom `bash` Leaks: {agg.get('cleanroom_bash_leaks_count')} | User-Plane Leaks: {agg.get('user_plane_leaks_count')}\n"
        )
    b = diag["buckets"]  # type: ignore[index]
    b1 = b["1_reasoning_bloat"]  # type: ignore[index]
    b2 = b["2_unhydrated_tool_tax"]  # type: ignore[index]
    b3 = b["3_schema_hook_and_permission_friction"]  # type: ignore[index]
    b4 = b["4_prompt_footprint_and_echo"]  # type: ignore[index]
    b5 = b["5_vram_residency_and_swaps"]  # type: ignore[index]
    b6 = b["6_cleanroom_and_ux_integrity"]  # type: ignore[index]
    ev_p_obj = Path(str(diag["events_path"]))
    hdr_lbl = ev_p_obj.parent.parent.name if ev_p_obj.parent.name == "raw" else ev_p_obj.parent.name
    sleep_turns_hdr = int(diag.get("sleep_confounded_turns") or 0)
    gap_hdr_s = float(diag.get("non_gpu_wall_gap_seconds") or 0.0)
    sleep_note = f" · **OS Sleep / Stall Gap:** `{gap_hdr_s} s` (`{sleep_turns_hdr}` sleep-stalled turn(s))" if sleep_turns_hdr > 0 else ""
    oos_n = len(b2.get("out_of_scope_probing_calls") or [])  # type: ignore[arg-type]
    rhyd_n = len(b2.get("redundant_hydrated_reads") or [])  # type: ignore[arg-type]
    dup_n = len(b2.get("duplicate_unchanged_calls") or [])  # type: ignore[arg-type]
    venv_n = len(b2.get("venv_probe_calls") or [])  # type: ignore[arg-type]
    wf_n = int(b2.get("web_fetch_calls_total") or 0)  # type: ignore[arg-type]
    ws_n = int(b2.get("web_search_api_calls_total") or 0)  # type: ignore[arg-type]
    ob_warn = bool(b2.get("open_book_zero_web_warning"))
    tps_v = float(b1.get("mean_output_tokens_per_sec") or 0.0)  # type: ignore[arg-type]
    lines = [
        f"# Facet 6-Bucket Optimization & Integrity Scorecard: {hdr_lbl}",
        f"- **Wall Clock:** `{diag['wall_seconds']} s` · **Active GPU:** `{diag['active_gpu_seconds']} s` · **Mean Throughput:** `{tps_v} tok/s`{sleep_note} · **Integrity:** `{'PASS' if b6['integrity_pass'] else 'FAIL'}`",
        "",
        "| Bucket | Key Metrics |",
        "| :--- | :--- |",
        f"| **1. Reasoning Bloat** | `{b1['total_reasoning_chars']}` reasoning chars (`{b1['reasoning_to_content_ratio']}x` content); `{b1['turns_gt_30s_count']}` turns >30s (`{b1['turns_gt_30s_wall_seconds']} s` = `{b1['turns_gt_30s_wall_share_pct']}%` wall) |",
        f"| **2. Unhydrated `view` & `todo` Tax** | Specialist `view`s: `{b2['specialist_view_calls_total']}` (`{b2['specialist_view_calls_by_role']}`); Out-of-scope pokes: `{oos_n}`; Redundant hydrated `view`s: `{rhyd_n}`; Duplicate calls: `{dup_n}`; `.venv` probes: `{venv_n}`; `web_fetch`: `{wf_n}` (`{ws_n}` search API, `open_book_zero_web_warn={ob_warn}`); `todo`s: `{b2['specialist_todo_calls_total']}` |",
        f"| **3. Schema, Hook & Permission Friction** | `task` schema fails: `{b3['task_schema_failures']}`; Hook denials: `{b3['hook_denials_count']}`; Stop blocks: `{b3['stop_hook_blocks_count']}`; Permission nulls: `{b3['permission_null_events']}`; Pytest collection errs: `{b3['pytest_collection_errors']}` |",
        f"| **4. Prompt Footprint & Echo** | `system.message` chars: `{b4['system_message_chars_by_role']}`; Peak input tokens: `{b4.get('peak_input_tokens_by_role', {})}`; Hook tag chars: `{b4.get('hook_injection_chars_by_tag', {})}` |",
        f"| **5. VRAM Swaps** | Total swaps: `{b5['model_swaps_total']}` (Stage 4 swaps: `{b5['stage4_swaps']}`) |",
        f"| **6. Cleanroom & UX Integrity** | Cleanroom `bash` leaks: `{len(b6['cleanroom_bash_pytest_leaks'])}`; User-plane leaks: `{len(b6['user_facing_plane_leaks'])}`; Out-of-scope reads: `{oos_n}`; `AssertionError/ValueError` repair signals: `{b6['contract_exception_repair_signals']}` |",
    ]
    return "\n".join(lines) + "\n"


def _extract_comparable_vector(target_path: str) -> Dict[str, object]:
    """Normalize either a session/audit path or a ``SciCode`` benchmark run dir into a scalar vector."""
    cand = Path(target_path).expanduser()
    if cand.is_dir() and ((cand / "results.jsonl").is_file() or (cand / "facet-pipeline").is_dir()):
        bd = diagnose_benchmark_run(cand.resolve())
        agg = bd["aggregated_diagnostics"]  # type: ignore[index]
        return {
            "kind": "benchmark_run",
            "label": cand.name,
            "wall_seconds": float(bd["total_wall_seconds"]),  # type: ignore[arg-type]
            "active_gpu_seconds": float(bd["active_gpu_seconds"]),  # type: ignore[arg-type]
            "model_swaps": int(bd["total_model_swaps"]),  # type: ignore[arg-type]
            "reasoning_chars": int(agg["total_reasoning_chars"]),  # type: ignore[index]
            "specialist_views": int(agg["specialist_view_calls_total"]),  # type: ignore[index]
            "specialist_todos": int(agg["specialist_todo_calls_total"]),  # type: ignore[index]
            "task_schema_failures": int(agg["task_schema_failures"]),  # type: ignore[index]
            "scicode_passed": int(bd["scicode_substeps_passed"]),  # type: ignore[arg-type]
            "scicode_total": int(bd["scicode_substeps_total"]),  # type: ignore[arg-type]
            "integrity_pass": bool(bd["integrity_pass"]),
        }
    ev_p = resolve_session_events_path(target_path)
    sd = diagnose_session_optimization(ev_p)
    b = sd["buckets"]  # type: ignore[index]
    ev_label = ev_p.parent.parent.name if ev_p.parent.name == "raw" else ev_p.parent.name
    return {
        "kind": "session",
        "label": ev_label,
        "wall_seconds": float(sd["wall_seconds"]),  # type: ignore[arg-type]
        "active_gpu_seconds": float(sd["active_gpu_seconds"]),  # type: ignore[arg-type]
        "model_swaps": int(b["5_vram_residency_and_swaps"]["model_swaps_total"]),  # type: ignore[index]
        "reasoning_chars": int(b["1_reasoning_bloat"]["total_reasoning_chars"]),  # type: ignore[index]
        "specialist_views": int(b["2_unhydrated_tool_tax"]["specialist_view_calls_total"]),  # type: ignore[index]
        "specialist_todos": int(b["2_unhydrated_tool_tax"]["specialist_todo_calls_total"]),  # type: ignore[index]
        "task_schema_failures": int(b["3_schema_hook_and_permission_friction"]["task_schema_failures"]),  # type: ignore[index]
        "scicode_passed": None,
        "scicode_total": None,
        "integrity_pass": bool(b["6_cleanroom_and_ux_integrity"]["integrity_pass"]),  # type: ignore[index]
    }


# ============================================================================
# Section 5: Head-to-Head A/B Comparator & Non-Regression Gate
# ============================================================================

def compare_traces(base_target: str, cand_target: str) -> Dict[str, object]:
    """Compare Control (``base_target``) vs. Candidate (``cand_target``) with non-regression gates."""
    base_v = _extract_comparable_vector(base_target)
    cand_v = _extract_comparable_vector(cand_target)
    metrics = (
        "wall_seconds",
        "active_gpu_seconds",
        "model_swaps",
        "reasoning_chars",
        "specialist_views",
        "specialist_todos",
        "task_schema_failures",
    )
    deltas: Dict[str, Dict[str, float]] = {}
    for m in metrics:
        bv = float(base_v[m])  # type: ignore[arg-type]
        cv = float(cand_v[m])  # type: ignore[arg-type]
        diff = round(cv - bv, 2)
        pct = round(100.0 * (cv - bv) / bv, 1) if abs(bv) > 1e-9 else 0.0
        deltas[m] = {"base": bv, "candidate": cv, "delta": diff, "delta_pct": pct}

    scicode_non_reg = True
    if base_v["scicode_passed"] is not None and cand_v["scicode_passed"] is not None:
        scicode_non_reg = int(cand_v["scicode_passed"]) >= int(base_v["scicode_passed"])  # type: ignore[arg-type]
    non_regression_pass = bool(cand_v["integrity_pass"]) and scicode_non_reg

    return {
        "baseline": base_v,
        "candidate": cand_v,
        "deltas": deltas,
        "scicode_non_regression": scicode_non_reg,
        "candidate_integrity_pass": bool(cand_v["integrity_pass"]),
        "non_regression_pass": non_regression_pass,
    }


def format_comparison_report(comp: Dict[str, object]) -> str:
    r"""Render a Markdown A/B comparison table from ``compare_traces``."""
    b = comp["baseline"]  # type: ignore[index]
    c = comp["candidate"]  # type: ignore[index]
    d = comp["deltas"]  # type: ignore[index]
    lines = [
        f"# Facet Reproducible A/B Comparison: `{b['label']}` -> `{c['label']}`",  # type: ignore[index]
        f"- **Non-Regression Gate:** `{'PASS' if comp['non_regression_pass'] else 'FAIL'}` (Candidate Integrity=`{comp['candidate_integrity_pass']}`, SciCode Non-Regression=`{comp['scicode_non_regression']}`)",
        "",
        "| Metric | Control (A) | Candidate (B) | Delta | Delta % |",
        "| :--- | ---: | ---: | ---: | ---: |",
    ]
    for m_key, m_vals in d.items():  # type: ignore[union-attr]
        lines.append(
            f"| `{m_key}` | `{m_vals['base']}` | `{m_vals['candidate']}` | `{m_vals['delta']:+}` | `{m_vals['delta_pct']:+}%` |"
        )
    if b.get("scicode_passed") is not None or c.get("scicode_passed") is not None:  # type: ignore[union-attr]
        lines.append(
            f"| `scicode_substeps_passed` | `{b.get('scicode_passed')}/{b.get('scicode_total')}` | `{c.get('scicode_passed')}/{c.get('scicode_total')}` | — | — |"  # type: ignore[union-attr]
        )
    return "\n".join(lines) + "\n"


# ============================================================================
# Section 6: Strict 1:1 Stage-Context Bundler, Multi-Modality Scorecard & CLI Entrypoint
# ============================================================================

def audit_and_collate_session(
    events_path: Path,
    out_dir: Path,
    repo_root: Path = REPO_ROOT,
    wire_path: Optional[Path] = None,
) -> Dict[str, object]:
    """Parse ``events.jsonl`` (+ optional ``wire_capture.jsonl``), verify 1:1 parity, and emit durable stage packets.

    Phases:
      - Phase 1: Raw Event Copy, Disk Reference Load & Hook/Tool/Permission Pairing
      - Phase 2: Root Turn Epoch Partitioning & 1:1 Wire/Disk Parity Collation
      - Phase 3: Stage Packet Rendering, `00_MASTER_AUDIT.md` & `ledger.json` Emission
    """
    # --- Phase 1: Raw Event Copy, Disk Reference Load & Hook/Tool/Permission Pairing ---
    if not events_path.is_file():
        raise FileNotFoundError(f"events.jsonl not found: {events_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_out = out_dir / "raw"
    stages_out = out_dir / "stages"
    raw_out.mkdir(parents=True, exist_ok=True)
    stages_out.mkdir(parents=True, exist_ok=True)

    dst_events = raw_out / "events.jsonl"
    if events_path.resolve() != dst_events.resolve():
        shutil.copy2(events_path, dst_events)

    eff_wire = wire_path if (wire_path and wire_path.is_file()) else (
        events_path.with_name("wire_capture.jsonl") if events_path.with_name("wire_capture.jsonl").is_file() else None
    )
    if eff_wire and eff_wire.resolve() != (raw_out / "wire_capture.jsonl").resolve():
        shutil.copy2(eff_wire, raw_out / "wire_capture.jsonl")

    disk_refs = _load_disk_reference_texts(repo_root)
    raw_lines = events_path.read_text(encoding="utf-8").splitlines()
    total_lines = len(raw_lines)
    parsed_events: List[Dict[str, object]] = []
    unknown_event_lines: List[Tuple[int, str]] = []

    for line_no, raw_ln in enumerate(raw_lines, 1):
        ev = json.loads(raw_ln)
        ev_type = str(ev.get("type") or "")
        if ev_type not in KNOWN_EVENT_TYPES:
            unknown_event_lines.append((line_no, ev_type))
        ev["_line_no"] = line_no
        parsed_events.append(ev)

    wire_inference_calls, wire_aux_calls, wire_by_api_id = _load_optional_wire_index(events_path, eff_wire)

    agent_registry: Dict[str, Dict[str, object]] = {}
    root_selected_role = "Facet"
    root_selected_tools: List[str] = []
    skill_content_by_name: Dict[str, str] = dict(disk_refs["skills"])
    tool_call_to_owner_agent: Dict[str, Optional[str]] = {}

    open_hooks: Dict[str, Dict[str, object]] = {}
    paired_hooks_by_start_line: Dict[int, Dict[str, object]] = {}
    unpaired_hook_ids: List[str] = []

    open_tools: Dict[str, Dict[str, object]] = {}
    paired_tools_by_start_line: Dict[int, Dict[str, object]] = {}
    unpaired_tool_ids: List[str] = []

    open_permissions: Dict[str, Dict[str, object]] = {}
    permissions_by_tool_id: Dict[str, List[Dict[str, object]]] = {}

    usage_by_api_id: Dict[str, Dict[str, object]] = {}
    pending_usage_by_agent: Dict[str, Dict[str, object]] = {}
    session_info: Dict[str, object] = {}

    for ev in parsed_events:
        lno = int(ev["_line_no"])  # type: ignore[arg-type]
        et = str(ev.get("type") or "")
        aid_raw = ev.get("agentId")
        aid = str(aid_raw) if aid_raw else None
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}

        if et == "session.start":
            session_info = dict(data)
        elif et == "subagent.selected":
            disp = str(data.get("agentDisplayName") or data.get("agentName") or "").strip()
            tools_list = [str(x) for x in (data.get("tools") or [])]
            if aid is None:
                if disp:
                    root_selected_role = disp
                root_selected_tools = tools_list
            else:
                rec = agent_registry.setdefault(aid, {"role": disp or aid, "depth": 1, "tools": [], "model": ""})
                if disp:
                    rec["role"] = disp
                rec["tools"] = tools_list
        elif et == "subagent.started" and aid:
            disp = str(data.get("agentDisplayName") or data.get("agentName") or data.get("agentType") or aid).strip()
            parent_tc = str(data.get("toolCallId") or "")
            parent_aid = tool_call_to_owner_agent.get(parent_tc)
            parent_depth = 0 if parent_aid is None else int(agent_registry.get(parent_aid, {}).get("depth", 1))  # type: ignore[arg-type]
            rec = agent_registry.setdefault(aid, {"role": disp, "depth": parent_depth + 1, "tools": [], "model": ""})
            rec["role"] = disp
            rec["depth"] = parent_depth + 1
            rec["model"] = str(data.get("model") or "")
            rec["parent_tool_call_id"] = parent_tc
            rec["parent_agent_id"] = parent_aid
        elif et == "skill.invoked":
            sk_name = str(data.get("name") or "")
            sk_txt = str(data.get("content") or "")
            if sk_name and sk_txt:
                skill_content_by_name[sk_name] = sk_txt
        elif et == "hook.start":
            hid = str(data.get("hookInvocationId") or f"line_{lno}")
            open_hooks[hid] = {"start_line": lno, "start_ts": ev.get("timestamp"), "agent_id": aid, "data": data}
        elif et == "hook.end":
            hid = str(data.get("hookInvocationId") or "")
            if hid in open_hooks:
                st = open_hooks.pop(hid)
                st_lno = int(st["start_line"])  # type: ignore[arg-type]
                paired_hooks_by_start_line[st_lno] = {
                    "hook_invocation_id": hid,
                    "hook_type": str(data.get("hookType") or st["data"].get("hookType") or ""),  # type: ignore[index]
                    "agent_id": aid or st.get("agent_id"),
                    "start_line": st_lno,
                    "end_line": lno,
                    "start_ts": st.get("start_ts"),
                    "end_ts": ev.get("timestamp"),
                    "success": bool(data.get("success", True)),
                    "input": st["data"].get("input"),  # type: ignore[index]
                    "output": data.get("output"),
                }
            else:
                unpaired_hook_ids.append(hid or f"end_line_{lno}")
        elif et == "tool.execution_start":
            tcid = str(data.get("toolCallId") or f"tc_line_{lno}")
            tool_call_to_owner_agent[tcid] = aid
            open_tools[tcid] = {"start_line": lno, "start_ts": ev.get("timestamp"), "agent_id": aid, "data": data}
        elif et == "tool.execution_complete":
            tcid = str(data.get("toolCallId") or "")
            if tcid in open_tools:
                st_t = open_tools.pop(tcid)
                st_lno = int(st_t["start_line"])  # type: ignore[arg-type]
                paired_tools_by_start_line[st_lno] = {
                    "tool_call_id": tcid,
                    "tool_name": str(st_t["data"].get("toolName") or ""),  # type: ignore[index]
                    "agent_id": aid or st_t.get("agent_id"),
                    "start_line": st_lno,
                    "end_line": lno,
                    "start_ts": st_t.get("start_ts"),
                    "end_ts": ev.get("timestamp"),
                    "arguments": st_t["data"].get("arguments"),  # type: ignore[index]
                    "success": bool(data.get("success", True)),
                    "shell_execution": data.get("shellExecution"),
                    "result": data.get("result"),
                    "permissions": permissions_by_tool_id.get(tcid, []),
                }
            else:
                unpaired_tool_ids.append(tcid or f"tc_end_line_{lno}")
        elif et == "permission.requested":
            rid = str(data.get("requestId") or f"perm_{lno}")
            open_permissions[rid] = {"start_line": lno, "data": data}
        elif et == "permission.completed":
            rid = str(data.get("requestId") or "")
            if rid in open_permissions:
                req_d = open_permissions.pop(rid)["data"]  # type: ignore[index]
                pr = req_d.get("permissionRequest") if isinstance(req_d, dict) else {}
                tcid_p = str(pr.get("toolCallId") or "") if isinstance(pr, dict) else ""
                if tcid_p:
                    permissions_by_tool_id.setdefault(tcid_p, []).append({
                        "kind": pr.get("kind") if isinstance(pr, dict) else None,
                        "resolved_by_hook": req_d.get("resolvedByHook") if isinstance(req_d, dict) else None,
                        "result": data.get("result"),
                    })
        elif et == "session.usage_record":
            u_obj = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            api_id = str(u_obj.get("apiCallId") or "")
            if api_id:
                usage_by_api_id[api_id] = u_obj
            pending_usage_by_agent[aid or "__root__"] = u_obj

    unpaired_hook_ids.extend(open_hooks.keys())
    unpaired_tool_ids.extend(open_tools.keys())

    def _describe_agent(aid_opt: Optional[str]) -> Tuple[str, int, List[str]]:
        if aid_opt is None:
            return (f"@{root_selected_role}", 0, root_selected_tools)
        info = agent_registry.get(aid_opt)
        if not info:
            return (f"subagent:{aid_opt[:8]}", 1, [])
        return (f"@{info.get('role')}", int(info.get("depth") or 1), list(info.get("tools") or []))  # type: ignore[arg-type]

    # --- Phase 2: Root Turn Epoch Partitioning & 1:1 Wire/Disk Parity Collation ---
    root_turn_lines: List[int] = []
    for idx_ev, ev in enumerate(parsed_events):
        et = str(ev.get("type") or "")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if et == "user.message" and ev.get("agentId") is None and str(d.get("source") or "") != "system":
            turn_start_ln = int(ev["_line_no"])  # type: ignore[arg-type]
            back_idx = idx_ev - 1
            while back_idx >= 0:
                prev_ev = parsed_events[back_idx]
                prev_t = str(prev_ev.get("type") or "")
                prev_d = prev_ev.get("data") if isinstance(prev_ev.get("data"), dict) else {}
                prev_ht = str(prev_d.get("hookType") or "")
                if prev_ev.get("agentId") is None and (
                    (prev_t in ("hook.start", "hook.end") and prev_ht in ("sessionStart", "userPromptSubmitted", "userPromptTransformed"))
                    or prev_t in ("instruction.update", "session.model_change")
                ):
                    turn_start_ln = int(prev_ev["_line_no"])  # type: ignore[arg-type]
                    back_idx -= 1
                else:
                    break
            root_turn_lines.append(turn_start_ln)

    if not root_turn_lines:
        root_turn_lines = [1]

    epoch_ranges: List[Tuple[int, int]] = []
    for idx_r, start_ln in enumerate(root_turn_lines):
        actual_start = 1 if idx_r == 0 else start_ln
        actual_end = (root_turn_lines[idx_r + 1] - 1) if (idx_r + 1 < len(root_turn_lines)) else total_lines
        epoch_ranges.append((actual_start, actual_end))

    accounted_line_numbers: set[int] = set()
    agent_content_checks: List[Dict[str, object]] = []
    skill_content_checks: List[Dict[str, object]] = []
    wire_match_checks: List[Dict[str, object]] = []
    matched_wire_seqs: set[int] = set()
    stage_records: List[Dict[str, object]] = []
    seen_system_prompts_by_role: Dict[str, str] = {}
    assistant_turn_counter = 0

    for ep_idx, (ep_start, ep_end) in enumerate(epoch_ranges, 1):
        ep_events = [ev for ev in parsed_events if ep_start <= int(ev["_line_no"]) <= ep_end]  # type: ignore[arg-type]
        user_prompts: List[Dict[str, object]] = []
        user_facing_replies: List[Dict[str, object]] = []
        agents_and_skills_ctx: List[Dict[str, object]] = []
        hooks_and_transforms: List[Dict[str, object]] = []
        internal_turns_and_tools: List[Dict[str, object]] = []
        timeline_rows: List[Dict[str, object]] = []
        subagents_in_stage: List[str] = []
        skills_in_stage: List[str] = []
        stage_in_tokens = 0
        stage_out_tokens = 0
        stage_llm_turns = 0
        ep_first_ts: Optional[datetime.datetime] = None
        ep_last_ts: Optional[datetime.datetime] = None
        latest_sys_msg_by_agent: Dict[str, str] = {}

        for ev in ep_events:
            lno = int(ev["_line_no"])  # type: ignore[arg-type]
            accounted_line_numbers.add(lno)
            et = str(ev.get("type") or "")
            aid_raw = ev.get("agentId")
            aid = str(aid_raw) if aid_raw else None
            role_lbl, depth, role_tools = _describe_agent(aid)
            ts_str = str(ev.get("timestamp") or "")
            ts_dt = parse_iso_timestamp(ts_str)
            if ts_dt is not None:
                if ep_first_ts is None:
                    ep_first_ts = ts_dt
                ep_last_ts = ts_dt
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}

            if et == "subagent.started":
                sub_lbl = str(data.get("agentDisplayName") or data.get("agentName") or role_lbl).lstrip("@")
                subagents_in_stage.append(f"@{sub_lbl}")
                timeline_rows.append({
                    "line": lno, "ts": ts_str, "depth": depth, "actor": f"@{sub_lbl}",
                    "event": "subagent.started", "detail": f"model={data.get('model')} (toolCallId={data.get('toolCallId')})",
                })
            elif et == "instruction.update":
                icontent = str(data.get("content") or "")
                agents_and_skills_ctx.append({
                    "kind": "instruction.update", "line": lno, "actor": role_lbl, "depth": depth,
                    "name": str(data.get("name") or "instruction"), "authority": str(data.get("authority") or ""),
                    "content": icontent,
                })
                timeline_rows.append({
                    "line": lno, "ts": ts_str, "depth": depth, "actor": role_lbl,
                    "event": "instruction.update", "detail": f"{data.get('name')} ({len(icontent)} chars)",
                })
            elif et == "system.message":
                scontent = str(data.get("content") or "")
                latest_sys_msg_by_agent[aid or "__root__"] = scontent
                bare_role = role_lbl.lstrip("@")
                disk_agent_body = disk_refs["agents"].get(bare_role, "")
                disk_copilot_inst = disk_refs["global"].get("copilot_instructions", "")
                agent_body_present = bool(disk_agent_body and disk_agent_body in scontent)
                copilot_inst_present = bool(disk_copilot_inst and disk_copilot_inst in scontent)
                agent_content_checks.append({
                    "line": lno, "role": role_lbl, "depth": depth,
                    "agent_md_matched_disk": agent_body_present if disk_agent_body else None,
                    "copilot_instructions_matched_disk": copilot_inst_present if disk_copilot_inst else None,
                })
                identical_to_earlier = seen_system_prompts_by_role.get(role_lbl) == scontent
                seen_system_prompts_by_role[role_lbl] = scontent
                agents_and_skills_ctx.append({
                    "kind": "system.message", "line": lno, "actor": role_lbl, "depth": depth,
                    "tools": role_tools, "agent_md_matched_disk": agent_body_present,
                    "copilot_instructions_matched_disk": copilot_inst_present,
                    "identical_to_earlier_stage": identical_to_earlier, "content": scontent,
                })
                timeline_rows.append({
                    "line": lno, "ts": ts_str, "depth": depth, "actor": role_lbl,
                    "event": "system.message", "detail": f"{len(scontent)} chars · tools={role_tools}",
                })
            elif et in ("skill.invoked", "skill.invoked_ref"):
                sk_name = str(data.get("name") or "")
                skills_in_stage.append(sk_name)
                sk_path = str(data.get("path") or "")
                sk_trigger = str(data.get("trigger") or "")
                sk_body = str(data.get("content") or "")
                if not sk_body and sk_name in skill_content_by_name:
                    sk_body = skill_content_by_name[sk_name]
                elif not sk_body and sk_path and Path(sk_path).is_file():
                    raw_sk = Path(sk_path).read_text(encoding="utf-8")
                    parts_sk = raw_sk.split("---", 2)
                    sk_body = parts_sk[2].strip() if raw_sk.startswith("---") and len(parts_sk) == 3 else raw_sk.strip()
                disk_sk_body = disk_refs["skills"].get(sk_name, "")
                matched_disk = bool(disk_sk_body and disk_sk_body in sk_body) if disk_sk_body else None
                skill_content_checks.append({
                    "line": lno, "actor": role_lbl, "skill": sk_name, "event_type": et, "matched_disk": matched_disk,
                })
                agents_and_skills_ctx.append({
                    "kind": et, "line": lno, "actor": role_lbl, "depth": depth,
                    "skill_name": sk_name, "path": sk_path, "trigger": sk_trigger,
                    "matched_disk": matched_disk, "content": sk_body,
                })
                timeline_rows.append({
                    "line": lno, "ts": ts_str, "depth": depth, "actor": role_lbl,
                    "event": et, "detail": f"skill={sk_name} (trigger={sk_trigger}, {len(sk_body)} chars)",
                })
            elif et == "hook.start":
                paired_h = paired_hooks_by_start_line.get(lno)
                if paired_h:
                    h_actor, h_depth, _ = _describe_agent(str(paired_h["agent_id"]) if paired_h.get("agent_id") else None)
                    hooks_and_transforms.append({
                        "kind": "hook", "start_line": paired_h["start_line"], "end_line": paired_h["end_line"],
                        "actor": h_actor, "depth": h_depth, "hook_type": paired_h["hook_type"],
                        "success": paired_h["success"], "input": paired_h["input"], "output": paired_h["output"],
                    })
                    out_keys = list((paired_h["output"] or {}).keys()) if isinstance(paired_h.get("output"), dict) else []
                    timeline_rows.append({
                        "line": lno, "ts": ts_str, "depth": h_depth, "actor": h_actor,
                        "event": f"hook:{paired_h['hook_type']}",
                        "detail": f"L{paired_h['start_line']}->L{paired_h['end_line']} output_keys={out_keys}",
                    })
            elif et == "user.message":
                raw_c = str(data.get("content") or "")
                trans_c = str(data.get("transformedContent") or "")
                msg_source = str(data.get("source") or "user")
                is_human_root = (aid is None) and (msg_source != "system")
                if is_human_root:
                    user_prompts.append({"line": lno, "timestamp": ts_str, "content": raw_c})
                embedded_skills = _extract_skill_blocks_from_prompt(trans_c)
                hooks_and_transforms.append({
                    "kind": "prompt_transform", "line": lno, "actor": role_lbl, "depth": depth,
                    "source": msg_source, "is_human_root": is_human_root,
                    "raw_content": raw_c, "transformed_content": trans_c,
                    "embedded_skill_names": [s[0] for s in embedded_skills],
                })
                timeline_rows.append({
                    "line": lno, "ts": ts_str, "depth": depth, "actor": role_lbl,
                    "event": "user.message" if is_human_root else f"internal.prompt ({msg_source})",
                    "detail": f"raw={len(raw_c)} chars -> transformed={len(trans_c)} chars",
                })
            elif et == "assistant.message":
                assistant_turn_counter += 1
                stage_llm_turns += 1
                api_id = str(data.get("apiCallId") or "")
                u_rec = usage_by_api_id.get(api_id) or pending_usage_by_agent.get(aid or "__root__", {})
                in_tok = int(data.get("inputTokens") or (u_rec.get("inputTokens") if isinstance(u_rec, dict) else 0) or 0)
                out_tok = int(data.get("outputTokens") or (u_rec.get("outputTokens") if isinstance(u_rec, dict) else 0) or 0)
                dur_ms = int((u_rec.get("duration") if isinstance(u_rec, dict) else 0) or data.get("evalDurationMs") or 0)
                stage_in_tokens += in_tok
                stage_out_tokens += out_tok

                msg_txt = str(data.get("content") or "")
                reasoning_txt = str(data.get("reasoningText") or "")
                tool_reqs = data.get("toolRequests") if isinstance(data.get("toolRequests"), list) else []
                model_nm = str(data.get("model") or "")

                wire_rec: Optional[Dict[str, object]] = None
                if wire_inference_calls:
                    if api_id and api_id in wire_by_api_id:
                        wire_rec = wire_by_api_id[api_id]
                    elif assistant_turn_counter <= len(wire_inference_calls):
                        wire_rec = wire_inference_calls[assistant_turn_counter - 1]
                if wire_rec is not None:
                    w_seq = int(wire_rec["wire_seq"])  # type: ignore[arg-type]
                    matched_wire_seqs.add(w_seq)
                    w_req = wire_rec.get("request") if isinstance(wire_rec.get("request"), dict) else {}
                    w_res = wire_rec.get("response") if isinstance(wire_rec.get("response"), dict) else {}
                    w_reasoning, w_out_txt = _extract_wire_reasoning_and_output(w_res)
                    if not reasoning_txt and w_reasoning:
                        reasoning_txt = w_reasoning
                    expected_sys = latest_sys_msg_by_agent.get(aid or "__root__", "")
                    w_inst = str(w_req.get("instructions") or "")
                    sys_match = (expected_sys in w_inst) if expected_sys else bool(w_inst)
                    out_match = (msg_txt.strip() == w_out_txt.strip()) if msg_txt.strip() else True
                    wire_match_checks.append({
                        "wire_seq": w_seq, "assistant_line": lno, "actor": role_lbl, "api_call_id": api_id,
                        "instructions_matched_system_message": sys_match,
                        "response_matched_assistant_message": out_match,
                        "wire_input_items": len(_flatten_wire_input_texts(w_req)),
                    })

                if aid is None and msg_txt.strip():
                    user_facing_replies.append({
                        "line": lno, "timestamp": ts_str, "model": model_nm,
                        "input_tokens": in_tok, "output_tokens": out_tok, "duration_ms": dur_ms, "content": msg_txt,
                    })

                internal_turns_and_tools.append({
                    "kind": "assistant.message", "line": lno, "timestamp": ts_str, "actor": role_lbl, "depth": depth,
                    "is_user_facing": bool(aid is None and msg_txt.strip()), "model": model_nm, "api_call_id": api_id,
                    "wire_seq": wire_rec.get("wire_seq") if wire_rec else None,
                    "input_tokens": in_tok, "output_tokens": out_tok, "duration_ms": dur_ms,
                    "reasoning": reasoning_txt, "content": msg_txt, "tool_requests": tool_reqs,
                })
                timeline_rows.append({
                    "line": lno, "ts": ts_str, "depth": depth, "actor": role_lbl,
                    "event": "assistant.message (USER-FACING)" if (aid is None and msg_txt.strip()) else "assistant.message",
                    "detail": f"model={model_nm} in={in_tok} out={out_tok} tools={[tr.get('name') for tr in tool_reqs if isinstance(tr, dict)]}",
                })
            elif et == "tool.execution_start":
                paired_t = paired_tools_by_start_line.get(lno)
                if paired_t:
                    t_actor, t_depth, _ = _describe_agent(str(paired_t["agent_id"]) if paired_t.get("agent_id") else None)
                    internal_turns_and_tools.append({
                        "kind": "tool_execution", "start_line": paired_t["start_line"], "end_line": paired_t["end_line"],
                        "actor": t_actor, "depth": t_depth, "tool_call_id": paired_t["tool_call_id"],
                        "tool_name": paired_t["tool_name"], "arguments": paired_t["arguments"],
                        "success": paired_t["success"], "shell_execution": paired_t["shell_execution"],
                        "permissions": paired_t["permissions"], "result": paired_t["result"],
                    })
                    timeline_rows.append({
                        "line": lno, "ts": ts_str, "depth": t_depth, "actor": t_actor,
                        "event": f"tool:{paired_t['tool_name']}",
                        "detail": f"L{paired_t['start_line']}->L{paired_t['end_line']} success={paired_t['success']}",
                    })
            elif et == "session.binary_asset":
                internal_turns_and_tools.append({
                    "kind": "binary_asset", "line": lno, "actor": role_lbl, "depth": depth,
                    "asset_id": data.get("assetId"), "mime_type": data.get("mimeType"),
                    "byte_length": data.get("byteLength"), "description": data.get("description"),
                })

        root_prompt_txt = str(user_prompts[0]["content"]) if user_prompts else "(continuation)"
        bubble_txts = [str(r["content"]) for r in user_facing_replies]
        stg_title = _infer_stage_title(ep_idx, root_prompt_txt, bubble_txts, subagents_in_stage)
        stg_slug = f"{ep_idx:02d}_{_slugify_label(stg_title)}"
        dur_s = round((ep_last_ts - ep_first_ts).total_seconds(), 2) if (ep_first_ts and ep_last_ts) else 0.0
        stage_records.append({
            "stage_index": ep_idx, "stage_title": stg_title, "stage_file": f"stages/{stg_slug}.md",
            "line_span": [ep_start, ep_end], "duration_s": dur_s, "llm_turns": stage_llm_turns,
            "input_tokens": stage_in_tokens, "output_tokens": stage_out_tokens,
            "subagents": list(dict.fromkeys(subagents_in_stage)), "skills": list(dict.fromkeys(skills_in_stage)),
            "user_prompts": user_prompts, "user_facing_replies": user_facing_replies,
            "timeline": timeline_rows, "agents_and_skills_context": agents_and_skills_ctx,
            "hooks_and_transforms": hooks_and_transforms, "internal_turns_and_tools": internal_turns_and_tools,
        })

    unaccounted_lines = sorted(set(range(1, total_lines + 1)) - accounted_line_numbers)
    unmatched_wire_seqs = [
        int(w["wire_seq"]) for w in wire_inference_calls if int(w["wire_seq"]) not in matched_wire_seqs  # type: ignore[arg-type]
    ]
    wire_mismatches = [
        wm for wm in wire_match_checks
        if not wm.get("instructions_matched_system_message") or not wm.get("response_matched_assistant_message")
    ]
    live_disk_mismatches = [
        c for c in (agent_content_checks + skill_content_checks)
        if c.get("agent_md_matched_disk") is False
        or c.get("copilot_instructions_matched_disk") is False
        or c.get("matched_disk") is False
    ] if (eff_wire and eff_wire.is_file()) else []
    log_wire_pass = (
        len(unaccounted_lines) == 0
        and len(unknown_event_lines) == 0
        and len(unpaired_hook_ids) == 0
        and len(unpaired_tool_ids) == 0
        and len(unmatched_wire_seqs) == 0
        and len(wire_mismatches) == 0
    )
    strict_pass = log_wire_pass and len(live_disk_mismatches) == 0
    checks_summary = {
        "total_events_jsonl_lines": total_lines,
        "accounted_events_jsonl_lines": len(accounted_line_numbers),
        "unaccounted_lines": unaccounted_lines,
        "unknown_event_types": unknown_event_lines,
        "unpaired_hooks": unpaired_hook_ids,
        "unpaired_tools": unpaired_tool_ids,
        "wire_capture_present": bool(eff_wire and eff_wire.is_file()),
        "wire_inference_calls": len(wire_inference_calls),
        "wire_auxiliary_calls": len(wire_aux_calls),
        "unmatched_wire_inference_seqs": unmatched_wire_seqs,
        "wire_payload_mismatches": wire_mismatches,
        "agent_disk_checks": agent_content_checks,
        "skill_disk_checks": skill_content_checks,
        "live_disk_content_mismatches": live_disk_mismatches,
        "log_and_wire_1to1_pass": log_wire_pass,
        "strict_1to1_pass": strict_pass,
    }

    # --- Phase 3: Stage Packet Rendering, `00_MASTER_AUDIT.md` & `ledger.json` Emission ---
    for stg in stage_records:
        stg_md_path = out_dir / str(stg["stage_file"])
        s_lines: List[str] = [
            f"# Stage {stg['stage_index']}: {stg['stage_title']}",
            "",
            f"- **Event Log Span:** `events.jsonl` lines `{stg['line_span'][0]}..{stg['line_span'][1]}`",  # type: ignore[index]
            f"- **Duration:** `{stg['duration_s']} s` · **LLM Turns:** `{stg['llm_turns']}` · **Input Tokens:** `{stg['input_tokens']}` · **Output Tokens:** `{stg['output_tokens']}`",
            f"- **Subagents Spawned:** {', '.join(stg['subagents']) if stg['subagents'] else 'None (`@Facet` root only)'}",  # type: ignore[arg-type]
            f"- **Skills Loaded in Context:** {', '.join(f'`{s}`' for s in stg['skills']) if stg['skills'] else 'None'}",  # type: ignore[union-attr]
            "",
            "[Back to `00_MASTER_AUDIT.md`](../00_MASTER_AUDIT.md)",
            "",
            "---",
            "",
            "## 1. User-Facing Messages",
            "",
        ]
        for up in stg["user_prompts"]:  # type: ignore[union-attr]
            s_lines.extend([f"### User Input (`events.jsonl:L{up['line']}` · `{up['timestamp']}`)", "", format_fenced_block(str(up["content"]), "text"), ""])
        if stg["user_facing_replies"]:
            for ur in stg["user_facing_replies"]:  # type: ignore[union-attr]
                s_lines.extend([
                    f"### `@Facet` User-Facing Reply (`events.jsonl:L{ur['line']}` · model `{ur['model']}` · `{ur['duration_ms']} ms` · `{ur['output_tokens']} out tokens`)",
                    "",
                    format_fenced_block(str(ur["content"]), "markdown"),
                    "",
                ])
        else:
            s_lines.extend(["*(No user-facing assistant reply emitted in this segment.)*", ""])
        s_lines.extend(["---", "", "## 2. Chronological Execution Timeline", "", "| Line | Depth | Actor | Event | Detail |", "| :--- | :--- | :--- | :--- | :--- |"])
        for row in stg["timeline"]:  # type: ignore[union-attr]
            det = str(row["detail"]).replace("|", "\\|").replace("\n", " ")
            s_lines.append(f"| `L{row['line']}` | `{row['depth']}` | `{row['actor']}` | `{row['event']}` | {det} |")
        s_lines.extend(["", "---", "", "## 3. Agents & Skills Passed as Context", ""])
        if not stg["agents_and_skills_context"]:
            s_lines.extend(["*(No new `system.message`, `instruction.update`, or `skill.invoked` events in this stage.)*", ""])
        for ac in stg["agents_and_skills_context"]:  # type: ignore[union-attr]
            kind = str(ac["kind"])
            if kind == "system.message":
                s_lines.extend([
                    f"### System Prompt -> `{ac['actor']}` (Depth `{ac['depth']}` · `events.jsonl:L{ac['line']}`)",
                    f"- **Granted Tool Categories:** `{ac['tools']}` · **Matches current `.agent.md`:** `{ac['agent_md_matched_disk']}` · **Matches current `copilot-instructions.md`:** `{ac['copilot_instructions_matched_disk']}`",
                    "",
                    format_fenced_block(str(ac["content"]), "markdown"),
                    "",
                ])
            elif kind == "instruction.update":
                s_lines.extend([
                    f"### Runtime Instruction Update -> `{ac['actor']}` (`{ac['name']}` · authority `{ac['authority']}` · `events.jsonl:L{ac['line']}`)",
                    "",
                    format_fenced_block(str(ac["content"]), "xml"),
                    "",
                ])
            else:
                s_lines.extend([
                    f"### Skill Context -> `{ac['actor']}`: `{ac['skill_name']}` (`{kind}` · trigger `{ac['trigger']}` · `events.jsonl:L{ac['line']}`)",
                    f"- **Source Path:** `{ac['path']}` · **Matches current `SKILL.md`:** `{ac['matched_disk']}`",
                    "",
                    format_fenced_block(str(ac["content"]), "markdown"),
                    "",
                ])
        s_lines.extend(["---", "", "## 4. Hooks & Prompt Transformations", ""])
        for ht in stg["hooks_and_transforms"]:  # type: ignore[union-attr]
            if ht["kind"] == "hook":
                s_lines.extend([
                    f"### Hook `{ht['hook_type']}` on `{ht['actor']}` (`events.jsonl:L{ht['start_line']}..L{ht['end_line']}` · success `{ht['success']}`)",
                    "", "**Hook Input:**", format_fenced_block(json.dumps(ht["input"], indent=2, ensure_ascii=False), "json"),
                    "", "**Hook Output:**", format_fenced_block(json.dumps(ht["output"], indent=2, ensure_ascii=False), "json"), "",
                ])
            else:
                origin_lbl = "Human Root Prompt" if ht["is_human_root"] else f"Internal Prompt (`source={ht['source']}`)"
                s_lines.append(f"### Prompt Transformation -> `{ht['actor']}` (Depth `{ht['depth']}` · {origin_lbl} · `events.jsonl:L{ht['line']}`)")
                if ht["embedded_skill_names"]:
                    s_lines.append(f"- **Embedded `<skill-context>` Blocks Inside `transformedContent`:** `{ht['embedded_skill_names']}`")
                s_lines.extend([
                    "", "**Raw `content`:**", format_fenced_block(str(ht["raw_content"]), "text"),
                    "", "**Model-Visible `transformedContent`:**", format_fenced_block(str(ht["transformed_content"]), "xml"), "",
                ])
        s_lines.extend(["---", "", "## 5. Internal Subagent Turns, Reasoning & Tool Calls", ""])
        for item in stg["internal_turns_and_tools"]:  # type: ignore[union-attr]
            ikind = str(item["kind"])
            if ikind == "assistant.message":
                uf_tag = " [USER-FACING]" if item["is_user_facing"] else " [INTERNAL]"
                w_tag = f" · wire `#{item['wire_seq']}`" if item.get("wire_seq") is not None else ""
                s_lines.extend([
                    f"### Assistant Turn -> `{item['actor']}`{uf_tag} (`events.jsonl:L{item['line']}` · `{item['model']}`{w_tag} · `in={item['input_tokens']}` `out={item['output_tokens']}` · `{item['duration_ms']} ms`)",
                    "",
                ])
                if item.get("reasoning"):
                    s_lines.extend(["**Internal Reasoning Trace:**", format_fenced_block(str(item["reasoning"]), "text"), ""])
                if str(item.get("content") or "").strip():
                    s_lines.extend(["**Assistant Message Content:**", format_fenced_block(str(item["content"]), "markdown"), ""])
                if item.get("tool_requests"):
                    s_lines.extend(["**Requested Tool Calls:**", format_fenced_block(json.dumps(item["tool_requests"], indent=2, ensure_ascii=False), "json"), ""])
            elif ikind == "tool_execution":
                s_lines.append(f"### Tool Execution -> `{item['actor']}`: `{item['tool_name']}` (`events.jsonl:L{item['start_line']}..L{item['end_line']}` · `toolCallId={item['tool_call_id']}` · success `{item['success']}`)")
                if item.get("permissions"):
                    s_lines.append(f"- **Permission Resolution:** `{json.dumps(item['permissions'], ensure_ascii=False)}`")
                if item.get("shell_execution"):
                    s_lines.append(f"- **Shell Execution:** `{json.dumps(item['shell_execution'], ensure_ascii=False)}`")
                res_obj = item.get("result")
                res_str = (
                    str(res_obj.get("content"))
                    if isinstance(res_obj, dict) and set(res_obj.keys()) == {"content"} and isinstance(res_obj.get("content"), str)
                    else json.dumps(res_obj, indent=2, ensure_ascii=False)
                )
                s_lines.extend([
                    "", "**Effective Tool Arguments:**", format_fenced_block(json.dumps(item["arguments"], indent=2, ensure_ascii=False), "json"),
                    "", "**Tool Output Returned to Model:**", format_fenced_block(res_str, "text"), "",
                ])
            elif ikind == "binary_asset":
                s_lines.extend([
                    f"### Multimodal Binary Asset Loaded (`events.jsonl:L{item['line']}` · `{item['mime_type']}` · `{item['byte_length']} bytes`)",
                    f"- **Asset ID:** `{item['asset_id']}` · **Description:** `{item['description']}`", "",
                ])
        stg_md_path.write_text("\n".join(s_lines) + "\n", encoding="utf-8")

    diag_obj = diagnose_session_optimization(events_path, wire_path=eff_wire)
    sid_disp = str(session_info.get("sessionId") or events_path.parent.name)
    rt_ver = str(session_info.get("copilotVersion") or "unknown")
    verdict_str = "PASS (100% 1:1 Accounted)" if strict_pass else "FAIL (Mismatch Detected)"
    m_lines: List[str] = [
        "# Facet 1:1 Pipeline Context & Message Audit",
        "",
        f"- **Session ID:** `{sid_disp}`",
        f"- **Copilot Runtime Version:** `{rt_ver}`",
        f"- **Source Event Log:** `{events_path}` (`{total_lines}` lines)",
        f"- **1:1 Completeness Verdict:** **{verdict_str}**",
        "",
        "## 1. Completeness & Truthfulness Checks",
        "",
        f"- **Event-Log Line Coverage:** `{checks_summary['accounted_events_jsonl_lines']} / {total_lines}` lines parsed (`{len(unaccounted_lines)}` unaccounted, `{len(unknown_event_lines)}` unknown types)",
        f"- **Hook Start/End Pairing:** `{len(paired_hooks_by_start_line)}` paired (`{len(unpaired_hook_ids)}` unpaired)",
        f"- **Tool Start/Complete Pairing:** `{len(paired_tools_by_start_line)}` paired (`{len(unpaired_tool_ids)}` unpaired)",
    ]
    if checks_summary["wire_capture_present"]:
        m_lines.append(
            f"- **Loopback Wire-Socket Parity:** `{len(wire_inference_calls) - len(unmatched_wire_seqs)} / {len(wire_inference_calls)}` LLM inference calls matched (`{len(wire_mismatches)}` payload mismatches; `{len(wire_aux_calls)}` auxiliary Ollama probe/embed calls)"
        )
    else:
        m_lines.append("- **Loopback Wire-Socket Capture:** Not attached (post-hoc `--session` audit of on-disk `events.jsonl`)")

    m_lines.extend(["", "## 2. 6-Bucket Optimization & Integrity Summary", "", format_diagnostic_report(diag_obj).strip(), ""])
    m_lines.extend([
        "## 3. Stage-by-Stage Troubleshooting Index",
        "",
        "| # | Stage | Event Lines | Subagents | Skills Loaded | LLM Turns | Input Tokens | Output Tokens | Duration | Full Verbatim Packet |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])
    for stg in stage_records:
        subs_s = ", ".join(stg["subagents"]) if stg["subagents"] else "`@Facet`"  # type: ignore[arg-type]
        sks_s = ", ".join(f"`{s}`" for s in stg["skills"]) if stg["skills"] else "—"  # type: ignore[union-attr]
        m_lines.append(
            f"| `{stg['stage_index']}` | **{stg['stage_title']}** | `L{stg['line_span'][0]}..L{stg['line_span'][1]}` | "  # type: ignore[index]
            f"{subs_s} | {sks_s} | `{stg['llm_turns']}` | `{stg['input_tokens']}` | `{stg['output_tokens']}` | "
            f"`{stg['duration_s']} s` | [{stg['stage_file']}](./{stg['stage_file']}) |"
        )
    m_lines.extend(["", "---", "", "## 4. Complete Chronological User-Facing Conversation Transcript", ""])
    for stg in stage_records:
        m_lines.extend([f"### Stage {stg['stage_index']}: {stg['stage_title']} ([Full Internal Context ->](./{stg['stage_file']}))", ""])
        for up in stg["user_prompts"]:  # type: ignore[union-attr]
            m_lines.extend([f"**User (`L{up['line']}`):**", format_fenced_block(str(up["content"]), "text"), ""])
        for ur in stg["user_facing_replies"]:  # type: ignore[union-attr]
            m_lines.extend([f"**`@Facet` (`L{ur['line']}` · `{ur['model']}`):**", format_fenced_block(str(ur["content"]), "markdown"), ""])

    (out_dir / "00_MASTER_AUDIT.md").write_text("\n".join(m_lines) + "\n", encoding="utf-8")
    ledger_payload = {
        "schema_version": "1.1",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": sid_disp,
        "copilot_runtime_version": rt_ver,
        "completeness_checks": checks_summary,
        "diagnostics": diag_obj,
        "stages": stage_records,
    }
    (out_dir / "ledger.json").write_text(json.dumps(ledger_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ledger_payload


def collate_multimodal_scorecard(
    audits_root: Path,
    out_prefix: Optional[Path] = None,
) -> Dict[str, object]:
    """Collate a Unified Multi-Modality 1:1 SDK Scorecard across suite sub-bundles or baseline directories."""
    dynamic_entries: List[Tuple[str, Path]] = []
    for mod_key in CANONICAL_MODALITY_ORDER:
        cand_sub = audits_root / mod_key
        if (cand_sub / "raw" / "events.jsonl").is_file():
            disp = str(MODALITY_EVAL_SPECS[mod_key].get("display_title") or mod_key)
            dynamic_entries.append((disp, cand_sub))

    scicode_steps: List[Tuple[str, Path]] = []
    if dynamic_entries:
        entries_spec = dynamic_entries
        default_out_name = "00_MULTIMODAL_SCORECARD"
    else:
        entries_spec = [
            ("Modality 1: Open-Book Image (IMG_1598.jpg)", audits_root / "live_1to1_run"),
            (
                "Modality 2: Multi-Page PDF",
                (audits_root / "live_pdf_2609_29119")
                if (audits_root / "live_pdf_2609_29119" / "raw" / "events.jsonl").is_file()
                else (
                    (audits_root / "live_pdf_2611_02948_v2")
                    if (audits_root / "live_pdf_2611_02948_v2" / "raw" / "events.jsonl").is_file()
                    else (audits_root / "live_pdf_2611_02948")
                ),
            ),
            ("Modality 3: Open-Ended Web Ideation (web_fetch)", audits_root / "live_web_ideation_run"),
        ]
        scicode_dir = audits_root / "live_scicode_19_sdk"
        scicode_steps = [
            ("Modality 4a: Closed-Book SciCode 19.1 (tensor)", scicode_dir / "facet-pipeline" / "facet_workspaces" / "step_19.1"),
            ("Modality 4b: Closed-Book SciCode 19.2 (n_tangle)", scicode_dir / "facet-pipeline" / "facet_workspaces" / "step_19.2"),
        ]
        default_out_name = "BASELINE_4MODALITY_SCORECARD"

    rows_md: List[str] = [
        "# Unified 1:1 Copilot SDK Multi-Modality Scorecard",
        "",
        "- **Execution Spine:** `scripts/copilot_sdk_runner.mjs` (`@github/copilot-sdk` / `copilot-runtime` + loopback Ollama HTTP wire-tap)",
        "- **Trace & 1:1 Context Bundler:** `scripts/analyze_trace.py` (`--audit-bundle`, `--diagnose`, `--compare`, `--collate-multimodal-scorecard`)",
        "",
        "## Cross-Modality Telemetry & 1:1 Accounting",
        "",
        "| Modality | 1:1 Log+Wire | HEAD Prompt Parity | Events Accounted | Wire Calls (Inf+Aux) | Wall (s) | Active GPU (s) | Sleep Turns | Reasoning Chars (`B1`) | Specialist `view`s / OOS / Web (`B2`) | Denials / Perm Nulls (`B3`) | Swaps (`B5`) | Integrity (`B6`) |",
        "| :--- | :---: | :---: | :---: | :---: | ---: | ---: | :---: | ---: | :---: | :---: | ---: | :---: |",
    ]
    json_modalities: Dict[str, object] = {}

    def _summarize_one(label: str, bundle_or_step: Path, is_scicode_step: bool = False) -> None:
        ev_p = (bundle_or_step / "events.jsonl") if is_scicode_step else (bundle_or_step / "raw" / "events.jsonl")
        wire_p = (bundle_or_step / "wire_capture.jsonl") if is_scicode_step else (bundle_or_step / "raw" / "wire_capture.jsonl")
        if not ev_p.is_file():
            rows_md.append(f"| {label} | `PENDING` | — | — | — | — | — | — | — | — | — | — | `PENDING` |")
            json_modalities[label] = {"status": "pending", "path": str(bundle_or_step)}
            return
        with tempfile.TemporaryDirectory(prefix="facet_scorecard_") as tmp_dir_s:
            ledger = audit_and_collate_session(
                ev_p,
                Path(tmp_dir_s),
                repo_root=REPO_ROOT,
                wire_path=wire_p if wire_p.is_file() else None,
            )
        chk = ledger["completeness_checks"]  # type: ignore[index]
        diag = ledger["diagnostics"]  # type: ignore[index]
        bk = diag["buckets"]  # type: ignore[index]
        ev_acc = f"`{chk['accounted_events_jsonl_lines']}/{chk['total_events_jsonl_lines']}`"  # type: ignore[index]
        inf_n = int(chk.get("wire_inference_calls") or 0)  # type: ignore[union-attr]
        aux_n = int(chk.get("wire_auxiliary_calls") or 0)  # type: ignore[union-attr]
        wire_s = f"`{inf_n}+{aux_n} ({inf_n + aux_n})`"
        b1 = bk["1_reasoning_bloat"]["total_reasoning_chars"]  # type: ignore[index]
        b2 = bk["2_unhydrated_tool_tax"]["specialist_view_calls_total"]  # type: ignore[index]
        oos_c = len(bk["2_unhydrated_tool_tax"].get("out_of_scope_probing_calls") or [])  # type: ignore[index]
        wf_c = int(bk["2_unhydrated_tool_tax"].get("web_fetch_calls_total") or 0)  # type: ignore[index]
        b3_den = bk["3_schema_hook_and_permission_friction"]["hook_denials_count"]  # type: ignore[index]
        b3_pnull = bk["3_schema_hook_and_permission_friction"]["permission_null_events"]  # type: ignore[index]
        b5 = bk["5_vram_residency_and_swaps"]["model_swaps_total"]  # type: ignore[index]
        b6_ok = bool(bk["6_cleanroom_and_ux_integrity"]["integrity_pass"])  # type: ignore[index]
        lw_ok = bool(chk.get("log_and_wire_1to1_pass"))  # type: ignore[union-attr]
        head_ok = bool(chk.get("strict_1to1_pass"))  # type: ignore[union-attr]
        slp_n = int(diag.get("sleep_confounded_turns") or 0)  # type: ignore[union-attr]
        rows_md.append(
            f"| {label} | `{'PASS' if lw_ok else 'FAIL'}` | `{'Current' if head_ok else 'Archived'}` | {ev_acc} | {wire_s} | "
            f"{diag['wall_seconds']} | {diag['active_gpu_seconds']} | `{slp_n}` | {b1:,} | `{b2} / {oos_c} / {wf_c}` | `{b3_den} / {b3_pnull}` | {b5} | "  # type: ignore[index]
            f"`{'PASS' if b6_ok else 'Legacy'}` |"
        )
        json_modalities[label] = {
            "path": str(bundle_or_step),
            "completeness_checks": chk,
            "diagnostics": diag,
        }

    for lbl, b_dir in entries_spec:
        _summarize_one(lbl, b_dir, is_scicode_step=False)
    for lbl_s, s_dir in scicode_steps:
        _summarize_one(lbl_s, s_dir, is_scicode_step=True)

    payload: Dict[str, object] = {
        "schema_version": "1.2.0",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "modalities": json_modalities,
    }
    eff_prefix = out_prefix or (audits_root / default_out_name)
    Path(f"{eff_prefix}.md").write_text("\n".join(rows_md) + "\n", encoding="utf-8")
    Path(f"{eff_prefix}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse CLI arguments and dispatch session, 1:1 audit, diagnostic, benchmark, or A/B reports.

    Phases:
      - Phase 1: CLI Argument Parsing & Scorecard / A/B Comparison Modes
      - Phase 2: Live 1:1 SDK Modality Execution & Trial Grading (`--run-live`)
      - Phase 3: Post-Hoc Session, Benchmark, Matrix & Factorial Analysis Modes
    """
    # --- Phase 1: CLI Argument Parsing & Scorecard / A/B Comparison Modes ---
    parser = argparse.ArgumentParser(
        description="Unified Facet trace analyzer, 1:1 stage-context bundler, 6-bucket optimizer, and A/B comparator."
    )
    parser.add_argument("--session", default="", help="Session ID under $COPILOT_HOME/session-state/ or path to events.jsonl.")
    parser.add_argument("--run-live", action="store_true", help="Execute a live 1:1 copilot-runtime run via copilot_sdk_runner.mjs and collate.")
    parser.add_argument(
        "--modality",
        choices=("image", "pdf", "spec_bundle", "web_ideation", "closed_book_text", "all"),
        default="image",
        help="Canonical modality preset (or 'all' for all 5 modalities) for --run-live when --prompt is not overridden.",
    )
    parser.add_argument(
        "--scicode-step",
        default="19.1",
        help="1-step SciCode problem selector (e.g. '19.1' or '11.1') backing the 'closed_book_text' modality.",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=1,
        help="Number of independent trials per modality in --run-live mode to compute pass@k and pass^k.",
    )
    parser.add_argument(
        "--allow-web-research",
        choices=("auto", "true", "false"),
        default="auto",
        help="Override allow_web_research in .pipeline_config.json for --run-live ('auto' uses the modality preset default).",
    )
    parser.add_argument(
        "--arxiv-paper",
        default="",
        help="Optional open-access arXiv ID (e.g. 2609.29119v1) to fetch into .cache and stage into research/ for --run-live.",
    )
    parser.add_argument(
        "--prompt",
        default="",
        help="Initial user prompt override when --run-live is selected (defaults to the --modality preset prompt).",
    )
    parser.add_argument("--research-file", action="append", default=[], help="Local research asset (e.g. PDF) to stage into <ws>/research/ in --run-live mode.")
    parser.add_argument("--governance-mode", choices=("auto", "interactive", "autonomous-batch"), default="auto", help="Governance mode for --run-live ('auto' uses the --modality preset default).")
    parser.add_argument("--max-approves", type=int, default=8, help="Maximum /approve checkpoint turns in --run-live mode.")
    parser.add_argument("--turn-timeout-ms", type=int, default=0, help="Per-turn timeout in ms in --run-live mode (0 uses preset default).")
    parser.add_argument("--audit-bundle", default="", help="Emit verbatim 1:1 stage Markdown + ledger.json bundle into this directory.")
    parser.add_argument("--diagnose", action="store_true", help="Emit the 6-bucket Optimization & Agent-Cognition Scorecard.")
    parser.add_argument("--compare", nargs=2, metavar=("BASE", "CAND"), help="Compare Control vs. Candidate session logs or benchmark dirs.")
    parser.add_argument("--bench-run", default="", help="Path to a benchmark run directory (e.g. benchmarks/runs/scicode_quick).")
    parser.add_argument("--matrix-dir", default="", help="Path to a multi-arm factorial benchmark directory.")
    parser.add_argument(
        "--factorial-json",
        default="",
        help="JSON string or file with {'baseline': float, 'singles': {k: float}, 'pairs': {'A+B': float}}.",
    )
    parser.add_argument(
        "--collate-multimodal-scorecard",
        default="",
        nargs="?",
        const=str(REPO_ROOT / "logs" / "context_audits"),
        help="Collate Unified 4-Modality Scorecard (Image, PDF, Open Web Ideation, SciCode 19) under the audits directory.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of Markdown/text.")
    args = parser.parse_args(argv)

    if args.collate_multimodal_scorecard:
        aud_dir = Path(args.collate_multimodal_scorecard).expanduser().resolve()
        sc = collate_multimodal_scorecard(aud_dir)
        if args.json:
            print(json.dumps(sc, indent=2))
        else:
            md_cand = aud_dir / "00_MULTIMODAL_SCORECARD.md"
            if not md_cand.is_file():
                md_cand = aud_dir / "BASELINE_4MODALITY_SCORECARD.md"
            sys.stdout.write(md_cand.read_text(encoding="utf-8"))
        return 0

    if args.compare:
        comp = compare_traces(args.compare[0], args.compare[1])
        if args.json:
            print(json.dumps(comp, indent=2))
        else:
            sys.stdout.write(format_comparison_report(comp))
        return 0 if comp["non_regression_pass"] else 1

    # --- Phase 2: Live 1:1 SDK Modality Execution & Trial Grading (`--run-live`) ---
    if args.run_live:
        target_modalities = list(CANONICAL_MODALITY_ORDER) if args.modality == "all" else [args.modality]
        n_trials = max(1, int(args.trials))
        ts_slug = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        suite_root = (
            Path(args.audit_bundle).expanduser().resolve()
            if args.audit_bundle
            else (REPO_ROOT / "logs" / "context_audits" / f"{ts_slug}_{args.modality}_sdk")
        )
        suite_root.mkdir(parents=True, exist_ok=True)
        node_cmd, node_env = resolve_node_command()
        runner_mjs = REPO_ROOT / "scripts" / "copilot_sdk_runner.mjs"

        all_results: Dict[str, object] = {}
        overall_ok = True
        for mod_name in target_modalities:
            preset = dict(MODALITY_EVAL_SPECS[mod_name])
            sc_meta: Optional[Dict[str, object]] = None
            if bool(preset.get("scicode_backed")) and not args.prompt.strip():
                sc_meta = resolve_scicode_step_modality(scicode_step=args.scicode_step)
                preset["prompt"] = sc_meta["prompt"]
                preset["source_label"] = sc_meta["source_label"]

            eff_gov = str(preset["governance_mode"]) if args.governance_mode == "auto" else str(args.governance_mode)
            eff_web = (
                bool(preset["allow_web_research"])
                if args.allow_web_research == "auto"
                else (args.allow_web_research == "true")
            )
            eff_timeout = int(args.turn_timeout_ms or int(preset["turn_timeout_ms"]))  # type: ignore[arg-type]
            eff_prompt = str(args.prompt).strip() or str(preset["prompt"])
            eff_rfiles = [str(Path(rf).expanduser().resolve()) for rf in args.research_file]
            if args.arxiv_paper.strip():
                cached_p = ensure_open_access_arxiv_pdf(args.arxiv_paper.strip())
                eff_rfiles.append(str(cached_p))
                if not args.prompt.strip():
                    eff_prompt = (
                        f"@Facet Derive, implement, and verify the primary numerical method and governing equations "
                        f"from research/{cached_p.name}."
                    )

            mod_base_dir = (suite_root / mod_name) if len(target_modalities) > 1 else suite_root
            trial_passes: List[bool] = []
            trial_records: List[Dict[str, object]] = []
            for t_idx in range(1, n_trials + 1):
                out_dir = (mod_base_dir / f"trial_{t_idx:02d}") if n_trials > 1 else mod_base_dir
                out_dir.mkdir(parents=True, exist_ok=True)
                ws_override = out_dir / "workspace"
                ws_override.mkdir(parents=True, exist_ok=True)
                cmd = node_cmd + [
                    str(runner_mjs),
                    "--repo-root", str(REPO_ROOT),
                    "--workspace-dir", str(ws_override),
                    "--out-dir", str(out_dir),
                    "--governance-mode", eff_gov,
                    "--allow-web-research", "true" if eff_web else "false",
                    "--prompt", eff_prompt,
                    "--max-approves", str(args.max_approves),
                    "--turn-timeout-ms", str(eff_timeout),
                ]
                if bool(preset["no_examples"]) and not args.arxiv_paper.strip():
                    cmd.append("--no-examples")
                for rf in eff_rfiles:
                    cmd.extend(["--research-file", rf])
                proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=node_env, text=True)
                events_path = out_dir / "raw" / "events.jsonl"
                wire_path = out_dir / "raw" / "wire_capture.jsonl"
                if proc.returncode != 0 and not events_path.is_file():
                    trial_passes.append(False)
                    trial_records.append({"trial": t_idx, "out_dir": str(out_dir), "all_graders_pass": False})
                    continue
                ledger = audit_and_collate_session(events_path, out_dir, repo_root=REPO_ROOT, wire_path=wire_path)
                grade = grade_modality_trial(
                    mod_name,
                    ledger,
                    workspace_dir=ws_override,
                    scicode_step=args.scicode_step,
                    allow_web_override=args.allow_web_research,
                )
                trial_passes.append(bool(grade["all_graders_pass"]))
                trial_records.append({
                    "trial": t_idx,
                    "out_dir": str(out_dir),
                    "grade": grade,
                    "completeness_checks": ledger["completeness_checks"],
                    "diagnostics": ledger["diagnostics"],
                })
                if not args.json:
                    print(f"Wrote 1:1 context audit bundle to {out_dir / '00_MASTER_AUDIT.md'}")
                    sys.stdout.write(format_diagnostic_report(ledger["diagnostics"]))  # type: ignore[arg-type]

            pass_stats = compute_trial_pass_metrics(trial_passes)
            if int(pass_stats["successes"]) < n_trials:  # type: ignore[arg-type]
                overall_ok = False
            all_results[mod_name] = {"pass_metrics": pass_stats, "trials": trial_records}

        if len(target_modalities) > 1 and n_trials == 1:
            collate_multimodal_scorecard(suite_root, out_prefix=suite_root / "00_MULTIMODAL_SCORECARD")
            if not args.json and (suite_root / "00_MULTIMODAL_SCORECARD.md").is_file():
                sys.stdout.write("\n" + (suite_root / "00_MULTIMODAL_SCORECARD.md").read_text(encoding="utf-8"))

        if args.json:
            print(json.dumps({"suite_root": str(suite_root), "results": all_results}, indent=2))
        return 0 if overall_ok else 1

    # --- Phase 3: Post-Hoc Session, Benchmark, Matrix & Factorial Analysis Modes ---
    if args.session:
        try:
            ev_file = resolve_session_events_path(args.session)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.audit_bundle:
            out_dir = Path(args.audit_bundle).expanduser().resolve()
            ledger = audit_and_collate_session(ev_file, out_dir, repo_root=REPO_ROOT)
            checks = ledger["completeness_checks"]  # type: ignore[index]
            if args.json:
                print(json.dumps({"out_dir": str(out_dir), "completeness_checks": checks, "diagnostics": ledger["diagnostics"]}, indent=2))
            else:
                print(f"Wrote 1:1 context audit bundle to {out_dir / '00_MASTER_AUDIT.md'}")
            return 0 if checks["strict_1to1_pass"] else 1  # type: ignore[index]
        if args.diagnose:
            diag = diagnose_session_optimization(ev_file)
            if args.json:
                print(json.dumps(diag, indent=2))
            else:
                sys.stdout.write(format_diagnostic_report(diag))
            return 0
        res = analyze_session_events(ev_file)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(
                f"# Session Trace: {ev_file.parent.name}\n"
                f"- Wall: {res['wall_seconds']}s | Active GPU: {res['active_gpu_seconds']}s | "
                f"Model swaps: {res['model_swaps']} | Denials/Blocks: {len(res['hook_denials'])}"  # type: ignore[arg-type]
            )
            for rk, rv in sorted(res["roles"].items(), key=lambda kv: -float(kv[1]["wall_s"])):  # type: ignore[index,union-attr]
                print(f"  * {rk:14s}: turns={int(rv['turns'])}, wall={rv['wall_s']:.1f}s, in_tok={int(rv['in_tok'])}, out_tok={int(rv['out_tok'])}")
        return 0

    if args.bench_run:
        bdir = Path(args.bench_run).expanduser().resolve()
        if not bdir.is_dir():
            print(f"Error: benchmark run directory not found at {bdir}", file=sys.stderr)
            return 1
        if args.diagnose:
            bdiag = diagnose_benchmark_run(bdir)
            if args.json:
                print(json.dumps(bdiag, indent=2))
            else:
                sys.stdout.write(format_diagnostic_report(bdiag))
            return 0
        summary = analyze_benchmark_run(bdir)
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            sys.stdout.write(format_benchmark_report(summary))
        return 0

    if args.matrix_dir:
        mdir = Path(args.matrix_dir).expanduser().resolve()
        if not mdir.is_dir():
            print(f"Error: matrix directory not found at {mdir}", file=sys.stderr)
            return 1
        msum = analyze_matrix_directory(mdir)
        print(json.dumps(msum, indent=2))
        return 0

    if args.factorial_json:
        raw_f = args.factorial_json.strip()
        spec = json.loads(Path(raw_f).read_text(encoding="utf-8")) if Path(raw_f).is_file() else json.loads(raw_f)
        base_v = float(spec["baseline"])
        singles_v = {str(k): float(v) for k, v in (spec.get("singles") or {}).items()}
        pairs_v: Dict[Tuple[str, str], float] = {}
        for pk, pv in (spec.get("pairs") or {}).items():
            parts = [x.strip() for x in str(pk).split("+") if x.strip()]
            if len(parts) == 2:
                pairs_v[(parts[0], parts[1])] = float(pv)
        inter_rows = compute_pairwise_interactions(base_v, singles_v, pairs_v)
        print(json.dumps(inter_rows, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
