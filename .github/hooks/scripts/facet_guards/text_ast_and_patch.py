"""RFC 8259 Gemma JSON formatter, docstring/header normalizers, format-symmetric edit auto-snapper, and AST-1..AST-6 analyzers.

Function Map:
  - Section 1: Multi-Stage Format-Symmetric & AST `edit` Auto-Snapper (`resolve_edit_pair_with_stage`, `_infer_missing_create_path`)
  - Section 2: Lexical Scope (`AST-2`), Rational Pole (`AST-3`) & Method L3 Hunk Analyzers (`find_lexical_unbound_names`, `detect_rational_pole_sign_clip`)
  - Section 3: Markdown Sanitization & RFC 8259 Gemma JSON Escape Canonicalizer (`format_gemma_json_artifact`)
  - Section 4: Python Module & Verification Suite AST Contract Validators (`_hoist_misplaced_future_imports`, `validate_python_ast`, `validate_verification_script_ast`)
  - Section 5: Chat Link Encoding, Table Formatting & Command Fuzzy Matchers (`format_chat_file_link`, `is_near_miss_command`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import builtins
import difflib
import fnmatch
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap
import tokenize
import urllib.parse
import urllib.request

from .constants_and_context import (
    DEFAULT_CONFIG,
    HOOKS_REPO_ROOT,
    _EMOJI_STRIP_RE,
    _LATEX_N_EXACT_STEMS,
    _LATEX_N_PREFIX_STEMS,
    _LATEX_T_EXACT_STEMS,
    _LATEX_T_PREFIX_STEMS,
    _LOCAL_ABS_MD_LINK_RE,
    _PEP508_REQ_RE,
    _TYPOGRAPHIC_LIGATURE_MAP,
    extract_slug_from_file,
)
from .state_locks_and_seals import (
    load_pipeline_config,
    load_pipeline_fsm,
)

def _is_verification_target_name(filename, cfg, rel_path=None):
    """Check whether a file is a verification or audit target.

    Matches against ``AUDIT_FEEDBACK.json``, ``VERIFICATION.md``, configured
    ``toolchain.verification_globs``, and Python ``verify_*`` / ``test_*``
    naming conventions.

    Args:
        filename: Base filename to test.
        cfg: Merged pipeline configuration dictionary.
        rel_path: Optional workspace-relative POSIX path for directory globs.

    Returns:
        ``True`` if the target belongs to Stage 4 verification artifacts.
    """
    # Match filenames owned by `@Auditor` (`verify_*.py`, `test_*.py`, `LOCAL_CRITIC.json`, `AUDIT_FEEDBACK.json`, `VERIFICATION.md`).
    if filename in ("AUDIT_FEEDBACK.json", "VERIFICATION.md"):
        return True
    tc = (cfg or {}).get("toolchain") or {}
    globs = tc.get("verification_globs") or DEFAULT_CONFIG["toolchain"]["verification_globs"]
    for pat in globs:
        if not isinstance(pat, str) or not pat.strip():
            continue
        norm_pat = pat.strip().replace("\\", "/")
        if "/" in norm_pat:
            if rel_path and fnmatch.fnmatch(str(rel_path).replace("\\", "/"), norm_pat):
                return True
        else:
            # Return early when `fnmatch.fnmatch(filename, norm_pat)`.
            if fnmatch.fnmatch(filename, norm_pat):
                return True
    if filename.startswith(("verify_", "test_")) or filename.endswith("_test.py"):
        return True
    return False


def _find_implementation_files(cwd, cfg):
    r"""Locate Python production source files in ``cwd``.

    Excludes verification, test, and smoke harnesses from the workspace root.

    Args:
        cwd: Workspace root directory.
        cfg: Merged pipeline configuration dictionary.

    Returns:
        Sorted list of candidate production ``*.py`` ``Path`` objects.
    """
    # List workspace-root production `.py` files while excluding test suites, smoke scripts, and dunder files.
    found = []
    for p in sorted(cwd.glob("*.py")):
        if not p.is_file():
            continue
        if p.name.startswith(("test_", "audit_", "verify_", "smoke_", "setup", "conftest")):
            continue
        if p.name == "post_archive_embed.py" or p.name.endswith(("_test.py", "_smoke.py")):
            continue
        if _is_verification_target_name(p.name, cfg, rel_path=p.name):
            continue
        found.append(p)
    return found


# ============================================================================
# Section 1: Multi-Stage Format-Symmetric & AST `edit` Auto-Snapper
# Maintains format symmetry between `preToolUse` `edit(old_str)` blocks and `postToolUse` canonicalizers across whitespace, indentation, comments, docstrings, imports, and AST symbols.
# ============================================================================

def _split_lines_with_offsets(text):
    r"""Split ``text`` into lines with character offsets.

    Args:
        text: Source text to index line by line.

    Returns:
        List of ``(start, end_no_nl, end_with_nl, line_no_nl)`` tuples.
    """
    # Index every line's exact character start and end offsets (with and without `\r\n`) so snapped `old_str` slices stay byte-exact.
    spans = []
    pos = 0
    for raw_ln in text.splitlines(keepends=True):
        start = pos
        end_with_nl = pos + len(raw_ln)
        stripped_nl = raw_ln.rstrip("\r\n")
        end_no_nl = start + len(stripped_nl)
        spans.append((start, end_no_nl, end_with_nl, stripped_nl))
        pos = end_with_nl
    return spans


def _strip_python_comments_per_line(source_block):
    r"""Strip Python ``#`` comments safely using stdlib ``tokenize``.

    Preserves ``#`` characters inside string literals and normalizes trailing
    whitespace on each line.

    Args:
        source_block: Python source snippet or file content.

    Returns:
        List of comment-stripped lines, or ``None`` on tokenization failure.
    """
    # Use Python's `tokenize` module to strip `#` comments without corrupting `#` characters inside string literals.
    raw_lines = source_block.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    comment_cols = {}
    try:
        tok_gen = tokenize.generate_tokens(io.StringIO("\n".join(raw_lines) + "\n").readline)
        for tok in tok_gen:
            if tok.type == tokenize.COMMENT:
                lineno_1based, col_0based = tok.start
                if 1 <= lineno_1based <= len(raw_lines):
                    comment_cols[lineno_1based - 1] = min(comment_cols.get(lineno_1based - 1, col_0based), col_0based)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    cleaned = []
    for idx, ln in enumerate(raw_lines):
        if idx in comment_cols:
            cleaned.append(ln[: comment_cols[idx]].rstrip(" \t"))
        else:
            cleaned.append(ln.rstrip(" \t"))
    return cleaned


def _apply_indent_delta(new_str, delta):
    r"""Shift non-blank lines in ``new_str`` by a constant space offset.

    Args:
        new_str: Replacement text whose indentation should match disk context.
        delta: Integer column shift (positive indents, negative dedents).

    Returns:
        Shifted ``new_str`` preserving trailing newline convention, or the
        original ``new_str`` if negative dedent would clip non-space tokens.
    """
    # Shift every non-empty line in `new_str` left or right by `delta` spaces so replacement code matches the target block's indentation.
    if delta == 0 or not new_str:
        return new_str
    trailing_nl = "\n" if new_str.endswith(("\n", "\r\n")) else ""
    lines = new_str.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if delta > 0:
        pad = " " * delta
        shifted = [(pad + ln if ln.strip() else "") for ln in lines]
        return "\n".join(shifted) + trailing_nl
    need = -delta
    if all((not ln.strip()) or (len(ln) - len(ln.lstrip(" ")) >= need) for ln in lines):
        shifted = [(ln[need:] if ln.strip() else "") for ln in lines]
        return "\n".join(shifted) + trailing_nl
    return new_str


def resolve_edit_pair_with_stage(file_text, old_str, new_str, is_python=False):
    r"""Resolve ``old_str``/``new_str`` via deterministic format- and AST-aware stages and report which stage matched.

    Phases:
      - Phase 1: CRLF, Horizontal Whitespace, Indentation-Delta & JSON Canonical Snapping
      - Phase 2: Python Tokenize Comment-Strip, Raw-Docstring & Ruff Format Equivalence
      - Phase 3: Python AST Symbol, Whole-Module & Contiguous Import-Block Matching

    Args:
        file_text: Current file text on disk.
        old_str: Caller-supplied search block.
        new_str: Caller-supplied replacement block.
        is_python: Enable Python formatting, comment-strip, and AST stages when ``True``.

    Returns:
        Tuple ``(matched_old_str, adjusted_new_str, snap_stage_label)``, or
        ``None`` if exact match already exists, multiple matches exist, or no stage matches.
    """
    # --- Phase 1: CRLF, Horizontal Whitespace, Indentation-Delta & JSON Canonical Snapping ---
    if not old_str or not file_text or file_text.count(old_str) >= 1:
        return None

    norm_old = old_str.replace("\r\n", "\n").replace("\r", "\n")
    if file_text.count(norm_old) == 1:
        return (norm_old, new_str.replace("\r\n", "\n") if isinstance(new_str, str) else new_str, "stage1_crlf")

    file_spans = _split_lines_with_offsets(file_text)
    if not file_spans:
        return None
    old_ends_nl = old_str.endswith(("\n", "\r\n"))
    old_lines = norm_old.splitlines()
    if not old_lines or not any(ln.strip() for ln in old_lines):
        return None

    # Helper function `_slice_span`: Return verbatim substring in ``file_text`` across line indices.
    def _slice_span(first_idx, last_idx):
        r"""Return verbatim substring in ``file_text`` across line indices."""
        start_char = file_spans[first_idx][0]
        end_char = file_spans[last_idx][2] if old_ends_nl else file_spans[last_idx][1]
        return file_text[start_char:end_char]

    # Stage 1: Exact line count with CRLF & trailing horizontal whitespace normalized
    k = len(old_lines)
    old_rstripped = [ln.rstrip(" \t") for ln in old_lines]
    file_rstripped = [sp[3].rstrip(" \t") for sp in file_spans]
    if k <= len(file_spans):
        stage1_hits = [
            i for i in range(len(file_spans) - k + 1)
            if file_rstripped[i : i + k] == old_rstripped
        ]
        if len(stage1_hits) == 1:
            idx0 = stage1_hits[0]
            return (_slice_span(idx0, idx0 + k - 1), new_str, "stage1_ws")

    # Stage 2: Uniform indentation-delta + blank-line-insensitive exact code/text match
    old_nb = [(len(ln) - len(ln.lstrip(" ")), ln.strip()) for ln in old_lines if ln.strip() and not ln.startswith("\t")]
    if len(old_nb) >= 2 or (len(old_nb) == 1 and len(old_nb[0][1]) >= 16):
        file_nb = [
            (line_idx, len(sp[3]) - len(sp[3].lstrip(" ")), sp[3].strip())
            for line_idx, sp in enumerate(file_spans)
            if sp[3].strip() and not sp[3].startswith("\t")
        ]
        m = len(old_nb)
        stage2_hits = []
        for j in range(len(file_nb) - m + 1):
            window = file_nb[j : j + m]
            if [w[2] for w in window] != [o[1] for o in old_nb]:
                continue
            deltas = {window[p][1] - old_nb[p][0] for p in range(m)}
            if len(deltas) == 1:
                delta_val = next(iter(deltas))
                stage2_hits.append((window[0][0], window[-1][0], delta_val))
        if len(stage2_hits) == 1:
            s_idx, e_idx, delta_val = stage2_hits[0]
            return (_slice_span(s_idx, e_idx), _apply_indent_delta(new_str, delta_val), "stage2_indent")

    if not is_python:
        # JSON Canonical Re-Indent & Full-Document Snap (handles compact array/object old_str against indent=2 JSON on disk)
        if file_text.lstrip().startswith("{"):
            try:
                old_j = json.loads(norm_old.strip())
                pretty_old = json.dumps(old_j, indent=2, ensure_ascii=False)
                if file_text.count(pretty_old) == 1:
                    return (pretty_old, new_str, "stage2_json_pretty")
                if isinstance(old_j, dict) and isinstance(json.loads(file_text), dict) and new_str.lstrip().startswith("{"):
                    json.loads(new_str)
                    return (file_text.rstrip("\n"), new_str.rstrip("\n"), "stage2_json_doc")
            except Exception:
                pass
        return None

    # --- Phase 2: Python Tokenize Comment-Strip, Raw-Docstring & Ruff Format Equivalence ---
    # Stage 3: Python tokenize comment-stripped executable-statement exact match
    old_code_lines = _strip_python_comments_per_line(norm_old)
    file_code_lines = _strip_python_comments_per_line(file_text)
    if old_code_lines is not None and file_code_lines is not None and len(file_code_lines) >= len(file_spans):
        old_c_nb = [(len(ln) - len(ln.lstrip(" ")), ln.strip()) for ln in old_code_lines if ln.strip()]
        if len(old_c_nb) >= 2:
            file_c_nb = [
                (line_idx, len(file_code_lines[line_idx]) - len(file_code_lines[line_idx].lstrip(" ")), file_code_lines[line_idx].strip())
                for line_idx in range(len(file_spans))
                if file_code_lines[line_idx].strip()
            ]
            mc = len(old_c_nb)
            stage3_hits = []
            for j in range(len(file_c_nb) - mc + 1):
                window = file_c_nb[j : j + mc]
                if [w[2] for w in window] != [o[1] for o in old_c_nb]:
                    continue
                deltas = {window[p][1] - old_c_nb[p][0] for p in range(mc)}
                if len(deltas) == 1:
                    delta_val = next(iter(deltas))
                    stage3_hits.append((window[0][0], window[-1][0], delta_val))
            if len(stage3_hits) == 1:
                s_idx, e_idx, delta_val = stage3_hits[0]
                return (_slice_span(s_idx, e_idx), _apply_indent_delta(new_str, delta_val), "stage3_tokenize")

    # Stage 3b: Python docstring/raw-string canonical equivalence match
    try:
        canon_old = _canonicalize_module_docstring_order(_promote_latex_docstrings_to_raw(norm_old)).rstrip("\n")
        if canon_old and canon_old != norm_old.rstrip("\n") and file_text.count(canon_old) == 1:
            return (canon_old + ("\n" if old_ends_nl and file_text.count(canon_old + "\n") == 1 else ""), new_str, "stage3b_docstring_canonical")
    except Exception:
        pass

    # Stage 3c: Lossless Ruff formatter pre-normalization (exact 1-of-1 match after postToolUse auto-formatting)
    try:
        fmt_old = _format_python_fragment_with_ruff(norm_old)
        if fmt_old:
            fmt_cand = fmt_old.rstrip("\n")
            if fmt_cand and fmt_cand != norm_old.rstrip("\n"):
                if file_text.count(fmt_cand) == 1:
                    use_nl = "\n" if (old_ends_nl and file_text.count(fmt_cand + "\n") == 1) else ""
                    return (fmt_cand + use_nl, new_str, "stage3c_ruff_format")
                fmt_lines = fmt_cand.split("\n")
                m_fmt = len(fmt_lines)
                n_spans = len(file_spans)
                if 0 < m_fmt <= n_spans and any(fl.strip() for fl in fmt_lines):
                    fmt_indents = [len(fl) - len(fl.lstrip(" ")) if fl.strip() else None for fl in fmt_lines]
                    fmt_stripped = [fl.lstrip(" ") if fl.strip() else "" for fl in fmt_lines]
                    fmt_hits = []
                    for idx_f in range(n_spans - m_fmt + 1):
                        deltas_f = set()
                        ok_f = True
                        for k_f in range(m_fmt):
                            fl_line = file_spans[idx_f + k_f][3]
                            if fmt_indents[k_f] is None:
                                if fl_line.strip() != "":
                                    ok_f = False
                                    break
                            else:
                                if fl_line.lstrip(" ") != fmt_stripped[k_f]:
                                    ok_f = False
                                    break
                                deltas_f.add((len(fl_line) - len(fl_line.lstrip(" "))) - fmt_indents[k_f])
                        if ok_f and len(deltas_f) <= 1:
                            d_f = next(iter(deltas_f), 0)
                            fmt_hits.append((idx_f, idx_f + m_fmt - 1, d_f))
                    if len(fmt_hits) == 1:
                        s_idx, e_idx, d_f = fmt_hits[0]
                        return (_slice_span(s_idx, e_idx), _apply_indent_delta(new_str, d_f), "stage3c_ruff_format")
            # Stage 3d: Lossless Ruff formatter composed with tokenize comment-stripping (exact 1-of-1 executable statement match)
            fmt_code_lines = _strip_python_comments_per_line(fmt_old)
            if fmt_code_lines is not None and file_code_lines is not None and len(file_code_lines) >= len(file_spans):
                fmt_c_nb = [(len(ln) - len(ln.lstrip(" ")), ln.strip()) for ln in fmt_code_lines if ln.strip()]
                if len(fmt_c_nb) >= 2:
                    file_c_nb_3d = [
                        (line_idx, len(file_code_lines[line_idx]) - len(file_code_lines[line_idx].lstrip(" ")), file_code_lines[line_idx].strip())
                        for line_idx in range(len(file_spans))
                        if file_code_lines[line_idx].strip()
                    ]
                    mc_3d = len(fmt_c_nb)
                    s3d_hits = []
                    for j_3d in range(len(file_c_nb_3d) - mc_3d + 1):
                        win_3d = file_c_nb_3d[j_3d : j_3d + mc_3d]
                        if [w[2] for w in win_3d] != [o[1] for o in fmt_c_nb]:
                            continue
                        deltas_3d = {win_3d[p][1] - fmt_c_nb[p][0] for p in range(mc_3d)}
                        if len(deltas_3d) == 1:
                            s3d_hits.append((win_3d[0][0], win_3d[-1][0], next(iter(deltas_3d))))
                    if len(s3d_hits) == 1:
                        s_idx, e_idx, d_3d = s3d_hits[0]
                        return (_slice_span(s_idx, e_idx), _apply_indent_delta(new_str, d_3d), "stage3d_ruff_comment_strip")
    except Exception:
        pass

    # --- Phase 3: Python AST Symbol, Whole-Module & Contiguous Import-Block Matching ---
    # Stage 4: Python AST unique contiguous top-level symbol, import-block, or whole-module match
    try:
        dedented_old = "\n".join(old_lines)
        old_tree = ast.parse(dedented_old)
        file_tree = ast.parse(file_text)
        new_tree = None
        try:
            new_tree = ast.parse(new_str.replace("\r\n", "\n"))
        except Exception:
            new_tree = None
        def_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        if old_tree.body and all(isinstance(nd, def_types) for nd in old_tree.body):
            target_sig = [(type(nd).__name__, nd.name) for nd in old_tree.body]
            same_def_replacement = bool(
                new_tree is not None
                and new_tree.body
                and all(isinstance(nd, def_types) for nd in new_tree.body)
                and [(type(nd).__name__, nd.name) for nd in new_tree.body] == target_sig
            )
            r = len(target_sig)
            exact_sig_spans = []
            for j in range(len(file_tree.body) - r + 1):
                cand_nodes = file_tree.body[j : j + r]
                if not all(isinstance(nd, def_types) for nd in cand_nodes):
                    continue
                if [(type(nd).__name__, nd.name) for nd in cand_nodes] != target_sig:
                    continue
                decs0 = getattr(cand_nodes[0], "decorator_list", []) or []
                first_ln = min([cand_nodes[0].lineno] + [d.lineno for d in decs0]) - 1
                last_ln = int(getattr(cand_nodes[-1], "end_lineno", cand_nodes[-1].lineno)) - 1
                exact_sig_spans.append((first_ln, last_ln))
            sim_floor = 0.55 if (same_def_replacement and len(exact_sig_spans) == 1) else 0.82
            stage4_hits = []
            for first_ln, last_ln in exact_sig_spans:
                cand_slice = _slice_span(first_ln, last_ln)
                sim = difflib.SequenceMatcher(None, norm_old.strip(), cand_slice.strip()).ratio()
                if sim >= sim_floor:
                    stage4_hits.append((first_ln, last_ln))
            if len(stage4_hits) == 1:
                s_idx, e_idx = stage4_hits[0]
                return (_slice_span(s_idx, e_idx), new_str, "stage4_ast_symbol")

        # Stage 4b: Whole-module Python AST replacement when old_str spans >=82% of file with >=0.85 similarity
        if (
            new_tree is not None
            and len(old_tree.body) >= 2
            and len(new_tree.body) >= 2
            and len(old_lines) >= max(8, int(0.82 * len(file_spans)))
        ):
            doc_sim = difflib.SequenceMatcher(None, norm_old.strip(), file_text.strip()).ratio()
            if doc_sim >= 0.85:
                return (file_text.rstrip("\n"), new_str.rstrip("\n"), "stage4b_py_doc")

        # Stage 4c: Contiguous top-level Python import-block replacement
        imp_types = (ast.Import, ast.ImportFrom)

        def _imp_mod_key(nd: ast.AST) -> str:
            if isinstance(nd, ast.ImportFrom):
                return str(nd.module or "")
            if isinstance(nd, ast.Import) and nd.names:
                return str(nd.names[0].name)
            return ""

        if (
            new_tree is not None
            and len(old_tree.body) >= 3
            and all(isinstance(nd, imp_types) for nd in old_tree.body)
            and new_tree.body
            and all(isinstance(nd, imp_types) for nd in new_tree.body)
        ):
            first_mod = _imp_mod_key(old_tree.body[0])
            last_mod = _imp_mod_key(old_tree.body[-1])
            file_imp_runs: list[list[ast.AST]] = []
            cur_run: list[ast.AST] = []
            for f_nd in file_tree.body:
                if isinstance(f_nd, ast.Expr) and isinstance(f_nd.value, ast.Constant) and isinstance(f_nd.value.value, str):
                    if cur_run:
                        file_imp_runs.append(cur_run)
                        cur_run = []
                    continue
                if isinstance(f_nd, imp_types):
                    cur_run.append(f_nd)
                else:
                    if cur_run:
                        file_imp_runs.append(cur_run)
                        cur_run = []
            if cur_run:
                file_imp_runs.append(cur_run)
            imp_hits = []
            for run_nodes in file_imp_runs:
                a_idxs = [i for i, nd in enumerate(run_nodes) if _imp_mod_key(nd) == first_mod]
                b_idxs = [i for i, nd in enumerate(run_nodes) if _imp_mod_key(nd) == last_mod]
                for ai in a_idxs:
                    for bi in b_idxs:
                        if bi >= ai and (bi - ai + 1) >= 2:
                            imp_hits.append((run_nodes[ai].lineno - 1, int(getattr(run_nodes[bi], "end_lineno", run_nodes[bi].lineno)) - 1))
            if len(imp_hits) == 1:
                s_idx, e_idx = imp_hits[0]
                return (_slice_span(s_idx, e_idx), new_str, "stage4c_py_imports")
    except Exception:
        pass

    return None


def resolve_edit_pair(file_text, old_str, new_str, is_python=False):
    r"""Resolve ``old_str``/``new_str`` via the deterministic multi-stage cascade.

    Args:
        file_text: Current file text on disk.
        old_str: Caller-supplied search block.
        new_str: Caller-supplied replacement block.
        is_python: Enable Python formatting, comment-strip, and AST stages when ``True``.

    Returns:
        Tuple ``(matched_old_str, adjusted_new_str)``, or ``None``.
    """
    # Delegate to `resolve_edit_pair_with_stage` while keeping the 2-tuple return contract intact.
    res = resolve_edit_pair_with_stage(file_text, old_str, new_str, is_python=is_python)
    return (res[0], res[1]) if res is not None else None


def _get_declared_target_module(cwd):
    """Return normalized ``target_module`` filename from ``mapping.json``, or ``None``.

    Args:
        cwd: Active workspace root directory.

    Returns:
        Safe workspace-root Python filename (for example ``"mandelbrot.py"``),
        or ``None`` when ``mapping.json`` is absent or omits ``target_module``.
    """
    # Read `public_interface.target_module` from `mapping.json` and ensure it ends with `.py` without path traversal.
    mapping_file = cwd / "mapping.json"
    if not mapping_file.is_file():
        return None
    try:
        map_j = json.loads(mapping_file.read_text(encoding="utf-8"))
        pub_if = map_j.get("public_interface") or {}
        raw_tm = str(pub_if.get("target_module") or pub_if.get("module_name") or "").strip()
        if raw_tm and ".." not in raw_tm and "/" not in raw_tm and "\\" not in raw_tm:
            return raw_tm if Path(raw_tm).suffix else f"{raw_tm}.py"
    except Exception:
        pass
    return None


def _infer_missing_create_path(active_agent, current_stage, tool_args, cwd):
    """Infer an unambiguous ``create`` path when a model omits ``path``.

    Args:
        active_agent: Active specialist role name.
        current_stage: Current FSM stage identifier.
        tool_args: Raw ``create`` tool argument dictionary.
        cwd: Active workspace root directory.

    Returns:
        Inferred root artifact ``Path``, or ``None`` when ambiguous.
    """
    # Recover gracefully when a local model forgets the `path` argument in `create` if the active role and stage allow only one target file.
    if active_agent == "Scribe":
        return cwd / "SOURCE_OCR.md"
    if active_agent == "Researcher":
        if current_stage in ["STAGE_1_SPECIFICATION", "CHECKPOINT_1_AWAITING_APPROVAL"] or (cwd / "RESEARCH.md").is_file():
            return cwd / "manifest.json"
        return cwd / "RESEARCH.md"
    if active_agent == "Formalist":
        return cwd / "mapping.json"
    if active_agent == "Auditor" and isinstance(tool_args, dict):
        if current_stage in ("STAGE_1_CRITIC", "STAGE_2_CRITIC"):
            return cwd / "LOCAL_CRITIC.json"
        ft = str(tool_args.get("file_text", "")).lstrip()
        if ft.startswith("{"):
            return cwd / "AUDIT_FEEDBACK.json"
        if ft.startswith("#"):
            return cwd / "VERIFICATION.md"
        if any(tok in ft for tok in ("def test_", "import pytest", "from hypothesis", "import unittest")):
            existing_ver = sorted(p for p in cwd.glob("verify_*.py") if p.is_file())
            if len(existing_ver) == 1:
                return existing_ver[0]
            slug = (
                load_pipeline_fsm(cwd).get("concept_slug")
                or extract_slug_from_file(cwd / "mapping.json")
                or extract_slug_from_file(cwd / "manifest.json")
            )
            if slug:
                return cwd / f"verify_{slug}.py"
    if active_agent == "Engineer" and isinstance(tool_args, dict):
        ft = str(tool_args.get("file_text", "")).strip()
        if not ft:
            return None
        has_py_syntax = any(tok in ft for tok in ("def ", "class ", "import ", "from ", "return "))
        if not has_py_syntax and validate_requirements_txt(ft) == []:
            return cwd / "requirements.txt"
        if has_py_syntax:
            declared_mod = _get_declared_target_module(cwd)
            if declared_mod:
                return cwd / declared_mod
    return None


def extract_mandated_target_module(raw_text: str) -> str | None:
    r"""Extract an explicit caller-mandated ``<module>.py`` filename from prompt text.

    Args:
        raw_text: Raw user prompt or ``original_user_request`` string.

    Returns:
        Normalized ``<stem>.py`` module name when explicitly mandated, else ``None``.
    """
    # Detect explicit caller-mandated module filenames (such as `target module step_1.py`) inside the user's prompt.
    txt = str(raw_text or "")
    if not txt.strip():
        return None
    excluded = {
        "code_patch.py",
        "prepare_visual_sources.py",
        "pipeline_guards.py",
        "post_archive_embed.py",
        "setup.py",
        "conftest.py",
    }
    patterns = (
        r"\btarget[_\s-]+module\s*[:=]?\s*[`'\"]?([A-Za-z_][A-Za-z0-9_]*\.py)[`'\"]?",
        r"\b(?:implement|write|save|create|author|in|into|module|file)\s+(?:target\s+module\s+)?[`'\"]([A-Za-z_][A-Za-z0-9_]*\.py)[`'\"]",
    )
    for pat in patterns:
        for m in re.finditer(pat, txt, flags=re.IGNORECASE):
            cand = m.group(1).strip()
            if (
                cand
                and cand not in excluded
                and not cand.startswith(("verify_", "test_", "smoke_", "audit_"))
                and not cand.endswith("_test.py")
            ):
                return cand
    return None


# ============================================================================
# Section 2: Lexical Scope (`AST-2`), Rational Pole (`AST-3`) & Method L3 Hunk Analyzers
# Performs static Python scope analysis, loop-pole truncation checks, and semantic hunk partitioning without executing code.
# ============================================================================

def _collect_target_store_names(target_node: ast.AST) -> set[str]:
    """Extract all bound identifier names from an assignment/comprehension target AST node.

    Args:
        target_node: Python ``ast.AST`` assignment target subtree.

    Returns:
        Set of bound variable identifier strings.
    """
    # Collect all variable names bound on the left-hand side of an assignment, tuple unpack, or comprehension target.
    return {
        sub.id
        for sub in ast.walk(target_node)
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store)
    }


def find_lexical_unbound_names(py_path: Path) -> list[str]:
    """Perform lexical-scope AST analysis (`AST-2`) to catch unbound symbols across functions and closures.

    Walks module-level bindings, imports, builtins, function signatures, PEP 695
    type parameters, walrus ``:=`` bindings, comprehensions, ``try/except``
    handlers, and nested closures without executing user code.

    Args:
        py_path: Path to the Python module or verification script on disk.

    Returns:
        List of ``Line N: Unbound symbol '<name>'...`` error strings (empty when clean).
    """
    try:
        src = Path(py_path).read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(py_path))
    except Exception:
        return []

    # Walk every syntax node `node` in `ast.walk(tree)` to inspect module imports.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return []

    base_builtins = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__", "__all__", "TYPE_CHECKING"}
    errors: list[str] = []
    seen: set[tuple[int, str]] = set()
    scope_boundaries = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    type_alias_cls = getattr(ast, "TypeAlias", None)

    def _collect_block_locals(stmts: list[ast.stmt]) -> set[str]:
        loc: set[str] = set()

        def _scan_local_node(nd: ast.AST, is_direct_stmt: bool = False) -> None:
            if isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                loc.add(nd.name)
                return
            if not is_direct_stmt and isinstance(nd, scope_boundaries):
                return
            if isinstance(nd, ast.Import):
                for al in nd.names:
                    loc.add(al.asname or al.name.split(".")[0])
            elif isinstance(nd, ast.ImportFrom):
                for al in nd.names:
                    if al.name != "*":
                        loc.add(al.asname or al.name)
            elif isinstance(nd, (ast.Global, ast.Nonlocal)):
                loc.update(nd.names)
            elif isinstance(nd, ast.ExceptHandler) and nd.name:
                loc.add(nd.name)
            elif isinstance(nd, ast.Name) and isinstance(nd.ctx, ast.Store):
                loc.add(nd.id)
            elif type_alias_cls is not None and isinstance(nd, type_alias_cls):
                t_name = getattr(nd, "name", None)
                if isinstance(t_name, ast.Name):
                    loc.add(t_name.id)
                return
            for ch in ast.iter_child_nodes(nd):
                _scan_local_node(ch, False)

        for st in stmts:
            _scan_local_node(st, True)
        return loc

    module_bindings = _collect_block_locals(tree.body)

    def _walk_expr_or_stmt(node: ast.AST, visible: set[str], in_annotation: bool = False) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                _walk_expr_or_stmt(dec, visible, in_annotation)
            for d_val in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
                _walk_expr_or_stmt(d_val, visible, in_annotation)
            fn_tp = {getattr(tp, "name", "") for tp in getattr(node, "type_params", []) if getattr(tp, "name", "")}
            fn_scope = set(visible) | fn_tp | {node.name}
            for a in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs):
                fn_scope.add(a.arg)
            if node.args.vararg:
                fn_scope.add(node.args.vararg.arg)
            if node.args.kwarg:
                fn_scope.add(node.args.kwarg.arg)
            fn_scope |= _collect_block_locals(node.body)
            for st in node.body:
                _walk_expr_or_stmt(st, fn_scope, False)
            return

        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                _walk_expr_or_stmt(dec, visible, in_annotation)
            for b_expr in node.bases:
                _walk_expr_or_stmt(b_expr, visible, in_annotation)
            cls_tp = {getattr(tp, "name", "") for tp in getattr(node, "type_params", []) if getattr(tp, "name", "")}
            cls_locals = _collect_block_locals(node.body)
            cls_body_vis = set(visible) | cls_tp | cls_locals
            for st in node.body:
                if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Class attributes are NOT lexically in method scope unless accessed on self/cls
                    _walk_expr_or_stmt(st, set(visible) | cls_tp | {node.name}, False)
                else:
                    _walk_expr_or_stmt(st, cls_body_vis, False)
            return

        if isinstance(node, ast.Lambda):
            for d_val in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
                _walk_expr_or_stmt(d_val, visible, in_annotation)
            lam_scope = set(visible)
            for a in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs):
                lam_scope.add(a.arg)
            if node.args.vararg:
                lam_scope.add(node.args.vararg.arg)
            if node.args.kwarg:
                lam_scope.add(node.args.kwarg.arg)
            _walk_expr_or_stmt(node.body, lam_scope, in_annotation)
            return

        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            comp_scope = set(visible)
            for idx_g, gen in enumerate(node.generators):
                _walk_expr_or_stmt(gen.iter, visible if idx_g == 0 else comp_scope, in_annotation)
                comp_scope |= _collect_target_store_names(gen.target)
                for if_c in gen.ifs:
                    _walk_expr_or_stmt(if_c, comp_scope, in_annotation)
            if isinstance(node, ast.DictComp):
                _walk_expr_or_stmt(node.key, comp_scope, in_annotation)
                _walk_expr_or_stmt(node.value, comp_scope, in_annotation)
            else:
                _walk_expr_or_stmt(node.elt, comp_scope, in_annotation)
            return

        if isinstance(node, ast.AnnAssign):
            # Deferred PEP 563 string/type annotations skip runtime unbound checks
            if node.value is not None:
                _walk_expr_or_stmt(node.value, visible, False)
            return

        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and not in_annotation:
            if node.id not in visible and (node.lineno, node.id) not in seen:
                seen.add((node.lineno, node.id))
                errors.append(
                    f"Line {node.lineno}: Unbound symbol '{node.id}' referenced before assignment or missing import."
                )
            return

        for child in ast.iter_child_nodes(node):
            _walk_expr_or_stmt(child, visible, in_annotation)

    root_vis = base_builtins | module_bindings
    for top_s in tree.body:
        _walk_expr_or_stmt(top_s, root_vis, False)
    return errors


def detect_rational_pole_sign_clip(py_source: str) -> list[str]:
    """Detect domain-truncating one-sided pole bailouts (`AST-3`) inside iterative loops.

    Flags ``if d <= 0: break/return`` (or ``d < 0`` / ``0 >= d`` / ``0 > d``
    without ``raise``) inside a ``for``/``while`` loop when ``d`` appears as a
    rational division denominator ``... / d`` in the same function and ``d`` is
    not passed to a real branch-cut function (``sqrt``, ``log``, ``log2``,
    ``log10``, ``cholesky``, ``arccos``, ``arcsin``, ``arctanh``).

    Args:
        py_source: Candidate Python module source text.

    Returns:
        List of ``AST-3`` violation strings (empty when compliant).
    """
    # Enforce `AST-3`: flag loops that silently `break`/`return` on `if denom <= 0` when `denom` is a `/` divisor not inside a `sqrt`/`log` branch cut.
    try:
        tree = ast.parse(py_source)
    except Exception:
        return []

    branch_cut_calls = {"sqrt", "log", "log1p", "log2", "log10", "cholesky", "arccos", "arcsin", "atanh", "arctanh"}
    violations: list[str] = []

    for fn_node in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        denom_vars: set[str] = set()
        branch_vars: set[str] = set()
        for sub in ast.walk(fn_node):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Div):
                if isinstance(sub.right, ast.Name):
                    denom_vars.add(sub.right.id)
            elif isinstance(sub, ast.Call):
                cname = (
                    sub.func.id
                    if isinstance(sub.func, ast.Name)
                    else (sub.func.attr if isinstance(sub.func, ast.Attribute) else "")
                )
                if cname in branch_cut_calls:
                    for arg in sub.args:
                        for nm in [x.id for x in ast.walk(arg) if isinstance(x, ast.Name)]:
                            branch_vars.add(nm)

        if not denom_vars:
            continue

        for loop_node in [x for x in ast.walk(fn_node) if isinstance(x, (ast.For, ast.AsyncFor, ast.While))]:
            for if_node in [x for x in ast.walk(loop_node) if isinstance(x, ast.If)]:
                t_cmp = if_node.test
                cand_var: str | None = None
                if isinstance(t_cmp, ast.Compare) and len(t_cmp.ops) == 1 and len(t_cmp.comparators) == 1:
                    op0 = t_cmp.ops[0]
                    rhs0 = t_cmp.comparators[0]
                    if (
                        isinstance(op0, (ast.LtE, ast.Lt))
                        and isinstance(t_cmp.left, ast.Name)
                        and isinstance(rhs0, ast.Constant)
                        and rhs0.value in (0, 0.0)
                    ):
                        cand_var = t_cmp.left.id
                    elif (
                        isinstance(op0, (ast.GtE, ast.Gt))
                        and isinstance(t_cmp.left, ast.Constant)
                        and t_cmp.left.value in (0, 0.0)
                        and isinstance(rhs0, ast.Name)
                    ):
                        cand_var = rhs0.id
                if (
                    cand_var
                    and cand_var in denom_vars
                    and cand_var not in branch_vars
                    and any(isinstance(st, (ast.Break, ast.Return)) for st in if_node.body)
                    and not any(isinstance(st, ast.Raise) for st in if_node.body)
                ):
                    violations.append(
                        f"Line {if_node.lineno} in '{fn_node.name}': Domain-truncating one-sided rational denominator exit "
                        f"('if {cand_var} <= 0: break/return') truncates valid negative-curvature/negative-slope inputs. "
                        f"Guard near-zero division poles symmetrically via 'if abs({cand_var}) <= 1e-15:' (unless raising on an explicit caller precondition)."
                    )
    return violations


def _extract_file_semantic_hunks(file_text: str, filename: str) -> list[dict[str, object]]:
    r"""Partition a Python or JSON artifact into 1-based semantic line hunks for Method L3 pooling.

    Args:
        file_text: Raw text content of the file.
        filename: Base filename (``.py`` or ``.json``).

    Returns:
        List of hunk dictionaries ``{"id": str, "start": int, "end": int}``.
    """
    # Split a `.py` file into top-level AST function/class hunks (including header/tail gaps) or a `.json` file into keyed blocks for Method L3 pooling.
    lines = file_text.splitlines()
    n_lines = max(1, len(lines))
    hunks: list[dict[str, object]] = []
    if filename.endswith(".py"):
        try:
            tree = ast.parse(file_text)
            prev_end = 0
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    st = int(node.lineno)
                    en = int(getattr(node, "end_lineno", st) or st)
                    if st > prev_end + 1:
                        gap_s = prev_end + 1
                        gap_e = st - 1
                        lbl = "module_header" if prev_end == 0 else "module_scope"
                        hunks.append({"id": f"{lbl}[L{gap_s}..L{gap_e}]", "start": gap_s, "end": gap_e})
                    hunks.append({"id": f"{node.name}[L{st}..L{en}]", "start": st, "end": en})
                    prev_end = max(prev_end, en)
            if hunks and prev_end < n_lines:
                hunks.append({"id": f"module_tail[L{prev_end + 1}..L{n_lines}]", "start": prev_end + 1, "end": n_lines})
        except Exception:
            pass
    elif filename.endswith(".json"):
        top_key_pat = re.compile(r'^\s{2}"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
        id_pat = re.compile(r'"(?:obligation_id|spec_id|id)"\s*:\s*"([^"]+)"')
        anchors: list[tuple[int, str]] = []
        for idx, ln in enumerate(lines, 1):
            tk_m = top_key_pat.match(ln)
            if tk_m:
                anchors.append((idx, tk_m.group(1)))
                continue
            id_m = id_pat.search(ln)
            if id_m:
                anchors.append((max(1, idx - 1), id_m.group(1)))
        if anchors:
            if anchors[0][0] > 1:
                anchors.insert(0, (1, "root_header"))
            dedup_anchors: list[tuple[int, str]] = []
            for st_ln, hid in anchors:
                if dedup_anchors and st_ln <= dedup_anchors[-1][0]:
                    dedup_anchors[-1] = (dedup_anchors[-1][0], hid)
                else:
                    dedup_anchors.append((st_ln, hid))
            for pos_i, (st_ln, hid) in enumerate(dedup_anchors):
                en_ln = (dedup_anchors[pos_i + 1][0] - 1) if pos_i + 1 < len(dedup_anchors) else n_lines
                hunks.append({"id": f"{hid}[L{st_ln}..L{max(st_ln, en_ln)}]", "start": st_ln, "end": max(st_ln, en_ln)})
    if not hunks:
        step = 25
        for st in range(1, n_lines + 1, step):
            en = min(n_lines, st + step - 1)
            hunks.append({"id": f"block[L{st}..L{en}]", "start": st, "end": en})
    return hunks


def compute_method_l3_localized_hunks(
    file_text: str,
    filename: str,
    line_probs: dict[str, float],
    in_file_prob: float = 1.0,
) -> list[dict[str, object]]:
    """Compute Method L3 edit-scope hunks by pooling line ``Choice`` probabilities over AST/JSON hunks.

    Gates localization on TypeSafe's Companion ``Noul`` ``in_file_prob >= 0.70``
    so clean files never emit forced-choice false-positive edit hints, then
    ranks semantic hunks by pooled probability mass ``sum_{i in H} P(L_i)``.

    Args:
        file_text: Full source text of the candidate target file.
        filename: Base filename (e.g. ``step_1.py`` or ``mapping.json``).
        line_probs: Probability distribution over ``L001..L255`` from ``/v1/systemone``.
        in_file_prob: Companion ``Noul`` probability that the defect resides in ``filename``.

    Returns:
        Up to 2 ranked hunk dicts ``{"id": str, "start": int, "end": int, "mass": float}``.
    """
    # Gate on Companion `Noul` `in_file_prob >= 0.70`, sum line probabilities `P(L_i)` inside each AST/JSON hunk, and select up to 2 high-mass edit targets.
    if float(in_file_prob) < 0.70 or not file_text.strip() or not isinstance(line_probs, dict) or not line_probs:
        return []
    hunks = _extract_file_semantic_hunks(file_text, filename)
    scored: list[dict[str, object]] = []
    for h in hunks:
        st_i = int(h["start"])  # type: ignore[arg-type]
        en_i = int(h["end"])  # type: ignore[arg-type]
        mass = sum(float(line_probs.get(f"L{ln:03d}", 0.0) or 0.0) for ln in range(st_i, en_i + 1))
        scored.append({"id": str(h["id"]), "start": st_i, "end": en_i, "mass": round(mass, 4)})
    scored.sort(key=lambda item: float(item["mass"]), reverse=True)  # type: ignore[arg-type]
    if not scored or float(scored[0]["mass"]) < 0.35:  # type: ignore[arg-type]
        return []
    selected = [scored[0]]
    if len(scored) > 1 and float(scored[1]["mass"]) >= 0.12:  # type: ignore[arg-type]
        selected.append(scored[1])
    return selected


def render_edit_scope_hint(cwd: Path, target_filename: str, fsm: dict[str, object]) -> str:
    r"""Render an advisory ``<edit_scope_hint>`` block with verbatim hunk lines for scoped repair.

    Uses ``pending_rewind["localized_hunks"]`` when populated by System-1 Method L3
    or falls back to deterministic symbol/ID matching from ``AUDIT_FEEDBACK.json``
    or ``LOCAL_CRITIC.json``.

    Args:
        cwd: Workspace root directory.
        target_filename: Target file name (``manifest.json``, ``mapping.json``, or ``<module>.py``).
        fsm: Active pipeline FSM dictionary.

    Returns:
        Formatted ``<edit_scope_hint>...</edit_scope_hint>`` block, or ``""`` when unavailable.
    """
    # Format an advisory `<edit_scope_hint>` block showing verbatim lines around localized target hunks so repairs stay surgical.
    target_p = Path(cwd) / target_filename
    if not target_p.is_file():
        return ""
    try:
        src_text = target_p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    lines = src_text.splitlines()
    if not lines:
        return ""

    prw = fsm.get("pending_rewind") if isinstance(fsm.get("pending_rewind"), dict) else {}
    hunks: list[dict[str, object]] = list(prw.get("localized_hunks") or []) if isinstance(prw, dict) else []

    if not hunks:
        cand_hunks = _extract_file_semantic_hunks(src_text, target_filename)
        diag_blob = ""
        for fn in ("LOCAL_CRITIC.json", "AUDIT_FEEDBACK.json"):
            fp = Path(cwd) / fn
            if fp.is_file():
                diag_blob += "\n" + fp.read_text(encoding="utf-8", errors="replace")[:2000]
        for ch in cand_hunks:
            hid_prefix = str(ch["id"]).split("[", 1)[0]
            if hid_prefix and hid_prefix != "block" and hid_prefix in diag_blob:
                hunks.append({"id": ch["id"], "start": ch["start"], "end": ch["end"], "mass": 1.0})
                break

    if not hunks:
        return ""

    lang = "python" if target_filename.endswith(".py") else "json"
    out_lines = [
        "<edit_scope_hint>",
        f"- Advisory Scoped-Edit Target(s) in '{target_p.resolve()}' (apply minimal 'edit' or 'code_patch.py' on the relevant lines below; call 'view' if broader context is needed):",
    ]
    for h in hunks[:2]:
        st_l = max(1, int(h.get("start", 1)) - 1)  # type: ignore[arg-type]
        en_l = min(len(lines), int(h.get("end", len(lines))) + 1)  # type: ignore[arg-type]
        mass_v = h.get("mass")
        mass_tag = f", mass={float(mass_v):.2f}" if isinstance(mass_v, (int, float)) else ""
        excerpt = "\n".join(lines[st_l - 1 : en_l])[:1800]
        out_lines.append(f"  * Hunk `{h.get('id')}` (Lines {st_l}..{en_l}{mass_tag}):")
        out_lines.append(f"```{lang}\n{excerpt}\n```")
    out_lines.append("</edit_scope_hint>")
    return "\n".join(out_lines)


def _format_python_fragment_with_ruff(snippet: str) -> str | None:
    r"""Format a Python snippet via ``ruff format`` to recover lossless ``postToolUse`` canonicalization.

    Handles both complete Python fragments and ``@decorator`` / ``def`` / ``class``
    header prefixes whose bodies were omitted in ``old_str`` by appending a
    reversible sentinel statement before invoking ``ruff format``.

    Args:
        snippet: Candidate ``old_str`` snippet from an ``edit`` tool call.

    Returns:
        Formatted snippet preserving original outer indentation, or ``None`` if
        ``ruff`` is unavailable or the snippet cannot be formatted as Python.
    """
    if not snippet or not snippet.strip():
        return None
    ruff_bin = next(
        (
            str(cand_r)
            for cand_r in (
                Path.cwd() / ".venv" / "bin" / "ruff",
                Path(sys.executable).resolve().parent / "ruff",
                HOOKS_REPO_ROOT / ".venv" / "bin" / "ruff",
            )
            if cand_r.is_file()
        ),
        shutil.which("ruff"),
    )
    if not ruff_bin:
        return None
    normalized = _promote_latex_docstrings_to_raw(snippet.replace("\r\n", "\n"))
    nonempty = [ln for ln in normalized.splitlines() if ln.strip()]
    if not nonempty:
        return None
    min_lead = min(len(ln) - len(ln.lstrip(" ")) for ln in nonempty)
    base_indent = " " * min_lead
    dedented = textwrap.dedent(normalized)
    sentinel_line = "_facet_ruff_format_sentinel_ = 0"
    added_sentinel = False
    feed_src = dedented
    try:
        ast.parse(dedented)
    except SyntaxError:
        if not re.search(r"^\s*(?:@\S.*|(?:async\s+)?def\s+\w+|class\s+\w+)", dedented, re.MULTILINE):
            return None
        candidate_src = dedented.rstrip("\n") + f"\n    {sentinel_line}\n"
        try:
            ast.parse(candidate_src)
            feed_src = candidate_src
            added_sentinel = True
        except SyntaxError:
            return None
    proc = subprocess.run(
        [ruff_bin, "format", "--quiet", "--stdin-filename", "_facet_edit_fragment.py", "-"],
        input=feed_src,
        capture_output=True,
        text=True,
        timeout=3,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    formatted = proc.stdout
    if added_sentinel:
        fmt_lines = formatted.rstrip("\n").splitlines()
        while fmt_lines and not fmt_lines[-1].strip():
            fmt_lines.pop()
        if not fmt_lines or fmt_lines[-1].strip() != sentinel_line:
            return None
        fmt_lines.pop()
        while fmt_lines and not fmt_lines[-1].strip():
            fmt_lines.pop()
        formatted = "\n".join(fmt_lines) + "\n"
    if base_indent:
        formatted = textwrap.indent(formatted, base_indent)
    return formatted


def extract_on_disk_symbol_excerpt(
    file_path: Path | str,
    old_str: str,
    max_lines: int = 100,
) -> tuple[str, int, int, str] | None:
    r"""Extract the exact current on-disk lines of a uniquely named symbol targeted by a failed ``edit``.

    Args:
        file_path: Path to the Python module on disk.
        old_str: Failed ``old_str`` passed to ``edit``.
        max_lines: Maximum excerpt line count to emit.

    Returns:
        Tuple ``(symbol_name, start_line_1based, end_line_1based, excerpt_text)``,
        or ``None`` when no unique symbol definition matches.
    """
    p = Path(file_path)
    if p.suffix.lower() != ".py" or not p.is_file() or not old_str:
        return None
    try:
        disk_text = p.read_text(encoding="utf-8")
        tree = ast.parse(disk_text)
    except (OSError, SyntaxError):
        return None
    disk_lines = disk_text.splitlines()
    def_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    sym_match = re.search(
        r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
        old_str,
        re.MULTILINE,
    )
    node = None
    sym_name = ""
    if sym_match:
        sym_name = sym_match.group(1)
        top_hits = [nd for nd in tree.body if isinstance(nd, def_types) and nd.name == sym_name]
        matches = top_hits if len(top_hits) == 1 else [
            nd for nd in ast.walk(tree) if isinstance(nd, def_types) and nd.name == sym_name
        ]
        if len(matches) == 1:
            node = matches[0]
    if node is None:
        # Enclosing-AST body-interior fallback when old_str edits inside a function/class body without a def/class header.
        old_clean_lines = {
            ln.strip()
            for ln in old_str.splitlines()
            if ln.strip() and not ln.strip().startswith("#") and len(ln.strip()) >= 8
        }
        common_py_tokens = {
            "def", "return", "for", "range", "len", "int", "float", "np", "asarray",
            "zeros", "ones", "float64", "complex128", "int64", "else", "elif", "True",
            "False", "None", "ValueError",
        }
        old_idents = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", old_str)) - common_py_tokens
        scored: list[tuple[int, int, ast.AST]] = []
        for cand_nd in tree.body:
            if not isinstance(cand_nd, def_types):
                continue
            decs_c = getattr(cand_nd, "decorator_list", []) or []
            s_c = min([cand_nd.lineno] + [d.lineno for d in decs_c])
            e_c = int(getattr(cand_nd, "end_lineno", cand_nd.lineno))
            body_slice = "\n".join(disk_lines[s_c - 1 : e_c])
            cand_lines = {
                ln.strip()
                for ln in disk_lines[s_c - 1 : e_c]
                if ln.strip() and not ln.strip().startswith("#") and len(ln.strip()) >= 8
            }
            line_hits = len(old_clean_lines & cand_lines)
            ident_hits = len(old_idents & set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", body_slice)))
            if line_hits >= 1 or ident_hits >= 3:
                scored.append((line_hits, ident_hits, cand_nd))
        if scored:
            scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
            best = scored[0]
            runner_up = scored[1] if len(scored) > 1 else (-1, -1, None)
            if (best[0], best[1]) > (runner_up[0], runner_up[1]) and (
                best[0] >= 1 or best[1] >= max(3, runner_up[1] + 2)
            ):
                node = best[2]
                sym_name = str(getattr(node, "name", ""))
    if node is None:
        return None
    decs = getattr(node, "decorator_list", []) or []
    start_ln = min([node.lineno] + [d.lineno for d in decs])
    end_ln = int(getattr(node, "end_lineno", node.lineno))
    capped_end = min(end_ln, start_ln + max(1, max_lines) - 1, len(disk_lines))
    excerpt = "\n".join(disk_lines[start_ln - 1 : capped_end])
    if capped_end < end_ln:
        excerpt += f"\n# ... ({end_ln - capped_end} additional lines up to L{end_ln})"
    return (sym_name, start_ln, end_ln, excerpt)


def extract_ast_fault_cone_slice(
    file_path: Path | str,
    audit_feedback: dict[str, object] | None = None,
    mapping_doc: dict[str, object] | None = None,
    max_cone_lines: int = 220,
) -> str | None:
    r"""Extract an ``O(defect)`` AST Fault-Cone slice for warm repair on modules of any length.

    Includes module imports/constants, verbatim on-disk lines ``[Lstart..Lend]``
    of failing entrypoint(s) named in ``audit_feedback`` plus their transitive
    intra-module callees, and 1-line ``# [UNCHANGED OUTSIDE FAULT CONE]`` stubs
    for all untouched definitions.

    Args:
        file_path: Path to the existing Python module on disk.
        audit_feedback: Parsed ``AUDIT_FEEDBACK.json`` dictionary when available.
        mapping_doc: Parsed ``mapping.json`` dictionary when available.
        max_cone_lines: Soft cap on inline fault-cone lines.

    Returns:
        XML ``<hydrated_fault_cone ...>`` string, or ``None`` if unavailable.
    """
    p = Path(file_path)
    if p.suffix.lower() != ".py" or not p.is_file():
        return None
    try:
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return None
    lines = src.splitlines()
    def_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    top_defs = [nd for nd in tree.body if isinstance(nd, def_types)]
    if not top_defs:
        return None

    def _span(nd: ast.AST) -> tuple[int, int]:
        decs = getattr(nd, "decorator_list", []) or []
        s_l = min([int(getattr(nd, "lineno", 1))] + [int(getattr(d, "lineno", 1)) for d in decs])
        e_l = int(getattr(nd, "end_lineno", s_l))
        return (s_l, e_l)

    top_by_name = {nd.name: nd for nd in top_defs}
    callees_by_name: dict[str, set[str]] = {}
    for nd in top_defs:
        used: set[str] = set()
        for sub in ast.walk(nd):
            if isinstance(sub, ast.Name) and sub.id in top_by_name and sub.id != nd.name:
                used.add(sub.id)
        callees_by_name[nd.name] = used

    fb_blob = json.dumps(audit_feedback or {}, ensure_ascii=False)
    failed_specs: set[str] = set()
    if isinstance(audit_feedback, dict):
        for f_it in audit_feedback.get("failures") or []:  # type: ignore[union-attr]
            if isinstance(f_it, dict) and f_it.get("spec_id"):
                failed_specs.add(str(f_it["spec_id"]).strip())

    seed_names: set[str] = set()
    for nm in top_by_name:
        if re.search(rf"\b{re.escape(nm)}\b", fb_blob):
            seed_names.add(nm)
    if failed_specs and isinstance(mapping_doc, dict):
        for om in mapping_doc.get("operation_mappings") or []:  # type: ignore[union-attr]
            if isinstance(om, dict) and str(om.get("spec_id") or "").strip() in failed_specs:
                cc = str(om.get("code_construct") or om.get("target_function") or "")
                for nm in top_by_name:
                    if re.search(rf"\b{re.escape(nm)}\b", cc):
                        seed_names.add(nm)
    if not seed_names:
        seed_names = {nm for nm in top_by_name if not nm.startswith("_")} or set(top_by_name.keys())

    cone: set[str] = set(seed_names)
    frontier = list(seed_names)
    while frontier:
        cur_nm = frontier.pop()
        for cal in callees_by_name.get(cur_nm, set()):
            if cal not in cone:
                cone.add(cal)
                frontier.append(cal)

    first_def_ln = min(_span(nd)[0] for nd in top_defs)
    header_end = min(first_def_ln - 1, 45)
    header_txt = "\n".join(lines[:header_end]).rstrip()

    out_parts: list[str] = [
        f'<hydrated_fault_cone path="{p.name}" total_lines="{len(lines)}" cone_symbols="{",".join(sorted(cone))}">',
        f"# [MODULE HEADER L1..L{max(1, header_end)}] (lines inside FAULT CONE TARGET blocks are verbatim on-disk lines — do NOT call whole-file 'view')",
    ]
    if header_txt:
        out_parts.append(header_txt)
    budget_used = 0
    for nd in top_defs:
        s_l, e_l = _span(nd)
        span_len = e_l - s_l + 1
        if nd.name in cone and (budget_used == 0 or budget_used + span_len <= max_cone_lines):
            out_parts.append(f"\n# --- [FAULT CONE TARGET: {nd.name} | Exact Disk Lines L{s_l}..L{e_l}] ---")
            out_parts.append("\n".join(lines[s_l - 1 : e_l]))
            budget_used += span_len
        elif nd.name in cone:
            sig_ln = lines[nd.lineno - 1].strip()
            out_parts.append(
                f"\n{sig_ln} ...  # [FAULT CONE TARGET L{s_l}..L{e_l} ({span_len}L > inline cap) — inspect via view(path=\"{p}\", view_range=[{s_l}, {e_l}])]"
            )
        else:
            sig_ln = lines[nd.lineno - 1].strip()
            out_parts.append(f"\n{sig_ln} ...  # [UNCHANGED OUTSIDE FAULT CONE: L{s_l}..L{e_l}]")
    out_parts.append("</hydrated_fault_cone>")
    return "\n".join(out_parts)


# ============================================================================
# Section 3: Markdown Sanitization & RFC 8259 Gemma JSON Escape Canonicalizer
# Scrubs control tokens/emojis from Markdown and repairs unescaped LaTeX backslashes inside Gemma tool-call JSON payloads.
# ============================================================================

def sanitize_markdown_artifact_text(raw_text, normalize_ligatures=False):
    r"""Scrub leaked Gemma 4 channel tokens, emojis, and control bytes.

    Converts checkmark/cross badges (U+2705 / U+274C) to plain-ASCII status tokens
    and strips decorative emoji/dingbat codepoints so archived Markdown reports
    preserve Facet's zero-emoji invariant.

    Args:
        raw_text: Raw Markdown text read from disk.
        normalize_ligatures: When ``True``, replace Unicode typographic
            ligatures and minus signs with ASCII equivalents.

    Returns:
        Sanitized Markdown text string.
    """
    cleaned = re.sub(r"<\|?channel\|?>", "", raw_text)
    cleaned = re.sub(r"<\|channel>[a-z_]+", "", cleaned)
    cleaned = re.sub(r"<\|?turn\|?>", "", cleaned)
    cleaned = re.sub(r"<\|turn>[a-z_]+", "", cleaned)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", cleaned)
    # Normalize common status checkmarks before stripping remaining pictographs.
    cleaned = re.sub(r"[\u2705\u2713\u2714]\ufe0f?\s*(?=PASS\b|PASSED\b|SUCCESS\b)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[\u274c\u2717\u2718]\ufe0f?\s*(?=FAIL\b|FAILED\b)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[\u2705\u2713\u2714]\ufe0f?", "[PASS]", cleaned)
    cleaned = re.sub(r"[\u274c\u2717\u2718]\ufe0f?", "[FAIL]", cleaned)
    cleaned = _EMOJI_STRIP_RE.sub("", cleaned)
    if normalize_ligatures:
        for src_ch, dst_str in _TYPOGRAPHIC_LIGATURE_MAP.items():
            cleaned = cleaned.replace(src_ch, dst_str)
    return cleaned


def _strip_markdown_code_spans(text):
    r"""Remove fenced and inline Markdown code spans before XML scanning.

    Prevents example ``<uncertain>`` or ``<illegible/>`` documentation strings
    from being treated as active TEI P5 provenance tags.

    Args:
        text: Markdown source text.

    Returns:
        Markdown text with fenced code blocks and inline backticks removed.
    """
    # Strip triple-backtick blocks and inline backticks before scanning `SOURCE_OCR.md` so literal example tags aren't mistaken for real OCR tags.
    without_fences = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return re.sub(r"`[^`\n]*`", "", without_fences)


def _repair_gemma_json_string_escapes(raw_text):
    r"""Escape single-backslash LaTeX macros and raw control chars in JSON strings.

    Normalizes Gemma 4 ``<|"|>...<|"|>`` string payloads into strict RFC 8259
    JSON while distinguishing LaTeX macros (e.g. ``\\textbf``, ``\\nabla``)
    from valid JSON escapes (``\\t``, ``\\n``).

    Args:
        raw_text: Raw JSON payload string.

    Returns:
        Escape-repaired JSON string.
    """
    # Walk JSON character-by-character inside string quotes to double single-backslash LaTeX commands (`\frac`, `\times`, `\nabla`) without breaking `\n` or `\uXXXX`.
    out = []
    in_str = False
    i = 0
    n = len(raw_text)
    # Repeat step-by-step while `i < n`.
    while i < n:
        ch = raw_text[i]
        if not in_str:
            if ch == '"':
                in_str = True
            out.append(ch)
            i += 1
            continue

        if ch == '"':
            in_str = False
            out.append(ch)
            i += 1
            continue

        if ch == "\\":
            if i + 1 >= n:
                out.append("\\\\")
                i += 1
                continue
            nxt = raw_text[i + 1]
            if nxt in ('"', "\\", "/"):
                out.append("\\" + nxt)
                i += 2
                continue
            if nxt == "u" and i + 5 < n and all(c in "0123456789abcdefABCDEF" for c in raw_text[i + 2 : i + 6]):
                out.append(raw_text[i : i + 6])
                i += 6
                continue
            if nxt in ("f", "b"):
                out.append("\\\\" + nxt)
                i += 2
                continue
            if nxt == "r":
                if i + 2 < n and raw_text[i + 2].isalpha():
                    out.append("\\\\r")
                else:
                    out.append("\\r")
                i += 2
                continue
            if nxt == "t":
                tail = raw_text[i + 2 : i + 18]
                is_latex_t = any(tail.startswith(p) for p in _LATEX_T_PREFIX_STEMS) or any(
                    tail.startswith(sfx) and (len(tail) == len(sfx) or not tail[len(sfx)].isalpha())
                    for sfx in _LATEX_T_EXACT_STEMS
                )
                out.append("\\\\t" if is_latex_t else "\\t")
                i += 2
                continue
            if nxt == "n":
                tail = raw_text[i + 2 : i + 18]
                is_latex_n = any(tail.startswith(p) for p in _LATEX_N_PREFIX_STEMS) or any(
                    tail.startswith(sfx) and (len(tail) == len(sfx) or not tail[len(sfx)].isalpha())
                    for sfx in _LATEX_N_EXACT_STEMS
                )
                out.append("\\\\n" if is_latex_n else "\\n")
                i += 2
                continue
            out.append("\\\\" + nxt)
            i += 2
            continue

        code = ord(ch)
        if code < 0x20:
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ch == "\x0c":
                out.append("\\\\f")
            elif ch == "\x08":
                out.append("\\\\b")
            else:
                out.append(f"\\u{code:04x}")
            i += 1
            continue

        out.append(ch)
        i += 1
    return "".join(out)


def _find_residual_control_bytes(obj, path="$"):
    r"""Walk parsed JSON values to detect forbidden C0 control bytes.

    Args:
        obj: Parsed JSON structure.
        path: Current JSONPath prefix for diagnostic reporting.

    Returns:
        List of diagnostic strings describing any control bytes in strings.
    """
    # Recursively scan decoded JSON strings to catch any surviving ASCII control characters (`0x00..0x1f`, such as `0x0c` from an unescaped `\frac`).
    issues = []
    if isinstance(obj, str):
        m = re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", obj)
        if m:
            issues.append(f"Control byte 0x{ord(m.group(0)):02x} detected inside string at {path}.")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            issues.extend(_find_residual_control_bytes(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            issues.extend(_find_residual_control_bytes(item, f"{path}[{idx}]"))
    return issues


def _reject_non_rfc8259_constant(const_token):
    r"""Reject non-RFC-8259 ``NaN`` and ``Infinity`` numeric constants during JSON decoding.

    Args:
        const_token: Matched bare constant token (``"NaN"``, ``"Infinity"``, or ``"-Infinity"``).

    Raises:
        ValueError: Always raised to enforce strict RFC 8259 compliance.
    """
    # Raise `ValueError` if JSON contains bare `NaN` or `Infinity`, which violate strict RFC 8259 interoperability.
    raise ValueError(f"Non-RFC-8259 numeric constant '{const_token}' (NaN/Infinity) is prohibited in strict JSON.")


def format_gemma_json_artifact(raw_text):
    r"""Canonicalize Gemma 4 JSON tool payloads into strict RFC 8259 JSON.

    Strips Markdown code fences, repairs single-escaped LaTeX backslashes,
    rejects ``NaN``/``Infinity`` constants and residual control bytes, and
    pretty-prints the parsed object.

    Args:
        raw_text: Raw JSON file content read from disk.

    Returns:
        Tuple ``(formatted_json_str, parsed_obj, error_str)``.
    """
    # Strip optional Markdown code fences, repair LaTeX string escapes, parse strict RFC 8259 JSON, and re-serialize with clean 2-space indentation.
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped).strip()

    repaired = _repair_gemma_json_string_escapes(stripped)
    decoder = json.JSONDecoder(parse_constant=_reject_non_rfc8259_constant)
    # Execute guarded I/O or parsing and recover cleanly on error.
    try:
        parsed, end_idx = decoder.raw_decode(repaired.lstrip())
        leading_ws = len(repaired) - len(repaired.lstrip())
        trailing = repaired[leading_ws + end_idx :].strip()
        if trailing and not re.fullmatch(r"[\]\}`\s]+", trailing):
            return ("", None, f"Unexpected trailing tokens after root JSON value: {trailing[:80]!r}")
    except Exception as exc:
        return ("", None, str(exc))

    ctrl_issues = _find_residual_control_bytes(parsed)
    if ctrl_issues:
        return ("", None, "; ".join(ctrl_issues))

    formatted = json.dumps(parsed, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    return (formatted, parsed, None)


def _is_finite_number(val):
    """Check whether ``val`` is a finite ``int`` or ``float`` (excluding ``bool``).

    Args:
        val: Arbitrary decoded JSON value to inspect.

    Returns:
        ``True`` if ``val`` is a non-boolean finite numeric scalar; else ``False``.
    """
    # Return `True` for finite `int`/`float` scalars while excluding `bool` (since `bool` subclasses `int` in Python).
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))


def compute_module_ast_closure_hashes(py_path: Path) -> dict[str, str]:
    """Compute transitive intra-module AST closure SHA-256 hashes per top-level callable or class.

    Builds a static symbol dependency graph across top-level functions, classes,
    and module constants in ``py_path`` and hashes the canonical AST dump of
    each callable's reachable intra-module closure so unchanged entrypoints can
    be identified deterministically during Stage 4 repair cycles.

    Args:
        py_path: Path to the Python implementation module to analyze.

    Returns:
        Mapping from top-level function or class name to its 64-character hex
        SHA-256 AST dependency-closure digest (empty when missing or unparsable).
    """
    # Hash each public function together with all intra-module helpers/constants it calls so `@Auditor` knows which functions were untouched during repairs.
    if not py_path.is_file():
        return {}
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    except Exception:
        return {}

    defs: dict[str, ast.AST] = {}
    deps: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defs[t.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defs[node.target.id] = node

    for name, node in defs.items():
        referenced: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id in defs and sub.id != name:
                referenced.add(sub.id)
        deps[name] = referenced

    own_dumps = {k: ast.dump(n, annotate_fields=True, include_attributes=False) for k, n in defs.items()}
    hashes: dict[str, str] = {}
    for name in sorted(defs.keys()):
        if isinstance(defs[name], (ast.Assign, ast.AnnAssign)):
            continue
        visited: set[str] = set()
        stack = [name]
        # Traverse reachable symbols iteratively until the worklist is empty.
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            stack.extend(deps.get(cur, set()) - visited)
        closure_blob = "\n---\n".join(f"{sym}:{own_dumps[sym]}" for sym in sorted(visited))
        hashes[name] = hashlib.sha256(closure_blob.encode("utf-8")).hexdigest()
    return hashes


def _promote_latex_docstrings_to_raw(py_source: str) -> str:
    r"""Promote non-raw Python docstrings containing LaTeX backslashes to raw literals.

    Scans module, class, and function docstring tokens via ``tokenize`` and
    prepends ``r`` when an unprefixed string literal contains LaTeX control
    sequences (such as ``\bigotimes``, ``\times``, ``\frac``, ``\nabla``,
    ``\rho``, or ``\lVert``) that Python would otherwise decode into ASCII
    control characters (``0x08``, ``0x09``, ``0x0c``) or ``SyntaxWarning``.

    Args:
        py_source: Raw Python module source text.

    Returns:
        Updated Python source with backslashed docstrings prefixed by ``r``.
    """
    if "\\" not in py_source and "\x08" not in py_source:
        return py_source
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(py_source).readline))
    except Exception:
        return py_source

    docstring_positions: list[tuple[int, int]] = []
    expect_module_doc = True
    idx = 0
    n_toks = len(tokens)
    # Repeat step-by-step while `idx < n_toks`.
    while idx < n_toks:
        tok = tokens[idx]
        ttype, tstr, tstart = tok.type, tok.string, tok.start
        if ttype in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.ENCODING):
            idx += 1
            continue
        if expect_module_doc:
            expect_module_doc = False
            if ttype == tokenize.STRING:
                docstring_positions.append((tstart[0], tstart[1]))
                idx += 1
                continue
        if ttype == tokenize.NAME and tstr in ("def", "class"):
            # Advance to suite INDENT, then check first statement token for docstring
            j = idx + 1
            paren_depth = 0
            while j < n_toks:
                tj = tokens[j]
                if tj.type == tokenize.OP and tj.string in ("(", "[", "{"):
                    paren_depth += 1
                elif tj.type == tokenize.OP and tj.string in (")", "]", "}"):
                    paren_depth = max(0, paren_depth - 1)
                elif tj.type == tokenize.OP and tj.string == ":" and paren_depth == 0:
                    j += 1
                    break
                j += 1
            while j < n_toks and tokens[j].type in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT):
                j += 1
            if j < n_toks and tokens[j].type == tokenize.STRING:
                docstring_positions.append((tokens[j].start[0], tokens[j].start[1]))
        idx += 1

    if not docstring_positions:
        return py_source

    lines = py_source.splitlines(keepends=True)
    latex_esc_re = re.compile(r"\\[A-Za-z]")
    sq3 = "'" * 3
    for line_no, col_no in sorted(set(docstring_positions), reverse=True):
        if line_no < 1 or line_no > len(lines):
            continue
        ln = lines[line_no - 1]
        prefix_slice = ln[col_no : col_no + 4]
        if not (prefix_slice.startswith('"""') or prefix_slice.startswith(sq3) or prefix_slice.startswith('"') or prefix_slice.startswith("'")):
            continue
        # Extract literal span on/after col_no to test for LaTeX backslash commands
        tail_sub = "".join(lines[line_no - 1 : min(len(lines), line_no + 40)])[col_no:]
        if latex_esc_re.search(tail_sub):
            lines[line_no - 1] = ln[:col_no] + "r" + ln[col_no:]
    return "".join(lines)


