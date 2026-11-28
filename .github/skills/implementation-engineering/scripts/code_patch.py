#!/usr/bin/env python3
"""Structural AST symbol, line-range, and JSON-pointer patcher for Facet.

Provides surgical, rollback-safe commands for updating targeted Python functions,
line ranges, or JSON fields without regenerating entire files from scratch.
Validates syntax before committing (`py_compile` / `json.loads`), normalizes code
formatting via `ruff format` when available, and writes atomically via `os.replace`.

Commands:
  1. ``inspect <file.py> [--lines START:END] [--symbol NAME]``: Print the AST
     symbol table and 1-based line-numbered source view.
  2. ``replace-symbol <file.py> --symbol <NAME> [--code-file PATH | --stdin]``:
     Replace a unique function, async function, or class (including decorators).
  3. ``replace-lines <file.py> --start S --end E [--code-file PATH | --stdin]``:
     Replace an inclusive 1-based line range ``[S..E]``.
  4. ``replace-json <file.json> --pointer </rfc6901/path> [--value JSON | --stdin]``:
     Update a single RFC 6901 JSON Pointer path in ``manifest.json`` or ``mapping.json``.

Function Map:
  - Section 1: AST Symbol Resolution, Syntax Validation & Atomic Commit (`_collect_ast_symbols`, `_resolve_unique_symbol`, `_atomic_validate_and_commit`)
  - Section 2: Python AST Symbol & Line-Range Patch Subcommands (`cmd_inspect`, `cmd_replace_symbol`, `cmd_replace_lines`)
  - Section 3: RFC 6901 JSON Pointer Patcher & CLI Entrypoint (`build_parser`, `cmd_replace_json`, `main`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


# ============================================================================
# Section 1: AST Symbol Resolution, Syntax Validation & Atomic Commit
# ============================================================================

def _collect_ast_contract_warnings(target_path: Path, cwd: Path) -> List[str]:
    """Run Facet's AST contract checker on production modules.

    Skips verification/test/smoke scripts and repository infrastructure helpers.
    Loads ``pipeline_guards.py`` from the workspace or ``$COPILOT_HOME`` when
    present and delegates to ``validate_python_ast()``.

    Args:
        target_path: Patched Python file path.
        cwd: Resolved workspace root directory.

    Returns:
        List of AST contract warning strings, or ``[]`` when clean/skipped.
    """
    # Skip verification scripts and internal tooling; only check production modules.
    if (
        target_path.name.startswith(("verify_", "test_", "audit_", "smoke_"))
        or target_path.name.endswith("_test.py")
        or "scripts" in target_path.parts
        or "templates" in target_path.parts
    ):
        return []
    # Locate `pipeline_guards.py` across workspace, skill parent, or user `~/.copilot/` scope.
    copilot_home = Path(os.environ["COPILOT_HOME"]).expanduser() if os.environ.get("COPILOT_HOME", "").strip() else (Path.home() / ".copilot")
    candidates = [
        cwd / ".github" / "hooks" / "scripts" / "pipeline_guards.py",
        Path(__file__).resolve().parents[3] / "hooks" / "scripts" / "pipeline_guards.py",
        copilot_home / "hooks" / "scripts" / "pipeline_guards.py",
    ]
    for guard_file in candidates:
        if guard_file.is_file():
            # Dynamically load helper module, degrading gracefully if unavailable.
            try:
                # Dynamically load `pipeline_guards.validate_python_ast` to surface missing
                # citations, missing type annotations, or raw float `==` warnings immediately.
                spec = importlib.util.spec_from_file_location("_facet_guards_patch", str(guard_file))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)  # type: ignore[union-attr]
                    return list(mod.validate_python_ast(target_path))
            except Exception:
                return []
    return []


def _normalize_python_whitespace(source: str) -> str:
    r"""Normalize line endings and trailing spaces in Python source.

    Args:
        source: Raw Python source string.

    Returns:
        LF-normalized text with trailing horizontal spaces stripped and a
        single trailing newline (or empty string if blank).
    """
    normalized_lines = [line.rstrip(" \t") for line in source.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    body = "\n".join(normalized_lines).rstrip("\n")
    return (body + "\n") if body else ""


def _collect_ast_symbols(tree: ast.AST) -> List[Dict[str, object]]:
    """Collect module-level and class-nested AST symbol spans.

    Args:
        tree: Parsed ``ast.AST`` module tree.

    Returns:
        List of symbol metadata dicts containing ``name``, ``qualname``,
        ``kind``, 1-based ``start_line`` (including decorators), ``end_line``,
        and ``col_offset``.
    """
    symbols: List[Dict[str, object]] = []

    # Helper function `_span`: Compute decorator-inclusive 1-based line bounds and indent offset.
    def _span(node: ast.AST) -> Tuple[int, int, int]:
        """Compute decorator-inclusive 1-based line bounds and indent offset."""
        decs = getattr(node, "decorator_list", []) or []
        start = min([getattr(node, "lineno", 1)] + [getattr(d, "lineno", 1) for d in decs])
        end = int(getattr(node, "end_lineno", getattr(node, "lineno", start)))
        col = int(getattr(node, "col_offset", 0))
        return (int(start), end, col)

    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            s_line, e_line, col = _span(node)
            kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            symbols.append({"name": node.name, "qualname": node.name, "kind": kind, "start_line": s_line, "end_line": e_line, "col_offset": col})
        elif isinstance(node, ast.ClassDef):
            s_line, e_line, col = _span(node)
            symbols.append({"name": node.name, "qualname": node.name, "kind": "class", "start_line": s_line, "end_line": e_line, "col_offset": col})
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    ms, me, mcol = _span(sub)
                    mkind = "async def" if isinstance(sub, ast.AsyncFunctionDef) else "def"
                    symbols.append({
                        "name": sub.name,
                        "qualname": f"{node.name}.{sub.name}",
                        "kind": mkind,
                        "start_line": ms,
                        "end_line": me,
                        "col_offset": mcol,
                    })
    return symbols


def _resolve_unique_symbol(symbols: Sequence[Dict[str, object]], query: str) -> Dict[str, object]:
    """Resolve ``query`` to exactly one AST symbol entry.

    Args:
        symbols: Collected AST symbol entries from ``_collect_ast_symbols()``.
        query: Symbol name (e.g. ``solve_step``) or qualified name
            (e.g. ``Solver.step``).

    Returns:
        The unique matching symbol metadata dict.

    Raises:
        ValueError: If multiple definitions match ``query``.
        KeyError: If no definition matches ``query``.
    """
    exact_qual = [s for s in symbols if s["qualname"] == query]
    if len(exact_qual) == 1:
        return exact_qual[0]
    if len(exact_qual) > 1:
        spans = ", ".join(f"{s['qualname']} [{s['start_line']}..{s['end_line']}]" for s in exact_qual)
        raise ValueError(f"Ambiguous symbol '{query}': multiple definitions found ({spans}). Use replace-lines.")
    by_short = [s for s in symbols if s["name"] == query]
    if len(by_short) == 1:
        return by_short[0]
    if len(by_short) > 1:
        spans = ", ".join(f"{s['qualname']} [{s['start_line']}..{s['end_line']}]" for s in by_short)
        raise ValueError(f"Ambiguous symbol '{query}': qualify as ClassName.{query} ({spans}).")
    available = ", ".join(str(s["qualname"]) for s in symbols) or "none"
    raise KeyError(f"Symbol '{query}' not found. Available AST symbols: {available}")


def _read_replacement_payload(code_file: Optional[str], use_stdin: bool) -> str:
    """Read replacement Python code from a file path or standard input.

    Args:
        code_file: Optional path to a file holding replacement code.
        use_stdin: When ``True``, read replacement code from ``sys.stdin``.

    Returns:
        Raw replacement Python source string.

    Raises:
        FileNotFoundError: If ``code_file`` does not exist on disk.
        ValueError: If neither ``--code-file`` nor piped ``--stdin`` is supplied.
    """
    if code_file:
        p = Path(code_file).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Replacement code file does not exist: {p}")
        return p.read_text(encoding="utf-8")
    if use_stdin or not sys.stdin.isatty():
        return sys.stdin.read()
    raise ValueError("Provide replacement Python code via '--code-file <path>' or '--stdin'.")


def _atomic_validate_and_commit(target_path: Path, candidate_source: str, cwd: Path) -> str:
    r"""Compile-check, Ruff-format, and atomically write ``candidate_source``.

    Stages the candidate in a temporary sibling file so ``target_path`` remains
    completely untouched if ``py_compile`` rejects the edit.

    Args:
        target_path: Existing Python module path to replace.
        candidate_source: Full proposed Python source text.
        cwd: Resolved workspace root directory used to locate ``.venv/bin/ruff``.

    Returns:
        Final normalized (and Ruff-formatted, when available) file text.

    Raises:
        SyntaxError: If ``py_compile`` rejects ``candidate_source``.
    """
    # Write the candidate code to a sibling temporary file first so a syntax error
    # never corrupts or truncates the original module on disk.
    cleaned = _normalize_python_whitespace(candidate_source)
    parent = target_path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=f".patch_{target_path.stem}_", suffix=".py", dir=str(parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    # Compile-check the Python file so syntax errors are caught before overwriting.
    try:
        tmp_path.write_text(cleaned, encoding="utf-8")
        # Compile-check the staged file with Python's bytecode compiler before touching `target_path`.
        try:
            py_compile.compile(str(tmp_path), doraise=True)
        except py_compile.PyCompileError as ce:
            raise SyntaxError(f"Replacement rejected due to Python syntax error (target file untouched):\n{ce.msg}") from ce

        # Format the validated temporary file with `ruff format` when installed in `.venv` or PATH.
        ruff_candidates = [cwd / ".venv" / "bin" / "ruff", Path(shutil.which("ruff") or "")]
        for rbin in ruff_candidates:
            if rbin and str(rbin) != "." and rbin.is_file():
                subprocess.run([str(rbin), "format", "--quiet", str(tmp_path)], capture_output=True, text=True, timeout=15)
                break

        # Atomically swap the validated temporary file over `target_path` via POSIX `os.replace`.
        final_text = _normalize_python_whitespace(tmp_path.read_text(encoding="utf-8"))
        tmp_path.write_text(final_text, encoding="utf-8")
        os.replace(tmp_path, target_path)
        return final_text
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


# ============================================================================
# Section 2: Python AST Symbol & Line-Range Patch Subcommands
# ============================================================================

def cmd_inspect(target_path: Path, lines_spec: Optional[str], symbol_query: Optional[str]) -> int:
    r"""Print AST symbol spans and 1-based line-numbered code to stdout.

    Args:
        target_path: Python file to inspect.
        lines_spec: Optional ``START:END`` (or ``START-END``) 1-based slice.
        symbol_query: Optional symbol name to scope the line view.

    Returns:
        Zero exit status on success.

    Raises:
        ValueError: If ``lines_spec`` resolves to an invalid range.
    """
    source = target_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    lines = source.splitlines()
    total = len(lines)

    symbols: List[Dict[str, object]] = []
    syntax_note = "OK"
    try:
        tree = ast.parse(source, filename=str(target_path))
        symbols = _collect_ast_symbols(tree)
    except SyntaxError as se:
        syntax_note = f"SyntaxError at line {se.lineno}: {se.msg}"

    start_line, end_line = 1, max(total, 1)
    if symbol_query:
        matched = _resolve_unique_symbol(symbols, symbol_query)
        start_line = int(matched["start_line"])
        end_line = int(matched["end_line"])
    elif lines_spec:
        parts = lines_spec.replace("-", ":").split(":", 1)
        start_line = max(1, int(parts[0].strip()))
        end_line = min(total, int(parts[1].strip())) if len(parts) > 1 and parts[1].strip() else start_line
        if start_line > end_line:
            raise ValueError(f"Invalid line range '{lines_spec}' (file has {total} lines).")

    print(f"# File: {target_path} ({total} lines, AST: {syntax_note})")
    print("# AST Symbols:")
    if symbols:
        for sym in symbols:
            print(f"#   {sym['kind']} {sym['qualname']}  [lines {sym['start_line']}..{sym['end_line']}]")
    else:
        print("#   (none)")
    print(f"# Showing lines {start_line}..{min(end_line, total)}:")
    for lineno in range(start_line, min(end_line, total) + 1):
        print(f"{lineno:4d} | {lines[lineno - 1]}")
    return 0


def cmd_replace_symbol(target_path: Path, symbol_query: str, replacement_raw: str, cwd: Path) -> int:
    r"""Replace a unique AST symbol span and preserve enclosing class indent.

    Args:
        target_path: Python file to update.
        symbol_query: Unique function or ``Class.method`` name to replace.
        replacement_raw: Replacement Python definition text.
        cwd: Resolved workspace root directory.

    Returns:
        Zero exit status after printing a JSON status summary to stdout.

    Raises:
        ValueError: If ``replacement_raw`` is blank or ``symbol_query`` is ambiguous.
    """
    # Parse the current file and locate the exact 0-based start/end lines and column indent
    # of the target function, method, or class (including any `@decorator` lines above it).
    source = target_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    tree = ast.parse(source, filename=str(target_path))
    symbols = _collect_ast_symbols(tree)
    matched = _resolve_unique_symbol(symbols, symbol_query)
    s_idx = int(matched["start_line"]) - 1
    e_idx = int(matched["end_line"])
    col_offset = int(matched["col_offset"])

    # Dedent the replacement snippet first, then re-indent by `col_offset` spaces so
    # replacing an indented class method (`ClassName.method`) preserves class indentation.
    dedented = textwrap.dedent(replacement_raw.replace("\r\n", "\n")).strip("\n")
    if not dedented.strip():
        raise ValueError("Replacement symbol body cannot be empty.")
    if col_offset > 0:
        pad = " " * col_offset
        indented_lines = [(pad + ln if ln.strip() else "") for ln in dedented.splitlines()]
    else:
        indented_lines = dedented.splitlines()

    # Splice the updated symbol lines into the surrounding file and commit atomically.
    orig_lines = source.splitlines()
    updated_lines = orig_lines[:s_idx] + indented_lines + orig_lines[e_idx:]
    final_text = _atomic_validate_and_commit(target_path, "\n".join(updated_lines) + "\n", cwd)
    ast_warnings = _collect_ast_contract_warnings(target_path, cwd)
    out: Dict[str, object] = {
        "status": "ok",
        "action": "replace-symbol",
        "path": str(target_path),
        "symbol": matched["qualname"],
        "replaced_lines": [s_idx + 1, e_idx],
        "new_total_lines": len(final_text.splitlines()),
    }
    if ast_warnings:
        out["ast_warnings"] = ast_warnings
    print(json.dumps(out))
    return 0


def cmd_replace_lines(
    target_path: Path,
    start_line: int,
    end_line: int,
    replacement_raw: str,
    cwd: Path,
) -> int:
    r"""Replace an inclusive 1-based line range ``[start_line..end_line]``.

    Args:
        target_path: Python file to update.
        start_line: Inclusive 1-based start line number.
        end_line: Inclusive 1-based end line number.
        replacement_raw: Replacement lines (empty string deletes the range).
        cwd: Resolved workspace root directory.

    Returns:
        Zero exit status after printing a JSON status summary to stdout.

    Raises:
        ValueError: If ``start_line..end_line`` is out of bounds.
    """
    source = target_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    orig_lines = source.splitlines()
    total = len(orig_lines)
    if start_line < 1 or end_line < start_line or end_line > max(total, 1):
        raise ValueError(f"Line range {start_line}..{end_line} out of bounds for '{target_path.name}' ({total} lines).")
    rep_lines = replacement_raw.replace("\r\n", "\n").rstrip("\n").splitlines() if replacement_raw.strip("\n") else []
    updated_lines = orig_lines[: start_line - 1] + rep_lines + orig_lines[end_line:]
    final_text = _atomic_validate_and_commit(target_path, "\n".join(updated_lines) + "\n", cwd)
    ast_warnings = _collect_ast_contract_warnings(target_path, cwd)
    out_ln: Dict[str, object] = {
        "status": "ok",
        "action": "replace-lines",
        "path": str(target_path),
        "replaced_lines": [start_line, end_line],
        "new_total_lines": len(final_text.splitlines()),
    }
    if ast_warnings:
        out_ln["ast_warnings"] = ast_warnings
    print(json.dumps(out_ln))
    return 0


# ============================================================================
# Section 3: RFC 6901 JSON Pointer Patcher & CLI Entrypoint
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for ``code_patch.py``.

    Returns:
        Configured ``argparse.ArgumentParser`` with ``inspect``,
        ``replace-symbol``, and ``replace-lines`` subcommands.
    """
    parser = argparse.ArgumentParser(description="AST symbol and line-range Python code patcher for Facet.")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_ins = sub.add_parser("inspect", help="Inspect AST symbol spans and 1-based line-numbered code.")
    p_ins.add_argument("file", type=str)
    p_ins.add_argument("--lines", type=str, default=None, help="1-based line span START:END.")
    p_ins.add_argument("--symbol", type=str, default=None, help="Function or Class.method name.")

    p_sym = sub.add_parser("replace-symbol", help="Replace a unique Python AST function or class definition.")
    p_sym.add_argument("file", type=str)
    p_sym.add_argument("--symbol", type=str, required=True)
    p_sym.add_argument("--code-file", type=str, default=None)
    p_sym.add_argument("--stdin", action="store_true")

    p_ln = sub.add_parser("replace-lines", help="Replace 1-based inclusive line range [START..END].")
    p_ln.add_argument("file", type=str)
    p_ln.add_argument("--start", type=int, required=True)
    p_ln.add_argument("--end", type=int, required=True)
    p_ln.add_argument("--code-file", type=str, default=None)
    p_ln.add_argument("--stdin", action="store_true")

    p_js = sub.add_parser("replace-json", help="Replace an RFC 6901 JSON Pointer target inside a .json file.")
    p_js.add_argument("file", type=str)
    p_js.add_argument("--pointer", type=str, required=True, help="JSON Pointer (e.g. /concept_name or /verification_obligations/0/expected).")
    p_js.add_argument("--value", type=str, default=None, help="Inline JSON value literal.")
    p_js.add_argument("--code-file", type=str, default=None)
    p_js.add_argument("--stdin", action="store_true")
    return parser


