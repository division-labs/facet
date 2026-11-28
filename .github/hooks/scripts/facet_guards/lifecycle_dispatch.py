"""Copilot lifecycle hook entrypoints (`preToolUse`, `postToolUse`, `permissionRequest`, prompt/stop gates).

Function Map:
  - Section 1: Session Lifecycle, Error Classification, `permissionRequest` & `postToolUseFailure` Handlers (`handle_session_start`, `handle_permission_request`, `handle_post_failure`)
  - Section 2: Checkpoint Handoff Gate, `@Facet` Hypervisor Guard & `@Orchestrator` Coordinator Guard (`checkpoint_handoff_gate`, `handle_hypervisor_guard`, `handle_coordinator_guard`)
  - Section 3: `handle_pre_tool` — Modality Firewall, Cleanroom Confinement, Scoped-Edit Auto-Snap & `task` Routing (`handle_pre_tool`)
  - Section 4: `handle_validate_edit` — `postToolUse` Formatting, `AST-1..AST-6` Gates, `pytest` Proof & Checkpoint Sealing (`handle_validate_edit`)
  - Section 5: Contractual Slash Commands & Steered Command Reconciliation (`handle_user_prompt_submitted`, `handle_user_prompt_transformed`, `reconcile_steered_user_commands`)
  - Section 6: `@Facet` / `@Orchestrator` Prompt & Stop Gates (`handle_facet_prompt`, `handle_facet_stop`, `handle_subagent_stop`, `_format_plate_vector_glyph_anchor`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

from .constants_and_context import (
    ALLOWED_SUBAGENTS,
    APPROVED_SOURCES_FILENAME,
    BENIGN_TOOLS,
    CHECKPOINT_CTA_TEXT,
    HOOKS_REPO_ROOT,
    HOOKS_SCRIPTS_DIR,
    CONFIG_FILE_NAME,
    DEFAULT_FSM,
    FACET_PROMPT_MARKERS,
    FSM_FILE_NAME,
    REASONING_TRACE_SLOTS,
    SCOPED_EDIT_DELIVERABLES,
    SCRATCH_DIR_NAME,
    SPECIALIST_SPINNER_LABELS,
    SPEC_AND_REPORT_FILES,
    STAGE_SPECIALIST_ALLOWLIST,
    STAGE_SPINNER_LABELS,
    SYNTHETIC_HOOK_DIRECTIVE_MARKERS,
    TERMINAL_DENIAL_CLAUSE,
    TEXT_ONLY_SPECIALISTS,
    VISUAL_AND_PDF_SUFFIXES,
    _strip_dot_slash_prefix,
    compute_file_sha256,
    get_copilot_home,
    get_payload_cwd,
    get_session_id,
    slugify,
)
from .state_locks_and_seals import (
    _prune_empty_scratch_dir,
    append_run_telemetry_event,
    classify_error_incident,
    atomic_write_text,
    clear_stage_receipts,
    discard_rollback_backup,
    is_depth0_session,
    load_pipeline_config,
    load_pipeline_fsm,
    load_session_state,
    load_stage_receipts,
    notify_facet_lens_sse,
    purge_session_state_entries,
    restore_or_unlink_invalid_draft,
    save_pipeline_fsm,
    save_session_state,
    stage_rollback_backup,
    update_session_state,
    update_stage_receipts,
    verify_workspace_cryptographic_seals,
    write_reasoning_trace,
)
from .text_ast_and_patch import (
    _canonicalize_module_docstring_order,
    _find_implementation_files,
    _format_numbered_remediation_excerpt,
    _get_declared_target_module,
    _hoist_misplaced_future_imports,
    _infer_missing_create_path,
    _is_verification_target_name,
    _promote_latex_docstrings_to_raw,
    detect_rational_pole_sign_clip,
    extract_mandated_target_module,
    extract_on_disk_symbol_excerpt,
    find_lexical_unbound_names,
    format_chat_file_link,
    format_gemma_json_artifact,
    is_near_miss_command,
    normalize_markdown_file_links,
    resolve_edit_pair_with_stage,
    sanitize_markdown_artifact_text,
    sanitize_user_briefing_prose,
    validate_python_ast,
    validate_requirements_txt,
    validate_verification_script_ast,
)
from .visual_ocr_and_sources import (
    _assemble_source_ocr_from_plates,
    _clean_single_plate_markdown_body,
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
    discover_research_inputs,
    execute_add_source_command,
    format_working_set_denial,
    is_approved_research_url,
    load_approved_research_sources,
    parse_add_source_command,
    parse_and_normalize_tei_provenance,
    resolve_initial_active_stage,
)
from .spec_and_mapping_gates import (
    _compute_manifest_core_hash,
    _compute_mapping_implementation_contract_hash,
    apply_artifact_gate,
    detect_anchor_test_leakage,
    is_checkpoint_gate_required,
    run_systemone_spec_screen,
    run_systemone_stage3_leakage_screen,
    validate_manifest_structure,
    validate_mapping_structure,
    validate_target_module_entrypoints,
)
from .cleanroom_and_repair import (
    _clear_unresolved_validation_error,
    _resolve_active_worker_for_permission_request,
    _resolve_user_skill_scripts_in_cmd,
    _set_unresolved_validation_error,
    apply_audit_rewind_transition,
    rewrite_bash_command_for_workspace,
    validate_and_enrich_verification_report,
    validate_audit_feedback_structure,
    verify_expected_subagent_artifact,
)
from .ux_cards_and_archival import (
    _save_stage_briefing_entry,
    archive_completed_run,
    discover_referenced_archived_specs,
    extract_specialist_stage_briefing,
    prelinkify_briefing_workspace_mentions,
    render_checkpoint_briefing_block,
    render_checkpoint_reprompt_block,
    render_stage5_topic_result_briefing,
    render_status_pulse_block,
    snapshot_and_clean_interrupted_run,
)
from .context_and_hydration import (
    augment_specialist_task_prompt,
    format_corrected_orchestrator_call,
    has_canonical_orchestrator_brief,
    render_orchestrator_handoff_brief,
)

# ============================================================================
# Section 1: Session Lifecycle, Error Classification, `permissionRequest` & `postToolUseFailure` Handlers
# Handles session startup probes, crash logging, safe workspace/domain auto-approvals, and rollback recovery hints.
# ============================================================================

def handle_session_start(payload):
    """Probe Ollama availability and archived specs on ``sessionStart``.

    Args:
        payload: Hook stdin JSON dictionary.

    Returns:
        Dictionary with ``additionalContext`` summarizing Ollama and ``specs/``.
    """
    # Ping local Ollama (`/api/tags`), list available Gemma models, and summarize existing `specs/` packages and `research/` files at chat startup.
    cwd = get_payload_cwd(payload)

    endpoint = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434"
    if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
        endpoint = f"http://{endpoint}"

    tags_url = f"{endpoint.rstrip('/')}/api/tags"
    context_lines = []

    try:
        req = urllib.request.Request(tags_url, headers={"User-Agent": "Copilot-SessionStart-Hook"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [str(m.get("name")) for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]
            gemma_models = [m for m in models if "gemma" in m.lower()]
            context_lines.append(f"[INFO] [Environment Active] Ollama running at {endpoint}.")
            if gemma_models:
                context_lines.append(f"Available Gemma Models: {', '.join(gemma_models)}")
            else:
                context_lines.append(f"Total Local Models: {len(models)}.")
    except Exception:
        context_lines.append(
            f"[WARNING] [Environment Warning] Ollama not reachable at {endpoint}. "
            "Ensure 'ollama serve' is running."
        )

    if (cwd / "specs").exists():
        specs_dirs = [d.name for d in (cwd / "specs").iterdir() if d.is_dir() and not d.name.startswith(".")]
        if specs_dirs:
            context_lines.append(f"Archived Specifications available in specs/: {', '.join(sorted(specs_dirs))}. Ready for new run.")

    res_inputs = discover_research_inputs(cwd)
    if res_inputs["ocr_files"] or res_inputs["text_files"]:
        parts_desc = []
        if res_inputs["ocr_files"]:
            parts_desc.append(f"OCR-required={res_inputs['ocr_files']} (delegate Stage 0V to @Scribe first)")
        if res_inputs["text_files"]:
            parts_desc.append(f"Text={res_inputs['text_files']}")
        context_lines.append(f"Local Research Inputs in research/: {'; '.join(parts_desc)}.")

    output_context = "\n".join(context_lines) if context_lines else ""
    return {"additionalContext": output_context}


def _render_compaction_state_anchor(cwd: Path) -> str:
    r"""Build a compact post-compaction state anchor summarizing on-disk stage artifacts and OCR plate progress."""
    fsm = load_pipeline_fsm(cwd) if (cwd / FSM_FILE_NAME).is_file() else {}
    cur_stage = str(fsm.get("current_stage") or "IDLE")
    on_disk = [
        fn for fn in ("SOURCE_TEXT.md", "SOURCE_OCR.md", "RESEARCH.md", "manifest.json", "mapping.json", "AUDIT_FEEDBACK.json", "VERIFICATION.md")
        if (cwd / fn).is_file()
    ]
    py_mods = sorted(p.name for p in cwd.glob("*.py") if p.is_file())
    lines = [
        "<compaction_state_anchor>",
        f"[POST-COMPACTION ON-DISK ANCHOR]: Active stage='{cur_stage}', specs_on_disk={on_disk}, py_files={py_mods}.",
    ]
    req_plates = _load_required_ocr_plates(cwd)
    if req_plates:
        _, comp_plates, miss_plates = _inspect_source_ocr_plate_coverage(cwd)
        lines.append(
            f"- Stage 0V Plates: completed={len(comp_plates)}/{len(req_plates)}, remaining={[p.name for p in miss_plates]}."
        )
    lines.append("Rely on these verified on-disk files rather than repeating completed steps.")
    lines.append("</compaction_state_anchor>")
    return "\n".join(lines)


def _consume_compaction_anchor(session_id: str, agent_id: str, cwd: Path) -> str:
    r"""Consume ``compaction_pending_anchor`` for ``agent_id``/``session_id`` and return ``<compaction_state_anchor>``."""
    triggered = False
    for key in ([agent_id, session_id] if agent_id else [session_id]):
        if not key:
            continue
        st = load_session_state(key)
        if st.get("compaction_pending_anchor"):
            triggered = True
            update_session_state(key, lambda d: d.pop("compaction_pending_anchor", None))
    return (_render_compaction_state_anchor(cwd) + "\n") if triggered else ""


def handle_pre_compact(payload):
    """Record ``preCompact`` notifications so the next hook turn injects a crisp on-disk state anchor.

    Per GitHub Copilot Hooks Reference, ``preCompact`` stdout is ignored;
    setting ``compaction_pending_anchor`` in ``hooks_state.json`` delivers a
    compact ``<compaction_state_anchor>`` on the immediately following tool/prompt turn.

    Args:
        payload: Hook stdin JSON dictionary.

    Returns:
        Empty dict ``{}``.
    """
    session_id = get_session_id(payload)
    agent_id = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
    target_key = agent_id or session_id
    if target_key:
        update_session_state(
            target_key,
            lambda st: st.update({"compaction_pending_anchor": True, "last_pre_compact_ts": time.time()}),
        )
    append_run_telemetry_event(
        "pre_compact",
        {"agent_id": agent_id or None},
        session_id=session_id,
    )
    return {}


def handle_session_end(payload):
    """Handle ``sessionEnd`` lifecycle notifications.

    Args:
        payload: Hook stdin JSON dictionary.

    Returns:
        Empty dict ``{}``.
    """
    # No-op notification handler invoked when a chat session closes cleanly.
    del payload  # Unused.
    return {}


def handle_error_occurred(payload):
    """Append structured runtime error diagnostics to ``.pipeline_errors.log``.

    Args:
        payload: Hook stdin JSON dictionary carrying ``error`` and ``toolName``.

    Returns:
        Empty dict ``{}``.
    """
    cwd = get_payload_cwd(payload)
    session_id = get_session_id(payload)
    agent_id = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
    parent_agent_id = str(payload.get("parentAgentId") or payload.get("parent_agent_id") or "").strip()

    # Clear stale active_agent on crashed child and parent sessions.
    if agent_id:
        child_state = load_session_state(agent_id)
        linked_parent = str(child_state.get("parent_session_id") or "").strip()
        prev_parent_agent = child_state.get("previous_parent_agent")
        child_state["active_agent"] = None
        child_state["previous_parent_agent"] = None
        save_session_state(agent_id, child_state)
        for p_id in {p for p in (parent_agent_id, linked_parent, session_id if session_id != agent_id else "") if p}:
            p_state = load_session_state(p_id)
            p_state["active_agent"] = prev_parent_agent if p_state.get("parent_session_id") else None
            save_session_state(p_id, p_state)
    elif session_id:
        s_state = load_session_state(session_id)
        if s_state.get("active_agent") is not None:
            s_state["active_agent"] = None
            save_session_state(session_id, s_state)

    error_info = payload.get("error") or payload.get("message") or "Unknown error"
    error_ctx = str(payload.get("errorContext") or payload.get("error_context") or "unknown")
    recoverable = bool(payload.get("recoverable", False))
    agent_type = str(
        payload.get("agentType")
        or payload.get("agent_type")
        or payload.get("agentName")
        or payload.get("agent_name")
        or ""
    ).strip()
    tool_name = payload.get("toolName") or payload.get("tool_name") or "unknown_tool"
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    category, clean_msg = classify_error_incident(error_info, error_ctx, recoverable)
    fsm_exists = (cwd / FSM_FILE_NAME).is_file()
    sess_err_state = load_session_state(session_id) if session_id else {}
    is_facet_err_session = bool(
        fsm_exists
        or agent_type in (ALLOWED_SUBAGENTS | {"Facet", "Orchestrator"})
        or sess_err_state.get("is_facet_hypervisor_session")
        or sess_err_state.get("parent_session_id")
    )
    if not is_facet_err_session:
        return {}

    fsm = load_pipeline_fsm(cwd) if fsm_exists else {}
    stage = str(fsm.get("current_stage", "PRE_RUN_HALT")) if fsm_exists else "PRE_RUN_HALT"

    if fsm_exists:
        fsm["last_incident"] = {
            "timestamp": timestamp,
            "stage": stage,
            "category": category,
            "error_context": error_ctx,
            "recoverable": recoverable,
            "agent_type": agent_type or None,
            "message": clean_msg,
        }
        # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
        save_pipeline_fsm(cwd, fsm)

    log_entry = (
        f"[{timestamp}] [STAGE: {stage}] [CATEGORY: {category}] [CONTEXT: {error_ctx}] "
        f"[TOOL: {tool_name}] [SESSION: {session_id[:8] if session_id else 'none'}]\n"
        f"ERROR: {clean_msg}\n"
        f"--------------------------------------------------------------------------------\n"
    )
    try:
        err_log = cwd / ".pipeline_errors.log"
        with open(err_log, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to write to .pipeline_errors.log: {e}\n")

    return {}


def handle_permission_request(payload, user_local_file=None):
    """Evaluate ``permissionRequest`` for read-only tools, ``web_fetch``, and ``.venv`` shell calls.

    Auto-approves read-only discovery tools, ``web_fetch`` calls targeting
    domains in the 3-layer Approved Research Sources Registry, and strictly
    allowlisted Cleanroom-validated workspace ``.venv`` / skill shell commands.
    Returns ``{}`` for sandbox-bypass requests, unlisted URLs, or arbitrary
    shell commands so VS Code displays its native interactive confirmation card.

    Args:
        payload: Hook stdin JSON dictionary.
        user_local_file: Optional override path for ``approved_sources.local.json``.

    Returns:
        ``{"behavior": "allow"}`` when auto-approved, or ``{}`` to fall
        through to VS Code's standard permission flow.
    """
    cwd = get_payload_cwd(payload)
    tool_name = payload.get("toolName") or payload.get("tool_name") or ""
    tool_input = payload.get("toolInput") or payload.get("toolArgs") or payload.get("tool_input") or {}

    # Honor sandbox-bypass exception: never auto-approve sandbox escapes.
    if isinstance(tool_input, dict) and tool_input.get("requestSandboxBypass"):
        return {}

    safe_read_tools = {"read", "view", "grep", "glob", "search", "todo", "runTests"}
    # Return early when `tool_name in safe_read_tools`.
    if tool_name in safe_read_tools:
        return {"behavior": "allow"}

    if tool_name in ("web_fetch", "web_search") and isinstance(tool_input, dict):
        cfg_perm_w = load_pipeline_config(cwd)
        if not cfg_perm_w.get("allow_web_research", True):
            return {}
        if tool_name == "web_fetch":
            url = tool_input.get("url", "")
            sources = load_approved_research_sources(cwd, user_local_file=user_local_file)
            if is_approved_research_url(url, sources["domains"]):
                return {"behavior": "allow"}
        return {}

    # Auto-approve workspace-confined specialist file creates/edits that pass full preToolUse confinement & write-lock checks.
    if tool_name in ("create", "edit", "str_replace_editor", "apply_patch") and isinstance(tool_input, dict):
        raw_fp = str(tool_input.get("path") or tool_input.get("file_path") or tool_input.get("filePath") or "").strip()
        if not raw_fp:
            return {}
        active_agent_f, eff_sid_f = _resolve_active_worker_for_permission_request(payload)
        if active_agent_f not in ("Scribe", "Researcher", "Formalist", "Engineer", "Auditor"):
            return {}
        try:
            resolved_fp = (Path(raw_fp) if Path(raw_fp).is_absolute() else (cwd / raw_fp)).resolve()
            if not resolved_fp.is_relative_to(cwd.resolve()):
                return {}
        except Exception:
            return {}
        diff_txt = str(tool_input.get("diff") or "")
        is_new_create = (tool_name == "create") or ("--- a/dev/null" in diff_txt) or (not resolved_fp.exists())
        if is_new_create:
            ft_val = str(tool_input.get("file_text") or "")
            if not ft_val and diff_txt:
                ft_val = "\n".join(
                    ln[1:] for ln in diff_txt.splitlines() if ln.startswith("+") and not ln.startswith("+++")
                )
            sim_tname = "create"
            sim_targs = {"path": str(resolved_fp), "file_text": ft_val or "# draft\n"}
        else:
            sim_tname = "edit"
            sim_targs = {
                "path": str(resolved_fp),
                "old_str": str(tool_input.get("old_str") or "a"),
                "new_str": str(tool_input.get("new_str") or "b"),
            }
        pre_f_chk = handle_pre_tool({
            "cwd": str(cwd),
            "sessionId": eff_sid_f,
            "agentId": str(payload.get("agentId") or payload.get("agent_id") or ""),
            "agentType": active_agent_f,
            "toolName": sim_tname,
            "toolArgs": sim_targs,
            "_dry_run_permission_check": True,
        })
        if pre_f_chk.get("permissionDecision") != "deny":
            return {"behavior": "allow"}
        return {}

    if tool_name in ("bash", "powershell", "Bash") and isinstance(tool_input, dict):
        cmd = str(tool_input.get("command") or tool_input.get("fullCommandText") or "").strip()
        if not cmd:
            return {}
        active_agent, eff_perm_sid = _resolve_active_worker_for_permission_request(payload)
        if active_agent not in ("Scribe", "Engineer", "Auditor"):
            return {}
        # Always forbid command substitution / parent-directory traversal anywhere (including inside double quotes).
        if any(tok in cmd for tok in ("`", "$(", "..")):
            return {}
        # Mask quoted string bodies (including backslash-continued multiline python -c strings) so Python semicolons/newlines
        # inside quotes are allowed while unquoted shell chaining/redirection (`;`, `||`, `|`, `>`, `<`) is rejected.
        unquoted_cmd = re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", '""', cmd, flags=re.DOTALL)
        if any(tok in unquoted_cmd for tok in (";", "||", "|", ">", "<", "\n", "\r")):
            return {}
        # Must pass full preToolUse Cleanroom & write-lock inspection first
        pre_chk = handle_pre_tool({
            "cwd": str(cwd),
            "sessionId": eff_perm_sid,
            "agentType": active_agent,
            "toolName": "bash",
            "toolArgs": {"command": cmd},
            "_dry_run_permission_check": True,
        })
        if pre_chk.get("permissionDecision") == "deny":
            return {}
        if active_agent == "Scribe":
            return {"behavior": "allow"}

        segments = [seg.strip() for seg in cmd.split("&&") if seg.strip()]
        if not segments:
            return {}

        def _is_safe_worker_segment(seg: str, role: str) -> bool:
            if role == "Engineer":
                if re.match(r"^(?:python3?|(?:\./)?\.venv/bin/python3?)\s+-m\s+venv\s+(?:\./)?\.venv$", seg):
                    return True
                if re.match(r"^(?:\./)?\.venv/bin/(?:python3?\s+-m\s+)?pip\s+install\s+(?:-[qU]+\s+)*-r\s+requirements\.txt$", seg):
                    return True
                # Extract matched fields when the text fits this expected pattern.
                if re.match(r"^(?:\./)?\.venv/bin/python3?\s+-c\s+.+$", seg, re.DOTALL):
                    unsafe_py = ("subprocess", "os.system", "shutil.rmtree", ".unlink(", "urllib", "socket", "http.client", "write_text", "write_bytes")
                    return not any(u in seg for u in unsafe_py) and not bool(re.search(r"open\s*\([^)]*['\"][wa+]", seg))
                if re.match(r"^(?:python3?|(?:\./)?\.venv/bin/python3?)\s+\S*implementation-engineering/scripts/code_patch\.py\s+(?:inspect|replace-symbol|replace-lines)\b.+$", seg):
                    return True
                if re.match(r"^(?:\./)?\.venv/bin/(?:python3?\s+-m\s+)?(?:ruff|mypy|py_compile)\b.+$", seg):
                    return True
                return False
            # Auditor
            env_pref = r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"(?:\\.|[^\"\\])*\"|'[^']*'|[^\s\"']+)+\s+)*"
            if re.match(rf"^{env_pref}(?:\./)?\.venv/bin/(?:python3?\s+-m\s+)?pytest\b.+$", seg):
                return "-o " not in seg and "--override-ini" not in seg
            if re.match(r"^(?:\./)?\.venv/bin/python3?\s+-c\s+.+$", seg, re.DOTALL):
                unsafe_aud_py = ("subprocess", "os.system", "shutil.rmtree", ".unlink(", "urllib", "socket", "http.client", "write_text", "write_bytes")
                return not any(u in seg for u in unsafe_aud_py) and not bool(re.search(r"open\s*\([^)]*['\"][wa+]", seg))
            if re.match(r"^(?:\./)?\.venv/bin/(?:python3?\s+-m\s+)?(?:unittest|py_compile|ruff|mypy)\b.+$", seg):
                return True
            return False

        if all(_is_safe_worker_segment(s, active_agent) for s in segments):
            return {"behavior": "allow"}
        return {}

    return {}


def handle_post_failure(payload):
    r"""Track tool failure counts, restore staged backups, and emit recovery hints.

    Args:
        payload: Hook stdin JSON dictionary from ``postToolUseFailure``.

    Returns:
        Dictionary with ``additionalContext`` recovery guidance, or ``{}`` for
        benign non-pipeline tools.
    """
    # Increment per-tool failure counters, restore `.rollback_*` backups on failed `create` calls, and return targeted recovery guidance.
    tool_name = payload.get("toolName") or payload.get("tool_name") or ""
    tool_args = payload.get("toolArgs") or payload.get("tool_input")

    if tool_name in BENIGN_TOOLS or tool_name == "rename_chat":
        return {}

    session_id = get_session_id(payload)
    state = load_session_state(session_id)
    if tool_name in ("web_fetch", "web_search"):
        state["last_web_fetch_sig"] = None
        cwd_wf_fail = get_payload_cwd(payload)
        failed_u = str((tool_args or {}).get("url") or (tool_args or {}).get("query") or "").strip() if isinstance(tool_args, dict) else ""
        if failed_u and (cwd_wf_fail / SCRATCH_DIR_NAME).is_dir():
            def _drop_failed_wf(rec: dict[str, object]) -> None:
                urls = [u for u in (rec.get("web_grounding_urls") or []) if u != failed_u]  # type: ignore[union-attr]
                rec["web_grounding_urls"] = urls
            update_stage_receipts(cwd_wf_fail, _drop_failed_wf)
    # Record Stage 4 pytest failure so `two_phase_post_pytest` Cleanroom policy unlocks post-failure defect triage.
    if tool_name in ("bash", "powershell", "execute", "Bash") and isinstance(tool_args, dict):
        if "pytest" in str(tool_args.get("command") or ""):
            state["stage4_pytest_failed"] = True
            cwd_pf = get_payload_cwd(payload)
            if (cwd_pf / FSM_FILE_NAME).is_file():
                fsm_pf = load_pipeline_fsm(cwd_pf)
                if fsm_pf.get("current_stage") == "STAGE_4_VERIFICATION":
                    fsm_pf["stage4_pytest_failed"] = True
                    save_pipeline_fsm(cwd_pf, fsm_pf)
                    update_stage_receipts(cwd_pf, lambda r: r.__setitem__("stage4_pytest_failed", True))
    failures_by_tool = state.get("failures_by_tool", {})
    count = failures_by_tool.get(tool_name, 0) + 1
    failures_by_tool[tool_name] = count
    state["failures_by_tool"] = failures_by_tool
    state["last_failed_tool"] = tool_name
    state["last_failed_args"] = tool_args
    save_session_state(session_id, state)

    if tool_name in ["task", "agent"]:
        advice = (
            f"<failure_recovery>\n[ERROR] SUBAGENT DELEGATION FAILED: Tool '{tool_name}' failed ({count} failure(s)).\n"
            "Verify all required parameters: 'agent_type' (Researcher/Scribe/Formalist/Engineer/Auditor), "
            "'name' (string), 'description' (3-5 word concise UI intent string, REQUIRED), and 'prompt' (detailed task instructions).\n"
            "Ensure you emit a valid structured tool call rather than markdown text.\n</failure_recovery>"
        )
    elif tool_name == "create":
        if isinstance(tool_args, dict):
            raw_failed_p = str(tool_args.get("path") or tool_args.get("file_path") or tool_args.get("filePath") or "").strip()
            failed_name = Path(raw_failed_p).name if raw_failed_p else ""
            if failed_name and (failed_name in SPEC_AND_REPORT_FILES or failed_name == "requirements.txt" or failed_name.endswith(".py")):
                backup = get_payload_cwd(payload) / SCRATCH_DIR_NAME / f".rollback_{failed_name}"
                dest = get_payload_cwd(payload) / failed_name
                if backup.is_file() and not dest.exists():
                    try:
                        os.replace(backup, dest)
                    except Exception:
                        pass
                _prune_empty_scratch_dir(get_payload_cwd(payload))
        advice = (
            f"<failure_recovery>\n[ERROR] CREATE FILE FAILED: Tool 'create' failed ({count} failure(s)).\n"
            "Always pass 'path' (full absolute filesystem path) as the FIRST argument before 'file_text'. "
            "Both specification/report artifacts and workspace '*.py' modules support atomic 'create(path, file_text)' overwrites.\n</failure_recovery>"
        )
    elif tool_name in ["edit", "str_replace_editor", "apply_patch"]:
        excerpt_block = ""
        if isinstance(tool_args, dict):
            raw_ep = str(tool_args.get("path") or tool_args.get("file_path") or tool_args.get("filePath") or "").strip()
            raw_old = str(tool_args.get("old_str") or tool_args.get("old_string") or "")
            if raw_ep and raw_old:
                cand_py = (Path(raw_ep) if Path(raw_ep).is_absolute() else (get_payload_cwd(payload) / raw_ep)).resolve()
                ok_ws, _ = check_path_in_working_set(
                    cand_py,
                    compute_stage_working_set(
                        get_payload_cwd(payload),
                        active_agent=str(state.get("active_agent") or ""),
                    ),
                )
                if ok_ws:
                    exc_info = extract_on_disk_symbol_excerpt(cand_py, raw_old)
                    if exc_info is not None:
                        sym_n, s_ln, e_ln, exc_txt = exc_info
                        excerpt_block = (
                            f"\n<on_disk_symbol_excerpt symbol=\"{sym_n}\" lines=\"{s_ln}..{e_ln}\">\n"
                            f"{exc_txt}\n"
                            "</on_disk_symbol_excerpt>"
                        )
        advice = (
            f"<failure_recovery>\n[ERROR] EDIT FAILED: Tool '{tool_name}' failed ({count} failure(s)).\n"
            "1. Always pass 'path' (full absolute path) as the FIRST argument, followed by exact 'old_str' and 'new_str'.\n"
            "2. Copy 'old_str' verbatim from '<on_disk_symbol_excerpt>' below (when provided) or re-read via 'view(path=\"<absolute_path>\", view_range=[start, end])' so 'old_str' matches the exact current on-disk lines (use 'create(path, file_text)' only when authoring a brand-new deliverable or per-plate OCR file).\n"
            f"3. For '*.py' modules, you may also run './.venv/bin/python .github/skills/implementation-engineering/scripts/code_patch.py replace-symbol <file.py> <symbol> --stdin'.{excerpt_block}\n</failure_recovery>"
        )
    elif tool_name in ["view", "read"]:
        advice = (
            f"<failure_recovery>\n[ERROR] FILE READ FAILED: Tool '{tool_name}' failed ({count} failure(s)).\n"
            "File not found, path not absolute, or line range out of bounds. Consult '<workspace_map>' for exact in-scope file paths "
            "(external literature when allow_web_research=true must be retrieved via 'web_fetch' on Approved Research Domains).\n</failure_recovery>"
        )
    elif tool_name == "web":
        advice = (
            "<failure_recovery>\n[ERROR] INVALID TOOL NAME 'web': The runtime HTTP fetch tool is named 'web_fetch'.\n"
            "Invoke 'web_fetch(url=\"https://...\")' targeting one of the approved research domains injected in <subagent_context>.\n</failure_recovery>"
        )
    elif tool_name in ["execute", "bash", "powershell", "Bash"]:
        err_msg = str(tool_args) + " " + str(payload.get("error", ""))
        if any(term in err_msg.lower() for term in ["modulenotfounderror", "no module named", "importerror"]):
            advice = (
                "<failure_recovery>\n[ERROR] MISSING PYTHON DEPENDENCY: A required module was not found in the environment.\n"
                "Ensure a workspace virtual environment exists and dependencies are installed:\n"
                "  python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt\n"
                "Always run scripts and tests using './.venv/bin/python'.\n</failure_recovery>"
            )
        else:
            advice = (
                f"<failure_recovery>\n[ERROR] COMMAND EXECUTION FAILED: Tool '{tool_name}' failed ({count} failure(s)).\n"
                "Inspect stderr and exit status before retrying.\n</failure_recovery>"
            )
    else:
        advice = (
            f"<failure_recovery>\n[ERROR] TOOL FAILED: Tool '{tool_name}' failed ({count} failure(s)).\n"
            "Inspect tool arguments against the tool schema before retrying.\n</failure_recovery>"
        )

    comp_anchor_fail = _consume_compaction_anchor(
        session_id,
        str(payload.get("agentId") or payload.get("agent_id") or "").strip(),
        get_payload_cwd(payload),
    )
    return {"additionalContext": comp_anchor_fail + advice}


# ============================================================================
# Section 2: Checkpoint Handoff Gate, `@Facet` Hypervisor Guard & `@Orchestrator` Coordinator Guard
# Enforces synchronous delegation, single-stage-per-approval checkpoint halting, and hypervisor/coordinator tool boundaries.
# ============================================================================

def checkpoint_handoff_gate(
    payload: dict[str, object],
    state: dict[str, object] | None = None,
    active_agent: str | None = None,
) -> dict[str, object] | None:
    """Enforce sync-only handoffs and checkpoint handoff/worker tool blocking.

    Args:
        payload: Hook stdin JSON dictionary from ``preToolUse``.
        state: Optional preloaded session state dictionary.
        active_agent: Resolved active agent name, if any.

    Returns:
        Denial dictionary when blocked, or ``None`` to continue validation.
    """
    # Enforce synchronous `mode='sync'` subagent calls and block worker mutations or delegations while paused at a human `CHECKPOINT_*` gate.
    cwd = get_payload_cwd(payload)
    tool_name = str(payload.get("toolName") or payload.get("tool_name") or "")
    tool_args = payload.get("toolArgs") or payload.get("tool_input") or {}
    if isinstance(tool_args, str):
        try:
            parsed_ta = json.loads(tool_args)
            if isinstance(parsed_ta, dict):
                tool_args = parsed_ta
        except Exception:
            tool_args = {}

    if active_agent != "Facet" and tool_name in ("task", "agent") and isinstance(tool_args, dict):
        raw_mode = tool_args.get("mode")
        # Reject asynchronous subagent delegations so every stage handoff runs synchronously (`mode='sync'`).
        if raw_mode is not None and str(raw_mode).strip() not in ("", "sync"):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[SYNC HANDOFF REQUIRED]: Subagent delegations via '{tool_name}' must execute synchronously "
                    f"(mode='sync', not {raw_mode!r}) so checkpoints and errors surface immediately; {TERMINAL_DENIAL_CLAUSE}"
                ),
            }

    if not (cwd / FSM_FILE_NAME).is_file():
        return None
    fsm = load_pipeline_fsm(cwd)
    current_stage = str(fsm.get("current_stage", ""))
    if not current_stage.startswith("CHECKPOINT_"):
        return None

    if reconcile_steered_user_commands(payload):
        fsm = load_pipeline_fsm(cwd)
        current_stage = str(fsm.get("current_stage", ""))
        if not current_stage.startswith("CHECKPOINT_"):
            return None

    # Block stage progression or mutation while the current human checkpoint still awaits `/approve`.
    if tool_name in ("task", "agent", "write_agent", "send_message"):
        eff_st = state if isinstance(state, dict) else load_session_state(get_session_id(payload))
        if active_agent == "Facet" or is_depth0_session(payload, eff_st):
            sid_cur = str(get_session_id(payload) or "").strip()
            has_live_tx = bool(
                str(payload.get("transcriptPath") or payload.get("transcript_path") or "").strip()
                or bool(eff_st.get("checkpoint_delivery_pending"))
                or (bool(sid_cur) and (get_copilot_home() / "session-state" / sid_cur / "events.jsonl").is_file())
            )
            already_streamed = bool(_read_current_turn_assistant_text(payload).strip())
            if has_live_tx and not already_streamed:
                cp_reload = render_checkpoint_briefing_block(cwd, fsm)
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "<hypervisor_directive>\n"
                        "HALT TOOL CALLS: Only ONE stage executes per `/approve`. NOTHING from your tool calls has been shown to the user yet (all tool results and denials are 100% invisible to the user until you write your reply). "
                        "NEVER mention hooks, 'preToolUse', 'hypervisor gate', 'blocked', or claim 'The last checkpoint briefing was already presented'. "
                        "Emit ZERO tool calls now and output ONLY the complete summary paragraphs, visual preview, and `> **Stage ...` framed review card below verbatim:\n"
                        "</hypervisor_directive>\n\n"
                        f"{cp_reload}"
                    ),
                }
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "<hypervisor_directive>\n"
                    "HALT TOOL CALLS: Only ONE stage executes per `/approve`, and tool outputs/denials are invisible to the user until written into your reply. "
                    "NEVER mention 'hypervisor gate' or claim the briefing was already presented—emit ZERO tool calls now and output the COMPLETE summary paragraphs, visual preview (equations/code/table), and `> **Stage ...` framed review card from your previous `task` result.\n"
                    "</hypervisor_directive>"
                ),
            }
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"[GATEKEEPER CHECKPOINT ACTIVE]: {current_stage} has NOT been approved yet (any earlier '/approve' applied only to a prior stage); "
                f"'{tool_name}' handoffs and subagent continuations are strictly blocked. "
                f"DO NOT call 'task' or any other tool—output ONLY the stage checkpoint summary ending with "
                f"'{CHECKPOINT_CTA_TEXT}' and HALT immediately; {TERMINAL_DENIAL_CLAUSE}"
            ),
        }

    eff_state = state if isinstance(state, dict) else load_session_state(get_session_id(payload))
    has_sub_identity = bool(
        str(
            payload.get("agentId")
            or payload.get("agent_id")
            or payload.get("parentAgentId")
            or payload.get("parent_agent_id")
            or eff_state.get("parent_session_id")
            or ""
        ).strip()
    )
    is_worker = has_sub_identity and (active_agent not in ("Orchestrator", "Facet"))
    # Block stage progression or mutation while the current human checkpoint still awaits `/approve`.
    if is_worker and tool_name not in (BENIGN_TOOLS | {"view", "read", "read_file", "grep", "glob"}):
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"[GATEKEEPER CHECKPOINT ACTIVE]: Your stage deliverable was accepted and the pipeline entered "
                f"{current_stage}. Emit your Subagent Handshake Receipt now without further non-read tool calls "
                f"('{tool_name}'); {TERMINAL_DENIAL_CLAUSE}"
            ),
        }
    return None


def handle_hypervisor_guard(payload, set_session_flag=True):
    """Enforce read-only tool confinement and pre-flight seal checks on ``@Facet``.

    Restricts ``@Facet`` strictly to ``view`` (single read-only inspection tool)
    and ``task(agent_type="Orchestrator")``, records per-turn seal file reads,
    blocks automatic post-archive re-triggering while ``post_archive_halt_latch``
    is engaged, and verifies cryptographic seals against disk and the
    out-of-workspace Shadow Seal Ledger before launching ``@Orchestrator``.

    Args:
        payload: Hook stdin JSON dictionary from ``preToolUse``.
        set_session_flag: Whether to persist ``is_facet_hypervisor_session`` on
            the session entry (``False`` when validating an unflagged depth-0
            ``task(Orchestrator)`` call from ``pre-tool``).

    Returns:
        Empty dict ``{}``, ``modifiedArgs``, or a permission denial dictionary.
    """
    # Enforce `@Facet`'s strict read-only boundary: allow only seal-file `view` calls and synchronous `task(agent_type="Orchestrator")` after seals verify.
    session_id = get_session_id(payload)
    cwd = get_payload_cwd(payload).resolve()
    tool_name = str(payload.get("toolName") or payload.get("tool_name") or "tool")
    tool_args = payload.get("toolArgs") or payload.get("tool_input") or {}
    # If the host passed `toolArgs` as a raw JSON string, decode it into a dictionary first.
    if isinstance(tool_args, str):
        try:
            parsed_ta = json.loads(tool_args)
            if isinstance(parsed_ta, dict):
                tool_args = parsed_ta
        except Exception:
            tool_args = {}

    state = load_session_state(session_id)
    if set_session_flag:
        state["is_facet_hypervisor_session"] = True

    # Let harmless UI, memory, and session-inspection tools pass straight through.
    if tool_name in BENIGN_TOOLS:
        save_session_state(session_id, state)
        return {}

    cp_gate = checkpoint_handoff_gate(payload, state=state, active_agent="Facet")
    if cp_gate is not None:
        return cp_gate

    # Allow `@Facet` to call `view` ONLY on cryptographic seal/provenance JSON files
    # (`.pipeline_state.json` and `PROVENANCE.json`), denying directory scans or code reads
    # so `@Orchestrator` and `@Scribe` own all workspace and visual discovery.
    if tool_name in ("view", "read", "read_file"):
        raw_p = str(
            (tool_args.get("path") or tool_args.get("filePath") or tool_args.get("file_path") or "")
            if isinstance(tool_args, dict)
            else ""
        ).strip()
        # Require an explicit workspace-contained path argument on `@Facet` read-only `view` calls.
        if not raw_p:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[HYPERVISOR SANDBOX]: Missing 'path' argument in '{tool_name}'. "
                    f"Pass an absolute path inside '{cwd}' (e.g. '{cwd / FSM_FILE_NAME}')."
                ),
            }
        cand_p = Path(raw_p)
        resolved_p = (cand_p if cand_p.is_absolute() else (cwd / cand_p)).resolve()
        try:
            resolved_p.relative_to(cwd)
        except ValueError:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[HYPERVISOR SANDBOX]: Read-only inspection path '{raw_p}' escapes workspace root '{cwd}'."
                ),
            }
        # Block text-only agents from viewing raw image or PDF assets directly instead of `SOURCE_OCR.md`.
        if resolved_p.suffix.lower() in VISUAL_AND_PDF_SUFFIXES:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[MODALITY FIREWALL]: '@Facet' inspects JSON/Markdown seal files only and cannot read raw visual/PDF "
                    f"asset '{resolved_p.name}'. Inspect '{cwd / FSM_FILE_NAME}' via 'view', then invoke 'task(agent_type=\"Orchestrator\", mode=\"sync\", ...)' "
                    "so '@Orchestrator' delegates Stage 0V visual OCR to '@Scribe'."
                ),
            }
        rel_parts = resolved_p.relative_to(cwd).parts
        # Deny the tool call when `HYPERVISOR SEAL-ONLY VIEW CONFINEMENT` policy is violated.
        if resolved_p.is_dir() or (rel_parts and rel_parts[0] in ("examples", "research")):
            user_req_v = str(state.get("last_facet_user_prompt") or "")
            stage_v = (
                str(load_pipeline_fsm(cwd).get("current_stage", "STAGE_0_IDEATION"))
                if (cwd / FSM_FILE_NAME).is_file()
                else "STAGE_0_IDEATION"
            )
            if stage_v == "PRE_RUN_HALT":
                stage_v = resolve_initial_active_stage(cwd, user_request=user_req_v)
            canon_v = render_orchestrator_handoff_brief(cwd, stage_v, user_req_v)
            v_hint = "Do NOT call 'view'; invoke ONLY this exact 'task' call immediately:\n"
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[HYPERVISOR SEAL-ONLY VIEW CONFINEMENT]: '@Facet' inspects seal/provenance files only "
                    f"('{FSM_FILE_NAME}', 'PROVENANCE.json', or 'upstream_seals') and cannot scan directories or inspect "
                    f"'{resolved_p.relative_to(cwd)}' directly ('@Orchestrator' and '@Scribe' perform all folder and image discovery). "
                    f"{v_hint}{format_corrected_orchestrator_call(canon_v)}"
                ),
            }
        viewed = list(state.get("facet_turn_viewed_paths") or [])
        if str(resolved_p) not in viewed:
            viewed.append(str(resolved_p))
        state["facet_turn_viewed_paths"] = viewed
        save_session_state(session_id, state)
        if not cand_p.is_absolute() and isinstance(tool_args, dict) and "path" in tool_args:
            return {"modifiedArgs": dict(tool_args, path=str(resolved_p))}
        return {}

    # Gate `@Facet -> @Orchestrator` delegation: require `agent_type="Orchestrator"`,
    # `mode="sync"`, untampered workspace + `upstream_seals` + Shadow Ledger seals
    # verified deterministically in Python, and the canonical 4-line handoff brief.
    if tool_name in ("task", "agent") and isinstance(tool_args, dict):
        agent_type = str(tool_args.get("agent_type") or tool_args.get("name") or "").strip()
        raw_task_prompt = str(tool_args.get("prompt") or "")
        user_req_source = str(state.get("last_facet_user_prompt") or raw_task_prompt or "")
        stage_hint = (
            str(load_pipeline_fsm(cwd).get("current_stage", "STAGE_0_IDEATION"))
            if (cwd / FSM_FILE_NAME).is_file()
            else "STAGE_0_IDEATION"
        )
        if agent_type != "Orchestrator":
            state["facet_turn_handoff_attempts"] = int(state.get("facet_turn_handoff_attempts") or 0) + 1
            save_session_state(session_id, state)
            canon_brief = render_orchestrator_handoff_brief(cwd, stage_hint, user_req_source)
            view_note = "Do NOT call 'view'; invoke ONLY this exact corrected 'task' call immediately:\n"
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[HYPERVISOR INGRESS BOUNDARY]: @Facet may invoke ONLY 'Orchestrator' internally via 'task' "
                    f"(delegating directly to '{agent_type}' is prohibited). {view_note}"
                    f"{format_corrected_orchestrator_call(canon_brief)}"
                ),
            }
        if reconcile_steered_user_commands(payload):
            state = load_session_state(session_id)

        fsm_path = cwd / FSM_FILE_NAME
        # Halt dispatch immediately when `.pipeline_state.json` or an upstream provenance seal is missing or invalid.
        if not fsm_path.is_file():
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[HYPERVISOR HALT - SEAL VALIDATION FAILED]: Missing pipeline seal state file at '{fsm_path}'. "
                    "Do NOT retry 'task'; report this validation error to the user and halt."
                ),
            }

        fsm = load_pipeline_fsm(cwd)
        if fsm.get("post_archive_halt_latch"):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "[LOOP-BACK HALT ACTIVE]: Stage 5 archival is complete and the pipeline is parked cleanly. "
                    "Automatic re-triggering of '@Orchestrator' in the same cycle is prohibited. "
                    "Output the Topic & Result Summary and Next-Cycle Prompt with zero tool calls, and halt."
                ),
            }

        # Recompute SHA-256 seals across disk artifacts, `upstream_seals`, and the Shadow Seal Ledger deterministically in Python.
        cfg = load_pipeline_config(cwd)
        seal_ok, seal_msg = verify_workspace_cryptographic_seals(cwd, check_archive=False)
        if not seal_ok:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[HYPERVISOR HALT - SEAL VALIDATION FAILED]: {seal_msg} "
                    "Do NOT retry 'task'; surface this validation error to the user and halt."
                ),
            }

        canon_brief = render_orchestrator_handoff_brief(
            cwd, str(fsm.get("current_stage", "STAGE_0_IDEATION")), user_req_source
        )
        raw_mode = str(tool_args.get("mode") or "").strip()
        # Require subagent `task` delegations to run synchronously (`mode='sync'`).
        if raw_mode != "sync":
            state["facet_turn_handoff_attempts"] = int(state.get("facet_turn_handoff_attempts") or 0) + 1
            save_session_state(session_id, state)
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[SYNC HANDOFF REQUIRED]: '@Facet' -> '@Orchestrator' delegation must set mode='sync' "
                    f"(got {tool_args.get('mode')!r}). Do NOT call 'view' again; invoke ONLY this exact corrected 'task' call immediately:\n"
                    f"{format_corrected_orchestrator_call(canon_brief)}"
                ),
            }

        fsm_dirty = False
        if fsm.get("current_stage") == "PRE_RUN_HALT":
            fsm["current_stage"] = resolve_initial_active_stage(
                cwd, cfg, user_request=str(state.get("last_facet_user_prompt") or raw_task_prompt)
            )
            fsm["post_archive_halt_latch"] = False
            fsm_dirty = True
        if fsm.get("last_incident") is not None:
            fsm["last_incident"] = None
            fsm_dirty = True
        if fsm_dirty:
            # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
            save_pipeline_fsm(cwd, fsm)

        state["facet_turn_orchestrator_dispatched"] = True
        state["consecutive_stop_blocks"] = 0
        save_session_state(session_id, state)
        if not has_canonical_orchestrator_brief(raw_task_prompt):
            rewritten_args = dict(tool_args)
            rewritten_args["prompt"] = canon_brief
            if not str(rewritten_args.get("name") or "").strip():
                rewritten_args["name"] = "Orchestrator"
            if not str(rewritten_args.get("description") or "").strip():
                rewritten_args["description"] = STAGE_SPINNER_LABELS.get(str(fsm.get("current_stage") or ""), "Delegating pipeline stage")
            return {"modifiedArgs": rewritten_args}
        return {}

    user_req_ro = str(state.get("last_facet_user_prompt") or "")
    stage_ro = (
        str(load_pipeline_fsm(cwd).get("current_stage", "STAGE_0_IDEATION"))
        if (cwd / FSM_FILE_NAME).is_file()
        else "STAGE_0_IDEATION"
    )
    if stage_ro == "PRE_RUN_HALT":
        stage_ro = resolve_initial_active_stage(cwd, user_request=user_req_ro)
    canon_ro = render_orchestrator_handoff_brief(cwd, stage_ro, user_req_ro)
    ro_hint = "Do NOT call 'view'; invoke ONLY this exact 'task' call immediately:\n"
    return {
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"[HYPERVISOR READ-ONLY CONFINEMENT]: @Facet is restricted exclusively to a single read-only inspection tool "
            f"('view' on seal files) and internal '@Orchestrator' invocation via 'task'. Invoking '{tool_name}' is prohibited "
            f"('@Orchestrator' and '@Scribe' perform all workspace folder, file, and image discovery). "
            f"{ro_hint}{format_corrected_orchestrator_call(canon_ro)}"
        ),
    }


def handle_coordinator_guard(payload):
    """Enforce coordinator privilege separation on ``@Orchestrator``.

    Bound exclusively to ``@Orchestrator`` via ``orchestrator.agent.md``
    frontmatter so ``@Orchestrator`` can expose the full 7-category tool
    ceiling to child subagents in Copilot SDK 1.0.108+ while being blocked from
    invoking worker tools or ``skill`` directly.

    Args:
        payload: Hook stdin JSON dictionary from ``preToolUse``.

    Returns:
        Empty dict ``{}`` for benign session tools, or a permission denial dict.
    """
    # Block `@Orchestrator` from directly editing files, running `bash`, calling `web_fetch`, or loading worker skills so all stage work goes to specialists.
    tool_name = payload.get("toolName") or payload.get("tool_name") or "tool"
    if tool_name in BENIGN_TOOLS:
        return {}
    if tool_name == "skill":
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "[COORDINATOR PRIVILEGE SEPARATION]: @Orchestrator must not load worker skills ('formal-mapping', "
                "'technical-perception', 'implementation-engineering', 'verification-auditing') directly via 'skill'. "
                "Delegate the stage task to the designated specialist subagent via 'task'."
            )
        }
    return {
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"[COORDINATOR PRIVILEGE SEPARATION]: @Orchestrator is strictly a pipeline coordinator and cannot "
            f"directly invoke worker tool '{tool_name}'. Use read-only inspection ('view', 'grep', 'glob'), 'sql', or "
            "delegate execution/OCR/research/formalization to the designated specialist subagent via 'task'."
        )
    }


# ============================================================================
# Section 3: `handle_pre_tool` — Modality Firewall, Cleanroom Confinement, Scoped-Edit Auto-Snap & `task` Routing
# Intercepts tool calls prior to execution to enforce modality isolation, symmetrical Cleanroom locks, format-symmetric `edit` snapping, and canonical `task` routing.
# ============================================================================

def handle_pre_tool(payload):
    """Enforce pre-tool security, confinement, and stage gating rules.

    Phases:
      - Phase 1: Identical-Failure Loop Guard, Web Isolation & Modality Firewall
      - Phase 2: File Mutation Confinement, Write-Locks, Rollback Staging & `old_str` Auto-Snap
      - Phase 3: Shell Whitelists, Smoke-Probe Budgets & Working-Set Confinement
      - Phase 4: Subagent Delegation Gatekeeper & FSM Sequence Enforcement
      - Phase 5: Dynamic Python Path Rewriting & Stage 4 Stochastic `pytest` Floor

    Args:
        payload: Hook stdin JSON dictionary from ``preToolUse``.

    Returns:
        Dictionary with ``permissionDecision``/``permissionDecisionReason``,
        ``modifiedArgs``, or ``{}``.
    """
    session_id = get_session_id(payload)
    cwd = get_payload_cwd(payload)
    tool_name = payload.get("toolName") or payload.get("tool_name") or ""
    tool_args = payload.get("toolArgs") or payload.get("tool_input") or {}
    # If the host passed `toolArgs` as a raw JSON string, decode it into a dictionary first.
    if isinstance(tool_args, str):
        try:
            parsed_ta = json.loads(tool_args)
            if isinstance(parsed_ta, dict):
                tool_args = parsed_ta
        except Exception:
            pass

    state = load_session_state(session_id)
    depth0 = is_depth0_session(payload, state)
    if depth0:
        # At depth 0 route strictly on the Facet session flag and ignore any stale active_agent (Design A1).
        if state.get("is_facet_hypervisor_session"):
            return handle_hypervisor_guard(payload)
        if tool_name in ("view", "read", "read_file") and isinstance(tool_args, dict) and (cwd / FSM_FILE_NAME).is_file():
            raw_vp = str(tool_args.get("path") or tool_args.get("filePath") or tool_args.get("file_path") or "").strip()
            if raw_vp:
                cand_vp = Path(raw_vp)
                res_vp = (cand_vp if cand_vp.is_absolute() else (cwd / cand_vp)).resolve()
                try:
                    res_vp.relative_to(cwd.resolve())
                    viewed = list(state.get("facet_turn_viewed_paths") or [])
                    if str(res_vp) not in viewed:
                        viewed.append(str(res_vp))
                        state["facet_turn_viewed_paths"] = viewed
                        save_session_state(session_id, state)
                except ValueError:
                    pass
        if tool_name in ("task", "agent") and isinstance(tool_args, dict):
            req_agent = str(tool_args.get("agent_type") or tool_args.get("name") or "").strip()
            if req_agent == "Orchestrator":
                return handle_hypervisor_guard(payload, set_session_flag=False)
            # Block direct root-level launches of worker specialists when bypassing `@Facet` and `@Orchestrator`.
            if req_agent in ALLOWED_SUBAGENTS:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"[HYPERVISOR INGRESS REQUIRED]: Direct depth-0 launch of specialist '@{req_agent}' is prohibited. "
                        "Start via '@Facet' (or invoke 'task(agent_type=\"Orchestrator\", mode=\"sync\", ...)' "
                        f"after viewing '.pipeline_state.json'); {TERMINAL_DENIAL_CLAUSE}"
                    ),
                }
        active_agent = None
    else:
        # Resolve active subagent from payload fields first, then child/parent session state.
        active_agent = (
            payload.get("agentType")
            or payload.get("agent_type")
            or payload.get("agentName")
            or payload.get("agent_name")
            or state.get("active_agent")
        )
        if active_agent == "Facet":
            return handle_hypervisor_guard(payload)

    cp_gate = checkpoint_handoff_gate(payload, state=state, active_agent=active_agent)
    if cp_gate is not None:
        return cp_gate

    if active_agent in ALLOWED_SUBAGENTS and (cwd / FSM_FILE_NAME).is_file():
        fsm_turn = load_pipeline_fsm(cwd)
        cur_turn_stg = str(fsm_turn.get("current_stage") or "")
        child_aid_t = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
        launch_stg = str(
            (load_session_state(child_aid_t).get("subagent_launch_stage") if child_aid_t else None)
            or state.get("subagent_launch_stage")
            or ""
        )
        # Deny the tool call when `STAGE TRANSITION COMPLETE` policy is violated.
        if (
            launch_stg
            and cur_turn_stg != launch_stg
            and not cur_turn_stg.startswith("CHECKPOINT_")
            and active_agent not in STAGE_SPECIALIST_ALLOWLIST.get(cur_turn_stg, set())
        ):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[STAGE TRANSITION COMPLETE]: Your {launch_stg} deliverable passed validation and the pipeline has already "
                    f"advanced to {cur_turn_stg}. Do NOT invoke any more tools — immediately emit your final Subagent Handshake Receipt "
                    "text to return control to @Orchestrator."
                ),
            }
        child_afc = bool(load_session_state(child_aid_t).get("audit_feedback_counted")) if child_aid_t else False
        if active_agent == "Auditor" and cur_turn_stg == "STAGE_4_VERIFICATION" and (child_afc or bool(state.get("audit_feedback_counted"))) and fsm_turn.get("pending_rewind"):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "[AUDIT REWIND RECORDED]: 'AUDIT_FEEDBACK.json' has already been validated and queued for upstream rewind. "
                    "Do NOT invoke any more tools — immediately emit your final Subagent Handshake Receipt text to return control to @Orchestrator."
                ),
            }
        # Confine shell execution during local specification critique (`STAGE_1_CRITIC` allows single read-only `python -c` toy-vector probes; `STAGE_2_CRITIC` is static-only).
        if active_agent == "Auditor" and cur_turn_stg in ("STAGE_1_CRITIC", "STAGE_2_CRITIC") and tool_name in ("execute", "bash", "powershell", "Bash"):
            raw_critic_cmd = str((tool_args or {}).get("command") or "").strip() if isinstance(tool_args, dict) else ""
            unquoted_critic = re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", '""', raw_critic_cmd, flags=re.DOTALL)
            unsafe_critic_py = ("subprocess", "os.system", "shutil.rmtree", ".unlink(", "urllib", "socket", "http.client", "write_text", "write_bytes")
            is_allowed_s1_toy_probe = (
                cur_turn_stg == "STAGE_1_CRITIC"
                and tool_name in ("execute", "bash", "Bash")
                and bool(re.match(r"^(?:python3?|(?:\./)?\.venv/bin/python3?)\s+-c\s+.+$", raw_critic_cmd, re.DOTALL))
                and not any(tok in raw_critic_cmd for tok in ("`", "$(", ".."))
                and not any(tok in unquoted_critic for tok in (";", "||", "&&", "|", ">", "<", "\n", "\r"))
                and not any(u in raw_critic_cmd for u in unsafe_critic_py)
                and not bool(re.search(r"open\s*\([^)]*['\"][wa+]", raw_critic_cmd))
            )
            if not is_allowed_s1_toy_probe:
                s1_probe_hint = (
                    "Only a single read-only './.venv/bin/python -c \"import numpy as np; ...\"' toy-vector expression check is permitted in STAGE_1_CRITIC (no shell chaining, redirection, file mutation, or pytest); "
                    if cur_turn_stg == "STAGE_1_CRITIC"
                    else f"During {cur_turn_stg}, @Auditor performs static semantic review of the specification JSON only; "
                )
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"[CAPABILITY CONFINEMENT]: {s1_probe_hint}"
                        "write 'LOCAL_CRITIC.json' via 'create' (full pytest suites belong exclusively to STAGE_4_VERIFICATION)."
                    ),
                }

    if not depth0 and active_agent == "Orchestrator" and tool_name in (
        "create", "edit", "str_replace_editor", "apply_patch",
        "bash", "read_bash", "stop_bash", "list_bash", "powershell",
        "web_fetch", "web_search", "skill", "rename_chat",
    ):
        return handle_coordinator_guard(payload)

    failures_by_tool = state.get("failures_by_tool", {})
    edit_failures = failures_by_tool.get(tool_name, 0)

    # Helper function `_norm_arg_path`: Return resolved absolute path string from tool arguments, or ''.
    def _norm_arg_path(args_dict):
        """Return resolved absolute path string from tool arguments, or ''."""
        if not isinstance(args_dict, dict):
            return ""
        raw_p = str(args_dict.get("path") or args_dict.get("file_path") or args_dict.get("filePath") or "").strip()
        if not raw_p:
            return ""
        p_obj = Path(raw_p)
        return str((p_obj if p_obj.is_absolute() else (cwd / p_obj)).resolve())

    # --- Phase 1: Identical-Failure Loop Guard, Web Isolation & Modality Firewall ---
    cur_norm_path = _norm_arg_path(tool_args)
    prev_failed_args = state.get("last_failed_args")
    prev_norm_path = _norm_arg_path(prev_failed_args)
    if (
        edit_failures > 0
        and tool_name == state.get("last_failed_tool")
        and tool_name in ["edit", "str_replace_editor"]
        and isinstance(tool_args, dict)
        and isinstance(prev_failed_args, dict)
        and cur_norm_path != ""
        and cur_norm_path == prev_norm_path
        and tool_args.get("old_str") == prev_failed_args.get("old_str")
        and tool_args.get("old_str") is not None
    ):
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "[LOOP GUARD]: You attempted an edit on the same file with the exact same 'old_str' that just failed. "
                f"Either rewrite the module atomically via 'create(path=\"{cur_norm_path}\", file_text=\"...\")', "
                "inspect with 'view', or use 'code_patch.py inspect / replace-symbol / replace-lines'."
            )
        }

    # 1b. Enforce Stage 1 Markov RESEARCH.md isolation, closed-book mode, and duplicate web_fetch/web_search guard
    if tool_name in ("web_fetch", "web_search") and isinstance(tool_args, dict):
        if active_agent == "Researcher" and _is_markovian_stage1_with_research_md(cwd):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "[STAGE 1 MARKOV ISOLATION]: Approved 'RESEARCH.md' (pre-hydrated in context) is the sole canonical upstream "
                    f"source for 'manifest.json'. Do NOT call '{tool_name}' in Stage 1; compile 'manifest.json' directly from 'RESEARCH.md'."
                ),
            }
        cfg_wf = load_pipeline_config(cwd)
        if not cfg_wf.get("allow_web_research", True):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[CLOSED-BOOK MODE — WEB_FETCH DISABLED]: '.pipeline_config.json' sets 'allow_web_research: false'. "
                    f"Do NOT call '{tool_name}'. Extract mathematical formulations, constraints, and 'target_interface' strictly from "
                    "the user prompt, 'SOURCE_TEXT.md', 'SOURCE_OCR.md', and 'research/*' (set 'source_reference.doi_or_url' to 'prompt://user_request' "
                    "when no external URL is provided in the prompt)."
                ),
            }
        wf_url = str(tool_args.get("url") or tool_args.get("query") or "").strip()
        rewritten_wf_url = canonicalize_wikipedia_fetch_url(wf_url) if tool_name == "web_fetch" and wf_url else wf_url
        eff_wf_url = rewritten_wf_url or wf_url
        if eff_wf_url:
            wf_sig = f"{tool_name}|{eff_wf_url}|{tool_args.get('start_index', 0)}|{tool_args.get('max_length', 5000)}|{bool(tool_args.get('raw', False))}"
            if state.get("last_web_fetch_sig") == wf_sig:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"[LOOP GUARD]: Identical '{tool_name}' call already executed for '{eff_wf_url}' with the same pagination arguments. "
                        "Use the content already returned (if it returned '#REDIRECT [[Target]]', query the MediaWiki API with '&redirects=1' "
                        "or fetch the target title; if truncated, pass an incremented 'start_index' or larger 'max_length')."
                    ),
                }
            state["last_web_fetch_sig"] = wf_sig
            update_session_state(session_id, lambda st: st.__setitem__("last_web_fetch_sig", wf_sig))
            def _rec_wf_pre(rec: dict[str, object]) -> None:
                urls = list(rec.get("web_grounding_urls") or [])  # type: ignore[arg-type]
                if eff_wf_url not in urls:
                    urls.append(eff_wf_url)
                rec["web_grounding_urls"] = urls
            update_stage_receipts(cwd, _rec_wf_pre)
        if tool_name == "web_fetch" and rewritten_wf_url and rewritten_wf_url != wf_url:
            return {"modifiedArgs": dict(tool_args, url=rewritten_wf_url)}

    # Reset failure counter when discovery tools are used, normalize relative view paths, and enforce Modality Firewall
    if tool_name in ["view", "read", "read_file", "grep", "glob", "search"]:
        # Optional `axiomatic_split_and_loop_guard`: mtime-aware duplicate read/search loop guard for specialist subagents.
        if active_agent in ALLOWED_SUBAGENTS and isinstance(tool_args, dict):
            cfg_f6_ro = load_pipeline_config(cwd)
            cg_f6_ro = str(((cfg_f6_ro.get("experiments") or {}) if isinstance(cfg_f6_ro, dict) else {}).get("contract_and_gates") or "standard")
            if cg_f6_ro == "axiomatic_split_and_loop_guard":
                raw_ro_p = str(tool_args.get("path") or tool_args.get("filePath") or tool_args.get("file_path") or "").strip()
                ro_target = (Path(raw_ro_p) if Path(raw_ro_p).is_absolute() else (cwd / raw_ro_p)) if raw_ro_p else cwd
                try:
                    mtime_ns = ro_target.stat().st_mtime_ns if ro_target.exists() else -1
                except Exception:
                    mtime_ns = -1
                ro_sig = f"{tool_name}:{json.dumps(tool_args, sort_keys=True, ensure_ascii=True)}:{mtime_ns}"
                ro_seen = list(state.get("recent_readonly_calls") or [])
                if ro_sig in ro_seen:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[IDEMPOTENT READ-LOOP GUARD]: Identical '{tool_name}' call already executed on unchanged disk state in this turn. "
                            "Use the output already present in your context and proceed to the next step."
                        ),
                    }
                ro_seen.append(ro_sig)
                state["recent_readonly_calls"] = ro_seen[-24:]
                save_session_state(session_id, state)
        mod_view_args = None
        # Cleanroom source-read policy (`auditor_cleanroom_policy`): govern @Auditor Stage 4 reads of `<target_module>.py` implementation bodies.
        if active_agent == "Auditor" and isinstance(tool_args, dict) and (cwd / FSM_FILE_NAME).is_file():
            fsm_f7 = load_pipeline_fsm(cwd)
            if fsm_f7.get("current_stage") == "STAGE_4_VERIFICATION":
                cfg_f7 = load_pipeline_config(cwd)
                f7_pol = str(((cfg_f7.get("experiments") or {}) if isinstance(cfg_f7, dict) else {}).get("auditor_cleanroom_policy") or "unrestricted_read")
                if f7_pol in ("two_phase_post_pytest", "strict_blackbox"):
                    child_aid_f7 = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
                    ch_failed = bool(load_session_state(child_aid_f7).get("stage4_pytest_failed")) if child_aid_f7 else False
                    rec_failed = bool(load_stage_receipts(cwd).get("stage4_pytest_failed"))
                    pytest_failed = bool(fsm_f7.get("stage4_pytest_failed") or state.get("stage4_pytest_failed") or ch_failed or rec_failed)
                    impl_locked = (f7_pol == "strict_blackbox") or (not pytest_failed)
                    if impl_locked:
                        impl_names = {p.name for p in _find_implementation_files(cwd, cfg_f7)}
                        decl_m = _get_declared_target_module(cwd)
                        if decl_m:
                            impl_names.add(decl_m)
                        cand_ro: list[str] = []
                        for rk in ("path", "filePath", "file_path"):
                            if tool_args.get(rk):
                                cand_ro.append(str(tool_args[rk]))
                        p_arg = tool_args.get("paths")
                        if isinstance(p_arg, str) and p_arg:
                            cand_ro.append(p_arg)
                        elif isinstance(p_arg, list):
                            cand_ro.extend(str(x) for x in p_arg if x)
                        blocked_impl = next((Path(c).name for c in cand_ro if Path(c).name in impl_names), None)
                        if blocked_impl:
                            unlock_hint = (
                                "Implementation inspection unlocks only after executing './.venv/bin/python -m pytest verify_<slug>.py' if a test fails."
                                if f7_pol == "two_phase_post_pytest"
                                else "Under strict_blackbox Cleanroom verification, diagnose defects purely from black-box pytest tracebacks, reference models, and formal obligations."
                            )
                            return {
                                "permissionDecision": "deny",
                                "permissionDecisionReason": (
                                    f"[CLEANROOM BLINDNESS ({f7_pol})]: @Auditor cannot read implementation module '{blocked_impl}' during Cleanroom test authoring. "
                                    "Public signatures are provided in '<hydrated_public_surface>' (and 'mapping.json'). "
                                    f"Author 'verify_*.py' strictly from 'manifest.json' and 'mapping.json' and run pytest. {unlock_hint}"
                                ),
                            }
        if active_agent == "Engineer" and isinstance(tool_args, dict):
            eng_ro: list[str] = []
            for rk_e in ("path", "filePath", "file_path", "glob"):
                if tool_args.get(rk_e):
                    eng_ro.append(str(tool_args[rk_e]))
            p_arg_e = tool_args.get("paths")
            if isinstance(p_arg_e, str) and p_arg_e:
                eng_ro.append(p_arg_e)
            elif isinstance(p_arg_e, list):
                eng_ro.extend(str(x) for x in p_arg_e if x)
            cfg_e_ro = load_pipeline_config(cwd)
            blocked_ver = next(
                (Path(c_e).name for c_e in eng_ro if _is_verification_target_name(Path(c_e).name, cfg_e_ro, rel_path=c_e)),
                None,
            )
            if blocked_ver:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"[CLEANROOM BLINDNESS]: @Engineer cannot read @Auditor's Cleanroom verification suite ('{blocked_ver}'). "
                        "Diagnose and repair defects strictly from 'AUDIT_FEEDBACK.json', 'mapping.json', and 'manifest.json'."
                    ),
                }
        if active_agent == "Scribe" and isinstance(tool_args, dict):
            scribe_ro: list[str] = []
            for rk in ("path", "filePath", "file_path", "glob"):
                if tool_args.get(rk):
                    scribe_ro.append(str(tool_args[rk]))
            p_arg_s = tool_args.get("paths")
            if isinstance(p_arg_s, str) and p_arg_s:
                scribe_ro.append(p_arg_s)
            elif isinstance(p_arg_s, list):
                scribe_ro.extend(str(x) for x in p_arg_s if x)
            if any(Path(c).name == "SOURCE_TEXT.md" or "SOURCE_TEXT.md" in c for c in scribe_ro) or (
                tool_name == "grep" and (cwd / "SOURCE_TEXT.md").is_file() and not p_arg_s
            ):
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "[CHANNEL ORTHOGONALITY]: @Scribe transcribes visual elements independently into 'SOURCE_OCR.md' "
                        "and must NEVER read 'SOURCE_TEXT.md'. Inspect staged visual plates inside '.pipeline_scratch/ocr_staging/' via 'view' instead."
                    ),
                }
        if active_agent == "Researcher" and isinstance(tool_args, dict) and _is_markovian_stage1_with_research_md(cwd):
            res_ro: list[str] = []
            for rk in ("path", "filePath", "file_path", "glob"):
                if tool_args.get(rk):
                    res_ro.append(str(tool_args[rk]))
            p_arg_r = tool_args.get("paths")
            if isinstance(p_arg_r, str) and p_arg_r:
                res_ro.append(p_arg_r)
            elif isinstance(p_arg_r, list):
                res_ro.extend(str(x) for x in p_arg_r if x)
            allow_post_ocr = _has_post_research_stage1_ocr(cwd)
            blocked_raw_s1 = False
            for cand_r in res_ro:
                p_r = Path(cand_r)
                resolved_r = (p_r if p_r.is_absolute() else (cwd / p_r)).resolve()
                if p_r.name == "SOURCE_TEXT.md":
                    blocked_raw_s1 = True
                elif p_r.name == "SOURCE_OCR.md" and not allow_post_ocr:
                    blocked_raw_s1 = True
                else:
                    try:
                        resolved_r.relative_to((cwd / "research").resolve())
                        blocked_raw_s1 = True
                    except Exception:
                        pass
            if blocked_raw_s1:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "[STAGE 1 MARKOV ISOLATION]: Approved 'RESEARCH.md' (pre-hydrated in context) contains all upstream "
                        "information for 'manifest.json'. Do NOT re-read raw upstream files ('SOURCE_TEXT.md', 'SOURCE_OCR.md', or 'research/*') "
                        "in Stage 1; compile 'manifest.json' directly from 'RESEARCH.md' and 'schema.json'."
                    ),
                }
        if tool_name in ["view", "read", "read_file"] and isinstance(tool_args, dict):
            raw_view_path = str(tool_args.get("path") or tool_args.get("filePath") or tool_args.get("file_path") or "").strip()
            if raw_view_path and "/.pipeline_scratch/" in raw_view_path and not Path(raw_view_path).exists():
                rel_scr = raw_view_path.split("/.pipeline_scratch/", 1)[1]
                cand_scr = (cwd / SCRATCH_DIR_NAME / rel_scr).resolve()
                if cand_scr.is_relative_to(cwd.resolve()) and cand_scr.exists():
                    raw_view_path = str(cand_scr)
                    for k_vp in ("path", "filePath", "file_path"):
                        if k_vp in tool_args:
                            mod_view_args = dict(tool_args, **{k_vp: raw_view_path})
                            break
            if raw_view_path and Path(raw_view_path).suffix.lower() in VISUAL_AND_PDF_SUFFIXES:
                # Block text-only agents from viewing raw image or PDF assets directly instead of `SOURCE_OCR.md`.
                if active_agent in TEXT_ONLY_SPECIALISTS:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[MODALITY FIREWALL]: @{active_agent} is confined to text/code reasoning and cannot inspect raw "
                            f"visual or PDF assets ('{Path(raw_view_path).name}'). Read 'SOURCE_OCR.md' instead, or conclude "
                            "with Status: CONDITIONAL_PASS requesting @Orchestrator to delegate visual OCR to @Scribe."
                        )
                    }
                if active_agent == "Scribe" or "ocr_staging" in raw_view_path:
                    resolved_vp_obj = (Path(raw_view_path) if Path(raw_view_path).is_absolute() else (cwd / raw_view_path)).resolve()
                    resolved_vp = str(resolved_vp_obj)
                    if active_agent == "Scribe":
                        req_plates_v = _load_required_ocr_plates(cwd)
                        if len(req_plates_v) >= 2 and resolved_vp_obj in req_plates_v:
                            _, comp_v, miss_v = _inspect_source_ocr_plate_coverage(cwd)
                            comp_keys_v = {str(p) for p in comp_v}
                            child_aid_v = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
                            ch_state_v = load_session_state(child_aid_v) if (child_aid_v and child_aid_v != session_id) else None
                            pend_v = str(
                                (ch_state_v.get("pending_untranscribed_ocr_plate") if ch_state_v else None)
                                or state.get("pending_untranscribed_ocr_plate")
                                or ""
                            ).strip()
                            if pend_v and pend_v in comp_keys_v:
                                pend_v = ""
                                state["pending_untranscribed_ocr_plate"] = None
                                if ch_state_v is not None:
                                    ch_state_v["pending_untranscribed_ocr_plate"] = None
                                    save_session_state(child_aid_v, ch_state_v)
                            if pend_v and resolved_vp != pend_v:
                                pend_obj = Path(pend_v)
                                pend_num = (req_plates_v.index(pend_obj) + 1) if pend_obj in req_plates_v else 1
                                pend_md_p = _plate_markdown_path(cwd, pend_obj)
                                write_op_hint = "edit" if pend_md_p.is_file() else "create"
                                save_session_state(session_id, state)
                                return {
                                    "permissionDecision": "deny",
                                    "permissionDecisionReason": (
                                        f"[SINGLE-PLATE OCR GATE]: @Scribe processes strictly 1 visual plate at a time. "
                                        f"You already inspected Plate {pend_num} of {len(req_plates_v)} ('{pend_obj.name}') without persisting its "
                                        f"isolated plate transcript '{pend_md_p}'. "
                                        f"Persist Plate {pend_num} at '{pend_md_p}' via '{write_op_hint}' FIRST before calling 'view' on '{resolved_vp_obj.name}'."
                                    ),
                                }
                            if not pend_v and miss_v and resolved_vp_obj != miss_v[0] and resolved_vp not in comp_keys_v:
                                nxt_v = miss_v[0]
                                nxt_num_v = req_plates_v.index(nxt_v) + 1
                                return {
                                    "permissionDecision": "deny",
                                    "permissionDecisionReason": (
                                        f"[SINGLE-PLATE OCR GATE]: Process staged visual plates strictly 1 at a time in manifest order. "
                                        f"Next untranscribed plate is Plate {nxt_num_v} of {len(req_plates_v)}: call ONLY 'view(path=\"{nxt_v}\")' first."
                                    ),
                                }
                            if resolved_vp not in comp_keys_v:
                                state["pending_untranscribed_ocr_plate"] = resolved_vp
                                if ch_state_v is not None:
                                    ch_state_v["pending_untranscribed_ocr_plate"] = resolved_vp
                                    viewed_ch = list(ch_state_v.get("viewed_ocr_plates") or [])
                                    if resolved_vp not in viewed_ch:
                                        viewed_ch.append(resolved_vp)
                                        ch_state_v["viewed_ocr_plates"] = viewed_ch
                                    save_session_state(child_aid_v, ch_state_v)
                    viewed_plates = list(state.get("viewed_ocr_plates") or [])
                    if resolved_vp not in viewed_plates:
                        viewed_plates.append(resolved_vp)
                        state["viewed_ocr_plates"] = viewed_plates
                    state["uninspected_plate_denials"] = 0
                    save_session_state(session_id, state)
                    if active_agent == "Scribe":
                        child_aid_vp = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
                        if child_aid_vp and child_aid_vp != session_id:
                            ch_vp = load_session_state(child_aid_vp)
                            if ch_vp.get("uninspected_plate_denials"):
                                ch_vp["uninspected_plate_denials"] = 0
                                save_session_state(child_aid_vp, ch_vp)
            if raw_view_path and not Path(raw_view_path).is_absolute() and "path" in tool_args:
                mod_view_args = dict(tool_args, path=str((cwd / raw_view_path).resolve()))
        elif tool_name == "glob" and isinstance(tool_args, dict) and isinstance(tool_args.get("pattern"), str):
            raw_pat = tool_args["pattern"].strip()
            norm_pat = raw_pat
            # Strip any leading workspace root path prefix from the glob pattern.
            for base_cand in filter(None, [str(tool_args.get("paths") or "").strip(), str(cwd), str(cwd.resolve())]):
                prefix = base_cand.rstrip("/") + "/"
                if norm_pat.startswith(prefix):
                    norm_pat = norm_pat[len(prefix):]
                    break
            # Repair regex-style optional quantifiers in glob extensions (e.g. '*.[jp]?g?' -> '*')
            if "]?" in norm_pat or re.search(r"\.[a-zA-Z]\?[a-zA-Z]\?", norm_pat):
                parent_part = norm_pat.rsplit("/", 1)[0] + "/" if "/" in norm_pat else "**/"
                norm_pat = f"{parent_part}*"
            elif norm_pat.startswith("*."):
                norm_pat = f"**/{norm_pat}"
            if norm_pat != raw_pat:
                mod_view_args = dict(tool_args, pattern=norm_pat)
        # Enforce active `(role, stage)` working-set scope and auto-scope unscoped `grep`/`glob` searches.
        if active_agent in (ALLOWED_SUBAGENTS | {"Orchestrator"}) and isinstance(tool_args, dict):
            cfg_ws_ro = load_pipeline_config(cwd)
            fsm_ws_ro = load_pipeline_fsm(cwd) if (cwd / FSM_FILE_NAME).is_file() else {}
            ws_env = compute_stage_working_set(cwd, active_agent, fsm=fsm_ws_ro, cfg=cfg_ws_ro)
            if tool_name in ["view", "read", "read_file"]:
                raw_vp_chk = str(tool_args.get("path") or tool_args.get("filePath") or tool_args.get("file_path") or "").strip()
                if raw_vp_chk:
                    ok_ws, _ = check_path_in_working_set(raw_vp_chk, ws_env)
                    if not ok_ws:
                        return {
                            "permissionDecision": "deny",
                            "permissionDecisionReason": format_working_set_denial(cwd, active_agent, raw_vp_chk, ws_env, cfg=cfg_ws_ro),
                        }
            elif tool_name in ["grep", "glob", "search"]:
                raw_paths_arg = tool_args.get("paths")
                explicit_paths: list[str] = []
                if isinstance(raw_paths_arg, str) and raw_paths_arg.strip():
                    explicit_paths.append(raw_paths_arg.strip())
                elif isinstance(raw_paths_arg, list):
                    explicit_paths.extend(str(x).strip() for x in raw_paths_arg if str(x).strip())
                non_root_explicit = [
                    ep_s for ep_s in explicit_paths
                    if (Path(ep_s) if Path(ep_s).is_absolute() else (cwd / ep_s)).resolve() != cwd.resolve()
                ]
                if non_root_explicit:
                    for ep_s in non_root_explicit:
                        ok_ep, _ = check_path_in_working_set(ep_s, ws_env)
                        if not ok_ep:
                            return {
                                "permissionDecision": "deny",
                                "permissionDecisionReason": format_working_set_denial(cwd, active_agent, ep_s, ws_env, cfg=cfg_ws_ro),
                            }
                elif tool_name in ("grep", "glob"):
                    s_roots = list(ws_env.get("searchable_roots") or [])
                    eff_roots = [p for p in s_roots if Path(p).is_dir()] if tool_name == "glob" else s_roots
                    if eff_roots:
                        base_args = dict(mod_view_args) if isinstance(mod_view_args, dict) else dict(tool_args)
                        base_args["paths"] = [str(p) for p in eff_roots] if len(eff_roots) > 1 else str(eff_roots[0])
                        mod_view_args = base_args
                    elif (cwd / FSM_FILE_NAME).is_file() and not s_roots:
                        return {
                            "permissionDecision": "deny",
                            "permissionDecisionReason": format_working_set_denial(cwd, active_agent, f"{tool_name}(paths=<workspace>)", ws_env, cfg=cfg_ws_ro),
                        }
        if not state.get("unresolved_validation_error"):
            failures_by_tool["edit"] = 0
        failures_by_tool["view"] = 0
        state["failures_by_tool"] = failures_by_tool
        save_session_state(session_id, state)
        if mod_view_args is not None:
            return {"modifiedArgs": mod_view_args}

    # --- Phase 2: File Mutation Confinement, Write-Locks, Rollback Staging & `old_str` Auto-Snap ---
    if tool_name in ["edit", "create", "str_replace_editor", "apply_patch"] and isinstance(tool_args, dict):
        fsm = load_pipeline_fsm(cwd)
        cfg = load_pipeline_config(cwd)
        checkpoints = fsm.get("checkpoints", {})
        current_stage = fsm.get("current_stage", "")

        updated_mut_args = dict(tool_args)
        args_mutated = False

        raw_path = str(
            tool_args.get("path") or tool_args.get("file_path") or tool_args.get("filePath") or ""
        ).strip()
        # Infer unambiguous target path when `create` or single-module `edit` omits `path` (provided old_str & new_str are intact).
        if not raw_path and (tool_name == "create" or (tool_name in ("edit", "str_replace_editor") and active_agent == "Scribe")):
            inferred_p = _infer_missing_create_path(active_agent, current_stage, tool_args, cwd)
            if inferred_p is not None:
                raw_path = str(inferred_p.resolve())
                updated_mut_args["path"] = raw_path
                args_mutated = True
        elif not raw_path and tool_name in ("edit", "str_replace_editor") and active_agent == "Engineer":
            if tool_args.get("old_str") and tool_args.get("new_str") is not None:
                decl_tm = _get_declared_target_module(cwd)
                if decl_tm and (cwd / decl_tm).is_file():
                    raw_path = str((cwd / decl_tm).resolve())
                    updated_mut_args["path"] = raw_path
                    args_mutated = True

        # Reject file-mutation calls that omit the required target `path` parameter (flagging max_output_tokens JSON cutoffs).
        if not raw_path:
            is_trunc_edit = tool_name in ("edit", "str_replace_editor") and ("old_str" not in tool_args or "new_str" not in tool_args)
            trunc_hint = (
                " Your tool JSON was truncated at max_output_tokens because '<|thought|>' was too long and 'path' was not emitted first — keep '<|thought|>' under 10 lines!"
                if is_trunc_edit
                else ""
            )
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[MISSING REQUIRED 'path' PARAMETER]: Tool '{tool_name}' was called without a target 'path'. "
                    f"Always pass '\"path\": \"{cwd}/<filename>\"' as the FIRST argument before 'file_text' or 'old_str'.{trunc_hint}"
                )
            }

        resolved_target = Path(raw_path) if Path(raw_path).is_absolute() else (cwd / raw_path)
        if not Path(raw_path).is_absolute() or "path" not in updated_mut_args:
            updated_mut_args["path"] = str(resolved_target.resolve())
            # Remove alternate `file_path` / `filePath` keys once canonical `path` is populated.
            for alias_k in ("file_path", "filePath"):
                updated_mut_args.pop(alias_k, None)
            args_mutated = True

        target_name = resolved_target.name
        try:
            same_root = resolved_target.parent.resolve() == cwd.resolve()
        except Exception:
            same_root = False

        # A. Control-Plane & User Research Input Immutability Guard
        try:
            in_research_dir = resolved_target.resolve().is_relative_to((cwd / "research").resolve())
        except Exception:
            in_research_dir = False
        # Protect user-supplied files inside `research/` against modification by any subagent.
        if active_agent in ALLOWED_SUBAGENTS and in_research_dir:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "[INPUT SOURCE IMMUTABILITY]: 'research/' contains user-supplied reference documents and is strictly read-only."
                )
            }
        allowed_scribe_plate_img = _match_allowed_scribe_plate_md(cwd, resolved_target) if active_agent == "Scribe" else None
        if active_agent in ALLOWED_SUBAGENTS and (
            target_name in [CONFIG_FILE_NAME, FSM_FILE_NAME, APPROVED_SOURCES_FILENAME, "pipeline-guards.json", "pipeline_guards.py"]
            or "facet_guards" in resolved_target.parts
            or (SCRATCH_DIR_NAME in str(resolved_target) and allowed_scribe_plate_img is None)
        ):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "[CONTROL-PLANE IMMUTABILITY]: Direct mutation of pipeline configuration, FSM state, approved sources, "
                    f"or scratch staging files is strictly prohibited. Pipeline governance is managed externally by hooks; {TERMINAL_DENIAL_CLAUSE}"
                )
            }

        # B. Cryptographic Write-Lock on Deterministic SOURCE_TEXT.md and Approved Workspace-Root Specifications
        if same_root and target_name == "SOURCE_TEXT.md":
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "[WRITE-LOCK VIOLATION]: 'SOURCE_TEXT.md' is a deterministic verbatim PDF text artifact generated "
                    "automatically by prepare_visual_sources.py and cannot be manually created or edited."
                ),
            }
        if (
            ((same_root and target_name in ["manifest.json", "SOURCE_OCR.md"]) or allowed_scribe_plate_img is not None)
            and checkpoints.get("checkpoint_1", {}).get("approved")
        ):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[WRITE-LOCK VIOLATION]: '{target_name}' has been sealed at Checkpoint 1 "
                    "and is cryptographically locked against further modifications."
                )
            }
        # Block edits to `manifest.json`, `SOURCE_OCR.md`, or `mapping.json` after human checkpoint approval seals them.
        if same_root and target_name == "mapping.json" and checkpoints.get("checkpoint_2", {}).get("approved"):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "[WRITE-LOCK VIOLATION]: Mapping Document ('mapping.json') has been approved at Checkpoint 2 "
                    "and is cryptographically sealed against further modifications."
                )
            }

        # C. Strict Role-Scoped Write Confinement Across All Pipeline Subagents
        if active_agent == "Scribe":
            is_valid_scribe_dest = (same_root and target_name == "SOURCE_OCR.md") or (allowed_scribe_plate_img is not None)
            if not is_valid_scribe_dest or current_stage in [
                "STAGE_2_MAPPING", "STAGE_3_IMPLEMENTATION", "STAGE_4_VERIFICATION", "STAGE_5_ARCHIVAL_COMPLETE"
            ]:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"[CAPABILITY CONFINEMENT]: @Scribe is exclusively an upstream Stage 0V Vision/PDF OCR specialist "
                        f"confined to writing 'SOURCE_OCR.md' (single-plate) or '.pipeline_scratch/ocr_staging/plates/<plate_stem>.md' (multi-plate). "
                        f"Writing '{target_name}' is prohibited."
                    )
                }
            staging_dir = cwd / SCRATCH_DIR_NAME / "ocr_staging"
            if staging_dir.is_dir():
                staged_plates = sorted(
                    p for p in staging_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                )
                child_aid = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
                child_viewed = list(load_session_state(child_aid).get("viewed_ocr_plates") or []) if child_aid else []
                viewed_set = set(state.get("viewed_ocr_plates") or []) | set(child_viewed)
                required_plates = _load_required_ocr_plates(cwd)
                if len(required_plates) >= 2 and same_root and target_name == "SOURCE_OCR.md":
                    _, _comp_d, miss_d = _inspect_source_ocr_plate_coverage(cwd)
                    nxt_plate_d = miss_d[0] if miss_d else required_plates[0]
                    nxt_idx_d = required_plates.index(nxt_plate_d) + 1
                    nxt_md_d = _plate_markdown_path(cwd, nxt_plate_d)
                    _ocr_plates_dir(cwd).mkdir(parents=True, exist_ok=True)
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[PER-PLATE OCR ISOLATION REQUIRED]: '{staging_dir}' has {len(required_plates)} staged visual plates. "
                            f"Do NOT write monolithic 'SOURCE_OCR.md' directly — transcribe Plate {nxt_idx_d} of {len(required_plates)} "
                            f"('{nxt_plate_d.name}') into its isolated file via 'create(path=\"{nxt_md_d}\", file_text=\"...\")' "
                            "(after inspecting that plate via 'view'; 'SOURCE_OCR.md' is deterministically assembled once all plates validate)."
                        ),
                    }
                if allowed_scribe_plate_img is not None:
                    _ocr_plates_dir(cwd).mkdir(parents=True, exist_ok=True)
                    p_idx = (required_plates.index(allowed_scribe_plate_img) + 1) if allowed_scribe_plate_img in required_plates else 1
                    if str(allowed_scribe_plate_img) not in viewed_set:
                        prev_uninsp = int(state.get("uninspected_plate_denials") or 0)
                        if not payload.get("_dry_run_permission_check"):
                            state["uninspected_plate_denials"] = prev_uninsp + 1
                            save_session_state(session_id, state)
                            if child_aid and child_aid != session_id:
                                ch_un = load_session_state(child_aid)
                                ch_un["uninspected_plate_denials"] = int(ch_un.get("uninspected_plate_denials") or 0) + 1
                                save_session_state(child_aid, ch_un)
                        esc_prefix = (
                            f"[REPEAT DENIAL #{prev_uninsp + 1} — STOP CALLING '{tool_name.upper()}'!]: "
                            f"You have NOT called 'view' on '{allowed_scribe_plate_img.name}' yet (any text snippet in context is NOT a plate view). "
                            "Emit ZERO 'create'/'edit' calls on your next turn — "
                            if prev_uninsp >= 1
                            else "[UNINSPECTED PLATE TRANSCRIPTION BLOCKED]: "
                        )
                        return {
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                f"{esc_prefix}Inspect Plate {p_idx} of {len(required_plates)} "
                                f"('{allowed_scribe_plate_img.name}') strictly 1 plate at a time via 'view(path=\"{allowed_scribe_plate_img}\")' "
                                f"BEFORE writing '{resolved_target.name}'."
                            ),
                        }
                    if (
                        tool_name == "create"
                        and resolved_target.is_file()
                        and resolved_target.stat().st_size > 0
                        and edit_failures < 1
                        and not state.get("unresolved_validation_error")
                    ):
                        return {
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                f"[SCOPED EDIT REQUIRED — PRESERVE EXISTING ARTIFACT]: Plate {p_idx} transcript '{resolved_target.name}' "
                                f"already exists on disk. Apply an in-place fix via 'edit(path=\"{resolved_target.resolve()}\", old_str=\"...\", new_str=\"...\")' "
                                "or move on to the next untranscribed plate."
                            ),
                        }
                elif tool_name == "create":
                    if required_plates and str(required_plates[0]) not in viewed_set:
                        first_req = required_plates[0]
                        return {
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                f"[PLATE INSPECTION REQUIRED BEFORE SOURCE_OCR.md]: Inspect Plate 1 of {len(required_plates)} "
                                f"strictly 1 plate at a time via 'view(path=\"{first_req}\")' BEFORE creating 'SOURCE_OCR.md'."
                            ),
                        }
                    if staged_plates and not viewed_set:
                        sample_plate = staged_plates[0].resolve()
                        return {
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                "[PLATE INSPECTION REQUIRED BEFORE SOURCE_OCR.md]: Staged visual plate(s) exist in "
                                f"'{staging_dir}' (e.g. '{sample_plate}') and have not been inspected yet. Call "
                                f"'view(path=\"{sample_plate}\")' (1 plate at a time) BEFORE creating 'SOURCE_OCR.md'."
                            ),
                        }

        elif active_agent == "Researcher":
            # Confine `@Researcher` writes to `RESEARCH.md` (Stage 0) and `manifest.json` (Stage 1).
            if not same_root or target_name not in ["RESEARCH.md", "manifest.json"]:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"[CAPABILITY CONFINEMENT]: @Researcher is strictly confined to writing 'RESEARCH.md' (Stage 0) "
                        f"and 'manifest.json' (Stage 1) in the workspace root. Writing '{target_name}' is prohibited."
                    )
                }
            if (
                target_name == "RESEARCH.md"
                and not (cwd / "RESEARCH.md").is_file()
                and bool(cfg.get("allow_web_research", True))
            ):
                receipts_r0 = load_stage_receipts(cwd)
                has_web_ev = bool(receipts_r0.get("web_grounding_urls")) or bool(state.get("last_web_fetch_sig"))
                if not has_web_ev:
                    sources_r0 = load_approved_research_sources(cwd)
                    dom_r0 = ", ".join(sources_r0["domains"][:12])
                    hint_r0 = "; ".join(f"{k} -> {v}" for k, v in sorted(sources_r0["hints"].items()))
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "[OPEN-BOOK WEB GROUNDING REQUIRED]: 'allow_web_research' is enabled, so Stage 0 'RESEARCH.md' "
                            "must be grounded in at least one 'web_fetch(url=...)' call against Approved Research Domains "
                            f"({dom_r0})"
                            + (f" using structured search/lookup endpoints ({hint_r0})" if hint_r0 else "")
                            + " BEFORE creating 'RESEARCH.md'."
                        ),
                    }
            if target_name == "manifest.json" and (cwd / "RESEARCH.md").exists():
                c0 = checkpoints.get("checkpoint_0", {})
                # Hold Stage 1 `manifest.json` creation until `RESEARCH.md` is approved at Checkpoint 0.
                if is_checkpoint_gate_required("checkpoint_0", cfg) and not c0.get("approved"):
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "[GATEKEEPER CHECKPOINT 0]: 'RESEARCH.md' awaits human approval ('/approve') at Checkpoint 0 "
                            f"before 'manifest.json' can be written; {TERMINAL_DENIAL_CLAUSE}"
                        )
                    }

        elif active_agent == "Formalist":
            # Confine `@Formalist` writes strictly to `mapping.json` in the workspace root.
            if not same_root or target_name != "mapping.json":
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"[CAPABILITY CONFINEMENT]: @Formalist is strictly confined to writing 'mapping.json' in the workspace root in Stage 2 "
                        f"(environment packaging in 'requirements.txt' is owned by @Engineer in Stage 3). Writing '{target_name}' is prohibited."
                    )
                }

        elif active_agent == "Engineer":
            try:
                rel_t = str(resolved_target.resolve().relative_to(cwd.resolve())).replace("\\", "/")
            except ValueError:
                rel_t = target_name
            is_smoke_py = target_name.startswith("smoke_") or target_name.endswith("_smoke.py")
            # Prevent `@Engineer` from modifying specification documents or Cleanroom verification test files.
            if target_name in SPEC_AND_REPORT_FILES or is_smoke_py or _is_verification_target_name(target_name, cfg, rel_path=rel_t):
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"[CAPABILITY CONFINEMENT]: @Engineer is strictly prohibited from modifying specification, report, "
                        f"smoke, or verification oracle files ('{target_name}'). Modify production modules or 'requirements.txt' only "
                        "(run smoke checks inline via './.venv/bin/python -c \"...\"')."
                    )
                }
            # Deny the tool call when `TARGET MODULE MISMATCH` policy is violated.
            if same_root and tool_name == "create" and target_name.endswith(".py") and target_name != "__init__.py":
                declared_mod = _get_declared_target_module(cwd)
                # Require Stage 3 code creation to use the exact `public_interface.target_module` filename declared in `mapping.json`.
                if declared_mod and target_name != declared_mod and not (cwd / declared_mod).is_file():
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[TARGET MODULE MISMATCH]: mapping.json declares public_interface.target_module = '{declared_mod}', "
                            f"which does not exist yet on disk. Create '{cwd / declared_mod}' rather than '{target_name}'."
                        )
                    }

        elif active_agent == "Auditor":
            # Confine `@Auditor` to writing `LOCAL_CRITIC.json` during local specification critique stages.
            if current_stage in ("STAGE_1_CRITIC", "STAGE_2_CRITIC"):
                # Confine `@Auditor` to writing `LOCAL_CRITIC.json` during local specification critique stages.
                if not same_root or target_name != "LOCAL_CRITIC.json":
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[CAPABILITY CONFINEMENT]: During {current_stage}, @Auditor is strictly confined to writing 'LOCAL_CRITIC.json' "
                            f"in the workspace root via 'create' (Stage 4 'verify_*' and 'VERIFICATION.md' belong exclusively to STAGE_4_VERIFICATION). "
                            f"Mutating '{target_name}' is prohibited."
                        ),
                    }
            else:
                try:
                    rel_t = str(resolved_target.resolve().relative_to(cwd.resolve())).replace("\\", "/")
                except ValueError:
                    rel_t = target_name
                is_allowed_auditor_target = target_name == "LOCAL_CRITIC.json" or _is_verification_target_name(
                    target_name, cfg, rel_path=rel_t
                )
                # Confine `@Auditor` to writing `LOCAL_CRITIC.json` during local specification critique stages.
                if not is_allowed_auditor_target:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[CAPABILITY CONFINEMENT]: @Auditor is strictly confined to writing verification scripts "
                            f"('verify_*', 'test_*'), 'LOCAL_CRITIC.json', 'VERIFICATION.md', and 'AUDIT_FEEDBACK.json'. Mutating '{target_name}' is prohibited."
                        )
                    }

        # D. RRSI Create-Once, Scoped-Edit Thereafter Enforcement (Artifact-Preserving L0 Sparsity)
        is_repair_py = (
            same_root
            and target_name.endswith(".py")
            and target_name != "__init__.py"
            and (
                _is_verification_target_name(target_name, cfg)
                or int(fsm.get("repair_attempts", 0) or 0) > 0
            )
        )
        # Deny the tool call when `SCOPED EDIT REQUIRED — PRESERVE EXISTING ARTIFACT` policy is violated.
        if (
            same_root
            and tool_name == "create"
            and (target_name in SCOPED_EDIT_DELIVERABLES or is_repair_py)
            and resolved_target.is_file()
            and resolved_target.stat().st_size > 0
            and edit_failures < 1
            and not state.get("unresolved_validation_error")
        ):
            line_cnt = len(resolved_target.read_text(encoding="utf-8", errors="replace").splitlines())
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[SCOPED EDIT REQUIRED — PRESERVE EXISTING ARTIFACT]: '{target_name}' already exists on disk "
                    f"({line_cnt} lines). Do NOT overwrite or regenerate the whole file with 'create'. Inspect the relevant "
                    f"section via 'view' if needed and apply a minimal scoped replacement using "
                    f"'edit(path=\"{resolved_target.resolve()}\", old_str=\"...\", new_str=\"...\")' or 'code_patch.py'."
                ),
            }

        # E. Stage Existing Workspace Draft (Spec/Report, requirements.txt, or allowed *.py) to .rollback_<name> Before create
        if not payload.get("_dry_run_permission_check") and same_root and tool_name == "create" and (
            target_name in SPEC_AND_REPORT_FILES or target_name == "requirements.txt" or target_name.endswith(".py")
        ):
            if resolved_target.is_file():
                stage_rollback_backup(cwd, target_name)

        # F. Transparent multi-stage format-symmetric + Python AST old_str auto-snap on edit
        if (
            tool_name in ["edit", "str_replace_editor"]
            and resolved_target.is_file()
            and isinstance(updated_mut_args.get("old_str"), str)
            and isinstance(updated_mut_args.get("new_str"), str)
        ):
            try:
                disk_text = resolved_target.read_text(encoding="utf-8")
                snapped_with_stage = resolve_edit_pair_with_stage(
                    disk_text,
                    updated_mut_args["old_str"],
                    updated_mut_args["new_str"],
                    is_python=target_name.endswith(".py"),
                )
                if snapped_with_stage is not None:
                    snapped_old, adjusted_new, snap_stage = snapped_with_stage
                    updated_mut_args["old_str"] = snapped_old
                    updated_mut_args["new_str"] = adjusted_new
                    args_mutated = True
                    append_run_telemetry_event(
                        "edit_snap",
                        {"file": str(target_name), "agent": str(active_agent or ""), "snap_stage": snap_stage},
                        session_id=session_id,
                    )
            except Exception:
                pass

        if (
            same_root
            and target_name.endswith(".py")
            and target_name != "__init__.py"
            and not _is_verification_target_name(target_name, cfg)
            and not target_name.startswith(("smoke_", "test_", "verify_", "audit_"))
        ):
            cand_py_src = ""
            if tool_name == "create" and isinstance(updated_mut_args.get("file_text"), str):
                cand_py_src = updated_mut_args["file_text"]
            elif (
                tool_name in ("edit", "str_replace_editor")
                and resolved_target.is_file()
                and isinstance(updated_mut_args.get("old_str"), str)
                and isinstance(updated_mut_args.get("new_str"), str)
            ):
                disk_py = resolved_target.read_text(encoding="utf-8", errors="replace")
                if disk_py.count(updated_mut_args["old_str"]) == 1:
                    cand_py_src = disk_py.replace(updated_mut_args["old_str"], updated_mut_args["new_str"], 1)
            # Enforce `AST-3` before writing Python code: reject loops that exit early on
            # one-sided `if denom <= 0: break` checks when `denom` is a rational divisor
            # (requiring symmetric `abs(denom) <= eps` pole guards instead).
            if cand_py_src:
                pole_errs = detect_rational_pole_sign_clip(cand_py_src)
                if pole_errs:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[AST-3 POLE-GUARD DOMAIN VIOLATION in '{target_name}']: {pole_errs[0]} "
                            "Preserve full caller domain on non-positive intermediate denominators unless guarded by a true branch cut (sqrt/log)."
                        ),
                    }

        # Return `modifiedArgs` when path or command normalization updated the tool arguments.
        if args_mutated:
            return {"modifiedArgs": updated_mut_args}

    # --- Phase 3: Shell Whitelists, Smoke-Probe Budgets & Working-Set Confinement ---
    if tool_name in ["execute", "bash", "powershell", "Bash"] and isinstance(tool_args, dict):
        cmd = str(tool_args.get("command", "")).strip()

        # A0. Strict @Scribe Shell Whitelist: single prepare_visual_sources.py invocation ONLY
        if active_agent == "Scribe":
            forbidden_shell_tokens = [";", "&&", "||", "|", "`", "$(", ">", "<", "\n", "\r"]
            if any(tok in cmd for tok in forbidden_shell_tokens):
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "[CAPABILITY CONFINEMENT]: @Scribe shell execution forbids chaining or redirection operators."
                    )
                }
            # Normalize workspace-space paths BEFORE shlex.split so unquoted '/path/With Spaces/.github/...' succeeds.
            norm_scribe_cmd = rewrite_bash_command_for_workspace(_resolve_user_skill_scripts_in_cmd(cmd, cwd), cwd) or cmd
            try:
                argv = shlex.split(norm_scribe_cmd)
            except ValueError:
                argv = []
            allowed_py_bins = {"python", "python3", "./.venv/bin/python"}
            allowed_flags = {"--pdf", "--html", "--image", "--dir", "--url", "--pages", "--max-pages", "--dpi", "--out-dir", "--append"}
            valid_scribe_cmd = (
                len(argv) >= 3
                and argv[0] in allowed_py_bins
                and Path(argv[1]).name == "prepare_visual_sources.py"
                and "technical-perception/scripts/prepare_visual_sources.py" in argv[1].replace("\\", "/")
            )
            if valid_scribe_cmd:
                idx = 2
                # Repeat step-by-step while `idx < len(argv)`.
                while idx < len(argv):
                    tok = argv[idx]
                    flag_name = tok.split("=", 1)[0]
                    if flag_name not in allowed_flags:
                        valid_scribe_cmd = False
                        break
                    if flag_name == "--out-dir":
                        out_val = tok.split("=", 1)[1] if "=" in tok else (argv[idx + 1] if idx + 1 < len(argv) else "")
                        if not out_val or "ocr_staging" not in out_val:
                            valid_scribe_cmd = False
                            break
                        idx += 1 if "=" in tok else 2
                    elif flag_name == "--append":
                        idx += 1
                    else:
                        if "=" not in tok:
                            if idx + 1 >= len(argv):
                                valid_scribe_cmd = False
                                break
                            idx += 2
                        else:
                            idx += 1

            # Restrict `@Scribe` shell execution strictly to single `prepare_visual_sources.py` staging commands.
            if not valid_scribe_cmd:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "[CAPABILITY CONFINEMENT]: @Scribe shell execution is strictly confined to single invocations of "
                        "'python3 .github/skills/technical-perception/scripts/prepare_visual_sources.py' without shell chaining or redirection."
                    )
                }
            # Return `modifiedArgs` when path or command normalization updated the tool arguments.
            if norm_scribe_cmd != cmd:
                return {"modifiedArgs": dict(tool_args, command=norm_scribe_cmd)}
            return {}

        # Deny shell command execution for text-only specification roles (`@Researcher` and `@Formalist`).
        if active_agent in ["Researcher", "Formalist"]:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": f"[CAPABILITY CONFINEMENT]: @{active_agent} is prohibited from executing shell commands."
            }

        # A. Control-Plane Immutability Guard via Shell
        if active_agent in ALLOWED_SUBAGENTS and any(
            cf in cmd
            for cf in [
                CONFIG_FILE_NAME,
                FSM_FILE_NAME,
                APPROVED_SOURCES_FILENAME,
                "pipeline-guards.json",
                "pipeline_guards.py",
                "pytest_facet_stochastic.py",
                SCRATCH_DIR_NAME,
            ]
        ):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "[CONTROL-PLANE IMMUTABILITY]: Direct shell mutation of pipeline configuration, FSM state, "
                    f"or scratch staging files is strictly prohibited. Pipeline governance is managed externally by hooks; {TERMINAL_DENIAL_CLAUSE}"
                )
            }

        fsm = load_pipeline_fsm(cwd)
        cfg_sh = load_pipeline_config(cwd)
        checkpoints = fsm.get("checkpoints", {})

        # A2. Read-Only Shell Inspection Working-Set Parity: prevent `ls -R examples/`, `find specs/`, `cat README.md`, etc.
        if active_agent in ALLOWED_SUBAGENTS and active_agent != "Orchestrator":
            ws_sh = compute_stage_working_set(
                cwd,
                active_agent=str(active_agent),
                stage=str(fsm.get("current_stage") or ""),
                fsm=fsm,
                cfg=cfg_sh,
            )
            inspect_bins = {
                "ls", "find", "cat", "head", "tail", "less", "more", "nl",
                "rg", "grep", "egrep", "fgrep", "tree", "wc", "stat", "file", "du",
            }
            for raw_seg in re.split(r"(?:&&|\|\||;|\|)", cmd):
                seg = raw_seg.strip()
                if not seg:
                    continue
                try:
                    seg_argv = shlex.split(seg)
                except ValueError:
                    seg_argv = seg.split()
                if not seg_argv:
                    continue
                bin_nm = Path(seg_argv[0]).name
                if bin_nm not in inspect_bins:
                    continue
                has_rec_flag = any(
                    a in ("-R", "-r", "--recursive")
                    or (a.startswith("-") and not a.startswith("--") and "R" in a[1:])
                    for a in seg_argv[1:]
                )
                pos_args = [a for a in seg_argv[1:] if a and not a.startswith("-")]
                # For grep/rg, first positional arg is the pattern; subsequent positional args are paths.
                path_args = pos_args[1:] if bin_nm in ("rg", "grep", "egrep", "fgrep") else (pos_args or ["."])
                for p_arg in path_args:
                    if p_arg in ("/dev/null",) or p_arg.startswith((".venv", "./.venv")) or "/.venv/" in p_arg:
                        continue
                    glob_stem = re.split(r"[*?[]", p_arg, maxsplit=1)[0].rstrip("/") or p_arg
                    cand_sh = (Path(glob_stem) if Path(glob_stem).is_absolute() else (cwd / glob_stem)).resolve()
                    if cand_sh == cwd.resolve():
                        if bin_nm in ("find", "tree") or (bin_nm == "ls" and has_rec_flag):
                            return {
                                "permissionDecision": "deny",
                                "permissionDecisionReason": format_working_set_denial(
                                    cwd, str(active_agent), p_arg, ws_sh, cfg=cfg_sh
                                ),
                            }
                        continue
                    ok_sh_ws, _ = check_path_in_working_set(cand_sh, ws_sh)
                    if not ok_sh_ws:
                        return {
                            "permissionDecision": "deny",
                            "permissionDecisionReason": format_working_set_denial(
                                cwd, str(active_agent), p_arg, ws_sh, cfg=cfg_sh
                            ),
                        }

        # Cleanroom source-read policy (`auditor_cleanroom_policy`): prevent shell source-dumping (`cat`/`head`/`tail`/`inspect.getsource`) of `<target_module>.py`,
        # and prevent `@Auditor` from deleting an existing `verify_*.py` suite via `rm`/`unlink`.
        if active_agent == "Auditor" and re.search(r"\b(?:rm|unlink)\b[^\n;|&]*\bverify_[A-Za-z0-9_]*\.py\b", cmd):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "[EXISTING SUITE PRESERVATION]: Deleting 'verify_*.py' via 'rm'/'unlink' is prohibited. "
                    "Execute './.venv/bin/python -m pytest verify_<slug>.py' or apply a minimal scoped 'edit(path, old_str, new_str)'."
                ),
            }
        if active_agent == "Auditor" and fsm.get("current_stage") == "STAGE_4_VERIFICATION":
            f7_sh = str(((cfg_sh.get("experiments") or {}) if isinstance(cfg_sh, dict) else {}).get("auditor_cleanroom_policy") or "unrestricted_read")
            if f7_sh in ("two_phase_post_pytest", "strict_blackbox"):
                child_aid_sh = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
                ch_sh_fail = bool(load_session_state(child_aid_sh).get("stage4_pytest_failed")) if child_aid_sh else False
                sh_locked = (f7_sh == "strict_blackbox") or not bool(fsm.get("stage4_pytest_failed") or state.get("stage4_pytest_failed") or ch_sh_fail)
                if sh_locked and re.search(r"\b(?:cat|head|tail|less|more|nl)\b|inspect\.getsource|\.read_text\s*\(|open\s*\(", cmd):
                    impl_sh_names = {p.name for p in _find_implementation_files(cwd, cfg_sh)}
                    decl_sh = _get_declared_target_module(cwd)
                    if decl_sh:
                        impl_sh_names.add(decl_sh)
                    hit_impl = next((nm for nm in impl_sh_names if nm in cmd), None)
                    if hit_impl:
                        return {
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                f"[CLEANROOM BLINDNESS ({f7_sh})]: Shell source inspection of implementation module '{hit_impl}' is prohibited. "
                                "Use '<hydrated_public_surface>' and 'mapping.json', and execute black-box tests via './.venv/bin/python -m pytest'."
                            ),
                        }

        # B. Write-Lock via Shell (strip quoted -c '...' / "..." strings before checking '>' so Python '>' comparisons never false-trigger)
        def _strip_quoted_shell_literals(raw_sh: str) -> str:
            out_chars: list[str] = []
            q_ch: str | None = None
            esc = False
            for ch_s in raw_sh:
                if esc:
                    out_chars.append(" " if q_ch else ch_s)
                    esc = False
                    continue
                if ch_s == "\\":
                    esc = True
                    out_chars.append(" " if q_ch else ch_s)
                    continue
                if q_ch is not None:
                    if ch_s == q_ch:
                        q_ch = None
                        out_chars.append(ch_s)
                    else:
                        out_chars.append(" ")
                elif ch_s in ("'", '"'):
                    q_ch = ch_s
                    out_chars.append(ch_s)
                else:
                    out_chars.append(ch_s)
            return "".join(out_chars)

        # Protocol invariant: blank quoted `-c '...'` strings before checking `>` so Python `>=` or `->` expressions are never misread as shell redirection.
        unquoted_cmd = _strip_quoted_shell_literals(cmd)
        shell_mut_ops = (">", "rm ", "mv ", "cp ", "touch ", "tee ", "sed ", "awk ", "perl -i", "truncate ", "code_patch.py")
        py_file_mut_matches = re.findall(
            r"(?:open\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][wa+]|Path\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\.(?:write_text|write_bytes|unlink))",
            cmd,
        )
        has_py_mut = bool(re.search(r"\.(?:write_text|write_bytes)\s*\(|open\s*\([^)]*['\"][wa+]", cmd))
        has_shell_mut = any(op in unquoted_cmd for op in shell_mut_ops) or has_py_mut

        def _extract_mut_file_candidates() -> list[str]:
            cands = list(
                re.findall(
                    r"[A-Za-z0-9_./\-]+\.(?:py|json|md|txt|toml|cfg|ini|yaml|yml|sh|mjs|js)\b",
                    unquoted_cmd,
                )
            )
            for p_tup in py_file_mut_matches:
                for grp_p in p_tup:
                    if grp_p:
                        cands.append(grp_p)
            return [
                c_tok for c_tok in cands
                if not c_tok.startswith((".venv/", "./.venv/")) and "/.venv/" not in c_tok
            ]

        # Cap repetitive ad-hoc python -c triage probes on @Auditor so counterexamples flow promptly into AUDIT_FEEDBACK.json
        if active_agent == "Auditor":
            child_aid_sh = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
            aud_st_sh = load_session_state(child_aid_sh) if child_aid_sh else state
            if "pytest" in unquoted_cmd:
                if not payload.get("_dry_run_permission_check") and aud_st_sh.get("auditor_inline_probes"):
                    aud_st_sh["auditor_inline_probes"] = 0
                    save_session_state(child_aid_sh or session_id, aud_st_sh)
            elif re.search(r"\bpython3?\s+-c\b", unquoted_cmd):
                cur_probes = int(aud_st_sh.get("auditor_inline_probes") or 0)
                if cur_probes >= 3:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "[AUDIT TRIAGE PROBE LIMIT (3/3)]: Inline 'python -c' counterexample triage budget reached. "
                            "Do NOT run further ad-hoc 'python -c' probes — persist your minimal counterexample and root-cause "
                            "diagnosis in 'AUDIT_FEEDBACK.json' via 'create' immediately (or edit 'verify_*.py' and run 'pytest' if fixing a test harness defect)."
                        ),
                    }
                if not payload.get("_dry_run_permission_check"):
                    aud_st_sh["auditor_inline_probes"] = cur_probes + 1
                    save_session_state(child_aid_sh or session_id, aud_st_sh)

        # Symmetrical Cleanroom Execution Isolation & 3/3 Smoke-Probe Budget for @Engineer
        if active_agent == "Engineer":
            if re.search(r"\bpython3?\s+-c\b", unquoted_cmd) and not has_shell_mut:
                eng_aid_sh = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
                eng_st_sh = load_session_state(eng_aid_sh or session_id)
                cur_eng_p = int(eng_st_sh.get("engineer_inline_probes") or 0)
                if cur_eng_p >= 3:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "[ENGINEER SMOKE-PROBE LIMIT (3/3)]: Inline 'python -c' smoke-probe budget reached. "
                            "Do NOT run open-ended REPL exploration in bash. If a locked equation in 'manifest.json' empirically "
                            "violates a defining mathematical/inversion/conservation identity on your toy vector, conclude IMMEDIATELY "
                            "with '- **Status:** FAILED' and '- **Alternative / Limitation Analysis:** MANIFEST_DEFECT: <empirical residual and broken SPEC-k>' "
                            "(or 'MAPPING_DEFECT: ...' if 'mapping.json' omits a required parameter); otherwise apply a targeted 'edit' to '<target_module>.py'."
                        ),
                    }
                if not payload.get("_dry_run_permission_check"):
                    eng_st_sh["engineer_inline_probes"] = cur_eng_p + 1
                    save_session_state(eng_aid_sh or session_id, eng_st_sh)
            ver_cmd_cands = re.findall(r"[A-Za-z0-9_./\-]+\.py\b", unquoted_cmd)
            hit_ver_sh = next(
                (Path(vc).name for vc in ver_cmd_cands if _is_verification_target_name(Path(vc).name, cfg_sh, rel_path=vc)),
                None,
            )
            has_ver_import = bool(re.search(r"\b(?:from|import)\s+(?:verify_|test_)\w+", cmd))
            if hit_ver_sh or has_ver_import or re.search(r"(?:^|\s|-m\s+)pytest\b", unquoted_cmd):
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "[CLEANROOM BLINDNESS]: @Engineer cannot execute or inspect @Auditor's verification suite ('verify_*.py' / 'pytest'). "
                        "Reproduce and smoke-test your implementation fix inline against the counterexample in 'AUDIT_FEEDBACK.json' "
                        "using './.venv/bin/python -c \"from <module> import ...\"', then conclude so @Auditor runs Cleanroom verification."
                    ),
                }

        if has_shell_mut and "SOURCE_TEXT.md" in cmd:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "[WRITE-LOCK VIOLATION]: 'SOURCE_TEXT.md' is a deterministic verbatim extraction artifact "
                    "and cannot be modified via shell commands."
                ),
            }
        # Block edits to `manifest.json`, `SOURCE_OCR.md`, or `mapping.json` after human checkpoint approval seals them.
        if active_agent in ["Engineer", "Auditor"] and has_shell_mut:
            # Block edits to `manifest.json`, `SOURCE_OCR.md`, or `mapping.json` after human checkpoint approval seals them.
            if checkpoints.get("checkpoint_1", {}).get("approved") and any(m in cmd for m in ["manifest.json", "SOURCE_OCR.md"]):
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "[WRITE-LOCK VIOLATION]: Specification Manifest ('manifest.json' / 'SOURCE_OCR.md') has been approved at Checkpoint 1 "
                        "and is cryptographically sealed against further modifications."
                    )
                }
            # Block edits to `manifest.json`, `SOURCE_OCR.md`, or `mapping.json` after human checkpoint approval seals them.
            if checkpoints.get("checkpoint_2", {}).get("approved") and any(m in cmd for m in ["mapping.json"]):
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "[WRITE-LOCK VIOLATION]: Mapping Document ('mapping.json') has been approved at Checkpoint 2 "
                        "and is cryptographically sealed against further modifications."
                    )
                }

        # C. Symmetrical Cleanroom Shell & code_patch.py Confinement (@Engineer vs @Auditor)
        if active_agent == "Engineer" and has_shell_mut:
            # Extract candidate file arguments referenced in mutating shell / code_patch.py commands
            if "code_patch.py" in cmd and any(sub in cmd for sub in ("replace-symbol", "replace-lines")):
                m_cp = re.search(r"code_patch\.py['\"]?\s+(?:replace-symbol|replace-lines)\s+['\"]?([^\s'\"]+)", cmd)
                if m_cp:
                    cp_name = Path(m_cp.group(1)).name
                    # Prevent `@Engineer` from modifying specification documents or Cleanroom verification test files.
                    if cp_name in SPEC_AND_REPORT_FILES or _is_verification_target_name(cp_name, cfg_sh, rel_path=m_cp.group(1)):
                        return {
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                f"[CAPABILITY CONFINEMENT]: @Engineer is barred from mutating verification or specification file '{cp_name}' via code_patch.py."
                            )
                        }
            for tok in _extract_mut_file_candidates():
                t_base = Path(tok).name
                if t_base in ("code_patch.py", "python", "python3"):
                    continue
                if t_base in SPEC_AND_REPORT_FILES or _is_verification_target_name(t_base, cfg_sh, rel_path=tok):
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[CAPABILITY CONFINEMENT]: @Engineer is prohibited from mutating verification or specification file '{t_base}' via shell commands."
                        )
                    }

        if active_agent == "Auditor" and has_shell_mut:
            if "code_patch.py" in cmd and any(sub in cmd for sub in ("replace-symbol", "replace-lines")):
                m_cp_a = re.search(r"code_patch\.py['\"]?\s+(?:replace-symbol|replace-lines)\s+['\"]?([^\s'\"]+)", cmd)
                if m_cp_a:
                    cp_a_name = Path(m_cp_a.group(1)).name
                    if not _is_verification_target_name(cp_a_name, cfg_sh, rel_path=m_cp_a.group(1)):
                        return {
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                f"[CAPABILITY CONFINEMENT]: @Auditor is strictly confined to verification targets. "
                                f"Shell mutation of '{m_cp_a.group(1)}' is prohibited."
                            ),
                        }
            for tok in _extract_mut_file_candidates():
                t_base = Path(tok).name
                if tok == "/dev/null" or t_base in ("null", "code_patch.py", "python", "python3"):
                    continue
                if not _is_verification_target_name(t_base, cfg_sh, rel_path=tok):
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[CAPABILITY CONFINEMENT]: @Auditor is strictly confined to verification targets. "
                            f"Shell mutation of '{tok}' is prohibited."
                        )
                    }

    # --- Phase 4: Subagent Delegation Gatekeeper & FSM Sequence Enforcement ---
    if tool_name in ["task", "agent"] and isinstance(tool_args, dict):
        agent_type = tool_args.get("agent_type") or tool_args.get("name")

        if agent_type not in ALLOWED_SUBAGENTS:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Policy Violation: Delegating to agent '{agent_type}' is disallowed (self-delegation and built-in cloud agents are prohibited). "
                    "You must delegate exclusively via 'task' to one of the 5 local specialist agents: "
                    f"{', '.join(sorted(ALLOWED_SUBAGENTS))}."
                )
            }

        fsm = load_pipeline_fsm(cwd)
        current_stage = fsm.get("current_stage", "")
        if current_stage == "PRE_RUN_HALT":
            # Prevent launching a new `@Orchestrator` run in the same turn after Stage 5 archival sets `post_archive_halt_latch`.
            if fsm.get("post_archive_halt_latch"):
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "[LOOP-BACK HALT ACTIVE]: Pipeline is parked in 'PRE_RUN_HALT' following Stage 5 archival. "
                        "Conclude the cycle and await the next user prompt before starting another run."
                    ),
                }
            fsm["current_stage"] = "STAGE_0_IDEATION"
            # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
            save_pipeline_fsm(cwd, fsm)
            current_stage = "STAGE_0_IDEATION"

        allowed_for_stage = STAGE_SPECIALIST_ALLOWLIST.get(current_stage)
        if allowed_for_stage is not None and agent_type not in allowed_for_stage:
            expected_owners = " or ".join(f"@{r}" for r in sorted(allowed_for_stage))
            primary_owner = sorted(allowed_for_stage)[0]
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[STAGE ROUTING MISMATCH — {current_stage}]: Active stage '{current_stage}' belongs exclusively to "
                    f"{expected_owners} (delegating to '@{agent_type}' is out of sequence). Invoke "
                    f"'task(agent_type=\"{primary_owner}\", name=\"{primary_owner}\", mode=\"sync\", ...)' immediately."
                ),
            }

        # Checkpoint & Stage Confinement Requirements (checkpoint_handoff_gate already blocked active CHECKPOINT_* stages)
        checkpoints = fsm.get("checkpoints", {})
        if agent_type == "Scribe":
            if checkpoints.get("checkpoint_1", {}).get("approved") or current_stage in [
                "STAGE_2_MAPPING",
                "STAGE_3_IMPLEMENTATION",
                "STAGE_4_VERIFICATION",
                "STAGE_5_ARCHIVAL_COMPLETE",
            ]:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "[ROLE CONFINEMENT]: @Scribe is exclusively an upstream Stage 0V Vision/PDF OCR specialist "
                        "('SOURCE_OCR.md') and cannot be invoked in downstream stages. Specialists (@Researcher, @Formalist, "
                        "@Auditor) write their own JSON and Markdown artifacts directly."
                    )
                }

        elif agent_type == "Researcher":
            res_inputs = discover_research_inputs(cwd)
            # Require `@Scribe` to transcribe visual or PDF inputs into `SOURCE_OCR.md` before `@Researcher` runs.
            if res_inputs["ocr_files"] and not (cwd / "SOURCE_OCR.md").is_file():
                preview_ocr = ", ".join(res_inputs["ocr_files"][:5])
                cmd_args_pre = _format_stage_ocr_cmd_args(cwd, list(res_inputs["ocr_files"]))
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"[STAGE 0V OCR PRE-PASS REQUIRED]: Visual/PDF reference document(s) exist ({preview_ocr}) "
                        "and 'SOURCE_OCR.md' has not been generated yet. Delegate via 'task' to @Scribe first "
                        f"(running 'python3 .github/skills/technical-perception/scripts/prepare_visual_sources.py {cmd_args_pre}') "
                        "to transcribe 'SOURCE_OCR.md' before delegating to @Researcher."
                    )
                }
            r_file = cwd / "RESEARCH.md"
            c0 = checkpoints.get("checkpoint_0", {})
            if r_file.exists():
                current_hash = compute_file_sha256(r_file)
                if not c0.get("approved") or c0.get("approved_sha256") != current_hash:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "[GATEKEEPER CHECKPOINT 0]: Research Candidates ('RESEARCH.md') require human review and approval. "
                            "You must present the candidate comparison table to the user in chat and halt for their confirmation ('/approve') before extracting formulas; "
                            f"{TERMINAL_DENIAL_CLAUSE}"
                        )
                    }

        elif agent_type == "Formalist":
            manifest_file = cwd / "manifest.json"
            # Require `manifest.json` to exist on disk before advancing to downstream mapping or verification.
            if not manifest_file.exists():
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Missing Specification Manifest: 'manifest.json' does not exist on disk."
                }
            c1 = checkpoints.get("checkpoint_1", {})
            current_hash = compute_file_sha256(manifest_file)
            if not c1.get("approved") or c1.get("approved_sha256") != current_hash:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"[GATEKEEPER CHECKPOINT 1]: Specification Manifest ('manifest.json', sha256: {current_hash[:8] if current_hash else 'unknown'}) "
                        "has not been approved by the user. You must present the manifest summary to the user in chat and halt for their confirmation ('/approve') before delegating to @Formalist; "
                        f"{TERMINAL_DENIAL_CLAUSE}"
                    )
                }

        elif agent_type == "Engineer":
            mapping_file = cwd / "mapping.json"
            # Require `mapping.json` to exist on disk before delegating to Stage 3 implementation or Stage 4 verification.
            if not mapping_file.exists():
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Missing Mapping Document: 'mapping.json' does not exist on disk."
                }
            c2 = checkpoints.get("checkpoint_2", {})
            current_hash = compute_file_sha256(mapping_file)
            if not c2.get("approved") or c2.get("approved_sha256") != current_hash:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"[GATEKEEPER CHECKPOINT 2]: Mapping Document ('mapping.json', sha256: {current_hash[:8] if current_hash else 'unknown'}) "
                        "has not been approved by the user. You must present the mapping summary to the user in chat and halt for their confirmation ('/approve') before delegating to @Engineer. "
                        f"DO NOT retry this tool call or apologize. Conclude your turn immediately; {TERMINAL_DENIAL_CLAUSE}"
                    )
                }

        elif agent_type == "Auditor":
            if current_stage == "STAGE_1_CRITIC":
                # Require `manifest.json` to exist on disk before delegating to `STAGE_1_CRITIC`.
                if not (cwd / "manifest.json").is_file():
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Missing Specification Manifest: 'manifest.json' does not exist on disk for STAGE_1_CRITIC.",
                    }
            elif current_stage == "STAGE_2_CRITIC":
                # Require `mapping.json` to exist on disk before delegating to `STAGE_2_CRITIC`.
                if not (cwd / "mapping.json").is_file():
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Missing Mapping Document: 'mapping.json' does not exist on disk for STAGE_2_CRITIC.",
                    }
            else:
                mapping_file = cwd / "mapping.json"
                # Require `mapping.json` to exist on disk before delegating to Stage 3 implementation or Stage 4 verification.
                if not mapping_file.exists():
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Missing Mapping Document: 'mapping.json' does not exist on disk."
                    }
                c2 = checkpoints.get("checkpoint_2", {})
                current_hash = compute_file_sha256(mapping_file)
                if not c2.get("approved") or c2.get("approved_sha256") != current_hash:
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[GATEKEEPER CHECKPOINT 2]: Mapping Document ('mapping.json', sha256: {current_hash[:8] if current_hash else 'unknown'}) "
                            "has not been approved by the user. You must present the mapping summary to the user in chat and halt for their confirmation ('/approve') before delegating to @Auditor. "
                            f"DO NOT retry this tool call or apologize. Conclude your turn immediately; {TERMINAL_DENIAL_CLAUSE}"
                        )
                    }

                # Load `.pipeline_config.json` (falling back to `DEFAULT_CONFIG` when absent).
                cfg_aud = load_pipeline_config(cwd)
                declared_tm = _get_declared_target_module(cwd)
                declared_target_missing = declared_tm if (declared_tm and not (cwd / declared_tm).is_file()) else None
                impl_files = _find_implementation_files(cwd, cfg_aud)
                # Prevent entering Stage 4 verification before `@Engineer` has written the Stage 3 implementation module on disk.
                if declared_target_missing or not impl_files:
                    missing_desc = f"Declared target_module '{declared_target_missing}'" if declared_target_missing else "No implementation code file"
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"[SEQUENCE VIOLATION]: {missing_desc} does not exist on disk. "
                            "You must delegate to @Engineer (Stage 3) to implement the algorithm in production source code "
                            "before delegating to @Auditor for Stage 4 verification."
                        )
                    }

        clean_args = dict(tool_args)
        modified = False
        for field in ["model", "reasoning_effort", "context_tier"]:
            if field in clean_args:
                clean_args.pop(field, None)
                modified = True
        if "prompt" in clean_args and isinstance(agent_type, str):
            raw_task_p = str(clean_args.get("prompt") or "")
            aug_task_p = augment_specialist_task_prompt(cwd, agent_type, raw_task_p, fsm)
            if aug_task_p != raw_task_p:
                clean_args["prompt"] = aug_task_p
                modified = True
        # Ensure required Zod fields (`name`, `description`, `mode`) are always populated on `task` delegations.
        if isinstance(agent_type, str) and agent_type.strip():
            if not str(clean_args.get("name") or "").strip():
                clean_args["name"] = agent_type
                modified = True
            if "mode" not in clean_args or not str(clean_args.get("mode") or "").strip():
                clean_args["mode"] = "sync"
                modified = True
            if agent_type == "Researcher":
                norm_desc = (
                    "Drafting manifest"
                    if (checkpoints.get("checkpoint_0", {}).get("approved") or current_stage == "STAGE_1_SPECIFICATION")
                    else "Surveying literature"
                )
            elif agent_type == "Engineer":
                norm_desc = "Refining module" if int(fsm.get("repair_attempts", 0) or 0) > 0 else "Implementing module"
            elif agent_type == "Auditor" and current_stage in ("STAGE_1_CRITIC", "STAGE_2_CRITIC"):
                norm_desc = STAGE_SPINNER_LABELS.get(current_stage, "Auditing spec")
            else:
                norm_desc = SPECIALIST_SPINNER_LABELS.get(agent_type, str(clean_args.get("description") or f"Running {agent_type}"))
            if norm_desc and clean_args.get("description") != norm_desc:
                clean_args["description"] = norm_desc
                modified = True
        # Return `modifiedArgs` when path or command normalization updated the tool arguments.
        if modified:
            return {"modifiedArgs": clean_args}

    # --- Phase 5: Dynamic Python Path Rewriting & Stage 4 Stochastic `pytest` Floor ---
    if tool_name in ["execute", "bash", "Bash"] and isinstance(tool_args, dict):
        cmd = tool_args.get("command", "").strip()
        new_cmd = rewrite_bash_command_for_workspace(cmd, cwd) or cmd
        if (
            active_agent == "Auditor"
            and load_pipeline_fsm(cwd).get("current_stage") == "STAGE_4_VERIFICATION"
            and re.search(r"(?:^|\s|-m\s+)pytest\b", new_cmd)
            and "pytest_facet_stochastic" not in new_cmd
        ):
            plugin_dir_q = shlex.quote(str(HOOKS_SCRIPTS_DIR))
            new_cmd = (
                f"PYTHONPATH={plugin_dir_q}${{PYTHONPATH:+:$PYTHONPATH}} "
                f'PYTEST_ADDOPTS="-p pytest_facet_stochastic ${{PYTEST_ADDOPTS:-}}" '
                f"{new_cmd}"
            )
        if new_cmd and new_cmd != cmd:
            clean_args = dict(tool_args)
            clean_args["command"] = new_cmd
            return {"modifiedArgs": clean_args}

    return {}


# ============================================================================
# Section 4: `handle_validate_edit` — `postToolUse` Formatting, `AST-1..AST-6` Gates, `pytest` Proof & Checkpoint Sealing
# Canonicalizes written JSON/Markdown/Python files inside `<cwd>`, validates cross-stage contracts, records `pytest` execution proof, and seals stage checkpoints.
# ============================================================================

def handle_validate_edit(payload):
    r"""Validate and canonicalize artifacts on ``postToolUse``, attaching any pending post-compaction anchor."""
    res = _handle_validate_edit_inner(payload)
    comp_anchor = _consume_compaction_anchor(
        get_session_id(payload),
        str(payload.get("agentId") or payload.get("agent_id") or "").strip(),
        get_payload_cwd(payload),
    )
    if comp_anchor:
        out = dict(res) if isinstance(res, dict) else {}
        prev_ctx = str(out.get("additionalContext") or "")
        out["additionalContext"] = comp_anchor + prev_ctx
        return out
    return res


def _handle_validate_edit_inner(payload):
    r"""Validate and canonicalize artifacts on ``postToolUse``.

    Phases:
      - Phase 1: Web Byte Sanitization, `pytest` Execution Proof & Stage 0V Plate Staging
      - Phase 2: Python Normalization, `ruff format`, `AST-1..AST-6` & Anti-Leakage Gates
      - Phase 3: RFC 8259 Gemma JSON Canonicalization, Schema Gates & Checkpoint Sealing
      - Phase 4: Per-Plate OCR Assembly, Markdown Citation Gates & `VERIFICATION.md` Sign-Off

    Args:
        payload: Hook stdin JSON dictionary from ``postToolUse``.

    Returns:
        Dictionary with ``modifiedResult``, ``additionalContext``, or ``{}``.
    """
    session_id = get_session_id(payload)
    cwd = get_payload_cwd(payload)
    tool_name = payload.get("toolName") or payload.get("tool_name") or ""
    tool_args = payload.get("toolArgs") or payload.get("tool_input") or {}

    # Reset failure counter for any tool that completed successfully
    if tool_name:
        state = load_session_state(session_id)
        mutated_ro_cache = False
        if tool_name in state.get("failures_by_tool", {}):
            state["failures_by_tool"][tool_name] = 0
            mutated_ro_cache = True
        if tool_name in ("create", "edit", "str_replace_editor", "write_file", "bash", "execute", "Bash") and state.get("recent_readonly_calls"):
            state["recent_readonly_calls"] = []
            mutated_ro_cache = True
        if mutated_ro_cache:
            save_session_state(session_id, state)

    # --- Phase 1: Web Byte Sanitization, `pytest` Execution Proof & Stage 0V Plate Staging ---
    if tool_name in ["web_fetch", "web_search"]:
        raw_result = payload.get("toolResult") or payload.get("result") or ""
        res_type_wf = str(raw_result.get("resultType") or "success").lower() if isinstance(raw_result, dict) else "success"
        if res_type_wf != "failure" and isinstance(tool_args, dict):
            wf_u_done = str(tool_args.get("url") or tool_args.get("query") or "").strip()
            if wf_u_done:
                def _rec_wf_post(rec: dict[str, object]) -> None:
                    urls = list(rec.get("web_grounding_urls") or [])  # type: ignore[arg-type]
                    if wf_u_done not in urls:
                        urls.append(wf_u_done)
                    rec["web_grounding_urls"] = urls
                update_stage_receipts(cwd, _rec_wf_post)
        if isinstance(raw_result, dict):
            raw_text = raw_result.get("textResultForLlm") or raw_result.get("content") or json.dumps(raw_result)
        else:
            raw_text = str(raw_result)
        clean_text = unicodedata.normalize("NFC", raw_text)
        clean_text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", clean_text)
        return {
            "modifiedResult": {
                "resultType": "success",
                "textResultForLlm": clean_text
            }
        }

    # When `@Auditor` runs `pytest` via `bash` and all tests pass with zero failures,
    # record the exact SHA-256 hashes of `verify_*.py` and `<module>.py` in `.pipeline_state.json`
    # as cryptographic proof-of-execution required before `VERIFICATION.md` can be created.
    if tool_name in ["bash", "powershell", "execute", "Bash"]:
        cmd_str = str((tool_args or {}).get("command") or "")
        if "pytest" in cmd_str:
            raw_res = payload.get("toolResult") or payload.get("result") or ""
            res_txt = (
                str(raw_res.get("textResultForLlm") or raw_res.get("stdout") or raw_res.get("content") or "")
                if isinstance(raw_res, dict)
                else str(raw_res)
            )
            res_type = str(raw_res.get("resultType") or "").lower() if isinstance(raw_res, dict) else ""
            has_pass = bool(re.search(r"\b\d+\s+passed\b", res_txt))
            has_fail = bool(re.search(r"\b\d+\s+(?:failed|error|errors)\b|FAILURES|ERRORS|Traceback \(most recent call last\)", res_txt)) or res_type in ("failure", "error")
            if has_fail:
                st_pf_v = load_session_state(session_id)
                st_pf_v["stage4_pytest_failed"] = True
                save_session_state(session_id, st_pf_v)
                if (cwd / FSM_FILE_NAME).is_file():
                    fsm_pf_v = load_pipeline_fsm(cwd)
                    if fsm_pf_v.get("current_stage") == "STAGE_4_VERIFICATION":
                        fsm_pf_v["stage4_pytest_failed"] = True
                        save_pipeline_fsm(cwd, fsm_pf_v)
                        update_stage_receipts(cwd, lambda r: (r.__setitem__("stage4_pytest_failed", True), r.__setitem__("stage4_pytest_runs", int(r.get("stage4_pytest_runs", 0) or 0) + 1)))
            if has_pass and not has_fail:
                # Load `.pipeline_config.json` (falling back to `DEFAULT_CONFIG` when absent).
                cfg_b = load_pipeline_config(cwd)
                v_hashes = {
                    p.name: compute_file_sha256(p)
                    for p in sorted(list(cwd.glob("*.py")) + (list((cwd / "tests").glob("*.py")) if (cwd / "tests").is_dir() else []))
                    if p.is_file() and _is_verification_target_name(p.name, cfg_b, rel_path=str(p.relative_to(cwd)).replace("\\", "/"))
                }
                im_hashes = {p.name: compute_file_sha256(p) for p in _find_implementation_files(cwd, cfg_b) if p.is_file()}
                fsm_b = load_pipeline_fsm(cwd)
                fsm_b["last_verified_pytest"] = {"verify_hashes": v_hashes, "impl_hashes": im_hashes}
                save_pipeline_fsm(cwd, fsm_b)
                if (cwd / FSM_FILE_NAME).is_file() and fsm_b.get("current_stage") == "STAGE_4_VERIFICATION":
                    update_stage_receipts(cwd, lambda r: (r.__setitem__("stage4_pytest_passed", True), r.__setitem__("stage4_pytest_runs", int(r.get("stage4_pytest_runs", 0) or 0) + 1)))
                    return {
                        "additionalContext": (
                            "<verification_ready>\n"
                            "[CLEANROOM PYTEST PASSED]: All verification obligations passed (exit code 0). "
                            "STOP editing 'verify_*.py' (ignore any prior non-blocking advisory quality notes) and proceed IMMEDIATELY "
                            "on your very next turn to authoring 'VERIFICATION.md' via 'create' without calling 'view' on the implementation '.py' module.\n"
                            "</verification_ready>"
                        )
                    }
        elif "prepare_visual_sources.py" in cmd_str:
            child_aid_s = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
            for sid_clr in {session_id, child_aid_s} - {""}:
                st_clr = load_session_state(sid_clr)
                if st_clr.get("viewed_ocr_plates") or st_clr.get("pending_untranscribed_ocr_plate"):
                    st_clr["viewed_ocr_plates"] = []
                    st_clr["pending_untranscribed_ocr_plate"] = None
                    save_session_state(sid_clr, st_clr)
            req_staged = _load_required_ocr_plates(cwd)
            if req_staged:
                _ocr_plates_dir(cwd).mkdir(parents=True, exist_ok=True)
                first_p = req_staged[0]
                tot_m = len(req_staged)
                if tot_m >= 2:
                    first_md_p = _plate_markdown_path(cwd, first_p)
                    return {
                        "additionalContext": (
                            f"<ocr_staging_ready>\n[STAGE 0V — {tot_m} VISUAL PLATES STAGED]: Process STRICTLY 1 plate at a time via isolated per-plate Markdown files.\n"
                            f"1. Call ONLY `view(path=\"{first_p}\")` in your next turn (do NOT batch multiple `view` calls).\n"
                            f"2. Call `create(path=\"{first_md_p}\", file_text=\"...\")` containing the visible prose, `$$ ... $$` LaTeX equations, tables, figures, and any `<uncertain>` / `<illegible/>` tags from `{first_p.name}` ONLY.\n"
                            f"Repeat 1 plate at a time for Plates 2..{tot_m} (`plates/<plate_stem>.md` via `create`; use `edit` only for in-place XML fixes on a plate file). "
                            f"Do NOT create `{cwd / 'SOURCE_OCR.md'}` directly — once all {tot_m}/{tot_m} plate files validate, `{cwd / 'SOURCE_OCR.md'}` is deterministically assembled automatically.\n</ocr_staging_ready>"
                        )
                    }
                return {
                    "additionalContext": (
                        f"<ocr_staging_ready>\n[STAGE 0V — 1 VISUAL PLATE STAGED]: Call `view(path=\"{first_p}\")` next, "
                        f"then write `{cwd / 'SOURCE_OCR.md'}` via `create`.\n</ocr_staging_ready>"
                    )
                }
        return {}

    if tool_name in ("view", "read"):
        raw_vp_post = str((tool_args or {}).get("path") or (tool_args or {}).get("file_path") or "").strip() if isinstance(tool_args, dict) else ""
        if "ocr_staging" not in raw_vp_post:
            return {}
        vp_post = Path(raw_vp_post) if Path(raw_vp_post).is_absolute() else (cwd / raw_vp_post)
        if not vp_post.is_file() or vp_post.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return {}
        req_vp = _load_required_ocr_plates(cwd)
        vp_res = vp_post.resolve()
        dest_md_vp = _plate_markdown_path(cwd, vp_res) if len(req_vp) >= 2 else (cwd / "SOURCE_OCR.md")
        glyph_anc_vp = _format_plate_vector_glyph_anchor(cwd, vp_res)
        vp_idx_lbl = f"Plate {req_vp.index(vp_res) + 1} of {len(req_vp)} (`{vp_res.name}`)" if vp_res in req_vp else f"`{vp_res.name}`"
        return {
            "additionalContext": (
                f"<ocr_plate_viewed>\n[OK] {vp_idx_lbl} inspected — transcribe its visible prose, `$$ ... $$` LaTeX equations, "
                f"tables, figures, and any `<uncertain>` / `<illegible/>` tags into `{dest_md_vp}` via `create` now.{glyph_anc_vp}\n</ocr_plate_viewed>"
            )
        }

    if tool_name not in ["edit", "create", "str_replace_editor", "apply_patch"]:
        return {}

    raw_path = tool_args.get("path") or tool_args.get("file_path") or tool_args.get("filePath")
    if not raw_path:
        return {}

    target_path = Path(raw_path)
    if not target_path.is_absolute():
        target_path = cwd / target_path

    if not target_path.exists():
        return {}

    # Inspect filesystem metadata safely and ignore edits outside the active workspace root (e.g. session-state scratch files).
    try:
        resolved_cwd = cwd.resolve()
        resolved_post_target = target_path.resolve()
        try:
            resolved_post_target.relative_to(resolved_cwd)
        except ValueError:
            return {}
        is_root_artifact = resolved_post_target.parent == resolved_cwd
    except Exception:
        is_root_artifact = False

    # Load `.pipeline_config.json` (falling back to `DEFAULT_CONFIG` when absent).
    cfg_val = load_pipeline_config(cwd)
    static_val = str(cfg_val.get("toolchain", {}).get("static_validator", "py_compile")).strip().lower()

    # --- Phase 2: Python Normalization, `ruff format`, `AST-1..AST-6` & Anti-Leakage Gates ---
    if str(target_path).endswith(".py") and static_val != "none":
        # Normalize CRLF, strip trailing spaces, promote LaTeX backslashed docstrings to raw r"""...""", and hoist misplaced __future__ imports
        try:
            raw_py = target_path.read_text(encoding="utf-8")
            clean_lines = [ln.rstrip(" \t") for ln in raw_py.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
            norm_py = _hoist_misplaced_future_imports(_promote_latex_docstrings_to_raw("\n".join(clean_lines).rstrip("\n") + "\n"))
            if norm_py != raw_py:
                target_path.write_text(norm_py, encoding="utf-8")
        except Exception:
            pass

        # Syntax check via py_compile (with automatic rollback on atomic 'create' overwrite if SyntaxError!)
        res = subprocess.run(
            ["python3", "-m", "py_compile", str(target_path)],
            capture_output=True,
            text=True
        )
        if res.returncode != 0:
            error_msg = res.stderr.strip()
            if is_root_artifact and tool_name == "create":
                had_backup = (cwd / SCRATCH_DIR_NAME / f".rollback_{target_path.name}").is_file()
                restore_or_unlink_invalid_draft(cwd, target_path)
                restored_msg = " (broken draft discarded and prior working module restored)" if had_backup else " (broken draft discarded)"
                return {
                    "additionalContext": (
                        f"<validation_error>\n[ERROR] PYTHON SYNTAX ERROR in {target_path.name}{restored_msg}:\n"
                        f"{error_msg}\n"
                        f"Fix the syntax error and re-write via 'create(path=\"{target_path}\", file_text=\"...\")'.\n</validation_error>"
                    )
                }
            return {
                "additionalContext": (
                    f"<validation_error>\n[ERROR] PYTHON SYNTAX ERROR in {target_path.name}:\n"
                    f"{error_msg}\n"
                    "Please fix this syntax error before continuing.\n</validation_error>"
                )
            }

        if is_root_artifact:
            discard_rollback_backup(cwd, target_path.name)

        # Deterministic PEP 257 docstring ordering, Ruff unused-import cleanup (F401) & formatting on workspace files
        in_test_subdir = target_path.parent.name == "tests" and target_path.parent.parent.resolve() == cwd.resolve()
        if (is_root_artifact or in_test_subdir) and "scripts" not in target_path.parts and "templates" not in target_path.parts:
            try:
                cur_py = target_path.read_text(encoding="utf-8")
                pep257_py = _canonicalize_module_docstring_order(cur_py)
                if pep257_py != cur_py:
                    target_path.write_text(pep257_py, encoding="utf-8")
            except Exception:
                pass
            ruff_candidates = [
                cwd / ".venv" / "bin" / "ruff",
                Path(sys.executable).with_name("ruff"),
                HOOKS_REPO_ROOT / ".venv" / "bin" / "ruff",
            ]
            which_ruff = shutil.which("ruff")
            if which_ruff:
                ruff_candidates.append(Path(which_ruff))
            ruff_bin = next((cand for cand in ruff_candidates if str(cand) != "." and cand.is_file()), Path(""))
            if ruff_bin and str(ruff_bin) != "." and ruff_bin.is_file():
                try:
                    if target_path.name != "__init__.py":
                        # Auto-format the updated Python file with `ruff format` when available in `.venv`.
                        subprocess.run(
                            [str(ruff_bin), "check", "--select", "F401", "--fix", "--quiet", str(target_path)],
                            capture_output=True, text=True, timeout=12
                        )
                    # Auto-format the updated Python file with `ruff format` when available in `.venv`.
                    subprocess.run(
                        [str(ruff_bin), "format", "--quiet", str(target_path)],
                        capture_output=True, text=True, timeout=12
                    )
                except Exception:
                    pass

        try:
            rel_py = str(target_path.resolve().relative_to(cwd.resolve())).replace("\\", "/")
        except ValueError:
            rel_py = target_path.name
        is_verif_py = (
            target_path.name.startswith(("verify_", "test_", "audit_"))
            or target_path.name.endswith("_test.py")
            or _is_verification_target_name(target_path.name, cfg_val, rel_path=rel_py)
        )

        # Stage 4 Verification Script 3-Tier AST & Oracle-Soundness Check
        if (
            is_verif_py
            and not target_path.name.startswith("smoke_")
            and "scripts" not in target_path.parts
            and "templates" not in target_path.parts
        ):
            sound_errs, hyg_warns = validate_verification_script_ast(target_path, cwd=cwd)
            if sound_errs:
                _set_unresolved_validation_error(payload, session_id, f"{target_path.name}: {'; '.join(sound_errs[:2])}")
                excerpt = _format_numbered_remediation_excerpt(target_path)
                extra_notes = ("\nAdditional Hygiene Notes:\n" + "\n".join(f"- {w}" for w in hyg_warns)) if hyg_warns else ""
                return {
                    "additionalContext": (
                        f"<validation_error>\n[ERROR] UNSOUND ORACLE / TOLERANCE DEFECTS in {target_path.name} "
                        "(kept on disk for scoped edit; fix before signing off VERIFICATION.md):\n"
                        + "\n".join(f"- {err}" for err in sound_errs)
                        + extra_notes
                        + (f"\n\n{excerpt}" if excerpt else "")
                        + f"\nApply a minimal scoped 'edit' to '{target_path}' and re-run pytest.\n</validation_error>"
                    )
                }
            _clear_unresolved_validation_error(payload, session_id)
            if hyg_warns:
                return {
                    "additionalContext": (
                        f"<validation_warning>\n[WARNING] VERIFICATION SUITE QUALITY NOTES in {target_path.name}:\n"
                        + "\n".join(f"- {w}" for w in hyg_warns) + "\n"
                        "Consider adding strict '-> None' typing, module docstring, Hypothesis '@given' fuzzing, and 'pytest.raises' checks.\n</validation_warning>"
                    )
                }

        # AST Contract Validation (for production algorithm modules only)
        elif (
            not target_path.name.startswith(("verify_", "test_", "audit_", "smoke_"))
            and not target_path.name.endswith("_test.py")
            and "scripts" not in target_path.parts
            and "templates" not in target_path.parts
            and not target_path.name.startswith("install_")
        ):
            # Run hard Stage 3 Python gates (`AST-4b` entrypoint/parameter-name parity,
            # `M2` hardcoded test-vector leakage, and `AST-2` lexical-scope unbound symbols);
            # if any fail, keep the file on disk and return a numbered excerpt for scoped `edit`.
            ep_errs = validate_target_module_entrypoints(target_path, cwd) if is_root_artifact else []
            leak_errs = detect_anchor_test_leakage(target_path, cwd) if is_root_artifact else []
            lex_errs = find_lexical_unbound_names(target_path) if is_root_artifact else []
            hard_py_errs = ep_errs + leak_errs + lex_errs
            if hard_py_errs:
                _set_unresolved_validation_error(payload, session_id, f"{target_path.name}: {'; '.join(hard_py_errs[:2])}")
                excerpt = _format_numbered_remediation_excerpt(target_path)
                return {
                    "additionalContext": (
                        f"<validation_error>\n[ERROR] INTERFACE OR GENERALIZATION CONTRACT VIOLATION in {target_path.name}:\n"
                        + "\n".join(f"- {err}" for err in hard_py_errs)
                        + (f"\n\n{excerpt}" if excerpt else "")
                        + f"\nApply a minimal scoped 'edit' to '{target_path}' to fix the error(s) above.\n</validation_error>"
                    )
                }
            _clear_unresolved_validation_error(payload, session_id)
            if is_root_artifact:
                eng_aid_rst = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
                for sid_e_rst in {session_id, eng_aid_rst} - {""}:
                    st_e_rst = load_session_state(sid_e_rst)
                    if st_e_rst.get("engineer_inline_probes"):
                        st_e_rst["engineer_inline_probes"] = 0
                        save_session_state(sid_e_rst, st_e_rst)
                fsm_py = load_pipeline_fsm(cwd)
                prw_py = fsm_py.get("pending_rewind")
                if isinstance(prw_py, dict) and prw_py.get("specialist") == "Engineer":
                    fsm_py["pending_rewind"] = None
                    # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
                    save_pipeline_fsm(cwd, fsm_py)
                notify_facet_lens_sse(cwd, "artifact_write", str(fsm_py.get("current_stage") or ""))
            ast_errors = validate_python_ast(target_path)
            if ast_errors:
                clipped_errors = ast_errors[:10]
                return {
                    "additionalContext": (
                        f"<validation_warning>\n[WARNING] CODE QUALITY & CONTRACT WARNINGS in {target_path.name}:\n"
                        + "\n".join(f"- {err}" for err in clipped_errors) + "\n"
                        "Please ensure strict typing, defined helpers, and source equation docstrings per lint_rules.json.\n</validation_warning>"
                    )
                }
            if is_root_artifact and tool_name == "create":
                py_sha = compute_file_sha256(target_path)
                return {
                    "additionalContext": (
                        f"<validation_passed>[OK] {target_path.name} (sha256: {(py_sha or '')[:8]}) passed syntax, PEP 257, "
                        "and AST contract checks on disk. Run any quick smoke check inline via 'bash' "
                        "('./.venv/bin/python -c \"...\"') — DO NOT create 'test_*.py', 'verify_*.py', or 'smoke_*.py' files — "
                        "then conclude with your Subagent Handshake Receipt.</validation_passed>"
                    )
                }

    # --- Phase 3: RFC 8259 Gemma JSON Canonicalization, Schema Gates & Checkpoint Sealing ---
    elif str(target_path).endswith(".json"):
        raw_json_text = target_path.read_text(encoding="utf-8", errors="replace")
        if is_root_artifact and target_path.name in ["manifest.json", "mapping.json", "AUDIT_FEEDBACK.json", "LOCAL_CRITIC.json"]:
            formatted_text, json_data, fmt_err = format_gemma_json_artifact(raw_json_text)
            if fmt_err or json_data is None:
                if tool_name == "create" and (cwd / SCRATCH_DIR_NAME / f".rollback_{target_path.name}").is_file():
                    restore_or_unlink_invalid_draft(cwd, target_path)
                st_je = load_session_state(session_id)
                st_je["unresolved_validation_error"] = f"{target_path.name}: invalid JSON ({fmt_err})"
                save_session_state(session_id, st_je)
                excerpt = _format_numbered_remediation_excerpt(target_path)
                return {
                    "additionalContext": (
                        f"<validation_error>\n[ERROR] INVALID JSON in {target_path.name}:\n{fmt_err}"
                        + (f"\n\n{excerpt}" if excerpt else "")
                        + f"\nRepair '{target_path.name}' via scoped 'edit(path=\"{target_path}\", old_str=\"...\", new_str=\"...\")'.\n</validation_error>"
                    )
                }
            if formatted_text != raw_json_text:
                target_path.write_text(formatted_text, encoding="utf-8")
        else:
            try:
                json_data = json.loads(raw_json_text)
            except Exception as e:
                return {
                    "additionalContext": (
                        f"<validation_error>\n[ERROR] INVALID JSON in {target_path.name}:\n{e}\n"
                        "Please repair the JSON formatting.\n</validation_error>"
                    )
                }
            if not is_root_artifact:
                return {}

        # Pre-commit Schema Validation & FSM Transition Gate (Artifact-Preserving Scoped Edit + Hybrid Local Critic)
        if target_path.name == "manifest.json":
            schema_errs = validate_manifest_structure(json_data, cwd=cwd)
            if schema_errs:
                clipped = schema_errs[:10]
                if tool_name == "create" and (cwd / SCRATCH_DIR_NAME / ".rollback_manifest.json").is_file():
                    restore_or_unlink_invalid_draft(cwd, target_path)
                st_me = load_session_state(session_id)
                st_me["unresolved_validation_error"] = f"manifest.json: {'; '.join(clipped[:2])}"
                save_session_state(session_id, st_me)
                excerpt = _format_numbered_remediation_excerpt(target_path)
                return {
                    "additionalContext": (
                        f"<validation_error>\n[ERROR] INCOMPLETE SPECIFICATION MANIFEST in {target_path.name} (kept on disk for scoped edit):\n"
                        + "\n".join(f"- {e}" for e in clipped)
                        + (f"\n\n{excerpt}" if excerpt else "")
                        + f"\nApply a minimal scoped 'edit(path=\"{target_path}\", old_str=\"...\", new_str=\"...\")' to fix the field(s) above.\n</validation_error>"
                    )
                }
            discard_rollback_backup(cwd, "manifest.json")
            _clear_unresolved_validation_error(payload, session_id)
            sha_val = compute_file_sha256(target_path)
            fsm = load_pipeline_fsm(cwd)
            cfg = load_pipeline_config(cwd)
            c_name = json_data.get("concept_name")
            if c_name:
                fsm["concept_slug"] = slugify(c_name)
            prw_m = fsm.get("pending_rewind")
            if isinstance(prw_m, dict) and prw_m.get("specialist") == "Researcher":
                fsm["pending_rewind"] = None
            if (cwd / "LOCAL_CRITIC.json").is_file():
                (cwd / "LOCAL_CRITIC.json").unlink(missing_ok=True)
            lsc_post_s1 = str(cfg.get("enable_local_spec_critic") or "off")
            s1_critic_eligible = (
                lsc_post_s1 == "s1_and_s2"
                or (
                    lsc_post_s1 == "s1_only"
                    and len(json_data.get("formal_specifications") or []) >= 2
                    and ((cwd / "RESEARCH.md").is_file() or (cwd / "SOURCE_OCR.md").is_file())
                    and int(fsm.get("repair_attempts", 0) or 0) == 0
                )
            ) and int((fsm.get("local_critic_attempts") or {}).get("s1", 0)) < 1
            if s1_critic_eligible:
                fsm["current_stage"] = "STAGE_1_CRITIC"
            else:
                # Record artifact SHA-256 hash and either pause at a human checkpoint or auto-advance.
                apply_artifact_gate(
                    cwd, fsm, "checkpoint_1", sha_val,
                    "CHECKPOINT_1_AWAITING_APPROVAL", "STAGE_2_MAPPING", cfg
                )
            # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
            save_pipeline_fsm(cwd, fsm)
            return {
                "additionalContext": (
                    f"<validation_passed>[OK] manifest.json (sha256: {(sha_val or '')[:8]}) passed schema validation and is persisted on disk. "
                    "Conclude with '### Stage Briefing' (1-2 paragraphs on variable domains/roles, structural constraints vs. epistemic assumptions/ambiguities, and rtol/atol conditioning — do NOT repeat Stage 0 source discovery or emit standalone $$...$$ equations) followed by '## Subagent Handshake Receipt'.</validation_passed>"
                )
            }

        elif target_path.name == "mapping.json":
            # Normalize missing .py suffix on public_interface.target_module automatically (Landing Gate)
            pub_if_d = json_data.get("public_interface")
            if isinstance(pub_if_d, dict) and isinstance(pub_if_d.get("target_module"), str):
                tm_raw = pub_if_d["target_module"].strip()
                if tm_raw and not tm_raw.endswith(".py") and "/" not in tm_raw and ".." not in tm_raw:
                    pub_if_d["target_module"] = f"{tm_raw}.py"
                    target_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            schema_errs = validate_mapping_structure(json_data, cwd)
            if schema_errs:
                clipped = schema_errs[:10]
                if tool_name == "create" and (cwd / SCRATCH_DIR_NAME / ".rollback_mapping.json").is_file():
                    restore_or_unlink_invalid_draft(cwd, target_path)
                st_mpe = load_session_state(session_id)
                st_mpe["unresolved_validation_error"] = f"mapping.json: {'; '.join(clipped[:2])}"
                save_session_state(session_id, st_mpe)
                excerpt = _format_numbered_remediation_excerpt(target_path)
                return {
                    "additionalContext": (
                        f"<validation_error>\n[ERROR] INCOMPLETE MAPPING DOCUMENT in {target_path.name} (kept on disk for scoped edit):\n"
                        + "\n".join(f"- {e}" for e in clipped)
                        + (f"\n\n{excerpt}" if excerpt else "")
                        + f"\nApply a minimal scoped 'edit(path=\"{target_path}\", old_str=\"...\", new_str=\"...\")' or 'code_patch.py replace-json' to fix the field(s) above.\n</validation_error>"
                    )
                }
            discard_rollback_backup(cwd, "mapping.json")
            _clear_unresolved_validation_error(payload, session_id)
            sha_val = compute_file_sha256(target_path)
            fsm = load_pipeline_fsm(cwd)
            cfg = load_pipeline_config(cwd)
            prw_mp = fsm.get("pending_rewind")
            if isinstance(prw_mp, dict) and prw_mp.get("specialist") == "Formalist":
                fsm["pending_rewind"] = None
            if (cwd / "LOCAL_CRITIC.json").is_file():
                (cwd / "LOCAL_CRITIC.json").unlink(missing_ok=True)
            if str(cfg.get("enable_local_spec_critic") or "off") in ("s2_only", "s1_and_s2"):
                fsm["current_stage"] = "STAGE_2_CRITIC"
            else:
                # Record artifact SHA-256 hash and either pause at a human checkpoint or auto-advance.
                apply_artifact_gate(
                    cwd, fsm, "checkpoint_2", sha_val,
                    "CHECKPOINT_2_AWAITING_APPROVAL", "STAGE_3_IMPLEMENTATION", cfg
                )
            # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
            save_pipeline_fsm(cwd, fsm)
            return {
                "additionalContext": (
                    f"<validation_passed>[OK] mapping.json (sha256: {(sha_val or '')[:8]}) passed schema & interface-obligation validation and is persisted on disk. "
                    "Conclude with '### Stage Briefing' (1-2 paragraphs on target_module entrypoints, runtime preconditions vs. assumptions, and 4-predicate OBL-j coverage) followed by '## Subagent Handshake Receipt'.</validation_passed>"
                )
            }

        elif target_path.name == "LOCAL_CRITIC.json":
            fsm = load_pipeline_fsm(cwd)
            cfg = load_pipeline_config(cwd)
            lc_stage = str(json_data.get("stage") or fsm.get("current_stage") or "").strip().upper()
            lc_status = str(json_data.get("status") or json_data.get("overall_status") or "").strip().upper()
            lc_counts = fsm.get("local_critic_attempts")
            if not isinstance(lc_counts, dict):
                lc_counts = {"s1": 0, "s2": 0}
            is_s1 = "STAGE_1" in lc_stage or fsm.get("current_stage") == "STAGE_1_CRITIC"
            slot = "s1" if is_s1 else "s2"
            if lc_status in ("FAIL", "FAILED") and int(lc_counts.get(slot, 0) or 0) < 2:
                lc_counts[slot] = int(lc_counts.get(slot, 0) or 0) + 1
                fsm["local_critic_attempts"] = lc_counts
                fsm["current_stage"] = "STAGE_1_SPECIFICATION" if is_s1 else "STAGE_2_MAPPING"
                # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
                save_pipeline_fsm(cwd, fsm)
                target_role = "Researcher" if is_s1 else "Formalist"
                target_art = "manifest.json" if is_s1 else "mapping.json"
                return {
                    "additionalContext": (
                        f"<failure_diagnostic>\n[LOCAL CRITIC REWIND -> {fsm['current_stage']}]: Semantic issue recorded in LOCAL_CRITIC.json "
                        f"('{target_art}' preserved on disk). @{target_role} will apply a scoped 'edit' and re-verify. "
                        "Do NOT invoke any more tools — immediately conclude with your Subagent Handshake Receipt.\n</failure_diagnostic>"
                    )
                }
            # PASS (or local critic budget exhausted -> advance cleanly)
            target_path.unlink(missing_ok=True)
            if is_s1:
                m_sha = compute_file_sha256(cwd / "manifest.json")
                apply_artifact_gate(cwd, fsm, "checkpoint_1", m_sha, "CHECKPOINT_1_AWAITING_APPROVAL", "STAGE_2_MAPPING", cfg)
            else:
                mp_sha = compute_file_sha256(cwd / "mapping.json")
                apply_artifact_gate(cwd, fsm, "checkpoint_2", mp_sha, "CHECKPOINT_2_AWAITING_APPROVAL", "STAGE_3_IMPLEMENTATION", cfg)
            # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
            save_pipeline_fsm(cwd, fsm)
            return {
                "additionalContext": (
                    f"<validation_passed>[OK] Local semantic specification audit passed; pipeline advanced to {fsm['current_stage']}. "
                    "Do NOT invoke any more tools or run Stage 4 tests — immediately conclude with your Subagent Handshake Receipt.</validation_passed>"
                )
            }

        elif target_path.name == "AUDIT_FEEDBACK.json":
            norm_status, fb_errs = validate_audit_feedback_structure(json_data)
            if fb_errs:
                restore_or_unlink_invalid_draft(cwd, target_path)
                return {
                    "additionalContext": (
                        "<validation_error>\n[ERROR] INVALID AUDIT_FEEDBACK.json (draft discarded):\n"
                        + "\n".join(f"- {e}" for e in fb_errs[:5]) + "\n"
                        f"Re-write AUDIT_FEEDBACK.json conforming to audit_feedback_schema.json via 'create(path=\"{target_path}\", file_text=\"...\")'.\n</validation_error>"
                    )
                }
            target_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            # Preserve cumulative historical_attempts from any staged rollback of AUDIT_FEEDBACK.json before discarding rollback
            rb_fb = cwd / SCRATCH_DIR_NAME / ".rollback_AUDIT_FEEDBACK.json"
            if rb_fb.is_file():
                try:
                    prev_fb = json.loads(rb_fb.read_text(encoding="utf-8"))
                    prev_hist = list(prev_fb.get("historical_attempts") or [])
                    prev_fails = prev_fb.get("failures") if isinstance(prev_fb.get("failures"), list) else []
                    if prev_fails:
                        prev_hist.append(
                            {
                                "attempt_number": len(prev_hist) + 1,
                                "failure_count": len(prev_fails),
                                "summary": "; ".join(
                                    str(x.get("diagnosis") or x.get("description") or "")[:120]
                                    for x in prev_fails[:2]
                                    if isinstance(x, dict)
                                ),
                            }
                        )
                    if prev_hist and not json_data.get("historical_attempts"):
                        json_data["historical_attempts"] = prev_hist
                        target_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                except Exception:
                    pass
            discard_rollback_backup(cwd, "AUDIT_FEEDBACK.json")
            _clear_unresolved_validation_error(payload, session_id)
            if norm_status == "FAIL":
                # Classify audit defects (`MANIFEST` / `MAPPING` / `IMPLEMENTATION` / `TEST_HARNESS`) before counting code/spec rewinds.
                cfg = load_pipeline_config(cwd)
                max_rep = cfg.get("max_repair_attempts", 3)
                fsm = load_pipeline_fsm(cwd)
                state = load_session_state(session_id)
                state["audit_feedback_counted"] = True
                save_session_state(session_id, state)

                prev_attempts = int(fsm.get("repair_attempts", 0) or 0)
                fsm["repair_attempts"] = prev_attempts + 1
                rw_stage, rw_deleted, rw_role = apply_audit_rewind_transition(cwd, fsm, json_data)
                if rw_stage == "STAGE_4_VERIFICATION":
                    # TEST_HARNESS_DEFECT stays in Stage 4 for @Auditor to repair verify_*.py directly without burning an @Engineer repair cycle.
                    fsm["repair_attempts"] = prev_attempts
                    fsm["pending_rewind"] = None
                    save_pipeline_fsm(cwd, fsm)
                    return {
                        "additionalContext": (
                            "<failure_diagnostic>\n[WARNING] TEST HARNESS DEFECT RECORDED (Remaining in STAGE_4_VERIFICATION): "
                            "Apply a minimal scoped 'edit' to 'verify_*.py', re-run './.venv/bin/python -m pytest verify_*.py', "
                            "and then write 'VERIFICATION.md' (or re-classify if an upstream defect remains).\n</failure_diagnostic>"
                        )
                    }
                attempts = int(fsm.get("repair_attempts", 1) or 1)
                if attempts >= max_rep:
                    fsm["current_stage"] = "CHECKPOINT_REPAIR_ESCALATION"
                    fsm["pending_rewind"] = None
                    save_pipeline_fsm(cwd, fsm)
                    return {
                        "additionalContext": (
                            f"<failure_diagnostic>\n[ERROR] REFLEXION CIRCUIT BREAKER TRIPPED: Self-repair attempts ({attempts}/{max_rep}) "
                            "have been exhausted. Halting automated repair loop to prevent infinite token cycles. "
                            "Escalating failure diagnostics in AUDIT_FEEDBACK.json to human supervisor at CHECKPOINT_REPAIR_ESCALATION.\n</failure_diagnostic>"
                        )
                    }
                save_pipeline_fsm(cwd, fsm)
                del_note = f" Invalidated downstream file(s) removed: {', '.join(rw_deleted)} (AUDIT_FEEDBACK.json preserved on disk)." if rw_deleted else ""
                return {
                    "additionalContext": (
                        f"<failure_diagnostic>\n[WARNING] REFLEXION FAILURE DIAGNOSTIC RECORDED (Attempt {attempts}/{max_rep} -> {rw_stage}): "
                        f"Diagnosis persisted in AUDIT_FEEDBACK.json.{del_note} "
                        f"@{rw_role} will resolve the defect and resume the deterministic lifecycle.\n</failure_diagnostic>"
                    )
                }
            if norm_status == "PASS":
                fsm = load_pipeline_fsm(cwd)
                save_pipeline_fsm(cwd, fsm)

    # --- Phase 4: Per-Plate OCR Assembly, Markdown Citation Gates & `VERIFICATION.md` Sign-Off ---
    elif _match_allowed_scribe_plate_md(cwd, target_path) is not None:
        matched_plate_p = _match_allowed_scribe_plate_md(cwd, target_path)
        assert matched_plate_p is not None
        raw_plate = target_path.read_text(encoding="utf-8", errors="replace")
        cleaned_plate = _clean_single_plate_markdown_body(
            sanitize_markdown_artifact_text(raw_plate, normalize_ligatures=True).strip(),
            matched_plate_p.name,
        )
        if len(cleaned_plate) < 12:
            target_path.unlink(missing_ok=True)
            return {
                "additionalContext": (
                    f"<validation_error>\n[ERROR] EMPTY PLATE TRANSCRIPT '{target_path.name}' (draft discarded): "
                    f"Transcribe visible equations, constants, tables, and bounds from '{matched_plate_p.name}' via "
                    f"'create(path=\"{target_path}\", file_text=\"...\")'.\n</validation_error>"
                )
            }
        sanitized_plate, _unc_plate, xml_errs_p = parse_and_normalize_tei_provenance(cleaned_plate)
        if xml_errs_p:
            target_path.write_text(sanitized_plate + "\n", encoding="utf-8")
            _set_unresolved_validation_error(payload, session_id, f"{target_path.name}: {xml_errs_p[0]}")
            excerpt_p = _format_numbered_remediation_excerpt(target_path)
            return {
                "additionalContext": (
                    f"<validation_error>\n[ERROR] MALFORMED TEI P5 XML IN '{target_path.name}' (kept on disk for scoped edit):\n"
                    + "\n".join(f"- {e}" for e in xml_errs_p)
                    + (f"\n\n{excerpt_p}" if excerpt_p else "")
                    + f"\nApply a minimal scoped 'edit(path=\"{target_path}\", old_str=\"...\", new_str=\"...\")' on the offending line(s) above.\n</validation_error>"
                )
            }
        target_path.write_text(sanitized_plate + "\n", encoding="utf-8")
        _clear_unresolved_validation_error(payload, session_id)

        req_ocr_p, comp_ocr_p, miss_ocr_p = _inspect_source_ocr_plate_coverage(cwd)
        comp_ocr_keys = {str(p) for p in comp_ocr_p}
        child_aid_o = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
        for sid_o in {session_id, child_aid_o} - {""}:
            st_o = load_session_state(sid_o)
            pend_o = str(st_o.get("pending_untranscribed_ocr_plate") or "").strip()
            dirty_o = False
            if pend_o and pend_o in comp_ocr_keys:
                st_o["pending_untranscribed_ocr_plate"] = None
                dirty_o = True
            if comp_ocr_p and st_o.get("subagent_stop_blocks"):
                st_o["subagent_stop_blocks"] = 0
                dirty_o = True
            if dirty_o:
                save_session_state(sid_o, st_o)

        cur_plate_num = (req_ocr_p.index(matched_plate_p) + 1) if matched_plate_p in req_ocr_p else len(comp_ocr_p)
        if miss_ocr_p:
            nxt_ocr_p = miss_ocr_p[0]
            nxt_ocr_num = req_ocr_p.index(nxt_ocr_p) + 1
            nxt_md_p = _plate_markdown_path(cwd, nxt_ocr_p)
            return {
                "additionalContext": (
                    f"<ocr_plate_progress>\n[OK] Plate {cur_plate_num} of {len(req_ocr_p)} (`{matched_plate_p.name}`) validated "
                    f"({len(comp_ocr_p)}/{len(req_ocr_p)} visual plates complete; {len(miss_ocr_p)} remaining).\n"
                    "Do NOT conclude or emit your Handshake Receipt yet.\n"
                    "Next step (strictly 1 plate at a time):\n"
                    f"1. Call ONLY `view(path=\"{nxt_ocr_p}\")` to inspect Plate {nxt_ocr_num} of {len(req_ocr_p)} (`{nxt_ocr_p.name}`).\n"
                    f"2. After `view` completes, call `create(path=\"{nxt_md_p}\", file_text=\"...\")` to transcribe `{nxt_ocr_p.name}` ONLY.\n"
                    "</ocr_plate_progress>"
                )
            }

        _assembled_ocr, _all_unc = _assemble_source_ocr_from_plates(cwd, req_ocr_p)
        ocr_out_p = cwd / "SOURCE_OCR.md"
        ocr_sha = compute_file_sha256(ocr_out_p)
        fsm = load_pipeline_fsm(cwd)
        fsm["ocr_sha256"] = ocr_sha
        if (cwd / "SOURCE_TEXT.md").is_file():
            fsm["text_sha256"] = compute_file_sha256(cwd / "SOURCE_TEXT.md")
        save_pipeline_fsm(cwd, fsm)
        return {
            "additionalContext": (
                f"<validation_passed>[OK] Plate {cur_plate_num} of {len(req_ocr_p)} validated and SOURCE_OCR.md deterministically assembled "
                f"({len(req_ocr_p)}/{len(req_ocr_p)} plates verified, sha256: {(ocr_sha or '')[:8]}). "
                "Conclude with your Subagent Handshake Receipt.</validation_passed>"
            )
        }

    elif is_root_artifact and target_path.name == "SOURCE_OCR.md":
        raw_ocr = target_path.read_text(encoding="utf-8", errors="replace")
        cleaned_ocr = sanitize_markdown_artifact_text(raw_ocr, normalize_ligatures=True).strip()
        if not cleaned_ocr:
            restore_or_unlink_invalid_draft(cwd, target_path)
            return {
                "additionalContext": (
                    "<validation_error>\n[ERROR] EMPTY SOURCE_OCR.md (draft discarded): "
                    "Transcribe visible equations, constants, and bounds into SOURCE_OCR.md via 'create'.\n</validation_error>"
                )
            }
        sanitized_ocr, unc_items_ocr, xml_errs = parse_and_normalize_tei_provenance(cleaned_ocr)
        if xml_errs:
            if tool_name == "create" and (cwd / SCRATCH_DIR_NAME / ".rollback_SOURCE_OCR.md").is_file():
                restore_or_unlink_invalid_draft(cwd, target_path)
            else:
                target_path.write_text(sanitized_ocr + "\n", encoding="utf-8")
            _set_unresolved_validation_error(payload, session_id, f"SOURCE_OCR.md: {xml_errs[0]}")
            excerpt = _format_numbered_remediation_excerpt(target_path)
            return {
                "additionalContext": (
                    "<validation_error>\n[ERROR] MALFORMED TEI P5 XML IN SOURCE_OCR.md (kept on disk for scoped edit):\n"
                    + "\n".join(f"- {e}" for e in xml_errs)
                    + (f"\n\n{excerpt}" if excerpt else "")
                    + f"\nApply a minimal scoped 'edit(path=\"{target_path}\", old_str=\"...\", new_str=\"...\")' on the offending line(s) above.\n</validation_error>"
                )
            }
        final_ocr = sanitized_ocr + "\n"
        if final_ocr != raw_ocr:
            target_path.write_text(final_ocr, encoding="utf-8")
        discard_rollback_backup(cwd, "SOURCE_OCR.md")
        _clear_unresolved_validation_error(payload, session_id)

        ocr_sha = compute_file_sha256(target_path)
        fsm = load_pipeline_fsm(cwd)
        fsm["ocr_sha256"] = ocr_sha
        if (cwd / "SOURCE_TEXT.md").is_file():
            fsm["text_sha256"] = compute_file_sha256(cwd / "SOURCE_TEXT.md")
        save_pipeline_fsm(cwd, fsm)
        req_ocr_p = _load_required_ocr_plates(cwd)
        plates_note = f" ({len(req_ocr_p)}/{len(req_ocr_p)} plates verified)" if req_ocr_p else ""
        return {
            "additionalContext": (
                f"<validation_passed>[OK] SOURCE_OCR.md{plates_note} (sha256: {(ocr_sha or '')[:8]}) validated and persisted on disk. "
                "Conclude with your Subagent Handshake Receipt.</validation_passed>"
            )
        }

    elif target_path.name == "requirements.txt":
        req_text = target_path.read_text(encoding="utf-8", errors="replace")
        req_errs = validate_requirements_txt(req_text)
        if req_errs:
            if is_root_artifact:
                restore_or_unlink_invalid_draft(cwd, target_path)
            return {
                "additionalContext": (
                    "<validation_error>\n[ERROR] UNSAFE OR MALFORMED requirements.txt"
                    + (" (draft discarded):\n" if is_root_artifact else ":\n")
                    + "\n".join(f"- {e}" for e in req_errs[:5]) + "\n"
                    f"Re-write requirements.txt via 'create(path=\"{target_path}\", file_text=\"...\")' containing only standard PEP 508 package specifiers.\n</validation_error>"
                )
            }
        if is_root_artifact:
            discard_rollback_backup(cwd, "requirements.txt")
            _clear_unresolved_validation_error(payload, session_id)

    elif is_root_artifact and target_path.name == "RESEARCH.md":
        try:
            raw_r = target_path.read_text(encoding="utf-8", errors="replace")
            r_text = sanitize_markdown_artifact_text(raw_r, normalize_ligatures=False)
            if r_text != raw_r:
                target_path.write_text(r_text, encoding="utf-8")
            # Load `.pipeline_config.json` (falling back to `DEFAULT_CONFIG` when absent).
            cfg = load_pipeline_config(cwd)
            has_latex = bool(re.search(r"(\$|\\\[|\\\(|\\begin\{equation\})", r_text))
            has_cite = (not cfg.get("allow_web_research", True)) or any(
                k in r_text.lower()
                for k in ["arxiv", "doi", "http", "isbn", "ed.", "edition", "vol.", "pp.", "chapter", "section", "burden", "recipes", "cormen", "knuth", "dlmf", "press", "ieee", "rfc", "prompt://"]
            )
            warnings = []
            if not has_latex:
                warnings.append("Missing governing LaTeX mathematical formula(s) for candidate algorithms.")
            if not has_cite:
                warnings.append("Missing canonical literature or textbook citation references (arXiv, DOI, RFC, or textbook).")
            if warnings:
                restore_or_unlink_invalid_draft(cwd, target_path)
                return {
                    "additionalContext": (
                        f"<validation_error>\n[ERROR] INCOMPLETE RESEARCH BRIEF in {target_path.name} (draft discarded):\n"
                        + "\n".join(f"- {w}" for w in warnings) + "\n"
                        "The Pre-Manifest Contract requires every candidate algorithm to possess an explicit governing LaTeX formulation "
                        f"and a canonical literature/textbook citation. Re-write RESEARCH.md via 'create(path=\"{target_path}\", file_text=\"...\")'.\n</validation_error>"
                    )
                }

            discard_rollback_backup(cwd, "RESEARCH.md")
            _clear_unresolved_validation_error(payload, session_id)
            sha_val = compute_file_sha256(target_path)
            fsm = load_pipeline_fsm(cwd)
            apply_artifact_gate(
                cwd, fsm, "checkpoint_0", sha_val,
                "CHECKPOINT_0_AWAITING_APPROVAL", "STAGE_1_SPECIFICATION", cfg
            )
            # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
            save_pipeline_fsm(cwd, fsm)
            return {
                "additionalContext": (
                    f"<validation_passed>[OK] RESEARCH.md (sha256: {(sha_val or '')[:8]}) passed Pre-Manifest validation and is persisted on disk. "
                    "Conclude with '### Stage Briefing' (1-2 paragraphs + governing $$...$$ equation summarizing source provenance and candidate literature trade-offs) followed by '## Subagent Handshake Receipt'.</validation_passed>"
                )
            }
        except Exception:
            pass

    elif is_root_artifact and target_path.name == "VERIFICATION.md":
        raw_v = target_path.read_text(encoding="utf-8", errors="replace")
        v_text = sanitize_markdown_artifact_text(raw_v, normalize_ligatures=False).strip()
        if not v_text:
            restore_or_unlink_invalid_draft(cwd, target_path)
            return {
                "additionalContext": (
                    "<validation_error>\n[ERROR] EMPTY VERIFICATION.md (draft discarded): "
                    "Record obligation verdicts and tolerances in VERIFICATION.md via 'create'.\n</validation_error>"
                )
            }
        mapping_file = cwd / "mapping.json"
        if mapping_file.is_file():
            try:
                map_data = json.loads(mapping_file.read_text(encoding="utf-8"))
                expected_obls = [
                    str(o.get("obligation_id") or o.get("id"))
                    for o in map_data.get("verification_obligations", [])
                    if isinstance(o, dict) and (o.get("obligation_id") or o.get("id"))
                ]
                missing_obls = [oid for oid in expected_obls if oid not in v_text]
                if missing_obls:
                    restore_or_unlink_invalid_draft(cwd, target_path)
                    return {
                        "additionalContext": (
                            f"<validation_error>\n[ERROR] INCOMPLETE VERIFICATION.md (draft discarded): "
                            f"Missing explicit verdict(s) for verification obligation(s): {', '.join(missing_obls)}.\n"
                            f"Re-write VERIFICATION.md via 'create(path=\"{target_path}\", file_text=\"...\")' documenting every OBL-j verdict.\n</validation_error>"
                        )
                    }
            except Exception:
                pass

        # Fail-Closed Tier 1 Sign-Off Gate: reject VERIFICATION.md if any verify_*.py script has unsound oracle defects
        cand_verif_py = [
            p for p in sorted(list(cwd.glob("*.py")) + (list((cwd / "tests").glob("*.py")) if (cwd / "tests").is_dir() else []))
            if p.is_file()
            and not p.name.startswith("smoke_")
            and _is_verification_target_name(p.name, cfg_val, rel_path=str(p.relative_to(cwd)).replace("\\", "/"))
        ]
        for vf_py in cand_verif_py:
            v_sound_errs, _ = validate_verification_script_ast(vf_py, cwd=cwd)
            if v_sound_errs:
                restore_or_unlink_invalid_draft(cwd, target_path)
                return {
                    "additionalContext": (
                        f"<validation_error>\n[ERROR] UNSOUND VERIFICATION SUITE in {vf_py.name} (VERIFICATION.md draft discarded):\n"
                        + "\n".join(f"- {e}" for e in v_sound_errs) + "\n"
                        f"Fix '{vf_py.name}', re-run './.venv/bin/python -m pytest {vf_py.name}', and then re-write VERIFICATION.md.\n</validation_error>"
                    )
                }

        fsm = load_pipeline_fsm(cwd)
        if (cwd / FSM_FILE_NAME).is_file() and str(fsm.get("current_stage") or "") not in ("STAGE_4_VERIFICATION", "CHECKPOINT_REPAIR_ESCALATION"):
            restore_or_unlink_invalid_draft(cwd, target_path)
            return {
                "additionalContext": (
                    f"<validation_error>\n[ERROR] PREMATURE VERIFICATION.md OUTSIDE STAGE 4 (current_stage={fsm.get('current_stage')}):\n"
                    "VERIFICATION.md may only be authored during STAGE_4_VERIFICATION after @Formalist ('mapping.json') and @Engineer ('<module>.py') complete.\n</validation_error>"
                )
            }
        if cand_verif_py:
            last_pt = fsm.get("last_verified_pytest") if isinstance(fsm.get("last_verified_pytest"), dict) else None
            cur_v_hashes = {vf.name: compute_file_sha256(vf) for vf in cand_verif_py}
            cur_im_hashes = {ip.name: compute_file_sha256(ip) for ip in _find_implementation_files(cwd, cfg_val) if ip.is_file()}
            pt_ok = (
                isinstance(last_pt, dict)
                and last_pt.get("verify_hashes") == cur_v_hashes
                and last_pt.get("impl_hashes") == cur_im_hashes
            )
            if not pt_ok:
                if tool_name == "create" and (cwd / SCRATCH_DIR_NAME / ".rollback_VERIFICATION.md").is_file():
                    restore_or_unlink_invalid_draft(cwd, target_path)
                st_vp = load_session_state(session_id)
                st_vp["unresolved_validation_error"] = "VERIFICATION.md: pytest execution proof missing for current suite/module hashes"
                save_session_state(session_id, st_vp)
                vf_list = ", ".join(vf.name for vf in cand_verif_py)
                return {
                    "additionalContext": (
                        "<validation_error>\n[ERROR] UNEXECUTED VERIFICATION SUITE (PYTEST PROOF REQUIRED):\n"
                        f"Verification script(s) ({vf_list}) exist on disk, or code/tests changed since the last pytest run. "
                        f"Execute './.venv/bin/python -m pytest {cand_verif_py[0].name}' via 'bash' and verify all tests pass "
                        "BEFORE writing or updating VERIFICATION.md.\n</validation_error>"
                    )
                }
        ver_errs, v_text = validate_and_enrich_verification_report(v_text, cwd, fsm)
        if ver_errs:
            restore_or_unlink_invalid_draft(cwd, target_path)
            return {
                "additionalContext": (
                    "<validation_error>\n[ERROR] INVALID VERIFICATION SIGN-OFF in VERIFICATION.md (draft discarded):\n"
                    + "\n".join(f"- {e}" for e in ver_errs)
                    + "\n</validation_error>"
                )
            }

        final_v = v_text + "\n"
        if final_v != raw_v:
            target_path.write_text(final_v, encoding="utf-8")
        discard_rollback_backup(cwd, "VERIFICATION.md")
        _clear_unresolved_validation_error(payload, session_id)
        sha_val = compute_file_sha256(target_path)
        cfg = load_pipeline_config(cwd)
        apply_artifact_gate(
            cwd, fsm, "checkpoint_3", sha_val,
            "CHECKPOINT_3_AWAITING_APPROVAL", "STAGE_5_ARCHIVAL_COMPLETE", cfg
        )
        # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
        save_pipeline_fsm(cwd, fsm)
        return {
            "additionalContext": (
                f"<validation_passed>[OK] VERIFICATION.md (sha256: {(sha_val or '')[:8]}) verified across all obligations and persisted on disk. "
                "Conclude with '### Stage Briefing' (1-2 paragraphs summarizing verified anchor/reference/relational/Hypothesis regimes, residual margins vs. rtol/atol, and any resolved repairs) followed by '## Subagent Handshake Receipt'.</validation_passed>"
            )
        }

    return {}


# ============================================================================
# Section 5: Contractual Slash Commands & Steered Command Reconciliation
# Executes root-origin human slash commands and reconciles mid-turn steered commands from session transcripts.
# ============================================================================

def handle_user_prompt_submitted(payload, user_local_file=None, _steered_root_origin=False):
    """Handle contractual ``/approve``, ``/reset``, and ``/add-source`` commands.

    Executes atomic disk transitions for checkpoint approvals, interrupted-run
    snapshots, and approved research domain additions strictly on root-origin
    human prompts while ignoring subagent-origin and synthetic stop-hook
    feedback payloads.

    Args:
        payload: Hook stdin JSON dictionary from ``userPromptSubmitted``.
        user_local_file: Optional override path for ``approved_sources.local.json``.
        _steered_root_origin: Internal flag set when reconciling a verified
            root-envelope ``"delivery": "steering"`` transcript event.

    Returns:
        Empty dict ``{}`` (command hook stdout is ignored for this event).
    """
    cwd = get_payload_cwd(payload)
    session_id = get_session_id(payload)
    raw_prompt = str(payload.get("prompt", ""))
    prompt_lower = raw_prompt.lower().strip()

    # Ignore synthetic agentStop / subagentStop feedback prompts re-injected by the SDK so
    # facet_turn_viewed_paths and last_facet_user_prompt remain intact across stop-hook retries.
    if any(marker in raw_prompt for marker in SYNTHETIC_HOOK_DIRECTIVE_MARKERS):
        return {}

    state = load_session_state(session_id)
    if not _steered_root_origin:
        # Ignore nested subagent events here so root-level user session state stays clean.
        if not is_depth0_session(payload, state):
            return {}
        pending_cleanup = [
            str(x) for x in (state.get("pending_state_cleanup") or [])
            if str(x) and str(x) != session_id
        ]
        if pending_cleanup:
            purge_session_state_entries(pending_cleanup)
        # Every new root human turn clears stale active_agent, viewed paths, and handoff attempt state.
        state["active_agent"] = None
        state["facet_turn_viewed_paths"] = []
        state["facet_turn_handoff_attempts"] = 0
        state["checkpoint_delivery_pending"] = False
        state["consecutive_stop_blocks"] = 0
        state["last_subagent_failed"] = False
        state["last_facet_user_prompt"] = raw_prompt
        state["pending_state_cleanup"] = []
        # Clear sticky `is_facet_hypervisor_session` whenever the user submits a `[[PLAN]]` turn or a
        # plain non-`@Facet` chat message (`handle_facet_prompt` re-asserts `True` immediately on real `@Facet` turns).
        is_plan_turn = raw_prompt.lstrip().startswith("[[PLAN]]")
        is_explicit_facet_or_cmd = (
            prompt_lower.startswith("@facet")
            or prompt_lower in ("/approve", "/reset", "/status")
            or prompt_lower.startswith(("/approve ", "/reset ", "/status ", "/add-source"))
        )
        if is_plan_turn or not is_explicit_facet_or_cmd:
            state["is_facet_hypervisor_session"] = False
        save_session_state(session_id, state)

    # Contractual /add-source command: persist newly authorized research website(s)
    parsed_source_cmd = parse_add_source_command(raw_prompt)
    if parsed_source_cmd is not None:
        execute_add_source_command(parsed_source_cmd, cwd, user_local_file=user_local_file)
        return {}

    # Explicit-Only Workspace Reset Invariant: wipes ONLY occur when user sends /reset
    if prompt_lower == "/reset" or prompt_lower.startswith("/reset "):
        snap_res = snapshot_and_clean_interrupted_run(
            cwd, reason="User issued explicit /reset command", session_id=session_id, immediate_cleanup=True
        )
        clear_stage_receipts(cwd)
        state = load_session_state(session_id)
        state["pending_prompt_command_receipt"] = {
            "kind": "reset",
            "cwd": str(cwd.resolve()),
            "summary": snap_res,
            "timestamp": time.time(),
        }
        save_session_state(session_id, state)
        return {}

    # Contractual /approve flag enforcement
    is_approve_command = prompt_lower == "/approve" or prompt_lower.startswith("/approve ")
    if not is_approve_command:
        state = load_session_state(session_id)
        if "pending_prompt_command_receipt" in state:
            state.pop("pending_prompt_command_receipt", None)
            save_session_state(session_id, state)
        return {}

    fsm = load_pipeline_fsm(cwd)
    fsm_updated = False
    current_stage = fsm.get("current_stage", "")
    if not current_stage.startswith("CHECKPOINT_"):
        return {}

    approve_arg = prompt_lower[len("/approve"):].strip()
    other_stage_keywords = {
        "CHECKPOINT_0_AWAITING_APPROVAL": {"manifest", "mapping", "verification", "archive"},
        "CHECKPOINT_1_AWAITING_APPROVAL": {"mapping", "verification", "archive"},
        "CHECKPOINT_2_AWAITING_APPROVAL": {"manifest", "verification", "archive"},
        "CHECKPOINT_3_AWAITING_APPROVAL": {"manifest", "mapping"},
        "CHECKPOINT_REPAIR_ESCALATION": {"manifest", "mapping"},
    }
    if approve_arg in other_stage_keywords.get(current_stage, set()):
        return {}

    if current_stage == "CHECKPOINT_0_AWAITING_APPROVAL":
        r_sha = compute_file_sha256(cwd / "RESEARCH.md")
        if not r_sha:
            return {}
        fsm["checkpoints"]["checkpoint_0"]["approved"] = True
        fsm["checkpoints"]["checkpoint_0"]["approved_sha256"] = r_sha
        fsm["current_stage"] = "STAGE_1_SPECIFICATION"
        fsm_updated = True
    elif current_stage == "CHECKPOINT_1_AWAITING_APPROVAL":
        m_sha = compute_file_sha256(cwd / "manifest.json")
        if not m_sha:
            return {}
        fsm["checkpoints"]["checkpoint_1"]["approved"] = True
        fsm["checkpoints"]["checkpoint_1"]["approved_sha256"] = m_sha
        fsm["current_stage"] = "STAGE_2_MAPPING"
        fsm_updated = True
    elif current_stage == "CHECKPOINT_2_AWAITING_APPROVAL":
        map_sha = compute_file_sha256(cwd / "mapping.json")
        if not map_sha:
            return {}
        fsm["checkpoints"]["checkpoint_2"]["approved"] = True
        fsm["checkpoints"]["checkpoint_2"]["approved_sha256"] = map_sha
        fsm["current_stage"] = "STAGE_3_IMPLEMENTATION"
        fsm_updated = True
    elif current_stage in ["CHECKPOINT_3_AWAITING_APPROVAL", "CHECKPOINT_REPAIR_ESCALATION"]:
        v_sha = compute_file_sha256(cwd / "VERIFICATION.md")
        if not v_sha and current_stage == "CHECKPOINT_REPAIR_ESCALATION":
            v_sha = compute_file_sha256(cwd / "AUDIT_FEEDBACK.json")
        if not v_sha:
            return {}
        fsm["checkpoints"]["checkpoint_3"]["approved"] = True
        fsm["checkpoints"]["checkpoint_3"]["approved_sha256"] = v_sha
        fsm["current_stage"] = "STAGE_5_ARCHIVAL_COMPLETE"
        # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
        save_pipeline_fsm(cwd, fsm)
        arch_res = archive_completed_run(cwd, session_id=session_id)
        state = load_session_state(session_id)
        state["pending_prompt_command_receipt"] = {
            "kind": "archival_complete",
            "cwd": str(cwd.resolve()),
            "summary": arch_res,
            "timestamp": time.time(),
        }
        save_session_state(session_id, state)

    if fsm_updated:
        # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
        save_pipeline_fsm(cwd, fsm)

    return {}


def reconcile_steered_user_commands(payload, user_local_file=None):
    """Apply unprocessed ``"delivery": "steering"`` user commands from transcript.

    When a user submits ``/approve``, ``/reset``, or ``/add-source`` while an
    agent turn is still active ("Steer with Message" in VS Code), Copilot SDK
    appends ``user.message`` with ``"delivery": "steering"`` to ``events.jsonl``
    without firing ``userPromptSubmitted``. Inspecting the tail of
    ``transcriptPath`` ensures steered contractual commands execute atomically
    and idempotently on disk.

    Args:
        payload: Hook stdin JSON dictionary (from ``agentStop`` or ``preToolUse``).
        user_local_file: Optional override path for ``approved_sources.local.json``.

    Returns:
        ``True`` if at least one steered command was applied, else ``False``.
    """
    cwd = get_payload_cwd(payload)
    session_id = get_session_id(payload)
    raw_tp = str(payload.get("transcriptPath") or payload.get("transcript_path") or "").strip()
    if raw_tp:
        t_path = Path(raw_tp)
    elif session_id and session_id != "default_session":
        t_path = get_copilot_home() / "session-state" / session_id / "events.jsonl"
    else:
        return False
    if not t_path.is_file():
        return False

    try:
        with open(t_path, "rb") as tf:
            tf.seek(0, os.SEEK_END)
            fsize = tf.tell()
            offset = max(0, fsize - 65536)
            tf.seek(offset, os.SEEK_SET)
            raw_tail = tf.read().decode("utf-8", errors="replace")
        raw_lines = raw_tail.splitlines()
        if offset > 0 and raw_lines:
            raw_lines = raw_lines[1:]
    except Exception:
        return False

    state = load_session_state(session_id)
    processed_ids = list(state.get("processed_steering_ids", []))
    processed_set = set(processed_ids)
    applied_any = False

    for line in raw_lines:
        if '"user.message"' not in line or '"steering"' not in line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") != "user.message":
            continue
        data = ev.get("data") or {}
        if data.get("delivery") != "steering":
            continue
        if any(
            str(v or "").strip()
            for v in (
                ev.get("agentId"),
                ev.get("agent_id"),
                data.get("agentId"),
                data.get("agent_id"),
                data.get("parentToolCallId"),
            )
        ):
            continue
        msg_id = str(data.get("messageId") or ev.get("id") or "").strip()
        if not msg_id or msg_id in processed_set:
            continue
        content = str(data.get("content") or "").strip()
        lower_c = content.lower()
        is_add_src = parse_add_source_command(content) is not None
        is_reset = lower_c == "/reset" or lower_c.startswith("/reset ")
        is_approve = lower_c == "/approve" or lower_c.startswith("/approve ")
        if not (is_add_src or is_reset or is_approve):
            continue

        if is_approve:
            fsm_before = load_pipeline_fsm(cwd)
            stage_before = fsm_before.get("current_stage", "")
            if not stage_before.startswith("CHECKPOINT_"):
                continue
            handle_user_prompt_submitted(
                {"cwd": str(cwd), "sessionId": session_id, "prompt": content},
                user_local_file=user_local_file,
                _steered_root_origin=True,
            )
            fsm_after = load_pipeline_fsm(cwd)
            if (cwd / FSM_FILE_NAME).is_file() and fsm_after.get("current_stage") == stage_before:
                continue
        else:
            handle_user_prompt_submitted(
                {"cwd": str(cwd), "sessionId": session_id, "prompt": content},
                user_local_file=user_local_file,
                _steered_root_origin=True,
            )

        # Reload state in case handle_user_prompt_submitted updated receipts.
        state = load_session_state(session_id)
        processed_ids.append(msg_id)
        processed_set.add(msg_id)
        state["processed_steering_ids"] = processed_ids[-32:]
        save_session_state(session_id, state)
        applied_any = True

    return applied_any


def _read_current_turn_assistant_text(payload: dict[str, object]) -> str:
    """Return the latest non-empty assistant message from the active turn only.

    Stops scanning backwards at the current turn's ``user.message`` boundary so
    an un-flushed ``events.jsonl`` stream never reads a prior turn's reply, and
    polls up to five times at 50 ms intervals (250 ms max) only when the host
    has not yet flushed the current turn's ``assistant.message`` line to disk.

    Args:
        payload: Hook stdin JSON dictionary from ``agentStop``.

    Returns:
        Trimmed current-turn assistant text, or ``""`` when unavailable.
    """
    # Read `@Facet` or `@Orchestrator`'s reply from the current turn only (stopping at the latest `user.message` line in `events.jsonl` with up to 250ms flush polling).
    direct = str(payload.get("response") or payload.get("last_assistant_message") or "").strip()
    if direct:
        return direct
    raw_tp = str(payload.get("transcriptPath") or payload.get("transcript_path") or "").strip()
    t_path = Path(raw_tp) if raw_tp else None
    if not t_path or not t_path.is_file():
        sid_fb = str(get_session_id(payload) or "").strip()
        if sid_fb:
            cand_tp = get_copilot_home() / "session-state" / sid_fb / "events.jsonl"
            if cand_tp.is_file():
                t_path = cand_tp
    if not t_path or not t_path.is_file():
        return ""
    target_aid = payload.get("agentId") or payload.get("agent_id") or None

    for attempt in range(6):
        try:
            with open(t_path, "rb") as tf:
                tf.seek(0, os.SEEK_END)
                fsize = tf.tell()
                off = max(0, fsize - 65536)
                tf.seek(off, os.SEEK_SET)
                tail_txt = tf.read().decode("utf-8", errors="replace")
            t_lines = tail_txt.splitlines()
            if off > 0 and t_lines:
                t_lines = t_lines[1:]
            for ln in reversed(t_lines):
                if '"assistant.message"' not in ln and '"user.message"' not in ln:
                    continue
                ev = json.loads(ln)
                ev_aid = ev.get("agentId") or None
                if ev_aid != target_aid:
                    continue
                ev_type = ev.get("type")
                # Stop scanning further once `ev_type == 'user.message'`.
                if ev_type == "user.message":
                    break
                if ev_type == "assistant.message":
                    cand = str((ev.get("data") or {}).get("content") or "").strip()
                    if cand:
                        return cand
        except Exception:
            return ""
        if attempt < 5:
            time.sleep(0.05)
    return ""


def is_runaway_stop_continuation(
    payload: dict[str, object],
    state: dict[str, object] | None = None,
    counter_key: str = "consecutive_stop_blocks",
    max_blocks: int = 6,
) -> bool:
    """Return ``True`` when Copilot's native ``stop_hook_active`` flag or session block cap is reached."""
    if bool(payload.get("stop_hook_active", False)):
        return True
    if isinstance(state, dict) and int(state.get(counter_key, 0) or 0) >= max_blocks:
        return True
    return False