def _format_numbered_remediation_excerpt(file_path: Path, max_lines: int = 28) -> str:
    """Format a compact 1-based line-numbered excerpt for scoped-edit remediation hints.

    Args:
        file_path: Existing text/JSON/Python artifact on disk.
        max_lines: Maximum number of lines to render inline.

    Returns:
        Numbered code block excerpt, or ``""`` if the file is missing or empty.
    """
    # Render a 1-based line-numbered preview of an artifact on disk so an agent can construct an exact `edit(path, old_str, new_str)` patch.
    if not file_path.is_file():
        return ""
    try:
        raw_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    if not raw_lines:
        return ""
    nl = chr(10)
    shown = raw_lines[:max_lines]
    numbered = nl.join(f"{idx + 1:3d} | {ln}" for idx, ln in enumerate(shown))
    suffix = f"{nl}... ({len(raw_lines) - max_lines} more line(s))" if len(raw_lines) > max_lines else ""
    return f"Current file on disk ({file_path.name}, {len(raw_lines)} line(s) — preserved for scoped 'edit'):{nl}{numbered}{suffix}"


def validate_requirements_txt(req_text):
    r"""Validate PEP 508 package specifiers in ``requirements.txt``.

    Permits standard pinned/ranged specifiers, extras, inline comments, and
    environment markers while rejecting pip flags, direct URLs, VCS links,
    paths, and shell metacharacters.

    Args:
        req_text: Raw contents of ``requirements.txt``.

    Returns:
        List of line-numbered validation error strings (empty when valid).
    """
    errors = []
    forbidden_substrings = ["://", "`", "$(", "|", "&&", "||", "..", "\\"]
    for lineno, raw_line in enumerate(req_text.splitlines(), start=1):
        # Strip inline comment (# preceded by whitespace or at start)
        line = re.sub(r"(?:^|\s+)#.*$", "", raw_line).strip()
        if not line:
            continue
        if line.startswith("-"):
            errors.append(
                f"Line {lineno}: Pip option/flag '{line.split()[0]}' is prohibited in requirements.txt; declare PEP 508 package specifiers only."
            )
            continue
        if (
            any(tok in line for tok in forbidden_substrings)
            or "/" in line
            or "@" in line
            or line.lower().startswith(("git+", "hg+", "svn+", "bzr+", "file:"))
        ):
            errors.append(
                f"Line {lineno}: Direct URLs, VCS links, paths, and shell metacharacters are prohibited in requirements.txt ('{line[:60]}')."
            )
            continue
        if not _PEP508_REQ_RE.match(line):
            errors.append(
                f"Line {lineno}: Invalid PEP 508 requirement specifier '{line[:60]}'."
            )
    return errors