def cmd_replace_json(target_path: Path, pointer: str, raw_value: str) -> int:
    r"""Replace an RFC 6901 JSON Pointer field or array index atomically.

    Args:
        target_path: JSON file on disk to patch.
        pointer: RFC 6901 JSON Pointer starting with ``/`` (e.g. ``/concept_name``).
        raw_value: Valid JSON literal string representing the replacement value.

    Returns:
        Zero exit status after writing formatted JSON and printing a status object.

    Raises:
        ValueError: If ``pointer`` or ``raw_value`` is invalid or unresolvable.
    """
    # Decode RFC 6901 escape sequences (`~1` -> `/`, `~0` -> `~`) and walk down to
    # the parent dict or list right above the target leaf key/index.
    if not pointer.startswith("/") or len(pointer) < 2:
        raise ValueError(f"JSON Pointer must start with '/' and name at least one segment (got {pointer!r}).")
    doc = json.loads(target_path.read_text(encoding="utf-8"))
    new_val = json.loads(raw_value)
    tokens = [seg.replace("~1", "/").replace("~0", "~") for seg in pointer[1:].split("/")]
    cur: object = doc
    for seg in tokens[:-1]:
        if isinstance(cur, list):
            cur = cur[int(seg)]
        elif isinstance(cur, dict):
            if seg not in cur:
                raise KeyError(f"JSON Pointer segment '{seg}' not found.")
            cur = cur[seg]
        else:
            raise ValueError(f"Cannot traverse scalar at segment '{seg}'.")
    # Update the leaf slot (supporting list append when `idx == len(cur)`) and write atomically.
    leaf = tokens[-1]
    if isinstance(cur, list):
        idx = int(leaf)
        if idx == len(cur):
            cur.append(new_val)
        else:
            cur[idx] = new_val
    elif isinstance(cur, dict):
        cur[leaf] = new_val
    else:
        raise ValueError(f"Cannot assign JSON Pointer leaf '{leaf}' on scalar parent.")

    rendered = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".patch_{target_path.stem}_", suffix=".json", dir=str(target_path.parent))
    os.close(fd)
    tmp_p = Path(tmp_name)
    # Write temporary file and atomically replace target path on disk.
    try:
        tmp_p.write_text(rendered, encoding="utf-8")
        os.replace(tmp_p, target_path)
    finally:
        if tmp_p.exists():
            tmp_p.unlink(missing_ok=True)
    print(json.dumps({"status": "ok", "action": "replace-json", "path": str(target_path), "pointer": pointer}))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Validate workspace containment and dispatch ``code_patch.py`` subcommands.

    Args:
        argv: Optional argument list overriding ``sys.argv[1:]``.

    Returns:
        Zero on success, or ``1`` after writing a structured JSON error to stderr.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    cwd = Path.cwd().resolve()
    raw_file_arg = Path(args.file)
    target_path = raw_file_arg.expanduser().resolve()

    try:
        if ".." in raw_file_arg.parts:
            raise PermissionError(f"Path traversal ('..') is prohibited in code_patch.py target '{args.file}'.")
        if not raw_file_arg.is_absolute():
            try:
                target_path.relative_to(cwd)
            except ValueError as ve:
                raise PermissionError(f"Relative path '{args.file}' escapes workspace '{cwd}'.") from ve
        if not target_path.is_file():
            raise FileNotFoundError(f"Target file does not exist: {target_path}")
        if args.subcommand == "replace-json":
            if target_path.suffix != ".json":
                raise ValueError(f"replace-json only operates on '.json' files (got '{target_path.name}').")
            raw_val = args.value if args.value is not None else _read_replacement_payload(args.code_file, args.stdin)
            return cmd_replace_json(target_path, args.pointer, raw_val)
        if target_path.suffix != ".py":
            raise ValueError(f"code_patch.py only operates on '.py' files (got '{target_path.name}').")

        # Dispatch CLI action `args.subcommand == 'inspect'` to its dedicated handler.
        if args.subcommand == "inspect":
            return cmd_inspect(target_path, args.lines, args.symbol)
        if args.subcommand == "replace-symbol":
            payload = _read_replacement_payload(args.code_file, args.stdin)
            return cmd_replace_symbol(target_path, args.symbol, payload, cwd)
        # Dispatch CLI action `args.subcommand == 'replace-lines'` to its dedicated handler.
        if args.subcommand == "replace-lines":
            payload = _read_replacement_payload(args.code_file, args.stdin)
            return cmd_replace_lines(target_path, args.start, args.end, payload, cwd)
        return 1
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc), "path": str(target_path)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
