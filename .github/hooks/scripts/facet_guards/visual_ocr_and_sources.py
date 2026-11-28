"""Approved research domain registry, `/add-source`, input discovery, Stage 0V plate assembly, and epistemic XML provenance.

Function Map:
  - Section 1: Workspace Corpus & Explicit Asset Discovery (`discover_research_inputs`, `resolve_initial_active_stage`)
  - Section 2: 3-Layer Approved Research Domain Registry, MediaWiki API Canonicalizer & `/add-source` (`load_approved_research_sources`, `is_approved_research_url`, `canonicalize_wikipedia_fetch_url`)
  - Section 3: Stage 0V Per-Plate OCR Staging, Completeness Gate & Deterministic `SOURCE_OCR.md` Assembly (`_load_required_ocr_plates`, `_assemble_source_ocr_from_plates`)
  - Section 4: `<uncertain>` / `<illegible/>` Transcriptional Uncertainty XML Parser & Epistemic Extraction (`parse_and_normalize_tei_provenance`, `detect_ocr_uncertainties`)
  - Section 5: Epistemic Working-Set Scope Resolver & Mosaic Composition Seam (`compute_stage_working_set`, `check_path_in_working_set`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import base64
import datetime
import ipaddress
import json
from pathlib import Path
import re
import shutil
import urllib.parse
import urllib.request

from .constants_and_context import (
    APPROVED_SOURCES_FILENAME,
    CONFIG_FILE_NAME,
    DEFAULT_CONFIG,
    FSM_FILE_NAME,
    HOOKS_DIR,
    SCRATCH_DIR_NAME,
    SPEC_AND_REPORT_FILES,
    get_copilot_home,
)
from .state_locks_and_seals import (
    _prune_empty_scratch_dir,
    atomic_write_text,
    call_systemone_decision_model,
    load_pipeline_config,
    load_pipeline_fsm,
)
from .text_ast_and_patch import (
    _strip_markdown_code_spans,
    sanitize_markdown_artifact_text,
)

# ============================================================================
# Section 1: Workspace Corpus & Explicit Asset Discovery
# Discovers `<cwd>/research/` documents and explicitly named workspace assets to route initial pipeline entry stages.
# ============================================================================

def _has_non_readme_sibling_assets(folder: Path) -> bool:
    r"""Return ``True`` when ``folder`` contains at least one non-hidden specification/source asset besides ``README.md``."""
    if not folder.is_dir():
        return False
    return any(
        sib.is_file() and not sib.name.startswith(".") and sib.name.lower() != "readme.md"
        for sib in folder.iterdir()
    )


def _extract_explicit_workspace_source_paths(cwd: Path, user_request: str) -> tuple[list[str], list[str]]:
    r"""Resolve explicitly user-referenced workspace files/folders when ``dual_tier_explicit_promotion`` source ingress is active.

    Ignores repository UI assets (``docs/img/``), ``.git/``, ``.venv/``, and
    ``specs/`` unless the user explicitly wrote that exact relative path, and
    enforces strict ``cwd`` containment against path traversal.

    Args:
        cwd: Workspace root directory.
        user_request: Raw user prompt or ``original_user_request`` text.

    Returns:
        Tuple ``(promoted_ocr_files, promoted_text_files)`` of workspace-relative POSIX paths.
    """
    # Extract workspace-contained file or folder paths explicitly named in `user_request`.
    req_txt = str(user_request or "").strip()
    if not req_txt:
        return ([], [])
    cwd_res = Path(cwd).resolve()
    ocr_exts = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".svg"}
    text_exts = {".md", ".txt", ".tex", ".rst", ".bib", ".json", ".csv", ".yaml", ".yml"}
    found_ocr: list[str] = []
    found_txt: list[str] = []

    explicit_file_tokens = re.findall(r"[A-Za-z0-9_./\-]+\.[A-Za-z0-9]{2,5}", req_txt)
    explicit_files_resolved: set[Path] = set()
    file_scoped_example_dirs: set[str] = set()
    for f_tok in explicit_file_tokens:
        if "://" in f_tok or f_tok.startswith("/"):
            continue
        f_cand = (cwd_res / f_tok).resolve()
        try:
            f_rel = f_cand.relative_to(cwd_res)
        except ValueError:
            continue
        if f_cand.is_file():
            explicit_files_resolved.add(f_cand)
            if len(f_rel.parts) >= 2 and f_rel.parts[0] == "examples":
                file_scoped_example_dirs.add(f"examples/{f_rel.parts[1]}")

    raw_tokens = list(explicit_file_tokens)
    # Match folder-level `examples/<slug>` mentions ONLY when no trailing `/filename` is attached and no specific file in that folder was already named.
    for dir_tok in re.findall(r"\bexamples/[A-Za-z0-9_.\-]+(?!/[A-Za-z0-9_.\-])", req_txt):
        norm_dir_tok = dir_tok.rstrip("/")
        if norm_dir_tok in file_scoped_example_dirs:
            continue
        cand_dir = (cwd_res / norm_dir_tok).resolve()
        try:
            cand_dir.relative_to(cwd_res)
        except ValueError:
            continue
        if cand_dir.is_dir():
            for sp in sorted(cand_dir.rglob("*")):
                if sp.is_file():
                    raw_tokens.append(str(sp.relative_to(cwd_res)).replace("\\", "/"))
    # Also support explicit `examples` folder mention when paired with a subfolder keyword (e.g. `magical mystery`) when no specific file in that subfolder was named.
    if re.search(r"\bexamples\b", req_txt, re.IGNORECASE) and (cwd_res / "examples").is_dir():
        req_low = req_txt.lower()
        for sub in sorted((cwd_res / "examples").iterdir()):
            ex_key = f"examples/{sub.name}"
            if ex_key in file_scoped_example_dirs:
                continue
            if sub.is_dir() and (sub.name.lower() in req_low or sub.name.replace("_", " ").lower() in req_low):
                for sp in sorted(sub.rglob("*")):
                    if sp.is_file():
                        raw_tokens.append(str(sp.relative_to(cwd_res)).replace("\\", "/"))

    for tok in raw_tokens:
        if "://" in tok or tok.startswith("/"):
            continue
        cand = (cwd_res / tok).resolve()
        try:
            rel_p = cand.relative_to(cwd_res)
        except ValueError:
            continue
        if not cand.is_file():
            continue
        parts = rel_p.parts
        if any(p.startswith(".") for p in parts) or (parts and parts[0] in {".git", ".venv", "specs", "benchmarks", "scripts", "modelfiles"}):
            continue
        if len(parts) >= 2 and parts[0] == "docs" and parts[1] == "img" and "docs/img" not in req_txt:
            continue
        # Exclude human showcase `examples/<slug>/README.md` when real source/spec assets exist in that folder (unless explicitly named as a file).
        if (
            len(parts) >= 2
            and parts[0] == "examples"
            and cand.name.lower() == "readme.md"
            and cand not in explicit_files_resolved
            and _has_non_readme_sibling_assets(cand.parent)
        ):
            continue
        rel_s = str(rel_p).replace("\\", "/")
        ext = cand.suffix.lower()
        if ext in ocr_exts and rel_s not in found_ocr:
            found_ocr.append(rel_s)
        elif ext in text_exts and (len(parts) >= 2 or cand.name not in SPEC_AND_REPORT_FILES) and rel_s not in found_txt:
            found_txt.append(rel_s)
    return (found_ocr, found_txt)


def _format_prepare_visual_flag(rel_path: str) -> str:
    """Return the exact ``prepare_visual_sources.py`` CLI flag (``--pdf``, ``--html``, or ``--image``) for ``rel_path``.

    Args:
        rel_path: Workspace-relative POSIX path to a visual, PDF, or HTML source file.

    Returns:
        Formatted ``"--pdf <path>"``, ``"--html <path>"``, or ``"--image <path>"`` argument string.
    """
    # Map file extensions onto `prepare_visual_sources.py`'s whitelisted CLI flags.
    ext = Path(rel_path).suffix.lower()
    if ext == ".pdf":
        return f"--pdf {rel_path}"
    if ext in (".html", ".htm"):
        return f"--html {rel_path}"
    return f"--image {rel_path}"


def _format_stage_ocr_cmd_args(cwd: Path, ocr_files: list[str], cfg: dict | None = None) -> str:
    """Return exact ``prepare_visual_sources.py`` CLI arguments for ``ocr_files``."""
    eff_cfg = cfg if isinstance(cfg, dict) else load_pipeline_config(cwd)
    raw_mvp = eff_cfg.get("max_visual_pages", 32) if isinstance(eff_cfg, dict) else 32
    mvp_flag = f" --max-pages {int(raw_mvp)}" if isinstance(raw_mvp, int) and raw_mvp != 32 and raw_mvp >= 0 else ""
    if ocr_files and all(p.startswith("research/") for p in ocr_files) and (Path(cwd) / "research").is_dir():
        return f"--dir research{mvp_flag} --out-dir .pipeline_scratch/ocr_staging"
    if ocr_files:
        in_flags = " ".join(_format_prepare_visual_flag(p) for p in ocr_files[:4])
        return f"{in_flags}{mvp_flag} --out-dir .pipeline_scratch/ocr_staging"
    return f"--dir research{mvp_flag} --out-dir .pipeline_scratch/ocr_staging"


def discover_research_inputs(cwd, user_request=None, cfg=None, attachments=None):
    """Discover user-supplied reference documents inside ``<cwd>/research/`` (and optional explicitly referenced workspace paths).

    Classifies non-hidden files under ``research/`` into visual/PDF sources
    requiring Stage 0V ``@Scribe`` OCR (``SOURCE_OCR.md``) versus plain-text
    reference documents readable directly by ``@Researcher``. When experiment
    ``experiments.source_ingress_mode`` is set to
    ``"dual_tier_explicit_promotion"``, also includes workspace-contained paths
    explicitly named in ``user_request``.

    Args:
        cwd: Workspace root directory.
        user_request: Optional user request string (defaults to FSM ``original_user_request``).
        cfg: Optional merged pipeline configuration dictionary.

    Returns:
        Dictionary with ``exists`` (bool), ``ocr_files`` (sorted list of
        workspace-relative POSIX paths), and ``text_files`` (sorted list of
        workspace-relative POSIX paths).
    """
    # Scan `<cwd>/research/` and split user reference files into visual/PDF assets (needing Stage 0V `@Scribe` OCR) vs. plain-text files.
    cwd = Path(cwd)
    eff_cfg = cfg if isinstance(cfg, dict) else load_pipeline_config(cwd)
    ingress_mode = str(((eff_cfg.get("experiments") or {}) if isinstance(eff_cfg, dict) else {}).get("source_ingress_mode") or "dual_tier_explicit_promotion")
    eff_req = user_request
    if eff_req is None and ingress_mode == "dual_tier_explicit_promotion" and (cwd / FSM_FILE_NAME).is_file():
        eff_req = str(load_pipeline_fsm(cwd).get("original_user_request") or "")
    att_tokens = ""
    if ingress_mode == "dual_tier_explicit_promotion":
        eff_att = attachments
        if eff_att is None and (cwd / FSM_FILE_NAME).is_file():
            eff_att = load_pipeline_fsm(cwd).get("attached_source_paths")
        if isinstance(eff_att, list) and eff_att:
            att_tokens = " " + " ".join(str(x) for x in eff_att if x)
    promo_ocr, promo_txt = (
        _extract_explicit_workspace_source_paths(cwd, f"{eff_req or ''}{att_tokens}".strip())
        if ingress_mode == "dual_tier_explicit_promotion"
        else ([], [])
    )
    research_dir = cwd / "research"
    if not research_dir.is_dir():
        return {
            "exists": bool(promo_ocr or promo_txt),
            "ocr_files": promo_ocr,
            "text_files": promo_txt,
        }

    ocr_exts = {
        ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".svg"
    }
    text_exts = {
        ".md", ".txt", ".tex", ".rst", ".bib", ".json", ".csv", ".yaml", ".yml"
    }
    ocr_files = []
    text_files = []
    for p in sorted(research_dir.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel_parts = p.relative_to(research_dir).parts
            # Ignore hidden dot-files.
            if any(part.startswith(".") for part in rel_parts):
                continue
            rel_posix = str(p.relative_to(cwd)).replace("\\", "/")
        except ValueError:
            continue
        ext = p.suffix.lower()
        if ext in ocr_exts:
            ocr_files.append(rel_posix)
        elif ext in (".html", ".htm"):
            try:
                sample = p.read_text(encoding="utf-8", errors="replace")[:32768].lower()
            except Exception:
                sample = ""
            if any(tok in sample for tok in ("<img", "<svg", "<picture", ".pdf", "data:image/")):
                ocr_files.append(rel_posix)
            else:
                text_files.append(rel_posix)
        elif ext in text_exts:
            text_files.append(rel_posix)
    for po in promo_ocr:
        if po not in ocr_files:
            ocr_files.append(po)
    for pt in promo_txt:
        if pt not in text_files:
            text_files.append(pt)
    return {
        "exists": True,
        "ocr_files": ocr_files,
        "text_files": text_files,
    }


# ============================================================================
# Section 2: 3-Layer Approved Research Domain Registry, MediaWiki API Canonicalizer & `/add-source`
# Merges seed, user, and workspace domain allowlists and rewrites Wikipedia `/wiki/` URLs to redirect-safe API queries.
# ============================================================================

def normalize_research_domain(raw_entry):
    """Normalize a domain or URL into a validated public DNS hostname.

    Rejects wildcards, embedded credentials, ``localhost``, ``.local`` hosts,
    single-label hostnames, and raw IPv4/IPv6 literals.

    Args:
        raw_entry: Raw domain name or HTTP(S) URL string.

    Returns:
        Lowercase validated hostname, or ``None`` if invalid or unsafe.
    """
    # Extract and lowercase the hostname from a domain or URL, rejecting raw IP addresses to prevent SSRF bypasses.
    if not raw_entry or not isinstance(raw_entry, str):
        return None
    candidate = raw_entry.strip().strip("'\"`<>.,;")
    if not candidate or any(ch.isspace() for ch in candidate) or "*" in candidate or "@" in candidate:
        return None
    if "://" in candidate:
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            return None
        host = (parsed.hostname or "").lower().strip(".")
    else:
        parsed = urllib.parse.urlparse(f"https://{candidate}")
        if parsed.username or parsed.password:
            return None
        host = (parsed.hostname or "").lower().strip(".")
    if not host or "." not in host or host == "localhost" or host.endswith(".local"):
        return None
    try:
        ipaddress.ip_address(host)
        return None  # Reject raw IPv4/IPv6 literals to preserve SSRF & DNS allowlist guarantees
    except ValueError:
        pass
    if not re.match(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", host):
        return None
    return host


def load_approved_research_sources(cwd, user_local_file=None):
    """Merge approved research domains across the 3-layer source registry.

    Layers merged in order:
      1. Base registry (``.github/hooks/approved_sources.json`` or user scope).
      2. User-added persistent sources (``approved_sources.local.json``).
      3. Workspace config (``.pipeline_config.json`` ``approved_research_domains``).

    Args:
        cwd: Workspace root directory.
        user_local_file: Optional path overriding ``approved_sources.local.json``.

    Returns:
        Dict with sorted ``"domains"`` list and ``"hints"`` endpoint map.
    """
    # Combine built-in academic domains (`approved_sources.json`), user-saved domains (`approved_sources.local.json`), and workspace config domains.
    domains_set = set()
    hints = {}

    copilot_home = get_copilot_home()
    candidate_base_files = [
        Path(cwd) / ".github" / "hooks" / APPROVED_SOURCES_FILENAME,
        HOOKS_DIR / APPROVED_SOURCES_FILENAME,
        copilot_home / "hooks" / APPROVED_SOURCES_FILENAME,
    ]
    for base_path in candidate_base_files:
        if base_path.exists():
            try:
                raw = json.loads(base_path.read_text(encoding="utf-8"))
                entries = raw.get("sources", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                for item in entries:
                    if isinstance(item, dict):
                        dom = normalize_research_domain(item.get("domain", ""))
                        if dom:
                            domains_set.add(dom)
                            hint = item.get("fetch_hint")
                            if isinstance(hint, str) and hint.strip():
                                hints[dom] = hint.strip()
                    elif isinstance(item, str):
                        dom = normalize_research_domain(item)
                        if dom:
                            domains_set.add(dom)
                break  # Stop after loading highest-precedence base registry
            except Exception:
                pass

    local_path = Path(user_local_file) if user_local_file is not None else (copilot_home / "approved_sources.local.json")
    if local_path.exists():
        try:
            local_raw = json.loads(local_path.read_text(encoding="utf-8"))
            local_entries = (
                local_raw.get("sources", [])
                if isinstance(local_raw, dict)
                else (local_raw if isinstance(local_raw, list) else [])
            )
            for item in local_entries:
                val = item.get("domain", "") if isinstance(item, dict) else item
                dom = normalize_research_domain(val)
                if dom:
                    domains_set.add(dom)
                    if isinstance(item, dict) and isinstance(item.get("fetch_hint"), str) and item["fetch_hint"].strip():
                        hints[dom] = item["fetch_hint"].strip()
        except Exception:
            pass

    # Load `.pipeline_config.json` (falling back to `DEFAULT_CONFIG` when absent).
    cfg = load_pipeline_config(Path(cwd))
    for raw_dom in cfg.get("approved_research_domains", []):
        dom = normalize_research_domain(raw_dom)
        if dom:
            domains_set.add(dom)

    return {"domains": sorted(domains_set), "hints": hints}


def canonicalize_wikipedia_fetch_url(url: str) -> str:
    r"""Rewrite raw ``/wiki/<Title>`` Wikipedia URLs to MediaWiki's redirect + search API.

    Prevents HTTP 404 failures on slightly variant article titles (such as compound mathematical topic names) by querying ``w/api.php`` with
    ``redirects=1`` and ``list=search`` fallback in a single HTTP 200 JSON call.

    Args:
        url: Candidate URL passed to ``web_fetch``.

    Returns:
        Canonical MediaWiki Action API URL when ``url`` is an article ``/wiki/``
        link, otherwise ``url`` unchanged.
    """
    if not isinstance(url, str):
        return url
    m_wiki = re.match(r"^https?://([a-z]{2,3})\.wikipedia\.org/wiki/([^?#\s]+)$", url.strip())
    if not m_wiki:
        return url
    lang, raw_slug = m_wiki.group(1), m_wiki.group(2)
    decoded_title = urllib.parse.unquote(raw_slug).strip()
    if not decoded_title or ":" in decoded_title:
        return url
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "redirects": "1",
            "list": "search",
            "srsearch": decoded_title.replace("_", " "),
            "srlimit": "5",
            "formatversion": "2",
            "format": "json",
            "titles": decoded_title.replace(" ", "_"),
        }
    )
    return f"https://{lang}.wikipedia.org/w/api.php?{query}"


def is_approved_research_url(url, approved_domains):
    """Check whether ``url`` targets an approved HTTP(S) domain.

    Args:
        url: Candidate URL string.
        approved_domains: Collection of normalized allowed base domains.

    Returns:
        ``True`` when ``url`` uses HTTP(S) without embedded credentials and
        matches or is a subdomain of an entry in ``approved_domains``.
    """
    # Verify HTTP(S) scheme, reject embedded `user:pass@` URL credentials, and match the host or parent domain against `approved_domains`.
    if not url or not isinstance(url, str):
        return False
    raw = url.strip()
    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception:
        return False
    if parsed.scheme not in ("https", "http") or parsed.username or parsed.password or "@" in parsed.netloc:
        return False
    host = normalize_research_domain(parsed.hostname or "")
    if not host:
        return False
    return any(host == dom or host.endswith(f".{dom}") for dom in approved_domains)


def parse_add_source_command(prompt_text):
    r"""Parse ``/add-source [--list] [--workspace] [domain_or_url ...]``.

    Args:
        prompt_text: Raw user prompt string.

    Returns:
        Parsed command dict with ``scope``, ``list_only``, ``valid``, and
        ``invalid`` keys, or ``None`` if ``prompt_text`` is not ``/add-source``.
    """
    # Parse `/add-source [--workspace] [--list] <domains...>` into validated and rejected domain lists.
    if not isinstance(prompt_text, str):
        return None
    stripped = prompt_text.strip()
    lower = stripped.lower()
    if lower != "/add-source" and not lower.startswith("/add-source "):
        return None
    rest = stripped[len("/add-source"):].strip()
    tokens = [t for t in re.split(r"[\s,]+", rest) if t]
    scope = "user"
    list_only = False
    raw_targets = []
    for tok in tokens:
        if tok == "--workspace":
            scope = "workspace"
        elif tok in ("--list", "-l"):
            list_only = True
        else:
            raw_targets.append(tok)
    if not raw_targets:
        list_only = True
    valid = []
    invalid = []
    for raw_t in raw_targets:
        norm = normalize_research_domain(raw_t)
        if norm:
            if norm not in valid:
                valid.append(norm)
        else:
            invalid.append(raw_t)
    return {
        "scope": scope,
        "list_only": list_only,
        "valid": valid,
        "invalid": invalid
    }


def execute_add_source_command(parsed_cmd, cwd, user_local_file=None):
    """Persist validated domains from ``/add-source`` atomically.

    Args:
        parsed_cmd: Parsed output from ``parse_add_source_command()``.
        cwd: Workspace root directory.
        user_local_file: Optional override path for user-scoped source list.

    Returns:
        Summary dictionary with ``added``, ``already_present``, ``invalid``,
        and merged ``domains`` lists.
    """
    # Save newly approved domains atomically into either `.pipeline_config.json` (`--workspace`) or `~/.copilot/approved_sources.local.json`.
    cwd_path = Path(cwd)
    if not parsed_cmd or parsed_cmd.get("list_only") or not parsed_cmd.get("valid"):
        merged = load_approved_research_sources(cwd_path, user_local_file=user_local_file)
        return {"added": [], "already_present": [], "invalid": parsed_cmd.get("invalid", []) if parsed_cmd else [], "domains": merged["domains"]}

    before = set(load_approved_research_sources(cwd_path, user_local_file=user_local_file)["domains"])
    added = []
    already = []

    if parsed_cmd["scope"] == "workspace":
        cfg_path = cwd_path / CONFIG_FILE_NAME
        raw_cfg = {}
        if cfg_path.exists():
            try:
                loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw_cfg = loaded
            except Exception:
                raw_cfg = {}
        if not raw_cfg:
            raw_cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        existing_ws: list[str] = []
        for raw_d in raw_cfg.get("approved_research_domains", []):
            norm_d = normalize_research_domain(raw_d)
            if norm_d:
                existing_ws.append(norm_d)
        for dom in parsed_cmd["valid"]:
            if dom in before:
                already.append(dom)
            else:
                added.append(dom)
            if dom not in existing_ws:
                existing_ws.append(dom)
        raw_cfg["approved_research_domains"] = sorted(set(existing_ws))
        atomic_write_text(cfg_path, json.dumps(raw_cfg, indent=2))
    else:
        local_path = Path(user_local_file) if user_local_file is not None else (get_copilot_home() / "approved_sources.local.json")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_data = {"version": 1, "sources": []}
        if local_path.exists():
            try:
                loaded = json.loads(local_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("sources"), list):
                    local_data = loaded
            except Exception:
                pass
        existing_local = set()
        cleaned_sources = []
        for entry in local_data.get("sources", []):
            dom = normalize_research_domain(entry.get("domain", "") if isinstance(entry, dict) else entry)
            if dom and dom not in existing_local:
                existing_local.add(dom)
                cleaned_sources.append(entry if isinstance(entry, dict) else {"domain": dom})
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for dom in parsed_cmd["valid"]:
            if dom in before:
                already.append(dom)
            else:
                added.append(dom)
            if dom not in existing_local:
                existing_local.add(dom)
                cleaned_sources.append({"domain": dom, "added_at": now_iso})
        local_data["sources"] = cleaned_sources
        atomic_write_text(local_path, json.dumps(local_data, indent=2))

    merged_after = load_approved_research_sources(cwd_path, user_local_file=user_local_file)
    return {
        "added": added,
        "already_present": already,
        "invalid": parsed_cmd.get("invalid", []),
        "domains": merged_after["domains"]
    }


# ============================================================================
# Section 3: Stage 0V Per-Plate OCR Staging, Completeness Gate & Deterministic `SOURCE_OCR.md` Assembly
# Tracks 1-plate-at-a-time `@Scribe` inspection and deterministically assembles validated `plates/<stem>.md` transcripts.
# ============================================================================

def clean_ocr_staging(cwd):
    """Remove temporary ``ocr_staging/`` renders after Stage 0V finishes.

    Args:
        cwd: Workspace root directory.
    """
    # Delete temporary 180 DPI page plates in `.pipeline_scratch/ocr_staging/` once `@Scribe` finishes `SOURCE_OCR.md`.
    staging_dir = cwd / SCRATCH_DIR_NAME / "ocr_staging"
    if staging_dir.exists():
        try:
            shutil.rmtree(staging_dir)
        except Exception:
            pass
    _prune_empty_scratch_dir(cwd)


def resolve_initial_active_stage(
    cwd: Path,
    cfg: dict[str, object] | None = None,
    user_request: str = "",
) -> str:
    """Resolve the entry pipeline stage from ``PRE_RUN_HALT`` based on modality and closed-book mode.

    Skips redundant ``STAGE_0_IDEATION`` (``RESEARCH.md``) and enters
    ``STAGE_1_SPECIFICATION`` directly when ``allow_web_research`` is ``False``
    and no visual/PDF ``research/`` or prompt-promoted ``examples/`` files
    require Stage 0V ``@Scribe`` OCR.

    Args:
        cwd: Workspace root directory.
        cfg: Optional pre-loaded pipeline configuration dictionary.
        user_request: Optional user prompt text used for explicit ``examples/`` promotion.

    Returns:
        ``"STAGE_1_SPECIFICATION"`` for closed-book text-only Path A, else ``"STAGE_0_IDEATION"``.
    """
    eff_cfg = cfg if isinstance(cfg, dict) else load_pipeline_config(cwd)
    eff_req = str(user_request or "").strip()
    if not eff_req and (Path(cwd) / FSM_FILE_NAME).is_file():
        eff_req = str(load_pipeline_fsm(cwd).get("original_user_request") or "").strip()
    res_in = discover_research_inputs(cwd, user_request=eff_req, cfg=eff_cfg)
    if (
        not bool(eff_cfg.get("allow_web_research", True))
        and not res_in["ocr_files"]
        and not (Path(cwd) / "RESEARCH.md").is_file()
    ):
        return "STAGE_1_SPECIFICATION"
    return "STAGE_0_IDEATION"


def _is_markovian_stage1_with_research_md(cwd: Path, stage: str = "") -> bool:
    """Return ``True`` when Stage 1 ``manifest.json`` extraction runs downstream of an existing ``RESEARCH.md``."""
    cwd = Path(cwd)
    if not (cwd / "RESEARCH.md").is_file():
        return False
    eff_stage = str(stage or "").strip()
    if not eff_stage and (cwd / FSM_FILE_NAME).is_file():
        eff_stage = str(load_pipeline_fsm(cwd).get("current_stage") or "")
    return eff_stage in ("STAGE_1_SPECIFICATION", "STAGE_1_CRITIC", "CHECKPOINT_1_AWAITING_APPROVAL") or (cwd / "manifest.json").is_file()


def _has_post_research_stage1_ocr(cwd: Path) -> bool:
    """Return ``True`` only when ``SOURCE_OCR.md`` was freshly created after ``RESEARCH.md`` via Stage 1 visual escalation."""
    cwd = Path(cwd)
    r_p = cwd / "RESEARCH.md"
    o_p = cwd / "SOURCE_OCR.md"
    if not (r_p.is_file() and o_p.is_file()):
        return False
    try:
        return o_p.stat().st_mtime > r_p.stat().st_mtime + 0.05
    except Exception:
        return False


def filter_decorative_ocr_inputs(
    cwd: Path, ocr_files: list[str], cfg: dict[str, object] | None = None
) -> list[str]:
    """Filter purely decorative raster icons/logos from ``research/`` before Stage 0V ``@Scribe`` OCR.

    Preserves all ``.pdf`` and ``.svg`` documents unconditionally and uses
    ``pplx-decider-flash`` multimodal ``noul`` (`keep_alive: 0`) only on raster
    images ``<= 2 MB``, skipping OCR only when ``contains_technical_content < 0.15``.

    Args:
        cwd: Workspace root directory.
        ocr_files: Candidate workspace-relative visual/PDF paths from ``discover_research_inputs``.
        cfg: Optional merged pipeline configuration dictionary.

    Returns:
        Filtered list of ``ocr_files`` requiring ``@Scribe`` transcription.
    """
    # Use `pplx-decider-flash` multimodal `noul` (`keep_alive=0`) to skip purely decorative logos/icons in `research/` while keeping all PDFs/SVGs/equations.
    _ = cfg
    if not ocr_files:
        return []
    kept: list[str] = []
    raster_exts = {".png", ".jpg", ".jpeg", ".webp"}
    for rel in ocr_files:
        fp = Path(cwd) / rel
        if not fp.is_file() or fp.suffix.lower() not in raster_exts or fp.stat().st_size > 2_000_000:
            kept.append(rel)
            continue
        try:
            img_b64 = base64.b64encode(fp.read_bytes()).decode("ascii")
        except Exception:
            kept.append(rel)
            continue
        # Query `pplx-decider-flash` (`keep_alive=0`) via `/v1/systemone` for fast boundary triage.
        answers = call_systemone_decision_model(
            cwd,
            f"Inspect image '{rel}' in research/.",
            {
                "contains_technical_content": {
                    "type": "noul",
                    "instructions": "Does this image contain mathematical equations, scientific plots, tables, diagrams, or text needed for specification?",
                    "criteria": {
                        "true": "Contains equations, diagrams, tables, plots, or technical text.",
                        "false": "Purely decorative logo, icon, or wallpaper with zero technical content.",
                    },
                }
            },
            images=[img_b64],
        )
        if not isinstance(answers, dict):
            kept.append(rel)
            continue
        tech_p = float((answers.get("contains_technical_content") or {}).get("noul") or 1.0)  # type: ignore[union-attr]
        if tech_p >= 0.15:
            kept.append(rel)
    return kept


def _load_required_ocr_plates(cwd: Path) -> list[Path]:
    r"""Return ordered resolved visual plate paths from ``staging_manifest.json`` when present."""
    man_f = cwd / SCRATCH_DIR_NAME / "ocr_staging" / "staging_manifest.json"
    if not man_f.is_file():
        return []
    seen: set[str] = set()
    ordered: list[Path] = []
    try:
        man_j = json.loads(man_f.read_text(encoding="utf-8"))
        raw_cands: list[object] = []
        if isinstance(man_j.get("staged_images"), list):
            raw_cands.extend(man_j["staged_images"])
        for s_ent in (man_j.get("sources") or []):
            if isinstance(s_ent, dict) and isinstance(s_ent.get("staged_images"), list):
                raw_cands.extend(s_ent["staged_images"])
        for img_p in raw_cands:
            p_obj = Path(str(img_p))
            if p_obj.is_file():
                res_p = p_obj.resolve()
                key = str(res_p)
                if key not in seen:
                    seen.add(key)
                    ordered.append(res_p)
    except Exception:
        return []
    return ordered


def _ocr_plates_dir(cwd: Path) -> Path:
    r"""Return the isolated per-plate Stage 0V Markdown staging directory."""
    return cwd / SCRATCH_DIR_NAME / "ocr_staging" / "plates"


def _plate_markdown_path(cwd: Path, plate_path: Path) -> Path:
    r"""Return the deterministic per-plate Markdown transcript path for ``plate_path``."""
    return _ocr_plates_dir(cwd) / f"{plate_path.stem}.md"


def _match_allowed_scribe_plate_md(cwd: Path, target_path: Path) -> Path | None:
    r"""Return the staged visual plate ``Path`` if ``target_path`` is its allowed ``plates/<stem>.md`` file."""
    try:
        res_t = target_path.resolve()
        if res_t.suffix.lower() != ".md" or res_t.parent != _ocr_plates_dir(cwd).resolve():
            return None
    except Exception:
        return None
    for p_obj in _load_required_ocr_plates(cwd):
        if p_obj.stem == res_t.stem:
            return p_obj
    return None


def _clean_single_plate_markdown_body(raw_body: str, plate_name: str) -> str:
    r"""Strip wrapper headings or handshake receipts from an isolated per-plate OCR transcript."""
    cleaned_lines: list[str] = []
    in_skip_tail = False
    for ln in raw_body.splitlines():
        s = ln.strip()
        if re.match(r"^#{1,4}\s+.*(?:Epistemic\s+Uncertainty\s+Ledger|Subagent\s+Handshake\s+Receipt)\b", s, re.IGNORECASE):
            in_skip_tail = True
            continue
        if in_skip_tail:
            continue
        if re.match(r"^#\s+SOURCE_OCR\.md\b", s, re.IGNORECASE):
            continue
        if re.match(rf"^#{{1,4}}\s+Plate\s+\d+(?:\s+of\s+\d+)?\b.*{re.escape(plate_name)}", s, re.IGNORECASE):
            continue
        cleaned_lines.append(ln.rstrip())
    body = "\n".join(cleaned_lines).strip()
    body = re.sub(r"^(?:---\s*\n)+", "", body).strip()
    body = re.sub(r"(?:\n\s*---)+\s*$", "", body).strip()
    return body


def _assemble_source_ocr_from_plates(cwd: Path, req_plates: list[Path]) -> tuple[str, list[str]]:
    r"""Deterministically assemble ``<cwd>/SOURCE_OCR.md`` from validated ``plates/<stem>.md`` transcripts."""
    tot = len(req_plates)
    doc_parts: list[str] = ["# SOURCE_OCR.md", ""]
    for idx_p, p_obj in enumerate(req_plates, 1):
        md_p = _plate_markdown_path(cwd, p_obj)
        raw_p = md_p.read_text(encoding="utf-8", errors="replace") if md_p.is_file() else ""
        san_p = sanitize_markdown_artifact_text(raw_p, normalize_ligatures=True).strip()
        clean_body = _clean_single_plate_markdown_body(san_p, p_obj.name)
        if idx_p > 1:
            doc_parts.extend(["---", ""])
        doc_parts.append(f"## Plate {idx_p} of {tot} ({p_obj.name})")
        doc_parts.append("")
        doc_parts.append(clean_body)
        doc_parts.append("")

    assembled_draft = "\n".join(doc_parts).rstrip()
    sanitized_all, unc_items_all, _xml_errs = parse_and_normalize_tei_provenance(assembled_draft)
    ledger_lines = ["", "---", "", "## Epistemic Uncertainty Ledger"]
    if unc_items_all:
        for idx_u, u_item in enumerate(unc_items_all, 1):
            clean_u = str(u_item).replace("`", "'").strip()
            ledger_lines.append(f"- **Tag {idx_u}:** `{clean_u}`")
    else:
        ledger_lines.append(
            f"- None (`0` `<uncertain>` or `<illegible/>` tags across all {tot} transcribed plate(s))."
        )
    final_ocr = sanitized_all.rstrip() + "\n" + "\n".join(ledger_lines) + "\n"
    atomic_write_text(cwd / "SOURCE_OCR.md", final_ocr)
    return (final_ocr, unc_items_all)


def _extract_single_plate_headings(req_plates: list[Path], markdown_text: str) -> list[tuple[Path, str]]:
    r"""Extract ``(plate_path, section_body)`` pairs bounded by the next plate or ledger heading."""
    if not req_plates or not markdown_text.strip():
        return []
    headings = list(re.finditer(r"^(#{1,4})\s+(.+)$", markdown_text, re.MULTILINE))
    boundaries: list[tuple[int, int, Path | None]] = []
    for m_h in headings:
        h_title = m_h.group(2).strip()
        hits = [
            p_obj for p_obj in req_plates
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(p_obj.name)}(?![A-Za-z0-9_])", h_title, re.IGNORECASE)
        ]
        if len(hits) == 1:
            boundaries.append((m_h.start(), m_h.end(), hits[0]))
        elif re.search(r"\b(?:Epistemic\s+Uncertainty\s+Ledger|Subagent\s+Handshake\s+Receipt)\b", h_title, re.IGNORECASE):
            boundaries.append((m_h.start(), m_h.end(), None))

    matched_sections: list[tuple[Path, str]] = []
    for idx_b, (_b_start, b_end, plate_hit) in enumerate(boundaries):
        if plate_hit is None:
            continue
        sec_end = boundaries[idx_b + 1][0] if idx_b + 1 < len(boundaries) else len(markdown_text)
        raw_body = markdown_text[b_end:sec_end]
        clean_lines = [
            ln.strip() for ln in raw_body.splitlines()
            if ln.strip() and not re.match(r"^-{3,}$", ln.strip())
        ]
        matched_sections.append((plate_hit, "\n".join(clean_lines)))
    return matched_sections


def _inspect_source_ocr_plate_coverage(cwd: Path, ocr_text: str | None = None) -> tuple[list[Path], list[Path], list[Path]]:
    r"""Verify per-plate transcription completeness across ``plates/<stem>.md`` and ``SOURCE_OCR.md``."""
    req_plates = _load_required_ocr_plates(cwd)
    if not req_plates:
        return ([], [], [])
    eff_ocr_text = (
        ocr_text
        if ocr_text is not None
        else ((cwd / "SOURCE_OCR.md").read_text(encoding="utf-8", errors="replace") if (cwd / "SOURCE_OCR.md").is_file() else "")
    )

    valid_plate_files: set[str] = set()
    for p_obj in req_plates:
        md_p = _plate_markdown_path(cwd, p_obj)
        if md_p.is_file():
            raw_md = sanitize_markdown_artifact_text(
                md_p.read_text(encoding="utf-8", errors="replace"), normalize_ligatures=True
            ).strip()
            clean_md = _clean_single_plate_markdown_body(raw_md, p_obj.name)
            if len(clean_md) >= 12:
                _s_md, _u_md, xml_e_md = parse_and_normalize_tei_provenance(clean_md)
                if not xml_e_md:
                    valid_plate_files.add(str(p_obj))

    if len(req_plates) == 1:
        if str(req_plates[0]) in valid_plate_files:
            return (req_plates, list(req_plates), [])
        stripped_lines = []
        in_receipt = False
        for ln in eff_ocr_text.splitlines():
            s = ln.strip()
            if re.match(r"^##\s+Subagent\s+Handshake\s+Receipt\b", s, re.IGNORECASE):
                in_receipt = True
                continue
            if in_receipt:
                if s.startswith("## ") or s == "---":
                    in_receipt = False
                else:
                    continue
            if s.startswith("# SOURCE_OCR.md") or s.startswith("- **") or s == "---":
                continue
            if s:
                stripped_lines.append(s)
        if len("\n".join(stripped_lines)) >= 12:
            return (req_plates, list(req_plates), [])
        return (req_plates, [], list(req_plates))

    sec_map: dict[str, str] = {}
    for p_hit, body_txt in _extract_single_plate_headings(req_plates, eff_ocr_text):
        key = str(p_hit)
        prev = sec_map.get(key, "")
        if len(body_txt) > len(prev):
            sec_map[key] = body_txt

    completed: list[Path] = []
    missing: list[Path] = []
    for p_obj in req_plates:
        key = str(p_obj)
        if key in valid_plate_files or len(sec_map.get(key, "")) >= 15:
            completed.append(p_obj)
        else:
            missing.append(p_obj)
    return (req_plates, completed, missing)


# ============================================================================
# Section 4: `<uncertain>` / `<illegible/>` Transcriptional Uncertainty XML Parser & Epistemic Extraction
# Validates balanced TEI P5 XML provenance tags outside Markdown code spans and extracts uncertainty ledgers.
# ============================================================================

def parse_and_normalize_tei_provenance(ocr_text: str) -> tuple[str, list[str], list[str]]:
    r"""Parse, normalize, and stack-validate TEI P5 ``<uncertain>`` / ``<illegible/>`` provenance in Markdown.

    Distinguishes real TEI provenance elements (paired ``<uncertain ...>...</uncertain>``
    and self-closing ``<illegible .../>`` / ``<uncertain .../>``) from inline/fenced code
    spans and prose meta-commentary mentions (such as ``No <uncertain> or <illegible/> tags``
    or bare unattributed ``<uncertain>`` without ``</uncertain>``), canonicalizing prose
    tag-name mentions into backticked code spans so validation and uncertainty counting
    share one deterministic grammar.

    Args:
        ocr_text: Raw or ligature-normalized ``SOURCE_OCR.md`` text.

    Returns:
        Tuple ``(normalized_markdown, active_tei_tags, xml_errors)``.
    """
    raw = str(ocr_text or "")
    if not raw:
        return ("", [], [])

    meta_negation_re = re.compile(
        r"(?:epistemic\s+(?:status|uncertainty)|(?:\bno|\bzero|\b0|\bwithout|\bnone)\s+[^\n]*<(?:uncertain|illegible)|<uncertain>\s*(?:or|and|/)\s*<illegible|<(?:uncertain|illegible)[^>]*>\s+tags?\b)",
        re.IGNORECASE,
    )

    # First pass: outside fenced and inline code spans, backtick meta-negation mentions and bare unattributed unclosed <uncertain>.
    code_split_re = re.compile(r"(```[\s\S]*?```|`[^`\r\n]+`)")
    has_closing_uncertain = bool(re.search(r"</uncertain\s*>", _strip_markdown_code_spans(raw), flags=re.IGNORECASE))
    norm_chunks: list[str] = []
    for idx_c, chunk in enumerate(code_split_re.split(raw)):
        if idx_c % 2 == 1:
            norm_chunks.append(chunk)
            continue
        rebuilt_lines: list[str] = []
        for ln in chunk.splitlines(keepends=True):
            has_ln_close = "</uncertain" in ln.lower()
            if not has_ln_close and meta_negation_re.search(ln):
                ln = re.sub(r"(?<!`)<uncertain>(?!`)", "`<uncertain>`", ln, flags=re.IGNORECASE)
                ln = re.sub(r"(?<!`)<illegible\s*/>(?!`)", "`<illegible/>`", ln, flags=re.IGNORECASE)
            elif not has_closing_uncertain and not has_ln_close:
                # Bare `<uncertain>` with zero attributes and zero `</uncertain>` anywhere in the document is a prose tag-name mention.
                ln = re.sub(r"(?<!`)<uncertain>(?!`)", "`<uncertain>`", ln, flags=re.IGNORECASE)
            rebuilt_lines.append(ln)
        norm_chunks.append("".join(rebuilt_lines))
    normalized = "".join(norm_chunks)

    # Second pass: pushdown-stack validation and active-tag extraction over non-code spans, preserving line offsets.
    masked_chars = list(normalized)
    for m_code in code_split_re.finditer(normalized):
        for pos in range(m_code.start(), m_code.end()):
            if masked_chars[pos] != "\n":
                masked_chars[pos] = " "
    masked = "".join(masked_chars)
    norm_lines = normalized.splitlines()

    def _loc_hint(char_offset: int) -> str:
        ln_no = masked[:char_offset].count("\n") + 1
        preview = norm_lines[ln_no - 1].strip()[:120] if 1 <= ln_no <= len(norm_lines) else ""
        return f" on line {ln_no} ({preview!r})" if preview else f" on line {ln_no}"

    errors: list[str] = []
    active_tags: list[str] = []
    open_stack: list[tuple[str, int]] = []

    token_re = re.compile(r"<(/?)(uncertain|illegible)\b([^<>]*)(/?)>", re.IGNORECASE)
    scanned_end = 0
    for m_tok in token_re.finditer(masked):
        # Flag any truncated `<uncertain` or `<illegible` missing `>` prior to this match
        between = masked[scanned_end : m_tok.start()]
        m_trunc = re.search(r"<(/?)(uncertain|illegible)\b", between, flags=re.IGNORECASE)
        if m_trunc:
            t_nm = m_trunc.group(2).lower()
            errors.append(
                f"Malformed TEI P5 <{t_nm}> XML tag in SOURCE_OCR.md{_loc_hint(scanned_end + m_trunc.start())}: missing closing '>'."
            )
        scanned_end = m_tok.end()

        is_close = bool(m_tok.group(1))
        tag_nm = m_tok.group(2).lower()
        attrs_body = m_tok.group(3) or ""
        is_self_close = bool(m_tok.group(4)) or attrs_body.rstrip().endswith("/")

        if is_close and is_self_close:
            errors.append(
                f"Malformed TEI P5 </{tag_nm}/> XML tag in SOURCE_OCR.md{_loc_hint(m_tok.start())}: invalid closing syntax."
            )
            continue
        if is_self_close:
            active_tags.append(normalized[m_tok.start() : m_tok.end()])
            continue
        if not is_close:
            if tag_nm == "illegible":
                # Check if a matching </illegible> exists later; if not, treat bare `<illegible ...>` with attributes as active tag
                rest = masked[m_tok.end() :]
                if re.search(r"</illegible\s*>", rest, flags=re.IGNORECASE):
                    open_stack.append((tag_nm, m_tok.start()))
                else:
                    active_tags.append(normalized[m_tok.start() : m_tok.end()])
            else:
                open_stack.append((tag_nm, m_tok.start()))
        else:
            if open_stack and open_stack[-1][0] == tag_nm:
                _, open_pos = open_stack.pop()
                active_tags.append(normalized[open_pos : m_tok.end()])
            else:
                errors.append(
                    f"Malformed TEI P5 </{tag_nm}> XML tag in SOURCE_OCR.md{_loc_hint(m_tok.start())}: orphan closing tag without matching <{tag_nm}>."
                )

    tail = masked[scanned_end:]
    m_tail_trunc = re.search(r"<(/?)(uncertain|illegible)\b", tail, flags=re.IGNORECASE)
    if m_tail_trunc:
        t_nm = m_tail_trunc.group(2).lower()
        if t_nm == "illegible":
            errors.append(
                f"Malformed TEI P5 <illegible/> XML tag in SOURCE_OCR.md{_loc_hint(scanned_end + m_tail_trunc.start())}: use self-closing <illegible context=\"...\" reason=\"...\"/>."
            )
        else:
            errors.append(
                f"Malformed TEI P5 <uncertain> XML tag in SOURCE_OCR.md{_loc_hint(scanned_end + m_tail_trunc.start())}: every <uncertain ...> tag must be closed with </uncertain>."
            )

    for unclosed_nm, unclosed_pos in open_stack:
        errors.append(
            f"Malformed TEI P5 <{unclosed_nm}> XML tag in SOURCE_OCR.md{_loc_hint(unclosed_pos)}: every <{unclosed_nm} ...> tag must be closed "
            f"with </{unclosed_nm}> (or wrap literal tag-name mentions in backticks like `<{unclosed_nm}>`)."
        )

    return (normalized, active_tags, errors)


def detect_ocr_uncertainties(cwd):
    r"""Extract active TEI P5 provenance tags from ``SOURCE_OCR.md``."""
    ocr_path = Path(cwd) / "SOURCE_OCR.md"
    if not ocr_path.is_file():
        return []
    try:
        raw_text = ocr_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    _, active_tags, _ = parse_and_normalize_tei_provenance(raw_text)
    return active_tags


def extract_source_origin_from_ocr(cwd: Path, spec_dir: Path | None = None) -> str:
    """Extract the workspace-relative visual or PDF asset path from ``SOURCE_OCR.md`` or ``manifest.json``.

    Args:
        cwd: Workspace root directory.
        spec_dir: Optional archived ``specs/<slug>/`` directory to check first.

    Returns:
        Workspace-relative source asset path or non-HTTP ``doi_or_url`` origin
        string, or ``""`` when none is recorded.
    """
    base = spec_dir if (spec_dir and spec_dir.is_dir()) else cwd
    for cand in (base / "SOURCE_OCR.md", cwd / "SOURCE_OCR.md"):
        if cand.is_file():
            try:
                txt = cand.read_text(encoding="utf-8", errors="replace")
                m_rel = re.search(
                    r"((?:examples|research)/[A-Za-z0-9_\-./]+\.(?:png|jpg|jpeg|webp|gif|bmp|tiff|tif|pdf))",
                    txt,
                    re.IGNORECASE,
                )
                if m_rel:
                    return m_rel.group(1).strip()
            except Exception:
                pass
    for man_cand in (base / "manifest.json", cwd / "manifest.json"):
        if man_cand.is_file():
            try:
                sr = json.loads(man_cand.read_text(encoding="utf-8")).get("source_reference")
                if isinstance(sr, dict):
                    doi_u = str(sr.get("doi_or_url") or "").strip()
                    if doi_u and not doi_u.startswith(("http://", "https://")):
                        return doi_u
            except Exception:
                pass
    return ""


# ============================================================================
# Section 5: Epistemic Working-Set Scope Resolver & Mosaic Composition Seam
# Unifies active problem-folder confinement, linked `specs/<slug>/` composition scope,
# unscoped `grep`/`glob` search roots, and poka-yoke approved-source redirects.
# ============================================================================

_ALLOWED_ROOT_MARKDOWN_FILES = frozenset({
    "SOURCE_TEXT.md",
    "SOURCE_OCR.md",
    "RESEARCH.md",
    "VERIFICATION.md",
})

_BLOCKED_ROOT_META_FILES = frozenset({
    CONFIG_FILE_NAME,
    FSM_FILE_NAME,
    APPROVED_SOURCES_FILENAME,
    ".pipeline_errors.log",
    "LICENSE",
})

_BLOCKED_REPO_SUBDIRS = frozenset({
    "docs",
    "benchmarks",
    "scripts",
    "templates",
    "modelfiles",
})


def _is_blocked_root_meta_file(filename: str) -> bool:
    """Return ``True`` when ``filename`` in ``<cwd>`` is a control-plane or non-deliverable Markdown file."""
    if filename in _BLOCKED_ROOT_META_FILES or filename.startswith(".pipeline_"):
        return True
    if filename.lower().endswith(".md") and filename not in _ALLOWED_ROOT_MARKDOWN_FILES:
        return True
    return False


def resolve_mosaic_composer_linked_specs(
    cwd: Path,
    fsm: dict[str, object] | None = None,
    cfg: dict[str, object] | None = None,
) -> list[str]:
    """Resolve ``specs/<slug>`` packages selected via Mosaic Composition (Milestone 4 Seam).

    # --- FUTURE MOSAIC COMPOSITION SEAM (Mosaic Milestone 4 — Multi-Mode Composer) ---
    # When Facet Lens / Mosaic stages atomized `Module` nodes, `Link` bridges, or multi-layer
    # `Closure` facets (whether expanded as convex polygon faces or collapsed into single
    # point-group nodes) in the Multi-Mode Composer (`EXACT_PORT`, `GUARD_REQUIRED`,
    # `SHARED_DOMAIN_SET`), persist the selected `specs/<slug>` identifiers into
    # `.pipeline_config.json["mosaic_linked_specs"]` or `.pipeline_state.json["mosaic_linked_specs"]`.
    # Because leaf modules and higher-order composed facets share the identical
    # `specs/<slug>/PROVENANCE.json` contract, this single resolver composes across arbitrary
    # nesting depth while keeping working-set scope (`compute_stage_working_set`,
    # `<workspace_map>`, and `<upstream_mosaic_ports>`) bounded to immediate constituents.
    # ---------------------------------------------------------------------------------
    """
    cwd_p = Path(cwd)
    eff_cfg = cfg if isinstance(cfg, dict) else load_pipeline_config(cwd_p)
    eff_fsm = fsm if isinstance(fsm, dict) else (load_pipeline_fsm(cwd_p) if (cwd_p / FSM_FILE_NAME).is_file() else {})
    slugs: list[str] = []
    for raw_list in (eff_cfg.get("mosaic_linked_specs"), eff_fsm.get("mosaic_linked_specs")):
        if isinstance(raw_list, list):
            for item in raw_list:
                slug = str(item or "").strip().strip("/")
                if slug.startswith("specs/"):
                    slug = slug[len("specs/"):].split("/", 1)[0]
                if slug and ".." not in slug and "/" not in slug and slug not in slugs:
                    if (cwd_p / "specs" / slug / "PROVENANCE.json").is_file():
                        slugs.append(slug)
    return slugs


def resolve_linked_composition_specs(
    cwd: Path,
    fsm: dict[str, object] | None = None,
    cfg: dict[str, object] | None = None,
) -> list[str]:
    r"""Return deduplicated ``specs/<slug>`` identifiers explicitly linked to the active cycle."""
    cwd_p = Path(cwd)
    eff_cfg = cfg if isinstance(cfg, dict) else load_pipeline_config(cwd_p)
    eff_fsm = fsm if isinstance(fsm, dict) else (load_pipeline_fsm(cwd_p) if (cwd_p / FSM_FILE_NAME).is_file() else {})
    linked: list[str] = []

    def _add_slug(raw_val: object) -> None:
        s = str(raw_val or "").strip().replace("\\", "/").strip("/")
        if s.startswith("specs/"):
            s = s[len("specs/"):].split("/", 1)[0]
        if s and ".." not in s and "/" not in s and s.lower() != "readme.md" and s not in linked:
            if (cwd_p / "specs" / s).is_dir():
                linked.append(s)

    raw_ref = eff_fsm.get("referenced_archived_specs")
    if isinstance(raw_ref, list):
        for r_item in raw_ref:
            _add_slug(r_item)
    raw_seals = eff_cfg.get("upstream_seals")
    if isinstance(raw_seals, list):
        for s_item in raw_seals:
            _add_slug(s_item)
    orig_req = str(eff_fsm.get("original_user_request") or "")
    for m_sp in re.finditer(r"(?:^|[\s'\"`(])specs/([a-zA-Z0-9_-]+)", orig_req):
        _add_slug(m_sp.group(1))
    map_p = cwd_p / "mapping.json"
    if map_p.is_file():
        try:
            raw_up = json.loads(map_p.read_text(encoding="utf-8")).get("upstream_modules")
            if isinstance(raw_up, list):
                for up_item in raw_up:
                    if isinstance(up_item, dict):
                        _add_slug(up_item.get("spec_slug"))
        except Exception:
            pass
    for mosaic_slug in resolve_mosaic_composer_linked_specs(cwd_p, fsm=eff_fsm, cfg=eff_cfg):
        _add_slug(mosaic_slug)
    return linked


def compute_stage_working_set(
    cwd: Path,
    active_agent: str,
    stage: str | None = None,
    fsm: dict[str, object] | None = None,
    cfg: dict[str, object] | None = None,
) -> dict[str, object]:
    """Compute the canonical ``(role, stage)`` epistemic read/search working-set envelope."""
    cwd_res = Path(cwd).resolve()
    eff_cfg = cfg if isinstance(cfg, dict) else load_pipeline_config(cwd_res)
    eff_fsm = fsm if isinstance(fsm, dict) else (load_pipeline_fsm(cwd_res) if (cwd_res / FSM_FILE_NAME).is_file() else {})
    eff_stage = str(stage or eff_fsm.get("current_stage") or "")
    orig_req = str(eff_fsm.get("original_user_request") or "")
    res_in = discover_research_inputs(cwd_res, user_request=orig_req, cfg=eff_cfg)

    promoted_files: list[Path] = []
    promoted_dirs: list[Path] = []
    explicit_file_set: set[Path] = set()
    file_scoped_ex_dirs: set[Path] = set()
    for f_tok in re.findall(r"[A-Za-z0-9_./\-]+\.[A-Za-z0-9]{2,5}", orig_req):
        if "://" in f_tok or f_tok.startswith("/"):
            continue
        fc = (cwd_res / f_tok).resolve()
        if fc.is_file() and fc.is_relative_to(cwd_res):
            explicit_file_set.add(fc)
            f_parts = fc.relative_to(cwd_res).parts
            if len(f_parts) >= 2 and f_parts[0] == "examples":
                file_scoped_ex_dirs.add((cwd_res / f_parts[0] / f_parts[1]).resolve())

    for rel_item in list(res_in.get("ocr_files") or []) + list(res_in.get("text_files") or []):
        rel_parts = Path(str(rel_item)).parts
        if len(rel_parts) >= 2 and rel_parts[0] != "research":
            pf_obj = (cwd_res / str(rel_item)).resolve()
            if pf_obj.is_file() and pf_obj not in promoted_files:
                promoted_files.append(pf_obj)
            sub_dir = (cwd_res / rel_parts[0] / rel_parts[1]).resolve()
            if sub_dir.is_dir() and sub_dir not in file_scoped_ex_dirs and sub_dir not in promoted_dirs:
                promoted_dirs.append(sub_dir)

    linked_slugs = resolve_linked_composition_specs(cwd_res, fsm=eff_fsm, cfg=eff_cfg)
    linked_spec_dirs = [(cwd_res / "specs" / sl).resolve() for sl in linked_slugs if (cwd_res / "specs" / sl).is_dir()]

    custom_glob_dirs: list[Path] = []
    tc_cfg = eff_cfg.get("toolchain")
    if isinstance(tc_cfg, dict) and isinstance(tc_cfg.get("verification_globs"), list):
        for g_pat in tc_cfg["verification_globs"]:
            g_parts = Path(str(g_pat)).parts
            if len(g_parts) >= 2 and not g_parts[0].startswith(("*", ".")):
                cg_dir = (cwd_res / g_parts[0]).resolve()
                if cg_dir.is_dir() and cg_dir not in custom_glob_dirs:
                    custom_glob_dirs.append(cg_dir)

    skill_dirs: list[Path] = []
    for sk_cand in (cwd_res / ".github" / "skills", get_copilot_home() / "skills"):
        if sk_cand.exists():
            skill_dirs.append(sk_cand.resolve())

    markov_s1 = active_agent == "Researcher" and _is_markovian_stage1_with_research_md(cwd_res, eff_stage)
    research_dir = (cwd_res / "research").resolve()
    has_research_dir = research_dir.is_dir() and not markov_s1

    searchable_roots: list[Path] = []
    if has_research_dir:
        searchable_roots.append(research_dir)
    for p_dir in promoted_dirs:
        if p_dir not in searchable_roots:
            searchable_roots.append(p_dir)
    for p_file in promoted_files:
        if p_file not in searchable_roots and not any(p_file.is_relative_to(pd) for pd in promoted_dirs):
            searchable_roots.append(p_file)
    for l_dir in linked_spec_dirs:
        if l_dir not in searchable_roots:
            searchable_roots.append(l_dir)
    for c_dir in custom_glob_dirs:
        if c_dir not in searchable_roots:
            searchable_roots.append(c_dir)
    for root_f in sorted(cwd_res.iterdir()) if cwd_res.is_dir() else []:
        if not root_f.is_file() or root_f.name.startswith(".") or _is_blocked_root_meta_file(root_f.name):
            continue
        if markov_s1 and root_f.name in ("SOURCE_TEXT.md", "SOURCE_OCR.md") and not _has_post_research_stage1_ocr(cwd_res):
            continue
        if active_agent == "Scribe" and root_f.name == "SOURCE_TEXT.md":
            continue
        if root_f.suffix.lower() in {".md", ".json", ".py", ".txt"}:
            searchable_roots.append(root_f.resolve())

    readable_labels: list[str] = []
    for r_f in sorted(cwd_res.iterdir()) if cwd_res.is_dir() else []:
        if r_f.is_file() and not r_f.name.startswith(".") and not _is_blocked_root_meta_file(r_f.name):
            readable_labels.append(r_f.name)
    if has_research_dir:
        readable_labels.append("research/")
    for p_d in promoted_dirs:
        readable_labels.append(f"{p_d.relative_to(cwd_res).as_posix()}/")
    for p_f in promoted_files:
        if not any(p_f.is_relative_to(pd) for pd in promoted_dirs):
            readable_labels.append(p_f.relative_to(cwd_res).as_posix())
    for l_s in linked_slugs:
        readable_labels.append(f"specs/{l_s}/")

    return {
        "cwd": cwd_res,
        "active_agent": active_agent,
        "stage": eff_stage,
        "markov_s1": markov_s1,
        "has_research_dir": has_research_dir,
        "research_dir": research_dir,
        "promoted_files": promoted_files,
        "explicit_files": sorted(explicit_file_set),
        "promoted_dirs": promoted_dirs,
        "linked_spec_slugs": linked_slugs,
        "linked_spec_dirs": linked_spec_dirs,
        "custom_glob_dirs": custom_glob_dirs,
        "skill_dirs": skill_dirs,
        "searchable_roots": searchable_roots,
        "readable_labels": readable_labels,
    }


def check_path_in_working_set(target_path: str | Path, working_set: dict[str, object]) -> tuple[bool, str]:
    """Return ``(allowed, reason_code)`` for ``target_path`` under ``working_set``."""
    cwd_res: Path = working_set["cwd"]  # type: ignore[assignment]
    active_agent = str(working_set.get("active_agent") or "")
    p_raw = Path(str(target_path))
    resolved = (p_raw if p_raw.is_absolute() else (cwd_res / p_raw)).resolve()

    for sk_dir in working_set.get("skill_dirs") or []:  # type: ignore[union-attr]
        if resolved == sk_dir or resolved.is_relative_to(sk_dir):
            return (True, "skill_asset")

    if active_agent == "Scribe":
        ocr_stg = (cwd_res / SCRATCH_DIR_NAME / "ocr_staging").resolve()
        if resolved == ocr_stg or resolved.is_relative_to(ocr_stg):
            return (True, "scribe_ocr_staging")

    if not resolved.is_relative_to(cwd_res):
        return (False, "outside_workspace")

    if resolved == cwd_res:
        return (False, "workspace_root_listing_blocked")

    rel_parts = resolved.relative_to(cwd_res).parts
    if not rel_parts:
        return (False, "workspace_root_listing_blocked")

    top = rel_parts[0]
    if len(rel_parts) == 1:
        if _is_blocked_root_meta_file(top):
            return (False, "repo_meta_or_control_file")
        if resolved.is_dir():
            if top == "research" and bool(working_set.get("has_research_dir")):
                return (True, "research_dir")
            for c_dir in working_set.get("custom_glob_dirs") or []:  # type: ignore[union-attr]
                if resolved == c_dir:
                    return (True, "custom_toolchain_dir")
            return (False, f"top_level_dir_outside_scope:{top}")
        return (True, "root_deliverable")

    if top == "research":
        return (True, "research_input") if bool(working_set.get("has_research_dir")) else (False, "research_outside_scope")

    if top == "examples":
        promoted_f_set = set(working_set.get("promoted_files") or [])  # type: ignore[arg-type]
        explicit_f_set = set(working_set.get("explicit_files") or [])  # type: ignore[arg-type]
        if resolved in promoted_f_set:
            return (True, "promoted_example_file")
        for p_dir in working_set.get("promoted_dirs") or []:  # type: ignore[union-attr]
            if resolved == p_dir or resolved.is_relative_to(p_dir):
                if (
                    resolved.is_file()
                    and resolved.name.lower() == "readme.md"
                    and resolved not in explicit_f_set
                    and _has_non_readme_sibling_assets(resolved.parent)
                ):
                    return (False, "showcase_example_readme_excluded")
                return (True, "promoted_example_scope")
        return (False, "outside_promoted_example_scope")

    if top == "specs":
        for l_dir in working_set.get("linked_spec_dirs") or []:  # type: ignore[union-attr]
            if resolved == l_dir or resolved.is_relative_to(l_dir):
                return (True, "linked_composition_spec")
        return (False, "unlinked_specs_archive")

    for c_dir in working_set.get("custom_glob_dirs") or []:  # type: ignore[union-attr]
        if resolved == c_dir or resolved.is_relative_to(c_dir):
            return (True, "custom_toolchain_dir")

    if top in _BLOCKED_REPO_SUBDIRS or top.startswith("."):
        return (False, f"repo_subdir_outside_scope:{top}")

    for p_dir in working_set.get("promoted_dirs") or []:  # type: ignore[union-attr]
        if resolved == p_dir or resolved.is_relative_to(p_dir):
            return (True, "promoted_workspace_scope")

    return (False, f"unscoped_subdir:{top}")


def format_working_set_denial(
    cwd: Path,
    active_agent: str,
    target_desc: str,
    working_set: dict[str, object],
    cfg: dict[str, object] | None = None,
    user_local_file: str | Path | None = None,
) -> str:
    """Render a poka-yoke scope-boundary redirect listing exact allowed paths and approved web resources."""
    cwd_p = Path(cwd)
    eff_cfg = cfg if isinstance(cfg, dict) else load_pipeline_config(cwd_p)
    labels = list(working_set.get("readable_labels") or [])
    in_scope_str = ", ".join(labels[:12]) if labels else "none (no local reference files in scope)"
    msg = (
        f"[SCOPE BOUNDARY — OUTSIDE ACTIVE PROBLEM SCOPE]: @{active_agent} cannot read or search '{target_desc}' "
        "because it lies outside the active problem scope (parent index folders, sibling examples, unlinked 'specs/*' archives, "
        "and repository documentation/control files are isolated). "
        f"In-scope workspace items: {in_scope_str}."
    )
    if active_agent == "Researcher" and bool(eff_cfg.get("allow_web_research", True)) and not bool(working_set.get("markov_s1")):
        sources = load_approved_research_sources(cwd_p, user_local_file=user_local_file)
        dom_s = ", ".join(sources["domains"][:12])
        hint_s = "; ".join(f"{k} -> {v}" for k, v in sorted(sources["hints"].items()))
        msg += (
            f" Retrieve external literature via 'web_fetch(url=...)' against Approved Research Domains ({dom_s})"
            + (f" using structured search/lookup endpoints ({hint_s})." if hint_s else ".")
        )
    return msg


__all__ = [
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
    "check_path_in_working_set",
    "clean_ocr_staging",
    "compute_stage_working_set",
    "detect_ocr_uncertainties",
    "discover_research_inputs",
    "execute_add_source_command",
    "extract_source_origin_from_ocr",
    "filter_decorative_ocr_inputs",
    "format_working_set_denial",
    "is_approved_research_url",
    "load_approved_research_sources",
    "normalize_research_domain",
    "parse_add_source_command",
    "parse_and_normalize_tei_provenance",
    "resolve_initial_active_stage",
    "resolve_linked_composition_specs",
    "resolve_mosaic_composer_linked_specs",
]