# ============================================================================
# Section 4: Python Module & Verification Suite AST Contract Validators
# Enforces PEP 257 docstring order, `__future__` placement, strict typing, tolerance comparisons, and Hypothesis generator bounds.
# ============================================================================

def _annotation_contains_unconstrained_any(ann_node):
    r"""Return ``True`` if an AST annotation references unconstrained ``Any``.

    Args:
        ann_node: AST annotation node or ``None``.

    Returns:
        ``True`` if ``Any`` appears in the annotation, else ``False``.
    """
    # Walk a parameter or return type annotation AST to detect forbidden unconstrained `Any` usage.
    if ann_node is None:
        return False
    for sub in ast.walk(ann_node):
        if isinstance(sub, ast.Name) and sub.id == "Any":
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "Any":
            return True
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and re.search(r"\bAny\b", sub.value):
            return True
    return False


def _extract_float_literal(expr_node):
    """Return float value if ``expr_node`` is a numeric float literal, else ``None``.

    Args:
        expr_node: AST expression node (including signed unary literals).

    Returns:
        Signed ``float`` value when literal, otherwise ``None``.
    """
    # Extract a signed Python `float` literal (including `-0.5` unary negation) from an AST node, or `None` if not a float literal.
    if isinstance(expr_node, ast.Constant) and isinstance(expr_node.value, float):
        return expr_node.value
    if (
        isinstance(expr_node, ast.UnaryOp)
        and isinstance(expr_node.op, (ast.USub, ast.UAdd))
        and isinstance(expr_node.operand, ast.Constant)
        and isinstance(expr_node.operand.value, float)
    ):
        return -expr_node.operand.value if isinstance(expr_node.op, ast.USub) else expr_node.operand.value
    return None