def handle_orchestrator_stop(payload, user_local_file=None):
    r"""Reconcile steered commands and enforce checkpoint gate fidelity on ``agentStop``.

    Bound exclusively to ``@Orchestrator`` via ``orchestrator.agent.md`` so
    normal chats and child subagents incur zero ``agentStop`` overhead. Ensures
    Stage 5 archival executes on disk when ``/approve`` arrives via steering,
    continues ``@Orchestrator`` when ``C0``–``C2`` is approved via steering, and
    blocks premature archival/completion claims when ``.pipeline_state.json``
    still awaits human ``/approve`` sign-off.

    Args:
        payload: Hook stdin JSON dictionary from ``agentStop``.
        user_local_file: Optional override path for ``approved_sources.local.json``.

    Returns:
        ``{"decision": "allow"}`` or ``{"decision": "block", "reason": ...}``.
    """
    # Reconcile mid-turn steered `/approve` messages, prevent `@Orchestrator` from stopping mid-stage, and block premature archival claims before `/approve`.
    sub_type = str(payload.get("agentType") or payload.get("agent_type") or "").strip()
    if sub_type and sub_type != "Orchestrator":
        return {"decision": "allow"}

    cwd = get_payload_cwd(payload)
    reconciled = reconcile_steered_user_commands(payload, user_local_file=user_local_file)
    orch_sid = get_session_id(payload)
    orch_sess_state = load_session_state(orch_sid)
    # Protocol invariant: VS Code leaves `stop_hook_active=True` sticky after the first stop block in a turn; bound continuations via `orch_transient_stop_blocks` in `hooks_state.json`.
    orch_blocks_so_far = int(orch_sess_state.get("orch_transient_stop_blocks") or 0)
    if orch_blocks_so_far < 4 and (cwd / FSM_FILE_NAME).is_file():
        fsm = load_pipeline_fsm(cwd)
        cur_stage = fsm.get("current_stage", "")
        has_active_rewind = bool(fsm.get("pending_rewind"))
        if has_active_rewind or not (orch_sess_state.get("last_subagent_failed") and not reconciled):
            if cur_stage in STAGE_SPECIALIST_ALLOWLIST:
                orch_sess_state["orch_transient_stop_blocks"] = orch_blocks_so_far + 1
                save_session_state(orch_sid, orch_sess_state)
                owners = sorted(STAGE_SPECIALIST_ALLOWLIST[cur_stage])
                next_role = "Researcher" if ("Researcher" in owners and (cwd / "SOURCE_OCR.md").is_file()) else owners[0]
                steer_prefix = "Checkpoint approved via steering message. " if reconciled else "Pipeline is mid-execution in an active transient stage (do NOT stop before reaching a human checkpoint or Stage 5 archival). "
                return {
                    "decision": "block",
                    "reason": (
                        f"<subagent_directive>\n[ACTIVE STAGE: {cur_stage}] {steer_prefix}"
                        f"Invoke 'task(agent_type=\"{next_role}\", name=\"{next_role}\", mode=\"sync\", ...)' immediately to execute {cur_stage}.\n</subagent_directive>"
                    ),
                }
        if cur_stage.startswith("CHECKPOINT_") or cur_stage == "PRE_RUN_HALT":
            if orch_sess_state.get("orch_transient_stop_blocks"):
                orch_sess_state["orch_transient_stop_blocks"] = 0
                save_session_state(orch_sid, orch_sess_state)

        if cur_stage.startswith("CHECKPOINT_") and not is_runaway_stop_continuation(payload, orch_sess_state):
            last_text = _read_current_turn_assistant_text(payload)

            if last_text:
                premature_archival = bool(re.search(
                    r"archival\s+complete|transitioning\s+to\s+stage\s+5|now\s+(?:available|sealed)\s+in\s+[^\n]*specs/|the\s+task\s+is\s+complete|Complete\s*·\s*Sealed\s+Specification\s+Package|Where\s+to\s+Take\s+This\s+Next",
                    last_text,
                    re.IGNORECASE,
                ))
                missing_approve_cta = "/approve" not in last_text
                if premature_archival or missing_approve_cta:
                    slug_hint = fsm.get("concept_slug") or "<concept_slug>"
                    return {
                        "decision": "block",
                        "reason": (
                            f"<subagent_directive>\n"
                            f"[CRITICAL CHECKPOINT GATE VIOLATION — {cur_stage}]: Human sign-off has NOT been granted yet "
                            f"('.pipeline_state.json' is paused at {cur_stage} and 'specs/{slug_hint}/' has NOT been created on disk). "
                            "NEVER claim 'Archival Complete', 'transitioning to Stage 5', or 'The task is complete' before the user replies '/approve'. "
                            "Emit ZERO tool calls and output a brief corrective Checkpoint Gate prompt ending with: "
                            f"'{CHECKPOINT_CTA_TEXT}'\n"
                            "</subagent_directive>"
                        ),
                    }

    return {"decision": "allow"}


