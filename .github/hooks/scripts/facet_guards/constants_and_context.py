"""Core constants, FSM/checkpoint tables, precompiled regexes, HookContext cache, and path/session helpers.

Function Map:
  - Section 1: Pipeline Stage, Checkpoint & Capability Allowlist Tables (`DEFAULT_FSM`, `DEFAULT_CONFIG`, `CHECKPOINT_STAGE_SPECS`, `STAGE_SPECIALIST_ALLOWLIST`)
  - Section 2: Lexical, Typographic, LaTeX & PEP 508 Precompiled Patterns (`SYNTHETIC_HOOK_DIRECTIVE_MARKERS`, `FACET_PROMPT_MARKERS`, `_TYPOGRAPHIC_LIGATURE_MAP`)
  - Section 3: Stat-Coherent Per-Invocation Memoization (`HookContext`, `HOOK_CONTEXT`)
  - Section 4: Path, Hash, Slug & Payload Identity Helpers (`get_copilot_home`, `compute_file_sha256`, `get_payload_cwd`, `slugify`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Literal

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows fallback
    _fcntl = None  # type: ignore[assignment]


# ============================================================================
# Section 1: Pipeline Stage, Checkpoint & Capability Allowlist Tables
# Authoritative finite-state machine defaults, governance defaults, and role
# routing tables shared across all hook modules.
# ============================================================================

APPROVED_SOURCES_FILENAME = "approved_sources.json"


FACET_GUARDS_DIR = Path(__file__).resolve().parent


HOOKS_SCRIPTS_DIR = FACET_GUARDS_DIR.parent


HOOKS_DIR = HOOKS_SCRIPTS_DIR.parent


HOOKS_REPO_ROOT = HOOKS_DIR.parent.parent


FSM_FILE_NAME = ".pipeline_state.json"


SCRATCH_DIR_NAME = ".pipeline_scratch"


CONFIG_FILE_NAME = ".pipeline_config.json"


STAGE_RECEIPTS_FILENAME = "stage_receipts.json"


HOOK_TIMEOUT_ENVELOPE: dict[str, int] = {
    "sessionStart": 5,
    "preToolUse": 15,
    "postToolUseFailure": 5,
    "postToolUse": 30,
    "preCompact": 5,
    "userPromptSubmitted": 30,
    "userPromptTransformed": 5,
    "subagentStart": 5,
    "subagentStop": 15,
    "sessionEnd": 5,
    "errorOccurred": 5,
    "permissionRequest": 5,
    "agentStop": 15,
}


STATE_LOCK_STRIPES = 64


STATE_LOCK_TIMEOUT_SEC = 1.5


_HELD_STATE_LOCKS = threading.local()


DEFAULT_FSM = {
    "current_stage": "STAGE_0_IDEATION",
    "concept_slug": None,
    "cycle_index": 0,
    "post_archive_halt_latch": False,
    "repair_attempts": 0,
    "repair_history": [],
    "pending_rewind": None,
    "local_critic_attempts": {"s1": 0, "s2": 0},
    "last_verified_pytest": None,
    "original_user_request": "",
    "mandated_target_module": None,
    "ocr_sha256": None,
    "text_sha256": None,
    "checkpoints": {
        "checkpoint_0": {"name": "Candidate Research", "artifact": "RESEARCH.md", "approved": False, "approved_sha256": None},
        "checkpoint_1": {"name": "Specification Manifest", "artifact": "manifest.json", "approved": False, "approved_sha256": None},
        "checkpoint_2": {"name": "Formal Mapping", "artifact": "mapping.json", "approved": False, "approved_sha256": None},
        "checkpoint_3": {"name": "Verification Audit", "artifact": "VERIFICATION.md", "approved": False, "approved_sha256": None}
    },
    "handshakes": [],
    "last_archived_cycle": None,
    "last_incident": None,
    "state_seal_sha256": None,
}


# Coupled contract: .github/skills/spec-pipeline/pipeline_config.schema.json
# Every enum tuple and Literal alias below is asserted 1:1 against pipeline_config.schema.json in scripts/validate_system.py.
GovernanceMode = Literal["strict-4-gate", "streamlined-2-gate", "autonomous-batch"]
StaticValidatorMode = Literal["py_compile", "none"]
ScratchRetentionMode = Literal["consume-once", "archive-trace"]
LocalSpecCriticMode = Literal["s1_only", "system1_s1_s2", "off", "s2_only", "s1_and_s2"]
SourceIngressMode = Literal["strict_research_clobber", "strict_research_aligned", "dual_tier_explicit_promotion"]
CoordinatorResidencyMode = Literal["e4b_orchestrator", "e4b_slim_context", "unified_26b_s1_s4"]
Sys1TriggerPolicy = Literal["every_boundary", "rewind_and_s0v_only", "ast_risk_gated"]
ContextHydrationMode = Literal["metadata_only", "inline_compact_specs", "epistemic_working_set", "s2_s3_selective_hydration"]
RewindDagPolicy = Literal["linear_stage_cascade", "interface_hash_skip_s3", "slice_dependency_dag"]
ContractAndGatesMode = Literal["standard", "debiased_contract_ocr_gate", "axiomatic_split_and_loop_guard"]
AuditorCleanroomPolicy = Literal["unrestricted_read", "two_phase_post_pytest", "strict_blackbox"]
EngineerHydrationMode = Literal["dual_json", "unified_build_sheet", "typed_python_skeleton", "entrypoint_cards", "progressive_scaffold_fill"]
EngineerRepairMode = Literal["pointer_full_view", "ast_fault_cone", "ast_fault_cone_replace_symbol"]
EngineerEscalationMode = Literal["in_window_retry", "handoff_and_contract_escalation"]
StageChecklistMode = Literal["hook_subagent_start", "off"]

GOVERNANCE_MODES: tuple[GovernanceMode, ...] = ("strict-4-gate", "streamlined-2-gate", "autonomous-batch")
STATIC_VALIDATOR_MODES: tuple[StaticValidatorMode, ...] = ("py_compile", "none")
SCRATCH_RETENTION_MODES: tuple[ScratchRetentionMode, ...] = ("consume-once", "archive-trace")
LOCAL_SPEC_CRITIC_MODES: tuple[LocalSpecCriticMode, ...] = ("s1_only", "system1_s1_s2", "off", "s2_only", "s1_and_s2")
EXPERIMENT_ENUM_OPTIONS: dict[str, tuple[str, ...]] = {
    "source_ingress_mode": ("strict_research_clobber", "strict_research_aligned", "dual_tier_explicit_promotion"),
    "coordinator_residency": ("e4b_orchestrator", "e4b_slim_context", "unified_26b_s1_s4"),
    "sys1_trigger_policy": ("every_boundary", "rewind_and_s0v_only", "ast_risk_gated"),
    "context_hydration": ("metadata_only", "inline_compact_specs", "epistemic_working_set", "s2_s3_selective_hydration"),
    "rewind_dag_policy": ("linear_stage_cascade", "interface_hash_skip_s3", "slice_dependency_dag"),
    "contract_and_gates": ("standard", "debiased_contract_ocr_gate", "axiomatic_split_and_loop_guard"),
    "auditor_cleanroom_policy": ("unrestricted_read", "two_phase_post_pytest", "strict_blackbox"),
    "engineer_hydration_mode": ("dual_json", "unified_build_sheet", "typed_python_skeleton", "entrypoint_cards", "progressive_scaffold_fill"),
    "engineer_repair_mode": ("pointer_full_view", "ast_fault_cone", "ast_fault_cone_replace_symbol"),
    "engineer_escalation_mode": ("in_window_retry", "handoff_and_contract_escalation"),
    "stage_checklist_mode": ("hook_subagent_start", "off"),
}


DEFAULT_CONFIG = {
    "governance_mode": "strict-4-gate",
    "max_repair_attempts": 3,
    "toolchain": {
        "verification_globs": ["verify_*.py", "test_*.py", "LOCAL_CRITIC.json", "AUDIT_FEEDBACK.json", "VERIFICATION.md"],
        "static_validator": "py_compile"
    },
    "scratch_retention": "consume-once",
    "max_visual_pages": 32,
    "approved_research_domains": [],
    "allow_web_research": True,
    "enable_local_spec_critic": "s1_only",
    "decision_model": {
        "enabled": True,
        "model": "pplx-decider-flash",
        "confidence_threshold": 0.85,
        "keep_alive": 0,
        "timeout_sec": 12,
    },
    "enable_post_archive_embed": False,
    "upstream_seals": [],
    "experiments": {
        "source_ingress_mode": "dual_tier_explicit_promotion",
        "coordinator_residency": "e4b_orchestrator",
        "sys1_trigger_policy": "ast_risk_gated",
        "context_hydration": "s2_s3_selective_hydration",
        "rewind_dag_policy": "linear_stage_cascade",
        "contract_and_gates": "standard",
        "auditor_cleanroom_policy": "two_phase_post_pytest",
        "engineer_hydration_mode": "unified_build_sheet",
        "engineer_repair_mode": "pointer_full_view",
        "engineer_escalation_mode": "handoff_and_contract_escalation",
        "stage_checklist_mode": "hook_subagent_start",
    },
}


ALLOWED_SUBAGENTS = {"Researcher", "Scribe", "Formalist", "Engineer", "Auditor"}


TEXT_ONLY_SPECIALISTS = {"Researcher", "Formalist", "Engineer", "Auditor"}


STAGE_SPECIALIST_ALLOWLIST: dict[str, set[str]] = {
    "STAGE_0_IDEATION": {"Scribe", "Researcher"},
    "STAGE_1_SPECIFICATION": {"Scribe", "Researcher"},
    "STAGE_1_CRITIC": {"Auditor"},
    "STAGE_2_MAPPING": {"Formalist"},
    "STAGE_2_CRITIC": {"Auditor"},
    "STAGE_3_IMPLEMENTATION": {"Engineer"},
    "STAGE_4_VERIFICATION": {"Auditor"},
}


VALID_FAILURE_CLASSIFICATIONS = {
    "IMPLEMENTATION_DEFECT",
    "MAPPING_DEFECT",
    "MANIFEST_DEFECT",
    "TEST_HARNESS_DEFECT",
    "SPECIFICATION_AMBIGUITY",
    "DOMAIN_BOUND_LIMITATION",
}


BENIGN_TOOLS = {
    "get_current_session",
    "archive_chat",
    "list_sessions",
    "read_agent",
    "list_canvas_capabilities",
    "open_canvas",
    "invoke_canvas_action",
}


TERMINAL_DENIAL_CLAUSE = "report BLOCKED with `Status: CONDITIONAL_PASS`; do not retry or edit state."


CHECKPOINT_CTA_TEXT = "Reply `/approve` to continue · `/reset` to start over · or ask a question"


CHECKPOINT_STAGE_SPECS: dict[str, tuple[str, str, str]] = {
    "CHECKPOINT_0_AWAITING_APPROVAL": ("Stage 1 of 4 · Research & Discovery", "RESEARCH.md", "checkpoint_0"),
    "CHECKPOINT_1_AWAITING_APPROVAL": ("Stage 2 of 4 · Formal Specification", "manifest.json", "checkpoint_1"),
    "CHECKPOINT_2_AWAITING_APPROVAL": ("Stage 3 of 4 · Interface & Proof Plan", "mapping.json", "checkpoint_2"),
    "CHECKPOINT_3_AWAITING_APPROVAL": ("Stage 4 of 4 · Cleanroom Verification", "VERIFICATION.md", "checkpoint_3"),
    "CHECKPOINT_REPAIR_ESCALATION": ("Verification Escalation · Action Needed", "AUDIT_FEEDBACK.json", "checkpoint_3"),
}


CHECKPOINT_PROGRESS_DOTS: dict[str, str] = {
    "CHECKPOINT_0_AWAITING_APPROVAL": "`● ○ ○ ○`",
    "CHECKPOINT_1_AWAITING_APPROVAL": "`● ● ○ ○`",
    "CHECKPOINT_2_AWAITING_APPROVAL": "`● ● ● ○`",
    "CHECKPOINT_3_AWAITING_APPROVAL": "`● ● ● ●`",
    "CHECKPOINT_REPAIR_ESCALATION": "`● ● ● ○`",
}


CHECKPOINT_NEXT_PREVIEWS: dict[str, str] = {
    "CHECKPOINT_0_AWAITING_APPROVAL": "Formal specification equations, domain bounds, and tolerances in `manifest.json`",
    "CHECKPOINT_1_AWAITING_APPROVAL": "Typed Python interface & 4-predicate proof obligations in `mapping.json`",
    "CHECKPOINT_2_AWAITING_APPROVAL": "Python module implementation & 4-predicate cleanroom verification suite",
    "CHECKPOINT_3_AWAITING_APPROVAL": "Cryptographic provenance seal & package archival in `specs/<slug>/`",
    "CHECKPOINT_REPAIR_ESCALATION": "Unverified draft archival with recorded counterexamples (or `/reset` to restart cleanly)",
}


STAGE_SPINNER_LABELS: dict[str, str] = {
    "PRE_RUN_HALT": "A new journey begins",
    "STAGE_0_IDEATION": "Focusing on source",
    "STAGE_1_SPECIFICATION": "Drafting manifest",
    "STAGE_1_CRITIC": "Auditing manifest",
    "STAGE_2_MAPPING": "Mapping interface",
    "STAGE_2_CRITIC": "Auditing mapping",
    "STAGE_3_IMPLEMENTATION": "Resolving matters",
    "STAGE_4_VERIFICATION": "Verifying module",
}


SPECIALIST_SPINNER_LABELS: dict[str, str] = {
    "Scribe": "Reading visual source",
    "Formalist": "Mapping interface",
    "Auditor": "Running proofs",
}


SPEC_AND_REPORT_FILES = {
    "SOURCE_OCR.md",
    "SOURCE_TEXT.md",
    "RESEARCH.md",
    "manifest.json",
    "mapping.json",
    "VERIFICATION.md",
    "AUDIT_FEEDBACK.json",
    "LOCAL_CRITIC.json",
}


SCOPED_EDIT_DELIVERABLES = {
    "SOURCE_OCR.md",
    "RESEARCH.md",
    "manifest.json",
    "mapping.json",
    "VERIFICATION.md",
}


VISUAL_AND_PDF_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".pdf"
}


REASONING_TRACE_SLOTS = {
    "Scribe": "scribe_trace.md",
    "Researcher": "researcher_trace.md",
    "Formalist": "formalist_trace.md",
    "Engineer": "engineer_trace.md",
    "Auditor": "auditor_trace.md",
}


# ============================================================================
# Section 2: Lexical, Typographic, LaTeX & PEP 508 Precompiled Patterns
# Precompiled regular expressions and lookup tables for Markdown/JSON/PEP 508
# normalization and directive tag filtering.
# ============================================================================

SYNTHETIC_HOOK_DIRECTIVE_MARKERS = (
    "<hypervisor_directive>",
    "<subagent_directive>",
)


FACET_PROMPT_MARKERS = (
    "<hypervisor_context>",
    "<checkpoint_gate>",
    "<checkpoint_reprompt>",
    "<archival_complete>",
    "<reset_complete>",
    "<source_registry_update>",
    "<status_pulse>",
    "<recovery_notice>",
    *SYNTHETIC_HOOK_DIRECTIVE_MARKERS,
)


HOOK_STATE_SNAPSHOT_WHITELIST: tuple[str, ...] = (
    "parent_session_id",
    "active_agent",
    "failures_by_tool",
    "consecutive_failures",
    "consecutive_stop_blocks",
    "audit_feedback_counted",
    "previous_parent_agent",
)


_TYPOGRAPHIC_LIGATURE_MAP = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\u2212": "-",
}


_EMOJI_STRIP_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


_LATEX_T_PREFIX_STEMS = ("ext", "riangle", "hick", "hin", "wohead")


_LATEX_T_EXACT_STEMS = (
    "imes", "heta", "au", "ilde", "anh", "an", "o", "op", "frac", "iny", "ag", "herefore", "binom"
)


_LATEX_N_PREFIX_STEMS = ("subset", "supset", "ewcommand")


_LATEX_N_EXACT_STEMS = (
    "abla", "eq", "eg", "ot", "u", "i", "otin", "orm", "ewline", "atural", "mid", "less", "geq",
    "cong", "sim", "vdash", "models", "parallel", "perp", "exists"
)


_VALID_EQUIVALENCE_MODES = {
    "approximate_tolerance",
    "exact_equality",
    "structural_isomorphism",
    "metamorphic_relation",
    "state_trace_equivalence",
}


_VALID_PREDICATE_TYPES = {"anchor", "reference_model", "relational_law", "postcondition"}


_PEP508_MARKER_VARS = (
    r"(?:python_version|python_full_version|os_name|sys_platform|platform_release|"
    r"platform_system|platform_version|platform_machine|platform_python_implementation|"
    r"implementation_name|implementation_version|extra)"
)


_PEP508_MARKER_ATOM = (
    rf"{_PEP508_MARKER_VARS}\s*(?:===|==|~=|!=|<=|>=|<|>|in|not\s+in)\s*"
    r"(?:'[A-Za-z0-9._*+\-\s]*'|\"[A-Za-z0-9._*+\-\s]*\")"
)


_PEP508_REQ_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
    r"(?:\[[A-Za-z0-9,._\-\s]+\])?"
    r"(?:\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_\-]+(?:\s*,\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_\-]+)*)?"
    rf"(?:\s*;\s*{_PEP508_MARKER_ATOM}(?:\s+(?:and|or)\s+{_PEP508_MARKER_ATOM})*)?$"
)


_TRIVIAL_MATH_BASE_SCALARS = {0, 1, -1, 2, 0.0, 1.0, -1.0, 2.0, True, False, None, "", b""}


_LOCAL_ABS_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(<?((?:/|[A-Za-z]:/)[^)<>\r\n]+)>?\)")


_BRIEFING_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "has", "have",
    "had", "been", "into", "over", "under", "via", "within", "across", "which", "where", "when",
    "while", "its", "their", "used", "using", "based", "given", "defined", "defines",
})


_SELF_TALK_BRIEFING_RE = re.compile(
    r"\b(?:the user is asking|i have already:|i should now answer|let's review what|through my crystalline lens)\b",
    re.IGNORECASE,
)


# ============================================================================
# Section 3: Stat-Coherent Per-Invocation Memoization
# Caches `.pipeline_config.json` and `.pipeline_state.json` by `(mtime_ns, size)` to avoid repeated disk parses within a hook call while invalidating automatically on write.
# ============================================================================

class HookContext:
    """Stat-coherent per-process memoization cache for config and FSM JSON reads."""

    def __init__(self) -> None:
        self._config_cache: dict[str, tuple[int, int, dict[str, object]]] = {}
        self._fsm_cache: dict[str, tuple[int, int, dict[str, object]]] = {}

    def get_config(self, cfg_path: Path) -> dict[str, object] | None:
        try:
            st = cfg_path.stat()
            key = str(cfg_path.resolve())
            hit = self._config_cache.get(key)
            if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                return copy.deepcopy(hit[2])
        except OSError:
            return None
        return None

    def put_config(self, cfg_path: Path, data: dict[str, object]) -> None:
        try:
            st = cfg_path.stat()
            self._config_cache[str(cfg_path.resolve())] = (st.st_mtime_ns, st.st_size, copy.deepcopy(data))
        except OSError:
            pass

    def get_fsm(self, fsm_path: Path) -> dict[str, object] | None:
        try:
            st = fsm_path.stat()
            key = str(fsm_path.resolve())
            hit = self._fsm_cache.get(key)
            if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                return copy.deepcopy(hit[2])
        except OSError:
            return None
        return None

    def put_fsm(self, fsm_path: Path, data: dict[str, object]) -> None:
        try:
            st = fsm_path.stat()
            self._fsm_cache[str(fsm_path.resolve())] = (st.st_mtime_ns, st.st_size, copy.deepcopy(data))
        except OSError:
            pass

    def invalidate_path(self, target_path: Path) -> None:
        try:
            key = str(Path(target_path).resolve())
            self._config_cache.pop(key, None)
            self._fsm_cache.pop(key, None)
        except OSError:
            pass


HOOK_CONTEXT = HookContext()


# ============================================================================
# Section 4: Path, Hash, Slug & Payload Identity Helpers
# Pure filesystem/payload accessors shared across all higher-level guard modules.
# ============================================================================

def get_copilot_home() -> Path:
    """Resolve the user Copilot configuration directory.

    Returns:
        Path to ``$COPILOT_HOME`` when set, otherwise ``~/.copilot``.
    """
    # Prefer `$COPILOT_HOME` when set (e.g. isolated test runs), falling back to `~/.copilot`.
    env_home = os.environ.get("COPILOT_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".copilot"


def compute_file_sha256(file_path):
    r"""Compute the hexadecimal SHA-256 digest of a file.

    Args:
        file_path: Target path to hash.

    Returns:
        Hex SHA-256 digest string, or ``None`` if unreadable or missing.
    """
    # Stream the file in 64 KiB binary chunks so large PDFs or bundles hash without loading into RAM.
    if not file_path or not Path(file_path).exists():
        return None
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Repeat step-by-step while `(chunk := f.read(65536))`.
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def slugify(text):
    r"""Convert arbitrary concept text into a lowercase snake_case slug.

    Args:
        text: Source title or identifier text.

    Returns:
        Normalized identifier string, defaulting to ``"spec"`` when empty.
    """
    # Strip punctuation and collapse spaces/hyphens into underscores to form a safe directory name.
    text = re.sub(r"[^\w\s-]", "", str(text).lower())
    return re.sub(r"[-\s]+", "_", text).strip("_") or "spec"


def extract_slug_from_file(file_path):
    r"""Extract a canonical concept slug from a JSON or Markdown artifact.

    Args:
        file_path: Path to a specification JSON or Markdown brief.

    Returns:
        Derived snake_case slug, or ``None`` if no title/concept field exists.
    """
    # Read the concept title or target module from a JSON spec or `# Heading` in Markdown to derive a folder slug.
    if not file_path or not file_path.exists():
        return None
    try:
        if file_path.suffix == ".json":
            data = json.loads(file_path.read_text(encoding="utf-8"))
            pub_if = data.get("public_interface") if isinstance(data.get("public_interface"), dict) else {}
            name = (
                data.get("concept_slug")
                or data.get("concept_name")
                or data.get("algorithm_name")
                or data.get("target_module")
                or pub_if.get("target_module")
            )
            if name:
                stem = str(name)[:-3] if str(name).lower().endswith(".py") else str(name)
                return slugify(stem)
        elif file_path.suffix == ".md":
            lines = file_path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if line.startswith("# "):
                    title = line.lstrip("# ").strip()
                    title = re.sub(r"^(Technical Verification Audit Report:\s*|Research Brief:\s*)", "", title, flags=re.I)
                    return slugify(title)
    except Exception:
        pass
    return None


def get_session_id(payload):
    """Return the Copilot session identifier from a hook payload.

    Args:
        payload: Parsed hook stdin JSON dictionary.

    Returns:
        Session ID string, defaulting to ``"global"`` when absent.
    """
    # Normalize camelCase (`sessionId`) and snake_case (`session_id`) hook payload keys.
    return payload.get("sessionId") or payload.get("session_id") or "global"


def get_payload_cwd(payload):
    """Resolve the workspace working directory from a hook payload.

    Args:
        payload: Parsed hook stdin JSON dictionary.

    Returns:
        ``Path`` to ``payload["cwd"]`` when provided, else ``Path.cwd()``.
    """
    # Resolve the active project directory from the hook payload, defaulting to the process working directory.
    raw_cwd = payload.get("cwd")
    return Path(raw_cwd) if raw_cwd else Path.cwd()


def is_subagent_payload(payload: dict[str, object]) -> bool:
    """Determine whether a hook payload originates from a delegated subagent.

    Args:
        payload: Parsed hook stdin JSON dictionary.

    Returns:
        ``True`` if ``payload`` carries child ``agentId``, ``parentAgentId``,
        or a non-``Facet`` ``agentType`` / ``agentName``; otherwise ``False``.
    """
    # Detect child subagent hook calls by checking for `agentId`, `parentAgentId`, or a non-`Facet` role name.
    if not isinstance(payload, dict):
        return False
    for key in ("agentId", "agent_id", "parentAgentId", "parent_agent_id"):
        # Return early when `str(payload.get(key) or '').strip()`.
        if str(payload.get(key) or "").strip():
            return True
    for role_key in ("agentType", "agent_type", "agentName", "agent_name"):
        role_val = str(payload.get(role_key) or "").strip()
        if role_val and role_val != "Facet":
            return True
    return False


def _strip_dot_slash_prefix(rel_path: str) -> str:
    """Normalize a workspace-relative path by removing only leading ``./`` segments.

    Unlike ``str.lstrip("./")`` this preserves dotfile names such as
    ``.hidden/seal.json``.

    Args:
        rel_path: Workspace-relative path string to normalize.

    Returns:
        Forward-slash-normalized relative path with leading ``./`` stripped.
    """
    # Remove leading `./` prefixes without accidentally stripping the leading dot from hidden `.filename` paths.
    return re.sub(r"^(?:\./+)+", "", rel_path.replace("\\", "/"))

__all__ = [
    "ALLOWED_SUBAGENTS",
    "APPROVED_SOURCES_FILENAME",
    "AuditorCleanroomPolicy",
    "BENIGN_TOOLS",
    "CHECKPOINT_CTA_TEXT",
    "CHECKPOINT_NEXT_PREVIEWS",
    "CHECKPOINT_PROGRESS_DOTS",
    "CHECKPOINT_STAGE_SPECS",
    "CONFIG_FILE_NAME",
    "ContextHydrationMode",
    "ContractAndGatesMode",
    "CoordinatorResidencyMode",
    "DEFAULT_CONFIG",
    "DEFAULT_FSM",
    "EXPERIMENT_ENUM_OPTIONS",
    "EngineerEscalationMode",
    "EngineerHydrationMode",
    "EngineerRepairMode",
    "FACET_PROMPT_MARKERS",
    "FSM_FILE_NAME",
    "GOVERNANCE_MODES",
    "GovernanceMode",
    "HOOK_CONTEXT",
    "HOOK_STATE_SNAPSHOT_WHITELIST",
    "HOOK_TIMEOUT_ENVELOPE",
    "HookContext",
    "LOCAL_SPEC_CRITIC_MODES",
    "LocalSpecCriticMode",
    "REASONING_TRACE_SLOTS",
    "RewindDagPolicy",
    "SCOPED_EDIT_DELIVERABLES",
    "SCRATCH_DIR_NAME",
    "SCRATCH_RETENTION_MODES",
    "SPECIALIST_SPINNER_LABELS",
    "SPEC_AND_REPORT_FILES",
    "STAGE_RECEIPTS_FILENAME",
    "STAGE_SPECIALIST_ALLOWLIST",
    "STAGE_SPINNER_LABELS",
    "STATE_LOCK_STRIPES",
    "STATE_LOCK_TIMEOUT_SEC",
    "STATIC_VALIDATOR_MODES",
    "SYNTHETIC_HOOK_DIRECTIVE_MARKERS",
    "ScratchRetentionMode",
    "SourceIngressMode",
    "StageChecklistMode",
    "StaticValidatorMode",
    "Sys1TriggerPolicy",
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
]