def _is_discrete_meta_expr(expr_node):
    """Check whether an AST expression evaluates to a discrete metadata or type value.

    Args:
        expr_node: AST operand expression from an equality comparison.

    Returns:
        ``True`` for ``None``, ``bool``, ``str``, ``int`` literals, discrete
        type/length built-ins (``len``, ``isinstance``, ``type``), and array
        shape/dtype attributes; ``False`` otherwise.
    """
    # Identify discrete integer/boolean/string/shape/dtype expressions where exact `==` equality is completely valid.
    if isinstance(expr_node, ast.Constant):
        return expr_node.value is None or isinstance(expr_node.value, (bool, str)) or (
            isinstance(expr_node.value, int) and not isinstance(expr_node.value, bool)
        )
    if (
        isinstance(expr_node, ast.UnaryOp)
        and isinstance(expr_node.op, (ast.USub, ast.UAdd))
        and isinstance(expr_node.operand, ast.Constant)
        and isinstance(expr_node.operand.value, int)
        and not isinstance(expr_node.operand.value, bool)
    ):
        return True
    if isinstance(expr_node, ast.Call) and isinstance(expr_node.func, ast.Name):
        if expr_node.func.id in {"len", "type", "isinstance", "issubclass", "callable", "hasattr", "id", "ord", "chr"}:
            return True
    if isinstance(expr_node, ast.Attribute):
        if expr_node.attr in {"shape", "ndim", "size", "dtype", "itemsize", "nbytes"}:
            return True
    if isinstance(expr_node, (ast.Tuple, ast.List)) and expr_node.elts:
        return all(_is_discrete_meta_expr(el) for el in expr_node.elts)
    return False