# ============================================================================
# Section 6: `@Facet` / `@Orchestrator` Prompt & Stop Gates
# ============================================================================

def handle_facet_prompt(payload, user_local_file=None):
    r"""Prepare ``@Facet`` hypervisor context, genesis seals, and loop-back state.

    Strips unreachable ``rename_chat`` ``<system_reminder>`` blocks, initializes
    a signed genesis ``PRE_RUN_HALT`` state on Cycle 1, disarms the previous
    cycle's ``post_archive_halt_latch`` on a fresh user cycle request (without
    overwriting tampered seals), and injects ``<hypervisor_context>`` with exact
    seal paths to inspect via ``view``.

    Args:
        payload: Hook stdin JSON dictionary from ``userPromptTransformed``.
        user_local_file: Optional override path for ``approved_sources.local.json``.

    Returns:
        Dictionary with ``modifiedTransformedPrompt``, or ``{}``.
    """
    cwd = get_payload_cwd(payload).resolve()
    session_id = get_session_id(payload)
    raw_prompt = str(payload.get("prompt", ""))
    orig_transformed = payload.get("transformedPrompt") or raw_prompt or ""
    transformed_prompt = orig_transformed

    if "rename_chat" in transformed_prompt:
        transformed_prompt = re.sub(
            r"\n*<system_reminder>(?:(?!</system_reminder>)[\s\S])*\brename_chat\b[\s\S]*?</system_reminder>\s*",
            "\n\n",
            transformed_prompt,
        ).strip()

    if raw_prompt.lstrip().startswith("[[PLAN]]") or transformed_prompt.lstrip().startswith("[[PLAN]]"):
        return {}

    if any(marker in orig_transformed for marker in FACET_PROMPT_MARKERS):
        return {}

    state = load_session_state(session_id)
    state["is_facet_hypervisor_session"] = True
    state["facet_turn_viewed_paths"] = []
    state["facet_turn_handoff_attempts"] = 0
    state["facet_turn_orchestrator_dispatched"] = False
    state["consecutive_stop_blocks"] = 0
    state["last_facet_user_prompt"] = raw_prompt
    save_session_state(session_id, state)

    parsed_source_cmd = parse_add_source_command(raw_prompt)
    if parsed_source_cmd is not None:
        sources = load_approved_research_sources(cwd, user_local_file=user_local_file)
        active_list = ", ".join(sources["domains"])
        scope_label = "workspace (.pipeline_config.json)" if parsed_source_cmd["scope"] == "workspace" else "user (~/.copilot/approved_sources.local.json)"
        src_lines = [
            "\n\n<source_registry_update>",
            "[AGENT CONTEXT — DO NOT ECHO XML TAGS OR BRACKET PREFIXES: Emit ZERO tool calls and confirm this update in 1-2 natural sentences.]",
        ]
        if parsed_source_cmd["list_only"]:
            src_lines.append(f"Currently approved research domains ({len(sources['domains'])}): {active_list}.")
        else:
            if parsed_source_cmd["valid"]:
                src_lines.append(f"Added {', '.join(parsed_source_cmd['valid'])} to {scope_label}.")
            if parsed_source_cmd["invalid"]:
                src_lines.append(f"Skipped invalid domain specifier(s): {', '.join(parsed_source_cmd['invalid'])}.")
            src_lines.append(f"Active approved research domains ({len(sources['domains'])}): {active_list}.")
        src_lines.append("</source_registry_update>")
        return {"modifiedTransformedPrompt": transformed_prompt + "\n".join(src_lines)}

    norm_cmd_lower = re.sub(r"^\s*@facet\s+", "", raw_prompt, flags=re.IGNORECASE).lower().strip()
    is_status = norm_cmd_lower == "/status" or norm_cmd_lower.startswith("/status ")
    if is_status:
        return {"modifiedTransformedPrompt": transformed_prompt + "\n\n" + render_status_pulse_block(cwd)}
    near_status = is_near_miss_command(raw_prompt)
    if near_status == "/status":
        pulse_blk = render_status_pulse_block(cwd).replace(
            "\n\n> **Workspace Pulse**",
            "\n\nDid you mean `/status`?\n\n> **Workspace Pulse**",
            1,
        )
        return {"modifiedTransformedPrompt": transformed_prompt + "\n\n" + pulse_blk}

    is_approve = norm_cmd_lower == "/approve" or norm_cmd_lower.startswith("/approve ")
    is_reset = norm_cmd_lower == "/reset" or norm_cmd_lower.startswith("/reset ")

    cmd_receipt = state.get("pending_prompt_command_receipt")
    if (
        isinstance(cmd_receipt, dict)
        and cmd_receipt.get("cwd") == str(cwd)
        and (time.time() - float(cmd_receipt.get("timestamp", 0.0))) < 30.0
    ):
        r_kind = cmd_receipt.get("kind")
        r_sum = cmd_receipt.get("summary") or {}
        if r_kind == "archival_complete" and is_approve:
            slug_val = r_sum.get("slug", "archived_specification")
            prov_rel = r_sum.get("provenance_path") or f"specs/{slug_val}/PROVENANCE.json"
            topic_brief = str(r_sum.get("topic_result_brief") or "").strip() or render_stage5_topic_result_briefing(cwd, slug_val)
            arch_notice = (
                "\n\n<archival_complete>\n"
                f"All verified deliverables were archived to 'specs/{slug_val}/' and '{prov_rel}' cryptographic seals were verified on disk, leaving the workspace root clean.\n"
                "Emit ZERO tool calls in this turn (do NOT call 'view' or 'task') and NEVER output XML tags or internal state names. "
                "Output the complete mathematical/engineering synthesis, `> **Complete · Sealed Specification Package**` blockquote card (preserving `%20`-encoded file links intact), and `### Where to Take This Next` section followed by ONE fenced ` ```text\\n@Facet ...\\n``` ` prompt below, writing nothing after the closing fence:\n\n"
                f"{topic_brief}\n"
                "</archival_complete>"
            )
            return {"modifiedTransformedPrompt": transformed_prompt + arch_notice}
        if r_kind == "reset" and is_reset:
            genesis_fsm = json.loads(json.dumps(DEFAULT_FSM))
            genesis_fsm["current_stage"] = "PRE_RUN_HALT"
            genesis_fsm["post_archive_halt_latch"] = False
            # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
            save_pipeline_fsm(cwd, genesis_fsm)
            snap_dir = r_sum.get("target_dir") if isinstance(r_sum, dict) else None
            detail_msg = (
                f"In-progress files were saved to '{snap_dir}' and the workspace root is clean."
                if snap_dir
                else "The workspace root was already clean."
            )
            reset_notice = (
                "\n\n<reset_complete>\n"
                f"{detail_msg} Emit ZERO tool calls and NEVER echo XML tags or internal state names; confirm the reset naturally in 1-2 sentences and await the user's next prompt.\n"
                "</reset_complete>"
            )
            return {"modifiedTransformedPrompt": transformed_prompt + reset_notice}

    fsm_path = cwd / FSM_FILE_NAME
    if not fsm_path.is_file():
        if is_approve or is_reset:
            return {"modifiedTransformedPrompt": transformed_prompt} if transformed_prompt != orig_transformed else {}
        genesis_fsm = json.loads(json.dumps(DEFAULT_FSM))
        genesis_fsm["current_stage"] = "PRE_RUN_HALT"
        genesis_fsm["post_archive_halt_latch"] = False
        # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
        save_pipeline_fsm(cwd, genesis_fsm)

    fsm = load_pipeline_fsm(cwd)
    current_stage = str(fsm.get("current_stage", "PRE_RUN_HALT"))

    if current_stage == "PRE_RUN_HALT" and (is_approve or is_reset):
        return {"modifiedTransformedPrompt": transformed_prompt} if transformed_prompt != orig_transformed else {}

    # On a fresh user goal turn after a completed cycle, disarm post_archive_halt_latch ONLY if existing seals are untampered.
    if current_stage == "PRE_RUN_HALT" and fsm.get("post_archive_halt_latch") and not is_approve and not is_reset:
        untampered, _ = verify_workspace_cryptographic_seals(cwd, check_archive=False)
        if untampered:
            fsm["post_archive_halt_latch"] = False
            # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
            save_pipeline_fsm(cwd, fsm)

    if current_stage.startswith("CHECKPOINT_"):
        return {"modifiedTransformedPrompt": render_checkpoint_reprompt_block(cwd, fsm, raw_prompt)}

    recovery_prefix = ""
    last_inc = fsm.get("last_incident")
    if isinstance(last_inc, dict):
        inc_cat = str(last_inc.get("category") or "other")
        inc_stage = str(last_inc.get("stage") or current_stage)
        inc_agent = str(last_inc.get("agent_type") or "subagent")
        inc_ctx = str(last_inc.get("error_context") or "unknown")
        inc_rec = bool(last_inc.get("recoverable", False))
        inc_msg = str(last_inc.get("message") or "Unknown error")
        relaunch_call = format_corrected_orchestrator_call(
            render_orchestrator_handoff_brief(cwd, current_stage, raw_prompt)
        )
        recovery_prefix = (
            "\n\n<recovery_notice>\n"
            f"[PREVIOUS INCIDENT DETECTED — {inc_cat} at {inc_stage}]:\n"
            f"- Failed Agent/Context: {inc_agent} ({inc_ctx}, recoverable={inc_rec})\n"
            f"- Error Summary: {inc_msg}\n"
            "- Recovery Action: Relaunch '@Orchestrator' synchronously immediately with this exact call:\n"
            f"{relaunch_call}\n"
            "</recovery_notice>"
        )

    # On a fresh user goal turn, persist original_user_request and reset repair_history for the new cycle
    if current_stage in ("PRE_RUN_HALT", "STAGE_0_IDEATION") and not is_approve and not is_reset and raw_prompt.strip():
        clean_orig = re.sub(r"^\s*@facet\s+", "", raw_prompt, flags=re.IGNORECASE).strip()
        if clean_orig and fsm.get("original_user_request") != clean_orig:
            fsm["original_user_request"] = clean_orig
            fsm["mandated_target_module"] = extract_mandated_target_module(clean_orig)
            if current_stage == "PRE_RUN_HALT":
                fsm["repair_attempts"] = 0
                fsm["repair_history"] = []
            save_pipeline_fsm(cwd, fsm)

    ref_specs = discover_referenced_archived_specs(cwd, raw_prompt)
    if ref_specs:
        fsm["referenced_archived_specs"] = [r["slug"] for r in ref_specs]
        # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
        save_pipeline_fsm(cwd, fsm)
    elif current_stage == "PRE_RUN_HALT" and fsm.get("referenced_archived_specs"):
        fsm["referenced_archived_specs"] = []
        # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
        save_pipeline_fsm(cwd, fsm)

    # Load `.pipeline_config.json` (falling back to `DEFAULT_CONFIG` when absent).
    cfg = load_pipeline_config(cwd)
    seal_paths = [str(cwd / FSM_FILE_NAME)] + [
        str((cwd / _strip_dot_slash_prefix(str(rel))).resolve())
        for rel in cfg.get("upstream_seals", [])
    ]
    seal_list = ", ".join(seal_paths)
    stage_for_brief = (
        resolve_initial_active_stage(cwd, cfg, user_request=raw_prompt)
        if current_stage == "PRE_RUN_HALT"
        else current_stage
    )
    exact_orch_call = format_corrected_orchestrator_call(
        render_orchestrator_handoff_brief(cwd, stage_for_brief, raw_prompt),
        stage=current_stage,
    )
    hypervisor_block = (
        f"{recovery_prefix}\n\n<hypervisor_context>\n"
        "[HYPERVISOR DISPATCH GATE — READY TO LAUNCH @Orchestrator]\n"
        f"- Cryptographic seals verified on disk ({seal_list}).\n"
        "- Step 1: Do NOT call 'view', 'glob', or 'grep' — invoke AT MOST ONE synchronous '@Orchestrator' call immediately using this exact call:\n"
        f"{exact_orch_call}\n"
        f"- Step 2 (CRITICAL — STOP AFTER 1 `task` CALL & COPY REPORT VERBATIM): Any '/approve' in the user prompt authorized ONLY '{stage_for_brief}'. You may invoke `task` AT MOST ONCE in this entire user turn. As soon as that single `task` call returns its Markdown report, DO NOT call `task`, `view`, or any other tool again — your reply MUST reproduce the returned Markdown report and its closing `> **Stage ...**` (or `> **Complete ...**`) blockquote card VERBATIM in full from first paragraph through the final `>` line (never paraphrase away the `>` card and never emit XML tags).\n"
        "</hypervisor_context>"
    )
    return {"modifiedTransformedPrompt": transformed_prompt + hypervisor_block}


