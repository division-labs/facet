"""Cleanroom shell/permission resolution, `AUDIT_FEEDBACK.json` normalization, target-aware rewinds, and deliverable checks.

Function Map:
  - Section 1: Workspace-Safe Shell Rewriting & Nested Subagent Identity Resolution (`rewrite_bash_command_for_workspace`, `_resolve_active_worker_for_permission_request`)
  - Section 2: `AUDIT_FEEDBACK.json` Defect Classification & Target-Aware Rewind Transitions (`validate_audit_feedback_structure`, `apply_audit_rewind_transition`)
  - Section 3: `VERIFICATION.md` Sign-Off Validation, Repair Resolution Notes & Subagent Deliverable Gate (`validate_and_enrich_verification_report`, `verify_expected_subagent_artifact`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import re
import shlex

from .constants_and_context import (
    DEFAULT_FSM,
    SPEC_AND_REPORT_FILES,
    VALID_FAILURE_CLASSIFICATIONS,
    compute_file_sha256,
    get_copilot_home,
    get_session_id,
)
from .state_locks_and_seals import (
    call_systemone_decision_model,
    load_pipeline_config,
    load_pipeline_fsm,
    load_session_state,
    save_pipeline_fsm,
    save_session_state,
    update_stage_receipts,
)
from .text_ast_and_patch import (
    _find_implementation_files,
    _get_declared_target_module,
    _is_verification_target_name,
    _truncate_md_table_desc,
    compute_method_l3_localized_hunks,
    compute_module_ast_closure_hashes,
    sanitize_user_briefing_prose,
)
from .visual_ocr_and_sources import (
    _assemble_source_ocr_from_plates,
    _inspect_source_ocr_plate_coverage,
    _plate_markdown_path,
)
from .spec_and_mapping_gates import (
    _compute_manifest_core_hash,
    _compute_mapping_implementation_contract_hash,
    _compute_obligation_hashes,
    validate_manifest_structure,
    validate_mapping_structure,
)

def _resolve_active_worker_for_permission_request(payload: dict[str, object]) -> tuple[str | None, str]:
    r"""Resolve ``(active_agent, effective_session_id)`` across nested Depth-2 child subagents.

    ``copilot-runtime`` sends ``permissionRequest`` with ``sessionId = <root_session_id>`` and
    without ``agentId`` or ``agentType``. When ``@Facet`` delegates to ``@Orchestrator`` (Depth 1)
    and ``@Orchestrator`` delegates to a specialist (Depth 2), ``root_session_id['active_agent']``
    is ``"Orchestrator"`` while the active specialist lives on the child ``parent_session_id`` chain
    in ``hooks_state.json``.
    """
    explicit_role = (
        payload.get("agentType")
        or payload.get("agent_type")
        or payload.get("agentName")
        or payload.get("agent_name")
    )
    agent_id = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
    base_sid: str = str(agent_id or get_session_id(payload) or "")
    if explicit_role:
        return (str(explicit_role).strip(), base_sid)

    direct_role = load_session_state(base_sid).get("active_agent")
    if direct_role in ("Scribe", "Researcher", "Formalist", "Engineer", "Auditor"):
        return (str(direct_role), base_sid)

    s_file = get_copilot_home() / "session-state" / "hooks_state.json"
    if base_sid and s_file.exists():
        try:
            all_state = json.loads(s_file.read_text(encoding="utf-8"))
            if isinstance(all_state, dict):
                frontier: list[tuple[str, int]] = [(base_sid, 0)]
                visited: set[str] = {base_sid}
                best_worker: tuple[int, str, str] | None = None
                while frontier:
                    cur_id, depth = frontier.pop(0)
                    for cand_id, cand_val in all_state.items():
                        cid = str(cand_id)
                        if cid in visited or not isinstance(cand_val, dict):
                            continue
                        if str(cand_val.get("parent_session_id") or "").strip() != cur_id:
                            continue
                        c_role = str(cand_val.get("active_agent") or "").strip()
                        if not c_role:
                            continue
                        visited.add(cid)
                        nxt_depth = depth + 1
                        frontier.append((cid, nxt_depth))
                        if c_role in ("Scribe", "Researcher", "Formalist", "Engineer", "Auditor"):
                            if best_worker is None or nxt_depth >= best_worker[0]:
                                best_worker = (nxt_depth, c_role, cid)
                if best_worker is not None:
                    return (best_worker[1], best_worker[2])
        except Exception:
            pass
    return (str(direct_role) if direct_role else None, base_sid)


# ============================================================================
# Section 1: Workspace-Safe Shell Rewriting & Nested Subagent Identity Resolution
# Normalizes space-containing workspace paths in shell commands and resolves active Depth-2 leaf specialists.
# ============================================================================

def _resolve_user_skill_scripts_in_cmd(cmd, cwd):
    r"""Resolve user-scope skill script paths when ``cwd/.github/skills`` is absent.

    Args:
        cmd: Raw shell command string.
        cwd: Active workspace root directory.

    Returns:
        Command string with ``.github/skills/<subpath>.py`` rewritten to a
        shell-quoted ``$COPILOT_HOME/skills/<subpath>.py`` path when needed.
    """
    # When Facet is installed globally in `~/.copilot/` and `.github/skills/` is absent locally, rewrite helper script paths to `~/.copilot/skills/`.
    if ".github/skills/" not in cmd:
        return cmd
    copilot_skills = get_copilot_home() / "skills"

    # Helper function `_sub`: Return quoted user-scope skill path if workspace script is missing.
    def _sub(match):
        r"""Return quoted user-scope skill path if workspace script is missing."""
        rel = match.group(1)
        ws_candidate = cwd / ".github" / "skills" / rel
        if ws_candidate.exists():
            return match.group(0)
        user_candidate = copilot_skills / rel
        if user_candidate.exists():
            return shlex.quote(str(user_candidate))
        return match.group(0)

    return re.sub(r'(?<![\'"\w/])\.github/skills/([A-Za-z0-9._/\-]+\.py)', _sub, cmd)


def rewrite_bash_command_for_workspace(cmd, cwd):
    r"""Rewrite workspace paths and Python binaries for space-safe execution.

    1. Relativize unquoted ``<cwd>/<subpath>`` tokens when ``cwd`` contains
       spaces (preventing ``pytest``/``python`` path splitting).
    2. Fall back ``.github/skills/<subpath>`` to ``$COPILOT_HOME/skills/``.
    3. Rewrite leading ``python``/``pip``/``pytest`` commands to ``./.venv/bin/``.

    Args:
        cmd: Raw shell command string.
        cwd: Active workspace root directory.

    Returns:
        Rewritten shell command string.
    """
    updated = _resolve_user_skill_scripts_in_cmd(cmd, cwd)
    candidate_cwds = []
    for cand in [str(cwd.resolve()), os.path.abspath(str(cwd)), str(cwd).rstrip("/")]:
        if cand and " " in cand and cand not in candidate_cwds:
            candidate_cwds.append(cand)
    candidate_cwds.sort(key=len, reverse=True)
    for cwd_str in candidate_cwds:
        if (cwd_str + "/") in updated:
            pattern = re.compile(r'(?<![\'"=])' + re.escape(cwd_str + "/") + r'([^\s\'";|&<>]+)')
            updated = pattern.sub(lambda m: shlex.quote(m.group(1)), updated)
        if cwd_str in updated:
            bare_pat = re.compile(r'(?<![\'"=\S])' + re.escape(cwd_str) + r'(?=$|\s|[;|&])')
            updated = bare_pat.sub(".", updated)

    venv_py = cwd / ".venv" / "bin" / "python"
    venv_pip = cwd / ".venv" / "bin" / "pip"
    venv_pytest = cwd / ".venv" / "bin" / "pytest"
    if venv_py.exists():
        if updated.startswith("python3 "):
            updated = f"./.venv/bin/python {updated[8:]}"
        elif updated.startswith("python "):
            updated = f"./.venv/bin/python {updated[7:]}"
        elif updated.startswith("pip3 ") and venv_pip.exists():
            updated = f"./.venv/bin/pip {updated[5:]}"
        elif updated.startswith("pip ") and venv_pip.exists():
            updated = f"./.venv/bin/pip {updated[4:]}"
        elif updated.startswith("pytest") and venv_pytest.exists():
            updated = f"./.venv/bin/pytest{updated[6:]}"

    # Inject read-only PYTHONPATH for seal-verified upstream_modules declared in mapping.json.
    map_f = Path(cwd) / "mapping.json"
    if map_f.is_file() and (
        updated.startswith("./.venv/bin/python")
        or updated.startswith("./.venv/bin/pytest")
        or updated.startswith("python")
        or updated.startswith("pytest")
    ):
        try:
            mp_j = json.loads(map_f.read_text(encoding="utf-8"))
            raw_up = mp_j.get("upstream_modules")
            if isinstance(raw_up, list) and raw_up:
                up_paths: list[str] = []
                for item in raw_up:
                    if isinstance(item, dict) and isinstance(item.get("spec_slug"), str):
                        slug_u = item["spec_slug"].strip()
                        if slug_u and ".." not in slug_u and "/" not in slug_u:
                            cand_d = (Path(cwd) / "specs" / slug_u).resolve()
                            if (cand_d / "PROVENANCE.json").is_file():
                                up_paths.append(str(cand_d))
                if up_paths and "PYTHONPATH=" not in updated:
                    joined_pp = ":".join(up_paths)
                    updated = f"PYTHONPATH={shlex.quote(joined_pp)} {updated}"
        except Exception:
            pass
    return updated


# ============================================================================
# Section 2: `AUDIT_FEEDBACK.json` Defect Classification & Target-Aware Rewind Transitions
# Normalizes Stage 4 failure classifications and routes artifact-preserving rewinds across Stages 1–4.
# ============================================================================

def _extract_canonical_failure_classification(obj: dict[str, object]) -> tuple[str | None, str | None]:
    """Extract a canonical ``VALID_FAILURE_CLASSIFICATIONS`` label from a top-level or item dict.

    Inspects explicit classification keys first, then secondary category/type keys
    (ignoring descriptive sub-labels such as ``"NUMERICAL_INSTABILITY"`` unless
    they match ``VALID_FAILURE_CLASSIFICATIONS``), and finally ``rewind_target``
    or ``location`` / ``target_artifact`` filenames.

    Args:
        obj: Dictionary (either top-level ``AUDIT_FEEDBACK.json`` or ``failures[i]``).

    Returns:
        Tuple ``(canonical_classification, invalid_explicit_value)``.
    """
    if not isinstance(obj, dict):
        return (None, None)
    invalid_explicit: str | None = None
    for primary_k in (
        "failure_classification",
        "defect_classification",
        "defect_type",
        "defect_category",
        "classification",
    ):
        raw_v = obj.get(primary_k)
        if raw_v is not None and str(raw_v).strip():
            norm_v = str(raw_v).strip().upper()
            if norm_v in VALID_FAILURE_CLASSIFICATIONS:
                return (norm_v, None)
            if invalid_explicit is None:
                invalid_explicit = str(raw_v).strip()

    for secondary_k in ("category", "failure_type", "type"):
        raw_s = obj.get(secondary_k)
        if raw_s is not None and str(raw_s).strip():
            norm_s = str(raw_s).strip().upper()
            if norm_s in VALID_FAILURE_CLASSIFICATIONS:
                return (norm_s, None)

    rw_t = str(obj.get("rewind_target") or "").strip().upper()
    if rw_t == "STAGE_1_SPECIFICATION":
        return ("MANIFEST_DEFECT", None)
    if rw_t == "STAGE_2_MAPPING":
        return ("MAPPING_DEFECT", None)
    if rw_t == "STAGE_3_IMPLEMENTATION":
        return ("IMPLEMENTATION_DEFECT", None)
    if rw_t == "STAGE_4_VERIFICATION":
        return ("TEST_HARNESS_DEFECT", None)

    for loc_k in ("location", "target_artifact", "artifact", "file"):
        raw_loc = str(obj.get(loc_k) or "").strip()
        if raw_loc:
            loc_name = Path(raw_loc).name.lower()
            if loc_name == "manifest.json":
                return ("MANIFEST_DEFECT", None)
            if loc_name == "mapping.json":
                return ("MAPPING_DEFECT", None)
            if loc_name.startswith(("verify_", "test_")) and loc_name.endswith(".py"):
                return ("TEST_HARNESS_DEFECT", None)

    return (None, invalid_explicit)


def validate_audit_feedback_structure(data):
    """Validate ``AUDIT_FEEDBACK.json`` status, top-level/item aliases, and failure classifications.

    Normalizes ``defects`` to ``failures``, resolves both top-level and item-level
    classification aliases (``failure_classification``, ``defect_classification``,
    ``defect_type``, ``defect_category``, ``classification``, ``category``,
    ``location``) to canonical ``failure_classification``, and fails closed when
    no valid classification from ``VALID_FAILURE_CLASSIFICATIONS`` is provided
    (never silently overwriting unlabelled failures with ``IMPLEMENTATION_DEFECT``).

    Args:
        data: Parsed JSON object from ``AUDIT_FEEDBACK.json``.

    Returns:
        Tuple ``(normalized_status, errors_list)`` where ``normalized_status``
        is ``"FAIL"``, ``"PASS"``, or ``""``.
    """
    if not isinstance(data, dict):
        return ("", ["AUDIT_FEEDBACK.json must be a JSON object."])
    if "failures" not in data and isinstance(data.get("defects"), list):
        data["failures"] = data.pop("defects")
    raw_status = str(data.get("overall_status") or data.get("status") or "").strip().upper()
    if raw_status in ("FAILED", "FAIL"):
        failures = data.get("failures")
        if not isinstance(failures, list) or len(failures) == 0:
            return (
                "FAIL",
                ["AUDIT_FEEDBACK.json with FAILED/FAIL status must provide a non-empty 'failures' array of counterexamples."],
            )
        top_cls, top_invalid = _extract_canonical_failure_classification(data)
        top_obl = str(data.get("obligation_id") or data.get("id") or "").strip()
        top_desc = str(data.get("diagnosis") or data.get("description") or data.get("summary") or "").strip()
        errs = []
        resolved_all: list[str] = []
        for idx, item in enumerate(failures):
            if not isinstance(item, dict):
                errs.append(f"failures[{idx}] must be an object.")
                continue
            item_cls, item_invalid = _extract_canonical_failure_classification(item)
            resolved_cls = item_cls or top_cls
            if resolved_cls in VALID_FAILURE_CLASSIFICATIONS:
                item["failure_classification"] = resolved_cls
                resolved_all.append(resolved_cls)
            elif item_invalid is not None or top_invalid is not None:
                bad_cls = item_invalid if item_invalid is not None else top_invalid
                errs.append(
                    f"failures[{idx}] has invalid classification {bad_cls!r}; "
                    f"must be one of {sorted(VALID_FAILURE_CLASSIFICATIONS)}."
                )
            else:
                errs.append(
                    f"failures[{idx}] must explicitly specify 'failure_classification' "
                    f"in {sorted(VALID_FAILURE_CLASSIFICATIONS)} (at the item or top level)."
                )
            if not item.get("obligation_id") and not item.get("id") and top_obl:
                item["obligation_id"] = top_obl
            if not any(item.get(k) for k in ("diagnosis", "description", "summary")) and top_desc:
                item["diagnosis"] = top_desc
            if not any(item.get(k) for k in ("obligation_id", "id", "diagnosis", "description", "summary")):
                errs.append(f"failures[{idx}] must specify 'obligation_id' (or 'id') and 'diagnosis'.")
        if top_cls in VALID_FAILURE_CLASSIFICATIONS:
            data["failure_classification"] = top_cls
        elif len(set(resolved_all)) == 1:
            data["failure_classification"] = resolved_all[0]
        return ("FAIL", errs)
    if raw_status in ("SUCCESS", "PASS", "CONDITIONAL_PASS"):
        return ("PASS", [])
    return (
        "",
        [
            f"AUDIT_FEEDBACK.json must declare 'overall_status' or 'status' in "
            f"['FAILED', 'FAIL', 'CONDITIONAL_PASS', 'SUCCESS', 'PASS'] (got {raw_status!r})."
        ],
    )


def apply_audit_rewind_transition(
    cwd: Path, fsm: dict[str, object], fb_data: dict[str, object]
) -> tuple[str, list[str], str]:
    """Determine target-aware rewind stage, record ``repair_history``, and unseal checkpoints without deleting files.

    Preserves ``AUDIT_FEEDBACK.json`` and all workspace artifacts (``manifest.json``,
    ``mapping.json``, ``requirements.txt``, ``*.py``, ``verify_*.py``) on disk so
    specialists apply minimal RRSI scoped edits rather than regenerating files:
    - ``MANIFEST_DEFECT`` -> ``STAGE_1_SPECIFICATION`` (``@Researcher``), unseals C1 & C2.
    - ``MAPPING_DEFECT`` / ``SPECIFICATION_AMBIGUITY`` -> ``STAGE_2_MAPPING`` (``@Formalist``), unseals C2.
    - ``IMPLEMENTATION_DEFECT`` / ``DOMAIN_BOUND_LIMITATION`` -> ``STAGE_3_IMPLEMENTATION`` (``@Engineer``).

    Phases:
      - Phase 1: Failure Classification & Deterministic Rewind Target Routing
      - Phase 2: System-1 Defect-Layer Override & Method L3 Hunk Localization
      - Phase 3: Repair History Recording, Checkpoint Unsealing & Stage Receipt Reset

    Args:
        cwd: Workspace root directory.
        fsm: Mutable pipeline FSM dictionary updated in place with ``current_stage``,
            ``pending_rewind``, unsealed downstream ``checkpoints``, and ``repair_history``.
        fb_data: Parsed ``AUDIT_FEEDBACK.json`` payload authored by ``@Auditor``.

    Returns:
        Tuple ``(rewind_stage, deleted_filenames, target_specialist_role)`` where
        ``deleted_filenames`` is ``[]`` under artifact-preserving scoped repair.
    """
    # --- Phase 1: Failure Classification & Deterministic Rewind Target Routing ---
    _ = cwd
    explicit_target = str(fb_data.get("rewind_target") or "").strip()
    top_cls_rw, _ = _extract_canonical_failure_classification(fb_data)
    top_diag_rw = str(
        fb_data.get("diagnosis")
        or fb_data.get("description")
        or fb_data.get("summary")
        or fb_data.get("failure_reason")
        or fb_data.get("root_cause")
        or fb_data.get("issue")
        or fb_data.get("message")
        or ""
    ).strip()
    top_loc_rw = str(fb_data.get("location") or fb_data.get("target_artifact") or "").strip()
    top_obl_rw = str(fb_data.get("obligation_id") or fb_data.get("id") or "").strip()
    raw_fails = fb_data.get("failures") if isinstance(fb_data.get("failures"), list) else []
    classes: list[str] = []
    obl_ids: list[str] = []
    diag_bits: list[str] = []
    if top_diag_rw:
        loc_prefix = f"[{top_loc_rw}] " if top_loc_rw else ""
        diag_bits.append(f"{loc_prefix}{top_diag_rw}"[:260])
    if top_obl_rw:
        obl_ids.append(top_obl_rw)
    counterexamples: list[str] = []
    for f_obj in raw_fails:
        if not isinstance(f_obj, dict):
            continue
        item_cls_rw, _ = _extract_canonical_failure_classification(f_obj)
        fc = item_cls_rw or top_cls_rw or "IMPLEMENTATION_DEFECT"
        if fc in VALID_FAILURE_CLASSIFICATIONS:
            f_obj["failure_classification"] = fc
        else:
            fc = "IMPLEMENTATION_DEFECT"
        classes.append(fc)
        oid = str(f_obj.get("obligation_id") or f_obj.get("id") or "").strip()
        if oid and oid not in obl_ids:
            obl_ids.append(oid)
        dg = str(
            f_obj.get("diagnosis")
            or f_obj.get("description")
            or f_obj.get("summary")
            or f_obj.get("failure_reason")
            or f_obj.get("root_cause")
            or f_obj.get("issue")
            or f_obj.get("message")
            or ""
        ).strip()
        if dg and dg[:220] not in diag_bits and dg not in top_diag_rw:
            diag_bits.append(dg[:220])
        ce = str(
            f_obj.get("minimal_counterexample")
            or f_obj.get("counterexample")
            or f_obj.get("input_value")
            or f_obj.get("actual_observed")
            or f_obj.get("actual")
            or ""
        ).strip()
        if ce:
            counterexamples.append(ce[:160])

    if top_cls_rw in VALID_FAILURE_CLASSIFICATIONS and top_cls_rw not in classes:
        classes.append(top_cls_rw)

    if explicit_target in ("STAGE_1_SPECIFICATION", "STAGE_2_MAPPING", "STAGE_3_IMPLEMENTATION", "STAGE_4_VERIFICATION"):
        rewind_target = explicit_target
    elif "MANIFEST_DEFECT" in classes:
        rewind_target = "STAGE_1_SPECIFICATION"
    elif "MAPPING_DEFECT" in classes or "SPECIFICATION_AMBIGUITY" in classes:
        rewind_target = "STAGE_2_MAPPING"
    elif "IMPLEMENTATION_DEFECT" in classes or "DOMAIN_BOUND_LIMITATION" in classes:
        rewind_target = "STAGE_3_IMPLEMENTATION"
    elif classes and all(c == "TEST_HARNESS_DEFECT" for c in classes):
        rewind_target = "STAGE_4_VERIFICATION"
    else:
        rewind_target = "STAGE_3_IMPLEMENTATION"

    # --- Phase 2: System-1 Defect-Layer Override & Method L3 Hunk Localization ---
    localized_hunks: list[dict[str, object]] = []
    # When @Auditor defaulted to IMPLEMENTATION_DEFECT (and no explicit_target override was set),
    # run one batched System-1 boundary query to catch mislabelled upstream spec/mapping diagnoses + localize edit hunks!
    diag_summary = "; ".join(diag_bits)[:1200]
    if raw_fails and rewind_target != "STAGE_4_VERIFICATION":
        cand_file_for_l3 = (
            "manifest.json"
            if rewind_target == "STAGE_1_SPECIFICATION"
            else ("mapping.json" if rewind_target == "STAGE_2_MAPPING" else (_get_declared_target_module(Path(cwd)) or ""))
        )
        cand_path_l3 = (Path(cwd) / cand_file_for_l3) if cand_file_for_l3 else None
        cand_src_l3 = (
            cand_path_l3.read_text(encoding="utf-8", errors="replace")
            if (cand_path_l3 and cand_path_l3.is_file())
            else ""
        )
        l3_lines = cand_src_l3.splitlines()[:250]
        numbered_l3 = "\n".join(f"L{i:03d}: {ln}" for i, ln in enumerate(l3_lines, 1))
        line_ids_l3 = [f"L{i:03d}" for i in range(1, len(l3_lines) + 1)] or ["L001"]
        s1_questions: dict[str, object] = {}
        if not explicit_target and rewind_target == "STAGE_3_IMPLEMENTATION" and diag_summary:
            s1_questions["true_defect_layer"] = {
                "type": "choice",
                "instructions": (
                    "Based on `diagnosis` and `counterexamples`, which pipeline artifact actually contains the root defect?"
                ),
                "criteria": {
                    "IMPLEMENTATION_DEFECT": "Production Python code fails to implement valid specifications.",
                    "MAPPING_DEFECT": "`mapping.json` has a wrong anchor value, signature/dtype mismatch, contradictory obligation, or numerically ill-conditioned formal_predicate (e.g. raw determinant polynomial roundoff).",
                    "MANIFEST_DEFECT": "`manifest.json` has a wrong governing equation, unrequested domain narrowing, or unrealistic tolerance.",
                },
            }
        if l3_lines:
            s1_questions["in_file"] = {
                "type": "noul",
                "instructions": "Does `target_file_numbered` contain lines that must be edited to resolve `diagnosis`?",
                "criteria": {
                    "true": "Yes — `target_file_numbered` must be edited.",
                    "false": "No — `target_file_numbered` is clean; defect is upstream.",
                },
            }
            s1_questions["where_line"] = {
                "type": "choice",
                "instructions": "Which line ID in `target_file_numbered` is the primary locus to edit?",
                "criteria": {lid: None for lid in line_ids_l3},
            }
        if s1_questions:
            # Query `pplx-decider-flash` (`keep_alive=0`) via `/v1/systemone` for fast boundary triage.
            s1_ans = call_systemone_decision_model(
                Path(cwd),
                {
                    "diagnosis": diag_summary,
                    "counterexamples": counterexamples[:4],
                    "target_file": cand_file_for_l3,
                    "target_file_numbered": numbered_l3,
                },
                s1_questions,
            )
            if isinstance(s1_ans, dict):
                # Load `.pipeline_config.json` (falling back to `DEFAULT_CONFIG` when absent).
                cfg_rw = load_pipeline_config(Path(cwd))
                dm_rw = cfg_rw.get("decision_model") if isinstance(cfg_rw.get("decision_model"), dict) else {}
                tau_rw = float(dm_rw.get("confidence_threshold", 0.85) or 0.85)
                td_obj = s1_ans.get("true_defect_layer") if isinstance(s1_ans.get("true_defect_layer"), dict) else {}
                td_choice = str(td_obj.get("choice") or "")
                td_conf = float(td_obj.get("confidence") or 0.0)
                overrode_layer = False
                if td_conf >= tau_rw and td_choice in ("MANIFEST_DEFECT", "MAPPING_DEFECT"):
                    rewind_target = "STAGE_1_SPECIFICATION" if td_choice == "MANIFEST_DEFECT" else "STAGE_2_MAPPING"
                    classes = [td_choice]
                    overrode_layer = True
                if overrode_layer:
                    new_target_file = "manifest.json" if rewind_target == "STAGE_1_SPECIFICATION" else "mapping.json"
                    new_p = Path(cwd) / new_target_file
                    if new_p.is_file():
                        new_src = new_p.read_text(encoding="utf-8", errors="replace")
                        new_lns = new_src.splitlines()[:250]
                        if new_lns:
                            new_lids = [f"L{i:03d}" for i in range(1, len(new_lns) + 1)]
                            # Query `pplx-decider-flash` (`keep_alive=0`) via `/v1/systemone` for fast boundary triage.
                            s1_loc2 = call_systemone_decision_model(
                                Path(cwd),
                                {
                                    "diagnosis": diag_summary,
                                    "target_file": new_target_file,
                                    "target_file_numbered": "\n".join(f"L{i:03d}: {ln}" for i, ln in enumerate(new_lns, 1)),
                                },
                                {
                                    "in_file": {"type": "noul", "instructions": "Does `target_file_numbered` require editing?"},
                                    "where_line": {
                                        "type": "choice",
                                        "instructions": "Which line ID in `target_file_numbered` is the defect locus?",
                                        "criteria": {lid: None for lid in new_lids},
                                    },
                                },
                            )
                            if isinstance(s1_loc2, dict):
                                inf2 = float((s1_loc2.get("in_file") or {}).get("noul") or 0.0)  # type: ignore[union-attr]
                                wl2 = s1_loc2.get("where_line") if isinstance(s1_loc2.get("where_line"), dict) else {}
                                probs2 = wl2.get("probabilities") if isinstance(wl2.get("probabilities"), dict) else {}
                                if not probs2 and wl2.get("choice"):
                                    probs2 = {str(wl2["choice"]): 1.0}
                                localized_hunks = compute_method_l3_localized_hunks(new_src, new_target_file, probs2, in_file_prob=inf2)  # type: ignore[arg-type]
                elif cand_src_l3:
                    inf1 = float((s1_ans.get("in_file") or {}).get("noul") or 0.0)  # type: ignore[union-attr]
                    wl1 = s1_ans.get("where_line") if isinstance(s1_ans.get("where_line"), dict) else {}
                    probs1 = wl1.get("probabilities") if isinstance(wl1.get("probabilities"), dict) else {}
                    if not probs1 and wl1.get("choice"):
                        probs1 = {str(wl1["choice"]): 1.0}
                    localized_hunks = compute_method_l3_localized_hunks(cand_src_l3, cand_file_for_l3, probs1, in_file_prob=inf1)  # type: ignore[arg-type]

    # --- Phase 3: Repair History Recording, Checkpoint Unsealing & Stage Receipt Reset ---
    role_map = {
        "STAGE_1_SPECIFICATION": "Researcher",
        "STAGE_2_MAPPING": "Formalist",
        "STAGE_3_IMPLEMENTATION": "Engineer",
        "STAGE_4_VERIFICATION": "Auditor",
    }
    specialist_role = role_map[rewind_target]

    hist_list = fsm.get("repair_history")
    if not isinstance(hist_list, list):
        hist_list = []
    hist_list.append(
        {
            "attempt": int(fsm.get("repair_attempts", 1) or 1),
            "rewind_target": rewind_target,
            "specialist": specialist_role,
            "classifications": classes or ["IMPLEMENTATION_DEFECT"],
            "obligation_ids": obl_ids,
            "counterexamples": counterexamples,
            "diagnosis": "; ".join(diag_bits)[:360],
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    )
    fsm["repair_history"] = hist_list

    cps = fsm.get("checkpoints")
    if not isinstance(cps, dict):
        cps = json.loads(json.dumps(DEFAULT_FSM["checkpoints"]))
        fsm["checkpoints"] = cps

    if rewind_target == "STAGE_1_SPECIFICATION":
        cps["checkpoint_1"] = {
            "name": "Specification Manifest",
            "artifact": "manifest.json",
            "approved": False,
            "approved_sha256": None,
        }
        cps["checkpoint_2"] = {
            "name": "Formal Mapping",
            "artifact": "mapping.json",
            "approved": False,
            "approved_sha256": None,
        }
    elif rewind_target == "STAGE_2_MAPPING":
        cps["checkpoint_2"] = {
            "name": "Formal Mapping",
            "artifact": "mapping.json",
            "approved": False,
            "approved_sha256": None,
        }

    fsm["current_stage"] = rewind_target
    decl_m_rw = _get_declared_target_module(Path(cwd))
    fsm["pending_rewind"] = {
        "target_stage": rewind_target,
        "specialist": specialist_role,
        "attempt": int(fsm.get("repair_attempts", 1) or 1),
        "localized_hunks": localized_hunks,
        "prev_manifest_core_hash": _compute_manifest_core_hash(Path(cwd) / "manifest.json"),
        "prev_mapping_interface_hash": _compute_mapping_implementation_contract_hash(Path(cwd) / "mapping.json"),
        "prev_obligation_hashes": _compute_obligation_hashes(Path(cwd) / "mapping.json"),
        "prev_module_closure_hashes": compute_module_ast_closure_hashes(Path(cwd) / decl_m_rw) if decl_m_rw else {},
    }
    def _reset_s4_receipts(rec: dict[str, object]) -> None:
        rec["stage4_pytest_passed"] = False
        rec["stage4_pytest_failed"] = False

    update_stage_receipts(Path(cwd), _reset_s4_receipts)
    return (rewind_target, [], specialist_role)


# ============================================================================
# Section 3: `VERIFICATION.md` Sign-Off Validation, Repair Resolution Notes & Subagent Deliverable Gate
# Verifies zero-failure obligation tables in `VERIFICATION.md` and checks designated deliverable persistence on stop.
# ============================================================================

def validate_and_enrich_verification_report(
    v_text: str, cwd: Path, fsm: dict[str, object]
) -> tuple[list[str], str]:
    r"""Validate zero-failure ``VERIFICATION.md`` invariants and append repair/rewind history.

    Blocks ``CONDITIONAL_PASS``, ``FAIL``/``FAILED`` overall verdicts, and any
    individual ``OBL-j: FAIL/FAILED`` entries so unresolved defects must be
    routed through ``AUDIT_FEEDBACK.json``. When prior code repairs or upstream
    spec rewinds occurred, ensures a ``## Specification & Repair History``
    section is present in the persisted report.

    Args:
        v_text: Sanitized Markdown text of ``VERIFICATION.md``.
        cwd: Workspace root directory inspected for ``AUDIT_FEEDBACK.json``.
        fsm: Active pipeline FSM dictionary containing ``repair_history``.

    Returns:
        Tuple ``(validation_errors, enriched_markdown_text)``.
    """
    # Reject `VERIFICATION.md` if any obligation is `FAILED` or `CONDITIONAL_PASS`, and append `## Specification & Repair History` when prior repairs occurred.
    errors: list[str] = []
    if re.search(
        r"^\s*(?:#+\s*)?(?:\*\*)?(?:Overall\s+)?(?:Status|Verdict)(?:\*\*)?\s*:\s*`?(?:CONDITIONAL_PASS|FAIL|FAILED)\b",
        v_text,
        re.IGNORECASE | re.MULTILINE,
    ):
        errors.append(
            "VERIFICATION.md cannot certify completion with 'CONDITIONAL_PASS' or 'FAILED' status. "
            "Write 'AUDIT_FEEDBACK.json' via 'create' classifying the defect ('MAPPING_DEFECT', 'MANIFEST_DEFECT', or 'IMPLEMENTATION_DEFECT') "
            "so the pipeline rewinds to the responsible specialist to fix it."
        )
    failed_obl_matches = re.findall(
        r"(?:\|\s*(OBL-[A-Za-z0-9_-]+)\s*\|[^\r\n]*\|\s*`?(?:FAIL|FAILED)`?\s*\||-\s*\*?\*?(OBL-[A-Za-z0-9_-]+)\*?\*?\s*:\s*`?(?:FAIL|FAILED)\b)",
        v_text,
        re.IGNORECASE,
    )
    failed_obls = sorted({a or b for a, b in failed_obl_matches if (a or b)})
    if failed_obls:
        errors.append(
            f"VERIFICATION.md contains failed obligation(s) ({', '.join(failed_obls)}). "
            "All verification obligations must PASS before authoring VERIFICATION.md; if an anchor or formula in mapping.json / manifest.json is wrong, "
            "emit 'AUDIT_FEEDBACK.json' with 'failure_classification': 'MAPPING_DEFECT' or 'MANIFEST_DEFECT' instead."
        )
    if errors:
        return errors, v_text

    hist_entries = fsm.get("repair_history") if isinstance(fsm.get("repair_history"), list) else []
    fb_path = cwd / "AUDIT_FEEDBACK.json"
    summary_bullets: list[str] = []
    for entry in hist_entries:
        if isinstance(entry, dict):
            att = entry.get("attempt", 1)
            cls_s = ", ".join(entry.get("classifications") or ["IMPLEMENTATION_DEFECT"])
            stg = entry.get("rewind_target", "STAGE_3_IMPLEMENTATION")
            oids = ", ".join(entry.get("obligation_ids") or []) or "contract"
            diag = str(entry.get("diagnosis") or "").strip()
            summary_bullets.append(
                f"- **Attempt {att} (`{cls_s}` -> `{stg}`, `{oids}`):** {diag or 'Resolved prior verification discrepancy.'}"
            )
    if not summary_bullets and fb_path.is_file():
        try:
            fb_j = json.loads(fb_path.read_text(encoding="utf-8"))
            for f0 in (fb_j.get("failures") or [])[:3]:
                if isinstance(f0, dict):
                    fc = str(f0.get("failure_classification") or "IMPLEMENTATION_DEFECT")
                    oid = str(f0.get("obligation_id") or f0.get("id") or "OBL")
                    dg = str(f0.get("diagnosis") or f0.get("description") or "").strip()
                    summary_bullets.append(f"- **Resolved `{fc}` (`{oid}`):** {dg[:220]}")
        except Exception:
            pass

    enriched = v_text
    if summary_bullets and "Specification & Repair History" not in v_text and not any(
        b_tok in v_text for b_tok in ("MAPPING_DEFECT", "MANIFEST_DEFECT", "AUDIT_FEEDBACK", "Repair History")
    ):
        chk_m = re.search(r"\n\[CHECKPOINT:\s*S4\s*->\s*C3\]\s*$", enriched)
        hist_sec = "\n\n## Specification & Repair History\n\n" + "\n".join(summary_bullets)
        if chk_m:
            enriched = enriched[: chk_m.start()].rstrip() + hist_sec + "\n\n[CHECKPOINT: S4 -> C3]"
        else:
            enriched = enriched.rstrip() + hist_sec
    return [], enriched


def _set_unresolved_validation_error(payload: dict[str, object], session_id: str, err_summary: str) -> None:
    r"""Record an unresolved local validation error on both session and child-agent hook state."""
    for sid_k in filter(None, {session_id, str(payload.get("agentId") or payload.get("agent_id") or "").strip()}):
        st = load_session_state(sid_k)
        st["unresolved_validation_error"] = err_summary
        save_session_state(sid_k, st)


def _clear_unresolved_validation_error(payload: dict[str, object], session_id: str) -> None:
    r"""Clear any latched ``unresolved_validation_error`` once an artifact passes ``postToolUse`` validation."""
    for sid_k in filter(None, {session_id, str(payload.get("agentId") or payload.get("agent_id") or "").strip()}):
        st = load_session_state(sid_k)
        if st.pop("unresolved_validation_error", None) is not None:
            save_session_state(sid_k, st)


def extract_repair_resolution_note(cwd: Path, fsm: dict[str, object], spec_dir: Path | None = None) -> str:
    """Extract a candid 1-sentence summary of resolved Stage 4 defects when ``repair_attempts > 0``.

    Args:
        cwd: Workspace root directory.
        fsm: Active or pre-archival pipeline FSM dictionary.
        spec_dir: Optional archived ``specs/<slug>/`` directory containing
            ``AUDIT_HISTORY.json``.

    Returns:
        Concise summary of the resolved obligation defect, or ``""`` when zero
        repair attempts occurred.
    """
    # Summarize any resolved Stage 4 edge case from `AUDIT_HISTORY.json` / `AUDIT_FEEDBACK.json` in one candid sentence.
    rep_n = int(fsm.get("repair_attempts", 0) or 0)
    if rep_n <= 0:
        return ""
    slug = str(fsm.get("concept_slug") or "").strip()
    candidates = [cwd / "AUDIT_FEEDBACK.json"]
    # Scan directory contents when `spec_dir and spec_dir.is_dir()`.
    if spec_dir and spec_dir.is_dir():
        candidates.insert(0, spec_dir / "AUDIT_HISTORY.json")
    elif slug:
        candidates.append(cwd / "specs" / slug / "AUDIT_HISTORY.json")
    for fb_p in candidates:
        if not fb_p.is_file():
            continue
        try:
            fb_data = json.loads(fb_p.read_text(encoding="utf-8"))
            raw_fails = fb_data.get("failures")
            fails = raw_fails if isinstance(raw_fails, list) else []
            if fails and isinstance(fails[0], dict):
                f0 = fails[0]
                oid = str(f0.get("obligation_id") or f0.get("id") or fb_data.get("obligation_id") or "").strip()
                oid_disp = "precondition contract" if oid.upper() in {"", "N/A", "NA", "NONE", "NULL"} else f"`{oid}`"
                raw_cls, _ = _extract_canonical_failure_classification(f0)
                if not raw_cls:
                    raw_cls, _ = _extract_canonical_failure_classification(fb_data)
                cls_human_map = {
                    "MANIFEST_DEFECT": "specification contract",
                    "MAPPING_DEFECT": "interface & proof mapping",
                    "IMPLEMENTATION_DEFECT": "module implementation",
                    "TEST_HARNESS_DEFECT": "verification harness",
                }
                cat = cls_human_map.get(
                    str(raw_cls or ""),
                    str(f0.get("defect_category") or f0.get("category") or "boundary check").replace("_", " ").lower().strip(),
                )
                summ = sanitize_user_briefing_prose(
                    str(
                        f0.get("diagnosis")
                        or f0.get("description")
                        or f0.get("summary")
                        or f0.get("root_cause")
                        or f0.get("failure_reason")
                        or f0.get("message")
                        or fb_data.get("diagnosis")
                        or fb_data.get("description")
                        or fb_data.get("summary")
                        or ""
                    ).strip()
                )
                if not summ or summ.lower().rstrip(".") == cat.lower().rstrip("."):
                    return f"Resolved {oid_disp} ({cat})"
                m_first_sent = re.match(r"^(.{20,180}?[.!?])(?:\s+[A-Z]|$)", summ)
                concise_summ = m_first_sent.group(1).rstrip(".") if m_first_sent else _truncate_md_table_desc(summ, max_len=140)
                return f"Resolved {oid_disp} ({cat}): {concise_summ}"
        except Exception:
            pass
    return ""


def verify_expected_subagent_artifact(agent_name, current_stage, status_val, cleaned_response, cwd):
    r"""Verify on disk that a subagent persisted its designated deliverable.

    Args:
        agent_name: Specialist role name (``Scribe``, ``Researcher``,
            ``Formalist``, ``Engineer``, or ``Auditor``).
        current_stage: Active FSM stage constant.
        status_val: Uppercase status extracted from the handshake receipt.
        cleaned_response: Sanitized subagent response text.
        cwd: Workspace root directory.

    Returns:
        Tuple ``(expected_artifact_name, error_message)`` where both elements
        may be ``None`` if no error occurred or no file is required.
    """
    # Confirm on disk that the finishing specialist actually wrote a non-empty, schema-valid deliverable before allowing `subagentStop` to advance.
    if status_val in ["FAILED", "FAIL"]:
        if agent_name == "Auditor":
            fb_path = cwd / "AUDIT_FEEDBACK.json"
            if not fb_path.exists():
                return (
                    "AUDIT_FEEDBACK.json",
                    f"'AUDIT_FEEDBACK.json' was not written to '{fb_path}'. When reporting FAILED, @Auditor must persist counter-examples in AUDIT_FEEDBACK.json."
                )
        return (None, None)

    if agent_name == "Scribe":
        ocr_path = cwd / "SOURCE_OCR.md"
        req_stop_p, comp_stop_p, miss_stop_p = _inspect_source_ocr_plate_coverage(cwd)
        if miss_stop_p:
            nxt_stop_p = miss_stop_p[0]
            nxt_stop_num = req_stop_p.index(nxt_stop_p) + 1
            miss_names = ", ".join(p.name for p in miss_stop_p[:6])
            if len(req_stop_p) >= 2:
                nxt_stop_md = _plate_markdown_path(cwd, nxt_stop_p)
                return (
                    "SOURCE_OCR.md",
                    f"Incomplete multi-plate Stage 0V OCR: {len(comp_stop_p)}/{len(req_stop_p)} staged plates transcribed "
                    f"(missing isolated plate files for: {miss_names}). Call ONLY 'view(path=\"{nxt_stop_p}\")' for Plate {nxt_stop_num} of {len(req_stop_p)} "
                    f"(1 plate at a time) and write '{nxt_stop_md}' via 'create'.",
                )
            return ("SOURCE_OCR.md", f"Expected OCR transcript 'SOURCE_OCR.md' does not exist at '{ocr_path}'.")
        if req_stop_p and (not ocr_path.is_file() or ocr_path.stat().st_size == 0):
            _assemble_source_ocr_from_plates(cwd, req_stop_p)
            fsm_s = load_pipeline_fsm(cwd)
            fsm_s["ocr_sha256"] = compute_file_sha256(ocr_path)
            if (cwd / "SOURCE_TEXT.md").is_file():
                fsm_s["text_sha256"] = compute_file_sha256(cwd / "SOURCE_TEXT.md")
            save_pipeline_fsm(cwd, fsm_s)
        if not ocr_path.is_file() or ocr_path.stat().st_size == 0:
            return ("SOURCE_OCR.md", f"Expected OCR transcript 'SOURCE_OCR.md' does not exist at '{ocr_path}'.")
        return ("SOURCE_OCR.md", None)

    if agent_name == "Researcher":
        manifest_path = cwd / "manifest.json"
        research_path = cwd / "RESEARCH.md"
        mode_match = re.search(r"-\s*\*\*Mode:\*\*\s*([A-Za-z_]+)", cleaned_response, re.IGNORECASE)
        mode_val = mode_match.group(1).upper() if mode_match else ""
        art_match = re.search(r"-\s*\*\*Artifact Written:\*\*\s*`?([A-Za-z0-9_\-\.]+)`?", cleaned_response, re.IGNORECASE)
        claimed_art = art_match.group(1).strip() if art_match else ""

        is_stage1 = mode_val == "EXTRACTION" or current_stage in [
            "STAGE_1_SPECIFICATION", "STAGE_1_CRITIC", "CHECKPOINT_1_AWAITING_APPROVAL", "STAGE_2_MAPPING"
        ]
        awaiting_target_missing = (not manifest_path.exists()) if is_stage1 else (not research_path.exists())
        if (
            status_val == "CONDITIONAL_PASS"
            and awaiting_target_missing
            and claimed_art not in ("RESEARCH.md", "manifest.json")
            and any(k in cleaned_response.lower() for k in ["scribe", "source_ocr", "ocr", "visual", "pdf", "img", "figure"])
        ):
            return (None, None)

        if mode_val == "DISCOVERY" or claimed_art == "RESEARCH.md":
            expect_manifest = False
        elif mode_val == "EXTRACTION" or claimed_art == "manifest.json" or manifest_path.exists():
            expect_manifest = True
        else:
            expect_manifest = current_stage in ["STAGE_1_SPECIFICATION", "CHECKPOINT_1_AWAITING_APPROVAL", "STAGE_2_MAPPING"]
        if expect_manifest:
            if not manifest_path.is_file() or manifest_path.stat().st_size == 0:
                return ("manifest.json", f"Expected Specification Manifest 'manifest.json' does not exist at '{manifest_path}'.")
            try:
                j_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as e:
                return ("manifest.json", f"'manifest.json' contains invalid JSON: {e}")
            errs = validate_manifest_structure(j_data, cwd=cwd)
            if errs:
                return ("manifest.json", f"'manifest.json' failed schema validation: {'; '.join(errs[:3])}")
            return ("manifest.json", None)

        if not research_path.is_file() or research_path.stat().st_size == 0:
            return ("RESEARCH.md", f"Expected Research Brief 'RESEARCH.md' does not exist at '{research_path}'.")
        return ("RESEARCH.md", None)

    if agent_name == "Formalist":
        mapping_path = cwd / "mapping.json"
        if not mapping_path.is_file() or mapping_path.stat().st_size == 0:
            return ("mapping.json", f"Expected Mapping Document 'mapping.json' does not exist at '{mapping_path}'.")
        try:
            j_data = json.loads(mapping_path.read_text(encoding="utf-8"))
        except Exception as e:
            return ("mapping.json", f"'mapping.json' contains invalid JSON: {e}")
        errs = validate_mapping_structure(j_data, cwd)
        if errs:
            return ("mapping.json", f"'mapping.json' failed schema validation: {'; '.join(errs[:3])}")
        return ("mapping.json", None)

    if agent_name == "Engineer":
        # Load `.pipeline_config.json` (falling back to `DEFAULT_CONFIG` when absent).
        cfg_eng = load_pipeline_config(cwd)
        mod_file = _get_declared_target_module(cwd)
        if mod_file:
            mod_path = cwd / mod_file
            if not mod_path.is_file() or mod_path.stat().st_size == 0:
                return (mod_file, f"Declared target_module '{mod_file}' in mapping.json does not exist at '{mod_path}'.")
            return (mod_file, None)
        impl_files = _find_implementation_files(cwd, cfg_eng)
        if not impl_files:
            return ("<module>.py", f"No production implementation file ('*.py') exists in '{cwd}'.")
        return (impl_files[0].name, None)

    if agent_name == "Auditor":
        if current_stage in ("STAGE_5_ARCHIVAL_COMPLETE", "PRE_RUN_HALT"):
            archived_ver = list((cwd / "specs").glob("*/VERIFICATION.md")) if (cwd / "specs").exists() else []
            if archived_ver:
                return ("VERIFICATION.md", None)
        # Load `.pipeline_config.json` (falling back to `DEFAULT_CONFIG` when absent).
        cfg_aud = load_pipeline_config(cwd)
        verify_files = [
            p for p in list(cwd.glob("*")) + (list((cwd / "tests").glob("*")) if (cwd / "tests").is_dir() else [])
            if p.is_file()
            and p.name not in SPEC_AND_REPORT_FILES
            and _is_verification_target_name(p.name, cfg_aud, rel_path=str(p.relative_to(cwd)))
        ]
        if not verify_files:
            return ("verify_<slug>.py", f"No verification test script ('verify_*' / configured verification_globs) exists in '{cwd}'.")
        ver_md = cwd / "VERIFICATION.md"
        if not ver_md.is_file() or ver_md.stat().st_size == 0:
            return ("VERIFICATION.md", f"Expected Verification Report 'VERIFICATION.md' does not exist at '{ver_md}'.")
        return ("VERIFICATION.md", None)

    return (None, None)

__all__ = [
    "_clear_unresolved_validation_error",
    "_extract_canonical_failure_classification",
    "_resolve_active_worker_for_permission_request",
    "_resolve_user_skill_scripts_in_cmd",
    "_set_unresolved_validation_error",
    "apply_audit_rewind_transition",
    "extract_repair_resolution_note",
    "rewrite_bash_command_for_workspace",
    "validate_and_enrich_verification_report",
    "validate_audit_feedback_structure",
    "verify_expected_subagent_artifact",
]