def _compare_continuous_eq_violation(comp_node, check_binop=False):
    """Detect unsafe exact ``==`` or ``!=`` comparisons on continuous floating-point expressions.

    Args:
        comp_node: ``ast.Compare`` node to inspect.
        check_binop: When ``True``, also flag exact comparisons over binary
            arithmetic expressions (``+``, ``-``, ``*``, ``/``, ``**``).

    Returns:
        Human-readable description of the offending float operand when an
        exact comparison violation is detected, otherwise ``None``.
    """
    # Flag `==` / `!=` comparisons against float literals or continuous arithmetic expressions where `math.isclose` / `np.allclose` is required.
    if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in comp_node.ops):
        return None
    operands = [comp_node.left] + list(comp_node.comparators)
    if any(_is_discrete_meta_expr(o) for o in operands):
        return None
    for operand in operands:
        fl_val = _extract_float_literal(operand)
        if fl_val is not None:
            return f"float literal '{fl_val}'"
    if check_binop:
        for operand in operands:
            if isinstance(operand, ast.BinOp) and isinstance(
                operand.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
            ):
                return "continuous arithmetic expression"
    return None


def _is_scalar_minmax_over_abs(call_node):
    """Detect Python built-in ``max``/``min`` calls applied to ``abs``/``np.abs`` operands.

    Passing multi-element NumPy arrays to Python's scalar ``max(np.abs(a), ...)``
    raises ``ValueError: The truth value of an array with more than one element
    is ambiguous``.

    Args:
        call_node: ``ast.Call`` node to inspect.

    Returns:
        ``True`` if ``call_node`` invokes built-in ``max`` or ``min`` with two
        or more arguments containing ``abs(...)`` or ``*.abs(...)``; else ``False``.
    """
    # Catch `max(np.abs(a), ...)` patterns that crash with `ValueError` on multi-element NumPy arrays.
    if not (isinstance(call_node, ast.Call) and isinstance(call_node.func, ast.Name)):
        return False
    if call_node.func.id not in {"max", "min"} or len(call_node.args) < 2:
        return False
    for arg in call_node.args:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Call):
                if isinstance(sub.func, ast.Name) and sub.func.id == "abs":
                    return True
                if isinstance(sub.func, ast.Attribute) and sub.func.attr == "abs":
                    return True
    return False


