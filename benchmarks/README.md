# Facet × `SciCode` Canonical Benchmark Harness

[`run_benchmark.py`](./run_benchmark.py) implements the **exact official `SciCode` evaluation procedure** from [scicode-bench.github.io](https://scicode-bench.github.io/) and [`scicode-bench/SciCode`](https://github.com/scicode-bench/SciCode) (*Tian et al., NeurIPS 2024 Datasets & Benchmarks*, [arXiv:2407.13168](https://arxiv.org/abs/2407.13168)).

---

## Canonical Fidelity Checklist

1. **Verbatim Prompt Templates (`eval/data/`):**
   - **Standard Setup (`--with-background` omitted, default):** Uses `eval/data/background_comment_template.txt` (`DEFAULT_PROMPT_TEMPLATE`), where models must generate their own scientific background and code without scientist-annotated hints.
   - **With Background (`--with-background`):** Uses `eval/data/multistep_template.txt` (`BACKGROUND_PROMPT_TEMPLATE`).
2. **Strict Step-by-Step Causal Accumulation (`ScicodePromptingAssistant`):**
   - Subproblems (`1..k`) are presented strictly in order across all arms.
   - Subproblem `k` sees **only** subproblems `1..k-1` and the arm's **own** generated code (`previous_llm_code[0..k-2]`)—never future subproblems, never scientist gold solutions, and never held-out test cases.
   - Preserves the official `generated_code/{with_background|without_background}/{prob_id}.{num_steps}.py` and `evaluation_logs/` file hierarchy, including the three canonical skipped/fixed `test`-split steps (`13.6`, `62.1`, `76.3`).
3. **Official `test_data.h5` Evaluator (`ScicodeEvaluator`):**
   - Evaluates generated `{prob_id}.{num_steps}.py` files against the official `1.0 GB` numeric target archive (`benchmarks/.cache/test_data.h5`, auto-downloaded on first run) via `process_hdf5_to_tuple` and `scicode.compare.cmp`.
   - Reports both **Main Problem Resolve Rate** (requires passing **all** subproblems of a main problem) and **Subproblem Resolve Rate** (`validation`: `15` problems / `50` subproblems; `test`: `65` problems / `288` subproblems).

---

## Three Apples-to-Apples Local Ollama Arms

| Arm | Step Generation Protocol | Cleanroom 4-Predicate `@Auditor` |
| :--- | :--- | :--- |
| `raw-zeroshot` | Official single-turn `SciCode` prompt per substep on the local Engineer model (`gemma4:26b-a4b-it-q8_0` or user-configured tag). | No |
| `raw-self-refine` | Official `SciCode` prompt + local AST/import/signature verification and self-repair turn per substep. | No |
| `facet-pipeline` | Full two-tier `S1 -> S5` `@Facet` cleanroom pipeline (`autonomous-batch`, `"allow_web_research": false` direct `STAGE_1` genesis, `AST-1..AST-4` deterministic gates, `"enable_local_spec_critic": "system1_s1_s2"` boundary `pplx-decider-flash` spec screening + Stage 4 4-predicate verification + `Method L3` advisory `<edit_scope_hint>` scoped edits) per substep. | **Yes** (`LOCAL_CRITIC.json` + `VERIFICATION.md` + `PROVENANCE.json` + System-1 target-aware spec rewind) |

---

## Usage

### 1. Canonical Sanity Checks (`gold` & `dummy` against Official `test_data.h5`)

```bash
./.venv/bin/python benchmarks/run_benchmark.py --split validation --mode gold
./.venv/bin/python benchmarks/run_benchmark.py --split validation --mode dummy
```

*(Note: Against the unmodified upstream x86-recorded `test_data.h5` on Apple Silicon ARM64, `--mode gold` passes `48/50` subproblems (`13/15` main problems); `78.3` scales its objective by wall-clock `time.time()` and `70.8` TC3 exhibits a `1.5e-4` x86-vs-ARM64 LAPACK delta. `ScicodeEvaluator` and `test_data.h5` are intentionally kept 100% unmodified across all arms.)*

### 2. Run the Curated `--preset quick` Slice (`3` Main Problems / `4` Subproblems × 3 Arms, `~30 min`)

```bash
./.venv/bin/python benchmarks/run_benchmark.py --split validation --preset quick
```

Evaluates `CG` (`1.1`, Numerical Linear Algebra), `IncomChol` (`4.1`, Incomplete Cholesky), and `n_tangle` (`19.1 -> 19.2`, 2-step Quantum Information causal chain) across all 3 arms while updating `<run_dir>/live_lens_workspace` on each substep so **Facet Lens** streams live stage transitions, equations, and 4-predicate proof outcomes in real time (`100.0%` `3/3` main problem and `100.0%` `4/4` subproblem resolve rate across all 3 arms on `gemma4:26b-a4b-it-q8_0` + `pplx-decider-flash` under `OLLAMA_MAX_LOADED_MODELS=1`).

### 3. Run the Full `validation` Split (`15` Main Problems, `50` Subproblems × 3 Arms)

```bash
# Standard SciCode leaderboard setup (without scientist background)
./.venv/bin/python benchmarks/run_benchmark.py --split validation

# With scientist-annotated background
./.venv/bin/python benchmarks/run_benchmark.py --split validation --with-background
```

### 4. Resume an Interrupted Run & Compare Regression Runs

```bash
# Resume at substep granularity
./.venv/bin/python benchmarks/run_benchmark.py --resume benchmarks/runs/<run_id>

# Render a side-by-side Main % / Substep % / Wall-Clock delta table across two runs
./.venv/bin/python benchmarks/run_benchmark.py --compare-runs benchmarks/runs/<baseline_run> benchmarks/runs/<candidate_run>
```

Each run's `RESULTS.md` and `results.jsonl` record **Main Problem Resolve Rate**, **Subproblem Resolve Rate**, **Silent-Bug Catches**, **Avg Wall (s)**, **Avg Tok/s** (`eval_tokens_per_sec` from Ollama `eval_duration`), **Output Tokens**, and **Repairs (`spec_rewinds`)**.