def handle_facet_stop(payload, user_local_file=None):
    r"""Verify post-archive ``PROVENANCE.json`` integrity and control-plane dispatch on ``@Facet`` stop.

    Enforces the Streaming Non-Duplication Invariant: because VS Code Chat has
    already streamed ``last_text`` to the user's bubble before ``agentStop``
    runs, this hook never blocks for post-hoc cosmetic formatting (links,
    previews, emojis, or phrasing) once user-facing text has streamed. It blocks
    strictly on missing control-plane actions (steered ``/approve`` execution,
    un-dispatched active stages, un-read ``PROVENANCE.json``, or missing
    checkpoint ``/approve`` gate cards).

    Args:
        payload: Hook stdin JSON dictionary from ``agentStop``.
        user_local_file: Optional override path for ``approved_sources.local.json``.

    Returns:
        ``{"decision": "allow"}`` or ``{"decision": "block", "reason": ...}``.
    """
    sub_type = str(payload.get("agentType") or payload.get("agent_type") or "").strip()
    if sub_type and sub_type != "Facet":
        return {"decision": "allow"}

    cwd = get_payload_cwd(payload).resolve()
    session_id = get_session_id(payload)
    reconciled = reconcile_steered_user_commands(payload, user_local_file=user_local_file)
    state = load_session_state(session_id)
    if is_runaway_stop_continuation(payload, state) or not (cwd / FSM_FILE_NAME).is_file():
        return {"decision": "allow"}

    fsm = load_pipeline_fsm(cwd)
    cur_stage = str(fsm.get("current_stage", ""))

    last_text = _read_current_turn_assistant_text(payload)
    raw_u_early = str(state.get("last_facet_user_prompt") or "").strip()
    u_norm_early = re.sub(r"^\s*@facet\s+", "", raw_u_early, flags=re.IGNORECASE).lower().strip()
    is_status_turn = (
        u_norm_early == "/status"
        or u_norm_early.startswith("/status ")
        or is_near_miss_command(raw_u_early) == "/status"
    )
    if is_status_turn:
        return {"decision": "allow"}

    if reconciled and cur_stage in STAGE_SPECIALIST_ALLOWLIST:
        return {
            "decision": "block",
            "reason": (
                f"<hypervisor_directive>\n[ACTIVE STAGE: {cur_stage}] Checkpoint approved via steering message. "
                f"Invoke 'task(agent_type=\"Orchestrator\")' immediately to execute {cur_stage}.\n"
                "</hypervisor_directive>"
            ),
        }

    if cur_stage == "PRE_RUN_HALT" and fsm.get("post_archive_halt_latch"):
        arch_ok, arch_err = verify_workspace_cryptographic_seals(cwd, check_archive=True)
        if not arch_ok:
            if "[HYPERVISOR HALT - ARCHIVAL VERIFICATION FAILED]" not in last_text and "archival verification" not in last_text.lower():
                return {
                    "decision": "block",
                    "reason": (
                        f"<hypervisor_directive>\n[HYPERVISOR HALT - ARCHIVAL VERIFICATION FAILED]: {arch_err}\n"
                        "Do NOT certify archival completion or invoke 'task'. Explain clearly to the user which archived file failed integrity verification and halt.\n"
                        "</hypervisor_directive>"
                    ),
                }
            return {"decision": "allow"}

        if last_text and "@Facet" not in last_text:
            return {
                "decision": "block",
                "reason": (
                    "<hypervisor_directive>\n[NEXT-CYCLE PROMPT REQUIRED]: Archival seals are verified. "
                    "Emit ZERO tool calls and append ONLY a `### Where to Take This Next` lead-in and ONE fenced ready-to-use prompt "
                    "('@Facet <next engineering objective>') without repeating text already written.\n"
                    "</hypervisor_directive>"
                ),
            }
        return {"decision": "allow"}

    if cur_stage.startswith("CHECKPOINT_") and last_text:
        premature_archival = bool(re.search(
            r"archival\s+complete|transitioning\s+to\s+stage\s+5|now\s+(?:available|sealed)\s+in\s+[^\n]*specs/|the\s+task\s+is\s+complete|Complete\s*·\s*Sealed\s+Specification\s+Package|Where\s+to\s+Take\s+This\s+Next",
            last_text,
            re.IGNORECASE,
        ))
        has_framed_stage_card = bool(re.search(
            r"^\s*>\s*\*\*(?:Stage\s+\d+\s+of\s+4|Repair\s+Escalation|Escalation)",
            last_text,
            re.MULTILINE,
        ))
        has_canonical_cta = CHECKPOINT_CTA_TEXT in last_text or "Reply `/approve` to continue" in last_text
        has_gate_leak = bool(re.search(
            r"internal\s+hypervisor\s+gate|briefing\s+was\s+(?:successfully\s+)?(?:generated\s+and\s+)?presented",
            last_text,
            re.IGNORECASE,
        ))
        missing_approve_cta = (
            "/approve" not in last_text
            or has_gate_leak
            or (cur_stage != "CHECKPOINT_REPAIR_ESCALATION" and not has_framed_stage_card and not has_canonical_cta)
        )
        if premature_archival or missing_approve_cta:
            cp_card_only = render_checkpoint_briefing_block(cwd, fsm)
            return {
                "decision": "block",
                "reason": (
                    "<hypervisor_directive>\n"
                    "Human sign-off has NOT been granted yet for this checkpoint (Stage 5 archival has not run). "
                    "Emit ZERO tool calls and output ONLY the missing 3-line '>' Gate Card below without repeating paragraphs already shown:\n"
                    "</hypervisor_directive>\n\n"
                    f"{cp_card_only}"
                ),
            }
        if state.get("checkpoint_delivery_pending"):
            state["checkpoint_delivery_pending"] = False
            save_session_state(session_id, state)
        return {"decision": "allow"}

    # Prevent @Facet from exiting conversationally (or hallucinating a '[PENDING]' checkpoint)
    # on an active execution stage before dispatching @Orchestrator!
    raw_u_stop = str(state.get("last_facet_user_prompt") or "").strip()
    u_stop_low = raw_u_stop.lower()
    in_active_exec_stage = cur_stage in STAGE_SPECIALIST_ALLOWLIST
    needs_dispatch_from_pre_run = (
        cur_stage == "PRE_RUN_HALT"
        and bool(raw_u_stop)
        and u_stop_low != "/approve"
        and not u_stop_low.startswith("/approve ")
    )
    if (
        (in_active_exec_stage or needs_dispatch_from_pre_run)
        and not fsm.get("post_archive_halt_latch")
        and not state.get("facet_turn_orchestrator_dispatched")
        and u_stop_low != "/reset"
        and not u_stop_low.startswith("/reset ")
        and parse_add_source_command(raw_u_stop) is None
        and "[HYPERVISOR HALT" not in last_text
        and int(state.get("consecutive_stop_blocks") or 0) < 2
    ):
        seal_ok_stop, _ = verify_workspace_cryptographic_seals(cwd, check_archive=False)
        if seal_ok_stop:
            state["consecutive_stop_blocks"] = int(state.get("consecutive_stop_blocks") or 0) + 1
            save_session_state(session_id, state)
            stage_stop = "STAGE_0_IDEATION" if cur_stage == "PRE_RUN_HALT" else cur_stage
            canon_stop = render_orchestrator_handoff_brief(cwd, stage_stop, raw_u_stop or "/approve")
            step_hint = "Do NOT stop, call 'view', or announce commencement; invoke ONLY this exact synchronous '@Orchestrator' call immediately:\n"
            return {
                "decision": "block",
                "reason": (
                    "<hypervisor_directive>\n"
                    f"[ORCHESTRATOR DISPATCH REQUIRED] ({stage_stop}): Stage '{stage_stop}' has NOT executed yet. "
                    f"{step_hint}{format_corrected_orchestrator_call(canon_stop)}\n"
                    "</hypervisor_directive>"
                ),
            }

    return {"decision": "allow"}