def _extract_module_docstring_relaxed(tree):
    """Extract module docstring before or right after ``__future__`` imports.

    Args:
        tree: Parsed module ``ast.AST`` tree.

    Returns:
        Docstring text if found, otherwise ``None``.
    """
    # Read a module's top docstring even if the author accidentally placed `from __future__ import annotations` above it.
    doc = ast.get_docstring(tree)
    if doc:
        return doc
    idx = 0
    while idx < len(tree.body) and isinstance(tree.body[idx], ast.ImportFrom) and tree.body[idx].module == "__future__":
        idx += 1
    if idx < len(tree.body) and isinstance(tree.body[idx], ast.Expr):
        val = tree.body[idx].value
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            return val.value
    return None


def _hoist_misplaced_future_imports(source_text: str) -> str:
    r"""Hoist misplaced top-level ``from __future__ import ...`` lines before ``ast.parse``.

    Fast-paths valid Python unchanged. Only intervenes when ``ast.parse`` fails
    with ``from __future__ imports must occur at the beginning of the file``,
    relocating unindented ``from __future__ import ...`` lines above non-docstring
    code and canonicalizing PEP 257 module docstring order.

    Args:
        source_text: Candidate Python module source text.

    Returns:
        Syntax-repaired Python source when repairable, else ``source_text``.
    """
    try:
        ast.parse(source_text)
        return source_text
    except SyntaxError as se:
        if "from __future__ imports must occur at the beginning of the file" not in str(se.msg):
            return source_text
    except Exception:
        return source_text

    lines = source_text.splitlines()
    fut_re = re.compile(r"^from\s+__future__\s+import\s+[^\n#]+(?:#.*)?$")
    fut_lines: list[str] = []
    kept_lines: list[str] = []
    for ln in lines:
        if fut_re.match(ln):
            fut_lines.append(ln)
        else:
            kept_lines.append(ln)
    if not fut_lines:
        return source_text

    insert_at = 0
    while insert_at < len(kept_lines) and (not kept_lines[insert_at].strip() or kept_lines[insert_at].lstrip().startswith("#")):
        insert_at += 1
    rebuilt = kept_lines[:insert_at] + fut_lines + [""] + kept_lines[insert_at:]
    cand = "\n".join(rebuilt).rstrip("\n") + "\n"
    try:
        ast.parse(cand)
        return _canonicalize_module_docstring_order(cand)
    except Exception:
        return source_text


