# Ollama Modelfile Architecture for Facet

This directory contains the source Modelfile blueprints for the local Gemma 4 model suite running on a Mac Mini M5 Pro (64GB Unified Memory).

## Model Allocation Matrix

| Role | Blueprint File | Base Model | Context | Sampling & Mode | Cognitive Focus |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`@Researcher`**, **`@Formalist`**, **`@Engineer`** & **`@Auditor`** | `Modelfile.engineer-auditor` *(default)* / `Modelfile.{researcher,formalist}` | `gemma4:26b-a4b-it-q8_0` | 128K (`131072`) | **Thinking ON** (`T=1.0, p=0.95, k=64`) | Unified high-speed MoE literature grounding (`RESEARCH.md` + `manifest.json`), discrete formalization (`mapping.json`), coding, and 4-predicate cleanroom verification (`VERIFICATION.md` / `AUDIT_FEEDBACK.json`) with zero `S1..S4` VRAM swap. |
| **`@Scribe`** | `Modelfile.e4b` *(default)* / `Modelfile.scribe` | `gemma4:e4b` | 128K (`131072`) | **Direct Vision** (`T=0.1, p=0.95, k=64`) | Dedicated `~150M` Vision Encoder for upstream Stage 0V PDF/diagram/web-figure OCR into `SOURCE_OCR.md` with TEI P5 `<uncertain>`/`<illegible/>` provenance. |
| **`@Orchestrator`** *(hidden)* | `Modelfile.e4b` *(default)* / `Modelfile.orchestrator` | `gemma4:e4b` | 128K (`131072`) | **Direct Routing** (`T=0.1, p=0.95, k=64`) | Low-variance `S0V + S0->S5` stage routing, human approval checkpoints, and subagent coordination (`0.0s` swap with `@Facet` and `@Scribe` via shared `Modelfile.e4b`). |
| **`@Facet`** *(visible)* | `Modelfile.e4b` *(default)* / `Modelfile.facet` | `gemma4:e4b` | 128K (`131072`) | **Direct Gating** (`T=0.1, p=0.95, k=64`) | Transient read-only hypervisor: pre-run seal validation, internal `@Orchestrator` invocation, post-archive `PROVENANCE.json` verification, and `PRE_RUN_HALT` loop-back. |
| **System-1 Boundary Decider** | Native `/v1/systemone` (`keep_alive=0`) | `pplx-decider-flash` | 256K (`262144`) | **Calibrated `choice`/`noul`** (`0` output tok) | Sub-second stage-boundary semantic spec screening, Stage-4 diagnosis-vs-label override, and `Method L3` AST/JSON hunk edit-scope localization (`0 B` persistent VRAM). |

---

## Hardware & Metal Memory Concurrency Invariant

*Apple Silicon 64GB Unified Memory Architecture:*
- Default Metal wired memory budget: ~48 GB.
- Active specialist + coordinator weight set: `gemma4:26b-a4b-it-q8_0` (~27.6 GB wired with `q8_0` KV) + `gemma4:e4b` (9.4 GB) = **37.0 GB** total (fitting 100% inside the macOS unified page cache alongside transient `pplx-decider-flash` at 5.6 GB with `keep_alive=0`).
- **Single-Model Residency & 8-Bit KV-Cache Invariant (Prescribed):** Configure **`OLLAMA_MAX_LOADED_MODELS=1`**, **`OLLAMA_FLASH_ATTENTION=1`**, and **`OLLAMA_KV_CACHE_TYPE=q8_0`** on the Ollama server:
  - **macOS (`Ollama.app`, launched via `launchd`):** Shell `export` lines do not reach `Ollama.app`; run `launchctl setenv OLLAMA_MAX_LOADED_MODELS 1`, `launchctl setenv OLLAMA_FLASH_ATTENTION 1`, and `launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0`, then restart `Ollama.app`.
  - **Single-model residency (`OLLAMA_MAX_LOADED_MODELS=1`):** Keeps `loaded runners count=1` (strict non-co-residency) across every stage transition. Because pipeline `task` execution is strictly sequential (`0 tok/s` from idle models), unloading inactive models frees unified memory for macOS, VS Code, Python/Hypothesis, and the filesystem page cache (`1.6 s` `e4b`, `4.1–5.0 s` `26b-a4b`, `~0.9 s` one-shot `pplx-decider-flash` with `keep_alive=0`), while unifying `@Researcher`, `@Formalist`, `@Engineer`, and `@Auditor` onto `gemma4:26b-a4b-it-q8_0` eliminates intermediate `S1 -> S2 -> S3 -> S4` specialist swaps entirely.
  - **Dual-runner `q8_0` KV cache (`OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0`):** Ollama exposes KV-cache quantization only at the server level (not as a Modelfile `PARAMETER`) and applies it automatically where supported: on GGUF `llama-server` models (`gemma4:26b-a4b-it-q8_0` and any GGUF `12b` tag), `--flash-attn on --cache-type-k q8_0 --cache-type-v q8_0` reduces the 128K (`131072`) KV buffer by **45%** from `2860.00 MiB` (`2560 MiB` global + `300 MiB` sliding-window in `f16`) to **`1572.50 MiB`** (`1360 MiB` global + `212.5 MiB` sliding-window in `q8_0`), saving `~1.26 GiB` wired Metal memory with negligible precision impact; Safetensors `mlx` models (`gemma4:e4b`, `embeddinggemma-2:740m-mxfp8`) retain their native MLX Metal KV caches unharmed.

