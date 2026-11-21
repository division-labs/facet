# Facet Installation and Setup Guide

This guide details how to install and configure **Facet** for local execution with Google DeepMind Gemma 4 models in Visual Studio Code.

---

## 1. Prerequisites and System Requirements

Facet is optimized for Apple Silicon (M-series) unified memory and standard Linux/macOS workstations running Ollama:

- **Operating System:** macOS (Sonoma, Sequoia, or newer) or Linux (x86_64 or aarch64).
- **Python:** Python 3.9 or newer (uses standard library modules: `json`, `ast`, `hashlib`, `urllib`, `argparse`).
- **Ollama:** Version 0.6.0 or newer. Install from [ollama.com](https://ollama.com).
- **Visual Studio Code:** Latest stable version with the GitHub Copilot extension installed and authenticated, using the **Copilot harness** in Chat (VS Code documents the legacy **Local harness** as deprecated and scheduled for removal in February 2027).

### Hardware Reference

| Configuration | Target Hardware | Models Allocated | Typical Memory Footprint |
| :--- | :--- | :--- | :--- |
| **Reference (64GB)** | Mac Mini / Studio M-series (64GB Unified) | 26B-A4B MoE, E4B Vision, `pplx-decider-flash` (`gemma4-64gb-apple-silicon.json`) | ~27.6 GB peak wired GPU memory (`OLLAMA_MAX_LOADED_MODELS=1`) |
| **Compact Local (16GB–24GB)** | 16GB–24GB Apple Silicon or Linux/Windows GPU | 12B Unified, E4B, E2B Vision (`local-ollama-16gb.json`) | ~9.5 GB peak GPU memory |


---

## 2. Pull Base Gemma 4 Models in Ollama

Download the official base Gemma 4 models from the Ollama library:

```bash
# Core reasoning, vision & boundary-decision models (64GB reference stack)
ollama pull gemma4:26b-a4b-it-q8_0
ollama pull gemma4:e4b
ollama pull pplx-decider-flash

# Optional: Semantic embedding sink
ollama pull embeddinggemma-2:740m-mxfp8
```

---

## 3. Compile the Facet Modelfiles

Facet provides tailored Modelfile blueprints that configure 128K context windows (`num_ctx 131072`) and role-matched sampling parameters without polluting the global chat tags:

```bash
chmod +x modelfiles/build_modelfiles.sh
./modelfiles/build_modelfiles.sh
```

This compiles two unified Gemma 4 base tags via Ollama's HTTP `/api/create` endpoint with `"stop": []` (clearing inherited stop sequences so native `PARSER gemma4` handles thinking and `<|tool_call>` delimiters cleanly) and verifies `pplx-decider-flash`:
1. `gemma4:26b-a4b-it-q8_0` for `@Researcher`, `@Formalist`, `@Engineer`, and `@Auditor` (unified high-throughput MoE literature grounding, discrete `mapping.json` formalization, typed coding, and 4-predicate cleanroom verification at `T=1.0, top_p=0.95, top_k=64` with zero `S1..S4` VRAM swap).
2. `gemma4:e4b` for `@Facet`, `@Orchestrator`, and `@Scribe` (shared warm runner with zero swap; low-variance `S0V + S0->S5` routing and dedicated `~150M`-encoder Vision/PDF OCR into `SOURCE_OCR.md` at `T=0.1, top_p=0.95, top_k=64`).
3. `pplx-decider-flash` invoked exclusively at stage transitions via `/v1/systemone` with `"keep_alive": 0` (`<1 s` semantic spec screening, Stage-4 triage override, and `Method L3` edit line-scope localization with `0 B` persistent VRAM).

### Hardware Concurrency & 8-Bit KV-Cache Settings (Prescribed)

On Apple Silicon with 64GB unified memory, configure **`OLLAMA_MAX_LOADED_MODELS=1`**, **`OLLAMA_FLASH_ATTENTION=1`**, and **`OLLAMA_KV_CACHE_TYPE=q8_0`** on the Ollama server. Because pipeline `task` execution is strictly sequential, keeping `loaded runners count=1` (strict non-co-residency) prevents idle models from holding wired Metal memory and preserves the macOS filesystem page cache (`~1.6 s` `e4b`, `~4.1–5.0 s` `26b-a4b`, `~0.9 s` `pplx-decider-flash` with `keep_alive=0`, and `0.0 s` swap across `S1 -> S2 -> S3 -> S4`). Ollama applies `OLLAMA_KV_CACHE_TYPE=q8_0` automatically where supported: on GGUF `llama-server` models (`gemma4:26b-a4b-it-q8_0` and any GGUF `12b` tag), it cuts the 128K KV buffer by 45% (`2860.00 MiB -> 1572.50 MiB`), while Safetensors `mlx` models (`gemma4:e4b`, `embeddinggemma-2:740m-mxfp8`) retain their native MLX Metal KV caches.

**macOS (`Ollama.app` — required because `launchd` apps ignore shell `.zshrc` exports):**

```bash
launchctl setenv OLLAMA_MAX_LOADED_MODELS 1
launchctl setenv OLLAMA_FLASH_ATTENTION 1
launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0
# Then quit and reopen Ollama.app from the menu bar so the server inherits the variables.
```

**Linux / standalone CLI (`ollama serve`):**

```bash
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
```

---

## 4. Deployment Options

Facet can be used either as a project-specific **Workspace Template** or as a machine-wide **User-Level Customization**.

### Option A: Workspace Template (Recommended)

When starting a new project, clone the Facet repository as your workspace root:

```bash
git clone https://github.com/division-labs/facet.git my-project
cd my-project
python3 -m venv .venv
./.venv/bin/pip install -r templates/domains/scientific-computing/requirements.txt
```

Open `my-project` in VS Code. The Copilot extension will automatically discover:
- Custom agents in `.github/agents/*.agent.md`.
- Workspace-scoped **Facet Lens** Copilot Canvas in [extension.mjs](../.github/extensions/facet-lens/extension.mjs) (`id: "facet-pipeline-lens"`).
- Deterministic hooks in `.github/hooks/pipeline-guards.json`.
- Agent skills in `.github/skills/*/SKILL.md`.
- Ambient instructions in `.github/copilot-instructions.md`.
- Workspace VS Code settings in `.vscode/settings.json` (`"chat.requestQueuing.defaultAction": "queue"` so follow-up `/approve` messages wait for Checkpoint briefings to finish cleanly, and `"chat.math.enabled": true` for KaTeX equation rendering).

### Option B: User-Level Installation (`~/.copilot/`)

If you want Facet's personas, skills, and safety hooks available across all your repositories without copying `.github/` into each one, install them into your user profile directory (and optionally add `"chat.requestQueuing.defaultAction": "queue"` and `"chat.math.enabled": true` to your VS Code User `settings.json`):

#### Automated User-Level Installation

Run the included installer:

```bash
# Preview actions without modifying disk
python3 scripts/install_user_level.py --dry-run

# Perform installation (preserves any existing user model identifiers)
python3 scripts/install_user_level.py
```

The installer (respecting `$COPILOT_HOME` when set, defaulting to `~/.copilot/`):
1. Copies all 7 custom agent profiles (visible `@Facet`, hidden `@Orchestrator`, and 5 specialists) to `~/.copilot/agents/`, preserving existing user `model:` tags and rewriting the inline `@Facet` (`hypervisor-guard`, `facet-prompt`, `facet-stop`) and `@Orchestrator` hook commands to quoted user-scope paths.
2. Synchronizes all 5 domain skills (and sets executable permissions on bundled `scripts/*.py` helpers) to `~/.copilot/skills/`.
3. Synchronizes `copilot-instructions.md` to `~/.copilot/copilot-instructions.md`.
4. Copies the Modelfiles to `~/.copilot/modelfiles/`.
5. Installs `pipeline_guards.py` and `approved_sources.json` to `~/.copilot/hooks/` and generates `~/.copilot/hooks/pipeline-guards.json` with quoted `--scope user` commands (keeping `.github/extensions/facet-lens/` strictly Workspace-scoped so personal non-Facet repositories are untouched).

#### Manual User-Level Installation

If preferred, copy the files manually (prefer `scripts/install_user_level.py` so inline hook paths in `facet.agent.md` and `orchestrator.agent.md` are rewritten automatically):

```bash
mkdir -p ~/.copilot/agents ~/.copilot/skills ~/.copilot/hooks/scripts ~/.copilot/modelfiles

cp -R .github/agents/*.agent.md ~/.copilot/agents/
cp -R .github/skills/* ~/.copilot/skills/
cp .github/copilot-instructions.md ~/.copilot/copilot-instructions.md
cp -R modelfiles/* ~/.copilot/modelfiles/
cp .github/hooks/approved_sources.json ~/.copilot/hooks/approved_sources.json
cp .github/hooks/scripts/pipeline_guards.py ~/.copilot/hooks/scripts/pipeline_guards.py
chmod +x ~/.copilot/hooks/scripts/pipeline_guards.py \
  ~/.copilot/skills/technical-perception/scripts/prepare_visual_sources.py \
  ~/.copilot/skills/implementation-engineering/scripts/code_patch.py
```

Then create `~/.copilot/hooks/pipeline-guards.json` with `--scope user` attached to all commands.

---

## 5. Scope-Aware Hook Precedence

When hooks exist in both `.github/hooks/` and `~/.copilot/hooks/`, the Copilot runtime invokes both by default. Facet eliminates duplicate execution via **Scope-Aware Hook Shadowing**:
- The user-level hook receives `--scope user`.
- If an active workspace hook (`.github/hooks/pipeline-guards.json`) exists in the current project and is not disabled, the user-level hook yields immediately (`{}`) in less than 1 millisecond.
- A secondary atomic POSIX lock (`os.O_CREAT | os.O_EXCL`) prevents race conditions between concurrent processes.

---

## 6. Verification and Health Check

Validate that all schemas, visual source extractors, Modality Firewall rules, TEI P5 provenance gates, stage-boundary fault injections, multi-axis Mandelbrot pipeline trajectories, runtime CLI entrypoints, and local Ollama integrations pass with 100% success:

```bash
./.venv/bin/python scripts/validate_system.py
```

For ad-hoc targeted runs or expanded randomized trajectory sampling across all 9 configuration axes, pass `--pathway`, `--fuzz-trajectories`, or `--offline`:

```bash
./.venv/bin/python scripts/validate_system.py --pathway trajectories --fuzz-trajectories 12 --seed 7
./.venv/bin/python scripts/validate_system.py --offline
```

Expected output summary:
```
=== Pathway 1: Static Contracts, Packaging & Runtime Entrypoint Inventory ===
  [PASS] All 4 JSON schemas, approved_sources.json, & shipped examples/mandelbrot_set/ bundle verified
  [PASS] VS Code queuing/math settings, 7 agents (visible @Facet + hidden @Orchestrator), 5 skills, 7 clean-stop Modelfiles, & URL/path decoupling verified
  [PASS] Templates, presets, install_user_level.py, & build_modelfiles.sh (2-tag/6-tag/stop-guards) verified
  [PASS] Docs links, zero-emoji/branding, non-bleed/non-archaeology firewall, & 5-script/22-action inventory verified

=== Pathway 2: Source Domain Governance, Visual OCR Staging & TEI P5 Provenance ===
  [PASS] Approved domain registry, /add-source, duplicate web_fetch guard, & .venv permissionRequest verified
  [PASS] Multi-format visual staging (PDF/JPEG/HTML/SVG/BMP/GIF/WebP/TIFF), quarantine, & 302 guard verified
  [PASS] Modality Firewall, @Scribe confinement, & SOURCE_OCR.md TEI P5 sanitization/rollback verified

=== Pathway 3: Stage-Boundary Fault Injections, 4-Layer Editing & Cleanroom Oracles ===
  [PASS] Stage 0..2 Gemma <|'|> LaTeX JSON, .rollback_* recovery, SPEC coverage, & continuous predicate guards verified
  [PASS] Stage 3 PEP 508/257, AST parameter/Any/float guards, 4-stage edit cascade, & code_patch.py verified
  [PASS] Stage 4 symmetrical Cleanroom guards, 3-Tier verify_*.py soundness/hygiene gate, & emoji scrubbing verified
  [PASS] @Facet hypervisor genesis, read-only confinement, seal forgery/ledger parity, upstream_seals, & human-gate halts verified

=== Pathway 4: Multi-Axis Pipeline Trajectories (Spanning Matrix + 4 Random Samples) & Hook CLI ===
  [PASS] Executed 6 orthogonal spanning trajectories across all governance, entry, edit, & equivalence axes
  [PASS] Executed 4 seeded non-baked random trajectories (seed=20261115) with 100% invariant preservation
  [PASS] All 22 pipeline_guards CLI actions, scope/lock guards, & post_archive_embed CLI verified

=== Pathway 5: Live Local Model Integrations (Vision OCR, CRLF Edit & 768D Embeddings) ===
  [PASS] Modelfile contract on 2 built tags and first-system rule verified
  [PASS] Live gemma4:e4b multimodal OCR verified on Mandelbrot plates (0 confabulations)
  [PASS] Live gemma4:26b-a4b-it-q8_0 CRLF surgical edit on Mandelbrot module rescued in 1 shot
  [PASS] Live embeddinggemma-2:740m-mxfp8 /api/embed indexed 11 768D Mandelbrot chunks

>>> ALL VALIDATION TESTS PASSED (100% SUCCESS) <<<
```