def _canonicalize_module_docstring_order(source_text):
    r"""Place a misplaced module docstring above ``from __future__`` imports.

    Per PEP 257 and Python Language Reference Section 7.11.2, a module
    docstring only populates ``module.__doc__`` when it appears as the first
    statement before any ``from __future__ import ...`` directive.

    Args:
        source_text: Normalized Python source string.

    Returns:
        Reordered Python source string when a leading ``__future__`` import
        immediately precedes a standalone docstring literal, else ``source_text``.
    """
    # Automatically move a misplaced module docstring above `from __future__ import annotations` so Python populates `module.__doc__` per PEP 257.
    try:
        tree = ast.parse(source_text)
    except Exception:
        return source_text
    if ast.get_docstring(tree) is not None or len(tree.body) < 2:
        return source_text
    idx = 0
    while idx < len(tree.body) and isinstance(tree.body[idx], ast.ImportFrom) and tree.body[idx].module == "__future__":
        idx += 1
    if idx == 0 or idx >= len(tree.body):
        return source_text
    doc_node = tree.body[idx]
    if not (isinstance(doc_node, ast.Expr) and isinstance(doc_node.value, ast.Constant) and isinstance(doc_node.value.value, str)):
        return source_text
    lines = source_text.splitlines()
    fut_start = tree.body[0].lineno - 1
    fut_end = getattr(tree.body[idx - 1], "end_lineno", None)
    doc_start = doc_node.lineno - 1
    doc_end = getattr(doc_node, "end_lineno", None)
    if fut_end is None or doc_end is None or fut_end > doc_start:
        return source_text
    prefix = lines[:fut_start]
    fut_lines = lines[fut_start:fut_end]
    between = [ln for ln in lines[fut_end:doc_start] if ln.strip().startswith("#")]
    doc_lines = lines[doc_start:doc_end]
    suffix = lines[doc_end:]
    candidate = "\n".join(prefix + doc_lines + [""] + between + fut_lines + suffix).rstrip("\n") + "\n"
    try:
        chk = ast.parse(candidate)
        if ast.get_docstring(chk) is not None:
            return candidate
    except Exception:
        return source_text
    return source_text