### Estimated Footprint (`num_ctx 131072`, from `ollama show`) and Measured Resident Size

| Tag | Total / active params | Weights | KV + Metal scratch | Wired total |
| :--- | :--- | :--- | :--- | :--- |
| `gemma4:26b-a4b-it-q8_0` | 25.23B / 3.8B active | 28.0 GB | ~1.6 GB (`q8_0`) | ~27.6 GB |
| `gemma4:e4b` | 8.07B (PLE) / 4.5B active | 9.5 GB | ~2.0 GB | ~9.4–11.5 GB |
| `pplx-decider-flash` | 4B Vision+Text (`keep_alive=0`) | 5.6 GB | transient | `0 B` persistent (`5.6 GB` peak) |
| `gemma4:e2b` | 5.20B (PLE) / 2.3B active | 7.5 GB | ~1.5-2.5 GB | ~9.0-10.0 GB |

- **Measured resident size (`/api/ps`, `context_length 131072`):** `gemma4:e4b` `9.42 GB` (`mlx`), `gemma4:26b-a4b-it-q8_0` `28.1 GB` (`f16` KV) / `~27.6 GB` (`q8_0` KV on `llama-server`), `pplx-decider-flash` `5.63 GB` during `<1 s` boundary `/v1/systemone` calls (`0 B` immediately after via `"keep_alive": 0`).
- **Why single-model transient residency beats persistent co-residency:** Pipeline `task` execution is strictly sequential (`@Facet` is blocked awaiting `@Orchestrator` and decodes nothing while specialists run), so keeping two models wired buys zero concurrency while starving the unified filesystem page cache. Under `OLLAMA_MAX_LOADED_MODELS=1`, inactive models unload cleanly and `e4b` reloads from warm page cache in `~1.6 s`.
- **`gemma4:e2b` is not used for `@Facet` on 64GB hardware:** PLE keeps 5.20B parameters (7.5 GB) mapped, saving only ~2.0 GB versus E4B while halving active parameters (2.3B versus 4.5B), and under `OLLAMA_MAX_LOADED_MODELS=1` a distinct `e2b` tag would force a `7.5 GB <-> 9.4 GB` unload/reload on every `@Facet <-> @Orchestrator` handoff. Sharing `gemma4:e4b` across `@Facet`, `@Orchestrator`, and `@Scribe` keeps that handoff at `0.0 s`. `e2b` remains the shared tag for `@Facet`, `@Orchestrator`, and `@Scribe` only in the 16GB profile.

---

## How to Build the Models in Ollama

> **Clean-Stop Compilation Invariant (`stop=[]`):** Custom `PARAMETER stop` strings (`<channel|>`, `<|channel>thought`, `<|channel>call`) intercept Gemma 4's native thinking-close and `<|tool_call>` delimiters before Ollama's built-in `PARSER gemma4` executes, and `ollama create -f` inherits prior `PARAMETER stop` arrays from existing tags. Always compile via `bash modelfiles/build_modelfiles.sh` (which calls Ollama's `/api/create` endpoint with `"stop": []` and verifies via `ollama show <tag> --modelfile` that zero `PARAMETER stop` overrides remain).

### Recommended: Clean-Stop Shared Base Tags (`build_modelfiles.sh`)
Updates Ollama's internal registry in place (`gemma4:26b-a4b-it-q8_0` across all four reasoning specialists and shared `gemma4:e4b` from `Modelfile.e4b`) with 128K context, empirically verified sampling (`T=0.1, top_p=0.95, top_k=64` on `E4B`; `T=1.0, top_p=0.95, top_k=64` on `26B-A4B`), and `"stop": []`:

```bash
bash modelfiles/build_modelfiles.sh
```

### Optional: Clean-Stop Dedicated Role Tags (`--role-tags`)
If you prefer distinct system prompts on separate tags (`Modelfile.facet` vs. `Modelfile.orchestrator` vs. `Modelfile.scribe`), build all 6 dedicated role tags with `"stop": []` via `/api/create` and point `model:` in `.github/agents/*.agent.md` to them:

```bash
bash modelfiles/build_modelfiles.sh --role-tags
```
