"""Scope shadowing, POSIX flock/O_EXCL concurrency, atomic writes, FSM/config/session I/O, and cryptographic seals.

Function Map:
  - Section 1: Configuration & FSM Loaders (`load_pipeline_config`, `load_pipeline_fsm`, `is_depth0_session`)
  - Section 2: RFC 6962 Binary Merkle Tree, Canonical SHA-256 Sealing & Shadow Ledger (`compute_rfc6962_merkle_root`, `build_merkle_audit_path`, `verify_merkle_audit_path`, `sync_shadow_seal_ledger`, `verify_workspace_cryptographic_seals`)
  - Section 3: Striped POSIX `flock` Serialization & Crash-Safe Atomic Persistence (`invocation_state_locks`, `atomic_write_text`)
  - Section 4: Session State Tree, Rollback Staging, System-1 Transport & Scope Deduplication (`save_session_state`, `check_scope_and_lock`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import copy
import contextlib
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request

from .constants_and_context import (
    CONFIG_FILE_NAME,
    DEFAULT_CONFIG,
    DEFAULT_FSM,
    EXPERIMENT_ENUM_OPTIONS,
    FSM_FILE_NAME,
    GOVERNANCE_MODES,
    HOOK_CONTEXT,
    HOOK_STATE_SNAPSHOT_WHITELIST,
    LOCAL_SPEC_CRITIC_MODES,
    REASONING_TRACE_SLOTS,
    SCRATCH_DIR_NAME,
    SCRATCH_RETENTION_MODES,
    STAGE_RECEIPTS_FILENAME,
    STATE_LOCK_STRIPES,
    STATE_LOCK_TIMEOUT_SEC,
    _HELD_STATE_LOCKS,
    _fcntl,
    _strip_dot_slash_prefix,
    compute_file_sha256,
    get_copilot_home,
    get_payload_cwd,
    get_session_id,
    is_subagent_payload,
)

# ============================================================================
# Section 1: Configuration & FSM Loaders
# Reads and normalizes `.pipeline_config.json`, `.pipeline_state.json`, and root-vs-subagent depth state.
# ============================================================================

def is_depth0_session(payload: dict[str, object], state: dict[str, object] | None = None) -> bool:
    """Determine whether a hook invocation originates from a depth-0 root session.

    Args:
        payload: Parsed hook stdin JSON dictionary.
        state: Optional pre-loaded session state dictionary for ``sessionId``.

    Returns:
        ``True`` when neither ``payload`` nor ``state`` links to a parent
        session or subagent; ``False`` otherwise.
    """
    # Confirm this event came directly from the top-level user chat rather than a nested worker subagent.
    if is_subagent_payload(payload):
        return False
    eff_state = state if isinstance(state, dict) else load_session_state(get_session_id(payload))
    return not bool(str(eff_state.get("parent_session_id") or "").strip())


def load_pipeline_config(cwd):
    """Load and validate ``.pipeline_config.json`` merged with defaults.

    Args:
        cwd: Workspace root directory.

    Returns:
        Normalized configuration dictionary containing ``governance_mode``,
        ``max_repair_attempts``, ``toolchain``, ``scratch_retention``,
        ``approved_research_domains``, ``allow_web_research``,
        ``enable_post_archive_embed``, and ``upstream_seals``.
    """
    # Read `.pipeline_config.json` when present, clamp numeric bounds, normalize boolean/string critic modes, and merge with `DEFAULT_CONFIG`.
    cfg_path = Path(cwd) / CONFIG_FILE_NAME
    if cfg_path.exists():
        cached_cfg = HOOK_CONTEXT.get_config(cfg_path)
        if cached_cfg is not None:
            return cached_cfg
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                merged = json.loads(json.dumps(DEFAULT_CONFIG))
                max_rep = max(1, min(5, int(cfg.get("max_repair_attempts", 3))))
                mode = cfg.get("governance_mode", "strict-4-gate")
                if mode not in GOVERNANCE_MODES:
                    mode = "strict-4-gate"
                merged["governance_mode"] = mode
                merged["max_repair_attempts"] = max_rep
                if isinstance(cfg.get("toolchain"), dict):
                    merged["toolchain"].update(cfg["toolchain"])
                retention = cfg.get("scratch_retention", "consume-once")
                if retention not in SCRATCH_RETENTION_MODES:
                    retention = "consume-once"
                merged["scratch_retention"] = retention
                raw_domains = cfg.get("approved_research_domains", [])
                if isinstance(raw_domains, list):
                    merged["approved_research_domains"] = [
                        str(d).strip() for d in raw_domains if isinstance(d, str) and str(d).strip()
                    ]
                merged["allow_web_research"] = bool(cfg.get("allow_web_research", True))
                raw_lsc = cfg.get("enable_local_spec_critic", DEFAULT_CONFIG["enable_local_spec_critic"])
                if isinstance(raw_lsc, bool):
                    merged["enable_local_spec_critic"] = "s1_and_s2" if raw_lsc else "off"
                elif isinstance(raw_lsc, str) and raw_lsc in LOCAL_SPEC_CRITIC_MODES:
                    merged["enable_local_spec_critic"] = raw_lsc
                else:
                    merged["enable_local_spec_critic"] = "s1_only"
                raw_dm = cfg.get("decision_model")
                if isinstance(raw_dm, dict):
                    dm_merged = dict(DEFAULT_CONFIG["decision_model"])  # type: ignore[arg-type]
                    dm_merged["enabled"] = bool(raw_dm.get("enabled", True))
                    if isinstance(raw_dm.get("model"), str) and raw_dm["model"].strip():
                        dm_merged["model"] = raw_dm["model"].strip()
                    if isinstance(raw_dm.get("confidence_threshold"), (int, float)):
                        dm_merged["confidence_threshold"] = max(0.5, min(0.99, float(raw_dm["confidence_threshold"])))
                    if "keep_alive" in raw_dm:
                        dm_merged["keep_alive"] = raw_dm["keep_alive"]
                    if isinstance(raw_dm.get("timeout_sec"), (int, float)):
                        dm_merged["timeout_sec"] = max(1.0, min(60.0, float(raw_dm["timeout_sec"])))
                    merged["decision_model"] = dm_merged
                merged["enable_post_archive_embed"] = bool(cfg.get("enable_post_archive_embed", False))
                raw_seals = cfg.get("upstream_seals", [])
                if isinstance(raw_seals, list):
                    merged["upstream_seals"] = [
                        str(s).strip() for s in raw_seals if isinstance(s, str) and str(s).strip()
                    ]
                # Validate optional runtime policy overrides against single-source `EXPERIMENT_ENUM_OPTIONS`.
                raw_exp = cfg.get("experiments")
                if isinstance(raw_exp, dict):
                    exp_merged = dict(DEFAULT_CONFIG["experiments"])  # type: ignore[arg-type]
                    for ek, allowed_vals in EXPERIMENT_ENUM_OPTIONS.items():
                        ev = raw_exp.get(ek)
                        if isinstance(ev, str) and ev in allowed_vals:
                            exp_merged[ek] = ev
                    merged["experiments"] = exp_merged
                HOOK_CONTEXT.put_config(cfg_path, merged)
                return copy.deepcopy(merged)
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def load_pipeline_fsm(cwd):
    """Load persistent FSM state from ``.pipeline_state.json``.

    Args:
        cwd: Workspace root directory.

    Returns:
        FSM state dictionary merged with ``DEFAULT_FSM["checkpoints"]``.
    """
    # Load `.pipeline_state.json` from disk and backfill any missing checkpoint keys from `DEFAULT_FSM`.
    fsm_path = Path(cwd) / FSM_FILE_NAME
    if fsm_path.exists():
        cached_fsm = HOOK_CONTEXT.get_fsm(fsm_path)
        if cached_fsm is not None:
            return cached_fsm
        try:
            data = json.loads(fsm_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "current_stage" in data:
                merged_cp = json.loads(json.dumps(DEFAULT_FSM["checkpoints"]))
                if isinstance(data.get("checkpoints"), dict):
                    for ck, cv in data["checkpoints"].items():
                        if isinstance(cv, dict):
                            merged_cp.setdefault(ck, {}).update(cv)
                data["checkpoints"] = merged_cp
                HOOK_CONTEXT.put_fsm(fsm_path, data)
                return copy.deepcopy(data)
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_FSM))


# ============================================================================
# Section 2: RFC 6962 Binary Merkle Tree, Canonical SHA-256 Sealing & Shadow Ledger
# Computes fixed-point SHA-256 state seals and verifies parity against the out-of-workspace Shadow Seal Ledger.
# ============================================================================

import hmac


def _collect_merkle_leaves(
    artifact_hashes: dict[str, object],
    upstream_merkle_roots: dict[str, object] | None = None,
) -> list[tuple[str, bytes]]:
    r"""Collect sorted `(leaf_key, 32-byte_digest)` pairs across artifacts and upstream roots."""
    leaves: list[tuple[str, bytes]] = []
    for rel_p, hex_d in sorted((artifact_hashes or {}).items()):
        if isinstance(rel_p, str) and isinstance(hex_d, str) and len(hex_d) == 64:
            try:
                leaves.append((f"artifact:{rel_p}", bytes.fromhex(hex_d)))
            except ValueError:
                pass
    for up_slug, up_hex in sorted((upstream_merkle_roots or {}).items()):
        if isinstance(up_slug, str) and isinstance(up_hex, str) and len(up_hex) == 64:
            try:
                leaves.append((f"upstream:{up_slug}", bytes.fromhex(up_hex)))
            except ValueError:
                pass
    leaves.sort(key=lambda kv: kv[0])
    return leaves


def _merkle_leaf_digest(leaf_key: str, digest_bytes: bytes) -> bytes:
    r"""Compute RFC 6962 §2.1 domain-separated leaf hash `SHA-256(0x00 || key || 0x00 || sha256)`."""
    return hashlib.sha256(b"\x00" + leaf_key.encode("utf-8") + b"\x00" + digest_bytes).digest()


def _mth_bytes(leaf_hashes: list[bytes]) -> bytes:
    r"""Compute RFC 6962 §2.1 Merkle Tree Hash over pre-hashed leaves without odd-leaf duplication."""
    n = len(leaf_hashes)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return leaf_hashes[0]
    k = 1 << ((n - 1).bit_length() - 1)
    return hashlib.sha256(b"\x01" + _mth_bytes(leaf_hashes[:k]) + _mth_bytes(leaf_hashes[k:])).digest()


def compute_rfc6962_merkle_root(
    artifact_hashes: dict[str, object],
    upstream_merkle_roots: dict[str, object] | None = None,
) -> str:
    """Compute the RFC 6962 §2.1 binary Merkle root hex digest over package and upstream hashes.

    Args:
        artifact_hashes: Mapping of package-relative artifact paths to SHA-256 hex digests.
        upstream_merkle_roots: Optional mapping of upstream `specs/<slug>` identifiers to
            their sealed `merkle_root_sha256` (forming a compositional Merkle DAG).

    Returns:
        Lowercase 64-character hexadecimal SHA-256 Merkle root.
    """
    leaves = _collect_merkle_leaves(artifact_hashes, upstream_merkle_roots)
    return _mth_bytes([_merkle_leaf_digest(k, d) for k, d in leaves]).hex()


def build_merkle_audit_path(
    target_leaf_key: str,
    artifact_hashes: dict[str, object],
    upstream_merkle_roots: dict[str, object] | None = None,
) -> list[tuple[str, str]]:
    """Construct an RFC 6962 §2.1.1 logarithmic inclusion proof for `target_leaf_key`.

    Args:
        target_leaf_key: Canonical leaf key (e.g. `"artifact:mandelbrot.py"` or `"upstream:fft"`).
        artifact_hashes: Mapping of package-relative artifact paths to SHA-256 hex digests.
        upstream_merkle_roots: Optional mapping of upstream slugs to Merkle root hex digests.

    Returns:
        Ordered bottom-up list of `("left" | "right", sibling_hash_hex)` steps.

    Raises:
        KeyError: If `target_leaf_key` is not present in the leaf set.
    """
    leaves = _collect_merkle_leaves(artifact_hashes, upstream_merkle_roots)
    keys = [k for k, _ in leaves]
    if target_leaf_key not in keys:
        raise KeyError(f"Leaf '{target_leaf_key}' not present in Merkle tree.")
    leaf_hashes = [_merkle_leaf_digest(k, d) for k, d in leaves]
    idx = keys.index(target_leaf_key)

    def _path(m: int, sub: list[bytes]) -> list[tuple[str, str]]:
        n = len(sub)
        if n <= 1:
            return []
        k = 1 << ((n - 1).bit_length() - 1)
        if m < k:
            return _path(m, sub[:k]) + [("right", _mth_bytes(sub[k:]).hex())]
        return _path(m - k, sub[k:]) + [("left", _mth_bytes(sub[:k]).hex())]

    return _path(idx, leaf_hashes)


def verify_merkle_audit_path(
    leaf_key: str,
    leaf_sha256_hex: str,
    audit_path: list[tuple[str, str]],
    expected_root_hex: str,
) -> bool:
    r"""Verify an RFC 6962 §2.1.1 single-leaf inclusion proof against `expected_root_hex`.

    Args:
        leaf_key: Canonical leaf key (`"artifact:<path>"` or `"upstream:<slug>"`).
        leaf_sha256_hex: 64-char SHA-256 hex digest of the target artifact or upstream root.
        audit_path: Ordered bottom-up `(side, sibling_hex)` inclusion proof steps.
        expected_root_hex: Expected 64-char RFC 6962 `merkle_root_sha256` hex string.

    Returns:
        `True` if and only if the audit path hashes to `expected_root_hex`.
    """
    try:
        if len(leaf_sha256_hex) != 64 or len(expected_root_hex) != 64:
            return False
        acc = _merkle_leaf_digest(leaf_key, bytes.fromhex(leaf_sha256_hex))
        for side, sib_hex in audit_path:
            if len(sib_hex) != 64:
                return False
            sib = bytes.fromhex(sib_hex)
            if side == "right":
                acc = hashlib.sha256(b"\x01" + acc + sib).digest()
            elif side == "left":
                acc = hashlib.sha256(b"\x01" + sib + acc).digest()
            else:
                return False
        return hmac.compare_digest(acc.hex(), expected_root_hex.lower())
    except Exception:
        return False


def compute_canonical_seal_digest(record: dict[str, object]) -> str:
    """Compute a deterministic SHA-256 hex seal over canonical JSON fields.

    Excludes self-referential seal fields (``state_seal_sha256`` and
    ``seal_sha256``) so signing and verification are idempotent fixed points.

    Args:
        record: State or provenance dictionary to hash.

    Returns:
        Lowercase 64-character hexadecimal SHA-256 digest.
    """
    # Hash sorted-key compact JSON (excluding `state_seal_sha256` / `seal_sha256` themselves) so tamper checks are deterministic.
    filtered = {
        k: v for k, v in record.items()
        if k not in ("state_seal_sha256", "seal_sha256")
    }
    canonical_bytes = json.dumps(filtered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def sync_shadow_seal_ledger(
    cwd: Path,
    *,
    fsm_seal: str | None = None,
    provenance_rel: str | None = None,
    provenance_seal: str | None = None,
    clear_fsm_seal: bool = False,
) -> Path:
    """Atomically mirror workspace cryptographic seals outside ``cwd``.

    Maintains an out-of-workspace reference ledger under
    ``$COPILOT_HOME/session-state/seal_ledgers/`` so worker code confined to
    ``cwd`` cannot forge both an artifact and its recorded SHA-256 seal during
    ``@Facet``'s transient away phase.

    Args:
        cwd: Workspace root directory.
        fsm_seal: Optional canonical seal digest for ``.pipeline_state.json``.
        provenance_rel: Optional workspace-relative ``PROVENANCE.json`` path.
        provenance_seal: Optional canonical seal digest for ``provenance_rel``.
        clear_fsm_seal: When ``True``, clear stored FSM seal on ``/reset``.

    Returns:
        Path to the out-of-workspace shadow ledger JSON file.
    """
    # Mirror the workspace's latest state and provenance SHA-256 seals into `~/.copilot/session-state/seal_ledgers/` outside the repo.
    resolved_cwd = str(Path(cwd).resolve())
    cwd_key = hashlib.sha256(resolved_cwd.encode("utf-8")).hexdigest()[:24]
    ledger_dir = get_copilot_home() / "session-state" / "seal_ledgers"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / f"seal_{cwd_key}.json"

    ledger: dict[str, object] = {
        "cwd": resolved_cwd,
        "fsm_state_seal_sha256": None,
        "provenance_seals": {},
    }
    if ledger_path.is_file():
        try:
            loaded = json.loads(ledger_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                ledger.update(loaded)
        except Exception:
            pass

    prov_map = ledger.get("provenance_seals")
    if not isinstance(prov_map, dict):
        prov_map = {}
        ledger["provenance_seals"] = prov_map

    if clear_fsm_seal:
        ledger["fsm_state_seal_sha256"] = None
    elif fsm_seal is not None:
        ledger["fsm_state_seal_sha256"] = fsm_seal

    if provenance_rel and provenance_seal:
        prov_map[str(provenance_rel).replace("\\", "/")] = provenance_seal

    ledger["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    atomic_write_text(ledger_path, json.dumps(ledger, indent=2))
    return ledger_path


def verify_workspace_cryptographic_seals(
    cwd: Path,
    *,
    check_archive: bool = False,
) -> tuple[bool, str]:
    """Validate ``.pipeline_state.json``, upstream seals, and shadow ledger parity.

    Args:
        cwd: Workspace root directory.
        check_archive: When ``True``, require a valid ``last_archived_cycle``
            ``PROVENANCE.json`` bundle in ``PRE_RUN_HALT``.

    Returns:
        Tuple ``(is_valid, diagnostic_message)``.
    """
    cwd_path = Path(cwd).resolve()
    fsm_path = cwd_path / FSM_FILE_NAME
    if not fsm_path.is_file():
        return (False, f"Missing pipeline seal state file at '{fsm_path}'.")
    try:
        raw_fsm = json.loads(fsm_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return (False, f"Corrupted JSON in '{fsm_path}': {exc}")
    if not isinstance(raw_fsm, dict) or not isinstance(raw_fsm.get("current_stage"), str):
        return (False, f"Malformed state structure in '{fsm_path}' (missing 'current_stage').")

    cwd_key = hashlib.sha256(str(cwd_path).encode("utf-8")).hexdigest()[:24]
    ledger_path = get_copilot_home() / "session-state" / "seal_ledgers" / f"seal_{cwd_key}.json"
    shadow_fsm_seal: str | None = None
    shadow_prov_seals: dict[str, str] = {}
    if ledger_path.is_file():
        try:
            ldata = json.loads(ledger_path.read_text(encoding="utf-8"))
            if isinstance(ldata, dict):
                raw_sf = ldata.get("fsm_state_seal_sha256")
                if isinstance(raw_sf, str) and raw_sf:
                    shadow_fsm_seal = raw_sf
                raw_sp = ldata.get("provenance_seals")
                if isinstance(raw_sp, dict):
                    shadow_prov_seals = {str(k): str(v) for k, v in raw_sp.items() if isinstance(v, str)}
        except Exception:
            pass

    # Step 1: Recompute the canonical SHA-256 digest of `.pipeline_state.json` and
    # verify both self-consistency and parity against the out-of-workspace Shadow Seal Ledger.
    recorded_state_seal = raw_fsm.get("state_seal_sha256")
    expected_state_seal = compute_canonical_seal_digest(raw_fsm)
    if recorded_state_seal is not None or shadow_fsm_seal is not None:
        if not isinstance(recorded_state_seal, str) or len(recorded_state_seal) != 64 or recorded_state_seal != expected_state_seal:
            return (
                False,
                f"State seal digest mismatch in '{fsm_path}' (expected '{expected_state_seal[:12]}...', observed '{str(recorded_state_seal)[:12]}...').",
            )
        if shadow_fsm_seal is not None and recorded_state_seal != shadow_fsm_seal:
            return (
                False,
                f"Shadow Seal Ledger parity failure for '{fsm_path}' (workspace seal '{recorded_state_seal[:12]}...' != ledger '{shadow_fsm_seal[:12]}...').",
            )

    current_stage = str(raw_fsm.get("current_stage", ""))
    if not check_archive and current_stage.startswith("CHECKPOINT_"):
        return (
            False,
            f"Pipeline is paused at unapproved human gate '{current_stage}'. Reply '/approve' before invoking '@Orchestrator'.",
        )

    # Step 2: During an active run, confirm that `SOURCE_OCR.md` and every human-approved
    # checkpoint artifact (`RESEARCH.md`, `manifest.json`, `mapping.json`, `VERIFICATION.md`)
    # still match their approved SHA-256 hashes byte-for-byte on disk.
    if current_stage != "PRE_RUN_HALT":
        ocr_seal = raw_fsm.get("ocr_sha256")
        if isinstance(ocr_seal, str) and ocr_seal:
            actual_ocr = compute_file_sha256(cwd_path / "SOURCE_OCR.md")
            if actual_ocr != ocr_seal:
                return (
                    False,
                    f"Upstream OCR seal mismatch for 'SOURCE_OCR.md' (expected '{ocr_seal[:12]}...', observed '{str(actual_ocr)[:12]}...').",
                )
        text_seal = raw_fsm.get("text_sha256")
        if isinstance(text_seal, str) and text_seal:
            actual_text = compute_file_sha256(cwd_path / "SOURCE_TEXT.md")
            if actual_text != text_seal:
                return (
                    False,
                    f"Upstream PDF text seal mismatch for 'SOURCE_TEXT.md' (expected '{text_seal[:12]}...', observed '{str(actual_text)[:12]}...').",
                )
        checkpoints = raw_fsm.get("checkpoints")
        if isinstance(checkpoints, dict):
            for cp_key, cp_info in sorted(checkpoints.items()):
                if isinstance(cp_info, dict) and cp_info.get("approved"):
                    art_name = str(cp_info.get("artifact") or "")
                    app_sha = str(cp_info.get("approved_sha256") or "")
                    actual_sha = compute_file_sha256(cwd_path / art_name) if art_name else None
                    if not app_sha or len(app_sha) != 64 or actual_sha != app_sha:
                        return (
                            False,
                            f"Approved checkpoint seal violation at '{cp_key}' ('{art_name}' expected '{app_sha[:12]}...', observed '{str(actual_sha)[:12]}...').",
                        )

    # Load `.pipeline_config.json` (falling back to `DEFAULT_CONFIG` when absent).
    cfg = load_pipeline_config(cwd_path)
    prov_targets: list[str] = [str(p) for p in cfg.get("upstream_seals", [])]
    last_arch = raw_fsm.get("last_archived_cycle")
    # Return early when `check_archive`.
    if check_archive:
        if current_stage != "PRE_RUN_HALT" or not isinstance(last_arch, dict) or not last_arch.get("provenance_path"):
            return (False, "Post-run archival cycle did not record 'PRE_RUN_HALT' and 'last_archived_cycle.provenance_path'.")
    if isinstance(last_arch, dict) and isinstance(last_arch.get("provenance_path"), str):
        lp = last_arch["provenance_path"]
        if lp not in prov_targets:
            prov_targets.append(lp)

    # Step 3: Verify any declared `upstream_seals` (and `last_archived_cycle` right after Stage 5)
    # by checking `PROVENANCE.json` status, composite `seal_sha256`, Shadow Ledger parity, and per-file hashes.
    for rel_prov in prov_targets:
        norm_rel = _strip_dot_slash_prefix(rel_prov)
        prov_path = (cwd_path / norm_rel).resolve()
        try:
            prov_path.relative_to(cwd_path)
        except ValueError:
            return (False, f"Upstream seal path '{rel_prov}' escapes workspace root '{cwd_path}'.")
        if not prov_path.is_file():
            return (False, f"Expected archival provenance seal '{norm_rel}' does not exist on disk.")
        try:
            prov_data = json.loads(prov_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return (False, f"Invalid JSON in provenance seal '{norm_rel}': {exc}")
        if not isinstance(prov_data, dict) or prov_data.get("status") != "VERIFIED_COMPLETE":
            return (False, f"Provenance seal '{norm_rel}' is missing 'status': 'VERIFIED_COMPLETE'.")
        rec_pseal = prov_data.get("seal_sha256")
        exp_pseal = compute_canonical_seal_digest(prov_data)
        if not isinstance(rec_pseal, str) or len(rec_pseal) != 64 or rec_pseal != exp_pseal:
            return (
                False,
                f"Composite Merkle seal mismatch in '{norm_rel}' (expected '{exp_pseal[:12]}...', observed '{str(rec_pseal)[:12]}...').",
            )
        if norm_rel in shadow_prov_seals and shadow_prov_seals[norm_rel] != rec_pseal:
            return (
                False,
                f"Shadow Seal Ledger parity failure for '{norm_rel}' (workspace '{rec_pseal[:12]}...' != ledger '{shadow_prov_seals[norm_rel][:12]}...').",
            )
        if isinstance(last_arch, dict) and last_arch.get("provenance_path") == norm_rel:
            if last_arch.get("seal_sha256") != rec_pseal:
                return (False, f"Loop-back state seal does not match '{norm_rel}' ('seal_sha256' mismatch).")
        art_hashes = prov_data.get("artifact_hashes")
        if not isinstance(art_hashes, dict) or not art_hashes:
            return (False, f"Provenance seal '{norm_rel}' contains empty 'artifact_hashes'.")
        rec_merkle = prov_data.get("merkle_root_sha256")
        raw_up_roots = prov_data.get("upstream_merkle_roots")
        up_roots = raw_up_roots if isinstance(raw_up_roots, dict) else None
        if rec_merkle is not None:
            exp_merkle = compute_rfc6962_merkle_root(art_hashes, up_roots)
            if not isinstance(rec_merkle, str) or len(rec_merkle) != 64 or rec_merkle != exp_merkle:
                return (
                    False,
                    f"RFC 6962 binary Merkle root mismatch in '{norm_rel}' (expected '{exp_merkle[:12]}...', observed '{str(rec_merkle)[:12]}...').",
                )
        if isinstance(up_roots, dict):
            for up_slug, exp_up_root in sorted(up_roots.items()):
                up_prov_f = cwd_path / "specs" / str(up_slug) / "PROVENANCE.json"
                if not up_prov_f.is_file():
                    return (False, f"Upstream Merkle DAG dependency 'specs/{up_slug}/PROVENANCE.json' is missing on disk.")
                try:
                    up_pj = json.loads(up_prov_f.read_text(encoding="utf-8"))
                except Exception as exc_u:
                    return (False, f"Invalid JSON in upstream provenance 'specs/{up_slug}/PROVENANCE.json': {exc_u}")
                obs_up_root = up_pj.get("merkle_root_sha256") or up_pj.get("seal_sha256")
                if obs_up_root != exp_up_root:
                    return (
                        False,
                        f"Upstream Merkle DAG root mismatch for 'specs/{up_slug}' referenced in '{norm_rel}' (expected '{str(exp_up_root)[:12]}...', observed '{str(obs_up_root)[:12]}...').",
                    )
        arch_dir = prov_path.parent
        raw_root_only = prov_data.get("root_artifacts")
        # Root-resident reference files (e.g. research/ inputs) stay at their original paths,
        # so they are integrity-checked only immediately after archival (check_archive=True).
        root_only = {str(n) for n in raw_root_only} if isinstance(raw_root_only, list) else set()
        for art_key, art_sha in sorted(art_hashes.items()):
            if art_key in root_only:
                if not check_archive:
                    continue
                target_art = cwd_path / art_key
            else:
                target_art = arch_dir / art_key
            if not target_art.is_file():
                return (False, f"Archived artifact '{art_key}' referenced in '{norm_rel}' is missing on disk.")
            obs_sha = compute_file_sha256(target_art)
            if not isinstance(art_sha, str) or obs_sha != art_sha:
                return (
                    False,
                    f"Archived artifact '{art_key}' failed SHA-256 integrity against '{norm_rel}' (expected '{str(art_sha)[:12]}...', observed '{str(obs_sha)[:12]}...').",
                )

    return (True, "All cryptographic seals verified.")


# ============================================================================
# Section 3: Striped POSIX `flock` Serialization & Crash-Safe Atomic Persistence
# Prevents torn reads/writes across concurrent tool calls via 64-stripe `flock` descriptors and temp-file `os.replace`.
# ============================================================================

def atomic_write_text(target_path: Path, content: str) -> None:
    """Write ``content`` to ``target_path`` via a unique temporary file and ``os.replace``.

    Args:
        target_path: Destination file path.
        content: UTF-8 string payload to persist atomically.
    """
    # Write UTF-8 text to a PID-and-nanosecond-unique temp file in the same folder, then atomically rename (`os.replace`) over the target.
    dest = Path(target_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.parent / f".{dest.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, dest)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise


def acquire_state_lock_file(
    lock_path: Path,
    timeout_sec: float = STATE_LOCK_TIMEOUT_SEC,
) -> tuple[int | None, str | None]:
    """Acquire an exclusive non-blocking ``fcntl.flock`` with bounded timeout.

    Re-entrant within the same thread via ``_HELD_STATE_LOCKS`` and degrades
    cleanly to unlocked ``(None, None)`` when ``fcntl`` is unavailable or when
    ``timeout_sec`` expires (logging to ``stderr``, never ``stdout``).

    Args:
        lock_path: Target ``.flock`` sidecar file under ``state_locks/``.
        timeout_sec: Maximum seconds to wait before proceeding unlocked.

    Returns:
        Tuple ``(fd, resolved_key)`` when acquired, or ``(None, None)``.
    """
    # Acquire a non-blocking POSIX `fcntl.flock` with a 1.5s retry window and thread-local re-entrancy tracking so concurrent hooks never deadlock.
    _pg_mod = sys.modules.get("pipeline_guards")
    _eff_fcntl = getattr(_pg_mod, "_fcntl", _fcntl) if _pg_mod is not None else _fcntl
    if _eff_fcntl is None:
        return None, None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_key = str(lock_path.resolve())
    except Exception:
        return None, None

    held: set[str] | None = getattr(_HELD_STATE_LOCKS, "paths", None)
    if held is None:
        held = set()
        _HELD_STATE_LOCKS.paths = held
    if resolved_key in held:
        return None, None

    fd: int | None = None
    # Acquire exclusive OS file descriptor/lock and handle contention cleanly.
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        # Retry until `True` (waiting briefly between attempts).
        while True:
            # Acquire exclusive OS file descriptor/lock and handle contention cleanly.
            try:
                _eff_fcntl.flock(fd, _eff_fcntl.LOCK_EX | _eff_fcntl.LOCK_NB)
                held.add(resolved_key)
                return fd, resolved_key
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    os.close(fd)
                    sys.stderr.write(
                        f"Warning: Timed out after {timeout_sec:.2f}s acquiring state lock {lock_path.name}; proceeding unlocked.\n"
                    )
                    return None, None
                time.sleep(0.005)
    except Exception as exc:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        sys.stderr.write(f"Warning: State lock unavailable ({lock_path.name}: {exc}); proceeding unlocked.\n")
        return None, None


def release_state_lock_file(fd: int | None, held_key: str | None) -> None:
    """Release an ``fcntl.flock`` descriptor acquired by ``acquire_state_lock_file``.

    Args:
        fd: Open file descriptor returned by ``acquire_state_lock_file``, or
            ``None`` when operating unlocked or re-entrantly.
        held_key: Thread-local re-entrancy tracking key to discard, or ``None``.
    """
    # Unlock and close the `.flock` file descriptor and clear its thread-local re-entrancy entry.
    if held_key is not None:
        held: set[str] | None = getattr(_HELD_STATE_LOCKS, "paths", None)
        if held is not None:
            held.discard(held_key)
    if fd is not None:
        # Acquire exclusive OS file descriptor/lock and handle contention cleanly.
        try:
            _pg_mod = sys.modules.get("pipeline_guards")
            _eff_fcntl = getattr(_pg_mod, "_fcntl", _fcntl) if _pg_mod is not None else _fcntl
            if _eff_fcntl is not None:
                _eff_fcntl.flock(fd, _eff_fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass


@contextlib.contextmanager
def invocation_state_locks(
    session_id: str,
    cwd: Path | str | None,
    timeout_sec: float = STATE_LOCK_TIMEOUT_SEC,
):
    """Serialize read-modify-write state sequences across session and workspace stripes.

    Always acquires the session stripe strictly before the workspace stripe so
    lock ordering is cycle-free across concurrent processes.

    Args:
        session_id: Session or child ``agentId`` identifier for stripe hashing.
        cwd: Workspace root directory path for workspace stripe hashing.
        timeout_sec: Maximum seconds to wait when acquiring each stripe lock.

    Yields:
        Control to the caller while holding both striped file locks.
    """
    # Lock the session stripe strictly before the workspace stripe to guarantee a global, cycle-free lock acquisition order.
    lock_dir = get_copilot_home() / "session-state" / "state_locks"
    sess_idx = int(hashlib.sha256(str(session_id or "default").encode("utf-8")).hexdigest()[:8], 16) % STATE_LOCK_STRIPES
    sess_fd, sess_key = acquire_state_lock_file(lock_dir / f"sess_{sess_idx:02d}.flock", timeout_sec=timeout_sec)
    ws_fd: int | None = None
    ws_key: str | None = None
    if cwd is not None and str(cwd).strip():
        try:
            ws_norm = str(Path(cwd).resolve())
        except Exception:
            ws_norm = str(cwd)
        ws_idx = int(hashlib.sha256(ws_norm.encode("utf-8")).hexdigest()[:8], 16) % STATE_LOCK_STRIPES
        ws_fd, ws_key = acquire_state_lock_file(lock_dir / f"ws_{ws_idx:02d}.flock", timeout_sec=timeout_sec)
    try:
        yield
    finally:
        release_state_lock_file(ws_fd, ws_key)
        release_state_lock_file(sess_fd, sess_key)


def notify_facet_lens_sse(cwd: Path, event_kind: str = "fsm_update", stage: str = "") -> bool:
    """Emit a zero-token loopback push to an active ``Facet Lens`` server if registered.

    Looks up ``live_endpoint_<ws_digest>.json`` under
    ``$COPILOT_HOME/extension-data/facet-pipeline-lens/`` and sends a bounded
    ``80 ms`` ``POST /api/notify`` request over ``127.0.0.1`` so open Canvas
    panels reload stage state immediately without agent tool calls.

    Args:
        cwd: Workspace root directory whose Canvas instance should be notified.
        event_kind: Short event label (e.g. ``"fsm_update"``).
        stage: Active ``current_stage`` identifier persisted in ``.pipeline_state.json``.

    Returns:
        ``True`` if an active local ``Facet Lens`` server acknowledged with HTTP 200,
        otherwise ``False``.
    """
    # Look up any running local `Facet Lens` Canvas server for this workspace and send an 80ms `POST /api/notify` push so its UI updates live.
    try:
        canonical = str(Path(cwd).resolve())
        ws_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        home_candidates = [get_copilot_home()]
        default_home = Path.home() / ".copilot"
        if default_home not in home_candidates:
            home_candidates.append(default_home)
        delivered = False
        for base_home in home_candidates:
            ep_file = base_home / "extension-data" / "facet-pipeline-lens" / f"live_endpoint_{ws_digest}.json"
            if not ep_file.is_file():
                continue
            try:
                ep_data = json.loads(ep_file.read_text(encoding="utf-8"))
                url = str(ep_data.get("url") or "").strip()
                if not url.startswith("http://127.0.0.1:"):
                    continue
                payload = json.dumps(
                    {"event": event_kind, "stage": stage, "workspaceRoot": canonical}
                ).encode("utf-8")
                req = urllib.request.Request(
                    url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
                )
                with urllib.request.urlopen(req, timeout=0.08) as resp:
                    if int(getattr(resp, "status", 200)) == 200:
                        delivered = True
            except Exception:
                continue
        return delivered
    except Exception:
        return False


def save_pipeline_fsm(cwd, fsm_data):
    r"""Sign, shadow-mirror, and atomically write ``.pipeline_state.json``.

    Args:
        cwd: Workspace root directory.
        fsm_data: Serializable FSM dictionary to persist.
    """
    # Recompute `state_seal_sha256`, write `.pipeline_state.json` atomically, mirror the seal to the Shadow Ledger, and ping `Facet Lens`.
    try:
        signed_fsm = dict(fsm_data)
        seal_hex = compute_canonical_seal_digest(signed_fsm)
        signed_fsm["state_seal_sha256"] = seal_hex
        fsm_data["state_seal_sha256"] = seal_hex
        fsm_path = Path(cwd) / FSM_FILE_NAME
        atomic_write_text(fsm_path, json.dumps(signed_fsm, indent=2))
        sync_shadow_seal_ledger(Path(cwd), fsm_seal=seal_hex)
        notify_facet_lens_sse(Path(cwd), "fsm_update", str(signed_fsm.get("current_stage") or ""))
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to save FSM state: {e}\n")


# ============================================================================
# Section 4: Session State Tree, Rollback Staging, System-1 Transport & Scope Deduplication
# Manages `hooks_state.json`, `.rollback_*` draft backups, `/v1/systemone` HTTP queries, and `--scope user` shadowing.
# ============================================================================

def load_session_state(session_id):
    """Load per-session hook tracking state from ``hooks_state.json``.

    Args:
        session_id: Session or child ``agentId`` key.

    Returns:
        Session tracking dictionary with ``failures_by_tool`` and ``active_agent``.
    """
    # Read the per-session tracking dictionary (`active_agent`, viewed seal paths, failure counts) from `$COPILOT_HOME/session-state/hooks_state.json`.
    s_file = get_copilot_home() / "session-state" / "hooks_state.json"
    if s_file.exists():
        try:
            all_state = json.loads(s_file.read_text(encoding="utf-8"))
            if isinstance(all_state, dict):
                session_data = all_state.get(session_id, {})
                if isinstance(session_data, dict):
                    if "failures_by_tool" not in session_data:
                        session_data["failures_by_tool"] = {}
                    return session_data
        except Exception:
            pass
    return {"last_failed_tool": None, "last_failed_args": None, "failures_by_tool": {}, "active_agent": None}


def save_session_state(session_id, state):
    r"""Atomically merge and persist ``session_id`` into ``hooks_state.json``.

    Acquires a short global merge lock under ``state_locks/`` so concurrent
    sessions never overwrite one another's entries in the shared file.

    Args:
        session_id: Session or child ``agentId`` key.
        state: Session tracking dictionary to store.
    """
    # Acquire the global merge lock, re-read `hooks_state.json` so sibling sessions aren't clobbered, update `session_id`, and write atomically.
    s_dir = get_copilot_home() / "session-state"
    s_file = s_dir / "hooks_state.json"
    global_lock = s_dir / "state_locks" / "hooks_state_global.flock"
    g_fd, g_key = acquire_state_lock_file(global_lock)
    try:
        all_state = {}
        if s_file.exists():
            try:
                all_state = json.loads(s_file.read_text(encoding="utf-8"))
                if not isinstance(all_state, dict):
                    all_state = {}
            except Exception:
                all_state = {}
        all_state[session_id] = state
        atomic_write_text(s_file, json.dumps(all_state, indent=2))
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to save session state: {e}\n")
    finally:
        release_state_lock_file(g_fd, g_key)


def update_session_state(session_id: str, mutator_fn) -> dict[str, object]:
    """Execute a linearizable read-modify-write transaction on ``hooks_state.json``.

    Holds ``hooks_state_global.flock`` across the full read, mutation, and
    atomic persistence cycle so concurrent ``preToolUse`` / ``postToolUse``
    invocations from parallel tool batches never drop sibling updates.

    Args:
        session_id: Session or child ``agentId`` key.
        mutator_fn: Callable receiving and mutating the session state dictionary in place.

    Returns:
        Updated session tracking dictionary.
    """
    s_dir = get_copilot_home() / "session-state"
    s_file = s_dir / "hooks_state.json"
    global_lock = s_dir / "state_locks" / "hooks_state_global.flock"
    g_fd, g_key = acquire_state_lock_file(global_lock)
    entry: dict[str, object] = {
        "last_failed_tool": None,
        "last_failed_args": None,
        "failures_by_tool": {},
        "active_agent": None,
    }
    try:
        all_state: dict[str, object] = {}
        if s_file.exists():
            try:
                loaded = json.loads(s_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    all_state = loaded
            except Exception:
                all_state = {}
        cur = all_state.get(session_id)
        if isinstance(cur, dict):
            entry = copy.deepcopy(cur)
            if not isinstance(entry.get("failures_by_tool"), dict):
                entry["failures_by_tool"] = {}
        mutator_fn(entry)
        all_state[session_id] = entry
        atomic_write_text(s_file, json.dumps(all_state, indent=2))
    except Exception as exc:
        sys.stderr.write(f"Warning: Failed to update session state: {exc}\n")
    finally:
        release_state_lock_file(g_fd, g_key)
    return entry


DEFAULT_STAGE_RECEIPTS: dict[str, object] = {
    "web_grounding_urls": [],
    "viewed_ocr_plates": [],
    "stage4_pytest_runs": 0,
    "stage4_pytest_passed": False,
    "stage4_pytest_failed": False,
}


def _stage_receipts_lock_path(cwd: Path) -> Path:
    resolved_ws = str(Path(cwd).resolve())
    stripe = int(hashlib.sha256(resolved_ws.encode("utf-8")).hexdigest()[:8], 16) % STATE_LOCK_STRIPES
    return get_copilot_home() / "session-state" / "state_locks" / f"ws_{stripe:02d}.flock"


def load_stage_receipts(cwd: Path) -> dict[str, object]:
    r"""Load cycle-scoped tool execution evidence from ``.pipeline_scratch/stage_receipts.json``."""
    r_path = Path(cwd) / SCRATCH_DIR_NAME / STAGE_RECEIPTS_FILENAME
    receipts = copy.deepcopy(DEFAULT_STAGE_RECEIPTS)
    if r_path.is_file():
        try:
            raw = json.loads(r_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                receipts.update(raw)
        except Exception:
            pass
    return receipts


def update_stage_receipts(cwd: Path, mutator_fn) -> dict[str, object]:
    r"""Atomically read, mutate, and persist ``.pipeline_scratch/stage_receipts.json`` under workspace lock."""
    cwd_p = Path(cwd)
    r_path = cwd_p / SCRATCH_DIR_NAME / STAGE_RECEIPTS_FILENAME
    lock_p = _stage_receipts_lock_path(cwd_p)
    l_fd, l_key = acquire_state_lock_file(lock_p)
    receipts = copy.deepcopy(DEFAULT_STAGE_RECEIPTS)
    try:
        if r_path.is_file():
            try:
                raw = json.loads(r_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    receipts.update(raw)
            except Exception:
                pass
        mutator_fn(receipts)
        r_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(r_path, json.dumps(receipts, indent=2))
    except Exception as exc:
        sys.stderr.write(f"Warning: Failed to update stage receipts: {exc}\n")
    finally:
        release_state_lock_file(l_fd, l_key)
    return receipts


def clear_stage_receipts(cwd: Path) -> None:
    """Remove ``.pipeline_scratch/stage_receipts.json`` and prune ``.pipeline_scratch`` if empty."""
    cwd_p = Path(cwd)
    r_path = cwd_p / SCRATCH_DIR_NAME / STAGE_RECEIPTS_FILENAME
    if r_path.exists():
        try:
            r_path.unlink()
        except Exception:
            pass
    _prune_empty_scratch_dir(cwd_p)


def purge_session_state_entries(delete_ids: list[str]) -> int:
    r"""Delete specified child ``agentId`` entries from ``hooks_state.json`` under the global lock.

    Args:
        delete_ids: Child session / ``agentId`` keys scheduled for removal.

    Returns:
        Number of state entries removed from ``hooks_state.json``.
    """
    # Remove completed child subagent tracking entries from `hooks_state.json` on the next root human turn.
    targets = [str(x) for x in delete_ids if str(x)]
    if not targets:
        return 0
    s_dir = get_copilot_home() / "session-state"
    s_file = s_dir / "hooks_state.json"
    if not s_file.exists():
        return 0
    global_lock = s_dir / "state_locks" / "hooks_state_global.flock"
    g_fd, g_key = acquire_state_lock_file(global_lock)
    removed = 0
    try:
        all_state = json.loads(s_file.read_text(encoding="utf-8"))
        if not isinstance(all_state, dict):
            return 0
        for target_id in targets:
            if target_id in all_state:
                all_state.pop(target_id, None)
                removed += 1
        if removed:
            atomic_write_text(s_file, json.dumps(all_state, indent=2))
        return removed
    except Exception as exc:
        sys.stderr.write(f"Warning: Failed to purge completed session entries: {exc}\n")
        return 0
    finally:
        release_state_lock_file(g_fd, g_key)


def snapshot_session_tree_state(
    session_id: str | None,
    target_dir: Path | None,
    immediate_cleanup: bool = False,
) -> Path | None:
    r"""Snapshot whitelisted session-tree state at archival/reset and schedule or run cleanup.

    Walks ``parent_session_id`` upward from ``session_id`` to the root session
    and downward to all descendants, writes ``hook_state_snapshot.json`` into
    ``target_dir`` (when provided), and either deletes descendant entries
    immediately (on ``/reset``) or records ``pending_state_cleanup`` on the
    root entry for removal at the next root human turn.

    Args:
        session_id: Active root or child session identifier, or ``None``.
        target_dir: Destination archive directory for ``hook_state_snapshot.json``,
            or ``None`` when performing cleanup without writing a snapshot.
        immediate_cleanup: When ``True``, purge descendant entries immediately;
            when ``False``, defer cleanup until the next root human turn.

    Returns:
        ``Path`` to the written ``hook_state_snapshot.json`` file, or ``None``
        when skipped or when ``target_dir`` is ``None``.
    """
    norm_sid = str(session_id or "").strip()
    if not norm_sid:
        sys.stderr.write("Info: Skipping hook_state_snapshot and descendant cleanup (no sessionId provided).\n")
        return None

    s_dir = get_copilot_home() / "session-state"
    s_file = s_dir / "hooks_state.json"
    global_lock = s_dir / "state_locks" / "hooks_state_global.flock"
    g_fd, g_key = acquire_state_lock_file(global_lock)
    try:
        all_state: dict[str, object] = {}
        if s_file.exists():
            try:
                loaded = json.loads(s_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    all_state = loaded
            except Exception:
                all_state = {}

        # 1. Walk parent_session_id upward to find root_id (cycle-guarded).
        root_id = norm_sid
        visited_up = {root_id}
        while True:
            cur_entry = all_state.get(root_id)
            parent_id = str(cur_entry.get("parent_session_id") or "").strip() if isinstance(cur_entry, dict) else ""
            # Stop scanning further once `not parent_id or parent_id in visited_up`.
            if not parent_id or parent_id in visited_up:
                break
            visited_up.add(parent_id)
            root_id = parent_id

        # 2. BFS downward from root_id across parent_session_id edges.
        descendant_set: set[str] = set()
        frontier = [root_id]
        while frontier:
            parent_cur = frontier.pop(0)
            for cand_id, cand_val in all_state.items():
                if cand_id == root_id or cand_id in descendant_set or not isinstance(cand_val, dict):
                    continue
                if str(cand_val.get("parent_session_id") or "").strip() == parent_cur:
                    descendant_set.add(str(cand_id))
                    frontier.append(str(cand_id))
        descendant_ids = sorted(descendant_set)

        snap_entries: dict[str, dict[str, object]] = {}
        for tree_sid in [root_id] + descendant_ids:
            raw_e = all_state.get(tree_sid)
            if isinstance(raw_e, dict):
                snap_entries[tree_sid] = {
                    wk: raw_e[wk] for wk in HOOK_STATE_SNAPSHOT_WHITELIST if wk in raw_e
                }
            else:
                snap_entries[tree_sid] = {}

        snap_path: Path | None = None
        if target_dir is not None:
            target_dir.mkdir(parents=True, exist_ok=True)
            snap_path = target_dir / "hook_state_snapshot.json"
            snap_payload = {
                "root_session_id": root_id,
                "descendant_session_ids": descendant_ids,
                "entries": snap_entries,
            }
            atomic_write_text(snap_path, json.dumps(snap_payload, indent=2))

        root_entry = all_state.get(root_id)
        if not isinstance(root_entry, dict):
            root_entry = {"last_failed_tool": None, "last_failed_args": None, "failures_by_tool": {}, "active_agent": None}

        if immediate_cleanup:
            for d_id in descendant_ids:
                all_state.pop(d_id, None)
            root_entry["active_agent"] = None
            root_entry["facet_turn_viewed_paths"] = []
            root_entry["facet_turn_handoff_attempts"] = 0
            root_entry["pending_state_cleanup"] = []
            all_state[root_id] = root_entry
            atomic_write_text(s_file, json.dumps(all_state, indent=2))
        elif descendant_ids:
            existing_pending = {
                str(x) for x in (root_entry.get("pending_state_cleanup") or []) if str(x) and str(x) != root_id
            }
            root_entry["pending_state_cleanup"] = sorted(existing_pending | descendant_set)
            all_state[root_id] = root_entry
            atomic_write_text(s_file, json.dumps(all_state, indent=2))

        return snap_path
    except Exception as exc:
        sys.stderr.write(f"Warning: Failed to snapshot session tree state: {exc}\n")
        return None
    finally:
        release_state_lock_file(g_fd, g_key)


def write_reasoning_trace(cwd, agent_role, content):
    r"""Persist an optional reasoning trace when ``archive-trace`` is enabled.

    Args:
        cwd: Workspace root directory.
        agent_role: Specialist role name (e.g. ``"Researcher"``).
        content: Cleaned subagent response text.

    Returns:
        SHA-256 hex digest of the written trace file, or ``None`` when skipped.
    """
    # Persist per-specialist reasoning logs into `.pipeline_scratch/` only when `scratch_retention == 'archive-trace'`.
    cfg = load_pipeline_config(cwd)
    if cfg.get("scratch_retention") != "archive-trace":
        return None
    slot_filename = REASONING_TRACE_SLOTS.get(agent_role) or REASONING_TRACE_SLOTS.get(str(agent_role).capitalize())
    if not slot_filename or not content:
        return None
    try:
        scratch_dir = cwd / SCRATCH_DIR_NAME
        scratch_dir.mkdir(parents=True, exist_ok=True)
        canonical_path = scratch_dir / slot_filename
        tmp_path = scratch_dir / f".tmp_{os.getpid()}_{int(time.time() * 1000)}"
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, canonical_path)
        return compute_file_sha256(canonical_path)
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to write reasoning trace {slot_filename}: {e}\n")
        return None


def _prune_empty_scratch_dir(cwd):
    r"""Remove ``.pipeline_scratch/`` when no transient files remain inside it.

    Args:
        cwd: Workspace root directory.
    """
    # Delete `.pipeline_scratch/` once all temporary rollback and OCR staging files have been consumed.
    scratch_dir = cwd / SCRATCH_DIR_NAME
    if not scratch_dir.exists():
        return
    try:
        if not list(scratch_dir.iterdir()):
            scratch_dir.rmdir()
    except Exception:
        pass


def stage_rollback_backup(cwd, target_name):
    r"""Move an existing draft to ``.rollback_<name>`` prior to ``create``.

    Allows atomic ``create`` overwrites on existing files while preserving
    byte-exact rollback if ``postToolUse`` validation or compilation fails.

    Args:
        cwd: Workspace root directory.
        target_name: Base filename in ``cwd`` to back up before overwriting.
    """
    # Copy an existing artifact to `.pipeline_scratch/.rollback_<name>` before `create` runs so broken drafts can be rolled back.
    src_path = cwd / target_name
    if not src_path.is_file():
        return
    try:
        scratch_dir = cwd / SCRATCH_DIR_NAME
        scratch_dir.mkdir(parents=True, exist_ok=True)
        backup_path = scratch_dir / f".rollback_{target_name}"
        os.replace(src_path, backup_path)
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to stage rollback backup for {target_name}: {e}\n")


def discard_rollback_backup(cwd, target_name):
    """Delete a staged rollback backup once the replacement passes validation.

    Args:
        cwd: Workspace root directory.
        target_name: Base filename whose ``.rollback_<name>`` backup can be removed.
    """
    # Remove `.rollback_<name>` after `postToolUse` confirms the newly written file passed validation.
    scratch_dir = cwd / SCRATCH_DIR_NAME
    if not scratch_dir.exists():
        return
    backup_path = scratch_dir / f".rollback_{target_name}"
    if backup_path.exists():
        try:
            backup_path.unlink()
        except Exception:
            pass
    _prune_empty_scratch_dir(cwd)


def restore_or_unlink_invalid_draft(cwd, target_path):
    """Discard an invalid newly created draft and restore any staged backup.

    Args:
        cwd: Workspace root directory.
        target_path: Path to the invalid draft file to unlink.
    """
    # Restore the previous good `.rollback_<name>` backup (or remove a broken first draft) when `create` writes invalid syntax.
    target_name = target_path.name
    try:
        if target_path.exists():
            target_path.unlink()
    except Exception:
        pass
    backup_path = cwd / SCRATCH_DIR_NAME / f".rollback_{target_name}"
    if backup_path.is_file():
        try:
            os.replace(backup_path, cwd / target_name)
        except Exception:
            pass
    _prune_empty_scratch_dir(cwd)


def classify_error_incident(
    error_info: object,
    error_context: object = None,
    recoverable: object = None,
) -> tuple[str, str]:
    r"""Extract a stack-free error message and classify incident category.

    Checks empty-response 422 patterns strictly before context-overflow patterns
    because Copilot's empty-response message mentions ``context window`` in its
    explanatory text.

    Args:
        error_info: Raw ``error`` dict or message string from ``errorOccurred``.
        error_context: Optional ``errorContext`` string.
        recoverable: Optional ``recoverable`` boolean.

    Returns:
        Tuple ``(category, clean_message)`` where ``category`` is one of
        ``"quota_exceeded"``, ``"empty_response_422"``, ``"context_overflow"``,
        or ``"other"``.
    """
    if isinstance(error_info, dict):
        raw_msg = str(error_info.get("message") or error_info.get("name") or "Unknown error")
    else:
        raw_msg = str(error_info or "Unknown error")

    # Strip native stack frames (e.g. '0: <unknown>' / '__pthread_exit') if embedded in a raw string.
    raw_msg = re.split(r"\n\s*0:\s+", raw_msg, maxsplit=1)[0].strip()
    if len(raw_msg) >= 2 and raw_msg[0] == '"' and raw_msg[-1] == '"':
        try:
            unq = json.loads(raw_msg)
            if isinstance(unq, str):
                raw_msg = unq.strip()
        except Exception:
            pass

    code_hint = ""
    clean_msg = raw_msg
    if raw_msg.startswith("{"):
        try:
            inner = json.loads(raw_msg)
            if isinstance(inner, dict):
                code_hint = str(inner.get("code") or inner.get("type") or "")
                inner_msg = str(inner.get("message") or "").strip()
                if inner_msg:
                    clean_msg = f"{inner_msg} ({code_hint})" if code_hint else inner_msg
        except Exception:
            pass

    probe = f"{clean_msg} {code_hint} {error_context or ''}".lower()
    if any(
        tok in probe
        for tok in (
            "quota_exceeded",
            "exceeded your monthly quota",
            "additional_spend_limit_reached",
            "rate limit",
            "status 429",
        )
    ):
        return ("quota_exceeded", clean_msg)
    if any(
        tok in probe
        for tok in (
            "returned an empty response",
            "empty response",
            "no text or tool calls",
            "status 422",
            " 422 ",
        )
    ):
        return ("empty_response_422", clean_msg)
    if any(
        tok in probe
        for tok in (
            "context_length_exceeded",
            "maximum context length",
            "context length",
            "exceeds the context window",
            "prompt is too long",
            "token limit exceeded",
        )
    ):
        return ("context_overflow", clean_msg)
    if any(
        tok in probe
        for tok in (
            "stream timed out",
            "http stream timed out",
            "request timed out",
            "read timed out",
            "timed out",
        )
    ):
        return ("stream_timeout", clean_msg)
    return ("other", clean_msg)


def _default_systemone_http_transport(
    url: str, payload: dict[str, object], timeout_sec: float
) -> dict[str, object] | None:
    """Send a JSON POST request to Ollama's native ``/v1/systemone`` endpoint.

    Args:
        url: Full HTTP endpoint URL (e.g. ``http://localhost:11434/v1/systemone``).
        payload: Request body dictionary carrying ``model``, ``state``, ``questions``,
            and ``keep_alive``.
        timeout_sec: Socket timeout in seconds.

    Returns:
        Parsed JSON response dictionary, or ``None`` on network/HTTP errors.
    """
    # POST a JSON decision query to Ollama's native `/v1/systemone` endpoint and return the decoded response dict (or `None` if offline).
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=max(1.0, float(timeout_sec))) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
            return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


_SYSTEMONE_HTTP_TRANSPORT = _default_systemone_http_transport


def append_run_telemetry_event(event_type: str, fields: dict[str, object], session_id: str | None = None) -> None:
    r"""Append a content-redacted telemetry record strictly to ``$COPILOT_HOME/session-state/run_telemetry.jsonl``.

    Never writes inside the repository or ``specs/<slug>/``, and never records
    raw prompt text, source code bodies, or specification prose.

    Args:
        event_type: Short telemetry category (e.g. ``"hook"``, ``"systemone"``, ``"edit_snap"``, ``"stage_span"``).
        fields: Whitelisted numeric/enum metadata fields.
        session_id: Optional active session identifier.
    """
    # Persist lightweight JSONL telemetry outside the workspace so `specs/<slug>/` stays completely untouched.
    try:
        t_dir = get_copilot_home() / "session-state"
        t_dir.mkdir(parents=True, exist_ok=True)
        t_path = t_dir / "run_telemetry.jsonl"
        rec: dict[str, object] = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "type": str(event_type),
        }
        if session_id:
            rec["sessionId"] = str(session_id)
        rec.update(fields)
        payload_bytes = (json.dumps(rec, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(str(t_path), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(fd, payload_bytes)
        finally:
            os.close(fd)
    except Exception:
        pass


def call_systemone_decision_model(
    cwd: Path,
    state_obj: object,
    questions: dict[str, object],
    images: list[str] | None = None,
) -> dict[str, object] | None:
    """Invoke the configured System-1 decision model with strict ``keep_alive: 0`` non-co-residency.

    Executes only at stage-transition boundaries when ``decision_model.enabled``
    is ``True`` and ``FACET_DISABLE_SYSTEMONE != "1"``. Automatically falls back
    to ``None`` if Ollama or the model is unavailable.

    Args:
        cwd: Workspace root directory used to load ``.pipeline_config.json``.
        state_obj: Structured ``state`` object or string inspected by the decider.
        questions: Mapping of question IDs to ``choice`` / ``noul`` / ``score`` specs.
        images: Optional base64-encoded image strings for multimodal ``S0V`` checks.

    Returns:
        The ``answers`` dictionary from ``/v1/systemone``, or ``None`` if disabled/offline.
    """
    # Query `pplx-decider-flash` at stage boundaries with `keep_alive=0` so `0 B` remain in VRAM under `OLLAMA_MAX_LOADED_MODELS=1`.
    if os.environ.get("FACET_DISABLE_SYSTEMONE") == "1":
        return None
    cfg = load_pipeline_config(cwd)
    dm_cfg = cfg.get("decision_model")
    if not isinstance(dm_cfg, dict) or not bool(dm_cfg.get("enabled", True)):
        return None
    model_name = str(dm_cfg.get("model") or "pplx-decider-flash").strip()
    keep_alive_val = dm_cfg.get("keep_alive", 0)
    timeout_s = min(float(dm_cfg.get("timeout_sec", 12.0) or 12.0), 20.0)
    base_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"
    body: dict[str, object] = {
        "model": model_name,
        "state": state_obj,
        "questions": questions,
        "keep_alive": keep_alive_val,
    }
    if images:
        body["images"] = images
    t0_s1 = time.monotonic()
    _pg_mod = sys.modules.get("pipeline_guards")
    _eff_transport = getattr(_pg_mod, "_SYSTEMONE_HTTP_TRANSPORT", _SYSTEMONE_HTTP_TRANSPORT) if _pg_mod is not None else _SYSTEMONE_HTTP_TRANSPORT
    resp = _eff_transport(f"{base_url}/v1/systemone", body, timeout_s)
    lat_ms = round((time.monotonic() - t0_s1) * 1000.0, 2)
    if isinstance(resp, dict) and isinstance(resp.get("answers"), dict):
        ans = resp["answers"]
        append_run_telemetry_event(
            "systemone",
            {
                "model": model_name,
                "keep_alive": keep_alive_val,
                "latency_ms": lat_ms,
                "questions": sorted(str(k) for k in questions.keys()),
                "has_images": bool(images),
            },
        )
        return ans  # type: ignore[return-value]
    append_run_telemetry_event("systemone", {"model": model_name, "latency_ms": lat_ms, "unreachable": True})
    return None


def check_scope_and_lock(payload, scope, action):
    """Enforce scope-aware hook shadowing and atomic POSIX deduplication.

    User-scope hooks yield immediately when an active workspace hook config
    exists. Concurrent duplicate events are suppressed using an exclusive
    ``O_CREAT | O_EXCL`` lock file keyed on the event fingerprint.

    Args:
        payload: Hook stdin JSON dictionary.
        scope: Hook execution scope (``"workspace"`` or ``"user"``).
        action: CLI hook action name (e.g. ``"pre-tool"``).

    Returns:
        ``True`` if this invocation should yield/skip, ``False`` to proceed.
    """
    # Bypass dual-hook event locks for agent-scoped hooks and CLI admin actions.
    if action in (
        "hypervisor-guard", "facet-prompt", "facet-stop",
        "coordinator-guard", "orchestrator-prompt", "orchestrator-stop",
        "reset", "garden-specs",
    ):
        return False

    # 1. Scope-Aware Shadowing: User hook yields immediately if workspace hook exists
    if scope == "user":
        cwd = get_payload_cwd(payload)
        ws_hook_config = cwd / ".github" / "hooks" / "pipeline-guards.json"
        if ws_hook_config.exists():
            try:
                cfg = json.loads(ws_hook_config.read_text(encoding="utf-8"))
                # Return early when `not cfg.get('disableAllHooks', False)`.
                if not cfg.get("disableAllHooks", False):
                    return True  # Yield execution to workspace hook
            except Exception:
                pass

    # 2. Secondary Atomic Event Lock via os.O_CREAT | os.O_EXCL
    session_id = get_session_id(payload)
    timestamp = str(payload.get("timestamp") or "")
    tool_call_id = str(payload.get("toolCallId") or payload.get("id") or "")
    tool_name = payload.get("toolName") or payload.get("tool_name") or ""
    agent_name = (
        payload.get("agentName")
        or payload.get("agent_name")
        or payload.get("agentType")
        or payload.get("agent_type")
        or ""
    )

    fingerprint_raw = f"{session_id}:{action}:{timestamp}:{tool_call_id}:{tool_name}:{agent_name}".encode("utf-8")
    lock_hash = hashlib.sha256(fingerprint_raw).hexdigest()

    try:
        lock_dir = get_copilot_home() / "session-state" / "hook_locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        # Purge stale locks (> 15 seconds)
        now = time.time()
        for old in lock_dir.glob("*.lock"):
            try:
                if now - old.stat().st_mtime > 15:
                    old.unlink()
            except Exception:
                pass

        lock_path = lock_dir / f"{lock_hash}.lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return False  # Successfully acquired exclusive event lock
    except FileExistsError:
        return True  # Event already claimed by another hook instance
    except Exception:
        return False


def append_hook_trace_record(
    action: str,
    scope: str,
    payload: dict[str, object],
    result: dict[str, object],
    yielded: bool = False,
) -> None:
    """Append a whitelisted metadata trace line when ``FACET_HOOK_TRACE=1``.

    Never writes to ``stdout`` or ``stderr`` and never emits prompt text, tool
    file contents, or subagent response bodies.

    Args:
        action: Dispatched CLI hook action name.
        scope: Execution scope (``"workspace"`` or ``"user"``).
        payload: Hook stdin JSON dictionary.
        result: Returned hook decision dictionary.
        yielded: Whether ``check_scope_and_lock`` suppressed execution.
    """
    # When `FACET_HOOK_TRACE=1`, append one metadata-only JSON line (never prompt or file contents) to `~/.copilot/session-state/hook_trace.jsonl`.
    if os.environ.get("FACET_HOOK_TRACE") != "1":
        return
    try:
        tool_name = str(payload.get("toolName") or payload.get("tool_name") or "")
        tool_args = payload.get("toolArgs") or payload.get("tool_input")
        mode_val = ""
        target_agent = ""
        if tool_name in ("task", "agent") and isinstance(tool_args, dict):
            mode_val = str(tool_args.get("mode") or "")
            target_agent = str(tool_args.get("agent_type") or "")

        if yielded:
            decision = "yield"
        elif isinstance(result, dict):
            decision = str(
                result.get("permissionDecision")
                or result.get("decision")
                or result.get("behavior")
                or "allow"
            )
        else:
            decision = "allow"

        record: dict[str, object] = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "action": str(action),
            "scope": str(scope),
            "sessionId": get_session_id(payload),
            "agentId": str(payload.get("agentId") or payload.get("agent_id") or ""),
            "agentType": str(
                payload.get("agentType")
                or payload.get("agent_type")
                or payload.get("agentName")
                or payload.get("agent_name")
                or ""
            ),
            "parentAgentId": str(payload.get("parentAgentId") or payload.get("parent_agent_id") or ""),
            "toolName": tool_name,
            "mode": mode_val,
            "targetAgent": target_agent,
            "decision": decision,
            "outputKeys": sorted(str(k) for k in result.keys()) if isinstance(result, dict) else [],
        }
        trace_dir = get_copilot_home() / "session-state"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_path = trace_dir / "hook_trace.jsonl"
        line_bytes = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(str(trace_path), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line_bytes)
        finally:
            os.close(fd)
    except Exception:
        pass

__all__ = [
    "DEFAULT_STAGE_RECEIPTS",
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
    "clear_stage_receipts",
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
    "load_stage_receipts",
    "notify_facet_lens_sse",
    "purge_session_state_entries",
    "release_state_lock_file",
    "restore_or_unlink_invalid_draft",
    "save_pipeline_fsm",
    "save_session_state",
    "snapshot_session_tree_state",
    "stage_rollback_backup",
    "update_session_state",
    "update_stage_receipts",
    "sync_shadow_seal_ledger",
    "verify_workspace_cryptographic_seals",
    "write_reasoning_trace",
]