def _collect_module_defined_names(tree):
    """Collect defined symbols, imports, parameters, and Python builtins.

    Args:
        tree: Parsed module ``ast.AST`` tree.

    Returns:
        Set of locally defined or imported symbol names, or ``None`` when a
        wildcard ``from ... import *`` makes static resolution incomplete.
    """
    # Gather module-wide defined functions, classes, imports, parameters, and builtins for fast undefined-call checks.
    defined = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            fn_args = getattr(node, "args", None)
            if fn_args is not None:
                for arg in list(fn_args.posonlyargs) + list(fn_args.args) + list(fn_args.kwonlyargs):
                    defined.add(arg.arg)
                if fn_args.vararg is not None:
                    defined.add(fn_args.vararg.arg)
                if fn_args.kwarg is not None:
                    defined.add(fn_args.kwarg.arg)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                defined.add((alias.asname or alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return None
                defined.add(alias.asname or alias.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
    return defined


def validate_python_ast(py_path):
    r"""Check Python AST for citations, typing, undefined calls, and floats.

    Args:
        py_path: Path to the Python source file to inspect.

    Returns:
        Up to 5 diagnostic warning/error strings (empty when compliant).
    """
    diagnostics = []
    try:
        source_text = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source_text, filename=str(py_path))
    except SyntaxError as se:
        return [f"Line {se.lineno}: Syntax Error: {se.msg}"]
    except Exception as e:
        return [f"AST Parse Error: {e}"]

    # 1. Module Docstring Citation Check (accepting docstring before OR right after 'from __future__ import annotations')
    module_doc = _extract_module_docstring_relaxed(tree)
    if not module_doc:
        diagnostics.append("Missing module-level docstring with literature citation.")
    else:
        doc_lower = module_doc.lower()
        has_citation = any(
            k in doc_lower
            for k in [
                "implements", "eq.", "equation", "page", "section", "rfc", "doi", "arxiv",
                "theorem", "http", "references", "proc.", "journal", "vol.", "isbn", "chapter"
            ]
        ) or bool(re.search(r"\b(18|19|20)\d{2}\b", doc_lower))
        if not has_citation:
            diagnostics.append("Module docstring lacks literature citation (e.g. 'Implements Eq. (X.Y), Page Z of ...').")

    defined_names = _collect_module_defined_names(tree)

    # 2. Undefined Helper Call Check (catches incomplete refactors like calling undefined '_solve_dirichlet')
    if defined_names is not None:
        seen_undef = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                callee = node.func.id
                if callee not in defined_names and callee not in seen_undef:
                    seen_undef.add(callee)
                    diagnostics.append(
                        f"Line {node.lineno}: Call to undefined function/symbol '{callee}' (incomplete refactor or missing definition)."
                    )

    # 3. Function Typing, Unconstrained Any & Float Equality Checks
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_name = node.name
            if fn_name.startswith("_"):
                continue  # Skip private functions

            # Return type annotation check
            if node.returns is None:
                diagnostics.append(f"Line {node.lineno}: Function '{fn_name}' is missing return type annotation.")
            elif _annotation_contains_unconstrained_any(node.returns):
                diagnostics.append(
                    f"Line {node.lineno}: Function '{fn_name}' uses unconstrained 'Any' in return annotation (prohibited by lint_rules.json)."
                )

            # Argument type annotation check across posonly, positional, kwonly, *args, and **kwargs
            all_fn_args = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
            if node.args.vararg is not None:
                all_fn_args.append(node.args.vararg)
            if node.args.kwarg is not None:
                all_fn_args.append(node.args.kwarg)
            for arg in all_fn_args:
                if arg.arg in ["self", "cls"]:
                    continue
                if arg.annotation is None:
                    diagnostics.append(f"Line {arg.lineno}: Parameter '{arg.arg}' in function '{fn_name}' lacks type annotation.")
                elif _annotation_contains_unconstrained_any(arg.annotation):
                    diagnostics.append(
                        f"Line {arg.lineno}: Parameter '{arg.arg}' in function '{fn_name}' uses unconstrained 'Any' (prohibited by lint_rules.json)."
                    )

            # Check for raw float equality comparison (including negative float literals like -0.5)
            for child in ast.walk(node):
                if isinstance(child, ast.Compare):
                    for op in child.ops:
                        if isinstance(op, (ast.Eq, ast.NotEq)):
                            for operand in [child.left] + child.comparators:
                                fl_val = _extract_float_literal(operand)
                                if fl_val is not None:
                                    diagnostics.append(
                                        f"Line {child.lineno}: Raw float equality comparison detected ('== {fl_val}'). "
                                        "Use math.isclose or np.allclose under tolerance contracts."
                                    )

    # 4. Cross-check public entrypoint signatures against mapping.json when present in the same directory
    map_p = Path(py_path).parent / "mapping.json"
    if map_p.is_file():
        try:
            mp_data = json.loads(map_p.read_text(encoding="utf-8"))
            pub = mp_data.get("public_interface") if isinstance(mp_data.get("public_interface"), dict) else {}
            tmod = str(pub.get("target_module") or pub.get("module_name") or "").strip()
            if not tmod or tmod == Path(py_path).name:
                fn_nodes = {
                    n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                for ep in (pub.get("entrypoints") or []):
                    if not isinstance(ep, dict) or not isinstance(ep.get("name"), str):
                        continue
                    en = ep["name"].strip()
                    if en not in fn_nodes:
                        continue
                    fnode = fn_nodes[en]
                    raw_ps = ep.get("parameters") if isinstance(ep.get("parameters"), list) else []
                    wants_vararg = any(
                        isinstance(p, dict) and (str(p.get("name") or "").strip().startswith("*") and not str(p.get("name") or "").strip().startswith("**") or p.get("kind") == "var_positional")
                        for p in raw_ps
                    )
                    if wants_vararg and fnode.args.vararg is None:
                        diagnostics.append(
                            f"Line {fnode.lineno}: Function '{en}' must accept variadic '*args' per mapping.json (use 'def {en}(*args: ...)' rather than a single sequence parameter)."
                        )
        except Exception:
            pass

    return diagnostics[:5]  # Cap top 5 diagnostics to avoid attention bloat


def validate_verification_script_ast(py_path, cwd=None):
    """Audit Stage 4 ``verify_*.py`` / ``test_*.py`` scripts across two tiers.

    Separates fail-closed Cleanroom oracle-soundness errors (Tier 1, which also
    gate ``VERIFICATION.md`` sign-off) from static hygiene and fuzzing coverage
    warnings (Tier 2).

    Phases:
      - Phase 1: Contract Discovery & Lexical Unbound-Symbol Resolution
      - Phase 2: Hypothesis Float Bounds, Anti-Seed-Pinning & `math.isclose` Kwarg Soundness
      - Phase 3: Signature Typing, Continuous Float-Equality & Unused-Import Hygiene

    Args:
        py_path: Path to the verification Python script on disk.
        cwd: Optional workspace root directory to inspect ``manifest.json`` and
            ``mapping.json`` contracts.

    Returns:
        Tuple ``(soundness_errors, hygiene_warnings)`` of diagnostic lists.
    """
    # --- Phase 1: Contract Discovery & Lexical Unbound-Symbol Resolution ---
    soundness_errors = []
    hygiene_warnings = []
    try:
        source_text = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source_text, filename=str(py_path))
    except SyntaxError as se:
        return ([f"Line {se.lineno}: Syntax Error: {se.msg}"], [])
    except Exception as exc:
        return ([f"AST Parse Error: {exc}"], [])

    eq_mode = "approximate_tolerance"
    has_preconditions = False
    has_fuzz_obligations = False
    if cwd is not None:
        manifest_file = cwd / "manifest.json"
        if manifest_file.is_file():
            try:
                m_data = json.loads(manifest_file.read_text(encoding="utf-8"))
                vc = m_data.get("verification_contract")
                if isinstance(vc, dict) and isinstance(vc.get("equivalence_mode"), str):
                    eq_mode = vc["equivalence_mode"].strip()
            except Exception:
                pass
        mapping_file = cwd / "mapping.json"
        if mapping_file.is_file():
            try:
                map_data = json.loads(mapping_file.read_text(encoding="utf-8"))
                pre_list = map_data.get("precondition_assertions")
                has_preconditions = isinstance(pre_list, list) and len(pre_list) > 0
                obls = map_data.get("verification_obligations")
                if isinstance(obls, list):
                    has_fuzz_obligations = any(
                        isinstance(o, dict) and o.get("predicate_type") in ("postcondition", "relational_law")
                        for o in obls
                    )
            except Exception:
                pass

    lex_unbound_v = find_lexical_unbound_names(Path(py_path))
    if lex_unbound_v:
        soundness_errors.extend(lex_unbound_v)
    else:
        defined_names = _collect_module_defined_names(tree)
        if defined_names is not None:
            seen_undef = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    callee = node.func.id
                    if callee not in defined_names and callee not in seen_undef:
                        seen_undef.add(callee)
                        soundness_errors.append(
                            f"Line {node.lineno}: Call to undefined function/symbol '{callee}'."
                        )

    cfg_vast = load_pipeline_config(Path(cwd)) if cwd is not None else DEFAULT_CONFIG
    cg_vast = str(((cfg_vast.get("experiments") or {}) if isinstance(cfg_vast, dict) else {}).get("contract_and_gates") or "standard")
    check_ast4d = cg_vast in ("debiased_contract_ocr_gate", "axiomatic_split_and_loop_guard")
    has_complex_or_np = "numpy" in source_text or "complex" in source_text or "1j" in source_text

    # --- Phase 2: Hypothesis Float Bounds, Anti-Seed-Pinning & `math.isclose` Kwarg Soundness ---
    # Extract maximum explicit numeric magnitude in manifest.json domain_bounds (default ceiling 1e8)
    max_manifest_bound = 1e8
    if cwd is not None and (Path(cwd) / "manifest.json").is_file():
        try:
            vc_b = json.loads((Path(cwd) / "manifest.json").read_text(encoding="utf-8")).get("verification_contract") or {}
            db_raw = vc_b.get("domain_bounds") if isinstance(vc_b, dict) else None
            if isinstance(db_raw, dict):
                for bv in db_raw.values():
                    if isinstance(bv, (int, float)) and math.isfinite(bv):
                        max_manifest_bound = max(max_manifest_bound, abs(float(bv)))
                    elif isinstance(bv, list):
                        for elem in bv:
                            if isinstance(elem, (int, float)) and math.isfinite(elem):
                                max_manifest_bound = max(max_manifest_bound, abs(float(elem)))
        except Exception:
            pass

    for node in ast.walk(tree):
        # AST-5: Enforce finite, well-conditioned bounds on Hypothesis `st.floats(...)` / `floats(...)` generators
        is_hyp_floats = isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "floats")
            or (isinstance(node.func, ast.Name) and node.func.id == "floats")
        )
        if is_hyp_floats:
            min_node = node.args[0] if len(node.args) >= 1 else next((kw.value for kw in node.keywords if kw.arg == "min_value"), None)
            max_node = node.args[1] if len(node.args) >= 2 else next((kw.value for kw in node.keywords if kw.arg == "max_value"), None)
            min_missing = min_node is None or (isinstance(min_node, ast.Constant) and min_node.value is None)
            max_missing = max_node is None or (isinstance(max_node, ast.Constant) and max_node.value is None)
            if min_missing or max_missing:
                soundness_errors.append(
                    f"Line {node.lineno}: Unbounded Hypothesis 'floats()' generates IEEE-754 subnormals/overflows (1e-308..1e+308) "
                    "that violate numerical conditioning; specify explicit finite 'min_value' and 'max_value' bounds (e.g. min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False)."
                )
            else:
                for b_lbl, b_nd in (("min_value", min_node), ("max_value", max_node)):
                    b_lit = _extract_float_literal(b_nd)
                    if b_lit is not None and abs(b_lit) > max_manifest_bound:
                        soundness_errors.append(
                            f"Line {node.lineno}: Hypothesis 'floats()' {b_lbl}={b_lit:g} exceeds well-conditioned float64 range (+/-{max_manifest_bound:g}); "
                            "use moderate finite bounds (e.g. -1e3..1e3) unless manifest.json explicitly declares wider domain_bounds."
                        )

        # AST-6: Forbid deterministic seed-pinning (`@seed(...)` or `derandomize=True`) that masks stochastic failures
        if isinstance(node, ast.Call):
            is_seed_call = (
                (isinstance(node.func, ast.Name) and node.func.id == "seed")
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "seed"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "hypothesis"
                )
            )
            if is_seed_call:
                soundness_errors.append(
                    f"Line {node.lineno}: Deterministic Hypothesis '@seed(...)' pinning is prohibited in Cleanroom verification; "
                    "implementations and obligations must hold across unseeded stochastic draws."
                )
            is_settings_call = (
                (isinstance(node.func, ast.Name) and node.func.id == "settings")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "settings")
            )
            if is_settings_call:
                for kw in node.keywords:
                    if kw.arg == "derandomize" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        soundness_errors.append(
                            f"Line {node.lineno}: 'derandomize=True' in Hypothesis '@settings(...)' is prohibited; "
                            "verification suites must remain robust across stochastic draws."
                        )

        if _is_scalar_minmax_over_abs(node):
            soundness_errors.append(
                f"Line {node.lineno}: Python built-in '{node.func.id}()' over abs/np.abs raises ValueError on "
                "multi-element NumPy arrays and omits atol. Use 'np.allclose(..., rtol=..., atol=...)' or 'np.maximum'."
            )
        # AST-4d: Catch `math.isclose(..., rtol=..., atol=...)` invalid kwargs and complex/ndarray misuse.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "math"
            and node.func.attr == "isclose"
        ):
            kw_names = {kw.arg for kw in node.keywords if kw.arg}
            if kw_names & {"rtol", "atol"}:
                soundness_errors.append(
                    f"Line {node.lineno}: 'math.isclose()' does not accept 'rtol'/'atol' keyword arguments (raises TypeError at runtime). "
                    "Use 'np.isclose(..., rtol=..., atol=...)' / 'np.allclose(...)' or 'math.isclose(..., rel_tol=..., abs_tol=...)'."
                )
            elif check_ast4d and has_complex_or_np:
                hygiene_warnings.append(
                    f"Line {node.lineno} (AST-4d): Prefer 'np.allclose' / 'np.isclose' over 'math.isclose' when testing NumPy arrays or complex scalars."
                )

    # --- Phase 3: Signature Typing, Continuous Float-Equality & Unused-Import Hygiene ---
    test_fn_count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_name = node.name
            if fn_name.startswith("test_") or fn_name.startswith("verify_"):
                test_fn_count += 1
            if not fn_name.startswith("_"):
                if node.returns is None:
                    hygiene_warnings.append(
                        f"Line {node.lineno}: Function '{fn_name}' is missing return type annotation (e.g. '-> None')."
                    )
                elif _annotation_contains_unconstrained_any(node.returns):
                    hygiene_warnings.append(
                        f"Line {node.lineno}: Function '{fn_name}' uses unconstrained 'Any' in return annotation."
                    )
                all_fn_args = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
                for arg in all_fn_args:
                    if arg.arg in ("self", "cls"):
                        continue
                    if arg.annotation is None:
                        hygiene_warnings.append(
                            f"Line {arg.lineno}: Parameter '{arg.arg}' in '{fn_name}' lacks type annotation."
                        )
                    elif _annotation_contains_unconstrained_any(arg.annotation):
                        hygiene_warnings.append(
                            f"Line {arg.lineno}: Parameter '{arg.arg}' in '{fn_name}' uses unconstrained 'Any'."
                        )

            if eq_mode == "approximate_tolerance":
                for child in ast.walk(node):
                    if isinstance(child, ast.Assert):
                        for sub in ast.walk(child.test):
                            if isinstance(sub, ast.Compare):
                                viol = _compare_continuous_eq_violation(sub, check_binop=True)
                                if viol:
                                    soundness_errors.append(
                                        f"Line {sub.lineno}: Exact equality assertion ('==') on {viol} in '{fn_name}'. "
                                        "Use 'math.isclose(..., rel_tol=..., abs_tol=...)' or 'np.allclose(..., rtol=..., atol=...)'."
                                    )
                    elif isinstance(child, ast.Compare):
                        viol_lit = _compare_continuous_eq_violation(child, check_binop=False)
                        if viol_lit:
                            soundness_errors.append(
                                f"Line {child.lineno}: Raw float equality comparison on {viol_lit} in '{fn_name}'. "
                                "Use 'math.isclose' or 'np.allclose'."
                            )

    if test_fn_count > 0:
        # Fallback stdlib check for unused top-level imports when ruff is absent
        used_names = {
            sub.id for sub in ast.walk(tree) if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
        }
        for sub in ast.walk(tree):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                used_names.update(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", sub.value))
        for top_stmt in tree.body:
            if isinstance(top_stmt, ast.Import):
                for alias in top_stmt.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    if bound not in used_names:
                        hygiene_warnings.append(f"Line {top_stmt.lineno}: Unused import '{bound}'.")
            elif isinstance(top_stmt, ast.ImportFrom) and top_stmt.module != "__future__":
                for alias in top_stmt.names:
                    if alias.name == "*":
                        continue
                    bound = alias.asname or alias.name
                    if bound not in used_names:
                        hygiene_warnings.append(f"Line {top_stmt.lineno}: Unused import '{bound}'.")

        if not _extract_module_docstring_relaxed(tree):
            hygiene_warnings.append("Missing module-level docstring summarizing verified specifications and obligations.")
        if has_preconditions and "raises(" not in source_text and "AssertionError" not in source_text:
            hygiene_warnings.append(
                "Missing negative precondition test: verify 'precondition_assertions' from mapping.json using 'pytest.raises((AssertionError, ValueError))'."
            )
        if has_fuzz_obligations and eq_mode == "approximate_tolerance" and "@given" not in source_text:
            hygiene_warnings.append(
                "Missing Hypothesis '@given' property fuzzing test for postcondition/relational_law obligations."
            )
        if cwd is not None and (Path(cwd) / "mapping.json").is_file():
            try:
                mp_cov = json.loads((Path(cwd) / "mapping.json").read_text(encoding="utf-8"))
                pub_cov = mp_cov.get("public_interface") if isinstance(mp_cov.get("public_interface"), dict) else {}
                ep_names_cov = [
                    (ep.get("name") if isinstance(ep, dict) else ep)
                    for ep in (pub_cov.get("entrypoints") or [])
                ]
                referenced_syms = {
                    sub.id for sub in ast.walk(tree) if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
                } | {
                    sub.attr for sub in ast.walk(tree) if isinstance(sub, ast.Attribute)
                }
                missing_called = [
                    str(en).strip()
                    for en in ep_names_cov
                    if isinstance(en, str) and en.strip() and en.strip() not in referenced_syms
                ]
                if missing_called:
                    soundness_errors.append(
                        f"Vacuous Verification Suite (AST-4c): '{Path(py_path).name}' never references or invokes declared "
                        f"mapping.json entrypoint(s) {missing_called}."
                    )
            except Exception:
                pass

    # Deduplicate preserving order
    dedup_err = list(dict.fromkeys(soundness_errors))[:5]
    dedup_warn = list(dict.fromkeys(hygiene_warnings))[:5]
    return (dedup_err, dedup_warn)


# ============================================================================
# Section 5: Chat Link Encoding, Table Formatting & Command Fuzzy Matchers
# Formats `%20`-encoded CommonMark file links, unclipped Markdown table cells, and near-miss slash-command hints.
# ============================================================================

def is_near_miss_command(raw_prompt: str) -> str | None:
    r"""Detect near-miss typos or conversational affirmatives for contractual commands.

    Args:
        raw_prompt: Raw user prompt text submitted in Chat.

    Returns:
        Suggested canonical command (``"/approve"``, ``"/reset"``, or ``"/status"``)
        when ``raw_prompt`` is a near-miss typo or conversational affirmative;
        ``None`` when ``raw_prompt`` is already an exact command or a substantive
        user question.
    """
    # Catch typos like `/appove`, `approve/`, `/stauts`, or casual affirmatives (`yes`, `lgtm`) via `difflib` so `@Facet` replies with a fast zero-tool hint.
    stripped = re.sub(r"^\s*@facet\s+", "", str(raw_prompt or ""), flags=re.IGNORECASE).strip()
    if not stripped or len(stripped) > 32 or len(stripped.split()) > 3:
        return None
    lower_s = stripped.lower()
    if (
        lower_s == "/approve"
        or lower_s.startswith("/approve ")
        or lower_s == "/reset"
        or lower_s.startswith("/reset ")
        or lower_s == "/status"
        or lower_s.startswith("/status ")
    ):
        return None
    if lower_s in {"yes", "y", "ok", "okay", "lgtm", "looks good", "continue", "proceed", "ship it"}:
        return "/approve"
    first_tok = re.sub(r"[^a-z]", "", lower_s.split()[0])
    if len(first_tok) >= 4:
        if difflib.SequenceMatcher(None, first_tok, "approve").ratio() >= 0.72:
            return "/approve"
        if difflib.SequenceMatcher(None, first_tok, "reset").ratio() >= 0.75:
            return "/reset"
        if difflib.SequenceMatcher(None, first_tok, "status").ratio() >= 0.75:
            return "/status"
    return None


def format_chat_file_link(label: str, target_path: Path) -> str:
    r"""Format a VS Code Chat Markdown file link using ``%20`` URL-encoded POSIX paths.

    Avoids ``<...>`` angle brackets around ``/Users/...`` paths (which ``gemma4:e4b``
    misinterprets as ``</...>`` closing XML tags and strips) while producing valid
    zero-whitespace CommonMark links that VS Code's ``URI.parse`` resolves natively.

    Args:
        label: Link display label (typically the filename).
        target_path: Filesystem path to resolve as a POSIX link target.

    Returns:
        Clickable Markdown link ``[label](/url/encoded/path)``.
    """
    abs_posix = Path(target_path).resolve().as_posix()
    enc_posix = urllib.parse.quote(urllib.parse.unquote(abs_posix), safe="/:")
    return f"[{label}]({enc_posix})"


def normalize_markdown_file_links(text: str) -> str:
    r"""Normalize angle-bracketed or space-containing local Markdown links to ``%20`` encoding.

    Args:
        text: Markdown response text to normalize.

    Returns:
        Markdown text with ``[label](/path with spaces)`` and
        ``[label](</path with spaces>)`` rewritten to ``[label](/path%20with%20spaces)``.
    """
    def _enc_match(m: re.Match[str]) -> str:
        lbl = m.group(1)
        raw_p = urllib.parse.unquote(m.group(2).strip())
        # Preserve optional `:line` suffix on symbol links.
        enc_p = urllib.parse.quote(raw_p, safe="/:#")
        return f"[{lbl}]({enc_p})"

    return _LOCAL_ABS_MD_LINK_RE.sub(_enc_match, str(text or ""))


def _escape_md_cell(text: str) -> str:
    r"""Escape pipe and newline characters for safe Markdown table cells.

    Args:
        text: Raw cell text.

    Returns:
        Single-line string with ``|`` escaped as ``\\|``.
    """
    # Collapse newlines and escape `|` pipes so LaTeX norms (`||x||`) never break Markdown table columns.
    return str(text or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def _truncate_md_table_desc(text: str, max_len: int = 180) -> str:
    r"""Format a Markdown table cell without slicing mid-word or mid-formula.

    Args:
        text: Raw description or predicate text.
        max_len: Soft character ceiling before word-boundary truncation.

    Returns:
        Escaped single-line cell string with balanced inline ``$`` delimiters.
    """
    cell = _escape_md_cell(text)
    if len(cell) <= max_len:
        return cell
    cut = cell[:max_len]
    # Avoid cutting inside an unclosed inline `$...$` math span when possible.
    if cut.count("$") % 2 == 1:
        last_dollar = cut.rfind("$")
        if last_dollar > 24:
            cut = cut[:last_dollar].rstrip()
    last_space = cut.rfind(" ")
    if last_space > max_len // 2:
        cut = cut[:last_space].rstrip(" ,;:-")
    return f"{cut}..."


def sanitize_user_briefing_prose(text: str) -> str:
    r"""Scrub internal agent/process narration and emojis from user-facing briefing prose.

    Args:
        text: Raw specialist ``### Stage Briefing`` or halt message text.

    Returns:
        Clean, domain-focused user-facing Markdown text.
    """
    cleaned = _EMOJI_STRIP_RE.sub("", str(text or ""))
    cleaned = re.sub(
        r"\bThe extraction process has successfully identified\b",
        "Source analysis identified",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bTo resolve this,\s*I implemented\b",
        "To resolve this, the implementation applies",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bI implemented\b",
        "The implementation applies",
        cleaned,
    )
    cleaned = re.sub(
        r"[^.!?\n]*(?:\ban inline smoke check confirmed\b|\bprocessed by (?:the )?@?Scribe\b|\bready for synthesis into\b|\bavailable for review by (?:the )?@?Auditor\b)[^.!?\n]*[.!?]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    for role_tok, plain_lbl in (
        ("@Scribe", "visual OCR"),
        ("@Researcher", "literature analysis"),
        ("@Formalist", "interface mapping"),
        ("@Engineer", "module implementation"),
        ("@Auditor", "cleanroom verification"),
        ("@Orchestrator", "the pipeline"),
    ):
        cleaned = re.sub(rf"`?{re.escape(role_tok)}`?", plain_lbl, cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _format_symbol_for_prose(raw_symbol: str) -> str:
    r"""Format a manifest/mapping variable symbol cleanly for Markdown prose."""
    sym = str(raw_symbol or "").strip()
    if not sym:
        return ""
    if sym.startswith("\\") or "^" in sym or "{" in sym:
        return f"${sym}$"
    return f"`{sym}`"

__all__ = [
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
    "_format_python_fragment_with_ruff",
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
]