def handle_user_prompt_transformed(payload, user_local_file=None, orchestrator_scoped=False):
    r"""Inject checkpoint gates, source updates, and scoped stage anchors.

    Suppresses stage/checkpoint injection on ``[[PLAN]]`` prompts, strips VS Code's
    unreachable ``rename_chat`` ``<system_reminder>`` on ``@Orchestrator``, and
    emits ``<stage_anchor>`` only when ``.pipeline_state.json`` exists on disk or
    when invoked by ``@Orchestrator``'s agent-scoped hook.

    Args:
        payload: Hook stdin JSON dictionary from ``userPromptTransformed``.
        user_local_file: Optional override path for ``approved_sources.local.json``.
        orchestrator_scoped: ``True`` when invoked from ``orchestrator.agent.md``.

    Returns:
        Dictionary with ``modifiedTransformedPrompt``, or ``{}``.
    """
    cwd = get_payload_cwd(payload)
    session_id = get_session_id(payload)
    raw_prompt_early = str(payload.get("prompt", ""))
    state_early = load_session_state(session_id)
    if not is_depth0_session(payload, state_early):
        sub_role = str(
            payload.get("agentType")
            or payload.get("agent_type")
            or payload.get("agentName")
            or payload.get("agent_name")
            or state_early.get("active_agent")
            or ""
        ).strip()
        if sub_role != "Orchestrator":
            return {}
    if not orchestrator_scoped and is_depth0_session(payload, state_early) and re.match(r"^\s*@facet(?:\b|$)", raw_prompt_early, re.IGNORECASE):
        return handle_facet_prompt(payload, user_local_file=user_local_file)

    fsm_exists = (cwd / FSM_FILE_NAME).is_file()
    fsm = load_pipeline_fsm(cwd)
    current_stage = fsm.get("current_stage", "")

    if not orchestrator_scoped and state_early.get("is_facet_hypervisor_session"):
        return {}

    raw_prompt = str(payload.get("prompt", ""))
    if any(marker in raw_prompt for marker in SYNTHETIC_HOOK_DIRECTIVE_MARKERS):
        return {}
    prompt_lower = raw_prompt.lower().strip()
    orig_transformed = payload.get("transformedPrompt") or raw_prompt or ""
    transformed_prompt = orig_transformed

    # Strip VS Code's internal auto-title <system_reminder> requesting 'rename_chat',
    # which is not part of @Orchestrator's 7 Copilot CLI built-in tool categories.
    if orchestrator_scoped and "rename_chat" in transformed_prompt:
        transformed_prompt = re.sub(
            r"\n*<system_reminder>(?:(?!</system_reminder>)[\s\S])*\brename_chat\b[\s\S]*?</system_reminder>\s*",
            "\n\n",
            transformed_prompt,
        ).strip()

    parsed_source_cmd = parse_add_source_command(raw_prompt)
    if parsed_source_cmd is not None and not orchestrator_scoped:
        sources = load_approved_research_sources(cwd, user_local_file=user_local_file)
        active_list = ", ".join(sources["domains"])
        scope_label = "workspace (.pipeline_config.json)" if parsed_source_cmd["scope"] == "workspace" else "user (~/.copilot/approved_sources.local.json)"
        lines = ["\n\n<source_registry_update>"]
        if parsed_source_cmd["list_only"]:
            lines.append(f"[INFO] Approved Research Sources ({len(sources['domains'])} active): {active_list}")
            lines.append("Usage: '/add-source <domain_or_url>' (user scope) or '/add-source --workspace <domain_or_url>' (workspace scope).")
        else:
            if parsed_source_cmd["valid"]:
                lines.append(
                    f"[OK] Authorized Research Domain(s) in {scope_label}: {', '.join(parsed_source_cmd['valid'])}. "
                    "Requests to these domains (and their subdomains) via 'web_fetch' are now auto-approved."
                )
            if parsed_source_cmd["invalid"]:
                lines.append(
                    f"[WARNING] Rejected invalid or unsafe domain specifier(s): {', '.join(parsed_source_cmd['invalid'])} "
                    "(must be a valid public DNS hostname without wildcards, credentials, or private IPs)."
                )
            lines.append(f"Active Approved Domains ({len(sources['domains'])}): {active_list}")
        lines.append("</source_registry_update>")
        transformed_prompt = transformed_prompt + "\n".join(lines)
        return {"modifiedTransformedPrompt": transformed_prompt}

    # Never inject @Orchestrator stage/checkpoint anchors into VS Code Plan Mode ('[[PLAN]]') prompts
    if raw_prompt.lstrip().startswith("[[PLAN]]") or transformed_prompt.lstrip().startswith("[[PLAN]]"):
        return {}

    # Surface deterministic Stage 5 archival or /reset receipts before checking FSM state
    state = load_session_state(session_id)
    cmd_receipt = state.get("pending_prompt_command_receipt")
    if (
        isinstance(cmd_receipt, dict)
        and cmd_receipt.get("cwd") == str(cwd.resolve())
        and (time.time() - float(cmd_receipt.get("timestamp", 0.0))) < 30.0
    ):
        r_kind = cmd_receipt.get("kind")
        r_sum = cmd_receipt.get("summary") or {}
        if r_kind == "archival_complete" and (prompt_lower == "/approve" or prompt_lower.startswith("/approve ")):
            if "<archival_complete>" in transformed_prompt:
                return {}
            slug_val = r_sum.get("slug", "archived_specification")
            topic_brief = str(r_sum.get("topic_result_brief") or "").strip() or render_stage5_topic_result_briefing(cwd, slug_val)
            arch_notice = (
                "\n\n<archival_complete>\n"
                f"All verified deliverables were archived into 'specs/{slug_val}/', leaving the workspace root clean.\n"
                "Emit ZERO tool calls (do NOT call 'bash', 'task', 'create', or 'edit') and NEVER output XML tags or internal state names. "
                "Output the complete mathematical/engineering synthesis, `> **Complete · Sealed Specification Package**` blockquote card verbatim, and `### Where to Take This Next` section followed by ONE fenced ` ```text\\n@Facet ...\\n``` ` prompt below, writing nothing after the closing fence:\n\n"
                f"{topic_brief}\n"
                "</archival_complete>"
            )
            return {"modifiedTransformedPrompt": transformed_prompt + arch_notice}
        if r_kind == "reset" and (prompt_lower == "/reset" or prompt_lower.startswith("/reset ")):
            if "<reset_complete>" in transformed_prompt:
                return {}
            snap_dir = r_sum.get("target_dir") if isinstance(r_sum, dict) else None
            detail_msg = (
                f"In-progress artifacts were snapshotted to '{snap_dir}' and the workspace root is now clean."
                if snap_dir
                else "Workspace root was already clean."
            )
            reset_notice = (
                "\n\n<reset_complete>\n"
                f"{detail_msg} Emit ZERO tool calls and NEVER echo XML tags or internal state names; confirm the reset concisely and await the user's next prompt.\n"
                "</reset_complete>"
            )
            return {"modifiedTransformedPrompt": transformed_prompt + reset_notice}

    # Never inject STAGE_0_IDEATION onto bare /approve or /reset prompts when no active FSM exists
    if not fsm_exists and (
        prompt_lower == "/approve"
        or prompt_lower.startswith("/approve ")
        or prompt_lower == "/reset"
        or prompt_lower.startswith("/reset ")
    ):
        return {}

    if fsm_exists and current_stage.startswith("CHECKPOINT_"):
        if "<checkpoint_gate>" in transformed_prompt or "<checkpoint_reprompt>" in transformed_prompt:
            return {"modifiedTransformedPrompt": transformed_prompt} if transformed_prompt != orig_transformed else {}
        if not orchestrator_scoped:
            return {"modifiedTransformedPrompt": render_checkpoint_reprompt_block(Path(cwd).resolve(), fsm, raw_prompt)}
        checkpoint_notice = (
            "\n\n<checkpoint_gate>\n"
            "[GATE] HUMAN REVIEW CHECKPOINT ACTIVE: The pipeline is currently awaiting human authorization.\n"
            f"{CHECKPOINT_CTA_TEXT}\n"
            "</checkpoint_gate>"
        )
        return {"modifiedTransformedPrompt": transformed_prompt + checkpoint_notice}

    # Only inject <stage_anchor> on @Orchestrator's agent-scoped hook (never bleed into regular workspace chats)
    if not orchestrator_scoped:
        return {}
    if current_stage == "PRE_RUN_HALT":
        current_stage = "STAGE_0_IDEATION"
    if (
        "<stage_anchor>" in transformed_prompt
        or "Internal Coordinator Routing Mandate (" in transformed_prompt
        or "<archival_complete>" in transformed_prompt
        or "<reset_complete>" in transformed_prompt
    ):
        return {"modifiedTransformedPrompt": transformed_prompt} if transformed_prompt != orig_transformed else {}

    res_inputs = discover_research_inputs(cwd, user_request=str(fsm.get("original_user_request") or ""), cfg=load_pipeline_config(cwd))
    if res_inputs["ocr_files"] and not (cwd / "SOURCE_OCR.md").is_file():
        cmd_args_s0 = _format_stage_ocr_cmd_args(cwd, list(res_inputs["ocr_files"]))
        s0_hint = (
            f"Visual/PDF document(s) detected ({', '.join(res_inputs['ocr_files'][:5])}) without SOURCE_OCR.md. "
            f"Delegate via 'task' to @Scribe FIRST (Stage 0V: 'prepare_visual_sources.py {cmd_args_s0}' -> SOURCE_OCR.md) "
            "before delegating to @Researcher."
        )
    else:
        s0_hint = (
            "Delegate via 'task' to @Scribe (if PDF/image/visual URL provided) or @Researcher (Path B Discovery -> RESEARCH.md, or Path A -> manifest.json). "
            "Never delegate to @Formalist/@Engineer/@Auditor before manifest.json exists."
        )

    stage_routing_hint = {
        "STAGE_0_IDEATION": s0_hint,
        "STAGE_1_SPECIFICATION": "Delegate via 'task' to @Researcher to author 'manifest.json' from approved RESEARCH.md / SOURCE_OCR.md.",
        "STAGE_2_MAPPING": "Delegate via 'task' to @Formalist to author 'mapping.json' from approved manifest.json.",
        "STAGE_3_IMPLEMENTATION": "Delegate via 'task' to @Engineer to author/update 'requirements.txt', provision './.venv', and implement/repair production '*.py' code.",
        "STAGE_4_VERIFICATION": "Delegate via 'task' to @Auditor to execute clean-room 'verify_*.py' tests and write 'VERIFICATION.md' or 'AUDIT_FEEDBACK.json'.",
    }.get(current_stage)
    if stage_routing_hint:
        anchor_block = f"\n\n<stage_anchor>\n[ACTIVE STAGE: {current_stage}] {stage_routing_hint}\n</stage_anchor>"
        return {"modifiedTransformedPrompt": transformed_prompt + anchor_block}
    return {}


