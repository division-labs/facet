"""Specification Manifest and Mapping Document validators, caller parity, dimensional units, leakage, and System-1 screening.

Function Map:
  - Section 1: Epistemic Gate Resolution & Zero-VRAM Structural Risk Detector (`apply_artifact_gate`, `detect_deterministic_spec_or_code_risk`)
  - Section 2: Boundary System-1 Spec & Anti-Leakage Screening (`run_systemone_spec_screen`, `run_systemone_stage3_leakage_screen`)
  - Section 3: `manifest.json` Schema, Obligation Coverage & Caller Interface Parity (`validate_manifest_structure`, `validate_obligation_coverage`)
  - Section 4: `mapping.json` Schema, `AST-1` Predicate Compilation & Stub `mypy` Check (`validate_mapping_structure`, `_validate_mapping_stub_composition_types`)
  - Section 5: Anchor-Literal Anti-Leakage & Target-Module Entrypoint Verification (`detect_anchor_test_leakage`, `validate_target_module_entrypoints`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .constants_and_context import (
    DEFAULT_FSM,
    FSM_FILE_NAME,
    HOOKS_REPO_ROOT,
    _TRIVIAL_MATH_BASE_SCALARS,
    _VALID_EQUIVALENCE_MODES,
    _VALID_PREDICATE_TYPES,
    compute_file_sha256,
)
from .state_locks_and_seals import (
    append_run_telemetry_event,
    atomic_write_text,
    call_systemone_decision_model,
    compute_canonical_seal_digest,
    load_pipeline_config,
    load_pipeline_fsm,
)
from .text_ast_and_patch import (
    _compare_continuous_eq_violation,
    _get_declared_target_module,
    _is_finite_number,
    compute_method_l3_localized_hunks,
    extract_mandated_target_module,
)
from .visual_ocr_and_sources import (
    detect_ocr_uncertainties,
)

# ============================================================================
# Section 1: Epistemic Gate Resolution & Zero-VRAM Structural Risk Detector
# Determines human checkpoint requirements and screens AST/JSON artifacts for structural risk before invoking System-1.
# ============================================================================

def manifest_has_epistemic_assumptions(cwd, cfg=None):
    """Return ``True`` if OCR uncertainties or gate-triggering manifest assumptions exist.

    Args:
        cwd: Workspace root directory.
        cfg: Optional merged pipeline configuration dictionary (honors ``contract_and_gates``).

    Returns:
        ``True`` when ``SOURCE_OCR.md`` carries ``<uncertain>``/``<illegible/>``
        tags, or (under ``standard``) when ``manifest.json`` marks any
        specification as ``"assumption"``.
    """
    # Always require human review when optical OCR flagged `<uncertain>` or `<illegible/>` tags.
    cwd = Path(cwd)
    if detect_ocr_uncertainties(cwd):
        return True
    eff_cfg = cfg if isinstance(cfg, dict) else load_pipeline_config(cwd)
    cg_mode = str(((eff_cfg.get("experiments") or {}) if isinstance(eff_cfg, dict) else {}).get("contract_and_gates") or "standard")
    manifest_path = cwd / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        # Under `debiased_contract_ocr_gate` or `axiomatic_split_and_loop_guard`, routine textbook
        # mathematical preconditions do not pause `autonomous-batch` unless `unresolved_ambiguities` is set.
        if cg_mode in ("debiased_contract_ocr_gate", "axiomatic_split_and_loop_guard"):
            return bool(data.get("unresolved_ambiguities"))
        specs = data.get("formal_specifications") or []
        for s in specs:
            if isinstance(s, dict) and s.get("epistemic_status") == "assumption":
                return True
    except Exception:
        pass
    return False


def is_checkpoint_gate_required(checkpoint_key, cfg, has_epistemic_assumptions=False):
    """Determine whether human approval is required at ``checkpoint_key``.

    Args:
        checkpoint_key: One of ``checkpoint_0``..``checkpoint_3``.
        cfg: Merged pipeline configuration dictionary.
        has_epistemic_assumptions: Whether ``manifest.json`` contains epistemic
            assumptions (forces Checkpoint 1 pause even under ``autonomous-batch``).

    Returns:
        ``True`` if the pipeline must pause for ``/approve``, else ``False``.
    """
    # Decide whether to pause for human `/approve` based on `governance_mode` (`strict-4-gate`, `streamlined-2-gate`, or `autonomous-batch`).
    mode = (cfg or {}).get("governance_mode", "strict-4-gate")
    if mode == "autonomous-batch":
        if checkpoint_key == "checkpoint_1" and has_epistemic_assumptions:
            return True
        return False
    if mode == "streamlined-2-gate":
        return checkpoint_key in ("checkpoint_1", "checkpoint_3")
    return True


def apply_artifact_gate(cwd, fsm, checkpoint_key, sha_val, checkpoint_stage, next_auto_stage, cfg=None):
    """Seal or auto-advance a stage checkpoint when a deliverable is written.

    Args:
        cwd: Workspace root directory.
        fsm: Mutable FSM dictionary loaded from ``.pipeline_state.json``.
        checkpoint_key: Target checkpoint key (``checkpoint_0``..``checkpoint_3``).
        sha_val: SHA-256 digest of the validated deliverable on disk.
        checkpoint_stage: FSM stage name when pausing for human sign-off.
        next_auto_stage: FSM stage name when auto-approving.
        cfg: Optional preloaded pipeline configuration dictionary.

    Returns:
        ``True`` if a human approval gate is now active, else ``False``.
    """
    # Record the artifact's SHA-256 hash and either pause at a human `CHECKPOINT_*` state or auto-advance to the next stage.
    if cfg is None:
        cfg = load_pipeline_config(cwd)
    if "checkpoints" not in fsm or not isinstance(fsm["checkpoints"], dict):
        fsm["checkpoints"] = json.loads(json.dumps(DEFAULT_FSM["checkpoints"]))
    if checkpoint_key not in fsm["checkpoints"] or not isinstance(fsm["checkpoints"][checkpoint_key], dict):
        fsm["checkpoints"][checkpoint_key] = json.loads(json.dumps(DEFAULT_FSM["checkpoints"][checkpoint_key]))
    cp = fsm["checkpoints"][checkpoint_key]
    cp["sha256"] = sha_val

    has_assumptions = (checkpoint_key == "checkpoint_1") and manifest_has_epistemic_assumptions(cwd, cfg=cfg)
    if is_checkpoint_gate_required(checkpoint_key, cfg, has_epistemic_assumptions=has_assumptions):
        if cp.get("approved") and cp.get("approved_sha256") == sha_val:
            return False
        cp["approved"] = False
        fsm["current_stage"] = checkpoint_stage
        return True

    cp["approved"] = True
    cp["approved_sha256"] = sha_val
    fsm["current_stage"] = next_auto_stage
    return False


def detect_deterministic_spec_or_code_risk(cwd: Path, artifact_name: str, fsm: dict[str, object]) -> bool:
    """Detect zero-VRAM AST/structural risk signals gating ``ast_risk_gated`` System-1 boundary screening.

    Checks ``manifest.json`` for missing caller ``target_interface``, unrequested
    strict-positivity domain narrowing, or OCR provenance; checks ``mapping.json``
    for dimension-gated ternary branches in ``formal_predicate`` (e.g.
    ``if len(psi) == 4 else 0.0``) or narrow predicate diversity; and checks
    ``*.py`` modules for multi-branch numeric-constant return tables.

    Args:
        cwd: Workspace root directory.
        artifact_name: ``"manifest.json"``, ``"mapping.json"``, or ``"<module>.py"``.
        fsm: Active pipeline FSM dictionary.

    Returns:
        ``True`` if a structural risk signal warrants ``/v1/systemone`` screening; else ``False``.
    """
    # Perform `<1 ms` CPU structural checks so clean artifacts pay zero System-1 VRAM swap overhead.
    art_p = Path(cwd) / artifact_name
    if not art_p.is_file():
        return False
    orig_req = str(fsm.get("original_user_request") or "")
    try:
        if artifact_name == "manifest.json":
            if (Path(cwd) / "SOURCE_OCR.md").is_file():
                return True
            data = json.loads(art_p.read_text(encoding="utf-8"))
            if "def " in orig_req and not data.get("target_interface"):
                return True
            cons_txt = " ".join(str(c) for c in (data.get("constraints") or []))
            if "> 0" in cons_txt and "> 0" not in orig_req and "positive" not in orig_req.lower():
                return True
            return False
        if artifact_name == "mapping.json":
            data = json.loads(art_p.read_text(encoding="utf-8"))
            obls = data.get("verification_obligations") or []
            if len(obls) >= 2 and len({str(o.get("predicate_type") or "") for o in obls if isinstance(o, dict)}) < 2:
                return True
            for ob in obls:
                if not isinstance(ob, dict):
                    continue
                fp = str(ob.get("formal_predicate") or "").strip()
                if not fp:
                    continue
                try:
                    p_ast = ast.parse(fp, mode="eval")
                    for sub in ast.walk(p_ast):
                        # Flag ternary branches like `... if len(psi) == 4 else 0.0` (dimension-specific constant fallback branch)
                        if isinstance(sub, ast.IfExp) and any(
                            isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
                            for n in (sub.body, sub.orelse)
                        ):
                            return True
                except SyntaxError:
                    return True
            return False
        if artifact_name.endswith(".py"):
            tree = ast.parse(art_p.read_text(encoding="utf-8"))
            const_return_ifs = 0
            for node in ast.walk(tree):
                if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
                    has_num_cmp = any(isinstance(c, ast.Constant) and isinstance(c.value, (int, float)) for c in node.test.comparators)
                    has_const_ret = any(
                        isinstance(s, ast.Return) and isinstance(s.value, (ast.Constant, ast.List, ast.Tuple))
                        for s in node.body
                    )
                    if has_num_cmp and has_const_ret:
                        const_return_ifs += 1
            return const_return_ifs >= 2
    except Exception:
        return False
    return False


# ============================================================================
# Section 2: Boundary System-1 Spec & Anti-Leakage Screening
# Invokes boundary `pplx-decider-flash` (`keep_alive=0`) when deterministic risk signals fire at Stage 1–3 transitions.
# ============================================================================

def run_systemone_spec_screen(
    cwd: Path, artifact_name: str, fsm: dict[str, object], cfg: dict[str, object]
) -> tuple[bool, str]:
    r"""Run boundary-only ``pplx-decider-flash`` semantic screening (`system1_s1_s2`) on ``manifest.json`` or ``mapping.json``.

    Executes only at ``subagentStop`` when ``enable_local_spec_critic == "system1_s1_s2"``
    and upstream source text exists. On ``defect_prob >= threshold`` (default ``0.85``)
    within the 2-attempt cap, writes ``LOCAL_CRITIC.json`` with ``Method L3``
    localized hunks and rewinds to ``STAGE_1_SPECIFICATION`` / ``STAGE_2_MAPPING``.

    Args:
        cwd: Workspace root directory.
        artifact_name: ``"manifest.json"`` or ``"mapping.json"``.
        fsm: Mutable pipeline FSM dictionary.
        cfg: Merged pipeline configuration dictionary.

    Returns:
        Tuple ``(triggered_rewind, summary_message)``.
    """
    # Screen `manifest.json` or `mapping.json` at `subagentStop` (`system1_s1_s2`, `tau >= 0.85`) for domain narrowing or formula bugs in `<1 s`.
    if str(cfg.get("enable_local_spec_critic") or "off") != "system1_s1_s2":
        return (False, "")
    sys1_pol = str(((cfg.get("experiments") or {}) if isinstance(cfg, dict) else {}).get("sys1_trigger_policy") or "every_boundary")
    if sys1_pol == "rewind_and_s0v_only":
        return (False, "")
    if sys1_pol == "ast_risk_gated" and not detect_deterministic_spec_or_code_risk(Path(cwd), artifact_name, fsm):
        return (False, "")
    art_path = Path(cwd) / artifact_name
    if not art_path.is_file():
        return (False, "")
    orig_req = str(fsm.get("original_user_request") or "").strip()
    ocr_txt = (Path(cwd) / "SOURCE_OCR.md").read_text(encoding="utf-8", errors="replace")[:3000] if (Path(cwd) / "SOURCE_OCR.md").is_file() else ""
    res_txt = (Path(cwd) / "RESEARCH.md").read_text(encoding="utf-8", errors="replace")[:3000] if (Path(cwd) / "RESEARCH.md").is_file() else ""
    man_txt = (Path(cwd) / "manifest.json").read_text(encoding="utf-8", errors="replace")[:4000] if (artifact_name == "mapping.json" and (Path(cwd) / "manifest.json").is_file()) else ""
    upstream_blob = "\n\n".join(x for x in (orig_req, ocr_txt, res_txt, man_txt) if x).strip()
    if not upstream_blob:
        return (False, "")

    lc_counts = fsm.get("local_critic_attempts")
    if not isinstance(lc_counts, dict):
        lc_counts = {"s1": 0, "s2": 0}
    slot = "s1" if artifact_name == "manifest.json" else "s2"
    if int(lc_counts.get(slot, 0) or 0) >= 2:
        return (False, "")

    art_text = art_path.read_text(encoding="utf-8", errors="replace")
    lines = art_text.splitlines()[:250]
    if not lines:
        return (False, "")
    numbered = "\n".join(f"L{i:03d}: {ln}" for i, ln in enumerate(lines, 1))
    line_ids = [f"L{i:03d}" for i in range(1, len(lines) + 1)]
    # Query `pplx-decider-flash` (`keep_alive=0`) via `/v1/systemone` for fast boundary triage.
    answers = call_systemone_decision_model(
        cwd,
        {"artifact_name": artifact_name, "upstream_specification": upstream_blob[:5000], "artifact_numbered": numbered},
        {
            "has_semantic_defect": {
                "type": "noul",
                "instructions": (
                    f"Does `{artifact_name}` (`artifact_numbered`) contain a genuine mathematical error, unrequested caller-domain "
                    "narrowing, wrong signature, missing tie-break/overflow handling, or contradictory anchor value vs `upstream_specification`?"
                ),
                "criteria": {
                    "true": "Yes — contains a substantive mathematical, domain-narrowing, signature, or anchor defect.",
                    "false": "No — faithful, mathematically sound, and domain-preserving.",
                },
            },
            "defect_category": {
                "type": "choice",
                "instructions": "Which defect category best describes the issue if present?",
                "criteria": {
                    "domain_narrowing": "Restricts valid caller inputs (e.g. excluding 0, negative slopes/eigenvalues, or ArrayLike inputs).",
                    "formula_or_tiebreak": "Wrong mathematical sign, equation, or zero/tie-break handling.",
                    "wrong_anchor_or_predicate": "Incorrect anchor expected value or contradictory formal predicate.",
                    "signature_mismatch": "Entrypoint or parameter name mismatch against caller specification.",
                },
            },
            "where_line": {
                "type": "choice",
                "instructions": "Which line ID in `artifact_numbered` is the primary defect locus?",
                "criteria": {lid: None for lid in line_ids},
            },
        },
    )
    if not isinstance(answers, dict):
        return (False, "")
    dm_cfg = cfg.get("decision_model") if isinstance(cfg.get("decision_model"), dict) else {}
    tau = float(dm_cfg.get("confidence_threshold", 0.85) or 0.85)
    defect_prob = float((answers.get("has_semantic_defect") or {}).get("noul") or 0.0)  # type: ignore[union-attr]
    append_run_telemetry_event(
        "systemone_verdict",
        {
            "purpose": f"spec_screen_{artifact_name}",
            "artifact": artifact_name,
            "probability": round(defect_prob, 4),
            "threshold": tau,
            "triggered": bool(defect_prob >= tau),
        },
    )
    if defect_prob < tau:
        return (False, "")

    cat = str((answers.get("defect_category") or {}).get("choice") or "formula_or_tiebreak")  # type: ignore[union-attr]
    w_ans = answers.get("where_line") if isinstance(answers.get("where_line"), dict) else {}
    l_probs = w_ans.get("probabilities") if isinstance(w_ans.get("probabilities"), dict) else {}
    top_lid = str(w_ans.get("choice") or "L001")
    if not l_probs and top_lid:
        l_probs = {top_lid: 1.0}
    hunks = compute_method_l3_localized_hunks(art_text, artifact_name, l_probs, in_file_prob=defect_prob)  # type: ignore[arg-type]

    lc_counts[slot] = int(lc_counts.get(slot, 0) or 0) + 1
    fsm["local_critic_attempts"] = lc_counts
    is_s1 = artifact_name == "manifest.json"
    target_stage = "STAGE_1_SPECIFICATION" if is_s1 else "STAGE_2_MAPPING"
    target_role = "Researcher" if is_s1 else "Formalist"
    cps = fsm.get("checkpoints")
    if isinstance(cps, dict):
        cp_key = "checkpoint_1" if is_s1 else "checkpoint_2"
        if cp_key in cps and isinstance(cps[cp_key], dict):
            cps[cp_key]["approved"] = False
            cps[cp_key]["approved_sha256"] = None
    fsm["current_stage"] = target_stage
    fsm["pending_rewind"] = {
        "target_stage": target_stage,
        "specialist": target_role,
        "attempt": lc_counts[slot],
        "localized_hunks": hunks,
    }
    lc_payload = {
        "stage": "STAGE_1_CRITIC" if is_s1 else "STAGE_2_CRITIC",
        "status": "FAIL",
        "source": "system1_decider",
        "defect_probability": round(defect_prob, 4),
        "issues": [
            {
                "category": cat,
                "locus": top_lid,
                "description": (
                    f"System-1 semantic check flagged '{cat}' near {top_lid} (p={defect_prob:.2f} >= {tau:.2f}). "
                    f"Verify '{artifact_name}' against the caller specification and apply a minimal scoped 'edit'."
                ),
            }
        ],
    }
    atomic_write_text(Path(cwd) / "LOCAL_CRITIC.json", json.dumps(lc_payload, indent=2) + "\n")
    return (True, f"System-1 flagged {cat} in {artifact_name} near {top_lid} (p={defect_prob:.2f})")


def run_systemone_stage3_leakage_screen(
    cwd: Path, py_path: Path, cfg: dict[str, object]
) -> str | None:
    """Screen a Stage 3 Python module at ``subagentStop`` for hardcoded test-input lookup tables.

    Args:
        cwd: Workspace root directory.
        py_path: Path to the Stage 3 implementation module.
        cfg: Merged pipeline configuration dictionary.

    Returns:
        Violation description string when ``noul >= max(threshold, 0.88)``, else ``None``.
    """
    # Screen Stage 3 implementation code at `subagentStop` for hardcoded benchmark lookup tables (`noul >= 0.88`) while allowing valid math constants.
    sys1_pol_s3 = str(((cfg.get("experiments") or {}) if isinstance(cfg, dict) else {}).get("sys1_trigger_policy") or "every_boundary")
    if sys1_pol_s3 == "rewind_and_s0v_only":
        return None
    if sys1_pol_s3 == "ast_risk_gated" and not detect_deterministic_spec_or_code_risk(Path(cwd), py_path.name, load_pipeline_fsm(Path(cwd))):
        return None
    if not py_path.is_file():
        return None
    src = py_path.read_text(encoding="utf-8", errors="replace")
    if not src.strip():
        return None
    answers = call_systemone_decision_model(
        cwd,
        {"module": py_path.name, "code": src[:6000]},
        {
            "hardcoded_test_cheat": {
                "type": "noul",
                "instructions": (
                    "Does `code` hardcode specific benchmark/test input-to-output lookup branches instead of computing "
                    "the general mathematical algorithm (note: standard mathematical quadrature weights, physical constants, "
                    "and Butcher tableaus are completely valid and NOT cheats)?"
                ),
                "criteria": {
                    "true": "Yes — branches on specific test inputs to return canned answers.",
                    "false": "No — implements a genuine mathematical algorithm or legitimate numerical constants.",
                },
            }
        },
    )
    if not isinstance(answers, dict):
        return None
    dm_cfg = cfg.get("decision_model") if isinstance(cfg.get("decision_model"), dict) else {}
    tau = max(0.88, float(dm_cfg.get("confidence_threshold", 0.85) or 0.85))
    cheat_p = float((answers.get("hardcoded_test_cheat") or {}).get("noul") or 0.0)  # type: ignore[union-attr]
    append_run_telemetry_event(
        "systemone_verdict",
        {
            "purpose": "stage3_leakage_screen",
            "artifact": py_path.name,
            "probability": round(cheat_p, 4),
            "threshold": tau,
            "triggered": bool(cheat_p >= tau),
        },
    )
    if cheat_p >= tau:
        return (
            f"System-1 generalization screen detected probable hardcoded test-case lookup branching in '{py_path.name}' "
            f"(p={cheat_p:.2f}). Implement the general mathematical recurrence across the full input domain."
        )
    return None


# ============================================================================
# Section 3: `manifest.json` Schema, Obligation Coverage & Caller Interface Parity
# Validates Stage 1 manifests, unified `SPEC-k` obligation coverage, caller signatures, and SI dimensional unit consistency.
# ============================================================================

def validate_manifest_structure(data, cwd=None):
    """Validate ``manifest.json`` against Specification Manifest invariants.

    Checks required fields, unique ``spec_id`` entries, ``verification_contract``
    tolerances and bounds, and TEI P5 ``ambiguities``/``assumption`` propagation
    when ``SOURCE_OCR.md`` contains uncertainty tags.

    Args:
        data: Parsed JSON object from ``manifest.json``.
        cwd: Optional workspace root directory used to inspect ``SOURCE_OCR.md``.

    Returns:
        List of structural or provenance validation errors (empty when valid).
    """
    # Check `manifest.json` required fields, unique `SPEC-k` IDs, finite tolerances, and mandatory `ambiguities` when `SOURCE_OCR.md` has `<uncertain>` tags.
    errors = []
    if not isinstance(data, dict):
        return ["manifest.json must be a JSON object."]
    if not isinstance(data.get("concept_name"), str) or not data.get("concept_name", "").strip():
        errors.append("Missing required non-empty string field 'concept_name'.")
    source_ref = data.get("source_reference")
    if not isinstance(source_ref, dict) or not isinstance(source_ref.get("doi_or_url"), str) or not source_ref.get("doi_or_url", "").strip():
        errors.append("Missing required 'source_reference' object with non-empty 'doi_or_url'.")

    variables = data.get("variables")
    if not isinstance(variables, list) or len(variables) == 0:
        errors.append("Missing or empty required 'variables' list.")
    else:
        for idx, var in enumerate(variables):
            if not isinstance(var, dict) or not var.get("symbol") or not var.get("domain"):
                errors.append(f"Variable item {idx} must be an object declaring 'symbol' and 'domain'.")

    constraints = data.get("constraints")
    if not isinstance(constraints, list) or any(not isinstance(c, str) or not c.strip() for c in constraints):
        errors.append("Missing required 'constraints' list of non-empty strings.")

    specs = data.get("formal_specifications")
    has_assumption = False
    seen_spec_ids = set()
    if not isinstance(specs, list) or len(specs) == 0:
        errors.append("Must declare at least one entry in 'formal_specifications'.")
    else:
        for i, s in enumerate(specs):
            if not isinstance(s, dict):
                errors.append(f"Specification item {i} must be an object.")
                continue
            sid = s.get("spec_id") or s.get("id")
            if not sid or not isinstance(sid, str):
                errors.append(f"Specification item {i} is missing string identifier ('spec_id' or 'id').")
            elif sid in seen_spec_ids:
                errors.append(f"Duplicate specification identifier '{sid}' in 'formal_specifications'.")
            else:
                seen_spec_ids.add(sid)
            expr = s.get("expression") if s.get("expression") is not None else s.get("latex")
            if not isinstance(expr, str) or not expr.strip():
                errors.append(f"Specification item {i} must declare a non-empty string 'expression' (or 'latex').")
            if not s.get("grounding_evidence"):
                errors.append(f"Specification item {i} is missing 'grounding_evidence'.")
            ep_status = s.get("epistemic_status")
            if ep_status not in ["canonical", "derived", "assumption"]:
                errors.append(f"Specification item {i} has invalid 'epistemic_status' (must be canonical, derived, or assumption).")
            if ep_status == "assumption":
                has_assumption = True

    contract = data.get("verification_contract")
    if not isinstance(contract, dict):
        errors.append("Missing required 'verification_contract' object.")
    else:
        eq_mode = contract.get("equivalence_mode")
        if eq_mode not in _VALID_EQUIVALENCE_MODES:
            errors.append(
                f"Invalid 'verification_contract.equivalence_mode' ({eq_mode!r}); must be one of {sorted(_VALID_EQUIVALENCE_MODES)}."
            )
        for tol_key in ("rtol", "atol"):
            if tol_key in contract and contract[tol_key] is not None:
                if not _is_finite_number(contract[tol_key]) or float(contract[tol_key]) < 0.0:
                    errors.append(f"'{tol_key}' in verification_contract must be a finite non-negative number.")
        bounds = contract.get("domain_bounds")
        if bounds is not None:
            if not isinstance(bounds, dict):
                errors.append("'domain_bounds' must be an object mapping variable names to [min, max] pairs.")
            else:
                for b_name, b_pair in bounds.items():
                    if (
                        not isinstance(b_pair, list)
                        or len(b_pair) != 2
                        or not _is_finite_number(b_pair[0])
                        or not _is_finite_number(b_pair[1])
                        or float(b_pair[0]) > float(b_pair[1])
                    ):
                        errors.append(f"Invalid 'domain_bounds[{b_name!r}]': expected [min, max] with finite min <= max.")

    if cwd is not None:
        ocr_tags = detect_ocr_uncertainties(cwd)
        if ocr_tags:
            ambiguities = data.get("ambiguities")
            valid_amb = isinstance(ambiguities, list) and any(isinstance(a, str) and a.strip() for a in ambiguities)
            if not valid_amb:
                errors.append(
                    f"Epistemic Provenance Violation: SOURCE_OCR.md contains {len(ocr_tags)} TEI P5 uncertainty tag(s) "
                    "(<uncertain> or <illegible/>), so manifest.json must populate a non-empty 'ambiguities' list."
                )
            if not has_assumption:
                errors.append(
                    "Epistemic Provenance Violation: SOURCE_OCR.md contains <uncertain> or <illegible/> tag(s), "
                    "so at least one affected specification entry must set 'epistemic_status': 'assumption'."
                )

    return errors


def validate_obligation_coverage(manifest_data, mapping_data):
    r"""Verify every ``SPEC-k`` in ``manifest.json`` is covered by ``mapping.json``.

    Args:
        manifest_data: Parsed JSON object from ``manifest.json``.
        mapping_data: Parsed JSON object from ``mapping.json``.

    Returns:
        Sorted list of uncovered specification IDs.
    """
    # Confirm every `SPEC-k` equation in `manifest.json` is traced by at least one obligation or operation mapping in `mapping.json`.
    if not isinstance(manifest_data, dict) or not isinstance(mapping_data, dict):
        return []
    specs = manifest_data.get("formal_specifications") or []
    manifest_spec_ids = set()
    for s in specs:
        if isinstance(s, dict):
            sid = s.get("id") or s.get("spec_id")
            if sid:
                manifest_spec_ids.add(str(sid))
    # No items or errors apply when `not manifest_spec_ids`; return an empty list.
    if not manifest_spec_ids:
        return []

    covered_ids: set[str] = set()

    def _collect_spec_refs(raw_val: object) -> None:
        if isinstance(raw_val, list):
            for item_v in raw_val:
                _collect_spec_refs(item_v)
        elif isinstance(raw_val, str) and raw_val.strip():
            s_val = raw_val.strip()
            covered_ids.add(s_val)
            for tok in re.findall(r"\bSPEC-[A-Za-z0-9_-]+\b", s_val):
                covered_ids.add(tok)
            for part_s in re.split(r"[,;\s]+", s_val):
                if part_s.strip():
                    covered_ids.add(part_s.strip())

    for obl in mapping_data.get("verification_obligations", []):
        if isinstance(obl, dict):
            for k_obl in ("source_spec_id", "spec_id", "spec_ids", "covers", "spec_refs", "id"):
                if obl.get(k_obl) is not None:
                    _collect_spec_refs(obl.get(k_obl))
    for op in mapping_data.get("operation_mappings", []):
        if isinstance(op, dict):
            for k_op in ("spec_id", "spec_ids", "covers", "spec_refs", "operation", "math_operation"):
                if op.get(k_op) is not None:
                    _collect_spec_refs(op.get(k_op))

    return [sid for sid in sorted(manifest_spec_ids) if sid not in covered_ids]


def extract_source_interface_contracts(cwd: Path, fsm: dict[str, object] | None = None) -> dict[str, dict[str, object]]:
    r"""Extract caller-mandated Python function contracts from ``manifest.json``, user prompt, or ``research/``.

    Distinguishes unimplemented Python stub headers (docstring-only, ``pass``,
    ``...``, or ``NotImplementedError``) from already-implemented helper
    functions via indentation and body inspection, and detects variadic
    ``*args`` skeletons when ``def fn():`` is paired with a docstring declaring
    ``*args`` or ``args: variable/arbitrary/any number...``.

    Args:
        cwd: Workspace root directory containing optional ``manifest.json`` and
            ``research/`` reference documents.
        fsm: Optional pre-loaded FSM dictionary carrying ``original_user_request``.

    Returns:
        Mapping from function name to contract metadata (``name``, ``params``,
        ``has_vararg``, ``has_kwarg``, and ``required_target``).
    """
    contracts: dict[str, dict[str, object]] = {}
    eff_fsm = fsm if isinstance(fsm, dict) else (load_pipeline_fsm(cwd) if (cwd / FSM_FILE_NAME).is_file() else {})

    def _parse_def_blocks_from_text(raw_txt: str, *, allow_required_stub: bool = False) -> None:
        txt = str(raw_txt or "")
        if not txt.strip():
            return
        def_re = re.compile(
            r"^([ \t]*)def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?:->[^:]+)?:[ \t]*(?:\r?\n[ \t]*(?:'''([\s\S]*?)'''|\"\"\"([\s\S]*?)\"\"\"))?",
            re.MULTILINE,
        )
        discovered: list[dict[str, object]] = []
        for m in def_re.finditer(txt):
            def_indent = len(m.group(1).expandtabs(4))
            fn_name = m.group(2)
            if fn_name.startswith(("test_", "_")):
                continue
            raw_params = m.group(3).strip()
            doc_body = (m.group(4) or m.group(5) or "").strip()
            # Check whether executable indented body lines follow the header/docstring
            tail_lines = txt[m.end() :].splitlines()
            body_exec_lines: list[str] = []
            is_stub = True
            for idx_tl, tl in enumerate(tail_lines):
                if idx_tl == 0 and not tl.strip():
                    continue
                stripped = tl.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("```"):
                    break
                line_indent = len(tl[: len(tl) - len(tl.lstrip())].expandtabs(4))
                # Stop scanning further once `line_indent <= def_indent`.
                if line_indent <= def_indent:
                    break
                body_exec_lines.append(stripped)
            if not body_exec_lines:
                is_stub = True
            elif len(body_exec_lines) == 1 and re.match(r"^(?:pass|\.\.\.|raise\s+NotImplementedError\b)", body_exec_lines[0]):
                is_stub = True
            elif len(body_exec_lines) == 1 and body_exec_lines[0].startswith("return "):
                try:
                    fn_ast = ast.parse(f"def {fn_name}({raw_params}):\n    {body_exec_lines[0]}\n").body[0]
                    if isinstance(fn_ast, ast.FunctionDef):
                        bound_params = {
                            a.arg
                            for a in (list(fn_ast.args.posonlyargs) + list(fn_ast.args.args) + list(fn_ast.args.kwonlyargs))
                        }
                        if fn_ast.args.vararg:
                            bound_params.add(fn_ast.args.vararg.arg)
                        if fn_ast.args.kwarg:
                            bound_params.add(fn_ast.args.kwarg.arg)
                        builtin_names = set(dir(builtins))
                        loaded_names = {
                            sub.id for sub in ast.walk(fn_ast.body[0]) if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
                        }
                        is_stub = bool(loaded_names - bound_params - builtin_names)
                    else:
                        is_stub = False
                except SyntaxError:
                    is_stub = False
            else:
                is_stub = False

            parsed_params: list[str] = []
            has_vararg = False
            has_kwarg = False
            if raw_params:
                try:
                    stub_fn = ast.parse(f"def {fn_name}({raw_params}):\n    pass\n").body[0]
                    if isinstance(stub_fn, ast.FunctionDef):
                        for a in list(stub_fn.args.posonlyargs) + list(stub_fn.args.args):
                            if a.arg not in ("self", "cls"):
                                parsed_params.append(a.arg)
                        if stub_fn.args.vararg is not None:
                            has_vararg = True
                            parsed_params.append(f"*{stub_fn.args.vararg.arg}")
                        for ka in stub_fn.args.kwonlyargs:
                            parsed_params.append(ka.arg)
                        if stub_fn.args.kwarg is not None:
                            has_kwarg = True
                            parsed_params.append(f"**{stub_fn.args.kwarg.arg}")
                except SyntaxError:
                    continue
            elif re.search(r"\bargs\s*:\s*(?:any|arbitrary|variable)\s+number|\*args\b", doc_body, re.IGNORECASE):
                has_vararg = True
                parsed_params = ["*args"]
            discovered.append(
                {
                    "name": fn_name,
                    "params": parsed_params,
                    "has_vararg": has_vararg,
                    "has_kwarg": has_kwarg,
                    "is_stub": is_stub,
                }
            )

        has_any_stub = any(bool(d["is_stub"]) for d in discovered)
        has_any_impl = any(not bool(d["is_stub"]) for d in discovered)
        for d in discovered:
            if has_any_stub and has_any_impl and not d["is_stub"]:
                # Already-implemented helper function provided as context alongside an unimplemented stub
                continue
            fn_n = str(d["name"])
            contracts[fn_n] = {
                "name": fn_n,
                "params": d["params"],
                "has_vararg": d["has_vararg"],
                "has_kwarg": d["has_kwarg"],
                "required_target": bool(allow_required_stub and d["is_stub"]),
            }

    orig_req = str(eff_fsm.get("original_user_request") or "").strip()
    if orig_req:
        _parse_def_blocks_from_text(orig_req, allow_required_stub=True)

    res_dir = cwd / "research"
    # Scan directory contents when `res_dir.is_dir()`.
    if res_dir.is_dir():
        for rf in sorted(res_dir.glob("*")):
            if rf.is_file() and rf.suffix.lower() in (".md", ".txt", ".rst"):
                try:
                    _parse_def_blocks_from_text(rf.read_text(encoding="utf-8", errors="replace"), allow_required_stub=False)
                except Exception:
                    pass

    man_p = cwd / "manifest.json"
    if man_p.is_file():
        try:
            m_data = json.loads(man_p.read_text(encoding="utf-8"))
            t_if = m_data.get("target_interface")
            if isinstance(t_if, dict) and isinstance(t_if.get("entrypoints"), list):
                for ep in t_if["entrypoints"]:
                    if not isinstance(ep, dict) or not isinstance(ep.get("name"), str):
                        continue
                    en = ep["name"].strip()
                    if isinstance(ep.get("signature"), str) and ep["signature"].strip().startswith("def "):
                        _parse_def_blocks_from_text(ep["signature"], allow_required_stub=True)
                    if isinstance(ep.get("parameters"), list) and ep["parameters"]:
                        p_list: list[str] = []
                        h_var = False
                        h_kw = False
                        for po in ep["parameters"]:
                            if isinstance(po, dict) and isinstance(po.get("name"), str):
                                pn = po["name"].strip()
                                pk = str(po.get("kind") or "")
                                if pn.startswith("**") or pk == "var_keyword":
                                    h_kw = True
                                    p_list.append(pn if pn.startswith("**") else f"**{pn.lstrip('*')}")
                                elif pn.startswith("*") or pk == "var_positional":
                                    h_var = True
                                    p_list.append(pn if pn.startswith("*") else f"*{pn}")
                                elif pn:
                                    p_list.append(pn)
                        # Preserve prompt-level skeleton contract if already extracted; otherwise register manifest contract.
                        if en not in contracts:
                            contracts[en] = {
                                "name": en,
                                "params": p_list,
                                "has_vararg": h_var,
                                "has_kwarg": h_kw,
                                "required_target": True,
                            }
        except Exception:
            pass
    return contracts


def validate_dimensional_unit_consistency(
    manifest_data: dict[str, object], mapping_data: dict[str, object]
) -> list[str]:
    r"""Validate optional SI / dimensional ``unit`` tags across ``manifest.json`` and ``mapping.json``.

    Checks balanced dimensional bracket notation (e.g. ``[L]``, ``[T^-1]``) and
    verifies that any symbol carrying a ``unit`` tag in both ``manifest.json``
    (``variables[*]``) and ``mapping.json`` (``type_mappings[*]``) preserves
    identical normalized units across Stage 1 and Stage 2.

    Args:
        manifest_data: Parsed ``manifest.json`` dictionary.
        mapping_data: Parsed ``mapping.json`` dictionary.

    Returns:
        List of dimensional syntax or cross-stage unit mismatch error strings.
    """
    # Verify balanced dimensional brackets (e.g. `[L*T^-1]`) and ensure matching symbols in `manifest.json` and `mapping.json` declare identical physical units.
    errs: list[str] = []
    man_units: dict[str, str] = {}
    for v in (manifest_data.get("variables") or []):  # type: ignore[union-attr]
        if isinstance(v, dict) and isinstance(v.get("symbol"), str) and isinstance(v.get("unit"), str):
            u_raw = v["unit"].strip()
            if not u_raw:
                continue
            if u_raw.count("[") != u_raw.count("]"):
                errs.append(f"Malformed Dimensional Unit on '{v['symbol']}': unbalanced brackets in {u_raw!r}.")
                continue
            man_units[v["symbol"].strip()] = re.sub(r"\s+", "", u_raw)

    for tm in (mapping_data.get("type_mappings") or []):  # type: ignore[union-attr]
        if not isinstance(tm, dict):
            continue
        sym = str(tm.get("symbol") or tm.get("math_symbol") or "").strip()
        u_map = str(tm.get("unit") or "").strip()
        if not sym or not u_map:
            continue
        if u_map.count("[") != u_map.count("]"):
            errs.append(f"Malformed Dimensional Unit in type_mappings for '{sym}': unbalanced brackets in {u_map!r}.")
            continue
        norm_map = re.sub(r"\s+", "", u_map)
        if sym in man_units and man_units[sym] != norm_map:
            errs.append(
                f"Dimensional Unit Mismatch for '{sym}': manifest.json declares unit '{man_units[sym]}', "
                f"but mapping.json type_mappings declares '{norm_map}'."
            )
    return errs


# ============================================================================
# Section 4: `mapping.json` Schema, `AST-1` Predicate Compilation & Stub `mypy` Check
# Validates Stage 2 `public_interface`, `AST-1` `eval`-mode predicates, variable symbol parity, and dependency slice hashes.
# ============================================================================

def normalize_math_symbol_variants(raw_sym: str) -> set[str]:
    r"""Extract canonical Python identifier candidates from a LaTeX mathematical symbol.

    Normalizes finite-difference increments (``\Delta t`` -> ``{'dt', 'delta_t', 'd_t'}``,
    excluding bare coordinate ``'t'``), unwraps LaTeX formatting/accent macros, and
    strips superscripts/subscripts (``q_i^n`` -> ``{'q', 'q_i', 'q_i_n'}``) so subscripted
    ``manifest.json`` ``input``/``parameter`` variables participate in Stage 2 parity checks.
    """
    s = str(raw_sym or "").strip().strip("$")
    if not s:
        return set()
    for _ in range(3):
        s = re.sub(
            r"\\(?:mathbf|mathrm|mathcal|mathbb|text|operatorname|hat|tilde|bar|vec)\{([^{}]+)\}",
            r"\1",
            s,
        ).strip()
    m_delta = re.match(r"^\\?[Dd]elta\s*([A-Za-z][A-Za-z0-9_]*)$", s)
    if m_delta:
        base_d = m_delta.group(1)
        return {
            f"d{base_d}",
            f"delta_{base_d}",
            f"d_{base_d}",
            f"d{base_d}".lower(),
            f"delta_{base_d}".lower(),
        }
    variants: set[str] = set()
    no_sup = re.sub(r"\^(?:\{[^{}]*\}|[A-Za-z0-9_+\-]+)", "", s).strip()
    no_sub_sup = re.sub(r"_(?:\{[^{}]*\}|[A-Za-z0-9_+\-]+)", "", no_sup).strip()
    for cand in (s, no_sup, no_sub_sup):
        cleaned = re.sub(r"\\([A-Za-z]+)", r"\1", cand)
        cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", cleaned).strip("_")
        if cleaned and cleaned[0].isalpha() and cleaned.isidentifier():
            variants.add(cleaned)
            variants.add(cleaned.lower())
    return variants


_OPAQUE_CONTAINER_TYPE_RE = re.compile(
    r"^(?:dict|Dict|mapping|Mapping|list|List|tuple|Tuple"
    r"|(?:dict|Dict|Mapping|Sequence|list|List|tuple|Tuple)\s*\[[^\]]*\bAny\b[^\]]*\])$"
)


def validate_mapping_structure(data, cwd):
    """Validate ``mapping.json`` schema, parameters, and specification coverage.

    Checks ``public_interface``, ``verification_obligations``, ``type_mappings``,
    ``operation_mappings``, ``precondition_assertions``, interface-obligation
    parameter name consistency, and bidirectional ``SPEC-k`` coverage.

    Phases:
      - Phase 1: Public Interface, Obligation Eval Syntax & Precondition Complexity Gates
      - Phase 2: Bidirectional Manifest Coverage, Foreign-Key Symbol Parity & Stub Composition Check
      - Phase 3: Upstream Provenance Seal Verification

    Args:
        data: Parsed JSON object from ``mapping.json``.
        cwd: Workspace root directory used to load ``manifest.json``.

    Returns:
        List of structural or coverage error strings (empty when valid).
    """
    # --- Phase 1: Public Interface, Obligation Eval Syntax & Precondition Complexity Gates ---
    errors = []
    if not isinstance(data, dict):
        return ["mapping.json must be a JSON object."]
    if not isinstance(data.get("concept_name"), str) or not data.get("concept_name", "").strip():
        errors.append("Missing required field 'concept_name'.")
    pub_iface = data.get("public_interface")
    if not isinstance(pub_iface, dict):
        errors.append("Missing required 'public_interface' object.")
    else:
        mod_name = pub_iface.get("target_module") or pub_iface.get("module_name")
        if not mod_name or not isinstance(mod_name, str):
            errors.append("'public_interface' must specify 'target_module' (or 'module_name').")
        elif ".." in mod_name or "/" in mod_name or "\\" in mod_name:
            errors.append(f"Invalid 'target_module' ({mod_name!r}): path traversal or directory separators are prohibited.")
        else:
            fsm_mp_check = load_pipeline_fsm(Path(cwd)) if (Path(cwd) / FSM_FILE_NAME).is_file() else {}
            mandated_tm = str(
                fsm_mp_check.get("mandated_target_module")
                or extract_mandated_target_module(str(fsm_mp_check.get("original_user_request") or ""))
                or ""
            ).strip()
            norm_mod_py = mod_name.strip() if mod_name.strip().endswith(".py") else f"{mod_name.strip()}.py"
            if mandated_tm and norm_mod_py != mandated_tm:
                errors.append(
                    f"Target Module Mismatch (AST-4a): caller specification mandates target_module '{mandated_tm}', "
                    f"but mapping.json declared '{mod_name}'."
                )
        entrypoints = pub_iface.get("entrypoints")
        if not isinstance(entrypoints, list) or len(entrypoints) == 0:
            errors.append("'public_interface.entrypoints' must be a non-empty list.")
        else:
            for idx, ep in enumerate(entrypoints):
                if isinstance(ep, dict):
                    ep_name = ep.get("name")
                    if not isinstance(ep_name, str) or not ep_name.isidentifier():
                        errors.append(f"Entrypoint {idx} has invalid Python identifier name {ep_name!r}.")
                elif isinstance(ep, str):
                    if not ep.isidentifier():
                        errors.append(f"Entrypoint {idx} has invalid Python identifier name {ep!r}.")
                else:
                    errors.append(f"Entrypoint {idx} must be an object or identifier string.")

    obls = data.get("verification_obligations")
    seen_obl_ids = set()
    if not isinstance(obls, list) or len(obls) == 0:
        errors.append("Missing or empty 'verification_obligations' list.")
    else:
        for i, obl in enumerate(obls):
            if not isinstance(obl, dict):
                errors.append(f"Verification obligation {i} must be an object.")
                continue
            oid = obl.get("obligation_id") or obl.get("id")
            if not oid or not isinstance(oid, str):
                errors.append(f"Verification obligation {i} is missing 'obligation_id' (or 'id').")
            elif oid in seen_obl_ids:
                errors.append(f"Duplicate verification obligation ID '{oid}'.")
            else:
                seen_obl_ids.add(oid)
            if not (obl.get("spec_id") or obl.get("source_spec_id")):
                errors.append(f"Verification obligation {i} is missing 'spec_id' (or 'source_spec_id').")
            ptype = obl.get("predicate_type")
            if ptype not in _VALID_PREDICATE_TYPES:
                errors.append(
                    f"Verification obligation {i} has invalid 'predicate_type' ({ptype!r}); "
                    f"must be one of {sorted(_VALID_PREDICATE_TYPES)}."
                )
            # Enforce `AST-1`: normalize predicate field aliases to `formal_predicate` and
            # parse with `ast.parse(..., mode="eval")` so procedural `=` statements inside
            # `lambda` or empty predicates are caught immediately in <1 ms.
            if not str(obl.get("formal_predicate") or "").strip():
                for alias_fp in ("predicate", "expression", "formal_property"):
                    alias_val = obl.get(alias_fp)
                    if isinstance(alias_val, str) and alias_val.strip():
                        obl["formal_predicate"] = alias_val.strip()
                        break
            fp_raw = str(obl.get("formal_predicate") or "").strip()
            if not fp_raw:
                errors.append(
                    f"Verification obligation '{oid or i}' is missing non-empty 'formal_predicate' Python expression."
                )
            else:
                fp_eval_cand = (
                    f"(not ({fp_raw.split(' implies ', 1)[0]})) or ({fp_raw.split(' implies ', 1)[1]})"
                    if " implies " in fp_raw
                    else fp_raw
                )
                try:
                    ast.parse(fp_eval_cand, mode="eval")
                except SyntaxError as fp_syn:
                    errors.append(
                        f"Invalid Python eval expression in '{oid or i}' formal_predicate (SyntaxError: {fp_syn.msg} — "
                        f"procedural statements/assignments like '=' are prohibited inside formal_predicate): {fp_raw!r}"
                    )

    t_maps = data.get("type_mappings")
    if not isinstance(t_maps, list) or len(t_maps) == 0:
        errors.append("Missing or empty 'type_mappings' list.")
    else:
        for idx, tm in enumerate(t_maps):
            if not isinstance(tm, dict) or not (tm.get("symbol") or tm.get("math_symbol")) or not tm.get("computational_type"):
                errors.append(f"type_mappings[{idx}] must specify 'symbol' (or 'math_symbol') and 'computational_type'.")

    op_maps = data.get("operation_mappings")
    if op_maps is None:
        data["operation_mappings"] = []
    elif not isinstance(op_maps, list):
        errors.append("'operation_mappings' must be a list when provided.")
    else:
        for idx, om in enumerate(op_maps):
            if not isinstance(om, dict) or not (
                om.get("operation") or om.get("math_operation") or om.get("spec_id")
            ):
                errors.append(f"operation_mappings[{idx}] must specify 'operation', 'math_operation', or 'spec_id'.")

    pre_asserts = data.get("precondition_assertions")
    if not isinstance(pre_asserts, list) or any(not isinstance(a, str) or not a.strip() for a in pre_asserts):
        errors.append("'precondition_assertions' must be a list of non-empty assertion strings.")
    else:
        # Enforce `AST-1` on `precondition_assertions`: every assertion must parse as valid
        # Python and stick to O(1)/O(N) structural checks (banning O(N^3) decompositions like `eigvalsh`).
        forbidden_pre_calls = {
            "eig", "eigh", "eigvals", "eigvalsh", "svd", "cond", "det", "slogdet",
            "inv", "pinv", "cholesky", "qr", "matrix_rank",
        }
        for idx_pa, pa_str in enumerate(pre_asserts):
            try:
                pa_tree = ast.parse(pa_str, mode="exec")
            except SyntaxError as pa_syn:
                errors.append(f"Invalid Python syntax in precondition_assertions[{idx_pa}] ({pa_syn.msg}): {pa_str!r}")
                continue
            for pa_n in ast.walk(pa_tree):
                if isinstance(pa_n, ast.Call):
                    pa_fn = (
                        pa_n.func.id
                        if isinstance(pa_n.func, ast.Name)
                        else (pa_n.func.attr if isinstance(pa_n.func, ast.Attribute) else "")
                    )
                    if pa_fn in forbidden_pre_calls:
                        errors.append(
                            f"Forbidden expensive/spectral decomposition '{pa_fn}()' in precondition_assertions[{idx_pa}] ({pa_str!r}): "
                            "preconditions must be O(1)/O(N) structural checks (ndim, shape, finite, scalar bounds), never O(N^3) decompositions."
                        )

    declared_param_names = set()
    has_typed_entrypoint_params = False
    has_varargs_escape = False
    src_contracts = extract_source_interface_contracts(Path(cwd))
    declared_ep_names: set[str] = set()
    if isinstance(pub_iface, dict) and isinstance(pub_iface.get("entrypoints"), list):
        dict_eps = [ep for ep in pub_iface["entrypoints"] if isinstance(ep, dict) and isinstance(ep.get("parameters"), list)]
        if dict_eps and len(dict_eps) == len(pub_iface["entrypoints"]):
            has_typed_entrypoint_params = True
            for ep in dict_eps:
                ep_n = str(ep.get("name") or "").strip()
                if ep_n:
                    declared_ep_names.add(ep_n)
                ep_pnames: list[str] = []
                ep_has_vararg = False
                for p_obj in ep.get("parameters", []):
                    if isinstance(p_obj, dict) and isinstance(p_obj.get("name"), str):
                        pname = p_obj["name"].strip()
                        pkind = str(p_obj.get("kind") or "").strip()
                        if pname.startswith("**") or pkind == "var_keyword":
                            has_varargs_escape = True
                            ep_pnames.append(pname if pname.startswith("**") else f"**{pname.lstrip('*')}")
                        elif pname.startswith("*") or pkind == "var_positional":
                            has_varargs_escape = True
                            ep_has_vararg = True
                            ep_pnames.append(pname if pname.startswith("*") else f"*{pname}")
                        elif pname:
                            declared_param_names.add(pname)
                            ep_pnames.append(pname)
                if ep_n in src_contracts:
                    sc = src_contracts[ep_n]
                    exp_params: list[str] = list(sc.get("params") or [])  # type: ignore[arg-type]
                    exp_vararg = bool(sc.get("has_vararg"))
                    if exp_vararg and not ep_has_vararg:
                        errors.append(
                            f"Source-Interface Variadic Mismatch for '{ep_n}': source specification requires variadic '*args' "
                            f"({exp_params}), but mapping.json declared fixed positional parameter(s) {ep_pnames}. "
                            "Declare '{\"name\": \"*args\", \"type\": \"...\", \"kind\": \"var_positional\"}' in public_interface.entrypoints."
                        )
                    elif not exp_vararg and len(ep_pnames) != len(exp_params):
                        errors.append(
                            f"Source-Interface Parameter Count Mismatch for '{ep_n}': mapping.json declares {ep_pnames} "
                            f"({len(ep_pnames)} param(s)), but source specification mandates exact signature '{ep_n}({', '.join(exp_params)})' "
                            f"({len(exp_params)} param(s)). Derive internal variables inside the function body rather than adding/dropping parameters."
                        )
        required_targets = [k for k, v in src_contracts.items() if v.get("required_target")]
        missing_targets = [t for t in required_targets if t not in declared_ep_names]
        if missing_targets and declared_ep_names:
            errors.append(
                f"Source-Interface Missing Required Entrypoint(s): source specification mandates entrypoint(s) {missing_targets}, "
                f"but mapping.json declared {sorted(declared_ep_names)}."
            )

    if has_typed_entrypoint_params and declared_param_names and not has_varargs_escape:
        # Enforce Interface-Obligation Parameter Consistency (prevents OBL-3 'bc_type' TypeError contradictions!)
        for idx, ac in enumerate(data.get("anchor_test_cases", []) if isinstance(data.get("anchor_test_cases"), list) else []):
            if isinstance(ac, dict):
                ac_in = ac.get("inputs") if isinstance(ac.get("inputs"), dict) else ac.get("input")
                if isinstance(ac_in, dict):
                    unknown_keys = sorted(k for k in ac_in.keys() if isinstance(k, str) and k.isidentifier() and k not in declared_param_names)
                    if unknown_keys:
                        errors.append(
                            f"Interface-Anchor Parameter Mismatch in anchor_test_cases[{idx}]: input key(s) {unknown_keys} "
                            f"are not declared in public_interface.entrypoints parameters ({sorted(declared_param_names)})."
                        )
        for idx, obl in enumerate(obls if isinstance(obls, list) else []):
            if isinstance(obl, dict):
                obl_id = obl.get("obligation_id") or obl.get("id") or f"OBL-{idx + 1}"
                obl_in = obl.get("inputs") if isinstance(obl.get("inputs"), dict) else obl.get("input")
                if isinstance(obl_in, dict):
                    unknown_keys = sorted(k for k in obl_in.keys() if isinstance(k, str) and k.isidentifier() and k not in declared_param_names)
                    if unknown_keys:
                        errors.append(
                            f"Interface-Obligation Parameter Mismatch in '{obl_id}': input key(s) {unknown_keys} "
                            f"are not declared in public_interface.entrypoints parameters ({sorted(declared_param_names)}). "
                            "Add the parameter(s) to public_interface or align the obligation inputs."
                        )

    # --- Phase 2: Bidirectional Manifest Coverage, Foreign-Key Symbol Parity & Stub Composition Check ---
    manifest_path = cwd / "manifest.json"
    eq_mode = "approximate_tolerance"
    if manifest_path.exists():
        try:
            m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            v_contract = m_data.get("verification_contract")
            if isinstance(v_contract, dict) and isinstance(v_contract.get("equivalence_mode"), str):
                eq_mode = v_contract["equivalence_mode"].strip()
            missing_ids = validate_obligation_coverage(m_data, data)
            if missing_ids:
                errors.append(
                    f"Incomplete Specification Coverage: The following claim(s) from manifest.json lack matching "
                    f"verification_obligations or operation_mappings: {', '.join(missing_ids)}."
                )
            errors.extend(validate_dimensional_unit_consistency(m_data, data))
            # Enforce exact foreign-key symbol coverage (with permissive stem fallback) + stub mypy call-composition check.
            if has_typed_entrypoint_params and not has_varargs_escape and not src_contracts:
                exact_tm_syms: set[str] = set()
                type_map_syms: set[str] = set()
                for tm_obj in (t_maps if isinstance(t_maps, list) else []):
                    if isinstance(tm_obj, dict):
                        raw_ts = str(tm_obj.get("symbol") or tm_obj.get("math_symbol") or "").strip()
                        if raw_ts:
                            exact_tm_syms.add(raw_ts)
                            exact_tm_syms.add(raw_ts.strip("$").strip())
                        clean_ts = re.sub(r"^\\[A-Za-z]+\{([A-Za-z0-9_]+)\}$", r"\1", raw_ts).lstrip("\\").strip()
                        if clean_ts:
                            type_map_syms.add(clean_ts)
                all_mapped_syms = declared_param_names | type_map_syms | exact_tm_syms
                norm_declared = {p.lower().lstrip("_") for p in all_mapped_syms}
                for v_item in (m_data.get("variables") or []):
                    if not isinstance(v_item, dict):
                        continue
                    v_role = str(v_item.get("role") or "").strip().lower()
                    v_sym = str(v_item.get("symbol") or "").strip()
                    v_clean = v_sym.strip("$").strip()
                    sym_variants = normalize_math_symbol_variants(v_sym)
                    if v_role in ("input", "parameter") and (v_clean or sym_variants):
                        covered_fk = (v_sym in exact_tm_syms) or (v_clean in exact_tm_syms) or (v_clean in declared_param_names)
                        covered_stem = bool(sym_variants) and any(
                            sv in all_mapped_syms or sv.lower().lstrip("_") in norm_declared for sv in sym_variants
                        )
                        if not covered_fk and not covered_stem:
                            errors.append(
                                f"Symbolic Parameter Parity Mismatch: manifest.json variable '{v_sym}' "
                                f"(role='{v_role}') must appear verbatim in type_mappings[*].symbol "
                                f"(mapping '{v_sym}' to its computational type while keeping Python entrypoint parameter names idiomatic) "
                                f"or as parameter {sorted(sym_variants)} in public_interface.entrypoints ({sorted(all_mapped_syms)})."
                            )
                for ep_chk in (pub_iface.get("entrypoints") or [] if isinstance(pub_iface, dict) else []):
                    if isinstance(ep_chk, dict):
                        for p_chk in (ep_chk.get("parameters") or []):
                            if isinstance(p_chk, dict):
                                pt_s = str(p_chk.get("type") or "").strip()
                                if _OPAQUE_CONTAINER_TYPE_RE.match(pt_s):
                                    errors.append(
                                        f"Opaque Container Type Prohibited on parameter '{p_chk.get('name')}' ('{pt_s}'): "
                                        "replace bare unparameterized containers ('dict', 'Dict[str, Any]', bare 'list'/'tuple') "
                                        "with explicit typed parameters or parameterized generics (e.g. 'NDArray[np.float64]', "
                                        "'float', 'int', 'list[NDArray[np.complex128]]', 'tuple[int, int]')."
                                    )
                errors.extend(_validate_mapping_stub_composition_types(data, Path(cwd)))
        except Exception:
            pass

    # --- Phase 3: Upstream Provenance Seal Verification ---
    # Verify declared upstream_modules against sealed specs/<spec_slug>/PROVENANCE.json digests.
    raw_up_mods = data.get("upstream_modules")
    if isinstance(raw_up_mods, list):
        for idx_u, up_m in enumerate(raw_up_mods):
            if not isinstance(up_m, dict):
                errors.append(f"upstream_modules[{idx_u}] must be an object.")
                continue
            u_slug = str(up_m.get("spec_slug") or "").strip()
            u_mod = str(up_m.get("module") or "").strip()
            if not u_slug or ".." in u_slug or "/" in u_slug or not u_mod or ".." in u_mod or "/" in u_mod:
                errors.append(f"upstream_modules[{idx_u}] has invalid spec_slug/module ({u_slug!r}, {u_mod!r}).")
                continue
            u_dir = Path(cwd) / "specs" / u_slug
            u_prov_p = u_dir / "PROVENANCE.json"
            u_mod_p = u_dir / u_mod
            if not u_prov_p.is_file() or not u_mod_p.is_file():
                errors.append(
                    f"Upstream Archive Missing for upstream_modules[{idx_u}]: expected sealed '{u_prov_p}' and '{u_mod_p}'."
                )
                continue
            try:
                u_prov = json.loads(u_prov_p.read_text(encoding="utf-8"))
                u_check = dict(u_prov)
                stored_seal = str(u_check.pop("seal_sha256", "") or "")
                expected_seal = compute_canonical_seal_digest(u_check)
                art_map = u_prov.get("artifact_hashes") if isinstance(u_prov.get("artifact_hashes"), dict) else {}
                actual_mod_sha = compute_file_sha256(u_mod_p)
                if u_prov.get("status") != "VERIFIED_COMPLETE" or stored_seal != expected_seal or art_map.get(u_mod) != actual_mod_sha:
                    errors.append(
                        f"Upstream Archive Seal/Hash Mismatch for 'specs/{u_slug}/{u_mod}': PROVENANCE.json seal or module digest failed verification."
                    )
            except Exception as exc_u:
                errors.append(f"Upstream Archive unreadable for 'specs/{u_slug}': {exc_u}")

    if eq_mode == "approximate_tolerance" and isinstance(obls, list):
        for idx, obl in enumerate(obls):
            if not isinstance(obl, dict):
                continue
            obl_id = obl.get("obligation_id") or obl.get("id") or f"OBL-{idx + 1}"
            tol_str = str(obl.get("tolerance", "")).strip().lower()
            fp = str(obl.get("formal_predicate") or obl.get("formal_property") or "").strip()
            if not fp or tol_str == "exact":
                continue
            if re.search(r"(?<![A-Za-z0-9_.])(?:max|min)\s*\([^)]*\b(?:np\.)?abs\s*\(", fp):
                errors.append(
                    f"Unsafe multi-dimensional array comparison in '{obl_id}' formal_predicate: Python built-in max()/min() "
                    "fails on multi-element NumPy arrays and omits atol. Express continuous equivalence using "
                    "'np.allclose(..., rtol=rtol, atol=atol)' or 'math.isclose(..., rel_tol=rtol, abs_tol=atol)'."
                )
                continue
            try:
                fp_tree = ast.parse(fp, mode="eval")
                for sub_n in ast.walk(fp_tree):
                    if isinstance(sub_n, ast.Compare):
                        viol = _compare_continuous_eq_violation(sub_n, check_binop=True)
                        if viol:
                            errors.append(
                                f"Exact floating-point equality ('==') in '{obl_id}' formal_predicate ({fp!r}): "
                                "under 'approximate_tolerance', use 'math.isclose' or 'np.allclose(..., rtol=rtol, atol=atol)' "
                                "(or set '\"tolerance\": \"exact\"' only for discrete integer/boolean invariants)."
                            )
                            break
            except Exception:
                pass

    return errors


def _validate_mapping_stub_composition_types(data: dict, cwd: Path) -> list[str]:
    r"""Synthesize a lightweight typed stub for entrypoints + cross-entrypoint ``formal_predicate`` calls and run ``mypy``."""
    pub = data.get("public_interface") or {}
    eps = pub.get("entrypoints") if isinstance(pub, dict) else None
    if not isinstance(eps, list) or len(eps) < 2:
        return []
    ep_names = {str(ep.get("name") or "").strip() for ep in eps if isinstance(ep, dict) and ep.get("name")}
    if len(ep_names) < 2:
        return []
    lines = [
        "from __future__ import annotations",
        "import math",
        "from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union",
        "import numpy as np",
        "from numpy.typing import ArrayLike, NDArray",
    ]
    param_types_by_name: dict[str, str] = {}
    for ep in eps:
        if not isinstance(ep, dict) or not str(ep.get("name") or "").strip():
            continue
        ename = str(ep["name"]).strip()
        p_parts = []
        for p in (ep.get("parameters") or []):
            if not isinstance(p, dict) or not p.get("name"):
                continue
            pn = str(p["name"]).strip()
            pt = str(p.get("type") or "Any").strip() or "Any"
            if not pn.startswith("*"):
                param_types_by_name.setdefault(pn, pt)
            p_parts.append(f"{pn}: {pt}" if pt else pn)
        rtype = str(ep.get("return_type") or ep.get("returns") or "Any").strip() or "Any"
        lines.append(f"def {ename}({', '.join(p_parts)}) -> {rtype}: ...")

    check_idx = 0
    for obl in (data.get("verification_obligations") or []):
        if not isinstance(obl, dict):
            continue
        fp = str(obl.get("formal_predicate") or "").strip()
        used_eps = [en for en in ep_names if re.search(rf"\b{re.escape(en)}\s*\(", fp)]
        if len(used_eps) < 2 or "lambda" in fp or " implies " in fp:
            continue
        try:
            tree = ast.parse(fp, mode="eval")
        except SyntaxError:
            continue
        free_vars = sorted({
            n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id in param_types_by_name and n.id not in ep_names
        })
        arg_sig = ", ".join(f"{v}: {param_types_by_name[v]}" for v in free_vars)
        lines.append(f"def _check_obl_{check_idx}({arg_sig}) -> Any:")
        lines.append(f"    return {fp}")
        check_idx += 1

    if check_idx == 0:
        return []
    mypy_cands = [cwd / ".venv" / "bin" / "mypy", HOOKS_REPO_ROOT / ".venv" / "bin" / "mypy"]
    mypy_bin = next((mc for mc in mypy_cands if mc.is_file()), None)
    if mypy_bin is None:
        return []
    scratch_d = cwd / ".pipeline_scratch"
    scratch_d.mkdir(parents=True, exist_ok=True)
    stub_p = scratch_d / "_mapping_stub_typecheck.py"
    try:
        stub_p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        res = subprocess.run(
            [str(mypy_bin), "--no-error-summary", "--ignore-missing-imports", str(stub_p)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return []
    if res.returncode == 0:
        return []
    errs: list[str] = []
    for ln_m in (res.stdout or "").splitlines():
        if ": error:" in ln_m and ("arg-type" in ln_m or "call-arg" in ln_m or "index" in ln_m):
            errs.append(
                f"Cross-Entrypoint Type Composition Mismatch in formal_predicate (mypy): {ln_m.split(': error:', 1)[1].strip()}"
            )
    return errs[:3]


def _compute_manifest_core_hash(manifest_path: Path) -> str:
    """Hash ``formal_specifications``, ``variables``, and ``target_interface`` in ``manifest.json`` for ``slice_dependency_dag`` dependency tracking.

    Args:
        manifest_path: Path to ``manifest.json`` on disk.

    Returns:
        64-character SHA-256 hex digest, or ``""`` if unreadable.
    """
    # Hash core equation and interface slices while excluding `ambiguities` and `verification_contract` tolerances.
    if not manifest_path.is_file():
        return ""
    try:
        d = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return ""
        core = {k: d.get(k) for k in ("formal_specifications", "variables", "target_interface")}
        return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
    except Exception:
        return ""


def _compute_obligation_hashes(mapping_path: Path) -> dict[str, str]:
    """Compute per-obligation SHA-256 hashes inside ``mapping.json`` for incremental Stage 4 obligation scoping.

    Args:
        mapping_path: Path to ``mapping.json`` on disk.

    Returns:
        Mapping ``{obligation_id: sha256_hex}``.
    """
    # Hash each `OBL-j` entry independently so `@Auditor` knows which obligations changed during a rewind.
    if not mapping_path.is_file():
        return {}
    try:
        d = json.loads(mapping_path.read_text(encoding="utf-8"))
        out: dict[str, str] = {}
        for idx, ob in enumerate((d.get("verification_obligations") if isinstance(d, dict) else None) or [], 1):
            if isinstance(ob, dict):
                oid = str(ob.get("obligation_id") or ob.get("id") or f"OBL-{idx}")
                out[oid] = hashlib.sha256(json.dumps(ob, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
        return out
    except Exception:
        return {}


def _compute_mapping_implementation_contract_hash(mapping_path: Path) -> str:
    """Hash the implementation-governing slices of ``mapping.json`` for ``S2 -> S4`` unchanged-interface skip checks.

    Covers ``public_interface``, ``type_mappings``, ``operation_mappings``, and
    ``precondition_assertions`` while excluding ``verification_obligations`` and
    ``anchor_test_cases`` so test-only mapping fixes can skip redundant Stage 3
    re-runs when ``<target_module>.py`` already satisfies ``AST-4b``.

    Args:
        mapping_path: Path to ``mapping.json`` on disk.

    Returns:
        64-character SHA-256 hex digest, or ``""`` if unreadable.
    """
    # Hash only the fields of `mapping.json` that govern production code generation.
    if not mapping_path.is_file():
        return ""
    try:
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ""
        slice_obj = {
            k: data.get(k)
            for k in ("public_interface", "type_mappings", "operation_mappings", "precondition_assertions", "upstream_modules")
        }
        canon = json.dumps(slice_obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()
    except Exception:
        return ""


# ============================================================================
# Section 5: Anchor-Literal Anti-Leakage & Target-Module Entrypoint Verification
# Blocks hardcoded test-vector lookup cheats in Stage 3 modules and verifies all declared entrypoints exist.
# ============================================================================

def _collect_nontrivial_anchor_literals(val: object) -> set[str]:
    """Extract canonicalized non-trivial scalar and vector literals from ``anchor_test_cases``.

    Args:
        val: Arbitrary JSON value inside ``mapping.json`` ``anchor_test_cases``.

    Returns:
        Set of normalized literal strings excluding trivial mathematical base
        constants (``0``, ``1``, ``-1``, ``2``).
    """
    # Collect non-trivial numeric constants from `anchor_test_cases` (ignoring `0`, `1`, `-1`, `2`) so `detect_anchor_test_leakage` can spot hardcoded answers.
    out: set[str] = set()
    if isinstance(val, bool) or val is None:
        return out
    if isinstance(val, (int, float)):
        if val not in _TRIVIAL_MATH_BASE_SCALARS and abs(float(val)) not in (0.0, 1.0, 2.0):
            out.add(f"{float(val):.8g}")
    elif isinstance(val, (list, tuple)):
        if len(val) >= 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in val):
            out.add(json.dumps([round(float(x), 8) for x in val]))
        for item in val:
            out.update(_collect_nontrivial_anchor_literals(item))
    elif isinstance(val, dict):
        for sub_v in val.values():
            out.update(_collect_nontrivial_anchor_literals(sub_v))
    return out


def detect_anchor_test_leakage(py_path: Path, cwd: Path) -> list[str]:
    """Detect hardcoded anchor test-vector cheats and forbidden imports in production Python code.

    Implements the empirically calibrated RRSI Anchor-Scoped Generalization
    Critic (``M2``, 0% false-positive rate on canonical mathematical base cases
    ``0``, ``1``, ``-1``, ``2``, ``len()``, ``ndim``, ``shape``) together with
    Cleanroom / ``upstream_modules`` DAG import checks (``M6``).

    Args:
        py_path: Path to the production ``*.py`` module being validated.
        cwd: Workspace root directory containing optional ``mapping.json``.

    Returns:
        List of blocking AST generalization or import-boundary error strings.
    """
    if not py_path.is_file():
        return []
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    except Exception:
        return []

    anchor_lits: set[str] = set()
    map_p = cwd / "mapping.json"
    if map_p.is_file():
        try:
            mp = json.loads(map_p.read_text(encoding="utf-8"))
            anchor_lits.update(_collect_nontrivial_anchor_literals(mp.get("anchor_test_cases") or []))
        except Exception:
            pass

    errors: list[str] = []
    # Walk every syntax node `node` in `ast.walk(tree)` to inspect module imports.
    for node in ast.walk(tree):
        # M6: Cleanroom & control-plane import firewall on production modules
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod_names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else ([node.module] if node.module else [])
            )
            for mname in mod_names:
                leaf = mname.split(".")[-1]
                if leaf.startswith(("verify_", "test_", "smoke_")) or "pipeline_guards" in mname:
                    errors.append(
                        f"Line {node.lineno}: Cleanroom Import Violation — production module cannot import '{mname}'."
                    )

        # M2: Anchor-scoped hardcoded test-vector branch check
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Compare) and any(isinstance(op, ast.Eq) for op in test.ops):
                lhs_is_meta = (
                    (isinstance(test.left, ast.Call) and isinstance(test.left.func, ast.Name) and test.left.func.id in {"len", "type", "isinstance"})
                    or (isinstance(test.left, ast.Attribute) and test.left.attr in {"ndim", "shape", "size", "dtype"})
                )
                if not lhs_is_meta and isinstance(test.left, (ast.Name, ast.Tuple)):
                    non_triv_cmp = False
                    for comp in test.comparators:
                        if isinstance(comp, ast.Constant) and isinstance(comp.value, (int, float)) and not isinstance(comp.value, bool):
                            if comp.value not in _TRIVIAL_MATH_BASE_SCALARS:
                                if (anchor_lits and f"{float(comp.value):.8g}" in anchor_lits) or isinstance(comp.value, float):
                                    non_triv_cmp = True
                        elif isinstance(comp, (ast.List, ast.Tuple)) and len(comp.elts) >= 2:
                            non_triv_cmp = True
                    if non_triv_cmp:
                        for stmt in node.body:
                            if isinstance(stmt, ast.Return) and stmt.value is not None:
                                rv = stmt.value
                                if isinstance(rv, ast.Constant) and rv.value not in _TRIVIAL_MATH_BASE_SCALARS:
                                    errors.append(
                                        f"Line {node.lineno}: Hardcoded test-vector branch detected ('if {ast.unparse(test)}: return {ast.unparse(rv)}'). Implement the general mathematical formulation."
                                    )
                                elif isinstance(rv, (ast.List, ast.Tuple, ast.Call)) and any(
                                    isinstance(sub, ast.Constant) and isinstance(sub.value, (int, float)) and sub.value not in _TRIVIAL_MATH_BASE_SCALARS
                                    for sub in ast.walk(rv)
                                ):
                                    errors.append(
                                        f"Line {node.lineno}: Hardcoded vector literal branch detected on '{ast.unparse(test)}'. Implement the general mathematical formulation."
                                    )
            elif isinstance(test, ast.Call) and any(
                fn_tok in ast.unparse(test.func) for fn_tok in ("array_equal", "allclose", "isclose")
            ):
                if any(isinstance(a, (ast.List, ast.Tuple)) and len(a.elts) >= 2 for a in test.args) and any(
                    isinstance(s, ast.Return) for s in node.body
                ):
                    errors.append(
                        f"Line {node.lineno}: Hardcoded array test-vector dispatch ('{ast.unparse(test)}') is prohibited."
                    )
    return errors[:5]


def validate_target_module_entrypoints(py_path: Path, cwd: Path) -> list[str]:
    """Verify that ``<target_module>.py`` defines every entrypoint declared in ``mapping.json``.

    Checks module top-level ``def`` / ``async def`` / ``class`` symbols against
    ``public_interface.entrypoints`` for symbol presence, variadic ``*args`` /
    ``**kwargs`` compatibility, and positional parameter arity.

    Args:
        py_path: Path to the Python module written by ``@Engineer``.
        cwd: Workspace root directory containing ``mapping.json``.

    Returns:
        List of entrypoint or signature mismatch errors (empty when compliant).
    """
    # Enforce `AST-4b`: verify `<target_module>.py` defines every entrypoint from `mapping.json` with matching `*args`, arity, and exact positional parameter names.
    declared_mod = _get_declared_target_module(cwd)
    if not declared_mod or py_path.name != declared_mod:
        return []
    map_p = cwd / "mapping.json"
    if not map_p.is_file() or not py_path.is_file():
        return []
    try:
        mp = json.loads(map_p.read_text(encoding="utf-8"))
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    except Exception:
        return []

    pub = mp.get("public_interface") if isinstance(mp.get("public_interface"), dict) else {}
    raw_eps = pub.get("entrypoints") if isinstance(pub.get("entrypoints"), list) else []
    top_defs: dict[str, ast.AST] = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    errs: list[str] = []
    for ep in raw_eps:
        ep_name = ep.get("name") if isinstance(ep, dict) else (ep if isinstance(ep, str) else "")
        ep_name = str(ep_name or "").strip()
        if not ep_name:
            continue
        if ep_name not in top_defs:
            errs.append(
                f"Missing Declared Entrypoint '{ep_name}' in '{py_path.name}': mapping.json public_interface.entrypoints "
                f"requires top-level definition '{ep_name}' (found: {sorted(top_defs.keys()) or 'none'})."
            )
            continue
        node = top_defs[ep_name]
        if isinstance(ep, dict) and isinstance(ep.get("parameters"), list) and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params_spec = [p for p in ep["parameters"] if isinstance(p, dict) and isinstance(p.get("name"), str)]
            wants_vararg = any(
                p["name"].strip().startswith("*") and not p["name"].strip().startswith("**") or p.get("kind") == "var_positional"
                for p in params_spec
            )
            if wants_vararg and node.args.vararg is None:
                errs.append(
                    f"Line {node.lineno}: Entrypoint '{ep_name}' must accept variadic '*args' per mapping.json."
                )
            elif not wants_vararg and node.args.vararg is None:
                spec_fixed = [
                    p["name"].strip()
                    for p in params_spec
                    if not p["name"].strip().startswith("*") and p.get("kind") not in ("var_positional", "var_keyword")
                ]
                actual_pos = [a.arg for a in (list(node.args.posonlyargs) + list(node.args.args)) if a.arg not in ("self", "cls")]
                min_required = len(actual_pos) - len(node.args.defaults)
                if len(spec_fixed) < min_required or (len(spec_fixed) > len(actual_pos) + len(node.args.kwonlyargs) and node.args.kwarg is None):
                    errs.append(
                        f"Line {node.lineno}: Entrypoint '{ep_name}' parameter arity ({actual_pos}) is incompatible "
                        f"with mapping.json parameters ({spec_fixed})."
                    )
                elif spec_fixed and actual_pos[: len(spec_fixed)] != spec_fixed:
                    errs.append(
                        f"Line {node.lineno}: Entrypoint '{ep_name}' positional parameter names ({actual_pos[:len(spec_fixed)]}) "
                        f"do not match mapping.json parameter names ({spec_fixed}). Preserve exact parameter names so keyword calls succeed."
                    )
    return errs

__all__ = [
    "_collect_nontrivial_anchor_literals",
    "_compute_manifest_core_hash",
    "_compute_mapping_implementation_contract_hash",
    "_compute_obligation_hashes",
    "apply_artifact_gate",
    "detect_anchor_test_leakage",
    "detect_deterministic_spec_or_code_risk",
    "extract_source_interface_contracts",
    "is_checkpoint_gate_required",
    "manifest_has_epistemic_assumptions",
    "run_systemone_spec_screen",
    "run_systemone_stage3_leakage_screen",
    "validate_dimensional_unit_consistency",
    "validate_manifest_structure",
    "validate_mapping_structure",
    "validate_obligation_coverage",
    "validate_target_module_entrypoints",
]
