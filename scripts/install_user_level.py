#!/usr/bin/env python3
"""Installs Facet customizations into the user's Copilot directory.

Copies custom agents, skills, ambient instructions, Modelfiles, and lifecycle
hooks from this repository into ``~/.copilot/`` (or ``$COPILOT_HOME``) so
``@Facet`` works across any folder on the machine:
  - Rewrites hook commands from ``--scope workspace`` to ``--scope user`` so
    user-level hooks step aside automatically whenever a repository has its own
    workspace hooks, preventing duplicate execution.
  - Preserves existing ``model:`` lines in ``~/.copilot/agents/*.agent.md``
    unless ``--force-models`` is passed.
  - Leaves ``.github/extensions/facet-lens/`` strictly Workspace-scoped.

Usage:
  ./.venv/bin/python scripts/install_user_level.py [--dry-run] [--force-models]
      [--build-ollama]

Function Map:
  - Section 1: User-Scope Path, Model-Tag Preservation & Directory Synchronizers (`get_user_copilot_dir`, `extract_model_from_agent`, `sync_agents`, `sync_directory`)
  - Section 2: CLI Installer & Optional Modelfile Builder (`main`)

Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# ============================================================================
# Section 1: User-Scope Path, Model-Tag Preservation & Directory Synchronizers
# ============================================================================

# Resolve the repository root one folder above `scripts/`.
REPO_ROOT: Path = Path(__file__).parent.parent.resolve()


def get_user_copilot_dir() -> Path:
    r"""Resolve the target user Copilot configuration directory.

    Returns:
        Path to ``$COPILOT_HOME`` when set, otherwise ``~/.copilot``.
    """
    # Respect `$COPILOT_HOME` when configured (for isolated tests or custom setups).
    env_home = os.environ.get("COPILOT_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    # Otherwise default to the standard user-scope Copilot folder (`~/.copilot`).
    return Path.home() / ".copilot"


def extract_model_from_agent(file_path: Path) -> Optional[str]:
    r"""Extract the YAML frontmatter ``model:`` line from an agent file.

    Args:
        file_path: Path to an existing ``*.agent.md`` customization file.

    Returns:
        The raw ``model: ...`` line if present and readable, otherwise ``None``.
    """
    if not file_path.exists():
        return None
    try:
        # Read the destination `.agent.md` file and capture its `model: [...]` line
        # so re-running the installer never clobbers a user's custom Ollama tag.
        text = file_path.read_text(encoding="utf-8")
        match = re.search(r"^model:\s*(.+)$", text, re.MULTILINE)
        if match:
            return match.group(0)
    except Exception:
        pass
    return None


def sync_agents(
    src_dir: Path,
    dst_dir: Path,
    preserve_models: bool = True,
    dry_run: bool = False,
) -> None:
    r"""Copy custom agents to user scope and rewrite inline hook paths.

    Args:
        src_dir: Repository ``.github/agents/`` source directory.
        dst_dir: Destination ``$COPILOT_HOME/agents/`` directory.
        preserve_models: When ``True``, keep any existing ``model:`` line in
            destination agent files.
        dry_run: When ``True``, print planned actions without writing files.
    """
    if not dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)
    # Quote the user-scope `pipeline_guards.py` path in case the home path has spaces.
    user_hook_script = dst_dir.parent / "hooks" / "scripts" / "pipeline_guards.py"
    quoted_hook = shlex.quote(str(user_hook_script))
    for agent_file in sorted(src_dir.glob("*.agent.md")):
        target = dst_dir / agent_file.name
        content = agent_file.read_text(encoding="utf-8")

        # Agent-scoped inline hooks inside `facet.agent.md` and `orchestrator.agent.md`
        # When installed globally in `~/.copilot/agents/`, those commands must point
        # still work in folders that do not contain a `.github/hooks/` directory.
        for hook_action in (
            "hypervisor-guard",
            "facet-prompt",
            "facet-stop",
            "coordinator-guard",
            "orchestrator-prompt",
            "orchestrator-stop",
        ):
            content = content.replace(
                f"python3 .github/hooks/scripts/pipeline_guards.py --scope workspace {hook_action}",
                f"python3 {quoted_hook} --scope user {hook_action}",
            )

        # Keep the user's existing `model:` frontmatter line intact unless `--force-models` is set.
        if preserve_models and target.exists():
            existing_model_line = extract_model_from_agent(target)
            if existing_model_line:
                content = re.sub(r"^model:\s*.+$", existing_model_line, content, flags=re.MULTILINE)
                print(f"  [Model Preserved] {agent_file.name}: Kept existing '{existing_model_line}'")

        if dry_run:
            print(f"  [Dry-Run] Would copy {agent_file.name} -> {target}")
        else:
            target.write_text(content, encoding="utf-8")
            print(f"  [Installed] {agent_file.name} -> {target}")


def sync_directory(
    src_dir: Path,
    dst_dir: Path,
    dry_run: bool = False,
    subdirs_only: bool = False,
) -> None:
    """Recursively synchronize files and subdirectories into ``dst_dir``.

    Args:
        src_dir: Source directory to copy from.
        dst_dir: Destination directory to create/update.
        dry_run: When ``True``, print planned copy actions without mutating disk.
        subdirs_only: When ``True``, copy only child directories and skip top-level files.
    """
    if not src_dir.exists():
        return
    if not dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)
    for item in sorted(src_dir.iterdir()):
        target = dst_dir / item.name
        # Scan directory contents when `item.is_dir()`.
        if item.is_dir():
            if dry_run:
                print(f"  [Dry-Run] Would sync dir {item.name}/ -> {target}/")
            else:
                shutil.copytree(item, target, dirs_exist_ok=True)
                print(f"  [Installed Dir] {item.name}/ -> {target}/")
        elif item.is_file() and not subdirs_only:
            if dry_run:
                print(f"  [Dry-Run] Would copy {item.name} -> {target}")
            else:
                shutil.copy2(item, target)
                print(f"  [Installed File] {item.name} -> {target}")


# ============================================================================
# Section 2: CLI Installer & Optional Modelfile Builder
# ============================================================================

def main() -> None:
    r"""Parse CLI arguments and synchronize Facet into user Copilot scope."""
    parser = argparse.ArgumentParser(description="Install Facet to ~/.copilot/ (or $COPILOT_HOME)")
    parser.add_argument("--dry-run", action="store_true", help="Preview file actions without writing")
    parser.add_argument("--force-models", action="store_true", help="Overwrite existing model IDs in ~/.copilot/agents/")
    parser.add_argument("--build-ollama", action="store_true", help="Automatically compile Ollama Modelfiles in modelfiles/")
    args = parser.parse_args()

    user_copilot_dir = get_user_copilot_dir()

    print("=== Facet: User-Level Installer ===")
    print(f"Source Repository: {REPO_ROOT}")
    print(f"Target Directory:  {user_copilot_dir}")
    if args.dry_run:
        print("Mode: DRY RUN (no files will be written)")
    print()

    # 1. Sync Agents.
    print("1. Installing Custom Agents...")
    sync_agents(REPO_ROOT / ".github" / "agents", user_copilot_dir / "agents", preserve_models=not args.force_models, dry_run=args.dry_run)

    # 2. Sync Skills.
    print("\n2. Installing Agent Skills...")
    sync_directory(REPO_ROOT / ".github" / "skills", user_copilot_dir / "skills", dry_run=args.dry_run, subdirs_only=True)
    if not args.dry_run:
        # Ensure every helper script installed under `~/.copilot/skills/*/scripts/` is executable.
        for script_py in (user_copilot_dir / "skills").glob("*/scripts/*.py"):
            if script_py.is_file():
                os.chmod(script_py, 0o755)

    # 3. Sync Ambient Instructions.
    print("\n3. Installing Ambient Instructions...")
    instructions_src = REPO_ROOT / ".github" / "copilot-instructions.md"
    instructions_dst = user_copilot_dir / "copilot-instructions.md"
    if instructions_src.exists():
        if args.dry_run:
            print(f"  [Dry-Run] Would copy copilot-instructions.md -> {instructions_dst}")
        else:
            shutil.copy2(instructions_src, instructions_dst)
            print(f"  [Installed] copilot-instructions.md -> {instructions_dst}")

    # 4. Sync Modelfiles.
    print("\n4. Installing Modelfiles...")
    sync_directory(REPO_ROOT / "modelfiles", user_copilot_dir / "modelfiles", dry_run=args.dry_run)

    # 5. Sync Hooks and Generate User Hook Config.
    print("\n5. Installing Deterministic Hooks...")
    hook_script_src = REPO_ROOT / ".github" / "hooks" / "scripts" / "pipeline_guards.py"
    hook_script_dst = user_copilot_dir / "hooks" / "scripts" / "pipeline_guards.py"
    facet_guards_pkg_src = REPO_ROOT / ".github" / "hooks" / "scripts" / "facet_guards"
    facet_guards_pkg_dst = user_copilot_dir / "hooks" / "scripts" / "facet_guards"
    stochastic_plugin_src = REPO_ROOT / ".github" / "hooks" / "scripts" / "pytest_facet_stochastic.py"
    stochastic_plugin_dst = user_copilot_dir / "hooks" / "scripts" / "pytest_facet_stochastic.py"
    approved_sources_src = REPO_ROOT / ".github" / "hooks" / "approved_sources.json"
    approved_sources_dst = user_copilot_dir / "hooks" / "approved_sources.json"
    hook_config_dst = user_copilot_dir / "hooks" / "pipeline-guards.json"

    if hook_script_src.exists():
        if args.dry_run:
            if facet_guards_pkg_src.is_dir():
                sync_directory(facet_guards_pkg_src, facet_guards_pkg_dst, dry_run=True)
            print(f"  [Dry-Run] Would copy pipeline_guards.py -> {hook_script_dst}")
            if stochastic_plugin_src.exists():
                print(f"  [Dry-Run] Would copy pytest_facet_stochastic.py -> {stochastic_plugin_dst}")
            if approved_sources_src.exists():
                print(f"  [Dry-Run] Would copy approved_sources.json -> {approved_sources_dst}")
            print(f"  [Dry-Run] Would generate {hook_config_dst}")
        else:
            hook_script_dst.parent.mkdir(parents=True, exist_ok=True)
            if facet_guards_pkg_src.is_dir():
                if facet_guards_pkg_dst.exists():
                    shutil.rmtree(facet_guards_pkg_dst)
                sync_directory(facet_guards_pkg_src, facet_guards_pkg_dst, dry_run=False)
            shutil.copy2(hook_script_src, hook_script_dst)
            os.chmod(hook_script_dst, 0o755)
            print(f"  [Installed] pipeline_guards.py -> {hook_script_dst}")
            if stochastic_plugin_src.exists():
                shutil.copy2(stochastic_plugin_src, stochastic_plugin_dst)
                print(f"  [Installed] pytest_facet_stochastic.py -> {stochastic_plugin_dst}")
            if approved_sources_src.exists():
                shutil.copy2(approved_sources_src, approved_sources_dst)
                print(f"  [Installed] approved_sources.json -> {approved_sources_dst}")

            # Copy the workspace hook registry and rewrite every command from
            # workspace scope to user scope (`--scope user`) so `pipeline_guards.py`
            # knows to yield automatically whenever a workspace-level hook is active.
            q_hook = shlex.quote(str(hook_script_dst))
            ws_hook_src = json.loads((REPO_ROOT / ".github" / "hooks" / "pipeline-guards.json").read_text(encoding="utf-8"))
            user_hooks = {}
            for ev_name, entries in ws_hook_src.get("hooks", {}).items():
                rewritten_entries = []
                # Walk `entries` and collect matching entries (`entry`).
                for entry in entries:
                    new_entry = dict(entry)
                    if "command" in new_entry:
                        new_entry["command"] = new_entry["command"].replace(
                            ".github/hooks/scripts/pipeline_guards.py --scope workspace",
                            f"{q_hook} --scope user",
                        )
                    rewritten_entries.append(new_entry)
                user_hooks[ev_name] = rewritten_entries
            user_hook_config = {"version": ws_hook_src.get("version", 1), "hooks": user_hooks}
            hook_config_dst.write_text(json.dumps(user_hook_config, indent=2), encoding="utf-8")
            print(f"  [Installed] pipeline-guards.json -> {hook_config_dst}")

    # 6. Build Ollama Models if requested (delegating to build_modelfiles.sh so stop=[] clears inherited stops)
    if args.build_ollama:
        print("\n6. Compiling Ollama Modelfiles via clean-stop builder...")
        build_script = REPO_ROOT / "modelfiles" / "build_modelfiles.sh"
        if build_script.is_file():
            if args.dry_run:
                print(f"  [Dry-Run] Would execute: bash {build_script}")
            else:
                subprocess.run(["bash", str(build_script)], check=True)

    print("\n[OK] Installation complete. Start a new Copilot session to use Facet.")

if __name__ == "__main__":
    main()