def handle_subagent_stop(payload):
    r"""Validate deliverable persistence and route FSM transitions on stop.

    Phases:
      - Phase 1: Channel-Token Stripping, On-Disk Deliverable Verification & Compaction Yield
      - Phase 2: Cryptographic Handshake Ledger & Hash-Bound Specialist Briefings
      - Phase 3: Bidirectional Reflexion Self-Repair & Contract Escalation Rewinds
      - Phase 4: Deterministic Stage Transition Routing & Persistent FSM Sealing

    Args:
        payload: Hook stdin JSON dictionary from ``subagentStop``.

    Returns:
        Dictionary containing ``decision``/``reason`` on missing deliverables
        or ``modifiedResponse`` on completion.
    """
    session_id = get_session_id(payload)
    agent_id = str(payload.get("agentId") or payload.get("agent_id") or "").strip()
    cwd = get_payload_cwd(payload)
    agent_name = (
        payload.get("agentName")
        or payload.get("agent_name")
        or payload.get("agentType")
        or payload.get("agent_type")
        or "Subagent"
    )
    raw_response = payload.get("response") or payload.get("last_assistant_message") or ""
    stop_hook_active = bool(payload.get("stop_hook_active"))
    fsm = load_pipeline_fsm(cwd)
    cfg = load_pipeline_config(cwd)

    # --- Phase 1: Channel-Token Stripping, On-Disk Deliverable Verification & Compaction Yield ---
    cleaned_response = re.sub(r"<\|?channel\|?>", "", raw_response)
    cleaned_response = re.sub(r"<\|channel>[a-z_]+", "", cleaned_response)
    cleaned_response = re.sub(r"<\|?turn\|?>", "", cleaned_response)
    cleaned_response = normalize_markdown_file_links(re.sub(r"<\|turn>[a-z_]+", "", cleaned_response).strip())

    status_match = re.search(r"-\s*\*\*Status:\*\*\s*([A-Za-z_]+)", cleaned_response, re.IGNORECASE)
    status_val = status_match.group(1).upper() if status_match else "COMPLETED"

    current_stage = fsm.get("current_stage", "")
    iso_now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    launch_stg = str(
        (load_session_state(agent_id).get("subagent_launch_stage") if agent_id else None)
        or load_session_state(session_id).get("subagent_launch_stage")
        or ""
    )

    # Bounded multi-chance subagentStop remediation: VS Code sets sticky `stop_hook_active=True` after the very first
    # subagentStop block, so track per-agent `total_subagent_stop_blocks < 3` when `agent_id` is present.
    child_st_early = load_session_state(agent_id) if agent_id else {}
    subagent_stop_exhausted = (
        int(child_st_early.get("total_subagent_stop_blocks") or 0) >= 3
        if agent_id
        else stop_hook_active
    )

    def _bump_subagent_stop_blocks() -> None:
        if agent_id:
            st_b = load_session_state(agent_id)
            st_b["total_subagent_stop_blocks"] = int(st_b.get("total_subagent_stop_blocks") or 0) + 1
            save_session_state(agent_id, st_b)

    # 1a. Block premature completion if the latest write left an unresolved postToolUse <validation_error>
    child_unres = child_st_early.get("unresolved_validation_error") if agent_id else None
    sess_unres = load_session_state(session_id).get("unresolved_validation_error")
    active_unres = child_unres or sess_unres
    if active_unres and not subagent_stop_exhausted:
        _bump_subagent_stop_blocks()
        return {
            "decision": "block",
            "reason": (
                f"<subagent_directive>\n<validation_error>\n[ERROR] UNRESOLVED LOCAL VALIDATION FAILURE: {active_unres}\n"
                "Apply a minimal scoped 'edit' to fix the artifact on disk, clear all validation errors, and conclude with '### Stage Briefing' (when applicable) followed by '## Subagent Handshake Receipt'.\n"
                "</validation_error>\n</subagent_directive>"
            ),
        }

    # 1b. Mandatory On-Disk Artifact Verification before accepting completion
    is_local_critic_stop = (
        agent_name == "Auditor"
        and launch_stg != "STAGE_4_VERIFICATION"
        and (
            launch_stg in ("STAGE_1_CRITIC", "STAGE_2_CRITIC")
            or (cwd / "LOCAL_CRITIC.json").is_file()
            or (
                not (cwd / "AUDIT_FEEDBACK.json").is_file()
                and current_stage in ("STAGE_1_SPECIFICATION", "STAGE_2_MAPPING", "CHECKPOINT_1_AWAITING_APPROVAL", "CHECKPOINT_2_AWAITING_APPROVAL", "STAGE_3_IMPLEMENTATION")
                and not (cwd / "VERIFICATION.md").is_file()
            )
        )
    )
    if agent_name == "Auditor" and current_stage in ("STAGE_1_CRITIC", "STAGE_2_CRITIC"):
        expected_artifact, artifact_err = (
            "LOCAL_CRITIC.json",
            f"Expected Local Semantic Critic verdict 'LOCAL_CRITIC.json' has not been written yet at '{cwd / 'LOCAL_CRITIC.json'}'.",
        )
    elif is_local_critic_stop:
        status_val = "COMPLETED"
        expected_artifact, artifact_err = (
            "manifest.json" if (launch_stg == "STAGE_1_CRITIC" or not (cwd / "mapping.json").is_file()) else "mapping.json",
            None,
        )
    else:
        # Derive Auditor FAILED vs SUCCESS from disk state when AUDIT_FEEDBACK.json was just recorded for an active rewind
        if agent_name == "Auditor" and fsm.get("pending_rewind") and (cwd / "AUDIT_FEEDBACK.json").is_file() and not (cwd / "VERIFICATION.md").is_file():
            status_val = "FAILED"
        expected_artifact, artifact_err = verify_expected_subagent_artifact(
            agent_name, current_stage, status_val, cleaned_response, cwd
        )
        # Prevent truncated `<|thought|>` turns (`cleaned_response == ""` / `status_match is None`) from silently
        # returning a 0-edit no-op when an existing deliverable on disk was supposed to be patched during repair/rewind.
        if (
            artifact_err is None
            and agent_name in ("Engineer", "Formalist", "Researcher")
            and status_match is None
            and expected_artifact
            and (cwd / expected_artifact).is_file()
        ):
            pre_sha_early = str(child_st_early.get("pre_artifact_sha256") or "")
            cur_sha_early = compute_file_sha256(cwd / expected_artifact) or ""
            repair_pending_on_disk = (cwd / "AUDIT_FEEDBACK.json").is_file() or (cwd / "LOCAL_CRITIC.json").is_file()
            if pre_sha_early and cur_sha_early == pre_sha_early and (not cleaned_response.strip() or repair_pending_on_disk):
                artifact_err = (
                    f"Turn ended before modifying existing '{expected_artifact}' (SHA-256 unchanged from pre-stage state) "
                    "or emitting '## Subagent Handshake Receipt'. Apply your minimal targeted 'edit' immediately without "
                    "repeating prior internal reasoning, then emit '## Subagent Handshake Receipt'."
                )
    # RC-3: When `@Scribe` has 0 staged visual plates in `.pipeline_scratch/ocr_staging/` and `SOURCE_OCR.md`
    # does not exist, do NOT pressure `@Scribe` into fabricating `SOURCE_OCR.md` — fail cleanly in 1 pass.
    scribe_staging_dir = cwd / SCRATCH_DIR_NAME / "ocr_staging"
    scribe_has_staged_plates = scribe_staging_dir.is_dir() and any(p.is_file() for p in scribe_staging_dir.iterdir())
    skip_scribe_confab_block = bool(
        agent_name == "Scribe"
        and status_match is None
        and not (cwd / "SOURCE_OCR.md").is_file()
        and not scribe_has_staged_plates
        and any(tok in cleaned_response.lower() for tok in ("does not exist", "missing", "cannot", "no visual", "failed"))
    )
    esc_mode_stop = str(((cfg.get("experiments") or {}) if isinstance(cfg, dict) else {}).get("engineer_escalation_mode") or "handoff_and_contract_escalation")
    is_compaction_yield = False
    if artifact_err:
        is_truncated_thought_turn = not cleaned_response.strip()
        trunc_blocks_so_far = int(
            (child_st_early if agent_id else load_session_state(session_id)).get("truncated_thought_blocks") or 0
        )
        sess_comp_yields = int(load_session_state(session_id).get("compaction_yields_in_stage") or 0)
        if is_truncated_thought_turn and not skip_scribe_confab_block:
            if esc_mode_stop == "in_window_retry" and trunc_blocks_so_far == 0 and not subagent_stop_exhausted:
                _bump_subagent_stop_blocks()
                target_trunc_key = agent_id or session_id
                if target_trunc_key:
                    update_session_state(
                        target_trunc_key,
                        lambda st: st.update({"truncated_thought_blocks": int(st.get("truncated_thought_blocks") or 0) + 1}),
                    )
                return {
                    "decision": "block",
                    "reason": (
                        "<subagent_directive>\n<truncation_recovery>\n"
                        f"[OUTPUT TOKEN CEILING HIT INSIDE <|thought|>]: Your prior turn hit the 16,384-token output limit inside `<|thought|>` before emitting a tool call ({artifact_err}).\n"
                        f"Keep `<|thought|>` strictly under 10 lines (NEVER draft Python code or docstrings inside `<|thought|>`) and invoke `'edit'` or `'create'` for '{expected_artifact or 'artifact'}' immediately.\n"
                        "</truncation_recovery>\n</subagent_directive>"
                    ),
                }
            if sess_comp_yields < 2 and agent_name in ALLOWED_SUBAGENTS and agent_name != "Orchestrator":
                is_compaction_yield = True
                status_val = "COMPACTION_YIELD"
                # Persist tail of truncated `<|thought|>` so the fresh context window avoids repeating full-file `view` or re-derivation
                trunc_tail = str(payload.get("reasoningText") or payload.get("reasoning") or "").strip()
                tx_path_str = str(payload.get("transcriptPath") or payload.get("transcript_path") or "").strip()
                if not trunc_tail and tx_path_str and Path(tx_path_str).is_file():
                    try:
                        with open(tx_path_str, "rb") as tx_f:
                            tx_f.seek(0, os.SEEK_END)
                            tx_sz = tx_f.tell()
                            tx_f.seek(max(0, tx_sz - 131072))
                            tail_chunk = tx_f.read().decode("utf-8", errors="replace")
                        for tx_ln in reversed(tail_chunk.splitlines()):
                            if '"assistant.message"' in tx_ln and '"reasoningText"' in tx_ln:
                                try:
                                    ev_obj = json.loads(tx_ln)
                                    rt_cand = str((ev_obj.get("data") or {}).get("reasoningText") or "").strip()
                                    if rt_cand:
                                        trunc_tail = rt_cand
                                        break
                                except Exception:
                                    continue
                    except OSError:
                        pass
                note_body = trunc_tail[-1200:] if trunc_tail else "Prior turn exhausted the 16,384-token output ceiling inside <|thought|> before calling a tool."
                try:
                    note_p = cwd / SCRATCH_DIR_NAME / "prior_truncation_note.txt"
                    note_p.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_text(note_p, note_body)
                except Exception:
                    pass
                update_session_state(
                    session_id,
                    lambda st: st.update({"compaction_yields_in_stage": int(st.get("compaction_yields_in_stage") or 0) + 1}),
                )
                cleaned_response = (
                    f"## Subagent Handshake Receipt\n"
                    f"- **Stage:** {current_stage}\n"
                    f"- **Status:** COMPACTION_YIELD\n"
                    f"- **Artifact:** {expected_artifact or 'pending'}\n"
                    f"- **Note:** Yielding back to @Orchestrator after output-cap `<|thought|>` truncation so a fresh @{agent_name} context window resumes cleanly with `<prior_attempt_diagnosis>`."
                )
        is_eng_upstream_esc = bool(
            agent_name == "Engineer"
            and status_val in ("FAILED", "FAIL")
            and ("MANIFEST_DEFECT" in cleaned_response.upper() or "MAPPING_DEFECT" in cleaned_response.upper())
        )
        if not is_compaction_yield and not is_eng_upstream_esc:
            if not subagent_stop_exhausted and not skip_scribe_confab_block:
                _bump_subagent_stop_blocks()
                return {
                    "decision": "block",
                    "reason": (
                        f"<subagent_directive>\n<validation_error>\n[ERROR] MISSING OR INVALID ARTIFACT ON DISK: {artifact_err}\n"
                        f"Persist the valid '{expected_artifact or 'artifact'}' on disk (via 'edit' if a draft already exists, or 'create' if missing), then conclude with '### Stage Briefing' (when applicable) followed by '## Subagent Handshake Receipt'.\n"
                        "</validation_error>\n</subagent_directive>"
                    )
                }
            status_val = "FAILED"
            cleaned_response = re.sub(
                r"(-\s*\*\*Status:\*\*\s*)(?:SUCCESS|CONDITIONAL_PASS|COMPLETED)",
                r"\1FAILED",
                cleaned_response,
                flags=re.IGNORECASE,
            )
            cleaned_response += (
                f"\n\n<validation_error>\n[ERROR] ARTIFACT PERSISTENCE FAILURE: @{agent_name} concluded without persisting "
                f"a valid artifact on disk ({artifact_err}). Status downgraded to FAILED.\n</validation_error>"
            )

    # Pop active_agent in child agentId and restore parent session's active_agent (e.g. 'Orchestrator' at Depth 1)
    audit_already_counted = False
    restored_parent_agent = None
    if agent_id:
        child_state = load_session_state(agent_id)
        audit_already_counted = bool(child_state.get("audit_feedback_counted"))
        restored_parent_agent = child_state.get("previous_parent_agent")
        child_state["active_agent"] = None
        child_state["previous_parent_agent"] = None
        child_state["audit_feedback_counted"] = False
        child_state.pop("subagent_launch_stage", None)
        save_session_state(agent_id, child_state)

    stage_t0 = float(child_state.get("stage_started_ts") or 0.0) if agent_id else 0.0
    pre_art_sha = str(child_state.get("pre_artifact_sha256") or "") if agent_id else ""
    post_art_sha = str(compute_file_sha256(cwd / expected_artifact) or "") if (expected_artifact and (cwd / expected_artifact).is_file()) else ""
    if status_val not in ("FAILED", "FAIL", "COMPACTION_YIELD"):
        update_session_state(session_id, lambda st: st.pop("compaction_yields_in_stage", None))
    state = load_session_state(session_id)
    audit_already_counted = audit_already_counted or bool(state.get("audit_feedback_counted"))
    state["active_agent"] = restored_parent_agent
    state["audit_feedback_counted"] = False
    is_engineer_manifest_rewind = bool(
        agent_name == "Engineer"
        and esc_mode_stop == "handoff_and_contract_escalation"
        and status_val in ("FAILED", "FAIL")
        and "MANIFEST_DEFECT" in cleaned_response.upper()
        and int(fsm.get("repair_attempts", 0) or 0) < int(cfg.get("max_repair_attempts", 3) or 3)
    )
    is_engineer_mapping_rewind = bool(
        agent_name == "Engineer"
        and esc_mode_stop == "handoff_and_contract_escalation"
        and status_val in ("FAILED", "FAIL")
        and not is_engineer_manifest_rewind
        and "MAPPING_DEFECT" in cleaned_response.upper()
        and int(fsm.get("repair_attempts", 0) or 0) < int(cfg.get("max_repair_attempts", 3) or 3)
    )
    is_reflexion_rewind_stop = bool(
        (agent_name == "Auditor" and (fsm.get("pending_rewind") or (cwd / "AUDIT_FEEDBACK.json").is_file()))
        or is_engineer_mapping_rewind
        or is_engineer_manifest_rewind
    )
    state["last_subagent_failed"] = bool(
        status_val in ("FAILED", "FAIL")
        and agent_name != "Orchestrator"
        and not is_reflexion_rewind_stop
    )
    state.pop("subagent_launch_stage", None)
    save_session_state(session_id, state)
    if agent_name in ALLOWED_SUBAGENTS:
        append_run_telemetry_event(
            "stage_span",
            {
                "agent": str(agent_name),
                "stage": str(current_stage),
                "status": str(status_val),
                "artifact": str(expected_artifact or ""),
                "duration_s": round(time.time() - stage_t0, 3) if stage_t0 > 0 else None,
                "sha256_before": pre_art_sha[:16] if pre_art_sha else None,
                "sha256_after": post_art_sha[:16] if post_art_sha else None,
                "artifact_mutated": bool(post_art_sha and post_art_sha != pre_art_sha),
                "noop_stage": bool(pre_art_sha and post_art_sha and pre_art_sha == post_art_sha),
                "experiments": dict((cfg.get("experiments") or {}) if isinstance(cfg, dict) else {}),
            },
            session_id=session_id,
        )

    if agent_name == "Orchestrator":
        reconcile_steered_user_commands(payload)
        fsm_now = load_pipeline_fsm(cwd)
        orch_stage = str(fsm_now.get("current_stage", ""))
        if orch_stage == "PRE_RUN_HALT" and fsm_now.get("post_archive_halt_latch"):
            last_arch = fsm_now.get("last_archived_cycle") or {}
            arch_slug = str(last_arch.get("concept_slug") or "archived_specification")
            topic_brief = str(last_arch.get("topic_result_brief") or "").strip() or render_stage5_topic_result_briefing(cwd, arch_slug)
            # Return pure, tag-free user-facing Markdown directly so neither VS Code's Orchestrator widget nor @Facet can leak XML/bracketed flags.
            return {"modifiedResponse": topic_brief}
        if orch_stage.startswith("CHECKPOINT_"):
            state["checkpoint_delivery_pending"] = True
            save_session_state(session_id, state)
            cp_brief = render_checkpoint_briefing_block(cwd, fsm_now)
            # Return pure, tag-free user-facing Markdown directly so neither VS Code's Orchestrator widget nor @Facet can leak XML/bracketed flags.
            return {"modifiedResponse": cp_brief}
        # Scrub leaked routing headers, XML tags, or misleading `/approve` footers when `@Orchestrator` halts on a non-checkpoint blocker.
        scrubbed_halt = re.sub(r"\n*\*\*Checkpoint Summary:\*\*\s*\n*\[ACTIVE STAGE:[^\n]*\n*(?:Never delegate[^\n]*\n*)?", "\n", cleaned_response)
        scrubbed_halt = re.sub(r"\[ACTIVE STAGE:[^\n]*\n?", "", scrubbed_halt)
        scrubbed_halt = re.sub(r"</?[a-z_]+(?:\s[^>]*)?>", "", scrubbed_halt)
        scrubbed_halt = re.sub(
            r"\n*(?:Ask a question about this stage,\s*reply\s*`?/approve`?\s*to continue,\s*or\s*`?/reset`?\s*to start over\.?|Reply\s*`?/approve`?\s*to continue\s*·\s*`?/reset`?\s*to start over\s*·\s*or ask a question\.?)\s*",
            "\n",
            scrubbed_halt,
            flags=re.IGNORECASE,
        ).strip()
        scrubbed_halt = prelinkify_briefing_workspace_mentions(sanitize_user_briefing_prose(scrubbed_halt), cwd)
        if not scrubbed_halt:
            scrubbed_halt = "Execution paused before reaching a checkpoint because a required input or specification precondition is missing. Provide the missing input or reply `/reset` to start over."
        return {"modifiedResponse": scrubbed_halt.strip()}

    # --- Phase 2: Cryptographic Handshake Ledger & Hash-Bound Specialist Briefings ---
    if status_val in ["SUCCESS", "CONDITIONAL_PASS", "COMPLETED"]:
        trace_sha = write_reasoning_trace(cwd, agent_name, cleaned_response)
        artifact_path = (cwd / expected_artifact) if expected_artifact else None
        art_sha = compute_file_sha256(artifact_path) if (artifact_path and artifact_path.is_file()) else trace_sha
        if "handshakes" not in fsm:
            fsm["handshakes"] = []
        handshake_entry = {
            "agent": agent_name,
            "stage": current_stage,
            "artifact": expected_artifact,
            "sha256": art_sha,
            "status": status_val,
            "timestamp": iso_now,
        }
        if trace_sha:
            handshake_entry["trace_file"] = f"{SCRATCH_DIR_NAME}/{REASONING_TRACE_SLOTS.get(agent_name, '')}"
        fsm["handshakes"].append(handshake_entry)
        # Persist stage- and SHA-256-bound specialist briefings (excluding local critic and intermediate failure feedback).
        if (
            agent_name in ("Researcher", "Formalist", "Engineer", "Auditor")
            and not is_local_critic_stop
            and expected_artifact not in (None, "LOCAL_CRITIC.json")
            and not (agent_name == "Auditor" and expected_artifact == "AUDIT_FEEDBACK.json" and fsm.get("current_stage") != "CHECKPOINT_REPAIR_ESCALATION")
        ):
            spec_brief_txt = extract_specialist_stage_briefing(cleaned_response)
            if spec_brief_txt:
                bucket_key = {
                    "Researcher": "specification" if expected_artifact == "manifest.json" else "research",
                    "Formalist": "mapping",
                    "Engineer": "implementation",
                    "Auditor": "verification",
                }.get(agent_name, agent_name.lower())
                _save_stage_briefing_entry(
                    cwd,
                    fsm,
                    bucket_key,
                    spec_brief_txt,
                    artifact_name=str(expected_artifact or ""),
                    artifact_sha256=str(art_sha or ""),
                )
        # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
        save_pipeline_fsm(cwd, fsm)

    # --- Phase 3: Bidirectional Reflexion Self-Repair & Contract Escalation Rewinds ---
    if is_engineer_manifest_rewind:
        max_rep_em = int(cfg.get("max_repair_attempts", 3) or 3)
        cur_rep_em = int(fsm.get("repair_attempts", 0) or 0) + 1
        fsm["repair_attempts"] = cur_rep_em
        mf_match = re.search(r"MANIFEST_DEFECT\s*[:\-]?\s*(.+)", cleaned_response, re.IGNORECASE)
        mf_diag_txt = (
            mf_match.group(1).strip()[:900]
            if mf_match
            else "Locked equation in manifest.json empirically violated a defining mathematical identity during Stage 3 smoke evaluation."
        )
        fb_p_em = cwd / "AUDIT_FEEDBACK.json"
        fb_j_em: dict[str, object] = {}
        if fb_p_em.is_file():
            try:
                fb_j_em = json.loads(fb_p_em.read_text(encoding="utf-8"))
            except Exception:
                fb_j_em = {}
        fb_j_em["overall_status"] = "FAIL"
        fb_j_em["failure_classification"] = "MANIFEST_DEFECT"
        fb_j_em["rewind_target"] = "STAGE_1_SPECIFICATION"
        existing_fails_m = list(fb_j_em.get("failures") or []) if isinstance(fb_j_em.get("failures"), list) else []
        existing_fails_m.insert(0, {
            "obligation_id": "OBL-EQUATION-CONSISTENCY",
            "spec_id": "SPEC-1",
            "failure_classification": "MANIFEST_DEFECT",
            "diagnosis": f"@Engineer Upstream Equation Consistency Escalation: {mf_diag_txt}",
        })
        fb_j_em["failures"] = existing_fails_m
        atomic_write_text(fb_p_em, json.dumps(fb_j_em, indent=2) + "\n")
        rw_stage_m, _, rw_role_m = apply_audit_rewind_transition(cwd, fsm, fb_j_em)
        save_pipeline_fsm(cwd, fsm)
        directive_em = (
            f"\n\n<subagent_directive>\n"
            f"[WARNING] Upstream Manifest Equation Escalation Rewind Active: Attempt {cur_rep_em}/{max_rep_em}\n"
            f"Active State: [{rw_stage_m}] Delegating to @{rw_role_m}\n\n"
            "[MANDATORY DIRECTIVE FOR ORCHESTRATOR - STAGE 1 MANIFEST REWIND]:\n"
            f"- @Engineer proved an empirical algebraic inconsistency in locked 'manifest.json' ({mf_diag_txt}).\n"
            "- 'AUDIT_FEEDBACK.json' has been updated with 'MANIFEST_DEFECT' and Checkpoints 1 & 2 have been unsealed while preserving all files on disk.\n"
            "- Immediately delegate to @Researcher (Stage 1) via synchronous 'task' to apply a minimal scoped 'edit' to 'manifest.json' resolving the inconsistent equation.\n"
            "</subagent_directive>"
        )
        return {"modifiedResponse": cleaned_response + directive_em}

    if is_engineer_mapping_rewind:
        max_rep_eng = int(cfg.get("max_repair_attempts", 3) or 3)
        cur_rep_eng = int(fsm.get("repair_attempts", 0) or 0)
        if cur_rep_eng == 0:
            cur_rep_eng = 1
            fsm["repair_attempts"] = 1
        m_diag_match = re.search(r"MAPPING_DEFECT\s*[:\-]?\s*(.+)", cleaned_response, re.IGNORECASE)
        eng_diag_txt = (
            m_diag_match.group(1).strip()[:800]
            if m_diag_match
            else "Public interface in mapping.json omits a required mathematical parameter needed to satisfy verification obligations."
        )
        fb_p_eng = cwd / "AUDIT_FEEDBACK.json"
        fb_j_eng: dict[str, object] = {}
        if fb_p_eng.is_file():
            try:
                fb_j_eng = json.loads(fb_p_eng.read_text(encoding="utf-8"))
            except Exception:
                fb_j_eng = {}
        fb_j_eng["overall_status"] = "FAIL"
        fb_j_eng["failure_classification"] = "MAPPING_DEFECT"
        fb_j_eng["rewind_target"] = "STAGE_2_MAPPING"
        existing_fails = list(fb_j_eng.get("failures") or []) if isinstance(fb_j_eng.get("failures"), list) else []
        existing_fails.insert(0, {
            "obligation_id": "OBL-INTERFACE",
            "spec_id": "SPEC-1",
            "failure_classification": "MAPPING_DEFECT",
            "diagnosis": f"@Engineer Contract Solvability Escalation: {eng_diag_txt}",
        })
        fb_j_eng["failures"] = existing_fails
        atomic_write_text(fb_p_eng, json.dumps(fb_j_eng, indent=2) + "\n")
        rw_stage_e, _, rw_role_e = apply_audit_rewind_transition(cwd, fsm, fb_j_eng)
        save_pipeline_fsm(cwd, fsm)
        directive_eng = (
            f"\n\n<subagent_directive>\n"
            f"[WARNING] Interface Contract Escalation Rewind Active: Attempt {cur_rep_eng}/{max_rep_eng}\n"
            f"Active State: [{rw_stage_e}] Delegating to @{rw_role_e}\n\n"
            "[MANDATORY DIRECTIVE FOR ORCHESTRATOR - STAGE 2 INTERFACE REWIND]:\n"
            f"- @Engineer identified an unsolvable interface omission in 'mapping.json' ({eng_diag_txt}).\n"
            "- 'AUDIT_FEEDBACK.json' has been updated with 'MAPPING_DEFECT' and Checkpoint 2 has been unsealed while preserving all files on disk.\n"
            "- Immediately delegate to @Formalist (Stage 2) via synchronous 'task' to apply a minimal scoped 'edit' to 'mapping.json' resolving the missing parameter/interface contract.\n"
            "</subagent_directive>"
        )
        return {"modifiedResponse": cleaned_response + directive_eng}

    if agent_name == "Auditor":
        if status_val in ["FAILED", "FAIL"]:
            max_rep = cfg.get("max_repair_attempts", 3)
            if audit_already_counted:
                repair_attempts = fsm.get("repair_attempts", 1)
            else:
                repair_attempts = fsm.get("repair_attempts", 0) + 1
                fsm["repair_attempts"] = repair_attempts

            if repair_attempts < max_rep and fsm.get("current_stage") != "CHECKPOINT_REPAIR_ESCALATION":
                if not audit_already_counted:
                    fb_p_stop = cwd / "AUDIT_FEEDBACK.json"
                    fb_j_stop = json.loads(fb_p_stop.read_text(encoding="utf-8")) if fb_p_stop.is_file() else {}
                    # Classify audit defects (`MANIFEST` / `MAPPING` / `IMPLEMENTATION`) and rewind while keeping files on disk.
                    rw_stage_s, _, rw_role_s = apply_audit_rewind_transition(cwd, fsm, fb_j_stop)
                else:
                    rw_stage_s = str(fsm.get("current_stage") or "STAGE_3_IMPLEMENTATION")
                    if rw_stage_s not in ("STAGE_1_SPECIFICATION", "STAGE_2_MAPPING", "STAGE_3_IMPLEMENTATION", "STAGE_4_VERIFICATION"):
                        rw_stage_s = "STAGE_3_IMPLEMENTATION"
                        fsm["current_stage"] = rw_stage_s
                    rw_role_s = {
                        "STAGE_1_SPECIFICATION": "Researcher",
                        "STAGE_2_MAPPING": "Formalist",
                        "STAGE_3_IMPLEMENTATION": "Engineer",
                        "STAGE_4_VERIFICATION": "Auditor",
                    }[rw_stage_s]
                # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
                save_pipeline_fsm(cwd, fsm)
                if rw_stage_s == "STAGE_1_SPECIFICATION":
                    action_instr = (
                        "- Upstream MANIFEST_DEFECT recorded in AUDIT_FEEDBACK.json (all artifacts preserved on disk; C1 & C2 unsealed).\n"
                        "- Immediately delegate to @Researcher (Stage 1) to apply a scoped 'edit' to 'manifest.json' incorporating AUDIT_FEEDBACK.json, then follow the deterministic lifecycle forward."
                    )
                elif rw_stage_s == "STAGE_2_MAPPING":
                    action_instr = (
                        "- Upstream MAPPING_DEFECT recorded in AUDIT_FEEDBACK.json (all artifacts preserved on disk; C2 unsealed).\n"
                        "- Immediately delegate to @Formalist (Stage 2) to apply a scoped 'edit' to 'mapping.json' incorporating AUDIT_FEEDBACK.json, then follow the deterministic lifecycle forward."
                    )
                elif rw_stage_s == "STAGE_4_VERIFICATION":
                    action_instr = (
                        "- TEST_HARNESS_DEFECT recorded in AUDIT_FEEDBACK.json (staying in Stage 4).\n"
                        "- Immediately delegate to @Auditor (Stage 4) to apply a scoped 'edit' to 'verify_*.py', re-run pytest, and emit 'VERIFICATION.md'."
                    )
                else:
                    action_instr = (
                        "- DO NOT modify specifications or weaken tolerances.\n"
                        "- Immediately delegate to @Engineer with the task: 'Inspect AUDIT_FEEDBACK.json and repair code implementation to satisfy failed verification obligations.'"
                    )
                directive = (
                    f"\n\n<subagent_directive>\n"
                    f"[WARNING] Reflexion Repair / Spec Rewind Loop Active: Attempt {repair_attempts}/{max_rep}\n"
                    f"Active State: [{rw_stage_s}] Delegating to @{rw_role_s}\n\n"
                    "[MANDATORY DIRECTIVE FOR ORCHESTRATOR - BOUNDED REPAIR]:\n"
                    f"- Verification failed on attempt {repair_attempts}/{max_rep}.\n"
                    f"{action_instr}\n"
                    "</subagent_directive>"
                )
                # Append `@Orchestrator`'s next-step `<subagent_directive>` onto the sanitized specialist output.
                return {"modifiedResponse": cleaned_response + directive}
            else:
                fsm["current_stage"] = "CHECKPOINT_REPAIR_ESCALATION"
                # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
                save_pipeline_fsm(cwd, fsm)
                esc_brief = render_checkpoint_briefing_block(cwd, fsm)
                directive = (
                    f"\n\n<subagent_directive>\n"
                    f"[ERROR] Checkpoint Escalation: Circuit Breaker Tripped after {max_rep} Failed Attempts\n"
                    "Active State: [CHECKPOINT_REPAIR_ESCALATION] Awaiting Human Guidance\n\n"
                    "[MANDATORY DIRECTIVE FOR ORCHESTRATOR - REPAIR ESCALATION]:\n"
                    f"- Maximum automated repair attempts ({max_rep}) reached without satisfying all obligations.\n"
                    "- Emit ZERO tool calls, output ONLY the 3-line '>' Gate Card below verbatim, and HALT immediately:\n"
                    f"{esc_brief}\n"
                    "</subagent_directive>"
                )
                # Append `@Orchestrator`'s next-step `<subagent_directive>` onto the sanitized specialist output.
                return {"modifiedResponse": cleaned_response + directive}
        else:
            # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
            save_pipeline_fsm(cwd, fsm)

    # --- Phase 4: Deterministic Stage Transition Routing & Persistent FSM Sealing ---
    auto_arch_res = None
    if status_val not in ["FAILED", "FAIL", "COMPACTION_YIELD"]:
        if agent_name == "Scribe":
            clean_ocr_staging(cwd)
            fsm["ocr_sha256"] = compute_file_sha256(cwd / "SOURCE_OCR.md")
            if (cwd / "SOURCE_TEXT.md").is_file():
                fsm["text_sha256"] = compute_file_sha256(cwd / "SOURCE_TEXT.md")
            save_pipeline_fsm(cwd, fsm)
            transition_banner = "[TRANSITION: OCR -> RESEARCHER] SOURCE_OCR.md Verified; Delegating to @Researcher"

        elif agent_name == "Researcher":
            if expected_artifact is None:
                transition_banner = "[TRANSITION: RESEARCHER -> OCR] Delegating Visual/PDF Extraction to @Scribe (SOURCE_OCR.md)"
            elif expected_artifact == "RESEARCH.md":
                r_sha = compute_file_sha256(cwd / "RESEARCH.md")
                gate_active = apply_artifact_gate(
                    cwd, fsm, "checkpoint_0", r_sha,
                    "CHECKPOINT_0_AWAITING_APPROVAL", "STAGE_1_SPECIFICATION", cfg
                )
                # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
                save_pipeline_fsm(cwd, fsm)
                transition_banner = (
                    "[CHECKPOINT: S0 -> C0] Awaiting Human Candidate Approval before Stage 1"
                    if gate_active
                    else "[TRANSITION: S0 -> S1] Candidate Brief Auto-Approved; Delegating to @Researcher for Stage 1 Specification"
                )
            else:
                m_sha = compute_file_sha256(cwd / "manifest.json")
                m_spec_cnt = 0
                try:
                    m_json = json.loads((cwd / "manifest.json").read_text(encoding="utf-8"))
                    m_spec_cnt = len(m_json.get("formal_specifications") or [])
                    if m_json.get("concept_name"):
                        fsm["concept_slug"] = slugify(m_json["concept_name"])
                except Exception:
                    pass
                lsc_mode_s1 = str(cfg.get("enable_local_spec_critic") or "off")
                s1_flagged, s1_note = (
                    run_systemone_spec_screen(cwd, "manifest.json", fsm, cfg)
                    if lsc_mode_s1 == "system1_s1_s2"
                    else (False, "")
                )
                if s1_flagged:
                    # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
                    save_pipeline_fsm(cwd, fsm)
                    transition_banner = f"[TRANSITION: S1_SYS1 -> S1] {s1_note}; Delegating to @Researcher for Scoped Edit on manifest.json"
                elif (
                    lsc_mode_s1 == "s1_and_s2"
                    or (
                        lsc_mode_s1 == "s1_only"
                        and m_spec_cnt >= 2
                        and ((cwd / "RESEARCH.md").is_file() or (cwd / "SOURCE_OCR.md").is_file())
                        and int(fsm.get("repair_attempts", 0) or 0) == 0
                    )
                ) and int((fsm.get("local_critic_attempts") or {}).get("s1", 0)) < 1:
                    fsm["current_stage"] = "STAGE_1_CRITIC"
                    # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
                    save_pipeline_fsm(cwd, fsm)
                    transition_banner = "[TRANSITION: S1 -> S1_CRITIC] Delegating to @Auditor for Local Manifest Semantic & Executable Audit"
                else:
                    # Under `slice_dependency_dag` rewind policy, if a `MANIFEST_DEFECT` edit updated only tolerances/ambiguities
                    # without changing core equations/variables and `mapping.json` + `<module>.py` remain valid, jump `S1 -> S4`.
                    rw_dag_s1 = str(((cfg.get("experiments") or {}) if isinstance(cfg, dict) else {}).get("rewind_dag_policy") or "linear_stage_cascade")
                    prw_s1 = fsm.get("pending_rewind") if isinstance(fsm.get("pending_rewind"), dict) else None
                    prev_mc_h = str((prw_s1 or {}).get("prev_manifest_core_hash") or "")
                    cur_mc_h = _compute_manifest_core_hash(cwd / "manifest.json")
                    decl_m_s1 = _get_declared_target_module(cwd)
                    can_skip_s2_s3 = False
                    if (
                        rw_dag_s1 == "slice_dependency_dag"
                        and prev_mc_h
                        and cur_mc_h == prev_mc_h
                        and (cwd / "mapping.json").is_file()
                        and decl_m_s1
                        and (cwd / decl_m_s1).is_file()
                    ):
                        try:
                            mp_d_s1 = json.loads((cwd / "mapping.json").read_text(encoding="utf-8"))
                            can_skip_s2_s3 = (
                                not validate_mapping_structure(mp_d_s1, cwd)
                                and not validate_target_module_entrypoints(cwd / decl_m_s1, cwd)
                            )
                        except Exception:
                            can_skip_s2_s3 = False
                    next_after_s1 = "STAGE_4_VERIFICATION" if can_skip_s2_s3 else "STAGE_2_MAPPING"
                    gate_active = apply_artifact_gate(
                        cwd, fsm, "checkpoint_1", m_sha,
                        "CHECKPOINT_1_AWAITING_APPROVAL", next_after_s1, cfg
                    )
                    if can_skip_s2_s3 and not gate_active:
                        fsm["pending_rewind"] = None
                        append_run_telemetry_event("rewind_skip_s2_s3", {"module": decl_m_s1}, session_id=session_id)
                    save_pipeline_fsm(cwd, fsm)
                    transition_banner = (
                        "[CHECKPOINT: S1 -> C1] Awaiting Human Manifest Approval before Stage 2"
                        if gate_active
                        else (
                            "[TRANSITION: S1 -> S4] Manifest Tolerances/Notes Updated (Core Equations Unchanged); Skipping Redundant Stages 2-3 and Delegating to @Auditor"
                            if can_skip_s2_s3
                            else "[TRANSITION: S1 -> S2] Manifest Auto-Approved; Delegating to @Formalist for Stage 2 Discretization"
                        )
                    )

        elif agent_name == "Formalist":
            map_sha = compute_file_sha256(cwd / "mapping.json")
            lsc_mode_s2 = str(cfg.get("enable_local_spec_critic") or "off")
            s2_flagged, s2_note = (
                run_systemone_spec_screen(cwd, "mapping.json", fsm, cfg)
                if lsc_mode_s2 == "system1_s1_s2"
                else (False, "")
            )
            if s2_flagged:
                # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
                save_pipeline_fsm(cwd, fsm)
                transition_banner = f"[TRANSITION: S2_SYS1 -> S2] {s2_note}; Delegating to @Formalist for Scoped Edit on mapping.json"
            elif lsc_mode_s2 in ("s2_only", "s1_and_s2"):
                fsm["current_stage"] = "STAGE_2_CRITIC"
                # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
                save_pipeline_fsm(cwd, fsm)
                transition_banner = "[TRANSITION: S2 -> S2_CRITIC] Delegating to @Auditor for Local Mapping Semantic Audit"
            else:
                # Incremental rewind policy (`interface_hash_skip_s3` / `slice_dependency_dag`): if a `MAPPING_DEFECT` rewind updated only
                # obligations/anchors without altering the implementation-governing interface slice and `<module>.py`
                # already passes `AST-4b`, advance directly to `STAGE_4_VERIFICATION` and skip redundant Stage 3.
                rw_dag_pol = str(((cfg.get("experiments") or {}) if isinstance(cfg, dict) else {}).get("rewind_dag_policy") or "linear_stage_cascade")
                prw_f5 = fsm.get("pending_rewind") if isinstance(fsm.get("pending_rewind"), dict) else None
                prev_if_h = str((prw_f5 or {}).get("prev_mapping_interface_hash") or "")
                cur_if_h = _compute_mapping_implementation_contract_hash(cwd / "mapping.json")
                decl_mod_f5 = _get_declared_target_module(cwd)
                can_skip_s3 = bool(
                    rw_dag_pol in ("interface_hash_skip_s3", "slice_dependency_dag")
                    and prev_if_h
                    and cur_if_h == prev_if_h
                    and decl_mod_f5
                    and (cwd / decl_mod_f5).is_file()
                    and not validate_target_module_entrypoints(cwd / decl_mod_f5, cwd)
                )
                next_after_s2 = "STAGE_4_VERIFICATION" if can_skip_s3 else "STAGE_3_IMPLEMENTATION"
                gate_active = apply_artifact_gate(
                    cwd, fsm, "checkpoint_2", map_sha,
                    "CHECKPOINT_2_AWAITING_APPROVAL", next_after_s2, cfg
                )
                if can_skip_s3 and not gate_active:
                    fsm["pending_rewind"] = None
                    append_run_telemetry_event("rewind_skip_s3", {"module": decl_mod_f5, "interface_hash": cur_if_h[:16]}, session_id=session_id)
                save_pipeline_fsm(cwd, fsm)
                transition_banner = (
                    "[CHECKPOINT: S2 -> C2] Awaiting Human Mapping Approval before Stage 3"
                    if gate_active
                    else (
                        "[TRANSITION: S2 -> S4] Mapping Obligations Updated (Public Interface Unchanged); Skipping Redundant Stage 3 and Delegating Directly to @Auditor"
                        if can_skip_s3
                        else "[TRANSITION: S2 -> S3] Mapping Auto-Approved; Delegating to @Engineer for Stage 3 Implementation"
                    )
                )

        elif agent_name == "Engineer":
            # Scrub any stale receipt reference to uncreated test_*.py / verify_*.py files
            for ghost_m in list(re.finditer(r"`?((?:test|verify|smoke)_[A-Za-z0-9_]+\.py)`?", cleaned_response)):
                if not (cwd / ghost_m.group(1)).exists():
                    cleaned_response = cleaned_response.replace(ghost_m.group(0), "a fresh clean-room verification suite")
            impl_candidates = _find_implementation_files(cwd, cfg)
            s3_leak_msg = (
                run_systemone_stage3_leakage_screen(cwd, impl_candidates[0], cfg)
                if (impl_candidates and not stop_hook_active)
                else None
            )
            if s3_leak_msg:
                return {
                    "decision": "block",
                    "reason": f"<subagent_directive>\n<validation_error>\n[ERROR] GENERALIZATION VIOLATION: {s3_leak_msg}\nApply a minimal scoped 'edit' to remove hardcoded test-case branches.\n</validation_error>\n</subagent_directive>",
                }
            fsm["current_stage"] = "STAGE_4_VERIFICATION"
            fsm["pending_rewind"] = None
            # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
            save_pipeline_fsm(cwd, fsm)
            transition_banner = "[TRANSITION: S3 -> S4] Delegating to @Auditor for Stage 4 Clean-Room Verification Audit"

        elif agent_name == "Auditor":
            fsm_fresh = load_pipeline_fsm(cwd)
            fresh_stg = str(fsm_fresh.get("current_stage") or current_stage)
            if launch_stg in ("STAGE_1_CRITIC", "STAGE_2_CRITIC") or current_stage in ("STAGE_1_CRITIC", "STAGE_2_CRITIC") or fresh_stg in ("STAGE_1_SPECIFICATION", "STAGE_2_MAPPING", "CHECKPOINT_1_AWAITING_APPROVAL", "CHECKPOINT_2_AWAITING_APPROVAL", "STAGE_3_IMPLEMENTATION"):
                fsm = fsm_fresh
                if fresh_stg == "STAGE_1_SPECIFICATION":
                    transition_banner = "[TRANSITION: S1_CRITIC -> S1] Local Critic Requested Scoped Edit on manifest.json; Delegating to @Researcher"
                elif fresh_stg == "STAGE_2_MAPPING" and (launch_stg == "STAGE_2_CRITIC" or (cwd / "LOCAL_CRITIC.json").is_file()):
                    transition_banner = "[TRANSITION: S2_CRITIC -> S2] Local Critic Requested Scoped Edit on mapping.json; Delegating to @Formalist"
                elif fresh_stg == "CHECKPOINT_1_AWAITING_APPROVAL":
                    transition_banner = "[CHECKPOINT: S1 -> C1] Awaiting Human Manifest Approval before Stage 2"
                elif fresh_stg == "STAGE_2_MAPPING" or launch_stg == "STAGE_1_CRITIC" or current_stage == "STAGE_1_CRITIC":
                    transition_banner = "[TRANSITION: S1 -> S2] Local Manifest Audit Passed; Delegating to @Formalist for Stage 2 Discretization"
                elif fresh_stg == "CHECKPOINT_2_AWAITING_APPROVAL":
                    transition_banner = "[CHECKPOINT: S2 -> C2] Awaiting Human Mapping Approval before Stage 3"
                else:
                    transition_banner = "[TRANSITION: S2 -> S3] Local Mapping Audit Passed; Delegating to @Engineer for Stage 3 Implementation"
            else:
                v_sha = compute_file_sha256(cwd / "VERIFICATION.md")
                gate_active = apply_artifact_gate(
                    cwd, fsm, "checkpoint_3", v_sha,
                    "CHECKPOINT_3_AWAITING_APPROVAL", "STAGE_5_ARCHIVAL_COMPLETE", cfg
                )
                # Write updated `.pipeline_state.json`, sync the Shadow Seal Ledger, and notify Facet Lens.
                save_pipeline_fsm(cwd, fsm)
                if gate_active:
                    transition_banner = "[CHECKPOINT: S4 -> C3] Awaiting Human Verification Sign-Off before Stage 5 Archival"
                else:
                    # Seal verified deliverables into `specs/<slug>/`, write `PROVENANCE.json`, and reset root state.
                    auto_arch_res = archive_completed_run(cwd, session_id=session_id)
                    transition_banner = "[TRANSITION: S4 -> S5] Verification Auto-Approved and Specification Archived to specs/"
        else:
            transition_banner = f"[TRANSITION: {current_stage}] Processing Stage Handoff"
    elif status_val == "COMPACTION_YIELD":
        transition_banner = f"[COMPACTION YIELD: {current_stage}] Re-delegating to Fresh @{agent_name} Context Window"
    else:
        transition_banner = f"[TRANSITION: {current_stage}] Resolving Subagent Failure"

    recent_artifacts = []
    if isinstance(auto_arch_res, dict) and auto_arch_res.get("target_dir"):
        arch_dir_p = Path(auto_arch_res["target_dir"])
        # Compute and verify SHA-256 hashes for each `fname` in `auto_arch_res.get('files', [])`.
        for fname in auto_arch_res.get("files", []):
            fpath = arch_dir_p / fname
            if fpath.is_file():
                f_hash = compute_file_sha256(fpath)
                hash_snippet = f" (sha256: {f_hash[:8]})" if f_hash else ""
                recent_artifacts.append(f"{format_chat_file_link(fname, fpath)}{hash_snippet}")
    else:
        impl_names_on_disk = [p.name for p in _find_implementation_files(cwd, cfg)]
        verif_names_on_disk = [p.name for p in sorted(cwd.glob("verify_*.py")) if p.is_file()]
        candidate_names = list(dict.fromkeys(
            ["SOURCE_TEXT.md", "SOURCE_OCR.md", "RESEARCH.md", "manifest.json", "mapping.json", "requirements.txt"]
            + impl_names_on_disk
            + verif_names_on_disk
            + ["VERIFICATION.md"]
        ))
        # Compute and verify SHA-256 hashes for each `fname` in `candidate_names`.
        for fname in candidate_names:
            fpath = cwd / fname
            if fpath.exists():
                f_hash = compute_file_sha256(fpath)
                hash_snippet = f" (sha256: {f_hash[:8]})" if f_hash else ""
                recent_artifacts.append(f"{format_chat_file_link(fname, fpath)}{hash_snippet}")

    disk_info = f" [Verified artifacts on disk: {', '.join(recent_artifacts)}]" if recent_artifacts else ""

    is_checkpoint = "[CHECKPOINT:" in transition_banner

    if status_val == "COMPACTION_YIELD":
        directive = (
            f"\n\n<subagent_directive>\n"
            f"[SUBAGENT CONTEXT COMPACTION — RE-DELEGATE IMMEDIATELY TO FRESH @{agent_name}]\n"
            f"Active State: {transition_banner}{disk_info}\n"
            f"@{agent_name} hit the output token ceiling inside `<|thought|>`, so its accumulated reasoning history was compacted by ending the subagent turn. "
            f"Call synchronous `task(agent_type=\"{agent_name}\", ...)` immediately so a fresh @{agent_name} context window finishes persisting '{expected_artifact or 'the stage deliverable'}' from the existing on-disk state.\n"
            "</subagent_directive>"
        )
    elif status_val in ["FAILED", "FAIL"]:
        directive = (
            f"\n\n<subagent_directive>\n"
            f"[WARNING] Subagent @{agent_name} Reported FAILURE\n"
            f"Active State: {transition_banner}\n"
            "1. Evaluate whether the subagent investigated an alternative formulation, paper, or acceptable domain condition.\n"
            f"2. If an acceptable grounded compromise exists, re-delegate to @{agent_name} to persist its designated deliverable noting explicit limitations.\n"
            "3. If fatal (such as missing prerequisite files in 'research/'), present the exact blocker concisely and halt immediately (do NOT append '/approve' when not paused at a CHECKPOINT_* gate; instruct the user how to stage the required input or run '/reset').\n"
            "</subagent_directive>"
        )
    elif is_checkpoint:
        c3_archival_guard = (
            "- CRITICAL (Checkpoint 3 — Stage 5 Archival HAS NOT HAPPENED YET): 'specs/' has NOT been created on disk yet. "
            "NEVER write 'Archival Complete', 'transitioning to Stage 5', or 'The task is complete' in this message; "
            "Stage 5 archival runs ONLY AFTER the user replies `/approve`.\n"
            if "S4 -> C3" in transition_banner
            else ""
        )
        cp_stage_name = str(fsm.get("current_stage") or "CHECKPOINT")
        directive = (
            f"\n\n<subagent_directive>\n"
            f"[GATE] Human Checkpoint Active: Subagent @{agent_name} Succeeded{disk_info}\n"
            f"Active State: {transition_banner}\n\n"
            "[MANDATORY DIRECTIVE FOR ORCHESTRATOR - HUMAN CHECKPOINT ACTIVE]:\n"
            "- A Human Review Checkpoint is active. Emit ZERO tool calls and ZERO emojis in this response (DO NOT call 'task', 'view', or 'sql' alongside your text).\n"
            f"{c3_archival_guard}"
            f"- Output ONLY '- **Status:** CONDITIONAL_PASS (Paused at {cp_stage_name}; awaiting /approve)' and HALT immediately (@Facet presents the user briefing automatically on return).\n"
            "</subagent_directive>"
        )
    else:
        directive = (
            f"\n\n<subagent_directive>\n"
            f"[INFO] Automated Transition: Subagent @{agent_name} Succeeded{disk_info}\n"
            f"Active State: {transition_banner}\n\n"
            "[MANDATORY DIRECTIVE FOR ORCHESTRATOR - AUTOMATED TRANSITION]:\n"
            "- This stage is fully automated and does not require human approval.\n"
            "- Briefly state what was produced in 1 sentence.\n"
            f"- Immediately issue the tool call to delegate to the next subagent as specified in {transition_banner}.\n"
            "</subagent_directive>"
        )

    clean_result = cleaned_response + directive
    # Append `@Orchestrator`'s next-step `<subagent_directive>` onto the sanitized specialist output.
    return {"modifiedResponse": clean_result}


