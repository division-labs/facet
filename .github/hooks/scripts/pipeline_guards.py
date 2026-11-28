#!/usr/bin/env python3
"""Deterministic hook reference monitor for Google Gemma 4 and GitHub Copilot.

Architecture & Lifecycle Overview:
----------------------------------
Small, locally hosted open-weight models (like Gemma 4 running in Ollama) are
fast and private, but if left completely on their own across a long coding task,
they can skip planning steps, accidentally overwrite working files, invent
relaxed tests that hide bugs, or lose track of which stage comes next.

``pipeline_guards.py`` solves that problem without using any GPU memory. Every
time VS Code Copilot starts a turn, calls a tool (like ``view``, ``create``,
``edit``, ``bash``, ``web_fetch``, or ``task``), or finishes a subagent turn,
VS Code pipes a tiny JSON event to ``pipeline_guards.py`` over ``stdin`` and
reads back a JSON decision on ``stdout``:
  - In ``preToolUse`` (BEFORE a tool runs), this script can allow the call,
    deny an unsafe or out-of-order call with a helpful remediation hint, or
    rewrite arguments cleanly (for example snapping a slightly mis-indented
    ``old_str`` onto the exact lines on disk, or injecting the caller's exact
    function signature into a subagent's prompt).
  - In ``postToolUse`` (AFTER a file is written), this script formats JSON,
    promotes LaTeX docstrings to raw ``r\"\"\"`` strings so Python backslashes
    never corrupt text, runs ``AST-1..AST-4`` syntax/scope/contract checks,
    notifies the ``Facet Lens`` visual panel over local SSE, and advances the
    pipeline state file (``.pipeline_state.json``).
  - At stage boundaries (``subagentStop``), it verifies that the expected file
    actually exists on disk, optionally runs a sub-second System-1 check via
    ``pplx-decider-flash`` (``/v1/systemone``, ``keep_alive=0``), and seals
    completed runs into ``specs/<concept_slug>/PROVENANCE.json`` with SHA-256
    cryptographic hashes mirrored outside the workspace.

Acyclic 9-Layer Package Map (``facet_guards/`` + ``pipeline_guards.py`` Facade):
  0. ``constants_and_context.py``  — Stage/checkpoint tables, regexes, ``HookContext`` cache, path/session helpers
  1. ``state_locks_and_seals.py``  — Scope shadowing, POSIX ``flock``/``O_EXCL``, atomic I/O, SHA-256 & Shadow Ledger
  2. ``text_ast_and_patch.py``     — RFC 8259 JSON formatter, docstring/``__future__`` normalizers, ``edit`` snapper, ``AST-1..6``
  3. ``visual_ocr_and_sources.py`` — 3-layer domain registry, MediaWiki API rewrite, Stage 0V plate assembly, TEI P5 XML
  4. ``spec_and_mapping_gates.py`` — ``manifest.json`` & ``mapping.json`` validators, caller parity, System-1 boundary screen
  5. ``cleanroom_and_repair.py``   — Shell rewriting, ``AUDIT_FEEDBACK.json`` normalization, target-aware rewinds, deliverable gate
  6. ``ux_cards_and_archival.py``  — Style A review cards, visual previews, Stage 5 pruning, dossier compiler, ``PROVENANCE.json``
  7. ``context_and_hydration.py``  — Role-scoped ``<workspace_map>``, Cleanroom context hydration, ``subagentStart``
  8. ``lifecycle_dispatch.py``     — Copilot hook entrypoints (``preToolUse``, ``postToolUse``, ``permissionRequest``, prompt/stop)
  9. ``pipeline_guards.py``        — Backward-compatible symbol re-export facade & CLI dispatcher (``main``)

Function Map:
  - Section 1: Package Import Bootstrap & Public Facade Re-Exports (`_SCRIPTS_DIR`, `DEFAULT_FSM`, `DEFAULT_CONFIG`)
  - Section 2: Hook CLI Dispatcher (`main`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.parse

# ============================================================================
# Section 1: Package Import Bootstrap & Public Facade Re-Exports
# ============================================================================

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

if __name__ == "__main__":
    try:
        import importlib as _importlib_preflight
        _importlib_preflight.import_module("facet_guards")
    except (SyntaxError, ImportError) as _self_host_err:
        _raw_in = sys.stdin.read()
        try:
            _pl = json.loads(_raw_in) if _raw_in.strip() else {}
        except Exception:
            _pl = {}
        _cwd_fb = Path(str(_pl.get("cwd") or "")).resolve() if isinstance(_pl, dict) and _pl.get("cwd") else Path.cwd()
        if (_cwd_fb / ".pipeline_state.json").is_file():
            raise
        sys.stderr.write(f"[facet-self-host-fallback] Idle workspace import error bypassed: {_self_host_err}\n")
        sys.stdout.write("{}\n")
        sys.exit(0)

from facet_guards import (  # noqa: E402
    ALLOWED_SUBAGENTS,
    APPROVED_SOURCES_FILENAME,
    AuditorCleanroomPolicy,
    BENIGN_TOOLS,
    CHECKPOINT_CTA_TEXT,
    CHECKPOINT_NEXT_PREVIEWS,
    CHECKPOINT_PROGRESS_DOTS,
    CHECKPOINT_STAGE_SPECS,
    CONFIG_FILE_NAME,
    ContextHydrationMode,
    ContractAndGatesMode,
    CoordinatorResidencyMode,
    DEFAULT_CONFIG,
    DEFAULT_FSM,
    DEFAULT_STAGE_RECEIPTS,
    EXPERIMENT_ENUM_OPTIONS,
    EngineerEscalationMode,
    EngineerHydrationMode,
    EngineerRepairMode,
    FACET_PROMPT_MARKERS,
    FSM_FILE_NAME,
    GOVERNANCE_MODES,
    GovernanceMode,
    HOOK_CONTEXT,
    HOOK_STATE_SNAPSHOT_WHITELIST,
    HOOK_TIMEOUT_ENVELOPE,
    HookContext,
    LOCAL_SPEC_CRITIC_MODES,
    LocalSpecCriticMode,
    REASONING_TRACE_SLOTS,
    RewindDagPolicy,
    SCOPED_EDIT_DELIVERABLES,
    SCRATCH_DIR_NAME,
    SCRATCH_RETENTION_MODES,
    SPECIALIST_SPINNER_LABELS,
    SPEC_AND_REPORT_FILES,
    STAGE_RECEIPTS_FILENAME,
    STAGE_SPECIALIST_ALLOWLIST,
    STAGE_SPINNER_LABELS,
    STATE_LOCK_STRIPES,
    STATE_LOCK_TIMEOUT_SEC,
    STATIC_VALIDATOR_MODES,
    SYNTHETIC_HOOK_DIRECTIVE_MARKERS,
    ScratchRetentionMode,
    SourceIngressMode,
    StageChecklistMode,
    StaticValidatorMode,
    Sys1TriggerPolicy,
    TERMINAL_DENIAL_CLAUSE,
    TEXT_ONLY_SPECIALISTS,
    VALID_FAILURE_CLASSIFICATIONS,
    VISUAL_AND_PDF_SUFFIXES,
    _BRIEFING_STOPWORDS,
    _EMOJI_STRIP_RE,
    _HELD_STATE_LOCKS,
    _LATEX_N_EXACT_STEMS,
    _LATEX_N_PREFIX_STEMS,
    _LATEX_T_EXACT_STEMS,
    _LATEX_T_PREFIX_STEMS,
    _LOCAL_ABS_MD_LINK_RE,
    _PEP508_MARKER_ATOM,
    _PEP508_MARKER_VARS,
    _PEP508_REQ_RE,
    _SELF_TALK_BRIEFING_RE,
    _TRIVIAL_MATH_BASE_SCALARS,
    _TYPOGRAPHIC_LIGATURE_MAP,
    _VALID_EQUIVALENCE_MODES,
    _VALID_PREDICATE_TYPES,
    _fcntl,
    _strip_dot_slash_prefix,
    compute_file_sha256,
    extract_slug_from_file,
    get_copilot_home,
    get_payload_cwd,
    get_session_id,
    is_subagent_payload,
    slugify,
    _SYSTEMONE_HTTP_TRANSPORT,
    _default_systemone_http_transport,
    _prune_empty_scratch_dir,
    acquire_state_lock_file,
    append_hook_trace_record,
    append_run_telemetry_event,
    atomic_write_text,
    call_systemone_decision_model,
    check_scope_and_lock,
    classify_error_incident,
    clear_stage_receipts,
    build_merkle_audit_path,
    compute_canonical_seal_digest,
    compute_rfc6962_merkle_root,
    verify_merkle_audit_path,
    discard_rollback_backup,
    invocation_state_locks,
    is_depth0_session,
    load_pipeline_config,
    load_pipeline_fsm,
    load_session_state,
    load_stage_receipts,
    notify_facet_lens_sse,
    purge_session_state_entries,
    release_state_lock_file,
    restore_or_unlink_invalid_draft,
    save_pipeline_fsm,
    save_session_state,
    snapshot_session_tree_state,
    stage_rollback_backup,
    sync_shadow_seal_ledger,
    update_session_state,
    update_stage_receipts,
    verify_workspace_cryptographic_seals,
    write_reasoning_trace,
    _annotation_contains_unconstrained_any,
    _apply_indent_delta,
    _canonicalize_module_docstring_order,
    _collect_module_defined_names,
    _collect_target_store_names,
    _compare_continuous_eq_violation,
    _escape_md_cell,
    _extract_file_semantic_hunks,
    _extract_float_literal,
    _extract_module_docstring_relaxed,
    _find_implementation_files,
    _find_residual_control_bytes,
    _format_numbered_remediation_excerpt,
    _format_symbol_for_prose,
    _get_declared_target_module,
    _hoist_misplaced_future_imports,
    _infer_missing_create_path,
    _is_discrete_meta_expr,
    _is_finite_number,
    _is_scalar_minmax_over_abs,
    _is_verification_target_name,
    _promote_latex_docstrings_to_raw,
    _reject_non_rfc8259_constant,
    _repair_gemma_json_string_escapes,
    _split_lines_with_offsets,
    _strip_markdown_code_spans,
    _strip_python_comments_per_line,
    _truncate_md_table_desc,
    compute_method_l3_localized_hunks,
    compute_module_ast_closure_hashes,
    detect_rational_pole_sign_clip,
    extract_ast_fault_cone_slice,
    extract_mandated_target_module,
    extract_on_disk_symbol_excerpt,
    find_lexical_unbound_names,
    format_chat_file_link,
    format_gemma_json_artifact,
    is_near_miss_command,
    normalize_markdown_file_links,
    render_edit_scope_hint,
    resolve_edit_pair,
    resolve_edit_pair_with_stage,
    sanitize_markdown_artifact_text,
    sanitize_user_briefing_prose,
    validate_python_ast,
    validate_requirements_txt,
    validate_verification_script_ast,
    _assemble_source_ocr_from_plates,
    _clean_single_plate_markdown_body,
    _extract_explicit_workspace_source_paths,
    _extract_single_plate_headings,
    _format_prepare_visual_flag,
    _format_stage_ocr_cmd_args,
    _has_post_research_stage1_ocr,
    _inspect_source_ocr_plate_coverage,
    _is_markovian_stage1_with_research_md,
    _load_required_ocr_plates,
    _match_allowed_scribe_plate_md,
    _ocr_plates_dir,
    _plate_markdown_path,
    canonicalize_wikipedia_fetch_url,
    check_path_in_working_set,
    clean_ocr_staging,
    compute_stage_working_set,
    detect_ocr_uncertainties,
    discover_research_inputs,
    execute_add_source_command,
    extract_source_origin_from_ocr,
    filter_decorative_ocr_inputs,
    format_working_set_denial,
    is_approved_research_url,
    load_approved_research_sources,
    normalize_research_domain,
    parse_add_source_command,
    parse_and_normalize_tei_provenance,
    resolve_initial_active_stage,
    resolve_linked_composition_specs,
    resolve_mosaic_composer_linked_specs,
    _collect_nontrivial_anchor_literals,
    _compute_manifest_core_hash,
    _compute_mapping_implementation_contract_hash,
    _compute_obligation_hashes,
    apply_artifact_gate,
    detect_anchor_test_leakage,
    detect_deterministic_spec_or_code_risk,
    extract_source_interface_contracts,
    is_checkpoint_gate_required,
    manifest_has_epistemic_assumptions,
    normalize_math_symbol_variants,
    run_systemone_spec_screen,
    run_systemone_stage3_leakage_screen,
    validate_dimensional_unit_consistency,
    validate_manifest_structure,
    validate_mapping_structure,
    validate_obligation_coverage,
    validate_target_module_entrypoints,
    _clear_unresolved_validation_error,
    _extract_canonical_failure_classification,
    _resolve_active_worker_for_permission_request,
    _resolve_user_skill_scripts_in_cmd,
    _set_unresolved_validation_error,
    apply_audit_rewind_transition,
    extract_repair_resolution_note,
    rewrite_bash_command_for_workspace,
    validate_and_enrich_verification_report,
    validate_audit_feedback_structure,
    verify_expected_subagent_artifact,
    _briefing_content_tokens,
    _briefing_exceeds_prior_overlap,
    _humanize_predicate_basis,
    _load_stage_briefings_entries,
    _load_stage_briefings_map,
    _render_review_gate_card_lines,
    _resolve_stage_briefing_text,
    _sanitize_mermaid_label,
    _save_stage_briefing_entry,
    _synthesize_fallback_stage_summary,
    archive_completed_run,
    discover_referenced_archived_specs,
    extract_artifact_presentation_facts,
    extract_specialist_stage_briefing,
    generate_archived_spec_dossier,
    handle_garden_specs,
    prelinkify_briefing_workspace_mentions,
    prune_stage5_artifacts,
    recall_semantic_archived_specs,
    render_checkpoint_briefing_block,
    render_checkpoint_reprompt_block,
    render_stage5_topic_result_briefing,
    render_stage_visual_preview,
    render_status_pulse_block,
    snapshot_and_clean_interrupted_run,
    _build_public_surface_stub_block,
    _is_stub_function_node,
    augment_specialist_task_prompt,
    build_canonical_specialist_task_prompt,
    build_deterministic_stage_checklist,
    build_role_workspace_map,
    compile_epistemic_working_set,
    format_corrected_orchestrator_call,
    handle_subagent_start,
    has_canonical_orchestrator_brief,
    render_orchestrator_handoff_brief,
    _format_plate_vector_glyph_anchor,
    _read_current_turn_assistant_text,
    checkpoint_handoff_gate,
    handle_coordinator_guard,
    handle_error_occurred,
    handle_facet_prompt,
    handle_facet_stop,
    handle_hypervisor_guard,
    handle_orchestrator_stop,
    handle_permission_request,
    handle_post_failure,
    handle_pre_compact,
    handle_pre_tool,
    handle_session_end,
    handle_session_start,
    handle_subagent_stop,
    handle_user_prompt_submitted,
    handle_user_prompt_transformed,
    handle_validate_edit,
    is_runaway_stop_continuation,
    reconcile_steered_user_commands,
)


# ============================================================================
# Section 2: Hook CLI Dispatcher
# Coupled contract: .github/hooks/pipeline-guards.json & .github/agents/*.agent.md
# Action names dispatched below must stay synchronized with workspace/agent hook commands (verified in scripts/validate_system.py).
# ============================================================================

def main():
    """Parse CLI arguments, read hook JSON from stdin, and dispatch action."""
    # Parse CLI `--scope` and `action`, deduplicate dual-scope hook events, acquire striped state locks, dispatch the handler, and write JSON to `stdout`.
    parser = argparse.ArgumentParser(description="Facet Hook Controller")
    parser.add_argument("action", nargs="?", default="")
    parser.add_argument("--scope", default="workspace", choices=["workspace", "user"])
    args, _ = parser.parse_known_args()

    action = args.action
    scope = args.scope

    # Execute guarded I/O or parsing and recover cleanly on error.
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    # Handle `check_scope_and_lock(payload, scope, action)`.
    if check_scope_and_lock(payload, scope, action):
        append_hook_trace_record(action, scope, payload, {}, yielded=True)
        sys.stdout.write(json.dumps({}))
        return

    # Hold striped session and workspace file locks for the duration of this hook.
    with invocation_state_locks(get_session_id(payload), get_payload_cwd(payload)):
        if action == "pre-tool":
            result = handle_pre_tool(payload)
        elif action == "hypervisor-guard":
            result = handle_hypervisor_guard(payload)
        elif action == "facet-prompt":
            result = handle_facet_prompt(payload)
        elif action == "facet-stop":
            result = handle_facet_stop(payload)
        elif action == "coordinator-guard":
            result = handle_coordinator_guard(payload)
        elif action == "orchestrator-prompt":
            result = handle_user_prompt_transformed(payload, orchestrator_scoped=True)
        elif action == "orchestrator-stop":
            result = handle_orchestrator_stop(payload)
        elif action == "post-failure":
            result = handle_post_failure(payload)
        elif action == "validate-edit":
            result = handle_validate_edit(payload)
        elif action == "session-start":
            result = handle_session_start(payload)
        elif action == "pre-compact":
            result = handle_pre_compact(payload)
        elif action == "user-prompt":
            result = handle_user_prompt_submitted(payload)
        elif action == "user-prompt-transformed":
            result = handle_user_prompt_transformed(payload)
        elif action == "subagent-start":
            result = handle_subagent_start(payload)
        elif action == "subagent-stop":
            result = handle_subagent_stop(payload)
        elif action == "session-end":
            result = handle_session_end(payload)
        elif action == "error-occurred":
            result = handle_error_occurred(payload)
        elif action == "permission-request":
            result = handle_permission_request(payload)
        elif action == "garden-specs":
            result = handle_garden_specs(get_payload_cwd(payload))
        elif action == "reset":
            raw_cli_sid = str(payload.get("sessionId") or payload.get("session_id") or "").strip() or None
            res = snapshot_and_clean_interrupted_run(
                get_payload_cwd(payload),
                reason="CLI manual reset",
                session_id=raw_cli_sid,
                immediate_cleanup=bool(raw_cli_sid),
            )
            if res:
                result = {"status": "success", "archived_and_cleaned": res}
            else:
                result = {"status": "noop", "message": "Root workspace is already clean."}
        else:
            result = {}

    append_hook_trace_record(action, scope, payload, result, yielded=False)
    if isinstance(result, dict) and (result.get("permissionDecision") == "deny" or result.get("decision") == "block"):
        append_run_telemetry_event(
            "hook_gate",
            {
                "action": str(action),
                "toolName": str(payload.get("toolName") or payload.get("tool_name") or ""),
                "decision": str(result.get("permissionDecision") or result.get("decision") or ""),
            },
            session_id=get_session_id(payload),
        )
    sys.stdout.write(json.dumps(result))


__all__ = [
    "main",
    "urllib",
    "ALLOWED_SUBAGENTS",
    "APPROVED_SOURCES_FILENAME",
    "BENIGN_TOOLS",
    "CHECKPOINT_CTA_TEXT",
    "CHECKPOINT_NEXT_PREVIEWS",
    "CHECKPOINT_PROGRESS_DOTS",
    "CHECKPOINT_STAGE_SPECS",
    "CONFIG_FILE_NAME",
    "DEFAULT_CONFIG",
    "DEFAULT_FSM",
    "FACET_PROMPT_MARKERS",
    "FSM_FILE_NAME",
    "HOOK_CONTEXT",
    "HOOK_STATE_SNAPSHOT_WHITELIST",
    "HookContext",
    "REASONING_TRACE_SLOTS",
    "SCOPED_EDIT_DELIVERABLES",
    "SCRATCH_DIR_NAME",
    "SPECIALIST_SPINNER_LABELS",
    "SPEC_AND_REPORT_FILES",
    "STAGE_SPECIALIST_ALLOWLIST",
    "STAGE_SPINNER_LABELS",
    "STATE_LOCK_STRIPES",
    "STATE_LOCK_TIMEOUT_SEC",
    "SYNTHETIC_HOOK_DIRECTIVE_MARKERS",
    "TERMINAL_DENIAL_CLAUSE",
    "TEXT_ONLY_SPECIALISTS",
    "VALID_FAILURE_CLASSIFICATIONS",
    "VISUAL_AND_PDF_SUFFIXES",
    "_BRIEFING_STOPWORDS",
    "_EMOJI_STRIP_RE",
    "_HELD_STATE_LOCKS",
    "_LATEX_N_EXACT_STEMS",
    "_LATEX_N_PREFIX_STEMS",
    "_LATEX_T_EXACT_STEMS",
    "_LATEX_T_PREFIX_STEMS",
    "_LOCAL_ABS_MD_LINK_RE",
    "_PEP508_MARKER_ATOM",
    "_PEP508_MARKER_VARS",
    "_PEP508_REQ_RE",
    "_SELF_TALK_BRIEFING_RE",
    "_TRIVIAL_MATH_BASE_SCALARS",
    "_TYPOGRAPHIC_LIGATURE_MAP",
    "_VALID_EQUIVALENCE_MODES",
    "_VALID_PREDICATE_TYPES",
    "_fcntl",
    "_strip_dot_slash_prefix",
    "compute_file_sha256",
    "extract_slug_from_file",
    "get_copilot_home",
    "get_payload_cwd",
    "get_session_id",
    "is_subagent_payload",
    "slugify",
    "_SYSTEMONE_HTTP_TRANSPORT",
    "_default_systemone_http_transport",
    "_prune_empty_scratch_dir",
    "acquire_state_lock_file",
    "append_hook_trace_record",
    "append_run_telemetry_event",
    "atomic_write_text",
    "call_systemone_decision_model",
    "check_scope_and_lock",
    "classify_error_incident",
    "build_merkle_audit_path",
    "compute_canonical_seal_digest",
    "compute_rfc6962_merkle_root",
    "verify_merkle_audit_path",
    "discard_rollback_backup",
    "invocation_state_locks",
    "is_depth0_session",
    "load_pipeline_config",
    "load_pipeline_fsm",
    "load_session_state",
    "notify_facet_lens_sse",
    "purge_session_state_entries",
    "release_state_lock_file",
    "restore_or_unlink_invalid_draft",
    "save_pipeline_fsm",
    "save_session_state",
    "snapshot_session_tree_state",
    "stage_rollback_backup",
    "sync_shadow_seal_ledger",
    "verify_workspace_cryptographic_seals",
    "write_reasoning_trace",
    "_annotation_contains_unconstrained_any",
    "_apply_indent_delta",
    "_canonicalize_module_docstring_order",
    "_collect_module_defined_names",
    "_collect_target_store_names",
    "_compare_continuous_eq_violation",
    "_escape_md_cell",
    "_extract_file_semantic_hunks",
    "_extract_float_literal",
    "_extract_module_docstring_relaxed",
    "_find_implementation_files",
    "_find_residual_control_bytes",
    "_format_numbered_remediation_excerpt",
    "_format_symbol_for_prose",
    "_get_declared_target_module",
    "_hoist_misplaced_future_imports",
    "_infer_missing_create_path",
    "_is_discrete_meta_expr",
    "_is_finite_number",
    "_is_scalar_minmax_over_abs",
    "_is_verification_target_name",
    "_promote_latex_docstrings_to_raw",
    "_reject_non_rfc8259_constant",
    "_repair_gemma_json_string_escapes",
    "_split_lines_with_offsets",
    "_strip_markdown_code_spans",
    "_strip_python_comments_per_line",
    "_truncate_md_table_desc",
    "compute_method_l3_localized_hunks",
    "compute_module_ast_closure_hashes",
    "detect_rational_pole_sign_clip",
    "extract_ast_fault_cone_slice",
    "extract_mandated_target_module",
    "extract_on_disk_symbol_excerpt",
    "find_lexical_unbound_names",
    "format_chat_file_link",
    "format_gemma_json_artifact",
    "is_near_miss_command",
    "normalize_markdown_file_links",
    "render_edit_scope_hint",
    "resolve_edit_pair",
    "resolve_edit_pair_with_stage",
    "sanitize_markdown_artifact_text",
    "sanitize_user_briefing_prose",
    "validate_python_ast",
    "validate_requirements_txt",
    "validate_verification_script_ast",
    "_assemble_source_ocr_from_plates",
    "_clean_single_plate_markdown_body",
    "_extract_explicit_workspace_source_paths",
    "_extract_single_plate_headings",
    "_format_prepare_visual_flag",
    "_format_stage_ocr_cmd_args",
    "_has_post_research_stage1_ocr",
    "_inspect_source_ocr_plate_coverage",
    "_is_markovian_stage1_with_research_md",
    "_load_required_ocr_plates",
    "_match_allowed_scribe_plate_md",
    "_ocr_plates_dir",
    "_plate_markdown_path",
    "canonicalize_wikipedia_fetch_url",
    "clean_ocr_staging",
    "detect_ocr_uncertainties",
    "discover_research_inputs",
    "execute_add_source_command",
    "extract_source_origin_from_ocr",
    "filter_decorative_ocr_inputs",
    "is_approved_research_url",
    "load_approved_research_sources",
    "normalize_research_domain",
    "parse_add_source_command",
    "parse_and_normalize_tei_provenance",
    "resolve_initial_active_stage",
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
    "normalize_math_symbol_variants",
    "run_systemone_spec_screen",
    "run_systemone_stage3_leakage_screen",
    "validate_dimensional_unit_consistency",
    "validate_manifest_structure",
    "validate_mapping_structure",
    "validate_obligation_coverage",
    "validate_target_module_entrypoints",
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
    "_briefing_content_tokens",
    "_briefing_exceeds_prior_overlap",
    "_humanize_predicate_basis",
    "_load_stage_briefings_entries",
    "_load_stage_briefings_map",
    "_render_review_gate_card_lines",
    "_resolve_stage_briefing_text",
    "_sanitize_mermaid_label",
    "_save_stage_briefing_entry",
    "_synthesize_fallback_stage_summary",
    "archive_completed_run",
    "discover_referenced_archived_specs",
    "extract_artifact_presentation_facts",
    "extract_specialist_stage_briefing",
    "generate_archived_spec_dossier",
    "handle_garden_specs",
    "prelinkify_briefing_workspace_mentions",
    "prune_stage5_artifacts",
    "recall_semantic_archived_specs",
    "render_checkpoint_briefing_block",
    "render_checkpoint_reprompt_block",
    "render_stage5_topic_result_briefing",
    "render_stage_visual_preview",
    "render_status_pulse_block",
    "snapshot_and_clean_interrupted_run",
    "_build_public_surface_stub_block",
    "_is_stub_function_node",
    "augment_specialist_task_prompt",
    "build_canonical_specialist_task_prompt",
    "build_deterministic_stage_checklist",
    "build_role_workspace_map",
    "compile_epistemic_working_set",
    "format_corrected_orchestrator_call",
    "handle_subagent_start",
    "has_canonical_orchestrator_brief",
    "render_orchestrator_handoff_brief",
    "_format_plate_vector_glyph_anchor",
    "_read_current_turn_assistant_text",
    "checkpoint_handoff_gate",
    "handle_coordinator_guard",
    "handle_error_occurred",
    "handle_facet_prompt",
    "handle_facet_stop",
    "handle_hypervisor_guard",
    "handle_orchestrator_stop",
    "handle_permission_request",
    "handle_post_failure",
    "handle_pre_compact",
    "handle_pre_tool",
    "handle_session_end",
    "handle_session_start",
    "handle_subagent_stop",
    "handle_user_prompt_submitted",
    "handle_user_prompt_transformed",
    "handle_validate_edit",
    "is_runaway_stop_continuation",
    "reconcile_steered_user_commands",
    "HOOK_TIMEOUT_ENVELOPE",
    "STAGE_RECEIPTS_FILENAME",
    "DEFAULT_STAGE_RECEIPTS",
    "clear_stage_receipts",
    "load_stage_receipts",
    "update_session_state",
    "update_stage_receipts",
    "check_path_in_working_set",
    "compute_stage_working_set",
    "format_working_set_denial",
    "resolve_linked_composition_specs",
    "resolve_mosaic_composer_linked_specs",
    "AuditorCleanroomPolicy",
    "ContextHydrationMode",
    "ContractAndGatesMode",
    "CoordinatorResidencyMode",
    "EXPERIMENT_ENUM_OPTIONS",
    "EngineerEscalationMode",
    "EngineerHydrationMode",
    "EngineerRepairMode",
    "GOVERNANCE_MODES",
    "GovernanceMode",
    "LOCAL_SPEC_CRITIC_MODES",
    "LocalSpecCriticMode",
    "RewindDagPolicy",
    "SCRATCH_RETENTION_MODES",
    "STATIC_VALIDATOR_MODES",
    "ScratchRetentionMode",
    "SourceIngressMode",
    "StageChecklistMode",
    "StaticValidatorMode",
    "Sys1TriggerPolicy",
]


if __name__ == "__main__":
    main()