def _format_plate_vector_glyph_anchor(cwd: Path, plate_path: Path) -> str:
    r"""Extract lossless ``CGPDFScanner`` math-token snippets for ``plate_path`` (empty on non-PDF raster plates)."""
    m_pg = re.search(r"page_0*(\d+)\.(?:png|jpg)$", plate_path.name, re.IGNORECASE)
    st_md = cwd / "SOURCE_TEXT.md"
    if not m_pg or not st_md.is_file():
        return ""
    pg_num = int(m_pg.group(1))
    try:
        st_full = st_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m_sec = re.search(
        rf"^## Page {pg_num} of \d+\s*$([\s\S]*?)(?=^## Page \d+ of \d+|\Z)",
        st_full,
        re.MULTILINE,
    )
    if not m_sec:
        return ""
    eq_snips: list[str] = []
    for ln_v in m_sec.group(1).splitlines():
        s_v = ln_v.strip()
        if any(tok in s_v for tok in ("[FRAC_BAR]", "^{", "_{", "=", "∑", "∏", "∫", "√", "†", "∗", "˜", "¯", "±", "∈", "≠")):
            if len(s_v) <= 280:
                eq_snips.append(s_v)
    if not eq_snips:
        return ""
    joined_snips = "\n".join(f"  {sn}" for sn in eq_snips[:24])
    return (
        f"\n<vector_glyph_anchor page=\"{pg_num}\">\n"
        f"[LOSSLESS PDF VECTOR GLYPH TOKENS FOR `{plate_path.name}` — RECONCILE TINY SUB-GLYPHS! "
        "Cross-check 1px tildes/overbars (e.g. '˜H' -> '\\tilde{H}', '¯z' -> '\\overline{z}'), "
        "conjugate stars/daggers ('−t^{∗}_{eff}' -> '-t_{\\mathrm{eff}}^*', '†' -> '^\\dagger'), "
        "minus signs in arguments/subscripts ('(−k)', '^{−1}'), and '[FRAC_BAR]' denominators against the visual plate]:\n"
        f"{joined_snips}\n"
        "</vector_glyph_anchor>"
    )


__all__ = [
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
]
